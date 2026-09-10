"""Integración del loader parquet → PostgreSQL.

Requiere un PostgreSQL accesible vía env `TEST_ANALYTICS_DB_URL`
(ej: postgresql+psycopg://navifault:navifault_dev@localhost:5437/navifault).
Usa un schema desechable `analytics_test` que se crea y se dropea.
"""

from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

TEST_DB_URL = os.environ.get("TEST_ANALYTICS_DB_URL")
TEST_SCHEMA = "analytics_test"

pytestmark = pytest.mark.integration


@pytest.fixture
def engine():
    if not TEST_DB_URL:
        pytest.skip("TEST_ANALYTICS_DB_URL no configurada")
    eng = create_engine(TEST_DB_URL)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG no accesible: {exc}")
    yield eng
    with eng.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
    eng.dispose()


@pytest.fixture
def semantic_dirs(tmp_path):
    dims = tmp_path / "semantic" / "dims"
    facts = tmp_path / "semantic" / "facts"
    dims.mkdir(parents=True)
    facts.mkdir(parents=True)
    return dims, facts


def _write_seed(dims, facts, *, kms=100.0, comb_ralenti=None):
    pd.DataFrame(
        {
            "vehicle_id": ["v1"],
            "database_name": ["navitrans"],
            "device_id": ["b39D"],
            "vehicle_label": ["TLK250"],
            "motor_type": ["ISDNV"],
            "group_key": ["isdNV"],
            "rpm_class": ["isdNV"],
            "fuel_type_raw": ["DIESEL"],
            "fuel_kind": ["liquid"],
            "fuel_unit": ["gal"],
            "fuel_classification_source": ["vehicle_master"],
            "fuel_classification_conflict": [False],
            "is_active": [True],
        }
    ).to_parquet(dims / "dim_vehicle.parquet", index=False)

    pd.DataFrame(
        {
            "date_key": [20260319],
            "date": [dt.date(2026, 3, 19)],
            "year": [2026],
            "month": [3],
            "day": [19],
        }
    ).to_parquet(dims / "dim_date.parquet", index=False)

    fact_data = {
        "fact_row_id": ["f1"],
        "vehicle_id": ["v1"],
        "database_name": ["navitrans"],
        "motor_type": ["ISDNV"],
        "fuel_kind": ["liquid"],
        "fuel_unit": ["gal"],
        "date_key": [20260319],
        "Fecha": [dt.date(2026, 3, 19)],
        "Placa": ["TLK250"],
        "Kms ECM": [kms],
        "Kms GPS": [kms],
        "Kms Effective": [kms],
        "Distance Source": ["ecm"],
        "Distance Quality Status": ["ok"],
        "Distance Quality Reason": ["ok"],
        "GPS Quality Valid": [True],
        "Distance Quality Fingerprint": ["a" * 64],
        "Distance Threshold Version": ["v1"],
        "% Ralentí": [12.5],
        "Revisión": [False],
    }
    if comb_ralenti is not None:
        fact_data["Comb Ralentí"] = [comb_ralenti]
    pd.DataFrame(fact_data).to_parquet(
        facts / "fact_combustible_daily.parquet", index=False
    )


def _patch(monkeypatch, engine, dims, facts):
    import config
    from load import db, load_semantic

    monkeypatch.setattr(config, "ANALYTICS_DB_URL", TEST_DB_URL, raising=False)
    monkeypatch.setattr(config, "ANALYTICS_DB_SCHEMA", TEST_SCHEMA, raising=False)
    monkeypatch.setattr(config, "SEMANTIC_DIMS_PATH", str(dims), raising=False)
    monkeypatch.setattr(config, "SEMANTIC_FACTS_PATH", str(facts), raising=False)
    return db, load_semantic


def _count(engine, table):
    with engine.connect() as c:
        return c.execute(text(f'SELECT count(*) FROM "{TEST_SCHEMA}".{table}')).scalar()


def test_load_creates_and_loads(monkeypatch, engine, semantic_dirs):
    dims, facts = semantic_dirs
    _write_seed(dims, facts)
    _, load_semantic = _patch(monkeypatch, engine, dims, facts)

    load_semantic.main()

    assert _count(engine, "dim_vehicle") == 1
    assert _count(engine, "dim_date") == 1
    assert _count(engine, "fact_combustible_daily") == 1

    # columnas normalizadas
    with engine.connect() as c:
        row = c.execute(
            text(
                f'SELECT kms_ecm, pct_ralenti, revision '
                f'FROM "{TEST_SCHEMA}".fact_combustible_daily WHERE fact_row_id = \'f1\''
            )
        ).one()
    assert row.kms_ecm == 100.0
    assert row.pct_ralenti == 12.5
    assert row.revision is False


def test_idempotent_and_upsert(monkeypatch, engine, semantic_dirs):
    dims, facts = semantic_dirs
    _write_seed(dims, facts, kms=100.0)
    _, load_semantic = _patch(monkeypatch, engine, dims, facts)

    load_semantic.main()
    load_semantic.main()  # re-run: no debe duplicar
    assert _count(engine, "fact_combustible_daily") == 1

    # cambiar valor y recargar: debe ACTUALIZAR, no insertar
    _write_seed(dims, facts, kms=250.0)
    load_semantic.main()
    assert _count(engine, "fact_combustible_daily") == 1
    with engine.connect() as c:
        kms = c.execute(
            text(
                f'SELECT kms_ecm FROM "{TEST_SCHEMA}".fact_combustible_daily '
                f"WHERE fact_row_id = 'f1'"
            )
        ).scalar()
    assert kms == 250.0


def test_adds_new_semantic_columns_without_recreating_table(
    monkeypatch, engine, semantic_dirs
):
    dims, facts = semantic_dirs
    _write_seed(dims, facts)
    _, load_semantic = _patch(monkeypatch, engine, dims, facts)
    load_semantic.main()

    _write_seed(dims, facts, comb_ralenti=3.6)
    load_semantic.main()

    with engine.connect() as c:
        value = c.execute(
            text(
                f'SELECT comb_ralenti FROM "{TEST_SCHEMA}".fact_combustible_daily '
                "WHERE fact_row_id = 'f1'"
            )
        ).scalar()
    assert value == 3.6


def test_indexes_and_fks(monkeypatch, engine, semantic_dirs):
    dims, facts = semantic_dirs
    _write_seed(dims, facts)
    _, load_semantic = _patch(monkeypatch, engine, dims, facts)
    load_semantic.main()

    with engine.connect() as c:
        idx = c.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                f"WHERE schemaname = '{TEST_SCHEMA}' "
                "AND tablename = 'fact_combustible_daily'"
            )
        ).scalars().all()
        fks = c.execute(
            text(
                "SELECT conname FROM pg_constraint "
                f"WHERE connamespace = '{TEST_SCHEMA}'::regnamespace AND contype = 'f'"
            )
        ).scalars().all()

    assert any("veh_date" in i for i in idx)
    with engine.connect() as c:
        dim_vehicle_idx = c.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                f"WHERE schemaname = '{TEST_SCHEMA}' "
                "AND tablename = 'dim_vehicle'"
            )
        ).scalars().all()
    assert "ix_dim_vehicle_device_id" in dim_vehicle_idx
    assert "fk_fact_combustible_daily_vehicle_id" in fks
    assert "fk_fact_combustible_daily_date_key" in fks
