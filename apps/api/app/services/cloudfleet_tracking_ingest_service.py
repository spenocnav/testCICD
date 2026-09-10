"""Ingesta segura del runtime privado del sidecar de etiquetas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cloudfleet_sync_service import (
    _set_watermark,
    get_watermark,
    parse_cf_datetime,
)
from app.services.cloudfleet_tracking_service import (
    normalize_tracking_row,
    upsert_tracking_events,
)

TRACKING_INGEST_WATERMARK = "cloudfleet_tracking_ingest"
TRACKING_HEALTH_STATE_KEY = "cloudfleet_tracking_health"

# Campos del `worker_health.json` que se reenvían a la base. Allowlist estricta:
# el archivo del sidecar es privado y contiene además cursores y rutas internas
# que no deben cruzar la frontera del API (INTEGRATION.md, y SEC-023 sobre no
# exponer detalle operativo interno). No hay texto libre en estos campos: las
# `reasons` son vocabulario cerrado del worker.
_HEALTH_TOP_FIELDS = ("status", "ready", "generatedAt", "lastSuccessfulCycleAt", "nextCycleAt")
_HEALTH_METRIC_FIELDS = (
    "intervalSeconds",
    "cycleDurationSeconds",
    "activeOrders",
    "activeTrackingBacklog",
    "activeNeverAttempted",
    "historicalBootstrapStatus",
    "historicalBootstrapTotalOrders",
    "historicalBootstrapCompletedOrders",
)
_HEALTH_REASONS_MAX = 12


@dataclass(frozen=True)
class RuntimeSnapshot:
    orders: list[dict[str, Any]]
    tracking_rows: list[dict[str, Any]]
    rejected_terminal_lines: int
    latest_extracted_at: datetime | None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"No se pudo leer el contrato JSON del sidecar: {path.name}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"El contrato JSON del sidecar no es un objeto: {path.name}")
    return payload


def _read_jsonl(
    path: Path,
    *,
    tracking_after: datetime | None = None,
) -> tuple[list[dict[str, Any]], int, datetime | None]:
    try:
        handle = path.open("r", encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"No se pudo leer el ledger del sidecar: {path.name}") from exc

    rows: list[dict[str, Any]] = []
    rejected_terminal_lines = 0
    latest_extracted_at: datetime | None = None
    with handle:
        for index, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                if not line.endswith(("\n", "\r")):
                    rejected_terminal_lines += 1
                    continue
                raise RuntimeError(f"Ledger del sidecar corrupto en la línea {index}") from exc
            if not isinstance(value, dict):
                raise RuntimeError(f"Ledger del sidecar inválido en la línea {index}")
            extracted_at = parse_cf_datetime(value.get("extracted_at"))
            if tracking_after and extracted_at and extracted_at < tracking_after:
                continue
            if extracted_at and (latest_extracted_at is None or extracted_at > latest_extracted_at):
                latest_extracted_at = extracted_at
            rows.append(value)
    return rows, rejected_terminal_lines, latest_extracted_at


def read_runtime(
    runtime_dir: str | Path,
    *,
    tracking_after: datetime | None = None,
) -> RuntimeSnapshot:
    """Read only the canonical catalog and ledger from a private runtime."""
    root = Path(runtime_dir).expanduser().resolve()
    catalog_path = root / "order_catalog.json"
    ledger_path = root / "tracking_ledger.jsonl"
    for _attempt in range(3):
        catalog_before = catalog_path.stat()
        ledger_before = ledger_path.stat()
        catalog = _read_json(catalog_path)
        raw_orders = catalog.get("orders")
        if not isinstance(raw_orders, list):
            raise RuntimeError("El catálogo del sidecar no contiene una lista de órdenes")

        orders: list[dict[str, Any]] = []
        for order in raw_orders:
            if not isinstance(order, dict) or order.get("number") is None:
                raise RuntimeError("El catálogo contiene una orden sin número")
            orders.append(order)

        tracking_rows, rejected, latest_extracted_at = _read_jsonl(
            ledger_path,
            tracking_after=tracking_after,
        )
        if catalog_before == catalog_path.stat() and ledger_before == ledger_path.stat():
            return RuntimeSnapshot(
                orders=orders,
                tracking_rows=tracking_rows,
                rejected_terminal_lines=rejected,
                latest_extracted_at=latest_extracted_at,
            )
    raise RuntimeError("El runtime del sidecar cambió durante tres lecturas consecutivas")


async def ingest_runtime(
    db: AsyncSession,
    runtime_dir: str | Path,
    *,
    batch_size: int = 500,
) -> dict[str, int]:
    """Ingest a complete snapshot; the caller owns commit/rollback.

    The sidecar catalog is a reduced projection of ``/work-orders`` and is used
    here **only in memory**, to enrich each tracking row. It is deliberately not
    written to ``cloudfleet_work_orders``: that table is owned by the CloudFleet
    sync, which carries the full record (costs, reason, detected issue, raw). A
    second writer holding fewer columns would blank them on every cycle.
    """
    watermark = parse_cf_datetime(await get_watermark(db, TRACKING_INGEST_WATERMARK))
    tracking_after = watermark - timedelta(minutes=10) if watermark is not None else None
    snapshot = read_runtime(runtime_dir, tracking_after=tracking_after)
    orders_by_number = {int(order["number"]): order for order in snapshot.orders}
    normalized_events = [
        normalize_tracking_row(
            row,
            order=orders_by_number.get(int(row["work_order_number"])),
            ordinal=index,
        )
        for index, row in enumerate(snapshot.tracking_rows)
    ]
    events_inserted = await upsert_tracking_events(
        db,
        normalized_events,
        batch_size=batch_size,
    )
    if snapshot.latest_extracted_at is not None:
        await _set_watermark(
            db,
            TRACKING_INGEST_WATERMARK,
            snapshot.latest_extracted_at,
            datetime.now(UTC),
        )
    return {
        "catalog_orders": len(snapshot.orders),
        "tracking_rows_read": len(snapshot.tracking_rows),
        "tracking_events_inserted": events_inserted,
        "rejected_terminal_lines": snapshot.rejected_terminal_lines,
    }


def read_worker_health(runtime_dir: str | Path) -> dict[str, Any] | None:
    """Read the sidecar health file, reduced to the published allowlist.

    Returns ``None`` when the worker has not written one yet. Never raises: a
    missing or malformed health file is itself the signal the UI must show, and
    it must not abort the data path.
    """
    path = Path(runtime_dir).expanduser().resolve() / "worker_health.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    health: dict[str, Any] = {
        field: payload.get(field) for field in _HEALTH_TOP_FIELDS if field in payload
    }
    raw_reasons = payload.get("reasons")
    if isinstance(raw_reasons, list):
        health["reasons"] = [
            str(reason)[:64] for reason in raw_reasons[:_HEALTH_REASONS_MAX] if reason is not None
        ]
    raw_metrics = payload.get("metrics")
    if isinstance(raw_metrics, dict):
        health["metrics"] = {
            field: raw_metrics[field] for field in _HEALTH_METRIC_FIELDS if field in raw_metrics
        }
    return health


async def record_ingest_health(
    db: AsyncSession,
    runtime_dir: str | Path,
    *,
    ok: bool,
    counters: dict[str, int] | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Publish one heartbeat for the ingest cycle plus the worker's own health.

    The heartbeat is written on every cycle, including failures. Advancing only
    the data watermark would be ambiguous: a quiet workshop and a dead ingestor
    both look like "nothing new".
    """
    moment = now or datetime.now(UTC)
    detail: dict[str, Any] = {
        "ingest": {
            "ok": ok,
            "at": moment.isoformat(),
            "counters": counters or {},
            # Mensaje corto y acotado: la traza completa vive en los logs.
            "error": (str(error)[:300] if error else None),
        },
        "worker": read_worker_health(runtime_dir),
    }
    await db.execute(
        text(
            "INSERT INTO sync_state (key, watermark, detail, created_at, updated_at) "
            "VALUES (:k, :wm, CAST(:detail AS jsonb), :now, :now) "
            "ON CONFLICT (key) DO UPDATE SET watermark = EXCLUDED.watermark, "
            "detail = EXCLUDED.detail, updated_at = :now"
        ),
        {
            "k": TRACKING_HEALTH_STATE_KEY,
            "wm": moment,
            "detail": json.dumps(detail, ensure_ascii=False, default=str),
            "now": moment,
        },
    )
    return detail
