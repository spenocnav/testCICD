"""Contrato de tablas semánticas → PostgreSQL (schema `analytics`).

`TABLES` espeja las listas `DIM_CHECKS` / `FACT_CHECKS` de
`validate/validate_semantic_model.py` (mismo orden: dims primero, luego facts).
Es la fuente de verdad de la DDL del schema `analytics`.

Este módulo es **puro** (solo dataclasses); no importa `config` ni pandas, para que
pueda usarse en tests sin credenciales ni dependencias pesadas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# (columna_fk, tabla_referenciada, columna_referenciada)
Fk = tuple[str, str, str]
# (nombre lógico, columnas en orden). Los nombres se prefijan con ``ix_``.
IndexSpec = tuple[str, tuple[str, ...]]


@dataclass(frozen=True)
class TableSpec:
    parquet_name: str
    pg_table: str
    kind: str  # 'dim' | 'fact'
    pk: list[str]
    fks: list[Fk] = field(default_factory=list)
    # Índices que responden a consultas reales del portal. No inferirlos solo
    # por presencia de columnas: mensual y eventos usan claves temporales
    # diferentes y necesitan desempate estable.
    indexes: list[IndexSpec] = field(default_factory=list)
    # Overrides de tipo por columna (snake_case). Solo lo que no se infiere bien.
    type_overrides: dict[str, str] = field(default_factory=dict)


_KEY_OVERRIDES = {"date_key": "BIGINT", "fecha": "DATE"}


DIMS: list[TableSpec] = [
    TableSpec(
        "dim_vehicle.parquet",
        "dim_vehicle",
        "dim",
        ["vehicle_id"],
        indexes=[("dim_vehicle_device_id", ("device_id",))],
    ),
    TableSpec(
        "dim_date.parquet", "dim_date", "dim", ["date_key"],
        type_overrides={
            "date_key": "BIGINT",
            "date": "DATE",
            "month_start_date": "DATE",
        },
    ),
    TableSpec("dim_rule.parquet", "dim_rule", "dim", ["rule_sk"]),
    TableSpec("dim_diagnostic.parquet", "dim_diagnostic", "dim", ["diagnostic_sk"]),
    TableSpec("dim_controller.parquet", "dim_controller", "dim", ["controller_sk"]),
    TableSpec("dim_failure_mode.parquet", "dim_failure_mode", "dim", ["failure_mode_sk"]),
]


FACTS: list[TableSpec] = [
    TableSpec(
        "fact_combustible_daily.parquet", "fact_combustible_daily", "fact",
        ["fact_row_id"],
        fks=[
            ("vehicle_id", "dim_vehicle", "vehicle_id"),
            ("date_key", "dim_date", "date_key"),
        ],
        indexes=[
            (
                "fact_combustible_daily_fuel_date",
                ("fuel_kind", "date_key"),
            ),
        ],
        type_overrides=dict(_KEY_OVERRIDES),
    ),
    TableSpec(
        "fact_combustible_monthly.parquet", "fact_combustible_monthly", "fact",
        ["fact_row_id"],
        fks=[
            ("vehicle_id", "dim_vehicle", "vehicle_id"),
            ("month_start_date_key", "dim_date", "date_key"),
        ],
        indexes=[
            (
                "fact_combustible_monthly_veh_month",
                ("vehicle_id", "month_start_date_key"),
            ),
            (
                "fact_combustible_monthly_month",
                ("month_start_date_key",),
            ),
            (
                "fact_combustible_monthly_fuel_month",
                ("fuel_kind", "month_start_date_key"),
            ),
        ],
        type_overrides={"month_start_date_key": "BIGINT", "month_key": "BIGINT"},
    ),
    TableSpec(
        "fact_habito_event.parquet", "fact_habito_event", "fact",
        ["event_sk"],
        fks=[
            ("vehicle_id", "dim_vehicle", "vehicle_id"),
            ("date_key", "dim_date", "date_key"),
            ("rule_sk", "dim_rule", "rule_sk"),
        ],
        indexes=[
            (
                "fact_habito_event_scope_time",
                ("vehicle_id", "date_key", "fecha_y_hora_del_evento", "event_sk"),
            ),
        ],
        type_overrides=dict(_KEY_OVERRIDES),
    ),
    TableSpec(
        "fact_fault_event.parquet", "fact_fault_event", "fact",
        ["row_id"],
        fks=[
            ("vehicle_id", "dim_vehicle", "vehicle_id"),
            ("date_key", "dim_date", "date_key"),
            ("diagnostic_sk", "dim_diagnostic", "diagnostic_sk"),
            ("controller_sk", "dim_controller", "controller_sk"),
            ("failure_mode_sk", "dim_failure_mode", "failure_mode_sk"),
        ],
        indexes=[
            (
                "fact_fault_event_scope_time",
                ("vehicle_id", "date_key", "fecha_de_falla", "row_id"),
            ),
        ],
        type_overrides=dict(_KEY_OVERRIDES),
    ),
    TableSpec(
        # Episodios de ralentí (Análisis Ralentí). Lo escribe directamente
        # extract_ralenti.py en PostgreSQL: no pasa por silver/semantic ni por
        # load_semantic. El parquet_name es nominal para el contrato.
        "fact_ralenti_event.parquet", "fact_ralenti_event", "fact",
        ["event_sk"],
        fks=[
            ("vehicle_id", "dim_vehicle", "vehicle_id"),
            ("date_key", "dim_date", "date_key"),
        ],
        indexes=[
            (
                "fact_ralenti_event_scope_time",
                ("vehicle_id", "date_key", "inicio", "event_sk"),
            ),
        ],
        type_overrides={
            **_KEY_OVERRIDES,
            "inicio": "TIMESTAMPTZ",
            "fin": "TIMESTAMPTZ",
            "extracted_at": "TIMESTAMPTZ",
            "duracion_segundos": "DOUBLE PRECISION",
            "eventos_fuente": "BIGINT",
            "rpm_promedio": "DOUBLE PRECISION",
            "rpm_maximo": "DOUBLE PRECISION",
            "rpm_minimo": "DOUBLE PRECISION",
            "rpm_muestras": "BIGINT",
            "velocidad_maxima_kmh": "DOUBLE PRECISION",
            "latitud": "DOUBLE PRECISION",
            "longitud": "DOUBLE PRECISION",
            "hora_local": "BIGINT",
        },
    ),
    TableSpec(
        "fact_factor_carga_daily.parquet", "fact_factor_carga_daily", "fact",
        ["fact_row_id"],
        fks=[
            ("vehicle_id", "dim_vehicle", "vehicle_id"),
            ("date_key", "dim_date", "date_key"),
        ],
        type_overrides=dict(_KEY_OVERRIDES),
    ),
    TableSpec(
        "fact_def_daily.parquet", "fact_def_daily", "fact",
        ["fact_row_id"],
        fks=[
            ("vehicle_id", "dim_vehicle", "vehicle_id"),
            ("date_key", "dim_date", "date_key"),
        ],
        type_overrides=dict(_KEY_OVERRIDES),
    ),
    TableSpec(
        "fact_altimetria_reading.parquet", "fact_altimetria_reading", "fact",
        ["row_id"],
        fks=[
            ("vehicle_id", "dim_vehicle", "vehicle_id"),
            ("date_key", "dim_date", "date_key"),
        ],
        type_overrides=dict(_KEY_OVERRIDES),
    ),
    TableSpec(
        "fact_pedal_reading.parquet", "fact_pedal_reading", "fact",
        ["row_id"],
        fks=[
            ("vehicle_id", "dim_vehicle", "vehicle_id"),
            ("date_key", "dim_date", "date_key"),
        ],
        indexes=[
            (
                "fact_pedal_reading_scope_time",
                ("vehicle_id", "date_key", "fecha_y_hora", "row_id"),
            ),
        ],
        type_overrides=dict(_KEY_OVERRIDES),
    ),
    TableSpec(
        "fact_location_point.parquet", "fact_location_point", "fact",
        ["log_row_id"],
        fks=[
            ("vehicle_id", "dim_vehicle", "vehicle_id"),
            ("date_key", "dim_date", "date_key"),
        ],
        type_overrides=dict(_KEY_OVERRIDES),
    ),
]


TABLES: list[TableSpec] = DIMS + FACTS
