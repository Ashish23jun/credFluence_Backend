from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.dispute import Dispute
from app.models.dispute_recipient import DisputeRecipient
from app.models.review import Review

router = APIRouter(prefix="/disputes", tags=["disputes"])

VALID_TYPES = ("verification", "duplicate_name", "false_claim")


class DisputeCreatePayload(BaseModel):
    review_id: str
    type: str
    reason: str
    target_org_id: str | None = None  # required for duplicate_name + false_claim


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

    # Verify review exists
    result = await db.execute(select(Review).where(Review.id == payload.review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")

    dispute = Dispute(
        review_id=review.id,
        filed_by_user_id=current_user["id"],
        type=payload.type,
        reason=payload.reason,
        status="open",
    )
    db.add(dispute)
    await db.flush()

    # Route based on type
    if payload.type == "verification":
        db.add(DisputeRecipient(
            dispute_id=dispute.id,
            recipient_type="platform_admin",
        ))
    else:
        db.add(DisputeRecipient(
            dispute_id=dispute.id,
            recipient_type="org_admin",
            recipient_org_id=payload.target_org_id,
        ))

    await db.commit()
    await db.refresh(dispute)

    return {
        "success": True,
        "message": "Dispute filed successfully",
        "data": {
            "id": str(dispute.id),
            "type": dispute.type,
            "status": dispute.status,
        },
    }


@router.get("", response_model=dict)
async def list_disputes(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user_org_id = current_user.get("org", {}).get("id") if current_user.get("org") else None
    is_org_admin = (
        current_user.get("org", {}).get("membership_role") == "admin"
        if current_user.get("org") else False
    )

    if is_org_admin and user_org_id:
        # Org admins see disputes targeting their org
        result = await db.execute(
            select(Dispute).join(DisputeRecipient).where(
                DisputeRecipient.recipient_type == "org_admin",
                DisputeRecipient.recipient_org_id == user_org_id,
            )
        )
    else:
        # Regular users see their own filed disputes
        result = await db.execute(
            select(Dispute).where(Dispute.filed_by_user_id == current_user["id"])
        )

    disputes = result.scalars().all()
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
