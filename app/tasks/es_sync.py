"""Celery tasks for syncing profiles to Elasticsearch.

sync_profile_to_es   — called after any profile save (single profile)
reindex_all_profiles — one-time bulk reindex of all existing profiles
"""
import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import task_db_session
from app.models.profile import Profile
from app.repositories import es_repo
from app.repositories.social_account_repo import get_accounts_by_org_ids
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _pick_primary_social(social_accounts: list) -> tuple[int, str]:
    """Return (follower_count, platform) for the highest-reach account."""
    best = (0, "")
    for acct in social_accounts:
        stats = acct.stats or {}
        if acct.platform == "instagram":
            followers = int(stats.get("followers_count") or 0)
        elif acct.platform == "youtube":
            followers = int(stats.get("subscribers") or 0)
        elif acct.platform == "linkedin":
            followers = int(stats.get("connections") or 0)
        else:
            followers = 0
        if followers > best[0]:
            best = (followers, acct.platform)
    return best


def _profile_to_doc(profile: Profile, social_accounts: list) -> dict:
    follower_count, primary_platform = _pick_primary_social(social_accounts)
    return {
        "profile_id":       str(profile.id),
        "handle":           profile.handle or "",
        "display_name":     profile.display_name or "",
        "org_name":         profile.organization.name if profile.organization else "",
        "bio":              profile.bio or "",
        "profile_type":     profile.profile_type or "",
        "category":         profile.category or "",
        "niches":           profile.niches or [],
        "languages":        profile.languages or [],
        "trust_score":      profile.trust_score or 45,
        "review_count":     profile.review_count or 0,
        "follower_count":   follower_count,
        "primary_platform": primary_platform,
        "avatar_url":       profile.avatar_url or "",
        "is_claimed":       profile.is_claimed,
        "is_dummy":         profile.is_dummy,
        "is_opted_out":     profile.is_opted_out,
        "created_at":       profile.created_at.isoformat() if profile.created_at else "",
    }


@celery_app.task(
    name="app.tasks.es_sync.sync_profile_to_es",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def sync_profile_to_es(profile_id: str) -> None:
    asyncio.run(_sync_profile(profile_id))


@celery_app.task(name="app.tasks.es_sync.reindex_all_profiles")
def reindex_all_profiles() -> None:
    asyncio.run(_reindex_all())


async def _sync_profile(profile_id: str) -> None:
    async with task_db_session() as db:
        profile = await db.scalar(
            select(Profile)
            .where(Profile.id == uuid.UUID(profile_id))
            .options(selectinload(Profile.organization))
        )
        if not profile:
            logger.warning("sync_profile_to_es: profile %s not found", profile_id)
            return

        if profile.is_opted_out:
            await es_repo.delete_profile(profile_id)
            return

        org_sa_map = await get_accounts_by_org_ids(db, [profile.organization_id])
        social_accounts = org_sa_map.get(str(profile.organization_id), [])
        doc = _profile_to_doc(profile, social_accounts)
        await es_repo.index_profile(doc)
        logger.info("sync_profile_to_es: indexed profile %s", profile_id)


async def _reindex_all() -> None:
    async with task_db_session() as db:
        result = await db.execute(
            select(Profile)
            .options(selectinload(Profile.organization))
            .where(Profile.handle.isnot(None))
        )
        profiles = result.scalars().all()

        org_ids = [p.organization_id for p in profiles]
        org_sa_map = await get_accounts_by_org_ids(db, org_ids)

    docs = [
        _profile_to_doc(p, org_sa_map.get(str(p.organization_id), []))
        for p in profiles
    ]
    count = await es_repo.bulk_reindex(docs)
    logger.info("reindex_all_profiles: indexed %d profiles", count)
