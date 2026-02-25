"""
Evaluator — Per-agent scoring functions.

Each agent gets its own scorer that compares actual output
against expected ground truth and returns a score in [0, 1]
plus a list of specific failures for the optimizer to fix.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


# =========================================================================
# Score result
# =========================================================================

class ScoreResult:
    """Result of evaluating one test case."""

    def __init__(self, case_id: str, score: float, max_score: float,
                 failures: List[str], passes: List[str]):
        self.case_id = case_id
        self.score = score
        self.max_score = max_score
        self.failures = failures
        self.passes = passes

    @property
    def pct(self) -> float:
        return (self.score / self.max_score * 100) if self.max_score > 0 else 0.0

    def __repr__(self):
        return f"<Score {self.case_id}: {self.score:.1f}/{self.max_score:.1f} ({self.pct:.0f}%)>"


# =========================================================================
# Generic helpers
# =========================================================================

def _check_list_contains(actual: List[str], must_include: List[str],
                         label: str, case_insensitive: bool = True) -> Tuple[float, List[str], List[str]]:
    """Check that actual list contains required items. Returns (score, failures, passes)."""
    failures, passes = [], []
    if not must_include:
        return 1.0, failures, passes

    actual_lower = [a.lower() for a in actual] if case_insensitive else actual

    hits = 0
    for item in must_include:
        check = item.lower() if case_insensitive else item
        # Allow substring matching
        found = any(check in a or a in check for a in actual_lower)
        if found:
            hits += 1
            passes.append(f"{label} contains '{item}'")
        else:
            failures.append(f"{label} missing '{item}' (got: {actual[:5]})")

    return hits / len(must_include), failures, passes


def _check_list_excludes(actual: List[str], must_not: List[str],
                         label: str, case_insensitive: bool = True) -> Tuple[float, List[str], List[str]]:
    """Check that actual list does NOT contain forbidden items."""
    failures, passes = [], []
    if not must_not:
        return 1.0, failures, passes

    actual_lower = [a.lower() for a in actual] if case_insensitive else actual

    violations = 0
    for item in must_not:
        check = item.lower() if case_insensitive else item
        found = any(check in a or a in check for a in actual_lower)
        if found:
            violations += 1
            failures.append(f"{label} contains forbidden '{item}'")
        else:
            passes.append(f"{label} correctly excludes '{item}'")

    return 1.0 - (violations / len(must_not)), failures, passes


def _check_text_contains(text: str, must_contain: List[str],
                         label: str) -> Tuple[float, List[str], List[str]]:
    """Check that text contains required substrings."""
    failures, passes = [], []
    if not must_contain:
        return 1.0, failures, passes

    text_lower = text.lower()
    hits = 0
    for item in must_contain:
        if item.lower() in text_lower:
            hits += 1
            passes.append(f"{label} mentions '{item}'")
        else:
            failures.append(f"{label} missing mention of '{item}'")

    return hits / len(must_contain), failures, passes


def _check_text_excludes(text: str, must_not: List[str],
                         label: str) -> Tuple[float, List[str], List[str]]:
    """Check that text does NOT contain forbidden substrings."""
    failures, passes = [], []
    if not must_not:
        return 1.0, failures, passes

    text_lower = text.lower()
    violations = 0
    for item in must_not:
        if item.lower() in text_lower:
            violations += 1
            failures.append(f"{label} contains forbidden '{item}'")
        else:
            passes.append(f"{label} correctly excludes '{item}'")

    return 1.0 - (violations / len(must_not)), failures, passes


# =========================================================================
# Extract agent scorer
# =========================================================================

def score_extract(actual: Dict[str, Any], expected: Dict[str, Any]) -> ScoreResult:
    """Score the extract agent's output against expected."""
    case_id = expected.get("_case_id", "unknown")
    total_score, max_score = 0.0, 0.0
    all_failures, all_passes = [], []

    concepts = actual.get("concepts", [])
    entities = actual.get("entities", [])

    # Concepts must include
    if "concepts_must_include" in expected:
        max_score += 1.0
        s, f, p = _check_list_contains(concepts, expected["concepts_must_include"], "concepts")
        total_score += s
        all_failures.extend(f)
        all_passes.extend(p)

    # Concepts must not include
    if "concepts_must_not_include" in expected:
        max_score += 1.0
        s, f, p = _check_list_excludes(concepts, expected["concepts_must_not_include"], "concepts")
        total_score += s
        all_failures.extend(f)
        all_passes.extend(p)

    # Entities must include
    if "entities_must_include" in expected:
        max_score += 1.0
        s, f, p = _check_list_contains(entities, expected["entities_must_include"], "entities")
        total_score += s
        all_failures.extend(f)
        all_passes.extend(p)

    # Concept count bounds
    if "min_concepts" in expected:
        max_score += 0.5
        if len(concepts) >= expected["min_concepts"]:
            total_score += 0.5
            all_passes.append(f"concepts count {len(concepts)} >= min {expected['min_concepts']}")
        else:
            all_failures.append(f"too few concepts: {len(concepts)} < min {expected['min_concepts']}")

    if "max_concepts" in expected:
        max_score += 0.5
        if len(concepts) <= expected["max_concepts"]:
            total_score += 0.5
            all_passes.append(f"concepts count {len(concepts)} <= max {expected['max_concepts']}")
        else:
            all_failures.append(f"too many concepts: {len(concepts)} > max {expected['max_concepts']}")

    return ScoreResult(case_id, total_score, max_score, all_failures, all_passes)


