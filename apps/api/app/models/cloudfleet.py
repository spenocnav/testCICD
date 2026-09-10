"""Réplica de solo-lectura de la API de CloudFleet (vehículos, OTs, cronogramas).

Tablas alimentadas por el job de sync contra la API de CloudFleet
(app/services/cloudfleet_sync_service.py). La PK propia es UUID; la clave natural
(varia por tabla: `code`, `number`, `consecutive+date_to_execute`) vive en un
unique constraint para soportar upsert.

El campo `raw` guarda el JSON crudo de la respuesta de CloudFleet para
trazabilidad/reproceso. `synced_at` marca el último cambio material de la fila;
la frescura de cada recurso se obtiene de `sync_state`, evitando reescribir
todas las filas sin cambios en cada pasada.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CloudfleetVehicle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Vehículo según CloudFleet. Clave natural: `code`."""

    __tablename__ = "cloudfleet_vehicles"

    code: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    brand_name: Mapped[str | None] = mapped_column(nullable=True)
    line_name: Mapped[str | None] = mapped_column(nullable=True)
    type_name: Mapped[str | None] = mapped_column(nullable=True)
    city: Mapped[str | None] = mapped_column(nullable=True)
    cost_center: Mapped[str | None] = mapped_column(nullable=True)
    primary_group: Mapped[str | None] = mapped_column(nullable=True)
    group1: Mapped[str | None] = mapped_column(nullable=True)
    odometer: Mapped[float | None] = mapped_column(Float, nullable=True)
    hourmeter: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_in_master: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<CloudfleetVehicle code={self.code!r}>"


class CloudfleetMeterSyncState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Estado persistente de una lectura enviada a CloudFleet.

    CloudFleet confirma ``POST /meters/`` con 204 y no expone una clave de
    idempotencia. Guardamos la lectura antes de publicarla para no duplicarla
    tras un timeout: una respuesta ambigua queda ``uncertain`` hasta que el
    siguiente snapshot de vehículos confirme el valor en CloudFleet.
    """

    __tablename__ = "cloudfleet_meter_sync_state"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "meter_type", name="uq_cf_meter_state_vehicle_type"),
        CheckConstraint(
            "meter_type IN ('distance', 'hours')",
            name="ck_cf_meter_state_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'uncertain')",
            name="ck_cf_meter_state_status",
        ),
        CheckConstraint("meter_value >= 0", name="ck_cf_meter_state_value_nonnegative"),
        Index("ix_cf_meter_state_status", "status", "updated_at"),
    )

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    meter_type: Mapped[str] = mapped_column(String(16), nullable=False)
    meter_value: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    meter_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<CloudfleetMeterSyncState {self.vehicle_id}:{self.meter_type}:{self.status}>"


class CloudfleetWorkOrder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Orden de trabajo de mantenimiento según CloudFleet. Clave natural: `number`."""

    __tablename__ = "cloudfleet_work_orders"
    __table_args__ = (
        Index("ix_cf_wo_start_date", "start_date"),
        Index("ix_cf_wo_technical_completion_date", "technical_completion_date"),
        Index(
            "ix_cf_wo_vehicle_completion",
            "vehicle_code",
            "technical_completion_date",
        ),
    )

    number: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    vehicle_code: Mapped[str] = mapped_column(nullable=False, index=True)
    status: Mapped[str | None] = mapped_column(String, index=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_issue: Mapped[str | None] = mapped_column(Text, nullable=True)
    affects_vehicle_availability: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    warranty: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    workshop_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    technical_completion_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    final_completion_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    estimated_finish_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cf_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cf_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cost_center: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    total_cost_labors: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    total_cost_parts: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    maintenance_labels: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<CloudfleetWorkOrder number={self.number} status={self.status!r}>"


class CloudfleetTrackingEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable tracking observation received from CloudFleet.

    The logical identity is ``(work_order_number, tracking_key)``. The row keeps
    the bounded, normalized contract needed by the portal and deliberately does
    not persist provider payloads, files or signed URLs.
    """

    __tablename__ = "cloudfleet_tracking_events"
    __table_args__ = (
        UniqueConstraint(
            "work_order_number",
            "tracking_key",
            name="uq_cf_tracking_event_order_key",
        ),
        Index(
            "ix_cf_tracking_event_order_event_at",
            "work_order_number",
            "event_at",
        ),
        Index(
            "ix_cf_tracking_event_label_event_at",
            "label_id",
            "event_at",
        ),
    )

    work_order_number: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    tracking_key: Mapped[str] = mapped_column(String(128), nullable=False)
    tracking_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tracking_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tracking_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(320), nullable=True)
    vehicle_code: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    client: Mapped[str | None] = mapped_column(String, nullable=True)
    cd: Mapped[str | None] = mapped_column(String, nullable=True)
    work_order_status: Mapped[str | None] = mapped_column(String, nullable=True)
    work_order_type: Mapped[str | None] = mapped_column(String, nullable=True)
    label_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label_name: Mapped[str | None] = mapped_column(String, nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_at_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    event_time_quality: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tracking_observed_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tracking_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tracking_observation_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observation_window_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CloudfleetMaintenanceSchedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cronograma de mantenimiento según CloudFleet.

    No hay clave natural única: un mismo `(consecutive, date_to_execute)`
    puede traer varias tareas distintas (distinto `task_name`). La
    idempotencia del sync la garantiza `replace_schedules_window` (DELETE de
    la ventana + bulk INSERT); la PK es la uuid autogenerada. El índice en
    `date_to_execute` (de `mapped_column index=True`) y en `vehicle_code`
    siguen siendo útiles para consultas de ventana.
    """

    __tablename__ = "cloudfleet_maintenance_schedules"
    __table_args__ = (Index("ix_cf_sched_vehicle_due", "vehicle_code", "date_to_execute"),)

    consecutive: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vehicle_code: Mapped[str | None] = mapped_column(String, index=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    task_name: Mapped[str | None] = mapped_column(String, nullable=True)
    routine_name: Mapped[str | None] = mapped_column(String, nullable=True)
    schedule_type: Mapped[str | None] = mapped_column(String, nullable=True)
    schedule_source: Mapped[str | None] = mapped_column(String, nullable=True)
    date_to_execute: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    date_to_execute_days_diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    wo_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    wo_execution_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<CloudfleetMaintenanceSchedule consecutive={self.consecutive} "
            f"date_to_execute={self.date_to_execute}>"
        )
