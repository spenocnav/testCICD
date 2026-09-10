"""Housekeeping de refresh tokens.

Este módulo es **independiente** de la lógica de login/refresh/logout.
Sólo se encarga de mantener acotada la tabla ``refresh_tokens`` borrando
filas que ya no tienen valor funcional:

- Filas revocadas hace más de ``retention_seconds`` segundos.
- Filas expiradas hace más de ``retention_seconds`` segundos
  (cubrimos el caso de tokens emitidos que nunca fueron usados y que
  ya vencieron: también son purgados sin necesidad de revocación).

El proceso está pensado para correr como contenedor separado (ver
``scripts/run_refresh_token_purge.py`` y ``docker-compose.yml``). El
acceso a la base de datos se hace siempre con la configuración estándar
del API (``settings.effective_database_url``) — nunca con credenciales
hardcodeadas. La función ``purge_once`` además es idempotente: si se
llama dos veces seguidas sin filas nuevas, la segunda pasada devuelve 0
sin error.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.refresh_token import RefreshToken


@dataclass(frozen=True)
class PurgeResult:
    """Resultado de una pasada de purga acotada."""

    deleted: int
    cutoff: datetime


def _compute_cutoff(*, now: datetime | None = None) -> datetime:
    """Fecha máxima de expiración o revocación que puede purgarse."""
    base = now or datetime.now(UTC)
    return base - timedelta(seconds=settings.refresh_token_purge_retention_seconds)


async def count_purgeable(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Cuenta cuántas filas serían elegibles para purga.

    Útil para tests y métricas. No usa ``DELETE``."""
    cutoff = _compute_cutoff(now=now)
    stmt = select(func.count(RefreshToken.id)).where(
        or_(
            RefreshToken.revoked_at.is_not(None) & (RefreshToken.revoked_at < cutoff),
            RefreshToken.expires_at < cutoff,
        )
    )
    return int((await db.execute(stmt)).scalar_one())


async def purge_once(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> PurgeResult:
    """Ejecuta UNA pasada de purga, limitada a ``batch_size`` filas.

    Idempotente: si la tabla ya está limpia, devuelve ``deleted=0``.
    Pensada para ser llamada por el loop de housekeeping. La query es
    DELETE directo con ``LIMIT`` (Postgres: ``DELETE ... WHERE id IN
    (...subselect LIMIT n)``) para no sostener un lock largo sobre la
    tabla. Si hay más candidatas que ``batch_size``, la próxima
    invocación continuará.
    """
    cutoff = _compute_cutoff(now=now)
    limit = batch_size if batch_size is not None else settings.refresh_token_purge_batch_size
    if limit <= 0:
        raise ValueError("batch_size debe ser mayor que cero")

    # Subselect con LIMIT — Postgres soporta esta forma.
    # Una fila ya no tiene valor funcional si lleva revocada O expirada
    # más allá de la retención. Los dos índices permiten un BitmapOr barato.
    subq = (
        select(RefreshToken.id)
        .where(
            or_(
                RefreshToken.revoked_at.is_not(None) & (RefreshToken.revoked_at < cutoff),
                RefreshToken.expires_at < cutoff,
            )
        )
        .order_by(RefreshToken.expires_at)
        .limit(limit)
    )
    stmt = (
        delete(RefreshToken)
        .where(RefreshToken.id.in_(subq))
        .returning(RefreshToken.id)
    )
    deleted = len((await db.execute(stmt)).scalars().all())
    await db.commit()
    return PurgeResult(deleted=deleted, cutoff=cutoff)
