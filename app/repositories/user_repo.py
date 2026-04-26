from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.user import User


async def get_user_by_id(db: AsyncSession, user_id) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_google_id(db: AsyncSession, google_id: str) -> User | None:
    result = await db.execute(select(User).where(User.google_id == google_id))
    return result.scalar_one_or_none()


async def get_user_with_org(db: AsyncSession, user_id) -> User | None:
    result = await db.execute(
        select(User)
        .options(selectinload(User.organization), selectinload(User.memberships))
        .where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def get_user_with_org_and_memberships(db: AsyncSession, user_id) -> User | None:
    result = await db.execute(
        select(User)
        .options(selectinload(User.organization), selectinload(User.memberships))
        .where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def get_user_with_org_and_social(db: AsyncSession, user_id) -> User | None:
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.organization),
            selectinload(User.memberships),
            selectinload(User.social_accounts),
        )
        .where(User.id == user_id)
    )
    return result.scalar_one_or_none()
