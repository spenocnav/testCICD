from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.calificacion_config import CalificacionConfigValues


class VehicleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: str
    vehicle_label: str | None
    database_name: str | None
    motor_type: str | None
    group_key: str | None
    rpm_class: str | None
    fuel_type_raw: str | None = None
    fuel_kind: str = "liquid"
    fuel_unit: str = "gal"
    fuel_classification_source: str | None = None
    fuel_classification_conflict: bool = False
    is_active: bool | None
    vocacional: bool = False
    # Grupo interno del cliente (fleet_vehicle_groups.id, réplica de Navi
    # Vehículos). El árbol completo se sirve en GET /vehicles/groups; con este
    # id el cliente filtra por grupo reutilizando el filtro vehicle_id.
    vehicle_group_id: uuid.UUID | None = None


class CombustibleDailyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fact_row_id: str
    vehicle_id: str | None
    database_name: str | None
    motor_type: str | None
    date_key: int | None
    fecha: date | None
    placa: str | None
    # Distancia operativa ya resuelta. El contrato de cliente no expone las
    # fuentes crudas ni la trazabilidad de calidad.
    kms_ecm: float | None
    hrs_ecm: float | None
    hrs_gps: float | None
    comb: float | None
    comb_ralenti: float | None
    fuel_kind: str | None
    fuel_unit: str | None
    km_gal: float | None
    gal_hr: float | None
    gal_hr_ralenti: float | None
    km_m3: float | None
    m3_hr: float | None
    m3_hr_ralenti: float | None
    velocidad_promedio: float | None
    # Distribución por rango RPM
    pct_rango_bajo: float | None
    pct_rango_economico: float | None
    pct_rango_balanceado: float | None
    pct_rango_potencia: float | None
    pct_exceso_rpm: float | None
    pct_rango_potencia_ineficiente: float | None
    # Ralentí
    pct_ralenti: float | None
    ralenti: float | None


class PaginatedCombustible(BaseModel):
    items: list[CombustibleDailyRead]
    total: int
    limit: int
    offset: int


class CombustibleSummary(BaseModel):
    """Totales y promedios sobre todo el rango filtrado."""

    fuel_kind: str
    fuel_unit: str
    kms_ecm: float
    hrs_ecm: float
    hrs_gps: float
    comb: float
    comb_ralenti: float | None
    km_gal: float | None
    gal_hr: float | None
    gal_hr_ralenti: float | None
    km_m3: float | None
    m3_hr: float | None
    m3_hr_ralenti: float | None
    velocidad_promedio: float | None
    pct_ralenti: float | None
    pct_exceso_rpm: float | None
    n_vehiculos: int
    n_registros: int
    all_vocacional: bool = False


class CombustibleTimePoint(BaseModel):
    """Un punto de la serie temporal (diaria o mensual)."""

    fuel_kind: str
    fuel_unit: str
    periodo: int
    label: str
    kms_ecm: float | None
    kms_gps: float | None
    hrs_ecm: float | None
    hrs_gps: float | None
    comb: float | None
    comb_ralenti: float | None
    km_gal: float | None
    gal_hr: float | None
    gal_hr_ralenti: float | None
    km_m3: float | None
    m3_hr: float | None
    m3_hr_ralenti: float | None
    velocidad_promedio: float | None
    pct_ralenti: float | None


class VehicleRankingItem(BaseModel):
    vehicle_id: str | None
    vehicle_label: str | None
    placa: str | None
    value: float | None


# --- Hábitos operativos (rangos de RPM, ralentí, pedal) ---


