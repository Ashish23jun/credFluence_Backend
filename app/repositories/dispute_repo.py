from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dispute import Dispute
from app.models.dispute_recipient import DisputeRecipient
from app.models.review import Review


async def get_review_by_id(db: AsyncSession, review_id) -> Review | None:
    result = await db.execute(select(Review).where(Review.id == review_id))
    return result.scalar_one_or_none()


async def create_dispute_with_recipient(
    db: AsyncSession,
    review_id,
    filed_by_user_id,
    dispute_type: str,
    reason: str,
    recipient_type: str,
    target_org_id=None,
) -> Dispute:
    dispute = Dispute(
        review_id=review_id,
        filed_by_user_id=filed_by_user_id,
        type=dispute_type,
        reason=reason,
        status="open",
    )
    db.add(dispute)
    await db.flush()

    db.add(DisputeRecipient(
        dispute_id=dispute.id,
        recipient_type=recipient_type,
        recipient_org_id=target_org_id,
    ))

    await db.commit()
    await db.refresh(dispute)
    return dispute


async def list_disputes_for_user(db: AsyncSession, user_id) -> list[Dispute]:
    result = await db.execute(
        select(Dispute).where(Dispute.filed_by_user_id == user_id)
    )
    return result.scalars().all()


async def list_disputes_for_org(db: AsyncSession, org_id) -> list[Dispute]:
    result = await db.execute(
        select(Dispute).join(DisputeRecipient).where(
            DisputeRecipient.recipient_type == "org_admin",
            DisputeRecipient.recipient_org_id == org_id,
        )
    )
    return result.scalars().all()
