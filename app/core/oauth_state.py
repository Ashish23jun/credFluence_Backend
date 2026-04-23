"""
Redis-backed OAuth state store (CSRF protection for OAuth flows).

Replaces per-process in-memory dict so state works across Gunicorn workers
and multiple API instances.

State payload: {role, mode, user_id?}
  - user_id is only set for mode=connect flows
"""

import json

from app.core.redis import get_redis

STATE_TTL_SECONDS = 600  # 10 minutes
STATE_PREFIX = "oauth_state:"


async def save_state(
    state: str,
    role: str,
    mode: str = "signup",
    user_id: str | None = None,
) -> None:
    """Persist OAuth state with 10-min TTL."""
    redis = await get_redis()
    payload: dict = {"role": role, "mode": mode}
    if user_id:
        payload["user_id"] = user_id
    await redis.setex(f"{STATE_PREFIX}{state}", STATE_TTL_SECONDS, json.dumps(payload))


async def consume_state(state: str) -> dict | None:
    """Atomically read + delete state. Returns full payload dict or None if missing/expired."""
    redis = await get_redis()
    key = f"{STATE_PREFIX}{state}"
    async with redis.pipeline(transaction=True) as pipe:
        pipe.get(key)
        pipe.delete(key)
        results = await pipe.execute()
    raw = results[0]
    if not raw:
        return None
    return json.loads(raw)
