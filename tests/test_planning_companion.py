"""Tests for the planning companion graph node (v0.0.6 Phase 2)."""
import pytest
from unittest.mock import MagicMock

from core.nodes.planning_companion import (
    PLANNING_MODE_PREAMBLE,
    PLANNING_TOOLS,
    make_planning_companion_node,
)
from core.tools.kronos_tasks import TASK_ALL_TOOLS
from core.tools.kronos_read import COMPANION_TOOLS


class TestPlanningPreamble:
    """Test planning mode preamble content."""

    def test_preamble_mentions_planning(self):
        assert "planning" in PLANNING_MODE_PREAMBLE.lower()

    def test_preamble_mentions_draft(self):
        """Draft-by-default instruction must be in preamble."""
        assert "draft" in PLANNING_MODE_PREAMBLE.lower()

    def test_preamble_mentions_vault_first(self):
        """Vault-first instruction must be in preamble."""
        assert "vault" in PLANNING_MODE_PREAMBLE.lower()
        assert "search" in PLANNING_MODE_PREAMBLE.lower()

    def test_preamble_mentions_acceptance_criteria(self):
        """Acceptance criteria requirement must be in preamble."""
        assert "acceptance" in PLANNING_MODE_PREAMBLE.lower()

    def test_preamble_mentions_validation(self):
        """Self-validation instruction must be in preamble."""
        assert "validat" in PLANNING_MODE_PREAMBLE.lower()


class TestPlanningTools:
    """Test planning tool set composition."""

    def test_tools_include_task_tools(self):
        """Planning tools should include all task management tools."""
        tool_names = {t.name for t in PLANNING_TOOLS}
        task_names = {t.name for t in TASK_ALL_TOOLS}
        assert task_names.issubset(tool_names), (
            f"Missing task tools: {task_names - tool_names}"
        )

    def test_tools_include_companion_tools(self):
        """Planning tools should include vault read tools."""
        tool_names = {t.name for t in PLANNING_TOOLS}
        companion_names = {t.name for t in COMPANION_TOOLS}
        assert companion_names.issubset(tool_names), (
            f"Missing companion tools: {companion_names - tool_names}"
        )

    def test_tools_count(self):
        """Should have reasonable number of tools (task + vault read)."""
        assert len(PLANNING_TOOLS) >= len(TASK_ALL_TOOLS)


class TestPlanningCompanionFactory:
    """Test make_planning_companion_node factory."""

    def test_factory_returns_callable(self):
        config = MagicMock()
        config.model = "claude-sonnet-4-6"
        config.temperature = 0.7
        config.max_tokens = 4096
        node = make_planning_companion_node(config)
        assert callable(node)


class TestPlanningGraphIntegration:
    """Test planning companion integrates into graph."""

    def test_graph_target_planning_exists(self):
        """graph_target should accept 'planning'."""
        from core.nodes.graph_router import graph_route_decision
        assert graph_route_decision({"graph_target": "planning"}) == "planning"

    def test_planning_signals_imported(self):
        """PLANNING_SIGNALS should be importable from graph_router."""
        from core.nodes.graph_router import PLANNING_SIGNALS
        assert len(PLANNING_SIGNALS) > 0

    @pytest.mark.asyncio
    async def test_planning_signal_routes_correctly(self):
        """Planning signal in graph_router should route to planning."""
        from core.nodes.graph_router import graph_router_node
        msg = MagicMock()
        msg.content = "let's plan this out"
        state = {"messages": [msg]}
        result = await graph_router_node(state)
        assert result["graph_target"] == "planning"
