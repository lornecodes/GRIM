"""Router node — decide whether to think (companion) or delegate (agent).

The router examines the user message, knowledge context, and matched skills
to determine the appropriate path through the graph.

Routing uses consumer declarations from skill manifests. If a matched skill
has an execution consumer, it tells us which agent should handle it.
"""

from __future__ import annotations

import logging
from typing import Literal

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


async def router_node(state: GrimState) -> dict:
    """Decide: companion mode (think) or delegation mode (do).

    Priority:
    1. Check matched skills for consumer-declared delegation targets
    2. Fallback to keyword matching
    3. Default: companion mode
    """
    matched_skills = state.get("matched_skills", [])
    messages = state.get("messages", [])

    if not messages:
        return {"mode": "companion", "delegation_type": None}

    last_msg = messages[-1]
    message = (last_msg.content if hasattr(last_msg, "content") else str(last_msg)).lower()

    # 1. Check matched skills for delegation targets (consumer-aware)
    #    SkillContext doesn't carry the full Skill object, but we stored
    #    the skill_protocols dict. If a skill has write permissions or
    #    is a known action skill, delegate.
    for skill_ctx in matched_skills:
        delegation = _skill_ctx_to_delegation(skill_ctx)
        if delegation:
            logger.info(
                "Router: delegating to %s (skill %s matched)",
                delegation,
                skill_ctx.name,
            )
            return {"mode": "delegate", "delegation_type": delegation}

    # 2. Keyword fallback
    for delegation_type, keywords in DELEGATION_KEYWORDS.items():
        for keyword in keywords:
            if keyword in message:
                logger.info(
                    "Router: delegating to %s (keyword '%s')",
                    delegation_type,
                    keyword,
                )
                return {"mode": "delegate", "delegation_type": delegation_type}

    # 3. Default: companion mode
    logger.info("Router: companion mode")
    return {"mode": "companion", "delegation_type": None}


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
