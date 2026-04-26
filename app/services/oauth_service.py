from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.organization import Organization
from app.models.social_account import SocialAccount


def connect_redirect(platform: str, status: str, error_detail: str | None = None) -> RedirectResponse:
    params: dict = {"platform": platform, "status": status}
    if error_detail:
        params["error_detail"] = error_detail
    return RedirectResponse(
        url=f"{settings.frontend_url}/onboarding/connect-callback?{urlencode(params)}"
    )


def auth_redirect(jwt_access: str, jwt_refresh: str, user) -> RedirectResponse:
    params = urlencode({
        "access_token": jwt_access,
        "refresh_token": jwt_refresh,
        "user_id": str(user.id),
    })
    return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{params}")


def build_youtube_stats(ch: dict) -> dict:
    snippet = ch.get("snippet", {})
    stats = ch.get("statistics", {})
    subscribers = int(stats.get("subscriberCount", 0))
    video_count = int(stats.get("videoCount", 0))
    return {
        "subscribers": subscribers,
        "video_count": video_count,
        "total_views": int(stats.get("viewCount", 0)),
        "youtube_handle": snippet.get("customUrl"),
        "description": snippet.get("description"),
        "meets_threshold": subscribers >= 1 and video_count >= 0,
        "threshold_requirements": {"min_subscribers": 1, "min_videos": 0},
    }


def build_instagram_stats(ig: dict) -> dict:
    return {
        "followers_count": ig.get("followers_count", 0),
        "media_count": ig.get("media_count", 0),
        "biography": ig.get("biography"),
        "account_type": ig.get("account_type", "PERSONAL"),
    }


async def maybe_verify_creator(db: AsyncSession, user) -> None:
    """Auto-verify a creator's org when they connect a qualifying platform."""
    if user.role != "creator" or not user.organization_id:
        return

    accounts_result = await db.execute(
        select(SocialAccount).where(SocialAccount.user_id == user.id)
    )
    accounts = accounts_result.scalars().all()

    qualified = any(
        (acc.platform == "youtube" and (acc.stats or {}).get("meets_threshold"))
        or (acc.platform == "instagram" and (acc.stats or {}).get("account_type") in ("BUSINESS", "MEDIA_CREATOR"))
        for acc in accounts
    )

    if not qualified:
        return

    org_result = await db.execute(
        select(Organization).where(Organization.id == user.organization_id)
    )
    org = org_result.scalar_one()
    if org.verification_status != "verified":
        org.verification_status = "verified"
        org.verified_at = datetime.now(UTC)
