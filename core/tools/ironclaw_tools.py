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

    # Enforce staging path: if a staging job is active and the LLM provides
    # a relative path, prefix it with the staging output directory so files
    # land in the shared volume (not the container's WORKDIR).
    staging_path = tool_context.staging_path
    if staging_path and not path.startswith(staging_path):
        if not path.startswith("/"):
            path = f"{staging_path}{path}"
            logger.debug("claw_write_file: prefixed path with staging → %s", path)

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
# Agent dispatch + security scanning tools
# ---------------------------------------------------------------------------


@tool
async def claw_list_agents() -> str:
    """List IronClaw engine agent roles and their capabilities.

    Returns the roster of available agents (Coder, Researcher, Planner,
    Tester, Security Auditor, Reviewer) and coordination patterns.
    """
    bridge = _get_bridge()
    data = await bridge.list_agents()
    if not data.get("roles"):
        return "[No IronClaw agents available]"

    lines = ["## IronClaw Engine Agents\n"]
    for role in data.get("roles", []):
        caps = ", ".join(role.get("capabilities", []))
        delegate = " (can delegate)" if role.get("can_delegate") else ""
        lines.append(
            f"- **{role.get('name', role.get('id'))}** [{caps}]{delegate}: "
            f"{role.get('description', '')}"
        )

    lines.append(f"\nSessions: {data.get('active_sessions', 0)}/{data.get('max_concurrent_sessions', 0)}")
    lines.append("\nCoordination patterns: sequential, parallel, debate, hierarchical, pipeline")
    return "\n".join(lines)


@tool
async def claw_dispatch_workflow(
    task: str,
    agents: str,
    pattern: str = "sequential",
) -> str:
    """Dispatch a task to IronClaw engine agents using a coordination pattern.

    Orchestrates multiple IronClaw agents to collaborate on a task.

    Args:
        task: The task description for the agent team.
        agents: Comma-separated agent role IDs (e.g. "coder,tester,reviewer").
        pattern: Coordination pattern: sequential, parallel, debate, hierarchical, pipeline.
    """
    bridge = _get_bridge()
    agent_list = [a.strip() for a in agents.split(",")]

    pattern_config: dict = {"type": pattern}
    if pattern == "sequential":
        pattern_config["agent_order"] = agent_list
    elif pattern == "hierarchical" and len(agent_list) > 1:
        pattern_config["lead"] = agent_list[0]
        pattern_config["specialists"] = agent_list[1:]
    else:
        pattern_config["agents"] = agent_list

    result = await bridge.run_workflow(task, pattern_config)

    if result.get("status") == "failed":
        return f"[WORKFLOW FAILED] {result.get('error', 'Unknown error')}"

    lines = [
        "## Workflow Complete",
        f"- **Session**: {result.get('session_id', 'n/a')}",
        f"- **Status**: {result.get('status')}",
        f"- **Duration**: {result.get('duration_ms', 0)}ms",
        f"- **Agents**: {', '.join(result.get('agents_executed', []))}",
    ]

    for agent_id, agent_result in result.get("results", {}).items():
        lines.append(f"\n### {agent_id}")
        lines.append(str(agent_result)[:1000])

    return "\n".join(lines)


@tool
async def claw_scan_skill(code: str, file_name: str = "code.py") -> str:
    """Scan code for security vulnerabilities using IronClaw's security scanner.

    Checks for OWASP Top 10, CWE patterns, dangerous functions, credential
    exposure, and other security issues.

    Args:
        code: The source code to scan.
        file_name: Filename for context (default: code.py).
    """
    bridge = _get_bridge()
    result = await bridge.scan_skill(code, file_name)

    if result.get("error"):
        return f"[SCAN FAILED] {result['error']}"

    lines = [
        "## Security Scan Results",
        f"- **File**: {result.get('file_name', file_name)}",
        f"- **Findings**: {result.get('findings_count', 0)}",
        f"- **Risk Score**: {result.get('risk_score', 0)}/100",
        f"- **Recommendation**: {result.get('recommendation', 'N/A')}",
    ]

    for finding in result.get("findings", []):
        lines.append(
            f"\n**{finding.get('severity', 'UNKNOWN')}** — {finding.get('description', '')}\n"
            f"  Line {finding.get('line_number', '?')}: `{finding.get('matched_text', '')}`\n"
            f"  CWE: {finding.get('cwe', 'N/A')}"
        )

    if not result.get("findings"):
        lines.append("\nNo security issues found.")

    return "\n".join(lines)


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
    claw_list_agents,
    claw_dispatch_workflow,
    claw_scan_skill,
]

# Read-only subset (for safer operations)
IRONCLAW_READ_TOOLS = [
    claw_read_file,
    claw_list_dir,
]

# Agent dispatch + security scanning tools
IRONCLAW_DISPATCH_TOOLS = [
    claw_list_agents,
    claw_dispatch_workflow,
    claw_scan_skill,
]

# Register with tool registry
from core.tools.registry import tool_registry
tool_registry.register_group("ironclaw", IRONCLAW_TOOLS)
tool_registry.register_group("ironclaw_read", IRONCLAW_READ_TOOLS)
tool_registry.register_group("ironclaw_dispatch", IRONCLAW_DISPATCH_TOOLS)
