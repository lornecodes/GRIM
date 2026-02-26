"""Identity node — load GRIM's identity from Kronos and personality files.

Runs once at session start. Assembles the system prompt from:
1. identity/system_prompt.md (base personality)
2. identity/personality.yaml (field state values)
3. Kronos grim-identity FDO (extended identity)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.config import GrimConfig
from core.personality.prompt_builder import build_system_prompt, load_field_state
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

        # Build system prompt
        prompt = build_system_prompt(
            prompt_path=config.identity_prompt_path,
            personality_path=config.identity_personality_path,
            field_state=field_state,
            identity_fdo=identity_fdo,
        )

        logger.info(
            "Identity loaded — mode: %s, coherence: %.2f",
            field_state.expression_mode(),
            field_state.coherence,
        )

        return {
            "system_prompt": prompt,
            "field_state": field_state,
        }

    return identity_node
