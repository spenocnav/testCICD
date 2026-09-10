"""Schemas de la data maestra (bases geotab, credenciales, reglas).

Las credenciales NUNCA exponen el password (contrato §5: el frontend jamás ve
passwords de Geotab); solo metadata del pool.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GeotabCredentialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    label: str | None = None
    is_active: bool
    last_used_at: datetime | None = None
    synced_at: datetime | None = None


class GeotabRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_id: str
    name: str
    category: str  # operacion | habito_seguro
    event_type: str | None = None  # ej. exceso_rpm
    # Obligatorio para operación; en hábito seguro NULL aplica a toda la base.
    motor_type: str | None = None
    band: str | None = None  # banda canónica para reglas de operación
    is_descenso: bool = False
    description: str | None = None  # clasificación estable para hábito seguro
    is_active: bool


class GeotabDatabaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    database_name: str
    database_key: str
    connection_type: str
    plate_prefix: str | None = None
    is_active: bool
    synced_at: datetime | None = None
    credentials: list[GeotabCredentialRead] = []
    rules: list[GeotabRuleRead] = []
    vehicle_count: int = 0


class FleetMotorRead(BaseModel):
    """Motor presente en una flota, con sus datos de placa y sus rangos.

    Réplica de solo lectura: la fuente de verdad es Navi Vehículos. Las
    velocidades en NULL significan "aún no capturadas allá", nunca 0.
    """

    motor_type: str
    description: str | None = None
    governed_speed_rpm: int | None = None
    max_overspeed_rpm: int | None = None
    vehicle_count: int = 0
    # Cuántas bandas del eje de RPM tiene configuradas. 0 = sin configurar: en
    # una flota `range_mode='rpm'` sus vehículos no se pueden calcular.
    rpm_band_count: int = 0


class SyncResultRead(BaseModel):
    """Resumen de un sync de data maestra desde Navi Vehículos."""

    full: bool
    generated_at: str | None = None
    fleets: int = 0
    databases: int = 0
    credentials: int = 0
    rules: int = 0
    vehicles: int = 0
    motor_rpm_bands: int = 0
    motor_speeds: int = 0
    deactivated: dict[str, int] = {}


class VehicleExtractionStateRead(BaseModel):
    """Estado de extracción del worker para un (vehículo, dataset)."""

    dataset: str
    watermark: datetime | None = None  # último día completo extraído
    backfill_from: datetime | None = None  # fecha mínima a extraer
    from_date: datetime  # ventana efectiva resuelta para la próxima corrida
    last_run_at: datetime | None = None
    status: str
    # Categoría segura del último fallo; nunca contiene la excepción cruda.
    last_error: str | None = None


class BackfillRequest(BaseModel):
    """Pide re-extraer un vehículo desde `from_date`. `dataset=None` = todos."""

    from_date: datetime
    dataset: str | None = None


class BulkBackfillRequest(BaseModel):
    """Reprocesamiento masivo. `vehicle_ids=None` = todas las accesibles;
    `datasets=None` = todos. Solo deja el estado listo (no ejecuta)."""

    from_date: datetime
    vehicle_ids: list[uuid.UUID] | None = None
    datasets: list[str] | None = None


class BulkBackfillResult(BaseModel):
    vehicles: int
    datasets: int
    rows: int
