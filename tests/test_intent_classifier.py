"""Tests for intent classifier — structured output routing.

Tests all three tiers:
  1. Hard overrides (skill_delegation_hint)
  2. LLM structured output (mocked)
  3. Keyword fallback
Plus resolution helpers and edge cases.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.nodes.intent_classifier import (
    DELEGATION_TO_TARGET,
    HINT_TO_TARGET,
    SUBGRAPH_DESCRIPTIONS,
    TARGET_TO_DELEGATION,
    _build_context_block,
    _keyword_fallback,
    classify_intent,
    resolve_delegation_target,
    resolve_graph_target,
    resolve_mode,
)
from core.state import (
    Objective,
    ObjectiveStatus,
    RoutingDecision,
    SkillContext,
)


# ── Fixtures ────────────────────────────────────────────────────────────


def _msg(content: str):
    """Create a minimal message-like object."""
    m = MagicMock()
    m.content = content
    return m


def _state(message: str = "hello", **overrides) -> dict:
    """Build a minimal GrimState dict for testing."""
    base = {
        "messages": [_msg(message)],
        "matched_skills": [],
        "objectives": [],
        "skill_delegation_hint": None,
        "continuation_intent": None,
    }
    base.update(overrides)
    return base


# ── RoutingDecision model tests ─────────────────────────────────────────


class TestRoutingDecisionTargets:
    """Validate RoutingDecision target constraints."""

    def test_valid_targets(self):
        for target in ["conversation", "research", "code", "operations", "planning"]:
            d = RoutingDecision(target_subgraph=target, confidence=0.8, reasoning="test")
            assert d.target_subgraph == target

    def test_invalid_target(self):
        with pytest.raises(Exception):
            RoutingDecision(target_subgraph="invalid", confidence=0.8, reasoning="test")

    def test_confidence_bounds(self):
        d = RoutingDecision(target_subgraph="research", confidence=0.0, reasoning="low")
        assert d.confidence == 0.0
        d = RoutingDecision(target_subgraph="research", confidence=1.0, reasoning="high")
        assert d.confidence == 1.0

    def test_confidence_out_of_bounds(self):
        with pytest.raises(Exception):
            RoutingDecision(target_subgraph="research", confidence=1.5, reasoning="too high")
        with pytest.raises(Exception):
            RoutingDecision(target_subgraph="research", confidence=-0.1, reasoning="too low")


# ── Mapping tests ───────────────────────────────────────────────────────


class TestMappings:
    """Validate mapping dicts are complete and consistent."""

    def test_all_targets_have_delegation_mapping(self):
        """Every target in RoutingDecision should have a delegation mapping."""
        for target in ["conversation", "research", "code", "operations", "planning"]:
            assert target in TARGET_TO_DELEGATION

    def test_all_hints_have_target_mapping(self):
        """All known skill delegation hints map to a target."""
        known_hints = [
            "memory", "research", "code",
            "operate", "audit", "codebase", "planning",
        ]
        for hint in known_hints:
            assert hint in HINT_TO_TARGET, f"Missing hint mapping: {hint}"

    def test_delegation_to_target_covers_keyword_types(self):
        """Keyword delegation types should map to targets."""
        from core.nodes.keyword_router import DELEGATION_KEYWORDS
        for dtype in DELEGATION_KEYWORDS:
            assert dtype in DELEGATION_TO_TARGET, f"Missing delegation mapping: {dtype}"

    def test_target_to_delegation_values(self):
        """Check specific delegation mappings."""
        assert TARGET_TO_DELEGATION["conversation"] is None
        assert TARGET_TO_DELEGATION["planning"] is None
        assert TARGET_TO_DELEGATION["code"] == "code"
        assert TARGET_TO_DELEGATION["research"] == "research"
        assert TARGET_TO_DELEGATION["operations"] == "memory"


# ── Tier 1: Hard override tests ────────────────────────────────────────


class TestTier1HardOverrides:
    """Skill delegation hint bypasses LLM entirely."""

    @pytest.mark.asyncio
    async def test_skill_hint_memory(self):
        state = _state("capture this note", skill_delegation_hint="memory")
        result = await classify_intent(state)
        assert result.target_subgraph == "operations"
        assert result.confidence == 1.0
        assert "skill" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_skill_hint_code(self):
        state = _state("run this command", skill_delegation_hint="code")
        result = await classify_intent(state)
        assert result.target_subgraph == "code"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_skill_hint_research(self):
        state = _state("deep dive into PAC", skill_delegation_hint="research")
        result = await classify_intent(state)
        assert result.target_subgraph == "research"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_skill_hint_planning(self):
        state = _state("plan the sprint", skill_delegation_hint="planning")
        result = await classify_intent(state)
        assert result.target_subgraph == "planning"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_skill_hint_operate(self):
        state = _state("git status", skill_delegation_hint="operate")
        result = await classify_intent(state)
        assert result.target_subgraph == "code"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_skill_hint_codebase(self):
        state = _state("look at the code", skill_delegation_hint="codebase")
        result = await classify_intent(state)
        assert result.target_subgraph == "research"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_skill_hint_unknown_defaults_to_research(self):
        state = _state("do something", skill_delegation_hint="unknown_hint")
        result = await classify_intent(state)
        assert result.target_subgraph == "research"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_skill_hint_skips_llm_call(self):
        """Hard override should NOT call the LLM."""
        state = _state("capture this", skill_delegation_hint="memory")
        with patch("core.nodes.intent_classifier._llm_classify") as mock_llm:
            result = await classify_intent(state)
            mock_llm.assert_not_called()
        assert result.target_subgraph == "operations"


# ── Tier 2: LLM classification tests (mocked) ──────────────────────────


class TestTier2LLMClassification:
    """LLM structured output via mock — no real API calls."""

    @pytest.mark.asyncio
    async def test_llm_returns_routing_decision(self):
        expected = RoutingDecision(
            target_subgraph="code",
            confidence=0.9,
            reasoning="User wants to implement a feature",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(_state("implement the auth module"))
        assert result.target_subgraph == "code"
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_llm_conversation_classification(self):
        expected = RoutingDecision(
            target_subgraph="conversation",
            confidence=0.95,
            reasoning="Casual greeting",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(_state("hey how are you"))
        assert result.target_subgraph == "conversation"

    @pytest.mark.asyncio
    async def test_llm_planning_classification(self):
        expected = RoutingDecision(
            target_subgraph="planning",
            confidence=0.85,
            reasoning="Sprint planning request",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(_state("let's plan the next sprint"))
        assert result.target_subgraph == "planning"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_keywords(self):
        """If LLM throws, fall back to keyword matching."""
        with patch("core.nodes.intent_classifier._llm_classify", side_effect=Exception("API error")):
            result = await classify_intent(_state("write code for the auth module"))
        # "write code" matches code keyword → code
        assert result.target_subgraph == "code"
        assert result.confidence < 1.0  # fallback confidence is lower

    @pytest.mark.asyncio
    async def test_llm_returns_none_falls_back(self):
        """If LLM returns None (bad output), fall back to keywords."""
        with patch("core.nodes.intent_classifier._llm_classify", return_value=None):
            result = await classify_intent(_state("analyze this experiment"))
        # "analyze this" matches research keyword
        assert result.target_subgraph == "research"

    @pytest.mark.asyncio
    async def test_continuation_enrichment(self):
        """Continuation info should be added to LLM result."""
        expected = RoutingDecision(
            target_subgraph="code",
            confidence=0.8,
            reasoning="Continue coding",
        )
        state = _state(
            "keep going",
            continuation_intent="code",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(state)
        assert result.is_continuation is True

    @pytest.mark.asyncio
    async def test_llm_receives_skill_context(self):
        """Matched skills should be passed to the LLM context."""
        skill = SkillContext(
            name="vault-sync",
            version="1.0",
            description="Sync vault",
            permissions=["vault:write"],
        )
        state = _state("sync the vault", matched_skills=[skill])
        expected = RoutingDecision(
            target_subgraph="operations",
            confidence=0.9,
            reasoning="Vault sync skill matched",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected) as mock_llm:
            result = await classify_intent(state)
            # Verify _llm_classify was called
            mock_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_receives_active_objectives(self):
        """Active objectives should influence classification context."""
        obj = Objective(
            title="Implement auth module",
            status=ObjectiveStatus.ACTIVE,
            target_subgraph="code",
        )
        state = _state("let's continue", objectives=[obj])
        expected = RoutingDecision(
            target_subgraph="code",
            confidence=0.85,
            reasoning="Active objective targets code",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(state)
        assert result.target_subgraph == "code"


# ── Tier 3: Keyword fallback tests ─────────────────────────────────────


class TestTier3KeywordFallback:
    """Direct tests of _keyword_fallback function."""

    def test_memory_keyword(self):
        result = _keyword_fallback("capture this in the vault")
        assert result.target_subgraph == "operations"
        assert result.confidence == 0.7

    def test_research_keyword(self):
        result = _keyword_fallback("analyze this experiment")
        assert result.target_subgraph == "research"
        assert result.confidence == 0.7

    def test_code_keyword(self):
        result = _keyword_fallback("write code for auth")
        assert result.target_subgraph == "code"
        assert result.confidence == 0.7

    def test_operate_keyword(self):
        result = _keyword_fallback("git status please")
        assert result.target_subgraph == "code"
        assert result.confidence == 0.7

    def test_action_intent_match(self):
        result = _keyword_fallback("run the command on the server")
        assert result.target_subgraph == "code"
        assert result.confidence == 0.7

    def test_personal_signal(self):
        result = _keyword_fallback("how are you doing today")
        assert result.target_subgraph == "conversation"
        assert result.confidence == 0.6

    def test_personal_signal_with_research_override(self):
        """Personal signal suppressed by research keyword."""
        result = _keyword_fallback("how are you, can you analyze this experiment")
        # "analyze this" keyword match takes priority over personal signal
        assert result.target_subgraph == "research"

    def test_planning_signal(self):
        result = _keyword_fallback("let's plan the next sprint")
        assert result.target_subgraph == "planning"
        assert result.confidence == 0.7

    def test_short_message_defaults_to_conversation(self):
        result = _keyword_fallback("hi")
        assert result.target_subgraph == "conversation"
        assert result.confidence == 0.4

    def test_long_ambiguous_defaults_to_research(self):
        result = _keyword_fallback("I've been thinking about the relationship between entropy and information density in physical systems")
        assert result.target_subgraph == "research"
        assert result.confidence == 0.4

    def test_continuation_flag_preserved(self):
        result = _keyword_fallback("write code for this", is_continuation=True)
        assert result.is_continuation is True

    def test_codebase_keyword(self):
        result = _keyword_fallback("look at the code in the repo")
        assert result.target_subgraph == "research"


# ── Context block builder tests ─────────────────────────────────────────


class TestContextBlockBuilder:
    """Test the context block construction for LLM prompts."""

    def test_empty_context(self):
        block = _build_context_block(
            matched_skills=[], active_objectives=[],
            recent_messages=[], is_continuation=False,
        )
        assert block == "No additional context."

    def test_with_skills(self):
        skills = [
            SkillContext(name="vault-sync", version="1.0", description="Sync", permissions=[]),
            SkillContext(name="deep-ingest", version="1.0", description="Ingest", permissions=[]),
        ]
        block = _build_context_block(
            matched_skills=skills, active_objectives=[],
            recent_messages=[], is_continuation=False,
        )
        assert "vault-sync" in block
        assert "deep-ingest" in block

    def test_with_objectives(self):
        objs = [
            Objective(title="Build auth", status=ObjectiveStatus.ACTIVE, target_subgraph="code"),
        ]
        block = _build_context_block(
            matched_skills=[], active_objectives=objs,
            recent_messages=[], is_continuation=False,
        )
        assert "Build auth" in block
        assert "code" in block

    def test_with_continuation(self):
        block = _build_context_block(
            matched_skills=[], active_objectives=[],
            recent_messages=[], is_continuation=True,
        )
        assert "continuation" in block.lower()

    def test_with_conversation_depth(self):
        msgs = [_msg("a"), _msg("b"), _msg("c")]
        block = _build_context_block(
            matched_skills=[], active_objectives=[],
            recent_messages=msgs, is_continuation=False,
        )
        assert "3 messages" in block

    def test_skill_cap_at_5(self):
        skills = [
            SkillContext(name=f"skill-{i}", version="1.0", description="", permissions=[])
            for i in range(10)
        ]
        block = _build_context_block(
            matched_skills=skills, active_objectives=[],
            recent_messages=[], is_continuation=False,
        )
        # Should only show first 5
        assert "skill-4" in block
        assert "skill-5" not in block

    def test_objective_cap_at_3(self):
        objs = [
            Objective(title=f"Task {i}", status=ObjectiveStatus.ACTIVE, target_subgraph="code")
            for i in range(5)
        ]
        block = _build_context_block(
            matched_skills=[], active_objectives=objs,
            recent_messages=[], is_continuation=False,
        )
        assert "Task 2" in block
        assert "Task 3" not in block


# ── Resolution helper tests ─────────────────────────────────────────────


class TestResolveDelegationTarget:
    """Test RoutingDecision → concrete delegation target."""

    def test_conversation_no_delegation(self):
        d = RoutingDecision(target_subgraph="conversation", confidence=0.9, reasoning="chat")
        assert resolve_delegation_target(d) is None

    def test_planning_no_delegation(self):
        d = RoutingDecision(target_subgraph="planning", confidence=0.9, reasoning="plan")
        assert resolve_delegation_target(d) is None

    def test_code_to_code(self):
        d = RoutingDecision(target_subgraph="code", confidence=0.9, reasoning="code")
        assert resolve_delegation_target(d) == "code"

    def test_research_to_research(self):
        d = RoutingDecision(target_subgraph="research", confidence=0.9, reasoning="research")
        assert resolve_delegation_target(d) == "research"

    def test_operations_to_memory(self):
        d = RoutingDecision(target_subgraph="operations", confidence=0.9, reasoning="ops")
        assert resolve_delegation_target(d) == "memory"


class TestResolveGraphTarget:
    """Test RoutingDecision → graph-level routing (v0.0.6 compat bridge)."""

    def test_conversation_to_personal(self):
        d = RoutingDecision(target_subgraph="conversation", confidence=0.9, reasoning="chat")
        assert resolve_graph_target(d) == "personal"

    def test_planning_to_planning(self):
        d = RoutingDecision(target_subgraph="planning", confidence=0.9, reasoning="plan")
        assert resolve_graph_target(d) == "planning"

    def test_code_to_research(self):
        """Code still routes through research graph in v0.0.6 topology."""
        d = RoutingDecision(target_subgraph="code", confidence=0.9, reasoning="code")
        assert resolve_graph_target(d) == "research"

    def test_operations_to_research(self):
        """Operations still routes through research graph in v0.0.6 topology."""
        d = RoutingDecision(target_subgraph="operations", confidence=0.9, reasoning="ops")
        assert resolve_graph_target(d) == "research"

    def test_research_to_research(self):
        d = RoutingDecision(target_subgraph="research", confidence=0.9, reasoning="research")
        assert resolve_graph_target(d) == "research"


class TestResolveMode:
    """Test RoutingDecision → companion/delegate mode."""

    def test_conversation_companion(self):
        d = RoutingDecision(target_subgraph="conversation", confidence=0.9, reasoning="chat")
        assert resolve_mode(d) == "companion"

    def test_planning_companion(self):
        d = RoutingDecision(target_subgraph="planning", confidence=0.9, reasoning="plan")
        assert resolve_mode(d) == "companion"

    def test_code_delegate(self):
        d = RoutingDecision(target_subgraph="code", confidence=0.9, reasoning="code")
        assert resolve_mode(d) == "delegate"

    def test_operations_delegate(self):
        d = RoutingDecision(target_subgraph="operations", confidence=0.9, reasoning="ops")
        assert resolve_mode(d) == "delegate"

    def test_research_high_confidence_delegate(self):
        d = RoutingDecision(target_subgraph="research", confidence=0.8, reasoning="research")
        assert resolve_mode(d) == "delegate"

    def test_research_low_confidence_companion(self):
        d = RoutingDecision(target_subgraph="research", confidence=0.5, reasoning="maybe research")
        assert resolve_mode(d) == "companion"


# ── Edge cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        state = {"messages": []}
        result = await classify_intent(state)
        assert result.target_subgraph == "conversation"
        assert result.confidence == 0.5

    @pytest.mark.asyncio
    async def test_no_messages_key(self):
        state = {}
        result = await classify_intent(state)
        assert result.target_subgraph == "conversation"

    @pytest.mark.asyncio
    async def test_message_as_string(self):
        """Messages that aren't proper message objects should still work."""
        msg = MagicMock()
        msg.content = "implement the thing"
        state = _state("implement the thing")

        expected = RoutingDecision(
            target_subgraph="code", confidence=0.9, reasoning="Implementation request",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(state)
        assert result.target_subgraph == "code"

    @pytest.mark.asyncio
    async def test_model_override(self):
        """Custom model name should be passed to LLM call."""
        expected = RoutingDecision(
            target_subgraph="research", confidence=0.8, reasoning="test",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected) as mock:
            await classify_intent(_state("test"), model_name="claude-sonnet-4-6")
            call_kwargs = mock.call_args[1]
            assert call_kwargs["model_name"] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_subgraph_descriptions_not_empty(self):
        """Subgraph descriptions should contain all target names."""
        for target in ["conversation", "research", "code", "operations", "planning"]:
            assert target in SUBGRAPH_DESCRIPTIONS


# ── Full integration flow tests (mocked LLM) ────────────────────────────


class TestIntegrationFlow:
    """End-to-end classify_intent with mocked LLM for different user intents."""

    @pytest.mark.asyncio
    async def test_coding_request(self):
        expected = RoutingDecision(
            target_subgraph="code", confidence=0.92, reasoning="Explicit implementation request",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(_state("refactor the auth module to use JWT"))
        assert result.target_subgraph == "code"
        assert resolve_delegation_target(result) == "code"
        assert resolve_graph_target(result) == "research"
        assert resolve_mode(result) == "delegate"

    @pytest.mark.asyncio
    async def test_greeting(self):
        expected = RoutingDecision(
            target_subgraph="conversation", confidence=0.95, reasoning="Casual greeting",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(_state("hey grim, how are you doing?"))
        assert result.target_subgraph == "conversation"
        assert resolve_delegation_target(result) is None
        assert resolve_graph_target(result) == "personal"
        assert resolve_mode(result) == "companion"

    @pytest.mark.asyncio
    async def test_vault_operation(self):
        expected = RoutingDecision(
            target_subgraph="operations", confidence=0.88, reasoning="Vault sync operation",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(_state("sync the vault after those changes"))
        assert result.target_subgraph == "operations"
        assert resolve_delegation_target(result) == "memory"
        assert resolve_graph_target(result) == "research"
        assert resolve_mode(result) == "delegate"

    @pytest.mark.asyncio
    async def test_sprint_planning(self):
        expected = RoutingDecision(
            target_subgraph="planning", confidence=0.9, reasoning="Sprint planning request",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(_state("let's plan out the next sprint"))
        assert result.target_subgraph == "planning"
        assert resolve_delegation_target(result) is None
        assert resolve_graph_target(result) == "planning"
        assert resolve_mode(result) == "companion"

    @pytest.mark.asyncio
    async def test_research_query(self):
        expected = RoutingDecision(
            target_subgraph="research", confidence=0.85, reasoning="Knowledge retrieval",
        )
        with patch("core.nodes.intent_classifier._llm_classify", return_value=expected):
            result = await classify_intent(_state("what do we know about PAC regulation?"))
        assert result.target_subgraph == "research"
        assert resolve_delegation_target(result) == "research"
        assert resolve_graph_target(result) == "research"
        assert resolve_mode(result) == "delegate"

    @pytest.mark.asyncio
    async def test_skill_hint_overrides_everything(self):
        """Even with LLM mock, skill hint should win."""
        state = _state("hello", skill_delegation_hint="code")
        # Don't even patch LLM — it shouldn't be called
        result = await classify_intent(state)
        assert result.target_subgraph == "code"
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_full_fallback_chain(self):
        """LLM fails + no keyword match → default to conversation/research."""
        with patch("core.nodes.intent_classifier._llm_classify", side_effect=Exception("timeout")):
            result = await classify_intent(_state("hmm"))
        # "hmm" is short, no keywords → conversation
        assert result.target_subgraph == "conversation"
        assert result.confidence == 0.4
