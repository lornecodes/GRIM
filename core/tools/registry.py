"""Tool registry — centralized tool group management.

Tool modules register their groups here at import time.
Agents declare which tool_groups they need, and BaseAgent
resolves them through this registry.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Maps tool group names to lists of LangChain tools.

    Tool modules call register_group() when imported.
    Agents declare tool_groups as a class attribute, and
    BaseAgent.__init__ resolves them via for_agent().
    """

    def __init__(self) -> None:
        self._groups: dict[str, list[BaseTool]] = {}

    def register_group(self, name: str, tools: list[BaseTool]) -> None:
        """Register a named tool group."""
        self._groups[name] = list(tools)
        logger.debug("Tool group registered: %s (%d tools)", name, len(tools))

    def get_group(self, name: str) -> list[BaseTool]:
        """Get tools for a group. Returns empty list if not found."""
        return list(self._groups.get(name, []))

    def for_agent(self, tool_groups: list[str]) -> list[BaseTool]:
        """Resolve a list of tool group names into a flat tool list.

        Deduplicates by tool name (first occurrence wins).
        """
        seen: set[str] = set()
        tools: list[BaseTool] = []
        for group_name in tool_groups:
            for tool in self.get_group(group_name):
                if tool.name not in seen:
                    seen.add(tool.name)
                    tools.append(tool)
        return tools

    def groups(self) -> list[str]:
        """Return all registered group names."""
        return list(self._groups.keys())

    def __repr__(self) -> str:
        total = sum(len(t) for t in self._groups.values())
        return f"ToolRegistry({len(self._groups)} groups, {total} tools)"


# Module-level singleton
tool_registry = ToolRegistry()
