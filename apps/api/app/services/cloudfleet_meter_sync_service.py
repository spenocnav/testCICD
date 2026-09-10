"""Actualización conservadora de odómetros/horómetros hacia CloudFleet.

La lectura se toma directamente de MyGeotab antes del snapshot final de
CloudFleet:
``DiagnosticOdometerId`` llega en metros y ``DiagnosticEngineHoursId`` en
segundos, por lo que se convierten a kilómetros y horas respectivamente.

CloudFleet responde 204 y no ofrece una clave de idempotencia. El estado local
se persiste antes del POST; ante timeout o 5xx se marca ``uncertain`` y no se
reintenta a ciegas. Un ``429`` confirmado se reintenta de forma acotada,
respetando ``Retry-After``. Tras traer ``vehicles/``, el sync reconcilia la
lectura contra el espejo local de CloudFleet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import AsyncSessionLocal
from app.models.cloudfleet import CloudfleetMeterSyncState, CloudfleetVehicle
from app.models.fleet import Fleet
from app.models.master_data import GeotabDatabase, Vehicle
from app.services import cloudfleet_service, geotab_service, master_data_service
from app.services.cloudfleet_service import CloudfleetMeterError

log = get_logger("cloudfleet-meter-sync")

_ODOMETER_DIAGNOSTIC = "DiagnosticOdometerId"
_HOURMETER_DIAGNOSTIC = "DiagnosticEngineHoursId"
_METER_TYPES = ("distance", "hours")
_MAX_RATE_LIMIT_ATTEMPTS = 3


@dataclass(frozen=True)
class MeterTarget:
    vehicle_id: uuid.UUID
    vehicle_code: str
    database_key: str
    geotab_device_id: str


@dataclass(frozen=True)
class MeterReading:
    target: MeterTarget
    meter_type: str
    meter_value: Decimal
    meter_date: datetime


def empty_summary() -> dict[str, int]:
    return {
        "meters_targets": 0,
        "meters_readings": 0,
        "meters_sent": 0,
        "meters_skipped": 0,
        "meters_failed": 0,
        "meters_uncertain": 0,
        "meters_reconciled": 0,
    }


async def list_meter_sync_states(
    session: AsyncSession,
    *,
    fleet_ids: list[uuid.UUID] | None,
    statuses: list[str] | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, object]], int]:
    """Lista el estado de envío, respetando el alcance de flota del actor."""
    stmt = (
        select(
            CloudfleetMeterSyncState,
            Vehicle.plate,
            Vehicle.fleet_id,
            Fleet.name,
        )
        .join(Vehicle, Vehicle.id == CloudfleetMeterSyncState.vehicle_id)
        .outerjoin(Fleet, Fleet.id == Vehicle.fleet_id)
    )
    if fleet_ids is not None:
        if not fleet_ids:
            return [], 0
        stmt = stmt.where(Vehicle.fleet_id.in_(fleet_ids))
    if statuses:
        stmt = stmt.where(CloudfleetMeterSyncState.status.in_(statuses))

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await session.execute(
            stmt.order_by(CloudfleetMeterSyncState.updated_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    return (
        [
            {
                "fleetId": str(fleet_id) if fleet_id is not None else None,
                "fleetName": fleet_name,
                "vehicleId": str(state.vehicle_id),
                "plate": plate,
                "meterType": state.meter_type,
                "meterValue": float(state.meter_value),
                "meterDate": state.meter_date,
                "status": state.status,
                "sentAt": state.sent_at,
                "updatedAt": state.updated_at,
                "lastError": state.last_error,
            }
            for state, plate, fleet_id, fleet_name in rows
        ],
        total or 0,
    )


async def _list_targets() -> list[MeterTarget]:
    stmt = (
        select(
            Vehicle.id,
            Vehicle.plate,
            GeotabDatabase.database_key,
            Vehicle.geotab_device_id,
        )
        .join(GeotabDatabase, GeotabDatabase.id == Vehicle.geotab_database_id)
        .where(
            Vehicle.is_active.is_(True),
            Vehicle.geotab_customer_status == "found",
            Vehicle.geotab_device_id.is_not(None),
            GeotabDatabase.is_active.is_(True),
        )
        .order_by(GeotabDatabase.database_key, Vehicle.plate)
    )
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(stmt)).all()
    return [
        MeterTarget(
            vehicle_id=row.id,
            vehicle_code=row.plate,
            database_key=row.database_key,
            geotab_device_id=row.geotab_device_id,
        )
        for row in rows
    ]


async def _acquire_credential(
    database_key: str,
) -> master_data_service.GeotabCredentialLease | None:
    async with AsyncSessionLocal() as session:
        lease = await master_data_service.acquire_geotab_credential(session, database_key)
        await session.commit()
    return lease


async def _read_target_meters(
    client: httpx.AsyncClient,
    session: geotab_service.GeotabSession,
    target: MeterTarget,
    *,
    from_date: datetime,
) -> list[MeterReading]:
    odometer, hourmeter = (
        await geotab_service.get_latest_meter_reading(
            client,
            session,
            device_id=target.geotab_device_id,
            diagnostic_id=_ODOMETER_DIAGNOSTIC,
            from_date=from_date,
        ),
        await geotab_service.get_latest_meter_reading(
            client,
            session,
            device_id=target.geotab_device_id,
            diagnostic_id=_HOURMETER_DIAGNOSTIC,
            from_date=from_date,
        ),
    )
    readings: list[MeterReading] = []
    if odometer is not None:
        readings.append(
            MeterReading(
                target=target,
                meter_type="distance",
                meter_value=odometer.value / Decimal("1000"),
                meter_date=odometer.date_time,
            )
        )
    if hourmeter is not None:
        readings.append(
            MeterReading(
                target=target,
                meter_type="hours",
                meter_value=hourmeter.value / Decimal("3600"),
                meter_date=hourmeter.date_time,
            )
        )
    return readings


def _snapshot_meter(vehicle: CloudfleetVehicle | None, meter_type: str) -> Decimal | None:
    if vehicle is None:
        return None
    value = vehicle.odometer if meter_type == "distance" else vehicle.hourmeter
    return Decimal(str(value)) if value is not None else None


async def _prepare_reading(reading: MeterReading) -> bool:
    """Persiste una lectura a enviar y devuelve si es segura para publicar."""
    async with AsyncSessionLocal() as session:
        state = await session.scalar(
            select(CloudfleetMeterSyncState).where(
                CloudfleetMeterSyncState.vehicle_id == reading.target.vehicle_id,
                CloudfleetMeterSyncState.meter_type == reading.meter_type,
            )
        )
        mirror = await session.scalar(
            select(CloudfleetVehicle).where(CloudfleetVehicle.code == reading.target.vehicle_code)
        )
        # El endpoint /meters/ no admite vehículos ausentes del catálogo
        # CloudFleet. El snapshot base se actualiza antes de llegar aquí.
        if mirror is None:
            return False
        mirror_value = _snapshot_meter(mirror, reading.meter_type)

        retryable_rate_limited = bool(
            state is not None
            and state.status in {"pending", "uncertain"}
            and state.last_error
            and "Cloudfleet respondió 429" in state.last_error
        )
        if (
            state is not None
            and state.status in {"pending", "uncertain"}
            and not retryable_rate_limited
        ):
            return False
        if mirror_value is not None and reading.meter_value <= mirror_value:
            return False
        if (
            state is not None
            and reading.meter_value <= state.meter_value
            and not retryable_rate_limited
        ):
            return False

        if state is None:
            state = CloudfleetMeterSyncState(
                vehicle_id=reading.target.vehicle_id,
                meter_type=reading.meter_type,
                meter_value=reading.meter_value,
                meter_date=reading.meter_date,
                status="pending",
            )
            session.add(state)
        else:
            state.meter_value = reading.meter_value
            state.meter_date = reading.meter_date
            state.status = "pending"
            state.sent_at = None
            state.last_error = None
        await session.commit()
    return True


async def _set_delivery_result(
    reading: MeterReading,
    *,
    status: str,
    error: str | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        state = await session.scalar(
            select(CloudfleetMeterSyncState).where(
                CloudfleetMeterSyncState.vehicle_id == reading.target.vehicle_id,
                CloudfleetMeterSyncState.meter_type == reading.meter_type,
            )
        )
        if state is None:
            raise LookupError("No existe estado para la lectura preparada")
        state.status = status
        state.sent_at = datetime.now(UTC) if status == "sent" else None
        state.last_error = error[:1000] if error else None
        await session.commit()


async def sync_meters_before_snapshot() -> dict[str, int]:
    """Publica las lecturas Geotab antes de traer ``vehicles/`` de CloudFleet."""
    summary = empty_summary()
    if not settings.cloudfleet_meter_sync_enabled:
        return summary

    targets = await _list_targets()
    summary["meters_targets"] = len(targets)
    grouped: dict[str, list[MeterTarget]] = {}
    for target in targets:
        grouped.setdefault(target.database_key, []).append(target)

    from_date = datetime.now(UTC) - timedelta(days=settings.cloudfleet_meter_lookback_days)
    timeout = httpx.Timeout(settings.geotab_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as geotab_client:
        for database_key, database_targets in grouped.items():
            try:
                lease = await _acquire_credential(database_key)
                if lease is None:
                    summary["meters_skipped"] += len(database_targets) * len(_METER_TYPES)
                    continue
                geotab_session = await geotab_service.authenticate(
                    geotab_client,
                    database=lease.database_name,
                    username=lease.username,
                    password=lease.password,
                )
            except Exception:
                # No exponer nombres de base, usuarios o detalles de autenticación.
                log.warning("cloudfleet_meter_geotab_source_unavailable")
                summary["meters_failed"] += len(database_targets) * len(_METER_TYPES)
                continue

            for target in database_targets:
                try:
                    readings = await _read_target_meters(
                        geotab_client,
                        geotab_session,
                        target,
                        from_date=from_date,
                    )
                except geotab_service.GeotabError:
                    summary["meters_failed"] += len(_METER_TYPES)
                    continue

                summary["meters_readings"] += len(readings)
                summary["meters_skipped"] += len(_METER_TYPES) - len(readings)
                for reading in readings:
                    if not await _prepare_reading(reading):
                        summary["meters_skipped"] += 1
                        continue
                    payload = cloudfleet_service.build_meter_payload(
                        vehicle_code=reading.target.vehicle_code,
                        meter_date=reading.meter_date,
                        meter_type=reading.meter_type,
                        meter_value=reading.meter_value,
                        source_code=settings.cloudfleet_meter_source_code,
                        comment=settings.cloudfleet_meter_comment,
                    )
                    for attempt in range(_MAX_RATE_LIMIT_ATTEMPTS):
                        try:
                            await cloudfleet_service.create_meter(payload)
                        except CloudfleetMeterError as exc:
                            if exc.retryable and attempt < _MAX_RATE_LIMIT_ATTEMPTS - 1:
                                await _set_delivery_result(
                                    reading,
                                    status="pending",
                                    error=str(exc),
                                )
                                continue
                            delivery_status = "uncertain" if exc.uncertain else "failed"
                            await _set_delivery_result(
                                reading,
                                status=delivery_status,
                                error=str(exc),
                            )
                            summary[f"meters_{delivery_status}"] += 1
                            break
                        else:
                            await _set_delivery_result(reading, status="sent")
                            summary["meters_sent"] += 1
                            break
    return summary


async def reconcile_uncertain_meters() -> int:
    """Cierra como enviadas las lecturas ambiguas ya reflejadas en el snapshot."""
    reconciled = 0
    async with AsyncSessionLocal() as session:
        states = (
            await session.execute(
                select(CloudfleetMeterSyncState, Vehicle, CloudfleetVehicle)
                .join(Vehicle, Vehicle.id == CloudfleetMeterSyncState.vehicle_id)
                .outerjoin(CloudfleetVehicle, CloudfleetVehicle.code == Vehicle.plate)
                .where(CloudfleetMeterSyncState.status.in_(("pending", "uncertain")))
            )
        ).all()
        now = datetime.now(UTC)
        for state, _vehicle, mirror in states:
            mirror_value = _snapshot_meter(mirror, state.meter_type)
            if mirror_value is None or mirror_value < state.meter_value:
                continue
            state.status = "sent"
            state.sent_at = now
            state.last_error = None
            reconciled += 1
        if reconciled:
            await session.commit()
    return reconciled
