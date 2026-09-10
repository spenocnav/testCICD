"""Lectura de bases geotab (con credenciales y reglas) por flota.

Réplica de solo lectura del contrato de integración: el portal muestra esta
data pero no la edita; la fuente de verdad es Navi Vehículos.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Row, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.models.master_data import (
    GeotabDatabase,
    GeotabRule,
    MotorCatalog,
    MotorRpmBand,
    Vehicle,
)

# Datasets del ETL (InformesRendimiento), mismos `script_id` del
# estado_ejecucion.json. El estado de extracción se lleva por (vehículo, dataset).
EXTRACTION_DATASETS: tuple[str, ...] = (
    "analisis_combustible",
    "factor_carga",
    "posicion_pedal",
    "habito_seguro",
    "alertas",
    "altimetria",
    "consumo_def",
    "ubicaciones",
    # Bandas por rangos de RPM. Solo produce datos en flotas con
    # `range_mode='rpm'`, pero el estado se siembra para todos los vehículos:
    # así una flota que cambie de modo arranca con su historia disponible.
    "rangos_rpm",
    # Episodios de ralentí (Análisis Ralentí). Solo produce datos en flotas con
    # `ralenti_analysis_enabled`; el estado se siembra igual para todos.
    "ralenti",
)

EXTRACTION_ERROR_LABELS = {
    "authentication_error": "autenticación con Geotab",
    "network_error": "red o tiempo de espera",
    "rate_limit_error": "límite de solicitudes de Geotab",
    "permission_error": "permisos en Geotab",
    "api_request_error": "respuesta de la API de Geotab",
    "worker_error": "error interno del worker",
    "credentials_unavailable": "credenciales no disponibles",
}


def safe_extraction_error_label(error: str | None) -> str | None:
    """Evita devolver texto crudo del worker a clientes del Portal."""
    if not error:
        return None
    return EXTRACTION_ERROR_LABELS.get(error, "motivo no registrado; reintentar")


@dataclass(frozen=True)
class ApplicableRule:
    id: uuid.UUID
    geotab_rule_id: uuid.UUID
    rule_id: str
    name: str
    category: str
    event_type: str | None
    motor_type: str | None
    band: str | None
    is_descenso: bool
    description: str | None


@dataclass(frozen=True)
class PhysicalRulePlan:
    """Una consulta física a Geotab y todas sus aplicaciones semánticas."""

    geotab_rule_id: uuid.UUID
    rule_id: str
    name: str
    applications: tuple[ApplicableRule, ...]


@dataclass(frozen=True)
class VehicleRulePlan:
    vehicle_id: uuid.UUID
    plate: str
    fleet_id: uuid.UUID | None
    fleet_name: str | None
    geotab_database_id: uuid.UUID
    database_name: str
    database_key: str
    geotab_device_id: str
    motor_type: str | None
    rules: list[ApplicableRule] = field(default_factory=list)

    @property
    def physical_rules(self) -> list[PhysicalRulePlan]:
        """Agrupa aplicaciones por regla física para consultar Geotab una vez."""
        grouped: dict[uuid.UUID, list[ApplicableRule]] = {}
        for application in self.rules:
            grouped.setdefault(application.geotab_rule_id, []).append(application)
        return [
            PhysicalRulePlan(
                geotab_rule_id=geotab_rule_id,
                rule_id=applications[0].rule_id,
                name=applications[0].name,
                applications=tuple(applications),
            )
            for geotab_rule_id, applications in grouped.items()
        ]


@dataclass(frozen=True)
class GeotabCredentialLease:
    id: uuid.UUID
    geotab_database_id: uuid.UUID
    database_name: str
    database_key: str
    username: str
    password: str
    label: str | None
    leased_at: datetime | None


async def list_databases_by_fleet(
    db: AsyncSession, fleet_id: uuid.UUID
) -> tuple[Sequence[GeotabDatabase], dict[uuid.UUID, int]]:
    """Bases geotab de la flota con credenciales/reglas precargadas, más el
    conteo de vehículos por base."""
    stmt = (
        select(GeotabDatabase)
        .where(GeotabDatabase.fleet_id == fleet_id)
        .options(
            selectinload(GeotabDatabase.credentials),
            selectinload(GeotabDatabase.rules).selectinload(GeotabRule.applications),
        )
        .order_by(GeotabDatabase.database_name)
    )
    result = await db.execute(stmt)
    databases = result.scalars().all()

    counts_stmt = (
        select(Vehicle.geotab_database_id, func.count())
        .where(Vehicle.geotab_database_id.in_([d.id for d in databases]))
        .group_by(Vehicle.geotab_database_id)
    )
    count_rows = (await db.execute(counts_stmt)).tuples().all() if databases else []
    counts: dict[uuid.UUID, int] = {
        database_id: count
        for database_id, count in count_rows
        if database_id is not None
    }
    return databases, counts


async def list_motors_by_fleet(
    db: AsyncSession, fleet_id: uuid.UUID
) -> Sequence[Row[Any]]:
    """Motores presentes en los vehículos de la flota, con datos de placa.

    Solo salen los motores que la flota realmente usa: el catálogo es global y
    listarlo entero acá no diría nada del cliente. `rpm_band_count` viene por
    subconsulta para poder marcar los motores sin configurar sin traerse las
    bandas.
    """
    band_count = (
        select(func.count())
        .select_from(MotorRpmBand)
        .where(MotorRpmBand.motor_type == MotorCatalog.motor_type)
        .scalar_subquery()
    )
    stmt = (
        select(
            MotorCatalog.motor_type,
            MotorCatalog.description,
            MotorCatalog.governed_speed_rpm,
            MotorCatalog.max_overspeed_rpm,
            func.count(Vehicle.id).label("vehicle_count"),
            band_count.label("rpm_band_count"),
        )
        .join(Vehicle, Vehicle.motor_type == MotorCatalog.motor_type)
        .where(Vehicle.fleet_id == fleet_id)
        .group_by(
            MotorCatalog.motor_type,
            MotorCatalog.description,
            MotorCatalog.governed_speed_rpm,
            MotorCatalog.max_overspeed_rpm,
        )
        .order_by(func.count(Vehicle.id).desc(), MotorCatalog.motor_type)
    )
    return (await db.execute(stmt)).all()


async def list_rules_by_database_keys(
    db: AsyncSession, database_keys: Sequence[str]
) -> dict[str, list[GeotabRule]]:
    """Agrupa las reglas de todas las filas que representan una DB física.

    Una base Geotab puede estar replicada bajo varios clientes. Las reglas son
    propiedades de la DB física (`database_key`), no de la fila del cliente;
    este índice permite que las vistas administrativas respeten esa misma
    semántica.
    """
    keys = sorted({key for key in database_keys if key})
    if not keys:
        return {}

    result = await db.execute(
        select(GeotabDatabase)
        .where(
            GeotabDatabase.database_key.in_(keys),
            GeotabDatabase.is_active.is_(True),
        )
        .options(selectinload(GeotabDatabase.rules).selectinload(GeotabRule.applications))
    )
    rules_by_key: dict[str, list[GeotabRule]] = {key: [] for key in keys}
    for database in result.scalars().unique():
        rules = rules_by_key[database.database_key]
        for rule in database.rules:
            if rule.applications:
                rules.append(rule)
    return rules_by_key


async def list_vehicle_rule_plans(
    db: AsyncSession,
    *,
    fleet_ids: Sequence[uuid.UUID] | None = None,
    only_with_rules: bool = False,
) -> list[VehicleRulePlan]:
    """Reglas aplicables por vehículo para workers server-side.

    Usa la regla de oro del contrato: una regla aplica si vive bajo cualquier
    fila de `geotab_databases` con el mismo `database_key` de la base del
    vehículo. `category` define dónde se reporta. Las aplicaciones de operación
    siempre declaran motor; en hábitos seguros, NULL aplica a toda la base y un
    valor limita al motor del vehículo.
    """
    params: dict[str, object] = {"fleet_ids": list(fleet_ids or [])}
    fleet_filter = ""
    if fleet_ids is not None:
        fleet_filter = "AND v.fleet_id = ANY(:fleet_ids)"

    result = await db.execute(
        text(
            "SELECT "
            "v.id AS vehicle_id, v.plate, v.fleet_id, f.name AS fleet_name, "
            "v.geotab_database_id, d.database_name, d.database_key, "
            "v.geotab_device_id, v.motor_type AS vehicle_motor_type, "
            "app.id AS app_id, r.id AS rule_pk, r.rule_id, r.name AS rule_name, "
            "app.category, app.event_type, app.motor_type AS app_motor_type, "
            "app.band, app.is_descenso, app.description "
            "FROM vehicles v "
            "JOIN geotab_databases d ON d.id = v.geotab_database_id "
            "LEFT JOIN fleets f ON f.id = v.fleet_id "
            "LEFT JOIN geotab_databases sib ON sib.database_key = d.database_key "
            "AND sib.is_active "
            "LEFT JOIN geotab_rules r ON r.geotab_database_id = sib.id AND r.is_active "
            "LEFT JOIN geotab_rule_applications app ON app.geotab_rule_id = r.id "
            "AND app.is_active "
            "AND (app.motor_type IS NULL OR app.motor_type = v.motor_type) "
            "WHERE v.is_active "
            "AND v.geotab_customer_status = 'found' "
            "AND v.geotab_device_id IS NOT NULL "
            f"{fleet_filter} "
            "ORDER BY f.name NULLS LAST, v.plate, app.category, r.name"
        ),
        params,
    )

    plans: dict[uuid.UUID, VehicleRulePlan] = {}
    for row in result.mappings():
        vehicle_id = row["vehicle_id"]
        plan = plans.get(vehicle_id)
        if plan is None:
            plan = VehicleRulePlan(
                vehicle_id=vehicle_id,
                plate=row["plate"],
                fleet_id=row["fleet_id"],
                fleet_name=row["fleet_name"],
                geotab_database_id=row["geotab_database_id"],
                database_name=row["database_name"],
                database_key=row["database_key"],
                geotab_device_id=row["geotab_device_id"],
                motor_type=row["vehicle_motor_type"],
            )
            plans[vehicle_id] = plan
        if row["app_id"] is not None:
            plan.rules.append(
                ApplicableRule(
                    id=row["app_id"],
                    geotab_rule_id=row["rule_pk"],
                    rule_id=row["rule_id"],
                    name=row["rule_name"],
                    category=row["category"],
                    event_type=row["event_type"],
                    motor_type=row["app_motor_type"],
                    band=row["band"],
                    is_descenso=row["is_descenso"],
                    description=row["description"],
                )
            )

    values = list(plans.values())
    if only_with_rules:
        return [plan for plan in values if plan.rules]
    return values


async def acquire_geotab_credential(
    db: AsyncSession, database_key: str
) -> GeotabCredentialLease | None:
    """Toma la credencial activa más antigua de un pool por `database_key`.

    Marca `last_used_at` dentro de la misma transacción para rotación LRU. El
    caller decide cuándo hacer commit; si luego falla la autenticación contra
    Geotab puede desactivar esa credencial y pedir otra.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        text(
            "WITH candidate AS ("
            "SELECT gc.id "
            "FROM geotab_credentials gc "
            "JOIN geotab_databases d ON d.id = gc.geotab_database_id "
            "WHERE d.database_key = :database_key "
            "AND d.is_active "
            "AND gc.is_active "
            "ORDER BY gc.last_used_at NULLS FIRST, gc.created_at, gc.id "
            "FOR UPDATE OF gc SKIP LOCKED "
            "LIMIT 1"
            ") "
            "UPDATE geotab_credentials gc "
            "SET last_used_at = :now, updated_at = :now "
            "FROM candidate c, geotab_databases d "
            "WHERE gc.id = c.id AND d.id = gc.geotab_database_id "
            "RETURNING gc.id, gc.geotab_database_id, d.database_name, "
            "d.database_key, gc.username, gc.password_enc, gc.label, gc.last_used_at"
        ),
        {"database_key": database_key, "now": now},
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    return GeotabCredentialLease(
        id=row["id"],
        geotab_database_id=row["geotab_database_id"],
        database_name=row["database_name"],
        database_key=row["database_key"],
        username=row["username"],
        password=decrypt_secret(bytes(row["password_enc"])),
        label=row["label"],
        leased_at=row["last_used_at"],
    )


