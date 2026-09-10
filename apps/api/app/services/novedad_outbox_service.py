"""Outbox transaccional para el envío de Novedades a Cloudfleet.

Patrón:
1. El endpoint crea `Novedad` + `NovedadOutbox` en la misma transacción y
   hace COMMIT antes de la llamada externa.
2. El worker reclama ítems elegibles con `UPDATE ... WHERE ... RETURNING`
   (atómico, sin race entre procesos concurrentes) y los procesa.
3. Tras éxito: marca outbox `sent` + `Novedad` `sent` en un commit.
4. Tras error: marca outbox `failed` con `available_at` programado para
   backoff exponencial y `Novedad` `failed`. Reintento manual re-eligibiliza.

Limitación de exactly-once:
- Entre el COMMIT local y el POST a Cloudfleet hay una ventana en la que un
  crash del proceso puede dejar la Novedad en `pending` con un envío que
  Cloudfleet YA procesó (porque respondió 2xx pero el cliente murió antes de
  anotar `sent`). Cloudfleet no soporta una `Idempotency-Key` conocida que
  podamos reenviar, por lo que un reintento del worker crearía un duplicado
  en Cloudfleet. La mitigación práctica es el backoff y la revisión manual
  en este caso; el contrato funcional es "at-least-once con detección
  semántica por el usuario".
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.novedad import (
    NOVEDAD_OUTBOX_FAILED,
    NOVEDAD_OUTBOX_PENDING,
    NOVEDAD_OUTBOX_PROCESSING,
    NOVEDAD_OUTBOX_SENT,
    Novedad,
    NovedadOutbox,
)
from app.services import cloudfleet_service
from app.services.cloudfleet_service import CloudfleetError

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _compute_backoff(attempts: int) -> timedelta:
    """Backoff exponencial capeado. `attempts` es el conteo tras incrementar."""
    base = settings.novedad_outbox_backoff_base_seconds
    cap = settings.novedad_outbox_backoff_max_seconds
    # attempts>=1 -> base; attempts=2 -> 2*base; ...
    seconds = min(cap, base * (2 ** max(0, attempts - 1)))
    return timedelta(seconds=seconds)


@dataclass(frozen=True)
class ClaimedOutbox:
    """Item fuera de la DB, listo para procesar."""

    outbox_id: uuid.UUID
    novedad_id: uuid.UUID
    attempts: int


async def enqueue(
    db: AsyncSession,
    *,
    novedad: Novedad,
    available_at: datetime | None = None,
) -> NovedadOutbox:
    """Crea la fila de outbox en la misma transacción que la Novedad."""
    row = NovedadOutbox(
        novedad_id=novedad.id,
        status=NOVEDAD_OUTBOX_PENDING,
        attempts=0,
        available_at=available_at or _utcnow(),
    )
    db.add(row)
    await db.flush()
    return row


async def claim_one(
    db: AsyncSession,
    *,
    stale_after: timedelta,
    now: datetime | None = None,
    novedad_id: uuid.UUID | None = None,
) -> ClaimedOutbox | None:
    """Reclama atómicamente el próximo outbox elegible.

    Elegible = ((status=pending|failed AND available_at <= now)
                OR (status=processing AND locked_at <= now - stale_after)
                AND attempts < max_attempts.
    Sólo un worker verá la fila: `UPDATE ... WHERE ... RETURNING` toma el row
    lock implícito de PostgreSQL. Los demás ven 0 filas afectadas.
    """
    moment = now or _utcnow()
    stale_cutoff = moment - stale_after

    eligible = [
        or_(
            and_(
                NovedadOutbox.status.in_(
                    (NOVEDAD_OUTBOX_PENDING, NOVEDAD_OUTBOX_FAILED)
                ),
                NovedadOutbox.available_at <= moment,
            ),
            and_(
                NovedadOutbox.status == NOVEDAD_OUTBOX_PROCESSING,
                NovedadOutbox.locked_at.is_not(None),
                NovedadOutbox.locked_at <= stale_cutoff,
            ),
        ),
        NovedadOutbox.attempts < settings.novedad_outbox_max_attempts,
    ]
    if novedad_id is not None:
        eligible.append(NovedadOutbox.novedad_id == novedad_id)

    stmt = (
        update(NovedadOutbox)
        .where(
            NovedadOutbox.id.in_(
                select(NovedadOutbox.id)
                .where(*eligible)
                .order_by(NovedadOutbox.available_at, NovedadOutbox.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        )
        .values(
            status=NOVEDAD_OUTBOX_PROCESSING,
            locked_at=moment,
            attempts=NovedadOutbox.attempts + 1,
        )
        .returning(
            NovedadOutbox.id,
            NovedadOutbox.novedad_id,
            NovedadOutbox.attempts,
        )
    )
    row = (await db.execute(stmt)).first()
    await db.commit()
    if row is None:
        return None
    return ClaimedOutbox(
        outbox_id=row[0],
        novedad_id=row[1],
        attempts=int(row[2]),
    )


async def mark_sent(
    db: AsyncSession,
    *,
    claimed: ClaimedOutbox,
    issue_number: int | None,
    response: dict[str, Any] | None,
) -> bool:
    result = await db.execute(
        update(NovedadOutbox)
        .where(
            NovedadOutbox.id == claimed.outbox_id,
            NovedadOutbox.status == NOVEDAD_OUTBOX_PROCESSING,
            NovedadOutbox.attempts == claimed.attempts,
        )
        .values(
            status=NOVEDAD_OUTBOX_SENT,
            locked_at=None,
            last_error=None,
        )
        .returning(NovedadOutbox.id)
    )
    if result.first() is None:
        await db.rollback()
        return False
    await db.execute(
        update(Novedad)
        .where(Novedad.id == claimed.novedad_id)
        .values(
            cloudfleet_status="sent",
            cloudfleet_issue_number=issue_number,
            cloudfleet_response=response,
            cloudfleet_error=None,
        )
    )
    await db.commit()
    return True


async def mark_failed(
    db: AsyncSession,
    *,
    claimed: ClaimedOutbox,
    error: str,
    available_at: datetime,
) -> bool:
    result = await db.execute(
        update(NovedadOutbox)
        .where(
            NovedadOutbox.id == claimed.outbox_id,
            NovedadOutbox.status == NOVEDAD_OUTBOX_PROCESSING,
            NovedadOutbox.attempts == claimed.attempts,
        )
        .values(
            status=NOVEDAD_OUTBOX_FAILED,
            locked_at=None,
            last_error=error[:1000],
            available_at=available_at,
        )
        .returning(NovedadOutbox.id)
    )
    if result.first() is None:
        await db.rollback()
        return False
    await db.execute(
        update(Novedad)
        .where(Novedad.id == claimed.novedad_id)
        .values(cloudfleet_status="failed", cloudfleet_error=error[:1000])
    )
    await db.commit()
    return True


async def mark_retry(
    db: AsyncSession,
    *,
    outbox_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    """Hace un item failed elegible AHORA sin esperar el backoff programado.

    Devuelve True si el item estaba en failed y pasó a pending. False si no
    se pudo re-eligibilizar (p. ej. un worker ya lo tiene en processing).
    """
    moment = now or _utcnow()
    result = await db.execute(
        update(NovedadOutbox)
        .where(
            NovedadOutbox.id == outbox_id,
            NovedadOutbox.status == NOVEDAD_OUTBOX_FAILED,
        )
        .values(
            status=NOVEDAD_OUTBOX_PENDING,
            attempts=0,
            available_at=moment,
            locked_at=None,
        )
        .returning(NovedadOutbox.id)
    )
    await db.commit()
    return result.first() is not None


async def get_outbox_for_novedad(
    db: AsyncSession, *, novedad_id: uuid.UUID
) -> NovedadOutbox | None:
    stmt = select(NovedadOutbox).where(NovedadOutbox.novedad_id == novedad_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def process_claimed(claimed: ClaimedOutbox) -> bool:
    """Procesa un item reclamado: llama a Cloudfleet y actualiza Novedad +
    outbox en un commit. Devuelve True si quedó en `sent`.
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        novedad = (
            await db.execute(
                select(Novedad)
                .join(NovedadOutbox, NovedadOutbox.novedad_id == Novedad.id)
                .where(
                    Novedad.id == claimed.novedad_id,
                    NovedadOutbox.id == claimed.outbox_id,
                    NovedadOutbox.status == NOVEDAD_OUTBOX_PROCESSING,
                    NovedadOutbox.attempts == claimed.attempts,
                )
            )
        ).scalar_one_or_none()
        if novedad is None:
            return False

        if novedad.cloudfleet_status == "sent":
            # Ya enviado por otro camino: reconciliar outbox sin reenviar.
            return await mark_sent(
                db,
                claimed=claimed,
                issue_number=novedad.cloudfleet_issue_number,
                response=novedad.cloudfleet_response,
            )

        payload = cloudfleet_service.build_issue_payload(
            vehicle_code=novedad.vehicle_code,
            reported_at=novedad.reported_at.isoformat(),
            reported_by_id=novedad.reported_by_id,
            priority=novedad.priority,
            odometer=novedad.odometer,
            comment=novedad.comment,
            send_mail=novedad.send_mail,
        )
        try:
            result = await cloudfleet_service.create_issue(payload)
        except CloudfleetError as exc:
            available_at = _utcnow() + _compute_backoff(claimed.attempts)
            return await mark_failed(
                db,
                claimed=claimed,
                error=str(exc),
                available_at=available_at,
            )
        return await mark_sent(
            db,
            claimed=claimed,
            issue_number=result.issue_number,
            response=result.response,
        )


async def run_once() -> int:
    """Procesa un batch. Devuelve cuántos ítems se procesaron."""
    stale = timedelta(seconds=settings.novedad_outbox_stale_lock_seconds)
    batch = settings.novedad_outbox_batch_size
    processed = 0
    for _ in range(batch):
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            claimed = await claim_one(db, stale_after=stale)
        if claimed is None:
            break
        await process_claimed(claimed)
        processed += 1
    return processed


async def stats(db: AsyncSession) -> dict[str, int]:
    """Conteos por status, útil para diagnóstico."""
    stmt = select(NovedadOutbox.status, func.count(NovedadOutbox.id)).group_by(
        NovedadOutbox.status
    )
    rows = (await db.execute(stmt)).all()
    return {status_: int(count) for status_, count in rows}


def attach_outbox_state(novedad: Novedad) -> dict[str, Any] | None:
    """Helper de diagnóstico: expone el estado del outbox sin filtrarlo en
    la API pública. No se serializa a NovedadRead.
    """
    if novedad.outbox is None:
        return None
    o = novedad.outbox
    return {
        "status": o.status,
        "attempts": o.attempts,
        "available_at": o.available_at.isoformat() if o.available_at else None,
        "locked_at": o.locked_at.isoformat() if o.locked_at else None,
        "last_error": o.last_error,
    }


__all__ = [
    "ClaimedOutbox",
    "attach_outbox_state",
    "claim_one",
    "enqueue",
    "get_outbox_for_novedad",
    "mark_failed",
    "mark_retry",
    "mark_sent",
    "process_claimed",
    "run_once",
    "stats",
]
