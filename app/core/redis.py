"""
Redis client with explicit connection pool.

One process-wide pool is shared by cache, rate limiter, OAuth state store, and
Celery result backend. max_connections caps concurrent Redis ops per worker to
keep a single worker from saturating Redis under load.

The client is recreated whenever the running event loop changes (e.g. between
Celery task invocations that each call asyncio.run(), which creates a new loop).
"""

import asyncio

import redis.asyncio as aioredis

from app.core.config import settings

redis_client: aioredis.Redis | None = None
_redis_loop_id: int | None = None


async def get_redis() -> aioredis.Redis:
    global redis_client, _redis_loop_id
    loop = asyncio.get_running_loop()
    current_loop_id = id(loop)
    if redis_client is None or _redis_loop_id != current_loop_id:
        pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
        redis_client = aioredis.Redis(connection_pool=pool)
        _redis_loop_id = current_loop_id
    return redis_client


async def close_redis() -> None:
    global redis_client, _redis_loop_id
    if redis_client:
        await redis_client.aclose()
        redis_client = None
        _redis_loop_id = None
