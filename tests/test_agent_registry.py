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
        """Should discover all 5 agents (audit removed)."""
        reg = AgentRegistry.discover(grim_config)
        expected = {"memory", "code", "research", "operate", "codebase"}
        assert set(reg.names()) == expected

    def test_default_config_disables_code(self):
        """Default config disables 'code' agent (v0.0.6: code ops delegated)."""
        config = GrimConfig()
        assert "code" in config.agents_disabled

    def test_code_agent_excluded_with_default_config(self):
        """With default agents_disabled, code agent is not in registry."""
        config = GrimConfig()
        reg = AgentRegistry.discover(config, disabled=config.agents_disabled)
        assert "code" not in reg
        # Planning is now a graph-level branch, not a dispatched agent
        assert "planning" not in reg

    def test_planning_agent_not_discovered(self, grim_config):
        """Planning agent is deprecated — superseded by planning_companion graph node."""
        reg = AgentRegistry.discover(grim_config)
        assert "planning" not in reg

    def test_disabled_agents_excluded(self, grim_config):
        """Disabled agents should not appear in registry."""
        reg = AgentRegistry.discover(grim_config, disabled=["memory", "research"])
        assert "memory" not in reg
        assert "research" not in reg
        assert "operate" in reg

    def test_all_disabled(self, grim_config):
        """If all agents are disabled, registry should be empty."""
        all_names = ["memory", "code", "research", "operate", "planning", "codebase"]
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


class TestAgentMetadata:
    """Test dynamic agent metadata from registry."""

    def test_discover_stores_agent_classes(self, grim_config):
        """discover() stores __agent_class__ for agents that export it."""
        reg = AgentRegistry.discover(grim_config)
        assert len(reg._classes) > 0
        assert "memory" in reg._classes
        assert "research" in reg._classes

    def test_build_metadata_returns_list(self, grim_config):
        """build_metadata() returns a list of metadata dicts."""
        reg = AgentRegistry.discover(grim_config)
        metadata = reg.build_metadata(grim_config)
        assert isinstance(metadata, list)
        assert len(metadata) > 0

    def test_metadata_has_required_keys(self, grim_config):
        """Each metadata dict has all required keys for the UI."""
        reg = AgentRegistry.discover(grim_config)
        metadata = reg.build_metadata(grim_config)
        required_keys = {"id", "name", "role", "description", "tools", "color", "tier", "toggleable"}
        for meta in metadata:
            assert required_keys.issubset(meta.keys()), f"Missing keys in {meta.get('id')}: {required_keys - meta.keys()}"

    def test_metadata_tools_are_real_names(self, grim_config):
        """Tools in metadata are actual tool names, not placeholders."""
        reg = AgentRegistry.discover(grim_config)
        metadata = reg.build_metadata(grim_config)
        for meta in metadata:
            assert isinstance(meta["tools"], list)
            for tool_name in meta["tools"]:
                assert isinstance(tool_name, str) and len(tool_name) > 0

    def test_all_metadata_matches_build(self, grim_config):
        """all_metadata() returns cached metadata after build_metadata()."""
        reg = AgentRegistry.discover(grim_config)
        built = reg.build_metadata(grim_config)
        cached = reg.all_metadata()
        assert len(built) == len(cached)

    def test_disabled_agents_still_get_class_stored(self):
        """Disabled agents should still have their class stored for metadata."""
        config = GrimConfig(agents_disabled=["code"])
        reg = AgentRegistry.discover(config, disabled=config.agents_disabled)
        assert "code" in reg._classes
        assert "code" not in reg

    def test_register_with_class(self):
        """register() stores agent_class when provided."""
        from core.agents.base import BaseAgent
        reg = AgentRegistry()
        reg.register("test", lambda c: None, agent_class=BaseAgent)
        assert "test" in reg._classes
        assert reg._classes["test"] is BaseAgent


class TestNodeMetadata:
    """Test graph node metadata for companion nodes."""

    def test_graph_node_metadata_exists(self):
        """GRAPH_NODE_METADATA should contain entries for all companion nodes."""
        from core.nodes.metadata import GRAPH_NODE_METADATA
        ids = {m["id"] for m in GRAPH_NODE_METADATA}
        assert "companion" in ids
        assert "personal_companion" in ids
        assert "planning_companion" in ids

    def test_graph_node_metadata_schema(self):
        """Each node metadata entry has required keys."""
        from core.nodes.metadata import GRAPH_NODE_METADATA
        required_keys = {"id", "name", "role", "description", "tools", "color", "tier", "toggleable"}
        for meta in GRAPH_NODE_METADATA:
            assert required_keys.issubset(meta.keys()), f"Missing keys in {meta.get('id')}"

    def test_companion_has_tools(self):
        """Companion node metadata includes actual tool names."""
        from core.nodes.companion import NODE_METADATA
        assert len(NODE_METADATA["tools"]) > 0
        assert all(isinstance(t, str) for t in NODE_METADATA["tools"])

    def test_planning_has_task_tools(self):
        """Planning node metadata includes task management tools."""
        from core.nodes.planning_companion import NODE_METADATA
        tool_names = NODE_METADATA["tools"]
        assert any("task" in t for t in tool_names), f"No task tools in: {tool_names}"

    def test_all_nodes_grim_tier(self):
        """All companion nodes are grim tier, not toggleable."""
        from core.nodes.metadata import GRAPH_NODE_METADATA
        for meta in GRAPH_NODE_METADATA:
            assert meta["tier"] == "grim"
            assert meta["toggleable"] is False
