"""
OAuth 2.0 flows: LinkedIn + Instagram + Google/YouTube.

LinkedIn:  GET /auth/linkedin                    → GET /auth/linkedin/callback
Instagram: GET /auth/oauth/instagram             → GET /auth/oauth/instagram/callback
Google:    GET /auth/oauth/google?role=...       → GET /auth/oauth/google/callback

Google rules:
  - All roles can use Google login
  - Agency/Brand: must have hd (Google Workspace hosted domain) — personal Gmail blocked
  - Creator: any Google account allowed
    → has YouTube channel  → access_level = full
    → no YouTube channel   → access_level = limited (can't submit/receive reviews yet)

Social accounts:
  - All connected platforms stored in social_accounts table (one row per account)
  - Multiple accounts per platform supported
  - First connected account is automatically set as primary
"""

import json
import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.http_client import get_http_client
from app.core.oauth_state import consume_state, save_state
from app.core.security import create_access_token, create_refresh_token
from app.models.profile import Profile
from app.models.social_account import SocialAccount
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["oauth"])

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

INSTAGRAM_AUTH_URL = "https://api.instagram.com/oauth/authorize"
INSTAGRAM_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
INSTAGRAM_LONG_LIVED_TOKEN_URL = "https://graph.instagram.com/access_token"
INSTAGRAM_GRAPH_URL = "https://graph.instagram.com/v25.0"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

GOOGLE_SCOPES = " ".join([
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/youtube.readonly",
])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_primary_for_platform(social_accounts: list[SocialAccount], platform: str) -> bool:
    """Return True if user has no existing primary account for this platform."""
    return not any(sa.platform == platform and sa.is_primary for sa in social_accounts)


async def _get_or_create_social_account(
    db: AsyncSession,
    user: User,
    platform: str,
    platform_account_id: str,
    username: str | None,
    display_name: str | None,
    avatar_url: str | None,
    access_token: str | None,
    refresh_token: str | None,
    stats: dict,
) -> SocialAccount:
    """Upsert a SocialAccount row. Returns the account."""
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.user_id == user.id,
            SocialAccount.platform == platform,
            SocialAccount.platform_account_id == platform_account_id,
        )
    )
    sa = result.scalar_one_or_none()

    # Determine if this should be primary (first account for this platform)
    existing = await db.execute(
        select(SocialAccount).where(
            SocialAccount.user_id == user.id,
            SocialAccount.platform == platform,
        )
    )
    existing_accounts = existing.scalars().all()
    should_be_primary = not any(a.is_primary for a in existing_accounts)

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


def _build_redirect(frontend_url: str, jwt_access: str, jwt_refresh: str, user: User, extra: dict | None = None) -> RedirectResponse:
    user_payload: dict = {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "is_verified": user.is_verified,
        "subscription_tier": user.subscription_tier,
    }
    if extra:
        user_payload.update(extra)
    params = urlencode({
        "access_token": jwt_access,
        "refresh_token": jwt_refresh,
        "user": json.dumps(user_payload),
    })
    return RedirectResponse(url=f"{frontend_url}/auth/callback?{params}")


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------

@router.get("/linkedin")
async def linkedin_login(
    role: str = Query(default="creator"),
    mode: str = Query(default="signup"),
) -> RedirectResponse:
    if role not in ("creator", "agency", "brand"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if mode not in ("signup", "login"):
        raise HTTPException(status_code=400, detail="mode must be signup or login")

    state = secrets.token_urlsafe(32)
    await save_state(state, role, mode)

    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "state": state,
        "scope": "openid profile email",
    }
    return RedirectResponse(url=f"{LINKEDIN_AUTH_URL}?{urlencode(params)}")


