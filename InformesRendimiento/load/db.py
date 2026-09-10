"""Acceso a PostgreSQL para la capa de carga: engine, DDL y upsert idempotente."""

from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

import config
from load.naming import normalize_column
from load.schema import TableSpec


def get_engine() -> Engine:
    if not config.ANALYTICS_DB_URL:
        raise ValueError("ANALYTICS_DB_URL no configurada en .env")
    return create_engine(
        config.ANALYTICS_DB_URL,
        pool_pre_ping=True,
        pool_size=max(1, int(os.environ.get("ETL_DB_POOL_SIZE", "2"))),
        max_overflow=max(0, int(os.environ.get("ETL_DB_MAX_OVERFLOW", "0"))),
        pool_timeout=max(1, int(os.environ.get("ETL_DB_POOL_TIMEOUT_SECONDS", "30"))),
        # Sin esto, un error de upsert vuelca el lote completo (5.000 dicts) en el
        # mensaje de la excepción y desplaza el error real de PostgreSQL —
        # constraint y DETAIL— fuera de cualquier cola de log.
        hide_parameters=True,
        connect_args={"application_name": "informes-rendimiento-etl"},
    )


def _schema() -> str:
    return config.ANALYTICS_DB_SCHEMA


def ensure_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_schema()}"'))


def infer_pg_type(series: pd.Series, col_name: str) -> str:
    name = col_name.lower()
    # Solo claves temporales son enteras; otras *_key/*_sk (group_key, rule_sk) son texto.
    if name in ("date_key", "month_key") or name.endswith("_date_key"):
        return "BIGINT"
    if name == "fecha" or name.endswith("_date"):
        return "DATE"
    if "fecha_hora" in name or "fecha_y_hora" in name or name == "fecha_de_falla":
        return "TIMESTAMPTZ"
    dt = str(series.dtype)
    if dt.startswith("int"):
        return "BIGINT"
    if dt.startswith("float"):
        return "DOUBLE PRECISION"
    if dt == "bool":
        return "BOOLEAN"
    if dt.startswith("datetime"):
        return "TIMESTAMPTZ"
    return "TEXT"


def _normalized(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={c: normalize_column(c) for c in df.columns})


_MAX_EXACT_FLOAT_INT = 2 ** 53


def _canonical_for_hash(df_norm: pd.DataFrame) -> pd.DataFrame:
    """Normaliza el dtype de las columnas numéricas antes de hashear.

    El dtype que infiere pandas depende de QUÉ FILAS entraron al lote: la misma
    columna con los mismos valores sale `int64` si todas las filas son enteras y
    `float64` si una sola trae NaN o decimal. Al filtrar el catálogo de 250 a 16
    vehículos, `Tiempo Total en Rango de Descenso` pasó de float64 a int64 con
    valores idénticos, y eso marcó como "cambiada" toda la tabla.

    En PostgreSQL el tipo de columna lo fijó `create_table` en la primera corrida
    y no cambia, así que dos dtypes de pandas con el mismo valor se guardan igual:
    hashearlos distinto es un falso positivo puro.

    Se castea entero → float64 solo cuando es exacto (|v| < 2^53). Los IDs del
    modelo son strings, así que no los toca.
    """
    out = df_norm
    copied = False
    for column in df_norm.columns:
        series = df_norm[column]
        if not pd.api.types.is_integer_dtype(series.dtype):
            continue
        if series.notna().any():
            largest = series.abs().max()
            if pd.notna(largest) and largest >= _MAX_EXACT_FLOAT_INT:
                continue  # fuera del rango exacto de float64: se deja como está
        if not copied:
            out = df_norm.copy()
            copied = True
        out[column] = series.astype("float64")
    return out


def row_hashes(df_norm: pd.DataFrame) -> pd.Series:
    """Hash determinista por fila sobre TODAS las columnas (ya normalizadas).

    Mismo contenido ⇒ mismo hash, estable entre corridas (misma versión de pandas)
    y estable frente a la composición del lote (ver `_canonical_for_hash`).
    Se usa para la carga incremental: una fila cuyo hash no cambió ya está idéntica
    en PG, así que saltar su upsert es equivalente a re-upsertearla. Devuelve una
    Serie de strings alineada al índice de df_norm.
    """
    if df_norm.empty:
        return pd.Series([], dtype="object")
    canonical = _canonical_for_hash(df_norm)
    return pd.util.hash_pandas_object(canonical, index=False).astype("uint64").astype(str)


def create_table(engine: Engine, spec: TableSpec, df: pd.DataFrame) -> None:
    df = _normalized(df)
    cols_ddl: list[str] = []
    column_types: list[tuple[str, str]] = []
    for col in df.columns:
        pg_type = spec.type_overrides.get(col) or infer_pg_type(df[col], col)
        cols_ddl.append(f'"{col}" {pg_type}')
        column_types.append((col, pg_type))
    pk = ", ".join(f'"{normalize_column(c)}"' for c in spec.pk)
    ddl = (
        f'CREATE TABLE IF NOT EXISTS "{_schema()}".{spec.pg_table} '
        f'({", ".join(cols_ddl)}, PRIMARY KEY ({pk}))'
    )
    with engine.begin() as conn:
        conn.execute(text(ddl))
        # Migración aditiva del contrato semántico: CREATE TABLE IF NOT EXISTS
        # no evoluciona tablas ya desplegadas. Cada columna nueva del parquet se
        # incorpora antes del upsert, sin borrar ni reescribir datos existentes.
        for col, pg_type in column_types:
            conn.execute(text(
                f'ALTER TABLE "{_schema()}".{spec.pg_table} '
                f'ADD COLUMN IF NOT EXISTS "{col}" {pg_type}'
            ))


