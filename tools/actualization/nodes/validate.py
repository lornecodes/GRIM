"""
Validate Node — Quality gate for FDOs.

Catches the issues we found in v1:
- Acronym hallucination (CIP ≠ Cardano Improvement Proposal)
- Over-analysis of empty/trivial files
- Generic tags
- Absolute path leaks
- Malformed wikilinks

Uses a mix of rule-based checks (fast) and Claude review (for ambiguous cases).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from ..prompts import VALIDATE_SYSTEM, VALIDATE_USER
from ..state import ActualizationState

# Tags that are too generic to be useful
GENERIC_TAGS = {
    "code", "file", "data", "content", "document", "text",
    "configuration", "config", "information", "general",
    "project", "module", "script", "source",
}

# Max retry attempts for validation failures
MAX_RETRIES = 2


def validate(state: ActualizationState) -> Dict[str, Any]:
    """
    Quality gate for FDO drafts.

    Phase 1: Rule-based checks (fast, no API call)
    Phase 2: Claude review (only if Phase 1 passes or for edge cases)
    """
    fdo = state.get("fdo_draft")
    if not fdo:
        return {
            "validation": {
                "passed": False,
                "errors": ["No FDO draft to validate"],
                "warnings": [],
                "fixes_applied": [],
            }
        }

    meta = state.get("current_meta", {})
    errors: List[str] = []
    warnings: List[str] = []
    fixes_applied: List[str] = []

    # Make a mutable copy
    fdo = dict(fdo)

    # ---- Phase 1: Rule-based checks ----

    # 1. Fix absolute path leaks
    for field in ["summary", "details", "connections", "references"]:
        val = fdo.get(field, "")
        if isinstance(val, str) and re.search(r'[A-Z]:\\|/home/|/Users/', val):
            # Replace absolute paths with relative
            val = re.sub(
                r'(?:[A-Z]:\\[^\s]*\\|/home/[^\s]*/|/Users/[^\s]*/)',
                '',
                val,
            )
            fdo[field] = val
            fixes_applied.append(f"Removed absolute path from {field}")

    # 2. Filter generic tags
    tags = fdo.get("tags", [])
    original_tag_count = len(tags)
    tags = [t for t in tags if t.lower() not in GENERIC_TAGS]
    if len(tags) < original_tag_count:
        fixes_applied.append(f"Removed {original_tag_count - len(tags)} generic tags")
    fdo["tags"] = tags

    # 3. Check for empty/stub content
    content_len = len(state.get("current_content", "").strip())
    summary_len = len(fdo.get("summary", ""))
    details_len = len(fdo.get("details", ""))

    if content_len < 50 and (summary_len > 200 or details_len > 300):
        # Over-analysis detected
        fdo["summary"] = fdo["summary"][:100] + "..." if summary_len > 100 else fdo["summary"]
        fdo["details"] = ""
        fixes_applied.append("Trimmed over-analysis of trivial content")

    # 4. Check wikilink format
    connections = fdo.get("connections", "")
    bad_links = re.findall(r'\[\[[^\]]*\s[^\]]*\]\]', connections)
    if bad_links:
        # Fix spaces in wikilinks
        for bad in bad_links:
            fixed = "[[" + bad[2:-2].replace(" ", "-").lower() + "]]"
            connections = connections.replace(bad, fixed)
        fdo["connections"] = connections
        fixes_applied.append(f"Fixed {len(bad_links)} wikilinks with spaces")

    # 5. Ensure ID is valid
    fdo_id = fdo.get("id", "")
    if not fdo_id or " " in fdo_id:
        errors.append("Invalid FDO ID")

    # 6. Ensure title exists and isn't just the filename
    title = fdo.get("title", "")
    if not title or len(title) < 3:
        errors.append("Title is empty or too short")

    # ---- Phase 2: Claude validation (only for non-trivial content) ----

    retry_count = state.get("retry_count", 0)
    use_claude = (
        content_len > 200  # Worth validating
        and retry_count < MAX_RETRIES  # Haven't retried too many times
        and not errors  # Rule-based checks passed
    )

    if use_claude:
        claude_result = _claude_validate(state, fdo)
        if claude_result:
            if not claude_result.get("passed", True):
                claude_errors = claude_result.get("errors", [])
                errors.extend(claude_errors)

            warnings.extend(claude_result.get("warnings", []))

            # Apply suggested fixes
            suggested = claude_result.get("suggested_fixes", {})
            if suggested:
                if suggested.get("title") and suggested["title"] != fdo.get("title"):
                    fdo["title"] = suggested["title"]
                    fixes_applied.append(f"Title corrected by validator")
                if suggested.get("summary") and suggested["summary"] != fdo.get("summary"):
                    fdo["summary"] = suggested["summary"]
                    fixes_applied.append(f"Summary corrected by validator")
                if suggested.get("tags"):
                    fdo["tags"] = [t for t in suggested["tags"] if t.lower() not in GENERIC_TAGS]
                    fixes_applied.append(f"Tags corrected by validator")

    passed = len(errors) == 0

    return {
        "fdo_draft": fdo,
        "validation": {
            "passed": passed,
            "errors": errors,
            "warnings": warnings,
            "fixes_applied": fixes_applied,
        },
        "retry_count": retry_count + (0 if passed else 1),
    }


def _claude_validate(state: ActualizationState, fdo: Dict) -> Dict | None:
    """Call Claude to review the FDO for quality issues."""
    meta = state.get("current_meta", {})
    fdo_json = json.dumps(fdo, indent=2)

    prompt = VALIDATE_USER.format(
        fdo_id=fdo.get("id", "unknown"),
        source_path=meta.get("path", "unknown"),
        source_type=meta.get("source_type", "file"),
        fdo_json=fdo_json,
    )

    client = state["_client"]
    try:
        resp = client.messages.create(
            model=state["_model"],
            max_tokens=500,
            temperature=0.1,
            system=VALIDATE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()

        # Update token tracking (but since this modifies state indirectly,
        # the graph runner handles it via the returned dict)

        return _parse_json(text)
    except Exception:
        return None


def _parse_json(text: str) -> Dict | None:
    if not text:
        return None
    cleaned = text
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```\w*\n?', '', cleaned)
        cleaned = re.sub(r'\n?```$', '', cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None
