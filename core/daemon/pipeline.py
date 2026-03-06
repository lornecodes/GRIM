"""SQLite-backed pipeline store for tracking story lifecycle through the daemon."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from core.daemon.models import (
    InvalidTransition,
    PipelineItem,
    PipelineStatus,
    PRIORITY_ORDER,
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
)

logger = logging.getLogger(__name__)

# ── SQL ───────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pipeline (
    id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'backlog',
    priority INTEGER NOT NULL DEFAULT 2,
    assignee TEXT NOT NULL DEFAULT '',
    job_id TEXT,
    workspace_id TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    daemon_retries INTEGER NOT NULL DEFAULT 0,
    pr_number INTEGER,
    pr_url TEXT,
    pr_comment_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_pipeline_status_priority
ON pipeline (status, priority, created_at)
"""

_CREATE_STORY_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_pipeline_story
ON pipeline (story_id)
"""


class PipelineStore:
    """Persistent pipeline state backed by SQLite.

    Tracks stories through BACKLOG → READY → DISPATCHED → REVIEW → MERGED.
    Enforces valid state transitions via guards in advance().
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        """Create tables and indexes. Call once at boot."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute(_CREATE_TABLE)
            await db.execute(_CREATE_INDEX)
            await db.execute(_CREATE_STORY_INDEX)
            # Phase 4 migration: add PR columns to existing DBs
            for col, defn in [
                ("pr_number", "INTEGER"),
                ("pr_url", "TEXT"),
                ("pr_comment_count", "INTEGER NOT NULL DEFAULT 0"),
            ]:
                try:
                    await db.execute(f"ALTER TABLE pipeline ADD COLUMN {col} {defn}")
                except Exception:
                    pass  # Column already exists
            await db.commit()
        logger.info("PipelineStore initialized: %s", self._db_path)

    async def add(
        self,
        story_id: str,
        project_id: str,
        priority: int = 2,
        assignee: str = "",
    ) -> PipelineItem:
        """Add a new story to the pipeline as BACKLOG. Returns the created item."""
        item = PipelineItem(
            story_id=story_id,
            project_id=project_id,
            priority=priority,
            assignee=assignee,
        )
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """INSERT INTO pipeline
                   (id, story_id, project_id, status, priority, assignee,
                    job_id, workspace_id, error, attempts, daemon_retries,
                    pr_number, pr_url, pr_comment_count,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.id,
                    item.story_id,
                    item.project_id,
                    item.status.value,
                    item.priority,
                    item.assignee,
                    item.job_id,
                    item.workspace_id,
                    item.error,
                    item.attempts,
                    item.daemon_retries,
                    item.pr_number,
                    item.pr_url,
                    item.pr_comment_count,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                ),
            )
            await db.commit()
        logger.info("Pipeline item added: %s → %s (project=%s)", item.id, story_id, project_id)
        return item

    async def advance(
        self,
        item_id: str,
        new_status: PipelineStatus,
        **fields: Any,
    ) -> PipelineItem:
        """Transition an item to a new status with guard checks.

        Raises InvalidTransition if the transition is not allowed.
        Raises ValueError if item_id not found.

        Supported fields: job_id, workspace_id, error, attempts.
        """
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pipeline WHERE id = ?", (item_id,)) as cursor:
                row = await cursor.fetchone()

            if row is None:
                raise ValueError(f"Pipeline item not found: {item_id}")

            current_status = PipelineStatus(row["status"])

            # Guard: check valid transition
            allowed = VALID_TRANSITIONS.get(current_status, frozenset())
            if new_status not in allowed:
                raise InvalidTransition(current_status, new_status)

            # Build update
            now = datetime.now(timezone.utc).isoformat()
            sets = ["status = ?", "updated_at = ?"]
            params: list[Any] = [new_status.value, now]

            _ALLOWED_FIELDS = {
                "job_id", "workspace_id", "error",
                "attempts", "daemon_retries",
                "pr_number", "pr_url", "pr_comment_count",
            }
            for key, value in fields.items():
                if key in _ALLOWED_FIELDS:
                    sets.append(f"{key} = ?")
                    params.append(value)

            params.append(item_id)
            sql = f"UPDATE pipeline SET {', '.join(sets)} WHERE id = ?"
            await db.execute(sql, params)
            await db.commit()

        logger.info("Pipeline %s: %s → %s", item_id, current_status.value, new_status.value)
        item = await self.get(item_id)
        assert item is not None
        return item

    async def next_ready(self) -> PipelineItem | None:
        """Pull the highest-priority READY item. Does NOT advance status."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM pipeline WHERE status = 'ready' ORDER BY priority ASC, created_at ASC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None
        return _row_to_item(row)

    async def get(self, item_id: str) -> PipelineItem | None:
        """Retrieve a pipeline item by ID."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pipeline WHERE id = ?", (item_id,)) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None
        return _row_to_item(row)

    async def get_by_story(self, story_id: str) -> PipelineItem | None:
        """Retrieve a pipeline item by story ID."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pipeline WHERE story_id = ?", (story_id,)) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None
        return _row_to_item(row)

    async def get_by_job(self, job_id: str) -> PipelineItem | None:
        """Retrieve a pipeline item by its pool job ID."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pipeline WHERE job_id = ?", (job_id,)) as cursor:
                row = await cursor.fetchone()

        if row is None:
            return None
        return _row_to_item(row)

    async def list_items(
        self,
        status_filter: PipelineStatus | None = None,
        project_filter: str | None = None,
        limit: int = 100,
    ) -> list[PipelineItem]:
        """List pipeline items with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []

        if status_filter:
            conditions.append("status = ?")
            params.append(status_filter.value)
        if project_filter:
            conditions.append("project_id = ?")
            params.append(project_filter)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM pipeline{where} ORDER BY priority ASC, created_at ASC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()

        return [_row_to_item(r) for r in rows]

    async def prune_merged(self, days: int = 7) -> int:
        """Delete MERGED items older than `days`. Returns count deleted."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                "DELETE FROM pipeline WHERE status = 'merged' AND updated_at < ?",
                (cutoff,),
            )
            count = cursor.rowcount
            await db.commit()

        if count > 0:
            logger.info("Pruned %d merged pipeline items (older than %d days)", count, days)
        return count

    async def update_fields(self, item_id: str, **fields: Any) -> PipelineItem:
        """Update fields on an item WITHOUT changing status.

        Raises ValueError if item_id not found.
        Supported fields: job_id, workspace_id, error, attempts, daemon_retries,
                          pr_number, pr_url, pr_comment_count.
        """
        _ALLOWED = {
            "job_id", "workspace_id", "error",
            "attempts", "daemon_retries",
            "pr_number", "pr_url", "pr_comment_count",
        }
        now = datetime.now(timezone.utc).isoformat()
        sets = ["updated_at = ?"]
        params: list[Any] = [now]

        for key, value in fields.items():
            if key in _ALLOWED:
                sets.append(f"{key} = ?")
                params.append(value)

        if len(sets) == 1:
            # Only updated_at — nothing to change
            item = await self.get(item_id)
            if item is None:
                raise ValueError(f"Pipeline item not found: {item_id}")
            return item

        params.append(item_id)
        sql = f"UPDATE pipeline SET {', '.join(sets)} WHERE id = ?"

        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(sql, params)
            if cursor.rowcount == 0:
                raise ValueError(f"Pipeline item not found: {item_id}")
            await db.commit()

        item = await self.get(item_id)
        assert item is not None
        return item

    async def remove(self, item_id: str) -> bool:
        """Remove a pipeline item entirely. Returns True if deleted."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute("DELETE FROM pipeline WHERE id = ?", (item_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def count_by_status(self) -> dict[str, int]:
        """Return count of items grouped by status."""
        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT status, COUNT(*) as cnt FROM pipeline GROUP BY status"
            ) as cursor:
                rows = await cursor.fetchall()

        return {row["status"]: row["cnt"] for row in rows}


# ── Row → PipelineItem conversion ────────────────────────────────


def _row_to_item(row: aiosqlite.Row) -> PipelineItem:
    """Convert a SQLite row to a PipelineItem model."""
    return PipelineItem(
        id=row["id"],
        story_id=row["story_id"],
        project_id=row["project_id"],
        status=row["status"],
        priority=row["priority"],
        assignee=row["assignee"],
        job_id=row["job_id"],
        workspace_id=row["workspace_id"],
        error=row["error"],
        attempts=row["attempts"],
        daemon_retries=row["daemon_retries"],
        pr_number=row["pr_number"],
        pr_url=row["pr_url"],
        pr_comment_count=row["pr_comment_count"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
