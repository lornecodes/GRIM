"""Service smoke tests — verify all imports resolve and key objects construct.

These tests catch broken imports, missing modules, and misconfigured registries
after dead code cleanup or refactoring. They don't need MCP, Docker, or APIs.
"""

import importlib
import pytest
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════
# 1. Import Resolution — no broken imports after cleanup
# ═══════════════════════════════════════════════════════════════════════════


class TestImportResolution:
    """Verify all production modules can be imported without errors."""

    CORE_MODULES = [
        "core.config",
        "core.state",
        "core.graph",
        "core.graph_topology",
        "core.model_router",
        "core.client",
    ]

    NODE_MODULES = [
        "core.nodes.identity",
        "core.nodes.compress",
        "core.nodes.memory",
        "core.nodes.companion",
        "core.nodes.personal_companion",
        "core.nodes.planning_companion",
        "core.nodes.companion_router",
        "core.nodes.router",
        "core.nodes.keyword_router",
        "core.nodes.intent_classifier",
        "core.nodes.integrate",
        "core.nodes.evolve",
        "core.nodes.skill_match",
        "core.nodes.metadata",
    ]

    AGENT_MODULES = [
        "core.agents.base",
        "core.agents.registry",
        "core.agents.codebase_agent",
        "core.agents.memory_agent",
        "core.agents.operator_agent",
        "core.agents.research_agent",
    ]

    TOOL_MODULES = [
        "core.tools.context",
        "core.tools.registry",
        "core.tools.kronos_read",
        "core.tools.kronos_write",
        "core.tools.kronos_tasks",
        "core.tools.memory_tools",
        "core.tools.pool_tools",
    ]

    SKILL_MODULES = [
        "core.skills.loader",
        "core.skills.registry",
    ]

    CLIENT_MODULES = [
        "clients.discord_bot",
    ]

    SERVER_MODULES = [
        "server.app",
    ]

    @pytest.mark.parametrize("module", CORE_MODULES)
    def test_core_imports(self, module):
        importlib.import_module(module)

    @pytest.mark.parametrize("module", NODE_MODULES)
    def test_node_imports(self, module):
        importlib.import_module(module)

    @pytest.mark.parametrize("module", AGENT_MODULES)
    def test_agent_imports(self, module):
        importlib.import_module(module)

    @pytest.mark.parametrize("module", TOOL_MODULES)
    def test_tool_imports(self, module):
        importlib.import_module(module)

    @pytest.mark.parametrize("module", SKILL_MODULES)
    def test_skill_imports(self, module):
        importlib.import_module(module)

    @pytest.mark.parametrize("module", CLIENT_MODULES)
    def test_client_imports(self, module):
        importlib.import_module(module)

    @pytest.mark.parametrize("module", SERVER_MODULES)
    def test_server_imports(self, module):
        importlib.import_module(module)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Object Construction — key objects build without errors
# ═══════════════════════════════════════════════════════════════════════════


class TestObjectConstruction:
    """Verify key objects can be instantiated with defaults."""

    def test_grim_config_constructs(self, grim_config):
        """GrimConfig should construct with test defaults."""
        assert grim_config is not None
        assert grim_config.env == "debug"

    def test_grim_client_constructs(self, grim_config):
        """GrimClient should construct without API key (won't connect, but won't crash)."""
        from core.client import GrimClient
        client = GrimClient(config=grim_config)
        assert client is not None
        assert client.config == grim_config

    def test_build_graph_constructs(self, grim_config):
        """build_graph should produce a compiled graph without MCP."""
        from core.graph import build_graph
        graph = build_graph(grim_config, mcp_session=None)
        assert graph is not None

    def test_tool_context_singleton(self):
        """ToolContext singleton should exist and be configurable."""
        from core.tools.context import tool_context
        assert tool_context is not None
        assert hasattr(tool_context, "mcp_session")
        assert hasattr(tool_context, "workspace_root")
        assert hasattr(tool_context, "execution_pool")

    def test_agent_registry_discovers_agents(self, grim_config):
        """Agent registry should discover all active agents."""
        from core.agents.registry import AgentRegistry
        registry = AgentRegistry.discover(config=grim_config)
        names = set(registry.names())
        expected = {"memory", "code", "research", "operate", "codebase"}
        assert names == expected, f"Discovered: {names}, expected: {expected}"

    def test_tool_registry_has_groups(self):
        """Tool registry should have registered tool groups."""
        from core.tools.registry import tool_registry
        groups = tool_registry.groups()
        assert len(groups) >= 4, f"Only {len(groups)} tool groups: {groups}"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Tool Lists — verify tool lists are populated
# ═══════════════════════════════════════════════════════════════════════════


