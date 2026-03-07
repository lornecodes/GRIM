"""ExecutionPool — orchestrator for async job execution.

Ties together JobQueue + AgentSlots. Runs a main dispatch loop that
pulls jobs from the queue and assigns them to idle slots.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

from core.pool.events import PoolEvent, PoolEventBus, PoolEventType
from core.pool.models import (
    ClarificationNeeded,
    Job,
    JobStatus,
    JobType,
)
from core.pool.codebase import CodebaseManager
from core.pool.locks import ResourceLock
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
        self._workspace_root = Path(workspace_root) if workspace_root else None
        worktree_base = Path(workspace_root) / ".grim" / "worktrees" if workspace_root else None
        self._workspace_mgr: WorkspaceManager | None = (
            WorkspaceManager(worktree_base) if worktree_base else None
        )
        self._job_workspace_map: dict[str, str] = {}  # job_id → workspace_id

        # Codebase manager for bare-cache repo cloning
        cache_dir = Path(workspace_root) / "local" / "repos" if workspace_root else None
        self._codebase_mgr: CodebaseManager | None = None
        if cache_dir and self._workspace_root:
            self._codebase_mgr = CodebaseManager(
                cache_dir=cache_dir,
                workspace_root=self._workspace_root,
                repos_manifest=getattr(config, "repos_manifest", "repos.yaml"),
            )

        # Event bus for push notifications
        self.events = PoolEventBus()

        # Resource lock for shared operations (pytest, pip, npm, git main)
        self._resource_lock = ResourceLock()

        # Build Kronos MCP config — prefer SSE URL over stdio command
        self._kronos_mcp_url = getattr(config, "pool_kronos_url", "") or ""
        self._kronos_mcp_command = getattr(config, "kronos_mcp_command", "")
        self._kronos_mcp_args: list[str] = getattr(config, "kronos_mcp_args", ["-m", "kronos_mcp"])
        self._kronos_mcp_env: dict[str, str] = {}
        vault_path = getattr(config, "vault_path", None)
        skills_path = getattr(config, "skills_path", None)
        if vault_path:
            self._kronos_mcp_env["KRONOS_VAULT_PATH"] = str(vault_path)
        if skills_path:
            self._kronos_mcp_env["KRONOS_SKILLS_PATH"] = str(skills_path)
        if workspace_root:
            self._kronos_mcp_env["KRONOS_WORKSPACE_ROOT"] = str(workspace_root)

        # Managed Kronos SSE subprocess (auto-started if no external URL)
        self._kronos_process: Optional[asyncio.subprocess.Process] = None
        self._kronos_sse_port: int = 8319

    async def start(self) -> None:
        """Initialize queue, refresh caches, warm Kronos, and start dispatch loop."""
        import time as _time
        t_start = _time.time()

        await self._queue.initialize()

        # Clean up stale worktrees from previous runs in the BACKGROUND.
        # Bind-mount I/O on Docker Desktop is very slow for large repos
        # (10K files → 2+ min to delete), so this must not block startup.
        if self._workspace_mgr and self._workspace_root:
            async def _bg_cleanup() -> None:
                try:
                    repo_dirs = [
                        p for p in self._workspace_root.iterdir()
                        if p.is_dir() and (p / ".git").exists()
                    ]
                    pruned = await self._workspace_mgr.prune_stale(repo_dirs)
                    if pruned:
                        logger.info("Cleaned up %d stale worktrees (background)", pruned)
                except Exception as e:
                    logger.warning("Worktree cleanup failed: %s", e)
            asyncio.create_task(_bg_cleanup(), name="pool-worktree-cleanup")

        warm_on_start = getattr(self._config, "pool_warm_on_start", True)
        logger.info(
            "Pool startup: warm_on_start=%s, kronos_url=%r, kronos_cmd=%r",
            warm_on_start, self._kronos_mcp_url, self._kronos_mcp_command,
        )

        # Auto-start a local Kronos SSE server if no external URL is configured.
        # This is the single biggest perf win: all slots share one warm Kronos
        # instead of each spawning its own stdio subprocess + engine init.
        if warm_on_start and not self._kronos_mcp_url and self._kronos_mcp_command:
            t0 = _time.time()
            await self._start_kronos_sse()
            if self._kronos_mcp_url:
                logger.info(
                    "Kronos SSE auto-started in %.1fs → %s",
                    _time.time() - t0, self._kronos_mcp_url,
                )
            else:
                logger.warning(
                    "Kronos SSE auto-start FAILED after %.1fs — falling back to stdio",
                    _time.time() - t0,
                )
        elif not self._kronos_mcp_command:
            logger.warning("No kronos_mcp_command configured — pool agents have no vault tools")

        # Run codebase refresh + Kronos health check concurrently (both independent)
        startup_tasks: list[asyncio.Task] = []

        if self._codebase_mgr:
            startup_tasks.append(asyncio.create_task(
                self._refresh_codebase(), name="pool-codebase-refresh",
            ))

        if warm_on_start and self._kronos_mcp_url:
            startup_tasks.append(asyncio.create_task(
                self._warm_kronos(), name="pool-kronos-warmup",
            ))

        if startup_tasks:
            t0 = _time.time()
            await asyncio.gather(*startup_tasks, return_exceptions=True)
            logger.info("Pool startup tasks complete in %.1fs", _time.time() - t0)

        # Create agent slots
        num_slots = getattr(self._config, "pool_num_slots", 2)
        max_turns = getattr(self._config, "pool_max_turns_per_job", 20)

        self._slots = [
            AgentSlot(
                slot_id=f"slot-{i}",
                kronos_mcp_command=self._kronos_mcp_command,
                kronos_mcp_env=self._kronos_mcp_env,
                kronos_mcp_url=self._kronos_mcp_url,
                max_turns=max_turns,
            )
            for i in range(num_slots)
        ]
        logger.info(
            "Created %d slots with kronos_mcp_url=%r",
            num_slots, self._kronos_mcp_url,
        )

        # Pre-warm ALL slot subprocesses in parallel so first job is instant
        if warm_on_start:
            t1 = _time.time()
            await asyncio.gather(*(slot.warm() for slot in self._slots))
            logger.info("Slot subprocesses warmed in %.1fs (%d slots)", _time.time() - t1, num_slots)

        self._running = True
        self._loop_task = asyncio.create_task(self._main_loop())
        kronos_mode = "SSE" if self._kronos_mcp_url else "stdio"
        logger.info(
            "ExecutionPool READY: %d slots, Kronos=%s, total startup=%.1fs",
            num_slots, kronos_mode, _time.time() - t_start,
        )

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
            timeout = getattr(self._config, "pool_job_timeout_secs", 900)
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

        # Shutdown persistent slot subprocesses
        for slot in self._slots:
            await slot.shutdown()

        # Stop managed Kronos SSE server
        await self._stop_kronos_sse()

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
    def workspace_manager(self) -> WorkspaceManager | None:
        """Access the workspace manager (None if not configured)."""
        return self._workspace_mgr

    @property
    def codebase(self) -> CodebaseManager | None:
        """Access the codebase manager (None if not configured)."""
        return self._codebase_mgr

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
            "resource_locks": self._resource_lock.status(),
        }

    # ── Managed Kronos SSE server ──────────────────────────────

    async def _start_kronos_sse(self) -> None:
        """Spawn a local Kronos SSE server and wait until it's healthy.

        This eliminates the biggest bottleneck: each slot's claude.exe would
        otherwise spawn its own Kronos MCP stdio subprocess, each loading
        engines (~30-60s). With SSE, engines load once and all slots share it.
        """
        import subprocess as _sp

        cmd = [self._kronos_mcp_command] + self._kronos_mcp_args + [
            "--sse", "--port", str(self._kronos_sse_port),
        ]
        env = {**os.environ, **self._kronos_mcp_env}

        logger.info("Starting Kronos SSE: %s", " ".join(cmd))
        self._kronos_process = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for health endpoint to come up (engines need time to warm)
        url = f"http://127.0.0.1:{self._kronos_sse_port}"
        health_url = f"{url}/health"
        max_wait = 120  # seconds — semantic model can take 60s+
        poll = 0.5
        waited = 0.0

        import httpx as _httpx

        while waited < max_wait:
            # Check if process died
            if self._kronos_process.returncode is not None:
                stderr = ""
                if self._kronos_process.stderr:
                    stderr = (await self._kronos_process.stderr.read()).decode(errors="replace")
                logger.error("Kronos SSE process died (rc=%s): %s", self._kronos_process.returncode, stderr[:500])
                self._kronos_process = None
                return

            try:
                async with _httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(health_url)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("engines_initialized"):
                            logger.info(
                                "Kronos SSE healthy at %s (engines warm)", url,
                            )
                            self._kronos_mcp_url = url
                            return
                        else:
                            logger.debug("Kronos SSE up but engines still loading...")
            except Exception:
                pass  # Server not ready yet

            await asyncio.sleep(poll)
            waited += poll

        logger.warning(
            "Kronos SSE did not become healthy within %ds — falling back to stdio", max_wait,
        )
        await self._stop_kronos_sse()

    async def _stop_kronos_sse(self) -> None:
        """Kill the managed Kronos SSE subprocess if it's running."""
        if self._kronos_process and self._kronos_process.returncode is None:
            logger.info("Stopping managed Kronos SSE server")
            self._kronos_process.terminate()
            try:
                await asyncio.wait_for(self._kronos_process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._kronos_process.kill()
                await self._kronos_process.wait()
            logger.info("Kronos SSE server stopped")
        self._kronos_process = None

    # ── Startup helpers ─────────────────────────────────────────

    async def _refresh_codebase(self) -> None:
        """Refresh codebase caches (non-blocking — failures logged, not fatal)."""
        try:
            await self._codebase_mgr.load_manifest()
            results = await self._codebase_mgr.refresh_all()
            refreshed = sum(1 for v in results.values() if v)
            logger.info("Codebase caches refreshed: %d/%d", refreshed, len(results))
        except Exception:
            logger.exception("Failed to refresh codebase caches — continuing without")

    # ── Kronos warm-up ─────────────────────────────────────────

    async def _warm_kronos(self) -> None:
        """Verify Kronos MCP is reachable before accepting jobs.

        SSE mode: HTTP GET /health on the persistent server.
        Stdio mode: just log that stdio will spawn on demand (no pre-check).
        """
        if self._kronos_mcp_url:
            import httpx as _httpx

            health_url = self._kronos_mcp_url.rstrip("/") + "/health"
            try:
                async with _httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(health_url)
                    resp.raise_for_status()
                    data = resp.json()
                    engines_ready = data.get("engines_initialized", False)
                    if engines_ready:
                        logger.info("Kronos SSE server healthy (engines initialized)")
                    else:
                        logger.warning(
                            "Kronos SSE server reachable but engines not yet initialized "
                            "— first job may be slow"
                        )
            except Exception as e:
                logger.warning(
                    "Kronos SSE server unreachable at %s: %s — "
                    "falling back to stdio for this session",
                    health_url, e,
                )
                self._kronos_mcp_url = ""  # Fall back to stdio
        elif self._kronos_mcp_command:
            logger.info("Kronos configured for stdio — MCP will spawn per-job")
        else:
            logger.info("No Kronos MCP configured — pool agents run without vault tools")

    # ── Agent streaming callback ────────────────────────────────

    async def _on_agent_message(self, job_id: str, msg: dict) -> None:
        """Emit streaming events for each SDK message as it arrives."""
        role = msg.get("role")
        if role == "assistant":
            for block in msg.get("content", []):
                block_type = block.get("type")
                if block_type in ("text", "tool_use"):
                    # Exclude "type" from block data — it would clobber PoolEvent.to_dict()'s keys
                    block_data = {k: v for k, v in block.items() if k != "type"}
                    block_data["block_type"] = block_type
                    await self.events.emit(PoolEvent(
                        type=PoolEventType.AGENT_OUTPUT,
                        job_id=job_id,
                        data=block_data,
                    ))
        elif role == "result":
            # Tool results — forward as tool_result event
            result_data = {k: v for k, v in msg.items() if k != "type"}
            result_data["block_type"] = "result"
            await self.events.emit(PoolEvent(
                type=PoolEventType.AGENT_TOOL_RESULT,
                job_id=job_id,
                data=result_data,
            ))

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

        # Find idle slots (check both busy flag AND active task tracker)
        idle_slots = [
            s for s in self._slots
            if not s.busy and s.slot_id not in self._tasks
        ]
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

        # Create workspace for code jobs with a target repo (git worktree isolation)
        workspace_id: str | None = None
        if (
            job.job_type == JobType.CODE
            and self._workspace_mgr
            and self._workspace_root
            and job.target_repo
        ):
            repo_path = self._workspace_root / job.target_repo
            try:
                ws = await self._workspace_mgr.create(job.id, repo_path)
                workspace_id = ws.id
                self._job_workspace_map[job.id] = ws.id
                await self._queue.update_status(
                    job.id, JobStatus.RUNNING, workspace_id=ws.id,
                )
                slot.cwd = str(ws.worktree_path)
                # Give CODE agents read access to full workspace alongside their worktree
                if self._workspace_root:
                    slot.add_dirs = [str(self._workspace_root)]
                logger.info("Job %s worktree in %s: %s", job.id, job.target_repo, ws.id)
            except Exception as e:
                logger.warning("Failed to create workspace for %s in %s: %s", job.id, job.target_repo, e)

        # Fallback: no target_repo or non-CODE → workspace root as cwd
        if not slot.cwd and self._workspace_root:
            slot.cwd = str(self._workspace_root)

        try:
            result = await asyncio.wait_for(
                slot.execute(job, on_message=self._on_agent_message),
                timeout=getattr(self._config, "pool_job_timeout_secs", 900),
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
            slot.add_dirs = []  # Reset additional directories

        # Handle result
        if result.success:
            # Gather workspace diff if available
            diff_stat: str | None = None
            changed_files: list[str] | None = None
            if workspace_id and self._workspace_mgr:
                diff_stat = await self._workspace_mgr.get_branch_diff(workspace_id)
                changed_files = await self._workspace_mgr.list_changed_files(workspace_id)
                # Mark workspace as pending review (keep for PR/merge)
                ws = self._workspace_mgr.get(workspace_id)
                if ws:
                    ws.status = "pending_review"

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
                    "diff_stat": diff_stat,
                    "changed_files": changed_files,
                    "workspace_id": workspace_id,
                },
            ))
            logger.info("Job %s complete", job.id)

            # Emit review event for workspace jobs
            if workspace_id and self._workspace_mgr:
                await self.events.emit(PoolEvent(
                    type=PoolEventType.JOB_REVIEW,
                    job_id=job.id,
                    data={
                        "workspace_id": workspace_id,
                        "diff_stat": diff_stat,
                        "changed_files": changed_files,
                    },
                ))
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
