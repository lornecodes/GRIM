"""Workspace tools — file, shell, and git operations for doer agents.

These are the hands. Agents use these to actually do work in the
filesystem, run commands, and interact with git. Only available
to doer agents via Dispatch — the GRIM companion never uses these.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Workspace root — resolved at import time, overridable
_workspace_root: Path = Path(__file__).resolve().parent.parent.parent.parent
# That's GRIM/../.. = core_workspace root


def set_workspace_root(path: Path) -> None:
    """Override the workspace root (called at boot from config)."""
    global _workspace_root
    _workspace_root = path


def _resolve_path(rel: str) -> Path:
    """Resolve a relative path against workspace root.

    Security: refuses to escape the workspace.
    """
    target = (_workspace_root / rel).resolve()
    if not str(target).startswith(str(_workspace_root.resolve())):
        raise ValueError(f"Path escapes workspace: {rel}")
    return target


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

@tool
async def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a file from the workspace.

    Args:
        path: Relative path from workspace root (e.g. "GRIM/core/state.py")
        start_line: First line to read (1-based, default 1)
        end_line: Last line to read (0 = all remaining)

    Returns:
        File content as string.
    """
    target = _resolve_path(path)
    if not target.exists():
        return json.dumps({"error": f"File not found: {path}"})

    text = target.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    if end_line > 0:
        selected = lines[start_line - 1 : end_line]
    elif start_line > 1:
        selected = lines[start_line - 1 :]
    else:
        selected = lines

    return "".join(selected)


@tool
async def write_file(path: str, content: str) -> str:
    """Write content to a file in the workspace. Creates parent dirs.

    Args:
        path: Relative path from workspace root.
        content: Full file content to write.

    Returns:
        JSON result with bytes written.
    """
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return json.dumps({"ok": True, "path": str(target), "bytes": len(content)})


