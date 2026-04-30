"""Claim router.

Flow 1 — Seeded creator claim:
  GET  /claim/profile/{handle}   — preview (no auth needed)
  POST /claim/profile/{handle}   — match via connected Instagram, link user to seeded org

Flow 2 — Off-platform review claim:
  GET  /claim/review/{review_id} — preview review (no auth needed)
  POST /claim/review/{review_id} — link review to claimant's real profile, start dispute window
"""
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_delete, user_key
from app.core.database import get_db
from app.core.dependencies import get_current_user, require_onboarded
from app.models.notification import Notification
from app.models.organization_membership import OrganizationMembership
from app.models.profile import Profile
from app.models.review import Review
from app.models.social_account import SocialAccount
from app.models.user import User

router = APIRouter(prefix="/claim", tags=["claim"])

_DISPUTE_WINDOW_HOURS = 48


# ---------------------------------------------------------------------------
# GET /claim/review/{review_id}  — unauthenticated preview
# ---------------------------------------------------------------------------

@router.get("/review/{review_id}")
async def preview_review_claim(
    review_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return enough review data for the /claim page to render a preview."""
    try:
        rid = uuid.UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid review ID")

    review = await db.scalar(
        select(Review)
        .where(Review.id == rid)
        .options(
            selectinload(Review.target_profile),
            selectinload(Review.reviewer),
            selectinload(Review.ratings),
        )
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    target = review.target_profile
    if not target or not target.is_dummy:
        raise HTTPException(status_code=409, detail="This review is not available for claiming")

    if target.is_claimed:
        raise HTTPException(status_code=409, detail="This profile has already been claimed")

    reviewer = review.reviewer
    reviewer_name = reviewer.full_name or reviewer.email if reviewer else "Someone"

    return {
        "success": True,
        "message": "Review preview loaded",
        "data": {
            "review_id": str(review.id),
            "target_name": target.display_name,
            "target_type": target.profile_type,
            "reviewer_name": reviewer_name,
            "relationship": review.relationship_type,
            "body": review.body or "",
            "ratings": [
                {"category": r.category.replace("_", " ").title(), "score": r.score}
                for r in sorted(review.ratings, key=lambda x: x.category)
            ],
            "created_at": review.created_at.isoformat(),
        },
    }


# ---------------------------------------------------------------------------
# POST /claim/review/{review_id}  — authenticated claim
# ---------------------------------------------------------------------------

@router.post("/review/{review_id}")
async def claim_review_profile(
    review_id: str,
    current_user: dict = Depends(require_onboarded),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Link review to claimant's real profile and start the 48-hour dispute window."""
    try:
        rid = uuid.UUID(review_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid review ID")

    org = current_user.get("org")
    if not org:
        raise HTTPException(status_code=403, detail="Complete onboarding before claiming")

    # Load review + dummy target profile
    review = await db.scalar(
        select(Review)
        .where(Review.id == rid)
        .options(selectinload(Review.target_profile))
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    target = review.target_profile
    if not target or not target.is_dummy:
        raise HTTPException(status_code=409, detail="This review is not available for claiming")

    if target.is_claimed:
        raise HTTPException(status_code=409, detail="This profile has already been claimed")

    # Get claimant's real profile
    claimant_profile = await db.scalar(
        select(Profile).where(Profile.organization_id == uuid.UUID(org["id"]))
    )
    if not claimant_profile:
        raise HTTPException(status_code=403, detail="No profile found for your organisation")

    # Validate org types match
    if target.profile_type != claimant_profile.profile_type:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Review targets a {target.profile_type} but your profile is a "
                f"{claimant_profile.profile_type}"
            ),
        )

    # Redirect review to the real profile and open dispute window
    review.target_profile_id = claimant_profile.id
    review.dispute_window_expires_at = datetime.now(UTC) + timedelta(hours=_DISPUTE_WINDOW_HOURS)

    # In-app notification so the user can see the claimed review immediately
    notification = Notification(
        user_id=uuid.UUID(current_user["id"]),
        notification_type="review_received",
        title="A review has been linked to your profile",
        body=(
            f"You claimed a review. You have {_DISPUTE_WINDOW_HOURS} hours to file a "
            "dispute if the review is inaccurate."
        ),
        extra_data={"review_id": str(review.id)},
    )
    db.add(notification)

    await db.commit()

    return {
        "success": True,
        "message": "Profile claimed. Dispute window is now open.",
        "data": {
            "profile_handle": claimant_profile.handle,
            "dispute_window_expires_at": review.dispute_window_expires_at.isoformat(),
        },
    }


# ---------------------------------------------------------------------------
# GET /claim/profile/{handle}  — unauthenticated preview for seeded creator claim
# ---------------------------------------------------------------------------

@router.get("/profile/{handle}")
async def preview_profile_claim(
    handle: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return profile details so the claim page can show what will be claimed."""
    profile = await db.scalar(
        select(Profile).where(Profile.handle == handle.lower())
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if profile.is_claimed:
        raise HTTPException(status_code=409, detail="This profile has already been claimed")

    if profile.is_dummy:
        raise HTTPException(status_code=409, detail="This profile is not claimable via this route")

    # Extract instagram handle from social_links for display
    ig_handle = None
    if profile.social_links:
        for link in profile.social_links:
            if link.get("platform") == "instagram":
                ig_handle = link.get("handle")
                break

    return {
        "success": True,
        "message": "Profile preview loaded",
        "data": {
            "handle": profile.handle,
            "display_name": profile.display_name,
            "profile_type": profile.profile_type,
            "avatar_url": profile.avatar_url,
            "bio": profile.bio,
            "trust_score": profile.trust_score,
            "review_count": profile.review_count,
            "niches": profile.niches or [],
            "ig_handle": ig_handle,
            "is_claimed": profile.is_claimed,
        },
    }


# ---------------------------------------------------------------------------
# POST /claim/profile/{handle}  — authenticated seeded creator claim via Instagram match
# ---------------------------------------------------------------------------

@router.post("/profile/{handle}")
async def claim_seeded_profile(
    handle: str,
    current_user: dict = Depends(require_onboarded),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Verify Instagram ownership then link user to the seeded profile's org."""
    profile = await db.scalar(
        select(Profile)
        .where(Profile.handle == handle.lower())
        .options(selectinload(Profile.organization))
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if profile.is_claimed:
        raise HTTPException(status_code=409, detail="This profile has already been claimed")

    if profile.is_dummy:
        raise HTTPException(status_code=409, detail="This profile is not claimable via this route")

    # Check if user has a connected Instagram account with a matching handle
    user_id = uuid.UUID(current_user["id"])

    ig_accounts = await db.execute(
        select(SocialAccount).where(
            SocialAccount.user_id == user_id,
            SocialAccount.platform == "instagram",
        )
    )
    ig_accounts = ig_accounts.scalars().all()

    matched = any(
        (acc.username or "").lower().lstrip("@") == handle.lower()
        for acc in ig_accounts
    )
    if not matched:
        raise HTTPException(
            status_code=403,
            detail=(
                "No connected Instagram account matches this profile handle. "
                "Connect your Instagram account from Settings → Social accounts first."
            ),
        )

    # Add user as admin of the seeded profile's org
    seeded_org_id = profile.organization_id
    existing_membership = await db.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == seeded_org_id,
            OrganizationMembership.user_id == user_id,
        )
    )
    if not existing_membership:
        membership = OrganizationMembership(
            organization_id=seeded_org_id,
            user_id=user_id,
            role="admin",
            status="active",
        )
        db.add(membership)

    # Transfer user to the seeded org so get_current_user returns the right org
    user_row = await db.get(User, user_id)
    if user_row:
        user_row.organization_id = seeded_org_id

    # Mark profile as claimed
    profile.is_claimed = True

    await db.commit()

    # Bust the user cache so the next request picks up the new org
    await cache_delete(user_key(str(user_id)))

    return {
        "success": True,
        "message": "Profile claimed successfully. You are now an admin of this profile.",
        "data": {
            "handle": profile.handle,
            "display_name": profile.display_name,
            "org_id": str(seeded_org_id),
        },
    }
