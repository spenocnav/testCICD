"""Pipeline directo y acotado de factor de carga.

Los extractores solo hablan con Geotab y entregan micro-lotes a una cola
limitada. El hilo coordinador es el único que escribe PostgreSQL y confirma un
vehículo/día junto con su watermark.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import InterfaceError, OperationalError

import master_state
from config import CREDENTIALS, VEHICLE_BY_DB_DEVICE
from extract.extract_factor_carga import SCRIPT_ID, extract_chunk
from load import db
from load.microbatch import (
    MicroBatch,
    build_date_dimension,
    commit_microbatch,
    dataframe_sha256,
    ensure_single_transaction_database,
)
from transform.transform_factor_carga import transform_factor_carga
from utils import (
    COLOMBIA_TZ,
    authenticate,
    fmt_date,
    get_working_credentials,
    make_vehicle_id,
    retry_call,
)

log = logging.getLogger("factor_carga_stream")
_SENTINEL = object()

# Fallos que invalidan la corrida entera: no tiene sentido tolerarlos por
# vehículo porque el siguiente los volverá a encontrar igual.
_FATAL_EXCEPTIONS = (OperationalError, InterfaceError)


def failure_budget(planned_vehicles: int) -> int:
    """Cuántos vehículos pueden fallar sin invalidar la corrida.

    Un vehículo con datos raros en Geotab no puede dejar sin reportes a las
    demás flotas: se marca su estado en error, no avanza su watermark y el
    resto del pipeline continúa. Superado el presupuesto la corrida sí falla,
    porque deja de ser un caso aislado.
    """
    ratio = float(os.environ.get("ETL_STREAM_MAX_FAILED_RATIO", "0.1"))
    floor = int(os.environ.get("ETL_STREAM_MIN_TOLERATED_FAILURES", "5"))
    return max(floor, int(planned_vehicles * ratio))


@dataclass(frozen=True)
class VehiclePlan:
    portal_vehicle_id: str
    fleet_id: str
    database_name: str
    device_id: str
    plate: str | None
    motor_type: str | None
    group_key: str | None
    rpm_class: str | None
    analytics_vehicle_id: str
    cursor: datetime
    version: int
    to_date: datetime


@dataclass(frozen=True)
class ProducerAssignment:
    database_name: str
    credentials: dict[str, str]
    plans: tuple[VehiclePlan, ...]


@dataclass
class ExtractedBatch:
    batch: MicroBatch | None = None
    error: Exception | None = None
    portal_vehicle_id: str | None = None
    database_name: str | None = None


def today_midnight_utc() -> datetime:
    local = datetime.now(COLOMBIA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc)


def local_day_start_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("el cursor debe incluir zona horaria")
    local = value.astimezone(COLOMBIA_TZ)
    return datetime.combine(local.date(), time.min, COLOMBIA_TZ).astimezone(
        timezone.utc
    )


def clip_to_window(
    raw: pd.DataFrame,
    window_start: datetime,
    window_end: datetime,
) -> pd.DataFrame:
    """Descarta lecturas fuera de ``[window_start, window_end)``.

    Cuando el día pedido no tiene lecturas, ``StatusData`` responde con la
    última lectura anterior a ``fromDate`` para expresar "el valor vigente al
    inicio de la ventana". Ese eco pertenece a un día ya confirmado: si llega
    al micro-lote, el hecho resultante cae en otra fecha y la validación aborta
    el vehículo.
    """
    if raw.empty or "dateTime" not in raw.columns:
        return raw
    stamps = pd.to_datetime(raw["dateTime"], utc=True)
    inside = (stamps >= pd.Timestamp(window_start)) & (stamps < pd.Timestamp(window_end))
    if bool(inside.all()):
        return raw
    return raw.loc[inside].reset_index(drop=True)


def _vehicle_dimension(plan: VehiclePlan) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "vehicle_id": plan.analytics_vehicle_id,
                "database_name": plan.database_name,
                "device_id": plan.device_id,
                "vehicle_label": plan.plate,
                "motor_type": plan.motor_type,
                "group_key": plan.group_key,
                "rpm_class": plan.rpm_class,
                "is_active": True,
            }
        ]
    )


def build_plans(
    *,
    to_date: datetime,
    fleet_ids: set[str] | None = None,
) -> list[VehiclePlan]:
    windows = master_state.get_vehicle_windows(SCRIPT_ID, to_date)
    if windows is None:
        raise RuntimeError("modo directo requiere estado PostgreSQL disponible")

    plans: list[VehiclePlan] = []
    for window in windows:
        fleet_id = str(window["fleet_id"])
        if fleet_ids and fleet_id not in fleet_ids:
            continue
        key = (window["database_name"], window["device_id"])
        if key not in VEHICLE_BY_DB_DEVICE:
            continue
        plans.append(
            VehiclePlan(
                portal_vehicle_id=str(window["vehicle_id"]),
                fleet_id=fleet_id,
                database_name=window["database_name"],
                device_id=window["device_id"],
                plate=window["plate"],
                motor_type=window["motor_type"],
                group_key=window.get("group_key"),
                rpm_class=window.get("rpm_class"),
                analytics_vehicle_id=make_vehicle_id(
                    window["database_name"],
                    window["device_id"],
                ),
                cursor=window["from_date"],
                version=int(window["version"]),
                to_date=to_date,
            )
        )
    return sorted(plans, key=lambda p: (p.database_name, p.device_id))


def build_assignments(plans: list[VehiclePlan]) -> list[ProducerAssignment]:
    by_database: dict[str, list[VehiclePlan]] = {}
    for plan in plans:
        by_database.setdefault(plan.database_name, []).append(plan)

    assignments: list[ProducerAssignment] = []
    for database_name, database_plans in by_database.items():
        configured = CREDENTIALS.get(database_name) or []
        working = get_working_credentials(database_name, configured)
        if not working:
            raise RuntimeError(f"sin credenciales funcionales para {database_name}")
        count = min(len(working), len(database_plans))
        for index in range(count):
            assigned = tuple(database_plans[index::count])
            if assigned:
                assignments.append(
                    ProducerAssignment(
                        database_name=database_name,
                        credentials=working[index],
                        plans=assigned,
                    )
                )
    return assignments


def _put_bounded(
    output: queue.Queue,
    item: ExtractedBatch | object,
    cancel: threading.Event,
    *,
    force: bool = False,
) -> bool:
    while force or not cancel.is_set():
        try:
            output.put(item, timeout=0.5)
            return True
        except queue.Full:
            continue
    return False


def _extract_plan(
    api: Any,
    credentials: dict[str, str],
    plan: VehiclePlan,
    output: queue.Queue,
    cancel: threading.Event,
) -> None:
    day_start = local_day_start_utc(plan.cursor)
    version = plan.version
    expected_cursor = plan.cursor

    while day_start < plan.to_date and not cancel.is_set():
        day_end = min(day_start + timedelta(days=1), plan.to_date)
        if day_end - day_start != timedelta(days=1):
            raise ValueError("la ventana final no cubre un día Colombia completo")
        try:
            fetched = retry_call(
                extract_chunk,
                api,
                [plan.device_id],
                fmt_date(day_start),
                fmt_date(day_end - timedelta(milliseconds=1)),
                plan.database_name,
                _script_id=SCRIPT_ID,
                _label=f"[{plan.database_name}] micro-lote diario",
                _reauth=lambda: authenticate(plan.database_name, credentials, force=True),
            )
            raw = clip_to_window(fetched, day_start, day_end)
            if len(raw) != len(fetched):
                log.debug(
                    "[%s] %s %s: descarto %d lectura(s) fuera de la ventana",
                    SCRIPT_ID,
                    plan.database_name,
                    plan.device_id,
                    len(fetched) - len(raw),
                )
            dim = _vehicle_dimension(plan)
            facts = transform_factor_carga(raw, dim)
            batch = MicroBatch(
                dataset=SCRIPT_ID,
                portal_vehicle_id=plan.portal_vehicle_id,
                analytics_vehicle_id=plan.analytics_vehicle_id,
                expected_version=version,
                expected_cursor=expected_cursor,
                window_start=day_start,
                window_end=day_end,
                vehicle_dim=dim,
                date_dim=build_date_dimension(day_start.astimezone(COLOMBIA_TZ).date()),
                facts=facts,
                raw_row_count=len(raw),
            )
            if not _put_bounded(output, ExtractedBatch(batch=batch), cancel):
                return
        except Exception as exc:
            _put_bounded(
                output,
                ExtractedBatch(
                    error=exc,
                    portal_vehicle_id=plan.portal_vehicle_id,
                    database_name=plan.database_name,
                ),
                cancel,
            )
            return

        version += 1
        expected_cursor = day_end
        day_start = day_end


def _producer(
    assignment: ProducerAssignment,
    output: queue.Queue,
    cancel: threading.Event,
) -> None:
    try:
        api = authenticate(assignment.database_name, assignment.credentials)
        for plan in assignment.plans:
            if cancel.is_set():
                break
            _extract_plan(api, assignment.credentials, plan, output, cancel)
    except Exception as exc:
        _put_bounded(
            output,
            ExtractedBatch(error=exc, database_name=assignment.database_name),
            cancel,
        )
    finally:
        _put_bounded(output, _SENTINEL, cancel, force=True)


def _require_schema(engine: Engine) -> None:
    required = {
        ("public", "vehicle_extraction_state"),
        ("public", "etl_microbatch_commit"),
        ("analytics", "dim_vehicle"),
        ("analytics", "dim_date"),
        ("analytics", "fact_factor_carga_daily"),
    }
    with engine.connect() as conn:
        existing = set(
            conn.execute(
                text(
                    "SELECT table_schema, table_name FROM information_schema.tables "
                    "WHERE (table_schema = 'public' OR table_schema = :analytics)"
                ),
                {"analytics": os.environ.get("ANALYTICS_DB_SCHEMA", "analytics")},
            ).tuples()
        )
        version_exists = bool(
            conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name = 'vehicle_extraction_state' "
                    "AND column_name = 'version'"
                )
            ).scalar()
        )
    missing = required.difference(existing)
    if missing or not version_exists:
        raise RuntimeError(
            "falta preparar el esquema directo; ejecute las migraciones y "
            f"el bootstrap analytics (faltantes={sorted(missing)})"
        )


def _compare_existing(engine: Engine, batch: MicroBatch) -> dict[str, Any]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT fact_row_id, vehicle_id, database_name, date_key, fecha, "
                    "placa, factor_de_carga "
                    "FROM analytics.fact_factor_carga_daily "
                    "WHERE vehicle_id = :vehicle_id AND date_key = :date_key "
                    "ORDER BY fact_row_id"
                ),
                {
                    "vehicle_id": batch.analytics_vehicle_id,
                    "date_key": int(
                        batch.window_start.astimezone(COLOMBIA_TZ).strftime("%Y%m%d")
                    ),
                },
            )
            .mappings()
            .all()
        )
    existing = pd.DataFrame(rows)
    candidate = db._normalized(batch.facts)
    if existing.empty:
        existing = pd.DataFrame(columns=candidate.columns)
    existing = existing.reindex(columns=candidate.columns)
    existing_hash = dataframe_sha256(existing)
    candidate_hash = dataframe_sha256(candidate)
    existing_pks = set(existing.get("fact_row_id", pd.Series(dtype=str)).astype(str))
    candidate_pks = set(candidate.get("fact_row_id", pd.Series(dtype=str)).astype(str))
    return {
        "existing_rows": len(existing),
        "candidate_rows": len(candidate),
        "same_pk_set": existing_pks == candidate_pks,
        "exact": existing_hash == candidate_hash and existing_pks == candidate_pks,
        "existing_sha256": existing_hash,
        "candidate_sha256": candidate_hash,
    }


def run(
    *,
    fleet_ids: set[str] | None = None,
    to_date: datetime | None = None,
    max_extractors: int | None = None,
    queue_size: int | None = None,
    compare_existing: bool = True,
) -> dict[str, int]:
    ensure_single_transaction_database()
    to_date = to_date or today_midnight_utc()
    plans = build_plans(to_date=to_date, fleet_ids=fleet_ids)
    if not plans:
        log.info("[%s] No hay días pendientes.", SCRIPT_ID)
        return {
            "planned_vehicles": 0,
            "committed_days": 0,
            "fact_rows": 0,
            "compared_days": 0,
            "exact_matches": 0,
            "previous_commits": 0,
            "failed_vehicles": 0,
            "failed_databases": [],
            "failure_budget": 0,
        }

    engine = db.get_engine()
    _require_schema(engine)
    try:
        assignments = build_assignments(plans)
    except Exception:
        engine.dispose()
        raise
    if not assignments:
        engine.dispose()
        raise RuntimeError("no fue posible construir productores")

    max_extractors = max_extractors or max(
        1,
        int(os.environ.get("ETL_STREAM_MAX_EXTRACTORS", "4")),
    )
    queue_size = queue_size or max(
        1,
        int(os.environ.get("ETL_STREAM_QUEUE_SIZE", str(max_extractors * 2))),
    )

    output: queue.Queue = queue.Queue(maxsize=queue_size)
    cancel = threading.Event()
    committed_days = 0
    fact_rows = 0
    compared_days = 0
    exact_matches = 0
    previous_commits = 0
    failed_vehicles: dict[str, str] = {}
    failed_databases: dict[str, str] = {}
    fatal: Exception | None = None
    sentinels = 0

    def _register_failure(exc: Exception, item: ExtractedBatch | None) -> None:
        nonlocal fatal
        if isinstance(exc, _FATAL_EXCEPTIONS):
            fatal = fatal or exc
            cancel.set()
            return
        vehicle_id = item.portal_vehicle_id if item else None
        database_name = item.database_name if item else None
        error_code = master_state.safe_extraction_error_code(exc)
        if vehicle_id:
            failed_vehicles.setdefault(vehicle_id, error_code)
            master_state.mark_error(vehicle_id, SCRIPT_ID, error_code)
            log.warning(
                "[%s] vehículo %s omitido (%s)", SCRIPT_ID, vehicle_id, error_code
            )
            return
        # Sin vehículo: falló el productor completo (auth/credenciales de una
        # base Geotab). Se pierde esa base, no la corrida.
        failed_databases.setdefault(database_name or "desconocida", error_code)
        log.error(
            "[%s] base %s sin extracción (%s)", SCRIPT_ID, database_name, error_code
        )

    with ThreadPoolExecutor(
        max_workers=max_extractors,
        thread_name_prefix="geotab-factor",
    ) as executor:
        futures = [
            executor.submit(_producer, assignment, output, cancel)
            for assignment in assignments
        ]
        while sentinels < len(assignments):
            item = output.get()
            try:
                if item is _SENTINEL:
                    sentinels += 1
                    continue
                assert isinstance(item, ExtractedBatch)
                if cancel.is_set() and item.error is None:
                    continue
                if item.error is not None:
                    _register_failure(item.error, item)
                    continue
                assert item.batch is not None
                if compare_existing:
                    item.batch.comparison = _compare_existing(engine, item.batch)
                    compared_days += 1
                    exact_matches += int(bool(item.batch.comparison["exact"]))
                result = commit_microbatch(engine, item.batch)
                committed_days += 1
                fact_rows += result.fact_rows
                previous_commits += int(result.previous_ledger_entry)
                log.info(
                    "[%s] micro-lote confirmado (%d facts, comparación exacta=%s)",
                    SCRIPT_ID,
                    result.fact_rows,
                    (item.batch.comparison or {}).get("exact"),
                )
            except Exception as exc:
                _register_failure(exc, item if isinstance(item, ExtractedBatch) else None)
            finally:
                output.task_done()

        for future in futures:
            try:
                future.result()
            except Exception as exc:
                _register_failure(exc, None)

    engine.dispose()
    if fatal is not None:
        raise fatal

    budget = failure_budget(len(plans))
    if len(failed_vehicles) > budget:
        sample = next(iter(failed_vehicles.values()))
        raise RuntimeError(
            f"factor_carga directo: {len(failed_vehicles)} vehículos fallidos "
            f"superan el presupuesto de {budget}: {sample}"
        )
    if failed_databases and committed_days == 0:
        sample = next(iter(failed_databases.values()))
        raise RuntimeError(
            f"factor_carga directo: ninguna base entregó datos ({sample})"
        )

    result = {
        "planned_vehicles": len(plans),
        "committed_days": committed_days,
        "fact_rows": fact_rows,
        "compared_days": compared_days,
        "exact_matches": exact_matches,
        "previous_commits": previous_commits,
        "failed_vehicles": len(failed_vehicles),
        "failed_databases": sorted(failed_databases),
        "failure_budget": budget,
    }
    if failed_vehicles or failed_databases:
        log.warning(
            "[%s] corrida degradada: %d vehículo(s) y %d base(s) sin datos "
            "(presupuesto=%d). Sus watermarks no avanzaron; el próximo disparo "
            "los reintenta.",
            SCRIPT_ID,
            len(failed_vehicles),
            len(failed_databases),
            budget,
        )
    return result


def _parse_to_date(value: str) -> datetime:
    local_day = date.fromisoformat(value)
    return datetime.combine(local_day, time.min, COLOMBIA_TZ).astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fleet-id",
        action="append",
        default=[],
        help="UUID de flota; puede repetirse. Sin valor procesa todas.",
    )
    parser.add_argument(
        "--to-date",
        type=_parse_to_date,
        help="Fecha local Colombia exclusiva (AAAA-MM-DD).",
    )
    parser.add_argument("--max-extractors", type=int)
    parser.add_argument("--queue-size", type=int)
    parser.add_argument("--no-compare-existing", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    with master_state.run_lock("factor_carga_stream") as acquired:
        if not acquired:
            raise RuntimeError("otra corrida directa de factor_carga está activa")
        result = run(
            fleet_ids=set(args.fleet_id) or None,
            to_date=args.to_date,
            max_extractors=args.max_extractors,
            queue_size=args.queue_size,
            compare_existing=not args.no_compare_existing,
        )
    log.info("factor_carga directo completado: %s", result)


if __name__ == "__main__":
    main()
