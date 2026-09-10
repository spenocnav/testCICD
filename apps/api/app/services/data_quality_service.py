from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import and_, distinct, exists, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.cloudfleet import CloudfleetMeterSyncState, CloudfleetVehicle
from app.models.fleet import Fleet
from app.models.master_data import (
    GEOTAB_RULE_BANDS,
    GeotabCredential,
    GeotabDatabase,
    GeotabRule,
    GeotabRuleApplication,
    MotorRpmBand,
    SyncState,
    Vehicle,
    VehicleExtractionState,
)
from app.schemas.data_quality import (
    DataQualityExample,
    DataQualityMetric,
    DataQualitySummary,
)
from app.services.master_data_service import EXTRACTION_DATASETS, EXTRACTION_ERROR_LABELS

_SAMPLE_LIMIT = 5
_LEGACY_CONNECTION_STATUSES = ("connected", "disconnected")
_RPM_BAND_LABELS = {
    "rango_bajo": "Rango bajo",
    "rango_economico": "Rango económico",
    "rango_balanceado": "Rango balanceado",
    "rango_potencia": "Rango potencia",
    "rango_potencia_ineficiente": "Potencia ineficiente",
    "exceso_rpm": "Exceso de RPM",
    "ralenti": "Ralentí",
}


async def get_summary(
    db: AsyncSession,
    *,
    fleet_ids: Sequence[uuid.UUID],
    stale_after_hours: int,
) -> DataQualitySummary:
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=stale_after_hours)
    scoped_fleets = list(fleet_ids)

    if not scoped_fleets:
        return DataQualitySummary(
            generated_at=now,
            stale_after_hours=stale_after_hours,
            fleet_count=0,
            active_vehicles=0,
            healthy_vehicles=0,
            health_score=0.0,
            geotab_databases=0,
            latest_master_sync_at=None,
            metrics=_metrics(),
        )

    # Las flotas en modo 'rpm' no arman sus bandas desde reglas Geotab, así que
    # las métricas de cobertura de reglas no aplican ahí (y al revés: solo ellas
    # necesitan rangos de RPM por motor).
    rpm_mode_fleets = list(
        (
            await db.scalars(
                select(Fleet.id).where(
                    Fleet.id.in_(scoped_fleets), Fleet.range_mode == "rpm"
                )
            )
        ).all()
    )
    rpm_mode_lookup = set(rpm_mode_fleets)
    rules_mode_fleets = [
        fleet_id for fleet_id in scoped_fleets if fleet_id not in rpm_mode_lookup
    ]

    normalized_device_id = func.nullif(func.trim(Vehicle.geotab_device_id), "")
    normalized_customer_status = func.lower(
        func.trim(func.coalesce(Vehicle.geotab_customer_status, ""))
    )
    missing_device = and_(
        normalized_device_id.is_(None),
        normalized_customer_status.not_in(("not_found", "not_applicable")),
    )
    unresolved_device_status = and_(
        normalized_device_id.is_not(None),
        normalized_customer_status.not_in(("found", "not_found", *_LEGACY_CONNECTION_STATUSES)),
    )
    device_not_found = normalized_customer_status == "not_found"
    device_resolution_issue = or_(missing_device, unresolved_device_status, device_not_found)
    missing_database = Vehicle.geotab_database_id.is_(None)
    missing_motor = Vehicle.motor_type.is_(None)
    stale_vehicle = or_(Vehicle.synced_at.is_(None), Vehicle.synced_at < cutoff)
    healthy_vehicle = and_(
        ~device_resolution_issue,
        ~missing_database,
        ~missing_motor,
        ~stale_vehicle,
    )

    vehicle_result = await db.execute(
        select(
            func.count(Vehicle.id).label("total"),
            func.count(Vehicle.id).filter(healthy_vehicle).label("healthy"),
            func.count(Vehicle.id).filter(missing_device).label("missing_device"),
            func.count(Vehicle.id)
            .filter(unresolved_device_status)
            .label("device_status_unresolved"),
            func.count(Vehicle.id).filter(device_not_found).label("device_not_found"),
            func.count(Vehicle.id).filter(missing_database).label("missing_database"),
            func.count(Vehicle.id).filter(missing_motor).label("missing_motor"),
            func.count(Vehicle.id).filter(stale_vehicle).label("stale_vehicle"),
            func.max(Vehicle.synced_at).label("latest_sync"),
        ).where(
            Vehicle.is_active.is_(True),
            Vehicle.fleet_id.in_(scoped_fleets),
        )
    )
    vehicles = vehicle_result.one()

    active_credential = exists(
        select(1).where(
            GeotabCredential.geotab_database_id == GeotabDatabase.id,
            GeotabCredential.is_active.is_(True),
        )
    )
    database_result = await db.execute(
        select(
            func.count(GeotabDatabase.id).label("total"),
            func.count(GeotabDatabase.id).filter(~active_credential).label("without_credentials"),
            func.count(GeotabDatabase.id)
            .filter(
                or_(
                    GeotabDatabase.synced_at.is_(None),
                    GeotabDatabase.synced_at < cutoff,
                )
            )
            .label("stale"),
        ).where(
            GeotabDatabase.is_active.is_(True),
            GeotabDatabase.fleet_id.in_(scoped_fleets),
        )
    )
    databases = database_result.one()

    failed_extractions = await db.scalar(
        select(func.count(distinct(VehicleExtractionState.vehicle_id)))
        .join(Vehicle, Vehicle.id == VehicleExtractionState.vehicle_id)
        .where(
            Vehicle.is_active.is_(True),
            Vehicle.fleet_id.in_(scoped_fleets),
            VehicleExtractionState.status.in_(("error", "failed")),
        )
    )
    failed_extraction_examples = await _failed_extraction_examples(db, scoped_fleets)
    (
        missing_device_examples,
        unresolved_device_examples,
        not_found_device_examples,
    ) = await _device_resolution_examples(db, scoped_fleets)

    incomplete_extractions, incomplete_extraction_examples = await _incomplete_extraction_state(
        db, scoped_fleets
    )

    missing_cloudfleet, missing_cloudfleet_examples = await _missing_cloudfleet(db, scoped_fleets)
    cloudfleet_meter_errors, cloudfleet_meter_error_examples = await _cloudfleet_meter_errors(
        db, scoped_fleets
    )
    cloudfleet_watermark = await db.scalar(
        select(SyncState.watermark).where(SyncState.key == "cloudfleet_vehicles")
    )
    stale_cloudfleet = cloudfleet_watermark is None or _utc(cloudfleet_watermark) < cutoff
    stale_cloudfleet_examples = (
        [
            DataQualityExample(
                label=(
                    "Sin sincronización registrada"
                    if cloudfleet_watermark is None
                    else "Réplica fuera de vigencia"
                ),
                detail=(
                    None
                    if cloudfleet_watermark is None
                    else f"Último snapshot: {_utc(cloudfleet_watermark).isoformat()}"
                ),
            )
        ]
        if stale_cloudfleet
        else []
    )

    # Reglas de operación activas cuya banda de RPM no viene declarada por la
    # fuente maestra. Se reportan como incompletas: el portal no deriva semántica
    # estable desde el nombre de la regla.
    rules_without_band = await db.scalar(
        select(func.count(GeotabRuleApplication.id))
        .join(GeotabRule, GeotabRule.id == GeotabRuleApplication.geotab_rule_id)
        .join(GeotabDatabase, GeotabDatabase.id == GeotabRule.geotab_database_id)
        .where(
            GeotabRuleApplication.category == "operacion",
            GeotabRuleApplication.is_active.is_(True),
            GeotabRuleApplication.band.is_(None),
            GeotabRule.is_active.is_(True),
            GeotabDatabase.is_active.is_(True),
            GeotabDatabase.fleet_id.in_(rules_mode_fleets),
        )
    ) if rules_mode_fleets else 0
    missing_rpm_bands, missing_rpm_band_examples = (
        await _missing_rpm_bands(db, rules_mode_fleets) if rules_mode_fleets else (0, [])
    )
    missing_rpm_thresholds, missing_rpm_threshold_examples = (
        await _missing_rpm_thresholds(db, rpm_mode_fleets) if rpm_mode_fleets else (0, [])
    )
    distance_contract_ready = bool(
        await db.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'analytics' "
                "AND table_name = 'fact_combustible_daily' "
                "AND column_name = 'distance_quality_status')"
            )
        )
    )
    if distance_contract_ready:
        from app.services import distance_quality_service

        _distance_rows, distance_anomalies = (
            await distance_quality_service.list_distance_anomalies(
                db,
                fleet_ids=scoped_fleets,
                review_status="pending",
                limit=_SAMPLE_LIMIT,
                offset=0,
            )
        )
    else:
        distance_anomalies = 0
    distance_examples: list[DataQualityExample] = []

    total = int(vehicles.total or 0)
    healthy = int(vehicles.healthy or 0)
    values = {
        "missing_device": int(vehicles.missing_device or 0),
        "device_status_unresolved": int(vehicles.device_status_unresolved or 0),
        "device_not_found": int(vehicles.device_not_found or 0),
        "missing_database": int(vehicles.missing_database or 0),
        "missing_motor": int(vehicles.missing_motor or 0),
        "stale_vehicle": int(vehicles.stale_vehicle or 0),
        "failed_extraction": int(failed_extractions or 0),
        "incomplete_extraction_state": incomplete_extractions,
        "missing_cloudfleet": missing_cloudfleet,
        "cloudfleet_meter_errors": cloudfleet_meter_errors,
        "stale_cloudfleet": int(stale_cloudfleet),
        "without_credentials": int(databases.without_credentials or 0),
        "stale_database": int(databases.stale or 0),
        "rules_without_band": int(rules_without_band or 0),
        "missing_rpm_bands": missing_rpm_bands,
        "missing_rpm_thresholds": missing_rpm_thresholds,
        "distance_ecm_gps_anomalies": distance_anomalies,
    }
    examples = {
        "failed_extraction": failed_extraction_examples,
        "missing_device": missing_device_examples,
        "device_status_unresolved": unresolved_device_examples,
        "device_not_found": not_found_device_examples,
        "incomplete_extraction_state": incomplete_extraction_examples,
        "missing_cloudfleet": missing_cloudfleet_examples,
        "cloudfleet_meter_errors": cloudfleet_meter_error_examples,
        "stale_cloudfleet": stale_cloudfleet_examples,
        "missing_rpm_bands": missing_rpm_band_examples,
        "missing_rpm_thresholds": missing_rpm_threshold_examples,
        "distance_ecm_gps_anomalies": distance_examples,
    }

    return DataQualitySummary(
        generated_at=now,
        stale_after_hours=stale_after_hours,
        fleet_count=len(scoped_fleets),
        active_vehicles=total,
        healthy_vehicles=healthy,
        health_score=round(healthy / total * 100, 1) if total else 0.0,
        geotab_databases=int(databases.total or 0),
        latest_master_sync_at=vehicles.latest_sync,
        metrics=_metrics(values, examples),
    )


