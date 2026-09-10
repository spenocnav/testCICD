"""Solicitudes de corrida del ETL de reportes (InformesRendimiento).

Cola mínima entre el portal y el worker del ETL: el botón "Reportes" (o un
backfill) inserta una fila `pending`; el worker (contenedor
`reportes-etl-worker`) la reclama con SKIP LOCKED, corre el pipeline
(extract → transform → load a `analytics.*`) y cierra la fila. El historial
de la corrida en sí queda en `sync_run` (kind=reportes); esta tabla solo
coordina el disparo.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin


class EtlTriggerRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una solicitud de corrida del ETL. Estados: pending → running → done|error."""

    __tablename__ = "etl_trigger_request"

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    requested_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'done', 'error')",
            name="ck_etl_trigger_request_status",
        ),
        Index("ix_etl_trigger_request_status_created", status, "created_at"),
        Index(
            "ix_etl_trigger_request_pending",
            "created_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
    )


class EtlMicrobatchCommit(Base):
    """Ledger idempotente confirmado junto con facts y watermark."""

    __tablename__ = "etl_microbatch_commit"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)
    analytics_vehicle_id: Mapped[str] = mapped_column(String(64), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    output_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fact_row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    comparison: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "ix_etl_microbatch_vehicle_dataset_window",
            vehicle_id,
            dataset,
            window_start,
            window_end,
        ),
        Index("ix_etl_microbatch_committed_at", committed_at),
    )
