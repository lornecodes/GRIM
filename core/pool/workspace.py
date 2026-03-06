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
import json
import logging
import shutil
import tarfile
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

    async def get_branch_diff(
        self, workspace_id: str, base_branch: str = "main",
    ) -> str | None:
        """Get the diff stat of changes in a workspace branch vs its base."""
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return None

        try:
            result = await _run_git(
                ws.worktree_path,
                "diff", f"origin/{base_branch}...HEAD", "--stat",
            )
            return result
        except RuntimeError:
            return None

    async def get_full_diff(
        self, workspace_id: str, base_branch: str = "main",
    ) -> str | None:
        """Get the full unified diff of changes vs base branch."""
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return None

        try:
            return await _run_git(
                ws.worktree_path,
                "diff", f"origin/{base_branch}...HEAD",
            )
        except RuntimeError:
            return None

    async def list_changed_files(
        self, workspace_id: str, base_branch: str = "main",
    ) -> list[str] | None:
        """List files changed in workspace branch vs base. Returns filenames."""
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return None

        try:
            result = await _run_git(
                ws.worktree_path,
                "diff", "--name-only", f"origin/{base_branch}...HEAD",
            )
            return [f for f in result.splitlines() if f.strip()] if result else []
        except RuntimeError:
            return None

    async def get_commits(
        self, workspace_id: str, base_branch: str = "main",
    ) -> list[dict] | None:
        """Get commit history for workspace branch vs base.

        Returns list of {hash, short_hash, message, author, date} dicts,
        or None if workspace not found.
        """
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return None

        try:
            result = await _run_git(
                ws.worktree_path,
                "log", f"origin/{base_branch}..HEAD",
                "--format=%H|%h|%s|%an|%aI",
            )
            if not result:
                return []
            commits = []
            for line in result.splitlines():
                parts = line.split("|", 4)
                if len(parts) == 5:
                    commits.append({
                        "hash": parts[0],
                        "short_hash": parts[1],
                        "message": parts[2],
                        "author": parts[3],
                        "date": parts[4],
                    })
            return commits
        except RuntimeError:
            return None

    async def merge_to_base(
        self, workspace_id: str, base_branch: str = "main",
    ) -> bool:
        """Squash-merge workspace branch into base and destroy workspace.

        Returns True on success.
        """
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return False

        try:
            # Checkout base branch in main repo
            await _run_git(ws.repo_path, "checkout", base_branch)
            # Squash merge
            await _run_git(
                ws.repo_path,
                "merge", "--squash", ws.branch_name,
            )
            # Commit with job reference
            await _run_git(
                ws.repo_path,
                "commit", "-m", f"Squash merge from {ws.branch_name} (job: {ws.job_id})",
            )
            ws.status = "merged"
            # Cleanup worktree
            await self.destroy(workspace_id)
            return True
        except RuntimeError as e:
            logger.error("Merge failed for %s: %s", workspace_id, e)
            # Abort merge if in progress
            try:
                await _run_git(ws.repo_path, "merge", "--abort")
            except RuntimeError:
                pass
            return False

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

    # ── Snapshot / Restore ──────────────────────────────────────

    async def snapshot(
        self, workspace_id: str, snapshot_dir: Path,
    ) -> Path | None:
        """Save workspace state to a tar.gz archive with metadata.

        Returns the snapshot path, or None if workspace not found.
        """
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            return None

        snapshot_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_name = f"{ws.id}_{ts}.tar.gz"
        archive_path = snapshot_dir / archive_name

        # Write metadata alongside the archive
        metadata = {
            **ws.to_dict(),
            "snapshot_timestamp": ts,
        }
        meta_path = snapshot_dir / f"{ws.id}_{ts}.meta.json"

        def _create_archive():
            with tarfile.open(str(archive_path), "w:gz") as tar:
                tar.add(str(ws.worktree_path), arcname=ws.id)
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)

        await asyncio.to_thread(_create_archive)
        logger.info("Snapshot created: %s", archive_path)
        return archive_path

    async def restore_snapshot(
        self,
        snapshot_path: Path,
        job_id: str,
        repo_path: Path,
    ) -> Workspace | None:
        """Restore a workspace from a tar.gz snapshot.

        Creates a new workspace entry and extracts the archive.
        Returns the restored Workspace, or None on failure.
        """
        if not snapshot_path.exists():
            logger.error("Snapshot not found: %s", snapshot_path)
            return None

        # Read metadata
        meta_path = snapshot_path.with_suffix("").with_suffix(".meta.json")
        metadata: dict = {}
        if meta_path.exists():
            def _read_meta():
                with open(meta_path) as f:
                    return json.load(f)
            metadata = await asyncio.to_thread(_read_meta)

        # Create workspace entry
        suffix = job_id.replace("job-", "")[:8]
        ws_id = f"workspace-{suffix}"
        branch_name = metadata.get("branch_name", f"grim/{ws_id}")
        worktree_path = self._base_dir / ws_id

        self._base_dir.mkdir(parents=True, exist_ok=True)

        def _extract():
            with tarfile.open(str(snapshot_path), "r:gz") as tar:
                tar.extractall(str(self._base_dir))
            # Rename extracted dir to new ws_id if different
            old_id = metadata.get("id", ws_id)
            old_path = self._base_dir / old_id
            if old_path.exists() and old_path != worktree_path:
                old_path.rename(worktree_path)

        try:
            await asyncio.to_thread(_extract)
        except Exception as e:
            logger.error("Failed to extract snapshot: %s", e)
            return None

        ws = Workspace(
            id=ws_id,
            job_id=job_id,
            repo_path=repo_path,
            worktree_path=worktree_path,
            branch_name=branch_name,
            status="active",
        )
        self._workspaces[ws_id] = ws
        logger.info("Workspace restored from snapshot: %s → %s", snapshot_path, ws_id)
        return ws

    def list_snapshots(self, snapshot_dir: Path) -> list[dict]:
        """List available snapshots with metadata."""
        if not snapshot_dir.exists():
            return []
        snapshots = []
        for meta_file in sorted(snapshot_dir.glob("*.meta.json")):
            try:
                with open(meta_file) as f:
                    metadata = json.load(f)
                archive = meta_file.with_suffix("").with_suffix(".tar.gz")
                metadata["archive_path"] = str(archive)
                metadata["archive_exists"] = archive.exists()
                snapshots.append(metadata)
            except Exception:
                continue
        return snapshots


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
