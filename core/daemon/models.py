"""Pipeline models for the management daemon."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PipelineStatus(str, Enum):
    """Pipeline item lifecycle status."""

    BACKLOG = "backlog"          # story found in vault, not yet selected
    READY = "ready"              # eligible for dispatch
    DISPATCHED = "dispatched"    # job submitted to pool, running
    REVIEW = "review"            # job complete, workspace has changes
    MERGED = "merged"            # workspace merged, story resolved
    FAILED = "failed"            # job failed after retries
    BLOCKED = "blocked"          # job needs clarification


# Terminal statuses — no further transitions allowed
TERMINAL_STATUSES = frozenset({
    PipelineStatus.MERGED,
    PipelineStatus.FAILED,
})

# Valid state transitions
VALID_TRANSITIONS: dict[PipelineStatus, frozenset[PipelineStatus]] = {
    PipelineStatus.BACKLOG: frozenset({PipelineStatus.READY}),
    PipelineStatus.READY: frozenset({PipelineStatus.DISPATCHED}),
    PipelineStatus.DISPATCHED: frozenset({
        PipelineStatus.REVIEW,
        PipelineStatus.FAILED,
        PipelineStatus.BLOCKED,
    }),
    PipelineStatus.REVIEW: frozenset({PipelineStatus.MERGED, PipelineStatus.FAILED}),
    PipelineStatus.BLOCKED: frozenset({
        PipelineStatus.READY,       # clarification provided → re-queue
        PipelineStatus.FAILED,      # give up
    }),
    PipelineStatus.MERGED: frozenset(),
    PipelineStatus.FAILED: frozenset({PipelineStatus.READY}),  # retry
}


# Priority ordering — lower = higher priority (matches pool convention)
PRIORITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def _make_pipeline_id() -> str:
    return f"pipeline-{uuid.uuid4().hex[:8]}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PipelineItem(BaseModel):
    """A story tracked through the management pipeline."""

    id: str = Field(default_factory=_make_pipeline_id)
    story_id: str                               # e.g. story-mewtwo-009
    project_id: str                             # e.g. proj-mewtwo
    status: PipelineStatus = PipelineStatus.BACKLOG

    # Pool integration
    job_id: Optional[str] = None                # pool job ID when dispatched
    workspace_id: Optional[str] = None          # git worktree ID

    # Scheduling
    priority: int = 2                           # 0=critical..3=low
    assignee: str = ""                          # code/research/audit/plan

    # Timestamps
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    # Outcome
    error: Optional[str] = None
    attempts: int = 0


class InvalidTransition(Exception):
    """Raised when a pipeline state transition is not allowed."""

    def __init__(self, current: PipelineStatus, target: PipelineStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition: {current.value} → {target.value}")
