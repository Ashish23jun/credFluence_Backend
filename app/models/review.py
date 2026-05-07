import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Enum, ForeignKey,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

_RATING_CATEGORIES = (
    "communication", "professionalism", "reliability",
    "quality", "brief_adherence", "timeline_adherence",
    "payment_behavior",
)
_FLAG_TYPES = (
    "ghosted", "missed_deadline", "scope_creep",
    "rude_behavior", "contract_violation",
    "payment_not_made", "payment_partial", "payment_refused",
    "payment_delayed", "invoice_disputed",
)
_EVIDENCE_TYPES = ("screenshot", "email", "contract", "invoice", "chat")
_TAG_VALUES = (
    "fast_payment", "delayed_payment",
    "excellent_communication", "poor_communication",
    "high_quality", "low_quality",
    "easy_to_work_with", "difficult_client",
    "clear_brief", "vague_brief",
    "long_term_client", "repeat_collaboration",
)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False, index=True,
    )
    target_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    relationship_type: Mapped[str] = mapped_column(
        Enum(
            "brand_worked_with_creator",
            "brand_worked_with_agency",
            "agency_worked_with_creator",
            "agency_worked_with_brand",
            "agency_worked_with_agency",
            "creator_worked_with_brand",
            "creator_worked_with_agency",
            "creator_worked_with_creator",
            name="relationship_type",
        ),
        nullable=False,
    )

    # Deal context
    total_deal_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)

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

    # Contact details provided by reviewer for verification
    contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Free-text review body written by the reviewer
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # AI summary
    ai_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ocr_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Admin verification
    verified_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    dispute_window_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
        nullable=False, index=True,
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
    payments: Mapped[list["ReviewPayment"]] = relationship(
        "ReviewPayment", back_populates="review", cascade="all, delete-orphan"
    )
    ratings: Mapped[list["ReviewRating"]] = relationship(
        "ReviewRating", back_populates="review", cascade="all, delete-orphan"
    )
    flags: Mapped[list["ReviewFlag"]] = relationship(
        "ReviewFlag", back_populates="review", cascade="all, delete-orphan"
    )
    evidence: Mapped[list["ReviewEvidence"]] = relationship(
        "ReviewEvidence", back_populates="review", cascade="all, delete-orphan"
    )
    tags: Mapped[list["ReviewTag"]] = relationship(
        "ReviewTag", back_populates="review", cascade="all, delete-orphan"
    )
    likes: Mapped[list["ReviewLike"]] = relationship(
        "ReviewLike", back_populates="review", cascade="all, delete-orphan"
    )
    comments: Mapped[list["ReviewComment"]] = relationship(
        "ReviewComment", back_populates="review", cascade="all, delete-orphan",
        foreign_keys="ReviewComment.review_id",
    )
    reply: Mapped["ReviewReply | None"] = relationship(
        "ReviewReply", back_populates="review", uselist=False, cascade="all, delete-orphan"
    )


class ReviewPayment(Base):
    """Financial layer — tracks real payment behaviour (source of truth for trust score)."""
    __tablename__ = "review_payments"
    __table_args__ = (
        CheckConstraint(
            "payment_type IN ('advance','milestone','final')",
            name="ck_review_payments_type",
        ),
        CheckConstraint(
            "status IN ('pending','paid','late')",
            name="ck_review_payments_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Store in smallest currency unit (paise / cents) to avoid float issues
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    payment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    due_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    proof_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    review: Mapped["Review"] = relationship("Review", back_populates="payments")


class ReviewRating(Base):
    """Behaviour layer — flexible per-category ratings (1–5)."""
    __tablename__ = "review_ratings"
    __table_args__ = (
        CheckConstraint(
            f"category IN {_RATING_CATEGORIES}",
            name="ck_review_ratings_category",
        ),
        CheckConstraint("score BETWEEN 1 AND 5", name="ck_review_ratings_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    review: Mapped["Review"] = relationship("Review", back_populates="ratings")


class ReviewFlag(Base):
    """Negative signals — high-impact issues that trigger fast penalties."""
    __tablename__ = "review_flags"
    __table_args__ = (
        CheckConstraint(
            f"type IN {_FLAG_TYPES}",
            name="ck_review_flags_type",
        ),
        CheckConstraint(
            "severity IN ('low','medium','high')",
            name="ck_review_flags_severity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    review: Mapped["Review"] = relationship("Review", back_populates="flags")


class ReviewEvidence(Base):
    """Proof layer — increases trustworthiness of the review."""
    __tablename__ = "review_evidence"
    __table_args__ = (
        CheckConstraint(
            f"type IN {_EVIDENCE_TYPES}",
            name="ck_review_evidence_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_key: Mapped[str] = mapped_column(String(512), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    review: Mapped["Review"] = relationship("Review", back_populates="evidence")


class ReviewTag(Base):
    """Quick semantic signals (max 5 per review)."""
    __tablename__ = "review_tags"
    __table_args__ = (
        CheckConstraint(
            f"tag IN {_TAG_VALUES}",
            name="ck_review_tags_tag",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    tag: Mapped[str] = mapped_column(String(50), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    review: Mapped["Review"] = relationship("Review", back_populates="tags")


class ReviewLike(Base):
    __tablename__ = "review_likes"
    __table_args__ = (
        UniqueConstraint("review_id", "user_id", name="uq_review_likes_review_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    review: Mapped["Review"] = relationship("Review", back_populates="likes")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])


class ReviewComment(Base):
    __tablename__ = "review_comments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','removed','flagged')",
            name="ck_review_comments_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
        nullable=False, index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_comments.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )

    review: Mapped["Review"] = relationship(
        "Review", back_populates="comments", foreign_keys=[review_id]
    )
    author: Mapped["User"] = relationship("User", foreign_keys=[author_id])
    parent_comment: Mapped["ReviewComment | None"] = relationship(
        "ReviewComment", foreign_keys=[parent_comment_id], remote_side="ReviewComment.id",
        back_populates="replies",
    )
    replies: Mapped[list["ReviewComment"]] = relationship(
        "ReviewComment", foreign_keys="ReviewComment.parent_comment_id",
        back_populates="parent_comment",
        cascade="all, delete-orphan",
    )
    likes: Mapped[list["CommentLike"]] = relationship(
        "CommentLike", back_populates="comment", cascade="all, delete-orphan"
    )


class ReviewReply(Base):
    __tablename__ = "review_replies"
    __table_args__ = (
        UniqueConstraint("review_id", "org_id", name="uq_review_reply"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    review: Mapped["Review"] = relationship("Review", back_populates="reply")
    org: Mapped["Organization"] = relationship("Organization")


class CommentLike(Base):
    __tablename__ = "comment_likes"
    __table_args__ = (
        UniqueConstraint("comment_id", "user_id", name="uq_comment_like"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("review_comments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    comment: Mapped["ReviewComment"] = relationship("ReviewComment", back_populates="likes")
    user: Mapped["User"] = relationship("User")
