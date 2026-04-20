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
"""

import json
import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, create_refresh_token
from app.models.profile import Profile
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

# Scopes: identity + YouTube read-only
GOOGLE_SCOPES = " ".join([
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/youtube.readonly",
])

# In-memory state store (fine for single-instance dev; use Redis in prod)
_pending_states: dict[str, str] = {}


@router.get("/linkedin")
async def linkedin_login(role: str = Query(default="creator")) -> RedirectResponse:
    """Redirect user to LinkedIn authorization page."""
    if role not in ("creator", "agency", "brand"):
        raise HTTPException(status_code=400, detail="Invalid role")

    state = secrets.token_urlsafe(32)
    _pending_states[state] = role  # remember role so callback can use it

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
) -> dict:
    """Handle LinkedIn callback — exchange code for tokens and return JWT."""

    # Validate state
    role = _pending_states.pop(state, None)
    if role is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    # Exchange code for access token
    async with httpx.AsyncClient() as client:
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

    # Fetch user info via OpenID Connect userinfo endpoint
    async with httpx.AsyncClient() as client:
        userinfo_resp = await client.get(
            LINKEDIN_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if userinfo_resp.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch LinkedIn user info")

    userinfo = userinfo_resp.json()
    linkedin_id: str = userinfo.get("sub")  # OpenID Connect subject = LinkedIn member ID
    email: str | None = userinfo.get("email")

    if not linkedin_id:
        raise HTTPException(status_code=400, detail="LinkedIn did not return a user ID")

    # --- Find or create user ---
    role_mismatch = False

    # 1. Try by linkedin_id
    result = await db.execute(select(User).where(User.linkedin_id == linkedin_id))
    user = result.scalar_one_or_none()

    if not user and email:
        # 2. Try by email (account exists but not yet linked)
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.linkedin_id = linkedin_id  # link the account

    if not user:
        # 3. Create new user
        if not email:
            raise HTTPException(
                status_code=400,
                detail="LinkedIn did not provide an email. Enable the 'email' scope.",
            )
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
            handle=None,  # LinkedIn users have no handle — set when they fill profile
            avatar_url=userinfo.get("picture"),
            profile_type=role,
            is_claimed=True,
            access_level="full",
        )
        db.add(profile)
    else:
        # Existing user — block login if selected role doesn't match stored role
        if user.role != role:
            error_params = urlencode({
                "error": "role_mismatch",
                "existing_role": user.role,
                "selected_role": role,
            })
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")

    await db.commit()
    await db.refresh(user)

    # Issue JWT tokens
    jwt_access = create_access_token({"sub": str(user.id), "role": user.role})
    jwt_refresh = create_refresh_token({"sub": str(user.id)})

    user_json = json.dumps({
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "is_verified": user.is_verified,
        "subscription_tier": user.subscription_tier,
    })
    params = urlencode({
        "access_token": jwt_access,
        "refresh_token": jwt_refresh,
        "user": user_json,
    })
    return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{params}")


# ---------------------------------------------------------------------------
# Instagram OAuth (creators only)
# ---------------------------------------------------------------------------

@router.get("/oauth/instagram")
async def instagram_login(role: str = Query(default="creator")) -> RedirectResponse:
    """Redirect creator to Instagram authorization page."""
    if role != "creator":
        raise HTTPException(status_code=400, detail="Instagram OAuth is only available for creators")

    state = secrets.token_urlsafe(32)
    _pending_states[state] = role

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
    """Handle Instagram callback — exchange code, fetch profile, return JWT."""

    role = _pending_states.pop(state, None)
    if role is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    # Step 1: exchange code for short-lived token (1 hr)
    async with httpx.AsyncClient() as client:
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

    token_data = token_resp.json()
    short_lived_token: str = token_data["access_token"]

    # Step 2: exchange for long-lived token (60 days)
    async with httpx.AsyncClient() as client:
        ll_resp = await client.get(
            INSTAGRAM_LONG_LIVED_TOKEN_URL,
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.instagram_client_secret,
                "access_token": short_lived_token,
            },
        )

    long_lived_token = ll_resp.json().get("access_token", short_lived_token) if ll_resp.status_code == 200 else short_lived_token

    # Step 3: fetch Instagram profile
    async with httpx.AsyncClient() as client:
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

    # Step 4: find or create user
    result = await db.execute(select(User).where(User.instagram_id == instagram_id))
    user = result.scalar_one_or_none()

    instagram_stats = {
        "followers_count": ig.get("followers_count", 0),
        "media_count": ig.get("media_count", 0),
        "username": username,
        "profile_picture_url": ig.get("profile_picture_url"),
        "access_token": long_lived_token,
        "token_fetched_at": datetime.now(UTC).isoformat(),
    }

    if not user:
        # New user — create account + profile
        user = User(
            email=f"{instagram_id}@instagram.credfluence.internal",
            instagram_id=instagram_id,
            role="creator",
            is_verified=True,
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        await db.flush()  # get user.id before creating profile

        profile = Profile(
            user_id=user.id,
            display_name=ig.get("name") or username,
            handle=username,
            bio=ig.get("biography"),
            avatar_url=ig.get("profile_picture_url"),
            profile_type="creator",
            is_claimed=True,
            social_stats={"instagram": instagram_stats},
        )
        db.add(profile)
    else:
        # Existing user — block if role mismatch
        if user.role != "creator":
            error_params = urlencode({"error": "role_mismatch", "existing_role": user.role})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")

        # Refresh instagram stats + token on profile
        result = await db.execute(select(Profile).where(Profile.user_id == user.id))
        profile = result.scalar_one_or_none()
        if profile:
            existing = profile.social_stats or {}
            existing["instagram"] = instagram_stats
            profile.social_stats = existing
            profile.instagram_handle = username
            if ig.get("profile_picture_url"):
                profile.avatar_url = ig["profile_picture_url"]

    await db.commit()
    await db.refresh(user)

    # Issue JWT tokens
    jwt_access = create_access_token({"sub": str(user.id), "role": user.role})
    jwt_refresh = create_refresh_token({"sub": str(user.id)})

    user_json = json.dumps({
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "is_verified": user.is_verified,
        "subscription_tier": user.subscription_tier,
    })
    redirect_params = urlencode({
        "access_token": jwt_access,
        "refresh_token": jwt_refresh,
        "user": user_json,
    })
    return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{redirect_params}")


# ---------------------------------------------------------------------------
# Google OAuth + YouTube verification
# ---------------------------------------------------------------------------

@router.get("/oauth/google")
async def google_login(role: str = Query(default="creator")) -> RedirectResponse:
    """Redirect user to Google consent screen."""
    if role not in ("creator", "agency", "brand"):
        raise HTTPException(status_code=400, detail="Invalid role")

    state = secrets.token_urlsafe(32)
    _pending_states[state] = role

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "state": state,
        "access_type": "offline",   # get refresh token
        "prompt": "consent",        # always show consent to ensure refresh token issued
    }
    return RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/oauth/google/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Handle Google callback — verify identity, check YouTube, create user."""

    role = _pending_states.pop(state, None)
    if role is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    # Step 1: exchange code for tokens
    async with httpx.AsyncClient() as client:
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

    # Step 2: fetch Google user info
    async with httpx.AsyncClient() as client:
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
    hosted_domain: str | None = ginfo.get("hd")  # only present for Google Workspace accounts

    # Step 3: agency/brand must use Google Workspace (hosted domain)
    if role in ("agency", "brand") and not hosted_domain:
        error_params = urlencode({
            "error": "personal_account_blocked",
            "message": "Agencies and brands must use a Google Workspace business account, not a personal Gmail.",
        })
        return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")

    # Step 4: fetch YouTube channel (for all roles)
    youtube_stats: dict | None = None
    async with httpx.AsyncClient() as client:
        yt_resp = await client.get(
            YOUTUBE_CHANNELS_URL,
            params={
                "part": "snippet,statistics",
                "mine": "true",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if yt_resp.status_code == 200:
        yt_data = yt_resp.json()
        items = yt_data.get("items", [])
        if items:
            channel = items[0]
            snippet = channel.get("snippet", {})
            stats = channel.get("statistics", {})
            youtube_stats = {
                "channel_id": channel.get("id"),
                "channel_name": snippet.get("title"),
                "youtube_handle": snippet.get("customUrl"),  # e.g. @OwaisBolte
                "description": snippet.get("description"),
                "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url"),
                "subscribers": int(stats.get("subscriberCount", 0)),
                "video_count": int(stats.get("videoCount", 0)),
                "total_views": int(stats.get("viewCount", 0)),
                "verified_at": datetime.now(UTC).isoformat(),
            }

    # Creator with no YouTube → access_level stays limited
    # Creator with YouTube → access_level = full
    # Agency/Brand → access_level always full (passed hd check above)
    access_level = "limited"
    if role in ("agency", "brand"):
        access_level = "full"
    elif role == "creator" and youtube_stats:
        access_level = "full"

    # Step 5: find or create user
    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if not user and email:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.google_id = google_id  # link existing account

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

        # Build social_stats
        social_stats: dict = {}
        if youtube_stats:
            social_stats["youtube"] = youtube_stats

        google_handle = youtube_stats["channel_name"].lower().replace(" ", "_") if youtube_stats else email.split("@")[0]
        profile = Profile(
            user_id=user.id,
            display_name=name or email.split("@")[0],
            handle=google_handle,
            avatar_url=picture,
            profile_type=role,
            youtube_channel_id=youtube_stats["channel_id"] if youtube_stats else None,
            access_level=access_level,
            is_claimed=True,
            social_stats=social_stats if social_stats else None,
        )
        db.add(profile)
    else:
        # Existing user — block role mismatch
        if user.role != role:
            error_params = urlencode({
                "error": "role_mismatch",
                "existing_role": user.role,
                "selected_role": role,
            })
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")

        # Refresh YouTube stats + access_level on existing profile
        result = await db.execute(select(Profile).where(Profile.user_id == user.id))
        profile = result.scalar_one_or_none()
        if profile:
            if youtube_stats:
                existing = profile.social_stats or {}
                existing["youtube"] = youtube_stats
                profile.social_stats = existing
                profile.youtube_channel_id = youtube_stats["channel_id"]
            profile.access_level = access_level
            if picture:
                profile.avatar_url = picture

    await db.commit()
    await db.refresh(user)

    jwt_access = create_access_token({"sub": str(user.id), "role": user.role})
    jwt_refresh = create_refresh_token({"sub": str(user.id)})

    user_json = json.dumps({
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "is_verified": user.is_verified,
        "subscription_tier": user.subscription_tier,
        "access_level": access_level,
        "youtube_connected": youtube_stats is not None,
    })
    redirect_params = urlencode({
        "access_token": jwt_access,
        "refresh_token": jwt_refresh,
        "user": user_json,
    })
    return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{redirect_params}")
