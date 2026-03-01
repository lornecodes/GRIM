"""IronClaw Agent — sandboxed execution via the IronClaw engine.

The IronClaw agent delegates tool execution to the IronClaw REST gateway,
which applies 13-layer zero-trust security: RBAC, command guardian, DLP,
SSRF protection, sandbox isolation, audit logging, and cost tracking.

"Engine is the limbs, not the brain" — the LLM reasoning happens here
in the agent's tool-calling loop, but all tool EXECUTION flows through
IronClaw's sandboxed environment.
"""

from __future__ import annotations

import logging

from core.agents.base import BaseAgent
from core.config import GrimConfig
from core.state import AgentResult, GrimState
from core.tools.ironclaw_tools import IRONCLAW_TOOLS
from core.tools.kronos_read import COMPANION_TOOLS

logger = logging.getLogger(__name__)


class IronClawAgent(BaseAgent):
    """Agent that executes through IronClaw's sandboxed environment."""

    agent_name = "ironclaw"

    def __init__(self, config: GrimConfig) -> None:
        # IronClaw gets: sandboxed tools + read-only Kronos for context
        tools = IRONCLAW_TOOLS + COMPANION_TOOLS
        super().__init__(config=config, tools=tools)


def make_ironclaw_agent(config: GrimConfig):
    """Create an IronClaw Agent callable for the dispatch node.

    Returns an async function that takes GrimState and returns AgentResult.
    """
    agent = IronClawAgent(config)

    async def ironclaw_agent_fn(state: GrimState, *, event_queue=None) -> AgentResult:
        """Execute a task using IronClaw's sandboxed tools."""
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
            "sandboxed-execution",
            "code-execution",
            "shell-execution",
            "file-operations",
        ]

        for skill_name in protocol_priority:
            if skill_name in skill_protocols:
                protocol = skill_protocols[skill_name]
                logger.info("IronClaw agent: using protocol '%s'", skill_name)
                break

        if protocol is None and skill_protocols:
            first_key = next(iter(skill_protocols))
            protocol = skill_protocols[first_key]

        if protocol is None:
            protocol = (
                "You are the IronClaw sandbox agent for secure code execution.\n"
                "All tool calls execute through IronClaw's sandboxed environment.\n"
                "Use your tools to run code safely with security policies enforced.\n"
                "Always execute the task — do not say you can't do something "
                "if you have a tool that can do it."
            )

        # Build context from knowledge + IronClaw availability
        context = {}
        knowledge_context = state.get("knowledge_context", [])
        if knowledge_context:
            context["relevant_knowledge"] = ", ".join(
                f"{fdo.id} ({fdo.domain})" for fdo in knowledge_context[:5]
            )

        ironclaw_available = state.get("ironclaw_available", False)
        context["ironclaw_status"] = "connected" if ironclaw_available else "disconnected"
        context["sandbox"] = "All tool calls execute through IronClaw's sandboxed environment with security policies."

        # Staging pipeline (Phase 4): direct output to shared staging volume
        job_id = state.get("staging_job_id")
        if job_id:
            context["staging_path"] = f"/workspace/staging/{job_id}/output/"
            context["staging_instructions"] = (
                "Write ALL output files to the staging path above. "
                "Do NOT write to any other location. Files written here "
                "will be reviewed by the audit agent before acceptance."
            )

        # Feedback from previous audit failure (re-dispatch cycle)
        audit_feedback = state.get("audit_feedback")
        if audit_feedback:
            task = f"{task}\n\n{audit_feedback}"

        return await agent.execute(
            task=task,
            skill_protocol=protocol,
            context=context,
            event_queue=event_queue,
        )

    return ironclaw_agent_fn
