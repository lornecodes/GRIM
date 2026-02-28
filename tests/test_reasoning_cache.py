"""
Tests for the reasoning cache (companion tool-loop result caching).

All tests use mocked async Redis — no real Redis instance needed.
"""

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.reasoning_cache import ReasoningCache, _make_reasoning_key, REASONING_TTL


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_cache(enabled=True) -> tuple[ReasoningCache, AsyncMock]:
    """Create a ReasoningCache with a mock async Redis client."""
    mock_redis = AsyncMock()
    if enabled:
        cache = ReasoningCache(redis_client=mock_redis)
    else:
        cache = ReasoningCache(redis_client=None)
    return cache, mock_redis


SAMPLE_RESULTS = [
    {"name": "kronos_search", "args": {"query": "PAC"}, "content": '{"results": []}'},
    {"name": "kronos_get", "args": {"id": "pac-framework"}, "content": '{"id": "pac"}'},
]


# ─── Key Generation ──────────────────────────────────────────────────────────

class TestKeyGeneration:

    def test_same_msg_same_fdos_same_key(self):
        k1 = _make_reasoning_key("tell me about PAC", ["pac-framework", "sec-field"])
        k2 = _make_reasoning_key("tell me about PAC", ["pac-framework", "sec-field"])
        assert k1 == k2

    def test_different_msg_different_key(self):
        k1 = _make_reasoning_key("tell me about PAC", ["pac-framework"])
        k2 = _make_reasoning_key("tell me about SEC", ["pac-framework"])
        assert k1 != k2

    def test_different_fdos_different_key(self):
        k1 = _make_reasoning_key("tell me about PAC", ["pac-framework"])
        k2 = _make_reasoning_key("tell me about PAC", ["sec-field"])
        assert k1 != k2

    def test_fdo_order_insensitive(self):
        k1 = _make_reasoning_key("test", ["aaa", "bbb", "ccc"])
        k2 = _make_reasoning_key("test", ["ccc", "aaa", "bbb"])
        assert k1 == k2

    def test_case_insensitive_message(self):
        k1 = _make_reasoning_key("Tell Me About PAC", [])
        k2 = _make_reasoning_key("tell me about pac", [])
        assert k1 == k2

    def test_whitespace_normalized(self):
        k1 = _make_reasoning_key("  tell me about PAC  ", [])
        k2 = _make_reasoning_key("tell me about PAC", [])
        assert k1 == k2

    def test_key_format(self):
        key = _make_reasoning_key("test", ["fdo-1"])
        assert key.startswith("reasoning:")
        hash_part = key.split(":")[1]
        assert len(hash_part) == 16

    def test_empty_fdos(self):
        key = _make_reasoning_key("test", [])
        assert key.startswith("reasoning:")


# ─── Disabled Cache ──────────────────────────────────────────────────────────

class TestDisabledCache:

    @pytest.mark.asyncio
    async def test_get_returns_none_when_disabled(self):
        cache, _ = make_cache(enabled=False)
        result = await cache.get("test query", ["fdo-1"])
        assert result is None

    @pytest.mark.asyncio
    async def test_set_noop_when_disabled(self):
        cache, mock_redis = make_cache(enabled=False)
        await cache.set("test", ["fdo-1"], SAMPLE_RESULTS)
        mock_redis.setex.assert_not_called()

    def test_enabled_property_false(self):
        cache, _ = make_cache(enabled=False)
        assert cache.enabled is False

    def test_enabled_property_true(self):
        cache, _ = make_cache(enabled=True)
        assert cache.enabled is True


# ─── Cache Hit / Miss ────────────────────────────────────────────────────────

