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
    # Phase 4 PR lifecycle (disabled by default in unit tests)
    daemon_auto_pr: bool = False
    daemon_github_repo: str = ""
    daemon_pr_poll_interval: int = 999


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
        # Mock queue returns a CODE job so _handle_review stays in REVIEW
        # (auto_pr=False and github=None → stays in REVIEW for manual handling)
        code_job = Job(job_type=JobType.CODE, instructions="code task")
        mock_queue.get = AsyncMock(return_value=code_job)

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


# ── Phase 4: PR Lifecycle tests ───────────────────────────────


def _make_review_engine(
    tmp_path: Path,
    mock_queue: AsyncMock,
    event_bus: PoolEventBus,
    *,
    auto_pr: bool = True,
    github_client: Any = None,
    workspace_manager: Any = None,
    validate_output: bool = False,
    stories: list[dict] | None = None,
) -> ManagementEngine:
    """Create an engine configured for Phase 4 review tests."""
    vault = _make_vault(tmp_path, stories=stories)
    db = tmp_path / "review.db"
    config = MockConfig(
        daemon_db_path=db,
        vault_path=vault,
        daemon_poll_interval=999,
        daemon_auto_pr=auto_pr,
        daemon_validate_output=validate_output,
    )
    engine = ManagementEngine(
        config, mock_queue, event_bus,
        vault_path=vault,
        workspace_manager=workspace_manager,
    )
    # Inject mock github client
    if github_client is not None:
        engine._github = github_client
    elif auto_pr:
        engine._github = AsyncMock()
    return engine


class TestHandleReview:
    """Tests for _handle_review() — PR creation vs direct merge."""

    @pytest.mark.asyncio
    async def test_non_code_job_skips_pr(self, tmp_path, event_bus):
        """RESEARCH jobs advance directly to MERGED, no PR."""
        mock_queue = AsyncMock()
        research_job = Job(job_type=JobType.RESEARCH, instructions="research")
        mock_queue.get = AsyncMock(return_value=research_job)

        engine = _make_review_engine(tmp_path, mock_queue, event_bus)
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(item.id, PipelineStatus.REVIEW, workspace_id="ws-1")
        item = await engine.store.get(item.id)  # refetch with job_id

        await engine._handle_review(item, "ws-1")
        updated = await engine.store.get(item.id)
        assert updated.status == PipelineStatus.MERGED

    @pytest.mark.asyncio
    async def test_code_job_no_workspace_skips_pr(self, tmp_path, event_bus):
        """CODE jobs without workspace advance directly to MERGED."""
        mock_queue = AsyncMock()
        code_job = Job(job_type=JobType.CODE, instructions="code")
        mock_queue.get = AsyncMock(return_value=code_job)

        engine = _make_review_engine(tmp_path, mock_queue, event_bus)
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(item.id, PipelineStatus.REVIEW)
        item = await engine.store.get(item.id)

        await engine._handle_review(item, None)
        updated = await engine.store.get(item.id)
        assert updated.status == PipelineStatus.MERGED

    @pytest.mark.asyncio
    async def test_code_job_creates_pr(self, tmp_path, event_bus):
        """CODE job with workspace creates PR and emits JOB_REVIEW event."""
        mock_queue = AsyncMock()
        code_job = Job(job_type=JobType.CODE, instructions="code")
        mock_queue.get = AsyncMock(return_value=code_job)

        mock_github = AsyncMock()
        mock_github.push_branch = AsyncMock()
        mock_github.create_pr = AsyncMock(return_value=(42, "https://github.com/o/r/pull/42"))

        mock_ws_mgr = MagicMock()
        mock_ws = MagicMock()
        mock_ws.worktree_path = Path("/fake/worktree")
        mock_ws.branch_name = "grim/ws-abc"
        mock_ws.repo_path = Path("/fake/repo")
        mock_ws_mgr.get = MagicMock(return_value=mock_ws)
        mock_ws_mgr.get_branch_diff = AsyncMock(return_value="+1 -0")

        events_received = []
        event_bus.subscribe(lambda e: events_received.append(e))

        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus,
            github_client=mock_github,
            workspace_manager=mock_ws_mgr,
        )
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(item.id, PipelineStatus.REVIEW, workspace_id="ws-abc")
        item = await engine.store.get(item.id)

        await engine._handle_review(item, "ws-abc")

        # PR was created
        mock_github.push_branch.assert_called_once()
        mock_github.create_pr.assert_called_once()

        # PR info persisted
        updated = await engine.store.get(item.id)
        assert updated.pr_number == 42
        assert updated.pr_url == "https://github.com/o/r/pull/42"

        # JOB_REVIEW event emitted
        await asyncio.sleep(0.05)
        review_events = [e for e in events_received if e.type == PoolEventType.JOB_REVIEW]
        assert len(review_events) == 1
        assert review_events[0].data["pr_number"] == 42

    @pytest.mark.asyncio
    async def test_auto_pr_disabled_stays_in_review(self, tmp_path, event_bus):
        """With auto_pr=False, CODE jobs stay in REVIEW."""
        mock_queue = AsyncMock()
        code_job = Job(job_type=JobType.CODE, instructions="code")
        mock_queue.get = AsyncMock(return_value=code_job)

        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus, auto_pr=False,
        )
        engine._github = None  # ensure no github client
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(item.id, PipelineStatus.REVIEW, workspace_id="ws-1")
        item = await engine.store.get(item.id)

        await engine._handle_review(item, "ws-1")

        # Still in REVIEW — no PR, no auto-merge
        updated = await engine.store.get(item.id)
        assert updated.status == PipelineStatus.REVIEW

    @pytest.mark.asyncio
    async def test_pr_creation_failure_stays_in_review(self, tmp_path, event_bus):
        """PR creation failure leaves item in REVIEW (doesn't crash)."""
        mock_queue = AsyncMock()
        code_job = Job(job_type=JobType.CODE, instructions="code")
        mock_queue.get = AsyncMock(return_value=code_job)

        mock_github = AsyncMock()
        mock_github.push_branch = AsyncMock(side_effect=Exception("push failed"))

        mock_ws_mgr = MagicMock()
        mock_ws = MagicMock()
        mock_ws.worktree_path = Path("/fake/worktree")
        mock_ws.branch_name = "grim/ws-abc"
        mock_ws_mgr.get = MagicMock(return_value=mock_ws)

        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus,
            github_client=mock_github,
            workspace_manager=mock_ws_mgr,
        )
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(item.id, PipelineStatus.REVIEW, workspace_id="ws-1")
        item = await engine.store.get(item.id)

        await engine._handle_review(item, "ws-1")

        updated = await engine.store.get(item.id)
        assert updated.status == PipelineStatus.REVIEW  # didn't crash, stayed

    @pytest.mark.asyncio
    async def test_handle_complete_wires_to_handle_review(self, tmp_path, event_bus):
        """_handle_complete calls _handle_review for non-validated jobs."""
        mock_queue = AsyncMock()
        research_job = Job(job_type=JobType.RESEARCH, instructions="research")
        mock_queue.get = AsyncMock(return_value=research_job)

        engine = _make_review_engine(tmp_path, mock_queue, event_bus)
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")

        # Simulate JOB_COMPLETE event
        await engine._handle_complete(item, "job-1", {"workspace_id": "ws-1"})

        # Non-validated research job should go REVIEW → MERGED
        updated = await engine.store.get(item.id)
        assert updated.status == PipelineStatus.MERGED


