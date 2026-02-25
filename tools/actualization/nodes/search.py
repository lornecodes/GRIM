"""
Search Node — Query vault index for matching FDOs.

Pure function, no Claude call. Uses the VaultIndex to find
existing knowledge that matches the extracted concepts.
"""

from __future__ import annotations

from typing import Any, Dict

from ..state import ActualizationState


def search(state: ActualizationState) -> Dict[str, Any]:
    """
    Search the vault index for FDOs matching extracted concepts.
    Returns matches + formatted context string for prompts.
    """
    concepts = state.get("concepts", [])
    entities = state.get("entities", [])
    vault_index = state["_vault_index"]

    # Combine concepts and entities for search
    search_terms = concepts + entities

    if not search_terms:
        return {
            "vault_matches": [],
            "vault_context": "No concepts extracted — no vault search performed.",
        }

    matches = vault_index.search(search_terms, limit=10)
    context = vault_index.format_for_prompt(matches)

    return {
        "vault_matches": matches,
        "vault_context": context,
    }
