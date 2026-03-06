"""CodebaseManager — bare-cache repo management for coding agents.

Maintains bare git caches on the host for fast local clones. Coding agents
get working copies cloned from the local cache (seconds) instead of from
remote (minutes). The cache is refreshed periodically or on demand.

Lifecycle:
  1. init_cache(repo_name)     — git clone --bare <remote> <cache_dir>/<repo>.git
  2. refresh_cache(repo_name)  — git fetch --all in bare cache
  3. clone_for_workspace(...)  — git clone <cache> <workspace>/<repo> -b <branch>
  4. create_branch(...)        — git checkout -b <branch_name> in workspace clone

Cache layout:
  local/repos/
    GRIM.git/               # bare cache
    fracton.git/            # bare cache
    dawn-field-theory.git/  # bare cache
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# Timeout for git operations (seconds)
_GIT_TIMEOUT = 120


@dataclass
class RepoInfo:
    """Parsed repo entry from repos.yaml."""
    name: str
    remote: str
    tier: str = "core"
    path: str = ""
    description: str = ""


class CodebaseManager:
    """Manages bare git caches for fast workspace cloning.

    Usage::

        mgr = CodebaseManager(
            cache_dir=Path("local/repos"),
            workspace_root=Path(".."),
        )
        await mgr.load_manifest()  # reads repos.yaml
        await mgr.init_cache("GRIM")
        await mgr.clone_for_workspace("GRIM", Path("/tmp/ws-001"), branch="main")
    """

    def __init__(
        self,
        cache_dir: Path,
        workspace_root: Path,
        repos_manifest: str = "repos.yaml",
    ) -> None:
        self._cache_dir = cache_dir
        self._workspace_root = workspace_root
        self._manifest_path = workspace_root / repos_manifest
        self._repos: dict[str, RepoInfo] = {}

    async def load_manifest(self) -> int:
        """Load repos from manifest YAML. Returns count loaded."""
        if not self._manifest_path.exists():
            logger.warning("Repos manifest not found: %s", self._manifest_path)
            return 0

        def _parse():
            with open(self._manifest_path, "r") as f:
                data = yaml.safe_load(f)
            repos = {}
            for entry in data.get("repos", []):
                name = entry.get("name", "")
                remote = entry.get("remote", "")
                if not name or not remote:
                    continue
                repos[name] = RepoInfo(
                    name=name,
                    remote=remote,
                    tier=entry.get("tier", "core"),
                    path=entry.get("path", name),
                    description=entry.get("description", ""),
                )
            return repos

        self._repos = await asyncio.to_thread(_parse)
        logger.info("Loaded %d repos from manifest", len(self._repos))
        return len(self._repos)

    def get_repo(self, repo_name: str) -> RepoInfo | None:
        """Get repo info by name."""
        return self._repos.get(repo_name)

    def list_repos(self) -> list[str]:
        """List all known repo names."""
        return list(self._repos.keys())

    def list_cached_repos(self) -> list[str]:
        """List repos that have a local bare cache."""
        if not self._cache_dir.exists():
            return []
        return [
            p.name.removesuffix(".git")
            for p in self._cache_dir.iterdir()
            if p.is_dir() and p.name.endswith(".git")
        ]

    def cache_path(self, repo_name: str) -> Path:
        """Path to the bare cache for a repo."""
        return self._cache_dir / f"{repo_name}.git"

    def is_cached(self, repo_name: str) -> bool:
        """Check if a repo has a local bare cache."""
        return self.cache_path(repo_name).exists()

    # ── Cache operations ─────────────────────────────────────────

    async def init_cache(self, repo_name: str) -> Path:
        """Create a bare cache for a repo. Skips if already cached.

        Returns the cache path.
        """
        repo = self._repos.get(repo_name)
        if repo is None:
            raise ValueError(f"Unknown repo: {repo_name}")

        cache = self.cache_path(repo_name)
        if cache.exists():
            logger.info("Cache already exists for %s", repo_name)
            return cache

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        await _run_git(
            self._cache_dir,
            "clone", "--bare", repo.remote, str(cache),
        )
        logger.info("Bare cache created: %s", cache)
        return cache

    async def refresh_cache(self, repo_name: str) -> bool:
        """Fetch latest refs into bare cache. Returns True if cache exists."""
        cache = self.cache_path(repo_name)
        if not cache.exists():
            logger.warning("No cache for %s — run init_cache first", repo_name)
            return False

        await _run_git(cache, "fetch", "--all", "--prune")
        logger.info("Cache refreshed: %s", repo_name)
        return True

    async def refresh_all(self) -> dict[str, bool]:
        """Refresh all cached repos. Returns {repo: success} map."""
        cached = self.list_cached_repos()
        results = {}
        for name in cached:
            try:
                results[name] = await self.refresh_cache(name)
            except RuntimeError as e:
                logger.error("Failed to refresh %s: %s", name, e)
                results[name] = False
        return results

    async def init_all(self, tiers: list[str] | None = None) -> dict[str, bool]:
        """Initialize caches for repos matching the given tiers.

        Args:
            tiers: List of tiers to cache (default: ["core"]).

        Returns:
            {repo_name: success} map.
        """
        if tiers is None:
            tiers = ["core"]
        results = {}
        for name, repo in self._repos.items():
            if repo.tier not in tiers:
                continue
            try:
                await self.init_cache(name)
                results[name] = True
            except RuntimeError as e:
                logger.error("Failed to init cache for %s: %s", name, e)
                results[name] = False
        return results

    # ── Workspace cloning ────────────────────────────────────────

    async def clone_for_workspace(
        self,
        repo_name: str,
        workspace_path: Path,
        branch: str = "main",
    ) -> Path:
        """Clone a repo from bare cache into a workspace directory.

        Uses local bare cache as origin for fast cloning. Sets up
        the remote to track the original remote URL for push.

        Returns the cloned repo path.
        """
        cache = self.cache_path(repo_name)
        if not cache.exists():
            # Try to init cache first
            await self.init_cache(repo_name)

        clone_dest = workspace_path / repo_name
        if clone_dest.exists():
            logger.info("Clone already exists at %s", clone_dest)
            return clone_dest

        workspace_path.mkdir(parents=True, exist_ok=True)

        await _run_git(
            workspace_path,
            "clone", str(cache), repo_name, "-b", branch,
        )

        # Set the push URL to the real remote (bare cache is fetch-only)
        repo = self._repos.get(repo_name)
        if repo:
            await _run_git(
                clone_dest,
                "remote", "set-url", "--push", "origin", repo.remote,
            )

        logger.info("Cloned %s → %s (branch: %s)", repo_name, clone_dest, branch)
        return clone_dest

    async def create_branch(
        self,
        repo_path: Path,
        branch_name: str,
        start_point: str = "HEAD",
    ) -> str:
        """Create and checkout a new branch in a workspace clone.

        Returns the branch name.
        """
        await _run_git(repo_path, "checkout", "-b", branch_name, start_point)
        logger.info("Branch created: %s in %s", branch_name, repo_path)
        return branch_name


# ── Git helpers ──────────────────────────────────────────────────

async def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command and return stdout. Raises RuntimeError on failure."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_GIT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"git {' '.join(args)} timed out after {_GIT_TIMEOUT}s")

    if proc.returncode != 0:
        err_msg = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {err_msg}")

    return stdout.decode().strip()
