"""Read+Write Kronos tools — for the Memory Agent (doer).

These tools can create and modify FDOs in the Kronos vault.
Only the Memory Agent should use these, following skill protocols.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from core.tools.kronos_read import COMPANION_TOOLS, _call_mcp

logger = logging.getLogger(__name__)


@tool
async def kronos_create(
    id: str,
    title: str,
    domain: str,
    body: str,
    tags: list[str] | None = None,
    status: str = "seed",
    confidence: float = 0.5,
) -> str:
    """Create a new FDO in the Kronos vault.

    Args:
        id: Kebab-case identifier (e.g., "new-concept-name").
        title: Human-readable title.
        domain: Target domain (physics, ai-systems, computing, etc.).
        body: Full markdown body content.
        tags: Optional list of tags.
        status: FDO status — usually "seed" for new FDOs.
        confidence: Initial confidence (0.0-1.0), default 0.5.

    Returns:
        JSON string with creation result.
    """
    fields: dict[str, Any] = {
        "title": title,
        "domain": domain,
        "body": body,
        "status": status,
        "confidence": confidence,
    }
    if tags:
        fields["tags"] = tags

    result = await _call_mcp("kronos_create", id=id, fields=fields)
    return json.dumps(result, indent=2)


@tool
async def kronos_update(
    id: str,
    body: str | None = None,
    status: str | None = None,
    confidence: float | None = None,
    tags: list[str] | None = None,
    related: list[str] | None = None,
) -> str:
    """Update an existing FDO in the Kronos vault.

    Args:
        id: The FDO identifier to update.
        body: New body content (replaces existing).
        status: New status (seed, developing, stable, archived).
        confidence: Updated confidence (0.0-1.0).
        tags: Updated tags list.
        related: Updated related FDO IDs list.

    Returns:
        JSON string with update result.
    """
    fields: dict[str, Any] = {}
    if body is not None:
        fields["body"] = body
    if status is not None:
        fields["status"] = status
    if confidence is not None:
        fields["confidence"] = confidence
    if tags is not None:
        fields["tags"] = tags
    if related is not None:
        fields["related"] = related

    if not fields:
        return json.dumps({"error": "No fields to update"})

    result = await _call_mcp("kronos_update", id=id, fields=fields)
    return json.dumps(result, indent=2)


# All tools available to the Memory Agent: read + write
MEMORY_AGENT_TOOLS = [
    *COMPANION_TOOLS,
    kronos_create,
    kronos_update,
]
