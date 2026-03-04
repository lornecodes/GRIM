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
    agent_display_name = "IronClaw"
    agent_role = "execution"
    agent_description = "Execution layer — code writes, shell, testing, deployments, sandboxed ops"
    agent_color = "#ef4444"
    agent_tier = "ironclaw"
    agent_toggleable = True
    max_tool_steps = 20  # IronClaw needs more room: research + dispatch + file writes

    protocol_priority = [
        "sandboxed-execution",
        "code-execution",
        "shell-execution",
        "file-operations",
    ]
    default_protocol = (
        "You are the IronClaw sandbox agent for secure code execution.\n"
        "All tool calls execute through IronClaw's sandboxed environment.\n\n"
        "You have two modes of operation:\n"
        "1. **Direct tools**: claw_read_file, claw_write_file, claw_shell, "
        "claw_list_dir, claw_http_request — execute sandboxed operations directly.\n"
        "2. **Agent dispatch**: claw_list_agents to see available engine agents "
        "(Coder, Researcher, Planner, Tester, Security Auditor, Reviewer), then "
        "claw_dispatch_workflow to orchestrate multi-agent tasks with coordination "
        "patterns (sequential, parallel, debate, hierarchical, pipeline).\n"
        "3. **Security scanning**: claw_scan_skill to scan code for vulnerabilities.\n\n"
        "Always execute the task — do not say you can't do something "
        "if you have a tool that can do it."
    )

    def __init__(self, config: GrimConfig) -> None:
        tools = IRONCLAW_TOOLS + COMPANION_TOOLS
        super().__init__(config=config, tools=tools)


def make_ironclaw_agent(config: GrimConfig):
    """Create an IronClaw Agent callable for the dispatch node.

    Custom factory — IronClaw has staging pipeline logic and audit feedback
    handling that goes beyond the standard make_callable pattern.
    """
    agent = IronClawAgent(config)

    async def ironclaw_agent_fn(state: GrimState, *, event_queue=None) -> AgentResult:
        """Execute a task using IronClaw's sandboxed tools."""
        task = BaseAgent._extract_task(state)
        protocol = BaseAgent._find_protocol(
            state, agent.protocol_priority, agent.default_protocol
        )

        # Build context from knowledge + IronClaw availability
        context = agent.build_context(state)

        ironclaw_available = state.get("ironclaw_available", False)
        context["ironclaw_status"] = "connected" if ironclaw_available else "disconnected"
        context["sandbox"] = "All tool calls execute through IronClaw's sandboxed environment with security policies."

        # Emit IronClaw dispatch start
        agent._emit(event_queue, {
            "cat": "claw",
            "node": "ironclaw",
            "action": "start",
            "text": f"IronClaw dispatch — engine {'connected' if ironclaw_available else 'DISCONNECTED'}",
            "sandboxed": True,
        })

        # Staging pipeline (Phase 4): direct output to shared staging volume
        job_id = state.get("staging_job_id")
        if job_id:
            staging_path = f"/workspace/staging/{job_id}/output/"
            context["staging_path"] = staging_path
            context["staging_instructions"] = (
                "MANDATORY: Write ALL output files to the staging path above. "
                "Do NOT write to any other location — all paths are automatically "
                "redirected to staging regardless. Use RELATIVE paths like "
                "'myproject/main.py' (not absolute paths like '/workspace/myproject/main.py'). "
                "Files written here will be reviewed by the audit agent before acceptance."
            )
            # Enforce at tool layer: relative paths auto-prefixed with staging dir
            from core.tools.context import tool_context
            tool_context.staging_path = staging_path

        # Feedback from previous audit failure (re-dispatch cycle)
        audit_feedback = state.get("audit_feedback")
        if audit_feedback:
            task = f"{task}\n\n{audit_feedback}"

        try:
            result = await agent.execute(
                task=task,
                skill_protocol=protocol,
                context=context,
                event_queue=event_queue,
            )
        finally:
            # Clear staging path so it doesn't leak to subsequent calls
            from core.tools.context import tool_context
            tool_context.staging_path = None

        # Emit IronClaw dispatch end
        agent._emit(event_queue, {
            "cat": "claw",
            "node": "ironclaw",
            "action": "end",
            "text": f"IronClaw {'completed' if result.success else 'failed'}",
            "sandboxed": True,
        })

        return result

    return ironclaw_agent_fn


# Discovery attributes for AgentRegistry
__agent_name__ = "ironclaw"
__make_agent__ = make_ironclaw_agent
__agent_class__ = IronClawAgent
