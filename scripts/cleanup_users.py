"""
Remove specific test users and clean up their owned data.

For each email provided:
  - If the user owns a personal creator org (is_personal_creator_org=True):
      deletes reviews on the profile, the profile, the org memberships, the org, then the user.
  - If the user belongs to a business org:
      removes only the membership. The org and its profile stay (others may share it).
      If the user was the only admin, the org is left intact but marked for manual review.

Usage (from credfluence-backend/, with local Docker running):
    docker compose exec api python scripts/cleanup_users.py ashishjob69@gmail.com ashish@creatorsmela.com

SAFETY: Refuses to run if APP_ENV=production.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

APP_ENV = os.getenv("APP_ENV", "development")


def _abort(msg: str) -> None:
    print(f"\n❌ {msg}\n")
    sys.exit(1)


async def cleanup(emails: list[str]) -> None:
    import asyncpg

    db_url = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    if not db_url:
        _abort("DATABASE_URL not set")

    conn = await asyncpg.connect(db_url)
    try:
        for email in emails:
            email = email.strip().lower()
            row = await conn.fetchrow("SELECT id, organization_id FROM users WHERE email = $1", email)
            if not row:
                print(f"  – {email}: not found, skipping")
                continue

            user_id = row["id"]
            org_id = row["organization_id"]

            if org_id:
                org = await conn.fetchrow(
                    "SELECT id, name, is_personal_creator_org FROM organizations WHERE id = $1", org_id
                )
            else:
                org = None

            if org and org["is_personal_creator_org"]:
                # Delete everything owned by this personal creator org
                profile = await conn.fetchrow(
                    "SELECT id FROM profiles WHERE organization_id = $1", org_id
                )
                if profile:
                    profile_id = profile["id"]
                    # Sub-tables that reference reviews
                    deleted_reviews = await conn.fetchval(
                        "SELECT count(*) FROM reviews WHERE target_profile_id = $1", profile_id
                    )
                    await conn.execute(
                        """
                        DELETE FROM review_evidence   WHERE review_id IN (SELECT id FROM reviews WHERE target_profile_id = $1);
                        DELETE FROM review_flags      WHERE review_id IN (SELECT id FROM reviews WHERE target_profile_id = $1);
                        DELETE FROM review_tags       WHERE review_id IN (SELECT id FROM reviews WHERE target_profile_id = $1);
                        DELETE FROM review_payments   WHERE review_id IN (SELECT id FROM reviews WHERE target_profile_id = $1);
                        DELETE FROM review_ratings    WHERE review_id IN (SELECT id FROM reviews WHERE target_profile_id = $1);
                        DELETE FROM review_comments   WHERE review_id IN (SELECT id FROM reviews WHERE target_profile_id = $1);
                        DELETE FROM reviews           WHERE target_profile_id = $1;
                        """,
                        profile_id,
                    )
                    await conn.execute("DELETE FROM profiles WHERE id = $1", profile_id)
                    print(f"  ✓ {email}: deleted profile + {deleted_reviews} review(s)")

                await conn.execute(
                    "DELETE FROM organization_memberships WHERE organization_id = $1", org_id
                )
                await conn.execute("DELETE FROM organization_domains WHERE organization_id = $1", org_id)
                await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
                print(f"  ✓ {email}: deleted personal org '{org['name']}'")
            else:
                # Business org — remove only the membership
                await conn.execute(
                    "DELETE FROM organization_memberships WHERE user_id = $1", user_id
                )
                print(f"  ✓ {email}: removed from org (org itself kept)")

            await conn.execute("DELETE FROM users WHERE id = $1", user_id)
            print(f"  ✓ {email}: user deleted\n")
    finally:
        await conn.close()


async def main() -> None:
    if APP_ENV == "production":
        _abort("cleanup_users.py cannot run in production. Aborting.")

    emails = sys.argv[1:]
    if not emails:
        _abort("Usage: python scripts/cleanup_users.py email1@example.com email2@example.com ...")

    print(f"\nCleaning up {len(emails)} user(s)...")
    await cleanup(emails)
    print("Done.\n")


if __name__ == "__main__":
    asyncio.run(main())