# ---------------------------------------------------------------------------
# Estado de extracción por vehículo (worker ETL).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VehicleExtractionStateRow:
    dataset: str
    watermark: datetime | None
    backfill_from: datetime | None
    from_date: datetime  # ventana efectiva resuelta = COALESCE(watermark, backfill_from, default)
    last_run_at: datetime | None
    status: str
    last_error: str | None


@dataclass(frozen=True)
class VehicleExtractionWindow:
    vehicle_id: uuid.UUID
    plate: str
    database_key: str
    geotab_device_id: str
    motor_type: str | None
    dataset: str
    from_date: datetime
    to_date: datetime


def _default_backfill_from() -> datetime:
    """Último recurso si una fila no tiene backfill_from (no debería pasar tras
    el seed). El default real por vehículo es `created_at - lookback` (ver SQL)."""
    return datetime.now(UTC) - timedelta(days=settings.extraction_default_lookback_days)


async def ensure_vehicle_extraction_state(db: AsyncSession, vehicle_id: uuid.UUID) -> None:
    """Crea (idempotente) una fila por dataset para el vehículo, sembrando
    `backfill_from = vehicles.created_at - EXTRACTION_DEFAULT_LOOKBACK_DAYS` (el
    día que se registra; lookback 0 por defecto). No pisa filas existentes."""
    now = datetime.now(UTC)
    await db.execute(
        text(
            "INSERT INTO vehicle_extraction_state "
            "(id, vehicle_id, dataset, backfill_from, status, created_at, updated_at) "
            "SELECT gen_random_uuid(), v.id, ds, "
            "v.created_at - make_interval(days => :lookback), 'pending', :now, :now "
            "FROM vehicles v CROSS JOIN unnest(CAST(:datasets AS text[])) AS ds "
            "WHERE v.id = :vid "
            "ON CONFLICT (vehicle_id, dataset) DO NOTHING"
        ),
        {
            "vid": vehicle_id,
            "now": now,
            "lookback": settings.extraction_default_lookback_days,
            "datasets": list(EXTRACTION_DATASETS),
        },
    )


