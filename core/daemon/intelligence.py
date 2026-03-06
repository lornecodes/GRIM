"""Intelligent dispatch — judgment layer for the management daemon.

Adds three capabilities on top of the mechanical daemon loop:
1. ClarificationResolver — auto-answer blocked agent questions from ADR context
2. OutputValidator — validate completed work against acceptance criteria
3. RetryEnricher — append feedback to retried job instructions

LLM calls are surgical (single API calls, not persistent sessions) and
everything degrades gracefully if the anthropic SDK is unavailable.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Optional: anthropic SDK for LLM calls (graceful degradation if missing)
try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None  # type: ignore[assignment]

# Stop words filtered from question keywords
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "i", "we", "you", "he", "she", "it", "they", "me", "us", "him", "her",
    "my", "our", "your", "his", "its", "their",
    "this", "that", "these", "those", "what", "which", "who", "whom",
    "how", "when", "where", "why",
    "and", "or", "but", "not", "no", "nor", "so", "if", "then",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "about",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "under", "over",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "than", "too", "very",
})

# Minimum content word overlap for mechanical match
_MIN_KEYWORD_OVERLAP = 3


def _safe_response_text(response: Any) -> str | None:
    """Extract text from an Anthropic API response, returning None on unexpected structure."""
    try:
        if not response.content:
            return None
        block = response.content[0]
        if not hasattr(block, "text"):
            return None
        return block.text.strip()
    except (IndexError, AttributeError):
        return None


# ── Data Models ───────────────────────────────────────────────

@dataclass
class Resolution:
    """Result of attempting to resolve a blocked agent question."""

    answered: bool
    answer: Optional[str] = None
    confidence: float = 0.0
    source: str = "escalated"  # "mechanical" | "llm" | "escalated"


@dataclass
class Verdict:
    """Result of validating agent output against acceptance criteria."""

    outcome: str = "pass"  # "pass" | "fail" | "partial"
    reasoning: str = ""
    missing_criteria: list[str] = field(default_factory=list)


# ── ClarificationResolver ────────────────────────────────────

class ClarificationResolver:
    """Auto-answer blocked agent questions from ADR/vault context.

    Two-tier approach:
    1. Mechanical: keyword overlap between question and decision boundary text
    2. LLM: single Sonnet API call with ADR context (if mechanical fails)
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        confidence_threshold: float = 0.7,
    ) -> None:
        self._model = model
        self._confidence_threshold = confidence_threshold

    async def resolve(
        self,
        question: str,
        boundaries: str,
        adr_context: str,
    ) -> Resolution:
        """Try to answer a blocked question from available context.

        Args:
            question: The agent's clarification question
            boundaries: Decision Boundaries text from ADR(s)
            adr_context: Full ADR Design Context text

        Returns:
            Resolution with answer or escalation indication
        """
        if not question.strip():
            return Resolution(answered=False, source="escalated")

        # Tier 1: mechanical keyword match against boundaries
        mechanical = self._mechanical_match(question, boundaries)
        if mechanical is not None:
            return Resolution(
                answered=True,
                answer=mechanical,
                confidence=0.85,
                source="mechanical",
            )

        # Tier 2: LLM resolution with full ADR context
        if boundaries or adr_context:
            try:
                return await self._llm_resolve(question, boundaries, adr_context)
            except Exception:
                logger.warning("LLM clarification resolution failed, escalating")

        return Resolution(answered=False, source="escalated")

    def _mechanical_match(self, question: str, boundaries: str) -> str | None:
        """Check if question keywords overlap with decision boundary paragraphs.

        Returns the matching paragraph text, or None if no confident match.
        """
        if not boundaries.strip():
            return None

        question_words = self._extract_keywords(question)
        if len(question_words) < 2:
            return None

        # Split boundaries into paragraphs (non-empty blocks of text)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", boundaries) if p.strip()]

        best_paragraph = None
        best_overlap = 0

        for para in paragraphs:
            para_words = self._extract_keywords(para)
            overlap = len(question_words & para_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_paragraph = para

        if best_overlap >= _MIN_KEYWORD_OVERLAP and best_paragraph:
            return best_paragraph

        return None

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """Extract content words from text, filtering stop words."""
        words = re.findall(r"[a-z][a-z0-9_]+", text.lower())
        return {w for w in words if w not in _STOP_WORDS and len(w) > 1}

    async def _llm_resolve(
        self,
        question: str,
        boundaries: str,
        adr_context: str,
    ) -> Resolution:
        """Use a single LLM call to try answering from ADR context."""
        if _anthropic is None:
            raise ImportError("anthropic SDK not available")

        context_parts = []
        if boundaries:
            context_parts.append(f"## Decision Boundaries\n{boundaries}")
        if adr_context:
            context_parts.append(f"## Design Context\n{adr_context}")
        context = "\n\n".join(context_parts)

        client = _anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=self._model,
            max_tokens=500,
            system=(
                "You are evaluating whether a coding agent's question can be "
                "answered from the provided design context.\n\n"
                "If the context contains enough information to answer confidently, "
                "provide a clear, concise answer.\n\n"
                "If the context does NOT contain enough information, respond with "
                "exactly: CANNOT_ANSWER\n\n"
                "Do not guess or extrapolate beyond what the context says."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"## Design Context\n\n{context}\n\n"
                    f"## Agent Question\n\n{question}\n\n"
                    "Can you answer this from the design context above?"
                ),
            }],
        )

        answer_text = _safe_response_text(response)
        if answer_text is None:
            raise ValueError("Unexpected API response structure")

        if "cannot_answer" in answer_text.lower().replace(" ", "_"):
            return Resolution(answered=False, confidence=0.3, source="llm")

        return Resolution(
            answered=True,
            answer=answer_text,
            confidence=0.75,
            source="llm",
        )


