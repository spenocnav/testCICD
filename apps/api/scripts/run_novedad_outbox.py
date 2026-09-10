"""Worker durable para el outbox transaccional de Novedades.

Consume items en estado `pending` (o `processing` con `locked_at` antiguo) y
los envía a Cloudfleet. Tras éxito marca `sent`; tras error aplica backoff
exponencial. Manejo de señales para cierre limpio.

Uso (desde apps/api):
  uv run python scripts/run_novedad_outbox.py
  uv run python scripts/run_novedad_outbox.py --once        # procesa 1 batch y sale
  uv run python scripts/run_novedad_outbox.py --interval 2  # poll cada 2s

Variables relevantes (settings):
  NOVEDAD_OUTBOX_BATCH_SIZE, NOVEDAD_OUTBOX_POLL_INTERVAL_SECONDS,
  NOVEDAD_OUTBOX_STALE_LOCK_SECONDS, NOVEDAD_OUTBOX_MAX_ATTEMPTS,
  NOVEDAD_OUTBOX_BACKOFF_BASE_SECONDS, NOVEDAD_OUTBOX_BACKOFF_MAX_SECONDS.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from contextlib import suppress

sys.path.insert(0, ".")

from app.core.config import settings
from app.services.novedad_outbox_service import run_once


class _Shutdown:
    """Flag cooperativo. Las señales lo prenden."""

    def __init__(self) -> None:
        self.requested = False


async def _serve(
    *,
    interval: float,
    once: bool,
    shutdown: _Shutdown,
) -> None:
    while not shutdown.requested:
        try:
            processed = await run_once()
        except Exception as exc:  # mantener el loop vivo
            print(f"[outbox-worker] error en run_once: {exc!r}", flush=True)
            processed = 0
        if once:
            print(f"[outbox-worker] --once: procesados={processed}", flush=True)
            return
        # Si procesamos un batch lleno, no dormimos: probablemente hay más.
        if processed < settings.novedad_outbox_batch_size:
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(
                    asyncio.shield(asyncio.sleep(interval)),
                    timeout=interval + 1,
                )
                if shutdown.requested:
                    return
        if shutdown.requested:
            return
    print("[outbox-worker] shutdown solicitado, saliendo", flush=True)


def _install_signals(shutdown: _Shutdown) -> None:
    loop = asyncio.get_running_loop()

    def _handler() -> None:
        shutdown.requested = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, _handler)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Worker outbox para el envío de Novedades a Cloudfleet."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.novedad_outbox_poll_interval_seconds,
        help="Segundos entre polls cuando el batch está vacío.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Procesa un solo batch y sale (útil para tests/cron).",
    )
    args = parser.parse_args()

    shutdown = _Shutdown()
    if not args.once:
        _install_signals(shutdown)

    print(
        f"[outbox-worker] arrancando "
        f"(interval={args.interval}s, batch={settings.novedad_outbox_batch_size}, "
        f"stale={settings.novedad_outbox_stale_lock_seconds}s)",
        flush=True,
    )
    await _serve(interval=args.interval, once=args.once, shutdown=shutdown)


if __name__ == "__main__":
    asyncio.run(main())
