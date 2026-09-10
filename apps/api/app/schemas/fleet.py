from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class FleetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    is_active: bool
    # De dónde salen las bandas de RPM de la flota (lo decide Navi Vehículos):
    # 'reglas' = reglas Geotab; 'rpm' = rangos de RPM del motor. Read-only.
    range_mode: str = "reglas"
    # ¿La flota tiene >=1 vehículo activo? El selector global oculta las vacías.
    # `/me` lo calcula; otros productores (from_attributes) usan el default.
    has_vehicles: bool = True
    # Módulo "Análisis Ralentí" contratado para esta flota. Lo decide el
    # administrador en /gestion/flotas; la pestaña de Reportes se muestra sólo
    # si TODAS las flotas del alcance lo tienen activo.
    ralenti_analysis_enabled: bool = False


class FleetCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    is_active: bool = True


class FleetUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    is_active: bool | None = None
    ralenti_analysis_enabled: bool | None = None
