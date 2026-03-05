"""Tool context — consolidated dependency injection for tool modules.

Instead of scattered module-level globals (set_mcp_session, set_bridge, etc.),
all shared tool dependencies live here. Configured once at boot by graph.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """Shared dependencies for tool modules.

    Configured once at graph boot. Tool modules read from here
    instead of maintaining their own module-level globals.
    """

    mcp_session: Any = None
    workspace_root: Path | None = None
    execution_pool: Any = None  # ExecutionPool instance (Project Charizard)

    def configure(self, **kwargs) -> None:
        """Set multiple fields at once. Only sets non-None values."""
        for key, value in kwargs.items():
            if value is not None and hasattr(self, key):
                setattr(self, key, value)
                logger.info("ToolContext: %s configured", key)

    @property
    def mcp_available(self) -> bool:
        return self.mcp_session is not None


# Module-level singleton — configured once at boot
tool_context = ToolContext()
