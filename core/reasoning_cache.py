"""
Reasoning Cache — skip the companion tool loop for repeated queries.

Caches the companion's tool-gathering results (kronos_search, kronos_get, etc.)
so repeated/similar questions skip 3-4 LLM calls entirely. The companion makes
one final LLM call with pre-gathered context instead.

Key:   reasoning:{sha256(lower(user_msg) + sorted(fdo_ids))[:16]}
Value: JSON array of {name, args, content} dicts (tool results)
TTL:   10 minutes

Design:
  - Uses redis.asyncio since companion_node is async
  - Zero-dependency fallback: if Redis unavailable, tool loop runs normally
  - Never caches errors or empty results
  - All public methods catch exceptions — never raises
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

REASONING_TTL = 600  # 10 minutes


def _make_reasoning_key(user_msg: str, fdo_ids: list[str]) -> str:
    """Stable cache key from user message + knowledge context FDO IDs."""
    normalized = user_msg.strip().lower()
    sorted_ids = sorted(fdo_ids)
    blob = json.dumps({"msg": normalized, "fdo_ids": sorted_ids}, sort_keys=True)
    digest = hashlib.sha256(blob.encode()).hexdigest()[:16]
    return f"reasoning:{digest}"


class ReasoningCache:
    """Async Redis cache for companion tool-loop results. Fully optional."""

    def __init__(self, redis_client: Any = None):
        self._redis = redis_client
        self._enabled = redis_client is not None

    @classmethod
    async def from_env(cls) -> ReasoningCache:
        """Construct from GRIM_REDIS_URL env var. No-op cache if unavailable."""
        url = os.getenv("GRIM_REDIS_URL", os.getenv("KRONOS_REDIS_URL", ""))
        if not url:
            logger.info("GRIM_REDIS_URL not set — reasoning cache disabled")
            return cls(redis_client=None)

        try:
            import redis.asyncio as aioredis

            client = aioredis.Redis.from_url(
                url,
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=True,
            )
            await client.ping()
            logger.info("Reasoning cache connected: %s", url)
            return cls(redis_client=client)
        except ImportError:
            logger.warning("redis package not installed — reasoning cache disabled")
            return cls(redis_client=None)
        except Exception as e:
            logger.warning("Redis unavailable (%s): %s — reasoning cache disabled", url, e)
            return cls(redis_client=None)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def get(self, user_msg: str, fdo_ids: list[str]) -> list[dict] | None:
        """Return cached tool results, or None on miss/error/disabled."""
        if not self._enabled:
            return None

        key = _make_reasoning_key(user_msg, fdo_ids)
        try:
            value = await self._redis.get(key)
            if value is not None:
                logger.info("Reasoning cache HIT: %s", key)
                return json.loads(value)
            return None
        except Exception as e:
            logger.warning("Reasoning cache GET failed: %s", e)
            return None

    async def set(self, user_msg: str, fdo_ids: list[str], tool_results: list[dict]) -> None:
        """Store tool results with TTL. Silent on error. Skips empty results."""
        if not self._enabled or not tool_results:
            return

        key = _make_reasoning_key(user_msg, fdo_ids)
        try:
            value = json.dumps(tool_results, ensure_ascii=False)
            await self._redis.setex(key, REASONING_TTL, value)
            logger.info("Reasoning cache SET: %s (%d results)", key, len(tool_results))
        except Exception as e:
            logger.warning("Reasoning cache SET failed: %s", e)

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
