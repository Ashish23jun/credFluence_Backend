from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_platform_admin
from app.core.security import (
    create_admin_access_token,
    create_admin_refresh_token,
    decode_token,
    verify_password,
)
from app.models.platform_admin import PlatformAdmin
from app.schemas.admin_auth import AdminLoginRequest, AdminRefreshRequest

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])


@router.post("/login", response_model=dict)
async def admin_login(
    payload: AdminLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(PlatformAdmin).where(PlatformAdmin.email == payload.email)
    )
    admin = result.scalar_one_or_none()

    if not admin or not await verify_password(payload.password, admin.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin account is deactivated",
        )

    admin_id = str(admin.id)
    return {
        "success": True,
        "message": "Login successful",
        "data": {
            "admin": {
                "id": admin_id,
                "email": admin.email,
            },
            "access_token": create_admin_access_token(admin_id),
            "refresh_token": create_admin_refresh_token(admin_id),
            "token_type": "bearer",
            "expires_in": 1800,
        },
    }


@router.post("/refresh", response_model=dict)
async def admin_refresh(
    payload: AdminRefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        token_data = decode_token(payload.refresh_token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if token_data.get("type") != "admin_refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    admin_id = token_data.get("sub")
    result = await db.execute(
        select(PlatformAdmin).where(PlatformAdmin.id == admin_id)
    )
    admin = result.scalar_one_or_none()

    if not admin or not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin not found or deactivated",
        )

    return {
        "success": True,
        "message": "Token refreshed",
        "data": {
            "access_token": create_admin_access_token(str(admin.id)),
            "token_type": "bearer",
            "expires_in": 1800,
        },
    }


@router.get("/me", response_model=dict)
async def admin_me(
    current_admin: dict = Depends(get_current_platform_admin),
) -> dict:
    return {
        "success": True,
        "message": "OK",
        "data": current_admin,
    }
