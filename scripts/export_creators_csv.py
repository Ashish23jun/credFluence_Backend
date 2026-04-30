"""
Export all creator profiles to CSV.

Run inside the backend Docker container:
  docker exec -it credfluence-backend-api-1 python scripts/export_creators_csv.py

Copy out with:
  docker cp credfluence-backend-api-1:/tmp/creators_export.csv ./creators_export.csv
"""

import asyncio
import csv
import json
import sys

import asyncpg

DSN = "postgresql://credfluence:credfluence@postgres:5432/credfluence"
OUTPUT = "/tmp/creators_export.csv"

QUERY = """
SELECT
    p.id::text                  AS profile_id,
    p.display_name,
    p.handle,
    p.bio,
    p.location,
    p.category,
    p.trust_score,
    p.review_count,
    p.niches::text              AS niches,
    p.languages::text           AS languages,
    p.ai_summary_tags::text     AS ai_summary_tags,
    p.social_links::text        AS social_links,
    p.is_claimed,
    p.is_dummy,
    p.access_level,
    p.created_at                AS profile_created_at,

    o.id::text                  AS org_id,
    o.name                      AS org_name,
    o.verification_status,
    o.created_at                AS org_created_at,

    u.email,
    u.full_name,
    u.is_active,
    u.is_verified               AS email_verified,
    u.subscription_tier,
    u.onboarding_completed_at,
    u.created_at                AS user_created_at,

    COALESCE(
        json_agg(
            json_build_object(
                'platform', sa.platform,
                'username', sa.username,
                'followers', sa.stats->'followers',
                'following', sa.stats->'following',
                'posts',     sa.stats->'posts',
                'profile_url', sa.avatar_url
            )
        ) FILTER (WHERE sa.id IS NOT NULL),
        '[]'
    )::text                     AS social_accounts

FROM profiles p
JOIN organizations o ON o.id = p.organization_id
LEFT JOIN users u ON u.organization_id = o.id AND u.role = 'creator'
LEFT JOIN social_accounts sa ON sa.user_id = u.id

WHERE p.profile_type = 'creator'

GROUP BY p.id, o.id, u.id

ORDER BY p.trust_score DESC, p.created_at DESC;
"""


def flatten(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


async def main():
    print("Connecting to DB…")
    conn = await asyncpg.connect(DSN)

    print("Running query…")
    rows = await conn.fetch(QUERY)
    await conn.close()

    print(f"Found {len(rows)} creator rows.")
    if not rows:
        print("No creators found. Exiting.")
        sys.exit(0)

    fieldnames = list(rows[0].keys())

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: flatten(v) for k, v in dict(row).items()})

    print(f"Done. Written to {OUTPUT}")
    print()
    print("Copy to host:")
    print("  docker cp credfluence-backend-api-1:/tmp/creators_export.csv ./creators_export.csv")


if __name__ == "__main__":
    asyncio.run(main())
