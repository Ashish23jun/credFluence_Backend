"""
Onboarding router — auth required, no verification gate.

Endpoints are reachable by any authenticated user regardless of
onboarding_completed_at or org.verification_status.
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_delete, user_key
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_org_admin
from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.profile import Profile
from app.models.user import User
from app.services.org_service import approve_membership, reject_membership

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

_ALLOWED_MIME = {"image/jpeg", "image/png", "application/pdf"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class OrgUpdatePayload(BaseModel):
    name: str | None = None
    bio: str | None = None
    category: str | None = None
    location: str | None = None
    avatar_url: str | None = None


_GST_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


class VerificationDocsPayload(BaseModel):
    website: str
    gst_number: str | None = None
    gst_file_url: str | None = None
    cin_number: str | None = None
    cin_file_url: str | None = None
    trademark_file_url: str | None = None

    @field_validator("gst_number")
    @classmethod
    def validate_gst(cls, v: str | None) -> str | None:
        if v and not _GST_RE.match(v.strip().upper()):
            raise ValueError("Invalid GST number format (e.g. 27ABCDE1234F1Z5)")
        return v.strip().upper() if v else v

    @model_validator(mode="after")
    def gst_required(self) -> "VerificationDocsPayload":
        if not self.gst_number and not self.gst_file_url:
            raise ValueError("GST number or GST certificate is required")
        return self




# ---------------------------------------------------------------------------
# GET /onboarding/me — full onboarding context
# ---------------------------------------------------------------------------

@router.get("/me", response_model=dict)
async def onboarding_me(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user_id = current_user["id"]

    result = await db.execute(
        select(User)
        .options(
            selectinload(User.organization),
            selectinload(User.memberships),
            selectinload(User.social_accounts),
        )
        .where(User.id == user_id)
    )
    user = result.scalar_one()
    org = user.organization

    # Load org profile
    profile = None
    if org:
        prof_result = await db.execute(
            select(Profile).where(Profile.organization_id == org.id)
        )
        profile = prof_result.scalar_one_or_none()

    membership = next(
        (m for m in user.memberships if m.organization_id == org.id), None
    ) if org else None

    connected_platforms = [
        {
            "platform": sa.platform,
            "username": sa.username,
            "display_name": sa.display_name,
            "avatar_url": sa.avatar_url,
            "stats": sa.stats,
        }
        for sa in user.social_accounts
    ]

    return {
        "success": True,
        "message": "OK",
        "data": {
            "user": {
                "id": str(user.id),
                "email": user.email,
                "role": user.role,
                "subscription_tier": user.subscription_tier,
                "onboarding_completed_at": (
                    user.onboarding_completed_at.isoformat()
                    if user.onboarding_completed_at else None
                ),
            },
            "org": {
                "id": str(org.id),
                "name": org.name,
                "slug": org.slug,
                "org_type": org.org_type,
                "verification_status": org.verification_status,
                "is_personal_creator_org": org.is_personal_creator_org,
                "rejected_reason": org.rejected_reason,
                "verification_docs": org.verification_docs,
            } if org else None,
            "profile": {
                "display_name": profile.display_name if profile else None,
                "bio": profile.bio if profile else None,
                "category": profile.category if profile else None,
                "location": profile.location if profile else None,
                "avatar_url": profile.avatar_url if profile else None,
                "trust_score": profile.trust_score if profile else None,
                "access_level": profile.access_level if profile else None,
            } if profile else None,
            "membership": {
                "role": membership.role,
                "status": membership.status,
            } if membership else None,
            "connected_platforms": connected_platforms,
        },
    }


# ---------------------------------------------------------------------------
# PATCH /onboarding/org — update org name + profile details
# ---------------------------------------------------------------------------

@router.patch("/org", response_model=dict)
async def update_org(
    payload: OrgUpdatePayload,
    current_user: dict = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = current_user["org"]["id"]

    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one()

    prof_result = await db.execute(
        select(Profile).where(Profile.organization_id == org_id)
    )
    profile = prof_result.scalar_one_or_none()

    if payload.name is not None:
        org.name = payload.name

    if profile:
        if payload.bio is not None:
            profile.bio = payload.bio
        if payload.category is not None:
            profile.category = payload.category
        if payload.location is not None:
            profile.location = payload.location
        if payload.avatar_url is not None:
            profile.avatar_url = payload.avatar_url
        if payload.name is not None:
            profile.display_name = payload.name

    await db.commit()
    await cache_delete(user_key(current_user["id"]))

    return {"success": True, "message": "Organisation updated.", "data": {}}


# ---------------------------------------------------------------------------
# Shared S3 client helper
# ---------------------------------------------------------------------------

def _s3_client() -> "boto3.client":
    return boto3.client(
        "s3",
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(
            connect_timeout=5,
            read_timeout=10,
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    )


def _presign_get(key: str, expires: int = 3600) -> str:
    s3 = _s3_client()
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket_name.strip(), "Key": key},
        ExpiresIn=expires,
    )


# ---------------------------------------------------------------------------
# POST /onboarding/upload-doc/presign — return a presigned PUT URL for direct S3 upload
# ---------------------------------------------------------------------------

class PresignRequest(BaseModel):
    filename: str
    content_type: str


@router.post("/upload-doc/presign", response_model=dict)
async def presign_doc_upload(
    payload: PresignRequest,
    current_user: dict = Depends(require_org_admin),
) -> dict:
    if payload.content_type not in _ALLOWED_MIME:
        raise HTTPException(status_code=422, detail="File must be a PDF, JPG, or PNG")

    ext_map = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}
    ext = ext_map.get(payload.content_type, "bin")
    key = f"verification-docs/{current_user['org']['id']}/{uuid.uuid4()}.{ext}"

    def _presign() -> str:
        s3 = _s3_client()
        return s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.s3_bucket_name.strip(),
                "Key": key,
                "ContentType": payload.content_type,
            },
            ExpiresIn=300,  # 5 minutes to complete the upload
        )

    upload_url = await asyncio.to_thread(_presign)

    return {
        "success": True,
        "message": "Presigned URL generated.",
        "data": {"upload_url": upload_url, "key": key},
    }


# ---------------------------------------------------------------------------
# PATCH /onboarding/docs — save verification document metadata
# ---------------------------------------------------------------------------

@router.patch("/docs", response_model=dict)
async def save_verification_docs(
    payload: VerificationDocsPayload,
    current_user: dict = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = current_user["org"]["id"]
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one()

    org.verification_docs = {
        "website": payload.website,
        "gst": {
            "number": payload.gst_number or None,
            "file_key": payload.gst_file_url or None,
        },
        "cin": {
            "number": payload.cin_number or None,
            "file_key": payload.cin_file_url or None,
        },
        "trademark": {
            "file_key": payload.trademark_file_url or None,
        },
    }

    await db.commit()
    return {"success": True, "message": "Documents saved.", "data": {}}


# ---------------------------------------------------------------------------
# POST /onboarding/complete — mark onboarding done
# ---------------------------------------------------------------------------

@router.post("/complete", response_model=dict)
async def complete_onboarding(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(User)
        .options(selectinload(User.organization))
        .where(User.id == current_user["id"])
    )
    user = result.scalar_one()

    if user.onboarding_completed_at:
        return {
            "success": True,
            "message": "Onboarding already completed.",
            "data": {"verification_status": user.organization.verification_status if user.organization else None},
        }

    user.onboarding_completed_at = datetime.now(UTC)
    await db.commit()
    await cache_delete(user_key(str(user.id)))

    return {
        "success": True,
        "message": "Onboarding complete.",
        "data": {"verification_status": user.organization.verification_status if user.organization else None},
    }


# ---------------------------------------------------------------------------
# GET /onboarding/memberships/pending — list pending members (org admin only)
# ---------------------------------------------------------------------------

@router.get("/memberships/pending", response_model=dict)
async def list_pending_members(
    current_user: dict = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = current_user["org"]["id"]

    result = await db.execute(
        select(OrganizationMembership)
        .options(selectinload(OrganizationMembership.user))
        .where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.status == "pending",
        )
    )
    memberships = result.scalars().all()

    return {
        "success": True,
        "message": "OK",
        "data": [
            {
                "user_id": str(m.user_id),
                "email": m.user.email if m.user else None,
                "role": m.role,
                "created_at": m.created_at.isoformat(),
            }
            for m in memberships
        ],
    }


# ---------------------------------------------------------------------------
# POST /onboarding/memberships/{user_id}/approve
# ---------------------------------------------------------------------------

@router.post("/memberships/{member_user_id}/approve", response_model=dict)
async def approve_member(
    member_user_id: str,
    current_user: dict = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = current_user["org"]["id"]
    try:
        membership = await approve_membership(db, org_id, member_user_id, current_user["id"])
        await db.commit()
        await cache_delete(user_key(member_user_id))
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    return {
        "success": True,
        "message": "Member approved.",
        "data": {"user_id": member_user_id, "status": membership.status},
    }


# ---------------------------------------------------------------------------
# POST /onboarding/memberships/{user_id}/reject
# ---------------------------------------------------------------------------

@router.post("/memberships/{member_user_id}/reject", response_model=dict)
async def reject_member(
    member_user_id: str,
    current_user: dict = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = current_user["org"]["id"]
    try:
        membership = await reject_membership(db, org_id, member_user_id, current_user["id"])
        await db.commit()
        await cache_delete(user_key(member_user_id))
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    return {
        "success": True,
        "message": "Member rejected.",
        "data": {"user_id": member_user_id, "status": membership.status},
    }
