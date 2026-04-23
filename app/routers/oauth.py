"""
OAuth 2.0 flows — post-refactor.

Auth (signup / login):
  Google only:  GET /auth/oauth/google?role=...&mode=signup|login
                GET /auth/oauth/google/callback

Connect flows (existing auth required — call connect-init as a normal API fetch,
then redirect browser to the returned auth_url):
  Instagram:    GET /auth/oauth/instagram/connect-init
                GET /auth/oauth/instagram/callback
  LinkedIn:     GET /auth/oauth/linkedin/connect-init
                GET /auth/oauth/linkedin/callback
  YouTube:      GET /auth/oauth/youtube/connect-init
                GET /auth/oauth/google/callback  (shared callback, mode=connect)

Connect callbacks redirect to:
  {frontend_url}/onboarding/connect-callback?platform=...&status=success|error[&error_detail=...]
"""

import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_delete, user_key
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.http_client import get_http_client
from app.core.oauth_state import consume_state, save_state
from app.core.security import create_access_token, create_refresh_token
from app.models.organization import Organization
from app.models.profile import Profile
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.org_service import resolve_org_for_signup

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

GOOGLE_AUTH_SCOPES = "openid email profile"
GOOGLE_CONNECT_SCOPES = " ".join([
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/youtube.readonly",
])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _connect_redirect(platform: str, status: str, error_detail: str | None = None) -> RedirectResponse:
    params: dict = {"platform": platform, "status": status}
    if error_detail:
        params["error_detail"] = error_detail
    return RedirectResponse(
        url=f"{settings.frontend_url}/onboarding/connect-callback?{urlencode(params)}"
    )


def _auth_redirect(jwt_access: str, jwt_refresh: str, user: User) -> RedirectResponse:
    from urllib.parse import urlencode
    params = urlencode({
        "access_token": jwt_access,
        "refresh_token": jwt_refresh,
        "user_id": str(user.id),
    })
    return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{params}")