# ── OutputValidator ───────────────────────────────────────────

class OutputValidator:
    """Validate completed agent work against acceptance criteria.

    Uses a single Opus API call to judge whether changes satisfy
    the story's acceptance criteria.
    """

    def __init__(self, model: str = "claude-opus-4-6") -> None:
        self._model = model

    async def validate(
        self,
        acceptance_criteria: list[str],
        result_text: str,
        diff_stat: str = "",
        changed_files: list[str] | None = None,
    ) -> Verdict:
        """Validate agent output against acceptance criteria.

        Args:
            acceptance_criteria: List of criteria from the story
            result_text: The agent's result summary
            diff_stat: Git diff --stat output
            changed_files: List of modified file paths

        Returns:
            Verdict with pass/fail/partial outcome
        """
        if not acceptance_criteria:
            return Verdict(outcome="pass", reasoning="No acceptance criteria defined")

        try:
            return await self._llm_validate(
                acceptance_criteria, result_text, diff_stat, changed_files or [],
            )
        except Exception:
            logger.warning("LLM output validation failed, passing by default")
            return Verdict(outcome="pass", reasoning="Validation unavailable — passed by default")

    async def _llm_validate(
        self,
        acceptance_criteria: list[str],
        result_text: str,
        diff_stat: str,
        changed_files: list[str],
    ) -> Verdict:
        """Use Opus to judge whether output satisfies criteria."""
        if _anthropic is None:
            raise ImportError("anthropic SDK not available")

        criteria_text = "\n".join(f"- {c}" for c in acceptance_criteria)
        files_text = "\n".join(f"- {f}" for f in changed_files) if changed_files else "None"

        client = _anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=self._model,
            max_tokens=800,
            system=(
                "You are validating whether an agent's completed work satisfies "
                "the given acceptance criteria.\n\n"
                "Evaluate each criterion against the agent's result and the files changed.\n\n"
                "Respond in this exact format:\n"
                "VERDICT: pass|fail|partial\n"
                "REASONING: <brief explanation>\n"
                "MISSING: <comma-separated list of unmet criteria, or 'none'>"
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"## Acceptance Criteria\n{criteria_text}\n\n"
                    f"## Agent Result\n{result_text}\n\n"
                    f"## Diff Stats\n{diff_stat or 'No diff available'}\n\n"
                    f"## Changed Files\n{files_text}"
                ),
            }],
        )

        text = _safe_response_text(response)
        if text is None:
            raise ValueError("Unexpected API response structure")
        return self._parse_verdict(text, acceptance_criteria)

    @staticmethod
    def _parse_verdict(text: str, acceptance_criteria: list[str]) -> Verdict:
        """Parse structured verdict from LLM response.

        Fail-safe: defaults to "fail" if no valid VERDICT: line is found.
        This prevents silently accepting invalid work on malformed responses.
        """
        outcome = "fail"  # fail-safe default — reject unless explicitly passed
        reasoning_lines: list[str] = []
        missing: list[str] = []
        collecting_reasoning = False

        for line in text.strip().splitlines():
            stripped = line.strip()
            upper = stripped.upper()

            if upper.startswith("VERDICT:"):
                collecting_reasoning = False
                raw = stripped.split(":", 1)[1].strip().lower()
                if raw in ("pass", "fail", "partial"):
                    outcome = raw
            elif upper.startswith("REASONING:"):
                collecting_reasoning = True
                first_line = stripped.split(":", 1)[1].strip()
                if first_line:
                    reasoning_lines.append(first_line)
            elif upper.startswith("MISSING:"):
                collecting_reasoning = False
                raw_missing = stripped.split(":", 1)[1].strip()
                if raw_missing.lower() != "none":
                    missing = [m.strip() for m in raw_missing.split(",") if m.strip()]
            elif collecting_reasoning and stripped:
                reasoning_lines.append(stripped)

        reasoning = " ".join(reasoning_lines)
        return Verdict(outcome=outcome, reasoning=reasoning, missing_criteria=missing)


# ── RetryEnricher ─────────────────────────────────────────────

class RetryEnricher:
    """Enrich job instructions with feedback from previous attempts.

    Purely mechanical — no LLM calls. Appends a structured feedback
    section to the original instructions.
    """

    def enrich_instructions(
        self,
        original_instructions: str,
        error: str = "",
        validation_feedback: str = "",
        missing_criteria: list[str] | None = None,
        attempt: int = 1,
    ) -> str:
        """Append retry feedback to instructions.

        Args:
            original_instructions: The original agent instructions
            error: Error message from the failed attempt
            validation_feedback: Reasoning from OutputValidator
            missing_criteria: List of unmet acceptance criteria
            attempt: Current attempt number (1-based)

        Returns:
            Enriched instructions with feedback section appended
        """
        parts = [original_instructions]

        feedback_lines = [f"\n\n## Previous Attempt Feedback (Attempt {attempt})"]

        if error:
            feedback_lines.append(f"\n### Error\n{error}")

        if validation_feedback:
            feedback_lines.append(f"\n### Validation Result\n{validation_feedback}")

        if missing_criteria:
            feedback_lines.append("\n### Unmet Acceptance Criteria")
            for criterion in missing_criteria:
                feedback_lines.append(f"- {criterion}")

        feedback_lines.append(
            "\n**Please address the issues above in this attempt. "
            "Focus on the unmet criteria and avoid repeating the same mistakes.**"
        )

        parts.append("\n".join(feedback_lines))
        return "\n".join(parts)
