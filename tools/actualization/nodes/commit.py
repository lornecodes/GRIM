"""
Commit Node — Write FDO to vault and update live index.

The final node in the chain. Writes the FDO markdown file,
applies cross-link patches, and registers the new FDO in the
vault index so subsequent chunks can find it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ..state import ActualizationState


def commit(state: ActualizationState) -> Dict[str, Any]:
    """
    Write FDO to vault, apply cross-links, register in index.
    """
    fdo = state.get("fdo_draft")
    if not fdo:
        return {}

    source_id = state.get("source_id", "unknown")
    meta = state.get("current_meta", {})
    chunk_path = meta.get("path", "")

    writer = state["_writer"]
    vault_index = state["_vault_index"]
    crosslinker = state["_crosslinker"]

    # Compute vault-relative directory
    from ..nodes.actualize import slugify
    repo_slug = slugify(source_id)
    chunk_parent = str(Path(chunk_path).parent).replace("\\", "/")
    if chunk_parent == ".":
        vault_rel_dir = Path("repos") / repo_slug
    else:
        vault_rel_dir = Path("repos") / repo_slug / chunk_parent

    # Write the FDO
    fdo_vault_path = writer.write(fdo, vault_rel_dir)

    # Apply cross-link patches
    cross_links = state.get("cross_links", [])
    links_applied = 0
    for patch in cross_links:
        if crosslinker.patch(patch["target_fdo_path"], patch["link_text"]):
            links_applied += 1

    # Register in live index
    vault_index.register({
        "id": fdo["id"],
        "title": fdo.get("title", ""),
        "domain": fdo.get("domain", ""),
        "status": "seed",
        "confidence": fdo.get("confidence", 0.3),
        "summary": fdo.get("summary", "")[:300],
        "tags": fdo.get("tags", []),
        "concepts": [],
        "related": fdo.get("related", []),
        "source_repos": fdo.get("source_repos", []),
        "path": fdo_vault_path,
    })

    # Update accumulators
    fdos_created = list(state.get("fdos_created", []))
    fdos_created.append(fdo["id"])

    return {
        "fdos_created": fdos_created,
        "_last_fdo_path": fdo_vault_path,
        "_last_links_applied": links_applied,
    }
