"""Sync de la réplica de CloudFleet (vehículos, OTs, cronogramas).

Consume `app.services.cloudfleet_service` (GET) y aplica upserts idempotentes
sobre las tablas `cloudfleet_*` (definidas en `app.models.cloudfleet`).

Diseño:
- `upsert_*` y `replace_schedules_window` operan sobre una `AsyncSession`
  inyectada y NO hacen commit; el orquestador es dueño del commit.
- Watermark por recurso en `sync_state` (mismo patrón que `sync_service.py`).
- `run_cloudfleet_sync(...)` es el entrypoint: vehicles -> WO -> schedules.

NO confundir con `sync_service.py` (snapshot de Navi Vehículos). El watermark
de Cloudfleet vive en sus propias keys para no colisionar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.advisory_lock import session_advisory_lock
from app.db.session import AsyncSessionLocal, engine
from app.services import cloudfleet_meter_sync_service, cloudfleet_service, sync_run_service

_WM_VEHICLES = "cloudfleet_vehicles"
_WM_WORK_ORDERS = "cloudfleet_work_orders"
_WM_SCHEDULES = "cloudfleet_schedules"

_WORK_ORDER_CHUNK_MONTHS = 4
# CloudFleet limita maintenance-schedules a 180 días por request; 5 meses
# (~150d) queda siempre por debajo del tope.
_SCHEDULE_CHUNK_MONTHS = 5
_DEFAULT_LOOKBACK_DAYS = 730
_REFRESH_DAYS = 45
_SCHEDULE_LOOKAHEAD_DAYS = 90
_UPSERT_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Helpers de parseo
# ---------------------------------------------------------------------------
def parse_cf_datetime(value: Any) -> datetime | None:
    """Acepta str ISO con 'Z' (microseg truncados a 6), dict `{'date': ...}`,
    datetime naive (se asume UTC) o None. Devuelve siempre tz-aware UTC."""
    dt: datetime | None
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, dict):
        raw = value.get("date")
        if not raw:
            return None
        if not isinstance(raw, str):
            return None
        dt = _parse_iso(raw)
        if dt is None:
            return None
    elif isinstance(value, str):
        dt = _parse_iso(value)
        if dt is None:
            return None
    else:
        return None

    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt


def _parse_iso(raw: str) -> datetime | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt


def _nested_name(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("name", "code"):
            v = value.get(key)
            if isinstance(v, str) and v:
                return v
        return None
    if isinstance(value, str):
        return value
    return None


def _meter(value: Any) -> float | None:
    if isinstance(value, dict):
        last = value.get("lastMeter")
        if last is None:
            return None
        try:
            return float(last)
        except (TypeError, ValueError):
            return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _num(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except (InvalidOperation, ValueError):
            return None
    return None


def _string_list(value: Any, *, max_items: int = 20, max_length: int = 120) -> list[str] | None:
    """Keep only the bounded maintenance-label context from CloudFleet."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    cleaned = [
        " ".join(str(item).split())[:max_length]
        for item in value[:max_items]
        if item is not None and str(item).strip()
    ]
    return cleaned or None


def _parse_days_diff(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, dict):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------
async def get_watermark(db: AsyncSession, key: str) -> str | None:
    row = (
        await db.execute(text("SELECT watermark FROM sync_state WHERE key = :k"), {"k": key})
    ).scalar_one_or_none()
    return row.isoformat() if isinstance(row, datetime) else None


