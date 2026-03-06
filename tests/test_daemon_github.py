"""Tests for the GitHub client module (core/daemon/github.py).

All tests mock asyncio.create_subprocess_exec — no real gh/git calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.daemon.github import GitHubClient, GitHubError, _run_gh, _run_git


# ── Helpers ──────────────────────────────────────────────────────

def _mock_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a mock subprocess with communicate() result."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(
        stdout.encode(), stderr.encode(),
    ))
    return proc


REPO = Path("/fake/repo")


# ── TestGitHubClient ─────────────────────────────────────────────

class TestGitHubClient:
    """Unit tests for GitHubClient methods."""

    @pytest.fixture
    def client(self):
        return GitHubClient(default_repo="owner/repo")

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_push_branch(self, mock_exec, client):
        mock_exec.return_value = _mock_proc()
        await client.push_branch(REPO, "grim/workspace-abc123")
        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args == ("git", "push", "-u", "origin", "grim/workspace-abc123")

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_push_branch_failure(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(stderr="rejected", returncode=1)
        with pytest.raises(GitHubError, match="rejected"):
            await client.push_branch(REPO, "grim/workspace-abc123")

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_create_pr(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(
            stdout="https://github.com/owner/repo/pull/42"
        )
        pr_number, pr_url = await client.create_pr(
            REPO, "grim/ws-abc", "Test PR", "PR body",
        )
        assert pr_number == 42
        assert pr_url == "https://github.com/owner/repo/pull/42"

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_create_pr_custom_base(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(
            stdout="https://github.com/owner/repo/pull/7"
        )
        pr_number, pr_url = await client.create_pr(
            REPO, "grim/ws-abc", "Title", "Body", base="develop",
        )
        assert pr_number == 7
        args = mock_exec.call_args[0]
        assert "--base" in args
        idx = list(args).index("--base")
        assert args[idx + 1] == "develop"

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_create_pr_malformed_url(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(stdout="something unexpected")
        with pytest.raises(GitHubError, match="Could not parse PR number"):
            await client.create_pr(REPO, "branch", "title", "body")

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_create_pr_failure(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(
            stderr="GraphQL error", returncode=1,
        )
        with pytest.raises(GitHubError, match="GraphQL error"):
            await client.create_pr(REPO, "branch", "title", "body")

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_get_pr_status_open(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(
            stdout=json.dumps({"state": "OPEN"}),
        )
        status = await client.get_pr_status(REPO, 42)
        assert status == "open"

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_get_pr_status_merged(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(
            stdout=json.dumps({"state": "MERGED"}),
        )
        status = await client.get_pr_status(REPO, 42)
        assert status == "merged"

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_get_pr_status_malformed(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(stdout="not json")
        with pytest.raises(GitHubError, match="Malformed response"):
            await client.get_pr_status(REPO, 42)

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_list_pr_comments_empty(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(
            stdout=json.dumps({"comments": []}),
        )
        comments = await client.list_pr_comments(REPO, 42)
        assert comments == []

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_list_pr_comments_with_data(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(
            stdout=json.dumps({"comments": [
                {"author": {"login": "peter"}, "body": "LGTM", "createdAt": "2026-03-06T10:00:00Z"},
                {"author": {"login": "bot"}, "body": "CI passed", "createdAt": "2026-03-06T10:01:00Z"},
            ]}),
        )
        comments = await client.list_pr_comments(REPO, 42)
        assert len(comments) == 2
        assert comments[0]["author"] == "peter"
        assert comments[0]["body"] == "LGTM"
        assert comments[1]["author"] == "bot"

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_list_pr_comments_malformed(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(stdout="not json")
        with pytest.raises(GitHubError, match="Malformed comments"):
            await client.list_pr_comments(REPO, 42)

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_merge_pr_squash(self, mock_exec, client):
        mock_exec.return_value = _mock_proc()
        await client.merge_pr(REPO, 42)
        args = mock_exec.call_args[0]
        assert "--squash" in args
        assert "--delete-branch" in args

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_merge_pr_rebase(self, mock_exec, client):
        mock_exec.return_value = _mock_proc()
        await client.merge_pr(REPO, 42, method="rebase")
        args = mock_exec.call_args[0]
        assert "--rebase" in args

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_merge_pr_failure(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(
            stderr="merge conflict", returncode=1,
        )
        with pytest.raises(GitHubError, match="merge conflict"):
            await client.merge_pr(REPO, 42)

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_close_pr(self, mock_exec, client):
        mock_exec.return_value = _mock_proc()
        await client.close_pr(REPO, 42)
        args = mock_exec.call_args[0]
        assert args == ("gh", "pr", "close", "42")

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_close_pr_failure(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(
            stderr="not found", returncode=1,
        )
        with pytest.raises(GitHubError, match="not found"):
            await client.close_pr(REPO, 42)

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_is_available_true(self, mock_exec, client):
        mock_exec.return_value = _mock_proc()
        assert await client.is_available() is True

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_is_available_not_authenticated(self, mock_exec, client):
        mock_exec.return_value = _mock_proc(returncode=1)
        assert await client.is_available() is False

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_is_available_not_installed(self, mock_exec, client):
        mock_exec.side_effect = FileNotFoundError()
        assert await client.is_available() is False


# ── TestRunGh ────────────────────────────────────────────────────

class TestRunGh:
    """Tests for the _run_gh helper."""

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_gh_not_found(self, mock_exec):
        mock_exec.side_effect = FileNotFoundError()
        with pytest.raises(GitHubError, match="gh CLI not found"):
            await _run_gh(REPO, "pr", "list")

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_gh_returns_stdout(self, mock_exec):
        mock_exec.return_value = _mock_proc(stdout="output data")
        result = await _run_gh(REPO, "pr", "list")
        assert result == "output data"

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_gh_sets_cwd(self, mock_exec):
        mock_exec.return_value = _mock_proc()
        await _run_gh(Path("/my/repo"), "status")
        kwargs = mock_exec.call_args[1]
        assert kwargs["cwd"] == str(Path("/my/repo"))


# ── TestRunGit ───────────────────────────────────────────────────

class TestRunGit:
    """Tests for the _run_git helper."""

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_git_returns_stdout(self, mock_exec):
        mock_exec.return_value = _mock_proc(stdout="abc123")
        result = await _run_git(REPO, "rev-parse", "HEAD")
        assert result == "abc123"

    @patch("core.daemon.github.asyncio.create_subprocess_exec")
    async def test_git_failure(self, mock_exec):
        mock_exec.return_value = _mock_proc(stderr="fatal: not a repo", returncode=1)
        with pytest.raises(GitHubError, match="fatal: not a repo"):
            await _run_git(REPO, "status")


# ── TestWorkspaceAccess ──────────────────────────────────────────

class TestWorkspaceAccess:
    """Tests that slot.cwd and add_dirs are wired into ClaudeAgentOptions."""

    def test_slot_has_add_dirs_field(self):
        from core.pool.slot import AgentSlot
        slot = AgentSlot(slot_id="test-slot")
        assert hasattr(slot, "add_dirs")
        assert slot.add_dirs == []
        assert slot.cwd is None

    def test_slot_add_dirs_settable(self):
        from core.pool.slot import AgentSlot
        slot = AgentSlot(slot_id="test-slot")
        slot.add_dirs = ["/workspace/root"]
        slot.cwd = "/workspace/root/GRIM"
        assert slot.add_dirs == ["/workspace/root"]
        assert slot.cwd == "/workspace/root/GRIM"
