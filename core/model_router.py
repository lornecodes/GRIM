"""Model router — multi-tier model selection for GRIM.

Routes requests to the optimal model tier based on complexity:
  - haiku:  greetings, factual Q&A, tool dispatch, summarization
  - sonnet: code gen, analysis, multi-step tasks (default)
  - opus:   architecture, deep research, complex reasoning

Self-contained module — portable to other agents.

Routing stages:
  1. Explicit overrides (/fast, /deep, /sonnet)
  2. Feature scoring (zero latency heuristics)
  3. LLM classifier (optional, disabled by default)
  4. Fallback to default tier
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ── Model Tier Definitions ────────────────────────────────────────────────

TIER_MODELS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}

# ── Routing Decision ──────────────────────────────────────────────────────


@dataclass
class RoutingDecision:
    """Result of model routing — which tier to use and why."""

    tier: str          # "haiku" | "sonnet" | "opus"
    model: str         # full model ID
    reason: str        # human-readable reason
    confidence: float  # 0-1, how confident the decision is
    stage: int         # which routing stage decided (1-4)


# ── Stage 1: Explicit Overrides ───────────────────────────────────────────

_OVERRIDE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^/(fast|haiku)\b", re.IGNORECASE), "haiku"),
    (re.compile(r"^/(deep|opus)\b", re.IGNORECASE), "opus"),
    (re.compile(r"^/sonnet\b", re.IGNORECASE), "sonnet"),
]


def _check_explicit_override(message: str) -> RoutingDecision | None:
    """Stage 1: Check for explicit /command overrides."""
    stripped = message.strip()
    for pattern, tier in _OVERRIDE_PATTERNS:
        if pattern.search(stripped):
            return RoutingDecision(
                tier=tier,
                model=TIER_MODELS[tier],
                reason=f"explicit override: {pattern.pattern}",
                confidence=1.0,
                stage=1,
            )
    return None


# ── Stage 2: Feature Scoring ─────────────────────────────────────────────

# Intent keywords (lowercase)
_HAIKU_KEYWORDS = [
    "hello", "hi ", "hey ", "thanks", "thank you", "good morning",
    "good night", "what is", "who is", "define ",
    "summarize", "tldr", "status", "what time",
]

_SONNET_KEYWORDS = [
    "implement", "write code", "refactor", "create file", "add test",
    "fix bug", "debug", "code review", "write a function",
    "```", "def ", "class ", "import ", "function ",
]

_OPUS_KEYWORDS = [
    "architecture", "design system", "deep analysis", "compare approaches",
    "trade-off", "tradeoff", "complex reasoning", "philosophical",
    "emergent", "recursive", "first principles", "derive ",
    "proof", "theorem", "research question", "long-term strategy",
]


@dataclass
class _FeatureScores:
    """Accumulated scores per tier from feature analysis."""
    haiku: int = 0
    sonnet: int = 0
    opus: int = 0

    def top_tier(self) -> tuple[str, int]:
        scores = {"haiku": self.haiku, "sonnet": self.sonnet, "opus": self.opus}
        return max(scores.items(), key=lambda x: x[1])

    def runner_up_score(self) -> int:
        scores = sorted([self.haiku, self.sonnet, self.opus], reverse=True)
        return scores[1] if len(scores) > 1 else 0

    def confidence(self) -> float:
        top_tier, top_score = self.top_tier()
        runner_up = self.runner_up_score()
        if top_score == 0:
            return 0.0
        return (top_score - runner_up) / (top_score + 1)


def _score_features(
    message: str,
    *,
    has_objectives: bool = False,
    has_compressed_context: bool = False,
    matched_write_skill: bool = False,
    fdo_count: int = 0,
) -> RoutingDecision | None:
    """Stage 2: Score message features to select tier.

    Returns a RoutingDecision if confidence exceeds threshold, else None.
    """
    scores = _FeatureScores()
    msg_lower = message.lower()

    # ── Keyword intent scoring (check first, length is secondary) ──
    has_sonnet_kw = False
    has_opus_kw = False

    for kw in _HAIKU_KEYWORDS:
        if kw in msg_lower:
            scores.haiku += 4
            break  # one match is enough

    for kw in _SONNET_KEYWORDS:
        if kw in msg_lower:
            scores.sonnet += 4
            has_sonnet_kw = True
            break

    for kw in _OPUS_KEYWORDS:
        if kw in msg_lower:
            scores.opus += 4
            has_opus_kw = True
            break

    # ── Message length signals (only boost haiku if no competing keywords) ──
    if len(message) < 80 and "```" not in message and not has_sonnet_kw and not has_opus_kw:
        scores.haiku += 3

    if len(message) > 500:
        scores.sonnet += 1

    if len(message) > 1500:
        scores.opus += 1

    # ── Code block detection ──
    if "```" in message or message.count("\n") > 10:
        scores.sonnet += 2

    # ── GRIM-specific signals ──
    if has_objectives:
        scores.sonnet += 1

    if has_compressed_context:
        scores.sonnet += 1

    if matched_write_skill:
        scores.sonnet += 2

    if fdo_count > 5:
        scores.sonnet += 1

    # ── Evaluate confidence ──
    confidence = scores.confidence()
    if confidence < 0.6:
        return None  # not confident enough, fall through to next stage

    top_tier, _ = scores.top_tier()
    return RoutingDecision(
        tier=top_tier,
        model=TIER_MODELS[top_tier],
        reason=f"feature scoring (h={scores.haiku}, s={scores.sonnet}, o={scores.opus})",
        confidence=round(confidence, 3),
        stage=2,
    )


# ── Stage 3: LLM Classifier (optional) ───────────────────────────────────

_CLASSIFIER_PROMPT = """Classify this user message into one of three complexity tiers.

