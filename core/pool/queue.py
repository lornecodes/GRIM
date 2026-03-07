"""SQLite-backed job queue with priority ordering and workspace-aware scheduling."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from core.pool.models import (
    Job,
    JobPriority,
    JobStatus,
    JobType,
    PRIORITY_ORDER,
    TERMINAL_STATUSES,
)

logger = logging.getLogger(__name__)

# ── SQL ───────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 2,
    workspace_id TEXT,
    target_repo TEXT,
    instructions TEXT NOT NULL,
    plan TEXT,
    kronos_domains TEXT,
    kronos_fdo_ids TEXT,
    assigned_slot TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 2,
    clarification_question TEXT,
    clarification_answer TEXT,
    result TEXT,
    error TEXT,
    transcript TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_jobs_status_priority
ON jobs (status, priority, created_at)
"""


class JobQueue:
    """Persistent job queue backed by SQLite.

    Thread-safe via aiosqlite (runs SQLite in a thread pool).
    WAL mode for concurrent reads. Atomic next() for slot dispatch.

    Uses a persistent connection to avoid per-query open/close overhead.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _conn(self) -> aiosqlite.Connection:
        """Return the persistent connection, reconnecting if needed."""
        if self._db is None:
            self._db = await aiosqlite.connect(str(self._db_path))
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA foreign_keys=ON")
            await self._db.execute("PRAGMA synchronous=NORMAL")
            await self._db.execute("PRAGMA cache_size=-8000")  # 8MB cache
            await self._db.execute("PRAGMA busy_timeout=5000")
        return self._db

    async def initialize(self) -> None:
        """Create tables and indexes. Call once at boot."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        db = await self._conn()
        await db.execute(_CREATE_TABLE)
        await db.execute(_CREATE_INDEX)
        # Migration: add target_repo column if missing (existing DBs)
        try:
            await db.execute("ALTER TABLE jobs ADD COLUMN target_repo TEXT")
        except Exception:
            pass  # Column already exists
        await db.commit()
        # Warm up — force SQLite to read the index into cache
        await db.execute("SELECT COUNT(*) FROM jobs")
        logger.info("JobQueue initialized: %s", self._db_path)

    async def close(self) -> None:
        """Close the persistent connection. Call on shutdown."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def submit(self, job: Job) -> str:
        """Insert a new job into the queue. Returns job_id."""
        db = await self._conn()
        await db.execute(
            """INSERT INTO jobs
               (id, job_type, status, priority, workspace_id, target_repo,
                instructions, plan, kronos_domains, kronos_fdo_ids,
                assigned_slot, retry_count, max_retries,
                clarification_question, clarification_answer,
                result, error, transcript, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.id,
                job.job_type.value,
                job.status.value,
                PRIORITY_ORDER[job.priority],
                job.workspace_id,
                job.target_repo,
                job.instructions,
                job.plan,
                json.dumps(job.kronos_domains),
                json.dumps(job.kronos_fdo_ids),
                job.assigned_slot,
                job.retry_count,
                job.max_retries,
                job.clarification_question,
                job.clarification_answer,
                job.result,
                job.error,
                json.dumps(job.transcript),
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
            ),
        )
        await db.commit()
        logger.info("Job submitted: %s (%s, %s)", job.id, job.job_type.value, job.priority.value)
        return job.id

    async def next(self, busy_workspaces: set[str] | None = None) -> Job | None:
        """Pull the next eligible job from the queue.

        Atomically selects the highest-priority QUEUED job and marks it ASSIGNED.
        Skips jobs whose workspace_id is in busy_workspaces (sequential mode).

        Returns None if no eligible jobs.
        """
        db = await self._conn()

        # Build WHERE clause
        where = "status = 'queued'"
        params: list[Any] = []
        if busy_workspaces:
            placeholders = ",".join("?" for _ in busy_workspaces)
            where += f" AND (workspace_id IS NULL OR workspace_id NOT IN ({placeholders}))"
            params.extend(busy_workspaces)

        # Atomic select + update in one transaction
        async with db.execute(
            f"SELECT id FROM jobs WHERE {where} ORDER BY priority ASC, created_at ASC LIMIT 1",
            params,
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        job_id = row["id"]
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE jobs SET status = 'assigned', updated_at = ? WHERE id = ? AND status = 'queued'",
            (now, job_id),
        )
        await db.commit()

        return await self.get(job_id)

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        **fields: Any,
    ) -> None:
        """Update job status and any additional fields."""
        now = datetime.now(timezone.utc).isoformat()
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status.value, now]

        # Map field names to column updates
        for key, value in fields.items():
            if key in ("result", "error", "assigned_slot", "workspace_id",
                       "clarification_question", "clarification_answer"):
                sets.append(f"{key} = ?")
                params.append(value)
            elif key == "retry_count":
                sets.append("retry_count = ?")
                params.append(value)
            elif key == "transcript":
                sets.append("transcript = ?")
                params.append(json.dumps(value) if isinstance(value, list) else value)

        params.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?"

        db = await self._conn()
        await db.execute(sql, params)
        await db.commit()

    async def cancel(self, job_id: str) -> bool:
        """Cancel a QUEUED or BLOCKED job. Returns False if not cancellable."""
        db = await self._conn()
        async with db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return False

        current = JobStatus(row["status"])
        if current not in (JobStatus.QUEUED, JobStatus.BLOCKED):
            return False

        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE jobs SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, job_id),
        )
        await db.commit()
        logger.info("Job cancelled: %s", job_id)
        return True

    async def get(self, job_id: str) -> Job | None:
        """Retrieve a job by ID."""
        db = await self._conn()
        async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None
        return _row_to_job(row)

    async def list_jobs(
        self,
        status_filter: JobStatus | None = None,
        type_filter: JobType | None = None,
        limit: int = 50,
    ) -> list[Job]:
        """List jobs, optionally filtered by status and/or job type."""
        conditions: list[str] = []
        params: list[Any] = []

        if status_filter:
            conditions.append("status = ?")
            params.append(status_filter.value)
        if type_filter:
            conditions.append("job_type = ?")
            params.append(type_filter.value)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        order = " ORDER BY priority ASC, created_at DESC" if status_filter else " ORDER BY created_at DESC"
        sql = f"SELECT * FROM jobs{where}{order} LIMIT ?"
        params.append(limit)

        db = await self._conn()
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()

        return [_row_to_job(r) for r in rows]

    async def request_clarification(self, job_id: str, question: str) -> None:
        """Block a running job and store the clarification question."""
        await self.update_status(
            job_id,
            JobStatus.BLOCKED,
            clarification_question=question,
        )
        logger.info("Job blocked for clarification: %s", job_id)

    async def provide_clarification(self, job_id: str, answer: str) -> None:
        """Store the answer and re-queue a blocked job."""
        await self.update_status(
            job_id,
            JobStatus.QUEUED,
            clarification_answer=answer,
        )
        logger.info("Clarification provided, job re-queued: %s", job_id)

    async def recover_orphans(self) -> int:
        """Mark abandoned 'running' jobs as failed on startup.

        When the container restarts, any job still in 'running' status was
        abandoned mid-execution. Mark them as failed with a clear error
        message so the daemon can detect and re-dispatch them.
        """
        db = await self._conn()
        cursor = await db.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE status = ?",
            ("failed", "Orphaned by process restart — will be re-dispatched",
             datetime.now(timezone.utc).isoformat(), "running"),
        )
        count = cursor.rowcount
        await db.commit()

        if count > 0:
            logger.warning("Recovered %d orphaned running jobs", count)
        return count

    async def prune_completed(self, days: int = 30) -> int:
        """Delete terminal jobs (complete/failed/cancelled) older than `days`.

        Returns count deleted.
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        terminal = tuple(s.value for s in TERMINAL_STATUSES)
        placeholders = ",".join("?" for _ in terminal)

        db = await self._conn()
        cursor = await db.execute(
            f"DELETE FROM jobs WHERE status IN ({placeholders}) AND updated_at < ?",
            (*terminal, cutoff),
        )
        count = cursor.rowcount
        await db.commit()

        if count > 0:
            logger.info("Pruned %d completed jobs (older than %d days)", count, days)
        return count


# ── Row → Job conversion ─────────────────────────────────────────

# Reverse map: integer → JobPriority
_INT_TO_PRIORITY = {v: k for k, v in PRIORITY_ORDER.items()}


def _row_to_job(row: aiosqlite.Row) -> Job:
    """Convert a SQLite row to a Job model."""
    return Job(
        id=row["id"],
        job_type=row["job_type"],
        status=row["status"],
        priority=_INT_TO_PRIORITY.get(row["priority"], JobPriority.NORMAL),
        workspace_id=row["workspace_id"],
        target_repo=row["target_repo"] if "target_repo" in row.keys() else None,
        instructions=row["instructions"],
        plan=row["plan"],
        kronos_domains=json.loads(row["kronos_domains"] or "[]"),
        kronos_fdo_ids=json.loads(row["kronos_fdo_ids"] or "[]"),
        assigned_slot=row["assigned_slot"],
        retry_count=row["retry_count"],
        max_retries=row["max_retries"],
        clarification_question=row["clarification_question"],
        clarification_answer=row["clarification_answer"],
        result=row["result"],
        error=row["error"],
        transcript=json.loads(row["transcript"] or "[]"),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
