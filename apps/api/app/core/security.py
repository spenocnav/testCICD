"""Helpers de seguridad: hashing de passwords y emisión/verificación de JWT."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

TokenType = Literal["access", "refresh"]

# bcrypt acepta máximo 72 bytes en el input. Truncamos para garantizar consistencia.
_BCRYPT_MAX_BYTES = 72


def _bcrypt_input(plain: str) -> bytes:
    encoded = plain.encode("utf-8")
    return encoded[:_BCRYPT_MAX_BYTES]


# ---------- Passwords ----------

def hash_password(plain: str) -> str:
    hashed = bcrypt.hashpw(_bcrypt_input(plain), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_input(plain), hashed.encode("utf-8"))
    except ValueError:
        return False


# bcrypt es CPU-bound: una verificación con coste 12 toma ~0,5 s. Llamarla desde
# una corrutina detiene TODAS las peticiones del worker Uvicorn. Se aísla en un
# pool propio: dedicado, para no competir por el threadpool compartido de AnyIO
# que usan otras llamadas bloqueantes; y acotado, para que una ráfaga de logins
# no monopolice la CPU del contenedor. Los hilos se crean bajo demanda.
_password_executor = ThreadPoolExecutor(
    max_workers=settings.password_hash_max_concurrency,
    thread_name_prefix="pwd-hash",
)


async def hash_password_async(plain: str) -> str:
    """`hash_password` fuera del event loop. Usar desde cualquier corrutina."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_password_executor, hash_password, plain)


async def verify_password_async(plain: str, hashed: str) -> bool:
    """`verify_password` fuera del event loop. Usar desde cualquier corrutina."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_password_executor, verify_password, plain, hashed)


# ---------- JWT ----------

def _create_token(
    subject: str,
    token_type: TokenType,
    ttl_seconds: int,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "type": token_type,
        "jti": secrets.token_urlsafe(16),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str, extra_claims: dict[str, Any] | None = None) -> str:
    return _create_token(
        subject=subject,
        token_type="access",
        ttl_seconds=settings.access_token_ttl_seconds,
        extra_claims=extra_claims,
    )


def create_refresh_token(subject: str) -> str:
    return _create_token(
        subject=subject,
        token_type="refresh",
        ttl_seconds=settings.refresh_token_ttl_seconds,
    )


class TokenError(Exception):
    """Token inválido, expirado o de tipo incorrecto."""


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise TokenError(str(exc)) from exc

    if expected_type and payload.get("type") != expected_type:
        raise TokenError(f"Token type mismatch: expected {expected_type}, got {payload.get('type')}")
    return payload


def hash_refresh_token(token: str) -> str:
    """Hash determinístico para almacenar refresh tokens en DB (lookup + verificación)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
