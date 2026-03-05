"""Sandbox MCP session wrapper — blocks vault/memory writes for eval testing.

When GRIM runs in sandbox mode (sandbox=True in state), this wrapper
intercepts write tool calls and returns synthetic success responses.
Read tools pass through to the real MCP session unchanged.

Two layers of defense:
1. SandboxMCPSession — wraps a real session object (for direct use)
2. Context-var based sandbox_call_mcp — intercepts _call_mcp at the tool level
   (per-request safe with concurrent WebSocket sessions)
"""

from __future__ import annotations

import contextvars
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Tools that modify vault, memory, tasks, calendar, or notes
WRITE_TOOLS = frozenset({
    "kronos_create",
    "kronos_update",
    "kronos_memory_update",
    "kronos_note_append",
    "kronos_task_create",
    "kronos_task_update",
    "kronos_task_move",
    "kronos_task_archive",
    "kronos_calendar_add",
    "kronos_calendar_update",
    "kronos_calendar_sync",
})


# ---------------------------------------------------------------------------
# Per-request sandbox flag (contextvar — safe with concurrent async)
# ---------------------------------------------------------------------------

_sandbox_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_sandbox_active", default=False,
)
_sandbox_blocked: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "_sandbox_blocked",
)


def activate_sandbox() -> None:
    """Enable sandbox mode for the current async context."""
    _sandbox_active.set(True)
    _sandbox_blocked.set([])
    logger.info("SANDBOX: activated for current request")


def deactivate_sandbox() -> None:
    """Disable sandbox mode for the current async context."""
    _sandbox_active.set(False)
    logger.info("SANDBOX: deactivated")


def is_sandbox_active() -> bool:
    """Check if sandbox mode is active in the current context."""
    return _sandbox_active.get(False)


def get_blocked_calls() -> list[dict[str, Any]]:
    """Return the audit trail of blocked calls in this sandbox session."""
    try:
        return _sandbox_blocked.get()
    except LookupError:
        return []


# ---------------------------------------------------------------------------
# Synthetic response helpers
# ---------------------------------------------------------------------------

@dataclass
class _SyntheticContent:
    """Mimics MCP TextContent for synthetic responses."""
    text: str


@dataclass
class _SyntheticResult:
    """Mimics MCP CallToolResult for synthetic responses."""
    content: list[_SyntheticContent] = field(default_factory=list)


def _make_synthetic_response(method: str, kwargs: dict[str, Any]) -> _SyntheticResult:
    """Build a plausible synthetic success response for a blocked write tool."""
    responses = {
        "kronos_create": {"status": "ok", "id": kwargs.get("id", "sandbox-fdo"), "sandbox": True},
        "kronos_update": {"status": "ok", "id": kwargs.get("id", "sandbox-fdo"), "sandbox": True},
        "kronos_memory_update": {"status": "ok", "sandbox": True},
        "kronos_note_append": {"status": "ok", "anchor": "sandbox-note", "sandbox": True},
        "kronos_task_create": {"status": "ok", "id": "sandbox-task-001", "sandbox": True},
        "kronos_task_update": {"status": "ok", "sandbox": True},
        "kronos_task_move": {"status": "ok", "sandbox": True},
        "kronos_task_archive": {"status": "ok", "archived": 0, "sandbox": True},
        "kronos_calendar_add": {"status": "ok", "event_id": "sandbox-event", "sandbox": True},
        "kronos_calendar_update": {"status": "ok", "sandbox": True},
        "kronos_calendar_sync": {"status": "ok", "sandbox": True},
    }
    payload = responses.get(method, {"status": "ok", "sandbox": True})
    return _SyntheticResult(content=[_SyntheticContent(text=json.dumps(payload))])


# ---------------------------------------------------------------------------
# SandboxMCPSession — wraps a real MCP session object
# ---------------------------------------------------------------------------

class SandboxMCPSession:
    """Wraps a real MCP session, intercepting write calls in sandbox mode.

    Read tools pass through to the real session. Write tools return
    synthetic success responses so GRIM doesn't error but nothing
    is persisted to the vault, memory, or task board.
    """

    def __init__(self, real_session: Any) -> None:
        self._real = real_session
        self.blocked_calls: list[dict[str, Any]] = []

    async def call_tool(self, method: str, kwargs: dict[str, Any] | None = None) -> Any:
        """Route tool calls — block writes, pass through reads."""
        kwargs = kwargs or {}
        if method in WRITE_TOOLS:
            logger.info("SANDBOX: blocked %s(%s)", method, list(kwargs.keys()))
            self.blocked_calls.append({"method": method, "kwargs": kwargs})
            return _make_synthetic_response(method, kwargs)
        return await self._real.call_tool(method, kwargs)

    def __getattr__(self, name: str) -> Any:
        """Proxy all other attributes to the real session."""
        return getattr(self._real, name)
