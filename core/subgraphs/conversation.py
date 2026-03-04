"""Conversation subgraph — wraps companion + personal companion.

Routes between companion (work-adjacent chat) and personal companion
(personality-forward, no work context) based on the routing decision.
No delegation — this subgraph only talks.
"""

from __future__ import annotations

from typing import Any

from core.config import GrimConfig
from core.state import GrimState
from core.subgraphs.base import make_subgraph_wrapper


def make_conversation_subgraph(
    companion_fn: Any,
    personal_fn: Any,
) -> Any:
    """Create the conversation subgraph wrapper.

    Uses the routing_decision to pick between companion (work chat)
    and personal_companion (casual chat). Both produce SubgraphOutput.
    """

    async def conversation_node(state: GrimState) -> dict:
        """Route to companion or personal based on routing context."""
        # Check if we were routed via personal or companion target
        graph_target = state.get("graph_target", "research")

        if graph_target == "personal":
            return await personal_fn(state)
        return await companion_fn(state)

    return make_subgraph_wrapper(
        name="Conversation",
        node_fn=conversation_node,
        source_subgraph="conversation",
    )
