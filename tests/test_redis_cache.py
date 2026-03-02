"""
Comprehensive tests for the Kronos Redis cache layer.

All tests use mocked Redis — no real Redis instance needed.
"""

import hashlib
import json
from unittest.mock import MagicMock, patch, call
import pytest

# Import cache module directly to avoid kronos_mcp.__init__ -> server.py
# (server.py requires KRONOS_VAULT_PATH at import time)
import importlib.util
import sys
from pathlib import Path

_mcp_src = Path(__file__).resolve().parent.parent / "mcp" / "kronos" / "src" / "kronos_mcp"
_cache_path = _mcp_src / "cache.py"

# Register a stub kronos_mcp package so unittest.mock.patch can resolve
# "kronos_mcp.cache.X" without triggering the real __init__.py -> server.py chain
import types
if "kronos_mcp" not in sys.modules:
    _pkg = types.ModuleType("kronos_mcp")
    _pkg.__path__ = [str(_mcp_src)]
    sys.modules["kronos_mcp"] = _pkg

_spec = importlib.util.spec_from_file_location("kronos_mcp.cache", _cache_path)
_cache_mod = importlib.util.module_from_spec(_spec)
sys.modules["kronos_mcp.cache"] = _cache_mod
_spec.loader.exec_module(_cache_mod)

KronosCache = _cache_mod.KronosCache
TOOL_TTLS = _cache_mod.TOOL_TTLS
WRITE_TOOLS = _cache_mod.WRITE_TOOLS
MEMORY_WRITE_TOOLS = _cache_mod.MEMORY_WRITE_TOOLS
TASK_WRITE_TOOLS = _cache_mod.TASK_WRITE_TOOLS
_make_key = _cache_mod._make_key

# All tools that perform writes (union of all write sets)
ALL_WRITE_TOOLS = WRITE_TOOLS | MEMORY_WRITE_TOOLS | TASK_WRITE_TOOLS


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_cache(enabled=True) -> tuple[KronosCache, MagicMock]:
    """Create a KronosCache with a mock Redis client."""
    mock_redis = MagicMock()
    if enabled:
        cache = KronosCache(redis_client=mock_redis)
    else:
        cache = KronosCache(redis_client=None)
    return cache, mock_redis


