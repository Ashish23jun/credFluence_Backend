import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    org_type: Mapped[str] = mapped_column(
        Enum("creator", "agency", "brand", name="org_type"),
        nullable=False,
        index=True,
    )

    # Verification
    verification_status: Mapped[str] = mapped_column(
        Enum("pending", "verified", "rejected", name="org_verification_status"),
        default="pending",
        nullable=False,
        index=True,
    )
    verification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    verification_docs: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # True for creator solo-orgs — hidden from UI
    is_personal_creator_org: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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
    domains: Mapped[list["OrganizationDomain"]] = relationship(
        "OrganizationDomain", back_populates="organization", cascade="all, delete-orphan"
    )
    memberships: Mapped[list["OrganizationMembership"]] = relationship(
        "OrganizationMembership", back_populates="organization", cascade="all, delete-orphan"
    )
    profile: Mapped["Profile"] = relationship(
        "Profile", back_populates="organization", uselist=False
    )
    users: Mapped[list["User"]] = relationship(
        "User", back_populates="organization"
    )
