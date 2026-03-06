"""Pool event bus — push notifications for job lifecycle and streaming events.

Allows interested parties (WebSocket connections, Discord bot, etc.) to
subscribe to pool events and receive real-time updates.

Lifecycle events:
  - job_submitted: New job entered the queue
  - job_started: Job dispatched to a slot
  - job_complete: Job finished successfully
  - job_failed: Job failed (after retries exhausted)
  - job_blocked: Job needs clarification
  - job_cancelled: Job was cancelled

Streaming events (emitted per-message during agent execution):
  - agent_output: Text chunk or tool_use from agent (scoped to job_id)
  - agent_tool_result: Tool result returned to agent (scoped to job_id)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class PoolEventType(str, Enum):
    """Types of pool lifecycle events."""

    JOB_SUBMITTED = "job_submitted"
    JOB_STARTED = "job_started"
    JOB_COMPLETE = "job_complete"
    JOB_FAILED = "job_failed"
    JOB_BLOCKED = "job_blocked"
    JOB_CANCELLED = "job_cancelled"
    JOB_REVIEW = "job_review"

    # Streaming events — emitted per-message during agent execution
    AGENT_OUTPUT = "agent_output"
    AGENT_TOOL_RESULT = "agent_tool_result"


@dataclass
class PoolEvent:
    """A pool lifecycle event."""

    type: PoolEventType
    job_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.type.value,
            "job_id": self.job_id,
            "timestamp": self.timestamp.isoformat(),
            **self.data,
        }


# Subscriber callback type: async function(event) → None
Subscriber = Callable[[PoolEvent], Coroutine[Any, Any, None]]


class PoolEventBus:
    """Simple async pub/sub for pool events.

    Usage::

        bus = PoolEventBus()
        bus.subscribe(my_handler)
        await bus.emit(PoolEvent(type=PoolEventType.JOB_COMPLETE, job_id="j1"))
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, callback: Subscriber) -> None:
        """Register a subscriber for all pool events."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        """Remove a subscriber."""
        self._subscribers = [s for s in self._subscribers if s is not callback]

    async def emit(self, event: PoolEvent) -> None:
        """Emit an event to all subscribers.

        Subscriber errors are logged but don't prevent other subscribers
        from receiving the event.
        """
        for sub in self._subscribers:
            try:
                await sub(event)
            except Exception:
                logger.exception("Pool event subscriber error for %s", event.type.value)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Event classification helpers
STREAMING_EVENT_TYPES = frozenset({
    PoolEventType.AGENT_OUTPUT,
    PoolEventType.AGENT_TOOL_RESULT,
})


def is_streaming_event(event: PoolEvent) -> bool:
    """Return True if the event is a high-frequency streaming event."""
    return event.type in STREAMING_EVENT_TYPES
