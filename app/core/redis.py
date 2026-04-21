"""
Redis client with explicit connection pool.

One process-wide pool is shared by cache, rate limiter, OAuth state store, and
Celery result backend. max_connections caps concurrent Redis ops per worker to
keep a single worker from saturating Redis under load.
"""

import redis.asyncio as aioredis

from app.core.config import settings

redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global redis_client
    if redis_client is None:
        pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=50,
        )
        redis_client = aioredis.Redis(connection_pool=pool)
    return redis_client


async def close_redis() -> None:
    global redis_client
    if redis_client:
        await redis_client.aclose()
        redis_client = None