# =========================================================================
# Judge agent scorer
# =========================================================================

def score_judge(actual: Dict[str, Any], expected: Dict[str, Any]) -> ScoreResult:
    """Score the judge agent's decision against expected."""
    case_id = expected.get("_case_id", "unknown")
    total_score, max_score = 0.0, 0.0
    all_failures, all_passes = [], []

    decision = actual.get("decision", "")

    # Exact decision match
    if "decision" in expected:
        max_score += 2.0
        if decision == expected["decision"]:
            total_score += 2.0
            all_passes.append(f"decision correct: {decision}")
        else:
            all_failures.append(f"wrong decision: got '{decision}', expected '{expected['decision']}'")

    # One-of decision match (flexible cases)
    if "decision_one_of" in expected:
        max_score += 2.0
        if decision in expected["decision_one_of"]:
            total_score += 2.0
            all_passes.append(f"decision acceptable: {decision}")
        else:
            all_failures.append(
                f"wrong decision: got '{decision}', expected one of {expected['decision_one_of']}")

    # Duplicate target
    if "duplicate_of" in expected:
        max_score += 1.0
        if actual.get("duplicate_of") == expected["duplicate_of"]:
            total_score += 1.0
            all_passes.append(f"correct duplicate target: {expected['duplicate_of']}")
        else:
            all_failures.append(
                f"wrong duplicate target: got '{actual.get('duplicate_of')}', "
                f"expected '{expected['duplicate_of']}'")

    # No API call check
    if expected.get("should_use_api") is False:
        max_score += 0.5
        if actual.get("used_api", True) is False:
            total_score += 0.5
            all_passes.append("correctly skipped API call")
        else:
            all_failures.append("used API call when heuristic skip was expected")

    # Reason mentions
    if "reason_must_mention" in expected:
        max_score += 0.5
        reason = actual.get("skip_reason", "") or ""
        s, f, p = _check_text_contains(reason, expected["reason_must_mention"], "skip_reason")
        total_score += s * 0.5
        all_failures.extend(f)
        all_passes.extend(p)

    return ScoreResult(case_id, total_score, max_score, all_failures, all_passes)


# =========================================================================
# Actualize agent scorer
# =========================================================================