async def _set_watermark(db: AsyncSession, key: str, value: datetime, now: datetime) -> None:
    await db.execute(
        text(
            "INSERT INTO sync_state (key, watermark, created_at, updated_at) "
            "VALUES (:k, :wm, :now, :now) "
            "ON CONFLICT (key) DO UPDATE SET watermark = EXCLUDED.watermark, "
            "updated_at = :now"
        ),
        {"k": key, "wm": value, "now": now},
    )


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------
async def upsert_vehicles(db: AsyncSession, records: list[dict[str, Any]]) -> int:
    """Upsert de vehículos CloudFleet por `code`. Devuelve cantidad procesada."""
    if not records:
        return 0
    now = datetime.now(UTC)
    from app.models.cloudfleet import CloudfleetVehicle  # import local: evita ciclos

    values_by_code: dict[str, dict[str, Any]] = {}
    for record in records:
        code = record.get("code")
        if not code:
            continue
        values_by_code[str(code)] = {
            "code": code,
            "brand_name": record.get("brandName") or record.get("brand_name"),
            "line_name": record.get("lineName") or record.get("line_name"),
            "type_name": record.get("typeName") or record.get("type_name"),
            "city": _nested_name(record.get("city")),
            "cost_center": _nested_name(record.get("costCenter")),
            "primary_group": _nested_name(record.get("primaryGroup")),
            "group1": _nested_name(record.get("group1")),
            "odometer": _meter(record.get("odometer")),
            "hourmeter": _meter(record.get("hourmeter")),
            "is_in_master": True,
            "raw": record,
            "synced_at": now,
        }
    values = list(values_by_code.values())
    for start in range(0, len(values), _UPSERT_BATCH_SIZE):
        batch = values[start : start + _UPSERT_BATCH_SIZE]
        stmt = pg_insert(CloudfleetVehicle).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["code"],
            set_={
                "brand_name": stmt.excluded.brand_name,
                "line_name": stmt.excluded.line_name,
                "type_name": stmt.excluded.type_name,
                "city": stmt.excluded.city,
                "cost_center": stmt.excluded.cost_center,
                "primary_group": stmt.excluded.primary_group,
                "group1": stmt.excluded.group1,
                "odometer": stmt.excluded.odometer,
                "hourmeter": stmt.excluded.hourmeter,
                "is_in_master": True,
                "raw": stmt.excluded.raw,
                "synced_at": stmt.excluded.synced_at,
                "updated_at": now,
            },
            where=or_(
                CloudfleetVehicle.brand_name.is_distinct_from(stmt.excluded.brand_name),
                CloudfleetVehicle.line_name.is_distinct_from(stmt.excluded.line_name),
                CloudfleetVehicle.type_name.is_distinct_from(stmt.excluded.type_name),
                CloudfleetVehicle.city.is_distinct_from(stmt.excluded.city),
                CloudfleetVehicle.cost_center.is_distinct_from(stmt.excluded.cost_center),
                CloudfleetVehicle.primary_group.is_distinct_from(stmt.excluded.primary_group),
                CloudfleetVehicle.group1.is_distinct_from(stmt.excluded.group1),
                CloudfleetVehicle.odometer.is_distinct_from(stmt.excluded.odometer),
                CloudfleetVehicle.hourmeter.is_distinct_from(stmt.excluded.hourmeter),
                CloudfleetVehicle.is_in_master.is_distinct_from(True),
                CloudfleetVehicle.raw.is_distinct_from(stmt.excluded.raw),
            ),
        )
        await db.execute(stmt)
    return len(values)


async def mark_vehicles_absent(db: AsyncSession, present_codes: set[str]) -> int:
    """Marca `is_in_master=false` los vehículos que NO vinieron en este sync."""
    res = await db.execute(
        text(
            "UPDATE cloudfleet_vehicles SET is_in_master = false, "
            "synced_at = :now, updated_at = :now "
            "WHERE code <> ALL(:seen) AND is_in_master"
        ),
        {"seen": list(present_codes) if present_codes else [""], "now": datetime.now(UTC)},
    )
    return int(getattr(res, "rowcount", 0) or 0)


