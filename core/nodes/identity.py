"""Identity node — load GRIM's identity from Kronos and personality files.

Runs once at session start. Assembles the system prompt from:
1. identity/system_prompt.md (base personality)
2. identity/personality.yaml (field state values)
3. Kronos grim-identity FDO (extended identity)
4. Personality cache (compiled from grim-personality FDO)
5. Caller context (compiled from people FDO for current caller)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.config import GrimConfig
from core.objectives import load_objectives
from core.personality.cache import compile_personality_cache, is_cache_stale
from core.personality.prompt_builder import build_system_prompt, load_field_state
from core.personality.user_cache import (
    PETER_FALLBACK,
    compile_caller_summary,
    compile_user_cache,
    is_user_cache_stale,
)
from core.state import GrimState

logger = logging.getLogger(__name__)


def make_identity_node(config: GrimConfig, mcp_session: Any = None):
    """Create an identity node closure with config and MCP session."""

    async def identity_node(state: GrimState) -> dict:
        """Load identity and assemble system prompt."""
        logger.info("Identity node: loading personality")

        # Load field state from personality.yaml
        field_state = load_field_state(config.identity_personality_path)

        # Try to enrich from Kronos FDO
        identity_fdo = None
        if mcp_session is not None:
            try:
                result = await mcp_session.call_tool("kronos_get", {"id": "grim-identity"})
                if hasattr(result, "content") and result.content:
                    identity_fdo = json.loads(result.content[0].text)
            except Exception:
                logger.debug("Could not load grim-identity from Kronos — using local only")

        # Compile personality cache if stale or missing
        cache_path = config.personality_cache_path
        if is_cache_stale(cache_path):
            if mcp_session is not None:
                try:
                    result = await mcp_session.call_tool("kronos_get", {"id": "grim-personality"})
                    if hasattr(result, "content") and result.content:
                        personality_fdo = json.loads(result.content[0].text)
                        compile_personality_cache(personality_fdo, cache_path)
                        logger.info("Personality cache compiled from Kronos")
                except Exception:
                    logger.debug("Could not refresh personality cache from Kronos — using existing")
            else:
                logger.debug("No MCP session — using existing personality cache")
        else:
            logger.debug("Personality cache is fresh — skipping Kronos fetch")

        # Resolve caller identity
        caller_id = state.get("caller_id", "peter")
        caller_context = await _resolve_caller(caller_id, mcp_session, config)

        # Load persistent working memory via MCP (falls back to direct file read)
        working_memory = ""
        try:
            if mcp_session is not None:
                result = await mcp_session.call_tool("kronos_memory_read", {})
                if hasattr(result, "content") and result.content:
                    data = json.loads(result.content[0].text)
                    raw_memory = data.get("content", "")
                else:
                    raw_memory = ""
            else:
                # Fallback to direct file read when no MCP session
                from core.memory_store import read_memory
                raw_memory = read_memory(config.vault_path)

            if raw_memory:
                # Truncate to avoid context bloat (keep under 4000 chars)
                if len(raw_memory) > 4000:
                    working_memory = raw_memory[:4000] + "\n\n...(truncated)"
                else:
                    working_memory = raw_memory
                logger.info("Loaded working memory (%d chars)", len(raw_memory))
        except Exception:
            logger.debug("Could not load working memory from vault")

        # Build system prompt (memory positioned early for LLM attention)
        prompt = build_system_prompt(
            prompt_path=config.identity_prompt_path,
            personality_path=config.identity_personality_path,
            field_state=field_state,
            identity_fdo=identity_fdo,
            personality_cache_path=cache_path,
            caller_id=caller_id,
            caller_context=caller_context,
            working_memory=working_memory or None,
        )

        # Load persistent objectives
        objectives = load_objectives(config.objectives_path)
        if objectives:
            active = [o for o in objectives if o.status == "active"]
            logger.info("Loaded %d objectives (%d active)", len(objectives), len(active))

        logger.info(
            "Identity loaded — mode: %s, coherence: %.2f",
            field_state.expression_mode(),
            field_state.coherence,
        )

        return {
            "system_prompt": prompt,
            "field_state": field_state,
            "caller_id": caller_id,
            "caller_context": caller_context,
            "objectives": objectives,
        }

    return identity_node


async def _resolve_caller(
    caller_id: str, mcp_session: Any, config: GrimConfig
) -> str | None:
    """Load caller profile from vault, with cache for the owner (Peter).

    - Peter: disk cache (like personality cache), hourly refresh
    - Services/friends: one-time vault lookup per session, no disk cache
    - Unknown: generic fallback
    """
    if caller_id == "peter":
        return await _resolve_peter(mcp_session, config)

    # Non-Peter caller: try vault lookup
    if mcp_session is not None:
        try:
            result = await mcp_session.call_tool("kronos_get", {"id": caller_id})
            if hasattr(result, "content") and result.content:
                fdo = json.loads(result.content[0].text)
                summary = compile_caller_summary(fdo)
                logger.info("Caller context loaded from vault: %s", caller_id)
                return summary
        except Exception:
            logger.debug("Could not load caller FDO for '%s'", caller_id)

    return f"## Caller: {caller_id}\n\nUnknown caller. Respond helpfully but do not assume familiarity."


async def _resolve_peter(mcp_session: Any, config: GrimConfig) -> str:
    """Resolve Peter's profile with disk cache."""
    user_cache_path = config.personality_cache_path.parent / "user.cache.md"

    if not is_user_cache_stale(user_cache_path):
        content = user_cache_path.read_text(encoding="utf-8").strip()
        # Strip HTML comment header
        lines = content.split("\n")
        body = "\n".join(l for l in lines if not l.strip().startswith("<!--"))
        if body.strip():
            logger.debug("User cache is fresh — skipping Kronos fetch")
            return body.strip()

    # Cache stale or missing — try vault
    if mcp_session is not None:
        try:
            result = await mcp_session.call_tool("kronos_get", {"id": "peter"})
            if hasattr(result, "content") and result.content:
                fdo = json.loads(result.content[0].text)
                compile_user_cache(fdo, user_cache_path)
                logger.info("User cache compiled from Kronos")
                # Read back the compiled cache
                content = user_cache_path.read_text(encoding="utf-8").strip()
                lines = content.split("\n")
                body = "\n".join(l for l in lines if not l.strip().startswith("<!--"))
                return body.strip()
        except Exception:
            logger.debug("Could not refresh user cache from Kronos")

    # Fallback — existing cache or hardcoded
    if user_cache_path.exists():
        content = user_cache_path.read_text(encoding="utf-8").strip()
        lines = content.split("\n")
        body = "\n".join(l for l in lines if not l.strip().startswith("<!--"))
        if body.strip():
            return body.strip()

    logger.info("Using hardcoded Peter fallback")
    return PETER_FALLBACK
