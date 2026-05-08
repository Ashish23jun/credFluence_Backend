import base64
import contextlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import get_current_user, get_optional_user, require_onboarded
from app.models.dispute import Dispute
from app.models.dispute_recipient import DisputeRecipient
from app.models.notification import Notification
from app.models.organization import Organization
from app.models.profile import Profile
from app.models.review import (
    CommentLike,
    Review,
    ReviewComment,
    ReviewEvidence,
    ReviewFlag,
    ReviewLike,
    ReviewPayment,
    ReviewRating,
    ReviewReply,
    ReviewTag,
)
from app.models.user import User
from app.repositories import notification_pref_repo
from app.repositories.profile_repo import get_my_reviews_cursor, get_profile_by_handle
from app.schemas.reviews import (
    CommentIn,
    EvidencePresignRequest,
    RecipientDisputePayload,
    ReplyIn,
    SubmitReviewRequest,
)
from app.services.profile_service import _reviewer_primary_social
from app.services.storage_service import presign_put
from app.tasks.review_notifications import (
    notify_comment_reply,
    notify_new_comment,
    notify_review_liked,
    notify_review_submitted,
    notify_review_verified,
    send_email_task,
)
from app.tasks.score import recalculate_trust_score

router = APIRouter(prefix="/reviews", tags=["reviews"])

_ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/webp",
    "application/pdf",
    "video/mp4", "video/quicktime", "video/webm",
}
_EVIDENCE_TYPES = {"screenshot", "email", "contract", "invoice", "chat", "video"}
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
        "video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm",
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
    if current_user["role"] == "creator" and current_user.get("access_level") == "limited":
        raise HTTPException(
            status_code=403,
            detail="Connect at least one platform (Instagram or YouTube) to write reviews.",
        )

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
            with contextlib.suppress(ValueError):
                due_date = date.fromisoformat(p.due_date)
        if p.paid_at:
            with contextlib.suppress(ValueError):
                paid_at = datetime.fromisoformat(p.paid_at).replace(tzinfo=UTC)
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



