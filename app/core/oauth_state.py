"""
Redis-backed OAuth state store (CSRF protection for OAuth flows).

Replaces per-process in-memory dict so state works across Gunicorn workers
and multiple API instances.
"""

import json

from app.core.redis import get_redis

STATE_TTL_SECONDS = 600  # 10 minutes
STATE_PREFIX = "oauth_state:"


async def save_state(state: str, role: str, mode: str = "signup") -> None:
    """Persist OAuth state → {role, mode} mapping with 10-min TTL."""
    redis = await get_redis()
    payload = json.dumps({"role": role, "mode": mode})
    await redis.setex(f"{STATE_PREFIX}{state}", STATE_TTL_SECONDS, payload)


async def consume_state(state: str) -> tuple[str, str] | None:
    """Atomically read + delete state. Returns (role, mode) if valid, None if missing/expired."""
    redis = await get_redis()
    key = f"{STATE_PREFIX}{state}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.get(key)
        pipe.delete(key)
        results = await pipe.execute()
    raw = results[0]
    if not raw:
        return None
    data = json.loads(raw)
    return data["role"], data.get("mode", "signup")