def score_actualize(actual: Dict[str, Any], expected: Dict[str, Any]) -> ScoreResult:
    """Score the actualize agent's FDO output."""
    case_id = expected.get("_case_id", "unknown")
    total_score, max_score = 0.0, 0.0
    all_failures, all_passes = [], []

    fdo = actual.get("fdo_draft", {}) or {}
    fdo_id = actual.get("fdo_id", "")

    # ID checks
    if "id_must_be" in expected:
        max_score += 1.5
        if fdo_id == expected["id_must_be"]:
            total_score += 1.5
            all_passes.append(f"correct ID: {fdo_id}")
        else:
            all_failures.append(f"wrong ID: got '{fdo_id}', expected '{expected['id_must_be']}'")

    if "id_must_not_be" in expected:
        max_score += 1.0
        if fdo_id != expected["id_must_not_be"]:
            total_score += 1.0
            all_passes.append(f"ID correctly differs from '{expected['id_must_not_be']}'")
        else:
            all_failures.append(f"ID collision: got '{fdo_id}' (forbidden)")

    if "id_must_contain" in expected:
        max_score += 1.0
        if expected["id_must_contain"] in fdo_id:
            total_score += 1.0
            all_passes.append(f"ID contains '{expected['id_must_contain']}'")
        else:
            all_failures.append(f"ID missing '{expected['id_must_contain']}': got '{fdo_id}'")

    # Title checks
    if "title_must_contain_one_of" in expected:
        max_score += 1.0
        title = fdo.get("title", "").lower()
        matched = any(t.lower() in title for t in expected["title_must_contain_one_of"])
        if matched:
            total_score += 1.0
            all_passes.append("title contains expected term")
        else:
            all_failures.append(f"title '{fdo.get('title', '')}' missing expected terms")

    # Summary length
    summary = fdo.get("summary", "")
    if "summary_min_length" in expected:
        max_score += 0.5
        if len(summary) >= expected["summary_min_length"]:
            total_score += 0.5
            all_passes.append(f"summary length {len(summary)} >= min {expected['summary_min_length']}")
        else:
            all_failures.append(f"summary too short: {len(summary)} < {expected['summary_min_length']}")

    if "summary_max_length" in expected:
        max_score += 0.5
        if len(summary) <= expected["summary_max_length"]:
            total_score += 0.5
            all_passes.append(f"summary length {len(summary)} <= max {expected['summary_max_length']}")
        else:
            all_failures.append(f"summary too long: {len(summary)} > {expected['summary_max_length']}")

    if "summary_must_mention" in expected:
        max_score += 1.0
        s, f, p = _check_text_contains(summary, expected["summary_must_mention"], "summary")
        total_score += s
        all_failures.extend(f)
        all_passes.extend(p)

    # Details length
    if "details_max_length" in expected:
        details = fdo.get("details", "")
        max_score += 0.5
        if len(details) <= expected["details_max_length"]:
            total_score += 0.5
            all_passes.append("details within length bounds")
        else:
            all_failures.append(f"details too long: {len(details)} > {expected['details_max_length']}")

    # Tags
    tags = fdo.get("tags", [])
    if "tags_must_include" in expected:
        max_score += 1.0
        s, f, p = _check_list_contains(tags, expected["tags_must_include"], "tags")
        total_score += s
        all_failures.extend(f)
        all_passes.extend(p)

    if "tags_must_include_some" in expected:
        max_score += 1.0
        found_any = any(
            any(t.lower() in a.lower() or a.lower() in t.lower() for a in tags)
            for t in expected["tags_must_include_some"]
        )
        if found_any:
            total_score += 1.0
            all_passes.append("tags include at least one expected tag")
        else:
            all_failures.append(f"tags missing all of {expected['tags_must_include_some']}")

    if "tags_must_not_include" in expected:
        max_score += 1.0
        s, f, p = _check_list_excludes(tags, expected["tags_must_not_include"], "tags")
        total_score += s
        all_failures.extend(f)
        all_passes.extend(p)

    # Content exclusion (hallucination check)
    if "must_not_contain" in expected:
        max_score += 2.0  # High weight for hallucinations
        all_text = " ".join(str(v) for v in fdo.values() if isinstance(v, str))
        s, f, p = _check_text_excludes(all_text, expected["must_not_contain"], "FDO content")
        total_score += s * 2.0
        all_failures.extend(f)
        all_passes.extend(p)

    # CIP expansion check
    if "if_expands_cip_must_be" in expected:
        max_score += 2.0
        all_text = " ".join(str(v) for v in fdo.values() if isinstance(v, str))
        # Check for wrong expansions
        wrong_cip = [
            "Cardano Improvement Proposal",
            "Content Identifier Protocol",
            "Customer Integration Platform",
        ]
        correct_cip = expected["if_expands_cip_must_be"]
        has_wrong = any(w.lower() in all_text.lower() for w in wrong_cip)
        has_correct = correct_cip.lower() in all_text.lower()

        if has_wrong:
            all_failures.append(f"wrong CIP expansion in output")
        elif has_correct:
            total_score += 2.0
            all_passes.append(f"correctly expanded CIP as '{correct_cip}'")
        else:
            # Didn't expand at all — that's also fine
            total_score += 1.5
            all_passes.append("CIP not expanded (acceptable)")

    # Domain
    if "domain_must_be" in expected:
        max_score += 0.5
        if fdo.get("domain") == expected["domain_must_be"]:
            total_score += 0.5
            all_passes.append(f"correct domain: {expected['domain_must_be']}")
        else:
            all_failures.append(f"wrong domain: got '{fdo.get('domain')}', expected '{expected['domain_must_be']}'")

    # Related
    if "related_must_include" in expected:
        max_score += 1.0
        related = fdo.get("related", [])
        s, f, p = _check_list_contains(related, expected["related_must_include"], "related")
        total_score += s
        all_failures.extend(f)
        all_passes.extend(p)

    return ScoreResult(case_id, total_score, max_score, all_failures, all_passes)


