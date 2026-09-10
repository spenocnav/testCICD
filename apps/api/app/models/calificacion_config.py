"""Calibración de la calificación por flota, append-only.

Una fila por cada vez que alguien guarda; **la última por flota gana**. No es
una fila mutable a propósito: este dato cambia el puntaje de TODA la flota, así
que hay que poder decir quién lo cambió, cuándo y a qué. Es el mismo patrón de
`distance_quality_decisions`.

Todos los parámetros son **nullable** y sin `server_default`. Dos razones:

- una fila escrita antes de añadir un parámetro nuevo tiene que seguir siendo
  legible, y `config_from_mapping` ya cae al default cuando la clave falta o es
  `None`;
- un `server_default` congelaría la calibración vigente dentro de la fila: el
  día que cambie un default del código, las filas viejas seguirían arrastrando
  el número antiguo sin que nadie lo haya decidido.

`is_reset=True` significa "esta flota volvió a los valores por defecto". Se
guarda como fila y no como borrado para que la decisión conste: los parámetros
quedan en NULL y la lectura devuelve los defaults, pero con el actor y la fecha
de quien lo decidió.

Los `CheckConstraint` son la SEGUNDA barrera, tolerante con NULL. La validación
de verdad es `app.services.calificacion_config.validate_config`, que además
comprueba las invariantes cruzadas que SQL no puede expresar barato (las sumas
de pesos). Estos checks sólo atajan una escritura hecha por fuera del servicio.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin

CALIFICACION_CONFIG_TABLE = "calificacion_config_versions"
CALIFICACION_CONFIG_FLEET_INDEX = "ix_calificacion_config_fleet_created"

# Parámetros acotados a [0, 1] uno por uno. El orden relativo entre
# `ralenti_target` y `ralenti_max` va en su propio check.
_UNIT_INTERVAL_FIELDS: tuple[str, ...] = (
    "peso_qhs",
    "peso_qho",
    "peso_eficiente",
    "peso_ralenti",
    "peso_exceso_rpm",
    "ralenti_target",
    "ralenti_max",
)

# Topes de densidad: un 0 dejaría el componente en 0 con un solo evento.
_POSITIVE_FIELDS: tuple[str, ...] = (
    "eventos_cap",
    "rpm_cap_comercial_1000km",
    "rpm_cap_vocacional_100h",
)


class CalificacionConfigVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = CALIFICACION_CONFIG_TABLE

    fleet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fleets.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Reparto entre seguridad y operación.
    peso_qhs: Mapped[float | None] = mapped_column(Float, nullable=True)
    peso_qho: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Reparto interno del QHO.
    peso_eficiente: Mapped[float | None] = mapped_column(Float, nullable=True)
    peso_ralenti: Mapped[float | None] = mapped_column(Float, nullable=True)
    peso_exceso_rpm: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Meta de tiempo en rango eficiente y ventana de ralentí.
    efic_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    ralenti_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    ralenti_max: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Topes de densidad de eventos.
    eventos_cap: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpm_cap_comercial_1000km: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpm_cap_vocacional_100h: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Agravación de los excesos sobre la gobernada del motor.
    rpm_high_weight: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Cortes de estado sobre el QGen.
    umbral_en_riesgo: Mapped[float | None] = mapped_column(Float, nullable=True)
    umbral_cumple: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Exposición mínima POR DÍA del periodo para que el vehículo se califique.
    # 0 desactiva la regla. Ver `CalificacionConfig.exposicion_minima_dia`.
    exposicion_minima_km_dia: Mapped[float | None] = mapped_column(Float, nullable=True)
    exposicion_minima_horas_dia: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )

    # True = promedio de flota ponderado por exposición (histórico), False =
    # promedio simple. Nullable como el resto: NULL cae al default del código.
    # No confundir con `is_reset`, que es un booleano de la FILA y no un
    # parámetro: por eso ese sí es NOT NULL con `server_default`.
    promedio_ponderado: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Severidad por tipo de evento de seguridad, en minúsculas, y el peso de un
    # tipo que todavía no está calibrado.
    qhs_event_weights: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    qhs_default_weight: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Qué penalizaciones anulan el puntaje: `{code: activa}` contra el registro
    # `PENALIZACIONES`. Va en JSONB y no en una columna booleana por penalización
    # por la misma razón que `qhs_event_weights`: el registro crece, y una
    # penalización nueva no debe costar una migración ni dejar filas viejas
    # ilegibles. Una clave ausente cae al default del registro, así que una fila
    # escrita antes de que existiera la penalización sigue resolviéndose bien.
    penalizaciones: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # "Volver a los valores por defecto" como decisión auditable, no como
    # borrado de la fila anterior.
    is_reset: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    __table_args__ = (
        *(
            CheckConstraint(
                f"{name} IS NULL OR ({name} >= 0 AND {name} <= 1)",
                name=f"ck_calificacion_config_{name}_unit",
            )
            for name in _UNIT_INTERVAL_FIELDS
        ),
        *(
            CheckConstraint(
                f"{name} IS NULL OR {name} > 0",
                name=f"ck_calificacion_config_{name}_positive",
            )
            for name in _POSITIVE_FIELDS
        ),
        CheckConstraint(
            "efic_target IS NULL OR (efic_target > 0 AND efic_target <= 1)",
            name="ck_calificacion_config_efic_target",
        ),
        CheckConstraint(
            "ralenti_target IS NULL OR ralenti_max IS NULL OR ralenti_target < ralenti_max",
            name="ck_calificacion_config_ralenti_orden",
        ),
        CheckConstraint(
            "rpm_high_weight IS NULL OR rpm_high_weight >= 1",
            name="ck_calificacion_config_rpm_high_weight",
        ),
        CheckConstraint(
            "umbral_en_riesgo IS NULL OR (umbral_en_riesgo >= 0 AND umbral_en_riesgo <= 100)",
            name="ck_calificacion_config_umbral_en_riesgo",
        ),
        CheckConstraint(
            "umbral_cumple IS NULL OR (umbral_cumple > 0 AND umbral_cumple <= 100)",
            name="ck_calificacion_config_umbral_cumple",
        ),
        CheckConstraint(
            "umbral_en_riesgo IS NULL OR umbral_cumple IS NULL OR umbral_en_riesgo < umbral_cumple",
            name="ck_calificacion_config_umbral_orden",
        ),
        CheckConstraint(
            "qhs_default_weight IS NULL OR qhs_default_weight >= 0",
            name="ck_calificacion_config_qhs_default_weight",
        ),
        # Cero es válido y significa "sin exposición mínima": es la salida para
        # una flota que prefiera calificar a todo el que tenga un kilómetro.
        *(
            CheckConstraint(
                f"{name} IS NULL OR {name} >= 0",
                name=f"ck_calificacion_config_{name}",
            )
            for name in ("exposicion_minima_km_dia", "exposicion_minima_horas_dia")
        ),
        # Tolerante con NULL como los demás. No comprueba los códigos —eso lo
        # hace `validate_config`, que es la autoridad—, sólo que la forma sea un
        # objeto JSON: un escalar o un arreglo escritos por fuera del servicio
        # harían estallar la lectura con un `TypeError`, que `_resolve_row` no
        # atrapa, y una calibración corrupta tumbaría el reporte entero en vez
        # de caer a los defaults.
        CheckConstraint(
            "penalizaciones IS NULL OR jsonb_typeof(penalizaciones) = 'object'",
            name="ck_calificacion_config_penalizaciones_objeto",
        ),
        # "La última por flota" es la única lectura caliente: el índice la
        # resuelve sin ordenar. Cubre también el filtro por `fleet_id` solo,
        # así que la columna no lleva índice propio.
        Index(
            CALIFICACION_CONFIG_FLEET_INDEX,
            "fleet_id",
            text("created_at DESC"),
        ),
    )
