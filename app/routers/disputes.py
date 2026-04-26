from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.repositories.dispute_repo import (
    create_dispute_with_recipient,
    get_review_by_id,
    list_disputes_for_org,
    list_disputes_for_user,
)
from app.services.dispute_service import route_dispute

router = APIRouter(prefix="/disputes", tags=["disputes"])

VALID_TYPES = ("verification", "duplicate_name", "false_claim")


class DisputeCreatePayload(BaseModel):
    review_id: str
    type: str
    reason: str
    target_org_id: str | None = None


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_dispute(
    payload: DisputeCreatePayload,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if payload.type not in VALID_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"type must be one of: {', '.join(VALID_TYPES)}",
        )
    if payload.type in ("duplicate_name", "false_claim") and not payload.target_org_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="target_org_id is required for duplicate_name and false_claim disputes",
        )

    review = await get_review_by_id(db, payload.review_id)
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")

    recipient_type, recipient_org_id = route_dispute(payload.type, payload.target_org_id)
    dispute = await create_dispute_with_recipient(
        db,
        review_id=review.id,
        filed_by_user_id=current_user["id"],
        dispute_type=payload.type,
        reason=payload.reason,
        recipient_type=recipient_type,
        target_org_id=recipient_org_id,
    )

    return {
        "success": True,
        "message": "Dispute filed successfully",
        "data": {"id": str(dispute.id), "type": dispute.type, "status": dispute.status},
    }


@router.get("", response_model=dict)
async def list_disputes(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    org = current_user.get("org") or {}
    is_org_admin = org.get("membership_role") == "admin"
    org_id = org.get("id")

    if is_org_admin and org_id:
        disputes = await list_disputes_for_org(db, org_id)
    else:
        disputes = await list_disputes_for_user(db, current_user["id"])

    return {
        "success": True,
        "message": "OK",
        "data": [
            {
                "id": str(d.id),
                "type": d.type,
                "status": d.status,
                "reason": d.reason,
                "created_at": d.created_at.isoformat(),
            }
            for d in disputes
        ],
    }
