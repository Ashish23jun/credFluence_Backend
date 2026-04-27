from sqlalchemy import Float, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.profile import Profile
from app.models.review import Review
from app.models.tag_aggregation import TagAggregation

_PUBLIC_STATUSES = ("verified", "in_dispute_window", "disputed")
_TRGM_THRESHOLD = 0.2  # word_similarity score floor for fuzzy match


def _search_filter(q: str):
    """Return an OR filter covering tsvector FTS + trigram fuzzy on name/handle."""
    tsq = func.websearch_to_tsquery("simple", q)
    return or_(
        Profile.search_vector.op("@@")(tsq),
        func.word_similarity(q, Profile.display_name) > _TRGM_THRESHOLD,
        func.word_similarity(q, Profile.handle) > _TRGM_THRESHOLD,
        Profile.display_name.ilike(f"%{q}%"),
        Profile.handle.ilike(f"%{q}%"),
    )


def _search_order(q: str):
    """Rank by ts_rank_cd (cover density, length-normalised) then trust_score."""
    tsq = func.websearch_to_tsquery("simple", q)
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
            func.avg(
                (Review.rating_communication + Review.rating_professionalism +
                 Review.rating_quality + Review.rating_reliability) / 4.0
            ).label("avg_rating"),
        )
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
        .order_by(Profile.trust_score.desc())
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
    return (await db.execute(
        select(
            func.avg(Review.rating_communication).label("avg_communication"),
            func.avg(Review.rating_professionalism).label("avg_professionalism"),
            func.avg(Review.rating_quality).label("avg_quality"),
            func.avg(Review.rating_reliability).label("avg_reliability"),
            func.avg(
                (Review.rating_communication + Review.rating_professionalism +
                 Review.rating_quality + Review.rating_reliability) / 4.0
            ).label("avg_rating"),
        )
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
        .options(_si(Review.reviewer).selectinload(User.organization))
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
