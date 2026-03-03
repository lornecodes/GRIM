"""Baseline comparison and regression detection."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from eval.schema import ComparisonResult, EvalRun, RegressionItem

logger = logging.getLogger(__name__)


def compare_runs(base: EvalRun, target: EvalRun, tolerance: float = 0.05) -> ComparisonResult:
    """Compare two eval runs and detect regressions.

    A regression is defined as a score drop > tolerance for any case.
    """
    result = ComparisonResult(
        base_run_id=base.run_id,
        target_run_id=target.run_id,
    )

    # Build case score maps
    base_scores: dict[str, float] = {}
    for suite in base.suites:
        for case in suite.cases:
            base_scores[case.case_id] = case.score

    target_scores: dict[str, float] = {}
    target_categories: dict[str, str] = {}
    for suite in target.suites:
        for case in suite.cases:
            target_scores[case.case_id] = case.score
            target_categories[case.case_id] = suite.category

    # Compare
    all_cases = set(base_scores) | set(target_scores)
    for case_id in all_cases:
        b_score = base_scores.get(case_id, 0.0)
        t_score = target_scores.get(case_id, 0.0)
        delta = t_score - b_score
        category = target_categories.get(case_id, "unknown")

        if delta < -tolerance:
            severity = "critical" if delta < -0.3 else "major" if delta < -0.15 else "minor"
            result.regressions.append(RegressionItem(
                case_id=case_id,
                category=category,
                base_score=b_score,
                target_score=t_score,
                delta=delta,
                severity=severity,
            ))
        elif delta > tolerance:
            result.improvements.append(RegressionItem(
                case_id=case_id,
                category=category,
                base_score=b_score,
                target_score=t_score,
                delta=delta,
                severity="improvement",
            ))
        else:
            result.unchanged += 1

    result.has_regressions = len(result.regressions) > 0
    result.overall_delta = target.overall_score - base.overall_score

    return result


def load_run(path: Path) -> EvalRun:
    """Load an eval run from a JSON file."""
    data = json.loads(path.read_text())
    return EvalRun(**data)


def find_latest_run(results_dir: Path, tier: int | str | None = None) -> EvalRun | None:
    """Find the most recent eval run result file."""
    files = sorted(results_dir.glob("*.json"), reverse=True)
    for f in files:
        try:
            run = load_run(f)
            if tier is not None and run.tier != tier:
                continue
            return run
        except Exception:
            continue
    return None


def save_run(run: EvalRun, results_dir: Path) -> Path:
    """Save an eval run to a JSON file."""
    results_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{run.timestamp.replace(':', '-').replace('T', '_')}_{run.run_id[:8]}.json"
    path = results_dir / filename
    path.write_text(run.model_dump_json(indent=2))
    logger.info("Saved eval results to %s", path)
    return path
