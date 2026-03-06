"""Unit tests for the ManagementEngine orchestration loop.

Uses real SQLite, temp vault, mocked pool queue and event bus.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from core.daemon.engine import ManagementEngine
from core.daemon.models import PipelineStatus
from core.daemon.pipeline import PipelineStore
from core.pool.events import PoolEvent, PoolEventBus, PoolEventType
from core.pool.models import Job, JobType


# ── Helpers ──────────────────────────────────────────────────────


def _make_vault(tmp_path: Path, stories: list[dict] | None = None) -> Path:
    """Create a temp vault with a single project."""
    vault = tmp_path / "vault"
    projects = vault / "projects"
    projects.mkdir(parents=True)

    if stories is None:
        stories = [
            {
                "id": "story-test-001",
                "title": "Test story",
                "status": "active",
                "priority": "high",
                "assignee": "code",
                "description": "Do the thing",
                "acceptance_criteria": ["It works"],
                "estimate_days": 1.0,
            },
        ]

    fm = {
        "id": "proj-test",
        "title": "Test Project",
        "domain": "projects",
        "status": "developing",
        "confidence": 0.7,
        "tags": ["epic"],
        "stories": stories,
    }
    fm_yaml = yaml.dump(fm, default_flow_style=False, sort_keys=False)
    (projects / "proj-test.md").write_text(
        f"---\n{fm_yaml}---\n\n# proj-test\n## Summary\nTest.",
        encoding="utf-8",
    )
    return vault


@dataclass
class MockConfig:
    daemon_enabled: bool = True
    daemon_poll_interval: float = 0.1  # fast for tests
    daemon_max_concurrent_jobs: int = 1
    daemon_project_filter: list[str] = field(default_factory=list)
    daemon_auto_dispatch: bool = True
    daemon_db_path: Path = field(default_factory=lambda: Path("test.db"))
    vault_path: Path = field(default_factory=lambda: Path("vault"))
    workspace_root: Path = field(default_factory=lambda: Path("vault").parent)
    # Phase 3 intelligence (disabled by default in unit tests)
    daemon_auto_resolve: bool = False
    daemon_validate_output: bool = False
    daemon_max_daemon_retries: int = 0
    daemon_resolve_model: str = "claude-sonnet-4-6"
    daemon_validate_model: str = "claude-opus-4-6"
    daemon_resolve_confidence_threshold: float = 0.7


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    return tmp_path / "daemon.db"


@pytest.fixture
def vault(tmp_path) -> Path:
    return _make_vault(tmp_path)


@pytest.fixture
def mock_queue() -> AsyncMock:
    q = AsyncMock()
    q.submit = AsyncMock(return_value="job-test-001")
    q.initialize = AsyncMock()
    return q


@pytest.fixture
def event_bus() -> PoolEventBus:
    return PoolEventBus()


@pytest.fixture
async def engine(tmp_path, tmp_db, vault, mock_queue, event_bus) -> ManagementEngine:
    config = MockConfig(
        daemon_db_path=tmp_db,
        vault_path=vault,
        daemon_poll_interval=999,  # effectively disable auto-loop for manual tests
    )
    eng = ManagementEngine(
        config=config,
        pool_queue=mock_queue,
        pool_events=event_bus,
        vault_path=vault,
    )
    return eng


# ── Lifecycle tests ──────────────────────────────────────────────


class TestEngineLifecycle:
    """Test start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_db(self, engine, tmp_db):
        await engine.start()
        assert tmp_db.exists()
        await engine.stop()

    @pytest.mark.asyncio
    async def test_start_subscribes_events(self, engine, event_bus):
        assert event_bus.subscriber_count == 0
        await engine.start()
        assert event_bus.subscriber_count == 1
        await engine.stop()

    @pytest.mark.asyncio
    async def test_stop_unsubscribes(self, engine, event_bus):
        await engine.start()
        assert event_bus.subscriber_count >= 1
        await engine.stop()
        assert event_bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_stop_without_start(self, engine):
        # Should not raise
        await engine.stop()


# ── Scan cycle tests ─────────────────────────────────────────────


class TestScanCycle:
    """Test vault scanning and pipeline sync."""

    @pytest.mark.asyncio
    async def test_scan_populates_pipeline(self, engine):
        await engine.start()
        await engine._scan_cycle()
        items = await engine.store.list_items()
        assert len(items) >= 1
        await engine.stop()

    @pytest.mark.asyncio
    async def test_scan_idempotent(self, engine):
        await engine.start()
        await engine._scan_cycle()
        await engine._scan_cycle()
        items = await engine.store.list_items()
        assert len(items) == 1
        await engine.stop()


# ── Promote cycle tests ──────────────────────────────────────────


