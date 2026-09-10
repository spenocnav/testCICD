"""Dispara el sync de la réplica de CloudFleet (vehículos, OTs, cronogramas).

Réplica local alimentada por upsert idempotente contra la API de CloudFleet.
Ver app/services/cloudfleet_sync_service.py.

Requiere en el entorno:
  - CLOUDFLEET_BASE_URL, CLOUDFLEET_API_KEY (token de CloudFleet).

Uso (desde apps/api):
  uv run python scripts/run_cloudfleet_sync.py                  # una pasada incremental
  uv run python scripts/run_cloudfleet_sync.py --full           # una pasada full
  uv run python scripts/run_cloudfleet_sync.py --from 2024-01-01
  uv run python scripts/run_cloudfleet_sync.py --refresh-days 30
  uv run python scripts/run_cloudfleet_sync.py --loop           # worker: loop por intervalo

El modo `--loop` es el que corre el servicio `cloudfleet-sync-worker` de
docker-compose (perfil "full"): una pasada incremental cada
`CLOUDFLEET_SYNC_INTERVAL_SECONDS` (default diario), sobreviviendo a fallos
transitorios para no reiniciar el proceso innecesariamente.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from contextlib import suppress
from datetime import date

sys.path.insert(0, ".")

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import engine
from app.services.cloudfleet_sync_service import run_cloudfleet_sync

configure_logging()
log = get_logger("cloudfleet-sync")


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


async def _tick(*, full: bool, date_from: date | None, refresh_days: int) -> None:
    """Una pasada. No propaga excepciones para mantener el loop vivo."""
    try:
        result = await run_cloudfleet_sync(
            full=full, date_from=date_from, refresh_days=refresh_days, trigger="worker"
        )
    except Exception:
        log.exception("cloudfleet_sync_error")
        return
    log.info(
        "cloudfleet_sync_done",
        mode="full" if full else "incremental",
        vehicles_upserted=result["vehicles_upserted"],
        work_orders_upserted=result["work_orders_upserted"],
        schedules_inserted=result["schedules_inserted"],
        meters_sent=result["meters_sent"],
        meters_failed=result["meters_failed"],
        meters_uncertain=result["meters_uncertain"],
    )


def _print_summary(result: dict[str, int], *, full: bool) -> None:
    modo = "full" if full else "incremental"
    print(
        f"CloudFleet sync {modo} OK: "
        f"vehículos fetched={result['vehicles_fetched']} "
        f"upserted={result['vehicles_upserted']} "
        f"marked_absent={result['vehicles_marked_absent']}; "
        f"OTs fetched={result['work_orders_fetched']} "
        f"upserted={result['work_orders_upserted']}; "
        f"schedules fetched={result['schedules_fetched']} "
        f"inserted={result['schedules_inserted']}; "
        f"meters sent={result['meters_sent']} "
        f"failed={result['meters_failed']} "
        f"uncertain={result['meters_uncertain']} "
        f"reconciled={result['meters_reconciled']}."
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync de la réplica de CloudFleet (vehículos, OTs, cronogramas)."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full sync (ignora watermark de OTs, default_from se mantiene).",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        type=str,
        default=None,
        help="Fecha mínima ISO (YYYY-MM-DD) para el rango incremental.",
    )
    parser.add_argument(
        "--refresh-days",
        type=int,
        default=45,
        help="Días hacia atrás desde el watermark para re-traer OTs (default 45).",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Corre como worker: una pasada cada intervalo hasta recibir SIGTERM.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.cloudfleet_sync_interval_seconds,
        help="Segundos entre pasadas en modo --loop (default: settings, diario).",
    )
    args = parser.parse_args()

    date_from: date | None = None
    if args.date_from:
        try:
            date_from = date.fromisoformat(args.date_from)
        except ValueError:
            parser.error(f"--from inválido: {args.date_from!r} (esperado YYYY-MM-DD)")

    if not args.loop:
        # Una sola pasada (CLI / cron externo). Propaga fallos por exit code.
        result = await run_cloudfleet_sync(
            full=args.full,
            date_from=date_from,
            refresh_days=args.refresh_days,
            trigger="cli",
        )
        _print_summary(result, full=args.full)
        with suppress(Exception):
            await engine.dispose()
        return

    log.info(
        "cloudfleet_sync_worker_starting",
        interval_s=args.interval,
        refresh_days=args.refresh_days,
    )
    shutdown = _Shutdown()
    _install_signals(shutdown)
    try:
        while not shutdown.requested:
            await _tick(full=args.full, date_from=date_from, refresh_days=args.refresh_days)
            with suppress(TimeoutError):
                await asyncio.wait_for(shutdown.event.wait(), timeout=args.interval)
        log.info("cloudfleet_sync_worker_shutdown")
    finally:
        with suppress(Exception):
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
