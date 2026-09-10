"""Contratos HTTP de la calibración de la calificación.

Los rangos por campo se declaran con `Field(ge=..., le=...)` porque dan un error
por campo, que es lo que la interfaz necesita para marcar el input culpable.
Las invariantes CRUZADAS —las sumas de pesos, `objetivo < máximo`, el orden de
los umbrales— NO se reimplementan aquí: se delega en `validate_config`, la misma
función que valida lo que se lee de la base. Duplicarlas garantizaría que un día
divergieran y que un payload aceptado por HTTP fuera ilegible al releerse.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.services.calificacion_config import (
    DEFAULT_CALIFICACION_CONFIG,
    MAX_QHS_EVENT_WEIGHTS,
    PENALIZACIONES,
    CalificacionConfig,
    CalificacionConfigError,
    config_from_mapping,
    config_to_mapping,
)


def _penalizaciones_por_defecto() -> dict[str, bool]:
    """El mapa completo con el default de cada penalización del registro."""
    return {p.code: p.default_activa for p in PENALIZACIONES}


class PenalizacionCatalogo(BaseModel):
    """Una penalización disponible, para que la interfaz pinte su interruptor.

    El catálogo viaja en la respuesta y NO se duplica en el frontend: cuando se
    añade una penalización al registro del backend, su interruptor y su
    explicación aparecen solos en la pantalla de calibración. Duplicar las
    etiquetas garantizaría que un día el texto de la pantalla describiera una
    regla distinta de la que el cálculo aplica.
    """

    code: str
    nombre: str
    descripcion: str


class CalificacionConfigPayload(BaseModel):
    """Los parámetros ajustables. Obligatorios salvo `penalizaciones`: la
    pantalla envía el formulario completo, y un payload parcial dejaría dudas
    sobre si un campo ausente significa "no cambiar" o "volver al default".
    """

    peso_qhs: float = Field(ge=0, le=1)
    peso_qho: float = Field(ge=0, le=1)

    peso_eficiente: float = Field(ge=0, le=1)
    peso_ralenti: float = Field(ge=0, le=1)
    peso_exceso_rpm: float = Field(ge=0, le=1)

    efic_target: float = Field(gt=0, le=1)
    ralenti_target: float = Field(ge=0, le=1)
    ralenti_max: float = Field(gt=0, le=1)

    eventos_cap: float = Field(gt=0)
    rpm_cap_comercial_1000km: float = Field(gt=0)
    rpm_cap_vocacional_100h: float = Field(gt=0)

    # 1.0 = un exceso sobre la gobernada cuenta igual que cualquier otro. No
    # puede ser menor: no es una calibración, es integridad mecánica.
    rpm_high_weight: float = Field(ge=1)

    umbral_en_riesgo: float = Field(ge=0, le=100)
    umbral_cumple: float = Field(gt=0, le=100)

    # 0 = sin exposición mínima: todo vehículo con algún kilómetro se califica.
    exposicion_minima_km_dia: float = Field(ge=0)
    exposicion_minima_horas_dia: float = Field(ge=0)

    # True = promedio de flota ponderado por exposición; False = promedio simple.
    promedio_ponderado: bool = True

    qhs_event_weights: dict[str, float] = Field(max_length=MAX_QHS_EVENT_WEIGHTS)
    qhs_default_weight: float = Field(ge=0)

    # Excepción deliberada a "todos obligatorios": el mapa de penalizaciones
    # crece con el registro, y exigirlo completo rompería a un cliente que
    # todavía no conoce la penalización añadida ayer. Ausente o `None` = cada
    # código cae a su default. Los códigos los valida `validate_config`.
    penalizaciones: dict[str, bool] | None = None

    @model_validator(mode="after")
    def _validar_invariantes_cruzadas(self) -> CalificacionConfigPayload:
        try:
            config_from_mapping(self.model_dump())
        except CalificacionConfigError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def to_config(self) -> CalificacionConfig:
        """`CalificacionConfig` ya validada, lista para persistir."""
        return config_from_mapping(self.model_dump())


class CalificacionConfigValues(BaseModel):
    """Los parámetros ajustables, sin validación cruzada.

    Se usa para publicar Y como cuerpo del PUT. Que NO tenga `model_validator`
    es justo el punto: las invariantes cruzadas se validan dentro del handler
    con `config_from_mapping`, de modo que `CalificacionConfigError` sale como
    un 400 con su mensaje íntegro y no como un 422 con el motivo enterrado en
    `detail[0].msg`. No añadir aquí un validador cruzado.
    """

    peso_qhs: float
    peso_qho: float
    peso_eficiente: float
    peso_ralenti: float
    peso_exceso_rpm: float
    efic_target: float
    ralenti_target: float
    ralenti_max: float
    eventos_cap: float
    rpm_cap_comercial_1000km: float
    rpm_cap_vocacional_100h: float
    rpm_high_weight: float
    umbral_en_riesgo: float
    umbral_cumple: float
    exposicion_minima_km_dia: float
    exposicion_minima_horas_dia: float
    promedio_ponderado: bool
    qhs_event_weights: dict[str, float]
    qhs_default_weight: float
    # `{code: activa}` contra el registro `PENALIZACIONES`. Al publicar viene
    # siempre completo. Al escribir se puede omitir: cada código ausente cae a
    # su default, que es lo que hace que un cliente viejo no apague sin querer
    # una penalización que no conoce.
    penalizaciones: dict[str, bool] = Field(default_factory=_penalizaciones_por_defecto)


class CalificacionConfigResponse(BaseModel):
    """La calibración efectiva más su procedencia y los valores por defecto.

    Los defaults viajan en cada respuesta a propósito: la interfaz muestra "por
    defecto: 30 %" junto a cada campo y ofrece "restablecer" sin una segunda
    petición y sin duplicar los números en el frontend.
    """

    config: CalificacionConfigValues
    # "defecto" | "flota" | "mixto". "mixto" significa que el alcance mezcla
    # flotas con calibraciones distintas: `config` son los defaults y la
    # pantalla debe pedir al usuario que elija una flota.
    origen: str
    fleet_id: str | None = None
    actualizado_en: datetime | None = None
    actualizado_por: str | None = None
    defaults: CalificacionConfigValues = Field(
        default_factory=lambda: CalificacionConfigValues(
            **config_to_mapping(DEFAULT_CALIFICACION_CONFIG)
        )
    )
    # Catálogo de penalizaciones: qué existe, cómo se llama y qué hace cada una.
    # Se publica junto a `config.penalizaciones`, que sólo trae los booleanos:
    # con las dos cosas la pantalla arma los interruptores sin conocer ningún
    # código de antemano.
    penalizaciones_disponibles: list[PenalizacionCatalogo] = Field(
        default_factory=lambda: [
            PenalizacionCatalogo(
                code=p.code, nombre=p.nombre, descripcion=p.descripcion
            )
            for p in PENALIZACIONES
        ]
    )


class CalificacionConfigHistoryItem(BaseModel):
    """Una versión guardada de la calibración."""

    id: str
    creado_en: datetime
    # True: esa versión fue un "volver a los valores por defecto".
    es_reset: bool
    actor_user_id: str | None = None
    actor_email: str | None = None
    # None cuando la fila es un reset o cuando ya no es aplicable.
    config: CalificacionConfigValues | None = None
    # False: la fila existe pero hoy no pasa la validación. Se muestra para que
    # se vea qué pasó, no para reutilizarla.
    valida: bool = True


class CalificacionConfigHistory(BaseModel):
    items: list[CalificacionConfigHistoryItem] = Field(default_factory=list)
