"""
Profiles router — public read-only endpoints.

GET  /profiles                       — paginated list with filters
GET  /profiles/leaderboard           — top profiles by trust score (Redis cached, 5 min TTL)
GET  /profiles/{handle}              — single profile detail
GET  /profiles/{handle}/reviews      — paginated reviews for a profile
GET  /profiles/{handle}/score-history
DELETE /profiles/{handle}/opt-out    — GDPR: mask PII, hide from listings
"""

import logging
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get, cache_set, invalidate_profile
from app.core.database import get_db
from app.core.dependencies import get_current_user, get_optional_user
from app.models.profile import Profile
from app.models.review import Review
from app.models.score_history import ScoreHistory
from app.repositories import es_repo, profile_repo
from app.repositories.social_account_repo import get_accounts_by_org_ids
from app.services import profile_service

logger = logging.getLogger(__name__)

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
_PARTY_STATUSES = ("verified", "in_dispute_window", "disputed", "pending_verification", "rejected", "quarantined")


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
    offset = (page - 1) * limit

    # ── Elasticsearch path (search queries only) ──────────────────────────────
    if search_query:
        try:
            es_hits, total = await es_repo.search_profiles(
                q=search_query,
                kind=kind,
                category=category,
                offset=offset,
                limit=limit,
                sort=sort,
            )
            if es_hits:
                logger.info("search '%s' → ES (%d hits)", search_query, total)
                # ES returned results — fetch full profile data from Postgres
                # using the profile_ids ES gave us (preserving ES rank order)
                profile_ids_ordered = [_uuid.UUID(h["profile_id"]) for h in es_hits]
                profiles = await profile_repo.get_profiles_by_ids(db, profile_ids_ordered)
                # Re-sort to match ES ranking order
                id_to_profile = {p.id: p for p in profiles}
                profiles = [id_to_profile[pid] for pid in profile_ids_ordered if pid in id_to_profile]

                org_ids = [p.organization_id for p in profiles]
                profile_ids = [p.id for p in profiles]
                ratings_map = await profile_repo.get_avg_ratings_for_profiles(db, profile_ids)
                org_sa_map = await get_accounts_by_org_ids(db, org_ids)
                tags_map = await profile_repo.get_tags_for_profiles(db, profile_ids)

                items = [
                    profile_service.build_profile_list_item(
                        p, p.organization,
                        org_sa_map.get(str(p.organization_id), []),
                        ratings_map.get(str(p.id)),
                        tags_map.get(str(p.id), []),
                    )
                    for p in profiles
                ]
                return {"success": True, "message": "OK", "data": {
                    "items": items, "total": total, "page": page, "limit": limit,
                    "pages": -(-total // limit),
                }}
        except Exception as e:
            logger.warning("Elasticsearch unavailable (%s), falling back to Postgres search", e)

    # ── Postgres path (no search query, or ES fallback) ───────────────────────
    filters = [
        Profile.is_opted_out.is_(False),
        Profile.handle.isnot(None),
    ]
    if kind:
        filters.append(Profile.profile_type == kind)
    if category and category != "all":
        filters.append(Profile.category == category)

    order_col = {
        "trust_desc":     Profile.trust_score.desc(),
        "trust_asc":      Profile.trust_score.asc(),
        "review_count":   Profile.review_count.desc(),
        "newest":         Profile.created_at.desc(),
        "followers_desc": _IG_FOLLOWERS_SORT,
    }[sort]

    total = await profile_repo.count_profiles(db, filters, search_query=search_query)
    profiles = await profile_repo.get_profiles_page(
        db, filters, order_col, offset=offset, limit=limit, search_query=search_query,
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
            p, p.organization,
            org_sa_map.get(str(p.organization_id), []),
            ratings_map.get(str(p.id)),
            tags_map.get(str(p.id), []),
        )
        for p in profiles
    ]
    return {"success": True, "message": "OK", "data": {
        "items": items, "total": total, "page": page, "limit": limit,
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
# GET /profiles/{handle}/score-history
# ---------------------------------------------------------------------------

@router.get("/{handle}/score-history")
async def get_score_history(
    handle: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    row = await profile_repo.get_profile_id_by_handle(db, handle)
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")

    result = await db.execute(
        select(ScoreHistory)
        .where(ScoreHistory.profile_id == row)
        .order_by(ScoreHistory.created_at.desc())
        .limit(limit)
    )
    entries = result.scalars().all()

    return {"success": True, "message": "OK", "data": [
        {
            "score": e.score,
            "review_count": e.review_count,
            "reason": e.reason,
            "created_at": e.created_at.isoformat(),
        }
        for e in reversed(entries)  # chronological order for charting
    ]}


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
    current_user: dict | None = Depends(get_optional_user),
) -> dict:
    row = await profile_repo.get_profile_slim_by_handle(db, handle)
    if not row:
        raise HTTPException(status_code=404, detail="Profile not found")
    profile_id, target_org_id = row.id, row.organization_id

    # Visibility: only verified reviews are public.
    # The reviewer and the recipient org also see in_dispute_window /
    # disputed / pending_verification so they can act on them.
    if current_user:
        user_id = _uuid.UUID(current_user["id"])
        user_org_id = (current_user.get("org") or {}).get("id")
        is_recipient = user_org_id and str(target_org_id) == str(user_org_id)

        if is_recipient:
            status_filter = Review.status.in_(_PARTY_STATUSES)
        else:
            # Show public reviews plus the reviewer's own (any status)
            status_filter = or_(
                Review.status == "verified",
                Review.reviewer_id == user_id,
            )
    else:
        status_filter = Review.status == "verified"

    filters = [Review.target_profile_id == profile_id, status_filter]
    if relationship_type:
        filters.append(Review.relationship_type == relationship_type)

    total = await profile_repo.count_reviews(db, filters)
    reviews = await profile_repo.get_reviews_page(
        db, filters, offset=(page - 1) * limit, limit=limit
    )

    current_user_id = current_user["id"] if current_user else None
    return {"success": True, "message": "OK", "data": {
        "items": [profile_service.build_review_item(r, current_user_id) for r in reviews],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": -(-total // limit),
    }}


# ---------------------------------------------------------------------------
# DELETE /profiles/{handle}/opt-out  — GDPR
# ---------------------------------------------------------------------------

@router.delete("/{handle}/opt-out")
async def opt_out_profile(
    handle: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    profile = await profile_repo.get_profile_by_handle(db, handle)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    user_org_id = (current_user.get("org") or {}).get("id")
    if not user_org_id or str(profile.organization_id) != str(user_org_id):
        raise HTTPException(status_code=403, detail="You can only opt out your own profile")

    profile.is_opted_out = True
    profile.bio = None
    profile.location = None
    profile.avatar_url = None
    profile.social_links = None
    profile.niches = None
    profile.languages = None
    await db.commit()
    await invalidate_profile(str(profile.id), handle)

    from app.tasks.es_sync import sync_profile_to_es
    sync_profile_to_es.delay(str(profile.id))

    return {"success": True, "message": "Profile opted out. Your data has been removed from public listings."}
