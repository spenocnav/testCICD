"""Auditoría de uso: una fila por petición autenticada del API.

La escribe `usage_tracker` en lote (nunca en el hot path de la petición) y la
lee la pantalla admin de uso. Append-only: las filas no se actualizan.

`user_id` no es FK a propósito: la auditoría sobrevive al borrado del usuario.
`route` es la plantilla de la ruta (`/api/v1/novedades/{novedad_id}`), nunca el
path crudo, para no persistir IDs ni placas y mantener la cardinalidad acotada.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, SmallInteger, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UsageEvent(Base):
    """Una petición autenticada registrada para auditoría de uso."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    route: Mapped[str] = mapped_column(String(200), nullable=False)
    status_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    # Flotas del header X-Fleet-Id; NULL/vacío = "todas las del alcance".
    fleet_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )

    __table_args__ = (
        CheckConstraint("duration_ms >= 0", name="ck_usage_events_duration_nonnegative"),
        CheckConstraint(
            "status_code >= 100 AND status_code <= 599",
            name="ck_usage_events_status_range",
        ),
        Index("ix_usage_events_ts", ts.desc()),
        Index("ix_usage_events_user_ts", user_id, ts.desc()),
    )
