"""Tracker de uso: cola en memoria + escritura en lote a `usage_events`.

El middleware llama a `tracker.record(...)` por cada petición autenticada; el
task de fondo (arrancado en el lifespan de la app) drena la cola cada
`usage_flush_interval_seconds` y persiste en un solo INSERT multi-fila.

Principios que no se deben romper:
- `record` NUNCA bloquea ni lanza: si la cola está llena, el evento se
  descarta y se cuenta. Telemetría perdida < petición frenada (PERF-009: el
  pool es chico y compartido).
- Un fallo del flush descarta el lote con un warning, no reintenta en bucle:
  reintentar martillaría una base que ya está mal.
- La retención se poda una vez al día desde el mismo task, no por petición.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, insert

from app.core.config import settings
from app.core.logging import get_logger
from app.models.usage import UsageEvent

logger = get_logger("usage_tracker")

_PRUNE_INTERVAL_SECONDS = 60 * 60 * 24


@dataclass(slots=True)
class _Event:
    ts: datetime
    user_id: uuid.UUID
    method: str
    route: str
    status_code: int
    duration_ms: int
    fleet_ids: list[uuid.UUID] | None


class UsageTracker:
    """Cola acotada + flusher. Una instancia por proceso (singleton de módulo)."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[_Event] = asyncio.Queue(
            maxsize=settings.usage_queue_max_size
        )
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0
        self._last_prune_monotonic = 0.0

    def record(
        self,
        *,
        user_id: uuid.UUID,
        method: str,
        route: str,
        status_code: int,
        duration_ms: float,
        fleet_ids: list[uuid.UUID] | None,
    ) -> None:
        """Encola sin bloquear. Descarta silenciosamente si la cola está llena."""
        if not settings.usage_tracking_enabled:
            return
        event = _Event(
            ts=datetime.now(UTC),
            user_id=user_id,
            method=method[:8],
            route=route[:200],
            status_code=status_code,
            duration_ms=max(0, round(duration_ms)),
            fleet_ids=fleet_ids or None,
        )
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped % 1000 == 1:
                logger.warning("usage_events_dropped", total_dropped=self._dropped)

    def start(self) -> None:
        if self._task is None and settings.usage_tracking_enabled:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        """Cancela el loop y hace un último flush de lo encolado."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._flush()

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(settings.usage_flush_interval_seconds)
            try:
                await self._flush()
                await self._maybe_prune()
            except asyncio.CancelledError:
                raise
            except Exception:
                # El lote drenado se pierde; el flush siguiente arranca limpio.
                logger.warning("usage_flush_failed", exc_info=True)

    def _drain(self) -> list[_Event]:
        batch: list[_Event] = []
        limit = settings.usage_flush_batch_size
        while len(batch) < limit:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    async def _flush(self) -> None:
        batch = self._drain()
        if not batch:
            return
        # Import diferido: evita el ciclo config -> session -> config al importar.
        from app.db.session import AsyncSessionLocal

        rows = [
            {
                "ts": e.ts,
                "user_id": e.user_id,
                "method": e.method,
                "route": e.route,
                "status_code": e.status_code,
                "duration_ms": e.duration_ms,
                "fleet_ids": e.fleet_ids,
            }
            for e in batch
        ]
        async with AsyncSessionLocal() as session:
            await session.execute(insert(UsageEvent), rows)
            await session.commit()

    async def _maybe_prune(self) -> None:
        now = time.monotonic()
        if now - self._last_prune_monotonic < _PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune_monotonic = now
        cutoff = datetime.now(UTC) - timedelta(days=settings.usage_retention_days)
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            result = await session.execute(delete(UsageEvent).where(UsageEvent.ts < cutoff))
            await session.commit()
        if result.rowcount:
            logger.info("usage_events_pruned", rows=result.rowcount)


tracker = UsageTracker()
