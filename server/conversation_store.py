"""Durable conversation storage — SQLite-backed message history.

Stores session metadata and message transcripts so v2 GrimClient
sessions survive server restarts.  This is a conversation *log*,
not SDK checkpointing — the Claude Agent SDK manages its own
internal conversation state.

Tables:
  sessions  — one row per session (id, caller, timestamps, metadata)
  messages  — one row per turn (user msg + assistant response pair)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger("grim.conversation_store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    caller_id     TEXT NOT NULL DEFAULT 'peter',
    created_at    TEXT NOT NULL,
    last_active   TEXT NOT NULL,
    closed        INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES sessions(id),
    turn_number   INTEGER NOT NULL,
    user_message  TEXT NOT NULL,
    assistant_message TEXT,
    cost_usd      REAL,
    tools_used    TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, turn_number);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    """Async SQLite store for conversation history."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        """Open the database and create tables if needed."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()
        logger.info("ConversationStore ready: %s", self.db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    # ── Sessions ──────────────────────────────────────────────────

    async def save_session(
        self,
        session_id: str,
        caller_id: str = "peter",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create or update a session record."""
        assert self._db
        now = _utc_now()
        meta_json = json.dumps(metadata or {})
        await self._db.execute(
            """
            INSERT INTO sessions (id, caller_id, created_at, last_active, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_active = excluded.last_active,
                metadata_json = excluded.metadata_json
            """,
            (session_id, caller_id, now, now, meta_json),
        )
        await self._db.commit()

    async def touch_session(self, session_id: str) -> None:
        """Update last_active timestamp."""
        assert self._db
        await self._db.execute(
            "UPDATE sessions SET last_active = ? WHERE id = ?",
            (_utc_now(), session_id),
        )
        await self._db.commit()

    async def close_session(self, session_id: str) -> None:
        """Mark a session as closed (but keep the data)."""
        assert self._db
        await self._db.execute(
            "UPDATE sessions SET closed = 1, last_active = ? WHERE id = ?",
            (_utc_now(), session_id),
        )
        await self._db.commit()

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get session metadata. Returns None if not found."""
        assert self._db
        cursor = await self._db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "session_id": row["id"],
            "caller_id": row["caller_id"],
            "created_at": row["created_at"],
            "last_active": row["last_active"],
            "closed": bool(row["closed"]),
            "metadata": json.loads(row["metadata_json"]),
        }

    async def list_sessions(
        self, *, include_closed: bool = False,
    ) -> list[dict[str, Any]]:
        """List sessions, newest first."""
        assert self._db
        query = "SELECT * FROM sessions"
        if not include_closed:
            query += " WHERE closed = 0"
        query += " ORDER BY last_active DESC"
        cursor = await self._db.execute(query)
        rows = await cursor.fetchall()
        return [
            {
                "session_id": r["id"],
                "caller_id": r["caller_id"],
                "created_at": r["created_at"],
                "last_active": r["last_active"],
                "closed": bool(r["closed"]),
                "metadata": json.loads(r["metadata_json"]),
            }
            for r in rows
        ]

    # ── Messages ──────────────────────────────────────────────────

    async def save_message(
        self,
        session_id: str,
        turn_number: int,
        user_message: str,
        assistant_message: str | None = None,
        cost_usd: float | None = None,
        tools_used: list[str] | None = None,
    ) -> int:
        """Save a conversation turn. Returns the message row ID."""
        assert self._db
        cursor = await self._db.execute(
            """
            INSERT INTO messages
                (session_id, turn_number, user_message, assistant_message,
                 cost_usd, tools_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                turn_number,
                user_message,
                assistant_message,
                cost_usd,
                json.dumps(tools_used) if tools_used else None,
                _utc_now(),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_messages(
        self,
        session_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get messages for a session, ordered by turn number."""
        assert self._db
        cursor = await self._db.execute(
            """
            SELECT * FROM messages
            WHERE session_id = ?
            ORDER BY turn_number ASC
            LIMIT ? OFFSET ?
            """,
            (session_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "turn_number": r["turn_number"],
                "user_message": r["user_message"],
                "assistant_message": r["assistant_message"],
                "cost_usd": r["cost_usd"],
                "tools_used": json.loads(r["tools_used"]) if r["tools_used"] else None,
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    async def get_message_count(self, session_id: str) -> int:
        """Get total message count for a session."""
        assert self._db
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def delete_session(self, session_id: str) -> bool:
        """Permanently delete a session and its messages. Returns True if it existed."""
        assert self._db
        cursor = await self._db.execute(
            "DELETE FROM sessions WHERE id = ?", (session_id,),
        )
        await self._db.execute(
            "DELETE FROM messages WHERE session_id = ?", (session_id,),
        )
        await self._db.commit()
        return cursor.rowcount > 0
