import uuid
from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
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

    # Payment reliability — core trust signal, feeds trust_score
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
    likes: Mapped[list["ReviewLike"]] = relationship(
        "ReviewLike", back_populates="review", cascade="all, delete-orphan"
    )
    comments: Mapped[list["ReviewComment"]] = relationship(
        "ReviewComment", back_populates="review", cascade="all, delete-orphan"
    )


class ReviewLike(Base):
    __tablename__ = "review_likes"
    __table_args__ = (
        UniqueConstraint("review_id", "user_id", name="uq_review_likes_review_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    review: Mapped["Review"] = relationship("Review", back_populates="likes")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])


class ReviewComment(Base):
    __tablename__ = "review_comments"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'removed', 'flagged')", name="ck_review_comments_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    review: Mapped["Review"] = relationship("Review", back_populates="comments")
    author: Mapped["User"] = relationship("User", foreign_keys=[author_id])
