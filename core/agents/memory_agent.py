"""Memory Agent — all Kronos vault write operations.

The Memory Agent follows skill protocols (kronos-capture, kronos-promote,
kronos-relate, kronos-reflect) to perform vault writes. It's the only
agent that can create or modify FDOs.

GRIM (the thinker) never writes to Kronos directly. When vault changes
are needed, GRIM formulates the request and Dispatch sends it here.
"""
from __future__ import annotations

import logging

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.tools.kronos_write import MEMORY_AGENT_TOOLS
from core.tools.memory_tools import MEMORY_TOOLS
from core.tools.kronos_tasks import TASK_ALL_TOOLS

logger = logging.getLogger(__name__)


class MemoryAgent(BaseAgent):
    """Agent for all Kronos vault operations + GRIM working memory."""

    agent_name = "memory"
    agent_display_name = "Memory"
    agent_role = "vault_ops"
    agent_description = "Kronos vault operations — search, create, update FDOs, task management"
    agent_color = "#8b5cf6"

    protocol_priority = [
        "kronos-capture", "kronos-promote",
        "kronos-relate", "kronos-reflect",
    ]
    default_protocol = (
        "You are a memory agent with full Kronos vault access.\n"
        "Use kronos tools to search, create, update, and relate FDOs.\n"
        "Use task tools (kronos_board_view, kronos_task_create, kronos_task_move, etc.) "
        "for project/task management operations.\n"
        "Always execute the task — do not say you can't do something "
        "if you have a tool that can do it."
    )

    def __init__(self, config: GrimConfig) -> None:
        tools = list(MEMORY_AGENT_TOOLS) + list(MEMORY_TOOLS) + list(TASK_ALL_TOOLS)
        super().__init__(config=config, tools=tools)


def make_memory_agent(config: GrimConfig):
    """Create a Memory Agent callable for the dispatch node."""
    return MemoryAgent.make_callable(config)


# Discovery attributes for AgentRegistry
__agent_name__ = "memory"
__make_agent__ = make_memory_agent
__agent_class__ = MemoryAgent
