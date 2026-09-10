"""Contratos del módulo de reportes "Análisis Ralentí".

Las claves de los buckets (`DurationBucketKey`, `RpmBucketKey`) se declaran
como `Literal` para que FastAPI responda 422 ante un valor desconocido. La
única fuente de verdad de sus límites y etiquetas es
`app.services.ralenti_service`; una prueba fija que ambos conjuntos coincidan.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

DurationBucketKey = Literal["lt1", "1_5", "5_10", "gt10"]
RpmBucketKey = Literal["lt600", "600_800", "800_1000", "1000_1200", "gt1200", "sin_rpm"]
RalentiSortField = Literal["inicio", "duracion_segundos", "rpm_promedio"]


class RalentiDurationBucket(BaseModel):
    bucket: str
    label: str
    eventos: int
    minutos: float
    pct_eventos: float
    pct_minutos: float
    duracion_promedio_min: float | None


class RalentiRpmBucket(BaseModel):
    bucket: str
    label: str
    eventos: int
    minutos: float
    pct_eventos: float
    pct_minutos: float


class RalentiSummary(BaseModel):
    total_eventos: int
    total_minutos: float
    duracion_promedio_min: float | None
    vehiculos: int
    # Siempre los 4 y los 6 buckets, en el orden fijo del servicio, con ceros
    # cuando no hay datos: el cliente pinta la distribución sin rellenar huecos.
    por_duracion: list[RalentiDurationBucket]
    por_rpm: list[RalentiRpmBucket]


class RalentiPlacaRow(BaseModel):
    vehicle_id: str
    placa: str | None
    eventos_lt1: int
    eventos_1_5: int
    eventos_5_10: int
    eventos_gt10: int
    minutos_lt1: float
    minutos_1_5: float
    minutos_5_10: float
    minutos_gt10: float
    total_eventos: int
    total_minutos: float
    # Bucket de duración con más MINUTOS para la placa (empate → el más largo).
    rango_dominante: str | None


class RalentiHeatCell(BaseModel):
    latitud: float
    longitud: float
    eventos: int
    minutos: float


class RalentiTimePoint(BaseModel):
    bucket: str  # 'YYYY-MM-DD' (daily) | 'YYYY-MM' (monthly)
    eventos: int
    minutos: float


class RalentiEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_sk: str
    vehicle_id: str | None
    placa: str | None
    inicio: datetime | None
    fin: datetime | None
    duracion_min: float
    eventos_fuente: int
    rpm_promedio: float | None
    rpm_maximo: float | None
    latitud: float | None
    longitud: float | None
    duration_bucket: str
    rpm_bucket: str


class PaginatedRalentiEvents(BaseModel):
    items: list[RalentiEventRead]
    total: int
    limit: int
    offset: int