# =========================================================================
# Validate agent scorer
# =========================================================================

def score_validate(actual: Dict[str, Any], expected: Dict[str, Any]) -> ScoreResult:
    """Score the validate agent's output."""
    case_id = expected.get("_case_id", "unknown")
    total_score, max_score = 0.0, 0.0
    all_failures, all_passes = [], []

    validation = actual.get("validation", {})
    fixed_fdo = actual.get("fdo_draft", {}) or {}
    fixes = validation.get("fixes_applied", [])

    # Pass/fail match
    if "passed" in expected:
        max_score += 1.0
        if validation.get("passed") == expected["passed"]:
            total_score += 1.0
            all_passes.append(f"correct pass/fail: {expected['passed']}")
        else:
            all_failures.append(f"wrong pass/fail: got {validation.get('passed')}")

    # Must fix specific issues
    if "must_fix" in expected:
        max_score += len(expected["must_fix"]) * 1.0
        fixes_lower = [f.lower() for f in fixes]
        for issue in expected["must_fix"]:
            found = any(issue.lower() in f for f in fixes_lower)
            if found:
                total_score += 1.0
                all_passes.append(f"caught and fixed: {issue}")
            else:
                all_failures.append(f"missed fix: {issue} (applied fixes: {fixes})")

    # Min fixes count
    if "min_fixes" in expected:
        max_score += 1.0
        if len(fixes) >= expected["min_fixes"]:
            total_score += 1.0
            all_passes.append(f"applied {len(fixes)} fixes >= min {expected['min_fixes']}")
        else:
            all_failures.append(f"too few fixes: {len(fixes)} < {expected['min_fixes']}")

    # Fixed content checks
    all_fixed_text = " ".join(str(v) for v in fixed_fdo.values() if isinstance(v, str))

    if "fixed_summary_must_not_contain" in expected:
        max_score += 1.5
        s, f, p = _check_text_excludes(
            fixed_fdo.get("summary", ""),
            expected["fixed_summary_must_not_contain"],
            "fixed summary",
        )
        total_score += s * 1.5
        all_failures.extend(f)
        all_passes.extend(p)

    if "fixed_must_not_contain" in expected:
        max_score += 1.5
        s, f, p = _check_text_excludes(all_fixed_text, expected["fixed_must_not_contain"], "fixed FDO")
        total_score += s * 1.5
        all_failures.extend(f)
        all_passes.extend(p)

    if "fixed_summary_max_length" in expected:
        max_score += 1.0
        summary = fixed_fdo.get("summary", "")
        if len(summary) <= expected["fixed_summary_max_length"]:
            total_score += 1.0
            all_passes.append(f"fixed summary trimmed to {len(summary)} chars")
        else:
            all_failures.append(
                f"fixed summary still too long: {len(summary)} > {expected['fixed_summary_max_length']}")

    if "fixed_connections_must_contain" in expected:
        max_score += 0.5
        conn = fixed_fdo.get("connections", "")
        if expected["fixed_connections_must_contain"] in conn:
            total_score += 0.5
            all_passes.append("wikilink format fixed")
        else:
            all_failures.append("wikilink format not fixed")

    if "fixed_connections_must_not_contain" in expected:
        max_score += 0.5
        conn = fixed_fdo.get("connections", "")
        if expected["fixed_connections_must_not_contain"] not in conn:
            total_score += 0.5
            all_passes.append("broken wikilink removed")
        else:
            all_failures.append("broken wikilink still present")

    # Error/warning counts
    if "max_errors" in expected:
        max_score += 0.5
        errs = len(validation.get("errors", []))
        if errs <= expected["max_errors"]:
            total_score += 0.5
            all_passes.append(f"errors within limit: {errs} <= {expected['max_errors']}")
        else:
            all_failures.append(f"too many errors: {errs} > {expected['max_errors']}")

    # Removed/kept tags
    if "removed_tags_should_include" in expected:
        max_score += 0.5
        original_tags = [t.lower() for t in (actual.get("_original_tags", []))]
        current_tags = [t.lower() for t in fixed_fdo.get("tags", [])]
        removed = set(original_tags) - set(current_tags)
        for tag in expected["removed_tags_should_include"]:
            if tag.lower() in removed:
                all_passes.append(f"correctly removed generic tag '{tag}'")
            else:
                all_failures.append(f"failed to remove generic tag '{tag}'")
        if all(t.lower() in removed for t in expected["removed_tags_should_include"]):
            total_score += 0.5

    if "kept_tags_should_include" in expected:
        max_score += 0.5
        current_tags = [t.lower() for t in fixed_fdo.get("tags", [])]
        for tag in expected["kept_tags_should_include"]:
            if tag.lower() in current_tags:
                all_passes.append(f"correctly kept tag '{tag}'")
            else:
                all_failures.append(f"incorrectly removed tag '{tag}'")
        if all(t.lower() in current_tags for t in expected["kept_tags_should_include"]):
            total_score += 0.5

    return ScoreResult(case_id, total_score, max_score, all_failures, all_passes)


