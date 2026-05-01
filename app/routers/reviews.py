import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_user, require_onboarded
from app.models.organization import Organization
from app.models.profile import Profile
from app.models.review import Review, ReviewEvidence, ReviewFlag, ReviewPayment, ReviewRating, ReviewTag
from app.repositories.profile_repo import get_profile_by_handle
from app.services.storage_service import presign_put

router = APIRouter(prefix="/reviews", tags=["reviews"])

_ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/webp",
    "application/pdf",
}
_EVIDENCE_TYPES = {"screenshot", "email", "contract", "invoice", "chat"}
_VALID_KINDS = {"creator", "agency", "brand"}

_VALID_RELATIONSHIPS = {
    "brand": {
        "creator": "brand_worked_with_creator",
        "agency":  "brand_worked_with_agency",
        # brand → brand: not allowed
    },
    "agency": {
        "creator": "agency_worked_with_creator",
        "brand":   "agency_worked_with_brand",
        "agency":  "agency_worked_with_agency",
    },
    "creator": {
        "brand":   "creator_worked_with_brand",
        "agency":  "creator_worked_with_agency",
        "creator": "creator_worked_with_creator",
    },
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class EvidencePresignRequest(BaseModel):
    content_type: str
    evidence_type: str


class RatingIn(BaseModel):
    category: str
    score: int


class PaymentIn(BaseModel):
    payment_type: str          # advance | milestone | final
    amount: int                # in smallest unit (paise)
    currency: str = "INR"
    status: str = "pending"    # pending | paid | late
    due_date: str | None = None
    paid_at: str | None = None


class FlagIn(BaseModel):
    type: str       # payment_not_made | payment_partial | payment_refused | payment_delayed | invoice_disputed | ghosted | missed_deadline | scope_creep | rude_behavior | contract_violation
    severity: str = "medium"  # low | medium | high


class EvidenceIn(BaseModel):
    type: str
    file_key: str


class OffPlatformTarget(BaseModel):
    name: str
    email: str
    kind: str           # creator | agency | brand
    youtube_url: str | None = None
    instagram_handle: str | None = None
    linkedin_url: str | None = None


class SubmitReviewRequest(BaseModel):
    # Exactly one of these must be set
    target_profile_handle: str | None = None
    off_platform: OffPlatformTarget | None = None

    body: str | None = None
    total_deal_value: int | None = None
    currency: str = "INR"
    contact_email: str
    contact_phone: str | None = None
    ratings: list[RatingIn] = []
    payments: list[PaymentIn] = []
    flags: list[FlagIn] = []
    tags: list[str] = []
    evidence: list[EvidenceIn] = []


# ---------------------------------------------------------------------------
# POST /reviews/evidence/presign
# ---------------------------------------------------------------------------

@router.post("/evidence/presign")
async def presign_evidence_upload(
    payload: EvidencePresignRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    if payload.content_type not in _ALLOWED_MIME:
        raise HTTPException(status_code=422, detail="Unsupported file type")
    if payload.evidence_type not in _EVIDENCE_TYPES:
        raise HTTPException(status_code=422, detail="Invalid evidence type")

    ext_map = {
        "image/jpeg": "jpg", "image/png": "png",
        "image/webp": "webp", "application/pdf": "pdf",
    }
    ext = ext_map.get(payload.content_type, "bin")
    key = f"review-evidence/{current_user['org']['id']}/{uuid.uuid4()}.{ext}"
    upload_url = await presign_put(key, payload.content_type)

    return {
        "success": True,
        "message": "Presigned URL generated.",
        "data": {"upload_url": upload_url, "key": key},
    }


# ---------------------------------------------------------------------------
# POST /reviews
# ---------------------------------------------------------------------------

@router.post("")
async def submit_review(
    payload: SubmitReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    # Validate: exactly one target source
    if not payload.target_profile_handle and not payload.off_platform:
        raise HTTPException(status_code=422, detail="Provide target_profile_handle or off_platform")
    if payload.target_profile_handle and payload.off_platform:
        raise HTTPException(status_code=422, detail="Provide only one of target_profile_handle or off_platform")

    reviewer_role = current_user["role"]
    reviewer_org_id = current_user["org"]["id"]

    if payload.target_profile_handle:
        # ── On-platform path ──────────────────────────────────────────────
        target = await get_profile_by_handle(db, payload.target_profile_handle)
        if not target:
            raise HTTPException(status_code=404, detail="Profile not found")

        target_kind = target.profile_type

        rel_map = _VALID_RELATIONSHIPS.get(reviewer_role, {})
        relationship_type = rel_map.get(target_kind)
        if not relationship_type:
            raise HTTPException(
                status_code=422,
                detail=f"A {reviewer_role} cannot review a {target_kind}",
            )

        if str(target.organization_id) == str(reviewer_org_id):
            raise HTTPException(status_code=422, detail="Cannot review your own profile")

        target_profile_id = target.id
        is_off_platform = False

    else:
        # ── Off-platform path ─────────────────────────────────────────────
        op = payload.off_platform
        if op.kind not in _VALID_KINDS:
            raise HTTPException(status_code=422, detail=f"Invalid kind: {op.kind}")

        rel_map = _VALID_RELATIONSHIPS.get(reviewer_role, {})
        relationship_type = rel_map.get(op.kind)
        if not relationship_type:
            raise HTTPException(
                status_code=422,
                detail=f"A {reviewer_role} cannot review a {op.kind}",
            )

        # Create dummy org + profile in this transaction
        slug = f"dummy-{uuid.uuid4().hex[:12]}"
        dummy_org = Organization(
            name=op.name.strip(),
            slug=slug,
            org_type=op.kind,
            verification_status="pending",
        )
        db.add(dummy_org)
        await db.flush()  # get dummy_org.id

        dummy_profile = Profile(
            organization_id=dummy_org.id,
            profile_type=op.kind,
            is_dummy=True,
        )
        db.add(dummy_profile)
        await db.flush()  # get dummy_profile.id

        target_profile_id = dummy_profile.id
        is_off_platform = True

    # Build Review
    # Off-platform: dispute window does NOT start until the target claims their profile.
    # On-platform: 48hr window starts immediately.
    dispute_window_expires_at = (
        None if is_off_platform else datetime.now(UTC) + timedelta(hours=48)
    )
    review = Review(
        reviewer_id=uuid.UUID(current_user["id"]),
        target_profile_id=target_profile_id,
        relationship_type=relationship_type,
        body=payload.body or None,
        contact_email=payload.contact_email.strip(),
        contact_phone=payload.contact_phone.strip() if payload.contact_phone else None,
        total_deal_value=payload.total_deal_value,
        currency=payload.currency,
        status="in_dispute_window",
        dispute_window_expires_at=dispute_window_expires_at,
    )
    db.add(review)
    await db.flush()  # get review.id

    # Ratings
    for r in payload.ratings:
        if not (1 <= r.score <= 5):
            continue
        db.add(ReviewRating(review_id=review.id, category=r.category, score=r.score))

    # Payments (max 3 — one per type)
    _VALID_PAYMENT_TYPES = {"advance", "milestone", "final"}
    _VALID_PAYMENT_STATUSES = {"pending", "paid", "late"}
    seen_types: set[str] = set()
    for p in payload.payments:
        if p.payment_type not in _VALID_PAYMENT_TYPES:
            continue
        if p.payment_type in seen_types:
            continue  # one row per type
        seen_types.add(p.payment_type)
        due_date = None
        paid_at = None
        if p.due_date:
            try:
                from datetime import date as _date
                due_date = _date.fromisoformat(p.due_date)
            except ValueError:
                pass
        if p.paid_at:
            try:
                paid_at = datetime.fromisoformat(p.paid_at).replace(tzinfo=UTC)
            except ValueError:
                pass
        db.add(ReviewPayment(
            review_id=review.id,
            amount=p.amount,
            currency=p.currency,
            payment_type=p.payment_type,
            status=p.status if p.status in _VALID_PAYMENT_STATUSES else "pending",
            due_date=due_date,
            paid_at=paid_at,
        ))

    # Flags
    _VALID_FLAG_TYPES = {
        "ghosted", "missed_deadline", "scope_creep", "rude_behavior", "contract_violation",
        "payment_not_made", "payment_partial", "payment_refused", "payment_delayed", "invoice_disputed",
    }
    _VALID_SEVERITIES = {"low", "medium", "high"}
    for f in payload.flags:
        if f.type not in _VALID_FLAG_TYPES:
            continue
        db.add(ReviewFlag(
            review_id=review.id,
            type=f.type,
            severity=f.severity if f.severity in _VALID_SEVERITIES else "medium",
        ))

    # Tags (max 5)
    for tag in payload.tags[:5]:
        db.add(ReviewTag(review_id=review.id, tag=tag))

    # Evidence
    for ev in payload.evidence:
        if ev.type not in _EVIDENCE_TYPES:
            continue
        db.add(ReviewEvidence(
            review_id=review.id,
            type=ev.type,
            file_key=ev.file_key,
            verified=False,
        ))

    await db.commit()

    # Fire fanout: in-app notifications + email (+ WhatsApp later)
    from app.tasks.review_notifications import notify_review_submitted
    notify_review_submitted.delay(str(review.id))

    success_message = (
        "Review submitted. The 48-hour dispute window will start once the target claims their profile."
        if is_off_platform
        else "Review submitted. It will go live after the 48-hour dispute window."
    )
    return {
        "success": True,
        "message": success_message,
        "data": {
            "id": str(review.id),
            "status": review.status,
            "dispute_window_expires_at": (
                review.dispute_window_expires_at.isoformat()
                if review.dispute_window_expires_at else None
            ),
        },
    }


# ---------------------------------------------------------------------------
# POST /reviews/{review_id}/accept  — recipient accepts review → goes live
# ---------------------------------------------------------------------------

class RecipientDisputePayload(BaseModel):
    reason: str
    evidence_keys: list[str] = []


@router.post("/{review_id}/accept")
async def accept_review(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_onboarded),
) -> dict:
    from sqlalchemy import select as _select
    from sqlalchemy.orm import selectinload as _si
    from app.models.organization import Organization
    from app.models.profile import Profile
    from app.models.user import User

    review = (await db.execute(
        _select(Review)
        .options(
            _si(Review.target_profile).selectinload(Profile.organization),
            _si(Review.reviewer),
        )
        .where(Review.id == uuid.UUID(review_id))
    )).scalar_one_or_none()

    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    user_org_id = (current_user.get("org") or {}).get("id")
    if not user_org_id or str(review.target_profile.organization_id) != str(user_org_id):
        raise HTTPException(status_code=403, detail="Only the review recipient can accept a review")

    if review.status != "in_dispute_window":
        raise HTTPException(
            status_code=409,
            detail=f"Review cannot be accepted when status is '{review.status}'",
        )

    review.status = "verified"
    review.verified_at = datetime.now(UTC)

    # Patch review_status in all related review_received notifications
    from sqlalchemy import update as _update
    from app.models.notification import Notification
    await db.execute(
        _update(Notification)
        .where(
            Notification.notification_type == "review_received",
            Notification.extra_data["review_id"].astext == review_id,
        )
        .values(extra_data=Notification.extra_data.op("||")({"review_status": "verified"}))
    )

    await db.commit()

    from app.tasks.score import recalculate_trust_score
    recalculate_trust_score.delay(str(review.target_profile_id))

    from app.tasks.review_notifications import notify_review_verified
    notify_review_verified.delay(review_id)

    return {
        "success": True,
        "message": "Review accepted. It is now publicly visible on your profile.",
        "data": {"review_id": review_id, "status": "verified"},
    }


# ---------------------------------------------------------------------------
# POST /reviews/{review_id}/dispute  — recipient disputes a review
# ---------------------------------------------------------------------------

@router.post("/{review_id}/dispute")
async def dispute_review(
    review_id: str,
    payload: RecipientDisputePayload,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_onboarded),
) -> dict:
    from sqlalchemy import select as _select
    from sqlalchemy.orm import selectinload as _si
    from app.models.dispute import Dispute
    from app.models.dispute_recipient import DisputeRecipient
    from app.models.profile import Profile
    from app.models.user import User

    review = (await db.execute(
        _select(Review)
        .options(_si(Review.target_profile))
        .where(Review.id == uuid.UUID(review_id))
    )).scalar_one_or_none()

    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    user_org_id = (current_user.get("org") or {}).get("id")
    if not user_org_id or str(review.target_profile.organization_id) != str(user_org_id):
        raise HTTPException(status_code=403, detail="Only the review recipient can dispute a review")

    if review.status != "in_dispute_window":
        raise HTTPException(
            status_code=409,
            detail=f"Review cannot be disputed when status is '{review.status}'",
        )

    existing = (await db.execute(
        _select(Dispute).where(Dispute.review_id == review.id)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="A dispute already exists for this review")

    dispute = Dispute(
        review_id=review.id,
        filed_by_user_id=uuid.UUID(current_user["id"]),
        type="recipient_dispute",
        reason=payload.reason.strip(),
        counter_evidence_keys=payload.evidence_keys if payload.evidence_keys else None,
    )
    db.add(dispute)
    await db.flush()

    db.add(DisputeRecipient(dispute_id=dispute.id, recipient_type="platform_admin"))

    review.status = "disputed"

    # Patch review_status in all related review_received notifications
    from sqlalchemy import update as _update_d
    from app.models.notification import Notification as _Notif
    await db.execute(
        _update_d(_Notif)
        .where(
            _Notif.notification_type == "review_received",
            _Notif.extra_data["review_id"].astext == review_id,
        )
        .values(extra_data=_Notif.extra_data.op("||")({"review_status": "disputed"}))
    )

    await db.commit()

    case_id = str(dispute.id)[:8].upper()

    # Notify both parties
    from app.tasks.review_notifications import send_email_task

    reviewer = (await db.execute(
        _select(User).where(User.id == review.reviewer_id)
    )).scalar_one_or_none()

    if reviewer:
        send_email_task.delay(
            "dispute_filed",
            reviewer.email,
            {"case_id": case_id, "review_id": str(review.id), "role": "reviewer"},
        )
    send_email_task.delay(
        "dispute_filed",
        current_user["email"],
        {"case_id": case_id, "review_id": str(review.id), "role": "target"},
    )

    return {
        "success": True,
        "message": "Dispute filed. Platform admins will review and mediate within 48 hours.",
        "data": {"dispute_id": str(dispute.id), "case_id": case_id},
    }


# ---------------------------------------------------------------------------
# GET /reviews  (stub — filtered listing for admin / future use)
# ---------------------------------------------------------------------------

@router.get("")
async def list_reviews() -> dict:
    return {"success": True, "message": "TODO", "data": []}
