"""Intent classifier — structured output routing via LLM.

Replaces keyword-based routing (graph_router + router) with a single
LLM call that produces a typed RoutingDecision. Uses Haiku for low
latency (~200ms) with fallback to keyword matching on failure.

Three routing tiers:
  1. Hard overrides — skill_delegation_hint bypasses LLM entirely
  2. LLM classification — Haiku structured output (primary path)
  3. Keyword fallback — existing patterns as safety net
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from core.model_router import TIER_MODELS
from core.nodes.keyword_router import (
    DELEGATION_KEYWORDS,
    match_action_intent,
    match_keywords,
)
from core.state import (
    GrimState,
    Objective,
    ObjectiveStatus,
    RoutingDecision,
    SkillContext,
)

logger = logging.getLogger(__name__)

# ── Target mapping ──────────────────────────────────────────────────────

# Maps RoutingDecision.target_subgraph → concrete delegation targets
# used by the existing dispatch system.
TARGET_TO_DELEGATION: dict[str, str | None] = {
    "conversation": None,       # companion mode, no delegation
    "research": "research",     # research agent or companion
    "code": "ironclaw",         # ironclaw agent for code execution
    "operations": "memory",     # memory agent for vault/task ops
    "planning": None,           # planning companion, no delegation
}

# Maps skill_delegation_hint → RoutingDecision.target_subgraph
HINT_TO_TARGET: dict[str, str] = {
    "memory": "operations",
    "research": "research",
    "ironclaw": "code",
    "code": "code",
    "operate": "operations",
    "audit": "code",           # audit is part of code pipeline
    "codebase": "research",    # codebase exploration = research
    "planning": "planning",
}

# Maps keyword match delegation types → RoutingDecision.target_subgraph
DELEGATION_TO_TARGET: dict[str, str] = {
    "memory": "operations",
    "research": "research",
    "ironclaw": "code",
    "operate": "operations",
    "audit": "code",
    "codebase": "research",
}

# ── Subgraph descriptions for the LLM ──────────────────────────────────

SUBGRAPH_DESCRIPTIONS = """Available subgraphs:
- conversation: Casual chat, greetings, emotional support, personal check-ins, general Q&A that doesn't need tools. Use when the user is just talking.
- research: Knowledge retrieval, vault lookups, FDO analysis, deep dives into concepts, ingestion. Use when the user wants information from the knowledge graph or needs analysis.
- code: Writing code, running commands, file operations, deployments, git operations, refactoring, implementing features. Use when the user wants something executed or built.
- operations: Vault management (capture, sync, promote), task/story management, calendar, memory operations. Use when the user wants to manage their knowledge base or task board.
- planning: Sprint planning, task breakdown, scoping work, prioritization, backlog grooming, creating stories. Use when the user wants to plan or organize work."""


# ── Classification prompt ───────────────────────────────────────────────

CLASSIFY_SYSTEM = """You are GRIM's intent router. Classify the user's message into exactly one target subgraph.

{subgraph_descriptions}

Context:
{context_block}