async def upsert_work_orders(db: AsyncSession, records: list[dict[str, Any]]) -> int:
    """Upsert de OTs CloudFleet por `number`. Devuelve cantidad procesada."""
    if not records:
        return 0
    now = datetime.now(UTC)
    from app.models.cloudfleet import CloudfleetWorkOrder

    values_by_number: dict[int, dict[str, Any]] = {}
    for record in records:
        number = record.get("number")
        if number is None:
            continue
        number_int = int(number)
        values_by_number[number_int] = {
            "number": number_int,
            "vehicle_code": record.get("vehicleCode") or record.get("vehicle_code"),
            "status": record.get("status"),
            "type": record.get("type"),
            "reason": record.get("reason"),
            "detected_issue": record.get("detectedIssue"),
            "affects_vehicle_availability": bool(record.get("affectsVehicleAvailability", False)),
            "warranty": bool(record.get("warranty", False)),
            "start_date": parse_cf_datetime(record.get("startDate")),
            "workshop_date": parse_cf_datetime(record.get("workshopDate")),
            "technical_completion_date": parse_cf_datetime(record.get("technicalCompletionDate")),
            "final_completion_date": parse_cf_datetime(record.get("finalCompletionDate")),
            "estimated_finish_date": parse_cf_datetime(record.get("estimatedFinishDate")),
            "cf_created_at": parse_cf_datetime(record.get("createdAt")),
            "cf_updated_at": parse_cf_datetime(record.get("updatedAt")),
            "cost_center": _nested_name(record.get("costCenter")),
            "city": _nested_name(record.get("city")),
            "total_cost": _num(record.get("totalCost")),
            "total_cost_labors": _num(record.get("totalCostLabors")),
            "total_cost_parts": _num(record.get("totalCostParts")),
            "maintenance_labels": _string_list(record.get("maintenanceLabels")),
            "raw": record,
            "synced_at": now,
        }
    values = list(values_by_number.values())
    for start in range(0, len(values), _UPSERT_BATCH_SIZE):
        batch = values[start : start + _UPSERT_BATCH_SIZE]
        stmt = pg_insert(CloudfleetWorkOrder).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["number"],
            set_={
                "vehicle_code": stmt.excluded.vehicle_code,
                "status": stmt.excluded.status,
                "type": stmt.excluded.type,
                "reason": stmt.excluded.reason,
                "detected_issue": stmt.excluded.detected_issue,
                "affects_vehicle_availability": stmt.excluded.affects_vehicle_availability,
                "warranty": stmt.excluded.warranty,
                "start_date": stmt.excluded.start_date,
                "workshop_date": stmt.excluded.workshop_date,
                "technical_completion_date": stmt.excluded.technical_completion_date,
                "final_completion_date": stmt.excluded.final_completion_date,
                "estimated_finish_date": stmt.excluded.estimated_finish_date,
                "cf_created_at": stmt.excluded.cf_created_at,
                "cf_updated_at": stmt.excluded.cf_updated_at,
                "cost_center": stmt.excluded.cost_center,
                "city": stmt.excluded.city,
                "total_cost": stmt.excluded.total_cost,
                "total_cost_labors": stmt.excluded.total_cost_labors,
                "total_cost_parts": stmt.excluded.total_cost_parts,
                "maintenance_labels": stmt.excluded.maintenance_labels,
                "raw": stmt.excluded.raw,
                "synced_at": stmt.excluded.synced_at,
                "updated_at": now,
            },
            where=or_(
                CloudfleetWorkOrder.vehicle_code.is_distinct_from(stmt.excluded.vehicle_code),
                CloudfleetWorkOrder.status.is_distinct_from(stmt.excluded.status),
                CloudfleetWorkOrder.type.is_distinct_from(stmt.excluded.type),
                CloudfleetWorkOrder.reason.is_distinct_from(stmt.excluded.reason),
                CloudfleetWorkOrder.detected_issue.is_distinct_from(stmt.excluded.detected_issue),
                CloudfleetWorkOrder.affects_vehicle_availability.is_distinct_from(
                    stmt.excluded.affects_vehicle_availability
                ),
                CloudfleetWorkOrder.warranty.is_distinct_from(stmt.excluded.warranty),
                CloudfleetWorkOrder.start_date.is_distinct_from(stmt.excluded.start_date),
                CloudfleetWorkOrder.workshop_date.is_distinct_from(stmt.excluded.workshop_date),
                CloudfleetWorkOrder.technical_completion_date.is_distinct_from(
                    stmt.excluded.technical_completion_date
                ),
                CloudfleetWorkOrder.final_completion_date.is_distinct_from(
                    stmt.excluded.final_completion_date
                ),
                CloudfleetWorkOrder.estimated_finish_date.is_distinct_from(
                    stmt.excluded.estimated_finish_date
                ),
                CloudfleetWorkOrder.cf_created_at.is_distinct_from(stmt.excluded.cf_created_at),
                CloudfleetWorkOrder.cf_updated_at.is_distinct_from(stmt.excluded.cf_updated_at),
                CloudfleetWorkOrder.cost_center.is_distinct_from(stmt.excluded.cost_center),
                CloudfleetWorkOrder.city.is_distinct_from(stmt.excluded.city),
                CloudfleetWorkOrder.total_cost.is_distinct_from(stmt.excluded.total_cost),
                CloudfleetWorkOrder.total_cost_labors.is_distinct_from(
                    stmt.excluded.total_cost_labors
                ),
                CloudfleetWorkOrder.total_cost_parts.is_distinct_from(
                    stmt.excluded.total_cost_parts
                ),
                CloudfleetWorkOrder.maintenance_labels.is_distinct_from(
                    stmt.excluded.maintenance_labels
                ),
                CloudfleetWorkOrder.raw.is_distinct_from(stmt.excluded.raw),
            ),
        )
        await db.execute(stmt)
    return len(values)