class TestToolLists:
    """Verify tool and MCP tool lists are correctly populated."""

    def test_kronos_tools_populated(self):
        """KRONOS_TOOLS should have MCP tool definitions."""
        from core.client import KRONOS_TOOLS
        assert len(KRONOS_TOOLS) >= 5, f"Only {len(KRONOS_TOOLS)} Kronos tools"

    def test_pool_tools_populated(self):
        """POOL_TOOLS should have pool tool definitions."""
        from core.client import POOL_TOOLS
        assert len(POOL_TOOLS) >= 1, f"Only {len(POOL_TOOLS)} pool tools"

    def test_discord_allowed_tools_populated(self):
        """DISCORD_ALLOWED_TOOLS should filter correctly."""
        from clients.discord_bot import DISCORD_ALLOWED_TOOLS
        assert len(DISCORD_ALLOWED_TOOLS) >= 3, f"Only {len(DISCORD_ALLOWED_TOOLS)} Discord tools"

    def test_no_ironclaw_tools_remain(self):
        """No ironclaw tool references should remain in active tool lists."""
        from core.tools.registry import tool_registry
        all_tools = []
        for group in tool_registry.groups():
            all_tools.extend(tool_registry.get_group(group))
        tool_names = [t.name if hasattr(t, 'name') else str(t) for t in all_tools]
        for name in tool_names:
            assert "ironclaw" not in name.lower(), f"Dead ironclaw tool found: {name}"
            assert "staging" not in name.lower(), f"Dead staging tool found: {name}"


# ═══════════════════════════════════════════════════════════════════════════
# 4. Graph Structure — verify the graph is well-formed after cleanup
# ═══════════════════════════════════════════════════════════════════════════


class TestGraphStructure:
    """Verify the LangGraph state graph is well-formed."""

    def test_graph_node_count(self, grim_config):
        """Graph should have exactly 11 nodes (no dispatch pipeline)."""
        from core.graph import build_graph
        graph = build_graph(grim_config, mcp_session=None)
        nodes = list(graph.get_graph().nodes.keys())
        # Exclude __start__ and __end__
        real_nodes = [n for n in nodes if not n.startswith("__")]
        assert len(real_nodes) == 11, f"Expected 11 nodes, got {len(real_nodes)}: {real_nodes}"

    def test_no_dispatch_nodes(self, grim_config):
        """Dispatch/audit pipeline nodes should be gone."""
        from core.graph import build_graph
        graph = build_graph(grim_config, mcp_session=None)
        nodes = set(graph.get_graph().nodes.keys())
        dead_nodes = {"dispatch", "audit_gate", "audit", "re_dispatch"}
        present = dead_nodes & nodes
        assert not present, f"Dead nodes still in graph: {present}"

    def test_no_ironclaw_delegation_keywords(self):
        """DELEGATION_KEYWORDS should not have 'ironclaw' or 'audit' keys."""
        from core.nodes.keyword_router import DELEGATION_KEYWORDS
        assert "ironclaw" not in DELEGATION_KEYWORDS, "ironclaw keyword group still exists"
        assert "audit" not in DELEGATION_KEYWORDS, "audit keyword group still exists"

    def test_operate_keywords_include_code_ops(self):
        """'operate' keywords should include former ironclaw code ops."""
        from core.nodes.keyword_router import DELEGATION_KEYWORDS
        operate_kws = DELEGATION_KEYWORDS.get("operate", [])
        # These were ironclaw keywords, now merged into operate
        assert any("write code" in kw for kw in operate_kws), "Missing code ops keywords"
        assert any("git" in kw for kw in operate_kws), "Missing git keywords"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Skill System — verify skills loaded correctly
# ═══════════════════════════════════════════════════════════════════════════


class TestSkillSystem:
    """Verify skill system health after cleanup."""

    def test_no_dead_skills_on_disk(self):
        """Deleted skills should not exist on disk."""
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        dead_skills = ["ironclaw-review", "staging-cleanup", "staging-organize"]
        for skill_name in dead_skills:
            assert not (skills_dir / skill_name).exists(), f"Dead skill still on disk: {skill_name}"

    def test_no_dead_slash_commands(self):
        """Deleted slash commands should not exist."""
        commands_dir = Path(__file__).resolve().parent.parent.parent / ".claude" / "commands"
        if not commands_dir.exists():
            pytest.skip("No .claude/commands directory")
        dead_commands = ["ironclaw-review.md", "staging-cleanup.md", "staging-organize.md"]
        for cmd in dead_commands:
            assert not (commands_dir / cmd).exists(), f"Dead command still on disk: {cmd}"

    def test_skill_registry_no_ironclaw_consumers(self):
        """Skill registry should not reference ironclaw or audit consumers."""
        from core.skills.registry import _AGENT_ALIASES
        # ironclaw and audit should not be in the alias map
        assert "ironclaw" not in _AGENT_ALIASES, "ironclaw still in _AGENT_ALIASES"
        assert "audit" not in _AGENT_ALIASES, "audit still in _AGENT_ALIASES"
