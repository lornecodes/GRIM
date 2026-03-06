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
    cfg.daemon_poll_interval = 999  # prevent auto-loop
    cfg.daemon_max_concurrent_jobs = overrides.get("max_concurrent", 1)
    cfg.daemon_auto_dispatch = overrides.get("auto_dispatch", True)
    cfg.daemon_db_path = db_path
    cfg.daemon_project_filter = overrides.get("project_filter", [])
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