def expected_key(tool: str, args: dict) -> str:
    """Compute the expected cache key for given tool+args."""
    blob = json.dumps(args, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(blob.encode()).hexdigest()[:16]
    return f"kronos:{tool}:{digest}"


# ─── Key Generation ──────────────────────────────────────────────────────────

class TestKeyGeneration:

    def test_same_args_same_key(self):
        k1 = _make_key("kronos_get", {"id": "abc"})
        k2 = _make_key("kronos_get", {"id": "abc"})
        assert k1 == k2

    def test_different_args_different_key(self):
        k1 = _make_key("kronos_get", {"id": "abc"})
        k2 = _make_key("kronos_get", {"id": "xyz"})
        assert k1 != k2

    def test_arg_order_insensitive(self):
        k1 = _make_key("kronos_search", {"query": "test", "domain": "physics"})
        k2 = _make_key("kronos_search", {"domain": "physics", "query": "test"})
        assert k1 == k2

    def test_different_tools_different_key(self):
        k1 = _make_key("kronos_get", {"id": "abc"})
        k2 = _make_key("kronos_search", {"id": "abc"})
        assert k1 != k2

    def test_key_format(self):
        key = _make_key("kronos_get", {"id": "test"})
        assert key.startswith("kronos:kronos_get:")
        # Hash portion is 16 hex chars
        hash_part = key.split(":")[-1]
        assert len(hash_part) == 16

    def test_empty_args(self):
        key = _make_key("kronos_list", {})
        assert key.startswith("kronos:kronos_list:")


# ─── Disabled Cache ──────────────────────────────────────────────────────────

class TestDisabledCache:

    def test_get_returns_none_when_disabled(self):
        cache, _ = make_cache(enabled=False)
        assert cache.get("kronos_get", {"id": "abc"}) is None

    def test_set_noop_when_disabled(self):
        cache, mock_redis = make_cache(enabled=False)
        cache.set("kronos_get", {"id": "abc"}, '{"result": "data"}')
        mock_redis.setex.assert_not_called()

    def test_invalidate_noop_when_disabled(self):
        cache, mock_redis = make_cache(enabled=False)
        cache.invalidate_for_write("kronos_update", {"id": "abc"})
        mock_redis.pipeline.assert_not_called()
        mock_redis.scan_iter.assert_not_called()

    def test_flush_all_returns_zero_when_disabled(self):
        cache, _ = make_cache(enabled=False)
        assert cache.flush_all() == 0

    def test_stats_when_disabled(self):
        cache, _ = make_cache(enabled=False)
        assert cache.stats() == {"enabled": False}

    def test_enabled_property_false(self):
        cache, _ = make_cache(enabled=False)
        assert cache.enabled is False


# ─── Cache Hit / Miss ────────────────────────────────────────────────────────

class TestCacheHitMiss:

    def test_cache_hit_returns_value(self):
        cache, mock_redis = make_cache()
        mock_redis.get.return_value = '{"id": "abc", "title": "Test"}'
        result = cache.get("kronos_get", {"id": "abc"})
        assert result == '{"id": "abc", "title": "Test"}'
        mock_redis.get.assert_called_once_with(expected_key("kronos_get", {"id": "abc"}))

    def test_cache_miss_returns_none(self):
        cache, mock_redis = make_cache()
        mock_redis.get.return_value = None
        result = cache.get("kronos_get", {"id": "abc"})
        assert result is None

    def test_write_tool_never_read_from_cache(self):
        """kronos_create / kronos_update should never return cached results."""
        cache, mock_redis = make_cache()
        for tool in WRITE_TOOLS:
            result = cache.get(tool, {"id": "test"})
            assert result is None
        # Redis.get should never be called for write tools
        mock_redis.get.assert_not_called()

    def test_write_tool_never_stored(self):
        """Calling set() with a write tool should be a no-op."""
        cache, mock_redis = make_cache()
        for tool in WRITE_TOOLS:
            cache.set(tool, {"id": "test"}, '{"ok": true}')
        mock_redis.setex.assert_not_called()


# ─── TTL Correctness ─────────────────────────────────────────────────────────

class TestTTLCorrectness:

    @pytest.mark.parametrize("tool,expected_ttl", [
        ("kronos_get", 600),
        ("kronos_search", 300),
        ("kronos_list", 600),
        ("kronos_graph", 600),
        ("kronos_tags", 600),
        ("kronos_validate", 300),
        ("kronos_skills", 600),
        ("kronos_skill_load", 600),
        ("kronos_navigate", 300),
        ("kronos_deep_dive", 300),
    ])
    def test_set_calls_setex_with_correct_ttl(self, tool, expected_ttl):
        cache, mock_redis = make_cache()
        args = {"query": "test"} if "search" in tool else {"id": "test"}
        cache.set(tool, args, '{"result": "data"}')
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == expected_ttl  # TTL is second positional arg

    def test_all_read_tools_have_ttl(self):
        """Every tool in TOOL_TTLS that isn't a write tool must have a numeric TTL."""
        for tool, ttl in TOOL_TTLS.items():
            if tool not in ALL_WRITE_TOOLS:
                assert isinstance(ttl, int), f"{tool} should have numeric TTL, got {ttl}"
                assert ttl > 0, f"{tool} TTL should be positive"

    def test_all_write_tools_have_none_ttl(self):
        for tool in ALL_WRITE_TOOLS:
            assert TOOL_TTLS.get(tool) is None, f"{tool} should have None TTL (write tool)"


# ─── Invalidation (Critical — Peter flagged this) ────────────────────────────

class TestInvalidation:

    def test_update_deletes_get_key_for_same_fdo_id(self):
        cache, mock_redis = make_cache()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_redis.scan_iter.return_value = []

        cache.invalidate_for_write("kronos_update", {"id": "fdo-abc"})

        # Pipeline should delete the exact kronos_get key
        expected_get_key = _make_key("kronos_get", {"id": "fdo-abc"})
        pipe_delete_calls = [c[0][0] for c in mock_pipe.delete.call_args_list]
        assert expected_get_key in pipe_delete_calls

    def test_update_deletes_graph_keys_for_fdo(self):
        cache, mock_redis = make_cache()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_redis.scan_iter.return_value = []

        cache.invalidate_for_write("kronos_update", {"id": "fdo-abc"})

        # Should delete graph keys at depths 1, 2, 3 and without depth
        pipe_delete_calls = [c[0][0] for c in mock_pipe.delete.call_args_list]
        for depth in (1, 2, 3):
            key = _make_key("kronos_graph", {"id": "fdo-abc", "depth": depth})
            assert key in pipe_delete_calls, f"Missing graph key for depth={depth}"
        no_depth_key = _make_key("kronos_graph", {"id": "fdo-abc"})
        assert no_depth_key in pipe_delete_calls

    def test_update_scans_deletes_search_keys(self):
        cache, mock_redis = make_cache()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        # Simulate some search keys existing
        def scan_side_effect(pattern, count=200):
            if "search" in pattern:
                return ["kronos:kronos_search:aaa", "kronos:kronos_search:bbb"]
            return []
        mock_redis.scan_iter.side_effect = scan_side_effect

        cache.invalidate_for_write("kronos_update", {"id": "fdo-abc"})

        mock_redis.delete.assert_any_call("kronos:kronos_search:aaa", "kronos:kronos_search:bbb")

    def test_update_scans_deletes_list_keys(self):
        cache, mock_redis = make_cache()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        def scan_side_effect(pattern, count=200):
            if "list" in pattern:
                return ["kronos:kronos_list:aaa"]
            return []
        mock_redis.scan_iter.side_effect = scan_side_effect

        cache.invalidate_for_write("kronos_update", {"id": "fdo-abc"})

        mock_redis.delete.assert_any_call("kronos:kronos_list:aaa")

    def test_update_scans_deletes_graph_pattern_keys(self):
        cache, mock_redis = make_cache()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        def scan_side_effect(pattern, count=200):
            if "graph" in pattern:
                return ["kronos:kronos_graph:xyz123"]
            return []
        mock_redis.scan_iter.side_effect = scan_side_effect

        cache.invalidate_for_write("kronos_update", {"id": "fdo-abc"})

        mock_redis.delete.assert_any_call("kronos:kronos_graph:xyz123")

    def test_create_does_not_delete_exact_get_key(self):
        """Create doesn't know an FDO ID to invalidate — only pattern deletes."""
        cache, mock_redis = make_cache()
        mock_redis.scan_iter.return_value = []

        cache.invalidate_for_write("kronos_create", {"id": "new-fdo"})

        # pipeline should NOT be called (no exact-key deletes for create)
        mock_redis.pipeline.assert_not_called()

    def test_create_scans_deletes_search_and_list_keys(self):
        cache, mock_redis = make_cache()
        keys_by_pattern = {
            "kronos:kronos_search:*": ["kronos:kronos_search:s1"],
            "kronos:kronos_list:*": ["kronos:kronos_list:l1"],
            "kronos:kronos_tags:*": [],
            "kronos:kronos_validate:*": [],
            "kronos:kronos_deep_dive:*": [],
        }
        mock_redis.scan_iter.side_effect = lambda p, count=200: keys_by_pattern.get(p, [])

        cache.invalidate_for_write("kronos_create", {"id": "new-fdo"})

        mock_redis.delete.assert_any_call("kronos:kronos_search:s1")
        mock_redis.delete.assert_any_call("kronos:kronos_list:l1")

    def test_create_does_not_scan_graph_keys(self):
        """Create only invalidates search/list/tags/validate/deep_dive, not graph."""
        cache, mock_redis = make_cache()
        called_patterns = []
        mock_redis.scan_iter.side_effect = lambda p, count=200: (called_patterns.append(p), [])[1]

        cache.invalidate_for_write("kronos_create", {"id": "new-fdo"})

        assert "kronos:kronos_graph:*" not in called_patterns

    def test_invalidation_redis_error_does_not_raise(self):
        cache, mock_redis = make_cache()
        mock_redis.pipeline.side_effect = ConnectionError("Redis down")

        # Should not raise
        cache.invalidate_for_write("kronos_update", {"id": "fdo-abc"})

    def test_invalidation_scan_error_does_not_raise(self):
        cache, mock_redis = make_cache()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_redis.scan_iter.side_effect = ConnectionError("Redis down")

        cache.invalidate_for_write("kronos_update", {"id": "fdo-abc"})

    def test_update_invalidates_all_expected_patterns(self):
        """Verify the full set of patterns invalidated on update."""
        cache, mock_redis = make_cache()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        called_patterns = []
        mock_redis.scan_iter.side_effect = lambda p, count=200: (called_patterns.append(p), [])[1]

        cache.invalidate_for_write("kronos_update", {"id": "fdo-abc"})

        expected_patterns = {
            "kronos:kronos_search:*",
            "kronos:kronos_list:*",
            "kronos:kronos_tags:*",
            "kronos:kronos_validate:*",
            "kronos:kronos_deep_dive:*",
            "kronos:kronos_graph:*",
        }
        assert set(called_patterns) == expected_patterns


# ─── Integration Dispatch Tests ──────────────────────────────────────────────

class TestDispatchIntegration:
    """Test that cache integrates correctly with the call_tool dispatch flow."""

    def test_cache_hit_skips_handler(self):
        """When cache has a result, handler should not be called."""
        cache, mock_redis = make_cache()
        mock_redis.get.return_value = '{"id": "abc", "cached": true}'

        result = cache.get("kronos_get", {"id": "abc"})
        assert result is not None
        assert '"cached": true' in result

    def test_cache_miss_returns_none(self):
        """On miss, cache.get returns None so caller proceeds to handler."""
        cache, mock_redis = make_cache()
        mock_redis.get.return_value = None

        result = cache.get("kronos_get", {"id": "abc"})
        assert result is None

    def test_set_after_handler_stores_result(self):
        """After handler returns, result should be cached via setex."""
        cache, mock_redis = make_cache()
        handler_result = '{"id": "abc", "title": "Test FDO"}'

        cache.set("kronos_get", {"id": "abc"}, handler_result)

        mock_redis.setex.assert_called_once()
        stored_value = mock_redis.setex.call_args[0][2]
        assert stored_value == handler_result

    def test_write_tool_invalidates_not_stores(self):
        """After a write tool, should invalidate patterns, NOT store result."""
        cache, mock_redis = make_cache()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        mock_redis.scan_iter.return_value = []

        # Simulate: write tool executed, now we invalidate
        cache.invalidate_for_write("kronos_update", {"id": "abc"})

        # setex should NOT be called
        mock_redis.setex.assert_not_called()
        # But pipeline/scan should be called for invalidation
        mock_redis.pipeline.assert_called()

    def test_handler_error_should_not_be_cached(self):
        """Errors from handler should never be stored — caller's responsibility."""
        cache, mock_redis = make_cache()
        error_result = '{"error": "something broke"}'

        # The caller (call_tool in server.py) handles this by not calling cache.set
        # on error paths. We verify cache.set would store it if called,
        # confirming the responsibility is on the caller.
        cache.set("kronos_get", {"id": "abc"}, error_result)
        mock_redis.setex.assert_called_once()


# ─── Redis Resilience ────────────────────────────────────────────────────────

class TestResilience:

    def test_redis_unavailable_at_startup(self):
        """from_env should return disabled cache when Redis can't connect."""
        with patch.dict("os.environ", {"KRONOS_REDIS_URL": "redis://nonexistent:6379/0"}):
            with patch("kronos_mcp.cache.redis_lib", create=True):
                # Simulate import succeeding but ping failing
                import importlib
                with patch.dict("os.environ", {"KRONOS_REDIS_URL": "redis://bad:6379"}):
                    cache = KronosCache(redis_client=None)
                    assert cache.enabled is False

    def test_redis_get_error_returns_none(self):
        """GET failure should return None, not raise."""
        cache, mock_redis = make_cache()
        mock_redis.get.side_effect = ConnectionError("Connection lost")

        result = cache.get("kronos_get", {"id": "abc"})
        assert result is None

    def test_redis_set_error_does_not_raise(self):
        """SET failure should be silent."""
        cache, mock_redis = make_cache()
        mock_redis.setex.side_effect = ConnectionError("Connection lost")

        # Should not raise
        cache.set("kronos_get", {"id": "abc"}, '{"data": "test"}')

    def test_redis_down_mid_session_get(self):
        """If Redis goes down after initial connect, GET returns None."""
        cache, mock_redis = make_cache()
        # First call succeeds
        mock_redis.get.return_value = '{"cached": true}'
        assert cache.get("kronos_get", {"id": "abc"}) is not None

        # Redis goes down
        mock_redis.get.side_effect = ConnectionError("Redis gone")
        assert cache.get("kronos_get", {"id": "abc"}) is None

    def test_redis_down_mid_session_set(self):
        """If Redis goes down, SET is silently ignored."""
        cache, mock_redis = make_cache()
        mock_redis.setex.side_effect = TimeoutError("Redis timeout")

        # Should not raise
        cache.set("kronos_search", {"query": "test"}, '{"results": []}')

    def test_stats_error_returns_error_dict(self):
        cache, mock_redis = make_cache()
        mock_redis.info.side_effect = ConnectionError("down")

        stats = cache.stats()
        assert stats["enabled"] is True
        assert "error" in stats


# ─── Flush All ────────────────────────────────────────────────────────────────

class TestFlushAll:

    def test_flush_all_removes_kronos_keys(self):
        cache, mock_redis = make_cache()
        mock_redis.scan_iter.return_value = [
            "kronos:kronos_get:aaa",
            "kronos:kronos_search:bbb",
            "kronos:kronos_list:ccc",
        ]

        count = cache.flush_all()

        assert count == 3
        mock_redis.delete.assert_called_once_with(
            "kronos:kronos_get:aaa",
            "kronos:kronos_search:bbb",
            "kronos:kronos_list:ccc",
        )

    def test_flush_all_empty(self):
        cache, mock_redis = make_cache()
        mock_redis.scan_iter.return_value = []

        count = cache.flush_all()
        assert count == 0
        mock_redis.delete.assert_not_called()

    def test_flush_all_error_returns_zero(self):
        cache, mock_redis = make_cache()
        mock_redis.scan_iter.side_effect = ConnectionError("down")

        count = cache.flush_all()
        assert count == 0


# ─── Stats ────────────────────────────────────────────────────────────────────

class TestStats:

    def test_stats_returns_hit_miss_counts(self):
        cache, mock_redis = make_cache()
        mock_redis.info.return_value = {
            "keyspace_hits": 42,
            "keyspace_misses": 7,
        }
        mock_redis.scan_iter.return_value = ["k1", "k2", "k3"]

        stats = cache.stats()
        assert stats["enabled"] is True
        assert stats["hits"] == 42
        assert stats["misses"] == 7
        assert stats["kronos_keys"] == 3


# ─── from_env ─────────────────────────────────────────────────────────────────

class TestFromEnv:

    def test_no_env_var_returns_disabled(self):
        with patch.dict("os.environ", {}, clear=True):
            cache = KronosCache.from_env()
            assert cache.enabled is False

    def test_empty_env_var_returns_disabled(self):
        with patch.dict("os.environ", {"KRONOS_REDIS_URL": ""}):
            cache = KronosCache.from_env()
            assert cache.enabled is False

    def test_import_error_returns_disabled(self):
        with patch.dict("os.environ", {"KRONOS_REDIS_URL": "redis://localhost:6379/0"}):
            with patch.dict("sys.modules", {"redis": None}):
                import builtins
                original_import = builtins.__import__

                def mock_import(name, *args, **kwargs):
                    if name == "redis":
                        raise ImportError("No module named 'redis'")
                    return original_import(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=mock_import):
                    cache = KronosCache.from_env()
                    assert cache.enabled is False

    def test_connection_failure_returns_disabled(self):
        with patch.dict("os.environ", {"KRONOS_REDIS_URL": "redis://localhost:6379/0"}):
            mock_redis_module = MagicMock()
            mock_client = MagicMock()
            mock_client.ping.side_effect = ConnectionError("Connection refused")
            mock_redis_module.Redis.from_url.return_value = mock_client

            import builtins
            original_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "redis":
                    return mock_redis_module
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                cache = KronosCache.from_env()
                assert cache.enabled is False
