"""Modelos read-only para el schema `analytics` (gestionado por InformesRendimiento).

AnalyticsBase es independiente de Base — Alembic no toca estas tablas.
NO importar en app/models/__init__.py ni en _register_models().

IMPORTANTE — el schema real (creado por el loader) tiene FOREIGN KEYs que estos
modelos NO declaran (a propósito: son read-only y no queremos que SQLAlchemy las
gestione). Al sembrar datos de prueba hay que respetarlas:
  - fact_combustible_daily.vehicle_id -> dim_vehicle.vehicle_id
  - fact_combustible_daily.date_key   -> dim_date.date_key
  - fact_habito_event.vehicle_id      -> dim_vehicle.vehicle_id
  - fact_habito_event.date_key        -> dim_date.date_key
  - fact_habito_event.rule_sk         -> dim_rule.rule_sk
  - fact_fault_event.vehicle_id       -> dim_vehicle.vehicle_id
  - fact_fault_event.date_key         -> dim_date.date_key
  - fact_fault_event.{diagnostic_sk,controller_sk,failure_mode_sk} -> dim_* (nullable)
La dimensión dim_date NO tiene modelo aquí porque el portal solo la consulta vía
el FK date_key (yyyymmdd); si se agrega, reflejar que solo date_key es NOT NULL.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AnalyticsBase(DeclarativeBase):
    pass


class DimVehicle(AnalyticsBase):
    __tablename__ = "dim_vehicle"
    __table_args__ = {"schema": "analytics"}

    vehicle_id: Mapped[str] = mapped_column(primary_key=True)
    database_name: Mapped[str | None]
    device_id: Mapped[str | None]
    vehicle_label: Mapped[str | None]
    motor_type: Mapped[str | None]
    group_key: Mapped[str | None]
    rpm_class: Mapped[str | None]
    fuel_type_raw: Mapped[str | None]
    fuel_kind: Mapped[str | None]
    fuel_unit: Mapped[str | None]
    fuel_classification_source: Mapped[str | None]
    fuel_classification_conflict: Mapped[bool | None]
    is_active: Mapped[bool | None]


class FactCombustibleDaily(AnalyticsBase):
    __tablename__ = "fact_combustible_daily"
    __table_args__ = {"schema": "analytics"}

    fact_row_id: Mapped[str] = mapped_column(primary_key=True)
    vehicle_id: Mapped[str | None] = mapped_column(index=True)
    database_name: Mapped[str | None]
    motor_type: Mapped[str | None]
    date_key: Mapped[int | None] = mapped_column(index=True)
    fecha: Mapped[date | None]
    placa: Mapped[str | None]
    # Distancia / horas / consumo (ECM y GPS)
    kms_ecm: Mapped[float | None]
    kms_gps: Mapped[float | None]
    kms_effective: Mapped[float | None]
    hrs_ecm: Mapped[float | None]
    hrs_gps: Mapped[float | None]
    comb: Mapped[float | None]
    comb_ralenti: Mapped[float | None]
    fuel_kind: Mapped[str | None]
    fuel_unit: Mapped[str | None]
    km_gal: Mapped[float | None]
    gal_hr: Mapped[float | None]
    gal_hr_ralenti: Mapped[float | None]
    km_m3: Mapped[float | None]
    m3_hr: Mapped[float | None]
    m3_hr_ralenti: Mapped[float | None]
    velocidad_promedio: Mapped[float | None]
    km_gal_effective: Mapped[float | None]
    km_m3_effective: Mapped[float | None]
    velocidad_promedio_effective: Mapped[float | None]
    distance_source: Mapped[str | None]
    distance_quality_status: Mapped[str | None]
    distance_quality_reason: Mapped[str | None]
    distance_diff_km: Mapped[float | None]
    distance_diff_pct: Mapped[float | None]
    gps_quality_valid: Mapped[bool | None]
    gps_trip_count: Mapped[int | None]
    ecm_reading_count: Mapped[int | None]
    distance_quality_fingerprint: Mapped[str | None]
    distance_threshold_version: Mapped[str | None]
    km_gal_gps: Mapped[float | None]
    gal_hr_gps: Mapped[float | None]
    velocidad_promedio_gps: Mapped[float | None]
    # Distribución por rango RPM
    pct_rango_bajo: Mapped[float | None]
    pct_rango_economico: Mapped[float | None]
    pct_rango_balanceado: Mapped[float | None]
    pct_rango_potencia: Mapped[float | None]
    pct_exceso_rpm: Mapped[float | None]
    pct_rango_potencia_ineficiente: Mapped[float | None]
    # Ralentí
    pct_ralenti_gps: Mapped[float | None]
    pct_ralenti_ecm: Mapped[float | None]
    pct_ralenti: Mapped[float | None]
    # Denominador efectivo de `pct_ralenti` y fuente de la que salió
    # ('ecm' | 'banda' | 'gps'). El ETL elige la fuente disponible y usa su
    # propio total de horas como divisor, así que este es el peso correcto para
    # reagregar el porcentaje; antes se asumía max(hrs_gps, hrs_ecm).
    horas_ralenti_base: Mapped[float | None]
    fuente_ralenti: Mapped[str | None]
    tiempo_total_en_rango: Mapped[float | None]
    ralenti: Mapped[float | None]
    ralenti_ecm: Mapped[float | None]
    tiempo_en_ralenti: Mapped[float | None]
    # Rangos en descenso
    pct_rango_bajo_descenso: Mapped[float | None]
    pct_rango_economico_descenso: Mapped[float | None]
    pct_rango_balanceado_descenso: Mapped[float | None]
    pct_rango_potencia_descenso: Mapped[float | None]
    pct_exceso_rpm_descenso: Mapped[float | None]
    pct_rango_potencia_ineficiente_descenso: Mapped[float | None]
    tiempo_total_en_rango_de_descenso: Mapped[float | None]
    # Rangos sin descenso
    pct_rango_bajo_sin_descenso: Mapped[float | None]
    pct_rango_economico_sin_descenso: Mapped[float | None]
    pct_rango_balanceado_sin_descenso: Mapped[float | None]
    pct_rango_potencia_sin_descenso: Mapped[float | None]
    pct_exceso_rpm_sin_descenso: Mapped[float | None]
    pct_rango_potencia_ineficiente_sin_descenso: Mapped[float | None]
    tiempo_total_en_rango_sin_descenso: Mapped[float | None]
    revision: Mapped[bool | None]


class FactCombustibleMonthly(AnalyticsBase):
    __tablename__ = "fact_combustible_monthly"
    __table_args__ = {"schema": "analytics"}

    fact_row_id: Mapped[str] = mapped_column(primary_key=True)
    vehicle_id: Mapped[str | None] = mapped_column(index=True)
    database_name: Mapped[str | None]
    motor_type: Mapped[str | None]
    placa: Mapped[str | None]
    month_key: Mapped[int | None] = mapped_column(index=True)
    month_start_date_key: Mapped[int | None] = mapped_column(index=True)
    anio_mes: Mapped[str | None]
    kms_ecm: Mapped[float | None]
    kms_effective: Mapped[float | None]
    hrs_ecm: Mapped[float | None]
    comb: Mapped[float | None]
    comb_ralenti: Mapped[float | None]
    ralenti_ecm: Mapped[float | None]
    fuel_kind: Mapped[str | None]
    fuel_unit: Mapped[str | None]
    km_gal: Mapped[float | None]
    gal_hr: Mapped[float | None]
    gal_hr_ralenti: Mapped[float | None]
    km_m3: Mapped[float | None]
    m3_hr: Mapped[float | None]
    m3_hr_ralenti: Mapped[float | None]
    velocidad_promedio: Mapped[float | None]


class FactFactorCargaDaily(AnalyticsBase):
    __tablename__ = "fact_factor_carga_daily"
    __table_args__ = {"schema": "analytics"}

    fact_row_id: Mapped[str] = mapped_column(primary_key=True)
    vehicle_id: Mapped[str | None] = mapped_column(index=True)
    database_name: Mapped[str | None]
    date_key: Mapped[int | None] = mapped_column(index=True)
    fecha: Mapped[date | None]
    placa: Mapped[str | None]
    factor_de_carga: Mapped[float | None]  # % de carga del motor


class FactPedalReading(AnalyticsBase):
    __tablename__ = "fact_pedal_reading"
    __table_args__ = {"schema": "analytics"}

    row_id: Mapped[str] = mapped_column(primary_key=True)
    vehicle_id: Mapped[str | None] = mapped_column(index=True)
    database_name: Mapped[str | None]
    date_key: Mapped[int | None] = mapped_column(index=True)
    fecha: Mapped[date | None]
    placa: Mapped[str | None]
    fecha_y_hora: Mapped[datetime | None]
    valor: Mapped[float | None]  # % de presión sobre el pedal del acelerador
    unidad_de_medida: Mapped[str | None]


class DimRule(AnalyticsBase):
    __tablename__ = "dim_rule"
    __table_args__ = {"schema": "analytics"}

    rule_sk: Mapped[str] = mapped_column(primary_key=True)
    rule_id: Mapped[str | None]
    rule_name: Mapped[str | None]
    source_script: Mapped[str | None]
    scope_type: Mapped[str | None]
    scope_value: Mapped[str | None]
    categoria: Mapped[str | None]


class FactHabitoEvent(AnalyticsBase):
    __tablename__ = "fact_habito_event"
    __table_args__ = {"schema": "analytics"}

    event_sk: Mapped[str] = mapped_column(primary_key=True)
    event_id: Mapped[str | None]
    vehicle_id: Mapped[str | None] = mapped_column(index=True)
    database_name: Mapped[str | None]
    date_key: Mapped[int | None] = mapped_column(index=True)
    rule_sk: Mapped[str | None] = mapped_column(index=True)
    rule_id: Mapped[str | None]
    event_type: Mapped[str | None]
    fecha: Mapped[date | None]
    placa: Mapped[str | None]
    longitud: Mapped[float | None]
    latitud: Mapped[float | None]
    fecha_y_hora_del_evento: Mapped[datetime | None]
    distancia_evento_mt: Mapped[float | None]
    evento_resumen: Mapped[str | None]
    duracion_evento: Mapped[float | None]
    observacion_corta: Mapped[str | None]
    # Métricas numéricas del evento, añadidas por el ETL (2026-08-27). Hasta
    # ahora los números vivían embebidos en el texto de `observacion_corta`
    # (`1900.25 RPM, 1.8 km/h, Carga: 36.52%`) y había que sacarlos con un
    # regex en SQL, a 7-17 s por consulta sobre 30 días.
    #
    # El loader del ETL las materializa con `ALTER TABLE ... ADD COLUMN IF NOT
    # EXISTS`: NO hay migración Alembic para estas columnas (AnalyticsBase no
    # lo gobierna Alembic). Mientras el ETL no reprocese el histórico llegan
    # NULL, así que todo consumidor debe tolerarlo (ver
    # `_rpm_value_expression`, que hace COALESCE contra el regex viejo).
    rpm: Mapped[float | None]  # RPM pico del evento
    velocidad_kmh: Mapped[float | None]  # velocidad del evento
    carga_pct: Mapped[float | None]  # factor de carga %
    g_force: Mapped[float | None]  # fuerza G del eje que define el evento, con signo
    g_axis: Mapped[str | None]  # longitudinal | lateral | vertical
    event_value: Mapped[float | None]  # la métrica que define ese tipo de evento
    event_value_unit: Mapped[str | None]  # RPM | km/h | G


class FactFaultEvent(AnalyticsBase):
    __tablename__ = "fact_fault_event"
    __table_args__ = {"schema": "analytics"}

    row_id: Mapped[str] = mapped_column(primary_key=True)
    vehicle_id: Mapped[str | None] = mapped_column(index=True)
    database_name: Mapped[str | None]
    date_key: Mapped[int | None] = mapped_column(index=True)
    diagnostic_sk: Mapped[str | None]
    controller_sk: Mapped[str | None]
    failure_mode_sk: Mapped[str | None]
    fecha: Mapped[date | None]
    movil: Mapped[str | None]
    fecha_de_falla: Mapped[datetime | None]
    codigo_diagnostico: Mapped[int | None]
    codigo_modo_de_falla: Mapped[float | None]
    nombre_fuente_diagnostico: Mapped[str | None]
    codigo_controlador: Mapped[int | None]
    estado_de_falla: Mapped[str | None]
    recuento_de_fallos: Mapped[int | None]
    tipo_de_atencion: Mapped[str | None]
    luz_de_parada_amber: Mapped[bool | None]
    luz_de_parada_roja: Mapped[bool | None]
    lampara_de_averia: Mapped[bool | None]
    lampara_de_advertencia: Mapped[bool | None]
    nombre_de_controlador: Mapped[str | None]
    diagnostico: Mapped[str | None]
    modo_de_falla: Mapped[str | None]


class FactRalentiEvent(AnalyticsBase):
    """Un EPISODIO de ralentí por fila (módulo "Análisis Ralentí").

    El ETL fusiona los eventos de excepción de ralentí consecutivos de un
    vehículo cuando el hueco entre ellos es corto; `eventos_fuente` dice
    cuántos eventos crudos forman el episodio. El DDL lo posee el ETL
    (InformesRendimiento): NO hay migración Alembic para esta tabla y el
    portal sólo la lee. `vehicle_id` comparte espacio de claves con
    `dim_vehicle.vehicle_id` (placa = `vehicle_label`, motor = `motor_type`).
    Los cortes de duración y RPM viven en `ralenti_service`, no aquí.
    """

    __tablename__ = "fact_ralenti_event"
    __table_args__ = {"schema": "analytics"}

    event_sk: Mapped[str] = mapped_column(primary_key=True)
    vehicle_id: Mapped[str | None] = mapped_column(index=True)
    database_name: Mapped[str | None]
    device_id: Mapped[str | None]
    rule_id: Mapped[str | None]
    rule_source: Mapped[str | None]  # 'banda' | 'geotab_idling'
    inicio: Mapped[datetime | None]
    fin: Mapped[datetime | None]
    duracion_segundos: Mapped[float | None]
    eventos_fuente: Mapped[int | None]
    rpm_promedio: Mapped[float | None]
    rpm_maximo: Mapped[float | None]
    rpm_minimo: Mapped[float | None]
    rpm_muestras: Mapped[int | None]
    velocidad_maxima_kmh: Mapped[float | None]
    latitud: Mapped[float | None]
    longitud: Mapped[float | None]
    date_key: Mapped[int | None] = mapped_column(index=True)  # yyyymmdd (Bogotá)
    fecha: Mapped[date | None]  # día local America/Bogota
    hora_local: Mapped[int | None]  # 0-23
    extracted_at: Mapped[datetime | None]