async def _device_resolution_examples(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> tuple[
    list[DataQualityExample],
    list[DataQualityExample],
    list[DataQualityExample],
]:
    """Clasifica por separado ID faltante, estado incierto y no encontrado."""
    normalized_device_id = func.nullif(func.trim(Vehicle.geotab_device_id), "")
    normalized_status = func.lower(func.trim(func.coalesce(Vehicle.geotab_customer_status, "")))
    scope = (
        Vehicle.is_active.is_(True),
        Vehicle.fleet_id.in_(fleet_ids),
    )
    missing_rows = (
        await db.execute(
            select(Vehicle.plate, Vehicle.geotab_customer_status)
            .where(
                *scope,
                normalized_device_id.is_(None),
                normalized_status.not_in(("not_found", "not_applicable")),
            )
            .order_by(Vehicle.plate)
            .limit(_SAMPLE_LIMIT)
        )
    ).all()
    unresolved_rows = (
        await db.execute(
            select(Vehicle.plate, Vehicle.geotab_customer_status)
            .where(
                *scope,
                normalized_device_id.is_not(None),
                normalized_status.not_in(("found", "not_found", *_LEGACY_CONNECTION_STATUSES)),
            )
            .order_by(Vehicle.plate)
            .limit(_SAMPLE_LIMIT)
        )
    ).all()
    not_found_rows = (
        await db.execute(
            select(Vehicle.plate, Vehicle.geotab_device_id)
            .where(*scope, normalized_status == "not_found")
            .order_by(Vehicle.plate)
            .limit(_SAMPLE_LIMIT)
        )
    ).all()

    def status_label(status: str | None) -> str:
        return (status or "").strip().lower() or "sin estado"

    missing = [
        DataQualityExample(
            label=plate,
            detail=f"Sin geotab_device_id; estado recibido: {status_label(status)}",
        )
        for plate, status in missing_rows
    ]
    unresolved = [
        DataQualityExample(
            label=plate,
            detail=(f"geotab_device_id presente, pero estado recibido: {status_label(status)}"),
        )
        for plate, status in unresolved_rows
    ]
    not_found = [
        DataQualityExample(
            label=plate,
            detail=(
                "Estado confirmado: not_found; no existe dispositivo en la base Geotab"
                if not device_id
                else "Estado confirmado: not_found, aunque quedó un device ID recibido"
            ),
        )
        for plate, device_id in not_found_rows
    ]
    return missing, unresolved, not_found


async def _failed_extraction_examples(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> list[DataQualityExample]:
    failed_vehicles = (
        select(
            Vehicle.id.label("vehicle_id"),
            Vehicle.plate.label("plate"),
        )
        .where(
            Vehicle.is_active.is_(True),
            Vehicle.fleet_id.in_(fleet_ids),
            exists(
                select(1).where(
                    VehicleExtractionState.vehicle_id == Vehicle.id,
                    VehicleExtractionState.status.in_(("error", "failed")),
                )
            ),
        )
        .order_by(Vehicle.plate)
        .limit(_SAMPLE_LIMIT)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                failed_vehicles.c.vehicle_id,
                failed_vehicles.c.plate,
                VehicleExtractionState.dataset,
                VehicleExtractionState.status,
                VehicleExtractionState.last_error,
                VehicleExtractionState.watermark,
            )
            .join(
                VehicleExtractionState,
                VehicleExtractionState.vehicle_id == failed_vehicles.c.vehicle_id,
            )
            .order_by(failed_vehicles.c.plate, VehicleExtractionState.dataset)
        )
    ).all()
    grouped: dict[uuid.UUID, tuple[str, int, list[str]]] = {}
    for vehicle_id, plate, dataset, status, last_error, watermark in rows:
        label, ok_count, failures = grouped.setdefault(vehicle_id, (plate, 0, []))
        if status == "ok":
            grouped[vehicle_id] = (label, ok_count + 1, failures)
            continue
        error_label = EXTRACTION_ERROR_LABELS.get(last_error) or "motivo no registrado; reintentar"
        detail = f"{dataset} ({status}: {error_label}"
        if watermark:
            detail += f"; ventana desde {_utc(watermark).date().isoformat()}"
        detail += ")"
        failures.append(detail)
        grouped[vehicle_id] = (label, ok_count, failures)
    return [
        DataQualityExample(
            label=plate,
            detail=f"{ok_count} dataset(s) OK; falló: " + ", ".join(failures),
        )
        for plate, ok_count, failures in grouped.values()
    ]


