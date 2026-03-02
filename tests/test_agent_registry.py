"""Tests for AgentRegistry auto-discovery and registration."""
import pytest
from core.agents.registry import AgentRegistry
from core.config import GrimConfig


class TestAgentRegistry:
    """Test manual registration."""

    def test_register_and_get(self):
        reg = AgentRegistry()
        factory = lambda config: "agent_fn"
        reg.register("test", factory)
        assert reg.get("test") is factory

    def test_names(self):
        reg = AgentRegistry()
        reg.register("a", lambda c: None)
        reg.register("b", lambda c: None)
        assert set(reg.names()) == {"a", "b"}

    def test_all(self):
        reg = AgentRegistry()
        f1 = lambda c: None
        f2 = lambda c: None
        reg.register("a", f1)
        reg.register("b", f2)
        result = reg.all()
        assert result["a"] is f1
        assert result["b"] is f2

    def test_contains(self):
        reg = AgentRegistry()
        reg.register("x", lambda c: None)
        assert "x" in reg
        assert "y" not in reg

    def test_len(self):
        reg = AgentRegistry()
        assert len(reg) == 0
        reg.register("a", lambda c: None)
        assert len(reg) == 1

    def test_get_missing_returns_none(self):
        reg = AgentRegistry()
        assert reg.get("nonexistent") is None

    def test_repr(self):
        reg = AgentRegistry()
        reg.register("a", lambda c: None)
        r = repr(reg)
        assert "1 agents" in r
        assert "a" in r


class TestAgentDiscovery:
    """Test auto-discovery from core/agents/ directory."""

    def test_discovers_all_agents(self, grim_config):
        """Should discover all 6 agents."""
        reg = AgentRegistry.discover(grim_config)
        expected = {"memory", "code", "research", "operate", "audit", "ironclaw"}
        assert set(reg.names()) == expected

    def test_disabled_agents_excluded(self, grim_config):
        """Disabled agents should not appear in registry."""
        reg = AgentRegistry.discover(grim_config, disabled=["ironclaw", "audit"])
        assert "ironclaw" not in reg
        assert "audit" not in reg
        assert "memory" in reg
        assert "code" in reg

    def test_all_disabled(self, grim_config):
        """If all agents are disabled, registry should be empty."""
        all_names = ["memory", "code", "research", "operate", "audit", "ironclaw"]
        reg = AgentRegistry.discover(grim_config, disabled=all_names)
        assert len(reg) == 0

    def test_factories_are_callable(self, grim_config):
        """Each discovered factory should be callable."""
        reg = AgentRegistry.discover(grim_config)
        for name, factory in reg.all().items():
            assert callable(factory), f"Factory for {name} is not callable"

    def test_skips_base_and_registry(self, grim_config):
        """Should not discover base.py or registry.py as agents."""
        reg = AgentRegistry.discover(grim_config)
        assert "base" not in reg
        assert "registry" not in reg

    def test_names_match_delegation_keywords(self, grim_config):
        """Discovered agent names should match router delegation types."""
        from core.nodes.keyword_router import DELEGATION_KEYWORDS
        reg = AgentRegistry.discover(grim_config)
        keyword_types = set(DELEGATION_KEYWORDS.keys())
        discovered = set(reg.names())
        # All keyword types should have a corresponding agent
        assert keyword_types.issubset(discovered), \
            f"Missing agents for: {keyword_types - discovered}"

    def test_discover_with_custom_dir(self, grim_config, tmp_path):
        """Discover from an empty directory should yield empty registry."""
        reg = AgentRegistry.discover(grim_config, agents_dir=tmp_path)
        assert len(reg) == 0

    def test_discover_skips_modules_without_exports(self, grim_config, tmp_path):
        """Modules without __agent_name__ / __make_agent__ are skipped."""
        # __init__.py is always skipped, and files without the attributes are ignored
        init_file = tmp_path / "__init__.py"
        init_file.write_text("")
        dummy = tmp_path / "dummy.py"
        dummy.write_text("x = 1\n")
        reg = AgentRegistry.discover(grim_config, agents_dir=tmp_path)
        assert len(reg) == 0
