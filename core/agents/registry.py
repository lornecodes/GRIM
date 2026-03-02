"""Agent registry — auto-discovery and registration of doer agents.

Scans core/agents/*.py for modules that export __agent_name__ and
__make_agent__. This eliminates manual agent registration in graph.py.
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any, Callable

from core.config import GrimConfig

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Registry of available doer agents.

    Agents are discovered from core/agents/ by looking for modules
    that export __agent_name__ (str) and __make_agent__ (factory fn).
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable] = {}

    def register(self, name: str, factory: Callable) -> None:
        """Register an agent factory function.

        Args:
            name: Agent delegation name (e.g., "memory", "code").
            factory: Callable that takes GrimConfig and returns an async agent callable.
        """
        self._factories[name] = factory
        logger.debug("Agent registered: %s", name)

    def get(self, name: str) -> Callable | None:
        """Get an agent factory by name."""
        return self._factories.get(name)

    def all(self) -> dict[str, Callable]:
        """Return all registered agent factories."""
        return dict(self._factories)

    def names(self) -> list[str]:
        """Return all registered agent names."""
        return list(self._factories.keys())

    @classmethod
    def discover(
        cls,
        config: GrimConfig,
        agents_dir: Path | None = None,
        disabled: list[str] | None = None,
    ) -> "AgentRegistry":
        """Auto-discover agents from the agents directory.

        Scans for Python modules with __agent_name__ and __make_agent__
        attributes. Skips base.py, __init__.py, and registry.py.

        Args:
            config: GrimConfig (unused currently, reserved for future filtering).
            agents_dir: Directory to scan. Defaults to core/agents/.
            disabled: List of agent names to skip (from config.agents_disabled).
        """
        registry = cls()
        disabled = disabled or []

        if agents_dir is None:
            agents_dir = Path(__file__).parent

        for py_file in sorted(agents_dir.glob("*.py")):
            if py_file.name in ("__init__.py", "base.py", "registry.py"):
                continue

            module_name = f"core.agents.{py_file.stem}"
            try:
                module = importlib.import_module(module_name)
            except Exception:
                logger.warning("Failed to import agent module: %s", module_name, exc_info=True)
                continue

            agent_name = getattr(module, "__agent_name__", None)
            make_fn = getattr(module, "__make_agent__", None)

            if agent_name is None or make_fn is None:
                continue

            if agent_name in disabled:
                logger.info("Agent '%s' disabled via config, skipping", agent_name)
                continue

            registry.register(agent_name, make_fn)
            logger.info("Discovered agent: %s (from %s)", agent_name, py_file.name)

        return registry

    def __len__(self) -> int:
        return len(self._factories)

    def __contains__(self, name: str) -> bool:
        return name in self._factories

    def __repr__(self) -> str:
        return f"AgentRegistry({len(self._factories)} agents: {', '.join(self.names())})"
