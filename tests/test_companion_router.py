"""Tests for the companion router node and edge function.

Tests the unified routing node that replaces graph_router + router,
plus the single conditional edge function that replaces both
graph_route_decision and route_decision.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.nodes.companion_router import (
    companion_route_decision,
    make_companion_router_node,
)
from core.state import RoutingDecision, SkillContext


# ── Fixtures ────────────────────────────────────────────────────────────


def _msg(content: str):
    m = MagicMock()
    m.content = content
    return m


def _config(**overrides):
    """Build a minimal GrimConfig mock."""
    cfg = MagicMock()
    cfg.routing_enabled = True
    cfg.routing_default_tier = "sonnet"
    cfg.routing_classifier_enabled = False
    cfg.routing_confidence_threshold = 0.6
    cfg.routing_timeout = 3.0
    cfg.models_disabled = []
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _state(message: str = "hello", **overrides) -> dict:
    base = {
        "messages": [_msg(message)],
        "matched_skills": [],
        "objectives": [],
        "skill_delegation_hint": None,
        "continuation_intent": None,
        "last_delegation_type": None,
        "knowledge_context": [],
        "context_summary": None,
    }
    base.update(overrides)
    return base


def _mock_model_decision(model="claude-sonnet-4-6", tier="sonnet"):
    d = MagicMock()
    d.model = model
    d.tier = tier
    d.stage = 4
    d.confidence = 0.5
    d.reason = "default"
    return d


# ── Node creation tests ─────────────────────────────────────────────────


class TestCompanionRouterNode:
    """Test the companion router node function."""

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        config = _config()
        node = make_companion_router_node(config)
        result = await node({"messages": []})
        assert result["graph_target"] == "research"
        assert result["mode"] == "companion"
        assert result["delegation_type"] is None

    @pytest.mark.asyncio
    async def test_conversation_routing(self):
        """Conversation intent → personal graph, companion mode."""
        decision = RoutingDecision(
            target_subgraph="conversation", confidence=0.9, reasoning="greeting",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("hey grim"))
        assert result["graph_target"] == "personal"
        assert result["mode"] == "companion"
        assert result["delegation_type"] is None

    @pytest.mark.asyncio
    async def test_code_routing(self):
        """Code intent → research graph, delegate mode, code."""
        decision = RoutingDecision(
            target_subgraph="code", confidence=0.9, reasoning="code request",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("implement auth"))
        assert result["graph_target"] == "research"
        assert result["mode"] == "delegate"
        assert result["delegation_type"] == "code"

    @pytest.mark.asyncio
    async def test_planning_routing(self):
        """Planning intent → planning graph, companion mode."""
        decision = RoutingDecision(
            target_subgraph="planning", confidence=0.85, reasoning="sprint plan",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("plan the sprint"))
        assert result["graph_target"] == "planning"
        assert result["mode"] == "companion"
        assert result["delegation_type"] is None

    @pytest.mark.asyncio
    async def test_research_high_confidence_delegates(self):
        """Research with high confidence → delegate to research agent."""
        decision = RoutingDecision(
            target_subgraph="research", confidence=0.8, reasoning="vault lookup",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("what do we know about PAC?"))
        assert result["graph_target"] == "research"
        assert result["mode"] == "delegate"
        assert result["delegation_type"] == "research"

    @pytest.mark.asyncio
    async def test_research_low_confidence_companion(self):
        """Research with low confidence → companion mode (no delegation)."""
        decision = RoutingDecision(
            target_subgraph="research", confidence=0.5, reasoning="maybe research",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("I was thinking about something"))
        assert result["graph_target"] == "research"
        assert result["mode"] == "companion"
        assert result["delegation_type"] is None

    @pytest.mark.asyncio
    async def test_operations_routing(self):
        """Operations intent → research graph, delegate to memory agent."""
        decision = RoutingDecision(
            target_subgraph="operations", confidence=0.85, reasoning="vault sync",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("sync the vault"))
        assert result["graph_target"] == "research"
        assert result["mode"] == "delegate"
        assert result["delegation_type"] == "memory"

    @pytest.mark.asyncio
    async def test_routing_decision_stored_in_state(self):
        """The full RoutingDecision should be stored for tracing."""
        decision = RoutingDecision(
            target_subgraph="code", confidence=0.9, reasoning="code request",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("write some code"))
        assert result["routing_decision"] is not None
        assert result["routing_decision"]["target_subgraph"] == "code"
        assert result["routing_decision"]["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_model_routing_called(self):
        """Model router should be called with correct config."""
        decision = RoutingDecision(
            target_subgraph="conversation", confidence=0.9, reasoning="chat",
        )
        mock_model = _mock_model_decision(model="claude-haiku-4-5-20251001", tier="haiku")
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=mock_model) as mock_rm:
            node = make_companion_router_node(_config())
            result = await node(_state("hi"))
            mock_rm.assert_called_once()
        assert result["selected_model"] == "claude-haiku-4-5-20251001"

    @pytest.mark.asyncio
    async def test_continuity_override(self):
        """Low-confidence decision should yield to continuity re-delegation."""
        decision = RoutingDecision(
            target_subgraph="conversation", confidence=0.4, reasoning="weak signal",
        )
        state = _state(
            "now do the next part",
            last_delegation_type="code",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(state)
        # "now " is in follow-up signals, last_delegation was code
        assert result["delegation_type"] == "code"
        assert result["mode"] == "delegate"
        assert result["graph_target"] == "research"

    @pytest.mark.asyncio
    async def test_continuity_no_override_when_confident(self):
        """High-confidence decision should NOT be overridden by continuity."""
        decision = RoutingDecision(
            target_subgraph="conversation", confidence=0.9, reasoning="clear greeting",
        )
        state = _state(
            "now how are you",
            last_delegation_type="code",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(state)
        # High confidence → no continuity override
        assert result["graph_target"] == "personal"
        assert result["mode"] == "companion"

    @pytest.mark.asyncio
    async def test_continuity_no_override_with_skill_hint(self):
        """Skill hint present → no continuity override (hint was handled in classify_intent)."""
        decision = RoutingDecision(
            target_subgraph="operations", confidence=1.0, reasoning="skill hint",
        )
        state = _state(
            "also do this",
            skill_delegation_hint="memory",
            last_delegation_type="code",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(state)
        # Skill hint → operations, not continuity override
        assert result["delegation_type"] == "memory"


# ── Edge function tests ─────────────────────────────────────────────────


class TestCompanionRouteDecision:
    """Test the unified conditional edge function — routes to subgraph names."""

    def test_personal_target(self):
        state = {"graph_target": "personal", "mode": "companion"}
        assert companion_route_decision(state) == "conversation"

    def test_planning_target(self):
        state = {"graph_target": "planning", "mode": "companion"}
        assert companion_route_decision(state) == "planning"

    def test_research_companion(self):
        state = {"graph_target": "research", "mode": "companion"}
        assert companion_route_decision(state) == "conversation"

    def test_research_delegate(self):
        state = {"graph_target": "research", "mode": "delegate", "delegation_type": "research"}
        assert companion_route_decision(state) == "research"

    def test_research_delegate_code(self):
        """Code delegation routes to code subgraph."""
        state = {"graph_target": "research", "mode": "delegate", "delegation_type": "code"}
        assert companion_route_decision(state) == "code"

    def test_default_research_companion(self):
        """Missing state fields default to conversation (companion mode)."""
        assert companion_route_decision({}) == "conversation"

    def test_personal_ignores_mode(self):
        """Personal always goes to conversation regardless of mode."""
        state = {"graph_target": "personal", "mode": "delegate"}
        assert companion_route_decision(state) == "conversation"

    def test_planning_ignores_mode(self):
        """Planning always goes to planning regardless of mode."""
        state = {"graph_target": "planning", "mode": "delegate"}
        assert companion_route_decision(state) == "planning"


# ── Integration: edge function matches node output ──────────────────────


class TestEdgeFunctionIntegration:
    """Verify edge function produces correct destinations for each routing path."""

    @pytest.mark.asyncio
    async def test_conversation_path(self):
        decision = RoutingDecision(
            target_subgraph="conversation", confidence=0.9, reasoning="chat",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("hey"))
        assert companion_route_decision(result) == "conversation"

    @pytest.mark.asyncio
    async def test_code_path(self):
        decision = RoutingDecision(
            target_subgraph="code", confidence=0.9, reasoning="code",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("implement auth"))
        assert companion_route_decision(result) == "code"

    @pytest.mark.asyncio
    async def test_planning_path(self):
        decision = RoutingDecision(
            target_subgraph="planning", confidence=0.85, reasoning="plan",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("plan sprint"))
        assert companion_route_decision(result) == "planning"

    @pytest.mark.asyncio
    async def test_research_companion_path(self):
        decision = RoutingDecision(
            target_subgraph="research", confidence=0.5, reasoning="maybe",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("hmm"))
        assert companion_route_decision(result) == "conversation"

    @pytest.mark.asyncio
    async def test_research_delegate_path(self):
        decision = RoutingDecision(
            target_subgraph="research", confidence=0.85, reasoning="vault query",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("search the vault"))
        assert companion_route_decision(result) == "research"

    @pytest.mark.asyncio
    async def test_operations_delegate_path(self):
        decision = RoutingDecision(
            target_subgraph="operations", confidence=0.9, reasoning="vault op",
        )
        with patch("core.nodes.companion_router.classify_intent", return_value=decision), \
             patch("core.nodes.companion_router.route_model", return_value=_mock_model_decision()):
            node = make_companion_router_node(_config())
            result = await node(_state("capture this"))
        assert companion_route_decision(result) == "research"
