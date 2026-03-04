"""Code subgraph — wraps ironclaw bridge + coder tools via dispatch.

Handles code writing, command execution, deployments, git operations.
Uses the existing dispatch mechanism to delegate to ironclaw or coder
agents. Can signal continuation to Planning if scope grows beyond
the current task.
"""

from __future__ import annotations

from typing import Any

from core.state import AgentResult, GrimState, Objective
from core.subgraphs.base import make_subgraph_wrapper


def _detect_code_continuation(result: dict, state: GrimState) -> dict | None:
    """Detect if code execution results suggest follow-up action.

    Patterns that signal continuation:
    - Agent failed → may need user input (no continuation, exit to user)
    - Agent succeeded with artifacts → may need research or planning next
    - Agent suggests scope expansion → continuation to planning
    """
    agent_result: AgentResult | None = result.get("agent_result") or state.get("agent_result")
    if agent_result is None:
        return None

    # Failed execution doesn't auto-continue — let user decide
    if not agent_result.success:
        return None

    # Successful with artifacts — check if there's more work queued
    objectives = state.get("objectives", [])
    if objectives:
        from core.state import ObjectiveStatus, get_next_objective
        next_obj = get_next_objective(objectives)
        if next_obj and next_obj.context.get("auto_continue"):
            target = next_obj.target_subgraph or "code"
            return {"next_intent": target, "context": f"Next objective: {next_obj.title}"}

    return None


def make_code_subgraph(dispatch_fn: Any) -> Any:
    """Create the code subgraph wrapper.

    Wraps the dispatch node when delegation_type is "ironclaw" or "code".
    Produces SubgraphOutput with code execution results.
    """

    async def code_dispatch(state: GrimState) -> dict:
        """Dispatch to ironclaw or coder agent."""
        return await dispatch_fn(state)

    return make_subgraph_wrapper(
        name="Code",
        node_fn=code_dispatch,
        source_subgraph="code",
        extract_continuation=_detect_code_continuation,
    )
