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
from core.state import AgentResult, GrimState
from core.tools.kronos_write import MEMORY_AGENT_TOOLS
from core.tools.memory_tools import MEMORY_TOOLS

logger = logging.getLogger(__name__)


class MemoryAgent(BaseAgent):
    """Agent for all Kronos vault operations + GRIM working memory."""

    agent_name = "memory"

    def __init__(self, config: GrimConfig) -> None:
        # Combine Kronos tools + GRIM memory tools (memory tools use MCP, no vault path needed)
        tools = list(MEMORY_AGENT_TOOLS) + list(MEMORY_TOOLS)
        super().__init__(config=config, tools=tools)


def make_memory_agent(config: GrimConfig):
    """Create a Memory Agent callable for the dispatch node.

    Returns an async function that takes GrimState and returns AgentResult.
    """
    agent = MemoryAgent(config)

    async def memory_agent_fn(state: GrimState, *, event_queue=None) -> AgentResult:
        """Execute a memory/vault operation following skill protocols."""
        messages = state.get("messages", [])
        skill_protocols = state.get("skill_protocols", {})

        # Extract the user's request
        task = ""
        if messages:
            last_msg = messages[-1]
            task = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        # Find the most relevant skill protocol for this operation
        protocol = None
        protocol_priority = [
            "kronos-capture",
            "kronos-promote",
            "kronos-relate",
            "kronos-reflect",
        ]

        for skill_name in protocol_priority:
            if skill_name in skill_protocols:
                protocol = skill_protocols[skill_name]
                logger.info("Memory agent: using protocol '%s'", skill_name)
                break

        # If no specific protocol matched, use a general instruction
        if protocol is None and skill_protocols:
            # Use the first available protocol
            first_key = next(iter(skill_protocols))
            protocol = skill_protocols[first_key]
            logger.info("Memory agent: using fallback protocol '%s'", first_key)

        if protocol is None:
            protocol = (
                "You are a memory agent with full Kronos vault access.\n"
                "Use kronos tools to search, create, update, and relate FDOs.\n"
                "Use file tools to read vault files and source material.\n"
                "Always execute the task — do not say you can't do something "
                "if you have a tool that can do it."
            )

        # Build context from knowledge
        context = {}
        knowledge_context = state.get("knowledge_context", [])
        if knowledge_context:
            context["relevant_fdos"] = ", ".join(
                f"{fdo.id} ({fdo.domain})" for fdo in knowledge_context[:5]
            )

        return await agent.execute(
            task=task,
            skill_protocol=protocol,
            context=context,
            event_queue=event_queue,
        )

    return memory_agent_fn
