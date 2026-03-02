"""Tests for graph-level routing and personal companion node."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.nodes.graph_router import (
    PERSONAL_SIGNALS,
    PLANNING_SIGNALS,
    _RESEARCH_OVERRIDES,
    _has_delegation_keywords,
    graph_route_decision,
    graph_router_node,
)
from core.nodes.personal_companion import (
    PERSONAL_MODE_PREAMBLE,
    make_personal_companion_node,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_state(message: str = "", skill_hint: str = None, **extras):
    """Build a minimal GrimState dict for testing."""
    msg = MagicMock()
    msg.content = message
    state = {"messages": [msg]} if message else {"messages": []}
    if skill_hint:
        state["skill_delegation_hint"] = skill_hint
    state.update(extras)
    return state


# ── Graph Router Node ────────────────────────────────────────────────────

class TestGraphRouterNode:
    """Test graph_router_node classification logic."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "how are you",
        "good morning",
        "i'm feeling stressed today",
        "just venting about stuff",
        "let's chat",
        "i'm frustrated with everything",
        "thanks for listening",
        "who are you",
        "hello grim",
        "how's your day",
        "i'm excited about something",
        "just checking in",
    ])
    async def test_personal_signals_route_to_personal(self, message):
        state = _make_state(message)
        result = await graph_router_node(state)
        assert result["graph_target"] == "personal", f"Expected personal for: {message!r}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "search the vault for PAC",
        "what is the RBF operator",
        "tell me about dawn field theory",
        "how does the experiment work",
        "fix this code please",
        "create a story for auth",
        "git status",
        "run the tests",
        "analyze this paper",
        "deploy the application",
    ])
    async def test_research_messages_route_to_research(self, message):
        state = _make_state(message)
        result = await graph_router_node(state)
        assert result["graph_target"] == "research", f"Expected research for: {message!r}"

    @pytest.mark.asyncio
    async def test_skill_hint_overrides_to_research(self):
        state = _make_state("how are you", skill_hint="memory")
        result = await graph_router_node(state)
        assert result["graph_target"] == "research"

    @pytest.mark.asyncio
    async def test_empty_messages_default_to_research(self):
        state = _make_state("")
        state["messages"] = []
        result = await graph_router_node(state)
        assert result["graph_target"] == "research"

    @pytest.mark.asyncio
    async def test_ambiguous_defaults_to_research(self):
        """Unknown messages should default to research (zero regression risk)."""
        state = _make_state("the quick brown fox jumps over the lazy dog")
        result = await graph_router_node(state)
        assert result["graph_target"] == "research"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "i'm frustrated with the experiment results",
        "i feel like the vault needs reorganizing",
        "i'm excited about the dawn field theory breakthrough",
        "just chatting about the code architecture",
    ])
    async def test_personal_with_research_override(self, message):
        """Personal signals overridden by research content → research."""
        state = _make_state(message)
        result = await graph_router_node(state)
        assert result["graph_target"] == "research", f"Expected research override for: {message!r}"

    @pytest.mark.asyncio
    async def test_delegation_keywords_route_to_research(self):
        """Existing delegation keywords always route to research."""
        state = _make_state("remember this concept")
        result = await graph_router_node(state)
        assert result["graph_target"] == "research"

    @pytest.mark.asyncio
    async def test_action_intent_routes_to_research(self):
        """Action-intent patterns route to research."""
        state = _make_state("run the shell command now")
        result = await graph_router_node(state)
        assert result["graph_target"] == "research"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("message", [
        "plan the sprint",
        "scope this work",
        "plan this implementation",
        "let's plan this out",
        "break this down into stories",
        "groom the backlog",
    ])
    async def test_planning_signals_route_to_planning(self, message):
        """Planning signals route to planning graph (v0.0.6 Phase 2)."""
        state = _make_state(message)
        result = await graph_router_node(state)
        assert result["graph_target"] == "planning", f"Expected planning for: {message!r}"

    @pytest.mark.asyncio
    async def test_personal_with_exact_planning_override(self):
        """Personal signal + exact planning signal → planning wins (checked first)."""
        state = _make_state("i'm excited, let's plan the sprint")
        result = await graph_router_node(state)
        # Planning signals are checked before personal signals
        assert result["graph_target"] == "planning"

    @pytest.mark.asyncio
    async def test_personal_with_fuzzy_planning_stays_personal(self):
        """Personal signal + near-miss planning content → personal (no exact match)."""
        state = _make_state("i'm excited about planning the sprint")
        result = await graph_router_node(state)
        # "planning the sprint" doesn't match "plan the sprint" (substring match)
        assert result["graph_target"] == "personal"

    @pytest.mark.asyncio
    async def test_skill_hint_planning_routes_to_planning(self):
        """Skill hint 'planning' routes to planning graph."""
        state = _make_state("anything", skill_hint="planning")
        result = await graph_router_node(state)
        assert result["graph_target"] == "planning"


# ── Graph Route Decision ─────────────────────────────────────────────────

class TestGraphRouteDecision:
    """Test graph_route_decision conditional edge function."""

    def test_research_target(self):
        assert graph_route_decision({"graph_target": "research"}) == "research"

    def test_personal_target(self):
        assert graph_route_decision({"graph_target": "personal"}) == "personal"

    def test_planning_target(self):
        assert graph_route_decision({"graph_target": "planning"}) == "planning"

    def test_default_when_missing(self):
        assert graph_route_decision({}) == "research"


# ── Has Delegation Keywords ──────────────────────────────────────────────

class TestHasDelegationKeywords:
    """Test _has_delegation_keywords helper."""

    def test_matches_memory_keyword(self):
        assert _has_delegation_keywords("capture this idea")

    def test_matches_code_keyword(self):
        assert _has_delegation_keywords("write code for parser")

    def test_no_match_for_casual(self):
        assert not _has_delegation_keywords("how are you today")


# ── Personal Signals Coverage ────────────────────────────────────────────

class TestPersonalSignals:
    """Verify personal signal keywords are reasonable."""

    def test_signals_are_lowercase(self):
        for sig in PERSONAL_SIGNALS:
            assert sig == sig.lower(), f"Signal should be lowercase: {sig!r}"

    def test_signals_are_nonempty(self):
        for sig in PERSONAL_SIGNALS:
            assert len(sig.strip()) > 0

    def test_no_duplicates(self):
        assert len(PERSONAL_SIGNALS) == len(set(PERSONAL_SIGNALS))

    def test_research_overrides_are_nonempty(self):
        assert len(_RESEARCH_OVERRIDES) > 0


# ── Personal Companion Node ──────────────────────────────────────────────

class TestPersonalCompanionNode:
    """Test make_personal_companion_node factory and behavior."""

    def test_preamble_content(self):
        """Preamble should mention personal companion mode."""
        assert "Personal Companion" in PERSONAL_MODE_PREAMBLE
        assert "task board" in PERSONAL_MODE_PREAMBLE.lower()
        assert "warm" in PERSONAL_MODE_PREAMBLE.lower()

    def test_factory_returns_callable(self):
        config = MagicMock()
        config.model = "claude-sonnet-4-6"
        config.temperature = 0.7
        config.max_tokens = 4096
        node = make_personal_companion_node(config)
        assert callable(node)
