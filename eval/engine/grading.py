"""Score aggregation, dimension weights, and pass/fail logic."""

from __future__ import annotations

from eval.schema import CaseResult, EvalRun, SuiteResult


def grade_tier1_suite(results: list[CaseResult], category: str) -> SuiteResult:
    """Grade a Tier 1 suite — binary pass/fail per case."""
    suite = SuiteResult(tier=1, category=category, cases=results)
    suite.compute_stats()
    return suite


def grade_tier2_suite(results: list[CaseResult], category: str) -> SuiteResult:
    """Grade a Tier 2 suite — multi-dimensional scoring."""
    suite = SuiteResult(tier=2, category=category, cases=results)
    suite.compute_stats()
    return suite


def check_thresholds(
    run: EvalRun,
    tier1_threshold: float = 1.0,
    tier2_overall_threshold: float = 0.70,
    tier2_dimension_min: float = 0.50,
) -> list[str]:
    """Check if an eval run meets quality thresholds.

    Returns list of failure reasons. Empty = all passed.
    """
    failures = []

    for suite in run.suites:
        if suite.tier == 1:
            if suite.score < tier1_threshold:
                failures.append(
                    f"Tier 1 '{suite.category}': {suite.score:.1%} "
                    f"(threshold: {tier1_threshold:.0%})"
                )
        elif suite.tier == 2:
            if suite.score < tier2_overall_threshold:
                failures.append(
                    f"Tier 2 '{suite.category}': {suite.score:.1%} "
                    f"(threshold: {tier2_overall_threshold:.0%})"
                )

            # Check per-dimension minimums
            for case in suite.cases:
                for dim in case.dimensions:
                    if dim.score < tier2_dimension_min:
                        failures.append(
                            f"Tier 2 '{case.case_id}' dimension '{dim.name}': "
                            f"{dim.score:.1%} (min: {tier2_dimension_min:.0%})"
                        )

    return failures