class TestApproveReject:
    """Tests for approve_item() and reject_item()."""

    @pytest.mark.asyncio
    async def test_approve_merges_pr_and_workspace(self, tmp_path, event_bus):
        """Approve merges PR, merges workspace, advances to MERGED."""
        mock_queue = AsyncMock()
        mock_github = AsyncMock()
        mock_github.merge_pr = AsyncMock()

        mock_ws_mgr = MagicMock()
        mock_ws = MagicMock()
        mock_ws.repo_path = Path("/fake/repo")
        mock_ws_mgr.get = MagicMock(return_value=mock_ws)
        mock_ws_mgr.merge_to_base = AsyncMock()
        mock_ws_mgr.destroy = AsyncMock()

        events_received = []
        event_bus.subscribe(lambda e: events_received.append(e))

        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus,
            github_client=mock_github,
            workspace_manager=mock_ws_mgr,
        )
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(
            item.id, PipelineStatus.REVIEW,
            workspace_id="ws-1", pr_number=42, pr_url="https://github.com/o/r/pull/42",
        )

        result = await engine.approve_item(item.id)
        assert result.status == PipelineStatus.MERGED
        mock_github.merge_pr.assert_called_once()
        mock_ws_mgr.merge_to_base.assert_called_once_with("ws-1")
        mock_ws_mgr.destroy.assert_called_once_with("ws-1")

        # DAEMON_APPROVED event emitted
        await asyncio.sleep(0.05)
        approved = [e for e in events_received if e.type == PoolEventType.DAEMON_APPROVED]
        assert len(approved) == 1
        assert approved[0].data["story_id"] == "story-test-001"

    @pytest.mark.asyncio
    async def test_approve_without_pr(self, tmp_path, event_bus):
        """Approve without PR just merges workspace + cleans up."""
        mock_queue = AsyncMock()
        mock_ws_mgr = MagicMock()
        mock_ws_mgr.merge_to_base = AsyncMock()
        mock_ws_mgr.destroy = AsyncMock()

        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus,
            workspace_manager=mock_ws_mgr,
        )
        engine._github = None
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(item.id, PipelineStatus.REVIEW, workspace_id="ws-1")

        result = await engine.approve_item(item.id)
        assert result.status == PipelineStatus.MERGED
        mock_ws_mgr.merge_to_base.assert_called_once()
        mock_ws_mgr.destroy.assert_called_once_with("ws-1")

    @pytest.mark.asyncio
    async def test_approve_wrong_status_raises(self, tmp_path, event_bus):
        """Approve on non-REVIEW item raises InvalidTransition."""
        mock_queue = AsyncMock()
        engine = _make_review_engine(tmp_path, mock_queue, event_bus)
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)

        from core.daemon.models import InvalidTransition
        with pytest.raises(InvalidTransition):
            await engine.approve_item(item.id)

    @pytest.mark.asyncio
    async def test_approve_not_found_raises(self, tmp_path, event_bus):
        """Approve on nonexistent item raises ValueError."""
        mock_queue = AsyncMock()
        engine = _make_review_engine(tmp_path, mock_queue, event_bus)
        await engine.store.initialize()

        with pytest.raises(ValueError, match="not found"):
            await engine.approve_item("pipeline-nonexistent")

    @pytest.mark.asyncio
    async def test_reject_closes_pr_and_destroys_workspace(self, tmp_path, event_bus):
        """Reject closes PR, destroys workspace, advances to FAILED, emits event."""
        mock_queue = AsyncMock()
        mock_github = AsyncMock()
        mock_github.close_pr = AsyncMock()

        mock_ws_mgr = MagicMock()
        mock_ws = MagicMock()
        mock_ws.repo_path = Path("/fake/repo")
        mock_ws_mgr.get = MagicMock(return_value=mock_ws)
        mock_ws_mgr.destroy = AsyncMock()

        events_received = []
        event_bus.subscribe(lambda e: events_received.append(e))

        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus,
            github_client=mock_github,
            workspace_manager=mock_ws_mgr,
        )
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(
            item.id, PipelineStatus.REVIEW,
            workspace_id="ws-1", pr_number=42,
        )

        result = await engine.reject_item(item.id)
        assert result.status == PipelineStatus.FAILED
        assert result.error == "Rejected by reviewer"
        mock_github.close_pr.assert_called_once()
        mock_ws_mgr.destroy.assert_called_once_with("ws-1")

        # DAEMON_REJECTED event emitted
        await asyncio.sleep(0.05)
        rejected = [e for e in events_received if e.type == PoolEventType.DAEMON_REJECTED]
        assert len(rejected) == 1
        assert rejected[0].data["story_id"] == "story-test-001"

    @pytest.mark.asyncio
    async def test_reject_wrong_status_raises(self, tmp_path, event_bus):
        """Reject on non-REVIEW item raises InvalidTransition."""
        mock_queue = AsyncMock()
        engine = _make_review_engine(tmp_path, mock_queue, event_bus)
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")

        from core.daemon.models import InvalidTransition
        with pytest.raises(InvalidTransition):
            await engine.reject_item(item.id)


