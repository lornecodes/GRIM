"""GrimLiveClient — WebSocket client for live integration testing.

Connects to a running GRIM server, sends messages with sandbox=true,
and collects all trace events until graph completion or timeout.
Supports multi-turn conversations by maintaining session state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]


@dataclass
class TurnTrace:
    """Trace data from a single turn (message → response)."""

    events: list[dict[str, Any]] = field(default_factory=list)
    response_text: str = ""
    wall_time_ms: int = 0
    error: str | None = None


@dataclass
class SessionTrace:
    """Full trace of a multi-turn session."""

    session_id: str = ""
    turns: list[TurnTrace] = field(default_factory=list)
    total_wall_time_ms: int = 0


class GrimLiveClient:
    """WebSocket client for live GRIM integration testing.

    Usage:
        client = GrimLiveClient("ws://localhost:8080")
        trace = await client.send_turns([
            "Hello GRIM",
            "Search the vault for PAC",
        ])
    """

    def __init__(
        self,
        ws_base_url: str = "ws://localhost:8080",
        timeout_ms: int = 120_000,
        sandbox: bool = True,
    ) -> None:
        if websockets is None:
            raise ImportError("websockets package required: pip install websockets")
        self.ws_base_url = ws_base_url.rstrip("/")
        self.timeout_s = timeout_ms / 1000.0
        self.sandbox = sandbox

    async def send_turns(
        self,
        messages: list[str],
        session_id: str | None = None,
        caller_id: str = "eval",
    ) -> SessionTrace:
        """Send multiple messages in sequence, collecting traces for each."""
        session_id = session_id or f"eval-{uuid.uuid4().hex[:8]}"
        url = f"{self.ws_base_url}/ws/{session_id}"
        session_trace = SessionTrace(session_id=session_id)
        t0_session = time.monotonic()

        try:
            async with websockets.connect(url) as ws:  # type: ignore[union-attr]
                for message in messages:
                    turn_trace = await self._send_one(ws, message, caller_id)
                    session_trace.turns.append(turn_trace)
        except Exception as exc:
            logger.error("WS connection error: %s", exc)
            # Add error to last turn or create one
            error_turn = TurnTrace(error=str(exc))
            if not session_trace.turns:
                session_trace.turns.append(error_turn)
            else:
                session_trace.turns[-1].error = str(exc)

        session_trace.total_wall_time_ms = int(
            (time.monotonic() - t0_session) * 1000
        )
        return session_trace

    async def _send_one(
        self,
        ws: Any,
        message: str,
        caller_id: str,
    ) -> TurnTrace:
        """Send a single message and collect all events until response."""
        trace = TurnTrace()
        t0 = time.monotonic()

        payload = {
            "message": message,
            "caller_id": caller_id,
            "sandbox": self.sandbox,
        }
        await ws.send(json.dumps(payload))

        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout_s)
                event = json.loads(raw)
                trace.events.append(event)

                evt_type = event.get("type", "")

                if evt_type == "response":
                    trace.response_text = event.get("content", "")
                    break
                elif evt_type == "error":
                    trace.error = event.get("content", event.get("message", ""))
                    break

        except asyncio.TimeoutError:
            trace.error = f"Timeout after {self.timeout_s}s"
        except Exception as exc:
            trace.error = str(exc)

        trace.wall_time_ms = int((time.monotonic() - t0) * 1000)
        return trace

    async def health_check(self) -> bool:
        """Check if the GRIM server is reachable."""
        try:
            import aiohttp
            url = self.ws_base_url.replace("ws://", "http://").replace("wss://", "https://")
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    return resp.status == 200
        except Exception:
            return False
