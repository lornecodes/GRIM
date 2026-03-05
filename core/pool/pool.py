"""ExecutionPool — orchestrator for async job execution.

Ties together JobQueue + AgentSlots. Runs a main dispatch loop that
pulls jobs from the queue and assigns them to idle slots.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from core.pool.events import PoolEvent, PoolEventBus, PoolEventType
from core.pool.models import (
    ClarificationNeeded,
    Job,
    JobStatus,
    JobType,
)
from core.pool.queue import JobQueue
from core.pool.slot import AgentSlot
from core.pool.workspace import WorkspaceManager

logger = logging.getLogger(__name__)


class ExecutionPool:
    """Manages a pool of AgentSlots that execute jobs from the queue.

    Usage:
        pool = ExecutionPool(queue, config)
        await pool.start()
        # ... submit jobs via queue ...
        await pool.stop()
    """

    def __init__(self, queue: JobQueue, config: Any) -> None:
        self._queue = queue
        self._config = config
        self._slots: list[AgentSlot] = []
        self._tasks: dict[str, asyncio.Task] = {}
        self._task_workspaces: dict[str, str] = {}  # slot_id → workspace_id
        self._loop_task: Optional[asyncio.Task] = None
        self._running = False

        # Workspace manager for git worktree isolation
        workspace_root = getattr(config, "workspace_root", None)
        worktree_base = Path(workspace_root) / ".grim" / "worktrees" if workspace_root else None
        self._workspace_mgr: WorkspaceManager | None = (
            WorkspaceManager(worktree_base) if worktree_base else None
        )
        self._job_workspace_map: dict[str, str] = {}  # job_id → workspace_id

        # Event bus for push notifications
        self.events = PoolEventBus()

        # Build Kronos MCP env from config
        self._kronos_mcp_command = getattr(config, "kronos_mcp_command", "")
        self._kronos_mcp_env: dict[str, str] = {}
        vault_path = getattr(config, "vault_path", None)
        skills_path = getattr(config, "skills_path", None)
        if vault_path:
            self._kronos_mcp_env["KRONOS_VAULT_PATH"] = str(vault_path)
        if skills_path:
            self._kronos_mcp_env["KRONOS_SKILLS_PATH"] = str(skills_path)
        if workspace_root:
            self._kronos_mcp_env["KRONOS_WORKSPACE_ROOT"] = str(workspace_root)
        self._workspace_root = Path(workspace_root) if workspace_root else None

    async def start(self) -> None:
        """Initialize queue and start the dispatch loop."""
        await self._queue.initialize()

        # Create agent slots
        num_slots = getattr(self._config, "pool_num_slots", 2)
        max_turns = getattr(self._config, "pool_max_turns_per_job", 20)

        self._slots = [
            AgentSlot(
                slot_id=f"slot-{i}",
                kronos_mcp_command=self._kronos_mcp_command,
                kronos_mcp_env=self._kronos_mcp_env,
                max_turns=max_turns,
            )
            for i in range(num_slots)
        ]

        self._running = True
        self._loop_task = asyncio.create_task(self._main_loop())
        logger.info("ExecutionPool started: %d slots", num_slots)

    async def stop(self) -> None:
        """Gracefully shut down the pool."""
        self._running = False

        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

        # Wait for running jobs to finish (with timeout)
        if self._tasks:
            logger.info("Waiting for %d running jobs to finish...", len(self._tasks))
            timeout = getattr(self._config, "pool_job_timeout_secs", 300)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks.values(), return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for jobs — cancelling remaining")
                for task in self._tasks.values():
                    task.cancel()

        self._tasks.clear()

        # Cleanup remaining workspaces
        if self._workspace_mgr:
            count = await self._workspace_mgr.destroy_all()
            if count:
                logger.info("Cleaned up %d workspaces on shutdown", count)

        logger.info("ExecutionPool stopped")

    async def submit(self, job: Job) -> str:
        """Submit a job to the queue. Returns job_id."""
        job_id = await self._queue.submit(job)
        await self.events.emit(PoolEvent(
            type=PoolEventType.JOB_SUBMITTED,
            job_id=job_id,
            data={"job_type": job.job_type.value, "priority": job.priority.value},
        ))
        return job_id

    @property
    def queue(self) -> JobQueue:
        """Access the underlying queue."""
        return self._queue

    @property
    def status(self) -> dict[str, Any]:
        """Current pool state — slot statuses + queue counts."""
        slots_info = []
        for slot in self._slots:
            slots_info.append({
                "slot_id": slot.slot_id,
                "busy": slot.busy,
                "current_job_id": slot.current_job_id,
            })
        return {
            "running": self._running,
            "slots": slots_info,
            "active_jobs": len(self._tasks),
            "active_workspaces": self._workspace_mgr.active_count if self._workspace_mgr else 0,
        }

    # ── Main dispatch loop ───────────────────────────────────────

    async def _main_loop(self) -> None:
        """Poll queue and dispatch jobs to idle slots."""
        poll_interval = getattr(self._config, "pool_poll_interval", 2.0)

        while self._running:
            try:
                await self._dispatch_cycle()
            except Exception:
                logger.exception("Error in dispatch cycle")

            await asyncio.sleep(poll_interval)

    async def _dispatch_cycle(self) -> None:
        """Single dispatch cycle: find idle slots, pull jobs, launch."""
        # Clean up completed tasks
        done_slots = [
            sid for sid, task in self._tasks.items() if task.done()
        ]
        for sid in done_slots:
            task = self._tasks.pop(sid)
            if task.exception():
                logger.error("Slot %s task crashed: %s", sid, task.exception())

        # Find idle slots
        idle_slots = [s for s in self._slots if not s.busy]
        if not idle_slots:
            return

        # Determine busy workspaces (for sequential workspace execution)
        busy_workspaces: set[str] = set()
        for slot in self._slots:
            if slot.busy and slot.current_job_id:
                # Look up the job's workspace — we track it via task metadata
                ws = self._task_workspaces.get(slot.slot_id)
                if ws:
                    busy_workspaces.add(ws)

        # Pull jobs for idle slots
        for slot in idle_slots:
            job = await self._queue.next(busy_workspaces or None)
            if job is None:
                break  # No more queued jobs

            # Track workspace
            if job.workspace_id:
                self._task_workspaces[slot.slot_id] = job.workspace_id
                busy_workspaces.add(job.workspace_id)

            # Launch execution as async task
            task = asyncio.create_task(
                self._run_job(slot, job),
                name=f"pool-{slot.slot_id}-{job.id}",
            )
            self._tasks[slot.slot_id] = task

    async def _run_job(self, slot: AgentSlot, job: Job) -> None:
        """Execute a job on a slot, handling outcomes."""
        logger.info("Dispatching job %s to %s", job.id, slot.slot_id)

        # Mark running
        await self._queue.update_status(
            job.id, JobStatus.RUNNING, assigned_slot=slot.slot_id,
        )
        await self.events.emit(PoolEvent(
            type=PoolEventType.JOB_STARTED,
            job_id=job.id,
            data={"slot_id": slot.slot_id},
        ))

        # Create workspace for code jobs (git worktree isolation)
        workspace_id: str | None = None
        if (
            job.job_type == JobType.CODE
            and self._workspace_mgr
            and self._workspace_root
        ):
            try:
                ws = await self._workspace_mgr.create(job.id, self._workspace_root)
                workspace_id = ws.id
                self._job_workspace_map[job.id] = ws.id
                slot.cwd = str(ws.worktree_path)
                logger.info("Job %s using workspace %s", job.id, ws.id)
            except Exception as e:
                logger.warning("Failed to create workspace for %s: %s — running in main repo", job.id, e)

        try:
            result = await asyncio.wait_for(
                slot.execute(job),
                timeout=getattr(self._config, "pool_job_timeout_secs", 300),
            )
        except ClarificationNeeded as e:
            await self._queue.request_clarification(job.id, e.question)
            await self.events.emit(PoolEvent(
                type=PoolEventType.JOB_BLOCKED,
                job_id=job.id,
                data={"question": e.question},
            ))
            logger.info("Job %s blocked for clarification: %s", job.id, e.question)
            return
        except asyncio.TimeoutError:
            await self._queue.update_status(
                job.id, JobStatus.FAILED, error="Job timed out",
            )
            await self.events.emit(PoolEvent(
                type=PoolEventType.JOB_FAILED,
                job_id=job.id,
                data={"error": "Job timed out"},
            ))
            logger.error("Job %s timed out", job.id)
            return
        except Exception as e:
            await self._queue.update_status(
                job.id, JobStatus.FAILED, error=str(e),
            )
            await self.events.emit(PoolEvent(
                type=PoolEventType.JOB_FAILED,
                job_id=job.id,
                data={"error": str(e)},
            ))
            logger.error("Job %s crashed: %s", job.id, e)
            return
        finally:
            self._task_workspaces.pop(slot.slot_id, None)
            slot.cwd = None  # Reset slot working directory

        # Cleanup workspace on failure/cancel (keep on success for PR review)
        if workspace_id and self._workspace_mgr:
            # If job failed, destroy the worktree
            # If succeeded, leave it for potential PR/merge
            pass  # Cleanup handled below per-outcome

        # Handle result
        if result.success:
            await self._queue.update_status(
                job.id,
                JobStatus.COMPLETE,
                result=result.result,
                transcript=result.transcript,
            )
            await self.events.emit(PoolEvent(
                type=PoolEventType.JOB_COMPLETE,
                job_id=job.id,
                data={
                    "result_preview": (result.result or "")[:200],
                    "cost_usd": result.cost_usd,
                    "num_turns": result.num_turns,
                },
            ))
            logger.info("Job %s complete", job.id)
        else:
            # Retry logic
            if job.retry_count < job.max_retries:
                new_count = job.retry_count + 1
                await self._queue.update_status(
                    job.id,
                    JobStatus.QUEUED,
                    retry_count=new_count,
                    error=result.error,
                    transcript=result.transcript,
                )
                logger.info("Job %s failed, retrying (%d/%d)", job.id, new_count, job.max_retries)
            else:
                await self._queue.update_status(
                    job.id,
                    JobStatus.FAILED,
                    error=result.error,
                    transcript=result.transcript,
                )
                await self.events.emit(PoolEvent(
                    type=PoolEventType.JOB_FAILED,
                    job_id=job.id,
                    data={"error": result.error, "retries": job.max_retries},
                ))
                logger.info("Job %s failed after %d retries", job.id, job.max_retries)
                # Destroy workspace on final failure
                if workspace_id and self._workspace_mgr:
                    await self._workspace_mgr.destroy(workspace_id)
                    self._job_workspace_map.pop(job.id, None)
