"""
Generic Redis-backed cache helpers.

Used for:
  - Hot user lookups (auth middleware)
  - Profile views
  - Trust scores
  - Leaderboards

All payloads are JSON-serialized.
"""

import json
from typing import Any

from app.core.redis import get_redis


async def cache_get(key: str) -> Any | None:
    """Return cached JSON value or None."""
    redis = await get_redis()
    raw = await redis.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


async def cache_set(key: str, value: Any, ttl_seconds: int = 300) -> None:
    """Store JSON-serializable value with TTL."""
    redis = await get_redis()
    await redis.setex(key, ttl_seconds, json.dumps(value, default=str))


async def cache_delete(key: str) -> None:
    """Invalidate a single key."""
    redis = await get_redis()
    await redis.delete(key)


async def cache_delete_pattern(pattern: str) -> int:
    """Invalidate all keys matching a glob pattern (e.g. 'profile:*'). Returns count deleted."""
    redis = await get_redis()
    count = 0
    async for key in redis.scan_iter(match=pattern, count=500):
        await redis.delete(key)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Namespaced key builders (avoid typos & keep keys consistent)
# ---------------------------------------------------------------------------

def user_key(user_id: str) -> str:
    return f"user:{user_id}"


def profile_key(profile_id: str) -> str:
    return f"profile:id:{profile_id}"


def profile_handle_key(handle: str) -> str:
    return f"profile:handle:{handle}"


def trust_score_key(profile_id: str) -> str:
    return f"trust_score:{profile_id}"


# ---------------------------------------------------------------------------
# Invalidation helpers — call after writes so stale cache doesn't linger
# ---------------------------------------------------------------------------

async def invalidate_user(user_id: str) -> None:
    """Call after user update, role change, or deactivation."""
    await cache_delete(user_key(user_id))


async def invalidate_profile(profile_id: str, handle: str | None = None) -> None:
    """Call after profile update or review verification."""
    await cache_delete(profile_key(profile_id))
    if handle:
        await cache_delete(profile_handle_key(handle))
    await cache_delete(trust_score_key(profile_id))
