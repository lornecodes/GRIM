"""Tier 1 routing evaluator — test graph_router and router decisions.

Imports the actual node functions and invokes them with constructed
GrimState dicts. No LLM calls, pure structural assertion.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from eval.schema import CheckResult, CaseResult, Tier1Case

logger = logging.getLogger(__name__)


def _build_state(case: Tier1Case) -> dict[str, Any]:
    """Build a minimal GrimState-like dict from a test case."""
    from langchain_core.messages import HumanMessage

    state: dict[str, Any] = {
        "messages": [HumanMessage(content=case.message)],
    }
    # Apply state overrides (skill_delegation_hint, last_delegation_type, etc.)
    state.update(case.state_overrides)
    return state


async def _eval_graph_router(case: Tier1Case) -> CaseResult:
    """Evaluate a case against graph_router_node."""
    from core.nodes.graph_router import graph_router_node

    start = time.monotonic()
    checks: list[CheckResult] = []

    try:
        state = _build_state(case)
        result = await graph_router_node(state)

        if case.expected.graph_target is not None:
            actual = result.get("graph_target")
            checks.append(CheckResult(
                name="graph_target",
                expected=case.expected.graph_target,
                actual=actual,
                passed=actual == case.expected.graph_target,
            ))

    except Exception as exc:
        return CaseResult(
            case_id=case.id,
            tier=1,
            category="routing",
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
        category="routing",
        tags=case.tags,
        passed=passed,
        score=1.0 if passed else 0.0,
        checks=checks,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


async def _eval_router(case: Tier1Case, config: Any = None) -> CaseResult:
    """Evaluate a case against router_node (mode + delegation_type)."""
    from core.config import GrimConfig
    from core.nodes.router import make_router_node

    start = time.monotonic()
    checks: list[CheckResult] = []

    if config is None:
        config = GrimConfig()
        config.routing_enabled = False  # skip model routing for eval

    try:
        router_node = make_router_node(config)
        state = _build_state(case)

        # Router needs matched_skills list
        if "matched_skills" not in state:
            state["matched_skills"] = []

        result = await router_node(state)

        if case.expected.mode is not None:
            actual = result.get("mode")
            checks.append(CheckResult(
                name="mode",
                expected=case.expected.mode,
                actual=actual,
                passed=actual == case.expected.mode,
            ))

        if case.expected.delegation_type is not None:
            actual = result.get("delegation_type")
            checks.append(CheckResult(
                name="delegation_type",
                expected=case.expected.delegation_type,
                actual=actual,
                passed=actual == case.expected.delegation_type,
            ))

    except Exception as exc:
        return CaseResult(
            case_id=case.id,
            tier=1,
            category="routing",
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
        category="routing",
        tags=case.tags,
        passed=passed,
        score=1.0 if passed else 0.0,
        checks=checks,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


async def evaluate_routing_case(case: Tier1Case, config: Any = None) -> CaseResult:
    """Evaluate a single routing test case.

    Dispatches to graph_router or router based on expected fields.
    """
    has_graph_target = case.expected.graph_target is not None
    has_mode = case.expected.mode is not None
    has_delegation = case.expected.delegation_type is not None

    # If only graph_target, test graph_router
    if has_graph_target and not has_mode and not has_delegation:
        return await _eval_graph_router(case)

    # If mode or delegation, need both graph_router + router
    if has_mode or has_delegation:
        # First check graph_target if specified
        results: list[CheckResult] = []
        start = time.monotonic()

        if has_graph_target:
            gr_result = await _eval_graph_router(case)
            results.extend(gr_result.checks)

        # Then check router
        router_result = await _eval_router(case, config)
        results.extend(router_result.checks)

        passed = all(c.passed for c in results)
        return CaseResult(
            case_id=case.id,
            tier=1,
            category="routing",
            tags=case.tags,
            passed=passed,
            score=1.0 if passed else 0.0,
            checks=results,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    # graph_router only
    return await _eval_graph_router(case)


async def evaluate_routing_suite(
    cases: list[Tier1Case],
    config: Any = None,
) -> list[CaseResult]:
    """Evaluate all routing cases."""
    results = []
    for case in cases:
        result = await evaluate_routing_case(case, config)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        logger.info("  %s %s", status, case.id)
    return results
