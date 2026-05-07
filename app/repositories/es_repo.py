"""Elasticsearch repository for profile search.

Responsibilities:
- ensure_index()       — create the index with the right mapping on startup
- index_profile()      — upsert one profile document into ES
- delete_profile()     — remove a profile from ES (opt-out)
- search_profiles()    — multi-match search with filters + sort
- bulk_reindex()       — push all existing profiles on first setup
"""
import logging
from typing import Any

from elasticsearch import NotFoundError, BadRequestError

from app.core.elastic import PROFILES_INDEX, get_es

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Index mapping
# Explanation:
#   - text fields (display_name, bio, handle) are full-text searchable
#   - keyword fields (profile_type, category, niches, languages) are exact-
#     match filterable — not analyzed, so "Fashion" != "fashion" won't break
#   - integer / boolean fields are just stored and used for filtering/sorting
# ─────────────────────────────────────────────────────────────────────────────

_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "analysis": {
            "tokenizer": {
                "handle_tokenizer": {
                    "type": "pattern",
                    "pattern": "[^a-zA-Z0-9]",
                },
            },
            "analyzer": {
                "handle_analyzer": {
                    "type": "custom",
                    "tokenizer": "handle_tokenizer",
                    "filter": ["lowercase", "asciifolding"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "profile_id":      {"type": "keyword"},
            "handle":          {"type": "text", "analyzer": "handle_analyzer", "fields": {"keyword": {"type": "keyword"}}},
            "display_name":    {"type": "text", "analyzer": "standard"},
            "org_name":        {"type": "text", "analyzer": "standard"},
            "bio":             {"type": "text", "analyzer": "standard"},
            "social_handles":  {"type": "text", "analyzer": "handle_analyzer"},
            "profile_type":    {"type": "keyword"},
            "category":        {"type": "keyword"},
            "niches":          {"type": "keyword"},
            "languages":       {"type": "keyword"},
            "trust_score":     {"type": "integer"},
            "review_count":    {"type": "integer"},
            "follower_count":  {"type": "long"},
            "primary_platform":{"type": "keyword"},
            "avatar_url":      {"type": "keyword", "index": False},
            "is_claimed":      {"type": "boolean"},
            "is_dummy":        {"type": "boolean"},
            "is_opted_out":    {"type": "boolean"},
            "created_at":      {"type": "date"},
        }
    },
}


async def ensure_index() -> None:
    """Called on app startup — creates the index if it doesn't exist yet."""
    es = get_es()
    try:
        await es.indices.get(index=PROFILES_INDEX)
        logger.debug("Elasticsearch index already exists: %s", PROFILES_INDEX)
        return
    except NotFoundError:
        pass
    await es.indices.create(index=PROFILES_INDEX, body=_MAPPING)
    logger.info("Created Elasticsearch index: %s", PROFILES_INDEX)


def _build_doc(profile_data: dict) -> dict:
    """Convert a profile dict (from DB) into an ES document."""
    return {
        "profile_id":       profile_data["profile_id"],
        "handle":           profile_data.get("handle") or "",
        "display_name":     profile_data.get("display_name") or "",
        "org_name":         profile_data.get("org_name") or "",
        "bio":              profile_data.get("bio") or "",
        "social_handles":   profile_data.get("social_handles") or [],
        "profile_type":     profile_data.get("profile_type") or "",
        "category":         profile_data.get("category") or "",
        "niches":           profile_data.get("niches") or [],
        "languages":        profile_data.get("languages") or [],
        "trust_score":      profile_data.get("trust_score") or 45,
        "review_count":     profile_data.get("review_count") or 0,
        "follower_count":   profile_data.get("follower_count") or 0,
        "primary_platform": profile_data.get("primary_platform") or "",
        "avatar_url":       profile_data.get("avatar_url") or "",
        "is_claimed":       profile_data.get("is_claimed") or False,
        "is_dummy":         profile_data.get("is_dummy") or False,
        "is_opted_out":     profile_data.get("is_opted_out") or False,
        "created_at":       profile_data.get("created_at") or "",
    }


async def index_profile(profile_data: dict) -> None:
    """Upsert a single profile into ES. profile_data must have 'profile_id'."""
    es = get_es()
    doc = _build_doc(profile_data)
    await es.index(
        index=PROFILES_INDEX,
        id=profile_data["profile_id"],
        document=doc,
    )


async def delete_profile(profile_id: str) -> None:
    """Remove a profile from ES (called on opt-out)."""
    es = get_es()
    try:
        await es.delete(index=PROFILES_INDEX, id=profile_id)
    except NotFoundError:
        pass


async def search_profiles(
    q: str,
    kind: str | None = None,
    category: str | None = None,
    offset: int = 0,
    limit: int = 20,
    sort: str = "trust_desc",
) -> tuple[list[dict], int]:
    """
    Full-text search across handle, display_name, org_name, bio.
    Returns (list_of_profile_dicts, total_count).
    """
    es = get_es()

    # ── filters (always applied) ──────────────────────────────────────────────
    must_filters: list[dict] = [
        {"term": {"is_opted_out": False}},
        {"exists": {"field": "handle"}},
    ]
    if kind:
        must_filters.append({"term": {"profile_type": kind}})
    if category and category != "all":
        must_filters.append({"term": {"category": category}})

    # ── full-text query ───────────────────────────────────────────────────────
    # multi_match searches across multiple fields simultaneously.
    # handle^3 means a match on handle is 3x more relevant than a bio match.
    query: dict[str, Any] = {
        "bool": {
            "must": [
                {
                    "multi_match": {
                        "query": q,
                        "fields": ["handle^3", "social_handles^3", "display_name^2", "org_name^2", "bio"],
                        "type": "best_fields",
                        "fuzziness": "AUTO",   # handles typos automatically
                        "prefix_length": 2,    # first 2 chars must match exactly
                    }
                }
            ],
            "filter": must_filters,
        }
    }

    # ── sort ──────────────────────────────────────────────────────────────────
    # For text search, relevance (_score) always leads; trust_score is secondary
    # so that "mortal" finds ig_mortal before a higher-scored "Mondal" profile.
    # Explicit sort overrides (review_count, followers_desc, newest) still apply.
    sort_clause: list[dict] = []
    if sort == "trust_asc":
        sort_clause = [{"trust_score": "asc"}, "_score"]
    elif sort == "review_count":
        sort_clause = [{"review_count": "desc"}, "_score"]
    elif sort == "followers_desc":
        sort_clause = [{"follower_count": "desc"}, "_score"]
    elif sort == "newest":
        sort_clause = [{"created_at": "desc"}, "_score"]
    else:
        # trust_desc (default) and unknown — relevance first, trust breaks ties
        sort_clause = ["_score", {"trust_score": "desc"}]

    response = await es.search(
        index=PROFILES_INDEX,
        body={
            "query": query,
            "sort": sort_clause,
            "from": offset,
            "size": limit,
            "_source": True,
        },
    )

    total = response["hits"]["total"]["value"]
    hits = [hit["_source"] for hit in response["hits"]["hits"]]
    return hits, total


async def bulk_reindex(profile_docs: list[dict]) -> int:
    """Bulk-upsert a list of profile dicts. Returns count indexed."""
    if not profile_docs:
        return 0

    es = get_es()
    operations = []
    for p in profile_docs:
        operations.append({"index": {"_index": PROFILES_INDEX, "_id": p["profile_id"]}})
        operations.append(_build_doc(p))

    response = await es.bulk(operations=operations)
    errors = [item for item in response["items"] if "error" in item.get("index", {})]
    if errors:
        logger.error("bulk_reindex: %d errors", len(errors))
    return len(profile_docs) - len(errors)
