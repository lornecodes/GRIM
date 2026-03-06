"""GitHubClient — async wrapper around the `gh` CLI for PR lifecycle.

Provides push, PR create/merge/close, comment listing, and status checks.
Uses asyncio subprocess execution. Degrades gracefully if gh is not installed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class GitHubError(Exception):
    """Raised when a GitHub CLI operation fails."""

    def __init__(self, command: str, message: str) -> None:
        self.command = command
        super().__init__(f"gh {command}: {message}")


class GitHubClient:
    """Async wrapper around the `gh` CLI for PR lifecycle operations."""

    def __init__(self, default_repo: str = "") -> None:
        self._default_repo = default_repo

    async def push_branch(self, repo_path: Path, branch_name: str) -> None:
        """Push a local branch to the remote."""
        await _run_git(repo_path, "push", "-u", "origin", branch_name)

    async def create_pr(
        self,
        repo_path: Path,
        branch: str,
        title: str,
        body: str,
        base: str = "main",
    ) -> tuple[int, str]:
        """Create a PR via gh CLI.

        Returns:
            (pr_number, pr_url) tuple
        """
        stdout = await _run_gh(
            repo_path, "pr", "create",
            "--head", branch, "--base", base,
            "--title", title, "--body", body,
        )
        # gh pr create outputs the PR URL on success
        # Parse number from URL: https://github.com/owner/repo/pull/123
        pr_url = stdout.strip()
        try:
            pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            raise GitHubError("pr create", f"Could not parse PR number from: {pr_url}")
        return pr_number, pr_url

    async def get_pr_status(self, repo_path: Path, pr_number: int) -> str:
        """Return PR state: open, merged, closed."""
        stdout = await _run_gh(
            repo_path, "pr", "view", str(pr_number),
            "--json", "state",
        )
        try:
            data = json.loads(stdout)
            return data["state"].lower()
        except (json.JSONDecodeError, KeyError) as e:
            raise GitHubError("pr view", f"Malformed response: {e}")

    async def list_pr_comments(
        self, repo_path: Path, pr_number: int,
    ) -> list[dict]:
        """List PR review comments.

        Returns list of {author, body, created_at} dicts.
        """
        stdout = await _run_gh(
            repo_path, "pr", "view", str(pr_number),
            "--json", "comments",
        )
        try:
            data = json.loads(stdout)
            return [
                {
                    "author": c.get("author", {}).get("login", "unknown"),
                    "body": c.get("body", ""),
                    "created_at": c.get("createdAt", ""),
                }
                for c in data.get("comments", [])
            ]
        except (json.JSONDecodeError, KeyError) as e:
            raise GitHubError("pr view", f"Malformed comments response: {e}")

    async def merge_pr(
        self, repo_path: Path, pr_number: int, method: str = "squash",
    ) -> None:
        """Merge a PR via gh CLI."""
        await _run_gh(
            repo_path, "pr", "merge", str(pr_number),
            f"--{method}", "--delete-branch",
        )

    async def close_pr(self, repo_path: Path, pr_number: int) -> None:
        """Close a PR without merging."""
        await _run_gh(repo_path, "pr", "close", str(pr_number))

    async def is_available(self) -> bool:
        """Check if gh CLI is installed and authenticated."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "gh", "auth", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        except FileNotFoundError:
            return False


# ── Helpers ──────────────────────────────────────────────────────

async def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command and return stdout. Raises GitHubError on failure."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode().strip() or stdout.decode().strip()
        raise GitHubError(f"git {' '.join(args)}", err_msg)

    return stdout.decode().strip()


async def _run_gh(cwd: Path, *args: str) -> str:
    """Run a gh CLI command and return stdout. Raises GitHubError on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise GitHubError(" ".join(args), "gh CLI not found — install from https://cli.github.com")

    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode().strip() or stdout.decode().strip()
        raise GitHubError(" ".join(args), err_msg)

    return stdout.decode().strip()
