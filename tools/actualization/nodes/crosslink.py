"""
CrossLink Node — Determine and apply backlinks to existing FDOs.

When a new FDO is created, this node checks which existing FDOs
should link back to it (based on vault match scores).
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..state import ActualizationState, CrossLinkPatch


CROSSLINK_THRESHOLD = 0.7  # Minimum match score to create a backlink


def crosslink(state: ActualizationState) -> Dict[str, Any]:
    """
    Determine which existing FDOs should get backlinks to the new FDO.
    Returns patches to be applied by the commit node.
    """
    fdo = state.get("fdo_draft")
    if not fdo:
        return {"cross_links": []}

    vault_matches = state.get("vault_matches", [])
    fdo_id = fdo.get("id", "")
    summary = fdo.get("summary", "")[:80]

    patches: List[CrossLinkPatch] = []

    for match in vault_matches:
        score = match.get("match_score", 0)
        if score < CROSSLINK_THRESHOLD:
            continue

        # Don't link to self
        if match["id"] == fdo_id:
            continue

        patches.append({
            "target_fdo_id": match["id"],
            "target_fdo_path": match["path"],
            "link_text": f"- [[{fdo_id}]] — {summary}",
        })

    return {"cross_links": patches}
