"""
Reset the database to a clean state.

Usage (from credfluence-backend/):
    python scripts/reset_db.py

What it does:
  1. Drops all tables (DROP SCHEMA public CASCADE + CREATE SCHEMA public)
  2. Re-runs all Alembic migrations (alembic upgrade head)
  3. Seeds one platform admin from ADMIN_EMAIL / ADMIN_PASSWORD in .env
  4. Flushes the Redis database (clears all cached sessions + OTP codes)

SAFETY: Refuses to run if APP_ENV=production.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

APP_ENV      = os.getenv("APP_ENV", "development")
DATABASE_URL = os.getenv("DATABASE_URL", "")
REDIS_URL    = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _abort(msg: str) -> None:
    print(f"\n❌ {msg}\n")
    sys.exit(1)


async def drop_and_recreate_schema() -> None:
    import asyncpg
    # asyncpg needs plain postgresql:// URL
    url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    await conn.execute("DROP SCHEMA public CASCADE")
    await conn.execute("CREATE SCHEMA public")
    await conn.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
    await conn.close()


async def flush_redis() -> None:
    import redis.asyncio as aioredis
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    await client.flushdb()
    await client.aclose()
    print("✅ Redis flushed")


async def main() -> None:
    if APP_ENV == "production":
        _abort("reset_db.py cannot run in production (APP_ENV=production). Aborting.")

    if not DATABASE_URL:
        _abort("DATABASE_URL not set in .env")

    print(f"\n⚠️  Resetting database (APP_ENV={APP_ENV})")
    print("   This will DELETE ALL DATA. You have 3 seconds to cancel (Ctrl+C)...")
    await asyncio.sleep(3)

    # 1. Drop + recreate schema
    await drop_and_recreate_schema()
    print("✅ Schema dropped and recreated")

    project_root = Path(__file__).parent.parent

    # 2. Run migrations
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr)
        _abort("Alembic migration failed")
    print("✅ Migrations applied")

    # 3. Seed admin
    seed_script = project_root / "scripts" / "seed_admin.py"
    result = subprocess.run(
        [sys.executable, str(seed_script)],
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr)
        _abort("Admin seeding failed")
    print(result.stdout.strip())

    # 4. Flush Redis
    await flush_redis()

    print("\n✅ Database reset complete. Fresh start!\n")


if __name__ == "__main__":
    asyncio.run(main())