class OperativoMonthlyPoint(BaseModel):
    periodo: int
    label: str
    # Total (incluye descenso) — compatibilidad.
    pct_rango_bajo: float | None
    pct_rango_economico: float | None
    pct_rango_balanceado: float | None
    pct_rango_potencia: float | None
    pct_rango_potencia_ineficiente: float | None
    pct_exceso_rpm: float | None
    pct_ralenti: float | None
    # Conteo de eventos RPM, no porcentaje de tiempo.
    eventos_rpm: int = 0
    # Conteos derivados de la carga del evento: descenso es carga < 5%.
    eventos_rpm_descenso: int = 0
    eventos_rpm_sin_descenso: int = 0
    # Sin descenso (gráfico principal de distribución de bandas).
    pct_rango_bajo_sin_descenso: float | None = None
    pct_rango_economico_sin_descenso: float | None = None
    pct_rango_balanceado_sin_descenso: float | None = None
    pct_rango_potencia_sin_descenso: float | None = None
    pct_rango_potencia_ineficiente_sin_descenso: float | None = None
    pct_exceso_rpm_sin_descenso: float | None = None
    # En descenso (gráfico aparte).
    pct_rango_bajo_descenso: float | None = None
    pct_rango_economico_descenso: float | None = None
    pct_rango_balanceado_descenso: float | None = None
    pct_rango_potencia_descenso: float | None = None
    pct_rango_potencia_ineficiente_descenso: float | None = None
    pct_exceso_rpm_descenso: float | None = None


class PedalSummary(BaseModel):
    promedio: float | None
    n_lecturas: int
    ultima_lectura: datetime | None
    unidad: str


class FactorCargaMonthlyPoint(BaseModel):
    periodo: int
    label: str
    factor_de_carga: float | None


class FactorCargaSummary(BaseModel):
    promedio: float | None
    n_lecturas: int
    monthly: list[FactorCargaMonthlyPoint]


# --- Hábitos seguros ---


class HabitoSummary(BaseModel):
    n_eventos: int
    n_vehiculos: int
    n_tipos: int
    duracion_promedio: float | None
    distancia_total_mt: float


class HabitoTypeBucket(BaseModel):
    event_type: str
    n_eventos: int
    duracion_promedio: float | None
    distancia_total_mt: float


class HabitoTimePoint(BaseModel):
    periodo: int
    label: str
    n_eventos: int


class HabitoEventRead(BaseModel):
    event_sk: str
    vehicle_id: str | None
    placa: str | None
    fecha: date | None
    fecha_y_hora_del_evento: datetime | None
    event_type: str | None
    rule_name: str | None
    categoria: str | None
    duracion_evento: float | None
    distancia_evento_mt: float | None
    observacion_corta: str | None
    latitud: float | None
    longitud: float | None
    # Contexto de motor y RPM resuelto en SQL (no re-parsear la observación en
    # el cliente): `rpm_value` sale de la observación del evento y los dos
    # límites del `motor_catalog` del motor del vehículo. NULL = dato no
    # capturado en Navi Vehículos.
    motor_type: str | None = None
    rpm_value: float | None = None
    rpm_governed_limit: int | None = None
    rpm_overspeed_limit: int | None = None
    # Métricas numéricas del evento publicadas por el ETL desde 2026-08-27.
    # Antes vivían embebidas en el texto de `observacion_corta`
    # (`15.0 km/h, Aceleración vertical: 1.99 G Force`) y la tabla no podía
    # ordenar por severidad ni mostrar máximos. Llegan NULL mientras el ETL no
    # reprocese el histórico, así que el cliente debe tolerarlo.
    #
    # `event_value` es la métrica que define ESE tipo de evento, y
    # `event_value_unit` la unidad en que está (`RPM` | `km/h` | `G`): las dos
    # se leen juntas o el número no significa nada, porque un 1.99 en G y un
    # 1.99 en RPM no son comparables.
    velocidad_kmh: float | None = None
    carga_pct: float | None = None
    g_force: float | None = None  # eje que define el evento, con signo
    g_axis: str | None = None  # longitudinal | lateral | vertical
    event_value: float | None = None
    event_value_unit: str | None = None


class PaginatedHabitos(BaseModel):
    items: list[HabitoEventRead]
    total: int
    limit: int
    offset: int


