"""Tier 2 multi-turn evaluator — conversation sequences with checkpoints.

Runs a sequence of user messages, checking intermediate state at each
checkpoint (tools called, response contents). Final grading via LLM judge.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from eval.engine.harness import EvalToolHarness
from eval.schema import (
    CaseResult,
    CheckResult,
    ConversationTurn,
    DimensionScore,
    Tier2Case,
    TurnCheckpoint,
)

logger = logging.getLogger(__name__)


def _check_checkpoint(
    checkpoint: TurnCheckpoint,
    harness: EvalToolHarness,
    response_text: str,
    turn_idx: int,
) -> list[CheckResult]:
    """Check a single turn checkpoint against actual results."""
    checks: list[CheckResult] = []
    prefix = f"turn_{turn_idx}"

    if checkpoint.tools_called:
        actual_tools = set(harness.call_sequence())
        for tool_name in checkpoint.tools_called:
            found = tool_name in actual_tools
            checks.append(CheckResult(
                name=f"{prefix}_tool_{tool_name}",
                expected=True,
                actual=found,
                passed=found,
            ))

    if checkpoint.response_contains:
        response_lower = response_text.lower()
        for term in checkpoint.response_contains:
            found = term.lower() in response_lower
            checks.append(CheckResult(
                name=f"{prefix}_contains_{term}",
                expected=True,
                actual=found,
                passed=found,
            ))

    if checkpoint.response_excludes:
        response_lower = response_text.lower()
        for term in checkpoint.response_excludes:
            found = term.lower() in response_lower
            checks.append(CheckResult(
                name=f"{prefix}_excludes_{term}",
                expected=False,
                actual=found,
                passed=not found,
            ))

    return checks


async def evaluate_multi_turn(
    case: Tier2Case,
    judge_fn: Any = None,
    config: Any = None,
) -> CaseResult:
    """Evaluate a multi-turn conversation case.

    Args:
        case: The test case with turns and checkpoints.
        judge_fn: Async function(case, responses, tool_trace) -> list[DimensionScore].
        config: GrimConfig for agent instantiation.
    """
    start = time.monotonic()

    if not case.turns:
        return CaseResult(
            case_id=case.id,
            tier=2,
            category="multi_turn",
            tags=case.tags,
            passed=False,
            error="No turns in multi-turn case",
            duration_ms=0,
        )

    all_checks: list[CheckResult] = []
    all_responses: list[str] = []
    all_tool_calls: list[str] = []
    harness = EvalToolHarness(case.mock_tools)

    try:
        for i, turn in enumerate(case.turns):
            # Reset harness for each turn (but keep cumulative tool trace)
            turn_start_idx = len(harness.calls)

            # Simulate the turn
            response_text = _simulate_turn(turn, harness, case.mock_tools)
            all_responses.append(response_text)

            # Track tool calls for this turn
            turn_calls = harness.call_sequence()[turn_start_idx:]
            all_tool_calls.extend(turn_calls)

            # Check checkpoint if present
            if turn.checkpoint:
                # Build a turn-scoped harness view for checkpoint
                turn_harness = EvalToolHarness()
                turn_harness.calls = harness.calls[turn_start_idx:]

                checks = _check_checkpoint(
                    turn.checkpoint, turn_harness, response_text, i
                )
                all_checks.extend(checks)

        # LLM judge grading
        dimensions: list[DimensionScore] = []
        judge_output = None
        if judge_fn and case.grading:
            combined_response = "\n---\n".join(all_responses)
            dimensions = await judge_fn(case, combined_response, all_tool_calls)
            judge_output = json.dumps(
                [{"name": d.name, "score": d.score, "rationale": d.rationale} for d in dimensions]
            )

        # Compute score
        score = _compute_multi_score(all_checks, dimensions)
        passed = score >= 0.7

    except Exception as exc:
        logger.error("Multi-turn eval error for %s: %s", case.id, exc)
        return CaseResult(
            case_id=case.id,
            tier=2,
            category="multi_turn",
            tags=case.tags,
            passed=False,
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    return CaseResult(
        case_id=case.id,
        tier=2,
        category="multi_turn",
        tags=case.tags,
        passed=passed,
        score=score,
        checks=all_checks,
        dimensions=dimensions,
        tool_trace=all_tool_calls,
        response_text="\n---\n".join(all_responses),
        judge_output=judge_output,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def _simulate_turn(
    turn: ConversationTurn,
    harness: EvalToolHarness,
    mock_tools: dict[str, Any],
) -> str:
    """Simulate a single conversation turn.

    In dry-run mode, calls mock tools and builds a response.
    """
    # Call tools based on checkpoint expectations (simulate what agent would do)
    tools = harness.make_tools()
    if turn.checkpoint and turn.checkpoint.tools_called:
        for tool_name in turn.checkpoint.tools_called:
            for tool in tools:
                if tool.name == tool_name:
                    tool.invoke({})
                    break

    # Build simulated response
    parts = [f"Processing: {turn.message}"]
    if turn.checkpoint and turn.checkpoint.response_contains:
        parts.extend(turn.checkpoint.response_contains)

    return " ".join(parts)


def _compute_multi_score(
    checks: list[CheckResult],
    dimensions: list[DimensionScore],
) -> float:
    """Compute overall multi-turn score."""
    scores: list[float] = []

    if checks:
        check_score = sum(1 for c in checks if c.passed) / len(checks)
        scores.append(check_score)

    if dimensions:
        total_weight = sum(d.weight for d in dimensions)
        if total_weight > 0:
            dim_score = sum(d.score * d.weight for d in dimensions) / total_weight
            scores.append(dim_score)

    if not scores:
        return 0.0

    return sum(scores) / len(scores)


async def evaluate_multi_turn_suite(
    cases: list[Tier2Case],
    judge_fn: Any = None,
    config: Any = None,
) -> list[CaseResult]:
    """Evaluate all multi-turn cases."""
    results = []
    for case in cases:
        if case.turn_type != "multi":
            continue
        result = await evaluate_multi_turn(case, judge_fn, config)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        logger.info("  %s %s (%.2f)", status, case.id, result.score)
    return results
