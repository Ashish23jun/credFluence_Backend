import json
import secrets

from app.core.redis import get_redis

OTP_TTL = 600          # 10 minutes
PENDING_TTL = 600      # same window — pending signup expires with OTP
OTP_PREFIX = "otp:"
PENDING_PREFIX = "pending_signup:"
PWD_RESET_PREFIX = "pwd_reset:"


def generate_otp() -> str:
    """Generate a cryptographically secure 6-digit OTP."""
    return "".join(secrets.choice("0123456789") for _ in range(6))


# ── OTP ───────────────────────────────────────────────────────────────────────

async def store_otp(email: str, otp: str) -> None:
    redis = await get_redis()
    await redis.setex(f"{OTP_PREFIX}{email}", OTP_TTL, otp)


async def verify_otp(email: str, otp: str) -> bool:
    redis = await get_redis()
    stored = await redis.get(f"{OTP_PREFIX}{email}")
    return stored == otp


async def delete_otp(email: str) -> None:
    redis = await get_redis()
    await redis.delete(f"{OTP_PREFIX}{email}")


# ── Pending signup ────────────────────────────────────────────────────────────

async def store_pending_signup(email: str, hashed_password: str, role: str) -> None:
    redis = await get_redis()
    payload = json.dumps({"email": email, "hashed_password": hashed_password, "role": role})
    await redis.setex(f"{PENDING_PREFIX}{email}", PENDING_TTL, payload)


async def get_pending_signup(email: str) -> dict | None:
    redis = await get_redis()
    raw = await redis.get(f"{PENDING_PREFIX}{email}")
    return json.loads(raw) if raw else None


async def delete_pending_signup(email: str) -> None:
    redis = await get_redis()
    await redis.delete(f"{PENDING_PREFIX}{email}")


# ── Password reset OTP ────────────────────────────────────────────────────────

async def store_pwd_reset_otp(email: str, otp: str) -> None:
    redis = await get_redis()
    await redis.setex(f"{PWD_RESET_PREFIX}{email}", OTP_TTL, otp)


async def verify_pwd_reset_otp(email: str, otp: str) -> bool:
    redis = await get_redis()
    stored = await redis.get(f"{PWD_RESET_PREFIX}{email}")
    return stored == otp


async def delete_pwd_reset_otp(email: str) -> None:
    redis = await get_redis()
    await redis.delete(f"{PWD_RESET_PREFIX}{email}")
