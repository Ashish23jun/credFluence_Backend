from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.social_account import SocialAccount
from app.models.user import User


async def get_accounts_by_org_ids(
    db: AsyncSession, org_ids: list
) -> dict[str, list[SocialAccount]]:
    """Return {org_id_str: [SocialAccount, ...]} for a batch of org IDs."""
    if not org_ids:
        return {}

    users_result = await db.execute(
        select(User.id, User.organization_id).where(User.organization_id.in_(org_ids))
    )
    user_rows = users_result.all()
    if not user_rows:
        return {}

    user_ids = [r.id for r in user_rows]
    user_org_map = {str(r.id): str(r.organization_id) for r in user_rows}

    sa_result = await db.execute(
        select(SocialAccount).where(SocialAccount.user_id.in_(user_ids))
    )
    mapping: dict[str, list[SocialAccount]] = {}
    for sa in sa_result.scalars().all():
        oid = user_org_map.get(str(sa.user_id))
        if oid:
            mapping.setdefault(oid, []).append(sa)
    return mapping


async def get_accounts_by_user_id(
    db: AsyncSession, user_id
) -> list[SocialAccount]:
    result = await db.execute(
        select(SocialAccount).where(SocialAccount.user_id == user_id)
    )
    return result.scalars().all()


async def upsert_social_account(
    db: AsyncSession,
    user,
    platform: str,
    platform_account_id: str,
    username: str | None,
    display_name: str | None,
    avatar_url: str | None,
    access_token: str | None,
    refresh_token: str | None,
    stats: dict,
) -> SocialAccount:
    from datetime import UTC, datetime

    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.user_id == user.id,
            SocialAccount.platform == platform,
            SocialAccount.platform_account_id == platform_account_id,
        )
    )
    sa = result.scalar_one_or_none()

    existing = await db.execute(
        select(SocialAccount).where(
            SocialAccount.user_id == user.id,
            SocialAccount.platform == platform,
        )
    )
    should_be_primary = not any(a.is_primary for a in existing.scalars().all())

    if sa:
        sa.username = username
        sa.display_name = display_name
        if avatar_url:
            sa.avatar_url = avatar_url
        sa.access_token = access_token
        if refresh_token:
            sa.refresh_token = refresh_token
        sa.stats = stats
        sa.last_synced_at = datetime.now(UTC)
    else:
        sa = SocialAccount(
            user_id=user.id,
            platform=platform,
            platform_account_id=platform_account_id,
            username=username,
            display_name=display_name,
            avatar_url=avatar_url,
            is_primary=should_be_primary,
            access_token=access_token,
            refresh_token=refresh_token,
            stats=stats,
            connected_at=datetime.now(UTC),
            last_synced_at=datetime.now(UTC),
        )
        db.add(sa)

    return sa
