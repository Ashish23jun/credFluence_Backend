import asyncio
import base64
import os
from datetime import UTC, datetime, timedelta

import bcrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jose import JWTError, jwt

from app.core.config import settings

# ---------------------------------------------------------------------------
# Password hashing (bcrypt directly — passlib has compatibility issues with bcrypt 4.x)
# Bcrypt is CPU-bound (~300ms). Run in thread pool to avoid blocking event loop.
# ---------------------------------------------------------------------------

def _hash_password_sync(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password_sync(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hash_password_sync, password)


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    return await asyncio.to_thread(_verify_password_sync, plain_password, hashed_password)


# ---------------------------------------------------------------------------
# JWT — RS256
# ---------------------------------------------------------------------------

def create_access_token(payload: dict) -> str:
    data = payload.copy()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    data.update({"exp": expire, "type": "access"})
    private_key = settings.jwt_private_key
    if not private_key:
        # Fallback for dev without RSA keys — use HS256 with secret
        return jwt.encode(data, settings.app_secret_key, algorithm="HS256")
    return jwt.encode(data, private_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(payload: dict) -> str:
    data = payload.copy()
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)
    data.update({"exp": expire, "type": "refresh"})
    private_key = settings.jwt_private_key
    if not private_key:
        return jwt.encode(data, settings.app_secret_key, algorithm="HS256")
    return jwt.encode(data, private_key, algorithm=settings.jwt_algorithm)


def create_admin_access_token(admin_id: str) -> str:
    data: dict = {"sub": admin_id, "type": "admin_access"}
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    data["exp"] = expire
    private_key = settings.jwt_private_key
    if not private_key:
        return jwt.encode(data, settings.app_secret_key, algorithm="HS256")
    return jwt.encode(data, private_key, algorithm=settings.jwt_algorithm)


def create_admin_refresh_token(admin_id: str) -> str:
    data: dict = {"sub": admin_id, "type": "admin_refresh"}
    expire = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expire_days)
    data["exp"] = expire
    private_key = settings.jwt_private_key
    if not private_key:
        return jwt.encode(data, settings.app_secret_key, algorithm="HS256")
    return jwt.encode(data, private_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    public_key = settings.jwt_public_key
    try:
        if not public_key:
            return jwt.decode(token, settings.app_secret_key, algorithms=["HS256"])
        return jwt.decode(token, public_key, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise ValueError(f"Invalid token: {e}") from e


# ---------------------------------------------------------------------------
# AES-256-GCM phone encryption
# ---------------------------------------------------------------------------

def _get_aes_key() -> bytes:
    key_hex = settings.phone_encryption_key
    return bytes.fromhex(key_hex)


def encrypt_phone(phone: str) -> str:
    key = _get_aes_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, phone.encode(), None)
    # Store nonce + ciphertext, base64 encoded
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode()


def decrypt_phone(encrypted: str) -> str:
    key = _get_aes_key()
    aesgcm = AESGCM(key)
    combined = base64.b64decode(encrypted.encode())
    nonce = combined[:12]
    ciphertext = combined[12:]
    return aesgcm.decrypt(nonce, ciphertext, None).decode()
