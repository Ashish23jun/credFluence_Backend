"""organizations and onboarding

Revision ID: c1d2e3f4a5b6
Revises: a1b2c3d4e5f6
Create Date: 2026-04-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Reusable ENUM references with create_type=False (types created via raw SQL below)
_org_type = postgresql.ENUM("creator", "agency", "brand", name="org_type", create_type=False)
_org_verification_status = postgresql.ENUM("pending", "verified", "rejected", name="org_verification_status", create_type=False)
_membership_role = postgresql.ENUM("admin", "member", name="membership_role", create_type=False)
_membership_status = postgresql.ENUM("pending", "active", "rejected", name="membership_status", create_type=False)
_dispute_type = postgresql.ENUM("verification", "duplicate_name", "false_claim", name="dispute_type", create_type=False)
_dispute_recipient_type = postgresql.ENUM("platform_admin", "org_admin", name="dispute_recipient_type", create_type=False)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Create enum types (DO $$ EXCEPTION pattern = safe CREATE IF NOT EXISTS)
    # ------------------------------------------------------------------
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE org_type AS ENUM ('creator', 'agency', 'brand');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE org_verification_status AS ENUM ('pending', 'verified', 'rejected');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE membership_role AS ENUM ('admin', 'member');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE membership_status AS ENUM ('pending', 'active', 'rejected');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE dispute_type AS ENUM ('verification', 'duplicate_name', 'false_claim');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE dispute_recipient_type AS ENUM ('platform_admin', 'org_admin');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # ------------------------------------------------------------------
    # organizations
    # ------------------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("org_type", _org_type, nullable=False),
        sa.Column("verification_status", _org_verification_status, nullable=False, server_default="pending"),
        sa.Column("verification_notes", sa.Text(), nullable=True),
        sa.Column("rejected_reason", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_admin_id", sa.UUID(), nullable=True),
        sa.Column("verification_docs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_personal_creator_org", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_organizations_slug"), "organizations", ["slug"], unique=True)
    op.create_index(op.f("ix_organizations_org_type"), "organizations", ["org_type"], unique=False)
    op.create_index(op.f("ix_organizations_verification_status"), "organizations", ["verification_status"], unique=False)

    # ------------------------------------------------------------------
    # organization_domains
    # ------------------------------------------------------------------
    op.create_table(
        "organization_domains",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain"),
    )
    op.create_index(op.f("ix_organization_domains_organization_id"), "organization_domains", ["organization_id"], unique=False)
    op.create_index(op.f("ix_organization_domains_domain"), "organization_domains", ["domain"], unique=True)

    # ------------------------------------------------------------------
    # Alter users: add organization_id + onboarding_completed_at
    # ------------------------------------------------------------------
    op.add_column("users", sa.Column("organization_id", sa.UUID(), nullable=False))
    op.add_column("users", sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_users_organization_id", "users", "organizations", ["organization_id"], ["id"], ondelete="RESTRICT")
    op.create_index(op.f("ix_users_organization_id"), "users", ["organization_id"], unique=False)

    # ------------------------------------------------------------------
    # organization_memberships
    # ------------------------------------------------------------------
    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", _membership_role, nullable=False, server_default="member"),
        sa.Column("status", _membership_status, nullable=False, server_default="pending"),
        sa.Column("approved_by_user_id", sa.UUID(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_membership"),
    )
    op.create_index(op.f("ix_organization_memberships_organization_id"), "organization_memberships", ["organization_id"], unique=False)
    op.create_index(op.f("ix_organization_memberships_user_id"), "organization_memberships", ["user_id"], unique=False)
    op.create_index(op.f("ix_organization_memberships_status"), "organization_memberships", ["status"], unique=False)

    # ------------------------------------------------------------------
    # Alter profiles: drop user_id, add organization_id, fix trust_score default
    # ------------------------------------------------------------------
    op.drop_index(op.f("ix_profiles_user_id"), table_name="profiles")
    op.drop_column("profiles", "user_id")
    op.add_column("profiles", sa.Column("organization_id", sa.UUID(), nullable=False))
    op.create_foreign_key("fk_profiles_organization_id", "profiles", "organizations", ["organization_id"], ["id"], ondelete="CASCADE")
    op.create_index(op.f("ix_profiles_organization_id"), "profiles", ["organization_id"], unique=True)
    op.alter_column("profiles", "trust_score", existing_type=sa.Integer(), server_default="450", existing_nullable=False)

    # ------------------------------------------------------------------
    # Alter disputes: add type column
    # ------------------------------------------------------------------
    op.add_column(
        "disputes",
        sa.Column("type", _dispute_type, nullable=False, server_default="verification"),
    )
    op.create_index(op.f("ix_disputes_type"), "disputes", ["type"], unique=False)

    # ------------------------------------------------------------------
    # dispute_recipients
    # ------------------------------------------------------------------
    op.create_table(
        "dispute_recipients",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("dispute_id", sa.UUID(), nullable=False),
        sa.Column("recipient_type", _dispute_recipient_type, nullable=False),
        sa.Column("recipient_org_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dispute_id"], ["disputes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_org_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_dispute_recipients_dispute_id"), "dispute_recipients", ["dispute_id"], unique=False)


def downgrade() -> None:
    op.drop_table("dispute_recipients")
    op.drop_index(op.f("ix_disputes_type"), table_name="disputes")
    op.drop_column("disputes", "type")

    op.drop_index(op.f("ix_profiles_organization_id"), table_name="profiles")
    op.drop_constraint("fk_profiles_organization_id", "profiles", type_="foreignkey")
    op.drop_column("profiles", "organization_id")
    op.add_column("profiles", sa.Column("user_id", sa.UUID(), nullable=True))
    op.create_index(op.f("ix_profiles_user_id"), "profiles", ["user_id"], unique=False)

    op.drop_table("organization_memberships")

    op.drop_index(op.f("ix_users_organization_id"), table_name="users")
    op.drop_constraint("fk_users_organization_id", "users", type_="foreignkey")
    op.drop_column("users", "onboarding_completed_at")
    op.drop_column("users", "organization_id")

    op.drop_table("organization_domains")
    op.drop_table("organizations")

    for enum_name in ["org_type", "org_verification_status", "membership_role", "membership_status", "dispute_type", "dispute_recipient_type"]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
