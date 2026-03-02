"""Planning Agent — DEPRECATED.

Superseded by core.nodes.planning_companion (v0.0.6 Phase 2).
Planning is now a graph-level branch, not a dispatched agent.

This module is kept for backward compatibility but is NOT auto-discovered
by AgentRegistry (discovery attributes removed).
"""
from __future__ import annotations

import logging

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.tools.kronos_read import COMPANION_TOOLS
from core.tools.kronos_tasks import TASK_ALL_TOOLS

logger = logging.getLogger(__name__)


class PlanningAgent(BaseAgent):
    """Agent for work planning — DEPRECATED, use planning_companion node."""

    agent_name = "planning"
    protocol_priority = ["sprint-plan", "task-manage"]
    default_protocol = (
        "You are a planning agent. Analyze work requests and determine scope "
        "(Story vs Feature vs Epic). Break work into phases and steps, then "
        "populate the task board with structured work items."
    )

    def __init__(self, config: GrimConfig) -> None:
        tools = list(TASK_ALL_TOOLS) + list(COMPANION_TOOLS)
        super().__init__(config=config, tools=tools)

    def build_context(self, state: dict) -> dict:
        context = {}
        knowledge_context = state.get("knowledge_context", [])
        if knowledge_context:
            fdo_summaries = []
            for fdo in knowledge_context[:5]:
                fdo_summaries.append(f"{fdo.id}: {fdo.summary[:100]}")
            context["relevant_knowledge"] = "\n".join(fdo_summaries)
        return context


def make_planning_agent(config: GrimConfig):
    """Create a Planning Agent callable — DEPRECATED."""
    return PlanningAgent.make_callable(config)


# NOTE: Discovery attributes intentionally removed.
# Planning is now a graph-level branch (planning_companion node),
# not a dispatched agent target.
