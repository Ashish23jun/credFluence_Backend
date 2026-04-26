from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.organization import Organization
from app.models.organization_membership import OrganizationMembership
from app.models.profile import Profile


async def get_org_by_id(db: AsyncSession, org_id) -> Organization | None:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    return result.scalar_one_or_none()


async def get_profile_by_org_id(db: AsyncSession, org_id) -> Profile | None:
    result = await db.execute(
        select(Profile).where(Profile.organization_id == org_id)
    )
    return result.scalar_one_or_none()


async def get_org_with_detail(db: AsyncSession, org_id) -> Organization | None:
    """Org with domains + memberships (with users) + profile — used by admin detail."""
    result = await db.execute(
        select(Organization)
        .options(
            selectinload(Organization.domains),
            selectinload(Organization.memberships).selectinload(OrganizationMembership.user),
            selectinload(Organization.profile),
        )
        .where(Organization.id == org_id)
    )
    return result.scalar_one_or_none()


async def list_orgs_by_status(
    db: AsyncSession, status: str, org_type: str | None = None
) -> list[Organization]:
    query = (
        select(Organization)
        .options(
            selectinload(Organization.domains),
            selectinload(Organization.memberships).selectinload(OrganizationMembership.user),
        )
        .where(
            Organization.is_personal_creator_org == False,  # noqa: E712
            Organization.verification_status == status,
        )
        .order_by(Organization.created_at.asc())
    )
    if org_type and org_type in ("agency", "brand"):
        query = query.where(Organization.org_type == org_type)
    result = await db.execute(query)
    return result.scalars().all()


async def list_pending_memberships(
    db: AsyncSession, org_id
) -> list[OrganizationMembership]:
    result = await db.execute(
        select(OrganizationMembership)
        .options(selectinload(OrganizationMembership.user))
        .where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.status == "pending",
        )
    )
    return result.scalars().all()
