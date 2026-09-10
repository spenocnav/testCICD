"""Operaciones de autenticación: login, refresh, logout."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_refresh_token,
    verify_password_async,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.rbac import get_user_permission_codes
from app.services.user_service import get_user_by_email


class AuthError(Exception):
    """Credenciales inválidas o token de refresh no aceptado."""


async def authenticate(db: AsyncSession, *, email: str, password: str) -> User:
    user = await get_user_by_email(db, email)
    if not user or not user.is_active or user.is_archived:
        raise AuthError("Credenciales inválidas")
    if not await verify_password_async(password, user.password_hash):
        raise AuthError("Credenciales inválidas")
    user.last_login_at = datetime.now(UTC)
    await db.flush()
    return user


def build_access_token(user: User) -> str:
    codes = sorted(get_user_permission_codes(user))
    return create_access_token(
        subject=str(user.id),
        extra_claims={"perms": codes},
    )


async def issue_refresh_token(
    db: AsyncSession,
    user: User,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> str:
    raw_token = create_refresh_token(subject=str(user.id))
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds)
    record = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_token),
        expires_at=expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    db.add(record)
    await db.flush()
    return raw_token


async def rotate_refresh_token(
    db: AsyncSession,
    raw_token: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[User, str, str]:
    """Valida refresh actual, lo revoca y emite nuevo par (access, refresh).

    Concurrencia: la revocación del token viejo se hace con un
    ``UPDATE ... WHERE token_hash = :h AND revoked_at IS NULL
    AND expires_at > now() RETURNING id, user_id``. Ese único
    UPDATE es atómico en Postgres: si dos requests llegan con el
    mismo refresh, sólo uno de los UPDATEs matchea una fila viva
    (el otro ve 0 filas y se rechaza con AuthError). Esto preserva
    el contrato observable: un único ``AuthError`` con detalle
    "Refresh revocado o desconocido" para la segunda request.
    """
    try:
        payload = decode_token(raw_token, expected_type="refresh")
    except TokenError as exc:
        raise AuthError(f"Refresh inválido: {exc}") from exc

    try:
        user_id_from_payload = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthError("Refresh sin sujeto válido") from exc

    token_hash = hash_refresh_token(raw_token)
    now = datetime.now(UTC)

    # Reclamo atómico: marca revoked_at sólo si la fila está viva.
    # Devolvemos user_id para no tener un SELECT extra posterior.
    claim_stmt = (
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
        .values(revoked_at=now)
        .returning(RefreshToken.id, RefreshToken.user_id)
    )
    result = await db.execute(claim_stmt)
    claimed = result.first()
    if claimed is None:
        # La fila no existe, ya está revocada, o expiró. Distinguimos
        # mensaje para observabilidad sin cambiar el contrato: cualquier
        # causa se traduce a AuthError como antes.
        existing = await db.execute(
            select(
                RefreshToken.id,
                RefreshToken.revoked_at,
                RefreshToken.expires_at,
            ).where(
                RefreshToken.token_hash == token_hash,
            )
        )
        row = existing.first()
        if row is None or row.revoked_at is not None:
            raise AuthError("Refresh revocado o desconocido")
        raise AuthError("Refresh expirado")

    # Defensa en profundidad: si el subject del JWT no coincide con el
    # dueño de la fila revivable, no emitimos un par nuevo. Esto cubre
    # un caso patológico (token forjado a mano o migración sucia) sin
    # tocar el flujo normal.
    if claimed.user_id != user_id_from_payload:
        raise AuthError("Refresh sin sujeto válido")

    user = await db.get(User, claimed.user_id)
    if not user or not user.is_active or user.is_archived:
        # El endpoint hará rollback, igual que en el flujo anterior.
        raise AuthError("Usuario no encontrado o inactivo")

    new_refresh = await issue_refresh_token(db, user, user_agent=user_agent, ip=ip)
    new_access = build_access_token(user)
    return user, new_access, new_refresh


async def revoke_refresh_token(db: AsyncSession, raw_token: str) -> None:
    token_hash = hash_refresh_token(raw_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    db_token = result.scalar_one_or_none()
    if db_token and db_token.revoked_at is None:
        db_token.revoked_at = datetime.now(UTC)
        await db.flush()
