"""InChatExecutionPool — ephemeral 1-slot execution within a conversation.

Runs a job inline with the current chat session. No SQLite persistence —
results are ephemeral. Jobs can be promoted to the background pool.

Usage::

    inchat = InChatExecutionPool(config)
    result = await inchat.run(job, on_event=my_callback)
    # Or cancel mid-run:
    inchat.cancel()
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional

from core.pool.events import PoolEvent, PoolEventBus, PoolEventType
from core.pool.models import Job, JobResult, JobStatus
from core.pool.slot import AgentSlot

logger = logging.getLogger(__name__)

# Callback type for progress events
OnEvent = Callable[[PoolEvent], Coroutine[Any, Any, None]]


class InChatExecutionPool:
    """1-slot ephemeral execution for in-chat jobs.

    No queue, no persistence. Runs one job at a time synchronously
    (from the caller's perspective — internally uses async slot).
    """

    def __init__(
        self,
        kronos_mcp_command: str = "",
        kronos_mcp_env: dict[str, str] | None = None,
        max_turns: int = 15,
    ) -> None:
        self._kronos_mcp_command = kronos_mcp_command
        self._kronos_mcp_env = kronos_mcp_env or {}
        self._max_turns = max_turns
        self._current_job: Job | None = None
        self._current_task: asyncio.Task | None = None
        self._cancelled = False
        self.events = PoolEventBus()

    @property
    def current_job(self) -> Job | None:
        """The currently running job, or None."""
        return self._current_job

    @property
    def busy(self) -> bool:
        return self._current_job is not None

    async def run(
        self,
        job: Job,
        on_event: OnEvent | None = None,
    ) -> JobResult:
        """Execute a job inline. Blocks until complete.

        Args:
            job: The job to execute.
            on_event: Optional callback for progress events.

        Returns:
            JobResult with outcome.
        """
        if self._current_job is not None:
            return JobResult(
                job_id=job.id,
                success=False,
                error="In-chat pool is already busy",
            )

        self._current_job = job
        self._cancelled = False

        # Subscribe the callback if provided
        if on_event:
            self.events.subscribe(on_event)

        # Emit start event
        await self.events.emit(PoolEvent(
            type=PoolEventType.JOB_STARTED,
            job_id=job.id,
            data={"slot_id": "inchat"},
        ))

        try:
            slot = AgentSlot(
                slot_id="inchat-0",
                kronos_mcp_command=self._kronos_mcp_command,
                kronos_mcp_env=self._kronos_mcp_env,
                max_turns=self._max_turns,
            )

            self._current_task = asyncio.current_task()
            result = await slot.execute(job)

            # Emit completion event
            if result.success:
                await self.events.emit(PoolEvent(
                    type=PoolEventType.JOB_COMPLETE,
                    job_id=job.id,
                    data={
                        "result_preview": (result.result or "")[:200],
                        "cost_usd": result.cost_usd,
                        "num_turns": result.num_turns,
                    },
                ))
            else:
                await self.events.emit(PoolEvent(
                    type=PoolEventType.JOB_FAILED,
                    job_id=job.id,
                    data={"error": result.error},
                ))

            return result

        except asyncio.CancelledError:
            await self.events.emit(PoolEvent(
                type=PoolEventType.JOB_CANCELLED,
                job_id=job.id,
            ))
            return JobResult(
                job_id=job.id,
                success=False,
                error="Job cancelled",
            )
        except Exception as e:
            await self.events.emit(PoolEvent(
                type=PoolEventType.JOB_FAILED,
                job_id=job.id,
                data={"error": str(e)},
            ))
            return JobResult(
                job_id=job.id,
                success=False,
                error=str(e),
            )
        finally:
            self._current_job = None
            self._current_task = None
            if on_event:
                self.events.unsubscribe(on_event)

    def cancel(self) -> bool:
        """Cancel the currently running job.

        Returns True if a job was running and cancel was requested.
        """
        if self._current_task and not self._current_task.done():
            self._cancelled = True
            self._current_task.cancel()
            return True
        return False
