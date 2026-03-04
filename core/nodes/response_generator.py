"""Response Generator — the heartbeat of the v0.10 loop.

Every subgraph output passes through this node. It:
  1. Formats the response for the current UX mode
  2. Updates objective statuses from subgraph output
  3. Maintains context_stack and subgraph_history
  4. Decides: loop back to router OR exit to user

The loop decision is based on:
  - Explicit continuation requests from subgraphs
  - Pending objectives with auto_continue
  - Safety valve (max_loops, blocked objectives)
"""

from __future__ import annotations

import logging
from typing import Any

from core.state import (
    GrimState,
    Objective,
    ObjectiveStatus,
    SubgraphOutput,
    UXMode,
    get_active_objectives,
    get_next_objective,
    update_objective,
)

logger = logging.getLogger(__name__)

# Default safety valve — max iterations before forced exit
DEFAULT_MAX_LOOPS = 10


# ── Loop continuation logic ─────────────────────────────────────────────

def should_auto_continue(
    *,
    objectives: list[Objective],
    loop_count: int,
    max_loops: int,
    subgraph_output: SubgraphOutput | None,
    ux_mode: str = UXMode.FULLSCREEN.value,
) -> tuple[bool, str | None, str]:
    """Decide whether the loop should continue automatically.

    Returns:
        (should_continue, continuation_intent, reason)
    """
    # Safety valve — hard stop at max_loops
    if loop_count >= max_loops:
        return False, None, f"Safety valve: reached max loops ({max_loops})"

    # Explicit continuation from subgraph output
    if subgraph_output and subgraph_output.continuation:
        next_intent = subgraph_output.continuation.get("next_intent")
        if next_intent:
            return True, next_intent, f"Explicit continuation → {next_intent}"

    # Check for pending objectives with auto_continue
    if objectives:
        next_obj = get_next_objective(objectives)
        if next_obj and next_obj.context.get("auto_continue"):
            target = next_obj.target_subgraph or "research"
            return True, target, f"Auto-continue objective: {next_obj.title}"

    # UX-mode-aware: Discord/mission_control are more autonomous
    if ux_mode in (UXMode.DISCORD.value, UXMode.MISSION_CONTROL.value):
        if objectives:
            active = get_active_objectives(objectives)
            pending_with_target = [
                o for o in active
                if o.status == ObjectiveStatus.PENDING and o.target_subgraph
            ]
            if pending_with_target:
                target = pending_with_target[0].target_subgraph
                return True, target, f"Autonomous mode: pending objective available"

    # All blocked objectives — don't loop, wait for user
    if objectives:
        active = get_active_objectives(objectives)
        if active and all(o.status == ObjectiveStatus.BLOCKED for o in active):
            return False, None, "All active objectives are blocked"

    # Default: don't continue — return to user
    return False, None, "No continuation signal"


# ── Objective update from subgraph output ────────────────────────────────

def _apply_objective_updates(
    objectives: list[Objective],
    updates: list[Objective],
) -> list[Objective]:
    """Apply objective updates from a subgraph's output.

    Updates are Objective instances with potentially changed status/artifacts.
    Merges by ID — updated versions replace existing ones.
    """
    if not updates:
        return objectives

    by_id = {o.id: o for o in objectives}
    for update in updates:
        by_id[update.id] = update

    return list(by_id.values())


# ── Response formatting ──────────────────────────────────────────────────

def _format_response(
    subgraph_output: SubgraphOutput,
    ux_mode: str,
) -> str:
    """Format subgraph response for the current UX mode.

    For now, only fullscreen mode is implemented. Other modes
    will be added in v0.0.15 (Multi-UX).
    """
    response = subgraph_output.response

    if ux_mode == UXMode.SIDEPANEL.value:
        # Concise mode — strip verbose formatting, keep key points
        # TODO: implement in v0.0.15
        return response

    if ux_mode == UXMode.DISCORD.value:
        # Brief mode — truncate for Discord message limits
        if len(response) > 1900:
            return response[:1900] + "\n\n...(truncated)"
        return response

    if ux_mode == UXMode.MISSION_CONTROL.value:
        # Status-oriented — highlight progress and blockers
        # TODO: implement in v0.0.15
        return response

    # Fullscreen (default) — rich, verbose, full markdown
    return response


