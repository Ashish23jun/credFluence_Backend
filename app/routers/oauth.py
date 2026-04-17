"""
LinkedIn OAuth 2.0 with OpenID Connect.

Flow:
  1. Frontend opens  GET /auth/linkedin?role=creator
  2. Backend redirects to LinkedIn authorization page
  3. LinkedIn redirects back to GET /auth/linkedin/callback?code=...&state=...
  4. Backend exchanges code for tokens, fetches user info
  5. Backend creates or logs in the user, returns JWT
"""

import json
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, create_refresh_token
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["oauth"])

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

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
            is_verified=True,  # email verified by LinkedIn
        )
        db.add(user)
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