@router.get("/linkedin/callback")
async def linkedin_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    state_data = await consume_state(state)
    if state_data is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    role, mode = state_data

    client = await get_http_client()
    token_resp = await client.post(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.linkedin_redirect_uri,
            "client_id": settings.linkedin_client_id,
            "client_secret": settings.linkedin_client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to exchange LinkedIn code for token")

    access_token = token_resp.json().get("access_token")

    userinfo_resp = await client.get(
        LINKEDIN_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    if userinfo_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch LinkedIn user info")

    userinfo = userinfo_resp.json()
    linkedin_id: str = userinfo.get("sub")
    email: str | None = userinfo.get("email")

    if not linkedin_id:
        raise HTTPException(status_code=400, detail="LinkedIn did not return a user ID")

    # Find existing user
    result = await db.execute(select(User).where(User.linkedin_id == linkedin_id))
    user = result.scalar_one_or_none()

    if not user and email:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.linkedin_id = linkedin_id

    if mode == "login":
        if not user:
            error_params = urlencode({"error": "account_not_found"})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")
        if user.role != role:
            error_params = urlencode({"error": "role_mismatch", "existing_role": user.role})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")
    else:
        # signup mode
        if not user:
            if not email:
                raise HTTPException(status_code=400, detail="LinkedIn did not provide an email")
            user = User(
                email=email,
                linkedin_id=linkedin_id,
                role=role,
                is_verified=True,
                email_verified_at=datetime.now(UTC),
            )
            db.add(user)
            await db.flush()

            li_name: str = userinfo.get("name") or email.split("@")[0]
            profile = Profile(
                user_id=user.id,
                display_name=li_name,
                handle=None,
                avatar_url=userinfo.get("picture"),
                profile_type=role,
                is_claimed=True,
                access_level="full",
            )
            db.add(profile)
        elif user.role != role:
            error_params = urlencode({"error": "role_mismatch", "existing_role": user.role})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")

    # Upsert LinkedIn social account
    await _get_or_create_social_account(
        db=db,
        user=user,
        platform="linkedin",
        platform_account_id=linkedin_id,
        username=None,
        display_name=userinfo.get("name"),
        avatar_url=userinfo.get("picture"),
        access_token=access_token,
        refresh_token=None,
        stats={"email": email, "name": userinfo.get("name")},
    )

    await db.commit()
    await db.refresh(user)

    jwt_access = create_access_token({"sub": str(user.id), "role": user.role})
    jwt_refresh = create_refresh_token({"sub": str(user.id)})
    return _build_redirect(settings.frontend_url, jwt_access, jwt_refresh, user)


# ---------------------------------------------------------------------------
# Instagram OAuth (creators only)
# ---------------------------------------------------------------------------

@router.get("/oauth/instagram")
async def instagram_login(
    mode: str = Query(default="signup"),
) -> RedirectResponse:
    if mode not in ("signup", "login"):
        raise HTTPException(status_code=400, detail="mode must be signup or login")

    state = secrets.token_urlsafe(32)
    await save_state(state, "creator", mode)

    params = {
        "client_id": settings.instagram_client_id,
        "redirect_uri": settings.instagram_redirect_uri,
        "scope": "instagram_business_basic",
        "response_type": "code",
        "state": state,
    }
    return RedirectResponse(url=f"{INSTAGRAM_AUTH_URL}?{urlencode(params)}")


@router.get("/oauth/instagram/callback")
async def instagram_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    state_data = await consume_state(state)
    if state_data is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    role, mode = state_data

    client = await get_http_client()

    # Exchange code for short-lived token
    token_resp = await client.post(
        INSTAGRAM_TOKEN_URL,
        data={
            "client_id": settings.instagram_client_id,
            "client_secret": settings.instagram_client_secret,
            "grant_type": "authorization_code",
            "redirect_uri": settings.instagram_redirect_uri,
            "code": code,
        },
    )

    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Instagram token exchange failed: {token_resp.text}")

    short_lived_token: str = token_resp.json()["access_token"]

    # Exchange for long-lived token (60 days)
    ll_resp = await client.get(
        INSTAGRAM_LONG_LIVED_TOKEN_URL,
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.instagram_client_secret,
            "access_token": short_lived_token,
        },
    )

    long_lived_token = ll_resp.json().get("access_token", short_lived_token) if ll_resp.status_code == 200 else short_lived_token

    # Fetch Instagram profile
    profile_resp = await client.get(
        f"{INSTAGRAM_GRAPH_URL}/me",
        params={
            "fields": "id,username,name,biography,followers_count,media_count,profile_picture_url,website",
            "access_token": long_lived_token,
        },
    )

    if profile_resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Failed to fetch Instagram profile: {profile_resp.text}")

    ig = profile_resp.json()
    instagram_id: str = ig["id"]
    username: str = ig.get("username", "")

    # Find existing user
    result = await db.execute(select(User).where(User.instagram_id == instagram_id))
    user = result.scalar_one_or_none()

    if mode == "login":
        if not user:
            error_params = urlencode({"error": "account_not_found"})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")
    else:
        # signup mode
        if not user:
            user = User(
                email=f"{instagram_id}@instagram.credfluence.internal",
                instagram_id=instagram_id,
                role="creator",
                is_verified=True,
                email_verified_at=datetime.now(UTC),
            )
            db.add(user)
            await db.flush()

            profile = Profile(
                user_id=user.id,
                display_name=ig.get("name") or username,
                handle=username,
                bio=ig.get("biography"),
                avatar_url=ig.get("profile_picture_url"),
                profile_type="creator",
                is_claimed=True,
                access_level="full",
            )
            db.add(profile)

    # Update avatar for existing user
    if user:
        prof_result = await db.execute(select(Profile).where(Profile.user_id == user.id))
        prof = prof_result.scalar_one_or_none()
        if prof and ig.get("profile_picture_url"):
            prof.avatar_url = ig["profile_picture_url"]

    # Upsert Instagram social account
    await _get_or_create_social_account(
        db=db,
        user=user,
        platform="instagram",
        platform_account_id=instagram_id,
        username=username,
        display_name=ig.get("name") or username,
        avatar_url=ig.get("profile_picture_url"),
        access_token=long_lived_token,
        refresh_token=None,
        stats={
            "followers_count": ig.get("followers_count", 0),
            "media_count": ig.get("media_count", 0),
            "biography": ig.get("biography"),
        },
    )

    await db.commit()
    await db.refresh(user)

    jwt_access = create_access_token({"sub": str(user.id), "role": user.role})
    jwt_refresh = create_refresh_token({"sub": str(user.id)})
    return _build_redirect(settings.frontend_url, jwt_access, jwt_refresh, user)


# ---------------------------------------------------------------------------
# Google OAuth + YouTube verification
# ---------------------------------------------------------------------------

@router.get("/oauth/google")
async def google_login(
    role: str = Query(default="creator"),
    mode: str = Query(default="signup"),
) -> RedirectResponse:
    if role not in ("creator", "agency", "brand"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if mode not in ("signup", "login"):
        raise HTTPException(status_code=400, detail="mode must be signup or login")

    state = secrets.token_urlsafe(32)
    await save_state(state, role, mode)

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/oauth/google/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    state_data = await consume_state(state)
    if state_data is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    role, mode = state_data

    client = await get_http_client()

    # Exchange code for tokens
    token_resp = await client.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
    )

    if token_resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Google token exchange failed: {token_resp.text}")

    token_data = token_resp.json()
    access_token: str = token_data["access_token"]
    refresh_token: str | None = token_data.get("refresh_token")

    # Fetch Google user info
    userinfo_resp = await client.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    if userinfo_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch Google user info")

    ginfo = userinfo_resp.json()
    google_id: str = ginfo["sub"]
    email: str = ginfo.get("email", "")
    name: str = ginfo.get("name", "")
    picture: str = ginfo.get("picture", "")
    hosted_domain: str | None = ginfo.get("hd")

    # Agency/Brand must use Google Workspace
    if role in ("agency", "brand") and not hosted_domain:
        error_params = urlencode({"error": "personal_account_blocked"})
        return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")

    # Fetch YouTube channel
    youtube_channel: dict | None = None
    yt_resp = await client.get(
        YOUTUBE_CHANNELS_URL,
        params={"part": "snippet,statistics", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    YOUTUBE_MIN_SUBSCRIBERS = 100
    YOUTUBE_MIN_VIDEOS = 5

    if yt_resp.status_code == 200:
        items = yt_resp.json().get("items", [])
        if items:
            ch = items[0]
            snippet = ch.get("snippet", {})
            stats = ch.get("statistics", {})
            subscribers = int(stats.get("subscriberCount", 0))
            video_count = int(stats.get("videoCount", 0))
            youtube_channel = {
                "channel_id": ch.get("id"),
                "channel_name": snippet.get("title"),
                "youtube_handle": snippet.get("customUrl"),
                "description": snippet.get("description"),
                "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url"),
                "subscribers": subscribers,
                "video_count": video_count,
                "total_views": int(stats.get("viewCount", 0)),
                # Whether channel meets minimum threshold for full access
                "meets_threshold": subscribers >= YOUTUBE_MIN_SUBSCRIBERS and video_count >= YOUTUBE_MIN_VIDEOS,
            }

    access_level = "limited"
    if role in ("agency", "brand"):
        access_level = "full"
    elif role == "creator" and youtube_channel and youtube_channel["meets_threshold"]:
        access_level = "full"

    # Find existing user
    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if not user and email:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.google_id = google_id

    if mode == "login":
        if not user:
            error_params = urlencode({"error": "account_not_found"})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")
        if user.role != role:
            error_params = urlencode({"error": "role_mismatch", "existing_role": user.role})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")
        # Update access_level + avatar on existing profile
        prof_result = await db.execute(select(Profile).where(Profile.user_id == user.id))
        prof = prof_result.scalar_one_or_none()
        if prof:
            prof.access_level = access_level
            if picture:
                prof.avatar_url = picture
    else:
        # signup mode
        if not user:
            user = User(
                email=email,
                google_id=google_id,
                role=role,
                is_verified=True,
                email_verified_at=datetime.now(UTC),
            )
            db.add(user)
            await db.flush()

            handle = youtube_channel["channel_name"].lower().replace(" ", "_") if youtube_channel else email.split("@")[0]
            profile = Profile(
                user_id=user.id,
                display_name=name or email.split("@")[0],
                handle=handle,
                avatar_url=picture,
                profile_type=role,
                access_level=access_level,
                is_claimed=True,
            )
            db.add(profile)
        elif user.role != role:
            error_params = urlencode({"error": "role_mismatch", "existing_role": user.role})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")
        else:
            # Update access_level + avatar on existing profile
            prof_result = await db.execute(select(Profile).where(Profile.user_id == user.id))
            prof = prof_result.scalar_one_or_none()
            if prof:
                prof.access_level = access_level
                if picture:
                    prof.avatar_url = picture

    # Upsert YouTube social account if channel found
    if youtube_channel:
        await _get_or_create_social_account(
            db=db,
            user=user,
            platform="youtube",
            platform_account_id=youtube_channel["channel_id"],
            username=youtube_channel.get("youtube_handle"),
            display_name=youtube_channel.get("channel_name"),
            avatar_url=youtube_channel.get("thumbnail"),
            access_token=access_token,
            refresh_token=refresh_token,
            stats={
                "subscribers": youtube_channel["subscribers"],
                "video_count": youtube_channel["video_count"],
                "total_views": youtube_channel["total_views"],
                "youtube_handle": youtube_channel.get("youtube_handle"),
                "description": youtube_channel.get("description"),
                "meets_threshold": youtube_channel["meets_threshold"],
                "threshold_requirements": {"min_subscribers": 100, "min_videos": 5},
            },
        )

    await db.commit()
    await db.refresh(user)

    jwt_access = create_access_token({"sub": str(user.id), "role": user.role})
    jwt_refresh = create_refresh_token({"sub": str(user.id)})
    return _build_redirect(settings.frontend_url, jwt_access, jwt_refresh, user, extra={
        "access_level": access_level,
        "youtube_connected": youtube_channel is not None,
    })
