"""Proceso de housekeeping de refresh tokens.

Borra, de forma idempotente y por lotes, las filas de
``refresh_tokens`` que llevan revocadas o expiradas más allá del
margen de retención configurado. Vive como servicio separado en
``docker-compose.yml`` (perfil "full") para no contaminar el proceso
del API ni inflar su ventana de deploy.

Uso (desde apps/api):
  uv run python scripts/run_refresh_token_purge.py            # loop infinito
  uv run python scripts/run_refresh_token_purge.py --once    # una pasada y sale
  uv run python scripts/run_refresh_token_purge.py \
      --interval 1800 --batch-size 500                       # overrides por CLI

Variables relevantes (settings):
  REFRESH_TOKEN_PURGE_INTERVAL_SECONDS, REFRESH_TOKEN_PURGE_RETENTION_SECONDS,
  REFRESH_TOKEN_PURGE_BATCH_SIZE.

Las credenciales se leen del mismo entorno que el API (DATABASE_URL
o DATABASE_URL_LOCAL según entorno), nunca del código.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from contextlib import suppress

sys.path.insert(0, ".")

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import AsyncSessionLocal, engine
from app.services.refresh_token_purge_service import purge_once

configure_logging()
log = get_logger("refresh-token-purge")


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


async def _tick(*, batch_size: int) -> int:
    """Una pasada. Devuelve cuántas filas se borraron."""
    async with AsyncSessionLocal() as session:
        try:
            result = await purge_once(session, batch_size=batch_size)
        except Exception:
            # Mantener el loop vivo: un fallo transitorio de DB no debe
            # tumbar el proceso (k8s lo reiniciaría innecesariamente).
            await session.rollback()
            log.exception("refresh_token_purge_error")
            return 0
        if result.deleted:
            log.info(
                "refresh_token_purge_batch",
                deleted=result.deleted,
                cutoff=result.cutoff.isoformat(),
            )
        return result.deleted


async def _serve(
    *,
    interval: float,
    once: bool,
    batch_size: int,
    shutdown: _Shutdown,
) -> None:
    if once:
        deleted = await _tick(batch_size=batch_size)
        log.info("refresh_token_purge_once_done", deleted=deleted)
        return

    while not shutdown.requested:
        deleted = await _tick(batch_size=batch_size)
        # Si la última pasada llenó el batch, es probable que haya más.
        if deleted >= batch_size:
            # Pequeño respiro para no saturar la DB.
            with suppress(asyncio.CancelledError):
                await asyncio.sleep(0.1)
            continue
        try:
            await asyncio.wait_for(shutdown.event.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
    log.info("refresh_token_purge_shutdown")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Housekeeping de refresh tokens (purga idempotente)."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.refresh_token_purge_interval_seconds,
        help="Segundos entre pasadas cuando el batch no se llenó.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=settings.refresh_token_purge_batch_size,
        help="Máximo de filas borradas por pasada.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta una sola pasada y sale (útil para tests/cron).",
    )
    args = parser.parse_args()

    log.info(
        "refresh_token_purge_starting",
        interval_s=args.interval,
        batch_size=args.batch_size,
        retention_s=settings.refresh_token_purge_retention_seconds,
        once=args.once,
    )

    shutdown = _Shutdown()
    if not args.once:
        _install_signals(shutdown)

    try:
        await _serve(
            interval=args.interval,
            once=args.once,
            batch_size=args.batch_size,
            shutdown=shutdown,
        )
    finally:
        with suppress(Exception):
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
