"""
Pool MCP Server — execution pool introspection and control.

Hybrid architecture:
  - Read operations: direct SQLite access (WAL-safe concurrent reads)
  - Write operations: HTTP proxy to GRIM REST API (pool orchestrator owns state)

Follows the Kronos MCP pattern: lazy init, tool handler dict, serialization lock,
per-tool timeouts, file-based logging, Windows newline fix.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Sequence

from mcp.server import Server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource

from pool_mcp.db import PoolDB
from pool_mcp.skills import SkillsEngine

# ── Logging (file, not stderr — pipe buffer deadlock on Windows) ─────────────

_log_dir = os.getenv("POOL_LOG_DIR", os.path.join(os.path.dirname(__file__), "..", ".."))
try:
    _log_path = os.path.abspath(os.path.join(_log_dir, ".pool-mcp.log"))
    os.makedirs(os.path.dirname(_log_path), exist_ok=True)
    _handler = logging.FileHandler(_log_path, encoding="utf-8")
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"
    ))
    logging.root.addHandler(_handler)
    logging.root.setLevel(logging.INFO)
except Exception:
    logging.basicConfig(level=logging.WARNING)

logger = logging.getLogger("pool-mcp")

# ── Configuration ────────────────────────────────────────────────────────────

db_path = os.getenv("POOL_DB_PATH", "")
grim_api = os.getenv("GRIM_API_URL", "http://localhost:8000")
workspace_root = os.getenv("POOL_WORKSPACE_ROOT", "")
skills_path = os.getenv("POOL_SKILLS_PATH", os.path.join(
    os.path.dirname(__file__), "..", "..", "skills"
))

# ── Global engines (lazy-init) ───────────────────────────────────────────────

pool_db: PoolDB | None = None
skills_engine: SkillsEngine | None = None
_engines_initialized = False


def _ensure_initialized():
    """Lazy-init on first tool call. Must be called under _tool_lock."""
    global pool_db, skills_engine, _engines_initialized
    if _engines_initialized:
        return
    if not db_path:
        raise RuntimeError("POOL_DB_PATH environment variable is required")

    t0 = time.time()
    pool_db = PoolDB(db_path)
    skills_engine = SkillsEngine(skills_path) if skills_path else None
    _engines_initialized = True
    logger.info("Pool MCP initialized in %.1fs (db=%s, skills=%s)",
                time.time() - t0, db_path, skills_path)


# ── Tool definitions ─────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    # ── pool:read ────────────────────────────────────────────────────
    Tool(
        name="pool_status",
        description="Get execution pool overview: job counts by status, queue depth.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="pool_list_jobs",
        description="List pool jobs with optional filters. Returns newest first.",
        inputSchema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["queued", "running", "complete", "failed",
                             "blocked", "cancelled", "review"],
                    "description": "Filter by job status",
                },
                "job_type": {
                    "type": "string",
                    "enum": ["code", "research", "audit", "plan", "index"],
                    "description": "Filter by agent type",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "Max results (default 20, max 100)",
                },
                "since": {
                    "type": "string",
                    "description": "ISO date — only jobs created after this (YYYY-MM-DD)",
                },
            },
        },
    ),
    Tool(
        name="pool_job_detail",
        description="Get full details for a specific job including transcript, cost, and workspace info.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID to look up",
                },
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="pool_job_logs",
        description="Get transcript/output lines for a job with pagination. Use for reading agent conversation history.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID",
                },
                "offset": {
                    "type": "integer",
                    "default": 0,
                    "description": "Skip first N lines",
                },
                "limit": {
                    "type": "integer",
                    "default": 50,
                    "description": "Max lines to return (default 50, max 200)",
                },
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="pool_list_workspaces",
        description="List active git worktree workspaces created by pool agents.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="pool_workspace_diff",
        description="Get the git diff for a workspace (changes made by the agent).",
        inputSchema={
            "type": "object",
            "properties": {
                "workspace_id": {
                    "type": "string",
                    "description": "Workspace ID",
                },
            },
            "required": ["workspace_id"],
        },
    ),
    Tool(
        name="pool_metrics",
        description="Aggregated pool metrics: completion rates, counts by type.",
        inputSchema={"type": "object", "properties": {}},
    ),
    # ── pool:write ───────────────────────────────────────────────────
    Tool(
        name="pool_submit",
        description="Submit a new job to the execution pool. Returns the queued job ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_type": {
                    "type": "string",
                    "enum": ["code", "research", "audit", "plan"],
                    "description": "Type of agent to execute the job",
                },
                "instructions": {
                    "type": "string",
                    "description": "What the agent should do",
                },
                "priority": {
                    "type": "string",
                    "enum": ["critical", "high", "normal", "low"],
                    "default": "normal",
                    "description": "Job priority (default normal)",
                },
                "kronos_fdo_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Kronos FDO IDs for context (optional)",
                },
            },
            "required": ["job_type", "instructions"],
        },
    ),
    Tool(
        name="pool_cancel",
        description="Cancel a queued or blocked job.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID to cancel",
                },
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="pool_clarify",
        description="Provide a clarification answer to a blocked job that asked a question.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID that is blocked",
                },
                "answer": {
                    "type": "string",
                    "description": "Answer to the agent's clarification question",
                },
            },
            "required": ["job_id", "answer"],
        },
    ),
    Tool(
        name="pool_retry",
        description="Re-queue a failed job for another attempt.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Failed job ID to retry",
                },
            },
            "required": ["job_id"],
        },
    ),
    Tool(
        name="pool_review",
        description="Approve or reject a completed job. Approve merges the workspace; reject destroys it.",
        inputSchema={
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID in review status",
                },
                "action": {
                    "type": "string",
                    "enum": ["approve", "reject"],
                    "description": "Approve (merge workspace) or reject (destroy workspace)",
                },
            },
            "required": ["job_id", "action"],
        },
    ),
    # ── system ───────────────────────────────────────────────────────
    Tool(
        name="pool_skills",
        description=(
            "List all available pool skills. Skills are instruction protocols "
            "that tell you how to perform complex pool tasks (job management, "
            "agent monitoring, workspace review). Load a skill with pool_skill_load."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="pool_skill_load",
        description=(
            "Load the full instruction protocol for a pool skill. Returns the "
            "complete step-by-step protocol including phases, quality gates, "
            "and guidelines. Follow the protocol to perform the task correctly."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name (e.g., 'pool-manage', 'agent-monitor', 'workspace-manage')",
                },
            },
            "required": ["name"],
        },
    ),
]

# ── Tool groups (access control) ─────────────────────────────────────────────

TOOL_GROUPS = {
    "pool:read": [
        "pool_status", "pool_list_jobs", "pool_job_detail", "pool_job_logs",
        "pool_list_workspaces", "pool_workspace_diff", "pool_metrics",
    ],
    "pool:write": [
        "pool_submit", "pool_cancel", "pool_clarify", "pool_retry", "pool_review",
    ],
    "system": [
        "pool_skills", "pool_skill_load",
    ],
}

# ── Timeouts ─────────────────────────────────────────────────────────────────

TOOL_TIMEOUTS: dict[str, float] = {
    "pool_status": 10,
    "pool_list_jobs": 10,
    "pool_job_detail": 10,
    "pool_job_logs": 10,
    "pool_metrics": 10,
    "pool_list_workspaces": 15,
    "pool_workspace_diff": 15,
    "pool_submit": 30,
    "pool_cancel": 30,
    "pool_clarify": 30,
    "pool_retry": 30,
    "pool_review": 30,
    "pool_skills": 10,
    "pool_skill_load": 10,
}
DEFAULT_TIMEOUT: float = 15.0

# ── Helpers ──────────────────────────────────────────────────────────────────


def _json(obj: Any) -> str:
    """Serialize to JSON."""
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)


def _http_get(path: str) -> dict:
    """GET from GRIM API."""
    import requests
    try:
        resp = requests.get(f"{grim_api}{path}", timeout=25)
        return resp.json()
    except Exception as e:
        return {"error": f"GRIM API unreachable: {e}"}


def _http_post(path: str, body: dict | None = None) -> dict:
    """POST to GRIM API."""
    import requests
    try:
        resp = requests.post(
            f"{grim_api}{path}",
            json=body or {},
            headers={"Content-Type": "application/json"},
            timeout=25,
        )
        return resp.json()
    except Exception as e:
        return {"error": f"GRIM API unreachable: {e}"}


# ── Read handlers (SQLite) ───────────────────────────────────────────────────


def handle_pool_status(args: dict) -> str:
    return _json(pool_db.get_stats())


def handle_pool_list_jobs(args: dict) -> str:
    status = (args.get("status") or "").strip() or None
    job_type = (args.get("job_type") or "").strip() or None
    limit = min(int(args.get("limit", 20)), 100)
    since = (args.get("since") or "").strip() or None
    jobs = pool_db.list_jobs(status=status, job_type=job_type, limit=limit, since=since)
    return _json({"jobs": jobs, "count": len(jobs)})


def handle_pool_job_detail(args: dict) -> str:
    job_id = (args.get("job_id") or "").strip()
    if not job_id:
        return _json({"error": "job_id required"})
    job = pool_db.get_job(job_id)
    if job is None:
        return _json({"error": f"Job not found: {job_id}"})
    return _json(job)


def handle_pool_job_logs(args: dict) -> str:
    job_id = (args.get("job_id") or "").strip()
    if not job_id:
        return _json({"error": "job_id required"})
    offset = max(0, int(args.get("offset", 0)))
    limit = min(max(1, int(args.get("limit", 50))), 200)
    return _json(pool_db.get_transcript(job_id, offset=offset, limit=limit))


def handle_pool_list_workspaces(args: dict) -> str:
    return _json(_http_get("/api/pool/workspaces"))


def handle_pool_workspace_diff(args: dict) -> str:
    ws_id = (args.get("workspace_id") or "").strip()
    if not ws_id:
        return _json({"error": "workspace_id required"})
    return _json(_http_get(f"/api/pool/workspaces/{ws_id}/diff"))


def handle_pool_metrics(args: dict) -> str:
    return _json(pool_db.get_metrics())


# ── Write handlers (HTTP proxy) ──────────────────────────────────────────────


def handle_pool_submit(args: dict) -> str:
    job_type = (args.get("job_type") or "").strip()
    instructions = (args.get("instructions") or "").strip()
    if not job_type or not instructions:
        return _json({"error": "job_type and instructions required"})

    body: dict[str, Any] = {
        "job_type": job_type,
        "instructions": instructions,
    }
    if args.get("priority"):
        body["priority"] = args["priority"]
    if args.get("kronos_fdo_ids"):
        body["kronos_fdo_ids"] = args["kronos_fdo_ids"]

    return _json(_http_post("/api/pool/jobs", body))


def handle_pool_cancel(args: dict) -> str:
    job_id = (args.get("job_id") or "").strip()
    if not job_id:
        return _json({"error": "job_id required"})
    return _json(_http_post(f"/api/pool/jobs/{job_id}/cancel"))


def handle_pool_clarify(args: dict) -> str:
    job_id = (args.get("job_id") or "").strip()
    answer = (args.get("answer") or "").strip()
    if not job_id:
        return _json({"error": "job_id required"})
    if not answer:
        return _json({"error": "answer required"})
    return _json(_http_post(f"/api/pool/jobs/{job_id}/clarify", {"answer": answer}))


def handle_pool_retry(args: dict) -> str:
    job_id = (args.get("job_id") or "").strip()
    if not job_id:
        return _json({"error": "job_id required"})
    return _json(_http_post(f"/api/pool/jobs/{job_id}/retry"))


def handle_pool_review(args: dict) -> str:
    job_id = (args.get("job_id") or "").strip()
    action = (args.get("action") or "").strip()
    if not job_id:
        return _json({"error": "job_id required"})
    if action not in ("approve", "reject"):
        return _json({"error": "action must be 'approve' or 'reject'"})
    return _json(_http_post(f"/api/pool/jobs/{job_id}/review", {"action": action}))


# ── Skill handlers ───────────────────────────────────────────────────────────


def handle_pool_skills(args: dict) -> str:
    if not skills_engine:
        return _json({"error": "Skills engine not initialized"})
    skills_engine.refresh()
    return _json({"skills": skills_engine.list_skills()})


def handle_pool_skill_load(args: dict) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return _json({"error": "name required"})
    if not skills_engine:
        return _json({"error": "Skills engine not initialized"})
    skills_engine.refresh()
    skill = skills_engine.get_skill(name)
    if not skill:
        available = [s["name"] for s in skills_engine.list_skills()]
        return _json({"error": f"Skill not found: {name}", "available": available})
    return _json({
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "type": skill.skill_type,
        "phases": skill.phases,
        "permissions": skill.permissions,
        "quality_gates": skill.quality_gates,
        "protocol": skill.protocol,
    })


# ── Handler dispatch table ───────────────────────────────────────────────────

HANDLERS: dict[str, Any] = {
    "pool_status": handle_pool_status,
    "pool_list_jobs": handle_pool_list_jobs,
    "pool_job_detail": handle_pool_job_detail,
    "pool_job_logs": handle_pool_job_logs,
    "pool_list_workspaces": handle_pool_list_workspaces,
    "pool_workspace_diff": handle_pool_workspace_diff,
    "pool_metrics": handle_pool_metrics,
    "pool_submit": handle_pool_submit,
    "pool_cancel": handle_pool_cancel,
    "pool_clarify": handle_pool_clarify,
    "pool_retry": handle_pool_retry,
    "pool_review": handle_pool_review,
    "pool_skills": handle_pool_skills,
    "pool_skill_load": handle_pool_skill_load,
}

# ── MCP Server ───────────────────────────────────────────────────────────────

_tool_lock: asyncio.Lock | None = None


def _get_tool_lock() -> asyncio.Lock:
    global _tool_lock
    if _tool_lock is None:
        _tool_lock = asyncio.Lock()
    return _tool_lock


app = Server("pool-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(
    name: str, arguments: Any
) -> Sequence[TextContent | ImageContent | EmbeddedResource]:
    if not isinstance(arguments, dict):
        arguments = {}

    handler = HANDLERS.get(name)
    if not handler:
        raise ValueError(f"Unknown tool: {name}")

    async with _get_tool_lock():
        if not _engines_initialized:
            await asyncio.to_thread(_ensure_initialized)

        try:
            timeout = TOOL_TIMEOUTS.get(name, DEFAULT_TIMEOUT)
            result = await asyncio.wait_for(
                asyncio.to_thread(handler, arguments),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error("Tool %s timed out after %.0fs", name, timeout)
            return [TextContent(
                type="text",
                text=_json({"error": f"{name} timed out after {timeout}s"}),
            )]
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e, exc_info=True)
            return [TextContent(
                type="text",
                text=_json({"error": str(e)}),
            )]

    return [TextContent(type="text", text=result)]


# ── Entry point ──────────────────────────────────────────────────────────────

async def main():
    """Start the MCP stdio server."""
    from mcp.server.stdio import stdio_server
    import anyio
    from io import TextIOWrapper
    import sys

    logger.info("Pool MCP starting (db=%s, api=%s)", db_path, grim_api)

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=16))

    # Windows: fix \r\n → \n for MCP protocol
    fixed_stdout = anyio.wrap_file(
        TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="")
    )

    async with stdio_server(stdout=fixed_stdout) as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )
