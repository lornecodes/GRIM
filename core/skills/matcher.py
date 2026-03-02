"""Skill matcher — match user messages against skill triggers per turn."""

from __future__ import annotations

import logging
import re

from core.skills.registry import Skill, SkillRegistry

logger = logging.getLogger(__name__)


def match_skills(
    message: str,
    registry: SkillRegistry,
    disabled: list[str] | None = None,
) -> list[Skill]:
    """Match a user message against all registered skill triggers.

    Returns list of matched skills, sorted by relevance (most triggers hit first).
    Skills in the ``disabled`` list are excluded from matching.

    Matching strategy:
    1. Keyword match — case-insensitive substring check
    2. Intent match — exact or fuzzy intent matching (future: LLM-based)
    """
    if not message or not registry:
        return []

    disabled_set = set(disabled) if disabled else set()
    message_lower = message.lower()
    scored: list[tuple[int, Skill]] = []

    for skill in registry.all():
        if skill.name in disabled_set:
            continue
        score = _score_skill(message_lower, skill)
        if score > 0:
            scored.append((score, skill))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    matched = [skill for _, skill in scored]
    if matched:
        logger.info(
            "Matched %d skill(s): %s",
            len(matched),
            ", ".join(s.name for s in matched),
        )

    return matched


def _score_skill(message_lower: str, skill: Skill) -> int:
    """Score how well a message matches a skill's triggers.

    Returns 0 for no match, higher = better match.
    """
    score = 0
    triggers = skill.triggers or {}

    # Ensure triggers is a dict (handle edge cases)
    if not isinstance(triggers, dict):
        return 0

    # Keyword matching
    keywords = triggers.get("keywords", [])
    if not isinstance(keywords, list):
        keywords = []

    for kw in keywords:
        if not kw or not isinstance(kw, str):
            continue
        kw_lower = kw.lower()
        # Multi-word phrases use substring match (strong signal)
        if " " in kw_lower:
            if kw_lower in message_lower:
                score += 3  # exact phrase match is strong
        elif len(kw_lower) <= 4:
            # Short words use word boundary
            pattern = rf"\b{re.escape(kw_lower)}\b"
            if re.search(pattern, message_lower):
                score += 2
        else:
            # Single words: use word boundary and only count if message
            # starts with it (imperative) or is preceded by "let's", "please", etc.
            pattern = rf"\b{re.escape(kw_lower)}\b"
            if re.search(pattern, message_lower):
                # Check if it's in a command context (not buried in a question)
                if _is_command_context(message_lower, kw_lower):
                    score += 2
                else:
                    score += 1  # weak match — word present but not imperative

    # Intent matching — only match explicitly declared intents
    intents = triggers.get("intents", [])
    if not isinstance(intents, list):
        intents = []

    for intent in intents:
        if not intent or not isinstance(intent, str):
            continue
        # Convert intent slug to words: "store-knowledge" → "store knowledge"
        intent_words = intent.replace("-", " ").replace("_", " ").lower()
        # Check if any intent word appears in message (word boundary)
        for word in intent_words.split():
            if len(word) > 3:
                # Use word-boundary match to avoid false positives
                # e.g., "relate" in "how does it relate" shouldn't trigger
                # "relate-concepts" intent unless it's clearly imperative
                pattern = rf"\b{re.escape(word)}\b"
                if re.search(pattern, message_lower):
                    score += 1

    # Require minimum score of 2 to count as a match
    # (single intent word hit = 1, not enough)
    return score if score >= 2 else 0


def _is_command_context(message: str, keyword: str) -> bool:
    """Check if a keyword appears in an imperative/command context.

    Returns True if the keyword is likely a user instruction (e.g.,
    "relate these FDOs" or "please link A to B") rather than appearing
    incidentally (e.g., "how does it relate to the golden ratio?").
    """
    # Questions are rarely commands
    if message.strip().startswith(("what ", "how ", "why ", "when ", "where ", "which ", "who ", "is ", "does ", "do ", "can ")):
        return False

    # If keyword appears at start of message, it's imperative
    if message.strip().startswith(keyword):
        return True

    # If preceded by command prefixes
    command_prefixes = ("please ", "let's ", "can you ", "go ", "now ", "run ")
    for prefix in command_prefixes:
        if prefix + keyword in message:
            return True

    return False
