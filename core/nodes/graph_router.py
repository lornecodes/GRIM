"""Graph-level router — classify intent into specialized graph pipelines.

Sits after preprocessing (skill_match), before graph-specific nodes.
Routes user messages to the appropriate graph:
  - "research" → existing research assistant pipeline (companion + dispatch)
  - "planning" → planning companion (scoping, task breakdown, board population)
  - "personal" → personal companion (conversational, no delegation)

v0.0.10: Uses LLM intent classifier (Haiku, ~200ms) with keyword fallback.
Previously used pure keyword/signal matching which was too rigid.
"""
from __future__ import annotations

import logging

from core.nodes.intent_classifier import classify_intent, resolve_graph_target
from core.state import GrimState

logger = logging.getLogger(__name__)

# Conservative personal signals — kept for Tier 3 keyword fallback
# (used by intent_classifier._keyword_fallback via import).
PERSONAL_SIGNALS: list[str] = [
    # Greetings / casual
    "how are you", "how's it going", "what's up",
    "how's your day", "good morning", "good night",
    "good evening", "hey grim", "hi grim", "hello grim",
    # Emotional / venting
    "i'm feeling", "i feel ", "i'm stressed", "i'm tired",
    "i'm frustrated", "i'm excited", "i'm worried",
    "i'm anxious", "i'm overwhelmed", "i'm burned out",
    "just venting", "need to vent", "let me vent",
    "thanks for listening", "i need to talk",
    # Conversational
    "just chatting", "let's chat", "casual",
    "off topic", "not work related",
    "tell me about yourself", "who are you",
    "what do you think about life",
    # Reflection
    "how's everything going", "just checking in",
    "wanted to say", "i appreciate you",
]

# Planning signals — kept for Tier 3 keyword fallback.
PLANNING_SIGNALS: list[str] = [
    # Explicit planning
    "let's plan", "plan this", "plan the implementation",
    "break this down", "break this into",
    "scope this", "scope the work",
    "what should we build", "what should i build",
    # Sprint / board management
    "plan sprint", "plan the sprint", "sprint plan",
    "groom backlog", "groom the backlog",
    "organize the backlog", "load up the board",
    # Task creation intent
    "create stories for", "create tasks for",
    "add stories for", "add tasks for",
    # Prioritization
    "prioritize the work", "prioritize this",
    "what should i work on",
    # Draft management
    "promote draft", "approve draft", "show drafts",
    "review drafts", "promote drafts",
]

# Keywords that indicate research/task intent even if personal signals match.
# Used by Tier 3 keyword fallback in intent_classifier.
_RESEARCH_OVERRIDES: list[str] = [
    "experiment", "vault", "fdo", "kronos", "task", "story",
    "code", "implement", "refactor", "deploy", "test",
    "dawn field", "pac ", "sec ", "rbf ", "med ",
    "analyze", "ingest", "research",
]


def _has_delegation_keywords(message: str) -> bool:
    """Check if message matches any existing delegation keyword."""
    from core.nodes.keyword_router import DELEGATION_KEYWORDS
    for keywords in DELEGATION_KEYWORDS.values():
        for keyword in keywords:
            if keyword in message:
                return True
    return False


async def graph_router_node(state: GrimState) -> dict:
    """Classify user message into a graph target using the LLM intent classifier.

    Three-tier routing:
    1. Skill delegation hints → immediate classification (no LLM)
    2. LLM structured output via Haiku (~200ms) → typed RoutingDecision
    3. Keyword/signal fallback → existing pattern matching

    Stores the full RoutingDecision in state so the downstream router
    node can read it without making a second LLM call.
    """
    decision = await classify_intent(state)
    graph_target = resolve_graph_target(decision)

    logger.info(
        "Graph router: %s (classifier: %s, confidence=%.2f — %s)",
        graph_target,
        decision.target_subgraph,
        decision.confidence,
        decision.reasoning,
    )

    return {
        "graph_target": graph_target,
        "routing_decision": decision.model_dump(),
    }


def graph_route_decision(state: GrimState) -> str:
    """LangGraph conditional edge function — returns next node name."""
    return state.get("graph_target", "research")
