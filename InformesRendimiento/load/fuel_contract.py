"""Migración aditiva del contrato de combustible en ``analytics``.

El API y el ETL se despliegan de forma independiente. Por eso no basta con que
el loader agregue las columnas al cargar el siguiente parquet: durante esa
ventana el API intentaría leer columnas inexistentes y devolvería 500.

Esta migración es idempotente y conserva el histórico:

- clasifica ``dim_vehicle`` con ``tipo_combustible`` cuando la tabla maestra está
  disponible;
- aplica N15 como fallback solo cuando el tipo viene vacío o desconocido;
- marca como líquido el hecho legacy únicamente si su vehículo fue clasificado
  como líquido, porque ese histórico ya está expresado en galones;
- deja sin clasificar los hechos legacy de vehículos a gas hasta que el ETL los
  recalcule desde los contadores crudos en m³.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from fuel import classify_fuel


_DIM_COLUMNS = {
    "fuel_type_raw": "TEXT",
    "fuel_kind": "TEXT",
    "fuel_unit": "TEXT",
    "fuel_classification_source": "TEXT",
    "fuel_classification_conflict": "BOOLEAN",
}

_DAILY_COLUMNS = {
    "kms_effective": "DOUBLE PRECISION",
    "km_gal_effective": "DOUBLE PRECISION",
    "km_m3_effective": "DOUBLE PRECISION",
    "velocidad_promedio_effective": "DOUBLE PRECISION",
    "distance_source": "TEXT",
    "distance_quality_status": "TEXT",
    "distance_quality_reason": "TEXT",
    "distance_diff_km": "DOUBLE PRECISION",
    "distance_diff_pct": "DOUBLE PRECISION",
    "gps_quality_valid": "BOOLEAN",
    "gps_trip_count": "BIGINT",
    "ecm_reading_count": "BIGINT",
    "distance_quality_fingerprint": "TEXT",
    "distance_threshold_version": "TEXT",
    "comb_ralenti": "DOUBLE PRECISION",
    "fuel_kind": "TEXT",
    "fuel_unit": "TEXT",
    "gal_hr_ralenti": "DOUBLE PRECISION",
    "km_m3": "DOUBLE PRECISION",
    "m3_hr": "DOUBLE PRECISION",
    "m3_hr_ralenti": "DOUBLE PRECISION",
}

_MONTHLY_COLUMNS = {
    "kms_effective": "DOUBLE PRECISION",
    "comb_ralenti": "DOUBLE PRECISION",
    "ralenti_ecm": "DOUBLE PRECISION",
    "fuel_kind": "TEXT",
    "fuel_unit": "TEXT",
    "gal_hr_ralenti": "DOUBLE PRECISION",
    "km_m3": "DOUBLE PRECISION",
    "m3_hr": "DOUBLE PRECISION",
    "m3_hr_ralenti": "DOUBLE PRECISION",
}


def _qualified(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def _table_exists(conn: Any, schema: str, table: str) -> bool:
    return (
        conn.execute(
            text("SELECT to_regclass(:qualified)"),
            {"qualified": f'"{schema}"."{table}"'},
        ).scalar()
        is not None
    )


def _add_columns(
    conn: Any,
    schema: str,
    table: str,
    columns: dict[str, str],
) -> None:
    if not _table_exists(conn, schema, table):
        return
    for column, pg_type in columns.items():
        conn.execute(
            text(
                f"ALTER TABLE {_qualified(schema, table)} "
                f'ADD COLUMN IF NOT EXISTS "{column}" {pg_type}'
            )
        )


def _vehicle_rows(conn: Any, schema: str) -> list[tuple[str, str | None, Any]]:
    """Lee el maestro si comparte DB; si no, conserva el fallback por motor."""

    has_vehicle_master = (
        conn.execute(text("SELECT to_regclass('public.vehicles')")).scalar()
        is not None
    )
    if not has_vehicle_master:
        return [
            (vehicle_id, motor_type, None)
            for vehicle_id, motor_type in conn.execute(
                text(
                    f"SELECT vehicle_id, motor_type "
                    f"FROM {_qualified(schema, 'dim_vehicle')}"
                )
            ).all()
        ]

    return [
        (vehicle_id, motor_type, raw_fuel_type)
        for vehicle_id, motor_type, raw_fuel_type in conn.execute(
            text(
                f"SELECT dv.vehicle_id, dv.motor_type, master.tipo_combustible "
                f"FROM {_qualified(schema, 'dim_vehicle')} dv "
                "LEFT JOIN LATERAL ("
                "  SELECT v.tipo_combustible "
                "  FROM public.vehicles v "
                "  WHERE v.geotab_device_id = dv.device_id AND v.is_active IS TRUE "
                "  ORDER BY v.updated_at DESC NULLS LAST "
                "  LIMIT 1"
                ") master ON TRUE"
            )
        ).all()
    ]


def ensure_fuel_contract(engine: Engine, schema: str) -> dict[str, int]:
    """Crea columnas y recupera hechos líquidos legacy de forma idempotente."""

    migrated = {"vehicles": 0, "daily_liquid": 0, "monthly_liquid": 0}
    with engine.begin() as conn:
        if not _table_exists(conn, schema, "dim_vehicle"):
            return migrated

        _add_columns(conn, schema, "dim_vehicle", _DIM_COLUMNS)
        _add_columns(conn, schema, "fact_combustible_daily", _DAILY_COLUMNS)
        _add_columns(conn, schema, "fact_combustible_monthly", _MONTHLY_COLUMNS)

        vehicle_rows = _vehicle_rows(conn, schema)
        if vehicle_rows:
            records = []
            for vehicle_id, motor_type, raw_fuel_type in vehicle_rows:
                fuel_kind, fuel_unit, source, conflict = classify_fuel(
                    raw_fuel_type,
                    motor_type,
                )
                records.append(
                    {
                        "vehicle_id": vehicle_id,
                        "fuel_type_raw": raw_fuel_type,
                        "fuel_kind": fuel_kind,
                        "fuel_unit": fuel_unit,
                        "source": source,
                        "conflict": conflict,
                    }
                )
            result = conn.execute(
                text(
                    f"UPDATE {_qualified(schema, 'dim_vehicle')} SET "
                    "fuel_type_raw = :fuel_type_raw, "
                    "fuel_kind = :fuel_kind, "
                    "fuel_unit = :fuel_unit, "
                    "fuel_classification_source = :source, "
                    "fuel_classification_conflict = :conflict "
                    "WHERE vehicle_id = :vehicle_id"
                ),
                records,
            )
            migrated["vehicles"] = max(0, result.rowcount or 0)

        for table, counter in (
            ("fact_combustible_daily", "daily_liquid"),
            ("fact_combustible_monthly", "monthly_liquid"),
        ):
            if not _table_exists(conn, schema, table):
                continue
            result = conn.execute(
                text(
                    f"UPDATE {_qualified(schema, table)} fact SET "
                    "fuel_kind = dim.fuel_kind, fuel_unit = dim.fuel_unit "
                    f"FROM {_qualified(schema, 'dim_vehicle')} dim "
                    "WHERE fact.vehicle_id = dim.vehicle_id "
                    "AND fact.fuel_kind IS NULL "
                    "AND dim.fuel_kind = 'liquid'"
                )
            )
            migrated[counter] = max(0, result.rowcount or 0)

        if _table_exists(conn, schema, "fact_combustible_daily"):
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_fact_combustible_daily_fuel_date "
                    f"ON {_qualified(schema, 'fact_combustible_daily')} "
                    "(fuel_kind, date_key)"
                )
            )
        if _table_exists(conn, schema, "fact_combustible_monthly"):
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_fact_combustible_monthly_fuel_month "
                    f"ON {_qualified(schema, 'fact_combustible_monthly')} "
                    "(fuel_kind, month_start_date_key)"
                )
            )

    return migrated
