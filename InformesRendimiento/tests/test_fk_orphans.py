"""Hechos históricos sin dimensión no pueden reventar la carga completa.

Los parquet de facts son acumulativos; `dim_vehicle` se reconstruye desde el
catálogo activo. Cuando el catálogo encoge, los hechos de los vehículos que
salieron quedan huérfanos y PostgreSQL los rechaza con ForeignKeyViolation. Antes
eso abortaba `load_semantic` en el primer fact afectado y las tablas siguientes
(fallas, pedal) nunca se cargaban.
"""

from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import load.db as db  # noqa: E402
import load.load_semantic as ls  # noqa: E402
import worker  # noqa: E402
from load.schema import TableSpec  # noqa: E402

SPEC = TableSpec(
    "f.parquet",
    "f",
    "fact",
    ["id"],
    fks=[("vehicle_id", "dim_vehicle", "vehicle_id")],
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": ["a", "b", "c"],
            "vehicle_id": ["v1", "v2", None],
            "kms": [1.0, 2.0, 3.0],
        }
    )


def test_keep_mask_descarta_solo_huerfanas(monkeypatch):
    monkeypatch.setattr(db, "fetch_key_set", lambda *_a, **_k: {"v1"})
    keep, dropped = ls._fk_keep_mask(object(), SPEC, db._normalized(_frame()))

    assert list(keep) == [True, False, True]  # NULL es válido para una FK
    assert dropped == {"vehicle_id": 1}


def test_keep_mask_sin_dim_creada_no_descarta(monkeypatch):
    """Primera corrida: la dim aún no existe y la FK tampoco, no hay nada que filtrar."""
    monkeypatch.setattr(db, "fetch_key_set", lambda *_a, **_k: None)
    keep, dropped = ls._fk_keep_mask(object(), SPEC, db._normalized(_frame()))

    assert keep.all()
    assert dropped == {}


def _run_loader(monkeypatch, tmp_path, keys: set) -> pd.DataFrame:
    """Corre `load_semantic.main` sin PostgreSQL y devuelve lo que se upsertearía."""
    facts = tmp_path / "facts"
    facts.mkdir(exist_ok=True)
    _frame().to_parquet(facts / SPEC.parquet_name, index=False)

    upserted: list[pd.DataFrame] = []
    monkeypatch.setattr(ls.schema, "TABLES", [SPEC])
    monkeypatch.setattr(config, "SEMANTIC_FACTS_PATH", str(facts), raising=False)
    monkeypatch.setattr(config, "LOAD_MANIFEST_PATH", str(tmp_path / "manifest"), raising=False)
    monkeypatch.setattr(config, "LOAD_FULL_UPSERT", False, raising=False)
    monkeypatch.setattr(config, "DISABLED_TABLES", set(), raising=False)
    monkeypatch.setattr(db, "get_engine", lambda: object())
    monkeypatch.setattr(db, "ensure_schema", lambda _e: None)
    monkeypatch.setattr(ls, "ensure_fuel_contract", lambda *_a: None)
    monkeypatch.setattr(db, "create_table", lambda *_a: None)
    monkeypatch.setattr(db, "create_indexes", lambda *_a: None)
    monkeypatch.setattr(db, "add_fk_constraints", lambda *_a: None)
    monkeypatch.setattr(db, "fetch_key_set", lambda *_a, **_k: keys)
    monkeypatch.setattr(db, "upsert_dataframe", lambda _e, _s, frame: upserted.append(frame))

    ls.main()
    return upserted[0]


def test_carga_omite_huerfanas_y_las_recupera_cuando_vuelve_la_dim(monkeypatch, tmp_path):
    primera = _run_loader(monkeypatch, tmp_path, {"v1"})
    assert sorted(primera["id"]) == ["a", "c"]

    # El vehículo vuelve al catálogo: sus hechos NO están en el manifiesto, así que
    # cuentan como nuevos y se suben; los ya cargados siguen sin cambios.
    segunda = _run_loader(monkeypatch, tmp_path, {"v1", "v2"})
    assert list(segunda["id"]) == ["b"]


def test_error_tail_conserva_el_mensaje_tras_una_linea_gigante():
    salida = "\n".join(
        [
            "Traceback (most recent call last):",
            "[parameters: " + "x" * 200_000 + "]",
            "sqlalchemy.exc.IntegrityError: viola la constraint fk_f_vehicle_id",
            "DETAIL:  Key (vehicle_id)=(v2) is not present in table \"dim_vehicle\".",
        ]
    )
    tail = worker._error_tail(salida)

    assert "IntegrityError" in tail
    assert "DETAIL" in tail
    assert "xxxxx" * 100 not in tail
    assert len(tail) <= worker._MAX_TAIL_CHARS
