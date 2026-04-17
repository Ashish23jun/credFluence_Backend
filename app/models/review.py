import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=False, index=True
    )
    target_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Relationship context
    relationship_type: Mapped[str] = mapped_column(
        Enum(
            "brand_worked_with_creator",
            "agency_worked_with_creator",
            "creator_worked_with_brand",
            "creator_worked_with_agency",
            name="relationship_type",
        ),
        nullable=False,
    )

    # Payment status
    payment_status: Mapped[str] = mapped_column(
        Enum("paid_on_time", "paid_late", "partially_paid", "unpaid", name="payment_status"),
        nullable=False,
    )

    # Ratings (1–5 each)
    rating_communication: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_professionalism: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_quality: Mapped[int] = mapped_column(Integer, nullable=False)
    rating_reliability: Mapped[int] = mapped_column(Integer, nullable=False)

    # Tags (max 3)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Evidence files (S3 keys, encrypted)
    evidence_keys: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # AI summary
    ai_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # OCR result
    ocr_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Lifecycle status
    status: Mapped[str] = mapped_column(
        Enum(
            "pending",
            "in_dispute_window",
            "disputed",
            "pending_verification",
            "verified",
            "rejected",
            "quarantined",
            name="review_status",
        ),
        default="in_dispute_window",
        nullable=False,
        index=True,
    )

    # Admin verification
    verified_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Dispute window expiry (set in Redis, but stored here for reference)
    dispute_window_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    reviewer: Mapped["User"] = relationship(
        "User", foreign_keys=[reviewer_id], back_populates="submitted_reviews"
    )
    target_profile: Mapped["Profile"] = relationship(
        "Profile", foreign_keys=[target_profile_id], back_populates="reviews_received"
    )
    dispute: Mapped["Dispute"] = relationship("Dispute", back_populates="review", uselist=False)
