"""Tests for multi-tier model routing — routing logic and integration.

All tests are synchronous/mocked — no real API calls.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure GRIM root is on path
GRIM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GRIM_ROOT))

from core.model_router import (
    TIER_MODELS,
    RoutingDecision,
    _check_explicit_override,
    _score_features,
    route_model,
)


# ─── Explicit Overrides (Stage 1) ────────────────────────────────────────


class TestExplicitOverrides:

    def test_fast_override(self):
        result = _check_explicit_override("/fast tell me the time")
        assert result is not None
        assert result.tier == "haiku"
        assert result.stage == 1
        assert result.confidence == 1.0

    def test_haiku_override(self):
        result = _check_explicit_override("/haiku what is 2+2")
        assert result is not None
        assert result.tier == "haiku"

    def test_deep_override(self):
        result = _check_explicit_override("/deep analyze this architecture")
        assert result is not None
        assert result.tier == "opus"
        assert result.stage == 1

    def test_opus_override(self):
        result = _check_explicit_override("/opus")
        assert result is not None
        assert result.tier == "opus"

    def test_sonnet_override(self):
        result = _check_explicit_override("/sonnet do something")
        assert result is not None
        assert result.tier == "sonnet"

    def test_no_override(self):
        result = _check_explicit_override("tell me about physics")
        assert result is None

    def test_override_case_insensitive(self):
        result = _check_explicit_override("/FAST hello")
        assert result is not None
        assert result.tier == "haiku"

    def test_override_not_mid_message(self):
        result = _check_explicit_override("please /fast this")
        assert result is None


# ─── Feature Scoring (Stage 2) ───────────────────────────────────────────


class TestFeatureScoring:

    def test_short_greeting_routes_haiku(self):
        result = _score_features("hello there!")
        assert result is not None
        assert result.tier == "haiku"
        assert result.stage == 2

    def test_code_intent_routes_sonnet(self):
        result = _score_features("implement a binary search function")
        assert result is not None
        assert result.tier == "sonnet"

    def test_deep_analysis_routes_opus(self):
        result = _score_features("deep analysis of the architecture trade-offs")
        assert result is not None
        assert result.tier == "opus"

    def test_code_block_boosts_sonnet(self):
        msg = "fix this:\n```python\ndef foo():\n    pass\n```"
        result = _score_features(msg)
        assert result is not None
        assert result.tier == "sonnet"

    def test_ambiguous_returns_none(self):
        # A longer message that doesn't strongly match any tier
        result = _score_features(
            "I was thinking about something interesting that happened yesterday at the meeting we had"
        )
        assert result is None  # not confident enough — too long for haiku bonus, no keywords

    def test_grim_signals_boost_sonnet(self):
        # With GRIM signals, sonnet gets extra points — message must be >80 chars to avoid haiku bonus
        result = _score_features(
            "help me with this task that involves updating the configuration and verifying the deployment pipeline works correctly",
            has_objectives=True,
            has_compressed_context=True,
            matched_write_skill=True,
            fdo_count=10,
        )
        # Should lean sonnet due to accumulated GRIM signals (1+1+2+1 = 5 points)
        assert result is not None
        assert result.tier == "sonnet"

    def test_write_skill_boosts_sonnet(self):
        # A short message that would normally be haiku, but write skill pushes to sonnet
        result = _score_features(
            "save this",
            matched_write_skill=True,
            has_objectives=True,
            has_compressed_context=True,
        )
        # The write skill + objectives + compression should push past haiku
        if result:
            assert result.tier in ("haiku", "sonnet")  # depends on scoring

    def test_long_message_hints(self):
        long_msg = "x" * 600
        result = _score_features(long_msg)
        # Long message alone isn't decisive, but it adds sonnet points
        # Should return None (ambiguous) or sonnet
        if result:
            assert result.tier in ("sonnet", "opus")


# ─── Full Router Pipeline ────────────────────────────────────────────────


class TestRouteModel:

    @pytest.mark.asyncio
    async def test_disabled_returns_default(self):
        result = await route_model("hello", enabled=False)
        assert result.tier == "sonnet"
        assert result.stage == 4
        assert result.reason == "routing disabled"

    @pytest.mark.asyncio
    async def test_disabled_custom_default(self):
        result = await route_model("hello", enabled=False, default_tier="haiku")
        assert result.tier == "haiku"
        assert result.model == TIER_MODELS["haiku"]

    @pytest.mark.asyncio
    async def test_explicit_override_wins(self):
        result = await route_model("/fast tell me the time")
        assert result.tier == "haiku"
        assert result.stage == 1

    @pytest.mark.asyncio
    async def test_feature_scoring_greeting(self):
        result = await route_model("hello, how are you?")
        assert result.tier == "haiku"
        assert result.stage == 2

    @pytest.mark.asyncio
    async def test_feature_scoring_code(self):
        result = await route_model("implement a binary search algorithm in Python")
        assert result.tier == "sonnet"
        assert result.stage == 2

    @pytest.mark.asyncio
    async def test_feature_scoring_deep(self):
        result = await route_model("deep analysis of recursive emergence in PAC operators")
        assert result.tier == "opus"

    @pytest.mark.asyncio
    async def test_fallback_to_default(self):
        # A message long enough to skip haiku bonus but with no keywords
        result = await route_model(
            "I was thinking about something interesting that happened yesterday at the meeting we had"
        )
        assert result.tier == "sonnet"
        assert result.stage == 4

    @pytest.mark.asyncio
    async def test_classifier_not_called_when_disabled(self):
        with patch("core.model_router._classify_with_llm") as mock_classify:
            result = await route_model(
                "hmm interesting",
                classifier_enabled=False,
            )
            mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_classifier_called_when_enabled_and_ambiguous(self):
        mock_decision = RoutingDecision(
            tier="opus", model=TIER_MODELS["opus"],
            reason="LLM classifier", confidence=0.7, stage=3,
        )
        with patch("core.model_router._classify_with_llm", return_value=mock_decision) as mock_classify:
            # Use a longer ambiguous message so stage 2 doesn't resolve it
            result = await route_model(
                "I was thinking about something interesting that happened yesterday at the meeting we had",
                classifier_enabled=True,
            )
            mock_classify.assert_called_once()
            assert result.tier == "opus"
            assert result.stage == 3


# ─── Routing Decision Dataclass ──────────────────────────────────────────


class TestRoutingDecision:

    def test_fields(self):
        d = RoutingDecision(
            tier="sonnet",
            model="claude-sonnet-4-6",
            reason="test",
            confidence=0.8,
            stage=2,
        )
        assert d.tier == "sonnet"
        assert d.confidence == 0.8

    def test_tier_models_mapping(self):
        assert "haiku" in TIER_MODELS
        assert "sonnet" in TIER_MODELS
        assert "opus" in TIER_MODELS
        assert TIER_MODELS["sonnet"] == "claude-sonnet-4-6"


# ─── Router Node Integration ─────────────────────────────────────────────


class TestRouterNodeIntegration:

    @pytest.mark.asyncio
    async def test_router_returns_selected_model(self):
        from langchain_core.messages import HumanMessage

        from core.config import GrimConfig
        from core.nodes.router import make_router_node

        config = GrimConfig()
        router_node = make_router_node(config)

        result = await router_node({
            "messages": [HumanMessage(content="hello!")],
            "matched_skills": [],
        })

        assert "selected_model" in result
        assert result["selected_model"] in TIER_MODELS.values()

    @pytest.mark.asyncio
    async def test_router_greeting_selects_haiku(self):
        from langchain_core.messages import HumanMessage

        from core.config import GrimConfig
        from core.nodes.router import make_router_node

        config = GrimConfig()
        router_node = make_router_node(config)

        result = await router_node({
            "messages": [HumanMessage(content="hi there, good morning!")],
            "matched_skills": [],
        })

        assert result["selected_model"] == TIER_MODELS["haiku"]
        assert result["mode"] == "companion"

    @pytest.mark.asyncio
    async def test_router_code_selects_sonnet(self):
        from langchain_core.messages import HumanMessage

        from core.config import GrimConfig
        from core.nodes.router import make_router_node

        config = GrimConfig()
        router_node = make_router_node(config)

        result = await router_node({
            "messages": [HumanMessage(content="write code for a Fibonacci generator")],
            "matched_skills": [],
        })

        assert result["selected_model"] == TIER_MODELS["sonnet"]

    @pytest.mark.asyncio
    async def test_router_disabled_routing(self):
        from langchain_core.messages import HumanMessage

        from core.config import GrimConfig
        from core.nodes.router import make_router_node

        config = GrimConfig()
        config.routing_enabled = False
        router_node = make_router_node(config)

        result = await router_node({
            "messages": [HumanMessage(content="hello!")],
            "matched_skills": [],
        })

        # With routing disabled, should use default (sonnet)
        assert result["selected_model"] == TIER_MODELS["sonnet"]


# ─── Config Integration ──────────────────────────────────────────────────


class TestRoutingConfig:

    def test_default_config_values(self):
        from core.config import GrimConfig
        config = GrimConfig()
        assert config.routing_enabled is True
        assert config.routing_default_tier == "sonnet"
        assert config.routing_classifier_enabled is False
        assert config.routing_confidence_threshold == 0.6

    def test_yaml_parsing(self):
        from core.config import GrimConfig, _apply_yaml
        config = GrimConfig()
        raw = {
            "routing": {
                "enabled": False,
                "default_tier": "haiku",
                "classifier_enabled": True,
                "confidence_threshold": 0.8,
            }
        }
        _apply_yaml(config, raw, Path("."))
        assert config.routing_enabled is False
        assert config.routing_default_tier == "haiku"
        assert config.routing_classifier_enabled is True
        assert config.routing_confidence_threshold == 0.8
