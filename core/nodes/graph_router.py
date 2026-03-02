"""Graph-level router — classify intent into specialized graph pipelines.

Sits after preprocessing (skill_match), before graph-specific nodes.
Routes user messages to the appropriate graph:
  - "research" → existing research assistant pipeline (companion + dispatch)
  - "planning" → planning companion (scoping, task breakdown, board population)
  - "personal" → personal companion (conversational, no delegation)

Default is always "research" to preserve v0.0.5 behavior exactly.
"""
from __future__ import annotations

import logging

from core.nodes.keyword_router import DELEGATION_KEYWORDS, match_action_intent
from core.state import GrimState

logger = logging.getLogger(__name__)

# Conservative personal signals — only activate on clear conversational intent.
# Anything ambiguous defaults to research (zero regression risk).
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

# Planning signals — scoping, task breakdown, sprint planning.
# These route to the planning graph, not the research graph.
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
# If the message contains both personal and research signals, research wins.
_RESEARCH_OVERRIDES: list[str] = [
    "experiment", "vault", "fdo", "kronos", "task", "story",
    "code", "implement", "refactor", "deploy", "test",
    "dawn field", "pac ", "sec ", "rbf ", "med ",
    "analyze", "ingest", "research",
]


def _has_delegation_keywords(message: str) -> bool:
    """Check if message matches any existing delegation keyword."""
    for keywords in DELEGATION_KEYWORDS.values():
        for keyword in keywords:
            if keyword in message:
                return True
    return False


async def graph_router_node(state: GrimState) -> dict:
    """Classify user message into a graph target.

    Priority:
    1. skill_delegation_hint set → research (needs agent delegation) unless planning-related
    2. Planning signals match → planning
    3. Delegation keywords match → research (existing routing)
    4. Action-intent patterns → research
    5. Personal signals match (without research/planning overrides) → personal
    6. Default → research
    """
    messages = state.get("messages", [])
    if not messages:
        return {"graph_target": "research"}

    last_msg = messages[-1]
    message = (last_msg.content if hasattr(last_msg, "content") else str(last_msg)).lower()

    # 1. Skill hint — check if it's planning-related first
    hint = state.get("skill_delegation_hint")
    if hint:
        if hint == "planning":
            logger.info("Graph router: planning (skill hint)")
            return {"graph_target": "planning"}
        logger.info("Graph router: research (skill hint)")
        return {"graph_target": "research"}

    # 2. Planning signals → planning graph
    if any(sig in message for sig in PLANNING_SIGNALS):
        logger.info("Graph router: planning")
        return {"graph_target": "planning"}

    # 3. Delegation keywords → research
    if _has_delegation_keywords(message):
        logger.info("Graph router: research (delegation keywords)")
        return {"graph_target": "research"}

    # 4. Action-intent patterns → research
    if match_action_intent(message):
        logger.info("Graph router: research (action-intent)")
        return {"graph_target": "research"}

    # 5. Personal signals (with research override check)
    if any(sig in message for sig in PERSONAL_SIGNALS):
        if any(ovr in message for ovr in _RESEARCH_OVERRIDES):
            logger.info("Graph router: research (personal signal overridden by research content)")
            return {"graph_target": "research"}
        logger.info("Graph router: personal")
        return {"graph_target": "personal"}

    # 6. Default → research
    logger.info("Graph router: research (default)")
    return {"graph_target": "research"}


def graph_route_decision(state: GrimState) -> str:
    """LangGraph conditional edge function — returns next node name."""
    return state.get("graph_target", "research")
