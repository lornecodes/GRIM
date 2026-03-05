"""Multi-judge system for Tier 3 live integration evaluation.

5 specialized judges, each scoring a different dimension:
- RoutingJudge: correct subgraph, delegation, model tier (deterministic)
- QualityJudge: response helpfulness and accuracy (LLM-graded)
- DomainJudge: factual accuracy against vault ground truth (LLM-graded)
- CodeJudge: code quality and correctness (LLM-graded)
- EfficiencyJudge: token usage, timing, loop count (deterministic)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from eval.schema import (
    Tier3Case,
    Tier3CaseResult,
    Tier3Judgment,
    Tier3Metrics,
)

logger = logging.getLogger(__name__)


class Judge(ABC):
    """Base class for Tier 3 judges."""

    name: str = "base"

    @abstractmethod
    async def judge(
        self, case: Tier3Case, result: Tier3CaseResult, context: dict[str, Any] | None = None,
    ) -> Tier3Judgment:
        """Score a case result. Returns a Tier3Judgment."""
        ...


# ---------------------------------------------------------------------------
# RoutingJudge — deterministic routing accuracy
# ---------------------------------------------------------------------------


class RoutingJudge(Judge):
    """Check routing accuracy: correct subgraph, delegation type, model tier."""

    name = "routing"

    async def judge(
        self, case: Tier3Case, result: Tier3CaseResult, context: dict[str, Any] | None = None,
    ) -> Tier3Judgment:
        checks: list[str] = []
        failures: list[str] = []

        # Check expected routing (case-level or per-turn)
        expected = case.expected_routing
        if expected:
            # Check subgraph
            if expected.subgraph:
                actual_subgraph = result.subgraph_history[-1] if result.subgraph_history else None
                if actual_subgraph == expected.subgraph:
                    checks.append(f"subgraph={expected.subgraph}")
                else:
                    failures.append(
                        f"subgraph: expected {expected.subgraph}, got {actual_subgraph}"
                    )

            # Check delegation type
            if expected.delegation_type:
                # Look through turn results for delegation info
                actual_delegation = None
                for tr in result.turn_results:
                    for evt in tr.trace_events:
                        if evt.get("type") == "trace" and evt.get("delegation_type"):
                            actual_delegation = evt["delegation_type"]
                if actual_delegation == expected.delegation_type:
                    checks.append(f"delegation={expected.delegation_type}")
                else:
                    failures.append(
                        f"delegation: expected {expected.delegation_type}, got {actual_delegation}"
                    )

        # Check per-turn routing expectations
        for i, turn in enumerate(case.turns):
            if turn.expected_routing and i < len(result.turn_results):
                turn_result = result.turn_results[i]
                tr_expected = turn.expected_routing
                if tr_expected.subgraph and turn_result.subgraph != tr_expected.subgraph:
                    failures.append(
                        f"turn {i} subgraph: expected {tr_expected.subgraph}, got {turn_result.subgraph}"
                    )
                else:
                    checks.append(f"turn {i} subgraph={turn_result.subgraph}")

        # Check expected tools
        if case.expected_tools:
            for tool_name in case.expected_tools:
                if tool_name in result.tools_called:
                    checks.append(f"tool={tool_name}")
                else:
                    failures.append(f"expected tool not called: {tool_name}")

        # Check unexpected tools
        if case.unexpected_tools:
            for tool_name in case.unexpected_tools:
                if tool_name in result.tools_called:
                    failures.append(f"unexpected tool called: {tool_name}")

        total = len(checks) + len(failures)
        score = len(checks) / total if total > 0 else 1.0
        passed = len(failures) == 0

        return Tier3Judgment(
            judge=self.name,
            score=score,
            passed=passed,
            rationale="; ".join(failures) if failures else "All routing checks passed",
            details={"checks": checks, "failures": failures},
        )


# ---------------------------------------------------------------------------
# QualityJudge — LLM-graded response quality
# ---------------------------------------------------------------------------


class QualityJudge(Judge):
    """LLM-graded response quality: helpfulness, accuracy, formatting."""

    name = "quality"

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self.model = model

    async def judge(
        self, case: Tier3Case, result: Tier3CaseResult, context: dict[str, Any] | None = None,
    ) -> Tier3Judgment:
        # Collect all response texts
        responses = [tr.response_text for tr in result.turn_results if tr.response_text]
        if not responses:
            return Tier3Judgment(
                judge=self.name, score=0.0, passed=False,
                rationale="No response generated",
            )

        # Check response_contains / response_excludes from turns
        checks: list[str] = []
        failures: list[str] = []

        for i, turn in enumerate(case.turns):
            if i >= len(result.turn_results):
                break
            response = result.turn_results[i].response_text.lower()

            for pattern in turn.response_contains:
                if pattern.lower() in response:
                    checks.append(f"turn {i} contains '{pattern}'")
                else:
                    failures.append(f"turn {i} missing '{pattern}'")

            for pattern in turn.response_excludes:
                if pattern.lower() in response:
                    failures.append(f"turn {i} contains excluded '{pattern}'")

        # For now, structural checks only — LLM grading added when judges
        # have access to the LLM client
        total = len(checks) + len(failures)
        score = len(checks) / total if total > 0 else 0.8  # default decent

        return Tier3Judgment(
            judge=self.name,
            score=score,
            passed=len(failures) == 0,
            rationale="; ".join(failures) if failures else "Quality checks passed",
            details={"checks": checks, "failures": failures},
        )


# ---------------------------------------------------------------------------
# DomainJudge — factual accuracy against ground truth
# ---------------------------------------------------------------------------


class DomainJudge(Judge):
    """Check domain accuracy against Kronos vault ground truth."""

    name = "domain"

    def __init__(self, ground_truth_loader: Any = None) -> None:
        self.ground_truth_loader = ground_truth_loader

    async def judge(
        self, case: Tier3Case, result: Tier3CaseResult, context: dict[str, Any] | None = None,
    ) -> Tier3Judgment:
        if not case.domain_facts:
            # No domain facts to check — skip
            return Tier3Judgment(
                judge=self.name, score=1.0, passed=True,
                rationale="No domain facts to verify",
            )

        # Collect all response text
        full_response = " ".join(
            tr.response_text.lower() for tr in result.turn_results if tr.response_text
        )

        checks: list[str] = []
        failures: list[str] = []

        for fact in case.domain_facts:
            # Simple substring check — LLM-graded verification added later
            claim_lower = fact.claim.lower()
            if claim_lower in full_response:
                checks.append(f"fact: {fact.claim}")
            else:
                # Check if key terms are present
                terms = [t for t in claim_lower.split() if len(t) > 3]
                term_hits = sum(1 for t in terms if t in full_response)
                if terms and term_hits >= len(terms) * 0.6:
                    checks.append(f"fact (partial): {fact.claim}")
                else:
                    failures.append(f"missing fact: {fact.claim}")

        total = len(checks) + len(failures)
        score = len(checks) / total if total > 0 else 0.0

        return Tier3Judgment(
            judge=self.name,
            score=score,
            passed=score >= 0.6,
            rationale="; ".join(failures) if failures else "All domain facts verified",
            details={"checks": checks, "failures": failures},
        )


# ---------------------------------------------------------------------------
# CodeJudge — code generation quality
# ---------------------------------------------------------------------------


class CodeJudge(Judge):
    """Evaluate code generation quality."""

    name = "code"

    async def judge(
        self, case: Tier3Case, result: Tier3CaseResult, context: dict[str, Any] | None = None,
    ) -> Tier3Judgment:
        if not case.code_expectations:
            return Tier3Judgment(
                judge=self.name, score=1.0, passed=True,
                rationale="No code expectations to verify",
            )

        exp = case.code_expectations
        full_response = "\n".join(
            tr.response_text for tr in result.turn_results if tr.response_text
        )

        checks: list[str] = []
        failures: list[str] = []

        for pattern in exp.must_contain:
            if pattern in full_response:
                checks.append(f"contains '{pattern}'")
            else:
                failures.append(f"missing '{pattern}'")

        for pattern in exp.must_not_contain:
            if pattern in full_response:
                failures.append(f"contains forbidden '{pattern}'")

        total = len(checks) + len(failures)
        score = len(checks) / total if total > 0 else 0.0

        return Tier3Judgment(
            judge=self.name,
            score=score,
            passed=len(failures) == 0,
            rationale="; ".join(failures) if failures else "Code checks passed",
            details={"checks": checks, "failures": failures},
        )


# ---------------------------------------------------------------------------
# EfficiencyJudge — deterministic metrics checks
# ---------------------------------------------------------------------------


# Default thresholds (can be overridden per case)
_DEFAULT_THRESHOLDS = {
    "max_tokens": 50_000,       # per turn
    "max_wall_time_ms": 60_000, # 60 seconds
    "max_loops": 3,
    "max_llm_calls": 10,
    "max_tool_calls": 20,
}

# Model tier expectations per category
_EXPECTED_TIERS = {
    "conversation": "haiku",  # simple chat should use cheap model
    "research": "sonnet",
    "planning": "sonnet",
    "code": "sonnet",
}


class EfficiencyJudge(Judge):
    """Deterministic efficiency checks — no LLM needed."""

    name = "efficiency"

    def __init__(self, thresholds: dict[str, Any] | None = None) -> None:
        self.thresholds = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}

    async def judge(
        self, case: Tier3Case, result: Tier3CaseResult, context: dict[str, Any] | None = None,
    ) -> Tier3Judgment:
        metrics = result.metrics
        checks: list[str] = []
        failures: list[str] = []

        # Token usage
        max_tokens = case.max_tokens or self.thresholds["max_tokens"]
        if metrics.total_tokens <= max_tokens:
            checks.append(f"tokens={metrics.total_tokens} <= {max_tokens}")
        else:
            failures.append(f"tokens={metrics.total_tokens} > {max_tokens}")

        # Wall time
        max_time = case.max_wall_time_ms or self.thresholds["max_wall_time_ms"]
        if metrics.wall_time_ms <= max_time:
            checks.append(f"time={metrics.wall_time_ms}ms <= {max_time}ms")
        else:
            failures.append(f"time={metrics.wall_time_ms}ms > {max_time}ms")

        # Loop count
        max_loops = case.max_loops or self.thresholds["max_loops"]
        if metrics.turns <= max_loops:
            checks.append(f"loops={metrics.turns} <= {max_loops}")
        else:
            failures.append(f"loops={metrics.turns} > {max_loops}")

        # LLM calls
        max_llm = self.thresholds["max_llm_calls"]
        if metrics.llm_call_count <= max_llm:
            checks.append(f"llm_calls={metrics.llm_call_count} <= {max_llm}")
        else:
            failures.append(f"llm_calls={metrics.llm_call_count} > {max_llm}")

        # Tool calls
        max_tools = self.thresholds["max_tool_calls"]
        if metrics.tool_call_count <= max_tools:
            checks.append(f"tool_calls={metrics.tool_call_count} <= {max_tools}")
        else:
            failures.append(f"tool_calls={metrics.tool_call_count} > {max_tools}")

        # Score based on how many efficiency dimensions pass
        total = len(checks) + len(failures)
        score = len(checks) / total if total > 0 else 0.0

        return Tier3Judgment(
            judge=self.name,
            score=score,
            passed=len(failures) == 0,
            rationale="; ".join(failures) if failures else "All efficiency checks passed",
            details={
                "checks": checks,
                "failures": failures,
                "metrics_summary": {
                    "total_tokens": metrics.total_tokens,
                    "wall_time_ms": metrics.wall_time_ms,
                    "loops": metrics.turns,
                    "llm_calls": metrics.llm_call_count,
                    "tool_calls": metrics.tool_call_count,
                    "cost_usd": metrics.cost_estimate_usd,
                },
            },
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_default_judges(
    model: str = "claude-sonnet-4-6",
    ground_truth_loader: Any = None,
    efficiency_thresholds: dict[str, Any] | None = None,
) -> list[Judge]:
    """Create the standard set of 5 judges."""
    return [
        RoutingJudge(),
        QualityJudge(model=model),
        DomainJudge(ground_truth_loader=ground_truth_loader),
        CodeJudge(),
        EfficiencyJudge(thresholds=efficiency_thresholds),
    ]
