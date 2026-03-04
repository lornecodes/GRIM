"""Research subgraph — wraps research + codebase agents via dispatch.

Handles knowledge retrieval, vault lookups, FDO analysis, deep dives.
Uses the existing dispatch mechanism to delegate to research or codebase
agents. Can signal continuation to Planning or Code when research
reveals actionable work.
"""

from __future__ import annotations

from typing import Any

from core.state import GrimState, Objective
from core.subgraphs.base import make_subgraph_wrapper


def _detect_research_continuation(result: dict, state: GrimState) -> dict | None:
    """Detect if research results suggest follow-up action.

    If the research uncovered actionable items (new objectives, code changes
    needed, planning required), signal continuation to the appropriate subgraph.
    """
    # For now, research doesn't auto-continue — user decides next action.
    # This can be enhanced to detect patterns like "this needs implementation"
    # or "suggest creating a story for this".
    return None


def make_research_subgraph(dispatch_fn: Any) -> Any:
    """Create the research subgraph wrapper.

    Wraps the dispatch node when delegation_type is "research" or "codebase".
    Produces SubgraphOutput with research results.
    """

    async def research_dispatch(state: GrimState) -> dict:
        """Dispatch to research or codebase agent."""
        return await dispatch_fn(state)

    return make_subgraph_wrapper(
        name="Research",
        node_fn=research_dispatch,
        source_subgraph="research",
        extract_continuation=_detect_research_continuation,
    )
