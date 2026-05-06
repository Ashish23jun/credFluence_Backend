"""Score engine tasks and dispute window checker."""
import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.cache import cache_set
from app.core.database import task_db_session
from app.models.notification import Notification
from app.models.organization_membership import OrganizationMembership
from app.models.profile import Profile
from app.models.review import Review
from app.models.score_history import ScoreHistory
from app.models.user import User
from app.repositories import notification_pref_repo
from app.repositories.profile_repo import get_leaderboard_profiles
from app.repositories.social_account_repo import get_accounts_by_org_ids
from app.services.profile_service import build_leaderboard_item
from app.services.score_engine import ReviewSignals, compute_new_trust_score
from app.tasks.celery_app import celery_app
from app.tasks.review_notifications import send_email_task

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Beat task: expire dispute windows every 5 min
# ──────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.score.check_dispute_windows")
def check_dispute_windows() -> None:
    asyncio.run(_check_dispute_windows())


@celery_app.task(name="app.tasks.score.refresh_leaderboard_cache")
def refresh_leaderboard_cache() -> None:
    asyncio.run(_refresh_leaderboard_cache())


# ──────────────────────────────────────────────────────────────────────────────
# On-demand task: recalculate one profile's trust score
# ──────────────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.score.recalculate_trust_score")
def recalculate_trust_score(profile_id: str) -> None:
    asyncio.run(_recalculate_trust_score(profile_id))


# ──────────────────────────────────────────────────────────────────────────────
# Async implementations
# ──────────────────────────────────────────────────────────────────────────────

async def _check_dispute_windows() -> None:
    async with task_db_session() as db:
        now = datetime.now(UTC)

        # Load expired reviews that are still in the window (not disputed)
        result = await db.execute(
            select(Review)
            .where(
                Review.status == "in_dispute_window",
                Review.dispute_window_expires_at <= now,
            )
            .options(
                selectinload(Review.target_profile).selectinload(Profile.organization),
                selectinload(Review.reviewer),
            )
        )
        expired = result.scalars().all()

        if not expired:
            return

        logger.info("check_dispute_windows: %d review(s) to verify", len(expired))

        for review in expired:
            review.status = "verified"
            review.verified_at = now

            target_profile = review.target_profile

            # Notify target org admins
            admin_rows = await db.execute(
                select(User)
                .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
                .where(
                    OrganizationMembership.organization_id == target_profile.organization_id,
                    OrganizationMembership.role == "admin",
                    OrganizationMembership.status == "active",
                )
            )
            admins = admin_rows.scalars().all()

            target_name = (
                target_profile.organization.name
                if target_profile.organization else "your organisation"
            )

            # Notify target org admins — flush each row to get its ID
            email_kwargs = {"target_name": target_name, "review_id": str(review.id), "role": "target"}
            for admin in admins:
                notif_id = None
                if await notification_pref_repo.is_enabled(db, admin.id, "in_app", "review_verified"):
                    notif = Notification(
                        user_id=admin.id,
                        notification_type="review_verified",
                        title="A review is now live",
                        body=f"A review for {target_name} passed the dispute window and is now public.",
                        extra_data={"review_id": str(review.id)},
                    )
                    db.add(notif)
                    await db.flush()
                    notif_id = str(notif.id)
                send_email_task.delay("review_live", admin.email, email_kwargs, notif_id, str(admin.id))

            # Notify the reviewer
            if review.reviewer_id:
                notif_id = None
                if await notification_pref_repo.is_enabled(db, review.reviewer_id, "in_app", "review_verified"):
                    reviewer_notif = Notification(
                        user_id=review.reviewer_id,
                        notification_type="review_verified",
                        title="Your review is now live",
                        body="The dispute window closed and your review is now publicly visible.",
                        extra_data={"review_id": str(review.id)},
                    )
                    db.add(reviewer_notif)
                    await db.flush()
                    notif_id = str(reviewer_notif.id)
                if review.reviewer and review.reviewer.email:
                    send_email_task.delay(
                        "review_live",
                        review.reviewer.email,
                        {"target_name": target_name, "review_id": str(review.id), "role": "reviewer"},
                        notif_id,
                        str(review.reviewer_id),
                    )

            recalculate_trust_score.delay(str(target_profile.id))

        await db.commit()


async def _recalculate_trust_score(profile_id: str) -> None:
    async with task_db_session() as db:
        pid = uuid.UUID(profile_id)

        profile = await db.get(Profile, pid)
        if not profile:
            logger.warning("recalculate_trust_score: profile %s not found", profile_id)
            return

        # Load all verified reviews for this profile (with signals)
        result = await db.execute(
            select(Review)
            .where(
                Review.target_profile_id == pid,
                Review.status == "verified",
            )
            .options(
                selectinload(Review.ratings),
                selectinload(Review.payments),
                selectinload(Review.flags),
                selectinload(Review.evidence),
            )
        )
        reviews = result.scalars().all()

        if not reviews:
            return

        # Walk every verified review and blend the score
        current_score = profile.trust_score
        for i, review in enumerate(reviews):
            signals = ReviewSignals(
                ratings={r.category: r.score for r in review.ratings},
                payments=[{"status": p.status} for p in review.payments],
                flags=[{"type": f.type, "severity": f.severity} for f in review.flags],
                evidence=[{"verified": e.verified} for e in review.evidence],
            )
            current_score = compute_new_trust_score(current_score, i, signals)

        profile.trust_score = current_score
        profile.review_count = len(reviews)
        db.add(ScoreHistory(
            profile_id=pid,
            score=current_score,
            review_count=len(reviews),
            reason="review_verified",
        ))
        await db.commit()

        logger.info(
            "recalculate_trust_score: profile %s → score=%d reviews=%d",
            profile_id, current_score, len(reviews),
        )


async def _refresh_leaderboard_cache() -> None:
    roles = [None, "creator", "agency", "brand"]
    limit = 20
    async with task_db_session() as db:
        for role in roles:
            profiles = await get_leaderboard_profiles(db, role, None, limit)
            org_ids = [p.organization_id for p in profiles]
            org_sa_map = await get_accounts_by_org_ids(db, org_ids)
            items = [
                build_leaderboard_item(p, p.organization, org_sa_map.get(str(p.organization_id), []))
                for p in profiles
            ]
            cache_key = f"leaderboard:{role or 'all'}:all:{limit}"
            await cache_set(cache_key, {"success": True, "message": "OK", "data": items}, ttl_seconds=300)
