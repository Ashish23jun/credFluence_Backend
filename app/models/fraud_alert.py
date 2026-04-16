import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FraudAlert(Base):
    __tablename__ = "fraud_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    rule_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # e.g. "review_bombing", "coordinated_attack", "bot_signup", "brute_force_login", "self_review"

    severity: Mapped[str] = mapped_column(
        Enum("low", "medium", "high", "critical", name="fraud_severity"),
        nullable=False,
        index=True,
    )

    # Entities involved
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    target_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    # Evidence
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Auto-actions taken
    auto_actions_taken: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # e.g. ["quarantine_review", "reduce_trust_weight", "block_ip"]

    status: Mapped[str] = mapped_column(
        Enum("open", "investigating", "resolved", "false_positive", name="fraud_alert_status"),
        default="open",
        nullable=False,
        index=True,
    )

    resolved_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )
