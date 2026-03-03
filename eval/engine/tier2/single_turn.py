"""Tier 2 single-turn evaluator — invoke agent with mock tools, grade response.

Supports two modes:
  - isolated: Build agent directly, call execute() with harness tools
  - full_graph: Build LangGraph, invoke with mock MCP session
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
    DimensionScore,
    Tier2Case,
    Tier2Expected,
)

logger = logging.getLogger(__name__)


def _structural_checks(
    case: Tier2Case,
    harness: EvalToolHarness,
    response_text: str,
) -> list[CheckResult]:
    """Run structural checks (no LLM needed) on the result."""
    checks: list[CheckResult] = []
    expected = case.expected or Tier2Expected()

    # Check tools_called
    if expected.tools_called:
        actual_tools = set(harness.call_sequence())
        for tool_name in expected.tools_called:
            found = tool_name in actual_tools
            checks.append(CheckResult(
                name=f"tool_called_{tool_name}",
                expected=True,
                actual=found,
                passed=found,
            ))

    # Check response_contains
    if expected.response_contains:
        response_lower = response_text.lower()
        for term in expected.response_contains:
            found = term.lower() in response_lower
            checks.append(CheckResult(
                name=f"contains_{term}",
                expected=True,
                actual=found,
                passed=found,
            ))

    # Check response_excludes
    if expected.response_excludes:
        response_lower = response_text.lower()
        for term in expected.response_excludes:
            found = term.lower() in response_lower
            checks.append(CheckResult(
                name=f"excludes_{term}",
                expected=False,
                actual=found,
                passed=not found,
            ))

    return checks


async def evaluate_single_turn(
    case: Tier2Case,
    judge_fn: Any = None,
    config: Any = None,
) -> CaseResult:
    """Evaluate a single-turn Tier 2 case.

    Args:
        case: The test case to evaluate.
        judge_fn: Async function(case, response, tool_trace) -> list[DimensionScore].
        config: GrimConfig for agent instantiation.
    """
    start = time.monotonic()

    if not case.message:
        return CaseResult(
            case_id=case.id,
            tier=2,
            category="single_turn",
            tags=case.tags,
            passed=False,
            error="No message in single-turn case",
            duration_ms=0,
        )

    # Build tool harness
    harness = EvalToolHarness(case.mock_tools)
    response_text = ""
    tool_trace: list[str] = []

    try:
        # For now, run structural checks with a simulated response.
        # Full agent invocation requires LLM access — gate behind config.
        if config and hasattr(config, "tier2_live") and config.tier2_live:
            # Live mode — actually invoke the agent
            response_text = await _invoke_agent_live(case, harness, config)
        else:
            # Dry-run mode — simulate tool calls and build a mock response
            response_text = _simulate_agent(case, harness)

        tool_trace = harness.call_sequence()

        # Structural checks
        checks = _structural_checks(case, harness, response_text)

        # LLM judge grading
        dimensions: list[DimensionScore] = []
        judge_output = None
        if judge_fn and case.grading:
            dimensions = await judge_fn(case, response_text, tool_trace)
            judge_output = json.dumps(
                [{"name": d.name, "score": d.score, "rationale": d.rationale} for d in dimensions]
            )

        # Compute score
        score = _compute_score(checks, dimensions)
        passed = score >= 0.7 and all(c.passed for c in checks)

    except Exception as exc:
        logger.error("Tier 2 eval error for %s: %s", case.id, exc)
        return CaseResult(
            case_id=case.id,
            tier=2,
            category="single_turn",
            tags=case.tags,
            passed=False,
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    return CaseResult(
        case_id=case.id,
        tier=2,
        category="single_turn",
        tags=case.tags,
        passed=passed,
        score=score,
        checks=checks,
        dimensions=dimensions,
        tool_trace=tool_trace,
        response_text=response_text,
        judge_output=judge_output,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def _simulate_agent(case: Tier2Case, harness: EvalToolHarness) -> str:
    """Simulate agent by calling mock tools in sequence and building response.

    Used in dry-run mode when no LLM is available. Exercises tool harness
    to verify structural expectations.
    """
    # Call each mock tool to record it
    tools = harness.make_tools()
    for tool in tools:
        tool.invoke({})

    # Build a simulated response from tool results
    parts = [f"Executed {len(tools)} tools."]
    for call in harness.calls:
        parts.append(f"Called {call.name}: {call.result}")

    return " ".join(parts)


async def _invoke_agent_live(
    case: Tier2Case,
    harness: EvalToolHarness,
    config: Any,
) -> str:
    """Actually invoke the agent with an LLM. Requires API access."""
    # This is the full live path — agent gets real LLM calls
    # but mock tools via harness.
    #
    # Implementation deferred to Phase 2 when we have the
    # full graph wired up for eval.
    raise NotImplementedError(
        "Live agent invocation not yet implemented. "
        "Use dry-run mode (tier2_live=False) for structural testing."
    )


def _compute_score(
    checks: list[CheckResult],
    dimensions: list[DimensionScore],
) -> float:
    """Compute overall score from structural checks + judge dimensions."""
    scores: list[float] = []

    # Structural check score
    if checks:
        check_score = sum(1 for c in checks if c.passed) / len(checks)
        scores.append(check_score)

    # Dimension scores (weighted)
    if dimensions:
        total_weight = sum(d.weight for d in dimensions)
        if total_weight > 0:
            dim_score = sum(d.score * d.weight for d in dimensions) / total_weight
            scores.append(dim_score)

    if not scores:
        return 0.0

    return sum(scores) / len(scores)


async def evaluate_single_turn_suite(
    cases: list[Tier2Case],
    judge_fn: Any = None,
    config: Any = None,
) -> list[CaseResult]:
    """Evaluate all single-turn cases."""
    results = []
    for case in cases:
        if case.turn_type != "single":
            continue
        result = await evaluate_single_turn(case, judge_fn, config)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        logger.info("  %s %s (%.2f)", status, case.id, result.score)
    return results
