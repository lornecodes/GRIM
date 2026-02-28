"""Router node — decide whether to think (companion) or delegate (agent).

The router examines the user message, knowledge context, and matched skills
to determine the appropriate path through the graph.

Routing uses consumer declarations from skill manifests. If a matched skill
has an execution consumer, it tells us which agent should handle it.

Also runs the model router to select the optimal model tier (haiku/sonnet/opus)
for the current turn.
"""

from __future__ import annotations

import logging
from typing import Literal

from core.config import GrimConfig
from core.model_router import route_model
from core.state import GrimState

logger = logging.getLogger(__name__)

# Fallback keywords for delegation when no skill consumer matches
DELEGATION_KEYWORDS = {
    "memory": [
        "capture this", "remember this", "save this",
        "promote", "organize vault", "triage inbox",
        "connect these", "relate these", "link these",
        "review vault", "vault health",
    ],
    "code": [
        "write code", "implement", "create file",
        "fix this code", "refactor", "add a test",
    ],
    "research": [
        "analyze this", "ingest", "summarize this paper",
        "deep dive", "review this document",
    ],
    "operate": [
        "upload to zenodo", "sync vault", "commit",
        "git status", "push to github", "run command",
    ],
}


def make_router_node(config: GrimConfig):
    """Create a router node closure with config for model routing."""

    async def router_node(state: GrimState) -> dict:
        """Decide: companion mode (think) or delegation mode (do).

        Also selects the optimal model tier via the model router.

        Priority:
        1. Check matched skills for consumer-declared delegation targets
        2. Fallback to keyword matching
        3. Default: companion mode
        """
        matched_skills = state.get("matched_skills", [])
        messages = state.get("messages", [])

        if not messages:
            return {"mode": "companion", "delegation_type": None, "selected_model": None}

        last_msg = messages[-1]
        raw_message = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
        message = raw_message.lower()

        # ── Mode routing (companion vs delegate) ──
        result: dict = {}

        # 1. Check matched skills for delegation targets (consumer-aware)
        delegation_found = False
        for skill_ctx in matched_skills:
            delegation = _skill_ctx_to_delegation(skill_ctx)
            if delegation:
                logger.info(
                    "Router: delegating to %s (skill %s matched)",
                    delegation,
                    skill_ctx.name,
                )
                result = {"mode": "delegate", "delegation_type": delegation}
                delegation_found = True
                break

        if not delegation_found:
            # 2. Keyword fallback
            for delegation_type, keywords in DELEGATION_KEYWORDS.items():
                for keyword in keywords:
                    if keyword in message:
                        logger.info(
                            "Router: delegating to %s (keyword '%s')",
                            delegation_type,
                            keyword,
                        )
                        result = {"mode": "delegate", "delegation_type": delegation_type}
                        delegation_found = True
                        break
                if delegation_found:
                    break

        if not delegation_found:
            # 3. Default: companion mode
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


def _skill_ctx_to_delegation(skill_ctx) -> str | None:
    """Map a SkillContext to a delegation type.

    Uses skill name patterns and permission hints.
    """
    name = skill_ctx.name

    # Kronos vault skills → memory agent
    if name.startswith("kronos-"):
        return "memory"

    # Code/file skills → coder agent
    if name in ("code-execution", "file-operations"):
        return "code"

    # Research skills → research agent
    if name in ("deep-ingest",):
        return "research"

    # Operations skills → operator agent
    if name in ("vault-sync", "git-operations", "shell-execution"):
        return "operate"

    # Check permissions for hints
    perms = skill_ctx.permissions
    if any("write" in p for p in perms):
        if any("vault" in p for p in perms):
            return "memory"
        if any("filesystem" in p for p in perms):
            return "code"
        if any("shell" in p for p in perms):
            return "operate"

    return None


def route_decision(state: GrimState) -> str:
    """LangGraph conditional edge function — returns next node name."""
    mode = state.get("mode", "companion")
    if mode == "delegate":
        return "dispatch"
    return "companion"
