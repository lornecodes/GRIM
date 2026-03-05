"""WorkspaceManager — git worktree isolation for pool jobs.

Each coding job gets its own git worktree with a fresh branch. Changes
stay isolated until explicitly merged. Worktrees are cleaned up after
job completion (or on pool shutdown).

Workspace lifecycle:
  1. create(job_id, repo_path) → creates worktree + branch
  2. Agent runs in worktree directory (slot.cwd = worktree path)
  3. On success: merge(workspace_id) → PR-ready branch
  4. On failure/cancel: destroy(workspace_id) → cleanup
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Workspace:
    """Metadata for a single git worktree workspace."""

    id: str                          # workspace-{job_id_suffix}
    job_id: str
    repo_path: Path                  # original repo root
    worktree_path: Path              # .grim/worktrees/{id}/
    branch_name: str                 # grim/{id}
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "active"           # active, merged, destroyed

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "repo_path": str(self.repo_path),
            "worktree_path": str(self.worktree_path),
            "branch_name": self.branch_name,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class WorkspaceManager:
    """Manages git worktree workspaces for pool jobs.

    Usage::

        mgr = WorkspaceManager(base_dir=Path("/path/to/.grim/worktrees"))
        ws = await mgr.create("job-abc12345", repo_path=Path("/repo"))
        # Agent runs in ws.worktree_path
        await mgr.destroy(ws.id)
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._workspaces: dict[str, Workspace] = {}

    async def create(
        self,
        job_id: str,
        repo_path: Path,
        base_ref: str = "HEAD",
    ) -> Workspace:
        """Create an isolated git worktree for a job.

        Creates a new branch `grim/{workspace_id}` from base_ref and
        a worktree at `{base_dir}/{workspace_id}/`.
        """
        suffix = job_id.replace("job-", "")[:8]
        ws_id = f"workspace-{suffix}"
        branch_name = f"grim/{ws_id}"
        worktree_path = self._base_dir / ws_id

        # Ensure base dir exists
        self._base_dir.mkdir(parents=True, exist_ok=True)

        # Create branch + worktree atomically
        try:
            await _run_git(
                repo_path,
                "worktree", "add", "-b", branch_name,
                str(worktree_path), base_ref,
            )
        except RuntimeError as e:
            # If branch already exists, try without -b
            if "already exists" in str(e):
                await _run_git(
                    repo_path,
                    "worktree", "add",
                    str(worktree_path), branch_name,
                )
            else:
                raise

        ws = Workspace(
            id=ws_id,
            job_id=job_id,
            repo_path=repo_path,
            worktree_path=worktree_path,
            branch_name=branch_name,
        )
        self._workspaces[ws_id] = ws
        logger.info("Workspace created: %s → %s", ws_id, worktree_path)
        return ws

    async def destroy(self, workspace_id: str) -> bool:
        """Remove a worktree and delete the branch.

        Returns True if workspace existed and was cleaned up.
        """
        ws = self._workspaces.pop(workspace_id, None)
        if ws is None:
            return False

        try:
            # Remove worktree
            await _run_git(ws.repo_path, "worktree", "remove", str(ws.worktree_path), "--force")
        except RuntimeError:
            # Fallback: manual cleanup if git worktree remove fails
            if ws.worktree_path.exists():
                shutil.rmtree(ws.worktree_path, ignore_errors=True)
            try:
                await _run_git(ws.repo_path, "worktree", "prune")
            except RuntimeError:
                pass

        # Delete the branch (force — we don't care about unmerged work on destroy)
        try:
            await _run_git(ws.repo_path, "branch", "-D", ws.branch_name)
        except RuntimeError:
            pass  # Branch may not exist if worktree creation partially failed

        ws.status = "destroyed"
        logger.info("Workspace destroyed: %s", workspace_id)
        return True

    async def get_branch_diff(self, workspace_id: str) -> str | None:
        """Get the diff of changes in a workspace branch vs its base."""
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return None

        try:
            result = await _run_git(ws.worktree_path, "diff", "HEAD~1", "--stat")
            return result
        except RuntimeError:
            return None

    async def destroy_all(self) -> int:
        """Destroy all active workspaces. Returns count destroyed."""
        ids = list(self._workspaces.keys())
        count = 0
        for ws_id in ids:
            if await self.destroy(ws_id):
                count += 1
        return count

    def get(self, workspace_id: str) -> Workspace | None:
        """Get workspace metadata by ID."""
        return self._workspaces.get(workspace_id)

    def list_workspaces(self) -> list[dict]:
        """List all active workspaces."""
        return [ws.to_dict() for ws in self._workspaces.values()]

    @property
    def active_count(self) -> int:
        return len(self._workspaces)


# ── Git helpers ──────────────────────────────────────────────────

async def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command and return stdout. Raises RuntimeError on failure."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode().strip() or stdout.decode().strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {err_msg}")

    return stdout.decode().strip()