def create_indexes(engine: Engine, spec: TableSpec, df: pd.DataFrame) -> None:
    norm = {normalize_column(c) for c in df.columns}
    sch = _schema()
    stmts: list[str] = []
    if {"vehicle_id", "date_key"} <= norm:
        stmts.append(
            f'CREATE INDEX IF NOT EXISTS ix_{spec.pg_table}_veh_date '
            f'ON "{sch}".{spec.pg_table} (vehicle_id, date_key)'
        )
        stmts.append(
            f'CREATE INDEX IF NOT EXISTS ix_{spec.pg_table}_date '
            f'ON "{sch}".{spec.pg_table} (date_key)'
        )
    for logical_name, columns in spec.indexes:
        missing = set(columns) - norm
        if missing:
            raise ValueError(
                f"{spec.pg_table}: índice {logical_name} referencia columnas "
                f"ausentes: {sorted(missing)}"
            )
        column_sql = ", ".join(f'"{column}"' for column in columns)
        stmts.append(
            f'CREATE INDEX IF NOT EXISTS ix_{logical_name} '
            f'ON "{sch}".{spec.pg_table} ({column_sql})'
        )
    if stmts:
        with engine.begin() as conn:
            for s in stmts:
                conn.execute(text(s))


def add_fk_constraints(engine: Engine, spec: TableSpec) -> None:
    sch = _schema()
    for col, ref_table, ref_col in spec.fks:
        col_n = normalize_column(col)
        ref_col_n = normalize_column(ref_col)
        cname = f"fk_{spec.pg_table}_{col_n}"
        sql = text(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{cname}') THEN "
            f'ALTER TABLE "{sch}".{spec.pg_table} ADD CONSTRAINT {cname} '
            f'FOREIGN KEY ("{col_n}") REFERENCES "{sch}".{ref_table} ("{ref_col_n}") NOT VALID; '
            f"END IF; END $$;"
        )
        with engine.begin() as conn:
            conn.execute(sql)


def fetch_key_set(engine: Engine, table: str, column: str) -> set | None:
    """Claves presentes hoy en una dimensión; None si la tabla aún no existe.

    La usa el loader para no mandar a PostgreSQL filas cuya dimensión no está
    cargada: el catálogo de vehículos activos encoge, pero los parquet de facts
    son acumulativos y conservan la historia de vehículos que ya salieron.
    """
    sch = _schema()
    with engine.connect() as conn:
        if conn.execute(
            text("SELECT to_regclass(:qname)"), {"qname": f'"{sch}"."{table}"'}
        ).scalar() is None:
            return None
        rows = conn.execute(text(f'SELECT "{column}" FROM "{sch}".{table}'))
        return {row[0] for row in rows}


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → list[dict] con NaN→None y escalares numpy→nativos (psycopg)."""
    df = df.astype(object).where(pd.notnull(df), None)
    records = df.to_dict("records")
    for row in records:
        for key, val in row.items():
            if isinstance(val, np.generic):
                row[key] = val.item()
            elif isinstance(val, float) and pd.isna(val):
                row[key] = None
    return records


def upsert_dataframe_conn(
    conn: Connection,
    spec: TableSpec,
    df: pd.DataFrame,
    batch: int = 5000,
) -> int:
    """Upsert sobre una conexión/transacción administrada por el caller."""
    if df.empty:
        return 0
    df = _normalized(df)
    cols = list(df.columns)
    pk = [normalize_column(c) for c in spec.pk]
    update_cols = [c for c in cols if c not in pk]

    collist = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    pklist = ", ".join(f'"{c}"' for c in pk)
    if update_cols:
        setlist = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols)
    else:
        setlist = f'"{pk[0]}" = EXCLUDED."{pk[0]}"'

    sql = text(
        f'INSERT INTO "{_schema()}".{spec.pg_table} ({collist}) VALUES ({placeholders}) '
        f"ON CONFLICT ({pklist}) DO UPDATE SET {setlist}"
    )

    records = _records(df)
    for i in range(0, len(records), batch):
        conn.execute(sql, records[i : i + batch])
    logging.debug("upsert %s: %d filas", spec.pg_table, len(records))
    return len(records)


def insert_dataframe_ignore_conn(
    conn: Connection,
    spec: TableSpec,
    df: pd.DataFrame,
    batch: int = 5000,
) -> int:
    """Inserta filas ausentes sin modificar las que ya existen.

    La vía de FaultData usa esta variante para dimensiones sintéticas: una
    referencia recién observada necesita una fila FK, pero el placeholder
    ``(Sin información)`` nunca debe pisar un nombre/código real ya cargado por
    el ETL diario.
    """
    if df.empty:
        return 0
    df = _normalized(df)
    cols = list(df.columns)
    pk = [normalize_column(c) for c in spec.pk]
    collist = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    pklist = ", ".join(f'"{c}"' for c in pk)
    sql = text(
        f'INSERT INTO "{_schema()}".{spec.pg_table} ({collist}) VALUES ({placeholders}) '
        f"ON CONFLICT ({pklist}) DO NOTHING"
    )
    records = _records(df)
    for i in range(0, len(records), batch):
        conn.execute(sql, records[i : i + batch])
    logging.debug("insert-ignore %s: %d filas", spec.pg_table, len(records))
    return len(records)


def upsert_dataframe(engine: Engine, spec: TableSpec, df: pd.DataFrame, batch: int = 5000) -> None:
    """Wrapper legacy: conserva una transacción propia por DataFrame."""
    with engine.begin() as conn:
        upsert_dataframe_conn(conn, spec, df, batch=batch)
