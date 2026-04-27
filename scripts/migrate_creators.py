"""
Migration script: import creators from Creator Central DB → CredFluence DB
Includes Instagram, YouTube, X, LinkedIn, Snapchat, Pinterest per creator.

Run inside the backend Docker container:
  docker exec -it credfluence-backend-api-1 python scripts/migrate_creators.py
"""

import math
import uuid
import logging
import re
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

SOURCE_DSN = "postgresql://postgres:HdkTEaZFCJEThlNSdqg4@72.60.222.59:5488/Creator_central_database"
TARGET_DSN = "postgresql://credfluence:credfluence@postgres:5432/credfluence"

BATCH_SIZE = 500

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def trust_score(max_followers: int, is_verified: bool = False) -> int:
    if max_followers <= 0:
        return 300
    score = 300 + int(math.log10(max(1, max_followers)) * 70)
    if is_verified:
        score += 50
    return min(900, max(300, score))


def clean_name(raw: str) -> str:
    cleaned = re.sub(r'[^\x20-\x7EÀ-ɏऀ-ॿ]', '', raw or '').strip()
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned or (raw or '').strip()


def title_language(lang: str) -> str:
    mapping = {
        'english': 'English', 'hindi': 'Hindi', 'tamil': 'Tamil',
        'telugu': 'Telugu', 'kannada': 'Kannada', 'malayalam': 'Malayalam',
        'marathi': 'Marathi', 'bengali': 'Bengali', 'gujarati': 'Gujarati',
        'punjabi': 'Punjabi', 'odia': 'Odia', 'urdu': 'Urdu',
        'assamese': 'Assamese', 'bhojpuri': 'Bhojpuri', 'rajasthani': 'Rajasthani',
    }
    return mapping.get((lang or '').lower(), (lang or '').title())


def build_location(city: str, state: str, country: str) -> str | None:
    parts = [p for p in [city, state, country] if p and p.strip()]
    return ', '.join(parts) if parts else None


def build_social_links(row: dict) -> list:
    links = []

    # Instagram
    ig_handle = (row['ig_handle'] or '').strip()
    if ig_handle:
        links.append({
            "platform": "instagram",
            "url": row['ig_url'] or f"https://www.instagram.com/{ig_handle}",
            "handle": ig_handle,
            "followers": int(row['ig_followers'] or 0),
        })

    # YouTube
    yt_handle = (row['yt_handle'] or '').strip()
    if yt_handle or row['yt_url']:
        links.append({
            "platform": "youtube",
            "url": row['yt_url'] or f"https://www.youtube.com/@{yt_handle}",
            "handle": yt_handle or None,
            "followers": int(row['yt_subscribers'] or 0),
        })

    # X / Twitter
    if row['x_url'] and row['x_url'].strip():
        links.append({"platform": "twitter", "url": row['x_url'].strip(), "handle": None, "followers": 0})

    # LinkedIn
    if row['linkedin_url'] and row['linkedin_url'].strip():
        links.append({"platform": "linkedin", "url": row['linkedin_url'].strip(), "handle": None, "followers": 0})

    # Snapchat
    if row['snapchat_url'] and row['snapchat_url'].strip():
        links.append({"platform": "snapchat", "url": row['snapchat_url'].strip(), "handle": None, "followers": 0})

    # Pinterest
    if row['pinterest_url'] and row['pinterest_url'].strip():
        links.append({"platform": "pinterest", "url": row['pinterest_url'].strip(), "handle": None, "followers": 0})

    return links


# ── Source query ──────────────────────────────────────────────────────────────

SOURCE_QUERY = """
SELECT
    c.creator_id,
    c.first_name,
    ig.ig_handle,
    ig.ig_url,
    COALESCE(ig.ig_followers, 0)          AS ig_followers,
    b.profile_pic,
    l.city_name,
    l.state_name,
    COALESCE(l.country_name, 'India')     AS country_name,
    o.x_url,
    o.linkedin_url,
    o.snapchat_url,
    o.pinterest_url,
    COALESCE(v.is_verified, false)         AS is_verified,
    yt.yt_handle,
    yt.yt_url,
    COALESCE(yt.yt_subscribers, 0)        AS yt_subscribers
FROM creators c
JOIN  creator_social_instagram ig ON ig.creator_id = c.creator_id
JOIN  creator_branding b           ON b.creator_id  = c.creator_id
JOIN  creator_location l           ON l.creator_id  = c.creator_id
JOIN  creator_social_other o       ON o.creator_id  = c.creator_id
JOIN  creator_verification v       ON v.creator_id  = c.creator_id
LEFT JOIN creator_social_youtube yt ON yt.creator_id = c.creator_id
WHERE ig.ig_handle IS NOT NULL AND ig.ig_handle != ''
ORDER BY c.creator_id
"""

CATEGORIES_QUERY = """
SELECT creator_id, array_agg(DISTINCT category_name ORDER BY category_name) AS niches
FROM creator_categories GROUP BY creator_id
"""