class TestPromoteCycle:
    """Test BACKLOG → READY promotion."""

    @pytest.mark.asyncio
    async def test_promote_backlog_to_ready(self, engine):
        await engine.start()
        await engine._scan_cycle()
        await engine._promote_cycle()

        ready = await engine.store.list_items(status_filter=PipelineStatus.READY)
        assert len(ready) == 1
        await engine.stop()

    @pytest.mark.asyncio
    async def test_promote_only_backlog(self, engine):
        await engine.start()
        await engine._scan_cycle()
        await engine._promote_cycle()
        # Promoting again should not create duplicates
        await engine._promote_cycle()

        ready = await engine.store.list_items(status_filter=PipelineStatus.READY)
        assert len(ready) == 1
        await engine.stop()


# ── Dispatch cycle tests ─────────────────────────────────────────


class TestDispatchCycle:
    """Test READY → DISPATCHED dispatch."""

    @pytest.mark.asyncio
    async def test_dispatch_submits_job(self, engine, mock_queue):
        await engine.start()
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        # Pool queue should have been called
        mock_queue.submit.assert_called_once()
        job = mock_queue.submit.call_args[0][0]
        assert isinstance(job, Job)
        assert job.job_type == JobType.CODE

        # Pipeline item should be DISPATCHED
        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 1
        assert dispatched[0].job_id == job.id
        await engine.stop()

    @pytest.mark.asyncio
    async def test_dispatch_respects_concurrency(self, tmp_path, tmp_db, mock_queue, event_bus):
        """With max_concurrent=1 and 2 READY items, only 1 should dispatch."""
        vault = _make_vault(tmp_path, stories=[
            {
                "id": "story-test-001",
                "title": "Story 1",
                "status": "active",
                "priority": "high",
                "assignee": "code",
            },
            {
                "id": "story-test-002",
                "title": "Story 2",
                "status": "active",
                "priority": "medium",
                "assignee": "code",
            },
        ])
        config = MockConfig(
            daemon_db_path=tmp_db,
            vault_path=vault,
            daemon_max_concurrent_jobs=1,
            daemon_poll_interval=999,
        )
        engine = ManagementEngine(config, mock_queue, event_bus, vault_path=vault)
        await engine.start()
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 1
        # The higher priority one should dispatch first
        assert dispatched[0].priority == 1  # high
        await engine.stop()

    @pytest.mark.asyncio
    async def test_dispatch_higher_concurrency(self, tmp_path, mock_queue, event_bus):
        """With max_concurrent=3 and 2 READY items, both should dispatch."""
        vault = _make_vault(tmp_path, stories=[
            {
                "id": "story-test-001",
                "title": "Story 1",
                "status": "active",
                "priority": "high",
                "assignee": "code",
            },
            {
                "id": "story-test-002",
                "title": "Story 2",
                "status": "active",
                "priority": "medium",
                "assignee": "research",
            },
        ])
        db = tmp_path / "daemon2.db"
        config = MockConfig(
            daemon_db_path=db,
            vault_path=vault,
            daemon_max_concurrent_jobs=3,
            daemon_poll_interval=999,
        )
        engine = ManagementEngine(config, mock_queue, event_bus, vault_path=vault)
        await engine.start()
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 2
        await engine.stop()

    @pytest.mark.asyncio
    async def test_dispatch_no_ready(self, engine, mock_queue):
        """No READY items = no dispatch."""
        await engine.start()
        await engine._dispatch_cycle()
        mock_queue.submit.assert_not_called()
        await engine.stop()

    @pytest.mark.asyncio
    async def test_dispatch_records_health(self, engine, mock_queue):
        await engine.start()
        await engine._scan_cycle()
        await engine._promote_cycle()
        await engine._dispatch_cycle()

        assert engine.health.dispatch_count == 1
        assert engine.health.last_dispatch_at is not None
        await engine.stop()


# ── Instructions building ────────────────────────────────────────


class TestBuildInstructions:
    """Test instruction generation from story data."""

    async def _setup_ready_item(self, engine):
        """Initialize store, scan, promote — without starting main loop."""
        await engine.store.initialize()
        await engine._scan_cycle()
        await engine._promote_cycle()
        return await engine.store.next_ready()

    @pytest.mark.asyncio
    async def test_instructions_include_story_id(self, engine):
        item = await self._setup_ready_item(engine)
        assert item is not None
        instructions = engine._build_instructions(item)
        assert "story-test-001" in instructions

    @pytest.mark.asyncio
    async def test_instructions_include_title(self, engine):
        item = await self._setup_ready_item(engine)
        assert item is not None
        instructions = engine._build_instructions(item)
        assert "Test story" in instructions

    @pytest.mark.asyncio
    async def test_instructions_include_acceptance_criteria(self, engine):
        item = await self._setup_ready_item(engine)
        assert item is not None
        instructions = engine._build_instructions(item)
        assert "It works" in instructions


# ── Event handler tests ──────────────────────────────────────────


