"""Tests for Planning Agent — task breakdown, scoping, and board population."""
import pytest
from unittest.mock import MagicMock

from core.agents.planning_agent import PlanningAgent, make_planning_agent


class TestPlanningAgent:
    """Test PlanningAgent construction and configuration."""

    def test_agent_name(self):
        assert PlanningAgent.agent_name == "planning"

    def test_protocol_priority(self):
        assert PlanningAgent.protocol_priority == ["sprint-plan", "task-manage"]

    def test_default_protocol_mentions_planning(self):
        assert "planning agent" in PlanningAgent.default_protocol.lower()

    def test_default_protocol_mentions_scope(self):
        assert "scope" in PlanningAgent.default_protocol.lower()

    def test_default_protocol_mentions_task_board(self):
        assert "task board" in PlanningAgent.default_protocol.lower()


class TestPlanningAgentTools:
    """Test Planning Agent has correct tool set."""

    def test_has_task_tools(self, grim_config):
        agent = PlanningAgent(grim_config)
        tool_names = [t.name for t in agent.tools]
        # Task management tools
        assert "kronos_task_create" in tool_names or "task_create" in tool_names or any("task" in n for n in tool_names)

    def test_has_companion_tools(self, grim_config):
        agent = PlanningAgent(grim_config)
        tool_names = [t.name for t in agent.tools]
        assert "kronos_search" in tool_names
        assert "kronos_get" in tool_names

    def test_no_shell_tools(self, grim_config):
        agent = PlanningAgent(grim_config)
        tool_names = [t.name for t in agent.tools]
        assert "run_shell" not in tool_names

    def test_no_file_write_tools(self, grim_config):
        agent = PlanningAgent(grim_config)
        tool_names = [t.name for t in agent.tools]
        assert "write_file" not in tool_names
        assert "edit_file" not in tool_names

    def test_no_git_tools(self, grim_config):
        agent = PlanningAgent(grim_config)
        tool_names = [t.name for t in agent.tools]
        assert "git_add_commit" not in tool_names
        assert "git_status" not in tool_names


class TestPlanningAgentBuildContext:
    """Test build_context method."""

    def test_empty_state(self, grim_config):
        agent = PlanningAgent(grim_config)
        context = agent.build_context({})
        assert isinstance(context, dict)

    def test_with_knowledge_context(self, grim_config, sample_fdo):
        agent = PlanningAgent(grim_config)
        context = agent.build_context({"knowledge_context": [sample_fdo]})
        assert "relevant_knowledge" in context
        assert sample_fdo.id in context["relevant_knowledge"]


class TestPlanningAgentFactory:
    """Test factory function and discovery attributes."""

    def test_factory_returns_callable(self, grim_config):
        fn = make_planning_agent(grim_config)
        assert callable(fn)

    def test_discovery_attributes_removed(self):
        """v0.0.6 Phase 2: planning agent deprecated — discovery attributes removed."""
        from core.agents import planning_agent as mod
        assert not hasattr(mod, "__agent_name__")
        assert not hasattr(mod, "__make_agent__")
