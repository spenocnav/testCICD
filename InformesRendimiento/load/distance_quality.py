"""Auditoría idempotente de decisiones automáticas de distancia."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine


def record_automatic_distance_decisions(engine: Engine, schema: str) -> int:
    if getattr(getattr(engine, "dialect", None), "name", None) != "postgresql":
        return 0
    with engine.begin() as conn:
        decisions_table = conn.execute(
            text("SELECT to_regclass('public.distance_quality_decisions')")
        ).scalar()
        fact_table = conn.execute(
            text("SELECT to_regclass(:table_name)"),
            {"table_name": f'"{schema}"."fact_combustible_daily"'},
        ).scalar()
        if decisions_table is None or fact_table is None:
            return 0
        contract_columns = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'fact_combustible_daily' "
                "AND column_name IN ('distance_quality_status', "
                "'distance_quality_fingerprint', 'distance_quality_reason', "
                "'distance_threshold_version', 'distance_source')"
            ),
            {"schema": schema},
        ).scalar_one()
        if int(contract_columns or 0) != 5:
            return 0
        rows = conn.execute(
            text(
                f'SELECT fact_row_id, vehicle_id, date_key, kms_ecm, kms_gps, '
                f'distance_quality_fingerprint, distance_quality_reason, '
                f'distance_threshold_version, distance_source '
                f'FROM "{schema}".fact_combustible_daily '
                "WHERE distance_quality_status <> 'ok' "
                "AND distance_quality_fingerprint IS NOT NULL"
            )
        ).mappings().all()
        if not rows:
            return 0
        now = datetime.now(UTC)
        records = []
        for row in rows:
            action = "use_gps" if row["distance_source"] == "gps_auto" else "exclude"
            fingerprint = row["distance_quality_fingerprint"]
            records.append(
                {
                    "id": uuid.uuid4(),
                    "fact_row_id": row["fact_row_id"],
                    "vehicle_id": row["vehicle_id"] or "",
                    "date_key": int(row["date_key"] or 0),
                    "fingerprint": fingerprint,
                    "action": action,
                    "reason": row["distance_quality_reason"],
                    "kms_ecm": row["kms_ecm"],
                    "kms_gps": row["kms_gps"],
                    "threshold": row["distance_threshold_version"],
                    "dedupe": f'{row["fact_row_id"]}:{fingerprint}:automatic',
                    "now": now,
                }
            )
        result = conn.execute(
            text(
                "INSERT INTO public.distance_quality_decisions ("
                "id, fact_row_id, analytics_vehicle_id, date_key, "
                "observation_fingerprint, origin, action, reason_code, "
                "observed_kms_ecm, observed_kms_gps, threshold_version, "
                "dedupe_key, created_at, updated_at) VALUES ("
                ":id, :fact_row_id, :vehicle_id, :date_key, :fingerprint, "
                "'automatic', :action, :reason, :kms_ecm, :kms_gps, :threshold, "
                ":dedupe, :now, :now) ON CONFLICT (dedupe_key) DO NOTHING"
            ),
            records,
        )
        return max(0, int(result.rowcount or 0))
