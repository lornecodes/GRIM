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
from core.pool.models import Job, JobType


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
    # Phase 4: PR lifecycle
    cfg.daemon_auto_pr = overrides.get("auto_pr", False)
    cfg.daemon_github_repo = overrides.get("github_repo", "")
    cfg.daemon_pr_poll_interval = 999
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
    # Return a CODE job from get() so _handle_review knows the job type
    code_job = Job(job_type=JobType.CODE, instructions="mock")
    q.get = AsyncMock(return_value=code_job)
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


# ── Phase 4: PR Lifecycle E2E ────────────────────────────────────


class TestPRLifecycleE2E:
    """End-to-end tests for Phase 4 PR lifecycle flows."""

    @pytest.mark.asyncio
    async def test_code_job_full_pr_lifecycle(self, tmp_path):
        """CODE job: BACKLOG → DISPATCHED → REVIEW (PR created) → approve → MERGED."""
        vault_path = tmp_path / "vault"
        _make_project_fdo(vault_path, "proj-alpha", [
            {
                "id": "story-alpha-010",
                "title": "Add feature Z",
                "status": "active",
                "priority": "high",
                "assignee": "code",
                "description": "Build feature Z",
                "acceptance_criteria": ["Tests pass"],
            },
        ])

        db = tmp_path / "pr_lifecycle.db"
        config = _make_config(vault_path, db, auto_pr=True)
        queue = AsyncMock()
        code_job = Job(job_type=JobType.CODE, instructions="build Z")
        queue.get = AsyncMock(return_value=code_job)
        events = PoolEventBus()

        mock_github = AsyncMock()
        mock_github.push_branch = AsyncMock()
        mock_github.create_pr = AsyncMock(return_value=(99, "https://github.com/o/r/pull/99"))
        mock_github.merge_pr = AsyncMock()

        mock_ws_mgr = MagicMock()
        mock_ws = MagicMock()
        mock_ws.worktree_path = Path("/fake/worktree")
        mock_ws.branch_name = "grim/ws-z"
        mock_ws.repo_path = Path("/fake/repo")
        mock_ws_mgr.get = MagicMock(return_value=mock_ws)
        mock_ws_mgr.get_branch_diff = AsyncMock(return_value="+10 -2")
        mock_ws_mgr.merge_to_base = AsyncMock()

        events_received = []
        events.subscribe(lambda e: events_received.append(e))

        engine = ManagementEngine(
            config, queue, events,
            vault_path=vault_path,
            workspace_manager=mock_ws_mgr,
        )
        engine._github = mock_github

        await engine.store.initialize()
        events.subscribe(engine._event_callback)

        # Cycle: BACKLOG → READY → DISPATCHED
        await engine._cycle()
        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 1
        job_id = dispatched[0].job_id

        # Simulate JOB_COMPLETE → REVIEW → PR created
        await events.emit(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id=job_id,
            data={"workspace_id": "ws-z"},
        ))

        item = await engine.store.get_by_job(job_id)
        assert item.status == PipelineStatus.REVIEW
        assert item.pr_number == 99
        assert item.pr_url == "https://github.com/o/r/pull/99"

        # JOB_REVIEW event emitted
        await asyncio.sleep(0.05)
        review_events = [e for e in events_received if e.type == PoolEventType.JOB_REVIEW]
        assert len(review_events) >= 1
        assert review_events[0].data["pr_number"] == 99

        # Approve → MERGED
        result = await engine.approve_item(item.id)
        assert result.status == PipelineStatus.MERGED
        mock_github.merge_pr.assert_called_once()
        mock_ws_mgr.merge_to_base.assert_called_once()

    @pytest.mark.asyncio
    async def test_research_job_auto_merges(self, tmp_path):
        """RESEARCH job: BACKLOG → DISPATCHED → REVIEW → auto-MERGED (no PR)."""
        vault_path = tmp_path / "vault"
        _make_project_fdo(vault_path, "proj-alpha", [
            {
                "id": "story-alpha-011",
                "title": "Research topic Q",
                "status": "active",
                "priority": "medium",
                "assignee": "research",
            },
        ])

        db = tmp_path / "research.db"
        config = _make_config(vault_path, db, auto_pr=True)
        queue = AsyncMock()
        research_job = Job(job_type=JobType.RESEARCH, instructions="research Q")
        queue.get = AsyncMock(return_value=research_job)
        events = PoolEventBus()

        engine = ManagementEngine(
            config, queue, events, vault_path=vault_path,
        )
        engine._github = AsyncMock()  # github exists but shouldn't be used

        await engine.store.initialize()
        events.subscribe(engine._event_callback)

        # Cycle
        await engine._cycle()
        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 1
        job_id = dispatched[0].job_id

        # JOB_COMPLETE → should auto-merge (no PR for research)
        await events.emit(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id=job_id,
            data={"workspace_id": "ws-q"},
        ))

        item = await engine.store.get_by_job(job_id)
        assert item.status == PipelineStatus.MERGED

        # GitHub should NOT have been called for push/create_pr
        engine._github.push_branch.assert_not_called()
        engine._github.create_pr.assert_not_called()

    @pytest.mark.asyncio
    async def test_reject_flow(self, tmp_path):
        """REVIEW → reject → FAILED with PR closed."""
        vault_path = tmp_path / "vault"
        _make_project_fdo(vault_path, "proj-alpha", [
            {
                "id": "story-alpha-012",
                "title": "Bad feature",
                "status": "active",
                "priority": "low",
                "assignee": "code",
            },
        ])

        db = tmp_path / "reject.db"
        config = _make_config(vault_path, db, auto_pr=True)
        queue = AsyncMock()
        code_job = Job(job_type=JobType.CODE, instructions="bad code")
        queue.get = AsyncMock(return_value=code_job)
        events = PoolEventBus()

        mock_github = AsyncMock()
        mock_github.push_branch = AsyncMock()
        mock_github.create_pr = AsyncMock(return_value=(55, "https://github.com/o/r/pull/55"))
        mock_github.close_pr = AsyncMock()

        mock_ws_mgr = MagicMock()
        mock_ws = MagicMock()
        mock_ws.worktree_path = Path("/fake/worktree")
        mock_ws.branch_name = "grim/ws-bad"
        mock_ws.repo_path = Path("/fake/repo")
        mock_ws_mgr.get = MagicMock(return_value=mock_ws)
        mock_ws_mgr.get_branch_diff = AsyncMock(return_value="+5 -0")
        mock_ws_mgr.destroy = AsyncMock()

        engine = ManagementEngine(
            config, queue, events,
            vault_path=vault_path,
            workspace_manager=mock_ws_mgr,
        )
        engine._github = mock_github

        await engine.store.initialize()
        events.subscribe(engine._event_callback)

        # Cycle + complete
        await engine._cycle()
        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        job_id = dispatched[0].job_id

        await events.emit(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id=job_id,
            data={"workspace_id": "ws-bad"},
        ))

        item = await engine.store.get_by_job(job_id)
        assert item.status == PipelineStatus.REVIEW

        # Reject
        result = await engine.reject_item(item.id)
        assert result.status == PipelineStatus.FAILED
        assert result.error == "Rejected by reviewer"
        mock_github.close_pr.assert_called_once()
        mock_ws_mgr.destroy.assert_called_once()

    @pytest.mark.asyncio
    async def test_github_unavailable_stays_in_review(self, tmp_path):
        """When GitHub is unavailable, CODE jobs stay in REVIEW (no crash)."""
        vault_path = tmp_path / "vault"
        _make_project_fdo(vault_path, "proj-alpha", [
            {
                "id": "story-alpha-013",
                "title": "Offline feature",
                "status": "active",
                "priority": "high",
                "assignee": "code",
            },
        ])

        db = tmp_path / "offline.db"
        config = _make_config(vault_path, db, auto_pr=False)
        queue = AsyncMock()
        code_job = Job(job_type=JobType.CODE, instructions="offline")
        queue.get = AsyncMock(return_value=code_job)
        events = PoolEventBus()

        engine = ManagementEngine(config, queue, events, vault_path=vault_path)
        engine._github = None  # no github

        await engine.store.initialize()
        events.subscribe(engine._event_callback)

        await engine._cycle()
        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        job_id = dispatched[0].job_id

        await events.emit(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id=job_id,
            data={"workspace_id": "ws-offline"},
        ))

        item = await engine.store.get_by_job(job_id)
        assert item.status == PipelineStatus.REVIEW  # stays, no crash

    @pytest.mark.asyncio
    async def test_comment_polling_emits_escalation(self, tmp_path):
        """PR comment polling detects new comments and emits DAEMON_ESCALATION."""
        vault_path = tmp_path / "vault"
        _make_project_fdo(vault_path, "proj-alpha", [
            {
                "id": "story-alpha-014",
                "title": "Feature with comments",
                "status": "active",
                "priority": "high",
                "assignee": "code",
            },
        ])

        db = tmp_path / "comments.db"
        config = _make_config(vault_path, db, auto_pr=True)
        queue = AsyncMock()
        code_job = Job(job_type=JobType.CODE, instructions="code")
        queue.get = AsyncMock(return_value=code_job)
        events = PoolEventBus()

        mock_github = AsyncMock()
        mock_github.push_branch = AsyncMock()
        mock_github.create_pr = AsyncMock(return_value=(77, "https://github.com/o/r/pull/77"))
        mock_github.list_pr_comments = AsyncMock(return_value=[
            {"author": "reviewer", "body": "Please fix indent", "createdAt": "2026-03-06T12:00:00Z"},
        ])

        mock_ws_mgr = MagicMock()
        mock_ws = MagicMock()
        mock_ws.worktree_path = Path("/fake/worktree")
        mock_ws.branch_name = "grim/ws-comments"
        mock_ws.repo_path = Path("/fake/repo")
        mock_ws_mgr.get = MagicMock(return_value=mock_ws)
        mock_ws_mgr.get_branch_diff = AsyncMock(return_value="+3 -1")

        events_received = []
        events.subscribe(lambda e: events_received.append(e))

        engine = ManagementEngine(
            config, queue, events,
            vault_path=vault_path,
            workspace_manager=mock_ws_mgr,
        )
        engine._github = mock_github

        await engine.store.initialize()
        events.subscribe(engine._event_callback)

        # Cycle + complete → PR created
        await engine._cycle()
        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        job_id = dispatched[0].job_id

        await events.emit(PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id=job_id,
            data={"workspace_id": "ws-comments"},
        ))

        # Poll for comments
        await engine._poll_pr_comments()
        await asyncio.sleep(0.05)

        escalations = [e for e in events_received if e.type == PoolEventType.DAEMON_ESCALATION]
        assert len(escalations) >= 1
        assert escalations[0].data["comment_author"] == "reviewer"
        assert escalations[0].data["comment_body"] == "Please fix indent"

    @pytest.mark.asyncio
    async def test_notifier_review_embed_has_pr_link(self, tmp_path):
        """JOB_REVIEW Discord embed includes PR link and story_id."""
        from core.pool.notifiers import DiscordWebhookNotifier

        notifier = DiscordWebhookNotifier(webhook_url="https://fake.discord.com/webhook")
        event = PoolEvent(
            type=PoolEventType.JOB_REVIEW,
            job_id="job-embed-test",
            data={
                "workspace_id": "ws-001",
                "story_id": "story-alpha-010",
                "pr_number": 42,
                "pr_url": "https://github.com/o/r/pull/42",
                "diff_stat": "+10 -2",
            },
        )
        embed = notifier._build_embed(event)
        assert "story-alpha-010" in embed["description"]
        assert "#42" in embed["description"]
        assert "https://github.com/o/r/pull/42" in embed["description"]
        assert embed["color"] == 0x3498DB  # Blue

    @pytest.mark.asyncio
    async def test_notifier_escalation_embed_has_comment(self, tmp_path):
        """DAEMON_ESCALATION embed for PR comments includes author + body."""
        from core.pool.notifiers import DiscordWebhookNotifier

        notifier = DiscordWebhookNotifier(webhook_url="https://fake.discord.com/webhook")
        event = PoolEvent(
            type=PoolEventType.DAEMON_ESCALATION,
            job_id="job-comment-test",
            data={
                "story_id": "story-alpha-010",
                "pr_number": 42,
                "comment_author": "peter",
                "comment_body": "LGTM",
                "reason": "New PR comment",
            },
        )
        embed = notifier._build_embed(event)
        assert "story-alpha-010" in embed["description"]
        assert "New PR comment" in embed["description"]
        assert embed["color"] == 0xE67E22  # Orange
