"""IronClaw tool wrappers — LangChain tools that delegate to the IronClaw gateway.

These tools mirror the workspace tools (file_read, file_write, shell, etc.)
but execute through IronClaw's sandboxed environment with security policies,
DLP scanning, and audit logging.

The bridge instance is injected at graph build time via set_bridge().
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from langchain_core.tools import tool

from core.bridge.ironclaw import IronClawBridge, ToolResult
from core.tools.context import tool_context

logger = logging.getLogger(__name__)


def set_bridge(bridge: IronClawBridge) -> None:
    """Inject the IronClaw bridge instance. Deprecated: use tool_context.configure()."""
    tool_context.ironclaw_bridge = bridge
    logger.info("IronClaw tools: bridge connected")


def _get_bridge() -> IronClawBridge:
    """Get the bridge, raising if not set."""
    if tool_context.ironclaw_bridge is None:
        raise RuntimeError("IronClaw bridge not initialized — call set_bridge() first")
    return tool_context.ironclaw_bridge


def _format_result(result: ToolResult) -> str:
    """Format a ToolResult as a string for the LLM."""
    parts = []

    if result.success:
        parts.append(result.output)
    else:
        parts.append(f"[FAILED] {result.output}")

    if result.stderr:
        parts.append(f"\n[stderr] {result.stderr}")

    if result.timed_out:
        parts.append("\n[TIMED OUT]")

    if result.duration_ms > 0:
        parts.append(f"\n[{result.duration_ms}ms | sandbox: {result.execution_id[:8] if result.execution_id else 'n/a'}]")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool definitions — each wraps an IronClaw gateway tool
# ---------------------------------------------------------------------------


@tool
async def claw_read_file(
    path: str,
    start_line: int = 1,
    end_line: int = 0,
) -> str:
    """Read a file through IronClaw's sandboxed environment.

    Security: DLP scanning applied to output. Deny-listed paths blocked.

    Args:
        path: File path to read (relative to workspace root).
        start_line: First line to read (1-indexed, default: 1).
        end_line: Last line to read (0 = entire file).
    """
    bridge = _get_bridge()
    args = {"path": path}
    if start_line > 1:
        args["start_line"] = start_line
    if end_line > 0:
        args["end_line"] = end_line

    result = await bridge.execute_tool("file_read", args)
    return _format_result(result)


@tool
async def claw_write_file(
    path: str,
    content: str,
) -> str:
    """Write a file through IronClaw's sandboxed environment.

    Security: Permission-checked, audit-logged, DLP-scanned.

    Args:
        path: File path to write (relative to workspace root).
        content: File content to write.
    """
    bridge = _get_bridge()
    result = await bridge.execute_tool("file_write", {
        "path": path,
        "content": content,
    })
    return _format_result(result)


@tool
async def claw_shell(
    command: str,
    cwd: str = ".",
) -> str:
    """Execute a shell command through IronClaw's sandboxed environment.

    Security: Command guardian validates patterns. DLP scans output.
    Dangerous patterns (rm -rf /, fork bombs, etc.) are blocked.

    Args:
        command: Shell command to execute.
        cwd: Working directory (default: workspace root).
    """
    bridge = _get_bridge()
    result = await bridge.execute_tool("shell", {
        "command": command,
        "cwd": cwd,
    })
    return _format_result(result)


@tool
async def claw_list_dir(
    path: str = ".",
) -> str:
    """List directory contents through IronClaw's sandboxed environment.

    Args:
        path: Directory path (relative to workspace root, default: current).
    """
    bridge = _get_bridge()
    result = await bridge.execute_tool("directory_list", {"path": path})
    return _format_result(result)


@tool
async def claw_http_request(
    url: str,
    method: str = "GET",
    body: Optional[str] = None,
    headers: Optional[str] = None,
) -> str:
    """Make an HTTP request through IronClaw's sandboxed environment.

    Security: SSRF protection applied. Domain allow/block lists enforced.

    Args:
        url: Target URL.
        method: HTTP method (GET, POST, PUT, DELETE).
        body: Request body (for POST/PUT).
        headers: JSON string of additional headers.
    """
    bridge = _get_bridge()
    args: dict = {"url": url, "method": method}
    if body:
        args["body"] = body
    if headers:
        try:
            args["headers"] = json.loads(headers)
        except json.JSONDecodeError:
            return "[FAILED] Invalid JSON in headers parameter"

    result = await bridge.execute_tool("http_request", args)
    return _format_result(result)


# ---------------------------------------------------------------------------
# Tool lists for agent registration
# ---------------------------------------------------------------------------

# All sandboxed tools
IRONCLAW_TOOLS = [
    claw_read_file,
    claw_write_file,
    claw_shell,
    claw_list_dir,
    claw_http_request,
]

# Read-only subset (for safer operations)
IRONCLAW_READ_TOOLS = [
    claw_read_file,
    claw_list_dir,
]

# Register with tool registry
from core.tools.registry import tool_registry
tool_registry.register_group("ironclaw", IRONCLAW_TOOLS)
tool_registry.register_group("ironclaw_read", IRONCLAW_READ_TOOLS)
