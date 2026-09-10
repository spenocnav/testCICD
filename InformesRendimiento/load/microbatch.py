"""Persistencia ACID de micro-lotes analíticos.

Un micro-lote confirma, en una única transacción PostgreSQL:

1. dimensiones requeridas;
2. hechos del vehículo/día;
3. evidencia idempotente en ``etl_microbatch_commit``;
4. avance del watermark con control optimista.

No abre conexiones desde hilos extractores y no toca Parquet.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

import config
from load import db
from load.schema import TableSpec
from utils import COLOMBIA_TZ

PIPELINE_VERSION = "factor-carga-stream-v1"
_BATCH_NAMESPACE = uuid.UUID("01826f4f-2b36-7ac4-a4a4-63f41c3ad55f")


class StaleMicroBatchError(RuntimeError):
    """El estado cambió después de planear el micro-lote."""


def _table_spec(pg_table: str) -> TableSpec:
    from load.schema import TABLES

    return next(spec for spec in TABLES if spec.pg_table == pg_table)


DIM_VEHICLE_SPEC = _table_spec("dim_vehicle")
DIM_DATE_SPEC = _table_spec("dim_date")
FACTOR_SPEC = _table_spec("fact_factor_carga_daily")


@dataclass
class MicroBatch:
    dataset: str
    portal_vehicle_id: str
    analytics_vehicle_id: str
    expected_version: int
    expected_cursor: datetime
    window_start: datetime
    window_end: datetime
    vehicle_dim: pd.DataFrame
    date_dim: pd.DataFrame
    facts: pd.DataFrame
    raw_row_count: int
    pipeline_version: str = PIPELINE_VERSION
    comparison: dict[str, Any] | None = None
    output_sha256: str = field(init=False)
    batch_id: uuid.UUID = field(init=False)

    def __post_init__(self) -> None:
        self.output_sha256 = dataframe_sha256(self.facts)
        identity = "|".join(
            [
                self.dataset,
                self.portal_vehicle_id,
                self.analytics_vehicle_id,
                self.window_start.isoformat(),
                self.window_end.isoformat(),
                self.pipeline_version,
                self.output_sha256,
            ]
        )
        self.batch_id = uuid.uuid5(_BATCH_NAMESPACE, identity)
        validate_microbatch(self)


@dataclass(frozen=True)
class CommitResult:
    batch_id: uuid.UUID
    output_sha256: str
    fact_rows: int
    previous_ledger_entry: bool


def _canonical_value(value: Any) -> str:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return "null"
    if isinstance(value, bool):
        return "bool:1" if value else "bool:0"
    if isinstance(value, float):
        return f"float64:{struct.pack('!d', value).hex()}"
    if isinstance(value, (datetime, date)):
        return f"date:{value.isoformat()}"
    if hasattr(value, "item"):
        return _canonical_value(value.item())
    return f"{type(value).__name__}:{value}"


def dataframe_sha256(frame: pd.DataFrame) -> str:
    """Huella estable en la misma representación física, incluidos floats."""
    normalized = db._normalized(frame)
    columns = sorted(normalized.columns)
    if columns:
        normalized = normalized[columns]
    pk = "fact_row_id" if "fact_row_id" in normalized.columns else None
    if pk:
        normalized = normalized.sort_values(pk).reset_index(drop=True)
    digest = hashlib.sha256()
    digest.update(("\x1f".join(columns) + "\n").encode())
    for row in normalized.itertuples(index=False, name=None):
        digest.update(("\x1f".join(_canonical_value(v) for v in row) + "\n").encode())
    return digest.hexdigest()


def build_date_dimension(day: date) -> pd.DataFrame:
    month_start = day.replace(day=1)
    return pd.DataFrame(
        [
            {
                "date_key": int(day.strftime("%Y%m%d")),
                "date": day,
                "year": day.year,
                "month": day.month,
                "day": day.day,
                "quarter": ((day.month - 1) // 3) + 1,
                "month_key": day.year * 100 + day.month,
                "month_start_date": month_start,
                "week_of_year": day.isocalendar().week,
                "day_of_week": day.isoweekday(),
                "is_weekend": day.isoweekday() >= 6,
            }
        ]
    )


def _database_identity(url: str) -> tuple[str | None, int, str | None, str | None]:
    parsed = make_url(url)
    return parsed.host, parsed.port or 5432, parsed.database, parsed.username


def ensure_single_transaction_database() -> None:
    """Falla cerrado cuando maestros y analytics no comparten DB física."""
    master_url = os.environ.get("MASTER_DB_URL")
    analytics_url = config.ANALYTICS_DB_URL
    if not master_url or not analytics_url:
        raise RuntimeError("modo directo requiere MASTER_DB_URL y ANALYTICS_DB_URL")
    if _database_identity(master_url) != _database_identity(analytics_url):
        raise RuntimeError(
            "modo directo requiere que MASTER_DB_URL y ANALYTICS_DB_URL "
            "apunten a la misma base PostgreSQL"
        )


def validate_microbatch(batch: MicroBatch) -> None:
    if batch.dataset != "factor_carga":
        raise ValueError(f"dataset directo no permitido: {batch.dataset}")
    if batch.window_start.tzinfo is None or batch.window_end.tzinfo is None:
        raise ValueError("la ventana del micro-lote debe incluir zona horaria")
    if batch.window_end - batch.window_start != timedelta(days=1):
        raise ValueError("el micro-lote debe cubrir exactamente un día")
    local_start = batch.window_start.astimezone(COLOMBIA_TZ)
    local_end = batch.window_end.astimezone(COLOMBIA_TZ)
    if local_start.time() != time.min or local_end.time() != time.min:
        raise ValueError("la ventana debe coincidir con medianoches de Colombia")
    if local_end.date() - local_start.date() != timedelta(days=1):
        raise ValueError("la ventana no representa un día Colombia")
    if batch.expected_cursor.tzinfo is None:
        raise ValueError("el cursor esperado debe incluir zona horaria")
    if len(batch.output_sha256) != 64:
        raise ValueError("huella de salida inválida")

    if not batch.facts.empty:
        normalized = db._normalized(batch.facts)
        expected_cols = {
            "fact_row_id",
            "vehicle_id",
            "database_name",
            "date_key",
            "fecha",
            "placa",
            "factor_de_carga",
        }
        if set(normalized.columns) != expected_cols:
            raise ValueError("columnas inesperadas en fact_factor_carga_daily")
        if (
            normalized["fact_row_id"].isna().any()
            or normalized["fact_row_id"].duplicated().any()
        ):
            raise ValueError("PK nula o duplicada en el micro-lote")
        if set(normalized["vehicle_id"].astype(str)) != {batch.analytics_vehicle_id}:
            raise ValueError(
                "el hecho no pertenece al vehículo analítico del micro-lote"
            )
        if set(normalized["fecha"]) != {local_start.date()}:
            raise ValueError("el hecho no pertenece al día del micro-lote")


def commit_microbatch(engine: Engine, batch: MicroBatch) -> CommitResult:
    """Confirma dimensiones, facts, ledger y watermark de forma atómica."""
    ensure_single_transaction_database()
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL lock_timeout = '10s'"))
        conn.execute(text("SET LOCAL statement_timeout = '120s'"))

        state = (
            conn.execute(
                text(
                    "SELECT version, watermark, backfill_from "
                    "FROM vehicle_extraction_state "
                    "WHERE vehicle_id = CAST(:vehicle_id AS uuid) AND dataset = :dataset "
                    "FOR UPDATE"
                ),
                {
                    "vehicle_id": batch.portal_vehicle_id,
                    "dataset": batch.dataset,
                },
            )
            .mappings()
            .one_or_none()
        )
        if state is None:
            raise StaleMicroBatchError("no existe el estado de extracción del vehículo")
        if int(state["version"]) != batch.expected_version:
            raise StaleMicroBatchError(
                "la versión del estado cambió durante la extracción"
            )
        cursor = state["watermark"] or state["backfill_from"]
        if cursor != batch.expected_cursor:
            raise StaleMicroBatchError("el cursor cambió durante la extracción")

        previous = bool(
            conn.execute(
                text("SELECT 1 FROM etl_microbatch_commit WHERE id = :id"),
                {"id": batch.batch_id},
            ).scalar()
        )

        db.upsert_dataframe_conn(conn, DIM_VEHICLE_SPEC, batch.vehicle_dim)
        db.upsert_dataframe_conn(conn, DIM_DATE_SPEC, batch.date_dim)
        db.upsert_dataframe_conn(conn, FACTOR_SPEC, batch.facts)

        conn.execute(
            text(
                "INSERT INTO etl_microbatch_commit "
                "(id, vehicle_id, dataset, analytics_vehicle_id, window_start, window_end, "
                "pipeline_version, output_sha256, raw_row_count, fact_row_count, comparison) "
                "VALUES (:id, CAST(:vehicle_id AS uuid), :dataset, :analytics_vehicle_id, "
                ":window_start, :window_end, :pipeline_version, :output_sha256, "
                ":raw_row_count, :fact_row_count, CAST(:comparison AS jsonb)) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": batch.batch_id,
                "vehicle_id": batch.portal_vehicle_id,
                "dataset": batch.dataset,
                "analytics_vehicle_id": batch.analytics_vehicle_id,
                "window_start": batch.window_start,
                "window_end": batch.window_end,
                "pipeline_version": batch.pipeline_version,
                "output_sha256": batch.output_sha256,
                "raw_row_count": batch.raw_row_count,
                "fact_row_count": len(batch.facts),
                "comparison": json.dumps(batch.comparison)
                if batch.comparison is not None
                else None,
            },
        )

        updated = conn.execute(
            text(
                "UPDATE vehicle_extraction_state "
                "SET watermark = CASE "
                "WHEN watermark IS NULL OR watermark < :window_end THEN :window_end "
                "ELSE watermark END, "
                "status = 'ok', last_run_at = now(), last_error = NULL, "
                "updated_at = now(), version = version + 1 "
                "WHERE vehicle_id = CAST(:vehicle_id AS uuid) AND dataset = :dataset "
                "AND version = :version"
            ),
            {
                "window_end": batch.window_end,
                "vehicle_id": batch.portal_vehicle_id,
                "dataset": batch.dataset,
                "version": batch.expected_version,
            },
        )
        if updated.rowcount != 1:
            raise StaleMicroBatchError(
                "no se pudo avanzar el watermark de forma atómica"
            )

    return CommitResult(
        batch_id=batch.batch_id,
        output_sha256=batch.output_sha256,
        fact_rows=len(batch.facts),
        previous_ledger_entry=previous,
    )
