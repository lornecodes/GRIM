"""Tier 1 keyword routing evaluator — test match_keywords + match_action_intent.

Direct invocation of routing functions with message strings.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from eval.schema import CheckResult, CaseResult, Tier1Case

logger = logging.getLogger(__name__)


async def evaluate_keyword_case(case: Tier1Case) -> CaseResult:
    """Evaluate a single keyword routing test case."""
    from core.nodes.keyword_router import match_action_intent, match_keywords

    start = time.monotonic()
    checks: list[CheckResult] = []
    message = case.message.lower()

    try:
        # Check keyword_match
        if case.expected.keyword_match is not None:
            actual = match_keywords(message)
            expected = case.expected.keyword_match
            # Handle "null" string → None
            if expected == "null":
                expected = None
            checks.append(CheckResult(
                name="keyword_match",
                expected=expected,
                actual=actual,
                passed=actual == expected,
            ))

        # Check action_intent
        if case.expected.action_intent is not None:
            actual = match_action_intent(message)
            expected = case.expected.action_intent
            if expected == "null":
                expected = None
            checks.append(CheckResult(
                name="action_intent",
                expected=expected,
                actual=actual,
                passed=actual == expected,
            ))

    except Exception as exc:
        return CaseResult(
            case_id=case.id,
            tier=1,
            category="keyword_routing",
            tags=case.tags,
            passed=False,
            score=0.0,
            error=str(exc),
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    passed = all(c.passed for c in checks)
    return CaseResult(
        case_id=case.id,
        tier=1,
        category="keyword_routing",
        tags=case.tags,
        passed=passed,
        score=1.0 if passed else 0.0,
        checks=checks,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


async def evaluate_keyword_suite(cases: list[Tier1Case]) -> list[CaseResult]:
    """Evaluate all keyword routing cases."""
    results = []
    for case in cases:
        result = await evaluate_keyword_case(case)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        logger.info("  %s %s", status, case.id)
    return results