Rules:
- Pick the SINGLE most appropriate subgraph
- If the message is ambiguous, prefer "conversation" for chat-like messages and "research" for work-like messages
- If skills were matched, their hints strongly suggest the right subgraph
- If objectives are active, consider whether the message continues them
- Confidence: 1.0 = obvious match, 0.5 = reasonable guess, <0.5 = uncertain
- Keep reasoning to ONE short sentence"""


def _build_context_block(
    *,
    matched_skills: list[SkillContext],
    active_objectives: list[Objective],
    recent_messages: list[BaseMessage],
    is_continuation: bool = False,
) -> str:
    """Build the context block injected into the classification prompt."""
    parts: list[str] = []

    if matched_skills:
        skill_names = ", ".join(s.name for s in matched_skills[:5])
        parts.append(f"Matched skills: {skill_names}")

    if active_objectives:
        obj_titles = ", ".join(
            f'"{o.title}" (→{o.target_subgraph or "unassigned"})'
            for o in active_objectives[:3]
        )
        parts.append(f"Active objectives: {obj_titles}")

    if is_continuation:
        parts.append("This is a continuation from a previous loop iteration.")

    if len(recent_messages) > 1:
        parts.append(f"Conversation depth: {len(recent_messages)} messages")

    return "\n".join(parts) if parts else "No additional context."


# ── Main classification function ────────────────────────────────────────

async def classify_intent(
    state: GrimState,
    *,
    model_name: str | None = None,
    timeout: float = 3.0,
) -> RoutingDecision:
    """Classify user intent into a RoutingDecision.

    Three-tier approach:
      1. Hard override — skill_delegation_hint maps directly (no LLM call)
      2. LLM structured output — Haiku with_structured_output(RoutingDecision)
      3. Keyword fallback — existing pattern matching as safety net

    Args:
        state: Current graph state.
        model_name: Override the classifier model (default: haiku).
        timeout: LLM call timeout in seconds.

    Returns:
        Typed RoutingDecision with target, confidence, and reasoning.
    """
    messages = state.get("messages", [])
    if not messages:
        return RoutingDecision(
            target_subgraph="conversation",
            confidence=0.5,
            reasoning="No messages in state.",
        )

    last_msg = messages[-1]
    message_text = (
        last_msg.content if hasattr(last_msg, "content") else str(last_msg)
    )

    # ── Tier 1: Hard overrides (skill hint) ─────────────────────────────
    hint = state.get("skill_delegation_hint")
    if hint:
        target = HINT_TO_TARGET.get(hint, "research")
        logger.info("Intent classifier: hard override from skill hint '%s' → %s", hint, target)
        return RoutingDecision(
            target_subgraph=target,
            confidence=1.0,
            reasoning=f"Skill delegation hint: {hint}",
        )

    # ── Tier 2: LLM structured output ──────────────────────────────────
    matched_skills = state.get("matched_skills", [])
    objectives = state.get("objectives", [])
    active_objectives = [
        o for o in objectives
        if o.status in (ObjectiveStatus.ACTIVE, ObjectiveStatus.PENDING)
    ] if objectives else []

    is_continuation = bool(state.get("continuation_intent"))

    try:
        decision = await _llm_classify(
            message_text=message_text,
            recent_messages=messages[-5:],  # last 5 for context
            matched_skills=matched_skills,
            active_objectives=active_objectives,
            is_continuation=is_continuation,
            model_name=model_name or TIER_MODELS["haiku"],
            timeout=timeout,
        )
        if decision is not None:
            # Enrich with continuation info
            if is_continuation:
                decision = decision.model_copy(update={
                    "is_continuation": True,
                    "continuation_context": state.get("continuation_intent", {}),
                })
            logger.info(
                "Intent classifier: LLM → %s (confidence=%.2f, reason=%s)",
                decision.target_subgraph, decision.confidence, decision.reasoning,
            )
            return decision
    except Exception:
        logger.warning("Intent classifier: LLM call failed, falling back to keywords", exc_info=True)

    # ── Tier 3: Keyword fallback ────────────────────────────────────────
    return _keyword_fallback(message_text, is_continuation)


async def _llm_classify(
    *,
    message_text: str,
    recent_messages: list[BaseMessage],
    matched_skills: list[SkillContext],
    active_objectives: list[Objective],
    is_continuation: bool,
    model_name: str,
    timeout: float,
) -> RoutingDecision | None:
    """Call Haiku with structured output to produce a RoutingDecision."""
    context_block = _build_context_block(
        matched_skills=matched_skills,
        active_objectives=active_objectives,
        recent_messages=recent_messages,
        is_continuation=is_continuation,
    )

    system_prompt = CLASSIFY_SYSTEM.format(
        subgraph_descriptions=SUBGRAPH_DESCRIPTIONS,
        context_block=context_block,
    )

    llm = ChatAnthropic(
        model=model_name,
        temperature=0.0,
        max_tokens=256,
        timeout=timeout,
    )
    structured_llm = llm.with_structured_output(RoutingDecision)

    result = await structured_llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=message_text),
    ])

    if isinstance(result, RoutingDecision):
        return result

    logger.warning("Intent classifier: unexpected LLM output type: %s", type(result))
    return None


def _keyword_fallback(message_text: str, is_continuation: bool = False) -> RoutingDecision:
    """Fallback to keyword matching when LLM classification fails.

    Maps existing keyword_router results to RoutingDecision format.
    """
    message = message_text.lower()

    # Keyword match → delegation type → target
    kw_match = match_keywords(message)
    if kw_match:
        target = DELEGATION_TO_TARGET.get(kw_match, "research")
        logger.info("Intent classifier: keyword fallback → %s (from '%s')", target, kw_match)
        return RoutingDecision(
            target_subgraph=target,
            confidence=0.7,
            reasoning=f"Keyword match: {kw_match}",
            is_continuation=is_continuation,
        )

    # Action-intent match → code
    if match_action_intent(message):
        logger.info("Intent classifier: keyword fallback → code (action-intent)")
        return RoutingDecision(
            target_subgraph="code",
            confidence=0.7,
            reasoning="Action-intent pattern match",
            is_continuation=is_continuation,
        )

    # Personal signals (imported from graph_router for completeness)
    from core.nodes.graph_router import PERSONAL_SIGNALS, _RESEARCH_OVERRIDES
    if any(sig in message for sig in PERSONAL_SIGNALS):
        if not any(ovr in message for ovr in _RESEARCH_OVERRIDES):
            logger.info("Intent classifier: keyword fallback → conversation")
            return RoutingDecision(
                target_subgraph="conversation",
                confidence=0.6,
                reasoning="Personal signal match",
                is_continuation=is_continuation,
            )

    # Planning signals
    from core.nodes.graph_router import PLANNING_SIGNALS
    if any(sig in message for sig in PLANNING_SIGNALS):
        logger.info("Intent classifier: keyword fallback → planning")
        return RoutingDecision(
            target_subgraph="planning",
            confidence=0.7,
            reasoning="Planning signal match",
            is_continuation=is_continuation,
        )

    # Default → conversation for short messages, research for longer
    if len(message_text.split()) < 5:
        logger.info("Intent classifier: keyword fallback → conversation (short message)")
        return RoutingDecision(
            target_subgraph="conversation",
            confidence=0.4,
            reasoning="Short message, defaulting to conversation",
            is_continuation=is_continuation,
        )

    logger.info("Intent classifier: keyword fallback → research (default)")
    return RoutingDecision(
        target_subgraph="research",
        confidence=0.4,
        reasoning="No strong signal, defaulting to research",
        is_continuation=is_continuation,
    )


# ── Resolution helpers ──────────────────────────────────────────────────

def resolve_delegation_target(decision: RoutingDecision) -> str | None:
    """Map a RoutingDecision to a concrete delegation target for dispatch.

    Returns None for subgraphs that don't delegate (conversation, planning).
    """
    return TARGET_TO_DELEGATION.get(decision.target_subgraph)


def resolve_graph_target(decision: RoutingDecision) -> str:
    """Map a RoutingDecision to a graph-level target for the current topology.

    Maps to the existing graph_router outputs: research | personal | planning.
    This bridges the new classification system with the v0.0.6 graph topology
    until subgraphs are fully wired (story-grim-006-013).
    """
    mapping = {
        "conversation": "personal",
        "research": "research",
        "code": "research",        # code goes through research → dispatch
        "operations": "research",  # operations goes through research → dispatch
        "planning": "planning",
    }
    return mapping.get(decision.target_subgraph, "research")


def resolve_mode(decision: RoutingDecision) -> str:
    """Map a RoutingDecision to the research-graph mode: companion | delegate."""
    if decision.target_subgraph in ("conversation", "planning"):
        return "companion"
    if decision.target_subgraph == "research":
        # Research can be companion (light) or delegate depending on confidence
        # High confidence + specific intent → delegate; otherwise companion
        if decision.confidence >= 0.7:
            return "delegate"
        return "companion"
    # code, operations → always delegate
    return "delegate"