class TestPRCommentPolling:
    """Tests for _poll_pr_comments()."""

    @pytest.mark.asyncio
    async def test_new_comments_emit_escalation(self, tmp_path, event_bus):
        """New PR comments are detected and emitted as escalation events."""
        mock_queue = AsyncMock()

        mock_github = AsyncMock()
        mock_github.list_pr_comments = AsyncMock(return_value=[
            {"author": "peter", "body": "Looks good", "createdAt": "2026-03-06T10:00:00Z"},
        ])

        mock_ws_mgr = MagicMock()
        mock_ws = MagicMock()
        mock_ws.repo_path = Path("/fake/repo")
        mock_ws_mgr.get = MagicMock(return_value=mock_ws)

        events_received = []
        event_bus.subscribe(lambda e: events_received.append(e))

        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus,
            github_client=mock_github,
            workspace_manager=mock_ws_mgr,
        )
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(
            item.id, PipelineStatus.REVIEW,
            workspace_id="ws-1", pr_number=42,
        )

        await engine._poll_pr_comments()
        await asyncio.sleep(0.05)

        escalations = [e for e in events_received if e.type == PoolEventType.DAEMON_ESCALATION]
        assert len(escalations) == 1
        assert escalations[0].data["comment_author"] == "peter"
        assert escalations[0].data["comment_body"] == "Looks good"

        # Count updated
        updated = await engine.store.get(item.id)
        assert updated.pr_comment_count == 1

    @pytest.mark.asyncio
    async def test_no_new_comments_no_event(self, tmp_path, event_bus):
        """No new comments → no escalation emitted."""
        mock_queue = AsyncMock()
        mock_github = AsyncMock()
        mock_github.list_pr_comments = AsyncMock(return_value=[])

        mock_ws_mgr = MagicMock()
        mock_ws = MagicMock()
        mock_ws.repo_path = Path("/fake/repo")
        mock_ws_mgr.get = MagicMock(return_value=mock_ws)

        events_received = []
        event_bus.subscribe(lambda e: events_received.append(e))

        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus,
            github_client=mock_github,
            workspace_manager=mock_ws_mgr,
        )
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(
            item.id, PipelineStatus.REVIEW,
            workspace_id="ws-1", pr_number=42,
        )

        await engine._poll_pr_comments()
        await asyncio.sleep(0.05)

        escalations = [e for e in events_received if e.type == PoolEventType.DAEMON_ESCALATION]
        assert len(escalations) == 0

    @pytest.mark.asyncio
    async def test_poll_skips_items_without_pr(self, tmp_path, event_bus):
        """Items without pr_number are skipped during poll."""
        mock_queue = AsyncMock()
        mock_github = AsyncMock()
        mock_github.list_pr_comments = AsyncMock()

        mock_ws_mgr = MagicMock()

        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus,
            github_client=mock_github,
            workspace_manager=mock_ws_mgr,
        )
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(item.id, PipelineStatus.REVIEW, workspace_id="ws-1")

        await engine._poll_pr_comments()

        # list_pr_comments should NOT have been called
        mock_github.list_pr_comments.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_error_doesnt_crash(self, tmp_path, event_bus):
        """Poll errors are caught and logged, not raised."""
        mock_queue = AsyncMock()
        mock_github = AsyncMock()
        mock_github.list_pr_comments = AsyncMock(side_effect=Exception("API error"))

        mock_ws_mgr = MagicMock()
        mock_ws = MagicMock()
        mock_ws.repo_path = Path("/fake/repo")
        mock_ws_mgr.get = MagicMock(return_value=mock_ws)

        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus,
            github_client=mock_github,
            workspace_manager=mock_ws_mgr,
        )
        await engine.store.initialize()

        item = await engine.store.add("story-test-001", "proj-test")
        await engine.store.advance(item.id, PipelineStatus.READY)
        await engine.store.advance(item.id, PipelineStatus.DISPATCHED, job_id="job-1")
        await engine.store.advance(
            item.id, PipelineStatus.REVIEW,
            workspace_id="ws-1", pr_number=42,
        )

        # Should not raise
        await engine._poll_pr_comments()


