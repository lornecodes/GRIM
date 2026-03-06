"""GRIM Execution Pool — Project Charizard Phase 1.

Async job execution via Claude Agent SDK. Jobs are queued in SQLite,
dispatched to AgentSlots, and executed with configurable tool permissions.
"""
from __future__ import annotations

from core.pool.audit import AuditResult, ToolVerdict, can_use_tool, is_safe_bash
from core.pool.events import PoolEvent, PoolEventBus, PoolEventType
from core.pool.models import Job, JobResult, JobStatus, JobType, JobPriority
from core.pool.pool import ExecutionPool
from core.pool.queue import JobQueue
from core.pool.slot import AgentSlot
from core.pool.workspace import Workspace, WorkspaceManager
from core.pool.codebase import CodebaseManager, RepoInfo
from core.pool.inchat import InChatExecutionPool
from core.pool.locks import ResourceLock, ResourceScope

__all__ = [
    "ExecutionPool",
    "InChatExecutionPool",
    "ResourceLock",
    "ResourceScope",
    "JobQueue",
    "AgentSlot",
    "Job",
    "JobResult",
    "JobStatus",
    "JobType",
    "JobPriority",
    "PoolEvent",
    "PoolEventBus",
    "PoolEventType",
    "Workspace",
    "WorkspaceManager",
    "CodebaseManager",
    "RepoInfo",
]
