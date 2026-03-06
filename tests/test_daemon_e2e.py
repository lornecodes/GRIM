"""End-to-end integration tests for the management daemon.

Full lifecycle with temp vault + temp SQLite + mocked pool queue.
Tests the complete story flow through the pipeline.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from core.daemon.engine import ManagementEngine
from core.daemon.health import HealthMonitor
from core.daemon.intelligence import Resolution
from core.daemon.models import PipelineItem, PipelineStatus, InvalidTransition
from core.daemon.pipeline import PipelineStore
from core.daemon.scanner import ProjectScanner
from core.pool.events import PoolEvent, PoolEventBus, PoolEventType


# ── Helpers ──────────────────────────────────────────────────────


def _make_project_fdo(vault_path: Path, proj_id: str, stories: list[dict]) -> None:
    """Write a minimal proj-* FDO file in the vault."""
    projects_dir = vault_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    fm = {
        "id": proj_id,
        "title": f"Project {proj_id}",
        "domain": "projects",
        "status": "developing",
        "confidence": 0.7,
        "tags": ["epic"],
        "stories": stories,
    }
    body = f"# {proj_id}\n\n## Summary\nTest project."
    fm_yaml = yaml.dump(fm, default_flow_style=False, sort_keys=False)
    fdo_path = projects_dir / f"{proj_id}.md"
    fdo_path.write_text(f"---\n{fm_yaml}---\n\n{body}", encoding="utf-8")


def _make_config(vault_path: Path, db_path: Path, **overrides):
    """Create a mock config object."""
    cfg = MagicMock()
    cfg.vault_path = vault_path
    cfg.workspace_root = vault_path.parent
    cfg.daemon_poll_interval = 999  # prevent auto-loop
    cfg.daemon_max_concurrent_jobs = overrides.get("max_concurrent", 1)
    cfg.daemon_auto_dispatch = overrides.get("auto_dispatch", True)
    cfg.daemon_db_path = db_path
    cfg.daemon_project_filter = overrides.get("project_filter", [])
    # Phase 3: intelligence config (disabled by default in E2E tests)
    cfg.daemon_auto_resolve = overrides.get("auto_resolve", False)
    cfg.daemon_validate_output = overrides.get("validate_output", False)
    cfg.daemon_max_daemon_retries = overrides.get("max_daemon_retries", 0)
    cfg.daemon_resolve_model = "claude-sonnet-4-6"
    cfg.daemon_validate_model = "claude-opus-4-6"
    cfg.daemon_resolve_confidence_threshold = 0.7
    return cfg


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path) -> Path:
    """Create a temp vault with test stories."""
    vault_path = tmp_path / "vault"

    _make_project_fdo(vault_path, "proj-alpha", [
        {
            "id": "story-alpha-001",
            "title": "Implement feature X",
            "status": "active",
            "priority": "high",
            "assignee": "code",
            "description": "Build the X feature",
            "acceptance_criteria": ["Tests pass", "Docs updated"],
        },
        {
            "id": "story-alpha-002",
            "title": "Research topic Y",
            "status": "in_progress",
            "priority": "medium",
            "assignee": "research",
            "description": "Investigate Y",
        },
    ])

    _make_project_fdo(vault_path, "proj-beta", [
        {
            "id": "story-beta-001",
            "title": "Security audit",
            "status": "active",
            "priority": "critical",
            "assignee": "audit",
            "description": "Full security review",
        },
    ])

    return vault_path


@pytest.fixture
def pool_queue():
    """Mock pool job queue."""
    q = AsyncMock()
    q.submit = AsyncMock()
    return q


@pytest.fixture
def pool_events():
    """Real PoolEventBus for event testing."""
    return PoolEventBus()


@pytest.fixture
async def engine(vault, tmp_path, pool_queue, pool_events):
    """Create and initialize a ManagementEngine (without starting the loop)."""
    db_path = tmp_path / "e2e.db"
    cfg = _make_config(vault, db_path)

    eng = ManagementEngine(
        config=cfg,
        pool_queue=pool_queue,
        pool_events=pool_events,
        vault_path=vault,
    )
    # Initialize store without starting the main loop
    await eng.store.initialize()
    pool_events.subscribe(eng._event_callback)
    yield eng
    pool_events.unsubscribe(eng._event_callback)


# ── Full lifecycle tests ─────────────────────────────────────────


class TestFullLifecycle:
    """Story flows through BACKLOG → READY → DISPATCHED → REVIEW."""

    @pytest.mark.asyncio
    async def test_scan_to_backlog(self, engine):
        """Scan vault and verify stories land in BACKLOG."""
        await engine._scan_cycle()

        items = await engine.store.list_items()
        assert len(items) == 3
        assert all(i.status == PipelineStatus.BACKLOG for i in items)

    @pytest.mark.asyncio
    async def test_promote_to_ready(self, engine):
        """BACKLOG items get promoted to READY."""
        await engine._scan_cycle()
        await engine._promote_cycle()

        items = await engine.store.list_items()
        assert all(i.status == PipelineStatus.READY for i in items)

    @pytest.mark.asyncio
    async def test_dispatch_to_pool(self, engine, pool_queue):
        """READY items get dispatched as pool jobs."""
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        # One dispatched (max_concurrent=1)
        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 1
        assert dispatched[0].job_id is not None

        # Pool queue got one submit call
        pool_queue.submit.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_happy_path(self, engine, pool_queue, pool_events):
        """Full lifecycle: scan → promote → dispatch → complete → review."""
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 1
        job_id = dispatched[0].job_id

        # Simulate pool completing the job
        await pool_events.emit(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id=job_id,
            data={"workspace_id": "ws-123"},
        ))

        # Item should now be in REVIEW
        item = await engine.store.get_by_job(job_id)
        assert item.status == PipelineStatus.REVIEW
        assert item.workspace_id == "ws-123"

    @pytest.mark.asyncio
    async def test_job_id_written_to_vault(self, engine, pool_queue):
        """After dispatch, job_id should be written back to vault story."""
        # TaskEngine is lazily initialized; mock it
        mock_te = MagicMock()
        mock_te.update_item = MagicMock()
        engine._task_engine = mock_te

        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        # Should have called update_item with the job_id
        mock_te.update_item.assert_called_once()
        call_args = mock_te.update_item.call_args
        assert "job_id" in call_args[0][1]  # second arg is the dict


# ── Failed path ──────────────────────────────────────────────────


class TestFailedPath:

    @pytest.mark.asyncio
    async def test_job_failed_event(self, engine, pool_events, pool_queue):
        """JOB_FAILED event transitions item to FAILED."""
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        job_id = dispatched[0].job_id

        await pool_events.emit(PoolEvent(
            type=PoolEventType.JOB_FAILED,
            job_id=job_id,
            data={"error": "Agent crashed"},
        ))

        item = await engine.store.get_by_job(job_id)
        assert item.status == PipelineStatus.FAILED
        assert item.error == "Agent crashed"

    @pytest.mark.asyncio
    async def test_retry_after_failure(self, engine, pool_events, pool_queue):
        """FAILED items can be retried by advancing to READY."""
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        item_id = dispatched[0].id
        job_id = dispatched[0].job_id

        # Fail the job
        await pool_events.emit(PoolEvent(
            type=PoolEventType.JOB_FAILED,
            job_id=job_id,
            data={"error": "timeout"},
        ))

        # Retry
        retried = await engine.store.advance(item_id, PipelineStatus.READY)
        assert retried.status == PipelineStatus.READY

        # Should be dispatchable again
        pool_queue.submit.reset_mock()
        await engine._dispatch_cycle()
        dispatched2 = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched2) == 1


# ── Blocked path ─────────────────────────────────────────────────


class TestBlockedPath:

    @pytest.mark.asyncio
    async def test_job_blocked_event(self, engine, pool_events, pool_queue):
        """JOB_BLOCKED event transitions item to BLOCKED."""
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        job_id = dispatched[0].job_id

        await pool_events.emit(PoolEvent(
            type=PoolEventType.JOB_BLOCKED,
            job_id=job_id,
        ))

        item = await engine.store.get_by_job(job_id)
        assert item.status == PipelineStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_unblock_to_ready(self, engine, pool_events, pool_queue):
        """BLOCKED items can be unblocked by advancing to READY."""
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        item_id = dispatched[0].id
        job_id = dispatched[0].job_id

        # Block it
        await pool_events.emit(PoolEvent(
            type=PoolEventType.JOB_BLOCKED,
            job_id=job_id,
        ))

        # Unblock
        unblocked = await engine.store.advance(item_id, PipelineStatus.READY)
        assert unblocked.status == PipelineStatus.READY


# ── Concurrency limits ───────────────────────────────────────────


class TestConcurrencyLimits:

    @pytest.mark.asyncio
    async def test_respects_max_concurrent(self, vault, tmp_path, pool_queue, pool_events):
        """Only dispatches up to max_concurrent_jobs."""
        db_path = tmp_path / "concurrent.db"
        cfg = _make_config(vault, db_path, max_concurrent=1)
        eng = ManagementEngine(
            config=cfg,
            pool_queue=pool_queue,
            pool_events=pool_events,
            vault_path=vault,
        )
        await eng.store.initialize()

        await eng._scan_cycle()
        await eng._promote_cycle()

        ready = await eng.store.list_items(status_filter=PipelineStatus.READY)
        assert len(ready) == 3  # all three stories

        await eng._dispatch_cycle()

        dispatched = await eng.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        still_ready = await eng.store.list_items(status_filter=PipelineStatus.READY)
        assert len(dispatched) == 1
        assert len(still_ready) == 2

    @pytest.mark.asyncio
    async def test_multi_concurrent(self, vault, tmp_path, pool_queue, pool_events):
        """With higher max_concurrent, dispatches more jobs."""
        db_path = tmp_path / "multi.db"
        cfg = _make_config(vault, db_path, max_concurrent=3)
        eng = ManagementEngine(
            config=cfg,
            pool_queue=pool_queue,
            pool_events=pool_events,
            vault_path=vault,
        )
        await eng.store.initialize()

        await eng._scan_cycle()
        await eng._promote_cycle()
        await eng._dispatch_cycle()

        dispatched = await eng.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 3


# ── Priority ordering ────────────────────────────────────────────


class TestPriorityOrdering:

    @pytest.mark.asyncio
    async def test_critical_dispatched_first(self, vault, tmp_path, pool_queue, pool_events):
        """Critical priority stories should be dispatched before lower priorities."""
        db_path = tmp_path / "priority.db"
        cfg = _make_config(vault, db_path, max_concurrent=1)
        eng = ManagementEngine(
            config=cfg,
            pool_queue=pool_queue,
            pool_events=pool_events,
            vault_path=vault,
        )
        await eng.store.initialize()

        await eng._scan_cycle()
        await eng._promote_cycle()
        await eng._dispatch_cycle()

        # Critical priority story should be dispatched first
        dispatched = await eng.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 1
        assert dispatched[0].story_id == "story-beta-001"
        assert dispatched[0].priority == 0  # critical


# ── Health monitor integration ───────────────────────────────────


class TestHealthIntegration:

    @pytest.mark.asyncio
    async def test_cycle_records_scan(self, engine):
        """A full cycle increments scan_count."""
        assert engine.health.scan_count == 0
        await engine._cycle()
        assert engine.health.scan_count == 1

    @pytest.mark.asyncio
    async def test_dispatch_records_count(self, engine, pool_queue):
        """Dispatching records dispatch_count."""
        assert engine.health.dispatch_count == 0
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()
        assert engine.health.dispatch_count == 1

    @pytest.mark.asyncio
    async def test_health_status_includes_pipeline(self, engine):
        """Health status includes pipeline counts."""
        await engine._scan_cycle()
        status = await engine.health.status(engine.store)
        assert "pipeline" in status
        assert status["running"] is True


# ── Unmanaged job events ignored ─────────────────────────────────


class TestUnmanagedJobs:

    @pytest.mark.asyncio
    async def test_ignores_unknown_job(self, engine, pool_events):
        """Events for unknown job_ids are silently ignored."""
        # Should not raise
        await pool_events.emit(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id="job-unknown-xyz",
        ))

    @pytest.mark.asyncio
    async def test_ignores_empty_job_id(self, engine, pool_events):
        """Events with no job_id are silently ignored."""
        await pool_events.emit(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id="",
        ))


# ── Idempotent scanning ─────────────────────────────────────────


class TestIdempotentScanning:

    @pytest.mark.asyncio
    async def test_double_scan_no_duplicates(self, engine):
        """Running scan twice doesn't create duplicate pipeline items."""
        await engine._scan_cycle()
        await engine._scan_cycle()

        items = await engine.store.list_items()
        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_full_cycle_idempotent(self, engine):
        """Running full cycle twice doesn't break state."""
        await engine._cycle()
        await engine._cycle()

        items = await engine.store.list_items()
        assert len(items) == 3


