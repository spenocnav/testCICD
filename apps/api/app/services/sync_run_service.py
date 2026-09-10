"""Registro y consulta del historial de corridas de sync (`sync_run`).

`start(...)`/`finish(...)` mantienen visibles las corridas internas largas
mientras están ejecutándose. `record(...)` conserva la ruta de inserción al
cierre para productores externos. Todas escriben en su PROPIA sesión/commit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.etl_trigger import EtlTriggerRequest
from app.models.sync_run import SyncRun
from app.schemas.data_quality import ActiveSyncRun

log = get_logger("sync-run")


async def start(
    *,
    kind: str,
    trigger: str,
    mode: str,
    started_at: datetime,
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
) -> uuid.UUID | None:
    """Publica una corrida en curso y devuelve su id.

    Se llama después de adquirir el advisory lock del orquestador. Si una
    ejecución anterior murió sin cerrar su fila, se marca como error antes de
    publicar la nueva. La auditoría es best-effort y nunca tumba el sync.
    """
    try:
        async with AsyncSessionLocal() as session:
            stale_rows = (
                await session.execute(
                    select(SyncRun).where(
                        SyncRun.kind == kind,
                        SyncRun.status == "running",
                    )
                )
            ).scalars()
            for stale in stale_rows:
                finished_at = max(started_at, stale.started_at)
                stale.status = "error"
                stale.finished_at = finished_at
                stale.duration_ms = max(
                    0, int((finished_at - stale.started_at).total_seconds() * 1000)
                )
                stale.error = "La ejecución se interrumpió antes de registrar su cierre."

            run = SyncRun(
                kind=kind,
                trigger=trigger,
                mode=mode,
                status="running",
                started_at=started_at,
                finished_at=None,
                duration_ms=None,
                actor_user_id=actor_user_id,
                actor_email=actor_email,
            )
            session.add(run)
            await session.commit()
            return run.id
    except Exception:
        log.exception("sync_run_start_error", kind=kind, trigger=trigger)
        return None


async def finish(
    run_id: uuid.UUID | None,
    *,
    kind: str,
    trigger: str,
    mode: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
) -> None:
    """Cierra una corrida publicada por :func:`start`.

    Si no fue posible crear la fila activa, cae en `record` para no perder el
    resultado histórico.
    """
    if run_id is None:
        await record(
            kind=kind,
            trigger=trigger,
            mode=mode,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            result=result,
            error=error,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
        )
        return

    duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    try:
        async with AsyncSessionLocal() as session:
            run = await session.get(SyncRun, run_id)
            if run is None:
                raise LookupError(f"sync_run {run_id} no existe")
            run.status = status
            run.finished_at = finished_at
            run.duration_ms = duration_ms
            run.result = result
            run.error = error[:2000] if error else None
            await session.commit()
    except Exception:
        log.exception("sync_run_finish_error", kind=kind, trigger=trigger, run_id=str(run_id))


async def record(
    *,
    kind: str,
    trigger: str,
    mode: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
) -> None:
    """Inserta una fila de auditoría. Nunca propaga: registrar no debe tumbar
    el sync ni el request que lo disparó."""
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    try:
        async with AsyncSessionLocal() as session:
            session.add(
                SyncRun(
                    kind=kind,
                    trigger=trigger,
                    mode=mode,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    result=result,
                    error=error[:2000] if error else None,
                    actor_user_id=actor_user_id,
                    actor_email=actor_email,
                )
            )
            await session.commit()
    except Exception:  # auditoría best-effort
        log.exception("sync_run_record_error", kind=kind, trigger=trigger)


async def list_recent(
    *, kind: str | None = None, limit: int = 20, offset: int = 0
) -> tuple[list[SyncRun], int]:
    """Página de corridas, más recientes primero, y el total del filtro.

    Devuelve `(items, total)`. `kind` filtra opcionalmente.

    Se desempata por `id` porque `started_at` no es único: dos corridas de
    distinto tipo pueden arrancar en el mismo instante y, sin un orden total,
    una fila podría repetirse o saltarse entre páginas.

    OFFSET es suficiente acá: `sync_run` crece de a una fila por corrida y la
    auditoría se mira por la primera página. Si algún día se pagina hondo,
    conviene keyset por `(started_at DESC, id DESC)`.
    """
    where = [SyncRun.kind == kind] if kind else []

    items_stmt = (
        select(SyncRun)
        .where(SyncRun.status != "running")
        .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    count_stmt = select(func.count()).select_from(SyncRun).where(SyncRun.status != "running")
    for condition in where:
        items_stmt = items_stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(items_stmt)).scalars().all()
        total = (await session.execute(count_stmt)).scalar_one()
    return list(rows), int(total)


async def list_active(*, kind: str | None = None) -> list[ActiveSyncRun]:
    """Corridas EN CURSO (encoladas o corriendo), más viejas primero.

    Las corridas internas se publican como `sync_run.status = running`. El ETL
    externo de reportes conserva su estado en `etl_trigger_request`.

    El reloj es el del servidor (`now()` de Python contra `started_at`), para que
    el tiempo transcurrido no dependa del reloj del navegador.
    """
    internal_stmt = select(SyncRun).where(SyncRun.status == "running")
    if kind is not None:
        internal_stmt = internal_stmt.where(SyncRun.kind == kind)
    internal_stmt = internal_stmt.order_by(SyncRun.started_at)

    async with AsyncSessionLocal() as session:
        internal_rows = (await session.execute(internal_stmt)).scalars().all()

        etl_rows: list[EtlTriggerRequest] = []
        if kind is None or kind == "reportes":
            etl_stmt = (
                select(EtlTriggerRequest)
                .where(EtlTriggerRequest.status.in_(["pending", "running"]))
                .order_by(EtlTriggerRequest.created_at)
            )
            etl_rows = list((await session.execute(etl_stmt)).scalars().all())

    now = datetime.now(UTC)
    active = [
        ActiveSyncRun(
            id=str(row.id),
            kind=row.kind,  # type: ignore[arg-type]
            status="running",
            started_at=row.started_at,
            queued_at=row.started_at,
            elapsed_ms=max(0, int((now - row.started_at).total_seconds() * 1000)),
            actor_email=row.actor_email,
        )
        for row in internal_rows
    ]
    for row in etl_rows:
        # `pending` aún no fue reclamada: el tiempo se cuenta desde que se encoló,
        # que es lo que el usuario percibe como espera.
        origin = row.started_at or row.created_at
        active.append(
            ActiveSyncRun(
                id=str(row.id),
                kind="reportes",
                status=row.status,  # type: ignore[arg-type]
                started_at=row.started_at,
                queued_at=row.created_at,
                elapsed_ms=max(0, int((now - origin).total_seconds() * 1000)),
                actor_email=row.requested_by_email,
            )
        )
    return sorted(active, key=lambda item: item.queued_at)
