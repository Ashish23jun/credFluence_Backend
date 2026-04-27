"""
Organization lifecycle service.

Handles org creation and membership for every signup path:
  - Creator   → always a fresh personal solo-org (1 user max, never domain-matched)
  - Agency/Brand → domain-matched; first user creates org+admin, rest join pending queue
"""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.models.organization_domain import OrganizationDomain
from app.models.organization_membership import OrganizationMembership
from app.models.profile import Profile
from app.models.user import User


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    slug = slug.strip("-")
    return slug[:100] or "org"


async def _unique_slug(db: AsyncSession, base: str) -> str:
    slug = base
    suffix = 1
    while True:
        result = await db.execute(select(Organization).where(Organization.slug == slug))
        if not result.scalar_one_or_none():
            return slug
        slug = f"{base}-{suffix}"
        suffix += 1


async def resolve_org_for_signup(
    db: AsyncSession,
    user: User,
    display_name: str,
) -> tuple[Organization, OrganizationMembership]:
    """
    Called inside /auth/verify-email (and OAuth signups) after the user row is created.

    Returns the (org, membership) pair. Caller must commit.
    The user.organization_id is set here before committing.
    """
    if user.role == "creator":
        return await _create_creator_org(db, user, display_name)
    return await _resolve_business_org(db, user)


async def _create_creator_org(
    db: AsyncSession,
    user: User,
    display_name: str,
) -> tuple[Organization, OrganizationMembership]:
    """Creator always gets a fresh private solo-org named after themselves."""
    base_slug = _slugify(display_name) or _slugify(user.email.split("@")[0])
    slug = await _unique_slug(db, base_slug)

    org = Organization(
        name=display_name,
        slug=slug,
        org_type="creator",
        is_personal_creator_org=True,
        verification_status="pending",
    )
    db.add(org)
    await db.flush()  # get org.id

    # Set organization_id on user, then flush user to get user.id
    user.organization_id = org.id
    db.add(user)
    await db.flush()  # get user.id

    # Profile seeded with placeholder data; user fills it in onboarding step 1
    profile = Profile(
        organization_id=org.id,
        display_name=display_name,
        profile_type="creator",
        trust_score=450,
        is_claimed=True,
        access_level="limited",
    )
    db.add(profile)

    membership = OrganizationMembership(
        organization_id=org.id,
        user_id=user.id,
        role="admin",
        status="active",
        approved_at=datetime.now(UTC),
    )
    db.add(membership)

    return org, membership


async def _resolve_business_org(
    db: AsyncSession,
    user: User,
) -> tuple[Organization, OrganizationMembership]:
    """
    Agency/brand: match by email domain.
      - No existing org → create org + admin membership.
      - Existing org → create pending member membership.
    """
    domain = user.email.split("@")[-1].lower()

    # Look up existing org by domain
    result = await db.execute(
        select(OrganizationDomain).where(OrganizationDomain.domain == domain)
    )
    org_domain = result.scalar_one_or_none()

    if org_domain:
        # Domain already claimed — join as pending member
        org_result = await db.execute(
            select(Organization).where(Organization.id == org_domain.organization_id)
        )
        org = org_result.scalar_one()

        user.organization_id = org.id
        db.add(user)
        await db.flush()  # get user.id

        membership = OrganizationMembership(
            organization_id=org.id,
            user_id=user.id,
            role="member",
            status="pending",
        )
        db.add(membership)
        return org, membership

    # First user on this domain — create org.
    # Always derive the placeholder name from the domain, never from the user's
    # personal name. The admin sets the real company name in onboarding step 1.
    org_name = domain.split(".")[0].title()
    base_slug = _slugify(org_name) or _slugify(domain.split(".")[0])
    slug = await _unique_slug(db, base_slug)

    org = Organization(
        name=org_name,
        slug=slug,
        org_type=user.role,
        is_personal_creator_org=False,
        verification_status="pending",
    )
    db.add(org)
    await db.flush()  # get org.id

    # Register domain
    org_domain_row = OrganizationDomain(
        organization_id=org.id,
        domain=domain,
        is_primary=True,
    )
    db.add(org_domain_row)

    # Placeholder profile
    profile = Profile(
        organization_id=org.id,
        display_name=org_name,
        profile_type=user.role,
        trust_score=450,
        is_claimed=True,
        access_level="limited",
    )
    db.add(profile)

    # Set organization_id on user, then flush user to get user.id
    user.organization_id = org.id
    db.add(user)
    await db.flush()  # get user.id

    # First user is admin, immediately active
    membership = OrganizationMembership(
        organization_id=org.id,
        user_id=user.id,
        role="admin",
        status="active",
        approved_at=datetime.now(UTC),
    )
    db.add(membership)

    return org, membership


async def approve_membership(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    approved_by: uuid.UUID,
) -> OrganizationMembership:
    result = await db.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user_id,
        )
    )
    membership = result.scalar_one()
    membership.status = "active"
    membership.approved_by_user_id = approved_by
    membership.approved_at = datetime.now(UTC)
    return membership


async def reject_membership(
    db: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    approved_by: uuid.UUID,
) -> OrganizationMembership:
    result = await db.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user_id,
        )
    )
    membership = result.scalar_one()
    membership.status = "rejected"
    membership.approved_by_user_id = approved_by
    membership.approved_at = datetime.now(UTC)
    return membership
