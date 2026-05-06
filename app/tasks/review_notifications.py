"""Review lifecycle notification orchestrator.

Fans out a single business event (a review was submitted) into the three
channels we care about: in-app notifications, email, and (future) WhatsApp.
Each channel has its own retry envelope so a slow email provider never blocks
a WhatsApp send and vice-versa.
"""
import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import task_db_session
from app.core.email import (
    send_dispute_filed_email,
    send_dispute_resolved_email,
    send_platform_admin_alert_email,
    send_review_live_email,
    send_review_received_email,
    send_review_received_invite_email,
)
from app.models.notification import Notification
from app.models.organization_membership import OrganizationMembership
from app.models.platform_admin import PlatformAdmin
from app.models.profile import Profile
from app.models.review import Review
from app.models.user import User
from app.repositories import notification_pref_repo
from app.tasks.celery_app import celery_app

_REL_LABEL = {
    "brand_worked_with_creator":  "Brand → Creator",
    "brand_worked_with_agency":   "Brand → Agency",
    "agency_worked_with_creator": "Agency → Creator",
    "agency_worked_with_brand":   "Agency → Brand",
    "agency_worked_with_agency":  "Agency → Agency",
    "creator_worked_with_brand":  "Creator → Brand",
    "creator_worked_with_agency": "Creator → Agency",
    "creator_worked_with_creator":"Creator → Creator",
}

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Celery entry points (sync wrappers around async work)
# ──────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.review_notifications.notify_review_submitted")
def notify_review_submitted(review_id: str) -> None:
    """Triggered after `POST /reviews` commits. Fanout orchestrator."""
    asyncio.run(_notify_review_submitted(review_id))


@celery_app.task(name="app.tasks.review_notifications.notify_review_verified")
def notify_review_verified(review_id: str) -> None:
    """Triggered after recipient accepts a review. Creates in-app notification + email for reviewer."""
    asyncio.run(_notify_review_verified(review_id))


