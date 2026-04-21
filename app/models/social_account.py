import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SocialAccount(Base):
    __tablename__ = "social_accounts"

    __table_args__ = (
        # Same account can't be connected twice to the same user
        UniqueConstraint("user_id", "platform", "platform_account_id", name="uq_social_user_platform_account"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    platform: Mapped[str] = mapped_column(
        Enum("youtube", "instagram", "linkedin", name="social_platform"),
        nullable=False,
        index=True,
    )

    # External platform identifier (YouTube channel ID, Instagram user ID, LinkedIn sub)
    platform_account_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Display info
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)   # @handle
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Primary = shown on public profile; one primary per platform per user enforced in app logic
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Tokens (store as-is; encrypt at rest via column-level encryption or vault in prod)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Platform-specific stats snapshot (refreshed on login + daily Celery task)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="social_accounts")
