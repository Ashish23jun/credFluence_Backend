import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, cache_get, cache_set
from app.models.notification_preference import (
    PREF_CHANNELS,
    PREF_TYPES,
    NotificationPreference,
)

_TTL = 600  # 10 minutes


def _pref_key(user_id: str, channel: str, type_: str) -> str:
    return f"notif_pref:{user_id}:{channel}:{type_}"


def _all_prefs_key(user_id: str) -> str:
    return f"notif_pref_all:{user_id}"


async def is_enabled(
    db: AsyncSession,
    user_id: uuid.UUID,
    channel: str,
    type_: str,
) -> bool:
    """Return True if the user has this preference enabled (default: True for missing rows)."""
    key = _pref_key(str(user_id), channel, type_)
    cached = await cache_get(key)
    if cached is not None:
        return bool(cached)

    row = await db.scalar(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.channel == channel,
            NotificationPreference.type == type_,
        )
    )
    enabled = row.enabled if row is not None else True
    await cache_set(key, enabled, _TTL)
    return enabled


async def get_all_preferences(db: AsyncSession, user_id: uuid.UUID) -> dict:
    """Return dict of {channel: {type: enabled}} with defaults (True) for missing rows."""
    cached = await cache_get(_all_prefs_key(str(user_id)))
    if cached is not None:
        return cached

    # Build defaults — everything enabled
    prefs: dict = {ch: {t: True for t in PREF_TYPES} for ch in PREF_CHANNELS}

    rows = (
        await db.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id
            )
        )
    ).scalars().all()

    for row in rows:
        if row.channel in prefs and row.type in prefs[row.channel]:
            prefs[row.channel][row.type] = row.enabled

    await cache_set(_all_prefs_key(str(user_id)), prefs, _TTL)
    return prefs


async def upsert_preference(
    db: AsyncSession,
    user_id: uuid.UUID,
    channel: str,
    type_: str,
    enabled: bool,
) -> None:
    """Insert or update a single preference row, then invalidate caches."""
    stmt = (
        pg_insert(NotificationPreference)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            channel=channel,
            type=type_,
            enabled=enabled,
        )
        .on_conflict_do_update(
            constraint="uq_notif_pref",
            set_={"enabled": enabled},
        )
    )
    await db.execute(stmt)
    await db.commit()

    await cache_delete(_pref_key(str(user_id), channel, type_))
    await cache_delete(_all_prefs_key(str(user_id)))


async def reset_preferences(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Delete all stored rows (restores all-enabled defaults), invalidate caches."""
    await db.execute(
        delete(NotificationPreference).where(
            NotificationPreference.user_id == user_id
        )
    )
    await db.commit()
    await cache_delete(_all_prefs_key(str(user_id)))
    for ch in PREF_CHANNELS:
        for t in PREF_TYPES:
            await cache_delete(_pref_key(str(user_id), ch, t))
