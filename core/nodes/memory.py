"""Memory node — query Kronos for relevant knowledge context.

Runs every turn. Analyzes the user message and retrieves
relevant FDOs to ground GRIM's responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from core.state import FDOSummary, GrimState

logger = logging.getLogger(__name__)

# Timeout for MCP search calls (seconds).  Semantic search can be slow
# on the first call while the embedding model loads, so we use a generous
# timeout and fall back to keyword-only on failure.
_SEARCH_TIMEOUT = 20


def make_memory_node(mcp_session: Any = None):
    """Create a memory node closure with MCP session."""

    async def memory_node(state: GrimState) -> dict:
        """Query Kronos for knowledge relevant to the current message."""
        messages = state.get("messages", [])
        if not messages:
            return {"knowledge_context": []}

        # Extract the latest user message
        last_msg = messages[-1]
        query = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        if not query or not mcp_session:
            return {"knowledge_context": []}

        logger.info("Memory node: searching Kronos for '%s'", query[:80])

        # Try keyword search first (fast); fall back gracefully
        data = await _search(mcp_session, query, semantic=False)
        if data is None:
            return {"knowledge_context": []}

        # Parse search results into FDOSummary objects
        summaries: list[FDOSummary] = []
        results_list = data if isinstance(data, list) else data.get("results", [])

        for item in results_list[:8]:  # cap at 8 for context window
            summaries.append(
                FDOSummary(
                    id=item.get("id", ""),
                    title=item.get("title", ""),
                    domain=item.get("domain", ""),
                    status=item.get("status", ""),
                    confidence=item.get("confidence", 0.0),
                    summary=item.get("summary", item.get("body", "")[:300]),
                    tags=item.get("tags", []),
                    related=item.get("related", []),
                )
            )

        logger.info("Memory node: found %d relevant FDOs", len(summaries))
        return {"knowledge_context": summaries}

    return memory_node


async def _search(mcp_session: Any, query: str, *, semantic: bool) -> dict | list | None:
    """Call kronos_search with a timeout. Returns parsed JSON or None."""
    try:
        result = await asyncio.wait_for(
            mcp_session.call_tool(
                "kronos_search",
                {"query": query, "semantic": semantic},
            ),
            timeout=_SEARCH_TIMEOUT,
        )
        if not (hasattr(result, "content") and result.content):
            return None
        return json.loads(result.content[0].text)
    except asyncio.TimeoutError:
        logger.warning("Memory node: search timed out (semantic=%s)", semantic)
        return None
    except Exception:
        logger.exception("Memory node: Kronos search failed")
        return None
