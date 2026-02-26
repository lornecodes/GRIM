"""Read-only Kronos tools — for the GRIM companion (thinker).

These tools query the Kronos vault via MCP but never write to it.
The companion uses these to ground its responses in knowledge.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# MCP client reference — set at boot by graph.py
_mcp_session: Any = None
_MCP_TIMEOUT = 15  # seconds per MCP call


def set_mcp_session(session: Any) -> None:
    """Inject the MCP client session (called once at boot)."""
    global _mcp_session
    _mcp_session = session


def get_mcp_session() -> Any:
    """Return the current MCP session (for diagnostics)."""
    return _mcp_session


async def _call_mcp(method: str, **kwargs: Any) -> Any:
    """Call a Kronos MCP tool and return the result."""
    if _mcp_session is None:
        logger.warning("MCP session not initialized")
        return {"error": "Kronos vault is not connected."}

    t0 = time.monotonic()
    logger.info("MCP call START: %s(%s)", method, list(kwargs.keys()))

    try:
        result = await asyncio.wait_for(
            _mcp_session.call_tool(method, kwargs),
            timeout=_MCP_TIMEOUT,
        )
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        logger.warning("MCP call %s TIMEOUT after %.1fs", method, elapsed)
        return {"error": f"Kronos {method} timed out ({elapsed:.0f}s). Use knowledge already in context."}

    elapsed = time.monotonic() - t0
    logger.info("MCP call %s OK in %.1fs", method, elapsed)
    # MCP returns content as list of TextContent objects
    if hasattr(result, "content") and result.content:
        text = result.content[0].text
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return {"text": text}
    return {}


@tool
async def kronos_search(query: str, semantic: bool = False) -> str:
    """Search the Kronos knowledge vault for relevant FDOs.

    Args:
        query: Search terms — use specific vocabulary for precision.
        semantic: Enable semantic (embedding) search in addition to keyword.
                  Default False (keyword-only is fast; semantic is slower but more nuanced).

    Returns:
        JSON string with matching FDOs (id, title, domain, confidence, summary).
    """
    result = await _call_mcp("kronos_search", query=query, semantic=semantic)
    return json.dumps(result, indent=2)


@tool
async def kronos_get(id: str) -> str:
    """Retrieve a specific FDO by ID from the Kronos vault.

    Args:
        id: The kebab-case FDO identifier (e.g., "pac-framework", "grim-architecture").

    Returns:
        JSON string with the full FDO content.
    """
    result = await _call_mcp("kronos_get", id=id)
    return json.dumps(result, indent=2)


@tool
async def kronos_list(domain: str | None = None) -> str:
    """List FDOs in the Kronos vault, optionally filtered by domain.

    Args:
        domain: Optional domain filter (e.g., "physics", "ai-systems", "projects").

    Returns:
        JSON string with list of FDO summaries.
    """
    kwargs = {}
    if domain:
        kwargs["domain"] = domain
    result = await _call_mcp("kronos_list", **kwargs)
    return json.dumps(result, indent=2)


# Convenience: all read-only tools as a list for LangGraph binding
COMPANION_TOOLS = [kronos_search, kronos_get, kronos_list]