async def _incomplete_extraction_state(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> tuple[int, list[DataQualityExample]]:
    expected = len(EXTRACTION_DATASETS)
    state_counts = (
        select(
            Vehicle.id.label("vehicle_id"),
            Vehicle.plate.label("plate"),
            func.count(distinct(VehicleExtractionState.dataset)).label("dataset_count"),
        )
        .outerjoin(
            VehicleExtractionState,
            VehicleExtractionState.vehicle_id == Vehicle.id,
        )
        .where(
            Vehicle.is_active.is_(True),
            Vehicle.fleet_id.in_(fleet_ids),
        )
        .group_by(Vehicle.id, Vehicle.plate)
        .having(func.count(distinct(VehicleExtractionState.dataset)) < expected)
        .subquery()
    )
    total = int(await db.scalar(select(func.count()).select_from(state_counts)) or 0)
    sample_rows = (
        await db.execute(
            select(
                state_counts.c.vehicle_id,
                state_counts.c.plate,
                state_counts.c.dataset_count,
            )
            .order_by(state_counts.c.plate)
            .limit(_SAMPLE_LIMIT)
        )
    ).all()
    sample_ids = [row.vehicle_id for row in sample_rows]
    present: dict[uuid.UUID, set[str]] = {vehicle_id: set() for vehicle_id in sample_ids}
    if sample_ids:
        dataset_rows = (
            await db.execute(
                select(
                    VehicleExtractionState.vehicle_id,
                    VehicleExtractionState.dataset,
                ).where(VehicleExtractionState.vehicle_id.in_(sample_ids))
            )
        ).all()
        for vehicle_id, dataset in dataset_rows:
            present[vehicle_id].add(dataset)

    examples = []
    for row in sample_rows:
        missing = [
            dataset for dataset in EXTRACTION_DATASETS if dataset not in present[row.vehicle_id]
        ]
        examples.append(
            DataQualityExample(
                label=row.plate,
                detail=(
                    f"{row.dataset_count} de {expected} datasets; faltan " + ", ".join(missing)
                ),
            )
        )
    return total, examples


async def _missing_cloudfleet(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> tuple[int, list[DataQualityExample]]:
    missing = or_(
        CloudfleetVehicle.id.is_(None),
        CloudfleetVehicle.is_in_master.is_(False),
    )
    join_condition = CloudfleetVehicle.code == Vehicle.plate
    total = int(
        await db.scalar(
            select(func.count(Vehicle.id))
            .outerjoin(CloudfleetVehicle, join_condition)
            .where(
                Vehicle.is_active.is_(True),
                Vehicle.fleet_id.in_(fleet_ids),
                missing,
            )
        )
        or 0
    )
    rows = (
        await db.execute(
            select(Vehicle.plate, CloudfleetVehicle.is_in_master)
            .outerjoin(CloudfleetVehicle, join_condition)
            .where(
                Vehicle.is_active.is_(True),
                Vehicle.fleet_id.in_(fleet_ids),
                missing,
            )
            .order_by(Vehicle.plate)
            .limit(_SAMPLE_LIMIT)
        )
    ).all()
    return total, [
        DataQualityExample(
            label=plate,
            detail=(
                "No existe en la réplica CloudFleet"
                if is_in_master is None
                else "CloudFleet lo marcó fuera del maestro"
            ),
        )
        for plate, is_in_master in rows
    ]


async def _missing_rpm_bands(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> tuple[int, list[DataQualityExample]]:
    scope_rows = (
        await db.execute(
            select(
                GeotabDatabase.database_key,
                func.min(GeotabDatabase.database_name).label("database_name"),
                Vehicle.motor_type,
            )
            .join(Vehicle, Vehicle.geotab_database_id == GeotabDatabase.id)
            .where(
                GeotabDatabase.is_active.is_(True),
                Vehicle.is_active.is_(True),
                Vehicle.fleet_id.in_(fleet_ids),
                Vehicle.motor_type.is_not(None),
            )
            .group_by(GeotabDatabase.database_key, Vehicle.motor_type)
            .order_by(func.min(GeotabDatabase.database_name), Vehicle.motor_type)
        )
    ).all()
    if not scope_rows:
        return 0, []

    database_keys = sorted({row.database_key for row in scope_rows})
    rule_database = aliased(GeotabDatabase)
    coverage_rows = (
        await db.execute(
            select(
                rule_database.database_key,
                GeotabRuleApplication.motor_type,
                GeotabRuleApplication.band,
                GeotabRuleApplication.is_descenso,
            )
            .join(
                GeotabRule,
                GeotabRule.id == GeotabRuleApplication.geotab_rule_id,
            )
            .join(rule_database, rule_database.id == GeotabRule.geotab_database_id)
            .where(
                rule_database.database_key.in_(database_keys),
                rule_database.is_active.is_(True),
                GeotabRule.is_active.is_(True),
                GeotabRuleApplication.is_active.is_(True),
                GeotabRuleApplication.category == "operacion",
                GeotabRuleApplication.band.is_not(None),
            )
        )
    ).all()
    configured = {
        (database_key, motor_type, band, is_descenso)
        for database_key, motor_type, band, is_descenso in coverage_rows
    }

    total = 0
    examples: list[DataQualityExample] = []
    for row in scope_rows:
        ascenso = [
            band
            for band in GEOTAB_RULE_BANDS
            if (row.database_key, row.motor_type, band, False) not in configured
        ]
        descenso = [
            band
            for band in GEOTAB_RULE_BANDS
            if band != "ralenti"
            and (row.database_key, row.motor_type, band, True) not in configured
        ]
        total += len(ascenso) + len(descenso)
        for direction, bands in (("Ascenso", ascenso), ("Descenso", descenso)):
            for band in bands:
                examples.append(
                    DataQualityExample(
                        label=f"{row.database_name} · {row.motor_type}",
                        detail=f"{direction}: {_RPM_BAND_LABELS[band]}",
                    )
                )
    return total, examples


async def _missing_rpm_thresholds(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> tuple[int, list[DataQualityExample]]:
    """Vehículos en flotas 'rpm' cuyo motor no tiene rangos configurados.

    Se cuentan vehículos y no motores porque es la unidad que se pierde: cada uno
    queda fuera del cálculo de bandas hasta que Navi Vehículos defina los cortes.
    Un vehículo sin `motor_type` también entra: sin motor no hay rangos posibles.
    """
    configured_motor = exists(
        select(1).where(MotorRpmBand.motor_type == Vehicle.motor_type)
    )
    rows = (
        await db.execute(
            select(
                Fleet.name,
                Vehicle.motor_type,
                func.count(Vehicle.id).label("vehicles"),
            )
            .outerjoin(Fleet, Fleet.id == Vehicle.fleet_id)
            .where(
                Vehicle.is_active.is_(True),
                Vehicle.fleet_id.in_(fleet_ids),
                or_(Vehicle.motor_type.is_(None), ~configured_motor),
            )
            .group_by(Fleet.name, Vehicle.motor_type)
            .order_by(func.count(Vehicle.id).desc())
        )
    ).all()

    total = sum(int(row.vehicles or 0) for row in rows)
    examples = [
        DataQualityExample(
            label=f"{row.name or 'Sin flota'} · {row.motor_type or 'Sin motor'}",
            detail=(
                f"{int(row.vehicles or 0)} vehículo"
                f"{'s' if int(row.vehicles or 0) != 1 else ''} sin rangos de RPM"
            ),
        )
        for row in rows[:_SAMPLE_LIMIT]
    ]
    return total, examples


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _cloudfleet_meter_errors(
    db: AsyncSession, fleet_ids: Sequence[uuid.UUID]
) -> tuple[int, list[DataQualityExample]]:
    """Cuenta rechazos de medidores y muestra una muestra accionable."""
    scope = (
        Vehicle.is_active.is_(True),
        Vehicle.fleet_id.in_(fleet_ids),
        CloudfleetMeterSyncState.status == "failed",
    )
    total = await db.scalar(
        select(func.count(CloudfleetMeterSyncState.id))
        .join(Vehicle, Vehicle.id == CloudfleetMeterSyncState.vehicle_id)
        .where(*scope)
    )
    rows = (
        await db.execute(
            select(
                Fleet.name,
                Vehicle.plate,
                CloudfleetMeterSyncState.meter_type,
                CloudfleetMeterSyncState.last_error,
            )
            .join(Vehicle, Vehicle.id == CloudfleetMeterSyncState.vehicle_id)
            .outerjoin(Fleet, Fleet.id == Vehicle.fleet_id)
            .where(*scope)
            .order_by(CloudfleetMeterSyncState.updated_at.desc())
            .limit(_SAMPLE_LIMIT)
        )
    ).all()
    examples = [
        DataQualityExample(
            label=(
                f"{fleet_name or 'Sin flota'} · {plate} · "
                f"{'Odómetro' if meter_type == 'distance' else 'Horómetro'}"
            ),
            detail=last_error or "CloudFleet rechazó la medición sin indicar el motivo.",
        )
        for fleet_name, plate, meter_type, last_error in rows
    ]
    return int(total or 0), examples


def _metrics(
    values: dict[str, int] | None = None,
    examples: dict[str, list[DataQualityExample]] | None = None,
) -> list[DataQualityMetric]:
    counts = values or {}
    samples = examples or {}
    definitions: tuple[tuple[str, str, Literal["critical", "warning"], str], ...] = (
        (
            "missing_device",
            "Dispositivo Geotab sin resolver",
            "critical",
            "Vehículos activos sin geotab_device_id y sin un estado confirmado de no aplicación "
            "o no encontrado.",
        ),
        (
            "device_status_unresolved",
            "Estado Geotab sin confirmar",
            "warning",
            "Vehículos con geotab_device_id cuyo estado es unknown, inválido o not_applicable; "
            "la validación no habilita ese ID hasta recibir una confirmación utilizable.",
        ),
        (
            "device_not_found",
            "Dispositivo Geotab no encontrado",
            "critical",
            "Vehículos cuyo estado fue confirmado como not_found en la base Geotab del cliente.",
        ),
        (
            "missing_database",
            "Sin base Geotab",
            "critical",
            "Vehículos activos que no están vinculados a una base Geotab.",
        ),
        (
            "missing_motor",
            "Motor sin clasificar",
            "warning",
            "Vehículos sin tipo de motor para reglas y analítica.",
        ),
        (
            "stale_vehicle",
            "Réplica maestra vencida",
            "warning",
            "Vehículos sin sincronización reciente desde la fuente maestra.",
        ),
        (
            "failed_extraction",
            "Vehículo con extracción en error",
            "critical",
            "Vehículos con una ventana fallida en al menos un dataset. Puede haber otros "
            "datasets correctos y datos históricos; la ventana fallida queda pendiente para "
            "reintento.",
        ),
        (
            "incomplete_extraction_state",
            "Cobertura de extracción incompleta",
            "warning",
            "Vehículos que no tienen estado para todos los datasets esperados del ETL.",
        ),
        (
            "missing_cloudfleet",
            "Vehículo ausente en CloudFleet",
            "critical",
            "Vehículos activos que no existen o ya no figuran en el maestro CloudFleet.",
        ),
        (
            "cloudfleet_meter_errors",
            "Errores de medidores CloudFleet",
            "critical",
            "Odómetros u horómetros que CloudFleet rechazó y requieren revisión o reintento.",
        ),
        (
            "stale_cloudfleet",
            "Réplica CloudFleet vencida",
            "warning",
            "La última sincronización de vehículos CloudFleet superó la ventana configurada.",
        ),
        (
            "without_credentials",
            "Base sin credenciales activas",
            "critical",
            "Bases Geotab activas que no tienen una credencial utilizable.",
        ),
        (
            "stale_database",
            "Base sin sincronización reciente",
            "warning",
            "Bases Geotab cuya réplica superó la ventana configurada.",
        ),
        (
            "rules_without_band",
            "Regla de operación sin banda RPM",
            "warning",
            "Reglas de operación activas sin banda declarada por la fuente maestra; "
            "no pueden computarse de forma confiable.",
        ),
        (
            "missing_rpm_bands",
            "Cobertura de bandas RPM incompleta",
            "critical",
            "Aplicaciones estándar de ascenso o descenso que faltan para una base y motor "
            "utilizados por vehículos activos. Solo aplica a flotas que arman sus rangos "
            "desde reglas Geotab.",
        ),
        (
            "missing_rpm_thresholds",
            "Motor sin rangos de RPM",
            "critical",
            "Vehículos de flotas configuradas como \"Rangos por RPM\" cuyo motor no tiene "
            "los cortes de RPM definidos en la fuente maestra. El ETL se salta esos "
            "vehículos: no calcula sus bandas en vez de asumir cortes de otro motor.",
        ),
        (
            "distance_ecm_gps_anomalies",
            "Desfase diario ECM-GPS",
            "critical",
            "Casos pendientes de validación interna. Las correcciones automáticas no se "
            "incluyen y este indicador no modifica el puntaje maestro.",
        ),
    )
    return [
        DataQualityMetric(
            key=key,
            label=label,
            count=counts.get(key, 0),
            severity=severity,
            description=description,
            examples=samples.get(key, []),
        )
        for key, label, severity, description in definitions
    ]
