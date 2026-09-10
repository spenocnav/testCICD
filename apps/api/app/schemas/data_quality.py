from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SyncRunKind = Literal["cloudfleet", "master", "reportes", "novedades"]
SyncRunTrigger = Literal["manual", "worker", "cli"]
SyncRunMode = Literal["full", "incremental"]
# "partial": la corrida terminó y cargó el modelo semántico, pero algún
# dominio no crítico quedó sin actualizar (ver `result.pasos_fallidos`).
SyncRunStatus = Literal["success", "error", "partial"]


class SyncRunItem(BaseModel):
    """Una corrida de sync para la sección Auditoría de syncs."""

    id: str
    kind: SyncRunKind
    trigger: SyncRunTrigger
    mode: SyncRunMode
    status: SyncRunStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    result: dict[str, Any] | None
    error: str | None
    actor_email: str | None


class ActiveSyncRun(BaseModel):
    """Una corrida EN CURSO, para que la auditoría no se vea vacía mientras el
    pipeline trabaja.

    CloudFleet publica una fila `sync_run` con estado `running`; el ETL externo
    de reportes se lee de `etl_trigger_request`.

    `elapsed_ms` se calcula en el servidor contra `started_at` para no depender
    del reloj del navegador; el cliente lo sigue incrementando entre refetches.
    """

    id: str
    kind: SyncRunKind
    status: Literal["pending", "running"]
    # None mientras está `pending`: el worker aún no la reclamó.
    started_at: datetime | None
    queued_at: datetime
    elapsed_ms: int
    actor_email: str | None


class SyncRunList(BaseModel):
    items: list[SyncRunItem]
    # Corridas en vuelo, más viejas primero. Vacío cuando no hay nada corriendo.
    # NO se paginan: son pocas y siempre se muestran arriba de la primera página.
    active: list[ActiveSyncRun] = Field(default_factory=list)
    # Total de corridas CERRADAS que matchean el filtro (sin contar `active`).
    total: int = 0
    limit: int = 20
    offset: int = 0


class SyncRunIngest(BaseModel):
    """Payload que un pipeline externo (InformesRendimiento) reporta tras cada
    corrida. `kind` se fija a "reportes" en el endpoint; el caller no lo manda."""

    trigger: SyncRunTrigger = "worker"
    mode: SyncRunMode = "incremental"
    status: SyncRunStatus
    started_at: datetime
    finished_at: datetime
    result: dict[str, Any] | None = None
    error: str | None = None
    actor_email: str | None = None


class DataQualityExample(BaseModel):
    label: str
    detail: str | None = None


class DataQualityMetric(BaseModel):
    key: str
    label: str
    count: int
    severity: Literal["critical", "warning"]
    description: str
    examples: list[DataQualityExample] = Field(default_factory=list)


class DataQualitySummary(BaseModel):
    generated_at: datetime
    stale_after_hours: int
    fleet_count: int
    active_vehicles: int
    healthy_vehicles: int
    health_score: float
    geotab_databases: int
    latest_master_sync_at: datetime | None
    metrics: list[DataQualityMetric]


class DistanceAnomalyItem(BaseModel):
    fact_row_id: str
    vehicle_id: str | None
    fecha: date | None
    placa: str | None
    motor_type: str | None
    kms_ecm: float | None
    kms_gps: float | None
    kms_effective: float | None
    distance_diff_km: float | None
    distance_diff_pct: float | None
    distance_source: str | None
    distance_quality_status: str | None
    distance_quality_reason: str | None
    distance_quality_fingerprint: str | None
    gps_quality_valid: bool
    resolution_action: str | None = None
    review_status: Literal["pending", "auto_corrected", "resolved", "excluded", "no_data"]


class DistanceAnomalyList(BaseModel):
    items: list[DistanceAnomalyItem]
    total: int
    limit: int
    offset: int


class DistanceResolutionCreate(BaseModel):
    #: `use_ecm` faltaba aquí, y esa ausencia era lo que remataba el defecto: la
    #: acción existía en el `CheckConstraint` de la tabla y en el diálogo mental
    #: del módulo, pero el endpoint la rechazaba con 422 antes de llegar al
    #: servicio. Publicar el ECM es el desenlace correcto cuando el ECM es
    #: coherente y el GPS perdió viajes, que es el caso mayoritario.
    action: Literal["use_ecm", "use_gps", "exclude", "restore_auto"]
    reason: str = Field(min_length=10, max_length=1000)
    expected_fingerprint: str = Field(min_length=64, max_length=64)


class TrackingHealth(BaseModel):
    """Semáforo del pipeline de etiquetas para la pantalla Calidad de datos.

    `status` lo decide el servidor; el cliente solo lo pinta.
    - `ok`: ingestor y worker frescos, sin condiciones bloqueantes.
    - `degraded`: el ingestor vive, pero el worker no reporta o está degradado.
    - `down`: el ingestor no late o está fallando.
    - `unknown`: nunca se publicó un heartbeat.
    """

    status: Literal["ok", "degraded", "down", "unknown"]
    headline: str
    notes: list[str] = Field(default_factory=list)
    ingest_ok: bool | None = None
    ingest_age_seconds: float | None = None
    worker_status: str | None = None
    worker_age_seconds: float | None = None
    worker_reasons: list[str] = Field(default_factory=list)
    events_total: int = 0
    events_with_label: int = 0
    last_event_at: datetime | None = None
    active_orders: int | None = None
    tracking_backlog: int | None = None
