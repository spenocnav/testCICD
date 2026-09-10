"""Informe de Ubicaciones: rastro de una placa consultado a MyGeotab al vuelo.

No lee del modelo semántico (`analytics.fact_location_point`): esos dominios
están inactivos y el informe debe reflejar lo que el proveedor tiene ahora. Por
cada día solicitado se piden las posiciones a Geotab, se muestrea un punto por
ventana de N minutos y se geocodifican solo esos puntos.

Nada se guarda: ni posiciones, ni direcciones, ni la sesión Geotab. El día
consultado viaja en la respuesta HTTP y se descarta.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.master_data import GeotabDatabase, Vehicle
from app.services import geotab_service, master_data_service

# Muestreos ofrecidos por el informe. Un `LogRecord` cada pocos segundos hace
# inútil (y carísimo de geocodificar) devolver el rastro crudo.
SAMPLE_MINUTES_CHOICES = (5, 10, 15, 30)
DEFAULT_SAMPLE_MINUTES = 10
# Ventana máxima del informe. Se consulta un día por petición, así que el tope
# limita cuántas peticiones encadena el cliente, no el tamaño de cada una.
MAX_DAYS = 31


class UbicacionesUnavailableError(Exception):
    """El vehículo no tiene una integración Geotab utilizable."""


@dataclass(frozen=True)
class VehicleGeotabTarget:
    """Datos mínimos para consultar el rastro de un vehículo autorizado."""

    plate: str
    device_id: str
    database_name: str
    database_key: str


async def resolve_vehicle_target(
    db: AsyncSession,
    *,
    vehicle_id: uuid.UUID,
    fleet_ids: list[uuid.UUID] | None,
) -> VehicleGeotabTarget | None:
    """Vehículo + base Geotab, restringido a las flotas autorizadas.

    Fail closed: sin flotas seleccionadas no hay vehículo que valga, y un
    vehículo de otra flota se comporta como inexistente.
    """
    if fleet_ids == []:
        return None
    stmt = (
        select(
            Vehicle.plate,
            Vehicle.geotab_device_id,
            GeotabDatabase.database_name,
            GeotabDatabase.database_key,
        )
        .join(GeotabDatabase, GeotabDatabase.id == Vehicle.geotab_database_id)
        .where(Vehicle.id == vehicle_id, Vehicle.is_active.is_(True))
    )
    if fleet_ids is not None:
        stmt = stmt.where(Vehicle.fleet_id.in_(fleet_ids))
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    plate, device_id, database_name, database_key = row
    if not device_id:
        raise UbicacionesUnavailableError(
            "El vehículo no tiene dispositivo Geotab asociado"
        )
    return VehicleGeotabTarget(
        plate=plate,
        device_id=device_id,
        database_name=database_name,
        database_key=database_key,
    )


def day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Día local del usuario (`reportes_timezone`) expresado en UTC."""
    tz = ZoneInfo(settings.reportes_timezone)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(ZoneInfo("UTC")), end.astimezone(ZoneInfo("UTC"))


def sample_records(
    records: list[geotab_service.GeotabLogRecord], sample_minutes: int
) -> list[geotab_service.GeotabLogRecord]:
    """Deja el primer registro de cada ventana de `sample_minutes`."""
    seconds = sample_minutes * 60
    sampled: list[geotab_service.GeotabLogRecord] = []
    last_bucket: int | None = None
    for record in records:
        bucket = int(record.date_time.timestamp()) // seconds
        if bucket != last_bucket:
            sampled.append(record)
            last_bucket = bucket
    return sampled


async def fetch_day(
    db: AsyncSession,
    *,
    vehicle_id: uuid.UUID,
    fleet_ids: list[uuid.UUID] | None,
    day: date,
    sample_minutes: int = DEFAULT_SAMPLE_MINUTES,
    with_address: bool = True,
) -> list[dict[str, Any]] | None:
    """Puntos muestreados de UN día, con dirección. `None` si no hay acceso.

    Se resuelve todo dentro de una sola sesión Geotab: posiciones del día,
    muestreo y geocodificación por lotes de los puntos que sobreviven.
    """
    target = await resolve_vehicle_target(
        db, vehicle_id=vehicle_id, fleet_ids=fleet_ids
    )
    if target is None:
        return None

    lease = await master_data_service.acquire_geotab_credential(
        db, target.database_key
    )
    if lease is None:
        raise UbicacionesUnavailableError(
            "No hay credenciales activas para la base Geotab del vehículo"
        )
    # La rotación LRU (`last_used_at`) queda firme antes de salir a la red: no
    # se sostiene una transacción abierta mientras se espera al proveedor.
    await db.commit()

    from_utc, to_utc = day_bounds_utc(day)
    timeout = httpx.Timeout(settings.geotab_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        session = await geotab_service.authenticate(
            client,
            database=target.database_name,
            username=lease.username,
            password=lease.password,
        )
        records = await geotab_service.get_log_records(
            client,
            session,
            device_id=target.device_id,
            from_date=from_utc,
            to_date=to_utc,
        )
        sampled = sample_records(records, sample_minutes)
        addresses: list[str | None] = [None] * len(sampled)
        if sampled and with_address:
            addresses = await geotab_service.resolve_addresses_in_batches(
                client,
                session,
                [(record.latitude, record.longitude) for record in sampled],
            )

    return [
        {
            "fecha_y_hora": record.date_time,
            "placa": target.plate,
            "latitude": record.latitude,
            "longitude": record.longitude,
            "speed": record.speed,
            "direccion": address,
        }
        for record, address in zip(sampled, addresses, strict=False)
    ]