class TestPoolEventHandler:
    """Test PoolEventBus event handling."""

    async def _setup_dispatched(self, engine, event_bus):
        """Initialize, run one cycle, subscribe to events."""
        await engine.store.initialize()
        event_bus.subscribe(engine._event_callback)
        await engine._cycle()
        dispatched = await engine.store.list_items(status_filter=PipelineStatus.DISPATCHED)
        assert len(dispatched) == 1
        return dispatched[0].job_id

    @pytest.mark.asyncio
    async def test_job_complete_to_review(self, engine, event_bus, mock_queue):
        job_id = await self._setup_dispatched(engine, event_bus)

        event = PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id=job_id,
            data={"workspace_id": "ws-001"},
        )
        await event_bus.emit(event)

        item = await engine.store.get_by_job(job_id)
        assert item is not None
        assert item.status == PipelineStatus.REVIEW
        assert item.workspace_id == "ws-001"

    @pytest.mark.asyncio
    async def test_job_failed_to_failed(self, engine, event_bus, mock_queue):
        job_id = await self._setup_dispatched(engine, event_bus)

        event = PoolEvent(
            type=PoolEventType.JOB_FAILED,
            job_id=job_id,
            data={"error": "agent crashed"},
        )
        await event_bus.emit(event)

        item = await engine.store.get_by_job(job_id)
        assert item is not None
        assert item.status == PipelineStatus.FAILED
        assert item.error == "agent crashed"

    @pytest.mark.asyncio
    async def test_job_blocked_to_blocked(self, engine, event_bus, mock_queue):
        job_id = await self._setup_dispatched(engine, event_bus)

        event = PoolEvent(
            type=PoolEventType.JOB_BLOCKED,
            job_id=job_id,
            data={},
        )
        await event_bus.emit(event)

        item = await engine.store.get_by_job(job_id)
        assert item is not None
        assert item.status == PipelineStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_ignores_unmanaged_jobs(self, engine, event_bus):
        """Events for jobs not in the pipeline should be ignored."""
        await engine.store.initialize()
        event_bus.subscribe(engine._event_callback)

        event = PoolEvent(
            type=PoolEventType.JOB_COMPLETE,
            job_id="job-unknown",
            data={},
        )
        # Should not raise
        await event_bus.emit(event)


# ── Full cycle tests ─────────────────────────────────────────────


class TestFullCycle:
    """Test the complete scan → promote → dispatch cycle."""

    @pytest.mark.asyncio
    async def test_single_cycle(self, engine, mock_queue):
        await engine.store.initialize()
        await engine._cycle()

        assert engine.health.scan_count == 1
        assert engine.health.dispatch_count == 1
        mock_queue.submit.assert_called_once()

    @pytest.mark.asyncio
    async def test_cycle_without_auto_dispatch(self, tmp_path, mock_queue, event_bus):
        """With auto_dispatch=False, BACKLOG items stay BACKLOG."""
        vault = _make_vault(tmp_path)
        db = tmp_path / "no_auto.db"
        config = MockConfig(
            daemon_db_path=db,
            vault_path=vault,
            daemon_auto_dispatch=False,
            daemon_poll_interval=999,
        )
        engine = ManagementEngine(config, mock_queue, event_bus, vault_path=vault)
        await engine.store.initialize()
        await engine._cycle()

        backlog = await engine.store.list_items(status_filter=PipelineStatus.BACKLOG)
        assert len(backlog) == 1
        mock_queue.submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_main_loop_runs(self, tmp_path, mock_queue, event_bus):
        """Verify the main loop executes at least one cycle."""
        vault = _make_vault(tmp_path)
        db = tmp_path / "loop.db"
        config = MockConfig(
            daemon_db_path=db,
            vault_path=vault,
            daemon_poll_interval=0.1,  # fast for this test
        )
        engine = ManagementEngine(config, mock_queue, event_bus, vault_path=vault)
        await engine.start()
        # Wait for initial delay (min(0.1, 5.0)=0.1) + at least 1 cycle
        await asyncio.sleep(0.5)
        await engine.stop()

        assert engine.health.scan_count >= 1

    @pytest.mark.asyncio
    async def test_assignee_maps_to_job_type(self, tmp_path, mock_queue, event_bus):
        """Research assignee → RESEARCH job type."""
        vault = _make_vault(tmp_path, stories=[
            {
                "id": "story-test-001",
                "title": "Research task",
                "status": "active",
                "priority": "medium",
                "assignee": "research",
            },
        ])
        db = tmp_path / "assignee.db"
        config = MockConfig(daemon_db_path=db, vault_path=vault, daemon_poll_interval=999)
        engine = ManagementEngine(config, mock_queue, event_bus, vault_path=vault)
        await engine.store.initialize()
        await engine._cycle()

        job = mock_queue.submit.call_args[0][0]
        assert job.job_type == JobType.RESEARCH
