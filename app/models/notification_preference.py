import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

PREF_CHANNELS = ("email", "in_app")

PREF_TYPES = (
    "review_received",
    "dispute_filed",
    "dispute_resolved",
    "review_verified",
    "review_rejected",
    "profile_claimed",
    "score_updated",
    "badge_earned",
)


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(
        Enum(*PREF_CHANNELS, name="notif_pref_channel"), nullable=False
    )
    type: Mapped[str] = mapped_column(
        Enum(*PREF_TYPES, name="notif_pref_type"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("user_id", "channel", "type", name="uq_notif_pref"),
        Index("ix_notif_pref_lookup", "user_id", "channel", "type"),
    )
