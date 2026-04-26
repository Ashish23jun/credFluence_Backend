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
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, user_key
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.http_client import get_http_client
from app.core.oauth_state import consume_state, save_state
from app.core.security import create_access_token, create_refresh_token
from app.models.profile import Profile
from app.models.user import User
from app.repositories.social_account_repo import get_accounts_by_user_id, upsert_social_account
from app.repositories.user_repo import get_user_by_email, get_user_by_google_id, get_user_with_org
from app.services.org_service import resolve_org_for_signup
from app.services.oauth_service import (
    auth_redirect,
    build_instagram_stats,
    build_youtube_stats,
    connect_redirect,
    maybe_verify_creator,
)

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
# Google — signup / login
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
            return connect_redirect("youtube", "error", "token_exchange_failed")
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
            return connect_redirect("youtube", "error", "userinfo_fetch_failed")
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
            return connect_redirect("youtube", "error", "missing_user_id")

        user = await get_user_with_org(db, connect_user_id)
        if not user:
            return connect_redirect("youtube", "error", "user_not_found")

        yt_resp = await client.get(
            YOUTUBE_CHANNELS_URL,
            params={"part": "snippet,statistics", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if yt_resp.status_code != 200 or not yt_resp.json().get("items"):
            return connect_redirect("youtube", "error", "no_youtube_channel")

        ch = yt_resp.json()["items"][0]
        await upsert_social_account(
            db=db,
            user=user,
            platform="youtube",
            platform_account_id=ch["id"],
            username=ch.get("snippet", {}).get("customUrl"),
            display_name=ch.get("snippet", {}).get("title"),
            avatar_url=ch.get("snippet", {}).get("thumbnails", {}).get("default", {}).get("url"),
            access_token=access_token,
            refresh_token=refresh_token,
            stats=build_youtube_stats(ch),
        )
        await maybe_verify_creator(db, user)
        await db.commit()
        await cache_delete(user_key(connect_user_id))
        return connect_redirect("youtube", "success")

    # --------------- signup / login mode ---------------
    if role in ("agency", "brand") and not hosted_domain:
        error_params = urlencode({"error": "personal_account_blocked"})
        return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")

    user = await get_user_by_google_id(db, google_id)

    if not user and email:
        user = await get_user_by_email(db, email)
        if user:
            user.google_id = google_id

    if mode == "login":
        if not user:
            error_params = urlencode({"error": "account_not_found"})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")
        if picture and user.organization_id:
            prof_result = await db.execute(
                select(Profile).where(Profile.organization_id == user.organization_id)
            )
            prof = prof_result.scalar_one_or_none()
            if prof:
                prof.avatar_url = picture
    else:
        if not user:
            user = User(
                email=email,
                google_id=google_id,
                role=role,
                is_verified=True,
            )
            await resolve_org_for_signup(db, user, display_name=name or email.split("@")[0])
        elif user.role != role:
            error_params = urlencode({"error": "role_mismatch", "existing_role": user.role})
            return RedirectResponse(url=f"{settings.frontend_url}/auth/callback?{error_params}")
        else:
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
    return auth_redirect(jwt_access, jwt_refresh, user)


# ---------------------------------------------------------------------------
# DELETE /oauth/{platform}/disconnect
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
    accounts = await get_accounts_by_user_id(db, user_id)
    platform_accounts = [a for a in accounts if a.platform == platform]

    if not platform_accounts:
        raise HTTPException(status_code=404, detail="Platform not connected")

    for account in platform_accounts:
        await db.delete(account)

    await db.commit()
    await cache_delete(user_key(user_id))

    return {"success": True, "message": f"{platform} disconnected.", "data": {}}


# ---------------------------------------------------------------------------
# YouTube connect-init
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
# Instagram connect-init + callback
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
        return connect_redirect("instagram", "error", "invalid_state")

    connect_user_id = state_data.get("user_id")
    if not connect_user_id:
        return connect_redirect("instagram", "error", "missing_user_id")

    user = await get_user_with_org(db, connect_user_id)
    if not user:
        return connect_redirect("instagram", "error", "user_not_found")

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
        return connect_redirect("instagram", "error", "token_exchange_failed")

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
        return connect_redirect("instagram", "error", "profile_fetch_failed")

    ig = profile_resp.json()
    if ig.get("account_type", "PERSONAL") == "PERSONAL":
        return connect_redirect("instagram", "error", "personal_account_not_supported")

    await upsert_social_account(
        db=db,
        user=user,
        platform="instagram",
        platform_account_id=ig["id"],
        username=ig.get("username"),
        display_name=ig.get("name") or ig.get("username"),
        avatar_url=ig.get("profile_picture_url"),
        access_token=long_lived_token,
        refresh_token=None,
        stats=build_instagram_stats(ig),
    )

    await maybe_verify_creator(db, user)
    await db.commit()
    await cache_delete(user_key(connect_user_id))
    return connect_redirect("instagram", "success")


# ---------------------------------------------------------------------------
# LinkedIn connect-init + callback
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
        return connect_redirect("linkedin", "error", "invalid_state")

    connect_user_id = state_data.get("user_id")
    if not connect_user_id:
        return connect_redirect("linkedin", "error", "missing_user_id")

    user = await get_user_with_org(db, connect_user_id)
    if not user:
        return connect_redirect("linkedin", "error", "user_not_found")

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
        return connect_redirect("linkedin", "error", "token_exchange_failed")

    access_token: str = token_resp.json()["access_token"]

    userinfo_resp = await client.get(
        LINKEDIN_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if userinfo_resp.status_code != 200:
        return connect_redirect("linkedin", "error", "userinfo_fetch_failed")

    li = userinfo_resp.json()

    await upsert_social_account(
        db=db,
        user=user,
        platform="linkedin",
        platform_account_id=li["sub"],
        username=None,
        display_name=li.get("name"),
        avatar_url=li.get("picture"),
        access_token=access_token,
        refresh_token=None,
        stats={"email": li.get("email"), "name": li.get("name")},
    )

    await db.commit()
    await cache_delete(user_key(connect_user_id))
    return connect_redirect("linkedin", "success")


# ---------------------------------------------------------------------------
# GET /oauth/social-accounts — current user's connected platforms
# ---------------------------------------------------------------------------

@router.get("/oauth/social-accounts", response_model=dict)
async def get_social_accounts(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    accounts = await get_accounts_by_user_id(db, current_user["id"])

    return {
        "success": True,
        "message": "OK",
        "data": {
            "social_accounts": [
                {
                    "platform": sa.platform,
                    "username": sa.username,
                    "display_name": sa.display_name,
                    "avatar_url": sa.avatar_url,
                    "is_primary": sa.is_primary,
                    "connected_at": sa.connected_at.isoformat() if sa.connected_at else None,
                    "stats": sa.stats,
                }
                for sa in accounts
            ]
        },
    }
