"""Memory tools — LangChain tools for GRIM's persistent working memory.

These let agents read and update memory.md via Kronos MCP tools.
Memory operations go through the MCP server (running on the host),
so writes persist across Docker container rebuilds.
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from core.tools.kronos_read import _call_mcp

logger = logging.getLogger(__name__)


@tool
async def read_grim_memory(section: str | None = None) -> str:
    """Read GRIM's persistent working memory (memory.md from kronos-vault).

    Args:
        section: Optional section name to read (e.g., "Active Objectives",
                 "User Preferences"). Omit to read full memory.

    Returns the memory content including sections:
    Active Objectives, Recent Topics, User Preferences,
    Key Learnings, Future Goals, Session Notes.
    """
    kwargs = {}
    if section:
        kwargs["section"] = section
    result = await _call_mcp("kronos_memory_read", **kwargs)
    if isinstance(result, dict):
        if "error" in result:
            return f"Error: {result['error']}"
        return result.get("content") or "(memory is empty)"
    return str(result) if result else "(memory is empty)"


@tool
async def update_grim_memory(section: str, content: str) -> str:
    """Update a specific section of GRIM's persistent working memory.

    Args:
        section: The section name to update (e.g., "Recent Topics",
                 "Key Learnings", "User Preferences", "Future Goals",
                 "Session Notes", "Active Objectives")
        content: The new content for that section (markdown format)
    """
    result = await _call_mcp(
        "kronos_memory_update",
        section=section,
        content=content,
    )
    if isinstance(result, dict):
        if "error" in result:
            return f"Error: {result['error']}"
        if result.get("ok"):
            return f"Updated section '{section}' in memory.md"
    return f"Memory update result: {result}"


MEMORY_TOOLS = [read_grim_memory, update_grim_memory]

# Register with tool registry
from core.tools.registry import tool_registry
tool_registry.register_group("memory", MEMORY_TOOLS)
