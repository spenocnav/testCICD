from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from load import db
from load.schema import TableSpec


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement) -> None:
        self.statements.append(str(statement))


class _Begin:
    def __init__(self, connection: _RecordingConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _RecordingConnection:
        return self.connection

    def __exit__(self, *_args) -> None:
        return None


class _RecordingEngine:
    def __init__(self) -> None:
        self.connection = _RecordingConnection()

    def begin(self) -> _Begin:
        return _Begin(self.connection)


def test_create_table_emite_migracion_aditiva_para_columnas_nuevas(monkeypatch):
    monkeypatch.setattr(db.config, "ANALYTICS_DB_SCHEMA", "analytics_test")
    engine = _RecordingEngine()
    spec = TableSpec(
        "fact_combustible_daily.parquet",
        "fact_combustible_daily",
        "fact",
        ["fact_row_id"],
    )
    frame = pd.DataFrame(
        {
            "fact_row_id": ["f1"],
            "Comb Ralentí": [3.6],
            "gal/hr Ralentí": [1.2],
        }
    )

    db.create_table(engine, spec, frame)

    statements = "\n".join(engine.connection.statements)
    assert "CREATE TABLE IF NOT EXISTS" in statements
    assert 'ADD COLUMN IF NOT EXISTS "comb_ralenti" DOUBLE PRECISION' in statements
    assert 'ADD COLUMN IF NOT EXISTS "gal_hr_ralenti" DOUBLE PRECISION' in statements
