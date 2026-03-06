"""Session manager — maps session IDs to GrimClient instances.

Handles create/resume/destroy lifecycle for v2 WebSocket chat sessions.
Each session gets its own GrimClient with persistent conversation context.
Optionally backed by ConversationStore for durable message history.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from core.client import GrimClient
from core.config import GrimConfig

logger = logging.getLogger("grim.sessions")


@dataclass
class SessionInfo:
    """Metadata about a managed session."""

    session_id: str
    client: GrimClient
    created_at: float = field(default_factory=time.monotonic)
    last_active: float = field(default_factory=time.monotonic)
    caller_id: str = "peter"

    def touch(self) -> None:
        self.last_active = time.monotonic()

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_active

    def to_dict(self) -> dict:
        info = self.client.session_info
        return {
            **info,
            "session_id": self.session_id,
            "caller_id": self.caller_id,
            "idle_seconds": round(self.idle_seconds),
        }


class SessionManager:
    """Manages GrimClient sessions for the v2 WebSocket endpoint.

    - Creates a new GrimClient per session_id (lazy, on first message)
    - Reuses existing sessions on reconnect (same session_id)
    - Reaps idle sessions after max_idle_seconds
    - Limits total concurrent sessions via max_sessions
    - Optionally persists conversation history via ConversationStore
    """

    def __init__(
        self,
        config: GrimConfig,
        *,
        max_sessions: int = 10,
        max_idle_seconds: float = 3600,  # 1 hour
        conversation_store: Optional[Any] = None,
    ):
        self.config = config
        self.max_sessions = max_sessions
        self.max_idle_seconds = max_idle_seconds
        self.store = conversation_store  # ConversationStore or None
        self._sessions: dict[str, SessionInfo] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the idle session reaper."""
        self._reaper_task = asyncio.create_task(self._reap_loop())
        logger.info("SessionManager started (max=%d, idle=%ds)",
                     self.max_sessions, self.max_idle_seconds)

    async def stop(self) -> None:
        """Stop all sessions and the reaper."""
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass

        async with self._lock:
            for info in list(self._sessions.values()):
                await self._destroy_session(info)
            self._sessions.clear()

        logger.info("SessionManager stopped")

    async def get_or_create(
        self,
        session_id: str,
        caller_id: str = "peter",
    ) -> GrimClient:
        """Get an existing session or create a new one.

        Returns a started GrimClient ready for send/send_streaming.
        """
        async with self._lock:
            if session_id in self._sessions:
                info = self._sessions[session_id]
                info.touch()
                return info.client

            # Evict oldest idle session if at capacity
            if len(self._sessions) >= self.max_sessions:
                await self._evict_oldest()

            client = GrimClient(
                self.config,
                max_turns=10,
                caller_id=caller_id,
            )
            await client.start()

            info = SessionInfo(
                session_id=session_id,
                client=client,
                caller_id=caller_id,
            )
            self._sessions[session_id] = info

            # Persist session record
            if self.store:
                try:
                    await self.store.save_session(session_id, caller_id=caller_id)
                except Exception as e:
                    logger.warning("Failed to persist session %s: %s", session_id, e)

            logger.info("Session created: %s (total=%d)", session_id, len(self._sessions))
            return client

    async def destroy(self, session_id: str) -> bool:
        """Destroy a session by ID. Returns True if it existed."""
        async with self._lock:
            info = self._sessions.pop(session_id, None)
            if info:
                await self._destroy_session(info)
                # Mark closed in store (keep history)
                if self.store:
                    try:
                        await self.store.close_session(session_id)
                    except Exception as e:
                        logger.warning("Failed to close session in store: %s", e)
                return True
            return False

    def list_sessions(self) -> list[dict]:
        """List all active sessions with metadata."""
        return [info.to_dict() for info in self._sessions.values()]

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def touch(self, session_id: str) -> None:
        """Update last-active timestamp for a session."""
        info = self._sessions.get(session_id)
        if info:
            info.touch()

    async def save_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str | None = None,
        cost_usd: float | None = None,
        tools_used: list[str] | None = None,
    ) -> None:
        """Persist a conversation turn to the store."""
        if not self.store:
            return
        try:
            # Get turn number from session info
            info = self._sessions.get(session_id)
            turn = info.client.session_info.get("turn_count", 1) if info else 1
            await self.store.save_message(
                session_id=session_id,
                turn_number=turn,
                user_message=user_message,
                assistant_message=assistant_message,
                cost_usd=cost_usd,
                tools_used=tools_used,
            )
            await self.store.touch_session(session_id)
        except Exception as e:
            logger.warning("Failed to save turn for %s: %s", session_id, e)

    async def get_history(
        self, session_id: str, *, limit: int = 100, offset: int = 0,
    ) -> list[dict]:
        """Get conversation history from the store."""
        if not self.store:
            return []
        return await self.store.get_messages(session_id, limit=limit, offset=offset)

    # ── Private ──────────────────────────────────────────────────

    async def _destroy_session(self, info: SessionInfo) -> None:
        try:
            await info.client.stop()
        except Exception as e:
            logger.warning("Error stopping session %s: %s", info.session_id, e)
        logger.info("Session destroyed: %s", info.session_id)

    async def _evict_oldest(self) -> None:
        """Evict the oldest idle session to make room."""
        if not self._sessions:
            return
        oldest = min(self._sessions.values(), key=lambda s: s.last_active)
        self._sessions.pop(oldest.session_id, None)
        await self._destroy_session(oldest)

    async def _reap_loop(self) -> None:
        """Periodically destroy idle sessions."""
        while True:
            await asyncio.sleep(60)  # check every minute
            async with self._lock:
                expired = [
                    sid for sid, info in self._sessions.items()
                    if info.idle_seconds > self.max_idle_seconds
                ]
                for sid in expired:
                    info = self._sessions.pop(sid)
                    await self._destroy_session(info)
                if expired:
                    logger.info("Reaped %d idle sessions", len(expired))
