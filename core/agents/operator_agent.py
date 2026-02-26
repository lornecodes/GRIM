"""Operator Agent — git, shell, and infrastructure operations.

The Operator handles:
- Git operations (status, commit, push)
- Shell command execution
- Vault sync operations
- Zenodo uploads and DOI management
- Infrastructure tasks

It follows skill protocols from git-operations, shell-execution, vault-sync.
"""

from __future__ import annotations

import logging

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.state import AgentResult, GrimState
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.workspace import GIT_TOOLS, SHELL_TOOLS, FILE_TOOLS

logger = logging.getLogger(__name__)


class OperatorAgent(BaseAgent):
    """Agent for operations, git, shell, and infrastructure."""

    agent_name = "operator"

    def __init__(self, config: GrimConfig) -> None:
        # Operator gets: git + shell + file reading + Kronos read (for context)
        tools = GIT_TOOLS + SHELL_TOOLS + FILE_TOOLS + list(COMPANION_TOOLS)
        super().__init__(config=config, tools=tools)


def make_operator_agent(config: GrimConfig):
    """Create an Operator Agent callable for the dispatch node.

    Returns an async function that takes GrimState and returns AgentResult.
    """
    agent = OperatorAgent(config)

    async def operator_agent_fn(state: GrimState) -> AgentResult:
        """Execute an operations task following skill protocols."""
        messages = state.get("messages", [])
        skill_protocols = state.get("skill_protocols", {})

        # Extract the user's request
        task = ""
        if messages:
            last_msg = messages[-1]
            task = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        # Find the most relevant skill protocol
        protocol = None
        protocol_priority = [
            "git-operations",
            "shell-execution",
            "vault-sync",
        ]

        for skill_name in protocol_priority:
            if skill_name in skill_protocols:
                protocol = skill_protocols[skill_name]
                logger.info("Operator agent: using protocol '%s'", skill_name)
                break

        if protocol is None and skill_protocols:
            first_key = next(iter(skill_protocols))
            protocol = skill_protocols[first_key]

        # Context
        context = {}
        knowledge_context = state.get("knowledge_context", [])
        if knowledge_context:
            context["relevant_knowledge"] = ", ".join(
                f"{fdo.id}" for fdo in knowledge_context[:5]
            )

        return await agent.execute(
            task=task,
            skill_protocol=protocol,
            context=context,
        )

    return operator_agent_fn
