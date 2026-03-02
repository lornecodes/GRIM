"""Coder Agent — code writing, editing, and file operations."""
from __future__ import annotations

import logging

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.workspace import FILE_TOOLS, SHELL_TOOLS

logger = logging.getLogger(__name__)


class CoderAgent(BaseAgent):
    """Agent for code and file operations."""

    agent_name = "coder"
    protocol_priority = ["code-execution", "file-operations"]
    default_protocol = (
        "You are a coding agent with file read/write, shell execution, and git access.\n"
        "Use your tools to write code, run tests, fix bugs, and refactor.\n"
        "Use run_shell for builds, tests, and linting. Use file tools for reading and editing.\n"
        "Always execute the task — do not say you can't do something "
        "if you have a tool that can do it."
    )

    def __init__(self, config: GrimConfig) -> None:
        tools = FILE_TOOLS + SHELL_TOOLS + COMPANION_TOOLS
        super().__init__(config=config, tools=tools)


def make_coder_agent(config: GrimConfig):
    """Create a Coder Agent callable for the dispatch node."""
    return CoderAgent.make_callable(config)


# Discovery attributes for AgentRegistry
__agent_name__ = "code"
__make_agent__ = make_coder_agent
