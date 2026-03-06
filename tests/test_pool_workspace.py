"""Tests for workspace success path, REST endpoints, and workspace operations."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from core.pool.events import PoolEventType
from core.pool.workspace import WorkspaceManager, Workspace


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def ws_mgr(tmp_path):
    """WorkspaceManager with temp base dir."""
    return WorkspaceManager(base_dir=tmp_path / "worktrees")


@pytest.fixture
def sample_workspace(tmp_path):
    """A pre-built Workspace for testing."""
    ws = Workspace(
        id="workspace-abc12345",
        job_id="job-abc12345678",
        repo_path=tmp_path / "repo",
        worktree_path=tmp_path / "worktrees" / "workspace-abc12345",
        branch_name="grim/workspace-abc12345",
    )
    (tmp_path / "worktrees" / "workspace-abc12345").mkdir(parents=True)
    return ws


# ── get_branch_diff ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_branch_diff_unknown_workspace(ws_mgr):
    result = await ws_mgr.get_branch_diff("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_branch_diff_uses_origin_base(ws_mgr, sample_workspace):
    ws_mgr._workspaces[sample_workspace.id] = sample_workspace
    with patch("core.pool.workspace._run_git", new_callable=AsyncMock) as mock_git:
        mock_git.return_value = " 2 files changed, 10 insertions(+)"
        result = await ws_mgr.get_branch_diff(sample_workspace.id)
        assert result == " 2 files changed, 10 insertions(+)"
        mock_git.assert_called_once_with(
            sample_workspace.worktree_path,
            "diff", "origin/main...HEAD", "--stat",
        )


@pytest.mark.asyncio
async def test_get_branch_diff_custom_base(ws_mgr, sample_workspace):
    ws_mgr._workspaces[sample_workspace.id] = sample_workspace
    with patch("core.pool.workspace._run_git", new_callable=AsyncMock) as mock_git:
        mock_git.return_value = "stats"
        await ws_mgr.get_branch_diff(sample_workspace.id, base_branch="develop")
        mock_git.assert_called_once_with(
            sample_workspace.worktree_path,
            "diff", "origin/develop...HEAD", "--stat",
        )


@pytest.mark.asyncio
async def test_get_branch_diff_git_error_returns_none(ws_mgr, sample_workspace):
    ws_mgr._workspaces[sample_workspace.id] = sample_workspace
    with patch("core.pool.workspace._run_git", new_callable=AsyncMock) as mock_git:
        mock_git.side_effect = RuntimeError("not a git repo")
        result = await ws_mgr.get_branch_diff(sample_workspace.id)
        assert result is None


# ── get_full_diff ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_full_diff_returns_unified(ws_mgr, sample_workspace):
    ws_mgr._workspaces[sample_workspace.id] = sample_workspace
    diff_text = "diff --git a/foo.py b/foo.py\n+new line"
    with patch("core.pool.workspace._run_git", new_callable=AsyncMock) as mock_git:
        mock_git.return_value = diff_text
        result = await ws_mgr.get_full_diff(sample_workspace.id)
        assert result == diff_text
        mock_git.assert_called_once_with(
            sample_workspace.worktree_path,
            "diff", "origin/main...HEAD",
        )


@pytest.mark.asyncio
async def test_get_full_diff_unknown_workspace(ws_mgr):
    assert await ws_mgr.get_full_diff("nope") is None


@pytest.mark.asyncio
async def test_get_full_diff_git_error(ws_mgr, sample_workspace):
    ws_mgr._workspaces[sample_workspace.id] = sample_workspace
    with patch("core.pool.workspace._run_git", new_callable=AsyncMock) as mock_git:
        mock_git.side_effect = RuntimeError("err")
        assert await ws_mgr.get_full_diff(sample_workspace.id) is None


# ── list_changed_files ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_changed_files(ws_mgr, sample_workspace):
    ws_mgr._workspaces[sample_workspace.id] = sample_workspace
    with patch("core.pool.workspace._run_git", new_callable=AsyncMock) as mock_git:
        mock_git.return_value = "core/pool/pool.py\ntests/test_pool.py"
        result = await ws_mgr.list_changed_files(sample_workspace.id)
        assert result == ["core/pool/pool.py", "tests/test_pool.py"]


@pytest.mark.asyncio
async def test_list_changed_files_empty(ws_mgr, sample_workspace):
    ws_mgr._workspaces[sample_workspace.id] = sample_workspace
    with patch("core.pool.workspace._run_git", new_callable=AsyncMock) as mock_git:
        mock_git.return_value = ""
        result = await ws_mgr.list_changed_files(sample_workspace.id)
        assert result == []


@pytest.mark.asyncio
async def test_list_changed_files_unknown(ws_mgr):
    assert await ws_mgr.list_changed_files("nope") is None


@pytest.mark.asyncio
async def test_list_changed_files_git_error(ws_mgr, sample_workspace):
    ws_mgr._workspaces[sample_workspace.id] = sample_workspace
    with patch("core.pool.workspace._run_git", new_callable=AsyncMock) as mock_git:
        mock_git.side_effect = RuntimeError("error")
        assert await ws_mgr.list_changed_files(sample_workspace.id) is None


# ── merge_to_base ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_merge_to_base_success(ws_mgr, sample_workspace):
    ws_mgr._workspaces[sample_workspace.id] = sample_workspace
    with patch("core.pool.workspace._run_git", new_callable=AsyncMock) as mock_git:
        mock_git.return_value = ""
        result = await ws_mgr.merge_to_base(sample_workspace.id)
        assert result is True
        # Should: checkout main, merge --squash, commit, then destroy
        calls = mock_git.call_args_list
        assert any("checkout" in str(c) and "main" in str(c) for c in calls)
        assert any("merge" in str(c) and "--squash" in str(c) for c in calls)
        assert any("commit" in str(c) for c in calls)


@pytest.mark.asyncio
async def test_merge_to_base_unknown_workspace(ws_mgr):
    assert await ws_mgr.merge_to_base("nope") is False


@pytest.mark.asyncio
async def test_merge_to_base_failure_aborts(ws_mgr, sample_workspace):
    ws_mgr._workspaces[sample_workspace.id] = sample_workspace
    with patch("core.pool.workspace._run_git", new_callable=AsyncMock) as mock_git:
        mock_git.side_effect = RuntimeError("merge conflict")
        result = await ws_mgr.merge_to_base(sample_workspace.id)
        assert result is False


# ── Pool success branch ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_job_success_emits_diff_data():
    """Pool _run_job success path collects diff data and emits JOB_REVIEW."""
    from core.pool.pool import ExecutionPool
    from core.pool.queue import JobQueue
    from core.pool.models import Job, JobType, JobResult

    config = MagicMock()
    config.workspace_root = "/tmp/ws"
    config.pool_num_slots = 1
    config.pool_max_turns_per_job = 5
    config.pool_poll_interval = 1.0
    config.pool_job_timeout_secs = 60
    config.kronos_mcp_command = ""
    config.vault_path = None
    config.skills_path = None
    config.repos_manifest = "repos.yaml"

    queue = MagicMock(spec=JobQueue)
    queue.update_status = AsyncMock()
    pool = ExecutionPool(queue, config)

    # Track emitted events
    emitted = []
    pool.events.subscribe(lambda e: emitted.append(e) or asyncio.sleep(0))

    # Create a mock slot
    slot = MagicMock()
    slot.slot_id = "slot-0"
    slot.busy = False
    slot.current_job_id = None
    slot.cwd = None
    slot.execute = AsyncMock(return_value=JobResult(
        job_id="test-job",
        success=True,
        result="All done",
        cost_usd=0.05,
        num_turns=3,
    ))

    # Create job
    job = Job(job_type=JobType.CODE, instructions="Fix the bug")

    # Mock workspace manager
    pool._workspace_mgr = MagicMock()
    pool._workspace_mgr.get_branch_diff = AsyncMock(return_value="2 files changed")
    pool._workspace_mgr.list_changed_files = AsyncMock(return_value=["foo.py"])
    pool._workspace_mgr.get = MagicMock(return_value=MagicMock(status="active"))
    pool._workspace_mgr.create = AsyncMock(return_value=MagicMock(
        id="workspace-test", worktree_path=Path("/tmp/ws/worktree"),
    ))
    pool._workspace_root = Path("/tmp/ws")

    await pool._run_job(slot, job)

    # Should have emitted JOB_COMPLETE and JOB_REVIEW
    event_types = [e.type for e in emitted]
    assert PoolEventType.JOB_COMPLETE in event_types

    # JOB_COMPLETE should have diff data
    complete_event = next(e for e in emitted if e.type == PoolEventType.JOB_COMPLETE)
    assert complete_event.data.get("diff_stat") == "2 files changed"
    assert complete_event.data.get("changed_files") == ["foo.py"]

    # JOB_REVIEW should have been emitted
    assert PoolEventType.JOB_REVIEW in event_types
    review_event = next(e for e in emitted if e.type == PoolEventType.JOB_REVIEW)
    assert review_event.data.get("workspace_id") is not None


# ── Pool workspace_manager property ──────────────────────────────


def test_pool_workspace_manager_property():
    from core.pool.pool import ExecutionPool
    from core.pool.queue import JobQueue
    from core.pool.workspace import WorkspaceManager

    config = MagicMock()
    config.workspace_root = "/tmp/ws"
    config.pool_num_slots = 1
    config.pool_max_turns_per_job = 5
    config.pool_poll_interval = 1.0
    config.pool_job_timeout_secs = 60
    config.kronos_mcp_command = ""
    config.vault_path = None
    config.skills_path = None
    config.repos_manifest = "repos.yaml"

    queue = MagicMock(spec=JobQueue)
    pool = ExecutionPool(queue, config)
    assert pool.workspace_manager is not None
    assert isinstance(pool.workspace_manager, WorkspaceManager)


def test_pool_workspace_manager_none_without_workspace():
    from core.pool.pool import ExecutionPool
    from core.pool.queue import JobQueue

    config = MagicMock()
    config.workspace_root = None
    config.pool_num_slots = 1
    config.pool_max_turns_per_job = 5
    config.pool_poll_interval = 1.0
    config.pool_job_timeout_secs = 60
    config.kronos_mcp_command = ""
    config.vault_path = None
    config.skills_path = None

    queue = MagicMock(spec=JobQueue)
    pool = ExecutionPool(queue, config)
    assert pool.workspace_manager is None


# ── JOB_REVIEW event type ───────────────────────────────────────


def test_job_review_event_type():
    assert PoolEventType.JOB_REVIEW.value == "job_review"


def test_all_event_types():
    """Ensure all expected event types exist."""
    expected = {
        "job_submitted", "job_started", "job_complete",
        "job_failed", "job_blocked", "job_cancelled", "job_review",
        "agent_output", "agent_tool_result",  # Phase 9 streaming events
        "daemon_escalation", "daemon_auto_resolved",  # Mewtwo Phase 3
        "daemon_approved", "daemon_rejected",  # Mewtwo Phase 4
    }
    actual = {e.value for e in PoolEventType}
    assert expected == actual


# ── REST endpoint smoke tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_workspace_endpoints_503_when_pool_disabled():
    """All workspace endpoints return 503 when pool is disabled."""
    from httpx import ASGITransport, AsyncClient
    from server.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/pool/workspaces")
        assert resp.status_code == 503

        resp = await client.get("/api/pool/workspaces/ws-1/diff")
        assert resp.status_code == 503

        resp = await client.post("/api/pool/workspaces/ws-1/merge", json={})
        assert resp.status_code == 503

        resp = await client.delete("/api/pool/workspaces/ws-1")
        assert resp.status_code == 503

        resp = await client.post("/api/pool/jobs/j1/retry")
        assert resp.status_code == 503

        resp = await client.post("/api/pool/jobs/j1/review", json={"action": "approve"})
        assert resp.status_code == 503


# ── Workspace.to_dict ────────────────────────────────────────────


def test_workspace_to_dict(sample_workspace):
    d = sample_workspace.to_dict()
    assert d["id"] == "workspace-abc12345"
    assert d["job_id"] == "job-abc12345678"
    assert d["status"] == "active"
    assert "created_at" in d
