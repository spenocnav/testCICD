"""Calibración de la calificación: qué se puede ajustar por flota y qué no.

La fórmula de la calificación vivía en constantes de módulo de
`analytics_service`. Este archivo la convierte en un objeto de configuración
para que cada flota pueda recalibrar los parámetros que dependen de SU
operación, sin tocar código y sin poder alterar lo que depende del motor.

FRONTERA, y es el punto entero de este módulo:

- **Ajustable** — depende de cómo opera el cliente: los pesos de QHS/QHO y de
  los tres componentes del QHO, la meta de tiempo en rango eficiente, la
  ventana de ralentí, los topes de densidad de eventos (por 1000 km o por 100
  horas ECM), la agravación de los excesos sobre la gobernada, los umbrales de
  estado, la exposición mínima para que un vehículo se pueda calificar y la
  severidad relativa de cada tipo de evento de seguridad.
- **NO ajustable** — depende del motor y viene de Navi Vehículos: la velocidad
  gobernada y la sobrevelocidad máxima (`public.motor_catalog`), y qué rango de
  RPM cuenta como económico o balanceado (`public.motor_rpm_bands`). Un cliente
  no puede declarar que su motor gira más de lo que gira.
- **Penalizaciones** — reglas que ANULAN el puntaje en vez de ponderarlo. Desde
  el 2026-08-28 se pueden activar y desactivar por flota, vía el registro
  `PENALIZACIONES`. El argumento en contra sigue en pie y conviene sopesarlo
  antes de apagar una: es integridad mecánica, no una calibración de operación.
  Lo que acota el daño es que apagarla NO oculta nada — los excesos se siguen
  contando, publicando y agravando; sólo dejan de anular el puntaje.

`DEFAULT_CALIFICACION_CONFIG` reproduce EXACTAMENTE los valores con los que la
calificación se calculó hasta el 2026-08-27. Cualquier cambio en esos números
cambia el puntaje de todas las flotas que no tienen calibración propia: no se
tocan sin medir el antes y el después.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

# Tolerancia al comparar sumas de pesos. Los pesos llegan como float desde JSON
# y desde `numeric` de PostgreSQL; exigir igualdad exacta rechazaría 0.5+0.3+0.2.
WEIGHT_SUM_TOLERANCE = 1e-6

_DEFAULT_QHS_EVENT_WEIGHTS: Mapping[str, float] = MappingProxyType(
    {
        "excesos de velocidad": 1.0,
        "frenadas bruscas": 1.0,
        "giros bruscos": 1.0,
        "aceleraciones bruscas": 0.5,
        "baches o resaltos fuertes": 0.25,
    }
)


#: Código de la penalización por superar la sobrevelocidad máxima del motor.
PENALIZACION_SOBREVELOCIDAD_RPM = "sobrevelocidad_rpm"


@dataclass(frozen=True, slots=True)
class Penalizacion:
    """Una anulación del puntaje que el cliente puede activar o desactivar.

    Existe como REGISTRO y no como un booleano suelto por flota porque se
    esperan más: cada penalización nueva se añade aquí y queda disponible para
    calibrar, sin migración, porque el mapa viaja en JSONB igual que
    `qhs_event_weights`.
    """

    code: str
    nombre: str
    descripcion: str
    #: Si una calibración guardada no menciona esta penalización, se asume este
    #: valor. En `True` a propósito: una penalización nueva aplica salvo que el
    #: cliente decida lo contrario, no al revés.
    default_activa: bool = True


PENALIZACIONES: tuple[Penalizacion, ...] = (
    Penalizacion(
        code=PENALIZACION_SOBREVELOCIDAD_RPM,
        nombre="Sobrevelocidad de RPM",
        descripcion=(
            "Un solo exceso por encima de la sobrevelocidad máxima del motor deja "
            "el puntaje general del vehículo en 0 y su estado en «No cumple», sin "
            "importar cuánto sumaran los demás componentes. El puntaje que habría "
            "tenido se conserva visible. Desactivarla NO oculta los excesos: se "
            "siguen contando y mostrando, sólo dejan de anular el puntaje."
        ),
    ),
)

PENALIZACION_CODES: frozenset[str] = frozenset(p.code for p in PENALIZACIONES)

_DEFAULT_PENALIZACIONES: Mapping[str, bool] = MappingProxyType(
    {p.code: p.default_activa for p in PENALIZACIONES}
)


class CalificacionConfigError(ValueError):
    """Calibración inválida. El mensaje es apto para mostrarse al usuario."""


@dataclass(frozen=True, slots=True)
class CalificacionConfig:
    """Parámetros ajustables de la calificación. Inmutable a propósito.

    Se construye una vez por petición y se pasa hacia abajo. Que sea `frozen`
    evita el error de que un cálculo intermedio la mute y las cifras de la
    tabla dejen de cuadrar con las del gauge.
    """

    # Reparto entre seguridad y operación.
    peso_qhs: float = 0.5
    peso_qho: float = 0.5

    # Reparto interno del QHO. Suman 1.
    peso_eficiente: float = 0.5
    peso_ralenti: float = 0.3
    peso_exceso_rpm: float = 0.2

    # Tiempo en rango eficiente: la fracción que ya puntúa 100.
    efic_target: float = 0.70

    # Ralentí: `target` o menos puntúa 100; `max` o más puntúa 0.
    ralenti_target: float = 0.10
    ralenti_max: float = 0.30

    # Densidad de eventos de seguridad por 1000 km que lleva el QHS a 0.
    eventos_cap: float = 15.0

    # Densidad de excesos de RPM ponderados que lleva el componente a 0, según
    # el tipo de operación.
    rpm_cap_comercial_1000km: float = 150.0
    rpm_cap_vocacional_100h: float = 1400.0

    # Cuánto cuenta un exceso que supera la gobernada del motor. 1.0 = igual que
    # cualquier otro; 2.0 = el doble.
    rpm_high_weight: float = 2.0

    # Cortes de estado sobre el QGen.
    umbral_en_riesgo: float = 70.0
    umbral_cumple: float = 85.0

    # Exposición mínima para que un vehículo se pueda calificar, expresada POR
    # DÍA del periodo consultado. No es un umbral absoluto a propósito: la
    # pantalla consulta tres meses por defecto, un mes al hacer clic en la serie
    # y lo que el usuario elija en el filtro, así que un mínimo fijo en km sería
    # indulgente en un rango largo y absurdo en uno corto.
    #
    # Por qué existe: QHS y QHO son TASAS (eventos por 1000 km, tiempo sobre
    # tiempo). Con un denominador minúsculo el puntaje deja de medir conducción
    # y pasa a medir que el vehículo no se movió: es imposible registrar un
    # evento de seguridad en 200 metros, así que el QHS sale 100. Ese puntaje no
    # es bueno, es indefinido, y además arrastraba el donut y la tabla.
    #
    # Un vehículo por debajo del mínimo NO se califica: su QGen queda en None,
    # sale del promedio y del donut, y `qgen_base` conserva lo que habría
    # puntuado. NO se le pone 0: no operar no es operar mal.
    #
    # 0 desactiva la regla, y es la salida para una flota que prefiera el
    # comportamiento anterior. Calibrado sobre la distribución real por
    # vehículo-mes (2026): 5 km/día deja fuera el 4,5 % de los vehículo-mes,
    # todos por debajo del p10 (3,12 km/día); la mediana de la flota es 56,7
    # km/día. En vocacionales 0,5 h/día deja fuera 2 de 82, con mediana 7,08
    # h/día.
    exposicion_minima_km_dia: float = 5.0
    exposicion_minima_horas_dia: float = 0.5

    # Cómo se agrega el puntaje de los vehículos en la cifra de la flota.
    #
    # True (histórico y default): PONDERADO por exposición —bloques de 1000 km
    # comerciales o 100 h ECM vocacionales—. Responde "¿cómo se condujo esta
    # flota?": un vehículo que hizo 5.000 km pesa más que uno que hizo 500,
    # porque aportó más operación al resultado.
    #
    # False: promedio SIMPLE, un vehículo un voto. Responde "¿cómo van mis
    # vehículos?": útil para una flota pequeña o muy heterogénea, donde el
    # ponderado deja al gauge describiendo sólo a los dos o tres vehículos
    # intensivos y esconde al resto.
    #
    # Ninguno de los dos es "el correcto": son dos preguntas distintas, y por
    # eso la elección es del cliente. Lo que NO cambia con la bandera es quién
    # entra al promedio: eso lo deciden la exposición mínima y la penalización.
    promedio_ponderado: bool = True

    # Severidad por tipo de evento de seguridad, en minúsculas.
    qhs_event_weights: Mapping[str, float] = field(default=_DEFAULT_QHS_EVENT_WEIGHTS)

    # Severidad de un tipo que todavía no está calibrado: cuenta completo.
    qhs_default_weight: float = 1.0

    # Qué penalizaciones anulan el puntaje. Clave: código del registro
    # `PENALIZACIONES`. Una clave ausente cae al default de su registro.
    penalizaciones: Mapping[str, bool] = field(default=_DEFAULT_PENALIZACIONES)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "qhs_event_weights", MappingProxyType(dict(self.qhs_event_weights))
        )
        object.__setattr__(
            self, "penalizaciones", MappingProxyType(dict(self.penalizaciones))
        )

    def penalizacion_activa(self, code: str) -> bool:
        """¿Está activa esta penalización para la flota?

        Una calibración guardada ANTES de que existiera la penalización no la
        menciona, y entonces manda el default del registro. Es la misma regla
        que el resto del módulo: la clave ausente cae al valor por defecto, no
        a "apagado".
        """
        if code in self.penalizaciones:
            return bool(self.penalizaciones[code])
        return bool(_DEFAULT_PENALIZACIONES.get(code, True))

    def event_weight(self, event_type: str | None) -> float:
        """Severidad del tipo, con el peso por defecto si no está calibrado."""
        key = (event_type or "").strip().lower()
        return self.qhs_event_weights.get(key, self.qhs_default_weight)

    def rpm_cap(self, *, vocacional: bool) -> float:
        return self.rpm_cap_vocacional_100h if vocacional else self.rpm_cap_comercial_1000km

    def exposicion_minima_dia(self, *, vocacional: bool) -> float:
        """Exposición mínima por día del periodo, en la unidad del vehículo.

        Un vocacional se mide en horas ECM y un comercial en kilómetros, igual
        que en `rpm_cap` y en `_rpm_exposure_units`: mezclar las dos unidades es
        justo el error que la separación por tipo de operación evita.
        """
        return (
            self.exposicion_minima_horas_dia
            if vocacional
            else self.exposicion_minima_km_dia
        )

    def with_overrides(self, **kwargs: Any) -> CalificacionConfig:
        return replace(self, **kwargs)


DEFAULT_CALIFICACION_CONFIG = CalificacionConfig()

# Campos que un cliente puede enviar. Se declara explícito y no por
# introspección: así añadir un campo interno al dataclass no lo vuelve
# editable por accidente desde HTTP.
EDITABLE_FIELDS: tuple[str, ...] = (
    "peso_qhs",
    "peso_qho",
    "peso_eficiente",
    "peso_ralenti",
    "peso_exceso_rpm",
    "efic_target",
    "ralenti_target",
    "ralenti_max",
    "eventos_cap",
    "rpm_cap_comercial_1000km",
    "rpm_cap_vocacional_100h",
    "rpm_high_weight",
    "umbral_en_riesgo",
    "umbral_cumple",
    "exposicion_minima_km_dia",
    "exposicion_minima_horas_dia",
    "promedio_ponderado",
    "qhs_event_weights",
    "qhs_default_weight",
    "penalizaciones",
)

#: Campos booleanos: `config_from_mapping` no los puede pasar por `float`.
_BOOL_FIELDS: frozenset[str] = frozenset({"promedio_ponderado"})

# Tope de tipos de evento calibrables. No es un límite de negocio sino de
# cardinalidad: el peso viaja en JSONB y se lee en cada petición.
MAX_QHS_EVENT_WEIGHTS = 40
MAX_EVENT_TYPE_LENGTH = 120


def validate_config(config: CalificacionConfig) -> None:
    """Rechaza una calibración que no produce un puntaje interpretable.

    Se valida acá y NO sólo en Pydantic porque el mismo objeto se construye
    desde la base: una fila escrita por una versión anterior del esquema tiene
    que fallar al leerse, no producir puntajes silenciosamente absurdos.
    """
    suma_top = config.peso_qhs + config.peso_qho
    if abs(suma_top - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise CalificacionConfigError(
            f"Los pesos de seguridad y operación deben sumar 1; suman {suma_top:g}."
        )
    suma_qho = config.peso_eficiente + config.peso_ralenti + config.peso_exceso_rpm
    if abs(suma_qho - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise CalificacionConfigError(
            "Los pesos de rango eficiente, ralentí y excesos de RPM deben sumar 1; "
            f"suman {suma_qho:g}."
        )
    for nombre in ("peso_qhs", "peso_qho", "peso_eficiente", "peso_ralenti", "peso_exceso_rpm"):
        valor = getattr(config, nombre)
        if not 0.0 <= valor <= 1.0:
            raise CalificacionConfigError(f"{nombre} debe estar entre 0 y 1; llegó {valor:g}.")

    if not 0.0 < config.efic_target <= 1.0:
        raise CalificacionConfigError(
            "La meta de tiempo en rango eficiente debe estar entre 0 y 1 (exclusivo en 0); "
            f"llegó {config.efic_target:g}."
        )
    if not 0.0 <= config.ralenti_target < config.ralenti_max <= 1.0:
        raise CalificacionConfigError(
            "El ralentí debe cumplir 0 ≤ objetivo < máximo ≤ 1; llegó objetivo "
            f"{config.ralenti_target:g} y máximo {config.ralenti_max:g}."
        )

    for nombre in ("eventos_cap", "rpm_cap_comercial_1000km", "rpm_cap_vocacional_100h"):
        valor = getattr(config, nombre)
        if valor <= 0:
            raise CalificacionConfigError(
                f"{nombre} debe ser mayor que 0; llegó {valor:g}. Un tope de 0 dejaría "
                "el componente en 0 para cualquier vehículo con un solo evento."
            )
    if config.rpm_high_weight < 1.0:
        raise CalificacionConfigError(
            "La agravación de los excesos sobre la gobernada no puede ser menor que 1: "
            f"un exceso no puede contar menos que un evento normal. Llegó {config.rpm_high_weight:g}."
        )

    if not 0.0 <= config.umbral_en_riesgo < config.umbral_cumple <= 100.0:
        raise CalificacionConfigError(
            "Los umbrales deben cumplir 0 ≤ 'no cumple' < 'cumple' ≤ 100; llegó "
            f"{config.umbral_en_riesgo:g} y {config.umbral_cumple:g}."
        )

    for nombre in ("exposicion_minima_km_dia", "exposicion_minima_horas_dia"):
        valor = getattr(config, nombre)
        if valor < 0:
            raise CalificacionConfigError(
                f"{nombre} no puede ser negativa; llegó {valor:g}. Usa 0 para "
                "desactivar la exposición mínima."
            )

    if len(config.qhs_event_weights) > MAX_QHS_EVENT_WEIGHTS:
        raise CalificacionConfigError(
            f"No se pueden calibrar más de {MAX_QHS_EVENT_WEIGHTS} tipos de evento."
        )
    for clave, peso in config.qhs_event_weights.items():
        if not clave or not clave.strip():
            raise CalificacionConfigError("Hay un tipo de evento con nombre vacío.")
        if len(clave) > MAX_EVENT_TYPE_LENGTH:
            raise CalificacionConfigError(
                f"El nombre del tipo de evento '{clave[:30]}…' es demasiado largo."
            )
        if peso < 0:
            raise CalificacionConfigError(
                f"La severidad de '{clave}' no puede ser negativa; llegó {peso:g}."
            )
    if config.qhs_default_weight < 0:
        raise CalificacionConfigError(
            "La severidad de un tipo no calibrado no puede ser negativa; llegó "
            f"{config.qhs_default_weight:g}."
        )

    # Se rechaza una penalización desconocida en vez de ignorarla: un código mal
    # escrito se guardaría como si nada y el cliente creería haberla apagado.
    for clave in config.penalizaciones:
        if clave not in PENALIZACION_CODES:
            conocidas = ", ".join(sorted(PENALIZACION_CODES))
            raise CalificacionConfigError(
                f"La penalización '{clave}' no existe. Las disponibles son: {conocidas}."
            )


def config_from_mapping(values: Mapping[str, Any]) -> CalificacionConfig:
    """Construye la calibración desde una fila/payload, validándola.

    Las claves ausentes o `None` caen al valor por defecto: así una fila escrita
    antes de añadir un campo sigue siendo legible.
    """
    kwargs: dict[str, Any] = {}
    for nombre in EDITABLE_FIELDS:
        if nombre not in values:
            continue
        valor = values[nombre]
        if valor is None:
            continue
        if nombre == "qhs_event_weights":
            kwargs[nombre] = {str(k).strip().lower(): float(v) for k, v in dict(valor).items()}
        elif nombre == "penalizaciones":
            kwargs[nombre] = {str(k).strip(): bool(v) for k, v in dict(valor).items()}
        elif nombre in _BOOL_FIELDS:
            # `float(True)` daría 1.0 y el dataclass quedaría con un float donde
            # declara un bool: comparaciones y serialización empezarían a
            # divergir según por dónde se construyó la calibración.
            kwargs[nombre] = bool(valor)
        else:
            kwargs[nombre] = float(valor)
    config = CalificacionConfig(**kwargs)
    validate_config(config)
    return config


def config_to_mapping(config: CalificacionConfig) -> dict[str, Any]:
    """Serializa sólo los campos editables, para persistir o publicar."""
    salida: dict[str, Any] = {}
    for nombre in EDITABLE_FIELDS:
        valor = getattr(config, nombre)
        if nombre in ("qhs_event_weights", "penalizaciones"):
            salida[nombre] = dict(valor)
        else:
            salida[nombre] = valor
    return salida