class HabitoMapPoint(BaseModel):
    """Punto georreferenciado para el mapa de eventos."""

    event_sk: str
    placa: str | None
    event_type: str | None
    fecha_y_hora_del_evento: datetime | None
    duracion_evento: float | None
    observacion_corta: str | None
    latitud: float
    longitud: float


# --- Calificación (score derivado) ---


class CalificacionEstado(BaseModel):
    """Conteo de vehículos por estado: rojo, amarillo, verde. Verde es cumplir."""

    no_cumple: int
    en_riesgo: int
    cumple: int


class CalificacionEvolucionPoint(BaseModel):
    periodo: int
    label: str
    qhs: float | None
    qho: float | None
    qgen: float | None


class CalificacionOperativoPoint(BaseModel):
    periodo: int
    label: str
    pct_eficiente: float | None
    pct_ralenti: float | None
    eventos_rpm: int
    eventos_rpm_sobre_gobernada: int


class CalificacionComponenteDetalle(BaseModel):
    """Un sumando del QHO de UN vehículo, con lo medido, la meta y lo perdido.

    Es el desglose que responde "por qué falla este vehículo": cada fila trae el
    valor observado ya formateado, el objetivo que debía cumplir y cuántos
    puntos del QHO se dejó en el camino.
    """

    nombre: str
    valor_texto: str  # lo medido, formateado es-CO
    objetivo_texto: str  # la meta, derivada de las constantes del cálculo
    puntos: float | None  # 0..100 del componente; None si no es evaluable
    peso: float  # fracción dentro del QHO
    aporte: float | None  # puntos * peso: lo que pone en el QHO
    puntos_perdidos: float | None  # peso * (100 - puntos)


class CalificacionEventoDetalle(BaseModel):
    """Conteo de eventos de seguridad de un tipo y lo que pesan en el QHS."""

    event_type: str
    n_eventos: int
    peso: float
    aporte_ponderado: float  # n_eventos * peso


class CalificacionVehiculoDetalle(BaseModel):
    """Desglose completo del puntaje de un vehículo, para el clic en su fila.

    Se sirve inline con la tabla a propósito: todo sale de los mismos datos que
    la calificación ya tiene en memoria, así que no cuesta consultas extra y el
    clic no vuelve a pagar los ~16 s de la agregación.
    """

    componentes_qho: list[CalificacionComponenteDetalle]
    eventos_qhs: list[CalificacionEventoDetalle]
    km: float | None
    horas_ecm: float | None
    exposicion_unidades: float | None  # bloques de 1000 km o 100 h
    exposicion_base: str  # "1000 km" | "100 horas ECM"
    # Mínimo del periodo en la unidad del vehículo (km o horas ECM); None si la
    # regla no aplica —la flota la desactivó o el periodo no tiene extremos—.
    exposicion_minima: float | None = None
    exposicion_suficiente: bool = True
    dias_periodo: int | None = None
    eventos_qhs_por_1000km: float | None
    eventos_qhs_tope: float
    motivos: list[str]  # frases legibles, ordenadas por puntos perdidos


class CalificacionVehiculo(BaseModel):
    vehicle_id: str
    placa: str | None
    vocacional: bool
    eventos_rpm: int
    eventos_rpm_sobre_gobernada: int
    eventos_rpm_sobre_sobrevelocidad: int
    penalizado_por_sobrevelocidad: bool
    # False: operó por debajo de la exposición mínima del periodo, así que su
    # puntaje sería una tasa sin denominador. `qgen` va en None y `qgen_base`
    # conserva lo que habría puntuado. No es lo mismo que penalizado: aquí no
    # hay falta, hay falta de datos.
    exposicion_suficiente: bool = True
    # Fracción del promedio de la flota que aporta este vehículo, 0..1. 0 en los
    # que no entran al promedio. Es la explicación auditable del gauge: un
    # vehículo puede tener 93,5 puntos y pesar el 0,003 %.
    peso_en_promedio: float | None = None
    qhs: float | None
    qho: float | None
    qgen: float | None  # efectivo: 0 si hay penalización
    qgen_base: float | None  # el puntaje antes del override
    estado: str | None
    detalle: CalificacionVehiculoDetalle | None