Tiers:
- haiku: Simple greetings, factual Q&A, status checks, summarization requests
- sonnet: Code generation, analysis, multi-step tasks, debugging
- opus: Architecture design, deep research, complex reasoning, philosophical inquiry

User message:
{message}

Respond with ONLY the tier name: haiku, sonnet, or opus"""


async def _classify_with_llm(message: str) -> RoutingDecision | None:
    """Stage 3: Use Haiku to classify ambiguous messages.

    Disabled by default — enable via config.routing_classifier_enabled.
    """
    try:
        import asyncio

        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage

        llm = ChatAnthropic(
            model=TIER_MODELS["haiku"],
            temperature=0.0,
            max_tokens=10,
        )

        prompt = _CLASSIFIER_PROMPT.format(message=message[:500])
        response = await asyncio.wait_for(
            llm.ainvoke([HumanMessage(content=prompt)]),
            timeout=1.5,
        )

        tier = response.content.strip().lower()
        if tier in TIER_MODELS:
            return RoutingDecision(
                tier=tier,
                model=TIER_MODELS[tier],
                reason="LLM classifier",
                confidence=0.7,
                stage=3,
            )
    except Exception:
        logger.debug("Model router: LLM classifier failed, falling through")

    return None


# ── Main Router ───────────────────────────────────────────────────────────


async def route_model(
    message: str,
    *,
    enabled: bool = True,
    default_tier: str = "sonnet",
    classifier_enabled: bool = False,
    confidence_threshold: float = 0.6,
    # GRIM-specific signals
    has_objectives: bool = False,
    has_compressed_context: bool = False,
    matched_write_skill: bool = False,
    fdo_count: int = 0,
    disabled_tiers: list[str] | None = None,
) -> RoutingDecision:
    """Route a message to the optimal model tier.

    4-stage pipeline:
      1. Explicit overrides (/fast, /deep, /sonnet)
      2. Feature scoring (heuristic, zero latency)
      3. LLM classifier (optional, Haiku, 1.5s timeout)
      4. Fallback to default tier

    Args:
        message: The user's message text.
        enabled: Whether routing is enabled (False → always default).
        default_tier: Fallback tier when routing can't decide.
        classifier_enabled: Whether to use the LLM classifier (stage 3).
        confidence_threshold: Minimum confidence for feature scoring.
        has_objectives: Whether active objectives exist in state.
        has_compressed_context: Whether context has been compressed.
        matched_write_skill: Whether a write-permission skill was matched.
        fdo_count: Number of FDOs in knowledge context.
        disabled_tiers: Tier names to block (e.g. ["opus"]). Falls back to default.

    Returns:
        RoutingDecision with selected tier, model, reason, and confidence.
    """
    _disabled = set(disabled_tiers or [])

    def _apply_disabled(d: RoutingDecision) -> RoutingDecision:
        """If the chosen tier is disabled, fall back to the default tier."""
        if d.tier in _disabled:
            fallback = default_tier if default_tier not in _disabled else "sonnet"
            logger.info(
                "Model router: tier '%s' disabled, falling back to '%s'",
                d.tier, fallback,
            )
            return RoutingDecision(
                tier=fallback,
                model=TIER_MODELS.get(fallback, TIER_MODELS["sonnet"]),
                reason=f"{d.reason} (tier '{d.tier}' disabled)",
                confidence=d.confidence,
                stage=d.stage,
            )
        return d

    if not enabled:
        return _apply_disabled(RoutingDecision(
            tier=default_tier,
            model=TIER_MODELS.get(default_tier, TIER_MODELS["sonnet"]),
            reason="routing disabled",
            confidence=1.0,
            stage=4,
        ))

    # Stage 1: Explicit overrides
    decision = _check_explicit_override(message)
    if decision:
        decision = _apply_disabled(decision)
        logger.info("Model router: %s (stage 1 — explicit override)", decision.tier)
        return decision

    # Stage 2: Feature scoring
    decision = _score_features(
        message,
        has_objectives=has_objectives,
        has_compressed_context=has_compressed_context,
        matched_write_skill=matched_write_skill,
        fdo_count=fdo_count,
    )
    if decision and decision.confidence >= confidence_threshold:
        decision = _apply_disabled(decision)
        logger.info("Model router: %s (stage 2 — %s)", decision.tier, decision.reason)
        return decision

    # Stage 3: LLM classifier (optional)
    if classifier_enabled:
        decision = await _classify_with_llm(message)
        if decision:
            decision = _apply_disabled(decision)
            logger.info("Model router: %s (stage 3 — LLM classifier)", decision.tier)
            return decision

    # Stage 4: Fallback
    fallback_tier = default_tier if default_tier not in _disabled else "sonnet"
    logger.info("Model router: %s (stage 4 — default fallback)", fallback_tier)
    return RoutingDecision(
        tier=fallback_tier,
        model=TIER_MODELS.get(fallback_tier, TIER_MODELS["sonnet"]),
        reason="default fallback",
        confidence=0.5,
        stage=4,
    )
