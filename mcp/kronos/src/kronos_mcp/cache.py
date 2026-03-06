"""
Kronos Redis Cache — optional caching layer for MCP tool dispatch.

Design:
  - Zero-dependency fallback: if Redis is unavailable, ALL calls fall through
    to handlers transparently. No errors surface to callers.
  - Thread-safe: redis-py uses a connection pool with auto-reconnect.
  - Key format: kronos:{tool_name}:{sha256(sorted_json(arguments))[:16]}
  - Invalidation on write: pattern-delete by tool prefix + exact-key delete
    for the affected FDO ID.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger("kronos-mcp.cache")

# TTLs in seconds. None = never cache (write tools).
TOOL_TTLS: dict[str, int | None] = {
    "kronos_get":        600,   # 10 min
    "kronos_search":     300,   # 5 min
    "kronos_list":       600,   # 10 min
    "kronos_graph":      600,   # 10 min
    "kronos_tags":       600,   # 10 min
    "kronos_validate":   300,   # 5 min
    "kronos_skills":     600,   # 10 min
    "kronos_skill_load": 600,   # 10 min
    "kronos_navigate":   300,   # 5 min
    "kronos_deep_dive":  300,   # 5 min
    "kronos_read_source":   120,   # 2 min — source files change during dev
    "kronos_search_source": 120,   # 2 min
    # Code intelligence tools
    "kronos_validate_sources":    120,   # 2 min — source files change during dev
    "kronos_find_implementation":  60,   # 1 min — active development
    "kronos_git_recent":          120,   # 2 min
    # Memory tools — short TTL for reads, never cache writes
    "kronos_memory_read":     60,    # 1 min
    "kronos_memory_sections": 60,    # 1 min
    "kronos_memory_update":   None,  # never cache — triggers memory invalidation
    # System tools
    "kronos_tool_groups":     600,   # 10 min — static data
    # Write tools — never cached, trigger invalidation
    "kronos_create":     None,
    "kronos_update":     None,
    # Task management tools
    "kronos_task_list":      120,   # 2 min
    "kronos_task_get":       120,   # 2 min
    "kronos_board_view":     120,   # 2 min
    "kronos_backlog_view":   120,   # 2 min
    "kronos_calendar_view":  120,   # 2 min
    # Task write tools — never cached
    "kronos_task_create":    None,
    "kronos_task_update":    None,
    "kronos_task_move":      None,
    "kronos_task_archive":   None,
    "kronos_calendar_add":   None,
    "kronos_calendar_update": None,
    "kronos_calendar_sync":  None,
}

# Tools that modify vault state (FDOs)
WRITE_TOOLS = {"kronos_create", "kronos_update"}

# Tools that modify memory (separate from vault FDOs)
MEMORY_WRITE_TOOLS = {"kronos_memory_update"}

# Tools that modify task/board/calendar state
TASK_WRITE_TOOLS = {
    "kronos_task_create", "kronos_task_update", "kronos_task_move",
    "kronos_task_archive", "kronos_calendar_add", "kronos_calendar_update",
    "kronos_calendar_sync",
}


def _make_key(tool_name: str, arguments: dict) -> str:
    """Stable cache key: kronos:{tool}:{sha256(sorted_args_json)[:16]}."""
    args_blob = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(args_blob.encode()).hexdigest()[:16]
    return f"kronos:{tool_name}:{digest}"


class KronosCache:
    """Redis-backed cache for Kronos MCP tool results. Fully optional."""

    def __init__(self, redis_client: Any = None):
        self._redis = redis_client
        self._enabled = redis_client is not None

    @classmethod
    def from_env(cls) -> "KronosCache":
        """Construct from KRONOS_REDIS_URL env var. No-op cache if unavailable."""
        url = os.getenv("KRONOS_REDIS_URL", "")
        if not url:
            logger.info("KRONOS_REDIS_URL not set — caching disabled")
            return cls(redis_client=None)

        try:
            import redis as redis_lib
            client = redis_lib.Redis.from_url(
                url,
                socket_connect_timeout=5,
                socket_timeout=5,
                health_check_interval=30,
                retry_on_timeout=True,
                decode_responses=True,
            )
            client.ping()
            logger.info(f"Redis cache connected: {url}")
            return cls(redis_client=client)
        except ImportError:
            logger.warning("redis package not installed — caching disabled")
            return cls(redis_client=None)
        except Exception as e:
            logger.warning(f"Redis unavailable ({url}): {e} — caching disabled")
            return cls(redis_client=None)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get(self, tool_name: str, arguments: dict) -> str | None:
        """Return cached result string, or None on miss/error/disabled."""
        if not self._enabled:
            return None
        ttl = TOOL_TTLS.get(tool_name)
        if ttl is None:
            return None  # Write tool — never read from cache

        key = _make_key(tool_name, arguments)
        try:
            value = self._redis.get(key)
            if value is not None:
                logger.debug(f"Cache HIT: {key}")
            return value
        except Exception as e:
            logger.warning(f"Cache GET failed for {key}: {e}")
            return None

    def set(self, tool_name: str, arguments: dict, value: str) -> None:
        """Store result with TTL. Silent on error."""
        if not self._enabled:
            return
        ttl = TOOL_TTLS.get(tool_name)
        if ttl is None:
            return

        key = _make_key(tool_name, arguments)
        try:
            self._redis.setex(key, ttl, value)
            logger.debug(f"Cache SET: {key} TTL={ttl}s")
        except Exception as e:
            logger.warning(f"Cache SET failed for {key}: {e}")

    def invalidate_for_write(self, tool_name: str, arguments: dict) -> None:
        """Invalidate cache entries after a write operation."""
        if not self._enabled:
            return

        try:
            # Memory writes only invalidate memory caches — never touch FDO caches
            if tool_name in MEMORY_WRITE_TOOLS:
                self._delete_patterns([
                    "kronos:kronos_memory_read:*",
                    "kronos:kronos_memory_sections:*",
                ])
                return

            # Task writes invalidate all task/board/calendar read caches
            if tool_name in TASK_WRITE_TOOLS:
                self._delete_patterns([
                    "kronos:kronos_task_list:*",
                    "kronos:kronos_task_get:*",
                    "kronos:kronos_board_view:*",
                    "kronos:kronos_backlog_view:*",
                    "kronos:kronos_calendar_view:*",
                ])
                return

            fdo_id = arguments.get("id", "")

            if tool_name == "kronos_update":
                # Exact-key invalidations for the specific FDO
                exact_keys = [
                    _make_key("kronos_get", {"id": fdo_id}),
                ]
                # Also invalidate graph queries for this FDO at all depths
                for depth in (1, 2, 3):
                    exact_keys.append(_make_key("kronos_graph", {"id": fdo_id, "depth": depth}))
                exact_keys.append(_make_key("kronos_graph", {"id": fdo_id}))
                self._delete_keys(exact_keys)

            # Both create and update invalidate all search/list/tag results
            patterns = [
                "kronos:kronos_search:*",
                "kronos:kronos_list:*",
                "kronos:kronos_tags:*",
                "kronos:kronos_validate:*",
                "kronos:kronos_deep_dive:*",
            ]
            if tool_name == "kronos_update":
                patterns.append("kronos:kronos_graph:*")
            self._delete_patterns(patterns)

        except Exception as e:
            logger.warning(f"Cache invalidation failed after {tool_name}: {e}")

    def _delete_keys(self, keys: list[str]) -> None:
        """Delete exact keys in a pipeline."""
        if not keys:
            return
        pipe = self._redis.pipeline()
        for k in keys:
            pipe.delete(k)
        pipe.execute()

    def _delete_patterns(self, patterns: list[str]) -> None:
        """Scan-and-delete by glob pattern. Uses SCAN to avoid blocking."""
        for pattern in patterns:
            keys_to_delete = list(self._redis.scan_iter(pattern, count=200))
            if keys_to_delete:
                self._redis.delete(*keys_to_delete)

    def health_check(self) -> bool:
        """Ping Redis. Returns False if unavailable or disabled."""
        if not self._enabled:
            return False
        try:
            return self._redis.ping()
        except Exception:
            return False

    def flush_all(self) -> int:
        """Flush all kronos:* keys. Returns count deleted."""
        if not self._enabled:
            return 0
        try:
            keys = list(self._redis.scan_iter("kronos:*", count=200))
            if keys:
                self._redis.delete(*keys)
            return len(keys)
        except Exception as e:
            logger.warning(f"flush_all failed: {e}")
            return 0

    def stats(self) -> dict:
        """Cache stats for health/debug."""
        if not self._enabled:
            return {"enabled": False}
        try:
            info = self._redis.info("stats")
            kronos_keys = len(list(self._redis.scan_iter("kronos:*", count=200)))
            return {
                "enabled": True,
                "hits": info.get("keyspace_hits", 0),
                "misses": info.get("keyspace_misses", 0),
                "kronos_keys": kronos_keys,
            }
        except Exception as e:
            return {"enabled": True, "error": str(e)}
