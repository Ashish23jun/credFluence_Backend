import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Owned by Organization (1:1); UNIQUE enforced via unique=True
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Identity
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    handle: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Type and category
    profile_type: Mapped[str] = mapped_column(
        Enum("creator", "agency", "brand", name="profile_type"),
        nullable=False,
        index=True,
    )
    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Trust score (300–900); default 450, updated after verification
    trust_score: Mapped[int] = mapped_column(Integer, default=450, nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Niches / content verticals (array of strings e.g. ["fashion", "lifestyle"])
    niches: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Languages spoken (array of strings e.g. ["Hindi", "English"])
    languages: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # AI-generated summary tags
    ai_summary_tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Claim status
    is_claimed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_dummy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # GDPR opt-out
    is_opted_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Access level (limited = needs more platform verifications)
    access_level: Mapped[str] = mapped_column(
        Enum("full", "limited", name="access_level"),
        default="limited",
        nullable=False,
    )

    # Search vector (populated by trigger/migration)
    # search_vector is managed by PostgreSQL tsvector — added in migration

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization", back_populates="profile")
    reviews_received: Mapped[list["Review"]] = relationship(
        "Review", foreign_keys="Review.target_profile_id", back_populates="target_profile"
    )
    badges: Mapped[list["Badge"]] = relationship("Badge", back_populates="profile")
    tag_aggregations: Mapped[list["TagAggregation"]] = relationship(
        "TagAggregation", back_populates="profile"
    )
