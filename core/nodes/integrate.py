"""Integrate node — absorb agent results back into conversation state.

After a doer agent completes its work, this node formats the results
for the user and adds them to the conversation as an AI message.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage

from core.state import GrimState

logger = logging.getLogger(__name__)


async def integrate_node(state: GrimState) -> dict:
    """Integrate agent results into the conversation."""
    agent_result = state.get("agent_result")

    if agent_result is None:
        return {}

    # Format result as a conversation message
    if agent_result.success:
        msg = f"**{agent_result.agent.title()} Agent**: {agent_result.summary}"
        if agent_result.artifacts:
            msg += "\n\nArtifacts: " + ", ".join(agent_result.artifacts)
    else:
        msg = f"**{agent_result.agent.title()} Agent** (failed): {agent_result.summary}"

    logger.info(
        "Integrate: %s agent %s — %s",
        agent_result.agent,
        "succeeded" if agent_result.success else "failed",
        agent_result.summary[:100],
    )

    return {
        "messages": [AIMessage(content=msg)],
        "agent_result": None,  # clear for next turn
        "last_delegation_type": agent_result.agent,  # persist for continuity
    }
