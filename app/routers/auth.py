from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, user_key
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.email import send_otp_email
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.repositories.user_repo import get_user_by_email, get_user_with_org_and_memberships
from app.schemas.auth import LoginRequest, RefreshRequest, RegisterRequest, VerifyEmailRequest
from app.services.otp_service import (
    delete_otp,
    delete_pending_signup,
    generate_otp,
    get_pending_signup,
    store_otp,
    store_pending_signup,
    verify_otp,
)
from app.services.org_service import resolve_org_for_signup

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_dict(user: User, org=None, membership=None) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_verified": user.is_verified,
        "subscription_tier": user.subscription_tier,
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
            "membership_status": membership.status if membership else None,
            "membership_role": membership.role if membership else None,
        } if org else None,
    }


@router.post("/register", response_model=dict, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> dict:
    if await get_user_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    hashed = await hash_password(payload.password)
    await store_pending_signup(payload.email, hashed, payload.role)

    otp = generate_otp()
    await store_otp(payload.email, otp)
    await send_otp_email(payload.email, otp)

    return {
        "success": True,
        "message": "OTP sent. Please verify your email to complete registration.",
        "data": {"pending_verification": True, "email": payload.email},
    }


@router.post("/verify-email", response_model=dict)
async def verify_email(payload: VerifyEmailRequest, db: AsyncSession = Depends(get_db)) -> dict:
    if not await verify_otp(payload.email, payload.otp):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP. Please check your email and try again.",
        )

    pending = await get_pending_signup(payload.email)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signup session expired. Please register again.",
        )

    if await get_user_by_email(db, payload.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account already exists")

    user = User(
        email=pending["email"],
        hashed_password=pending["hashed_password"],
        role=pending["role"],
        is_verified=True,
        email_verified_at=datetime.now(UTC),
    )
    org, membership = await resolve_org_for_signup(db, user, display_name=pending["email"].split("@")[0])

    await db.commit()
    await db.refresh(user)
    await delete_otp(payload.email)
    await delete_pending_signup(payload.email)

    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    return {
        "success": True,
        "message": "Email verified. Account created successfully.",
        "data": {
            "user": _user_dict(user, org, membership),
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": 1800,
        },
    }


@router.post("/resend-otp", response_model=dict)
async def resend_otp(email: str) -> dict:
    pending = await get_pending_signup(email)
    if not pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signup session expired. Please register again.",
        )

    otp = generate_otp()
    await store_otp(email, otp)
    await send_otp_email(email, otp)

    return {"success": True, "message": "OTP resent"}


@router.post("/login", response_model=dict)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> dict:
    user = await get_user_by_email(db, payload.email)

    if not user or not user.hashed_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if not await verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")

    if not user.is_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Please verify your email before logging in")

    user = await get_user_with_org_and_memberships(db, user.id)
    org = user.organization
    membership = next((m for m in user.memberships if m.organization_id == org.id), None) if org else None

    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    return {
        "success": True,
        "message": "Login successful",
        "data": {
            "user": _user_dict(user, org, membership),
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": 1800,
        },
    }


@router.post("/refresh", response_model=dict)
async def refresh_token(payload: RefreshRequest, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        token_data = decode_token(payload.refresh_token)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    if token_data.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user = await get_user_with_org_and_memberships(db, token_data.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or deactivated")

    org = user.organization
    membership = next((m for m in user.memberships if m.organization_id == org.id), None) if org else None

    return {
        "success": True,
        "message": "Token refreshed",
        "data": {
            "access_token": create_access_token({"sub": str(user.id), "role": user.role}),
            "token_type": "bearer",
            "expires_in": 1800,
            "user": _user_dict(user, org, membership),
        },
    }


class UpdateMePayload(BaseModel):
    full_name: str | None = None


@router.get("/me", response_model=dict)
async def get_me(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await get_user_with_org_and_memberships(db, current_user["id"])
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    org = user.organization
    membership = next((m for m in user.memberships if m.organization_id == org.id), None) if org else None
    return {"success": True, "message": "OK", "data": _user_dict(user, org, membership)}


@router.patch("/me", response_model=dict)
async def update_me(
    payload: UpdateMePayload,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await get_user_with_org_and_memberships(db, current_user["id"])
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.full_name is not None:
        user.full_name = payload.full_name.strip() or None

    await db.commit()
    await cache_delete(user_key(str(user.id)))

    org = user.organization
    membership = next((m for m in user.memberships if m.organization_id == org.id), None) if org else None
    return {"success": True, "message": "Profile updated.", "data": _user_dict(user, org, membership)}
