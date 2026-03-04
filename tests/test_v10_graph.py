"""Tests for v0.10 graph topology — companion router + subgraph wrappers + response generator loop.

Tests:
  - Graph compilation: v0.10 compiles with all expected nodes and edges
  - Routing accuracy: companion_route_decision routes to correct subgraphs
  - Subgraph integration: each subgraph produces SubgraphOutput
  - Loop behavior: response generator continuation, safety valve, exit
  - Objective lifecycle: create, update, auto_continue, block
  - Full pipeline: end-to-end routing through subgraph to response generator
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.config import GrimConfig
from core.graph import build_graph
from core.nodes.companion_router import (
    companion_route_decision,
    make_companion_router_node,
)
from core.nodes.response_generator import (
    make_response_generator_node,
    response_generator_decision,
    should_auto_continue,
)
from core.state import (
    Objective,
    ObjectiveStatus,
    RoutingDecision,
    SubgraphOutput,
    UXMode,
)
from core.subgraphs.base import make_subgraph_wrapper
from core.subgraphs.code import make_code_subgraph
from core.subgraphs.conversation import make_conversation_subgraph
from core.subgraphs.planning import make_planning_subgraph
from core.subgraphs.research import make_research_subgraph


# ── Fixtures ────────────────────────────────────────────────────────────


def _msg(content: str):
    m = MagicMock()
    m.content = content
    return m


def _state(message: str = "hello", **overrides) -> dict:
    base = {
        "messages": [_msg(message)],
        "objectives": [],
        "graph_target": "research",
        "mode": "companion",
        "delegation_type": None,
        "agent_result": None,
        "subgraph_output": None,
        "loop_count": 0,
        "max_loops": 10,
        "should_continue": False,
        "continuation_intent": None,
        "subgraph_history": [],
        "context_stack": [],
        "ux_mode": UXMode.FULLSCREEN.value,
        "matched_skills": [],
        "skill_delegation_hint": None,
        "last_delegation_type": None,
        "knowledge_context": [],
        "context_summary": None,
    }
    base.update(overrides)
    return base


def _mock_model_decision():
    d = MagicMock()
    d.model = "claude-sonnet-4-6"
    d.tier = "sonnet"
    return d


def _v10_config(**overrides):
    cfg = MagicMock(spec=GrimConfig)
    cfg.use_companion_router = True
    cfg.routing_timeout = 3.0
    cfg.routing_enabled = True
    cfg.routing_default_tier = "sonnet"
    cfg.routing_classifier_enabled = False
    cfg.routing_confidence_threshold = 0.6
    cfg.models_disabled = []
    cfg.agents_disabled = []
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ── Graph compilation tests ─────────────────────────────────────────────


class TestV10GraphCompilation:
    """Verify v0.10 graph compiles with correct topology."""

    def test_compiles_with_companion_router(self, grim_config):
        """v0.10 graph should compile when use_companion_router=True."""
        grim_config.use_companion_router = True
        graph = build_graph(grim_config, mcp_session=None)
        assert graph is not None

    def test_has_v10_nodes(self, grim_config):
        """v0.10 graph should have subgraph wrapper nodes instead of v0.0.6 routing."""
        grim_config.use_companion_router = True
        graph = build_graph(grim_config, mcp_session=None)
        nodes = list(graph.get_graph().nodes.keys())
        expected_v10 = [
            "identity", "compress", "memory", "skill_match",
            "companion_router",
            "conversation", "planning", "research", "code",
            "response_generator",
            "integrate", "evolve",
        ]
        for node in expected_v10:
            assert node in nodes, f"Missing v0.10 node: {node}"

    def test_no_v06_routing_nodes(self, grim_config):
        """v0.10 graph should NOT have v0.0.6 routing nodes."""
        grim_config.use_companion_router = True
        graph = build_graph(grim_config, mcp_session=None)
        nodes = list(graph.get_graph().nodes.keys())
        v06_only = ["graph_router", "router", "audit_gate", "audit", "re_dispatch"]
        for node in v06_only:
            assert node not in nodes, f"v0.0.6 node should not be in v0.10 graph: {node}"

    def test_entry_point_identity(self, grim_config):
        """Entry point should still be 'identity'."""
        grim_config.use_companion_router = True
        graph = build_graph(grim_config, mcp_session=None)
        g = graph.get_graph()
        start_edges = [e for e in g.edges if e[0] == "__start__"]
        assert any(e[1] == "identity" for e in start_edges)

    def test_node_count(self, grim_config):
        """v0.10 graph should have 12 nodes (excl __start__ and __end__)."""
        grim_config.use_companion_router = True
        graph = build_graph(grim_config, mcp_session=None)
        nodes = [n for n in graph.get_graph().nodes.keys()
                 if n not in ("__start__", "__end__")]
        assert len(nodes) == 12, f"Expected 12 nodes, got {len(nodes)}: {nodes}"

    def test_v06_still_compiles(self, grim_config):
        """v0.0.6 graph should still compile (regression check)."""
        grim_config.use_companion_router = False
        graph = build_graph(grim_config, mcp_session=None)
        nodes = list(graph.get_graph().nodes.keys())
        assert "graph_router" in nodes
        assert "companion_router" not in nodes


# ── Routing accuracy tests ──────────────────────────────────────────────


class TestV10RoutingAccuracy:
    """Verify companion_route_decision routes to correct v0.10 subgraphs."""

    @pytest.mark.parametrize("graph_target,mode,delegation,expected", [
        # Personal → conversation subgraph
        ("personal", "companion", None, "conversation"),
        ("personal", "delegate", "research", "conversation"),  # personal overrides mode
        # Planning → planning subgraph
        ("planning", "companion", None, "planning"),
        ("planning", "delegate", "ironclaw", "planning"),  # planning overrides mode
        # Companion (no delegation) → conversation subgraph
        ("research", "companion", None, "conversation"),
        # Research delegation → research subgraph
        ("research", "delegate", "research", "research"),
        ("research", "delegate", "memory", "research"),
        ("research", "delegate", "codebase", "research"),
        # Code delegation → code subgraph
        ("research", "delegate", "ironclaw", "code"),
    ])
    def test_routing_matrix(self, graph_target, mode, delegation, expected):
        state = {"graph_target": graph_target, "mode": mode, "delegation_type": delegation}
        assert companion_route_decision(state) == expected

    def test_defaults_to_conversation(self):
        """Empty state defaults to conversation (companion mode)."""
        assert companion_route_decision({}) == "conversation"

    def test_all_five_intent_categories_reachable(self):
        """All 4 subgraph destinations are reachable."""
        destinations = set()
        test_states = [
            {"graph_target": "personal"},
            {"graph_target": "planning"},
            {"graph_target": "research", "mode": "companion"},
            {"graph_target": "research", "mode": "delegate", "delegation_type": "research"},
            {"graph_target": "research", "mode": "delegate", "delegation_type": "ironclaw"},
        ]
        for state in test_states:
            destinations.add(companion_route_decision(state))
        assert destinations == {"conversation", "planning", "research", "code"}


# ── Subgraph → Response Generator pipeline tests ────────────────────────


class TestSubgraphOutputPipeline:
    """Test that subgraphs produce SubgraphOutput and response_generator consumes it."""

    @pytest.mark.asyncio
    async def test_conversation_produces_subgraph_output(self):
        companion_fn = AsyncMock(return_value={"messages": [AIMessage(content="Hey!")]})
        personal_fn = AsyncMock(return_value={"messages": [AIMessage(content="Personal!")]})
        sg = make_conversation_subgraph(companion_fn, personal_fn)
        result = await sg(_state(graph_target="research"))
        assert "subgraph_output" in result
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.source_subgraph == "conversation"
        assert output.response == "Hey!"

    @pytest.mark.asyncio
    async def test_planning_produces_subgraph_output(self):
        planning_fn = AsyncMock(return_value={"messages": [AIMessage(content="Plan ready")]})
        sg = make_planning_subgraph(planning_fn)
        result = await sg(_state("create stories"))
        assert "subgraph_output" in result
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.source_subgraph == "planning"

    @pytest.mark.asyncio
    async def test_research_produces_subgraph_output(self):
        dispatch_fn = AsyncMock(return_value={"messages": [AIMessage(content="Found 3 FDOs")]})
        sg = make_research_subgraph(dispatch_fn)
        result = await sg(_state())
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.source_subgraph == "research"

    @pytest.mark.asyncio
    async def test_code_produces_subgraph_output(self):
        dispatch_fn = AsyncMock(return_value={"messages": [AIMessage(content="Code written")]})
        sg = make_code_subgraph(dispatch_fn)
        result = await sg(_state())
        output = SubgraphOutput(**result["subgraph_output"])
        assert output.source_subgraph == "code"

    @pytest.mark.asyncio
    async def test_response_generator_reads_subgraph_output(self):
        """Response generator should parse SubgraphOutput from state."""
        output = SubgraphOutput(
            response="Hello world", artifacts=[], memory_updates={},
            objective_updates=[], continuation=None, source_subgraph="conversation",
        )
        state = _state(subgraph_output=output.model_dump())
        rg = make_response_generator_node()
        result = await rg(state)
        assert result["subgraph_output"] is None  # cleared for next iteration
        assert result["loop_count"] == 1
        assert "conversation" in result["subgraph_history"]

    @pytest.mark.asyncio
    async def test_full_pipeline_conversation_to_exit(self):
        """Conversation subgraph → response_generator → exit (no continuation)."""
        # Simulate conversation subgraph output
        companion_fn = AsyncMock(return_value={"messages": [AIMessage(content="Hello!")]})
        personal_fn = AsyncMock(return_value={"messages": []})
        sg = make_conversation_subgraph(companion_fn, personal_fn)
        sg_result = await sg(_state(graph_target="research"))

        # Feed into response generator
        rg_state = _state(subgraph_output=sg_result["subgraph_output"])
        rg = make_response_generator_node()
        rg_result = await rg(rg_state)

        # Should exit (conversation has no auto-continuation)
        assert rg_result["should_continue"] is False
        assert response_generator_decision(rg_result) == "exit"

    @pytest.mark.asyncio
    async def test_full_pipeline_planning_to_code_continuation(self):
        """Planning with 'build it' → response_generator → continue to code."""
        planning_fn = AsyncMock(return_value={"messages": [AIMessage(content="Plan ready")]})
        sg = make_planning_subgraph(planning_fn)
        sg_result = await sg(_state("build it"))

        # Verify planning detected execution intent
        output = SubgraphOutput(**sg_result["subgraph_output"])
        assert output.continuation is not None
        assert output.continuation["next_intent"] == "code"

        # Feed into response generator
        rg_state = _state(subgraph_output=sg_result["subgraph_output"])
        rg = make_response_generator_node()
        rg_result = await rg(rg_state)

        # Should continue to code
        assert rg_result["should_continue"] is True
        assert rg_result["continuation_intent"] == "code"
        assert response_generator_decision(rg_result) == "continue"


# ── Loop behavior tests ─────────────────────────────────────────────────


class TestLoopBehavior:
    """Test the response generator loop — continuation, safety valve, exit."""

    def test_safety_valve_at_max_loops(self):
        """Should stop at max_loops regardless of continuation signals."""
        output = SubgraphOutput(
            response="", artifacts=[], memory_updates={},
            objective_updates=[], continuation={"next_intent": "code"},
            source_subgraph="planning",
        )
        should, intent, reason = should_auto_continue(
            objectives=[], loop_count=10, max_loops=10,
            subgraph_output=output,
        )
        assert should is False
        assert "safety valve" in reason.lower()

    def test_explicit_continuation(self):
        output = SubgraphOutput(
            response="", artifacts=[], memory_updates={},
            objective_updates=[],
            continuation={"next_intent": "research"},
            source_subgraph="planning",
        )
        should, intent, reason = should_auto_continue(
            objectives=[], loop_count=0, max_loops=10,
            subgraph_output=output,
        )
        assert should is True
        assert intent == "research"

    def test_auto_continue_objective(self):
        obj = Objective(
            title="Deploy auth", status=ObjectiveStatus.PENDING,
            target_subgraph="code", context={"auto_continue": True},
        )
        should, intent, reason = should_auto_continue(
            objectives=[obj], loop_count=0, max_loops=10,
            subgraph_output=None,
        )
        assert should is True
        assert intent == "code"

    def test_no_continuation_by_default(self):
        should, intent, reason = should_auto_continue(
            objectives=[], loop_count=0, max_loops=10,
            subgraph_output=None,
        )
        assert should is False

    def test_blocked_objectives_stop_loop(self):
        obj = Objective(
            title="Blocked task", status=ObjectiveStatus.BLOCKED,
        )
        should, intent, reason = should_auto_continue(
            objectives=[obj], loop_count=0, max_loops=10,
            subgraph_output=None,
        )
        assert should is False
        assert "blocked" in reason.lower()

    @pytest.mark.asyncio
    async def test_loop_count_increments(self):
        """Each pass through response_generator should increment loop_count."""
        output = SubgraphOutput(
            response="test", artifacts=[], memory_updates={},
            objective_updates=[], continuation=None, source_subgraph="conversation",
        )
        state = _state(subgraph_output=output.model_dump(), loop_count=3)
        rg = make_response_generator_node()
        result = await rg(state)
        assert result["loop_count"] == 4

    @pytest.mark.asyncio
    async def test_subgraph_history_tracks(self):
        """Subgraph history should accumulate across loops."""
        output = SubgraphOutput(
            response="test", artifacts=[], memory_updates={},
            objective_updates=[], continuation=None, source_subgraph="research",
        )
        state = _state(
            subgraph_output=output.model_dump(),
            subgraph_history=["conversation"],
        )
        rg = make_response_generator_node()
        result = await rg(state)
        assert result["subgraph_history"] == ["conversation", "research"]

    @pytest.mark.asyncio
    async def test_context_stack_grows(self):
        """Context stack should grow with each loop."""
        output = SubgraphOutput(
            response="Result text", artifacts=["src/auth.py"],
            memory_updates={}, objective_updates=[],
            continuation=None, source_subgraph="code",
        )
        state = _state(subgraph_output=output.model_dump())
        rg = make_response_generator_node()
        result = await rg(state)
        assert len(result["context_stack"]) == 1
        entry = result["context_stack"][0]
        assert entry["source"] == "code"
        assert entry["response_length"] == len("Result text")
        assert entry["artifacts"] == ["src/auth.py"]


# ── Objective lifecycle tests ────────────────────────────────────────────


class TestObjectiveLifecycle:
    """Test objective creation, updates, and state transitions through the loop."""

    @pytest.mark.asyncio
    async def test_objective_updates_applied(self):
        """Response generator should apply objective updates from subgraph output."""
        existing_obj = Objective(
            id="obj-1", title="Task A", status=ObjectiveStatus.PENDING,
        )
        updated_obj = Objective(
            id="obj-1", title="Task A", status=ObjectiveStatus.ACTIVE,
        )
        output = SubgraphOutput(
            response="Working on it", artifacts=[], memory_updates={},
            objective_updates=[updated_obj],
            continuation=None, source_subgraph="code",
        )
        state = _state(
            objectives=[existing_obj],
            subgraph_output=output.model_dump(),
        )
        rg = make_response_generator_node()
        result = await rg(state)
        assert len(result["objectives"]) == 1
        assert result["objectives"][0].status == ObjectiveStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_new_objective_added(self):
        """New objectives from subgraph output should be appended."""
        new_obj = Objective(
            id="obj-new", title="New task", status=ObjectiveStatus.PENDING,
        )
        output = SubgraphOutput(
            response="Created task", artifacts=[], memory_updates={},
            objective_updates=[new_obj],
            continuation=None, source_subgraph="planning",
        )
        state = _state(subgraph_output=output.model_dump())
        rg = make_response_generator_node()
        result = await rg(state)
        assert len(result["objectives"]) == 1
        assert result["objectives"][0].id == "obj-new"

    @pytest.mark.asyncio
    async def test_auto_continue_objective_triggers_loop(self):
        """Pending objective with auto_continue should trigger continuation."""
        obj = Objective(
            id="obj-auto", title="Auto task",
            status=ObjectiveStatus.PENDING,
            target_subgraph="code",
            context={"auto_continue": True},
        )
        output = SubgraphOutput(
            response="Done", artifacts=[], memory_updates={},
            objective_updates=[], continuation=None,
            source_subgraph="research",
        )
        state = _state(
            objectives=[obj],
            subgraph_output=output.model_dump(),
        )
        rg = make_response_generator_node()
        result = await rg(state)
        assert result["should_continue"] is True
        assert result["continuation_intent"] == "code"

    @pytest.mark.asyncio
    async def test_blocked_objective_prevents_loop(self):
        """All blocked objectives should prevent auto-continuation."""
        obj = Objective(
            id="obj-blocked", title="Blocked task",
            status=ObjectiveStatus.BLOCKED,
        )
        output = SubgraphOutput(
            response="Can't proceed", artifacts=[], memory_updates={},
            objective_updates=[], continuation=None,
            source_subgraph="code",
        )
        state = _state(
            objectives=[obj],
            subgraph_output=output.model_dump(),
        )
        rg = make_response_generator_node()
        result = await rg(state)
        assert result["should_continue"] is False


# ── UX mode behavior tests ──────────────────────────────────────────────


class TestUXModeBehavior:
    """Test UX-mode-aware formatting and autonomy levels."""

    @pytest.mark.asyncio
    async def test_discord_truncation(self):
        """Discord mode should truncate long responses."""
        from core.nodes.response_generator import _format_response
        long_response = "x" * 2000
        output = SubgraphOutput(
            response=long_response, artifacts=[], memory_updates={},
            objective_updates=[], continuation=None, source_subgraph="conversation",
        )
        formatted = _format_response(output, UXMode.DISCORD.value)
        assert len(formatted) < 2000
        assert "truncated" in formatted

    @pytest.mark.asyncio
    async def test_fullscreen_passthrough(self):
        """Fullscreen mode should pass response through unchanged."""
        from core.nodes.response_generator import _format_response
        output = SubgraphOutput(
            response="Full **markdown** response", artifacts=[], memory_updates={},
            objective_updates=[], continuation=None, source_subgraph="conversation",
        )
        formatted = _format_response(output, UXMode.FULLSCREEN.value)
        assert formatted == "Full **markdown** response"

    def test_discord_autonomous_mode(self):
        """Discord mode should auto-continue with pending objectives."""
        obj = Objective(
            title="Deploy", status=ObjectiveStatus.PENDING,
            target_subgraph="code",
        )
        should, intent, reason = should_auto_continue(
            objectives=[obj], loop_count=0, max_loops=10,
            subgraph_output=None, ux_mode=UXMode.DISCORD.value,
        )
        assert should is True
        assert "autonomous" in reason.lower()

    def test_fullscreen_not_autonomous(self):
        """Fullscreen mode should NOT auto-continue without explicit signals."""
        obj = Objective(
            title="Deploy", status=ObjectiveStatus.PENDING,
            target_subgraph="code",
        )
        should, intent, reason = should_auto_continue(
            objectives=[obj], loop_count=0, max_loops=10,
            subgraph_output=None, ux_mode=UXMode.FULLSCREEN.value,
        )
        assert should is False


# ── Edge integration: router → subgraph → response_generator ────────────


class TestEdgeIntegration:
    """End-to-end flow through the v0.10 routing pipeline."""

    @pytest.mark.asyncio
    async def test_conversation_full_flow(self):
        """Router → conversation → response_generator → exit."""
        # 1. Router produces state
        router_state = {"graph_target": "personal", "mode": "companion"}
        assert companion_route_decision(router_state) == "conversation"

        # 2. Conversation subgraph runs
        companion_fn = AsyncMock(return_value={"messages": [AIMessage(content="Hi there")]})
        personal_fn = AsyncMock(return_value={"messages": [AIMessage(content="Personal hi")]})
        sg = make_conversation_subgraph(companion_fn, personal_fn)
        sg_result = await sg(_state(graph_target="personal"))
        output = SubgraphOutput(**sg_result["subgraph_output"])
        assert output.response == "Personal hi"

        # 3. Response generator processes
        rg = make_response_generator_node()
        rg_result = await rg(_state(subgraph_output=sg_result["subgraph_output"]))
        assert response_generator_decision(rg_result) == "exit"

    @pytest.mark.asyncio
    async def test_research_full_flow(self):
        """Router → research → response_generator → exit."""
        router_state = {"graph_target": "research", "mode": "delegate", "delegation_type": "research"}
        assert companion_route_decision(router_state) == "research"

        dispatch_fn = AsyncMock(return_value={"messages": [AIMessage(content="Found results")]})
        sg = make_research_subgraph(dispatch_fn)
        sg_result = await sg(_state())
        output = SubgraphOutput(**sg_result["subgraph_output"])
        assert output.source_subgraph == "research"

        rg = make_response_generator_node()
        rg_result = await rg(_state(subgraph_output=sg_result["subgraph_output"]))
        assert response_generator_decision(rg_result) == "exit"

    @pytest.mark.asyncio
    async def test_planning_to_code_loop(self):
        """Router → planning (build it) → response_generator → continue → code."""
        router_state = {"graph_target": "planning", "mode": "companion"}
        assert companion_route_decision(router_state) == "planning"

        planning_fn = AsyncMock(return_value={"messages": [AIMessage(content="Plan done")]})
        sg = make_planning_subgraph(planning_fn)
        sg_result = await sg(_state("build it"))

        # Response generator should signal continuation
        rg = make_response_generator_node()
        rg_result = await rg(_state(subgraph_output=sg_result["subgraph_output"]))
        assert response_generator_decision(rg_result) == "continue"
        assert rg_result["continuation_intent"] == "code"

    @pytest.mark.asyncio
    async def test_code_with_auto_continue_objective(self):
        """Code subgraph with auto_continue objective → loop back."""
        from core.state import AgentResult
        obj = Objective(
            title="Next step", status=ObjectiveStatus.PENDING,
            target_subgraph="code", context={"auto_continue": True},
        )
        dispatch_fn = AsyncMock(return_value={
            "messages": [AIMessage(content="Code done")],
            "agent_result": AgentResult(agent="ironclaw", success=True, summary="Done"),
        })
        sg = make_code_subgraph(dispatch_fn)
        sg_result = await sg(_state(objectives=[obj]))

        output = SubgraphOutput(**sg_result["subgraph_output"])
        assert output.continuation is not None
        assert output.continuation["next_intent"] == "code"

        rg = make_response_generator_node()
        rg_result = await rg(_state(
            objectives=[obj], subgraph_output=sg_result["subgraph_output"],
        ))
        assert response_generator_decision(rg_result) == "continue"

    @pytest.mark.asyncio
    async def test_multi_loop_safety_valve(self):
        """Loop should stop at max_loops even with continuation signal."""
        output = SubgraphOutput(
            response="Still going", artifacts=[], memory_updates={},
            objective_updates=[],
            continuation={"next_intent": "code"},
            source_subgraph="code",
        )
        # At loop 9 with max 10 — this is the 10th iteration (loop_count+1 >= max)
        state = _state(
            subgraph_output=output.model_dump(),
            loop_count=9,
            max_loops=10,
        )
        rg = make_response_generator_node()
        result = await rg(state)
        assert result["should_continue"] is False
        assert response_generator_decision(result) == "exit"

    @pytest.mark.asyncio
    async def test_subgraph_output_cleared_between_loops(self):
        """subgraph_output should be None after response_generator processes it."""
        output = SubgraphOutput(
            response="test", artifacts=[], memory_updates={},
            objective_updates=[], continuation=None, source_subgraph="conversation",
        )
        state = _state(subgraph_output=output.model_dump())
        rg = make_response_generator_node()
        result = await rg(state)
        assert result["subgraph_output"] is None
