"""Ingesta de baja latencia de ``FaultData`` mediante el GetFeed oficial.

El feed complementa, no reemplaza, el extractor diario:

* una fuente es (database_key físico, credencial, FaultData);
* el primer llamado se siembra desde la medianoche de America/Bogota;
* después se usa únicamente el ``toVersion`` opaco confirmado;
* cada página confirma dimensiones, hechos y cursor en una transacción;
* una página llena se drena inmediatamente, sin esperar el próximo intervalo;
* el fast lane confirma solo ``analytics`` + cursor; el lake/silver conserva un
  único dueño, el extractor diario, que reconcilia con ``prefer_new=True``.

GetFeed ignora DeviceSearch para FaultData. El alcance se aplica por tanto en el
cliente contra el catálogo activo de la fuente maestra. Varias credenciales de
la misma base conservan cursores separados; sus solapes convergen por la PK
histórica ``row_id`` de ``analytics.fact_fault_event``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

import config
import master_state
from extract.extract_fallas import fault_record_to_raw
from load import db
from load.microbatch import build_date_dimension
from load.schema import TABLES, TableSpec
from transform.transform_fallas import transform_fault_rows
from utils import authenticate, fmt_date, make_vehicle_id

log = logging.getLogger("faults-feed")

ENTITY_TYPE = "FaultData"
COLOMBIA_TZ = ZoneInfo("America/Bogota")
DEFAULT_RESULTS_LIMIT = 5000
MAX_RESULTS_LIMIT = 50000
_STATE_PREFIX = "geotab_feed:fault_data"


class FeedProtocolError(RuntimeError):
    """La respuesta no permite avanzar el feed con seguridad."""


class StaleFeedCursorError(RuntimeError):
    """Otro consumidor avanzó el cursor después de la llamada HTTP."""


class FeedShutdown(RuntimeError):
    """El worker pidió detener el drenaje entre llamadas/páginas."""


def _table_spec(name: str) -> TableSpec:
    return next(spec for spec in TABLES if spec.pg_table == name)


DIM_VEHICLE_SPEC = _table_spec("dim_vehicle")
DIM_DATE_SPEC = _table_spec("dim_date")
DIM_DIAGNOSTIC_SPEC = _table_spec("dim_diagnostic")
DIM_CONTROLLER_SPEC = _table_spec("dim_controller")
DIM_FAILURE_MODE_SPEC = _table_spec("dim_failure_mode")
FAULT_SPEC = _table_spec("fact_fault_event")


@dataclass(frozen=True)
class CatalogVehicle:
    database_key: str
    database_name: str
    device_id: str
    plate: str | None
    motor_type: str | None
    group_key: str | None
    rpm_class: str | None

    @property
    def analytics_vehicle_id(self) -> str:
        # Debe conservar exactamente la identidad usada por el ETL diario.
        return make_vehicle_id(self.database_name, self.device_id)


@dataclass(frozen=True, repr=False)
class FeedSource:
    database_key: str
    database_fingerprint: str
    credential_fingerprint: str
    username: str
    password: str

    @property
    def state_key(self) -> str:
        return (
            f"{_STATE_PREFIX}:{self.database_fingerprint}:"
            f"{self.credential_fingerprint}"
        )


@dataclass(frozen=True)
class FeedTopology:
    sources: tuple[FeedSource, ...]
    vehicles_by_database: dict[str, dict[str, tuple[CatalogVehicle, ...]]]


@dataclass(frozen=True)
class ReferenceDimensions:
    diagnostic: pd.DataFrame
    controller: pd.DataFrame
    failure_mode: pd.DataFrame


@dataclass
class FaultPayload:
    raw: pd.DataFrame
    facts: pd.DataFrame
    vehicle_dim: pd.DataFrame
    date_dim: pd.DataFrame
    known_dimensions: dict[str, pd.DataFrame] = field(default_factory=dict)
    placeholder_dimensions: dict[str, pd.DataFrame] = field(default_factory=dict)
    ignored_rows: int = 0

    @property
    def max_event_at(self) -> datetime | None:
        if self.raw.empty or "dateTime" not in self.raw:
            return None
        values = pd.to_datetime(self.raw["dateTime"], errors="coerce", utc=True).dropna()
        if values.empty:
            return None
        return values.max().to_pydatetime()


def _fingerprint(*parts: str, length: int = 24) -> str:
    canonical = "\x00".join(str(part).strip().casefold() for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]


def credential_fingerprint(database_key: str, username: str) -> str:
    """Identificador estable sin exponer el username ni depender del password."""
    return _fingerprint(database_key, username)


def database_fingerprint(database_key: str) -> str:
    return _fingerprint(database_key, length=16)


def colombia_midnight_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now debe incluir zona horaria")
    local = now.astimezone(COLOMBIA_TZ)
    return datetime.combine(local.date(), time.min, COLOMBIA_TZ).astimezone(timezone.utc)


def _physical_database_identity(url: str) -> tuple[str | None, int, str | None]:
    parsed = make_url(url)
    return parsed.host, parsed.port or 5432, parsed.database


def ensure_atomic_database() -> None:
    """Cursor público y facts analytics deben confirmar en la misma DB física."""
    master_url = os.environ.get("MASTER_DB_URL")
    analytics_url = config.ANALYTICS_DB_URL
    if not master_url or not analytics_url:
        raise RuntimeError(
            "FaultData GetFeed requiere MASTER_DB_URL y ANALYTICS_DB_URL"
        )
    if _physical_database_identity(master_url) != _physical_database_identity(
        analytics_url
    ):
        raise RuntimeError(
            "FaultData GetFeed requiere que MASTER_DB_URL y ANALYTICS_DB_URL "
            "apunten a la misma base PostgreSQL"
        )


def load_feed_topology() -> FeedTopology:
    """Carga catálogo/credenciales activos sin fallback hardcoded.

    ``database_key`` identifica la base física. ``database_name`` se conserva
    por vehículo porque forma parte de la PK histórica del modelo analítico.
    """
    master_url = os.environ.get("MASTER_DB_URL")
    fernet_key = os.environ.get("MASTER_FERNET_KEY")
    if not master_url or not fernet_key:
        raise RuntimeError("fuente maestra de FaultData no configurada")

    from cryptography.fernet import Fernet

    engine = create_engine(master_url, pool_pre_ping=True, hide_parameters=True)
    try:
        with engine.connect() as conn:
            vehicle_rows = conn.execute(
                text(
                    "SELECT gd.database_key, gd.database_name, "
                    "v.geotab_device_id, v.plate, v.motor_type, v.group_key, "
                    "v.rpm_class "
                    "FROM vehicles v "
                    "JOIN geotab_databases gd ON gd.id = v.geotab_database_id "
                    "JOIN fleets f ON f.id = v.fleet_id "
                    "WHERE f.is_active AND gd.is_active AND v.is_active "
                    "AND gd.connection_type = 'geotab' "
                    "AND v.geotab_device_id IS NOT NULL "
                    "ORDER BY gd.database_key, gd.database_name, v.plate"
                )
            ).all()
            credential_rows = conn.execute(
                text(
                    "SELECT gd.database_key, gc.username, gc.password_enc "
                    "FROM geotab_credentials gc "
                    "JOIN geotab_databases gd ON gd.id = gc.geotab_database_id "
                    "JOIN fleets f ON f.id = gd.fleet_id "
                    "WHERE f.is_active AND gd.is_active AND gc.is_active "
                    "AND gd.connection_type = 'geotab' "
                    "ORDER BY gd.database_key, gc.created_at, gc.username"
                )
            ).all()
    finally:
        engine.dispose()

    if not vehicle_rows:
        raise RuntimeError("fuente maestra sin vehículos activos para FaultData")

    vehicles: dict[str, dict[str, list[CatalogVehicle]]] = {}
    for database_key, database_name, device_id, plate, motor_type, group_key, rpm_class in vehicle_rows:
        vehicle = CatalogVehicle(
            database_key=str(database_key),
            database_name=str(database_name),
            device_id=str(device_id),
            plate=plate,
            motor_type=motor_type,
            group_key=group_key,
            rpm_class=rpm_class,
        )
        vehicles.setdefault(vehicle.database_key, {}).setdefault(
            vehicle.device_id, []
        ).append(vehicle)

    decryptor = Fernet(fernet_key.encode("utf-8"))
    sources_by_key: dict[str, FeedSource] = {}
    invalid_credentials = 0
    for database_key, username, password_enc in credential_rows:
        database_key = str(database_key)
        if database_key not in vehicles:
            continue
        try:
            password = decryptor.decrypt(bytes(password_enc)).decode("utf-8")
        except Exception:
            invalid_credentials += 1
            continue
        source = FeedSource(
            database_key=database_key,
            database_fingerprint=database_fingerprint(database_key),
            credential_fingerprint=credential_fingerprint(database_key, str(username)),
            username=str(username),
            password=password,
        )
        # La misma cuenta puede estar replicada bajo dos flotas que comparten la
        # base física. Un cursor por identidad, no por fila de catálogo.
        sources_by_key.setdefault(source.state_key, source)

    missing_database_count = sum(
        1
        for database_key in vehicles
        if not any(source.database_key == database_key for source in sources_by_key.values())
    )
    if missing_database_count:
        raise RuntimeError(
            f"{missing_database_count} base(s) activa(s) sin credencial utilizable "
            "para FaultData"
        )
    if invalid_credentials:
        log.warning(
            "fault_feed_credentials_unusable count=%d", invalid_credentials
        )

    frozen_vehicles = {
        database_key: {
            device_id: tuple(records)
            for device_id, records in by_device.items()
        }
        for database_key, by_device in vehicles.items()
    }
    return FeedTopology(
        sources=tuple(sources_by_key.values()),
        vehicles_by_database=frozen_vehicles,
    )


def load_reference_dimensions(engine: Engine) -> ReferenceDimensions:
    """Lee las dimensiones actuales que enriquecen el hecho.

    Se hace antes del HTTP/commit. Referencias aún desconocidas se insertan
    luego como placeholders sin sobrescribir metadata existente.
    """
    schema = config.ANALYTICS_DB_SCHEMA
    with engine.connect() as conn:
        diagnostic = pd.read_sql(
            text(
                f'SELECT diagnostic_sk, database_name, diagnostic_id, name, code, source_id '
                f'FROM "{schema}".dim_diagnostic'
            ),
            conn,
        )
        controller = pd.read_sql(
            text(
                f'SELECT controller_sk, database_name, controller_id, name, code '
                f'FROM "{schema}".dim_controller'
            ),
            conn,
        )
        failure_mode = pd.read_sql(
            text(
                f'SELECT failure_mode_sk, database_name, failure_mode_id, name, code '
                f'FROM "{schema}".dim_failure_mode'
            ),
            conn,
        )
    return ReferenceDimensions(diagnostic, controller, failure_mode)


def _extract_id(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("id")
    return None if value is None else str(value)


def map_feed_records(
    database_key: str,
    records: list[dict[str, Any]],
    vehicles_by_device: dict[str, tuple[CatalogVehicle, ...]],
    *,
    seed_from: datetime | None = None,
) -> tuple[pd.DataFrame, int]:
    """Filtra al catálogo activo y reutiliza el mapeo silver diario."""
    rows: list[dict[str, Any]] = []
    ignored = 0
    for record in records:
        device_id = _extract_id(record.get("device"))
        vehicle_records = vehicles_by_device.get(device_id or "", ())
        if not device_id or not vehicle_records:
            ignored += 1
            continue
        if seed_from is not None:
            event_at = pd.to_datetime(record.get("dateTime"), errors="coerce", utc=True)
            if pd.isna(event_at) or event_at.to_pydatetime() < seed_from:
                # GetFeed puede devolver unas pocas filas anteriores al fromDate
                # usado para sembrar; no pertenecen a la ventana inicial.
                ignored += 1
                continue
        for vehicle in vehicle_records:
            if vehicle.database_key != database_key:
                ignored += 1
                continue
            rows.append(
                fault_record_to_raw(vehicle.database_name, device_id, record)
            )
    if not rows:
        return pd.DataFrame(), ignored
    return pd.DataFrame(rows).drop_duplicates(subset=["row_id"], keep="last"), ignored


_REFERENCE_CONFIG = {
    "dim_diagnostic": (
        "diagnostic_sk",
        "diagnostic_id",
        ["diagnostic_sk", "database_name", "diagnostic_id", "name", "code", "source_id"],
    ),
    "dim_controller": (
        "controller_sk",
        "controller_id",
        ["controller_sk", "database_name", "controller_id", "name", "code"],
    ),
    "dim_failure_mode": (
        "failure_mode_sk",
        "failure_mode_id",
        ["failure_mode_sk", "database_name", "failure_mode_id", "name", "code"],
    ),
}


def _reference_rows(
    raw: pd.DataFrame,
    current: pd.DataFrame,
    table: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sk_col, id_col, columns = _REFERENCE_CONFIG[table]
    if raw.empty:
        empty = pd.DataFrame(columns=columns)
        return empty, empty.copy()
    refs = raw[[sk_col, "database_name", id_col]].drop_duplicates(subset=[sk_col])
    current = current.reindex(columns=columns).drop_duplicates(subset=[sk_col])
    known = current[current[sk_col].isin(refs[sk_col])].copy()
    missing = refs[~refs[sk_col].isin(set(known[sk_col]))].copy()
    placeholders = pd.DataFrame({column: None for column in columns}, index=missing.index)
    if not missing.empty:
        placeholders[sk_col] = missing[sk_col]
        placeholders["database_name"] = missing["database_name"]
        placeholders[id_col] = missing[id_col]
        placeholders["name"] = "(Sin información)"
    return known.reindex(columns=columns), placeholders.reindex(columns=columns)


def _vehicle_frames(
    vehicles_by_device: dict[str, tuple[CatalogVehicle, ...]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    vehicles = [vehicle for records in vehicles_by_device.values() for vehicle in records]
    raw = pd.DataFrame(
        [
            {
                "vehicle_id": vehicle.analytics_vehicle_id,
                "database_name": vehicle.database_name,
                "placa": vehicle.plate,
            }
            for vehicle in vehicles
        ]
    )
    semantic = pd.DataFrame(
        [
            {
                "vehicle_id": vehicle.analytics_vehicle_id,
                "database_name": vehicle.database_name,
                "device_id": vehicle.device_id,
                "vehicle_label": vehicle.plate,
                "motor_type": vehicle.motor_type,
                "group_key": vehicle.group_key,
                "rpm_class": vehicle.rpm_class,
                "is_active": True,
            }
            for vehicle in vehicles
        ]
    ).drop_duplicates(subset=["vehicle_id"])
    return raw.drop_duplicates(subset=["vehicle_id"]), semantic


def prepare_payload(
    raw: pd.DataFrame,
    vehicles_by_device: dict[str, tuple[CatalogVehicle, ...]],
    reference: ReferenceDimensions,
    *,
    ignored_rows: int = 0,
) -> FaultPayload:
    vehicle_raw, vehicle_dim = _vehicle_frames(vehicles_by_device)
    if raw.empty:
        return FaultPayload(
            raw=raw,
            facts=pd.DataFrame(),
            vehicle_dim=pd.DataFrame(),
            date_dim=pd.DataFrame(),
            ignored_rows=ignored_rows,
        )

    facts = transform_fault_rows(
        raw,
        dim_vehicle=vehicle_raw,
        dim_diagnostic=reference.diagnostic,
        dim_controller=reference.controller,
        dim_failure_mode=reference.failure_mode,
    )
    used_vehicle_ids = set(facts["vehicle_id"].dropna())
    vehicle_dim = vehicle_dim[vehicle_dim["vehicle_id"].isin(used_vehicle_ids)]
    days = sorted(set(facts["Fecha"].dropna()))
    date_dim = pd.concat(
        [build_date_dimension(day) for day in days], ignore_index=True
    ) if days else pd.DataFrame()

    known: dict[str, pd.DataFrame] = {}
    placeholders: dict[str, pd.DataFrame] = {}
    for table, current in (
        ("dim_diagnostic", reference.diagnostic),
        ("dim_controller", reference.controller),
        ("dim_failure_mode", reference.failure_mode),
    ):
        known[table], placeholders[table] = _reference_rows(raw, current, table)

    return FaultPayload(
        raw=raw,
        facts=facts,
        vehicle_dim=vehicle_dim,
        date_dim=date_dim,
        known_dimensions=known,
        placeholder_dimensions=placeholders,
        ignored_rows=ignored_rows,
    )


def read_cursor(engine: Engine, state_key: str) -> str | None:
    with engine.connect() as conn:
        value = conn.execute(
            text(
                "SELECT detail ->> 'cursor' FROM public.sync_state "
                "WHERE key = :key"
            ),
            {"key": state_key},
        ).scalar_one_or_none()
    return None if value is None else str(value)


def _merge_detail(
    current: dict[str, Any] | None,
    *,
    source: FeedSource,
    cursor: str,
    payload: FaultPayload,
    received_rows: int,
    polled_at: datetime,
) -> dict[str, Any]:
    detail = dict(current or {})
    detail.update(
        {
            "entity_type": ENTITY_TYPE,
            "database_fingerprint": source.database_fingerprint,
            "credential_fingerprint": source.credential_fingerprint,
            "cursor": cursor,
            "last_poll_at": polled_at.isoformat(),
            "last_batch_rows": received_rows,
            "last_fact_rows": len(payload.facts),
            "last_ignored_rows": payload.ignored_rows,
        }
    )
    event_at = payload.max_event_at
    if event_at is not None:
        candidate = event_at.astimezone(timezone.utc).isoformat()
        previous = detail.get("max_event_at")
        if previous is None or candidate > str(previous):
            detail["max_event_at"] = candidate
    return detail


def commit_page(
    engine: Engine,
    *,
    source: FeedSource,
    expected_cursor: str | None,
    to_version: str,
    payload: FaultPayload,
    received_rows: int,
    polled_at: datetime,
) -> None:
    """Confirma dims, fact y cursor con lock/CAS en una sola transacción."""
    bootstrap_detail = json.dumps(
        {
            "entity_type": ENTITY_TYPE,
            "database_fingerprint": source.database_fingerprint,
            "credential_fingerprint": source.credential_fingerprint,
        }
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.sync_state "
                "(key, watermark, detail, created_at, updated_at) "
                "VALUES (:key, NULL, CAST(:detail AS jsonb), :now, :now) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {
                "key": source.state_key,
                "detail": bootstrap_detail,
                "now": polled_at,
            },
        )
        state = (
            conn.execute(
                text(
                    "SELECT detail FROM public.sync_state "
                    "WHERE key = :key FOR UPDATE"
                ),
                {"key": source.state_key},
            )
            .mappings()
            .one()
        )
        current_detail = state.get("detail") or {}
        current_cursor = current_detail.get("cursor")
        if current_cursor is not None:
            current_cursor = str(current_cursor)
        if current_cursor != expected_cursor:
            raise StaleFeedCursorError("el cursor de FaultData cambió durante el fetch")

        if not payload.vehicle_dim.empty:
            # El ETL diario es dueño de la metadata del vehículo. El feed solo
            # siembra la FK faltante y nunca reemplaza columnas enriquecidas
            # por NULL o por un snapshot maestro desactualizado.
            db.insert_dataframe_ignore_conn(conn, DIM_VEHICLE_SPEC, payload.vehicle_dim)
        if not payload.date_dim.empty:
            db.upsert_dataframe_conn(conn, DIM_DATE_SPEC, payload.date_dim)

        reference_specs = {
            "dim_diagnostic": DIM_DIAGNOSTIC_SPEC,
            "dim_controller": DIM_CONTROLLER_SPEC,
            "dim_failure_mode": DIM_FAILURE_MODE_SPEC,
        }
        for table, spec in reference_specs.items():
            # Incluso la metadata conocida usa DO NOTHING: la dimensión diaria
            # es su dueña y una copia stale del feed no debe degradarla.
            known = payload.known_dimensions.get(table, pd.DataFrame())
            placeholders = payload.placeholder_dimensions.get(table, pd.DataFrame())
            if not known.empty:
                db.insert_dataframe_ignore_conn(conn, spec, known)
            if not placeholders.empty:
                db.insert_dataframe_ignore_conn(conn, spec, placeholders)

        if not payload.facts.empty:
            # ON CONFLICT DO UPDATE: el lote más reciente gana para la PK
            # histórica, equivalente a prefer_new=True en silver.
            db.upsert_dataframe_conn(conn, FAULT_SPEC, payload.facts)

        detail = _merge_detail(
            current_detail,
            source=source,
            cursor=to_version,
            payload=payload,
            received_rows=received_rows,
            polled_at=polled_at,
        )
        updated = conn.execute(
            text(
                "UPDATE public.sync_state SET watermark = :now, "
                "detail = CAST(:detail AS jsonb), updated_at = :now "
                "WHERE key = :key "
                "AND (detail ->> 'cursor') IS NOT DISTINCT FROM :expected"
            ),
            {
                "key": source.state_key,
                "expected": expected_cursor,
                "detail": json.dumps(detail),
                "now": polled_at,
            },
        )
        if updated.rowcount != 1:
            raise StaleFeedCursorError("no se pudo confirmar el cursor de FaultData")


def _get_feed(
    api,
    *,
    cursor: str | None,
    seed_from: datetime | None,
    results_limit: int,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "type_name": ENTITY_TYPE,
        "results_limit": results_limit,
    }
    if cursor is None:
        if seed_from is None:
            raise ValueError("el primer GetFeed requiere seed_from")
        params["search"] = {"fromDate": fmt_date(seed_from)}
    else:
        params["from_version"] = cursor
    result = api.call("GetFeed", **params)
    if not isinstance(result, dict):
        raise FeedProtocolError("GetFeed no devolvió un objeto")
    data = result.get("data")
    to_version = result.get("toVersion")
    if not isinstance(data, list) or to_version is None:
        raise FeedProtocolError("GetFeed no devolvió data/toVersion")
    return {"data": data, "to_version": str(to_version)}


def drain_source(
    api,
    engine: Engine,
    *,
    source: FeedSource,
    vehicles_by_device: dict[str, tuple[CatalogVehicle, ...]],
    reference: ReferenceDimensions,
    results_limit: int,
    now: datetime | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """Consume páginas hasta quedar al día; no duerme entre páginas llenas."""
    cursor = read_cursor(engine, source.state_key)
    seed_from = colombia_midnight_utc(now) if cursor is None else None
    pages = received_total = accepted_total = fact_total = 0

    while True:
        if stop_event is not None and stop_event.is_set():
            raise FeedShutdown("shutdown solicitado")
        response = _get_feed(
            api,
            cursor=cursor,
            seed_from=seed_from,
            results_limit=results_limit,
        )
        records = response["data"]
        to_version = response["to_version"]
        if len(records) >= results_limit and to_version == cursor:
            raise FeedProtocolError("GetFeed devolvió página llena sin avanzar cursor")

        raw, ignored = map_feed_records(
            source.database_key,
            records,
            vehicles_by_device,
            seed_from=seed_from,
        )
        payload = prepare_payload(
            raw,
            vehicles_by_device,
            reference,
            ignored_rows=ignored,
        )
        polled_at = datetime.now(timezone.utc)
        commit_page(
            engine,
            source=source,
            expected_cursor=cursor,
            to_version=to_version,
            payload=payload,
            received_rows=len(records),
            polled_at=polled_at,
        )

        pages += 1
        received_total += len(records)
        accepted_total += len(payload.raw)
        fact_total += len(payload.facts)
        cursor = to_version
        seed_from = None
        if len(records) < results_limit:
            break

    return {
        "pages": pages,
        "received_rows": received_total,
        "accepted_rows": accepted_total,
        "fact_rows": fact_total,
    }


def _results_limit() -> int:
    value = int(
        os.environ.get("ETL_FAULTS_FEED_RESULTS_LIMIT", str(DEFAULT_RESULTS_LIMIT))
    )
    if not 1 <= value <= MAX_RESULTS_LIMIT:
        raise ValueError(
            f"ETL_FAULTS_FEED_RESULTS_LIMIT debe estar entre 1 y {MAX_RESULTS_LIMIT}"
        )
    return value


def run_cycle(
    *,
    now: datetime | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """Toma el advisory global del ETL o salta el tick sin leer cursor."""
    with master_state.run_lock() as acquired:
        if not acquired:
            log.info("fault_feed_cycle_skipped reason=etl_global_lock")
            return {
                "sources_ok": 0,
                "sources_failed": 0,
                "pages": 0,
                "fact_rows": 0,
            }
        return _run_cycle_locked(now=now, stop_event=stop_event)


def _run_cycle_locked(
    *,
    now: datetime | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, int]:
    """Ejecuta el poll con el advisory global ya adquirido por ``run_cycle``."""
    ensure_atomic_database()
    topology = load_feed_topology()  # falla cerrado; nunca usa fallback local
    engine = db.get_engine()
    successes = failures = pages = facts = 0
    try:
        reference = load_reference_dimensions(engine)
        for source in topology.sources:
            if stop_event is not None and stop_event.is_set():
                break
            vehicles = topology.vehicles_by_database[source.database_key]
            with master_state.run_lock(source.state_key) as acquired:
                if not acquired:
                    continue
                try:
                    api = authenticate(
                        source.database_key,
                        {"username": source.username, "password": source.password},
                    )
                    result = drain_source(
                        api,
                        engine,
                        source=source,
                        vehicles_by_device=vehicles,
                        reference=reference,
                        results_limit=_results_limit(),
                        now=now,
                        stop_event=stop_event,
                    )
                    successes += 1
                    pages += result["pages"]
                    facts += result["fact_rows"]
                    log.info(
                        "fault_feed_source_done database=%s credential=%s "
                        "pages=%d facts=%d",
                        source.database_fingerprint,
                        source.credential_fingerprint,
                        result["pages"],
                        result["fact_rows"],
                    )
                except FeedShutdown:
                    break
                except Exception as exc:
                    failures += 1
                    log.error(
                        "fault_feed_source_error database=%s credential=%s code=%s",
                        source.database_fingerprint,
                        source.credential_fingerprint,
                        master_state.classify_extraction_error(exc),
                    )
    finally:
        engine.dispose()

    if failures and not successes:
        raise RuntimeError("ninguna fuente de FaultData pudo confirmar su cursor")
    return {
        "sources_ok": successes,
        "sources_failed": failures,
        "pages": pages,
        "fact_rows": facts,
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    result = run_cycle()
    log.info("fault_feed_cycle_done %s", result)


if __name__ == "__main__":
    main()
