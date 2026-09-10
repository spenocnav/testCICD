"""Contratos de la auditoría de uso (pantalla admin /gestion/uso)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel


class UsageDailyPoint(BaseModel):
    """Actividad de un día (zona horaria de reportes)."""

    day: date
    requests: int
    users: int


class UsageUserRow(BaseModel):
    """Actividad agregada de un usuario en el rango."""

    user_id: uuid.UUID
    # None si el usuario ya no existe en `users` (la auditoría lo sobrevive).
    email: str | None
    full_name: str | None
    is_active: bool | None
    requests: int
    last_seen: datetime
    top_section: str | None


class UsageSectionRow(BaseModel):
    """Actividad por sección del portal (segmento de la ruta del API)."""

    section: str
    requests: int
    users: int


class UsageFleetRow(BaseModel):
    """Actividad por flota consultada (header X-Fleet-Id)."""

    fleet_id: uuid.UUID
    fleet_name: str | None
    requests: int
    users: int


class UsageRouteRow(BaseModel):
    """Actividad por endpoint (plantilla de ruta)."""

    method: str
    route: str
    requests: int
    avg_ms: float
    p95_ms: float


class UsageSummary(BaseModel):
    """Resumen de uso del portal para el rango pedido."""

    start_date: date
    end_date: date
    total_requests: int
    active_users: int
    daily: list[UsageDailyPoint]
    users: list[UsageUserRow]
    sections: list[UsageSectionRow]
    fleets: list[UsageFleetRow]
    routes: list[UsageRouteRow]


class UsageUserDetail(BaseModel):
    """Desglose de la actividad de UN usuario en el rango."""

    user_id: uuid.UUID
    email: str | None
    full_name: str | None
    is_active: bool | None
    start_date: date
    end_date: date
    total_requests: int
    last_seen: datetime | None
    daily: list[UsageDailyPoint]
    sections: list[UsageSectionRow]
    fleets: list[UsageFleetRow]
    routes: list[UsageRouteRow]
