"""Token usage tracker — async SQLite persistence for LLM call metrics.

Stores per-request token usage from Claude API responses. Provides
query methods for dashboards and mission control.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    caller_id       TEXT NOT NULL DEFAULT 'unknown',
    model           TEXT,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_read      INTEGER NOT NULL DEFAULT 0,
    cache_create    INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER GENERATED ALWAYS AS (input_tokens + output_tokens) STORED
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_usage_ts ON token_usage(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_usage_caller ON token_usage(caller_id);",
]

_INSERT = """
INSERT INTO token_usage (timestamp, caller_id, model, input_tokens, output_tokens, cache_read, cache_create)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


class TokenTracker:
    """Async SQLite-backed token usage tracker."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Open connection and create schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA)
        for idx in _INDEXES:
            await self._conn.execute(idx)
        await self._conn.commit()
        logger.info("Token tracker initialized: %s", self.db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def record(
        self,
        caller_id: str,
        model: str | None,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_create: int = 0,
    ) -> None:
        """Record a single API call's token usage. Fire-and-forget safe."""
        if not self._conn:
            return
        try:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            await self._conn.execute(
                _INSERT,
                (now, caller_id, model, input_tokens, output_tokens, cache_read, cache_create),
            )
            await self._conn.commit()
        except Exception:
            logger.warning("Failed to record token usage", exc_info=True)

    async def summary(self, days: int = 30) -> dict:
        """Aggregate totals by caller and model for the given period."""
        if not self._conn:
            return {"error": "not initialized"}
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

        # Overall totals
        async with self._conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
            "COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_create),0), COUNT(*) "
            "FROM token_usage WHERE timestamp >= ?",
            (cutoff,),
        ) as cur:
            row = await cur.fetchone()
        totals = {
            "input_tokens": row[0],
            "output_tokens": row[1],
            "cache_read_tokens": row[2],
            "cache_create_tokens": row[3],
            "total_tokens": row[0] + row[1],
            "calls": row[4],
        }

        # By caller
        by_caller = {}
        async with self._conn.execute(
            "SELECT caller_id, SUM(input_tokens), SUM(output_tokens), COUNT(*) "
            "FROM token_usage WHERE timestamp >= ? GROUP BY caller_id",
            (cutoff,),
        ) as cur:
            async for r in cur:
                by_caller[r[0]] = {"input_tokens": r[1], "output_tokens": r[2], "calls": r[3]}

        # By model
        by_model = {}
        async with self._conn.execute(
            "SELECT model, SUM(input_tokens), SUM(output_tokens), COUNT(*) "
            "FROM token_usage WHERE timestamp >= ? GROUP BY model",
            (cutoff,),
        ) as cur:
            async for r in cur:
                by_model[r[0] or "unknown"] = {"input_tokens": r[1], "output_tokens": r[2], "calls": r[3]}

        return {
            "period_days": days,
            "totals": totals,
            "by_caller": by_caller,
            "by_model": by_model,
        }

    async def by_day(self, days: int = 30, caller_id: str | None = None) -> list[dict]:
        """Daily aggregates for charting."""
        if not self._conn:
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

        query = (
            "SELECT date(timestamp) as day, SUM(input_tokens), SUM(output_tokens), COUNT(*) "
            "FROM token_usage WHERE timestamp >= ?"
        )
        params: list = [cutoff]

        if caller_id:
            query += " AND caller_id = ?"
            params.append(caller_id)

        query += " GROUP BY day ORDER BY day"

        rows = []
        async with self._conn.execute(query, params) as cur:
            async for r in cur:
                rows.append({
                    "date": r[0],
                    "input_tokens": r[1],
                    "output_tokens": r[2],
                    "calls": r[3],
                })
        return rows

    async def recent(self, limit: int = 50) -> list[dict]:
        """Last N raw records."""
        if not self._conn:
            return []
        limit = min(limit, 500)
        rows = []
        async with self._conn.execute(
            "SELECT id, timestamp, caller_id, model, input_tokens, output_tokens, "
            "cache_read, cache_create, total_tokens FROM token_usage "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            async for r in cur:
                rows.append({
                    "id": r[0],
                    "timestamp": r[1],
                    "caller_id": r[2],
                    "model": r[3],
                    "input_tokens": r[4],
                    "output_tokens": r[5],
                    "cache_read": r[6],
                    "cache_create": r[7],
                    "total_tokens": r[8],
                })
        return rows
