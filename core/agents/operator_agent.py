"""Operator Agent — git, shell, and infrastructure operations."""
from __future__ import annotations

import logging

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.workspace import GIT_TOOLS, SHELL_TOOLS, FILE_TOOLS

logger = logging.getLogger(__name__)


class OperatorAgent(BaseAgent):
    """Agent for operations, git, shell, and infrastructure."""

    agent_name = "operator"
    protocol_priority = [
        "git-operations",
        "shell-execution",
        "vault-sync",
    ]
    default_protocol = (
        "You are an operations agent with full terminal/bash access.\n"
        "Use run_shell to execute any command the user needs: "
        "ping, curl, python, git, docker, system utilities, etc.\n"
        "Use git tools for git operations. Use file tools for reading/writing files.\n"
        "Always execute the task — do not say you can't do something "
        "if you have a tool that can do it."
    )

    def __init__(self, config: GrimConfig) -> None:
        tools = GIT_TOOLS + SHELL_TOOLS + FILE_TOOLS + list(COMPANION_TOOLS)
        super().__init__(config=config, tools=tools)


def make_operator_agent(config: GrimConfig):
    """Create an Operator Agent callable for the dispatch node."""
    return OperatorAgent.make_callable(config)


# Discovery attributes for AgentRegistry
__agent_name__ = "operate"
__make_agent__ = make_operator_agent