@celery_app.task(
    name="app.tasks.review_notifications.send_email_task",
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def send_email_task(
    kind: str,
    to_email: str,
    kwargs: dict,
    notification_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Generic email subtask.

    `notification_id`: if set, marks `email_sent = True` on the linked
    Notification row after successful delivery.
    `user_id`: if set, checks the user's email preference before sending.
    """
    asyncio.run(_dispatch_email(kind, to_email, kwargs, notification_id, user_id))


# ──────────────────────────────────────────────────────────────────────────────
# Async implementations
# ──────────────────────────────────────────────────────────────────────────────

def _reviewer_social(reviewer: User) -> dict:
    """Pick the platform with the highest follower count as primary handle."""
    result: dict = {"email": reviewer.email}

    candidates: list[tuple[int, str, str, str]] = []  # (followers, platform, handle, display)
    for acct in (reviewer.social_accounts or []):
        if not acct.username:
            continue
        stats = acct.stats or {}
        if acct.platform == "instagram":
            followers = int(stats.get("followers_count") or 0)
            candidates.append((followers, "instagram", acct.username, acct.display_name or acct.username))
        elif acct.platform == "youtube":
            followers = int(stats.get("subscribers") or 0)
            handle = stats.get("youtube_handle") or acct.username
            candidates.append((followers, "youtube", handle, acct.display_name or handle))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        top_followers, top_platform, top_handle, top_display = candidates[0]
        result[top_platform] = top_handle
        result["primary_platform"] = top_platform
        result["primary_handle"] = top_handle
        result["primary_display"] = top_display
        result["primary_followers"] = top_followers

    return result


def _build_review_snapshot(review: Review, reviewer_name: str) -> dict:
    """Serialise all review data into a JSON-safe dict for emails + notification extra_data."""
    return {
        "review_id": str(review.id),
        "reviewer_name": reviewer_name,
        "reviewer_contact": _reviewer_social(review.reviewer) if review.reviewer else {},
        "relationship": _REL_LABEL.get(review.relationship_type, review.relationship_type),
        "body": review.body or "",
        "total_deal_value": review.total_deal_value,
        "currency": review.currency,
        "ratings": [
            {"category": r.category.replace("_", " ").title(), "score": r.score}
            for r in sorted(review.ratings, key=lambda r: r.category)
        ],
        "payments": [
            {
                "type": p.payment_type,
                "amount_rupees": round(p.amount / 100, 2),
                "currency": p.currency,
                "status": p.status,
            }
            for p in review.payments
        ],
        "flags": [
            {"type": f.type.replace("_", " ").title(), "severity": f.severity}
            for f in review.flags
        ],
        "tags": [t.tag.replace("_", "-") for t in review.tags],
        "review_status": review.status,
        "evidence_count": len(review.evidence),
        "evidence": [
            {
                "id": str(e.id),
                "type": e.type,
                "filename": e.file_key.rsplit("/", 1)[-1],
                "file_key": e.file_key,
            }
            for e in review.evidence
        ],
    }


async def _notify_review_verified(review_id: str) -> None:
    """Create in-app Notification for the reviewer + email once a review goes verified."""
    async with task_db_session() as db:
        review = await db.scalar(
            select(Review)
            .where(Review.id == uuid.UUID(review_id))
            .options(
                selectinload(Review.target_profile).selectinload(Profile.organization),
                selectinload(Review.reviewer),
            )
        )
        if not review or not review.reviewer:
            logger.warning("notify_review_verified: review %s not found or has no reviewer", review_id)
            return

        reviewer = review.reviewer
        target_name = (
            review.target_profile.organization.name
            if review.target_profile and review.target_profile.organization
            else "the profile"
        )

        notif_id = None
        if await notification_pref_repo.is_enabled(db, reviewer.id, "in_app", "review_verified"):
            notification = Notification(
                user_id=reviewer.id,
                notification_type="review_verified",
                title="Your review is now live",
                body=f"Your review for {target_name} has been accepted and is now publicly visible.",
                extra_data={"review_id": str(review.id), "target_name": target_name},
            )
            db.add(notification)
            await db.flush()
            notif_id = str(notification.id)

        send_email_task.delay(
            "review_live",
            reviewer.email,
            {"target_name": target_name, "review_id": str(review.id), "role": "reviewer"},
            notif_id,
            str(reviewer.id),
        )

        await db.commit()


async def _notify_review_submitted(review_id: str) -> None:
    async with task_db_session() as db:
        review = await db.scalar(
            select(Review)
            .where(Review.id == uuid.UUID(review_id))
            .options(
                selectinload(Review.target_profile).selectinload(Profile.organization),
                selectinload(Review.reviewer).selectinload(User.organization),
                selectinload(Review.reviewer).selectinload(User.social_accounts),
                selectinload(Review.ratings),
                selectinload(Review.payments),
                selectinload(Review.flags),
                selectinload(Review.tags),
                selectinload(Review.evidence),
            )
        )
        if not review:
            logger.warning("notify_review_submitted: review %s not found", review_id)
            return

        target_profile = review.target_profile
        reviewer = review.reviewer
        reviewer_name = (
            (reviewer.organization.name if reviewer.organization else None)
            or reviewer.full_name
            or reviewer.email
        )

        snapshot = _build_review_snapshot(review, reviewer_name)

        if target_profile.is_dummy:
            await _handle_off_platform(db, review, target_profile, reviewer_name, snapshot)
        else:
            await _handle_on_platform(db, review, target_profile, reviewer_name, snapshot)

        await db.commit()


async def _handle_on_platform(
    db,
    review: Review,
    target_profile: Profile,
    reviewer_name: str,
    snapshot: dict,
) -> None:
    """Target org has admins → create in-app Notification rows + dispatch email per admin."""
    rows = await db.execute(
        select(User)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(
            OrganizationMembership.organization_id == target_profile.organization_id,
            OrganizationMembership.role == "admin",
            OrganizationMembership.status == "active",
        )
    )
    admins = rows.scalars().all()

    if not admins:
        logger.info("review %s: target org has no approved admins, skipping", review.id)
        return

    summary = snapshot.get("body", "")[:120] or "No written comment."

    for admin in admins:
        notif_id = None
        if await notification_pref_repo.is_enabled(db, admin.id, "in_app", "review_received"):
            notification = Notification(
                user_id=admin.id,
                notification_type="review_received",
                title="You received a new review",
                body=f"{reviewer_name} left a review for your organisation. {summary}",
                extra_data=snapshot,
            )
            db.add(notification)
            await db.flush()
            notif_id = str(notification.id)

        send_email_task.delay(
            "review_received",
            admin.email,
            {"reviewer_name": reviewer_name, "review_id": str(review.id), "snapshot": snapshot},
            notif_id,
            str(admin.id),
        )


async def _handle_off_platform(
    db,
    review: Review,
    target_profile: Profile,
    reviewer_name: str,
    snapshot: dict,
) -> None:
    """Target not on platform → invite email + platform admin alert (no in-app rows)."""
    target_name = target_profile.organization.name if target_profile.organization else "Unknown"

    if review.contact_email:
        claim_url = f"{settings.frontend_url}/claim?review={review.id}"
        send_email_task.delay(
            "review_received_invite",
            review.contact_email,
            {
                "reviewer_name": reviewer_name,
                "review_id": str(review.id),
                "claim_url": claim_url,
                "snapshot": snapshot,
            },
        )

    rows = await db.execute(select(PlatformAdmin))
    for admin in rows.scalars().all():
        send_email_task.delay(
            "platform_admin_alert",
            admin.email,
            {
                "target_name": target_name,
                "contact_email": review.contact_email or "(not provided)",
                "review_id": str(review.id),
            },
        )


# ──────────────────────────────────────────────────────────────────────────────
# Email dispatcher — routes "kind" → template fn, then marks email_sent in DB
# ──────────────────────────────────────────────────────────────────────────────

_KIND_TO_PREF: dict[str, tuple[str, str]] = {
    "review_received":  ("email", "review_received"),
    "review_live":      ("email", "review_verified"),
    "dispute_filed":    ("email", "dispute_filed"),
    "dispute_resolved": ("email", "dispute_resolved"),
}


async def _dispatch_email(
    kind: str,
    to_email: str,
    kwargs: dict,
    notification_id: str | None,
    user_id: str | None = None,
) -> None:
    if user_id and kind in _KIND_TO_PREF:
        channel, type_ = _KIND_TO_PREF[kind]
        async with task_db_session() as db:
            enabled = await notification_pref_repo.is_enabled(db, uuid.UUID(user_id), channel, type_)
        if not enabled:
            logger.info("email suppressed by preference: user=%s kind=%s", user_id, kind)
            return

    if kind == "review_received":
        await send_review_received_email(
            to_email,
            reviewer_name=kwargs["reviewer_name"],
            review_id=kwargs["review_id"],
            snapshot=kwargs.get("snapshot", {}),
        )
    elif kind == "review_received_invite":
        await send_review_received_invite_email(
            to_email,
            reviewer_name=kwargs["reviewer_name"],
            review_id=kwargs["review_id"],
            claim_url=kwargs["claim_url"],
            snapshot=kwargs.get("snapshot", {}),
        )
    elif kind == "platform_admin_alert":
        await send_platform_admin_alert_email(to_email, **kwargs)
    elif kind == "review_live":
        await send_review_live_email(to_email, **kwargs)
    elif kind == "dispute_resolved":
        await send_dispute_resolved_email(to_email, **kwargs)
    elif kind == "dispute_filed":
        await send_dispute_filed_email(to_email, **kwargs)
    else:
        logger.error("unknown email kind: %s", kind)
        return  # don't mark email_sent on unknown kinds

    # Email delivered — update the linked Notification row if one exists
    if notification_id:
        async with task_db_session() as db:
            notif = await db.get(Notification, uuid.UUID(notification_id))
            if notif:
                notif.email_sent = True
                await db.commit()
