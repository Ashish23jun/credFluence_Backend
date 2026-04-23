"""
Seed a platform admin account.

Usage (from credfluence-backend/):
    python scripts/seed_admin.py

Reads ADMIN_EMAIL and ADMIN_PASSWORD from .env.
Safe to re-run — does nothing if the email already exists.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import bcrypt
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

load_dotenv(Path(__file__).parent.parent / ".env")

ADMIN_EMAIL    = os.getenv("ADMIN_EMAIL",    "admin@credfluence.in")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Admin@1234!")
DATABASE_URL   = os.getenv("DATABASE_URL", "")


async def seed() -> None:
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)

    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        result = await db.execute(
            text("SELECT id FROM platform_admins WHERE email = :email"),
            {"email": ADMIN_EMAIL},
        )
        if result.fetchone():
            print(f"✅ Platform admin already exists: {ADMIN_EMAIL}")
            await engine.dispose()
            return

        hashed = bcrypt.hashpw(ADMIN_PASSWORD.encode(), bcrypt.gensalt()).decode()

        await db.execute(
            text("""
                INSERT INTO platform_admins (id, email, hashed_password, is_active, created_at, updated_at)
                VALUES (gen_random_uuid(), :email, :hashed_password, true, now(), now())
            """),
            {"email": ADMIN_EMAIL, "hashed_password": hashed},
        )
        await db.commit()

    print()
    print("✅ Platform admin seeded successfully")
    print(f"   Email    : {ADMIN_EMAIL}")
    print(f"   Password : {ADMIN_PASSWORD}")
    print()
    print("Login at POST /admin/auth/login")
    print()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
