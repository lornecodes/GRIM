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

# Fallback keywords for delegation when no skill consumer matches.
# Uses substring matching (keyword in message.lower()), so keep terms
# short and atomic — "echo" matches "do an echo command in powershell".
DELEGATION_KEYWORDS = {
    "memory": [
        "capture this", "remember this", "save this",
        "promote", "organize vault", "triage inbox",
        "connect these", "relate these", "link these",
        "review vault", "vault health",
        "store this", "add to vault", "update the vault",
        "create an fdo", "new knowledge entry",
    ],
    "code": [
        "write code", "implement", "create file",
        "fix this code", "refactor", "add a test",
        "write a function", "write a class", "edit the code",
        "modify the file", "update the code", "debug this",
        "write a script", "code this", "build this",
    ],
    "research": [
        "analyze this", "ingest", "summarize this paper",
        "deep dive", "review this document",
        "research this", "look into this", "investigate",
        "what does the literature say", "find papers on",
        "summarize this", "break this down",
    ],
    "operate": [
        # Shell / commands
        "run command", "run this", "execute this",
        "shell", "powershell", "bash", "terminal",
        "echo ", "mkdir", "ls ", "dir ", "pwd",
        "curl ", "wget ",
        # Git
        "git status", "git log", "git diff", "git pull",
        "git push", "commit", "push to github",
        # Files
        "list files", "show me the directory", "what files",
        "read the file", "show me the file", "cat ",
        # HTTP
        "http request", "fetch ", "call the api",
        "check the weather", "hit the endpoint",
        "make a request",
        # Ops
        "upload to zenodo", "sync vault", "deploy",
        "check the status", "test execution",
    ],
    "ironclaw": [
        "run sandboxed", "execute safely", "isolated shell",
        "sandboxed execution", "run in sandbox",
        "secure execute", "run securely",
        "run this safely", "execute in sandbox",
        "run isolated", "safe execution",
    ],
    "audit": [
        "review staging", "audit output", "check staged",
        "staging review", "review the output",
        "audit the files", "review execution output",
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

    # IronClaw skills → sandboxed execution agent
    if name in ("sandboxed-execution", "secure-shell", "ironclaw-execute"):
        return "ironclaw"

    # Audit/staging skills → audit or operator agent
    if name in ("ironclaw-review",):
        return "audit"
    if name in ("staging-organize", "staging-cleanup"):
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