# =========================================================================
# Crosslink agent scorer
# =========================================================================

def score_crosslink(actual: Dict[str, Any], expected: Dict[str, Any]) -> ScoreResult:
    """Score the crosslink agent's output."""
    case_id = expected.get("_case_id", "unknown")
    total_score, max_score = 0.0, 0.0
    all_failures, all_passes = [], []

    links = actual.get("cross_links", [])
    linked_ids = [l.get("target_fdo_id", "") for l in links]

    # Must link to
    if "must_link_to" in expected:
        max_score += len(expected["must_link_to"]) * 1.0
        for target in expected["must_link_to"]:
            if target in linked_ids:
                total_score += 1.0
                all_passes.append(f"correctly linked to {target}")
            else:
                all_failures.append(f"missing link to {target}")

    # Must not link to
    if "must_not_link_to" in expected:
        max_score += len(expected["must_not_link_to"]) * 1.0
        for target in expected["must_not_link_to"]:
            if target not in linked_ids:
                total_score += 1.0
                all_passes.append(f"correctly did not link to {target}")
            else:
                all_failures.append(f"false positive link to {target}")

    # Link count bounds
    if "min_links" in expected:
        max_score += 0.5
        if len(links) >= expected["min_links"]:
            total_score += 0.5
            all_passes.append(f"link count {len(links)} >= min {expected['min_links']}")
        else:
            all_failures.append(f"too few links: {len(links)} < {expected['min_links']}")

    if "max_links" in expected:
        max_score += 0.5
        if len(links) <= expected["max_links"]:
            total_score += 0.5
            all_passes.append(f"link count {len(links)} <= max {expected['max_links']}")
        else:
            all_failures.append(f"too many links: {len(links)} > {expected['max_links']}")

    return ScoreResult(case_id, total_score, max_score, all_failures, all_passes)


# =========================================================================
# Dispatcher
# =========================================================================

SCORERS = {
    "extract": score_extract,
    "judge": score_judge,
    "actualize": score_actualize,
    "validate": score_validate,
    "crosslink": score_crosslink,
}
