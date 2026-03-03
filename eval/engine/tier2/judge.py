"""LLM-as-judge scoring engine.

Sends the case context, agent response, and tool trace to an LLM
and extracts structured scores for each grading dimension.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from eval.schema import DimensionScore, Tier2Case

logger = logging.getLogger(__name__)

# Default dimension weights
DIMENSION_WEIGHTS: dict[str, float] = {
    "understanding": 1.0,
    "planning": 0.8,
    "execution": 1.0,
    "completeness": 0.8,
    "verification": 0.6,
}

JUDGE_SYSTEM_PROMPT = """\
You are an evaluation judge for GRIM, an AI assistant system.
Your job is to score the agent's response across several dimensions.

For each dimension, provide:
- A score from 0.0 to 1.0 (0.0 = complete failure, 1.0 = perfect)
- A brief rationale explaining the score

Respond ONLY with a JSON array of objects:
[
  {"dimension": "name", "score": 0.85, "rationale": "..."},
  ...
]

Be strict but fair. A score of 0.7+ means good performance.
A score below 0.5 means significant issues.
"""


def build_judge_prompt(
    case: Tier2Case,
    response_text: str,
    tool_trace: list[str],
) -> str:
    """Build the judge prompt from case data and agent output."""
    parts = [
        "## Test Case",
        f"**ID**: {case.id}",
        f"**Tags**: {', '.join(case.tags)}",
    ]

    if case.turn_type == "single" and case.message:
        parts.append(f"\n**User Message**: {case.message}")
    elif case.turns:
        parts.append("\n**Conversation**:")
        for i, turn in enumerate(case.turns):
            parts.append(f"  Turn {i+1} ({turn.role}): {turn.message}")

    parts.append(f"\n**Agent Response**:\n{response_text}")
    parts.append(f"\n**Tool Calls**: {', '.join(tool_trace) if tool_trace else 'None'}")

    if case.golden_response:
        parts.append(f"\n**Reference Response**:\n{case.golden_response}")

    parts.append("\n## Grading Dimensions")
    for dim_name, dim_prompt in case.grading.items():
        parts.append(f"- **{dim_name}**: {dim_prompt}")

    parts.append("\nScore each dimension 0.0-1.0 with rationale. Respond ONLY with JSON array.")

    return "\n".join(parts)


def parse_judge_response(response_text: str, case: Tier2Case) -> list[DimensionScore]:
    """Parse the judge's JSON response into DimensionScore objects."""
    # Try to extract JSON from response
    text = response_text.strip()

    # Handle fenced JSON
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse judge response as JSON: %s", text[:200])
        # Return default scores
        return [
            DimensionScore(
                name=dim,
                score=0.0,
                weight=DIMENSION_WEIGHTS.get(dim, 1.0),
                rationale="Judge response parsing failed",
            )
            for dim in case.grading
        ]

    dimensions = []
    for item in data:
        name = item.get("dimension", "")
        score = float(item.get("score", 0.0))
        rationale = item.get("rationale", "")
        weight = DIMENSION_WEIGHTS.get(name, 1.0)

        dimensions.append(DimensionScore(
            name=name,
            score=max(0.0, min(1.0, score)),  # clamp to [0, 1]
            weight=weight,
            rationale=rationale,
        ))

    return dimensions


async def judge_case(
    case: Tier2Case,
    response_text: str,
    tool_trace: list[str],
    model: str = "claude-sonnet-4-6",
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> list[DimensionScore]:
    """Score a case using an LLM judge.

    Args:
        case: The test case with grading dimensions.
        response_text: The agent's response.
        tool_trace: List of tool names called.
        model: Judge model ID.
        temperature: Judge temperature.
        max_tokens: Max judge response tokens.

    Returns:
        List of DimensionScore objects.
    """
    if not case.grading:
        return []

    try:
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = ChatAnthropic(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        prompt = build_judge_prompt(case, response_text, tool_trace)

        response = await llm.ainvoke([
            SystemMessage(content=JUDGE_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])

        return parse_judge_response(response.content, case)

    except ImportError:
        logger.warning("langchain_anthropic not available — skipping LLM judge")
        return [
            DimensionScore(
                name=dim,
                score=0.5,
                weight=DIMENSION_WEIGHTS.get(dim, 1.0),
                rationale="LLM judge not available (missing langchain_anthropic)",
            )
            for dim in case.grading
        ]
    except Exception as exc:
        logger.error("Judge invocation failed: %s", exc)
        return [
            DimensionScore(
                name=dim,
                score=0.0,
                weight=DIMENSION_WEIGHTS.get(dim, 1.0),
                rationale=f"Judge error: {exc}",
            )
            for dim in case.grading
        ]


def make_judge_fn(
    model: str = "claude-sonnet-4-6",
    temperature: float = 0.1,
    max_tokens: int = 2048,
):
    """Create a judge function closure with config baked in."""

    async def _judge(
        case: Tier2Case,
        response_text: str,
        tool_trace: list[str],
    ) -> list[DimensionScore]:
        return await judge_case(
            case, response_text, tool_trace,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    return _judge
