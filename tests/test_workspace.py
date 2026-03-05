"""Tests for WorkspaceManager — git worktree isolation for pool jobs.

Tests use mocked git commands (no real git operations).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.pool.workspace import Workspace, WorkspaceManager, _run_git


# ── Workspace model tests ──────────────────────────────────────────

class TestWorkspaceModel:
    def test_workspace_creation(self):
        ws = Workspace(
            id="workspace-abc12345",
            job_id="job-abc12345",
            repo_path=Path("/repo"),
            worktree_path=Path("/repo/.grim/worktrees/workspace-abc12345"),
            branch_name="grim/workspace-abc12345",
        )
        assert ws.id == "workspace-abc12345"
        assert ws.status == "active"
        assert ws.created_at.tzinfo is not None

    def test_workspace_to_dict(self):
        ws = Workspace(
            id="workspace-abc12345",
            job_id="job-abc12345",
            repo_path=Path("/repo"),
            worktree_path=Path("/repo/.grim/worktrees/workspace-abc12345"),
            branch_name="grim/workspace-abc12345",
        )
        d = ws.to_dict()
        assert d["id"] == "workspace-abc12345"
        assert d["job_id"] == "job-abc12345"
        assert d["status"] == "active"
        assert "created_at" in d
        assert d["branch_name"] == "grim/workspace-abc12345"

    def test_workspace_custom_status(self):
        ws = Workspace(
            id="ws-1",
            job_id="j-1",
            repo_path=Path("/r"),
            worktree_path=Path("/w"),
            branch_name="b",
            status="merged",
        )
        assert ws.status == "merged"


# ── WorkspaceManager tests ─────────────────────────────────────────

class TestWorkspaceManager:
    @pytest.fixture
    def mgr(self, tmp_path) -> WorkspaceManager:
        return WorkspaceManager(tmp_path / "worktrees")

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_create_workspace(self, mock_git, mgr, tmp_path):
        mock_git.return_value = ""
        ws = await mgr.create("job-abc12345", Path("/repo"))

        assert ws.id == "workspace-abc12345"
        assert ws.branch_name == "grim/workspace-abc12345"
        assert ws.job_id == "job-abc12345"
        assert ws.status == "active"
        assert mgr.active_count == 1

        # Verify git worktree add was called
        mock_git.assert_called_once()
        call_args = mock_git.call_args[0]
        assert call_args[0] == Path("/repo")
        assert "worktree" in call_args
        assert "add" in call_args
        assert "-b" in call_args

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_create_truncates_job_id(self, mock_git, mgr):
        mock_git.return_value = ""
        ws = await mgr.create("job-abcdefghijklmnop", Path("/repo"))
        # Should take first 8 chars after "job-"
        assert ws.id == "workspace-abcdefgh"

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_create_retries_if_branch_exists(self, mock_git, mgr):
        # First call fails with "already exists", second succeeds
        mock_git.side_effect = [
            RuntimeError("fatal: A branch named 'grim/workspace-abc12345' already exists"),
            "",
        ]
        ws = await mgr.create("job-abc12345", Path("/repo"))
        assert ws.id == "workspace-abc12345"
        assert mock_git.call_count == 2

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_create_raises_on_other_error(self, mock_git, mgr):
        mock_git.side_effect = RuntimeError("fatal: some other git error")
        with pytest.raises(RuntimeError, match="some other git error"):
            await mgr.create("job-abc12345", Path("/repo"))

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_destroy_workspace(self, mock_git, mgr):
        mock_git.return_value = ""
        ws = await mgr.create("job-abc12345", Path("/repo"))
        assert mgr.active_count == 1

        result = await mgr.destroy(ws.id)
        assert result is True
        assert mgr.active_count == 0
        assert ws.status == "destroyed"

    @pytest.mark.asyncio
    async def test_destroy_nonexistent(self, mgr):
        result = await mgr.destroy("workspace-nope")
        assert result is False

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_destroy_fallback_on_git_error(self, mock_git, mgr, tmp_path):
        """When git worktree remove fails, falls back to shutil.rmtree."""
        # Create workspace
        mock_git.return_value = ""
        ws = await mgr.create("job-abc12345", Path("/repo"))

        # Make destroy fail on worktree remove, then succeed on prune and branch delete
        mock_git.side_effect = [
            RuntimeError("worktree remove failed"),
            "",  # prune
            "",  # branch -D
        ]
        result = await mgr.destroy(ws.id)
        assert result is True
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_destroy_all(self, mock_git, mgr):
        mock_git.return_value = ""
        await mgr.create("job-aaa11111", Path("/repo"))
        await mgr.create("job-bbb22222", Path("/repo"))
        assert mgr.active_count == 2

        count = await mgr.destroy_all()
        assert count == 2
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_get_workspace(self, mock_git, mgr):
        mock_git.return_value = ""
        ws = await mgr.create("job-abc12345", Path("/repo"))

        found = mgr.get(ws.id)
        assert found is not None
        assert found.id == ws.id

        not_found = mgr.get("workspace-nope")
        assert not_found is None

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_list_workspaces(self, mock_git, mgr):
        mock_git.return_value = ""
        await mgr.create("job-aaa11111", Path("/repo"))
        await mgr.create("job-bbb22222", Path("/repo"))

        ws_list = mgr.list_workspaces()
        assert len(ws_list) == 2
        ids = {w["id"] for w in ws_list}
        assert "workspace-aaa11111" in ids
        assert "workspace-bbb22222" in ids

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_get_branch_diff(self, mock_git, mgr):
        mock_git.return_value = ""
        ws = await mgr.create("job-abc12345", Path("/repo"))

        mock_git.return_value = " 2 files changed, 10 insertions(+)"
        diff = await mgr.get_branch_diff(ws.id)
        assert diff is not None
        assert "files changed" in diff

    @pytest.mark.asyncio
    async def test_get_branch_diff_nonexistent(self, mgr):
        diff = await mgr.get_branch_diff("workspace-nope")
        assert diff is None

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_get_branch_diff_git_error(self, mock_git, mgr):
        mock_git.return_value = ""
        ws = await mgr.create("job-abc12345", Path("/repo"))

        mock_git.side_effect = RuntimeError("diff failed")
        diff = await mgr.get_branch_diff(ws.id)
        assert diff is None

    @pytest.mark.asyncio
    @patch("core.pool.workspace._run_git", new_callable=AsyncMock)
    async def test_create_with_custom_base_ref(self, mock_git, mgr):
        mock_git.return_value = ""
        ws = await mgr.create("job-abc12345", Path("/repo"), base_ref="main")
        call_args = mock_git.call_args[0]
        assert "main" in call_args


# ── Pool workspace integration tests ──────────────────────────────

class TestPoolWorkspaceIntegration:
    """Test ExecutionPool + WorkspaceManager wiring."""

    @pytest.fixture
    def mock_config(self, tmp_path):
        from dataclasses import dataclass

        @dataclass
        class MockConfig:
            pool_enabled: bool = True
            pool_num_slots: int = 1
            pool_poll_interval: float = 0.1
            pool_db_path: Path = tmp_path / "pool.db"
            pool_max_turns_per_job: int = 5
            pool_job_timeout_secs: int = 10
            kronos_mcp_command: str = ""
            vault_path: Path = None
            skills_path: Path = None
            workspace_root: Path = tmp_path / "repo"

        return MockConfig()

    def test_pool_creates_workspace_manager(self, mock_config, tmp_path):
        from core.pool.pool import ExecutionPool
        from core.pool.queue import JobQueue

        q = JobQueue(tmp_path / "pool.db")
        pool = ExecutionPool(q, mock_config)
        assert pool._workspace_mgr is not None
        assert pool._workspace_root is not None

    def test_pool_no_workspace_without_root(self, tmp_path):
        from dataclasses import dataclass
        from core.pool.pool import ExecutionPool
        from core.pool.queue import JobQueue

        @dataclass
        class NoRootConfig:
            pool_enabled: bool = True
            pool_num_slots: int = 1
            pool_poll_interval: float = 0.1
            pool_db_path: Path = tmp_path / "pool.db"
            pool_max_turns_per_job: int = 5
            pool_job_timeout_secs: int = 10
            kronos_mcp_command: str = ""
            vault_path: Path = None
            skills_path: Path = None
            workspace_root: Path = None

        q = JobQueue(tmp_path / "pool.db")
        pool = ExecutionPool(q, NoRootConfig())
        assert pool._workspace_mgr is None

    @pytest.mark.asyncio
    async def test_pool_status_includes_workspaces(self, mock_config, tmp_path):
        from core.pool.pool import ExecutionPool
        from core.pool.queue import JobQueue

        q = JobQueue(tmp_path / "pool.db")
        pool = ExecutionPool(q, mock_config)
        await pool.start()

        status = pool.status
        assert "active_workspaces" in status
        assert status["active_workspaces"] == 0

        await pool.stop()
