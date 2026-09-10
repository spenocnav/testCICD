from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CloudfleetStatus = Literal["pending", "sent", "failed"]
NovedadPriority = Literal["low", "medium", "high"]


class NovedadAttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    download_url: str


class NovedadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    fleet_id: uuid.UUID | None = None
    vehicle_code: str
    reported_at: datetime
    priority: NovedadPriority
    odometer: Decimal | None = None
    comment: str | None = None
    send_mail: bool
    cloudfleet_status: CloudfleetStatus
    cloudfleet_issue_number: int | None = None
    cloudfleet_error: str | None = None
    cloudfleet_response: dict[str, Any] | None = None
    # Estado REAL en CloudFleet, replicado por el sync diario. `None` = aún no
    # verificado. Independiente de `cloudfleet_status`, que es el del envío.
    external_is_done: bool | None = None
    external_done_at: datetime | None = None
    external_work_order_number: int | None = None
    external_synced_at: datetime | None = None
    #: Trabajo de la orden al que el taller ató la novedad. Sin él, la orden
    #: no dice cuál de sus trabajos corresponde a esta novedad.
    associated_labor_id: int | None = None
    associated_labor_name: str | None = None
    #: CloudFleet ya no conoce la issue. La novedad local se conserva —tiene
    #: evidencia en MinIO— pero deja de perseguir nada.
    external_deleted_at: datetime | None = None
    #: Estado de la orden enlazada. Distingue "el taller la tiene" de "el taller
    #: terminó", que `external_is_done` no distingue.
    external_work_order_status: str | None = None
    #: Referencia que el taller copia en cada trabajo de esta novedad.
    navifault_reference: str | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    attachments: list[NovedadAttachmentRead] = Field(default_factory=list)


class NovedadListItem(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID | None = None
    fleet_id: uuid.UUID | None = None
    vehicle_code: str
    reported_at: datetime
    priority: NovedadPriority
    comment: str | None = None
    cloudfleet_status: CloudfleetStatus
    cloudfleet_issue_number: int | None = None
    cloudfleet_error: str | None = None
    external_is_done: bool | None = None
    external_done_at: datetime | None = None
    external_work_order_number: int | None = None
    external_synced_at: datetime | None = None
    #: Trabajo de la orden al que el taller ató la novedad. Sin él, la orden
    #: no dice cuál de sus trabajos corresponde a esta novedad.
    associated_labor_id: int | None = None
    associated_labor_name: str | None = None
    #: CloudFleet ya no conoce la issue. La novedad local se conserva —tiene
    #: evidencia en MinIO— pero deja de perseguir nada.
    external_deleted_at: datetime | None = None
    #: Estado de la orden enlazada. Distingue "el taller la tiene" de "el taller
    #: terminó", que `external_is_done` no distingue.
    external_work_order_status: str | None = None
    #: Referencia que el taller copia en cada trabajo de esta novedad.
    navifault_reference: str | None = None
    attachment_count: int = 0
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class PaginatedNovedades(BaseModel):
    items: list[NovedadListItem]
    total: int
    limit: int
    offset: int


class GroupNovedadesBucket(BaseModel):
    """Conteo de novedades por grupo interno HOJA del vehículo reportado.

    ``group_id = None`` agrupa las novedades cuya placa no tiene grupo (o no
    existe en la réplica de vehículos). ``resueltas`` = ``external_is_done``
    confirmado en CloudFleet; ``abiertas`` incluye lo aún no verificado,
    misma semántica que el filtro ``external_done`` del listado.
    """

    group_id: uuid.UUID | None
    total: int
    abiertas: int
    resueltas: int


class NovedadStatusSyncResult(BaseModel):
    """Resumen de una pasada de réplica de estado desde CloudFleet."""

    candidates: int
    listed: int
    single_lookups: int
    resolved_now: int
    still_open: int
    missing: int
    missing_numbers: list[int] = Field(default_factory=list)