@router.post("/{review_id}/accept")
async def accept_review(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_onboarded),
) -> dict:
    review = (await db.execute(
        select(Review)
        .options(
            selectinload(Review.target_profile).selectinload(Profile.organization),
            selectinload(Review.reviewer),
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
    await db.execute(
        update(Notification)
        .where(
            Notification.notification_type == "review_received",
            Notification.extra_data["review_id"].astext == review_id,
        )
        .values(extra_data=Notification.extra_data.op("||")({"review_status": "verified"}))
    )

    await db.commit()

    recalculate_trust_score.delay(str(review.target_profile_id))
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
    review = (await db.execute(
        select(Review)
        .options(selectinload(Review.target_profile))
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
        select(Dispute).where(Dispute.review_id == review.id)
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
    await db.execute(
        update(Notification)
        .where(
            Notification.notification_type == "review_received",
            Notification.extra_data["review_id"].astext == review_id,
        )
        .values(extra_data=Notification.extra_data.op("||")({"review_status": "disputed"}))
    )

    case_id = str(dispute.id)[:8].upper()

    reviewer = (await db.execute(
        select(User).where(User.id == review.reviewer_id)
    )).scalar_one_or_none()

    reviewer_notif_id = None
    if reviewer and await notification_pref_repo.is_enabled(db, reviewer.id, "in_app", "dispute_filed"):
        reviewer_notif = Notification(
            user_id=reviewer.id,
            notification_type="dispute_filed",
            title="A dispute has been filed on your review",
            body=f"The recipient has disputed your review. Case ID {case_id}.",
            extra_data={"review_id": str(review.id), "case_id": case_id, "role": "reviewer"},
        )
        db.add(reviewer_notif)
        await db.flush()
        reviewer_notif_id = str(reviewer_notif.id)

    recipient_notif_id = None
    if await notification_pref_repo.is_enabled(db, uuid.UUID(current_user["id"]), "in_app", "dispute_filed"):
        recipient_notif = Notification(
            user_id=uuid.UUID(current_user["id"]),
            notification_type="dispute_filed",
            title="Your dispute has been received",
            body=f"We've received your dispute and platform admins will review it. Case ID {case_id}.",
            extra_data={"review_id": str(review.id), "case_id": case_id, "role": "target"},
        )
        db.add(recipient_notif)
        await db.flush()
        recipient_notif_id = str(recipient_notif.id)

    await db.commit()

    if reviewer:
        send_email_task.delay(
            "dispute_filed",
            reviewer.email,
            {"case_id": case_id, "review_id": str(review.id), "role": "reviewer"},
            reviewer_notif_id,
            str(reviewer.id),
        )
    send_email_task.delay(
        "dispute_filed",
        current_user["email"],
        {"case_id": case_id, "review_id": str(review.id), "role": "target"},
        recipient_notif_id,
        current_user["id"],
    )

    return {
        "success": True,
        "message": "Dispute filed. Platform admins will review and mediate within 48 hours.",
        "data": {"dispute_id": str(dispute.id), "case_id": case_id},
    }


# ---------------------------------------------------------------------------
# POST /reviews/{review_id}/like  — toggle like on a review
# ---------------------------------------------------------------------------

@router.post("/{review_id}/like")
async def toggle_review_like(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    rid = uuid.UUID(review_id)
    uid = uuid.UUID(current_user["id"])

    review = await db.get(Review, rid)
    if not review or review.status != "verified":
        raise HTTPException(status_code=404, detail="Review not found")

    existing = (await db.execute(
        select(ReviewLike).where(ReviewLike.review_id == rid, ReviewLike.user_id == uid)
    )).scalar_one_or_none()

    if existing:
        await db.delete(existing)
        liked = False
    else:
        db.add(ReviewLike(review_id=rid, user_id=uid))
        liked = True

    await db.commit()
    if liked:
        notify_review_liked.delay(review_id, current_user["id"])
    return {"success": True, "message": "OK", "data": {"liked": liked}}


# ---------------------------------------------------------------------------
# GET /reviews/{review_id}/comments  — paginated comments with replies
# ---------------------------------------------------------------------------

@router.get("/{review_id}/comments")
async def get_review_comments(
    review_id: str,
    page: int = 1,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: dict | None = Depends(get_optional_user),
) -> dict:
    rid = uuid.UUID(review_id)
    uid = uuid.UUID(current_user["id"]) if current_user else None
    offset = (page - 1) * limit

    # Top-level comments only (no parent)
    total = (await db.execute(
        select(func.count(ReviewComment.id))
        .where(ReviewComment.review_id == rid, ReviewComment.parent_comment_id.is_(None), ReviewComment.status == "active")
    )).scalar_one()

    result = await db.execute(
        select(ReviewComment)
        .where(ReviewComment.review_id == rid, ReviewComment.parent_comment_id.is_(None), ReviewComment.status == "active")
        .options(
            selectinload(ReviewComment.author).selectinload(User.social_accounts),
            selectinload(ReviewComment.author).selectinload(User.organization).selectinload(Organization.profile),
            selectinload(ReviewComment.likes),
            selectinload(ReviewComment.replies).selectinload(ReviewComment.author).selectinload(User.social_accounts),
            selectinload(ReviewComment.replies).selectinload(ReviewComment.author).selectinload(User.organization).selectinload(Organization.profile),
            selectinload(ReviewComment.replies).selectinload(ReviewComment.likes),
        )
        .order_by(ReviewComment.created_at.asc())
        .offset(offset).limit(limit)
    )
    comments = result.scalars().all()

    def _ser_comment(c: ReviewComment, current_uid, depth: int = 0) -> dict:
        author = None
        if c.author:
            social = _reviewer_primary_social(
                getattr(c.author, "social_accounts", None) or []
            )
            profile = getattr(getattr(c.author, "organization", None), "profile", None)
            if profile and profile.profile_type == "creator":
                display_name = profile.handle or c.author.full_name or c.author.email
            elif profile and profile.display_name:
                display_name = profile.display_name
            else:
                display_name = c.author.full_name or c.author.email
            author = {
                "id": str(c.author.id),
                "name": display_name,
                "handle": profile.handle if profile else None,
                "social": social or None,
            }
        replies: list = []
        if depth == 0:
            replies = [_ser_comment(r, current_uid, depth=1) for r in (c.replies or []) if r.status == "active"]
        return {
            "id": str(c.id),
            "body": c.body,
            "like_count": len(c.likes),
            "liked_by_me": any(lk.user_id == current_uid for lk in c.likes) if current_uid else False,
            "created_at": c.created_at.isoformat(),
            "author": author,
            "replies": replies,
        }

    return {"success": True, "message": "OK", "data": {
        "items": [_ser_comment(c, uid) for c in comments],
        "total": total, "page": page, "limit": limit, "pages": -(-total // limit),
    }}


# ---------------------------------------------------------------------------
# POST /reviews/{review_id}/comments  — add a top-level comment
# ---------------------------------------------------------------------------

@router.post("/{review_id}/comments")
async def add_review_comment(
    review_id: str,
    payload: CommentIn,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    rid = uuid.UUID(review_id)
    review = await db.get(Review, rid)
    if not review or review.status != "verified":
        raise HTTPException(status_code=404, detail="Review not found")

    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="Comment body cannot be empty")

    comment = ReviewComment(
        review_id=rid,
        author_id=uuid.UUID(current_user["id"]),
        body=payload.body.strip()[:2000],
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    notify_new_comment.delay(str(comment.id))
    return {"success": True, "message": "Comment added.", "data": {"id": str(comment.id)}}


# ---------------------------------------------------------------------------
# POST /reviews/{review_id}/comments/{comment_id}/like  — toggle comment like
# ---------------------------------------------------------------------------

@router.post("/{review_id}/comments/{comment_id}/like")
async def toggle_comment_like(
    review_id: str,
    comment_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    cid = uuid.UUID(comment_id)
    uid = uuid.UUID(current_user["id"])

    comment = await db.get(ReviewComment, cid)
    if not comment or str(comment.review_id) != review_id or comment.status != "active":
        raise HTTPException(status_code=404, detail="Comment not found")

    existing = (await db.execute(
        select(CommentLike).where(CommentLike.comment_id == cid, CommentLike.user_id == uid)
    )).scalar_one_or_none()

    if existing:
        await db.delete(existing)
        liked = False
    else:
        db.add(CommentLike(comment_id=cid, user_id=uid))
        liked = True

    await db.commit()
    return {"success": True, "message": "OK", "data": {"liked": liked}}


# ---------------------------------------------------------------------------
# POST /reviews/{review_id}/comments/{comment_id}/reply  — reply to a comment
# ---------------------------------------------------------------------------

@router.post("/{review_id}/comments/{comment_id}/reply")
async def reply_to_comment(
    review_id: str,
    comment_id: str,
    payload: CommentIn,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    cid = uuid.UUID(comment_id)
    rid = uuid.UUID(review_id)

    comment = await db.get(ReviewComment, cid)
    if not comment or str(comment.review_id) != review_id or comment.status != "active":
        raise HTTPException(status_code=404, detail="Comment not found")

    if comment.parent_comment_id is not None:
        raise HTTPException(status_code=422, detail="Cannot reply to a reply")

    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="Reply body cannot be empty")

    reply = ReviewComment(
        review_id=rid,
        author_id=uuid.UUID(current_user["id"]),
        body=payload.body.strip()[:2000],
        parent_comment_id=cid,
    )
    db.add(reply)
    await db.commit()
    await db.refresh(reply)

    notify_comment_reply.delay(str(reply.id))

    return {"success": True, "message": "Reply added.", "data": {"id": str(reply.id)}}


# ---------------------------------------------------------------------------
# POST /reviews/{review_id}/reply  — official org response (1 per review)
# ---------------------------------------------------------------------------

@router.post("/{review_id}/reply")
async def post_official_reply(
    review_id: str,
    payload: ReplyIn,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_onboarded),
) -> dict:
    rid = uuid.UUID(review_id)
    org_id = uuid.UUID(current_user["org"]["id"])

    review = (await db.execute(
        select(Review).options(selectinload(Review.target_profile))
        .where(Review.id == rid)
    )).scalar_one_or_none()

    if not review or review.status != "verified":
        raise HTTPException(status_code=404, detail="Review not found")

    if str(review.target_profile.organization_id) != str(org_id):
        raise HTTPException(status_code=403, detail="Only the reviewed organisation can post an official reply")

    existing = (await db.execute(
        select(ReviewReply).where(ReviewReply.review_id == rid, ReviewReply.org_id == org_id)
    )).scalar_one_or_none()

    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="Reply body cannot be empty")

    if existing:
        existing.body = payload.body.strip()[:2000]
        existing.updated_at = datetime.now(UTC)
    else:
        db.add(ReviewReply(review_id=rid, org_id=org_id, body=payload.body.strip()[:2000]))

    await db.commit()
    return {"success": True, "message": "Official reply saved.", "data": {}}


# ---------------------------------------------------------------------------
# GET /reviews  (stub — filtered listing for admin / future use)
# ---------------------------------------------------------------------------

@router.get("")
async def list_reviews() -> dict:
    return {"success": True, "message": "TODO", "data": []}


# ---------------------------------------------------------------------------
# GET /reviews/mine  — reviews submitted by the current user (cursor pagination)
# ---------------------------------------------------------------------------

def _encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    payload = {"ca": created_at.isoformat(), "id": str(row_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(cursor: str):
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(payload["ca"]), uuid.UUID(payload["id"])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor") from None


@router.get("/mine")
async def my_reviews(
    cursor: str | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
) -> dict:
    limit = max(1, min(limit, 50))
    reviewer_id = uuid.UUID(current_user["id"])

    cursor_created_at, cursor_id = None, None
    if cursor:
        cursor_created_at, cursor_id = _decode_cursor(cursor)

    reviews = await get_my_reviews_cursor(
        db, reviewer_id, limit + 1, cursor_created_at, cursor_id
    )

    has_more = len(reviews) > limit
    page = reviews[:limit]

    next_cursor = (
        _encode_cursor(page[-1].created_at, page[-1].id)
        if has_more and page
        else None
    )

    items = []
    for rev in page:
        target = rev.target_profile
        scores = [r.score for r in rev.ratings] if rev.ratings else []
        avg = round(sum(scores) / len(scores), 2) if scores else None
        items.append({
            "id": str(rev.id),
            "relationship_type": rev.relationship_type,
            "body": rev.body,
            "avg_rating": avg,
            "ratings": [{"category": r.category, "score": r.score} for r in (rev.ratings or [])],
            "tags": [t.tag for t in (rev.tags or [])],
            "status": rev.status,
            "dispute_window_expires_at": (
                rev.dispute_window_expires_at.isoformat()
                if rev.dispute_window_expires_at else None
            ),
            "created_at": rev.created_at.isoformat(),
            "target": {
                "handle": target.handle if target else None,
                "display_name": target.display_name if target else None,
                "profile_type": target.profile_type if target else None,
                "avatar_url": target.avatar_url if target else None,
                "is_dummy": bool(target.is_dummy) if target else False,
            } if target else None,
        })

    return {
        "success": True,
        "message": "Reviews retrieved.",
        "data": {
            "items": items,
            "next_cursor": next_cursor,
            "limit": limit,
        },
    }
