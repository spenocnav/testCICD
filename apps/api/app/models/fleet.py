from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Table,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.master_data import GeotabDatabase, Vehicle

# Modos de armado de bandas de RPM. Espejo de `customers.range_mode` en Navi
# Vehículos; 'reglas' es el default y el comportamiento histórico.
FLEET_RANGE_MODES: tuple[str, ...] = ("reglas", "rpm")

# Asignación N:N usuario <-> flota. Un usuario puede administrar/consultar
# varias flotas; una flota puede tener varios usuarios.
user_fleets = Table(
    "user_fleets",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "fleet_id",
        UUID(as_uuid=True),
        ForeignKey("fleets.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Index("ix_user_fleets_fleet_user", "fleet_id", "user_id"),
)


class Fleet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Flota sobre la que se scopean los datos. Se asigna a usuarios.

    El rol `admin` ve todas (bypass); los roles de flota solo ven las
    asignadas. El filtro de flota viaja por el header `X-Fleet-Id`.

    Es además la réplica local de `customers` de Navi Vehículos (contrato de
    integración): `source_id` = customers.id de origen; NULL en flotas creadas
    a mano en el portal.
    """

    __tablename__ = "fleets"
    __table_args__ = (
        CheckConstraint(
            "range_mode IN (" + ", ".join(f"'{mode}'" for mode in FLEET_RANGE_MODES) + ")",
            name="ck_fleets_range_mode",
        ),
    )

    source_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    code: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    # De dónde salen las bandas de RPM de esta flota (contrato de integración,
    # `customers.range_mode`): 'reglas' = reglas Geotab (comportamiento
    # histórico), 'rpm' = cortes del eje de revoluciones definidos por motor
    # (`motor_rpm_bands`). Lo decide Navi Vehículos; el portal solo lo replica.
    range_mode: Mapped[str] = mapped_column(
        default="reglas", server_default="reglas", nullable=False
    )
    # ¿Esta flota tiene contratado el "Análisis de Ralentí"? Gobierna DOS cosas
    # a la vez: que el ETL extraiga sus episodios de ralentí desde Geotab
    # (`extract_ralenti.py` lee este flag por vehículo vía config_source) y que
    # la pestaña "Análisis Ralentí" de Reportes sea visible para el alcance.
    # Se activa desde /gestion/flotas; nace apagado (migración f9a0b1c20058).
    ralenti_analysis_enabled: Mapped[bool] = mapped_column(
        default=False, server_default=sa.false(), nullable=False
    )
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    geotab_databases: Mapped[list[GeotabDatabase]] = relationship(
        "GeotabDatabase",
        back_populates="fleet",
        cascade="all, delete-orphan",
    )
    vehicles: Mapped[list[Vehicle]] = relationship(
        "Vehicle",
        back_populates="fleet",
    )

    def __repr__(self) -> str:
        return f"<Fleet code={self.code!r}>"