class TestBuildPRBody:
    """Tests for _build_pr_body()."""

    def test_body_includes_story_and_project(self, tmp_path, event_bus):
        mock_queue = AsyncMock()
        engine = _make_review_engine(tmp_path, mock_queue, event_bus)
        item = MagicMock(story_id="story-test-001", project_id="proj-test")
        body = engine._build_pr_body(item, None)
        assert "story-test-001" in body
        assert "proj-test" in body

    def test_body_includes_acceptance_criteria(self, tmp_path, event_bus):
        mock_queue = AsyncMock()
        engine = _make_review_engine(tmp_path, mock_queue, event_bus)
        item = MagicMock(story_id="story-test-001", project_id="proj-test")
        story_data = {
            "title": "Test",
            "description": "A description",
            "acceptance_criteria": ["criterion 1", "criterion 2"],
        }
        body = engine._build_pr_body(item, story_data)
        assert "- [ ] criterion 1" in body
        assert "- [ ] criterion 2" in body
        assert "A description" in body


class TestPRPollLoop:
    """Tests for _pr_poll_loop lifecycle."""

    @pytest.mark.asyncio
    async def test_pr_poll_task_starts_with_github(self, tmp_path, event_bus):
        """PR poll task is started when github client exists."""
        mock_queue = AsyncMock()
        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus, auto_pr=True,
        )
        await engine.start()

        assert engine._pr_poll_task is not None
        assert not engine._pr_poll_task.done()

        await engine.stop()

    @pytest.mark.asyncio
    async def test_pr_poll_task_not_started_without_github(self, tmp_path, event_bus):
        """PR poll task is not started when no github client."""
        mock_queue = AsyncMock()
        engine = _make_review_engine(
            tmp_path, mock_queue, event_bus, auto_pr=False,
        )
        engine._github = None
        await engine.start()

        assert engine._pr_poll_task is None

        await engine.stop()
