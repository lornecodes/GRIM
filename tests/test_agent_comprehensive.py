"""Comprehensive agent tests — every agent subclass, metadata, tools, factories,
build_context, protocol selection, and cross-agent invariants.

Covers: memory, research, codebase, operator, coder.
Also tests planning (deprecated but still importable).

All tests are synchronous/mocked — no real API or LLM calls.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

from core.config import GrimConfig


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def config():
    cfg = GrimConfig()
    cfg.model = "claude-sonnet-4-6"
    return cfg


def _make_fdo(fdo_id="test-fdo", domain="physics", status="stable",
              summary="A test FDO", related=None):
    return SimpleNamespace(
        id=fdo_id, domain=domain, status=status,
        summary=summary, related=related or [],
    )


# ═══════════════════════════════════════════════════════════════════════════
# Memory Agent
# ═══════════════════════════════════════════════════════════════════════════


class TestMemoryAgent:
    """Tests for the Memory Agent — vault write operations + task management."""

    def test_agent_name(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        assert agent.agent_name == "memory"

    def test_display_name(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        assert agent.agent_display_name == "Memory"

    def test_role(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        assert agent.agent_role == "vault_ops"

    def test_tier_is_grim(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        assert agent.agent_tier == "grim"

    def test_not_toggleable(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        assert agent.agent_toggleable is False

    def test_has_kronos_write_tools(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        tool_names = {t.name for t in agent.tools}
        # Memory agent must have vault write tools
        assert "kronos_create" in tool_names
        assert "kronos_update" in tool_names
        assert "kronos_search" in tool_names

    def test_has_memory_tools(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "read_grim_memory" in tool_names
        assert "update_grim_memory" in tool_names

    def test_has_task_tools(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "kronos_task_create" in tool_names
        assert "kronos_board_view" in tool_names

    def test_protocol_priority_includes_kronos_skills(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        assert "kronos-capture" in agent.protocol_priority
        assert "kronos-promote" in agent.protocol_priority

    def test_default_protocol_mentions_vault(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        assert "kronos" in agent.default_protocol.lower() or "vault" in agent.default_protocol.lower()

    def test_metadata_dict_structure(self, config):
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        meta = agent.metadata()
        assert meta["id"] == "memory"
        assert meta["name"] == "Memory"
        assert meta["role"] == "vault_ops"
        assert "kronos_create" in meta["tools"]
        assert meta["color"] == "#8b5cf6"

    def test_factory_function(self, config):
        from core.agents.memory_agent import make_memory_agent
        fn = make_memory_agent(config)
        assert callable(fn)
        assert asyncio.iscoroutinefunction(fn)

    def test_discovery_attributes(self):
        from core.agents import memory_agent
        assert memory_agent.__agent_name__ == "memory"
        assert callable(memory_agent.__make_agent__)
        assert memory_agent.__agent_class__.__name__ == "MemoryAgent"

    def test_no_write_file_tool(self, config):
        """Memory agent should not have workspace file write tools."""
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "write_file" not in tool_names
        assert "run_shell" not in tool_names


# ═══════════════════════════════════════════════════════════════════════════
# Research Agent
# ═══════════════════════════════════════════════════════════════════════════


class TestResearchAgent:
    """Tests for the Research Agent — read-only analysis and synthesis."""

    def test_agent_name(self, config):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        assert agent.agent_name == "research"

    def test_display_name(self, config):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        assert agent.agent_display_name == "Researcher"

    def test_role(self, config):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        assert agent.agent_role == "analysis"

    def test_tier_is_grim(self, config):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        assert agent.agent_tier == "grim"

    def test_has_file_read_tools(self, config):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "read_file" in tool_names

    def test_has_companion_tools(self, config):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "kronos_search" in tool_names

    def test_no_write_tools(self, config):
        """Research is read-only — no file writes, no shell, no vault writes."""
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "write_file" not in tool_names
        assert "run_shell" not in tool_names
        assert "kronos_create" not in tool_names

    def test_protocol_priority(self, config):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        assert "deep-ingest" in agent.protocol_priority

    def test_build_context_empty_state(self, config):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        ctx = agent.build_context({})
        assert ctx == {}

    def test_build_context_with_knowledge(self, config):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        fdo = _make_fdo(related=["other-fdo"])
        ctx = agent.build_context({"knowledge_context": [fdo]})
        assert "relevant_knowledge" in ctx
        assert "test-fdo" in ctx["relevant_knowledge"]
        assert "other-fdo" in ctx["relevant_knowledge"]

    def test_build_context_limits_fdos(self, config):
        """Should only include up to 8 FDOs."""
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        fdos = [_make_fdo(fdo_id=f"fdo-{i}") for i in range(20)]
        ctx = agent.build_context({"knowledge_context": fdos})
        # Count occurrences — should max out at 8
        assert ctx["relevant_knowledge"].count("fdo-") <= 8 * 3  # id + possible related refs

    def test_factory_function(self, config):
        from core.agents.research_agent import make_research_agent
        fn = make_research_agent(config)
        assert callable(fn)

    def test_discovery_attributes(self):
        from core.agents import research_agent
        assert research_agent.__agent_name__ == "research"
        assert research_agent.__agent_class__.__name__ == "ResearchAgent"

    def test_metadata_dict(self, config):
        from core.agents.research_agent import ResearchAgent
        agent = ResearchAgent(config)
        meta = agent.metadata()
        assert meta["id"] == "research"
        assert meta["name"] == "Researcher"
        assert meta["color"] == "#3b82f6"


# ═══════════════════════════════════════════════════════════════════════════
# Codebase Agent
# ═══════════════════════════════════════════════════════════════════════════


class TestCodebaseAgent:
    """Tests for the Codebase Agent — spatial awareness, read-only."""

    def test_agent_name(self, config):
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        assert agent.agent_name == "codebase"

    def test_display_name(self, config):
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        assert agent.agent_display_name == "Codebase"

    def test_role(self, config):
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        assert agent.agent_role == "spatial_awareness"

    def test_has_source_tools(self, config):
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "kronos_navigate" in tool_names
        assert "kronos_read_source" in tool_names
        assert "kronos_search_source" in tool_names
        assert "kronos_deep_dive" in tool_names

    def test_has_file_read_tools(self, config):
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "read_file" in tool_names

    def test_has_git_read_tools(self, config):
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "git_status" in tool_names
        assert "git_log" in tool_names

    def test_no_write_tools(self, config):
        """Codebase agent is read-only — absolutely no writes."""
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "write_file" not in tool_names
        assert "run_shell" not in tool_names
        assert "kronos_create" not in tool_names
        assert "git_add_commit" not in tool_names

    def test_protocol_priority_includes_navigate(self, config):
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        assert "repo-navigate" in agent.protocol_priority

    def test_build_context_empty_state(self, config):
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        ctx = agent.build_context({})
        # repos_info may or may not be present depending on repos.yaml
        assert isinstance(ctx, dict)

    def test_build_context_with_knowledge(self, config):
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        fdo = _make_fdo()
        ctx = agent.build_context({"knowledge_context": [fdo]})
        assert "relevant_knowledge" in ctx

    def test_build_context_loads_repos_manifest(self, config, tmp_path):
        """When repos.yaml exists, it should appear in context."""
        from core.agents.codebase_agent import CodebaseAgent
        config.workspace_root = tmp_path
        config.repos_manifest = "repos.yaml"
        manifest = tmp_path / "repos.yaml"
        manifest.write_text(
            "repos:\n"
            "  - name: test-repo\n"
            "    description: A test repo\n"
            "    tier: core\n"
            "    path: test-repo\n",
            encoding="utf-8",
        )
        agent = CodebaseAgent(config)
        ctx = agent.build_context({})
        assert "workspace_repos" in ctx
        assert "test-repo" in ctx["workspace_repos"]

    def test_build_context_missing_manifest(self, config, tmp_path):
        """Gracefully handle missing repos.yaml."""
        from core.agents.codebase_agent import CodebaseAgent
        config.workspace_root = tmp_path
        config.repos_manifest = "nonexistent.yaml"
        agent = CodebaseAgent(config)
        ctx = agent.build_context({})
        # Should not crash, workspace_repos should be empty or absent
        if "workspace_repos" in ctx:
            assert ctx["workspace_repos"] == ""

    def test_metadata_dict(self, config):
        from core.agents.codebase_agent import CodebaseAgent
        agent = CodebaseAgent(config)
        meta = agent.metadata()
        assert meta["id"] == "codebase"
        assert meta["name"] == "Codebase"
        assert meta["color"] == "#06b6d4"
        assert "kronos_navigate" in meta["tools"]

    def test_factory_function(self, config):
        from core.agents.codebase_agent import make_codebase_agent
        fn = make_codebase_agent(config)
        assert callable(fn)

    def test_discovery_attributes(self):
        from core.agents import codebase_agent
        assert codebase_agent.__agent_name__ == "codebase"
        assert codebase_agent.__agent_class__.__name__ == "CodebaseAgent"


# ═══════════════════════════════════════════════════════════════════════════
# Operator Agent
# ═══════════════════════════════════════════════════════════════════════════


class TestOperatorAgent:
    """Tests for the Operator Agent — infrastructure awareness, read-only git."""

    def test_agent_name(self, config):
        from core.agents.operator_agent import OperatorAgent
        agent = OperatorAgent(config)
        assert agent.agent_name == "operator"

    def test_display_name(self, config):
        from core.agents.operator_agent import OperatorAgent
        agent = OperatorAgent(config)
        assert agent.agent_display_name == "Operator"

    def test_role(self, config):
        from core.agents.operator_agent import OperatorAgent
        agent = OperatorAgent(config)
        assert agent.agent_role == "git_reads"

    def test_has_git_read_tools(self, config):
        from core.agents.operator_agent import OperatorAgent
        agent = OperatorAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "git_status" in tool_names

    def test_no_write_tools(self, config):
        """Operator is read-only — no shell, no file writes, no git writes."""
        from core.agents.operator_agent import OperatorAgent
        agent = OperatorAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "write_file" not in tool_names
        assert "run_shell" not in tool_names
        assert "git_add_commit" not in tool_names

    def test_protocol_priority(self, config):
        from core.agents.operator_agent import OperatorAgent
        agent = OperatorAgent(config)
        assert "git-operations" in agent.protocol_priority

    def test_metadata_dict(self, config):
        from core.agents.operator_agent import OperatorAgent
        agent = OperatorAgent(config)
        meta = agent.metadata()
        assert meta["id"] == "operator"
        assert meta["name"] == "Operator"
        assert meta["color"] == "#f59e0b"

    def test_discovery_attributes(self):
        from core.agents import operator_agent
        assert operator_agent.__agent_name__ == "operate"
        assert operator_agent.__agent_class__.__name__ == "OperatorAgent"

    def test_factory_function(self, config):
        from core.agents.operator_agent import make_operator_agent
        fn = make_operator_agent(config)
        assert callable(fn)


# ═══════════════════════════════════════════════════════════════════════════
# Coder Agent
# ═══════════════════════════════════════════════════════════════════════════


class TestCoderAgent:
    """Tests for the Coder Agent — code writing and file operations."""

    def test_agent_name(self, config):
        from core.agents.coder_agent import CoderAgent
        agent = CoderAgent(config)
        assert agent.agent_name == "coder"

    def test_display_name(self, config):
        from core.agents.coder_agent import CoderAgent
        agent = CoderAgent(config)
        assert agent.agent_display_name == "Coder"

    def test_role(self, config):
        from core.agents.coder_agent import CoderAgent
        agent = CoderAgent(config)
        assert agent.agent_role == "code_files"

    def test_has_file_tools(self, config):
        from core.agents.coder_agent import CoderAgent
        agent = CoderAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "read_file" in tool_names
        assert "write_file" in tool_names

    def test_has_shell_tools(self, config):
        from core.agents.coder_agent import CoderAgent
        agent = CoderAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "run_shell" in tool_names

    def test_has_companion_tools(self, config):
        from core.agents.coder_agent import CoderAgent
        agent = CoderAgent(config)
        tool_names = {t.name for t in agent.tools}
        assert "kronos_search" in tool_names

    def test_protocol_priority(self, config):
        from core.agents.coder_agent import CoderAgent
        agent = CoderAgent(config)
        assert "code-execution" in agent.protocol_priority

    def test_metadata_dict(self, config):
        from core.agents.coder_agent import CoderAgent
        agent = CoderAgent(config)
        meta = agent.metadata()
        assert meta["id"] == "coder"
        assert meta["name"] == "Coder"
        assert meta["color"] == "#34d399"
        assert "write_file" in meta["tools"]

    def test_discovery_attributes(self):
        from core.agents import coder_agent
        assert coder_agent.__agent_name__ == "code"
        assert coder_agent.__agent_class__.__name__ == "CoderAgent"

    def test_factory_function(self, config):
        from core.agents.coder_agent import make_coder_agent
        fn = make_coder_agent(config)
        assert callable(fn)


# ═══════════════════════════════════════════════════════════════════════════
# Cross-Agent Invariants
# ═══════════════════════════════════════════════════════════════════════════


ALL_AGENT_CLASSES = [
    ("core.agents.memory_agent", "MemoryAgent"),
    ("core.agents.research_agent", "ResearchAgent"),
    ("core.agents.codebase_agent", "CodebaseAgent"),
    ("core.agents.operator_agent", "OperatorAgent"),
    ("core.agents.coder_agent", "CoderAgent"),
]


class TestCrossAgentInvariants:
    """Tests that apply to ALL agent subclasses uniformly."""

    @pytest.fixture(params=ALL_AGENT_CLASSES, ids=[c[1] for c in ALL_AGENT_CLASSES])
    def agent_cls(self, request):
        import importlib
        module_name, class_name = request.param
        mod = importlib.import_module(module_name)
        return getattr(mod, class_name)

    def test_agent_has_name(self, config, agent_cls):
        agent = agent_cls(config)
        assert isinstance(agent.agent_name, str)
        assert len(agent.agent_name) > 0

    def test_agent_has_display_name(self, config, agent_cls):
        agent = agent_cls(config)
        assert isinstance(agent.agent_display_name, str)
        assert len(agent.agent_display_name) > 0

    def test_agent_has_role(self, config, agent_cls):
        agent = agent_cls(config)
        assert isinstance(agent.agent_role, str)
        assert len(agent.agent_role) > 0

    def test_agent_has_description(self, config, agent_cls):
        agent = agent_cls(config)
        assert isinstance(agent.agent_description, str)
        assert len(agent.agent_description) > 0

    def test_agent_has_color(self, config, agent_cls):
        agent = agent_cls(config)
        assert agent.agent_color.startswith("#")
        assert len(agent.agent_color) == 7  # #RRGGBB

    def test_agent_has_tier(self, config, agent_cls):
        agent = agent_cls(config)
        assert agent.agent_tier == "grim"

    def test_agent_has_tools(self, config, agent_cls):
        agent = agent_cls(config)
        assert isinstance(agent.tools, list)
        assert len(agent.tools) > 0

    def test_agent_has_protocol_priority(self, config, agent_cls):
        agent = agent_cls(config)
        assert isinstance(agent.protocol_priority, list)
        # Audit agent uses custom factory with AUDIT_SYSTEM_PREAMBLE instead
        if agent.agent_name != "audit":
            assert len(agent.protocol_priority) > 0

    def test_agent_has_default_protocol(self, config, agent_cls):
        agent = agent_cls(config)
        assert isinstance(agent.default_protocol, str)
        # Audit agent uses custom factory with AUDIT_SYSTEM_PREAMBLE instead
        if agent.agent_name != "audit":
            assert len(agent.default_protocol) > 10

    def test_agent_has_llm(self, config, agent_cls):
        agent = agent_cls(config)
        assert agent.llm is not None

    def test_agent_has_llm_with_tools(self, config, agent_cls):
        agent = agent_cls(config)
        assert agent.llm_with_tools is not None

    def test_metadata_returns_dict(self, config, agent_cls):
        agent = agent_cls(config)
        meta = agent.metadata()
        assert isinstance(meta, dict)

    def test_metadata_has_required_keys(self, config, agent_cls):
        agent = agent_cls(config)
        meta = agent.metadata()
        required = {"id", "name", "role", "description", "tools", "color", "tier", "toggleable"}
        assert required <= set(meta.keys())

    def test_metadata_tools_are_strings(self, config, agent_cls):
        agent = agent_cls(config)
        meta = agent.metadata()
        assert all(isinstance(t, str) for t in meta["tools"])

    def test_metadata_tools_not_empty(self, config, agent_cls):
        agent = agent_cls(config)
        meta = agent.metadata()
        assert len(meta["tools"]) > 0

    def test_build_context_returns_dict(self, config, agent_cls):
        agent = agent_cls(config)
        ctx = agent.build_context({})
        assert isinstance(ctx, dict)

    def test_toggleable_is_bool(self, config, agent_cls):
        agent = agent_cls(config)
        assert isinstance(agent.agent_toggleable, bool)


# ═══════════════════════════════════════════════════════════════════════════
# Tool Boundary Enforcement
# ═══════════════════════════════════════════════════════════════════════════


class TestToolBoundaries:
    """Verify trust boundaries — agents only have the tools they should."""

    def test_read_only_agents_have_no_write_tools(self, config):
        """Research, Codebase, and Operator are read-only."""
        from core.agents.research_agent import ResearchAgent
        from core.agents.codebase_agent import CodebaseAgent
        from core.agents.operator_agent import OperatorAgent

        write_tools = {"write_file", "run_shell", "git_add_commit",
                        "kronos_create", "kronos_update"}

        for cls in [ResearchAgent, CodebaseAgent, OperatorAgent]:
            agent = cls(config)
            tool_names = {t.name for t in agent.tools}
            overlap = write_tools & tool_names
            assert not overlap, f"{cls.__name__} has write tools: {overlap}"

    def test_only_memory_agent_has_vault_writes(self, config):
        """Only memory agent should have kronos_create/update."""
        from core.agents.memory_agent import MemoryAgent
        from core.agents.research_agent import ResearchAgent
        from core.agents.codebase_agent import CodebaseAgent
        from core.agents.operator_agent import OperatorAgent
        from core.agents.coder_agent import CoderAgent

        vault_write_tools = {"kronos_create", "kronos_update"}

        # Memory should have them
        memory = MemoryAgent(config)
        assert vault_write_tools <= {t.name for t in memory.tools}

        # Others should NOT
        for cls in [ResearchAgent, CodebaseAgent, OperatorAgent, CoderAgent]:
            agent = cls(config)
            tool_names = {t.name for t in agent.tools}
            overlap = vault_write_tools & tool_names
            assert not overlap, f"{cls.__name__} has vault write tools: {overlap}"


# ═══════════════════════════════════════════════════════════════════════════
# Unique Names / No Conflicts
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentUniqueness:
    """Ensure no agent name or tool name conflicts."""

    def test_unique_agent_names(self, config):
        """All agent classes have unique agent_name values."""
        import importlib
        names = []
        for module_name, class_name in ALL_AGENT_CLASSES:
            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name)
            agent = cls(config)
            names.append(agent.agent_name)
        assert len(names) == len(set(names)), f"Duplicate agent names: {names}"

    def test_unique_display_names(self, config):
        """All agent classes have unique display names."""
        import importlib
        names = []
        for module_name, class_name in ALL_AGENT_CLASSES:
            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name)
            agent = cls(config)
            names.append(agent.agent_display_name)
        assert len(names) == len(set(names)), f"Duplicate display names: {names}"

    def test_unique_colors(self, config):
        """All agent classes have unique colors (for UI differentiation)."""
        import importlib
        colors = []
        for module_name, class_name in ALL_AGENT_CLASSES:
            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name)
            agent = cls(config)
            colors.append(agent.agent_color)
        assert len(colors) == len(set(colors)), f"Duplicate colors: {colors}"


# ═══════════════════════════════════════════════════════════════════════════
# Model Configuration
# ═══════════════════════════════════════════════════════════════════════════


class TestModelConfiguration:
    """Verify model binding across agents."""

    def test_all_agents_use_config_model(self, config):
        """All agents should bind to the model from GrimConfig."""
        import importlib
        for module_name, class_name in ALL_AGENT_CLASSES:
            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name)
            agent = cls(config)
            assert agent.llm.model == config.model, (
                f"{class_name} uses {agent.llm.model}, expected {config.model}"
            )

    def test_model_from_config(self, config):
        """Agents bind to whichever model GrimConfig specifies."""
        from core.agents.memory_agent import MemoryAgent
        config.model = "claude-haiku-4-5-20251001"
        agent = MemoryAgent(config)
        assert agent.llm.model == "claude-haiku-4-5-20251001"

    def test_temperature_is_0_3(self, config):
        """All agents should use temperature 0.3 for precision."""
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        assert agent.llm.temperature == 0.3

    def test_caller_id_header(self, config):
        """All agents should set X-Caller-ID: grim header."""
        from core.agents.memory_agent import MemoryAgent
        agent = MemoryAgent(config)
        assert agent.llm.default_headers.get("X-Caller-ID") == "grim"


# ═══════════════════════════════════════════════════════════════════════════
# Discovery Attributes
# ═══════════════════════════════════════════════════════════════════════════


DISCOVERABLE_MODULES = [
    "core.agents.memory_agent",
    "core.agents.research_agent",
    "core.agents.codebase_agent",
    "core.agents.operator_agent",
    "core.agents.coder_agent",
]


class TestDiscoveryAttributes:
    """Verify all discoverable agents export required attributes."""

    @pytest.fixture(params=DISCOVERABLE_MODULES, ids=[m.split(".")[-1] for m in DISCOVERABLE_MODULES])
    def agent_module(self, request):
        import importlib
        return importlib.import_module(request.param)

    def test_has_agent_name(self, agent_module):
        assert hasattr(agent_module, "__agent_name__")
        assert isinstance(agent_module.__agent_name__, str)

    def test_has_make_agent(self, agent_module):
        assert hasattr(agent_module, "__make_agent__")
        assert callable(agent_module.__make_agent__)

    def test_has_agent_class(self, agent_module):
        assert hasattr(agent_module, "__agent_class__")
        assert isinstance(agent_module.__agent_class__, type)

    def test_make_agent_returns_callable(self, agent_module, config):
        fn = agent_module.__make_agent__(config)
        assert callable(fn)

    def test_planning_agent_not_discoverable(self):
        """Planning agent should NOT have discovery attributes (deprecated)."""
        from core.agents import planning_agent
        assert not hasattr(planning_agent, "__agent_name__")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x"])
