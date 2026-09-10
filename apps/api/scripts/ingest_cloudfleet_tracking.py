"""Ingesta de una publicación del sidecar de seguimiento CloudFleet.

Uso:
    uv run python scripts/ingest_cloudfleet_tracking.py --runtime /tracking-runtime

El proceso solo lee el runtime. El worker sidecar conserva la exclusividad de
escritura sobre sus archivos y este proceso publica la transacción en PostgreSQL.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")

from app.core.logging import configure_logging, get_logger
from app.db.session import AsyncSessionLocal
from app.services.cloudfleet_tracking_ingest_service import (
    ingest_runtime,
    record_ingest_health,
)

configure_logging()
log = get_logger("cloudfleet-tracking-ingest")


async def _publish_health(
    runtime_dir: str | Path,
    *,
    ok: bool,
    counters: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    """Write the heartbeat in its own transaction.

    Separate from the data session on purpose: when ingestion fails and its
    transaction rolls back, the failure heartbeat is exactly what has to
    survive so the portal can show the problem instead of a stale green dot.
    """
    try:
        async with AsyncSessionLocal() as session:
            await record_ingest_health(
                session,
                runtime_dir,
                ok=ok,
                counters=counters,
                error=error,
            )
            await session.commit()
    except Exception:
        # Nunca convertir un fallo del heartbeat en un fallo del ciclo.
        log.exception("cloudfleet_tracking_health_write_failed")


async def run_once(runtime_dir: str | Path) -> dict[str, int]:
    try:
        async with AsyncSessionLocal() as session:
            try:
                result = await ingest_runtime(session, runtime_dir)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception as exc:
        await _publish_health(runtime_dir, ok=False, error=f"{type(exc).__name__}: {exc}")
        raise
    await _publish_health(runtime_dir, ok=True, counters=result)
    log.info("cloudfleet_tracking_ingest_done", **result)
    return result


async def run_loop(runtime_dir: str | Path, interval_seconds: float) -> None:
    while True:
        try:
            await run_once(runtime_dir)
        except Exception:
            log.exception("cloudfleet_tracking_ingest_error")
        await asyncio.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        default=os.getenv("TRACKING_RUNTIME_DIR", "/tracking-runtime"),
        help="Directorio privado publicado por el worker sidecar",
    )
    parser.add_argument("--loop", action="store_true", help="Repite la ingesta periódicamente")
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.getenv("TRACKING_INGEST_INTERVAL_SECONDS", "30")),
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval debe ser mayor que cero")
    asyncio.run(run_loop(args.runtime, args.interval) if args.loop else run_once(args.runtime))


if __name__ == "__main__":
    main()
