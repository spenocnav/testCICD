"""Orquestador diario: maestra → reportes → CloudFleet → estado de novedades.

A la hora local configurada (`DAILY_SYNC_AT` en `DAILY_SYNC_TIMEZONE`, default
04:00 America/Bogota) corre los cuatro pasos EN ORDEN y de a uno:

  1. Fuente maestra (Navi Vehículos): `sync_service.run_sync(full=True)` —
     flotas, bases geotab, credenciales, reglas y vehículos. Va primero porque
     el ETL y los reportes dependen del catálogo de vehículos/credenciales. Es
     completo a propósito: un cambio de clasificación puede no modificar el
     `updated_at` que usa el sync incremental.
  2. Reportes (InformesRendimiento): inserta una solicitud `pending` en
     `etl_trigger_request` —igual que el botón del portal— y ESPERA a que el
     contenedor `reportes-etl-worker` la cierre. No corre el pipeline acá: ese
     código vive en otra imagen con las dependencias del ETL.
  3. Réplica CloudFleet: `cloudfleet_sync_service.run_cloudfleet_sync`
     (vehículos, OTs, cronogramas).
  4. Estado de novedades: `novedad_status_sync_service.run_novedad_status_sync`
     replica `isDone`/`doneAt`/`workOrderDoneNumber` de las issues de
     CloudFleet sobre `novedades.external_*`. Va después de la réplica porque
     comparte con ella el presupuesto de 30 peticiones/min del proveedor. Se
     apaga con `DAILY_SYNC_NOVEDADES_ENABLED=false`.

Un paso que falla NO detiene a los siguientes: son fuentes independientes y un
proveedor caído no debe dejar al resto sin datos frescos. Cada paso queda
auditado en `sync_run` (kind master/reportes/cloudfleet/novedades,
trigger=worker) por su propio servicio; acá solo se registra el encadenado.

Los pasos 1, 3 y 4 toman advisory lock (`navi-portal:master-sync`,
`navi-portal:cloudfleet-sync`, `navi-portal:novedad-status-sync`) y el 2 se
serializa por `etl_trigger_request`: un disparo manual desde el portal a la
misma hora no duplica trabajo, se salta.

Reemplaza el loop por intervalo del antiguo `cloudfleet-sync-worker`, que
disparaba a la hora de arranque del contenedor y no coordinaba con los otros.

Uso (desde apps/api):
  uv run python scripts/run_daily_sync.py                  # worker: espera la hora
  uv run python scripts/run_daily_sync.py --once           # corre la cadena ya y sale
  uv run python scripts/run_daily_sync.py --once --steps master,cloudfleet
  uv run python scripts/run_daily_sync.py --at 05:30 --tz UTC

Variables relevantes (settings): DAILY_SYNC_ENABLED, DAILY_SYNC_AT,
DAILY_SYNC_TIMEZONE, DAILY_SYNC_ETL_TIMEOUT_SECONDS, DAILY_SYNC_ETL_POLL_SECONDS,
DAILY_SYNC_CLOUDFLEET_REFRESH_DAYS, DAILY_SYNC_NOVEDADES_ENABLED.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.advisory_lock import OperationAlreadyRunningError
from app.db.session import AsyncSessionLocal, engine
from app.models.etl_trigger import EtlTriggerRequest
from app.services.cloudfleet_sync_service import run_cloudfleet_sync
from app.services.novedad_status_sync_service import run_novedad_status_sync
from app.services.sync_service import run_sync

configure_logging()
log = get_logger("daily-sync")

STEPS = ("master", "reportes", "cloudfleet", "novedades")

# Cotas del sleep: en vez de dormir hasta la hora de una sola vez, se despierta
# cada rato y recalcula. Así un salto de reloj del host (NTP, suspensión) no
# corre la ventana ni salta un día entero.
_MAX_SLEEP_SECONDS = 300.0


class _Shutdown:
    def __init__(self) -> None:
        self.event = asyncio.Event()

    @property
    def requested(self) -> bool:
        return self.event.is_set()


def _install_signals(shutdown: _Shutdown) -> None:
    loop = asyncio.get_running_loop()

    def _handler() -> None:
        shutdown.event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, _handler)


def next_fire_at(now: datetime, *, at: str, tz: ZoneInfo) -> datetime:
    """Próximo `at` (HH:MM) en `tz`, estrictamente futuro, devuelto en UTC."""
    hour, minute = (int(part) for part in at.split(":"))
    local = now.astimezone(tz)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        # Se recalcula sobre el día siguiente (no se suma un delta al instante)
        # para que la hora de pared se mantenga aunque cambie el offset.
        tomorrow = (local + timedelta(days=1)).date()
        candidate = datetime.combine(tomorrow, candidate.timetz().replace(tzinfo=None), tzinfo=tz)
    return candidate.astimezone(UTC)


async def _step_master() -> None:
    # La clasificación de motor debe reconciliarse aunque el vehículo siga
    # activo y la fuente no lo marque como cambiado en el delta incremental.
    result = await run_sync(full=True, trigger="worker")
    log.info(
        "daily_sync_master_done",
        fleets=result.fleets,
        databases=result.databases,
        credentials=result.credentials,
        rules=result.rules,
        vehicles=result.vehicles,
    )


async def _enqueue_etl() -> tuple[str, bool]:
    """Encola una corrida del ETL. Devuelve (request_id, era_nueva).

    Si ya hay una `pending`/`running` (disparo manual del portal), se engancha a
    esa en vez de apilar otra: el worker del ETL corre una sola a la vez.
    """
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(EtlTriggerRequest)
                .where(EtlTriggerRequest.status.in_(["pending", "running"]))
                .order_by(EtlTriggerRequest.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return str(existing.id), False

        request = EtlTriggerRequest(status="pending", requested_by_email=None)
        session.add(request)
        await session.commit()
        return str(request.id), True


async def _etl_status(request_id: str) -> str | None:
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(
                select(EtlTriggerRequest.status).where(EtlTriggerRequest.id == request_id)
            )
        ).scalar_one_or_none()


async def _step_reportes(*, timeout: float, poll: float, shutdown: _Shutdown) -> str:
    request_id, created = await _enqueue_etl()
    log.info("daily_sync_reportes_queued", request_id=request_id, created=created)

    deadline = asyncio.get_running_loop().time() + timeout
    while not shutdown.requested:
        status = await _etl_status(request_id)
        if status in {"done", "error"} or status is None:
            log.info(
                "daily_sync_reportes_finished",
                request_id=request_id,
                status=status or "missing",
            )
            if status == "error":
                raise RuntimeError(f"El ETL cerró la solicitud {request_id} con error.")
            return "ok"
        if asyncio.get_running_loop().time() >= deadline:
            # La solicitud sigue viva y el worker del ETL la va a cerrar; solo
            # se deja de esperar para no perder el paso de CloudFleet.
            raise TimeoutError(
                f"El ETL no terminó en {timeout:.0f}s (solicitud {request_id}, estado {status})."
            )
        with suppress(TimeoutError):
            await asyncio.wait_for(shutdown.event.wait(), timeout=poll)
    # SIGTERM durante la espera: la solicitud queda encolada para el worker del
    # ETL, pero esta corrida no la vio terminar.
    return "aborted"


async def _step_cloudfleet(*, refresh_days: int) -> None:
    result = await run_cloudfleet_sync(full=False, refresh_days=refresh_days, trigger="worker")
    log.info(
        "daily_sync_cloudfleet_done",
        vehicles_upserted=result["vehicles_upserted"],
        work_orders_upserted=result["work_orders_upserted"],
        schedules_inserted=result["schedules_inserted"],
    )


async def _step_novedades() -> str:
    if not settings.daily_sync_novedades_enabled:
        log.info("daily_sync_novedades_disabled")
        return "skipped"
    result = await run_novedad_status_sync(trigger="worker")
    log.info(
        "daily_sync_novedades_done",
        candidates=result["candidates"],
        resolved_now=result["resolved_now"],
        still_open=result["still_open"],
        missing=result["missing"],
    )
    return "partial" if result["missing"] else "ok"


async def run_chain(
    *,
    steps: tuple[str, ...],
    etl_timeout: float,
    etl_poll: float,
    refresh_days: int,
    shutdown: _Shutdown,
) -> dict[str, str]:
    """Corre los pasos en orden. Devuelve el estado por paso; nunca propaga."""
    outcome: dict[str, str] = {}
    started = datetime.now(UTC)
    log.info("daily_sync_chain_start", steps=list(steps))

    for step in STEPS:
        if step not in steps:
            outcome[step] = "skipped"
            continue
        if shutdown.requested:
            outcome[step] = "aborted"
            continue
        try:
            if step == "master":
                await _step_master()
                outcome[step] = "ok"
            elif step == "reportes":
                outcome[step] = await _step_reportes(
                    timeout=etl_timeout, poll=etl_poll, shutdown=shutdown
                )
            elif step == "cloudfleet":
                await _step_cloudfleet(refresh_days=refresh_days)
                outcome[step] = "ok"
            else:
                outcome[step] = await _step_novedades()
        except OperationAlreadyRunningError:
            # Otro proceso (botón del portal, CLI) ya está corriendo ese paso.
            log.warning("daily_sync_step_busy", step=step)
            outcome[step] = "busy"
        except TimeoutError as exc:
            log.warning("daily_sync_step_timeout", step=step, error=str(exc))
            outcome[step] = "timeout"
        except Exception:
            # La cadena sigue: cada fuente es independiente.
            log.exception("daily_sync_step_error", step=step)
            outcome[step] = "error"

    log.info(
        "daily_sync_chain_done",
        duration_s=round((datetime.now(UTC) - started).total_seconds(), 1),
        **outcome,
    )
    return outcome


async def _serve(
    *,
    at: str,
    tz: ZoneInfo,
    steps: tuple[str, ...],
    etl_timeout: float,
    etl_poll: float,
    refresh_days: int,
    shutdown: _Shutdown,
) -> None:
    while not shutdown.requested:
        target = next_fire_at(datetime.now(UTC), at=at, tz=tz)
        log.info(
            "daily_sync_waiting",
            next_run_local=target.astimezone(tz).isoformat(),
            next_run_utc=target.isoformat(),
        )
        while not shutdown.requested:
            remaining = (target - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    shutdown.event.wait(), timeout=min(remaining, _MAX_SLEEP_SECONDS)
                )
        if shutdown.requested:
            break
        await run_chain(
            steps=steps,
            etl_timeout=etl_timeout,
            etl_poll=etl_poll,
            refresh_days=refresh_days,
            shutdown=shutdown,
        )
    log.info("daily_sync_shutdown")


def _parse_steps(raw: str) -> tuple[str, ...]:
    requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(STEPS))
    if unknown:
        raise argparse.ArgumentTypeError(f"pasos desconocidos: {unknown} (válidos: {list(STEPS)})")
    # Se devuelve en el orden canónico, no en el que los escribió el usuario.
    return tuple(step for step in STEPS if step in requested)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orquestador diario: maestra → reportes → CloudFleet → estado de novedades."
    )
    parser.add_argument(
        "--at",
        default=settings.daily_sync_at,
        help="Hora local HH:MM de la corrida diaria (default: settings).",
    )
    parser.add_argument(
        "--tz",
        default=settings.daily_sync_timezone,
        help="Zona horaria de --at (default: settings, America/Bogota).",
    )
    parser.add_argument(
        "--steps",
        type=_parse_steps,
        default=STEPS,
        help="Subconjunto de pasos, separados por coma (default: los cuatro).",
    )
    parser.add_argument(
        "--etl-timeout",
        type=float,
        default=settings.daily_sync_etl_timeout_seconds,
        help="Segundos máximos de espera al ETL de reportes.",
    )
    parser.add_argument(
        "--refresh-days",
        type=int,
        default=settings.daily_sync_cloudfleet_refresh_days,
        help="Días hacia atrás para re-traer OTs de CloudFleet.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Corre la cadena inmediatamente y sale (cron externo / prueba).",
    )
    args = parser.parse_args()

    try:
        tz = ZoneInfo(args.tz)
    except Exception:
        parser.error(f"--tz inválido: {args.tz!r}")
    if not args.once:
        try:
            hour, minute = (int(part) for part in str(args.at).split(":"))
            datetime.now(tz).replace(hour=hour, minute=minute)
        except (ValueError, TypeError):
            parser.error(f"--at inválido: {args.at!r} (esperado HH:MM)")

    if not settings.daily_sync_enabled and not args.once:
        log.warning("daily_sync_disabled")
        return

    shutdown = _Shutdown()
    _install_signals(shutdown)

    log.info(
        "daily_sync_starting",
        at=args.at,
        timezone=args.tz,
        steps=list(args.steps),
        etl_timeout_s=args.etl_timeout,
        refresh_days=args.refresh_days,
        once=args.once,
    )

    try:
        if args.once:
            await run_chain(
                steps=args.steps,
                etl_timeout=args.etl_timeout,
                etl_poll=settings.daily_sync_etl_poll_seconds,
                refresh_days=args.refresh_days,
                shutdown=shutdown,
            )
            return
        await _serve(
            at=args.at,
            tz=tz,
            steps=args.steps,
            etl_timeout=args.etl_timeout,
            etl_poll=settings.daily_sync_etl_poll_seconds,
            refresh_days=args.refresh_days,
            shutdown=shutdown,
        )
    finally:
        with suppress(Exception):
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
