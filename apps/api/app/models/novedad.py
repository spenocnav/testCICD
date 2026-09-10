from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.fleet import Fleet
    from app.models.master_data import Vehicle
    from app.models.user import User


class Novedad(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Novedad local enviada a Cloudfleet y enriquecida con evidencias."""

    __tablename__ = "novedades"
    __table_args__ = (
        CheckConstraint(
            "priority IN ('low', 'medium', 'high')",
            name="ck_novedades_priority",
        ),
        CheckConstraint(
            "cloudfleet_status IN ('pending', 'sent', 'failed')",
            name="ck_novedades_cloudfleet_status",
        ),
        # Idempotencia HTTP: misma (created_by, idempotency_key) -> una sola
        # novedad. Filtro parcial para no chocar con claves NULL.
        Index(
            "uq_novedades_idem_per_user",
            "created_by_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "ix_novedades_fleet_reported_id",
            "fleet_id",
            "reported_at",
            "id",
        ),
        # Hot path del sync de estado: enviadas cuya issue no consta resuelta.
        Index(
            "ix_novedades_external_open",
            "cloudfleet_issue_number",
            postgresql_where=text("cloudfleet_status = 'sent' AND external_is_done IS NOT TRUE"),
        ),
        # Las borradas en CloudFleet son pocas y se consultan para liberar la
        # falla que quedó escalada contra una issue que ya no existe.
        Index(
            "ix_novedades_external_deleted",
            "external_deleted_at",
            postgresql_where=text("external_deleted_at IS NOT NULL"),
        ),
        # Única: la referencia se busca dentro del texto de los trabajos y dos
        # novedades con la misma marca harían ambiguo lo que vino a desambiguar.
        Index(
            "ux_novedades_navifault_reference",
            "navifault_reference",
            unique=True,
            postgresql_where=text("navifault_reference IS NOT NULL"),
        ),
    )

    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    fleet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleets.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    vehicle_code: Mapped[str] = mapped_column(nullable=False, index=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reported_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[str] = mapped_column(nullable=False)
    odometer: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    responsible_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Trabajo de la orden de CloudFleet al que el taller ató esta novedad
    #: (`associatedLabor` del detalle de la issue). Identifica el `labors[].id`
    #: de la orden y, por `parts[].laborId`, los repuestos de ESE trabajo: una
    #: orden puede tener varios y no todos son de esta novedad.
    associated_labor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    associated_labor_name: Mapped[str | None] = mapped_column(String, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    send_mail: Mapped[bool] = mapped_column(default=False, nullable=False)

    cloudfleet_status: Mapped[str] = mapped_column(default="pending", nullable=False)
    cloudfleet_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cloudfleet_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cloudfleet_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Estado REAL de la issue en CloudFleet (`isDone`, `doneAt`,
    # `workOrderDoneNumber`), replicado por `novedad_status_sync_service`.
    # Distinto de `cloudfleet_status`, que es el estado del ENVÍO. NULL en
    # `external_is_done` significa "nunca verificado", no "abierta".
    external_is_done: Mapped[bool | None] = mapped_column(nullable=True)
    external_done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_work_order_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: CloudFleet ya no conoce la issue (404 explícito por número). Es
    #: observación y no decisión: el sync la devuelve a NULL si la issue
    #: reaparece, así que un 404 transitorio se corrige en la corrida
    #: siguiente. Ausencia del LISTADO no cuenta: `includeDone=true` devuelve
    #: sólo las resueltas, de modo que toda issue abierta falta de él.
    external_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Estado de la orden enlazada (`opened`, `onTechnicalCompletion`, `closed`,
    #: `voided`). Es lo único que distingue "el taller la tiene" de "el taller
    #: terminó": `external_is_done` se activa al ASIGNAR la novedad a una orden,
    #: con la orden todavía abierta y el vehículo sin intervenir.
    external_work_order_status: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # Idempotencia HTTP. La clave viene del header `Idempotency-Key` (validada
    # por el endpoint). `request_fingerprint` resume los campos de negocio para
    # detectar reintentos con payload distinto bajo la misma clave.
    #: Referencia corta que viaja dentro del comentario de la novedad. El taller
    #: la copia en cada trabajo que corresponda, y esa copia es lo que ata los
    #: trabajos a la novedad: CloudFleet no publica ese enlace por API y sólo
    #: admite un trabajo por novedad.
    navifault_reference: Mapped[str | None] = mapped_column(String(32), nullable=True)

    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    vehicle: Mapped[Vehicle | None] = relationship("Vehicle")
    fleet: Mapped[Fleet | None] = relationship("Fleet")
    created_by: Mapped[User | None] = relationship("User")
    attachments: Mapped[list[NovedadAttachment]] = relationship(
        "NovedadAttachment",
        back_populates="novedad",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    outbox: Mapped[NovedadOutbox | None] = relationship(
        "NovedadOutbox",
        back_populates="novedad",
        uselist=False,
        cascade="all, delete-orphan",
    )


class NovedadAttachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Metadata local de una evidencia almacenada en MinIO."""

    __tablename__ = "novedad_attachments"
    __table_args__ = (
        Index("ix_novedad_attachments_novedad_id", "novedad_id"),
        Index("ix_novedad_attachments_object_key", "object_key", unique=True),
    )

    novedad_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("novedades.id", ondelete="CASCADE"),
        nullable=False,
    )
    bucket: Mapped[str] = mapped_column(nullable=False)
    object_key: Mapped[str] = mapped_column(nullable=False)
    filename: Mapped[str] = mapped_column(nullable=False)
    content_type: Mapped[str] = mapped_column(nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    novedad: Mapped[Novedad] = relationship("Novedad", back_populates="attachments")


# Estados internos del outbox. NO exponer `processing` al frontend: la Novedad
# conserva el contrato visual pending/sent/failed.
NOVEDAD_OUTBOX_PENDING = "pending"
NOVEDAD_OUTBOX_PROCESSING = "processing"
NOVEDAD_OUTBOX_SENT = "sent"
NOVEDAD_OUTBOX_FAILED = "failed"
NOVEDAD_OUTBOX_STATUSES = (
    NOVEDAD_OUTBOX_PENDING,
    NOVEDAD_OUTBOX_PROCESSING,
    NOVEDAD_OUTBOX_SENT,
    NOVEDAD_OUTBOX_FAILED,
)


class NovedadOutbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Outbox transaccional para el envío a Cloudfleet.

    Garantías:
    - Novedad y outbox se insertan en la misma transacción. El envío externo
      se hace DESPUÉS del commit local.
    - Reintento seguro: el worker reclama ítems con `UPDATE ... RETURNING`
      para que sólo un proceso avance a `processing`.
    - Recuperación de stale: `locked_at` antiguo permite que un nuevo worker
      reintente si el anterior murió a mitad del dispatch.
    """

    __tablename__ = "novedad_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'failed')",
            name="ck_novedad_outbox_status",
        ),
        UniqueConstraint("novedad_id", name="uq_novedad_outbox_novedad_id"),
        # Hot path del worker: "dame el próximo item elegible". El orden del
        # índice debe coincidir con la query (status, available_at, id).
        Index(
            "ix_novedad_outbox_due",
            "status",
            "available_at",
            "id",
            postgresql_where=text("status IN ('pending', 'failed')"),
        ),
        Index(
            "ix_novedad_outbox_stale_processing",
            "locked_at",
            "id",
            postgresql_where=text(
                "status = 'processing' AND locked_at IS NOT NULL"
            ),
        ),
    )

    novedad_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("novedades.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(nullable=False, default=NOVEDAD_OUTBOX_PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    novedad: Mapped[Novedad] = relationship("Novedad", back_populates="outbox")
