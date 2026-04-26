from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, user_key
from app.core.database import get_db
from app.core.dependencies import get_current_platform_admin
from app.models.social_account import SocialAccount
from app.models.user import User
from app.repositories.org_repo import get_org_by_id, get_org_with_detail, list_orgs_by_status
from app.services.admin_service import serialize_org_detail, serialize_org_list_item

router = APIRouter(prefix="/admin", tags=["admin"])


class OrgRejectPayload(BaseModel):
    reason: str


# ---------------------------------------------------------------------------
# GET /admin/orgs
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

    orgs = await list_orgs_by_status(db, org_status, org_type)
    return {
        "success": True,
        "message": "OK",
        "data": [serialize_org_list_item(o) for o in orgs],
    }


# ---------------------------------------------------------------------------
# GET /admin/orgs/{org_id}
# ---------------------------------------------------------------------------

@router.get("/orgs/{org_id}", response_model=dict)
async def get_org_detail(
    org_id: str,
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = await get_org_with_detail(db, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")

    member_user_ids = [m.user_id for m in org.memberships]
    sa_result = await db.execute(
        select(SocialAccount).where(SocialAccount.user_id.in_(member_user_ids))
    )
    social_accounts = sa_result.scalars().all()

    return {
        "success": True,
        "message": "OK",
        "data": serialize_org_detail(org, social_accounts),
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
    org = await get_org_by_id(db, org_id)
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
    org = await get_org_by_id(db, org_id)
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
