from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class VehicleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plate: str
    vin: str | None = None
    geotab_device_id: str | None = None
    geotab_customer_status: str
    fleet_id: uuid.UUID | None = None
    fleet_name: str | None = None
    geotab_database_id: uuid.UUID | None = None
    database_name: str | None = None
    marca: str | None = None
    linea: str | None = None
    marketing_model_name: str | None = None
    service_model_name: str | None = None
    ano_modelo: str | None = None
    tipo_combustible: str | None = None
    nombre_vehiculo: str | None = None
    vocacional: bool = False
    category: str = "Ninguna"
    engine_number: str | None = None
    technical_number: str | None = None
    cpl: str | None = None
    # Extensiones locales del ETL.
    motor_type: str | None = None
    group_key: str | None = None
    rpm_class: str | None = None
    tank_volume: float | None = None
    # Grupo interno del cliente (réplica de Navi Vehículos). El árbol completo
    # se sirve en GET /vehicles/groups; el nombre/ruta se resuelve del catálogo.
    vehicle_group_id: uuid.UUID | None = None
    is_active: bool
    synced_at: datetime | None = None


class FleetVehicleGroupRead(BaseModel):
    """Nodo del árbol de grupos internos de una flota (categoría/subcategoría).

    El árbol viaja plano: `parent_id` referencia otro nodo de la misma flota y
    NULL marca una categoría raíz. El cliente arma la jerarquía.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fleet_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    name: str
    is_active: bool


class VehicleToggle(BaseModel):
    is_active: bool


class PaginatedVehicles(BaseModel):
    items: list[VehicleRead]
    total: int
    total_all: int
    active_total: int
    limit: int
    offset: int


class MotorCurveCoverage(BaseModel):
    """CPL cubierto por un documento, cuántos vehículos lo usan y CÓMO se
    emparejó. El `match` va por CPL y no sólo a nivel del documento: un mismo
    PDF puede ser coincidencia exacta para unos vehículos y el único candidato
    del motor para otros."""

    cpl: str | None = None
    vehicle_count: int
    match: str


class MotorCurveRead(BaseModel):
    """Curva de par y potencia aplicable al alcance de flotas.

    `match` explica POR QUÉ se ofrece este documento:

    - `cpl`: el CPL del documento coincide con el del vehículo;
    - `motor`: el motor tiene un único documento y se usa aunque el CPL no
      coincida;
    - `ambiguo`: el motor tiene varios documentos y ninguno coincide con el CPL
      del vehículo. Se ofrecen todos porque elegir uno sería inventarlo.
    """

    id: uuid.UUID
    motor_type: str
    cpl: str | None = None
    original_filename: str | None = None
    content_type: str | None = None
    file_size: int | None = None
    source_updated_at: datetime | None = None
    match: str
    vehicle_count: int
    coverage: list[MotorCurveCoverage]
    # Estado de la caché local del binario: 'listo' | 'pending' | 'failed'.
    estado: str
    cacheado: bool


class MotorWithoutCurveRead(BaseModel):
    motor_type: str
    cpl: str | None = None
    vehicle_count: int


class MotorCurvesResponse(BaseModel):
    curvas: list[MotorCurveRead]
    # Motores del alcance sin documento. Se publican a propósito: el fail-closed
    # los haría desaparecer en silencio y el usuario no sabría qué le falta.
    sin_curva: list[MotorWithoutCurveRead]