# ── Main node ────────────────────────────────────────────────────────────

def make_response_generator_node():
    """Create the Response Generator node.

    This is the loop heartbeat — every subgraph output passes through here.
    """

    async def response_generator_node(state: GrimState) -> dict:
        """Process subgraph output, update state, decide loop/exit.

        Reads:
            - subgraph_output: serialized SubgraphOutput from the last subgraph
            - objectives: current objective list
            - loop_count, max_loops: loop safety valve
            - ux_mode: current UX surface
            - subgraph_history: trail of subgraphs visited

        Writes:
            - objectives: updated from subgraph output
            - should_continue: whether to loop back
            - continuation_intent: target subgraph for next loop
            - loop_count: incremented
            - subgraph_history: appended
            - context_stack: appended
        """
        loop_count = state.get("loop_count", 0)
        max_loops = state.get("max_loops", DEFAULT_MAX_LOOPS)
        ux_mode = state.get("ux_mode", UXMode.FULLSCREEN.value)
        objectives = list(state.get("objectives", []))
        subgraph_history = list(state.get("subgraph_history", []))
        context_stack = list(state.get("context_stack", []))

        # Parse subgraph output
        raw_output = state.get("subgraph_output")
        subgraph_output = None
        if raw_output:
            if isinstance(raw_output, dict):
                subgraph_output = SubgraphOutput(**raw_output)
            elif isinstance(raw_output, SubgraphOutput):
                subgraph_output = raw_output

        # Apply objective updates from subgraph
        if subgraph_output and subgraph_output.objective_updates:
            objectives = _apply_objective_updates(
                objectives, subgraph_output.objective_updates,
            )

        # Update history and context stack
        source = subgraph_output.source_subgraph if subgraph_output else "unknown"
        subgraph_history.append(source)
        if subgraph_output:
            context_stack.append({
                "loop": loop_count,
                "source": source,
                "response_length": len(subgraph_output.response),
                "artifacts": subgraph_output.artifacts,
                "has_continuation": subgraph_output.continuation is not None,
            })

        # Format response for UX mode
        formatted_response = ""
        if subgraph_output:
            formatted_response = _format_response(subgraph_output, ux_mode)

        # Decide: loop or exit
        should_continue, continuation_intent, reason = should_auto_continue(
            objectives=objectives,
            loop_count=loop_count + 1,  # +1 because we're completing this iteration
            max_loops=max_loops,
            subgraph_output=subgraph_output,
            ux_mode=ux_mode,
        )

        logger.info(
            "Response Generator [loop=%d]: source=%s, continue=%s, "
            "intent=%s, reason=%s, pending=%d, response=%d chars",
            loop_count,
            source,
            should_continue,
            continuation_intent,
            reason,
            len([o for o in objectives if o.status == ObjectiveStatus.PENDING]),
            len(formatted_response),
        )

        result: dict[str, Any] = {
            "objectives": objectives,
            "should_continue": should_continue,
            "continuation_intent": continuation_intent,
            "loop_count": loop_count + 1,
            "subgraph_history": subgraph_history,
            "context_stack": context_stack,
            # Clear subgraph_output for next iteration
            "subgraph_output": None,
        }

        return result

    return response_generator_node


def response_generator_decision(state: GrimState) -> str:
    """LangGraph conditional edge: loop back to router or exit.

    Returns:
        "continue" — loop back to companion_router for next iteration
        "exit" — proceed to integrate → evolve → END
    """
    if state.get("should_continue", False):
        return "continue"
    return "exit"
