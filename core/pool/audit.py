"""Tool audit gates — real-time tool filtering for pool agents.

Implements the canUseTool pattern from Lloyd's design:
- Destructive tools require explicit allowlisting per job type
- SAFE_BASH regex patterns for read-only commands
- Tool input validation (detect dangerous patterns)

This module provides tool-level gating that filters which tools
a pool agent can actually use, beyond the broad allowed_tools list.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ToolVerdict(str, Enum):
    """Result of a canUseTool check."""
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class AuditResult:
    """Result of evaluating a tool call."""
    verdict: ToolVerdict
    tool_name: str
    reason: str = ""
    input_summary: str = ""


# ── Safe Bash patterns ──────────────────────────────────────────

# Read-only bash commands that are always safe
SAFE_BASH_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*git\s+(status|log|diff|branch|show|rev-parse|remote|tag)", re.IGNORECASE),
    re.compile(r"^\s*ls\b", re.IGNORECASE),
    re.compile(r"^\s*cat\b", re.IGNORECASE),
    re.compile(r"^\s*head\b", re.IGNORECASE),
    re.compile(r"^\s*tail\b", re.IGNORECASE),
    re.compile(r"^\s*wc\b", re.IGNORECASE),
    re.compile(r"^\s*find\b", re.IGNORECASE),
    re.compile(r"^\s*grep\b", re.IGNORECASE),
    re.compile(r"^\s*rg\b", re.IGNORECASE),
    re.compile(r"^\s*echo\b", re.IGNORECASE),
    re.compile(r"^\s*pwd\b", re.IGNORECASE),
    re.compile(r"^\s*which\b", re.IGNORECASE),
    re.compile(r"^\s*whoami\b", re.IGNORECASE),
    re.compile(r"^\s*date\b", re.IGNORECASE),
    re.compile(r"^\s*python\s+-m\s+pytest\b", re.IGNORECASE),
    re.compile(r"^\s*python\s+-c\b", re.IGNORECASE),
    re.compile(r"^\s*npm\s+(test|run\s+test|run\s+lint)", re.IGNORECASE),
    re.compile(r"^\s*tree\b", re.IGNORECASE),
    re.compile(r"^\s*env\b", re.IGNORECASE),
    re.compile(r"^\s*printenv\b", re.IGNORECASE),
]

# Dangerous patterns that should always be blocked
DANGEROUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\s+--force\b", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\s+-[fd]", re.IGNORECASE),
    re.compile(r"\bcurl\b.*\|\s*(?:bash|sh)\b", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r">\s*/dev/", re.IGNORECASE),
    re.compile(r"\bkill\s+-9\b", re.IGNORECASE),
]

# Read-only tools (always safe)
READ_ONLY_TOOLS = frozenset({
    "Read", "Glob", "Grep",
    "mcp__kronos__kronos_search",
    "mcp__kronos__kronos_get",
    "mcp__kronos__kronos_list",
    "mcp__kronos__kronos_tags",
    "mcp__kronos__kronos_graph",
    "mcp__kronos__kronos_deep_dive",
    "mcp__kronos__kronos_navigate",
    "mcp__kronos__kronos_read_source",
    "mcp__kronos__kronos_search_source",
    "mcp__kronos__kronos_memory_read",
    "mcp__kronos__kronos_memory_sections",
    "mcp__kronos__kronos_notes_recent",
    "mcp__kronos__kronos_skills",
    "mcp__kronos__kronos_skill_load",
    "mcp__kronos__kronos_task_list",
    "mcp__kronos__kronos_task_get",
    "mcp__kronos__kronos_board_view",
    "mcp__kronos__kronos_backlog_view",
    "mcp__kronos__kronos_calendar_view",
})

# Write tools (need explicit allowlisting)
WRITE_TOOLS = frozenset({
    "Write", "Edit", "NotebookEdit",
    "mcp__kronos__kronos_create",
    "mcp__kronos__kronos_update",
    "mcp__kronos__kronos_note_append",
    "mcp__kronos__kronos_memory_update",
    "mcp__kronos__kronos_task_create",
    "mcp__kronos__kronos_task_update",
    "mcp__kronos__kronos_task_move",
    "mcp__kronos__kronos_task_archive",
    "mcp__kronos__kronos_calendar_add",
    "mcp__kronos__kronos_calendar_update",
    "mcp__kronos__kronos_calendar_sync",
})


def is_safe_bash(command: str) -> bool:
    """Check if a bash command matches a safe (read-only) pattern."""
    # Check dangerous patterns first
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return False

    # Check safe patterns
    for pattern in SAFE_BASH_PATTERNS:
        if pattern.match(command):
            return True

    return False


def can_use_tool(
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    *,
    allow_writes: bool = False,
    allow_bash: bool = False,
) -> AuditResult:
    """Evaluate whether a tool call should be allowed.

    Args:
        tool_name: The tool being called (e.g., "Write", "Bash", "mcp__kronos__kronos_search")
        tool_input: The tool's input arguments
        allow_writes: Whether write tools are permitted for this job
        allow_bash: Whether bash commands are permitted for this job

    Returns:
        AuditResult with verdict and reason.
    """
    tool_input = tool_input or {}

    # Read-only tools: always allowed
    if tool_name in READ_ONLY_TOOLS:
        return AuditResult(
            verdict=ToolVerdict.ALLOW,
            tool_name=tool_name,
            reason="read-only tool",
        )

    # Bash: check command safety
    if tool_name == "Bash":
        if not allow_bash:
            return AuditResult(
                verdict=ToolVerdict.DENY,
                tool_name=tool_name,
                reason="bash not allowed for this job type",
            )

        command = tool_input.get("command", "")
        if is_safe_bash(command):
            return AuditResult(
                verdict=ToolVerdict.ALLOW,
                tool_name=tool_name,
                reason="safe bash pattern",
                input_summary=command[:100],
            )

        # Non-safe bash: check for dangerous patterns
        for pattern in DANGEROUS_PATTERNS:
            if pattern.search(command):
                return AuditResult(
                    verdict=ToolVerdict.DENY,
                    tool_name=tool_name,
                    reason=f"dangerous pattern: {pattern.pattern}",
                    input_summary=command[:100],
                )

        # Non-safe, non-dangerous: allow if writes are permitted
        if allow_writes:
            return AuditResult(
                verdict=ToolVerdict.ALLOW,
                tool_name=tool_name,
                reason="bash allowed (writes permitted)",
                input_summary=command[:100],
            )

        return AuditResult(
            verdict=ToolVerdict.DENY,
            tool_name=tool_name,
            reason="non-safe bash command requires write permission",
            input_summary=command[:100],
        )

    # Write tools: only if allowed
    if tool_name in WRITE_TOOLS:
        if allow_writes:
            return AuditResult(
                verdict=ToolVerdict.ALLOW,
                tool_name=tool_name,
                reason="write tool allowed",
            )
        return AuditResult(
            verdict=ToolVerdict.DENY,
            tool_name=tool_name,
            reason="write tool not allowed for this job type",
        )

    # Unknown tool: allow by default (may be an MCP tool we don't track)
    return AuditResult(
        verdict=ToolVerdict.ALLOW,
        tool_name=tool_name,
        reason="unknown tool (allowed by default)",
    )
