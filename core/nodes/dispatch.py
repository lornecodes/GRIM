"""Dispatch node — route to the appropriate doer agent.

This is the DOER COORDINATOR. It receives the routing decision from
the Router and delegates to the appropriate specialist agent, injecting
the matched skill protocol as the agent's system prompt instructions.
"""

from __future__ import annotations

import logging

from core.state import AgentResult, GrimState

logger = logging.getLogger(__name__)


def make_dispatch_node(agents: dict):
    """Create a dispatch node closure with available agents.

    Args:
        agents: Dict mapping delegation_type → agent callable.
                e.g. {"memory": memory_agent_fn, "code": coder_agent_fn}
    """

    async def dispatch_node(state: GrimState) -> dict:
        """Dispatch to the appropriate agent based on delegation_type."""
        delegation_type = state.get("delegation_type")
        if not delegation_type:
            logger.warning("Dispatch: no delegation_type set, falling back to companion")
            return {"agent_result": None}

        agent_fn = agents.get(delegation_type)
        if agent_fn is None:
            logger.warning(
                "Dispatch: no agent for delegation_type '%s' — available: %s",
                delegation_type,
                list(agents.keys()),
            )
            return {
                "agent_result": AgentResult(
                    agent=delegation_type,
                    success=False,
                    summary=f"No agent available for '{delegation_type}' — Phase 2 feature.",
                )
            }

        logger.info("Dispatch: delegating to '%s' agent", delegation_type)

        try:
            result = await agent_fn(state)
            return {"agent_result": result}
        except Exception as exc:
            logger.exception("Dispatch: agent '%s' failed", delegation_type)
            return {
                "agent_result": AgentResult(
                    agent=delegation_type,
                    success=False,
                    summary=f"Agent '{delegation_type}' failed: {exc}",
                )
            }

    return dispatch_node
