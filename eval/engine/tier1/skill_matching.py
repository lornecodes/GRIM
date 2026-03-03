"""Tier 1 skill matching evaluator — test match_skills accuracy.

Loads skills from the registry and tests that messages trigger the
expected skills (or correctly don't trigger).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from eval.schema import CheckResult, CaseResult, Tier1Case

logger = logging.getLogger(__name__)


def _load_skill_registry(skills_path: Path | None = None):
    """Load the skill registry from disk."""
    from core.skills.loader import load_skills

    if skills_path is None:
        # Default to GRIM/skills/
        skills_path = Path(__file__).parents[3] / "skills"

    return load_skills(skills_path)


async def evaluate_skill_case(
    case: Tier1Case,
    registry: Any = None,
    skills_path: Path | None = None,
) -> CaseResult:
    """Evaluate a single skill matching test case."""
    from core.skills.matcher import match_skills

    start = time.monotonic()
    checks: list[CheckResult] = []

    if registry is None:
        registry = _load_skill_registry(skills_path)

    try:
        matched = match_skills(case.message, registry)
        matched_names = [s.name for s in matched]

        # Check matched_skills
        if case.expected.matched_skills is not None:
            expected = case.expected.matched_skills
            if len(expected) == 0:
                # Negative case — should NOT match any
                checks.append(CheckResult(
                    name="no_match",
                    expected=[],
                    actual=matched_names,
                    passed=len(matched_names) == 0,
                ))
            else:
                # Positive case — expected skills should be in matches
                for skill_name in expected:
                    checks.append(CheckResult(
                        name=f"matches_{skill_name}",
                        expected=skill_name,
                        actual=matched_names,
                        passed=skill_name in matched_names,
                    ))

        # Check top_skill
        if case.expected.top_skill is not None:
            actual_top = matched_names[0] if matched_names else None
            checks.append(CheckResult(
                name="top_skill",
                expected=case.expected.top_skill,
                actual=actual_top,
                passed=actual_top == case.expected.top_skill,
            ))

        # Check delegation_hint (from skill consumer mapping)
        if case.expected.delegation_hint is not None:
            # Use the deprecated shim to check delegation mapping
            from core.nodes.router import _skill_ctx_to_delegation

            actual_hint = None
            if matched:
                actual_hint = _skill_ctx_to_delegation(matched[0])

            checks.append(CheckResult(
                name="delegation_hint",
                expected=case.expected.delegation_hint,
                actual=actual_hint,
                passed=actual_hint == case.expected.delegation_hint,
            ))

    except Exception as exc:
        return CaseResult(
            case_id=case.id,
            tier=1,
            category="skill_matching",
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
        category="skill_matching",
        tags=case.tags,
        passed=passed,
        score=1.0 if passed else 0.0,
        checks=checks,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


async def evaluate_skill_suite(
    cases: list[Tier1Case],
    skills_path: Path | None = None,
) -> list[CaseResult]:
    """Evaluate all skill matching cases."""
    registry = _load_skill_registry(skills_path)
    results = []
    for case in cases:
        result = await evaluate_skill_case(case, registry, skills_path)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        logger.info("  %s %s", status, case.id)
    return results
