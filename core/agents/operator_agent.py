"""Operator Agent — infrastructure awareness and read-only git operations."""
from __future__ import annotations

import logging

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.workspace import GIT_READ_TOOLS

logger = logging.getLogger(__name__)


class OperatorAgent(BaseAgent):
    """Agent for infrastructure awareness and read-only git operations."""

    agent_name = "operator"
    protocol_priority = ["git-operations"]
    default_protocol = (
        "You are an infrastructure awareness agent with read-only git access.\n"
        "Use git tools to check repo status, review diffs, and browse commit logs.\n"
        "For shell execution, file writes, or git commits — those go through IronClaw.\n"
        "Always execute the task — do not say you can't do something "
        "if you have a tool that can do it."
    )

    def __init__(self, config: GrimConfig) -> None:
        tools = list(GIT_READ_TOOLS) + list(COMPANION_TOOLS)
        super().__init__(config=config, tools=tools)


def make_operator_agent(config: GrimConfig):
    """Create an Operator Agent callable for the dispatch node."""
    return OperatorAgent.make_callable(config)


# Discovery attributes for AgentRegistry
__agent_name__ = "operate"
__make_agent__ = make_operator_agent
