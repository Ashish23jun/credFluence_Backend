from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_get, cache_set, user_key
from app.core.database import get_db
from app.core.security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)
admin_bearer_scheme = HTTPBearer(auto_error=False)

USER_CACHE_TTL = 300  # 5 minutes


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = payload.get("sub")

    # Try Redis cache first
    cached = await cache_get(user_key(user_id))
    if cached:
        if not cached.get("is_active", True):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")
        return cached

    # Cache miss — fetch from DB with org + membership
    from app.models.user import User

    result = await db.execute(
        select(User)
        .options(selectinload(User.organization), selectinload(User.memberships))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")

    org = user.organization
    membership = next(
        (m for m in user.memberships if m.organization_id == org.id), None
    ) if org else None

    user_dict = {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "is_verified": user.is_verified,
        "subscription_tier": user.subscription_tier,
        "trust_weight": user.trust_weight,
        "onboarding_completed_at": (
            user.onboarding_completed_at.isoformat()
            if user.onboarding_completed_at else None
        ),
        "org": {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "org_type": org.org_type,
            "verification_status": org.verification_status,
            "is_personal_creator_org": org.is_personal_creator_org,
            "membership_role": membership.role if membership else None,
            "membership_status": membership.status if membership else None,
        } if org else None,
    }
    await cache_set(user_key(str(user.id)), user_dict, USER_CACHE_TTL)
    return user_dict


async def require_onboarded(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Blocks access if the user has not completed onboarding."""
    if not current_user.get("onboarding_completed_at"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please complete onboarding before accessing this resource.",
            headers={"X-Redirect-To": "/onboarding"},
        )
    return current_user


async def require_verified(
    current_user: dict = Depends(require_onboarded),
) -> dict:
    """Blocks access if the user's org is not verified by an admin."""
    org = current_user.get("org")
    if not org or org.get("verification_status") != "verified":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending verification. You can only access your own details.",
            headers={"X-Redirect-To": "/pending-verification"},
        )
    return current_user


async def require_org_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Requires the caller to be an active admin of their org."""
    org = current_user.get("org")
    if (
        not org
        or org.get("membership_role") != "admin"
        or org.get("membership_status") != "active"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organisation admin access required.",
        )
    return current_user


async def require_business_access(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Agency or brand only."""
    if current_user["role"] not in ("agency", "brand"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Business account required",
        )
    return current_user


async def get_current_platform_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(admin_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.get("type") != "admin_access":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access required",
        )

    admin_id = payload.get("sub")

    from app.models.platform_admin import PlatformAdmin
    from sqlalchemy import select as sa_select

    result = await db.execute(
        sa_select(PlatformAdmin).where(PlatformAdmin.id == admin_id)
    )
    admin = result.scalar_one_or_none()

    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin not found",
        )
    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin account is deactivated",
        )

    return {"id": str(admin.id), "email": admin.email}
