"""initial schema

Revision ID: 0001_init
Revises:
Create Date: 2026-05-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- ENUMs ---
    op.execute("CREATE TYPE user_role AS ENUM ('creator', 'agency', 'brand')")
    op.execute("CREATE TYPE subscription_tier AS ENUM ('free', 'premium')")
    op.execute("CREATE TYPE org_type AS ENUM ('creator', 'agency', 'brand')")
    op.execute("CREATE TYPE org_verification_status AS ENUM ('pending', 'verified', 'rejected')")
    op.execute("CREATE TYPE profile_type AS ENUM ('creator', 'agency', 'brand')")
    op.execute("CREATE TYPE access_level AS ENUM ('full', 'limited')")
    op.execute("CREATE TYPE social_platform AS ENUM ('youtube', 'instagram', 'linkedin')")
    op.execute("CREATE TYPE membership_role AS ENUM ('admin', 'member')")
    op.execute("CREATE TYPE membership_status AS ENUM ('pending', 'active', 'rejected')")
    op.execute(
        "CREATE TYPE relationship_type AS ENUM ("
        "'brand_worked_with_creator','brand_worked_with_agency',"
        "'agency_worked_with_creator','agency_worked_with_brand',"
        "'agency_worked_with_agency','creator_worked_with_brand',"
        "'creator_worked_with_agency','creator_worked_with_creator')"
    )
    op.execute(
        "CREATE TYPE review_status AS ENUM ("
        "'pending','in_dispute_window','disputed','pending_verification',"
        "'verified','rejected','quarantined')"
    )
    op.execute(
        "CREATE TYPE dispute_type AS ENUM ("
        "'verification','duplicate_name','false_claim','recipient_dispute')"
    )
    op.execute(
        "CREATE TYPE dispute_status AS ENUM ("
        "'open','investigating','resolved_in_favor','resolved_rejected')"
    )
    op.execute(
        "CREATE TYPE dispute_recipient_type AS ENUM ('platform_admin', 'org_admin')"
    )
    op.execute(
        "CREATE TYPE notification_type AS ENUM ("
        "'review_received','dispute_window_expiring','dispute_filed','dispute_resolved',"
        "'review_verified','review_rejected','profile_claimed','score_updated',"
        "'badge_earned','comment_reply','new_comment','review_liked','member_request')"
    )
    op.execute("CREATE TYPE notif_pref_channel AS ENUM ('email', 'in_app')")
    op.execute(
        "CREATE TYPE notif_pref_type AS ENUM ("
        "'review_received','dispute_filed','dispute_resolved','review_verified',"
        "'review_rejected','profile_claimed','score_updated','badge_earned',"
        "'comment_reply','new_comment','review_liked','member_request')"
    )
    op.execute(
        "CREATE TYPE fraud_severity AS ENUM ('low', 'medium', 'high', 'critical')"
    )
    op.execute(
        "CREATE TYPE fraud_alert_status AS ENUM ('open', 'investigating', 'resolved', 'false_positive')"
    )

    # --- organizations ---
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("org_type", sa.Enum("creator", "agency", "brand", name="org_type", create_type=False), nullable=False),
        sa.Column("verification_status", sa.Enum("pending", "verified", "rejected", name="org_verification_status", create_type=False), nullable=False, server_default="pending"),
        sa.Column("verification_notes", sa.Text, nullable=True),
        sa.Column("rejected_reason", sa.Text, nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("verification_docs", postgresql.JSONB, nullable=True),
        sa.Column("is_personal_creator_org", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)
    op.create_index("ix_organizations_org_type", "organizations", ["org_type"])
    op.create_index("ix_organizations_verification_status", "organizations", ["verification_status"])

    # --- platform_admins ---
    op.create_table(
        "platform_admins",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_platform_admins_email", "platform_admins", ["email"], unique=True)

    # --- activity_events ---
    op.create_table(
        "activity_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.String(255), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("event_name", sa.String(100), nullable=False),
        sa.Column("properties", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_activity_events_user_id", "activity_events", ["user_id"])
    op.create_index("ix_activity_events_session_id", "activity_events", ["session_id"])
    op.create_index("ix_activity_events_event_name", "activity_events", ["event_name"])
    op.create_index("ix_activity_events_created_at", "activity_events", ["created_at"])

    # --- fraud_alerts ---
    op.create_table(
        "fraud_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_name", sa.String(100), nullable=False),
        sa.Column("severity", sa.Enum("low", "medium", "high", "critical", name="fraud_severity", create_type=False), nullable=False),
        sa.Column("target_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("evidence", postgresql.JSONB, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("auto_actions_taken", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.Enum("open", "investigating", "resolved", "false_positive", name="fraud_alert_status", create_type=False), nullable=False, server_default="open"),
        sa.Column("resolved_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fraud_alerts_rule_name", "fraud_alerts", ["rule_name"])
    op.create_index("ix_fraud_alerts_severity", "fraud_alerts", ["severity"])
    op.create_index("ix_fraud_alerts_target_user_id", "fraud_alerts", ["target_user_id"])
    op.create_index("ix_fraud_alerts_status", "fraud_alerts", ["status"])
    op.create_index("ix_fraud_alerts_created_at", "fraud_alerts", ["created_at"])

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("hashed_password", sa.String(255), nullable=True),
        sa.Column("role", sa.Enum("creator", "agency", "brand", name="user_role", create_type=False), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_verified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("google_id", sa.String(255), nullable=True),
        sa.Column("linkedin_id", sa.String(255), nullable=True),
        sa.Column("instagram_id", sa.String(255), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone_encrypted", sa.Text, nullable=True),
        sa.Column("trust_weight", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("subscription_tier", sa.Enum("free", "premium", name="subscription_tier", create_type=False), nullable=False, server_default="free"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("google_id"),
        sa.UniqueConstraint("linkedin_id"),
        sa.UniqueConstraint("instagram_id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    # --- organization_domains ---
    op.create_table(
        "organization_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain"),
    )
    op.create_index("ix_organization_domains_organization_id", "organization_domains", ["organization_id"])
    op.create_index("ix_organization_domains_domain", "organization_domains", ["domain"], unique=True)

    # --- organization_memberships ---
    op.create_table(
        "organization_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Enum("admin", "member", name="membership_role", create_type=False), nullable=False, server_default="member"),
        sa.Column("status", sa.Enum("pending", "active", "rejected", name="membership_status", create_type=False), nullable=False, server_default="pending"),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_membership"),
    )
    op.create_index("ix_organization_memberships_organization_id", "organization_memberships", ["organization_id"])
    op.create_index("ix_organization_memberships_user_id", "organization_memberships", ["user_id"])
    op.create_index("ix_organization_memberships_status", "organization_memberships", ["status"])

    # --- profiles ---
    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("handle", sa.String(100), nullable=True),
        sa.Column("avatar_url", sa.Text, nullable=True),
        sa.Column("bio", sa.Text, nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("profile_type", sa.Enum("creator", "agency", "brand", name="profile_type", create_type=False), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("trust_score", sa.Integer, nullable=False, server_default="45"),
        sa.Column("review_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("niches", postgresql.JSONB, nullable=True),
        sa.Column("languages", postgresql.JSONB, nullable=True),
        sa.Column("ai_summary_tags", postgresql.JSONB, nullable=True),
        sa.Column("social_links", postgresql.JSONB, nullable=True),
        sa.Column("is_claimed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_dummy", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_opted_out", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("access_level", sa.Enum("full", "limited", name="access_level", create_type=False), nullable=False, server_default="limited"),
        sa.Column("search_vector", postgresql.TSVECTOR, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id"),
        sa.UniqueConstraint("handle"),
    )
    op.create_index("ix_profiles_organization_id", "profiles", ["organization_id"], unique=True)
    op.create_index("ix_profiles_handle", "profiles", ["handle"], unique=True)
    op.create_index("ix_profiles_profile_type", "profiles", ["profile_type"])
    op.create_index("ix_profiles_category", "profiles", ["category"])
    op.create_index("ix_profiles_search_vector", "profiles", ["search_vector"], postgresql_using="gin")

    # tsvector trigger
    op.execute("""
        CREATE OR REPLACE FUNCTION profiles_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', coalesce(NEW.display_name, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.handle, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(NEW.bio, '')), 'C') ||
                setweight(to_tsvector('english', coalesce(NEW.category, '')), 'C') ||
                setweight(to_tsvector('english', coalesce(NEW.location, '')), 'D');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER profiles_search_vector_trigger
        BEFORE INSERT OR UPDATE ON profiles
        FOR EACH ROW EXECUTE FUNCTION profiles_search_vector_update();
    """)

    # --- social_accounts ---
    op.create_table(
        "social_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.Enum("youtube", "instagram", "linkedin", name="social_platform", create_type=False), nullable=False),
        sa.Column("platform_account_id", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("avatar_url", sa.Text, nullable=True),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("access_token", sa.Text, nullable=True),
        sa.Column("refresh_token", sa.Text, nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", postgresql.JSONB, nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "platform", "platform_account_id", name="uq_social_user_platform_account"),
    )
    op.create_index("ix_social_accounts_user_id", "social_accounts", ["user_id"])
    op.create_index("ix_social_accounts_platform", "social_accounts", ["platform"])

    # --- reviews ---
    op.create_table(
        "reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relationship_type", sa.Enum(
            "brand_worked_with_creator", "brand_worked_with_agency",
            "agency_worked_with_creator", "agency_worked_with_brand",
            "agency_worked_with_agency", "creator_worked_with_brand",
            "creator_worked_with_agency", "creator_worked_with_creator",
            name="relationship_type", create_type=False,
        ), nullable=False),
        sa.Column("total_deal_value", sa.Integer, nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("status", sa.Enum(
            "pending", "in_dispute_window", "disputed", "pending_verification",
            "verified", "rejected", "quarantined",
            name="review_status", create_type=False,
        ), nullable=False, server_default="in_dispute_window"),
        sa.Column("contact_email", sa.String(254), nullable=True),
        sa.Column("contact_phone", sa.String(30), nullable=True),
        sa.Column("body", sa.Text, nullable=True),
        sa.Column("ai_summary", postgresql.JSONB, nullable=True),
        sa.Column("ocr_result", postgresql.JSONB, nullable=True),
        sa.Column("verified_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("admin_notes", sa.Text, nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispute_window_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reviews_reviewer_id", "reviews", ["reviewer_id"])
    op.create_index("ix_reviews_target_profile_id", "reviews", ["target_profile_id"])
    op.create_index("ix_reviews_status", "reviews", ["status"])
    op.create_index("ix_reviews_created_at", "reviews", ["created_at"])

    # --- review_payments ---
    op.create_table(
        "review_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("payment_type", sa.String(20), nullable=False),
        sa.Column("due_date", sa.Date, nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("proof_key", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("payment_type IN ('advance','milestone','final')", name="ck_review_payments_type"),
        sa.CheckConstraint("status IN ('pending','paid','late')", name="ck_review_payments_status"),
    )
    op.create_index("ix_review_payments_review_id", "review_payments", ["review_id"])

    # --- review_ratings ---
    op.create_table(
        "review_ratings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "category IN ('communication','professionalism','reliability','quality','brief_adherence','timeline_adherence','payment_behavior')",
            name="ck_review_ratings_category",
        ),
        sa.CheckConstraint("score BETWEEN 1 AND 5", name="ck_review_ratings_score"),
    )
    op.create_index("ix_review_ratings_review_id", "review_ratings", ["review_id"])

    # --- review_flags ---
    op.create_table(
        "review_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "type IN ('ghosted','missed_deadline','scope_creep','rude_behavior','contract_violation',"
            "'payment_not_made','payment_partial','payment_refused','payment_delayed','invoice_disputed')",
            name="ck_review_flags_type",
        ),
        sa.CheckConstraint("severity IN ('low','medium','high')", name="ck_review_flags_severity"),
    )
    op.create_index("ix_review_flags_review_id", "review_flags", ["review_id"])

    # --- review_evidence ---
    op.create_table(
        "review_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("file_key", sa.String(512), nullable=False),
        sa.Column("verified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "type IN ('screenshot','email','contract','invoice','chat')",
            name="ck_review_evidence_type",
        ),
    )
    op.create_index("ix_review_evidence_review_id", "review_evidence", ["review_id"])

    # --- review_tags ---
    op.create_table(
        "review_tags",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "tag IN ('fast_payment','delayed_payment','excellent_communication','poor_communication',"
            "'high_quality','low_quality','easy_to_work_with','difficult_client',"
            "'clear_brief','vague_brief','long_term_client','repeat_collaboration')",
            name="ck_review_tags_tag",
        ),
    )
    op.create_index("ix_review_tags_review_id", "review_tags", ["review_id"])

    # --- review_likes ---
    op.create_table(
        "review_likes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", "user_id", name="uq_review_likes_review_user"),
    )
    op.create_index("ix_review_likes_review_id", "review_likes", ["review_id"])
    op.create_index("ix_review_likes_user_id", "review_likes", ["user_id"])

    # --- review_comments ---
    op.create_table(
        "review_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("parent_comment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_comment_id"], ["review_comments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('active','removed','flagged')", name="ck_review_comments_status"),
    )
    op.create_index("ix_review_comments_review_id", "review_comments", ["review_id"])
    op.create_index("ix_review_comments_author_id", "review_comments", ["author_id"])
    op.create_index("ix_review_comments_parent_comment_id", "review_comments", ["parent_comment_id"])
    op.create_index("ix_review_comments_created_at", "review_comments", ["created_at"])

    # --- review_replies ---
    op.create_table(
        "review_replies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", "org_id", name="uq_review_reply"),
    )
    op.create_index("ix_review_replies_review_id", "review_replies", ["review_id"])

    # --- comment_likes ---
    op.create_table(
        "comment_likes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("comment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["comment_id"], ["review_comments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("comment_id", "user_id", name="uq_comment_like"),
    )
    op.create_index("ix_comment_likes_comment_id", "comment_likes", ["comment_id"])

    # --- disputes ---
    op.create_table(
        "disputes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filed_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Enum("verification", "duplicate_name", "false_claim", "recipient_dispute", name="dispute_type", create_type=False), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("counter_evidence_keys", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.Enum("open", "investigating", "resolved_in_favor", "resolved_rejected", name="dispute_status", create_type=False), nullable=False, server_default="open"),
        sa.Column("outcome", sa.String(30), nullable=True),
        sa.Column("resolved_by_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolution_notes", sa.Text, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["review_id"], ["reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["filed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id"),
        sa.CheckConstraint(
            "outcome IN ('reviewer_won','target_won','mutual_resolution')",
            name="ck_disputes_outcome",
        ),
    )
    op.create_index("ix_disputes_review_id", "disputes", ["review_id"], unique=True)
    op.create_index("ix_disputes_filed_by_user_id", "disputes", ["filed_by_user_id"])
    op.create_index("ix_disputes_type", "disputes", ["type"])
    op.create_index("ix_disputes_status", "disputes", ["status"])

    # --- dispute_recipients ---
    op.create_table(
        "dispute_recipients",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dispute_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_type", sa.Enum("platform_admin", "org_admin", name="dispute_recipient_type", create_type=False), nullable=False),
        sa.Column("recipient_org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["dispute_id"], ["disputes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_org_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dispute_recipients_dispute_id", "dispute_recipients", ["dispute_id"])

    # --- notifications ---
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_type", sa.Enum(
            "review_received", "dispute_window_expiring", "dispute_filed", "dispute_resolved",
            "review_verified", "review_rejected", "profile_claimed", "score_updated",
            "badge_earned", "comment_reply", "new_comment", "review_liked", "member_request",
            name="notification_type", create_type=False,
        ), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("extra_data", postgresql.JSONB, nullable=True),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_sent", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("whatsapp_sent", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_notification_type", "notifications", ["notification_type"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    # --- notification_preferences ---
    op.create_table(
        "notification_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.Enum("email", "in_app", name="notif_pref_channel", create_type=False), nullable=False),
        sa.Column("type", sa.Enum(
            "review_received", "dispute_filed", "dispute_resolved", "review_verified",
            "review_rejected", "profile_claimed", "score_updated", "badge_earned",
            "comment_reply", "new_comment", "review_liked", "member_request",
            name="notif_pref_type", create_type=False,
        ), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "channel", "type", name="uq_notif_pref"),
    )
    op.create_index("ix_notif_pref_lookup", "notification_preferences", ["user_id", "channel", "type"])

    # --- score_history ---
    op.create_table(
        "score_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("review_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reason", sa.String(64), nullable=False, server_default="review_verified"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_score_history_profile_id", "score_history", ["profile_id"])

    # --- tag_aggregations ---
    op.create_table(
        "tag_aggregations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(100), nullable=False),
        sa.Column("count", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tag_aggregations_profile_id", "tag_aggregations", ["profile_id"])
    op.create_index("ix_tag_aggregations_tag", "tag_aggregations", ["tag"])

    # --- profile_bookmarks ---
    op.create_table(
        "profile_bookmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "profile_id", name="uq_bookmark_user_profile"),
    )
    op.create_index("ix_profile_bookmarks_user_id", "profile_bookmarks", ["user_id"])
    op.create_index("ix_profile_bookmarks_profile_id", "profile_bookmarks", ["profile_id"])

    # --- badges ---
    op.create_table(
        "badges",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("badge_type", sa.String(100), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("icon_url", sa.Text, nullable=True),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_badges_profile_id", "badges", ["profile_id"])
    op.create_index("ix_badges_badge_type", "badges", ["badge_type"])


def downgrade() -> None:
    op.drop_table("badges")
    op.drop_table("profile_bookmarks")
    op.drop_table("tag_aggregations")
    op.drop_table("score_history")
    op.drop_table("notification_preferences")
    op.drop_table("notifications")
    op.drop_table("dispute_recipients")
    op.drop_table("disputes")
    op.drop_table("comment_likes")
    op.drop_table("review_replies")
    op.drop_table("review_comments")
    op.drop_table("review_likes")
    op.drop_table("review_tags")
    op.drop_table("review_evidence")
    op.drop_table("review_flags")
    op.drop_table("review_ratings")
    op.drop_table("review_payments")
    op.drop_table("reviews")
    op.drop_table("social_accounts")
    op.drop_table("profiles")
    op.drop_table("organization_memberships")
    op.drop_table("organization_domains")
    op.drop_table("users")
    op.drop_table("fraud_alerts")
    op.drop_table("activity_events")
    op.drop_table("platform_admins")
    op.drop_table("organizations")

    op.execute("DROP TRIGGER IF EXISTS profiles_search_vector_trigger ON profiles")
    op.execute("DROP FUNCTION IF EXISTS profiles_search_vector_update()")

    op.execute("DROP TYPE IF EXISTS fraud_alert_status")
    op.execute("DROP TYPE IF EXISTS fraud_severity")
    op.execute("DROP TYPE IF EXISTS notif_pref_type")
    op.execute("DROP TYPE IF EXISTS notif_pref_channel")
    op.execute("DROP TYPE IF EXISTS notification_type")
    op.execute("DROP TYPE IF EXISTS dispute_recipient_type")
    op.execute("DROP TYPE IF EXISTS dispute_status")
    op.execute("DROP TYPE IF EXISTS dispute_type")
    op.execute("DROP TYPE IF EXISTS review_status")
    op.execute("DROP TYPE IF EXISTS relationship_type")
    op.execute("DROP TYPE IF EXISTS membership_status")
    op.execute("DROP TYPE IF EXISTS membership_role")
    op.execute("DROP TYPE IF EXISTS social_platform")
    op.execute("DROP TYPE IF EXISTS access_level")
    op.execute("DROP TYPE IF EXISTS profile_type")
    op.execute("DROP TYPE IF EXISTS org_verification_status")
    op.execute("DROP TYPE IF EXISTS org_type")
    op.execute("DROP TYPE IF EXISTS subscription_tier")
    op.execute("DROP TYPE IF EXISTS user_role")