async def replace_schedules_window(
    db: AsyncSession,
    records: list[dict[str, Any]],
    due_from: date,
    due_to: date,
) -> int:
    """Borra la ventana [due_from, due_to+1d) y reinserta desde `records`."""
    from app.models.cloudfleet import CloudfleetMaintenanceSchedule

    now = datetime.now(UTC)
    upper = datetime.combine(due_to + timedelta(days=1), datetime.min.time()).replace(tzinfo=UTC)
    lower = datetime.combine(due_from, datetime.min.time()).replace(tzinfo=UTC)

    await db.execute(
        text(
            "DELETE FROM cloudfleet_maintenance_schedules "
            "WHERE date_to_execute >= :lo AND date_to_execute < :hi"
        ),
        {"lo": lower, "hi": upper},
    )

    values: list[dict[str, Any]] = []
    for record in records:
        date_to_execute = parse_cf_datetime(record.get("dateToExecute"))
        if date_to_execute is None:
            continue
        wo_execution = record.get("woExecution") or {}
        wo_number = wo_execution.get("number") if isinstance(wo_execution, dict) else None
        wo_execution_date = (
            parse_cf_datetime(wo_execution.get("date")) if isinstance(wo_execution, dict) else None
        )
        vehicle_code = _nested_name(record.get("vehicle")) or record.get("vehicleCode")
        values.append(
            {
                "consecutive": record.get("consecutive"),
                "vehicle_code": vehicle_code,
                "status": record.get("status"),
                "task_name": _nested_name(record.get("task")),
                "routine_name": _nested_name(record.get("routine")),
                "schedule_type": record.get("scheduleType"),
                "schedule_source": record.get("scheduleSource"),
                "date_to_execute": date_to_execute,
                "date_to_execute_days_diff": _parse_days_diff(record.get("dateToExecuteDaysDiff")),
                "wo_number": int(wo_number) if wo_number is not None else None,
                "wo_execution_date": wo_execution_date,
                "raw": record,
                "synced_at": now,
            }
        )

    # No hay clave natural fiable: la idempotencia proviene del DELETE de la
    # ventana dentro de esta transacción. INSERT multi-row evita un round-trip
    # por tarea.
    for start in range(0, len(values), _UPSERT_BATCH_SIZE):
        await db.execute(
            pg_insert(CloudfleetMaintenanceSchedule).values(
                values[start : start + _UPSERT_BATCH_SIZE]
            )
        )
    return len(values)


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------
def _chunks(start: date, end: date, months: int) -> list[tuple[date, date]]:
    """Divide [start, end] en ventanas de ~`months` meses (inclusivas)."""
    if start > end:
        return []
    chunks: list[tuple[date, date]] = []
    cursor = start
    step_months = max(1, months)
    while cursor <= end:
        # Avanzar `step_months` meses desde `cursor` (inclusive).
        year = cursor.year + (cursor.month - 1 + step_months) // 12
        month = (cursor.month - 1 + step_months) % 12 + 1
        try:
            nxt = cursor.replace(year=year, month=month)
        except ValueError:
            # día fuera de rango del mes destino: caer al último día de ese mes
            nxt = (cursor.replace(day=1, year=year, month=month) + timedelta(days=31)).replace(
                day=1
            ) - timedelta(days=1)
        chunk_end = min(nxt - timedelta(days=1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------
async def _sync_cloudfleet_core_unlocked(
    *,
    full: bool = False,
    date_from: date | None = None,
    refresh_days: int = _REFRESH_DAYS,
) -> dict[str, int]:
    """Sincroniza por etapas idempotentes con transacciones cortas.

    Las llamadas HTTP ocurren sin una transacción SQL abierta. Cada recurso
    confirma sus filas y watermark juntos; si una etapa posterior falla, la
    próxima corrida reanuda desde el último recurso confirmado.
    """
    today = date.today()
    default_from = date_from or (today - timedelta(days=_DEFAULT_LOOKBACK_DAYS))
    now_run = datetime.now(UTC)

    summary: dict[str, int] = {
        **cloudfleet_meter_sync_service.empty_summary(),
        "vehicles_fetched": 0,
        "vehicles_upserted": 0,
        "vehicles_marked_absent": 0,
        "work_orders_fetched": 0,
        "work_orders_upserted": 0,
        "schedules_fetched": 0,
        "schedules_inserted": 0,
    }

    # 0) Publicar primero los acumulados que llegan de MyGeotab. Cada lectura
    # lleva estado persistente y el cliente CloudFleet limita los POSTs a menos
    # de 30/min. Los errores por vehículo no impiden traer el resto del
    # snapshot; la corrida queda `partial` y los casos ambiguos se reconcilian
    # después de actualizar la réplica de vehículos.
    summary.update(await cloudfleet_meter_sync_service.sync_meters_before_snapshot())

    # Leer el watermark y liberar la sesión antes de cualquier espera de red.
    async with AsyncSessionLocal() as session:
        wo_wm_str = None if full else await get_watermark(session, _WM_WORK_ORDERS)
    wo_wm_date: date | None = None
    if wo_wm_str:
        try:
            wo_wm_date = datetime.fromisoformat(wo_wm_str).date()
        except ValueError:
            wo_wm_date = None

    # 1) Vehículos: fetch sin DB; upsert + watermark en una transacción.
    vehicles_records = await cloudfleet_service.list_vehicles()
    summary["vehicles_fetched"] = len(vehicles_records)
    present_codes: set[str] = {
        r["code"] for r in vehicles_records if isinstance(r, dict) and r.get("code")
    }
    async with AsyncSessionLocal() as session:
        summary["vehicles_upserted"] = await upsert_vehicles(session, vehicles_records)
        summary["vehicles_marked_absent"] = await mark_vehicles_absent(session, present_codes)
        await _set_watermark(session, _WM_VEHICLES, now_run, now_run)
        await session.commit()
    summary["meters_reconciled"] = await cloudfleet_meter_sync_service.reconcile_uncertain_meters()

    # 2) OTs: fetch de cada ventana antes de abrir su transacción. Los chunks
    # ya confirmados son re-aplicables por ON CONFLICT si falla uno posterior.
    if full or wo_wm_date is None:
        wo_from = default_from
    else:
        wo_from = max(default_from, wo_wm_date - timedelta(days=refresh_days))
    wo_to = today

    wo_total_fetched = 0
    wo_total_upserted = 0
    for chunk_from, chunk_to in _chunks(wo_from, wo_to, _WORK_ORDER_CHUNK_MONTHS):
        chunk_records = await cloudfleet_service.list_work_orders(chunk_from, chunk_to)
        wo_total_fetched += len(chunk_records)
        async with AsyncSessionLocal() as session:
            wo_total_upserted += await upsert_work_orders(session, chunk_records)
            await session.commit()
    summary["work_orders_fetched"] = wo_total_fetched
    summary["work_orders_upserted"] = wo_total_upserted
    async with AsyncSessionLocal() as session:
        await _set_watermark(session, _WM_WORK_ORDERS, now_run, now_run)
        await session.commit()

    # 3) Schedules: completar todo el fetch antes del replace atómico para no
    # publicar una ventana parcial.
    sch_from = default_from
    sch_to = today + timedelta(days=_SCHEDULE_LOOKAHEAD_DAYS)
    schedules_records: list[dict[str, Any]] = []
    for chunk_from, chunk_to in _chunks(sch_from, sch_to, _SCHEDULE_CHUNK_MONTHS):
        schedules_records.extend(
            await cloudfleet_service.list_maintenance_schedules(chunk_from, chunk_to)
        )
    summary["schedules_fetched"] = len(schedules_records)
    async with AsyncSessionLocal() as session:
        summary["schedules_inserted"] = await replace_schedules_window(
            session, schedules_records, sch_from, sch_to
        )
        await _set_watermark(session, _WM_SCHEDULES, now_run, now_run)

        await session.commit()

    return summary


async def run_cloudfleet_sync(
    *,
    full: bool = False,
    date_from: date | None = None,
    refresh_days: int = _REFRESH_DAYS,
    trigger: str = "cli",
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
) -> dict[str, int]:
    """Ejecuta una pasada y la registra en `sync_run` (éxito o error).

    El registro de auditoría se escribe en su propia sesión, así que persiste
    aunque la pasada falle y su transacción haga rollback."""
    mode = "full" if full else "incremental"
    # La fila `running` se publica solo después de obtener el lock: un segundo
    # disparo que recibe 409 nunca aparece falsamente como otra corrida activa.
    async with session_advisory_lock(engine, "navi-portal:cloudfleet-sync"):
        started_at = datetime.now(UTC)
        run_id = await sync_run_service.start(
            kind="cloudfleet",
            trigger=trigger,
            mode=mode,
            started_at=started_at,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
        )
        try:
            summary = await _sync_cloudfleet_core_unlocked(
                full=full,
                date_from=date_from,
                refresh_days=refresh_days,
            )
        except Exception as exc:
            await sync_run_service.finish(
                run_id,
                kind="cloudfleet",
                trigger=trigger,
                mode=mode,
                status="error",
                started_at=started_at,
                finished_at=datetime.now(UTC),
                error=str(exc),
                actor_user_id=actor_user_id,
                actor_email=actor_email,
            )
            raise
        sync_status = (
            "partial" if summary["meters_failed"] or summary["meters_uncertain"] else "success"
        )
        await sync_run_service.finish(
            run_id,
            kind="cloudfleet",
            trigger=trigger,
            mode=mode,
            status=sync_status,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            result=summary,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
        )
        return summary
