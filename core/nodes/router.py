"""Router node — decide whether to think (companion) or delegate (agent).

Examines the user message, knowledge context, and matched skills
to determine the appropriate path through the graph.

Also runs the model router to select the optimal model tier.
"""
from __future__ import annotations

import logging
from typing import Literal

from core.config import GrimConfig
from core.model_router import route_model
from core.nodes.keyword_router import (
    DELEGATION_KEYWORDS,
    is_follow_up,
    match_action_intent,
    match_keywords,
)
from core.state import GrimState

logger = logging.getLogger(__name__)

# Backward-compat re-export — tests import _skill_ctx_to_delegation from here.
# The canonical implementation is now Skill.delegation_target() in core/skills/registry.py.
# This shim can be removed once tests are updated.


def _skill_ctx_to_delegation(skill_ctx) -> str | None:
    """Map a SkillContext to a delegation type (deprecated shim).

    Prefer using Skill.delegation_target() via skill_delegation_hint in state.
    """
    name = skill_ctx.name

    # v0.0.6 agent boundaries:
    # GRIM = management (memory, planning, research read-only, operate read-only)
    # IronClaw = execution (code, shell, deploy, file writes, git writes)
    if name.startswith("kronos-") or name.startswith("memory-"):
        return "memory"
    if name in ("vault-sync",):
        return "memory"
    if name in ("sprint-plan", "task-manage"):
        # v0.0.6 Phase 2: planning is graph-level, task skills execute via memory agent
        return "memory"
    if name in ("deep-ingest",):
        return "research"
    if name in ("git-operations",):
        return "operate"
    if name in ("code-execution", "file-operations", "shell-execution"):
        return "ironclaw"
    if name in ("docker-release", "cliproxyapi", "ship-it"):
        return "ironclaw"
    if name in ("staging-organize", "staging-cleanup"):
        return "ironclaw"
    if name in ("sandboxed-execution", "secure-shell", "ironclaw-execute"):
        return "ironclaw"
    if name in ("ironclaw-review",):
        return "audit"

    perms = skill_ctx.permissions
    if any("write" in p for p in perms):
        if any("vault" in p for p in perms):
            return "memory"
        if any("filesystem" in p or "shell" in p for p in perms):
            return "ironclaw"

    return None


def make_router_node(config: GrimConfig):
    """Create a router node closure with config for model routing."""

    async def router_node(state: GrimState) -> dict:
        """Decide: companion mode (think) or delegation mode (do).

        Priority:
        1. skill_delegation_hint (set by skill_match from consumer declarations)
        2. Continuity (re-delegate to same agent for follow-ups)
        3. Keyword fallback
        4. Action-intent fallback
        5. Default: companion mode

        Also selects the optimal model tier via the model router.
        """
        matched_skills = state.get("matched_skills", [])
        messages = state.get("messages", [])

        if not messages:
            return {"mode": "companion", "delegation_type": None, "selected_model": None}

        last_msg = messages[-1]
        raw_message = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
        message = raw_message.lower()

        # ── Mode routing (companion vs delegate) ──
        delegation_type = None

        # 1. Skill delegation hint (from skill_match node)
        hint = state.get("skill_delegation_hint")
        if hint:
            delegation_type = hint
            logger.info("Router: delegating to %s (skill hint)", delegation_type)

        # 2. Continuity — re-delegate to same agent for follow-ups
        if not delegation_type:
            last_delegation = state.get("last_delegation_type")
            if last_delegation and is_follow_up(message):
                delegation_type = last_delegation
                logger.info("Router: continuity re-delegation to %s", delegation_type)

        # 3. Keyword fallback
        if not delegation_type:
            kw_match = match_keywords(message)
            if kw_match:
                delegation_type = kw_match
                logger.info("Router: delegating to %s (keyword)", delegation_type)

        # 4. Action-intent fallback
        if not delegation_type:
            action = match_action_intent(message)
            if action:
                delegation_type = action
                logger.info("Router: delegating to %s (action-intent)", delegation_type)

        # Build result
        if delegation_type:
            result = {"mode": "delegate", "delegation_type": delegation_type}
        else:
            logger.info("Router: companion mode")
            result = {"mode": "companion", "delegation_type": None}

        # ── Model routing (haiku / sonnet / opus) ──
        has_write_skill = any(
            any("write" in p for p in sc.permissions)
            for sc in matched_skills
        )
        knowledge_context = state.get("knowledge_context", [])

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

        result["selected_model"] = model_decision.model
        logger.info(
            "Router: model=%s tier=%s (stage %d, confidence %.2f — %s)",
            model_decision.model,
            model_decision.tier,
            model_decision.stage,
            model_decision.confidence,
            model_decision.reason,
        )

        return result

    return router_node


def route_decision(state: GrimState) -> str:
    """LangGraph conditional edge function — returns next node name."""
    mode = state.get("mode", "companion")
    if mode == "delegate":
        return "dispatch"
    return "companion"
