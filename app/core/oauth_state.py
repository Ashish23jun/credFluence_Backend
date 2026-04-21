"""
Redis-backed OAuth state store (CSRF protection for OAuth flows).

Replaces per-process in-memory dict so state works across Gunicorn workers
and multiple API instances.
"""

from app.core.redis import get_redis

STATE_TTL_SECONDS = 600  # 10 minutes
STATE_PREFIX = "oauth_state:"


async def save_state(state: str, role: str) -> None:
    """Persist OAuth state → role mapping with 10-min TTL."""
    redis = await get_redis()
    await redis.setex(f"{STATE_PREFIX}{state}", STATE_TTL_SECONDS, role)


async def consume_state(state: str) -> str | None:
    """Atomically read + delete state. Returns role if valid, None if missing/expired."""
    redis = await get_redis()
    key = f"{STATE_PREFIX}{state}"
    # Use pipeline for atomic get+del
    async with redis.pipeline(transaction=True) as pipe:
        pipe.get(key)
        pipe.delete(key)
        results = await pipe.execute()
    role = results[0]
    return role if role else None
