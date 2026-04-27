"""
Onboarding router — auth required, no verification gate.

Endpoints are reachable by any authenticated user regardless of
onboarding_completed_at or org.verification_status.
"""

import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, user_key
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_org_admin
from app.repositories.org_repo import (
    get_org_by_id,
    get_profile_by_org_id,
    list_pending_memberships,
)
from app.repositories.user_repo import get_user_with_org_and_social
from app.services.onboarding_service import build_docs_dict, build_onboarding_context
from app.services.org_service import approve_membership, reject_membership
from app.services.storage_service import presign_put

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

_ALLOWED_MIME = {"image/jpeg", "image/png", "application/pdf"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SocialLinkItem(BaseModel):
    platform: str   # instagram | youtube | linkedin | twitter | facebook | tiktok
    url: str
    label: str | None = None  # optional display label e.g. "Main Page", "India"


class OrgUpdatePayload(BaseModel):
    name: str | None = None
    bio: str | None = None
    category: str | None = None
    location: str | None = None
    avatar_url: str | None = None
    languages: list[str] | None = None
    niches: list[str] | None = None
    social_links: list[SocialLinkItem] | None = None  # agency/brand only


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


class PresignRequest(BaseModel):
    filename: str
    content_type: str


# ---------------------------------------------------------------------------
# GET /onboarding/me
# ---------------------------------------------------------------------------

@router.get("/me", response_model=dict)
async def onboarding_me(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await get_user_with_org_and_social(db, current_user["id"])
    org = user.organization
    profile = await get_profile_by_org_id(db, org.id) if org else None
    membership = next(
        (m for m in user.memberships if m.organization_id == org.id), None
    ) if org else None

    return {
        "success": True,
        "message": "OK",
        "data": build_onboarding_context(user, org, profile, membership, user.social_accounts),
    }


# ---------------------------------------------------------------------------
# PATCH /onboarding/org
# ---------------------------------------------------------------------------

@router.patch("/org", response_model=dict)
async def update_org(
    payload: OrgUpdatePayload,
    current_user: dict = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org_id = current_user["org"]["id"]
    org = await get_org_by_id(db, org_id)
    profile = await get_profile_by_org_id(db, org_id)

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
            profile.handle = org.slug
        if payload.languages is not None:
            profile.languages = payload.languages
        if payload.niches is not None:
            profile.niches = payload.niches
        if payload.social_links is not None:
            profile.social_links = [lnk.model_dump(exclude_none=True) for lnk in payload.social_links]

    await db.commit()
    await cache_delete(user_key(current_user["id"]))

    return {"success": True, "message": "Organisation updated.", "data": {}}


# ---------------------------------------------------------------------------
# POST /onboarding/upload-doc/presign
# ---------------------------------------------------------------------------

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
    upload_url = await presign_put(key, payload.content_type)

    return {
        "success": True,
        "message": "Presigned URL generated.",
        "data": {"upload_url": upload_url, "key": key},
    }


# ---------------------------------------------------------------------------
# PATCH /onboarding/docs
# ---------------------------------------------------------------------------

@router.patch("/docs", response_model=dict)
async def save_verification_docs(
    payload: VerificationDocsPayload,
    current_user: dict = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await get_org_by_id(db, current_user["org"]["id"])
    org.verification_docs = build_docs_dict(payload)
    await db.commit()
    return {"success": True, "message": "Documents saved.", "data": {}}


# ---------------------------------------------------------------------------
# POST /onboarding/complete
# ---------------------------------------------------------------------------

@router.post("/complete", response_model=dict)
async def complete_onboarding(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await get_user_with_org_and_social(db, current_user["id"])

    if user.onboarding_completed_at:
        return {
            "success": True,
            "message": "Onboarding already completed.",
            "data": {
                "verification_status": user.organization.verification_status
                if user.organization else None
            },
        }

    user.onboarding_completed_at = datetime.now(UTC)
    await db.commit()
    await cache_delete(user_key(str(user.id)))

    return {
        "success": True,
        "message": "Onboarding complete.",
        "data": {
            "verification_status": user.organization.verification_status
            if user.organization else None
        },
    }


# ---------------------------------------------------------------------------
# GET /onboarding/memberships/pending
# ---------------------------------------------------------------------------

@router.get("/memberships/pending", response_model=dict)
async def list_pending_members(
    current_user: dict = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    memberships = await list_pending_memberships(db, current_user["org"]["id"])
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
    try:
        membership = await approve_membership(db, current_user["org"]["id"], member_user_id, current_user["id"])
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
    try:
        membership = await reject_membership(db, current_user["org"]["id"], member_user_id, current_user["id"])
        await db.commit()
        await cache_delete(user_key(member_user_id))
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")

    return {
        "success": True,
        "message": "Member rejected.",
        "data": {"user_id": member_user_id, "status": membership.status},
    }
