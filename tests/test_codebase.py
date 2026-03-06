"""Tests for CodebaseManager — bare-cache repo management."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from core.pool.codebase import CodebaseManager, RepoInfo, _run_git


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def manifest_file(tmp_path):
    """Create a temporary repos.yaml manifest."""
    manifest = tmp_path / "repos.yaml"
    manifest.write_text("""\
repos:
  - name: alpha
    path: alpha
    remote: https://github.com/test/alpha.git
    tier: core
    description: Test repo alpha
  - name: beta
    path: beta
    remote: https://github.com/test/beta.git
    tier: support
    description: Test repo beta
  - name: gamma
    path: gamma
    remote: https://github.com/test/gamma.git
    tier: legacy
    description: Test repo gamma
""")
    return manifest


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"


@pytest.fixture
def mgr(tmp_path, manifest_file, cache_dir):
    """CodebaseManager with temp dirs."""
    return CodebaseManager(
        cache_dir=cache_dir,
        workspace_root=manifest_file.parent,
    )


# ── Manifest loading ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_manifest(mgr):
    count = await mgr.load_manifest()
    assert count == 3


@pytest.mark.asyncio
async def test_load_manifest_missing_file(tmp_path):
    mgr = CodebaseManager(
        cache_dir=tmp_path / "cache",
        workspace_root=tmp_path / "nonexistent",
    )
    count = await mgr.load_manifest()
    assert count == 0


@pytest.mark.asyncio
async def test_list_repos(mgr):
    await mgr.load_manifest()
    repos = mgr.list_repos()
    assert set(repos) == {"alpha", "beta", "gamma"}


@pytest.mark.asyncio
async def test_get_repo(mgr):
    await mgr.load_manifest()
    repo = mgr.get_repo("alpha")
    assert repo is not None
    assert repo.name == "alpha"
    assert repo.remote == "https://github.com/test/alpha.git"
    assert repo.tier == "core"


@pytest.mark.asyncio
async def test_get_repo_unknown(mgr):
    await mgr.load_manifest()
    assert mgr.get_repo("nonexistent") is None


# ── Cache path / status ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_path(mgr, cache_dir):
    assert mgr.cache_path("alpha") == cache_dir / "alpha.git"


@pytest.mark.asyncio
async def test_is_cached_false(mgr):
    assert mgr.is_cached("alpha") is False


@pytest.mark.asyncio
async def test_is_cached_true(mgr, cache_dir):
    (cache_dir / "alpha.git").mkdir(parents=True)
    assert mgr.is_cached("alpha") is True


@pytest.mark.asyncio
async def test_list_cached_repos(mgr, cache_dir):
    assert mgr.list_cached_repos() == []
    cache_dir.mkdir(parents=True)
    (cache_dir / "alpha.git").mkdir()
    (cache_dir / "beta.git").mkdir()
    (cache_dir / "not-a-cache").mkdir()  # no .git suffix
    cached = mgr.list_cached_repos()
    assert set(cached) == {"alpha", "beta"}


# ── init_cache ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_cache_unknown_repo(mgr):
    await mgr.load_manifest()
    with pytest.raises(ValueError, match="Unknown repo"):
        await mgr.init_cache("nonexistent")


@pytest.mark.asyncio
async def test_init_cache_creates_bare_clone(mgr, cache_dir):
    await mgr.load_manifest()
    with patch("core.pool.codebase._run_git", new_callable=AsyncMock) as mock_git:
        path = await mgr.init_cache("alpha")
        assert path == cache_dir / "alpha.git"
        mock_git.assert_called_once_with(
            cache_dir,
            "clone", "--bare", "https://github.com/test/alpha.git",
            str(cache_dir / "alpha.git"),
        )


@pytest.mark.asyncio
async def test_init_cache_skips_existing(mgr, cache_dir):
    await mgr.load_manifest()
    (cache_dir / "alpha.git").mkdir(parents=True)
    with patch("core.pool.codebase._run_git", new_callable=AsyncMock) as mock_git:
        path = await mgr.init_cache("alpha")
        assert path == cache_dir / "alpha.git"
        mock_git.assert_not_called()


# ── refresh_cache ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_cache_no_cache(mgr):
    await mgr.load_manifest()
    result = await mgr.refresh_cache("alpha")
    assert result is False


@pytest.mark.asyncio
async def test_refresh_cache_fetches(mgr, cache_dir):
    await mgr.load_manifest()
    (cache_dir / "alpha.git").mkdir(parents=True)
    with patch("core.pool.codebase._run_git", new_callable=AsyncMock) as mock_git:
        result = await mgr.refresh_cache("alpha")
        assert result is True
        mock_git.assert_called_once_with(
            cache_dir / "alpha.git",
            "fetch", "--all", "--prune",
        )


@pytest.mark.asyncio
async def test_refresh_all(mgr, cache_dir):
    await mgr.load_manifest()
    (cache_dir / "alpha.git").mkdir(parents=True)
    (cache_dir / "beta.git").mkdir(parents=True)
    with patch("core.pool.codebase._run_git", new_callable=AsyncMock):
        results = await mgr.refresh_all()
        assert results == {"alpha": True, "beta": True}


@pytest.mark.asyncio
async def test_refresh_all_handles_failure(mgr, cache_dir):
    await mgr.load_manifest()
    (cache_dir / "alpha.git").mkdir(parents=True)
    (cache_dir / "beta.git").mkdir(parents=True)
    with patch("core.pool.codebase._run_git", new_callable=AsyncMock) as mock_git:
        # First call succeeds, second fails
        mock_git.side_effect = [None, RuntimeError("network error")]
        results = await mgr.refresh_all()
        assert results["alpha"] is True
        assert results["beta"] is False


# ── init_all ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_all_default_core(mgr, cache_dir):
    await mgr.load_manifest()
    with patch("core.pool.codebase._run_git", new_callable=AsyncMock):
        results = await mgr.init_all()
        # Only "alpha" is tier=core
        assert "alpha" in results
        assert results["alpha"] is True
        assert "beta" not in results  # tier=support
        assert "gamma" not in results  # tier=legacy


@pytest.mark.asyncio
async def test_init_all_multiple_tiers(mgr, cache_dir):
    await mgr.load_manifest()
    with patch("core.pool.codebase._run_git", new_callable=AsyncMock):
        results = await mgr.init_all(tiers=["core", "support"])
        assert "alpha" in results
        assert "beta" in results
        assert "gamma" not in results


# ── clone_for_workspace ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_clone_for_workspace(mgr, cache_dir, tmp_path):
    await mgr.load_manifest()
    ws_path = tmp_path / "workspace"
    (cache_dir / "alpha.git").mkdir(parents=True)

    with patch("core.pool.codebase._run_git", new_callable=AsyncMock) as mock_git:
        result = await mgr.clone_for_workspace("alpha", ws_path, branch="main")
        assert result == ws_path / "alpha"
        # Should clone then set push URL
        assert mock_git.call_count == 2
        clone_call = mock_git.call_args_list[0]
        assert clone_call.args == (
            ws_path, "clone", str(cache_dir / "alpha.git"), "alpha", "-b", "main",
        )
        push_call = mock_git.call_args_list[1]
        assert push_call.args == (
            ws_path / "alpha",
            "remote", "set-url", "--push", "origin",
            "https://github.com/test/alpha.git",
        )


@pytest.mark.asyncio
async def test_clone_for_workspace_skips_existing(mgr, cache_dir, tmp_path):
    await mgr.load_manifest()
    ws_path = tmp_path / "workspace"
    (cache_dir / "alpha.git").mkdir(parents=True)
    (ws_path / "alpha").mkdir(parents=True)  # already cloned

    with patch("core.pool.codebase._run_git", new_callable=AsyncMock) as mock_git:
        result = await mgr.clone_for_workspace("alpha", ws_path)
        assert result == ws_path / "alpha"
        mock_git.assert_not_called()


@pytest.mark.asyncio
async def test_clone_inits_cache_if_missing(mgr, cache_dir, tmp_path):
    await mgr.load_manifest()
    ws_path = tmp_path / "workspace"

    call_count = 0
    async def fake_git(cwd, *args):
        nonlocal call_count
        call_count += 1
        if args[0] == "clone" and args[1] == "--bare":
            # init_cache: create the bare dir
            (cache_dir / "alpha.git").mkdir(parents=True)

    with patch("core.pool.codebase._run_git", side_effect=fake_git):
        result = await mgr.clone_for_workspace("alpha", ws_path)
        assert result == ws_path / "alpha"
        # init_cache + clone + set-url = 3 calls
        assert call_count == 3


# ── create_branch ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_branch(mgr, tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    with patch("core.pool.codebase._run_git", new_callable=AsyncMock) as mock_git:
        result = await mgr.create_branch(repo_path, "feature/test")
        assert result == "feature/test"
        mock_git.assert_called_once_with(
            repo_path, "checkout", "-b", "feature/test", "HEAD",
        )


@pytest.mark.asyncio
async def test_create_branch_from_ref(mgr, tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    with patch("core.pool.codebase._run_git", new_callable=AsyncMock) as mock_git:
        result = await mgr.create_branch(repo_path, "fix/bug", start_point="origin/main")
        assert result == "fix/bug"
        mock_git.assert_called_once_with(
            repo_path, "checkout", "-b", "fix/bug", "origin/main",
        )


# ── RepoInfo ─────────────────────────────────────────────────────


def test_repo_info_defaults():
    info = RepoInfo(name="test", remote="https://example.com/test.git")
    assert info.tier == "core"
    assert info.path == ""
    assert info.description == ""


# ── _run_git helper ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_git_success(tmp_path):
    """Integration test: actual git command."""
    # git init is safe and always works
    result = await _run_git(tmp_path, "init", "--bare")
    assert "Initialized" in result or result == ""


@pytest.mark.asyncio
async def test_run_git_failure(tmp_path):
    """git command that fails raises RuntimeError."""
    with pytest.raises(RuntimeError, match="failed"):
        await _run_git(tmp_path, "log", "--oneline")  # fails in non-repo


# ── Pool integration ─────────────────────────────────────────────


def test_pool_exposes_codebase_manager():
    """ExecutionPool creates CodebaseManager when workspace_root is set."""
    from core.pool.pool import ExecutionPool
    from core.pool.queue import JobQueue

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
    assert pool.codebase is not None
    assert isinstance(pool.codebase, CodebaseManager)


def test_pool_no_codebase_without_workspace():
    """ExecutionPool has no CodebaseManager without workspace_root."""
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
    assert pool.codebase is None