LANGUAGES_QUERY = """
SELECT creator_id, array_agg(DISTINCT language_name ORDER BY language_name) AS languages
FROM creator_languages GROUP BY creator_id
"""

# ── Clean previously migrated data ───────────────────────────────────────────

def clean_migrated(tgt):
    log.info("Cleaning previously migrated creator profiles and orgs...")
    with tgt.cursor() as cur:
        cur.execute("""
            DELETE FROM organizations
            WHERE is_personal_creator_org = true
              AND id NOT IN (SELECT organization_id FROM users WHERE organization_id IS NOT NULL)
        """)
        deleted = cur.rowcount
    tgt.commit()
    log.info(f"  Removed {deleted:,} previously migrated orgs (and their cascade-deleted profiles)")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    log.info("Connecting to source and target databases...")
    src = psycopg2.connect(SOURCE_DSN)
    tgt = psycopg2.connect(TARGET_DSN)
    src.set_session(readonly=True)

    try:
        clean_migrated(tgt)

        log.info("Loading categories and languages lookup maps...")
        with src.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(CATEGORIES_QUERY)
            categories_map = {r['creator_id']: list(r['niches']) for r in cur.fetchall()}

            cur.execute(LANGUAGES_QUERY)
            languages_map = {
                r['creator_id']: [title_language(l) for l in r['languages']]
                for r in cur.fetchall()
            }

        log.info(f"  Categories for {len(categories_map):,} | Languages for {len(languages_map):,}")

        with tgt.cursor() as cur:
            cur.execute("SELECT handle FROM profiles WHERE handle IS NOT NULL")
            existing_handles = {r[0] for r in cur.fetchall()}
        log.info(f"  {len(existing_handles):,} handles already in target DB")

        log.info("Fetching source creators...")
        with src.cursor(cursor_factory=psycopg2.extras.RealDictCursor, name='creators_cursor') as cur:
            cur.itersize = BATCH_SIZE
            cur.execute(SOURCE_QUERY)

            now = datetime.now(timezone.utc)
            inserted = skipped = dupes = with_yt = 0
            org_rows     = []
            profile_rows = []

            def flush():
                nonlocal inserted
                if not org_rows:
                    return
                with tgt.cursor() as tc:
                    psycopg2.extras.execute_values(tc, """
                        INSERT INTO organizations
                            (id, name, slug, org_type, verification_status,
                             is_personal_creator_org, created_at, updated_at)
                        VALUES %s
                        ON CONFLICT (slug) DO NOTHING
                    """, org_rows)

                    psycopg2.extras.execute_values(tc, """
                        INSERT INTO profiles
                            (id, organization_id, display_name, handle, avatar_url,
                             location, profile_type, niches, languages, social_links,
                             trust_score, review_count, is_claimed, is_dummy,
                             is_opted_out, access_level, created_at, updated_at)
                        VALUES %s
                        ON CONFLICT (organization_id) DO NOTHING
                    """, profile_rows)
                tgt.commit()
                inserted += len(org_rows)
                org_rows.clear()
                profile_rows.clear()

            for row in cur:
                handle = (row['ig_handle'] or '').strip().lower()
                if not handle:
                    skipped += 1
                    continue

                original = handle
                suffix = 2
                while handle in existing_handles:
                    handle = f"{original}_{suffix}"
                    suffix += 1
                    dupes += 1
                existing_handles.add(handle)

                name     = clean_name(row['first_name']) or handle
                org_id   = uuid.uuid4()
                profile_id = uuid.uuid4()

                niches    = categories_map.get(row['creator_id'], [])
                languages = languages_map.get(row['creator_id'], [])
                location  = build_location(row['city_name'], row['state_name'], row['country_name'])
                links     = build_social_links(row)

                # Use the highest follower count across all platforms for trust score
                max_followers = max(
                    int(row['ig_followers'] or 0),
                    int(row['yt_subscribers'] or 0),
                )
                score = trust_score(max_followers, row['is_verified'])

                if row['yt_subscribers'] and int(row['yt_subscribers']) > 0:
                    with_yt += 1

                org_rows.append((
                    str(org_id), name, handle, 'creator', 'verified',
                    True, now, now,
                ))
                profile_rows.append((
                    str(profile_id), str(org_id), name, handle,
                    row['profile_pic'] or None,
                    location,
                    'creator',
                    psycopg2.extras.Json(niches),
                    psycopg2.extras.Json(languages),
                    psycopg2.extras.Json(links),
                    score, 0, False, False, False, 'limited',
                    now, now,
                ))

                if len(org_rows) >= BATCH_SIZE:
                    flush()
                    log.info(f"  Inserted {inserted:,} | skipped {skipped:,} | dupes {dupes} | with YouTube {with_yt:,}")

            flush()

        log.info(f"\n✅ Done. Inserted: {inserted:,} | Skipped: {skipped:,} | Dupes: {dupes} | With YouTube: {with_yt:,}")

    finally:
        src.close()
        tgt.close()


if __name__ == "__main__":
    run()