# ── Engine start/stop lifecycle ──────────────────────────────────


class TestEngineLifecycle:

    @pytest.mark.asyncio
    async def test_start_stop(self, vault, tmp_path, pool_queue, pool_events):
        """Engine can start and stop cleanly."""
        db_path = tmp_path / "lifecycle.db"
        cfg = _make_config(vault, db_path)
        eng = ManagementEngine(
            config=cfg,
            pool_queue=pool_queue,
            pool_events=pool_events,
            vault_path=vault,
        )

        await eng.start()
        assert eng._running is True
        assert pool_events.subscriber_count >= 1

        await eng.stop()
        assert eng._running is False

    @pytest.mark.asyncio
    async def test_stop_without_start(self, vault, tmp_path, pool_queue, pool_events):
        """Stopping a never-started engine doesn't raise."""
        db_path = tmp_path / "nostart.db"
        cfg = _make_config(vault, db_path)
        eng = ManagementEngine(
            config=cfg,
            pool_queue=pool_queue,
            pool_events=pool_events,
            vault_path=vault,
        )
        await eng.stop()  # Should not raise


# ── Mewtwo↔Charizard Seam Tests ──────────────────────────────


class TestMewtwoCharizardSeam:
    """Integration tests with real JobQueue (not mocked).

    Validates the daemon↔pool interface boundary using real SQLite
    for both daemon pipeline and pool job queue.
    """

    @pytest.fixture
    async def seam(self, tmp_path):
        vault_path = tmp_path / "vault"
        _make_project_fdo(vault_path, "proj-seam", [
            {
                "id": "story-seam-001",
                "title": "Seam test story 1",
                "status": "active",
                "priority": "high",
                "assignee": "code",
                "description": "Test the seam",
                "acceptance_criteria": ["Tests pass"],
            },
            {
                "id": "story-seam-002",
                "title": "Seam test story 2",
                "status": "active",
                "priority": "medium",
                "assignee": "research",
            },
        ])

        daemon_db = tmp_path / "daemon.db"
        pool_db = tmp_path / "pool.db"

        from core.pool.queue import JobQueue
        queue = JobQueue(pool_db)
        await queue.initialize()

        events = PoolEventBus()

        cfg = _make_config(vault_path, daemon_db, max_concurrent=2)
        engine = ManagementEngine(
            config=cfg, pool_queue=queue, pool_events=events, vault_path=vault_path,
        )
        await engine.store.initialize()
        events.subscribe(engine._event_callback)

        yield engine, queue, events

        events.unsubscribe(engine._event_callback)

    @pytest.mark.asyncio
    async def test_dispatch_creates_real_job(self, seam):
        """Dispatch should create a real Job in JobQueue."""
        engine, queue, _ = seam

        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) >= 1

        # Verify the job actually exists in the real queue
        job = await queue.get(dispatched[0].job_id)
        assert job is not None
        assert "Seam test story" in job.instructions

    @pytest.mark.asyncio
    async def test_complete_event_advances_review(self, seam):
        """Simulated JOB_COMPLETE should advance pipeline to REVIEW."""
        engine, queue, events = seam

        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        job_id = dispatched[0].job_id

        # Simulate pool completing the job
        from core.pool.models import JobStatus
        await queue.update_status(job_id, JobStatus.COMPLETE, result="All done.")
        await events.emit(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id=job_id,
            data={"workspace_id": "ws-seam-001"},
        ))

        item = await engine.store.get_by_job(job_id)
        assert item.status == PipelineStatus.REVIEW
        assert item.workspace_id == "ws-seam-001"

    @pytest.mark.asyncio
    async def test_blocked_event_stays_blocked(self, seam):
        """JOB_BLOCKED with intelligence disabled should leave item BLOCKED."""
        engine, queue, events = seam

        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        job_id = dispatched[0].job_id

        await events.emit(PoolEvent(
            type=PoolEventType.JOB_BLOCKED,
            job_id=job_id,
            data={"question": "What should I do?"},
        ))

        item = await engine.store.get_by_job(job_id)
        assert item.status == PipelineStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_failed_event_advances_failed(self, seam):
        """JOB_FAILED with retries disabled should advance to FAILED."""
        engine, queue, events = seam

        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        job_id = dispatched[0].job_id

        await events.emit(PoolEvent(
            type=PoolEventType.JOB_FAILED,
            job_id=job_id,
            data={"error": "Agent crashed hard"},
        ))

        item = await engine.store.get_by_job(job_id)
        assert item.status == PipelineStatus.FAILED
        assert item.error == "Agent crashed hard"

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, seam):
        """Scan → promote → dispatch → complete → verify REVIEW, then dispatch next."""
        engine, queue, events = seam

        # First cycle: scan + promote + dispatch (max_concurrent=2, so both dispatch)
        await engine._cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 2

        # Complete the first job
        first_job_id = dispatched[0].job_id
        from core.pool.models import JobStatus
        await queue.update_status(first_job_id, JobStatus.COMPLETE, result="Done.")
        await events.emit(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id=first_job_id,
            data={"workspace_id": "ws-lifecycle-001"},
        ))

        first = await engine.store.get_by_job(first_job_id)
        assert first.status == PipelineStatus.REVIEW

        # Second job still dispatched
        second = await engine.store.get_by_job(dispatched[1].job_id)
        assert second.status == PipelineStatus.DISPATCHED

    @pytest.mark.asyncio
    async def test_auto_resolve_provides_clarification(self, seam):
        """With mocked resolver, auto-resolve should write clarification to real queue."""
        engine, queue, events = seam

        # Enable intelligence with mocked resolver
        mock_resolver = AsyncMock()
        mock_resolver.resolve = AsyncMock(return_value=Resolution(
            answered=True, answer="Use pattern B.", confidence=0.9, source="mechanical",
        ))
        mock_resolver._confidence_threshold = 0.7
        engine._auto_resolve = True
        engine._intelligence = {
            "resolver": mock_resolver,
            "validator": AsyncMock(),
            "enricher": MagicMock(),
        }

        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        job_id = dispatched[0].job_id

        # Block the job
        from core.pool.models import JobStatus
        await queue.update_status(job_id, JobStatus.BLOCKED)
        await events.emit(PoolEvent(
            type=PoolEventType.JOB_BLOCKED,
            job_id=job_id,
            data={"question": "Which pattern should I use?"},
        ))

        # Verify clarification was written to the real queue
        job = await queue.get(job_id)
        assert job.clarification_answer == "Use pattern B."
