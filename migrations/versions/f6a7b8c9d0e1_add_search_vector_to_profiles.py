"""add search vector to profiles

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable trigram extension for fuzzy search
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Add tsvector column
    op.execute(
        "ALTER TABLE profiles ADD COLUMN search_vector tsvector"
    )

    # Trigger function — 'simple' dict (no stemming, works for Indian names/handles)
    # Weights: A = display_name + handle (highest), B = bio + category, C = location
    op.execute("""
        CREATE FUNCTION profiles_search_vector_update() RETURNS trigger AS $$
        BEGIN
          NEW.search_vector :=
            setweight(to_tsvector('simple', coalesce(NEW.display_name, '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(NEW.handle, '')), 'A') ||
            setweight(to_tsvector('simple', coalesce(NEW.bio, '')), 'B') ||
            setweight(to_tsvector('simple', coalesce(NEW.category, '')), 'B') ||
            setweight(to_tsvector('simple', coalesce(NEW.location, '')), 'C');
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Trigger fires on INSERT and UPDATE
    op.execute("""
        CREATE TRIGGER profiles_search_vector_trigger
          BEFORE INSERT OR UPDATE ON profiles
          FOR EACH ROW EXECUTE FUNCTION profiles_search_vector_update();
    """)

    # GIN index for fast tsvector lookups
    op.execute(
        "CREATE INDEX profiles_search_vector_idx ON profiles USING GIN(search_vector)"
    )

    # GIN trigram indexes for fuzzy matching on display_name and handle
    op.execute(
        "CREATE INDEX profiles_display_name_trgm_idx ON profiles USING GIN(display_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX profiles_handle_trgm_idx ON profiles USING GIN(handle gin_trgm_ops)"
    )

    # Populate existing rows
    op.execute("""
        UPDATE profiles SET search_vector =
          setweight(to_tsvector('simple', coalesce(display_name, '')), 'A') ||
          setweight(to_tsvector('simple', coalesce(handle, '')), 'A') ||
          setweight(to_tsvector('simple', coalesce(bio, '')), 'B') ||
          setweight(to_tsvector('simple', coalesce(category, '')), 'B') ||
          setweight(to_tsvector('simple', coalesce(location, '')), 'C')
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS profiles_search_vector_trigger ON profiles")
    op.execute("DROP FUNCTION IF EXISTS profiles_search_vector_update()")
    op.execute("DROP INDEX IF EXISTS profiles_search_vector_idx")
    op.execute("DROP INDEX IF EXISTS profiles_display_name_trgm_idx")
    op.execute("DROP INDEX IF EXISTS profiles_handle_trgm_idx")
    op.execute("ALTER TABLE profiles DROP COLUMN IF EXISTS search_vector")
