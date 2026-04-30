import re

from sqlalchemy import Float, case, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.profile import Profile
from app.models.review import Review, ReviewRating
from app.models.tag_aggregation import TagAggregation

_PUBLIC_STATUSES = ("verified",)
_TRGM_SINGLE_THRESHOLD = 0.35  # word_similarity floor for single-word typo tolerance


def _prefix_tsquery(q: str):
    """
    Build a prefix-aware tsquery: all words exact AND last word with :* suffix.
    'elvish yad' → to_tsquery('simple', 'elvish & yad:*')
    This handles partial typing — 'yad' matches 'yadav' in the index.
    """
    words = [re.sub(r"[^\w]", "", w) for w in q.strip().split()]
    words = [w for w in words if w]
    if not words:
        return None
    terms = [f"{w}" for w in words[:-1]] + [f"{words[-1]}:*"]
    return func.to_tsquery("simple", " & ".join(terms))


def _search_filter(q: str):
    """
    Multi-word: prefix tsquery (last word gets :*) + exact phrase ilike.
    Single-word: prefix tsquery + trgm word_similarity for typo tolerance.
    No bare word_similarity on multi-word queries — shares a single surname
    (e.g. "Yadav") would flood results with false positives.
    """
    words = [w for w in q.strip().split() if w]
    prefix_tsq = _prefix_tsquery(q)

    base = []
    if prefix_tsq is not None:
        base.append(Profile.search_vector.op("@@")(prefix_tsq))

    if len(words) == 1:
        base += [
            func.word_similarity(q, Profile.display_name) > _TRGM_SINGLE_THRESHOLD,
            func.word_similarity(q, Profile.handle) > _TRGM_SINGLE_THRESHOLD,
        ]
    else:
        base += [
            Profile.display_name.ilike(f"%{q}%"),
            Profile.handle.ilike(f"%{q}%"),
        ]

    return or_(*base)


def _search_order(q: str):
    """Rank by ts_rank_cd (cover density, length-normalised) then trust_score."""
    # Use prefix tsquery for ranking too so partial words score correctly
    prefix_tsq = _prefix_tsquery(q)
    tsq = prefix_tsq if prefix_tsq is not None else func.websearch_to_tsquery("simple", q)
    rank = func.ts_rank_cd(Profile.search_vector, tsq, 32)
    trgm = func.greatest(
        func.word_similarity(q, Profile.display_name),
        func.word_similarity(q, Profile.handle),
    )
    # Combined: FTS rank * 0.7 + trigram score * 0.3
    combined = cast(rank * 0.7 + trgm * 0.3, Float)
    return combined.desc()


async def get_profiles_page(
    db: AsyncSession,
    filters: list,
    order_col,
    offset: int,
    limit: int,
    search_query: str | None = None,
) -> list[Profile]:
    stmt = (
        select(Profile)
        .options(selectinload(Profile.organization))
        .where(*filters)
        .order_by(_search_order(search_query) if search_query else order_col)
        .offset(offset)
        .limit(limit)
    )
    if search_query:
        stmt = stmt.where(_search_filter(search_query))
    result = await db.execute(stmt)
    return result.scalars().all()


async def count_profiles(
    db: AsyncSession,
    filters: list,
    search_query: str | None = None,
) -> int:
    stmt = select(func.count(Profile.id)).where(*filters)
    if search_query:
        stmt = stmt.where(_search_filter(search_query))
    return (await db.execute(stmt)).scalar_one()


async def get_avg_ratings_for_profiles(
    db: AsyncSession, profile_ids: list
) -> dict[str, float]:
    rows = (await db.execute(
        select(
            Review.target_profile_id,
            func.avg(cast(ReviewRating.score, Float)).label("avg_rating"),
        )
        .select_from(Review)
        .join(ReviewRating, ReviewRating.review_id == Review.id)
        .where(
            Review.target_profile_id.in_(profile_ids),
            Review.status.in_(_PUBLIC_STATUSES),
        )
        .group_by(Review.target_profile_id)
    )).all()
    return {str(r.target_profile_id): float(r.avg_rating) for r in rows}


async def get_tags_for_profiles(
    db: AsyncSession, profile_ids: list
) -> dict[str, list[dict]]:
    rows = (await db.execute(
        select(TagAggregation)
        .where(TagAggregation.profile_id.in_(profile_ids))
        .order_by(TagAggregation.count.desc())
    )).scalars().all()

    tags_map: dict[str, list[dict]] = {}
    for t in rows:
        pid = str(t.profile_id)
        bucket = tags_map.setdefault(pid, [])
        if len(bucket) < 3:
            bucket.append({"tag": t.tag, "count": t.count})
    return tags_map


async def get_leaderboard_profiles(
    db: AsyncSession,
    role: str | None,
    category: str | None,
    limit: int,
) -> list[Profile]:
    filters = [
        Profile.is_opted_out.is_(False),
        Profile.handle.isnot(None),
        Profile.trust_score.isnot(None),
    ]
    if role:
        filters.append(Profile.profile_type == role)
    if category and category != "all":
        filters.append(Profile.category == category)

    result = await db.execute(
        select(Profile)
        .options(selectinload(Profile.organization))
        .where(*filters)
        .order_by(text("""
            (SELECT (elem->>'followers')::bigint
             FROM jsonb_array_elements(social_links) elem
             WHERE elem->>'platform' = 'instagram'
             LIMIT 1) DESC NULLS LAST
        """))
        .limit(limit)
    )
    return result.scalars().all()


async def get_profile_by_handle(db: AsyncSession, handle: str) -> Profile | None:
    result = await db.execute(
        select(Profile)
        .options(
            selectinload(Profile.organization),
            selectinload(Profile.badges),
            selectinload(Profile.tag_aggregations),
        )
        .where(Profile.handle == handle, Profile.is_opted_out.is_(False))
    )
    return result.scalar_one_or_none()


async def get_profile_id_by_handle(db: AsyncSession, handle: str):
    return (await db.execute(
        select(Profile.id).where(
            Profile.handle == handle,
            Profile.is_opted_out.is_(False),
        )
    )).scalar_one_or_none()


async def get_single_profile_ratings(db: AsyncSession, profile_id):
    score = cast(ReviewRating.score, Float)
    return (await db.execute(
        select(
            func.avg(case((ReviewRating.category == "communication",   score))).label("avg_communication"),
            func.avg(case((ReviewRating.category == "professionalism", score))).label("avg_professionalism"),
            func.avg(case((ReviewRating.category == "quality",         score))).label("avg_quality"),
            func.avg(case((ReviewRating.category == "reliability",     score))).label("avg_reliability"),
            func.avg(score).label("avg_rating"),
        )
        .select_from(Review)
        .join(ReviewRating, ReviewRating.review_id == Review.id)
        .where(
            Review.target_profile_id == profile_id,
            Review.status.in_(_PUBLIC_STATUSES),
        )
    )).one()


async def get_reviews_page(
    db: AsyncSession,
    filters: list,
    offset: int,
    limit: int,
) -> list[Review]:
    from app.models.user import User
    from sqlalchemy.orm import selectinload as _si
    result = await db.execute(
        select(Review)
        .options(
            _si(Review.reviewer).selectinload(User.organization),
            _si(Review.ratings),
            _si(Review.payments),
            _si(Review.tags),
        )
        .where(*filters)
        .order_by(Review.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


async def count_reviews(db: AsyncSession, filters: list) -> int:
    return (
        await db.execute(select(func.count(Review.id)).where(*filters))
    ).scalar_one()
