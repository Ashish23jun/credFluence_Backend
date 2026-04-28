"""review_schema_refactor_and_score_engine

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-04-28 14:00:00.000000

- Drop flat review columns (payment_status, rating_*, tags, evidence_keys)
- Add total_deal_value + currency to reviews
- Create review_payments, review_ratings, review_flags, review_evidence, review_tags
- Add outcome column to disputes
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── reviews table: drop old flat columns ──────────────────────────────────
    op.drop_column('reviews', 'payment_status')
    op.drop_column('reviews', 'rating_communication')
    op.drop_column('reviews', 'rating_professionalism')
    op.drop_column('reviews', 'rating_quality')
    op.drop_column('reviews', 'rating_reliability')
    op.drop_column('reviews', 'tags')
    op.drop_column('reviews', 'evidence_keys')

    # Drop the native ENUM type that was created for payment_status
    op.execute("DROP TYPE IF EXISTS payment_status")

    # ── reviews table: add new columns ───────────────────────────────────────
    op.add_column('reviews', sa.Column('total_deal_value', sa.Integer(), nullable=True))
    op.add_column('reviews', sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'))

    # ── review_payments ───────────────────────────────────────────────────────
    op.create_table(
        'review_payments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('review_id', sa.UUID(), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='INR'),
        sa.Column('payment_type', sa.String(length=20), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('proof_key', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "payment_type IN ('advance','milestone','final')",
            name='ck_review_payments_type',
        ),
        sa.CheckConstraint(
            "status IN ('pending','paid','late')",
            name='ck_review_payments_status',
        ),
        sa.ForeignKeyConstraint(['review_id'], ['reviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_review_payments_review_id', 'review_payments', ['review_id'])

    # ── review_ratings ────────────────────────────────────────────────────────
    op.create_table(
        'review_ratings',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('review_id', sa.UUID(), nullable=False),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('communication','professionalism','reliability','quality','brief_adherence','timeline_adherence')",
            name='ck_review_ratings_category',
        ),
        sa.CheckConstraint('score BETWEEN 1 AND 5', name='ck_review_ratings_score'),
        sa.ForeignKeyConstraint(['review_id'], ['reviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_review_ratings_review_id', 'review_ratings', ['review_id'])

    # ── review_flags ──────────────────────────────────────────────────────────
    op.create_table(
        'review_flags',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('review_id', sa.UUID(), nullable=False),
        sa.Column('type', sa.String(length=40), nullable=False),
        sa.Column('severity', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('ghosted','missed_deadline','scope_creep','rude_behavior','contract_violation')",
            name='ck_review_flags_type',
        ),
        sa.CheckConstraint(
            "severity IN ('low','medium','high')",
            name='ck_review_flags_severity',
        ),
        sa.ForeignKeyConstraint(['review_id'], ['reviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_review_flags_review_id', 'review_flags', ['review_id'])

    # ── review_evidence ───────────────────────────────────────────────────────
    op.create_table(
        'review_evidence',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('review_id', sa.UUID(), nullable=False),
        sa.Column('type', sa.String(length=20), nullable=False),
        sa.Column('file_key', sa.String(length=512), nullable=False),
        sa.Column('verified', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('screenshot','email','contract','invoice','chat')",
            name='ck_review_evidence_type',
        ),
        sa.ForeignKeyConstraint(['review_id'], ['reviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_review_evidence_review_id', 'review_evidence', ['review_id'])

    # ── review_tags ───────────────────────────────────────────────────────────
    op.create_table(
        'review_tags',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('review_id', sa.UUID(), nullable=False),
        sa.Column('tag', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tag IN ('fast_payment','delayed_payment','excellent_communication','poor_communication',"
            "'high_quality','low_quality','easy_to_work_with','difficult_client',"
            "'clear_brief','vague_brief','long_term_client','repeat_collaboration')",
            name='ck_review_tags_tag',
        ),
        sa.ForeignKeyConstraint(['review_id'], ['reviews.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_review_tags_review_id', 'review_tags', ['review_id'])

    # ── disputes: add outcome column ──────────────────────────────────────────
    op.add_column(
        'disputes',
        sa.Column('outcome', sa.String(length=30), nullable=True),
    )
    op.create_check_constraint(
        'ck_disputes_outcome',
        'disputes',
        "outcome IN ('reviewer_won','target_won','mutual_resolution')",
    )


def downgrade() -> None:
    # disputes
    op.drop_constraint('ck_disputes_outcome', 'disputes', type_='check')
    op.drop_column('disputes', 'outcome')

    # review sub-tables
    op.drop_index('ix_review_tags_review_id', table_name='review_tags')
    op.drop_table('review_tags')
    op.drop_index('ix_review_evidence_review_id', table_name='review_evidence')
    op.drop_table('review_evidence')
    op.drop_index('ix_review_flags_review_id', table_name='review_flags')
    op.drop_table('review_flags')
    op.drop_index('ix_review_ratings_review_id', table_name='review_ratings')
    op.drop_table('review_ratings')
    op.drop_index('ix_review_payments_review_id', table_name='review_payments')
    op.drop_table('review_payments')

    # reviews table: restore old columns
    op.drop_column('reviews', 'currency')
    op.drop_column('reviews', 'total_deal_value')

    payment_status_enum = sa.Enum(
        'paid_on_time', 'paid_late', 'partially_paid', 'unpaid',
        name='payment_status',
    )
    payment_status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column('reviews', sa.Column('payment_status', payment_status_enum, nullable=False, server_default='unpaid'))
    op.add_column('reviews', sa.Column('rating_communication', sa.Integer(), nullable=False, server_default='3'))
    op.add_column('reviews', sa.Column('rating_professionalism', sa.Integer(), nullable=False, server_default='3'))
    op.add_column('reviews', sa.Column('rating_quality', sa.Integer(), nullable=False, server_default='3'))
    op.add_column('reviews', sa.Column('rating_reliability', sa.Integer(), nullable=False, server_default='3'))
    op.add_column('reviews', sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('reviews', sa.Column('evidence_keys', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
