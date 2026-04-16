import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ActivityEvent(Base):
    __tablename__ = "activity_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # Event name e.g. "auth.login", "review.submitted", "profile.viewed"
    event_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Arbitrary event payload
    properties: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        index=True,
    )
    # Partitioned by created_at weekly — partition DDL added in migration
