"""Coder Agent — code writing, editing, and file operations.

The Coder handles:
- Writing new files
- Editing existing code
- Searching the codebase
- Running scripts for validation

It follows skill protocols from code-execution and file-operations skills.
"""

from __future__ import annotations

import logging

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.state import AgentResult, GrimState
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.workspace import FILE_TOOLS, SHELL_TOOLS

logger = logging.getLogger(__name__)


class CoderAgent(BaseAgent):
    """Agent for code and file operations."""

    agent_name = "coder"

    def __init__(self, config: GrimConfig) -> None:
        # Coder gets: file tools + shell (for running tests) + read-only Kronos (for context)
        tools = FILE_TOOLS + SHELL_TOOLS + COMPANION_TOOLS
        super().__init__(config=config, tools=tools)


def make_coder_agent(config: GrimConfig):
    """Create a Coder Agent callable for the dispatch node.

    Returns an async function that takes GrimState and returns AgentResult.
    """
    agent = CoderAgent(config)

    async def coder_agent_fn(state: GrimState, *, event_queue=None) -> AgentResult:
        """Execute a coding task following skill protocols."""
        messages = state.get("messages", [])
        skill_protocols = state.get("skill_protocols", {})

        # Extract the user's request
        task = ""
        if messages:
            last_msg = messages[-1]
            task = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        # Find the most relevant skill protocol
        protocol = None
        protocol_priority = ["code-execution", "file-operations"]

        for skill_name in protocol_priority:
            if skill_name in skill_protocols:
                protocol = skill_protocols[skill_name]
                logger.info("Coder agent: using protocol '%s'", skill_name)
                break

        if protocol is None and skill_protocols:
            first_key = next(iter(skill_protocols))
            protocol = skill_protocols[first_key]

        if protocol is None:
            protocol = (
                "You are a coding agent with file read/write, shell execution, and git access.\n"
                "Use your tools to write code, run tests, fix bugs, and refactor.\n"
                "Use run_shell for builds, tests, and linting. Use file tools for reading and editing.\n"
                "Always execute the task — do not say you can't do something "
                "if you have a tool that can do it."
            )

        # Build context from knowledge
        context = {}
        knowledge_context = state.get("knowledge_context", [])
        if knowledge_context:
            context["relevant_knowledge"] = ", ".join(
                f"{fdo.id} ({fdo.domain})" for fdo in knowledge_context[:5]
            )

        return await agent.execute(
            task=task,
            skill_protocol=protocol,
            context=context,
            event_queue=event_queue,
        )

    return coder_agent_fn
