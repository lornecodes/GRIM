"""Pool tools — submit and monitor execution pool jobs.

LangChain tools for graph agents to interact with the execution pool.
Pool must be enabled in config and started before these tools function.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from langchain_core.tools import tool

from core.tools.context import tool_context

logger = logging.getLogger(__name__)


def _get_pool():
    """Get the execution pool from tool context, or raise."""
    pool = tool_context.execution_pool
    if pool is None:
        raise RuntimeError("Execution pool is not enabled or not started")
    return pool


# ── Tools ────────────────────────────────────────────────────────

@tool
async def pool_submit(
    job_type: str,
    instructions: str,
    priority: str = "normal",
    plan: Optional[str] = None,
    target_repo: Optional[str] = None,
    workspace_id: Optional[str] = None,
    kronos_domains: Optional[str] = None,
    kronos_fdo_ids: Optional[str] = None,
) -> str:
    """Submit a new job to the execution pool.

    Args:
        job_type: Type of job — "code", "research", "audit", or "plan".
        instructions: What the agent should do.
        priority: Job priority — "critical", "high", "normal", "low", "background".
        plan: Optional implementation plan for the agent to follow.
        target_repo: Target repository name (e.g. "GRIM", "dawn-field-theory"). Agent gets an isolated git worktree.
        workspace_id: Optional workspace to bind the job to (sequential execution).
        kronos_domains: Comma-separated Kronos domains for context (e.g. "physics,ai-systems").
        kronos_fdo_ids: Comma-separated FDO IDs for context (e.g. "pac-comprehensive,grim-architecture").

    Returns:
        Job ID and confirmation.
    """
    try:
        from core.pool.models import Job, JobPriority, JobType

        pool = _get_pool()

        # Parse enums
        try:
            jt = JobType(job_type.lower())
        except ValueError:
            return f"[ERROR] Invalid job_type: {job_type}. Use: code, research, audit, plan"

        try:
            jp = JobPriority(priority.lower())
        except ValueError:
            return f"[ERROR] Invalid priority: {priority}. Use: critical, high, normal, low, background"

        # Parse comma-separated lists
        domains = [d.strip() for d in kronos_domains.split(",")] if kronos_domains else []
        fdo_ids = [f.strip() for f in kronos_fdo_ids.split(",")] if kronos_fdo_ids else []

        job = Job(
            job_type=jt,
            priority=jp,
            instructions=instructions,
            plan=plan,
            target_repo=target_repo,
            workspace_id=workspace_id,
            kronos_domains=domains,
            kronos_fdo_ids=fdo_ids,
        )

        job_id = await pool.submit(job)
        return f"Job submitted: {job_id} (type={job_type}, priority={priority})"

    except RuntimeError as e:
        return f"[ERROR] {e}"
    except Exception as e:
        logger.exception("pool_submit failed")
        return f"[ERROR] Failed to submit job: {e}"


@tool
async def pool_status() -> str:
    """Get the current execution pool status.

    Returns slot states (busy/idle) and active job count.
    """
    try:
        pool = _get_pool()
        status = pool.status
        lines = [f"Pool running: {status['running']}", f"Active jobs: {status['active_jobs']}", ""]
        for slot in status["slots"]:
            state = f"BUSY (job: {slot['current_job_id']})" if slot["busy"] else "IDLE"
            lines.append(f"  {slot['slot_id']}: {state}")
        return "\n".join(lines)
    except RuntimeError as e:
        return f"[ERROR] {e}"
    except Exception as e:
        logger.exception("pool_status failed")
        return f"[ERROR] {e}"


@tool
async def pool_job_status(job_id: str) -> str:
    """Get full details of a specific pool job.

    Args:
        job_id: The job identifier (e.g. "job-a1b2c3d4").

    Returns:
        Job details including status, type, instructions, and result.
    """
    try:
        pool = _get_pool()
        job = await pool.queue.get(job_id)
        if job is None:
            return f"[ERROR] Job not found: {job_id}"

        lines = [
            f"Job: {job.id}",
            f"Type: {job.job_type.value}",
            f"Status: {job.status.value}",
            f"Priority: {job.priority.value}",
            f"Created: {job.created_at.isoformat()}",
            f"Updated: {job.updated_at.isoformat()}",
        ]
        if job.workspace_id:
            lines.append(f"Workspace: {job.workspace_id}")
        if job.assigned_slot:
            lines.append(f"Slot: {job.assigned_slot}")
        if job.retry_count > 0:
            lines.append(f"Retries: {job.retry_count}/{job.max_retries}")
        if job.clarification_question:
            lines.append(f"Clarification Q: {job.clarification_question}")
        if job.clarification_answer:
            lines.append(f"Clarification A: {job.clarification_answer}")

        lines.append(f"\nInstructions: {job.instructions[:500]}")

        if job.result:
            lines.append(f"\nResult: {job.result[:1000]}")
        if job.error:
            lines.append(f"\nError: {job.error}")

        return "\n".join(lines)

    except RuntimeError as e:
        return f"[ERROR] {e}"
    except Exception as e:
        logger.exception("pool_job_status failed")
        return f"[ERROR] {e}"


@tool
async def pool_cancel(job_id: str) -> str:
    """Cancel a queued or blocked pool job.

    Args:
        job_id: The job identifier to cancel.

    Returns:
        Confirmation or error if job cannot be cancelled.
    """
    try:
        pool = _get_pool()
        success = await pool.queue.cancel(job_id)
        if success:
            return f"Job cancelled: {job_id}"
        return f"[ERROR] Cannot cancel job {job_id} (may be running or already finished)"
    except RuntimeError as e:
        return f"[ERROR] {e}"
    except Exception as e:
        logger.exception("pool_cancel failed")
        return f"[ERROR] {e}"


@tool
async def pool_list_jobs(status_filter: Optional[str] = None, limit: int = 20) -> str:
    """List jobs in the execution pool queue.

    Args:
        status_filter: Optional status to filter by (queued/running/complete/failed/cancelled).
        limit: Maximum number of jobs to return (default 20).

    Returns:
        Formatted list of jobs with ID, type, status, and priority.
    """
    try:
        from core.pool.models import JobStatus

        pool = _get_pool()

        sf = None
        if status_filter:
            try:
                sf = JobStatus(status_filter.lower())
            except ValueError:
                return f"[ERROR] Invalid status: {status_filter}"

        jobs = await pool.queue.list_jobs(status_filter=sf, limit=limit)
        if not jobs:
            return "No jobs found"

        lines = [f"Jobs ({len(jobs)}):", ""]
        for job in jobs:
            age = ""
            lines.append(
                f"  {job.id}  {job.job_type.value:<10} "
                f"{job.status.value:<10} {job.priority.value:<10} "
                f"{job.instructions[:60]}"
            )
        return "\n".join(lines)

    except RuntimeError as e:
        return f"[ERROR] {e}"
    except Exception as e:
        logger.exception("pool_list_jobs failed")
        return f"[ERROR] {e}"


# ── Tool groups ──────────────────────────────────────────────────

POOL_READ_TOOLS = [pool_status, pool_job_status, pool_list_jobs]
POOL_WRITE_TOOLS = [pool_submit, pool_cancel]
POOL_TOOLS = POOL_READ_TOOLS + POOL_WRITE_TOOLS

# Register with tool registry
from core.tools.registry import tool_registry

tool_registry.register_group("pool_read", POOL_READ_TOOLS)
tool_registry.register_group("pool_write", POOL_WRITE_TOOLS)
tool_registry.register_group("pool", POOL_TOOLS)
