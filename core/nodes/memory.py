"""Memory node — query Kronos for relevant knowledge context.

Runs every turn. Analyzes the user message and retrieves
relevant FDOs to ground GRIM's responses.

Smart retrieval: alongside the standard keyword search, also
fetches best-practice FDOs and recent notes from the rolling log.
All three queries run in parallel for minimal latency.

Session knowledge accumulation: FDOs are accumulated across turns
via session_knowledge (LangGraph reducer). Dedup-aware search
re-surfaces cached entries and avoids redundant MCP calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from core.state import FDOSummary, GrimState, KnowledgeEntry

logger = logging.getLogger(__name__)

# Timeout for MCP search calls (seconds).  Semantic search can be slow
# on the first call while the embedding model loads, so we use a generous
# timeout and fall back to keyword-only on failure.
_SEARCH_TIMEOUT = 20


def _query_matches_fdo(query: str, fdo: FDOSummary) -> bool:
    """Check if a query has keyword overlap with an FDO (case-insensitive).

    Returns True if the query shares at least one significant word
    with the FDO's title, tags, or ID.
    """
    query_words = set(query.lower().split())
    # Remove common stop words
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                  "do", "does", "did", "has", "have", "had", "in", "on", "at",
                  "to", "for", "of", "with", "by", "from", "and", "or", "not",
                  "it", "its", "this", "that", "what", "how", "about", "me",
                  "my", "we", "our", "i", "you", "can", "could", "would",
                  "should", "will", "tell", "show", "explain", "describe"}
    query_words -= stop_words

    if not query_words:
        return False

    fdo_words = set()
    fdo_words.update(fdo.id.replace("-", " ").lower().split())
    fdo_words.update(fdo.title.lower().split())
    for tag in fdo.tags:
        fdo_words.update(tag.lower().split())

    return bool(query_words & fdo_words)


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

        turn = state.get("turn_count", 0)
        existing_knowledge = state.get("session_knowledge", [])
        existing_ids = {e.fdo.id for e in existing_knowledge}

        logger.info("Memory node: searching Kronos for '%s' (turn %d, %d cached)",
                     query[:80], turn, len(existing_knowledge))

        # Run three searches in parallel:
        # 1. Standard keyword search (existing behavior)
        # 2. Best practices tagged 'best-practice' (always)
        # 3. Recent notes from rolling logs (always)
        standard_task = _search(mcp_session, query, semantic=False)
        bp_task = _search_best_practices(mcp_session, query)
        notes_task = _fetch_recent_notes(mcp_session)

        results = await asyncio.gather(
            standard_task, bp_task, notes_task,
            return_exceptions=True,
        )
        standard_data, bp_data, notes_data = results

        # Parse standard results
        new_summaries: list[FDOSummary] = []
        if not isinstance(standard_data, Exception) and standard_data:
            results_list = standard_data if isinstance(standard_data, list) else standard_data.get("results", [])
            for item in results_list[:6]:  # reduced from 8 to leave room for BPs
                new_summaries.append(_to_summary(item))

        # Parse best-practice results (deduplicated against standard)
        seen_ids = {s.id for s in new_summaries}
        if not isinstance(bp_data, Exception) and bp_data:
            bp_list = bp_data if isinstance(bp_data, list) else bp_data.get("results", [])
            for item in bp_list[:2]:
                item_id = item.get("id", "")
                if item_id and item_id not in seen_ids:
                    new_summaries.append(_to_summary(item))
                    seen_ids.add(item_id)

        # Re-surface relevant cached entries that match current query
        # (these don't need a fresh MCP call — we already have them)
        resurfaced: list[FDOSummary] = []
        new_ids = {s.id for s in new_summaries}
        for entry in existing_knowledge:
            if entry.fdo.id not in new_ids and _query_matches_fdo(query, entry.fdo):
                resurfaced.append(entry.fdo)
                if len(resurfaced) >= 3:
                    break

        # Combine: fresh results first, then resurfaced from cache
        combined = new_summaries + resurfaced

        # Parse recent notes (separate state key)
        recent_notes: list[dict] = []
        if not isinstance(notes_data, Exception) and notes_data:
            entries = notes_data.get("entries", []) if isinstance(notes_data, dict) else []
            for entry in entries[:5]:
                recent_notes.append({
                    "title": entry.get("title", ""),
                    "date": entry.get("date", ""),
                    "tags": entry.get("tags", []),
                    "body": entry.get("body", "")[:200],
                    "anchor": entry.get("anchor", ""),
                })

        bp_count = len([s for s in combined if "best-practice" in s.tags])
        logger.info(
            "Memory node: %d FDOs (%d fresh + %d cached) + %d BPs + %d notes",
            len(combined),
            len(new_summaries),
            len(resurfaced),
            bp_count,
            len(recent_notes),
        )

        # Build session knowledge entries for accumulation (reducer handles dedup)
        new_entries = [
            KnowledgeEntry(
                fdo=s,
                fetched_turn=turn,
                fetched_by="memory",
                query=query[:100],
                last_referenced_turn=turn,
            )
            for s in combined
        ]

        result: dict[str, Any] = {
            "knowledge_context": combined[:8],       # per-turn (capped at 8)
            "session_knowledge": new_entries,         # accumulated via reducer
            "turn_count": turn + 1,                  # bump turn counter
        }
        if recent_notes:
            result["recent_notes"] = recent_notes
        return result

    return memory_node


def _to_summary(item: dict) -> FDOSummary:
    """Convert a search result dict to FDOSummary."""
    return FDOSummary(
        id=item.get("id", ""),
        title=item.get("title", ""),
        domain=item.get("domain", ""),
        status=item.get("status", ""),
        confidence=item.get("confidence", 0.0),
        summary=item.get("summary", item.get("body", "")[:300]),
        tags=item.get("tags", []),
        related=item.get("related", []),
    )


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


async def _search_best_practices(mcp_session: Any, query: str) -> dict | list | None:
    """Search for best-practice FDOs relevant to the query."""
    try:
        bp_query = f"best-practice {query}"
        return await _search(mcp_session, bp_query, semantic=False)
    except Exception:
        logger.debug("Best-practice search failed", exc_info=True)
        return None


async def _fetch_recent_notes(mcp_session: Any) -> dict | None:
    """Fetch recent notes from rolling logs."""
    try:
        result = await asyncio.wait_for(
            mcp_session.call_tool(
                "kronos_notes_recent",
                {"days": 30, "max_entries": 5},
            ),
            timeout=_SEARCH_TIMEOUT,
        )
        if hasattr(result, "content") and result.content:
            return json.loads(result.content[0].text)
        return None
    except asyncio.TimeoutError:
        logger.debug("Recent notes fetch timed out")
        return None
    except Exception:
        logger.debug("Recent notes fetch failed", exc_info=True)
        return None
