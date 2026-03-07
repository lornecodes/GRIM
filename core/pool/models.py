"""Job model and related types for the execution pool."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobType(str, Enum):
    """Type of work the agent should perform."""

    CODE = "code"
    RESEARCH = "research"
    AUDIT = "audit"
    PLAN = "plan"
    INDEX = "index"


class JobStatus(str, Enum):
    """Job lifecycle status."""

    QUEUED = "queued"
    ASSIGNED = "assigned"
    RUNNING = "running"
    BLOCKED = "blocked"          # awaiting clarification
    REVIEW = "review"            # sent to audit
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Terminal statuses — no further transitions allowed
TERMINAL_STATUSES = frozenset({
    JobStatus.COMPLETE,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
})


class JobPriority(str, Enum):
    """Job priority level."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    BACKGROUND = "background"


# Integer ordering for SQLite — lower = higher priority
PRIORITY_ORDER: dict[JobPriority, int] = {
    JobPriority.CRITICAL: 0,
    JobPriority.HIGH: 1,
    JobPriority.NORMAL: 2,
    JobPriority.LOW: 3,
    JobPriority.BACKGROUND: 4,
}


def _make_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:8]}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    """A unit of work for the execution pool."""

    id: str = Field(default_factory=_make_job_id)
    job_type: JobType
    status: JobStatus = JobStatus.QUEUED
    priority: JobPriority = JobPriority.NORMAL

    # Task definition
    instructions: str
    plan: Optional[str] = None

    # Context binding
    workspace_id: Optional[str] = None
    target_repo: Optional[str] = None  # e.g. "GRIM", "dawn-field-theory"
    kronos_domains: list[str] = Field(default_factory=list)
    kronos_fdo_ids: list[str] = Field(default_factory=list)

    # Timing
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    # Execution state
    assigned_slot: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 2

    # Clarification flow
    clarification_question: Optional[str] = None
    clarification_answer: Optional[str] = None

    # Output
    result: Optional[str] = None
    error: Optional[str] = None
    transcript: list[dict] = Field(default_factory=list)


@dataclass
class JobResult:
    """Result returned by an AgentSlot after executing a job."""

    job_id: str
    success: bool
    result: Optional[str] = None
    error: Optional[str] = None
    transcript: list[dict] = field(default_factory=list)
    cost_usd: Optional[float] = None
    num_turns: Optional[int] = None


class ClarificationNeeded(Exception):
    """Raised by an agent when it needs user input to proceed."""

    def __init__(self, question: str) -> None:
        self.question = question
        super().__init__(question)