@tool
async def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace one occurrence of old_string with new_string in a file.

    Args:
        path: Relative path from workspace root.
        old_string: Exact text to find (must match precisely).
        new_string: Replacement text.

    Returns:
        JSON result indicating success or failure.
    """
    target = _resolve_path(path)
    if not target.exists():
        return json.dumps({"error": f"File not found: {path}"})

    text = target.read_text(encoding="utf-8")
    count = text.count(old_string)

    if count == 0:
        return json.dumps({"error": "old_string not found in file"})
    if count > 1:
        return json.dumps({"error": f"old_string matches {count} locations — be more specific"})

    new_text = text.replace(old_string, new_string, 1)
    target.write_text(new_text, encoding="utf-8")
    return json.dumps({"ok": True, "path": str(target)})


@tool
async def list_directory(path: str = ".") -> str:
    """List contents of a directory in the workspace.

    Args:
        path: Relative path from workspace root (default: root).

    Returns:
        JSON array of entries with name and type.
    """
    target = _resolve_path(path)
    if not target.is_dir():
        return json.dumps({"error": f"Not a directory: {path}"})

    entries = []
    for item in sorted(target.iterdir()):
        entries.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "size": item.stat().st_size if item.is_file() else None,
        })
    return json.dumps(entries, indent=2)


@tool
async def search_files(pattern: str, path: str = ".") -> str:
    """Search for files matching a glob pattern.

    Args:
        pattern: Glob pattern (e.g. "**/*.py", "*.yaml")
        path: Directory to search from (relative to workspace root).

    Returns:
        JSON array of matching file paths.
    """
    target = _resolve_path(path)
    matches = [str(p.relative_to(_workspace_root)) for p in target.glob(pattern)]
    return json.dumps(matches[:100])  # cap results


@tool
async def grep_workspace(query: str, path: str = ".", file_pattern: str = "*") -> str:
    """Search file contents for a text pattern.

    Args:
        query: Text or regex to search for.
        path: Directory to search from (relative to workspace root).
        file_pattern: Glob filter for files to search (e.g. "*.py")

    Returns:
        JSON array of matches with file, line number, and text.
    """
    target = _resolve_path(path)
    import re

    matches = []
    try:
        regex = re.compile(query, re.IGNORECASE)
    except re.error:
        # Fall back to literal search
        regex = re.compile(re.escape(query), re.IGNORECASE)

    for filepath in target.rglob(file_pattern):
        if not filepath.is_file():
            continue
        # Skip binary, hidden, and huge files
        if filepath.suffix in (".pyc", ".db", ".sqlite", ".zip", ".gz", ".png", ".jpg"):
            continue
        if any(p.startswith(".") for p in filepath.parts):
            continue
        try:
            for i, line in enumerate(filepath.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if regex.search(line):
                    matches.append({
                        "file": str(filepath.relative_to(_workspace_root)),
                        "line": i,
                        "text": line.strip()[:200],
                    })
                    if len(matches) >= 50:
                        return json.dumps(matches)
        except Exception:
            continue

    return json.dumps(matches)


# ---------------------------------------------------------------------------
# Shell execution
# ---------------------------------------------------------------------------

@tool
async def run_shell(command: str, cwd: str = ".") -> str:
    """Run a shell command — you have full terminal/bash access.

    Use this to execute any shell command: ping, curl, python, pip, ls, cat,
    grep, git, docker, system utilities, scripts, etc. You can run anything
    that a terminal can run.

    Args:
        command: Shell command to execute (bash on Linux/Docker, PowerShell on Windows).
        cwd: Working directory relative to workspace root.

    Returns:
        JSON with exit_code, stdout, and stderr.
    """
    work_dir = _resolve_path(cwd)

    logger.info("Shell: %s (in %s)", command[:80], work_dir)

    try:
        # Use asyncio subprocess for non-blocking
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work_dir),
            env={**os.environ},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        return json.dumps({
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace")[:10000],
            "stderr": stderr.decode("utf-8", errors="replace")[:5000],
        })
    except asyncio.TimeoutError:
        return json.dumps({"error": "Command timed out after 120s"})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

async def _git(args: list[str], cwd: Path | None = None) -> dict:
    """Run a git command and return structured result."""
    work_dir = cwd or _workspace_root
    cmd = ["git"] + args

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(work_dir),
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

    return {
        "exit_code": proc.returncode,
        "stdout": stdout.decode("utf-8", errors="replace").strip(),
        "stderr": stderr.decode("utf-8", errors="replace").strip(),
    }


@tool
async def git_status(path: str = ".") -> str:
    """Get git status for a repository.

    Args:
        path: Relative path to the git repo root.

    Returns:
        JSON with current branch, staged, modified, and untracked files.
    """
    repo = _resolve_path(path)

    branch = await _git(["branch", "--show-current"], repo)
    status = await _git(["status", "--porcelain"], repo)

    staged, modified, untracked = [], [], []
    for line in status["stdout"].splitlines():
        if not line:
            continue
        idx, wt = line[0], line[1]
        fname = line[3:]
        if idx in ("A", "M", "D", "R"):
            staged.append(fname)
        if wt == "M":
            modified.append(fname)
        if idx == "?" and wt == "?":
            untracked.append(fname)

    return json.dumps({
        "branch": branch["stdout"],
        "staged": staged,
        "modified": modified,
        "untracked": untracked,
    }, indent=2)


@tool
async def git_diff(path: str = ".", staged: bool = False) -> str:
    """Get git diff for a repository.

    Args:
        path: Relative path to the git repo root.
        staged: If true, show staged (cached) diff instead of working tree.

    Returns:
        Diff text (truncated to 10000 chars).
    """
    repo = _resolve_path(path)
    args = ["diff", "--stat"]
    if staged:
        args.append("--cached")

    result = await _git(args, repo)
    return result["stdout"][:10000]


@tool
async def git_log(path: str = ".", count: int = 10) -> str:
    """Get recent git log entries.

    Args:
        path: Relative path to the git repo root.
        count: Number of recent commits (default 10).

    Returns:
        JSON array of commit summaries.
    """
    repo = _resolve_path(path)
    result = await _git(
        ["log", f"-{count}", "--pretty=format:%H|%an|%ad|%s", "--date=short"],
        repo,
    )

    commits = []
    for line in result["stdout"].splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({
                "hash": parts[0][:8],
                "author": parts[1],
                "date": parts[2],
                "message": parts[3],
            })

    return json.dumps(commits, indent=2)


@tool
async def git_add_commit(path: str, files: list[str], message: str) -> str:
    """Stage files and create a git commit.

    Args:
        path: Relative path to the git repo root.
        files: List of file paths to stage (relative to repo root). Use ["."] for all.
        message: Commit message.

    Returns:
        JSON with commit result.
    """
    repo = _resolve_path(path)

    # Stage files
    for f in files:
        add_result = await _git(["add", f], repo)
        if add_result["exit_code"] != 0:
            return json.dumps({"error": f"Failed to stage {f}: {add_result['stderr']}"})

    # Commit
    result = await _git(["commit", "-m", message], repo)
    return json.dumps({
        "ok": result["exit_code"] == 0,
        "output": result["stdout"] or result["stderr"],
    })


# ---------------------------------------------------------------------------
# Tool collections for agents
# ---------------------------------------------------------------------------

FILE_TOOLS = [read_file, write_file, edit_file, list_directory, search_files, grep_workspace]
SHELL_TOOLS = [run_shell]
GIT_TOOLS = [git_status, git_diff, git_log, git_add_commit]

# Full set for agents that need everything
ALL_WORKSPACE_TOOLS = FILE_TOOLS + SHELL_TOOLS + GIT_TOOLS
