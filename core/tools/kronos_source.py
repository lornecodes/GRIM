"""Source navigation tools — codebase spatial awareness for the Codebase Agent.

These tools provide read-only access to source code across the workspace.
Four wrap existing Kronos MCP tools (navigate, read_source, search_source,
deep_dive). Two add repo-aware git operations. Two support deep indexing
and incremental sync.

Trust boundary: ALL tools are read-only. The only write path for code
changes is submit_pull_request (deferred to Phase 4).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.tools import tool

from core.tools.context import tool_context
from core.tools.kronos_read import _call_mcp
from core.tools.workspace import _get_workspace_root, _git, _resolve_path

logger = logging.getLogger(__name__)


def _resolve_repo(repo: str) -> Path:
    """Resolve a repo name to its workspace path.

    Args:
        repo: Repository name (e.g., "GRIM", "dawn-field-theory").

    Returns:
        Absolute path to the repo directory.

    Raises:
        ValueError: If repo path escapes workspace or doesn't exist.
    """
    return _resolve_path(repo)


# ---------------------------------------------------------------------------
# MCP source tool wrappers
# ---------------------------------------------------------------------------


@tool
async def kronos_navigate(path: str) -> str:
    """Read directory metadata (meta.yaml) from any repo path.

    Returns description, semantic scope, files, and child directories.
    Falls back to basic file listing if no meta.yaml exists.

    Args:
        path: Relative path from workspace root (e.g., "dawn-field-theory/foundational").

    Returns:
        JSON with directory structure and metadata.
    """
    result = await _call_mcp("kronos_navigate", path=path)
    return json.dumps(result, indent=2)


@tool
async def kronos_read_source(
    repo: str, path: str, offset: int = 0, max_lines: int = 200
) -> str:
    """Read file content from a source path within the workspace.

    Supports pagination via offset/max_lines for large files.

    Args:
        repo: Repository name (e.g., "GRIM", "fracton").
        path: Relative path within the repo (e.g., "core/agents/base.py").
        offset: Line offset to start reading from (0-based, default 0).
        max_lines: Maximum lines to return (default 200, max 500).

    Returns:
        JSON with content, line_count, offset, lines_returned.
    """
    result = await _call_mcp(
        "kronos_read_source",
        repo=repo,
        path=path,
        offset=offset,
        max_lines=max_lines,
    )
    return json.dumps(result, indent=2)


@tool
async def kronos_search_source(
    query: str,
    pattern: str,
    depth: int = 0,
    max_matches: int = 30,
    context_lines: int = 2,
) -> str:
    """Search within source files referenced by an FDO's source_paths.

    Combines concept resolution with content grep — go from FDO ID to
    matching code lines in one call.

    Args:
        query: FDO ID (exact) or search query to find the concept.
        pattern: Text pattern to search for (case-insensitive substring).
        depth: Hops of related FDOs to include (default 0, max 3).
        max_matches: Maximum match groups to return (default 30).
        context_lines: Lines of context around each match (default 2).

    Returns:
        JSON with files_searched, files_with_matches, total_hits, matches.
    """
    result = await _call_mcp(
        "kronos_search_source",
        query=query,
        pattern=pattern,
        depth=depth,
        max_matches=max_matches,
        context_lines=context_lines,
    )
    return json.dumps(result, indent=2)


@tool
async def kronos_deep_dive(
    query: str, depth: int = 1, type_filter: str = ""
) -> str:
    """Gather all source material paths for a concept.

    Takes an FDO ID or search query and returns structured source_paths
    from the FDO and its related FDOs (up to depth hops).

    Args:
        query: FDO ID (exact) or search query.
        depth: How many hops of related FDOs to include (default 1, max 3).
        type_filter: Filter by type (experiment/script/module/doc/config/data).
                     Empty string for all types.

    Returns:
        JSON with sources_by_fdo and sources_by_repo.
    """
    kwargs: dict = {"query": query, "depth": depth}
    if type_filter:
        kwargs["type_filter"] = type_filter
    result = await _call_mcp("kronos_deep_dive", **kwargs)
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Repo-aware git tools
# ---------------------------------------------------------------------------


@tool
async def git_log_repo(repo: str, count: int = 10, since: str = "") -> str:
    """Get recent git log for a specific repo in the workspace.

    Args:
        repo: Repository name (e.g., "dawn-field-theory", "fracton").
        count: Number of recent commits (default 10).
        since: Optional date filter (e.g., "2026-02-28"). Empty for no filter.

    Returns:
        JSON array of commit summaries (hash, author, date, message).
    """
    repo_path = _resolve_repo(repo)
    if not repo_path.exists():
        return json.dumps({"error": f"Repository not found: {repo}"})

    args = ["log", f"-{count}", "--pretty=format:%H|%an|%ad|%s", "--date=short"]
    if since:
        args.append(f"--since={since}")

    result = await _git(args, repo_path)
    if result["exit_code"] != 0:
        return json.dumps({"error": result["stderr"] or "git log failed"})

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
async def git_diff_repo(repo: str, ref: str = "HEAD~1") -> str:
    """Get git diff stat for a specific repo against a reference.

    Args:
        repo: Repository name.
        ref: Git reference to diff against (default HEAD~1).

    Returns:
        JSON with diff summary (files changed, insertions, deletions).
    """
    repo_path = _resolve_repo(repo)
    if not repo_path.exists():
        return json.dumps({"error": f"Repository not found: {repo}"})

    result = await _git(["diff", "--stat", ref], repo_path)
    if result["exit_code"] != 0:
        return json.dumps({"error": result["stderr"] or "git diff failed"})

    return json.dumps({"diff_stat": result["stdout"][:10000]})


# ---------------------------------------------------------------------------
# Deep indexing + incremental sync
# ---------------------------------------------------------------------------


@tool
async def deep_index_repo(repo: str) -> str:
    """Build a comprehensive understanding document for a repository.

    Traverses the repo's directory structure via meta.yaml hierarchy,
    reads key files (README, pyproject.toml), and produces a structured
    summary. Store the result in GRIM memory for persistent awareness.

    Args:
        repo: Repository name (e.g., "fracton", "dawn-field-theory").

    Returns:
        JSON with repo understanding: description, structure, key modules,
        entry points, dependencies, recent activity.
    """
    repo_path = _resolve_repo(repo)
    if not repo_path.exists():
        return json.dumps({"error": f"Repository not found: {repo}"})

    index: dict = {
        "repo": repo,
        "path": str(repo_path),
        "structure": {},
        "key_files": {},
        "recent_commits": [],
    }

    # 1. Root meta.yaml / directory listing via MCP
    root_nav = await _call_mcp("kronos_navigate", path=repo)
    if isinstance(root_nav, dict) and "error" not in root_nav:
        index["structure"]["root"] = {
            "description": root_nav.get("description", ""),
            "semantic_scope": root_nav.get("semantic_scope", ""),
            "files": root_nav.get("files", []),
            "child_dirs": root_nav.get("child_directories", []),
        }

        # 2. Navigate top-level subdirectories (depth 1)
        for child in root_nav.get("child_directories", [])[:15]:
            child_path = f"{repo}/{child}"
            child_nav = await _call_mcp("kronos_navigate", path=child_path)
            if isinstance(child_nav, dict) and "error" not in child_nav:
                index["structure"][child] = {
                    "description": child_nav.get("description", ""),
                    "files": child_nav.get("files", [])[:20],
                    "child_dirs": child_nav.get("child_directories", []),
                }

    # 3. Read key config files
    for key_file in ["README.md", "pyproject.toml", "setup.py", "Cargo.toml", "package.json"]:
        file_path = repo_path / key_file
        if file_path.exists():
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                # Truncate large files
                index["key_files"][key_file] = content[:3000]
            except Exception:
                pass

    # 4. Recent git activity
    log_result = await _git(
        ["log", "-5", "--pretty=format:%H|%an|%ad|%s", "--date=short"],
        repo_path,
    )
    if log_result["exit_code"] == 0:
        for line in log_result["stdout"].splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                index["recent_commits"].append({
                    "hash": parts[0][:8],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })

    # 5. Language/tech detection
    techs = []
    if (repo_path / "pyproject.toml").exists() or (repo_path / "setup.py").exists():
        techs.append("python")
    if (repo_path / "package.json").exists():
        techs.append("node")
    if (repo_path / "Cargo.toml").exists():
        techs.append("rust")
    if (repo_path / "Dockerfile").exists() or (repo_path / "docker-compose.yml").exists():
        techs.append("docker")
    index["technologies"] = techs

    return json.dumps(index, indent=2)


@tool
async def repo_changes_since(repo: str, since: str) -> str:
    """Detect changes in a repo since a given date or commit.

    Uses git log + git diff to find what changed. Useful for
    incremental sync after deep indexing.

    Args:
        repo: Repository name.
        since: Date (YYYY-MM-DD) or commit hash.

    Returns:
        JSON with new_commits, files_changed, summary.
    """
    repo_path = _resolve_repo(repo)
    if not repo_path.exists():
        return json.dumps({"error": f"Repository not found: {repo}"})

    changes: dict = {"repo": repo, "since": since, "new_commits": [], "diff_stat": ""}

    # Get commits since date/ref
    log_result = await _git(
        ["log", f"--since={since}", "--pretty=format:%H|%an|%ad|%s", "--date=short"],
        repo_path,
    )
    if log_result["exit_code"] == 0:
        for line in log_result["stdout"].splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                changes["new_commits"].append({
                    "hash": parts[0][:8],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                })

    # Get diff stat
    diff_result = await _git(["diff", "--stat", f"HEAD@{{{since}}}"], repo_path)
    if diff_result["exit_code"] == 0:
        changes["diff_stat"] = diff_result["stdout"][:5000]
    else:
        # Fallback: diff against oldest commit in range
        if changes["new_commits"]:
            oldest = changes["new_commits"][-1]["hash"]
            diff_result = await _git(["diff", "--stat", oldest], repo_path)
            if diff_result["exit_code"] == 0:
                changes["diff_stat"] = diff_result["stdout"][:5000]

    changes["commit_count"] = len(changes["new_commits"])
    return json.dumps(changes, indent=2)


# ---------------------------------------------------------------------------
# Tool collections
# ---------------------------------------------------------------------------

SOURCE_NAV_TOOLS = [kronos_navigate, kronos_read_source, kronos_search_source, kronos_deep_dive]
SOURCE_GIT_TOOLS = [git_log_repo, git_diff_repo]
SOURCE_INDEX_TOOLS = [deep_index_repo, repo_changes_since]
SOURCE_ALL_TOOLS = SOURCE_NAV_TOOLS + SOURCE_GIT_TOOLS + SOURCE_INDEX_TOOLS

# Register with tool registry
from core.tools.registry import tool_registry

tool_registry.register_group("source_nav", SOURCE_NAV_TOOLS)
tool_registry.register_group("source_git", SOURCE_GIT_TOOLS)
tool_registry.register_group("source_index", SOURCE_INDEX_TOOLS)
tool_registry.register_group("source", SOURCE_ALL_TOOLS)
