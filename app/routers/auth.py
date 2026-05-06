from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, invalidate_user, user_key
from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.email import send_account_deletion_email, send_otp_email, send_password_reset_email
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    decrypt_phone,
    encrypt_phone,
    hash_password,
    verify_password,
)
from app.models.profile import Profile
from app.models.user import User
from app.repositories.user_repo import get_user_by_email, get_user_with_org_and_memberships
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UpdateMePayload,
    VerifyEmailRequest,
)
from app.services.org_service import resolve_org_for_signup
from app.services.otp_service import (
    delete_otp,
    delete_pending_signup,
    delete_pwd_reset_otp,
    generate_otp,
    get_pending_signup,
    store_otp,
    store_pending_signup,
    store_pwd_reset_otp,
    verify_otp,
    verify_pwd_reset_otp,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _get_access_level(db: AsyncSession, user: User, org) -> str:
    if user.role != "creator" or not org:
        return "full"
    result = await db.execute(
        select(Profile.access_level).where(Profile.organization_id == org.id)
    )
    level = result.scalar_one_or_none()
    return level if level else "limited"


def _user_dict(user: User, org=None, membership=None, access_level: str = "limited") -> dict:
    phone = None
    if user.phone_encrypted:
        try:
            phone = decrypt_phone(user.phone_encrypted)
        except Exception:
            pass
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "phone": phone,
        "role": user.role,
        "is_verified": user.is_verified,
        "subscription_tier": user.subscription_tier,
        "onboarding_completed_at": (
            user.onboarding_completed_at.isoformat()
            if user.onboarding_completed_at else None
        ),
        "access_level": access_level,
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
        if user.deleted_at and (datetime.now(UTC) - user.deleted_at).days <= 30:
            # Reactivate — user logged in within the 30-day grace window
            user.is_active = True
            user.deleted_at = None
            await db.commit()
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is deactivated")

    if not user.is_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Please verify your email before logging in")

    user = await get_user_with_org_and_memberships(db, user.id)
    org = user.organization
    membership = next((m for m in user.memberships if m.organization_id == org.id), None) if org else None
    access_level = await _get_access_level(db, user, org)

    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id)})

    return {
        "success": True,
        "message": "Login successful",
        "data": {
            "user": _user_dict(user, org, membership, access_level),
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
    access_level = await _get_access_level(db, user, org)

    return {
        "success": True,
        "message": "Token refreshed",
        "data": {
            "access_token": create_access_token({"sub": str(user.id), "role": user.role}),
            "token_type": "bearer",
            "expires_in": 1800,
            "user": _user_dict(user, org, membership, access_level),
        },
    }


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
    access_level = await _get_access_level(db, user, org)
    return {"success": True, "message": "OK", "data": _user_dict(user, org, membership, access_level)}


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

    if payload.phone is not None:
        stripped = payload.phone.strip()
        user.phone_encrypted = encrypt_phone(stripped) if stripped else None

    await db.commit()
    await cache_delete(user_key(str(user.id)))

    org = user.organization
    membership = next((m for m in user.memberships if m.organization_id == org.id), None) if org else None
    access_level = await _get_access_level(db, user, org)
    return {"success": True, "message": "Profile updated.", "data": {"user": _user_dict(user, org, membership, access_level)}}


# ---------------------------------------------------------------------------
# POST /auth/forgot-password
# ---------------------------------------------------------------------------

@router.post("/forgot-password", response_model=dict)
async def forgot_password(payload: ForgotPasswordRequest) -> dict:
    otp = generate_otp()
    await store_pwd_reset_otp(payload.email, otp)
    await send_password_reset_email(payload.email, otp)
    # Always return success — don't reveal whether email exists
    return {"success": True, "message": "If that email is registered, a reset code has been sent."}


# ---------------------------------------------------------------------------
# POST /auth/reset-password
# ---------------------------------------------------------------------------

@router.post("/reset-password", response_model=dict)
async def reset_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)) -> dict:
    if not await verify_pwd_reset_otp(payload.email, payload.otp):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset code.")

    user = await get_user_by_email(db, payload.email)
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset code.")

    if len(payload.new_password) < 8:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Password must be at least 8 characters.")

    user.hashed_password = await hash_password(payload.new_password)
    await db.commit()
    await delete_pwd_reset_otp(payload.email)
    await invalidate_user(str(user.id))

    return {"success": True, "message": "Password updated. You can now log in."}


# ---------------------------------------------------------------------------
# DELETE /auth/me  — soft-delete (30-day grace period)
# ---------------------------------------------------------------------------

@router.delete("/me", response_model=dict)
async def delete_account(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(User).where(User.id == current_user["id"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_active = False
    user.deleted_at = datetime.now(UTC)
    await db.commit()
    await invalidate_user(str(user.id))

    await send_account_deletion_email(user.email, days_remaining=30)

    return {"success": True, "message": "Account scheduled for deletion. Log in within 30 days to restore it."}
