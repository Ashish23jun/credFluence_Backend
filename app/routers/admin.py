from datetime import UTC, datetime

import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_delete, user_key
from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import get_current_platform_admin
from app.models.organization import Organization
from app.models.organization_domain import OrganizationDomain
from app.models.organization_membership import OrganizationMembership
from app.models.profile import Profile
from app.models.social_account import SocialAccount
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


def _presign_doc_key(key: str | None, expires: int = 3600) -> str | None:
    if not key:
        return None
    s3 = boto3.client(
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
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket_name.strip(), "Key": key},
        ExpiresIn=expires,
    )


def _serialize_docs(docs: dict | None) -> dict | None:
    if not docs:
        return None
    gst = docs.get("gst", {})
    cin = docs.get("cin", {})
    trademark = docs.get("trademark", {})
    return {
        "website": docs.get("website"),
        "gst": {
            "number": gst.get("number"),
            "file_url": _presign_doc_key(gst.get("file_key")),
        },
        "cin": {
            "number": cin.get("number"),
            "file_url": _presign_doc_key(cin.get("file_key")),
        },
        "trademark": {
            "file_url": _presign_doc_key(trademark.get("file_key")),
        },
    }


class OrgRejectPayload(BaseModel):
    reason: str


# ---------------------------------------------------------------------------
# GET /admin/orgs — list business orgs (brands + agencies), filterable by status
# ---------------------------------------------------------------------------

@router.get("/orgs", response_model=dict)
async def list_orgs(
    org_status: str = Query(default="pending", alias="status"),
    org_type: str | None = Query(default=None),
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if org_status not in ("pending", "verified", "rejected"):
        raise HTTPException(status_code=400, detail="status must be pending, verified, or rejected")

    query = (
        select(Organization)
        .options(
            selectinload(Organization.domains),
            selectinload(Organization.memberships).selectinload(OrganizationMembership.user),
        )
        .where(
            Organization.is_personal_creator_org == False,  # noqa: E712
            Organization.verification_status == org_status,
        )
        .order_by(Organization.created_at.asc())
    )

    if org_type and org_type in ("agency", "brand"):
        query = query.where(Organization.org_type == org_type)

    result = await db.execute(query)
    orgs = result.scalars().all()

    def _serialize_org(org: Organization) -> dict:
        admin_member = next(
            (m for m in org.memberships if m.role == "admin" and m.status == "active"), None
        )
        return {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "org_type": org.org_type,
            "verification_status": org.verification_status,
            "rejected_reason": org.rejected_reason,
            "created_at": org.created_at.isoformat(),
            "verified_at": org.verified_at.isoformat() if org.verified_at else None,
            "domains": [d.domain for d in org.domains],
            "member_count": len(org.memberships),
            "admin_email": admin_member.user.email if admin_member and admin_member.user else None,
        }

    return {
        "success": True,
        "message": "OK",
        "data": [_serialize_org(o) for o in orgs],
    }


# ---------------------------------------------------------------------------
# GET /admin/orgs/{org_id} — full org detail
# ---------------------------------------------------------------------------

@router.get("/orgs/{org_id}", response_model=dict)
async def get_org_detail(
    org_id: str,
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Organization)
        .options(
            selectinload(Organization.domains),
            selectinload(Organization.memberships).selectinload(OrganizationMembership.user),
            selectinload(Organization.profile),
        )
        .where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    member_user_ids = [m.user_id for m in org.memberships]
    social_result = await db.execute(
        select(SocialAccount).where(SocialAccount.user_id.in_(member_user_ids))
    )
    social_accounts = social_result.scalars().all()

    profile = org.profile

    return {
        "success": True,
        "message": "OK",
        "data": {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
            "org_type": org.org_type,
            "verification_status": org.verification_status,
            "verification_notes": org.verification_notes,
            "rejected_reason": org.rejected_reason,
            "verified_at": org.verified_at.isoformat() if org.verified_at else None,
            "created_at": org.created_at.isoformat(),
            "domains": [d.domain for d in org.domains],
            "profile": {
                "display_name": profile.display_name if profile else None,
                "bio": profile.bio if profile else None,
                "category": profile.category if profile else None,
                "location": profile.location if profile else None,
                "avatar_url": profile.avatar_url if profile else None,
                "trust_score": profile.trust_score if profile else None,
                "access_level": profile.access_level if profile else None,
            } if profile else None,
            "members": [
                {
                    "user_id": str(m.user_id),
                    "email": m.user.email if m.user else None,
                    "role": m.role,
                    "status": m.status,
                    "joined_at": m.created_at.isoformat(),
                }
                for m in org.memberships
            ],
            "social_accounts": [
                {
                    "user_id": str(sa.user_id),
                    "platform": sa.platform,
                    "username": sa.username,
                    "display_name": sa.display_name,
                    "stats": sa.stats,
                    "connected_at": sa.connected_at.isoformat() if sa.connected_at else None,
                }
                for sa in social_accounts
            ],
            "verification_docs": _serialize_docs(org.verification_docs),
        },
    }


# ---------------------------------------------------------------------------
# POST /admin/orgs/{org_id}/verify
# ---------------------------------------------------------------------------

@router.post("/orgs/{org_id}/verify", response_model=dict)
async def verify_org(
    org_id: str,
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    org.verification_status = "verified"
    org.verified_at = datetime.now(UTC)
    org.verified_by_admin_id = current_admin["id"]
    org.rejected_reason = None
    await db.commit()

    members_result = await db.execute(select(User).where(User.organization_id == org_id))
    for member in members_result.scalars().all():
        await cache_delete(user_key(str(member.id)))

    return {
        "success": True,
        "message": f"Organisation '{org.name}' verified.",
        "data": {"id": str(org.id), "verification_status": org.verification_status},
    }


# ---------------------------------------------------------------------------
# POST /admin/orgs/{org_id}/reject
# ---------------------------------------------------------------------------

@router.post("/orgs/{org_id}/reject", response_model=dict)
async def reject_org(
    org_id: str,
    payload: OrgRejectPayload,
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    org.verification_status = "rejected"
    org.rejected_reason = payload.reason
    org.verified_at = None
    await db.commit()

    members_result = await db.execute(select(User).where(User.organization_id == org_id))
    for member in members_result.scalars().all():
        await cache_delete(user_key(str(member.id)))

    return {
        "success": True,
        "message": f"Organisation '{org.name}' rejected.",
        "data": {
            "id": str(org.id),
            "verification_status": org.verification_status,
            "rejected_reason": org.rejected_reason,
        },
    }
