"""Auditoría de corridas de sync (réplica CloudFleet y snapshot maestro).

Cada fila es UNA pasada de un orquestador (`run_cloudfleet_sync` o
`run_sync`): cuándo corrió, quién/qué la disparó (worker, manual, cli), modo
(full/incremental), resultado (conteos en `result`) y estado (success/error).

A diferencia de `sync_state` (que solo guarda el último watermark por recurso,
sobreescrito en cada pasada), esta tabla es un HISTORIAL append-only para
revisar salud y trazabilidad de las sincronizaciones.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SyncRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una pasada de sincronización registrada para auditoría."""

    __tablename__ = "sync_run"

    # "cloudfleet" (réplica CloudFleet) | "master" (snapshot Navi Vehículos) |
    # "reportes" (pipeline InformesRendimiento, reportado vía endpoint de ingesta).
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # "manual" (botón) | "worker" (loop) | "cli" (una pasada a mano).
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    # "full" | "incremental".
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    # "running" mientras la pasada está en curso; al cerrar queda como
    # "success" | "partial" | "error".
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Conteos de la pasada (fetched/upserted/...). Espejo del summary/SyncResult.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Mensaje de error si status == "error".
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Actor cuando trigger == "manual". Denormalizamos el email para no
    # depender de un JOIN si el usuario se borra.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('master', 'cloudfleet', 'reportes', 'novedades')",
            name="ck_sync_run_kind",
        ),
        CheckConstraint(
            "trigger IN ('manual', 'worker', 'cli')",
            name="ck_sync_run_trigger",
        ),
        CheckConstraint(
            "mode IN ('full', 'incremental')",
            name="ck_sync_run_mode",
        ),
        CheckConstraint(
            "status IN ('running', 'success', 'partial', 'error')",
            name="ck_sync_run_status",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_sync_run_duration_nonnegative",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_sync_run_time_order",
        ),
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL AND duration_ms IS NULL) "
            "OR (status <> 'running' AND finished_at IS NOT NULL "
            "AND duration_ms IS NOT NULL)",
            name="ck_sync_run_lifecycle",
        ),
        Index("ix_sync_run_started_at", started_at.desc()),
        Index("ix_sync_run_kind_started_at", kind, started_at.desc()),
    )