async def list_vehicle_extraction_state(
    db: AsyncSession, vehicle_id: uuid.UUID
) -> list[VehicleExtractionStateRow]:
    """Estado por dataset de un vehículo (crea las filas faltantes la 1ra vez)."""
    await ensure_vehicle_extraction_state(db, vehicle_id)
    default_from = _default_backfill_from()
    rows = (
        await db.execute(
            text(
                "SELECT dataset, watermark, backfill_from, last_run_at, status, last_error "
                "FROM vehicle_extraction_state WHERE vehicle_id = :vid ORDER BY dataset"
            ),
            {"vid": vehicle_id},
        )
    ).mappings()
    return [
        VehicleExtractionStateRow(
            dataset=r["dataset"],
            watermark=r["watermark"],
            backfill_from=r["backfill_from"],
            from_date=r["watermark"] or r["backfill_from"] or default_from,
            last_run_at=r["last_run_at"],
            status=r["status"],
            # Los estados antiguos pueden contener texto crudo; la API solo
            # devuelve categorías seguras y nunca el detalle de la excepción.
            last_error=safe_extraction_error_label(r["last_error"]),
        )
        for r in rows
    ]


async def request_vehicle_backfill(
    db: AsyncSession,
    vehicle_id: uuid.UUID,
    from_date: datetime,
    *,
    dataset: str | None = None,
) -> int:
    """Re-extrae un vehículo desde `from_date`: `backfill_from = from_date` y
    `watermark = NULL`. `dataset=None` aplica a todos. Devuelve filas afectadas.

    Idempotente para el ETL: el loader upsertea por PK, así que re-extraer un
    rango ya cargado no duplica filas."""
    if dataset is not None and dataset not in EXTRACTION_DATASETS:
        raise ValueError(f"dataset desconocido: {dataset}")
    await ensure_vehicle_extraction_state(db, vehicle_id)
    now = datetime.now(UTC)
    params: dict[str, object] = {"vid": vehicle_id, "from": from_date, "now": now}
    sql = (
        "UPDATE vehicle_extraction_state "
        "SET backfill_from = :from, watermark = NULL, status = 'pending', "
        "last_error = NULL, version = version + 1, updated_at = :now "
        "WHERE vehicle_id = :vid"
    )
    if dataset is not None:
        sql += " AND dataset = :ds"
        params["ds"] = dataset
    res = await db.execute(text(sql), params)
    return int(getattr(res, "rowcount", 0) or 0)


