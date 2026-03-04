"""Planning subgraph — wraps planning companion + task board tools.

Handles work breakdown, scoping, sprint planning, story/task creation.
Planning is TERMINAL by default — it does NOT auto-continue unless the
user says something like "build it" or "start implementing".
"""

from __future__ import annotations

import re
from typing import Any

from core.state import GrimState, Objective
from core.subgraphs.base import make_subgraph_wrapper


# Phrases that indicate the user wants to move from planning to execution
_EXECUTION_SIGNALS = [
    "build it", "implement it", "start building", "start implementing",
    "go ahead", "let's do it", "make it happen", "start coding",
    "begin implementation", "start working on",
]


def _detect_execution_intent(result: dict, state: GrimState) -> dict | None:
    """Detect if the planning output signals a transition to code execution.

    Planning is terminal by default. Only continues if:
    1. The user's message contained an explicit execution signal, OR
    2. The planning companion's response indicates readiness for execution
       AND the user previously asked to proceed.
    """
    messages = state.get("messages", [])
    if not messages:
        return None

    last_msg = messages[-1]
    msg_text = (
        last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    ).lower()

    for signal in _EXECUTION_SIGNALS:
        if signal in msg_text:
            return {"next_intent": "code", "context": "user requested execution after planning"}

    return None


def make_planning_subgraph(planning_fn: Any) -> Any:
    """Create the planning subgraph wrapper.

    Wraps the planning_companion node. Does NOT auto-continue — planning
    is terminal unless the user explicitly requests execution.
    """
    return make_subgraph_wrapper(
        name="Planning",
        node_fn=planning_fn,
        source_subgraph="planning",
        extract_continuation=_detect_execution_intent,
    )
