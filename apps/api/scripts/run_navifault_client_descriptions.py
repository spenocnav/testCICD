#!/usr/bin/env python3
"""Worker durable de comunicaciones cliente Navifault bajo demanda."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from contextlib import suppress

sys.path.insert(0, ".")

from app.core.config import settings
from app.services.navifault_client_description_service import (
    run_lane_once,
    run_once,
    sweep_once,
)


class _Shutdown:
    def __init__(self) -> None:
        self.requested = False


def _install_signals(shutdown: _Shutdown) -> None:
    loop = asyncio.get_running_loop()

    def _handler() -> None:
        shutdown.requested = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, _handler)


async def _sweep_loop(shutdown: _Shutdown) -> None:
    """Pregenera las fallas recientes para que la ficha ya las tenga al abrirse.

    Un fallo aquí no puede tumbar al worker: los carriles son lo que un usuario
    está esperando.
    """
    while not shutdown.requested:
        if not settings.navifault_llm_sweep_enabled:
            await _sleep_until(shutdown, 5.0)
            continue
        try:
            reviewed, enqueued = await sweep_once()
        except Exception as exc:
            print(f"[navifault-client-worker] error en sweep_once: {exc!r}", flush=True)
        else:
            if enqueued:
                print(
                    f"[navifault-client-worker] barrido: llaves={reviewed} "
                    f"encoladas={enqueued}",
                    flush=True,
                )
        await _sleep_until(shutdown, settings.navifault_llm_sweep_interval_seconds)


async def _lane_loop(lane: str, *, interval: float, shutdown: _Shutdown) -> None:
    """Consume un carril en su propio bucle.

    Cada carril avanza a su ritmo: si el de fondo está a mitad de una
    generación, el interactivo no lo espera y toma su trabajo en el siguiente
    sondeo.
    """
    while not shutdown.requested:
        try:
            processed = await run_lane_once(lane)  # type: ignore[arg-type]
        except Exception as exc:  # Mantiene el carril vivo ante un error aislado.
            print(f"[navifault-client-worker] error en carril {lane}: {exc!r}", flush=True)
            processed = 0
        if processed == 0:
            await _sleep_until(shutdown, interval)


async def _sleep_until(shutdown: _Shutdown, seconds: float) -> None:
    """Duerme en tramos cortos para no retrasar el apagado."""
    remaining = seconds
    while remaining > 0 and not shutdown.requested:
        step = min(1.0, remaining)
        with suppress(asyncio.CancelledError):
            await asyncio.sleep(step)
        remaining -= step


async def _serve(*, interval: float, once: bool, shutdown: _Shutdown) -> None:
    if once:
        try:
            processed = await run_once()
        except Exception as exc:
            print(f"[navifault-client-worker] error en run_once: {exc!r}", flush=True)
            processed = 0
        print(f"[navifault-client-worker] --once: procesados={processed}", flush=True)
        return
    await asyncio.gather(
        _lane_loop("interactive", interval=interval, shutdown=shutdown),
        _lane_loop("background", interval=interval, shutdown=shutdown),
        _sweep_loop(shutdown),
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Worker LLM de comunicaciones cliente Navifault."
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.navifault_llm_worker_poll_seconds,
        help="Segundos entre consultas cuando no quedan elementos pendientes.",
    )
    parser.add_argument("--once", action="store_true", help="Procesa un batch y termina.")
    args = parser.parse_args()

    shutdown = _Shutdown()
    if not args.once:
        _install_signals(shutdown)
    print(
        "[navifault-client-worker] arrancando "
        f"(interval={args.interval}s, batch={settings.navifault_llm_worker_batch_size}, "
        f"slots={settings.navifault_llm_interactive_slots}"
        f"+{settings.navifault_llm_background_slots}, "
        f"barrido={'on' if settings.navifault_llm_sweep_enabled else 'off'}"
        f"/{settings.navifault_llm_sweep_interval_seconds}s"
        f"/{settings.navifault_llm_sweep_hours}h)",
        flush=True,
    )
    await _serve(interval=args.interval, once=args.once, shutdown=shutdown)


if __name__ == "__main__":
    asyncio.run(main())