async def request_bulk_backfill(
    db: AsyncSession,
    *,
    fleet_ids: Sequence[uuid.UUID],
    vehicle_ids: Sequence[uuid.UUID] | None,
    from_date: datetime,
    datasets: Sequence[str] | None = None,
) -> dict[str, int]:
    """Programa reprocesamiento masivo: setea `backfill_from` y `watermark=NULL`
    para los `(vehículo, dataset)` indicados. NO ejecuta nada — solo deja el
    estado listo para que el worker lo tome. Acotado a `fleet_ids` (flotas
    accesibles del usuario); `vehicle_ids=None/[]` = todas las de esas flotas;
    `datasets=None/[]` = todos. Devuelve {vehicles, datasets, rows}."""
    ds = list(datasets) if datasets else list(EXTRACTION_DATASETS)
    unknown = [d for d in ds if d not in EXTRACTION_DATASETS]
    if unknown:
        raise ValueError(f"dataset(s) desconocido(s): {', '.join(unknown)}")
    if not fleet_ids:
        return {"vehicles": 0, "datasets": len(ds), "rows": 0}

    now = datetime.now(UTC)
    params: dict[str, object] = {
        "from": from_date,
        "now": now,
        "ds": ds,
        "fleet_ids": list(fleet_ids),
        "vids": list(vehicle_ids or []),
    }
    veh_filter = "v.fleet_id = ANY(:fleet_ids) AND v.is_active"
    if vehicle_ids:
        veh_filter += " AND v.id = ANY(:vids)"

    # Upsert: crea las filas faltantes y reinicia las existentes en un solo paso.
    res = await db.execute(
        text(
            "INSERT INTO vehicle_extraction_state "
            "(id, vehicle_id, dataset, backfill_from, watermark, status, last_error, "
            "created_at, updated_at) "
            "SELECT gen_random_uuid(), v.id, d, :from, NULL, 'pending', NULL, :now, :now "
            "FROM vehicles v CROSS JOIN unnest(CAST(:ds AS text[])) AS d "
            f"WHERE {veh_filter} "
            "ON CONFLICT (vehicle_id, dataset) DO UPDATE SET "
            "backfill_from = EXCLUDED.backfill_from, watermark = NULL, status = 'pending', "
            "last_error = NULL, version = vehicle_extraction_state.version + 1, "
            "updated_at = EXCLUDED.updated_at"
        ),
        params,
    )
    rows = int(getattr(res, "rowcount", 0) or 0)
    return {"vehicles": rows // len(ds) if ds else 0, "datasets": len(ds), "rows": rows}


async def get_vehicle_extraction_windows(
    db: AsyncSession,
    dataset: str,
    to_date: datetime,
    *,
    fleet_ids: Sequence[uuid.UUID] | None = None,
) -> list[VehicleExtractionWindow]:
    """Ventana `[from_date, to_date)` por vehículo para que el worker extraiga
    `dataset`. Omite vehículos sin días nuevos (`from_date >= to_date`). Siembra
    el estado faltante de los vehículos extraíbles. Mismos filtros que
    `list_vehicle_rule_plans` (activo + status 'found' + device_id)."""
    if dataset not in EXTRACTION_DATASETS:
        raise ValueError(f"dataset desconocido: {dataset}")
    now = datetime.now(UTC)
    fleet_filter = ""
    params: dict[str, object] = {
        "ds": dataset,
        "lookback": settings.extraction_default_lookback_days,
        "to_date": to_date,
        "now": now,
        "fleet_ids": list(fleet_ids or []),
    }
    if fleet_ids is not None:
        fleet_filter = "AND v.fleet_id = ANY(:fleet_ids)"

    # Default por vehículo = su día de registro (created_at) - lookback.
    await db.execute(
        text(
            "INSERT INTO vehicle_extraction_state "
            "(id, vehicle_id, dataset, backfill_from, status, created_at, updated_at) "
            "SELECT gen_random_uuid(), v.id, :ds, "
            "v.created_at - make_interval(days => :lookback), 'pending', :now, :now "
            "FROM vehicles v "
            "WHERE v.is_active AND v.geotab_customer_status = 'found' "
            "AND v.geotab_device_id IS NOT NULL "
            f"{fleet_filter} "
            "ON CONFLICT (vehicle_id, dataset) DO NOTHING"
        ),
        params,
    )

    rows = (
        await db.execute(
            text(
                "SELECT v.id AS vehicle_id, v.plate, d.database_key, v.geotab_device_id, "
                "v.motor_type, COALESCE(s.watermark, s.backfill_from, "
                "v.created_at - make_interval(days => :lookback)) AS from_date "
                "FROM vehicles v "
                "JOIN geotab_databases d ON d.id = v.geotab_database_id "
                "LEFT JOIN vehicle_extraction_state s "
                "ON s.vehicle_id = v.id AND s.dataset = :ds "
                "WHERE v.is_active AND v.geotab_customer_status = 'found' "
                "AND v.geotab_device_id IS NOT NULL "
                f"{fleet_filter} "
                "AND COALESCE(s.watermark, s.backfill_from, "
                "v.created_at - make_interval(days => :lookback)) < :to_date "
                "ORDER BY d.database_key, v.plate"
            ),
            params,
        )
    ).mappings()
    return [
        VehicleExtractionWindow(
            vehicle_id=r["vehicle_id"],
            plate=r["plate"],
            database_key=r["database_key"],
            geotab_device_id=r["geotab_device_id"],
            motor_type=r["motor_type"],
            dataset=dataset,
            from_date=r["from_date"],
            to_date=to_date,
        )
        for r in rows
    ]


async def advance_vehicle_watermark(
    db: AsyncSession, vehicle_id: uuid.UUID, dataset: str, to_date: datetime
) -> None:
    """El worker avanza el watermark de un vehículo tras extraer OK hasta `to_date`."""
    now = datetime.now(UTC)
    await db.execute(
        text(
            "UPDATE vehicle_extraction_state SET watermark = :to, status = 'ok', "
            "last_run_at = :now, last_error = NULL, updated_at = :now, "
            "version = version + 1 "
            "WHERE vehicle_id = :vid AND dataset = :ds"
        ),
        {"to": to_date, "now": now, "vid": vehicle_id, "ds": dataset},
    )


async def mark_vehicle_extraction_error(
    db: AsyncSession, vehicle_id: uuid.UUID, dataset: str, error: str
) -> None:
    """El worker marca un fallo de extracción (el watermark NO avanza)."""
    now = datetime.now(UTC)
    await db.execute(
        text(
            "UPDATE vehicle_extraction_state SET status = 'error', last_error = :err, "
            "last_run_at = :now, updated_at = :now, version = version + 1 "
            "WHERE vehicle_id = :vid AND dataset = :ds"
        ),
        {"err": error[:1000], "now": now, "vid": vehicle_id, "ds": dataset},
    )