async def _upsert_social_account(
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


async def _load_user_with_org(db: AsyncSession, user_id: str) -> User | None:
    result = await db.execute(
        select(User)
        .options(selectinload(User.organization), selectinload(User.memberships))
        .where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def _maybe_verify_creator(db: AsyncSession, user: User) -> None:
    """Auto-verify a creator's org when they connect a qualifying platform.

    Qualifies if:
    - Any connected YouTube channel has 500+ subscribers AND 5+ videos, OR
    - Any connected Instagram is a BUSINESS or MEDIA_CREATOR account.
    """
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


# ---------------------------------------------------------------------------
# Google — signup / login (no YouTube fetch)
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
        "scope": GOOGLE_AUTH_SCOPES,
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

    mode = state_data["mode"]
    role = state_data.get("role", "creator")
    connect_user_id = state_data.get("user_id")

    client = await get_http_client()

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
        if mode == "connect":
            return _connect_redirect("youtube", "error", "token_exchange_failed")
        raise HTTPException(status_code=400, detail="Google token exchange failed")

    token_data = token_resp.json()
    access_token: str = token_data["access_token"]
    refresh_token: str | None = token_data.get("refresh_token")

    userinfo_resp = await client.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if userinfo_resp.status_code != 200:
        if mode == "connect":
            return _connect_redirect("youtube", "error", "userinfo_fetch_failed")
        raise HTTPException(status_code=400, detail="Failed to fetch Google user info")

    ginfo = userinfo_resp.json()
    google_id: str = ginfo["sub"]
    email: str = ginfo.get("email", "")
    name: str = ginfo.get("name", "")
    picture: str = ginfo.get("picture", "")
    hosted_domain: str | None = ginfo.get("hd")

    # --------------- connect mode: link YouTube to existing user ---------------
    if mode == "connect":
        if not connect_user_id:
            return _connect_redirect("youtube", "error", "missing_user_id")

        user = await _load_user_with_org(db, connect_user_id)
        if not user:
            return _connect_redirect("youtube", "error", "user_not_found")

        yt_resp = await client.get(
            YOUTUBE_CHANNELS_URL,
            params={"part": "snippet,statistics", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if yt_resp.status_code != 200 or not yt_resp.json().get("items"):
            return _connect_redirect("youtube", "error", "no_youtube_channel")

        ch = yt_resp.json()["items"][0]
        snippet = ch.get("snippet", {})
        stats = ch.get("statistics", {})
        subscribers = int(stats.get("subscriberCount", 0))
        video_count = int(stats.get("videoCount", 0))

        await _upsert_social_account(
            db=db,
            user=user,
            platform="youtube",
            platform_account_id=ch["id"],
            username=snippet.get("customUrl"),
            display_name=snippet.get("title"),
            avatar_url=snippet.get("thumbnails", {}).get("default", {}).get("url"),
            access_token=access_token,
            refresh_token=refresh_token,
            stats={
                "subscribers": subscribers,
                "video_count": video_count,
                "total_views": int(stats.get("viewCount", 0)),
                "youtube_handle": snippet.get("customUrl"),
                "description": snippet.get("description"),
                "meets_threshold": subscribers >= 500 and video_count >= 5,
                "threshold_requirements": {"min_subscribers": 500, "min_videos": 5},
            },
        )
        await _maybe_verify_creator(db, user)
        await db.commit()
        await cache_delete(user_key(connect_user_id))
        return _connect_redirect("youtube", "success")

    # --------------- signup / login mode ---------------

    # Agency/Brand must use Google Workspace
    if role in ("agency", "brand") and not hosted_domain:
        error_params = urlencode({"error": "personal_account_blocked"})
        return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")

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
        # Update avatar on org profile
        if picture and user.organization_id:
            prof_result = await db.execute(
                select(Profile).where(Profile.organization_id == user.organization_id)
            )
            prof = prof_result.scalar_one_or_none()
            if prof:
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
            await resolve_org_for_signup(db, user, display_name=name or email.split("@")[0])
        elif user.role != role:
            error_params = urlencode({"error": "role_mismatch", "existing_role": user.role})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")
        else:
            # Update avatar on existing org profile
            if picture and user.organization_id:
                prof_result = await db.execute(
                    select(Profile).where(Profile.organization_id == user.organization_id)
                )
                prof = prof_result.scalar_one_or_none()
                if prof:
                    prof.avatar_url = picture

    await db.commit()
    await db.refresh(user)

    jwt_access = create_access_token({"sub": str(user.id), "role": user.role})
    jwt_refresh = create_refresh_token({"sub": str(user.id)})
    return _auth_redirect(jwt_access, jwt_refresh, user)


# ---------------------------------------------------------------------------
# DELETE /oauth/{platform}/disconnect — remove a connected social account
# ---------------------------------------------------------------------------

@router.delete("/oauth/{platform}/disconnect", response_model=dict)
async def disconnect_platform(
    platform: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if platform not in ("instagram", "linkedin", "youtube"):
        raise HTTPException(status_code=400, detail="Invalid platform")

    user_id = current_user["id"]

    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.user_id == user_id,
            SocialAccount.platform == platform,
        )
    )
    accounts = result.scalars().all()

    if not accounts:
        raise HTTPException(status_code=404, detail="Platform not connected")

    for account in accounts:
        await db.delete(account)

    await db.commit()
    await cache_delete(user_key(user_id))

    return {"success": True, "message": f"{platform} disconnected.", "data": {}}


# ---------------------------------------------------------------------------
# YouTube connect-init (auth required)
# ---------------------------------------------------------------------------

@router.get("/oauth/youtube/connect-init", response_model=dict)
async def youtube_connect_init(
    current_user: dict = Depends(get_current_user),
) -> dict:
    state = secrets.token_urlsafe(32)
    await save_state(state, current_user["role"], "connect", user_id=current_user["id"])

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_CONNECT_SCOPES,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    return {
        "success": True,
        "message": "OK",
        "data": {"auth_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"},
    }


# ---------------------------------------------------------------------------
# Instagram connect-init + callback (connect only)
# ---------------------------------------------------------------------------

@router.get("/oauth/instagram/connect-init", response_model=dict)
async def instagram_connect_init(
    current_user: dict = Depends(get_current_user),
) -> dict:
    state = secrets.token_urlsafe(32)
    await save_state(state, current_user["role"], "connect", user_id=current_user["id"])

    params = {
        "client_id": settings.instagram_client_id,
        "redirect_uri": settings.instagram_redirect_uri,
        "scope": "instagram_business_basic",
        "response_type": "code",
        "state": state,
    }
    return {
        "success": True,
        "message": "OK",
        "data": {"auth_url": f"{INSTAGRAM_AUTH_URL}?{urlencode(params)}"},
    }


@router.get("/oauth/instagram/callback")
async def instagram_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    state_data = await consume_state(state)
    if state_data is None or state_data.get("mode") != "connect":
        return _connect_redirect("instagram", "error", "invalid_state")

    connect_user_id = state_data.get("user_id")
    if not connect_user_id:
        return _connect_redirect("instagram", "error", "missing_user_id")

    user = await _load_user_with_org(db, connect_user_id)
    if not user:
        return _connect_redirect("instagram", "error", "user_not_found")

    client = await get_http_client()

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
        return _connect_redirect("instagram", "error", "token_exchange_failed")

    short_lived_token: str = token_resp.json()["access_token"]

    ll_resp = await client.get(
        INSTAGRAM_LONG_LIVED_TOKEN_URL,
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.instagram_client_secret,
            "access_token": short_lived_token,
        },
    )
    long_lived_token = (
        ll_resp.json().get("access_token", short_lived_token)
        if ll_resp.status_code == 200
        else short_lived_token
    )

    profile_resp = await client.get(
        f"{INSTAGRAM_GRAPH_URL}/me",
        params={
            "fields": "id,username,name,biography,followers_count,media_count,profile_picture_url,website,account_type",
            "access_token": long_lived_token,
        },
    )
    if profile_resp.status_code != 200:
        return _connect_redirect("instagram", "error", "profile_fetch_failed")

    ig = profile_resp.json()
    account_type: str = ig.get("account_type", "PERSONAL")

    if account_type == "PERSONAL":
        return _connect_redirect("instagram", "error", "personal_account_not_supported")

    await _upsert_social_account(
        db=db,
        user=user,
        platform="instagram",
        platform_account_id=ig["id"],
        username=ig.get("username"),
        display_name=ig.get("name") or ig.get("username"),
        avatar_url=ig.get("profile_picture_url"),
        access_token=long_lived_token,
        refresh_token=None,
        stats={
            "followers_count": ig.get("followers_count", 0),
            "media_count": ig.get("media_count", 0),
            "biography": ig.get("biography"),
            "account_type": account_type,
        },
    )

    await _maybe_verify_creator(db, user)
    await db.commit()
    await cache_delete(user_key(connect_user_id))
    return _connect_redirect("instagram", "success")


# ---------------------------------------------------------------------------
# LinkedIn connect-init + callback (connect only)
# ---------------------------------------------------------------------------

@router.get("/oauth/linkedin/connect-init", response_model=dict)
async def linkedin_connect_init(
    current_user: dict = Depends(get_current_user),
) -> dict:
    state = secrets.token_urlsafe(32)
    await save_state(state, current_user["role"], "connect", user_id=current_user["id"])

    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "state": state,
        "scope": "openid profile email",
    }
    return {
        "success": True,
        "message": "OK",
        "data": {"auth_url": f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"},
    }


@router.get("/oauth/linkedin/callback")
async def linkedin_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    state_data = await consume_state(state)
    if state_data is None or state_data.get("mode") != "connect":
        return _connect_redirect("linkedin", "error", "invalid_state")

    connect_user_id = state_data.get("user_id")
    if not connect_user_id:
        return _connect_redirect("linkedin", "error", "missing_user_id")

    user = await _load_user_with_org(db, connect_user_id)
    if not user:
        return _connect_redirect("linkedin", "error", "user_not_found")

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
        return _connect_redirect("linkedin", "error", "token_exchange_failed")

    access_token: str = token_resp.json()["access_token"]

    userinfo_resp = await client.get(
        LINKEDIN_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if userinfo_resp.status_code != 200:
        return _connect_redirect("linkedin", "error", "userinfo_fetch_failed")

    li = userinfo_resp.json()
    linkedin_id: str = li["sub"]
    email: str | None = li.get("email")

    await _upsert_social_account(
        db=db,
        user=user,
        platform="linkedin",
        platform_account_id=linkedin_id,
        username=None,
        display_name=li.get("name"),
        avatar_url=li.get("picture"),
        access_token=access_token,
        refresh_token=None,
        stats={"email": email, "name": li.get("name")},
    )

    await db.commit()
    await cache_delete(user_key(connect_user_id))
    return _connect_redirect("linkedin", "success")
