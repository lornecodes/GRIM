"""Companion Router — single-node routing for the v0.10 graph.

Replaces the graph_router + router two-stage routing with a single
LLM-backed intent classifier. Sits after preprocessing (skill_match),
routes to the appropriate graph branch.

Responsibilities:
  1. Call classify_intent() for structured routing decision
  2. Map the RoutingDecision to graph targets (v0.0.6 compat bridge)
  3. Handle continuation routing from the Response Generator loop
  4. Select the model tier via the existing model_router
  5. Store all decisions in state for tracing

The node produces both graph-level routing (personal/research/planning)
and research-level routing (companion/delegate), collapsing what was
previously two separate nodes.
"""

from __future__ import annotations

import logging
from typing import Any

from core.config import GrimConfig
from core.model_router import route_model
from core.nodes.intent_classifier import (
    classify_intent,
    resolve_delegation_target,
    resolve_graph_target,
    resolve_mode,
)
from core.state import GrimState

logger = logging.getLogger(__name__)


def make_companion_router_node(config: GrimConfig):
    """Create a companion router node closure with config."""

    async def companion_router_node(state: GrimState) -> dict:
        """Classify intent, route model tier, produce full routing state.

        Replaces both graph_router_node and router_node:
          - graph_target: personal | research | planning
          - mode: companion | delegate (within research)
          - delegation_type: concrete agent target for dispatch
          - routing_decision: full RoutingDecision for tracing
          - selected_model: model ID from model_router

        Also handles continuation routing — when the Response Generator
        loops back, the continuation_intent is used to inform the next
        routing decision.
        """
        messages = state.get("messages", [])
        if not messages:
            return {
                "graph_target": "research",
                "mode": "companion",
                "delegation_type": None,
                "routing_decision": None,
                "selected_model": None,
            }

        # ── Intent classification ───────────────────────────────────────
        decision = await classify_intent(state, timeout=config.routing_timeout)

        # ── Resolve to graph topology ───────────────────────────────────
        graph_target = resolve_graph_target(decision)
        mode = resolve_mode(decision)
        delegation_type = resolve_delegation_target(decision)

        # Companion mode doesn't delegate — clear delegation_type
        if mode == "companion":
            delegation_type = None

        # ── Continuity override ─────────────────────────────────────────
        # If no strong signal from classifier, check for follow-up continuity
        if (
            decision.confidence < 0.6
            and not state.get("skill_delegation_hint")
        ):
            last_delegation = state.get("last_delegation_type")
            if last_delegation:
                from core.nodes.keyword_router import is_follow_up
                last_msg = messages[-1]
                msg_text = (
                    last_msg.content if hasattr(last_msg, "content")
                    else str(last_msg)
                ).lower()
                if is_follow_up(msg_text):
                    delegation_type = last_delegation
                    mode = "delegate"
                    graph_target = "research"
                    logger.info(
                        "Companion router: continuity override → %s",
                        delegation_type,
                    )

        # ── Model routing ───────────────────────────────────────────────
        last_msg = messages[-1]
        raw_message = (
            last_msg.content if hasattr(last_msg, "content")
            else str(last_msg)
        )
        matched_skills = state.get("matched_skills", [])
        knowledge_context = state.get("knowledge_context", [])

        has_write_skill = any(
            any("write" in p for p in sc.permissions)
            for sc in matched_skills
        )

        model_decision = await route_model(
            raw_message if isinstance(raw_message, str) else str(raw_message),
            enabled=config.routing_enabled,
            default_tier=config.routing_default_tier,
            classifier_enabled=config.routing_classifier_enabled,
            confidence_threshold=config.routing_confidence_threshold,
            has_objectives=bool(state.get("objectives")),
            has_compressed_context=bool(state.get("context_summary")),
            matched_write_skill=has_write_skill,
            fdo_count=len(knowledge_context),
            disabled_tiers=config.models_disabled,
        )

        # ── Build result ────────────────────────────────────────────────
        result = {
            "graph_target": graph_target,
            "mode": mode,
            "delegation_type": delegation_type,
            "routing_decision": decision.model_dump(),
            "selected_model": model_decision.model,
        }

        logger.info(
            "Companion router: %s → %s/%s (delegation=%s, model=%s, "
            "confidence=%.2f, reason=%s)",
            decision.target_subgraph,
            graph_target,
            mode,
            delegation_type,
            model_decision.model,
            decision.confidence,
            decision.reasoning,
        )

        return result

    return companion_router_node


def companion_route_decision(state: GrimState) -> str:
    """LangGraph conditional edge function — routes to graph branch.

    Replaces both graph_route_decision and route_decision.
    Returns the next node name based on the companion router's output.

    Mapping:
      graph_target="personal"  → "personal_companion"
      graph_target="planning"  → "planning_companion"
      graph_target="research" + mode="companion"  → "companion"
      graph_target="research" + mode="delegate"   → "dispatch"
    """
    graph_target = state.get("graph_target", "research")

    if graph_target == "personal":
        return "personal_companion"
    if graph_target == "planning":
        return "planning_companion"

    # Research branch — companion or dispatch
    mode = state.get("mode", "companion")
    if mode == "delegate":
        return "dispatch"
    return "companion"
