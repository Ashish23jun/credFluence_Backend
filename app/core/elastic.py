"""Shared async Elasticsearch client.

One module-level client instance reused across all requests and Celery tasks.
Call `get_es()` to get the client — don't instantiate AsyncElasticsearch directly anywhere else.
"""
import logging

from elasticsearch import AsyncElasticsearch

from app.core.config import settings

logger = logging.getLogger(__name__)

_es_client: AsyncElasticsearch | None = None

PROFILES_INDEX = "cf_profiles"


def get_es() -> AsyncElasticsearch:
    global _es_client
    if _es_client is None:
        _es_client = AsyncElasticsearch(hosts=[settings.es_host])
    return _es_client


async def close_es() -> None:
    global _es_client
    if _es_client is not None:
        await _es_client.close()
        _es_client = None
