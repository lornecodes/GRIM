"""Agent registry — auto-discovery and registration of doer agents.

Scans core/agents/*.py for modules that export __agent_name__ and
__make_agent__. This eliminates manual agent registration in graph.py.

Also collects agent metadata (display name, role, description, color, tools)
for the UI roster API via __agent_class__ module attributes.
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
    Optionally, __agent_class__ (BaseAgent subclass) is stored for metadata.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable] = {}
        self._classes: dict[str, type] = {}
        self._instances: dict[str, Any] = {}

    def register(self, name: str, factory: Callable, agent_class: type | None = None) -> None:
        """Register an agent factory function.

        Args:
            name: Agent delegation name (e.g., "memory", "code").
            factory: Callable that takes GrimConfig and returns an async agent callable.
            agent_class: Optional BaseAgent subclass for metadata extraction.
        """
        self._factories[name] = factory
        if agent_class is not None:
            self._classes[name] = agent_class
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

    def build_metadata(self, config: GrimConfig) -> list[dict]:
        """Instantiate agent classes and return UI-ready metadata.

        Metadata includes live tool names from the actual agent tool list.
        This should be called once at boot, not per-request.
        """
        result = []
        for name, cls in self._classes.items():
            try:
                instance = cls(config)
                self._instances[name] = instance
                result.append(instance.metadata())
            except Exception:
                logger.warning("Failed to build metadata for agent '%s'", name, exc_info=True)
        return result

    def all_metadata(self) -> list[dict]:
        """Return cached metadata from the last build_metadata() call."""
        return [inst.metadata() for inst in self._instances.values()]

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

            # Store agent class for metadata even if agent is disabled
            agent_class = getattr(module, "__agent_class__", None)

            if agent_name in disabled:
                logger.info("Agent '%s' disabled via config, skipping", agent_name)
                # Still register class for metadata (disabled agents show in roster)
                if agent_class is not None:
                    registry._classes[agent_name] = agent_class
                continue

            registry.register(agent_name, make_fn, agent_class=agent_class)
            logger.info("Discovered agent: %s (from %s)", agent_name, py_file.name)

        return registry

    def __len__(self) -> int:
        return len(self._factories)

    def __contains__(self, name: str) -> bool:
        return name in self._factories

    def __repr__(self) -> str:
        return f"AgentRegistry({len(self._factories)} agents: {', '.join(self.names())})"
