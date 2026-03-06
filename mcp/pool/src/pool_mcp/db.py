"""Read-only SQLite access layer for the pool job queue.

The pool process (GRIM server) owns writes. This module only reads.
SQLite WAL mode supports concurrent readers safely.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("pool-mcp.db")


class PoolDB:
    """Read-only access to the pool SQLite database."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            if not Path(self._db_path).exists():
                raise FileNotFoundError(f"Pool DB not found: {self._db_path}")
            self._conn = sqlite3.connect(self._db_path, timeout=5)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA query_only=ON")
            logger.info("Connected to pool DB: %s", self._db_path)
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Job queries ──────────────────────────────────────────────────

    def list_jobs(
        self,
        status: str | None = None,
        job_type: str | None = None,
        limit: int = 50,
        since: str | None = None,
    ) -> list[dict]:
        """List jobs with optional filters. Returns newest first."""
        conn = self._connect()
        clauses: list[str] = []
        params: list[Any] = []

        if status:
            clauses.append("status = ?")
            params.append(status)
        if job_type:
            clauses.append("job_type = ?")
            params.append(job_type)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM jobs {where} ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_job(self, job_id: str) -> dict | None:
        """Get full details for a single job."""
        conn = self._connect()
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row, include_transcript=True)

    def get_transcript(
        self, job_id: str, offset: int = 0, limit: int = 100
    ) -> dict:
        """Get transcript lines for a job with pagination."""
        conn = self._connect()
        row = conn.execute(
            "SELECT transcript, status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return {"error": f"Job not found: {job_id}"}

        transcript_raw = row["transcript"]
        if not transcript_raw:
            return {"job_id": job_id, "status": row["status"], "lines": [], "total": 0}

        try:
            lines = json.loads(transcript_raw)
        except (json.JSONDecodeError, TypeError):
            lines = []

        total = len(lines)
        page = lines[offset : offset + limit]
        return {
            "job_id": job_id,
            "status": row["status"],
            "lines": page,
            "total": total,
            "offset": offset,
            "has_more": offset + limit < total,
        }

    # ── Aggregate queries ────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Pool overview: counts by status."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        ).fetchall()
        by_status = {r["status"]: r["cnt"] for r in rows}

        total = conn.execute("SELECT COUNT(*) as cnt FROM jobs").fetchone()
        return {
            "total_jobs": total["cnt"] if total else 0,
            "by_status": by_status,
            "queued": by_status.get("queued", 0),
            "running": by_status.get("running", 0),
            "complete": by_status.get("complete", 0),
            "failed": by_status.get("failed", 0),
            "blocked": by_status.get("blocked", 0),
            "cancelled": by_status.get("cancelled", 0),
            "review": by_status.get("review", 0),
        }

    def get_metrics(self) -> dict:
        """Aggregated metrics: completion rate, avg duration, cost by type."""
        conn = self._connect()

        # Completion stats
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE status IN ('complete', 'failed')"
        ).fetchone()
        completed = conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE status = 'complete'"
        ).fetchone()
        failed = conn.execute(
            "SELECT COUNT(*) as cnt FROM jobs WHERE status = 'failed'"
        ).fetchone()

        total_n = total["cnt"] if total else 0
        completed_n = completed["cnt"] if completed else 0
        failed_n = failed["cnt"] if failed else 0

        # By type
        by_type = conn.execute(
            "SELECT job_type, COUNT(*) as cnt, "
            "SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) as completed "
            "FROM jobs GROUP BY job_type"
        ).fetchall()

        return {
            "total_finished": total_n,
            "completed": completed_n,
            "failed": failed_n,
            "completion_rate": round(completed_n / total_n, 3) if total_n > 0 else 0,
            "by_type": {
                r["job_type"]: {"total": r["cnt"], "completed": r["completed"]}
                for r in by_type
            },
        }

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row, include_transcript: bool = False) -> dict:
        """Convert a sqlite3.Row to a plain dict."""
        d = dict(row)
        # Parse JSON fields
        for field in ("kronos_domains", "kronos_fdo_ids"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        if include_transcript and d.get("transcript"):
            try:
                d["transcript"] = json.loads(d["transcript"])
            except (json.JSONDecodeError, TypeError):
                d["transcript"] = []
        elif not include_transcript:
            # Don't send full transcript in list views
            d.pop("transcript", None)
            # Include transcript line count instead
            if row["transcript"]:
                try:
                    d["transcript_lines"] = len(json.loads(row["transcript"]))
                except (json.JSONDecodeError, TypeError):
                    d["transcript_lines"] = 0
            else:
                d["transcript_lines"] = 0
        return d
