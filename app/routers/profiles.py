"""
Profiles router — public read-only endpoints.

GET /profiles                    — paginated list with filters
GET /profiles/leaderboard        — top profiles by trust score (Redis cached, 5 min TTL)
GET /profiles/{handle}           — single profile detail
GET /profiles/{handle}/reviews   — paginated reviews for a profile
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get, cache_set
from app.core.database import get_db
from app.models.profile import Profile
from app.models.review import Review
from app.repositories import profile_repo
from app.repositories.social_account_repo import get_accounts_by_org_ids
from app.services import profile_service

router = APIRouter(prefix="/profiles", tags=["profiles"])

_LEADERBOARD_TTL = 300  # 5 minutes

_VALID_KINDS = ("creator", "agency", "brand")
_VALID_SORTS = ("trust_desc", "trust_asc", "review_count", "newest", "followers_desc")

# Extracts Instagram followers from social_links JSONB for sorting
_IG_FOLLOWERS_SORT = text("""
    (SELECT (elem->>'followers')::bigint
     FROM jsonb_array_elements(social_links) elem
     WHERE elem->>'platform' = 'instagram'
     LIMIT 1) DESC NULLS LAST
""")
_PUBLIC_STATUSES = ("verified", "in_dispute_window", "disputed")


# ---------------------------------------------------------------------------
# GET /profiles
# ---------------------------------------------------------------------------

@router.get("")
async def list_profiles(
    kind: str | None = Query(None, description="creator | agency | brand"),
    category: str | None = Query(None),
    sort: str = Query("trust_desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=50),
    q: str | None = Query(None, description="Full-text search across name, handle, bio"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if kind and kind not in _VALID_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {_VALID_KINDS}")
    if sort not in _VALID_SORTS:
        sort = "trust_desc"

    search_query = q.strip() if q and q.strip() else None

    filters = [
        Profile.is_opted_out.is_(False),
        Profile.handle.isnot(None),
    ]
    if kind:
        filters.append(Profile.profile_type == kind)
    if category and category != "all":
        filters.append(Profile.category == category)

    order_col = {
        "trust_desc":    Profile.trust_score.desc(),
        "trust_asc":     Profile.trust_score.asc(),
        "review_count":  Profile.review_count.desc(),
        "newest":        Profile.created_at.desc(),
        "followers_desc": _IG_FOLLOWERS_SORT,
    }[sort]

    total = await profile_repo.count_profiles(db, filters, search_query=search_query)
    profiles = await profile_repo.get_profiles_page(
        db, filters, order_col, offset=(page - 1) * limit, limit=limit,
        search_query=search_query,
    )

    if not profiles:
        return {"success": True, "message": "OK", "data": {
            "items": [], "total": total, "page": page, "limit": limit, "pages": 0,
        }}

    profile_ids = [p.id for p in profiles]
    org_ids = [p.organization_id for p in profiles]

    ratings_map = await profile_repo.get_avg_ratings_for_profiles(db, profile_ids)
    org_sa_map = await get_accounts_by_org_ids(db, org_ids)
    tags_map = await profile_repo.get_tags_for_profiles(db, profile_ids)

    items = [
        profile_service.build_profile_list_item(
            p,
            p.organization,
            org_sa_map.get(str(p.organization_id), []),
            ratings_map.get(str(p.id)),
            tags_map.get(str(p.id), []),
        )
        for p in profiles
    ]

    return {"success": True, "message": "OK", "data": {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": -(-total // limit),
    }}


# ---------------------------------------------------------------------------
# GET /profiles/leaderboard
# ---------------------------------------------------------------------------

@router.get("/leaderboard")
async def get_leaderboard(
    role: str | None = Query(None, description="creator | agency | brand"),
    category: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict:
    clean_role = role if role in _VALID_KINDS else None
    cache_key = f"leaderboard:{clean_role or 'all'}:{category or 'all'}:{limit}"

    cached = await cache_get(cache_key)
    if cached:
        return cached

    profiles = await profile_repo.get_leaderboard_profiles(db, clean_role, category, limit)
    org_ids = [p.organization_id for p in profiles]
    org_sa_map = await get_accounts_by_org_ids(db, org_ids)

    items = [
        profile_service.build_leaderboard_item(p, p.organization, org_sa_map.get(str(p.organization_id), []))
        for p in profiles
    ]

    response = {"success": True, "message": "OK", "data": items}
    await cache_set(cache_key, response, ttl_seconds=_LEADERBOARD_TTL)
    return response


# ---------------------------------------------------------------------------
# GET /profiles/{handle}
# ---------------------------------------------------------------------------

@router.get("/{handle}")
async def get_profile(
    handle: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    profile = await profile_repo.get_profile_by_handle(db, handle)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    org_sa_map = await get_accounts_by_org_ids(db, [profile.organization_id])
    social_accounts = org_sa_map.get(str(profile.organization_id), [])
    rating_row = await profile_repo.get_single_profile_ratings(db, profile.id)
    top_tags = sorted(profile.tag_aggregations, key=lambda t: t.count, reverse=True)[:10]
    top_tags_list = [{"tag": t.tag, "count": t.count} for t in top_tags]

    return {"success": True, "message": "OK", "data": profile_service.build_profile_detail(
        profile, profile.organization, social_accounts, rating_row, top_tags_list
    )}


# ---------------------------------------------------------------------------
# GET /profiles/{handle}/reviews
# ---------------------------------------------------------------------------

@router.get("/{handle}/reviews")
async def get_profile_reviews(
    handle: str,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    relationship_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    profile_id = await profile_repo.get_profile_id_by_handle(db, handle)
    if not profile_id:
        raise HTTPException(status_code=404, detail="Profile not found")

    filters = [
        Review.target_profile_id == profile_id,
        Review.status.in_(_PUBLIC_STATUSES),
    ]
    if relationship_type:
        filters.append(Review.relationship_type == relationship_type)

    total = await profile_repo.count_reviews(db, filters)
    reviews = await profile_repo.get_reviews_page(
        db, filters, offset=(page - 1) * limit, limit=limit
    )

    return {"success": True, "message": "OK", "data": {
        "items": [profile_service.build_review_item(r) for r in reviews],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": -(-total // limit),
    }}
