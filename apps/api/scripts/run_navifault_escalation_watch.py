"""Vigilante de las fallas escaladas de Navifault a novedades de CloudFleet.

Por qué existe un sondeo y no un webhook: CloudFleet publica seis eventos y los
seis son de órdenes de trabajo y contadores. **No hay evento de novedades ni de
trabajos.** Y la transición que más importa —borrar el trabajo de la orden, que
reabre la novedad y le quita el número de orden— no cambia el estado de la orden,
así que no emitiría ninguno de los cuatro eventos que sí existen. El sondeo no es
una elección subóptima: es la única vía.

Por qué un proceso aparte del `daily-sync-worker`: para que un fallo de este
sondeo no pueda tumbar la cadena diaria, que es lo que alimenta los reportes.
Los dos llaman al mismo servicio y comparten su advisory lock, así que nunca
corren a la vez; el que llega segundo se salta la pasada y lo registra.

Alcance acotado a propósito: sólo las novedades escaladas cuyo ciclo de falla
sigue abierto. Son un puñado y gobiernan lo que se ve en pantalla. Sondear
además las novedades del módulo gastaría el presupuesto compartido de 30
peticiones por minuto en filas que nadie está esperando.

Uso (desde apps/api):
  uv run python scripts/run_navifault_escalation_watch.py
  uv run python scripts/run_navifault_escalation_watch.py --once
  uv run python scripts/run_navifault_escalation_watch.py --interval 300

Variables relevantes (settings):
  NAVIFAULT_ESCALATION_WATCH_ENABLED, NAVIFAULT_ESCALATION_WATCH_INTERVAL_SECONDS,
  CLOUDFLEET_ID_REPORTEDBY.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from contextlib import suppress

sys.path.insert(0, ".")

from app.core.config import settings
from app.core.logging import get_logger
from app.db.advisory_lock import OperationAlreadyRunningError
from app.db.session import AsyncSessionLocal
from app.services import navifault_order_service
from app.services.novedad_status_sync_service import run_novedad_status_sync

log = get_logger("navifault-escalation-watch")


class _Shutdown:
    """Bandera cooperativa. Las señales la prenden."""

    def __init__(self) -> None:
        self.requested = False


async def _una_pasada() -> None:
    try:
        # `trigger` sólo admite manual/worker/cli por CHECK, y "worker" es lo
        # que esto es. Lo que distingue esta corrida de la diaria en la
        # auditoría es `escalated_only` dentro del resultado.
        resumen = await run_novedad_status_sync(trigger="worker", escalated_only=True)
    except OperationAlreadyRunningError:
        # La cadena diaria o un disparo manual tienen el lock. No es un error:
        # esa pasada ya está haciendo el trabajo.
        log.info("navifault_escalation_watch_skipped", reason="lock_taken")
        return
    except Exception as exc:  # el bucle no puede morir por una pasada
        log.warning("navifault_escalation_watch_error", error=str(exc))
        return
    if resumen.get("candidates"):
        log.info("navifault_escalation_watch_done", **resumen)

    # Segunda mitad de la vigilancia: una gestión ya confirmada cuya orden se
    # anule después dejaría al portal diciendo "gestionada" sobre trabajo que ya
    # no existe. Nadie revierte desde el portal —eso dejaría huérfano el proceso
    # del taller—, así que la reversión se hereda de la orden.
    try:
        async with AsyncSessionLocal() as session:
            revertidas = await navifault_order_service.revert_unsupported_confirmations(session)
    except Exception as exc:  # el bucle no puede morir por una pasada
        log.warning("navifault_confirmation_watch_error", error=str(exc))
        return
    if revertidas.get("reverted"):
        log.info("navifault_confirmation_watch_done", **revertidas)


async def _servir(*, interval: float, once: bool, shutdown: _Shutdown) -> None:
    while not shutdown.requested:
        await _una_pasada()
        if once:
            return
        # Se duerme en tramos cortos para que una señal no espere el ciclo
        # completo: con 15 minutos de intervalo, un `docker stop` tardaría eso
        # en responder y acabaría en SIGKILL.
        restante = interval
        while restante > 0 and not shutdown.requested:
            paso = min(5.0, restante)
            await asyncio.sleep(paso)
            restante -= paso


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="una pasada y salir")
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="segundos entre pasadas (default: el de settings)",
    )
    return parser.parse_args()


async def _main() -> int:
    args = _parse_args()
    if not settings.navifault_escalation_watch_enabled and not args.once:
        log.info("navifault_escalation_watch_disabled")
        return 0
    if settings.cloudfleet_id_reportedby is None:
        # Sin reportante configurado el servicio no puede correr. Se avisa una
        # vez y se sale, en vez de repetir el mismo error cada intervalo.
        log.warning("navifault_escalation_watch_not_configured")
        return 0

    interval = args.interval or float(settings.navifault_escalation_watch_interval_seconds)
    shutdown = _Shutdown()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, lambda: setattr(shutdown, "requested", True))

    log.info("navifault_escalation_watch_start", interval_seconds=interval, once=args.once)
    await _servir(interval=interval, once=args.once, shutdown=shutdown)
    log.info("navifault_escalation_watch_stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