class CalificacionUmbrales(BaseModel):
    """Cortes de estado, publicados para que el gauge no los duplique.

    El front tenía sus propias constantes y quedaron desfasadas al recalibrar:
    pintaba un 78.3 como "No cumple (<90)" mientras la tabla, con el estado que
    calcula el backend, decía "Cumple".
    """

    en_riesgo: float  # QGen ≥ este valor deja el rojo y pasa a amarillo
    cumple: float  # QGen ≥ este valor es verde: cumple


class CalificacionComponente(BaseModel):
    """Un sumando del QHO, con su peso y la escala que lo normaliza.

    Describe la fórmula, no a un vehículo: el desglose con valores medidos de un
    vehículo concreto es `CalificacionComponenteDetalle`.
    """

    nombre: str
    peso: float  # fracción dentro del QHO
    detalle: str  # escala en texto, derivada de las constantes


class CalificacionEventoPeso(BaseModel):
    evento: str
    peso: float


class CalificacionMetodologia(BaseModel):
    """Cómo se calcula la nota. Se publica para que la explicación que ve el
    usuario salga de las mismas constantes que hacen el cálculo, y no de una
    copia escrita a mano que se desactualice al recalibrar."""

    peso_qho: float
    peso_qhs: float
    componentes_qho: list[CalificacionComponente]
    eventos_qhs: list[CalificacionEventoPeso]
    eventos_excluidos: list[str]
    eventos_tope_por_1000km: float
    peso_evento_no_listado: float
    penalizacion_sobrevelocidad: str
    agregacion: str
    exposicion_minima: str


class CalificacionConfiguracion(CalificacionConfigValues):
    """La calibración con la que se calculó la respuesta, más su procedencia.

    Hereda los parámetros de `CalificacionConfigValues` en vez de repetirlos:
    cuando se añade una perilla —o una penalización al registro— aparece acá
    sola. Y viajan planos, al lado de `origen`, porque son un solo hecho — "con
    qué fórmula se calculó esto", incluido qué penalizaciones estaban activas.
    """

    # "defecto" | "flota" | "mixto".
    origen: str
    fleet_id: str | None = None
    actualizado_en: datetime | None = None
    actualizado_por: str | None = None


class CalificacionResponse(BaseModel):
    promedio_general: float | None
    estado: CalificacionEstado
    umbrales: CalificacionUmbrales
    metodologia: CalificacionMetodologia
    # Calibración con la que se calculó ESTA respuesta, con su procedencia.
    # Viaja junto al puntaje y no en una petición aparte porque son el mismo
    # hecho: un `configuracion` de otra petición podría describir una fórmula
    # distinta de la que produjo estos números.
    #
    # `origen == "mixto"` es el caso que obliga a que el campo exista: el
    # alcance trae varias flotas con calibraciones distintas, el puntaje se
    # calculó con los valores por defecto y no corresponde a ninguna de ellas.
    # Sin este campo la pantalla mostraría umbrales que no son de nadie sin
    # poder avisarlo ni pedir que se elija una flota.
    #
    # Opcional en el contrato para que una respuesta servida por una versión
    # anterior siga validando; el servicio siempre lo puebla.
    configuracion: CalificacionConfiguracion | None = None
    evolucion: list[CalificacionEvolucionPoint]
    operativos: list[CalificacionOperativoPoint]
    vehiculos: list[CalificacionVehiculo]


# --- Fallas / alertas técnicas ---


class FaultSummary(BaseModel):
    n_fallas: int
    n_eventos: int = 0
    n_vehiculos: int
    n_diagnosticos: int
    n_urgentes: int


class FaultSeverityBucket(BaseModel):
    severity: str
    n_fallas: int


class FaultParetoItem(BaseModel):
    label: str
    n_fallas: int


class FaultTimePoint(BaseModel):
    periodo: int
    label: str
    n_fallas: int


class FaultEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_id: str
    vehicle_id: str | None
    movil: str | None
    fecha: date | None
    fecha_de_falla: datetime | None
    primera_fecha_de_falla: datetime | None = None
    codigo_diagnostico: int | None
    codigo_modo_de_falla: float | None = None
    codigo_controlador: int | None = None
    nombre_fuente_diagnostico: str | None = None
    diagnostico: str | None
    nombre_de_controlador: str | None
    modo_de_falla: str | None
    estado_de_falla: str | None
    recuento_de_fallos: int | None
    tipo_de_atencion: str | None
    luz_de_parada_roja: bool | None
    luz_de_parada_amber: bool | None
    lampara_de_averia: bool | None


class FaultTimelineItem(BaseModel):
    row_id: str
    fecha_de_falla: datetime | None
    estado_de_falla: str | None = None
    recuento_de_fallos: int | None = None
    tipo_de_atencion: str | None = None
    luz_de_parada_roja: bool | None = None
    luz_de_parada_amber: bool | None = None
    lampara_de_averia: bool | None = None


class PaginatedFallas(BaseModel):
    items: list[FaultEventRead]
    total: int
    limit: int
    offset: int


class PaginatedTimeline(BaseModel):
    items: list[FaultTimelineItem]
    total: int
    limit: int
    offset: int


class MotorTypeBucket(BaseModel):
    """Tipo de motor con conteo de vehículos en la flota activa."""

    motor_type: str
    n_vehiculos: int
    # Límites de placa del motor (public.motor_catalog). NULL = aún no
    # capturados; la UI debe avisar que ese motor no se puede filtrar por
    # umbral propio.
    governed_speed_rpm: int | None = None
    max_overspeed_rpm: int | None = None


class LocationPointRead(BaseModel):
    """Punto del rastro de un vehículo con su dirección (informe de Ubicaciones).

    Viene directo de MyGeotab en la misma petición: no hay fila equivalente en
    la base. `direccion` es null si el proveedor no reconoce el punto.
    """

    fecha_y_hora: datetime
    placa: str | None
    latitude: float
    longitude: float
    speed: float | None
    direccion: str | None = None


class UbicacionesPage(BaseModel):
    """Un día de rastro. El cliente encadena días con `next_cursor`.

    `next_cursor` es null cuando el día servido cierra el rango solicitado.
    """

    items: list[LocationPointRead]
    day: date
    sample_minutes: int
    next_cursor: date | None = None


# --- Agregación por grupo interno de vehículos (fleet_vehicle_groups) ---
#
# Una fila por grupo HOJA (`vehicles.vehicle_group_id` exacto; NULL = "sin
# grupo"). Todos los campos numéricos son ADITIVOS: el frontend hace el rollup
# por niveles del árbol y calcula las razones (km/gal, ev/1000km) después de
# sumar, nunca antes.


class GroupCombustibleBucket(BaseModel):
    """Totales de combustible de un grupo hoja en el rango filtrado."""

    group_id: uuid.UUID | None
    # Vehículos distintos con registros en el rango.
    n_vehiculos: int
    n_registros: int
    # Suma de la distancia efectiva (la misma columna resuelta del summary).
    kms: float
    comb: float
    # La misma base horaria que usa gal_hr/m3_hr en el summary.
    hrs: float
    comb_ralenti: float


class GroupHabitosBucket(BaseModel):
    """Eventos de hábitos seguros de un grupo hoja en el rango filtrado."""

    group_id: uuid.UUID | None
    n_vehiculos: int
    n_eventos: int
    # km de combustible del MISMO rango y alcance (todas las unidades), para
    # que el cliente normalice ev/1000km tras el rollup.
    kms: float


class GroupFallasBucket(BaseModel):
    """Fallas de un grupo hoja en el rango filtrado."""

    group_id: uuid.UUID | None
    n_vehiculos: int
    # Fallas únicas: la misma llave distinct del summary de fallas.
    n_fallas: int
    n_eventos: int
    # Mismas condiciones de "urgente" que el summary (luz de parada roja).
    n_urgentes: int