class TestCacheHitMiss:

    @pytest.mark.asyncio
    async def test_cache_hit_returns_tool_results(self):
        cache, mock_redis = make_cache()
        mock_redis.get.return_value = json.dumps(SAMPLE_RESULTS)
        result = await cache.get("tell me about PAC", ["pac-framework"])
        assert result is not None
        assert len(result) == 2
        assert result[0]["name"] == "kronos_search"
        assert result[1]["name"] == "kronos_get"

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        cache, mock_redis = make_cache()
        mock_redis.get.return_value = None
        result = await cache.get("unknown query", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_results_not_cached(self):
        cache, mock_redis = make_cache()
        await cache.set("test", ["fdo-1"], [])
        mock_redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_stores_with_correct_ttl(self):
        cache, mock_redis = make_cache()
        await cache.set("test query", ["fdo-1"], SAMPLE_RESULTS)
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == REASONING_TTL  # TTL = 600s

    @pytest.mark.asyncio
    async def test_set_stores_json_value(self):
        cache, mock_redis = make_cache()
        await cache.set("test", [], SAMPLE_RESULTS)
        stored_value = mock_redis.setex.call_args[0][2]
        parsed = json.loads(stored_value)
        assert len(parsed) == 2
        assert parsed[0]["name"] == "kronos_search"

    @pytest.mark.asyncio
    async def test_get_uses_correct_key(self):
        cache, mock_redis = make_cache()
        mock_redis.get.return_value = None
        expected_key = _make_reasoning_key("my query", ["fdo-a", "fdo-b"])
        await cache.get("my query", ["fdo-a", "fdo-b"])
        mock_redis.get.assert_called_once_with(expected_key)


# ─── Resilience ──────────────────────────────────────────────────────────────

class TestResilience:

    @pytest.mark.asyncio
    async def test_redis_get_error_returns_none(self):
        cache, mock_redis = make_cache()
        mock_redis.get.side_effect = ConnectionError("Redis down")
        result = await cache.get("test", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_redis_set_error_silent(self):
        cache, mock_redis = make_cache()
        mock_redis.setex.side_effect = ConnectionError("Redis down")
        # Should not raise
        await cache.set("test", [], SAMPLE_RESULTS)

    @pytest.mark.asyncio
    async def test_redis_down_mid_session(self):
        cache, mock_redis = make_cache()
        # First call succeeds
        mock_redis.get.return_value = json.dumps(SAMPLE_RESULTS)
        result = await cache.get("test", [])
        assert result is not None

        # Redis goes down
        mock_redis.get.side_effect = ConnectionError("gone")
        result = await cache.get("test", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_close_is_safe(self):
        cache, mock_redis = make_cache()
        await cache.close()
        mock_redis.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_when_disabled(self):
        cache, _ = make_cache(enabled=False)
        await cache.close()  # Should not raise


# ─── from_env ─────────────────────────────────────────────────────────────────

class TestFromEnv:

    @pytest.mark.asyncio
    async def test_no_env_var_returns_disabled(self):
        with patch.dict("os.environ", {}, clear=True):
            cache = await ReasoningCache.from_env()
            assert cache.enabled is False

    @pytest.mark.asyncio
    async def test_empty_env_var_returns_disabled(self):
        with patch.dict("os.environ", {"GRIM_REDIS_URL": ""}):
            cache = await ReasoningCache.from_env()
            assert cache.enabled is False

    @pytest.mark.asyncio
    async def test_fallback_to_kronos_redis_url(self):
        """Should fall back to KRONOS_REDIS_URL if GRIM_REDIS_URL not set."""
        import redis.asyncio as aioredis

        mock_client = AsyncMock()
        with patch.dict("os.environ", {"KRONOS_REDIS_URL": "redis://localhost:6379/0"}, clear=True):
            with patch.object(aioredis.Redis, "from_url", return_value=mock_client):
                cache = await ReasoningCache.from_env()
                assert cache.enabled is True


# ─── Prompt Parts Tests ──────────────────────────────────────────────────────

class TestPromptParts:
    """Test the PromptParts dataclass and build_system_prompt_parts."""

    def test_prompt_parts_full_combines_both(self):
        from core.personality.prompt_builder import PromptParts
        parts = PromptParts(static="IDENTITY", dynamic="KNOWLEDGE")
        assert parts.full() == "IDENTITY\n\nKNOWLEDGE"

    def test_prompt_parts_full_static_only(self):
        from core.personality.prompt_builder import PromptParts
        parts = PromptParts(static="IDENTITY", dynamic="")
        assert parts.full() == "IDENTITY"

    def test_build_system_prompt_parts_separates(self):
        from core.personality.prompt_builder import build_system_prompt_parts
        from core.state import FieldState, FDOSummary, SkillContext
        from pathlib import Path
        import tempfile, os

        # Create a temp identity file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("You are GRIM, a test identity.")
            prompt_path = Path(f.name)

        try:
            parts = build_system_prompt_parts(
                prompt_path=prompt_path,
                personality_path=Path("/nonexistent"),
                field_state=FieldState(),
                knowledge_context=[
                    FDOSummary(id="test-fdo", title="Test", domain="physics",
                               status="stable", confidence=0.9, summary="A test FDO",
                               tags=["test"], related=[]),
                ],
                matched_skills=[
                    SkillContext(name="test-skill", version="1.0",
                                description="A test skill",
                                permissions=["read"], triggers={}),
                ],
            )

            # Static should contain identity and field state
            assert "GRIM" in parts.static
            assert "Expression Mode" in parts.static

            # Dynamic should contain knowledge and skills
            assert "test-fdo" in parts.dynamic
            assert "test-skill" in parts.dynamic

            # Static should NOT contain knowledge/skills
            assert "test-fdo" not in parts.static
            assert "test-skill" not in parts.static

            # Dynamic should NOT contain identity
            assert "Expression Mode" not in parts.dynamic
        finally:
            os.unlink(prompt_path)

    def test_backward_compat_build_system_prompt(self):
        """build_system_prompt still returns a single string."""
        from core.personality.prompt_builder import build_system_prompt
        from core.state import FieldState
        from pathlib import Path

        result = build_system_prompt(
            prompt_path=Path("/nonexistent"),
            personality_path=Path("/nonexistent"),
            field_state=FieldState(),
        )
        assert isinstance(result, str)
        assert "GRIM" in result

    def test_no_knowledge_empty_dynamic(self):
        from core.personality.prompt_builder import build_system_prompt_parts
        from core.state import FieldState
        from pathlib import Path

        parts = build_system_prompt_parts(
            prompt_path=Path("/nonexistent"),
            personality_path=Path("/nonexistent"),
            field_state=FieldState(),
        )
        assert parts.dynamic == ""
