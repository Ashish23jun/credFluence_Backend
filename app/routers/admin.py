import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_delete, user_key
from app.core.database import get_db
from app.core.dependencies import get_current_platform_admin
from app.models.dispute import Dispute
from app.models.notification import Notification
from app.models.organization_membership import OrganizationMembership
from app.models.profile import Profile
from app.models.review import Review
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


# ---------------------------------------------------------------------------
# Schemas (reviews + disputes)
# ---------------------------------------------------------------------------

class ReviewRejectPayload(BaseModel):
    reason: str


class DisputeResolvePayload(BaseModel):
    outcome: str           # reviewer_won | target_won | mutual_resolution
    resolution_notes: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialise_review(review: Review) -> dict:
    return {
        "id": str(review.id),
        "status": review.status,
        "relationship_type": review.relationship_type,
        "body": review.body,
        "total_deal_value": review.total_deal_value,
        "currency": review.currency,
        "contact_email": review.contact_email,
        "dispute_window_expires_at": (
            review.dispute_window_expires_at.isoformat()
            if review.dispute_window_expires_at else None
        ),
        "created_at": review.created_at.isoformat(),
        "target_profile_id": str(review.target_profile_id),
        "reviewer_id": str(review.reviewer_id),
    }


def _serialise_dispute(dispute: Dispute) -> dict:
    return {
        "id": str(dispute.id),
        "review_id": str(dispute.review_id),
        "type": dispute.type,
        "reason": dispute.reason,
        "status": dispute.status,
        "outcome": dispute.outcome,
        "resolution_notes": dispute.resolution_notes,
        "resolved_at": dispute.resolved_at.isoformat() if dispute.resolved_at else None,
        "created_at": dispute.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# GET /admin/reviews
# ---------------------------------------------------------------------------

@router.get("/reviews")
async def list_admin_reviews(
    status: str = Query(default="in_dispute_window"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _allowed = {"in_dispute_window", "disputed", "pending_verification", "verified", "rejected", "quarantined"}
    if status not in _allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(sorted(_allowed))}")

    result = await db.execute(
        select(Review)
        .where(Review.status == status)
        .order_by(Review.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    reviews = result.scalars().all()

    return {
        "success": True,
        "message": "OK",
        "data": [_serialise_review(r) for r in reviews],
    }


# ---------------------------------------------------------------------------
# GET /admin/reviews/{review_id}
# ---------------------------------------------------------------------------

@router.get("/reviews/{review_id}")
async def get_admin_review(
    review_id: uuid.UUID,
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    review = await db.scalar(
        select(Review)
        .where(Review.id == review_id)
        .options(
            selectinload(Review.ratings),
            selectinload(Review.payments),
            selectinload(Review.flags),
            selectinload(Review.evidence),
            selectinload(Review.tags),
            selectinload(Review.dispute),
        )
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    data = _serialise_review(review)
    data["ratings"] = [{"category": r.category, "score": r.score} for r in review.ratings]
    data["payments"] = [{"type": p.payment_type, "status": p.status, "amount": p.amount} for p in review.payments]
    data["flags"] = [{"type": f.type, "severity": f.severity} for f in review.flags]
    data["tags"] = [t.tag for t in review.tags]
    data["evidence"] = [{"type": e.type, "file_key": e.file_key, "verified": e.verified} for e in review.evidence]
    data["dispute"] = _serialise_dispute(review.dispute) if review.dispute else None

    return {"success": True, "message": "OK", "data": data}


# ---------------------------------------------------------------------------
# POST /admin/reviews/{review_id}/verify
# ---------------------------------------------------------------------------

@router.post("/reviews/{review_id}/verify")
async def admin_verify_review(
    review_id: uuid.UUID,
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    review = await db.get(Review, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.status == "verified":
        raise HTTPException(status_code=409, detail="Review is already verified")

    review.status = "verified"
    review.verified_at = datetime.now(UTC)
    review.verified_by_admin_id = uuid.UUID(current_admin["id"])
    await db.commit()

    from app.tasks.score import recalculate_trust_score
    recalculate_trust_score.delay(str(review.target_profile_id))

    return {
        "success": True,
        "message": "Review verified.",
        "data": {"id": str(review.id), "status": review.status},
    }


# ---------------------------------------------------------------------------
# POST /admin/reviews/{review_id}/reject
# ---------------------------------------------------------------------------

@router.post("/reviews/{review_id}/reject")
async def admin_reject_review(
    review_id: uuid.UUID,
    payload: ReviewRejectPayload,
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    review = await db.get(Review, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.status in ("verified", "rejected"):
        raise HTTPException(status_code=409, detail=f"Review is already {review.status}")

    review.status = "rejected"
    review.admin_notes = payload.reason
    review.verified_by_admin_id = uuid.UUID(current_admin["id"])
    review.verified_at = datetime.now(UTC)
    await db.commit()

    return {
        "success": True,
        "message": "Review rejected.",
        "data": {"id": str(review.id), "status": review.status},
    }


# ---------------------------------------------------------------------------
# GET /admin/disputes
# ---------------------------------------------------------------------------

@router.get("/disputes")
async def list_admin_disputes(
    status: str = Query(default="open"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _allowed = {"open", "investigating", "resolved_in_favor", "resolved_rejected"}
    if status not in _allowed:
        raise HTTPException(status_code=400, detail=f"status must be one of: {', '.join(sorted(_allowed))}")

    result = await db.execute(
        select(Dispute)
        .where(Dispute.status == status)
        .order_by(Dispute.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    disputes = result.scalars().all()

    return {
        "success": True,
        "message": "OK",
        "data": [_serialise_dispute(d) for d in disputes],
    }


# ---------------------------------------------------------------------------
# GET /admin/disputes/{dispute_id}
# ---------------------------------------------------------------------------

@router.get("/disputes/{dispute_id}")
async def get_admin_dispute(
    dispute_id: uuid.UUID,
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    dispute = await db.scalar(
        select(Dispute)
        .where(Dispute.id == dispute_id)
        .options(selectinload(Dispute.review))
    )
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    data = _serialise_dispute(dispute)
    data["review"] = _serialise_review(dispute.review) if dispute.review else None

    return {"success": True, "message": "OK", "data": data}


# ---------------------------------------------------------------------------
# POST /admin/disputes/{dispute_id}/resolve
# ---------------------------------------------------------------------------

_VALID_OUTCOMES = {"reviewer_won", "target_won", "mutual_resolution"}


@router.post("/disputes/{dispute_id}/resolve")
async def resolve_dispute(
    dispute_id: uuid.UUID,
    payload: DisputeResolvePayload,
    current_admin: dict = Depends(get_current_platform_admin),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if payload.outcome not in _VALID_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"outcome must be one of: {', '.join(sorted(_VALID_OUTCOMES))}",
        )

    dispute = await db.scalar(
        select(Dispute)
        .where(Dispute.id == dispute_id)
        .options(
            selectinload(Dispute.review).selectinload(Review.reviewer),
            selectinload(Dispute.review).selectinload(
                Review.target_profile
            ).selectinload(Profile.organization),
        )
    )
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    if dispute.status in ("resolved_in_favor", "resolved_rejected"):
        raise HTTPException(status_code=409, detail="Dispute is already resolved")

    now = datetime.now(UTC)
    review = dispute.review

    # Resolve dispute
    if payload.outcome == "reviewer_won":
        dispute.status = "resolved_in_favor"
        review.status = "verified"
        review.verified_at = now
        review.verified_by_admin_id = uuid.UUID(current_admin["id"])
    elif payload.outcome == "target_won":
        dispute.status = "resolved_rejected"
        review.status = "rejected"
        review.verified_by_admin_id = uuid.UUID(current_admin["id"])
        review.verified_at = now
    else:  # mutual_resolution
        dispute.status = "resolved_in_favor"
        review.status = "verified"
        review.verified_at = now
        review.verified_by_admin_id = uuid.UUID(current_admin["id"])

    dispute.outcome = payload.outcome
    dispute.resolution_notes = payload.resolution_notes
    dispute.resolved_by_admin_id = uuid.UUID(current_admin["id"])
    dispute.resolved_at = now

    # In-app notification for the person who filed the dispute
    # Flush immediately to get the ID before dispatching the email task
    filer_notif = Notification(
        user_id=dispute.filed_by_user_id,
        notification_type="dispute_resolved",
        title="Your dispute has been resolved",
        body=f"Outcome: {payload.outcome.replace('_', ' ').title()}. {payload.resolution_notes[:120]}",
        extra_data={"dispute_id": str(dispute.id), "outcome": payload.outcome},
    )
    db.add(filer_notif)
    await db.flush()

    await db.commit()

    # Score recalc if review is now verified
    if review.status == "verified":
        from app.tasks.score import recalculate_trust_score
        recalculate_trust_score.delay(str(review.target_profile_id))

    # Emails — notify reviewer and filer (may be the same person, deduplicate)
    from app.tasks.review_notifications import send_email_task
    email_kwargs = {"case_id": str(dispute.id), "outcome": payload.outcome}
    notified: set[str] = set()

    reviewer_email = review.reviewer.email if review.reviewer else None
    if reviewer_email:
        # Reviewer has no separate in-app row here, so no notification_id
        send_email_task.delay("dispute_resolved", reviewer_email, email_kwargs)
        notified.add(reviewer_email)

    filer_result = await db.get(User, dispute.filed_by_user_id)
    if filer_result and filer_result.email and filer_result.email not in notified:
        # Filer's email is linked to the Notification row we just created
        send_email_task.delay("dispute_resolved", filer_result.email, email_kwargs, str(filer_notif.id))

    return {
        "success": True,
        "message": "Dispute resolved.",
        "data": _serialise_dispute(dispute),
    }
