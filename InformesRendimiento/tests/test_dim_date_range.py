"""dim_date debe cubrir el inicio del mes, no solo los días con lecturas."""

from __future__ import annotations

import os
from datetime import date

import pandas as pd

from transform import transform_dim_date


def _lake(monkeypatch, tmp_path, fechas: list[date]) -> None:
    path = tmp_path / "fact_status_readings.parquet"
    pd.DataFrame({"fecha_colombia": fechas}).to_parquet(path, index=False)
    monkeypatch.setattr(transform_dim_date, "FACT_FILES", [str(path)])


def test_range_starts_at_the_first_day_of_the_month(tmp_path, monkeypatch) -> None:
    _lake(monkeypatch, tmp_path, [date(2025, 1, 24), date(2026, 7, 25)])

    start, end = transform_dim_date._infer_range()

    # fact_combustible_monthly referencia 2025-01-01 para el mes 2025-01.
    assert start == date(2025, 1, 1)
    assert end > date(2026, 7, 25)


def test_generated_dim_covers_every_month_start(tmp_path, monkeypatch) -> None:
    _lake(monkeypatch, tmp_path, [date(2025, 1, 24), date(2025, 3, 10)])
    monkeypatch.setattr(transform_dim_date, "SEMANTIC_DIMS_PATH", str(tmp_path))
    monkeypatch.setattr(
        transform_dim_date,
        "OUTPUT_FILE",
        os.path.join(str(tmp_path), "dim_date.parquet"),
    )

    transform_dim_date.main()

    keys = set(pd.read_parquet(transform_dim_date.OUTPUT_FILE)["date_key"])
    assert {20250101, 20250201, 20250301} <= keys
