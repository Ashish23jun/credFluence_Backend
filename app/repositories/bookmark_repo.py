import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.bookmark import ProfileBookmark
from app.models.profile import Profile


async def toggle(db: AsyncSession, user_id: str, profile_id: uuid.UUID) -> bool:
    """Toggle bookmark. Returns True if now bookmarked, False if removed."""
    uid = uuid.UUID(user_id)
    existing = (await db.execute(
        select(ProfileBookmark).where(
            ProfileBookmark.user_id == uid,
            ProfileBookmark.profile_id == profile_id,
        )
    )).scalar_one_or_none()

    if existing:
        await db.execute(
            delete(ProfileBookmark).where(
                ProfileBookmark.user_id == uid,
                ProfileBookmark.profile_id == profile_id,
            )
        )
        await db.commit()
        return False

    db.add(ProfileBookmark(user_id=uid, profile_id=profile_id))
    await db.commit()
    return True


async def get_bookmarked_profiles(db: AsyncSession, user_id: str) -> list[Profile]:
    uid = uuid.UUID(user_id)
    rows = await db.execute(
        select(Profile)
        .join(ProfileBookmark, ProfileBookmark.profile_id == Profile.id)
        .where(ProfileBookmark.user_id == uid, Profile.is_opted_out.is_(False))
        .options(selectinload(Profile.organization))
        .order_by(ProfileBookmark.created_at.desc())
    )
    return list(rows.scalars().all())


async def get_bookmarked_handles(db: AsyncSession, user_id: str) -> set[str]:
    uid = uuid.UUID(user_id)
    rows = await db.execute(
        select(Profile.handle)
        .join(ProfileBookmark, ProfileBookmark.profile_id == Profile.id)
        .where(ProfileBookmark.user_id == uid, Profile.is_opted_out.is_(False))
    )
    return {h for (h,) in rows.all() if h}
