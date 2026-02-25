"""
Actualization Graph — LangGraph state machine for knowledge ingestion.

The graph processes one chunk at a time through:
    extract → search → judge → [actualize|extend|link|skip] → validate → crosslink → commit

Conditional edges route based on judge's decision.
Validation failures retry actualization (max 2 times).
"""

from __future__ import annotations

from typing import Any, Dict, Literal

from langgraph.graph import END, StateGraph

from .nodes.actualize import actualize, extend_existing
from .nodes.commit import commit
from .nodes.crosslink import crosslink
from .nodes.extract import extract
from .nodes.judge import judge
from .nodes.search import search
from .nodes.validate import validate
from .state import ActualizationState


# =========================================================================
# Routing functions (conditional edges)
# =========================================================================

def route_after_judge(state: ActualizationState) -> str:
    """Route based on judge's decision."""
    decision = state.get("decision", "new")
    if decision == "new":
        return "actualize"
    elif decision == "duplicate":
        return "handle_duplicate"
    elif decision == "extend":
        return "handle_extend"
    elif decision == "skip":
        return "handle_skip"
    return "actualize"  # Default to new


def route_after_validate(state: ActualizationState) -> str:
    """Route based on validation result."""
    validation = state.get("validation", {})
    retry_count = state.get("retry_count", 0)

    if validation.get("passed", False):
        return "crosslink"
    elif retry_count < 2:
        # Retry actualization with different temp
        return "actualize"
    else:
        # Give up and commit what we have (with warnings)
        return "crosslink"


# =========================================================================
# Terminal handler nodes (for skip/duplicate paths)
# =========================================================================

def handle_duplicate(state: ActualizationState) -> Dict[str, Any]:
    """Handle duplicate: link to existing, update accumulators."""
    dup_id = state.get("duplicate_of")
    meta = state.get("current_meta", {})
    path = meta.get("path", "")

    fdos_linked = list(state.get("fdos_linked", []))
    if dup_id:
        fdos_linked.append(dup_id)

        # Patch the existing FDO to note the additional source
        crosslinker = state.get("_crosslinker")
        vault_index = state.get("_vault_index")
        if crosslinker and vault_index and vault_index.has(dup_id):
            existing = vault_index.get(dup_id)
            source_id = state.get("source_id", "unknown")
            crosslinker.patch(
                existing["path"],
                f"- Also in: `{path}` ({source_id})",
            )

    return {"fdos_linked": fdos_linked}


def handle_skip(state: ActualizationState) -> Dict[str, Any]:
    """Handle skip: just record it."""
    meta = state.get("current_meta", {})
    path = meta.get("path", "")
    reason = state.get("skip_reason", "")

    fdos_skipped = list(state.get("fdos_skipped", []))
    fdos_skipped.append(f"{path}: {reason}")

    return {"fdos_skipped": fdos_skipped}


def handle_extend(state: ActualizationState) -> Dict[str, Any]:
    """Handle extend: add new info to an existing FDO."""
    return extend_existing(state)


# =========================================================================
# Build the graph
# =========================================================================

def build_actualization_graph() -> StateGraph:
    """
    Build and compile the LangGraph state machine.

    Graph topology:
        extract → search → judge ─┬─→ actualize → validate ─┬─→ crosslink → commit → END
                                   ├─→ handle_duplicate ──────→ END
                                   ├─→ handle_extend ─────────→ END
                                   └─→ handle_skip ───────────→ END
                                                      ↑ (retry) ┘
    """
    graph = StateGraph(ActualizationState)

    # Add nodes
    graph.add_node("extract", extract)
    graph.add_node("search", search)
    graph.add_node("judge", judge)
    graph.add_node("actualize", actualize)
    graph.add_node("validate", validate)
    graph.add_node("crosslink", crosslink)
    graph.add_node("commit", commit)
    graph.add_node("handle_duplicate", handle_duplicate)
    graph.add_node("handle_skip", handle_skip)
    graph.add_node("handle_extend", handle_extend)

    # Set entry point
    graph.set_entry_point("extract")

    # Linear edges
    graph.add_edge("extract", "search")
    graph.add_edge("search", "judge")

    # Conditional: judge → route
    graph.add_conditional_edges(
        "judge",
        route_after_judge,
        {
            "actualize": "actualize",
            "handle_duplicate": "handle_duplicate",
            "handle_extend": "handle_extend",
            "handle_skip": "handle_skip",
        },
    )

    # Actualize → validate
    graph.add_edge("actualize", "validate")

    # Conditional: validate → route
    graph.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "crosslink": "crosslink",
            "actualize": "actualize",  # retry
        },
    )

    # Linear: crosslink → commit → END
    graph.add_edge("crosslink", "commit")
    graph.add_edge("commit", END)

    # Terminal paths
    graph.add_edge("handle_duplicate", END)
    graph.add_edge("handle_skip", END)
    graph.add_edge("handle_extend", END)

    return graph.compile()
