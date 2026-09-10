"""Arnés de equivalencia de la carga incremental (Fase A).

Demuestra que el estado final en PG con carga incremental (upsert solo de filas
nuevas/cambiadas + manifiesto) es IDÉNTICO al de re-upsertear todo (comportamiento
previo), en varios escenarios: rerun idéntico, cambio de valor, filas nuevas,
filas eliminadas del origen y reordenamiento de columnas.

No requiere PostgreSQL: modela el upsert como un dict {pk: fila_completa}, que es
exactamente la semántica de ON CONFLICT (pk) DO UPDATE. La equivalencia se reduce
a "una fila saltada ya está idéntica en la DB", que es lo que el manifiesto+hash
garantizan.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import load.db as db  # noqa: E402
import load.load_semantic as ls  # noqa: E402
from load.schema import TableSpec  # noqa: E402

SPEC = TableSpec("t.parquet", "t", "fact", ["id"])


def _full_apply(store: dict, frame: pd.DataFrame):
    """Modela el upsert COMPLETO previo: re-escribe todas las filas."""
    fn = db._normalized(frame)
    for _, row in fn.iterrows():
        store[row["id"]] = tuple(row[c] for c in fn.columns)


def _incremental_apply(store: dict, frame: pd.DataFrame, spec):
    """Modela la carga incremental: upsert solo de filas nuevas/cambiadas, según
    el manifiesto en disco; luego reescribe el manifiesto con TODAS las filas."""
    fn = db._normalized(frame)
    hashes = db.row_hashes(fn)
    mask = ls._changed_mask(fn, spec, hashes, ls._read_manifest(spec))
    changed = fn[mask.to_numpy()]
    for _, row in changed.iterrows():
        store[row["id"]] = tuple(row[c] for c in fn.columns)
    ls._write_manifest(spec, fn, hashes)
    return int(mask.sum())


def _run_scenario(tmp_path, runs):
    """runs = lista de DataFrames (corridas sucesivas). Devuelve (store_full,
    store_inc, n_upserts_incrementales_por_corrida)."""
    config.LOAD_MANIFEST_PATH = str(tmp_path / "manifest")
    # limpiar manifiesto de corridas previas del mismo test
    mp = ls._manifest_path(SPEC)
    if os.path.exists(mp):
        os.remove(mp)

    store_full: dict = {}
    store_inc: dict = {}
    counts = []
    for frame in runs:
        _full_apply(store_full, frame)
        counts.append(_incremental_apply(store_inc, frame, SPEC))
    return store_full, store_inc, counts


def _df(rows, cols=("id", "val", "extra")):
    return pd.DataFrame(rows, columns=list(cols))


def test_identical_rerun(tmp_path):
    f = _df([("a", 1.0, "x"), ("b", 2.0, "y")])
    full, inc, counts = _run_scenario(tmp_path, [f, f.copy()])
    assert full == inc
    assert counts == [2, 0], "2da corrida idéntica no debe upsertear nada"


def test_value_change(tmp_path):
    f1 = _df([("a", 1.0, "x"), ("b", 2.0, "y")])
    f2 = _df([("a", 1.0, "x"), ("b", 99.0, "y")])  # cambia solo 'b'
    full, inc, counts = _run_scenario(tmp_path, [f1, f2])
    assert full == inc
    assert counts == [2, 1], "solo la fila cambiada se upsertea"


def test_new_rows(tmp_path):
    f1 = _df([("a", 1.0, "x")])
    f2 = _df([("a", 1.0, "x"), ("c", 3.0, "z")])  # nueva fila 'c'
    full, inc, counts = _run_scenario(tmp_path, [f1, f2])
    assert full == inc
    assert counts == [1, 1]


def test_removed_from_source_keeps_parity(tmp_path):
    # El flujo actual (upsert) tampoco borra: ambos deben mantener la fila vieja.
    f1 = _df([("a", 1.0, "x"), ("b", 2.0, "y")])
    f2 = _df([("a", 1.0, "x")])  # 'b' desaparece del origen
    full, inc, counts = _run_scenario(tmp_path, [f1, f2])
    assert full == inc
    assert "b" in inc and inc["b"] == full["b"]


def test_column_reorder_is_safe(tmp_path):
    f1 = _df([("a", 1.0, "x"), ("b", 2.0, "y")], cols=("id", "val", "extra"))
    f2 = _df([("a", 1.0, "x"), ("b", 2.0, "y")], cols=("id", "val", "extra"))[["id", "extra", "val"]]
    full, inc, counts = _run_scenario(tmp_path, [f1, f2])
    # Reordenar columnas puede marcar todo como cambiado (hash distinto) → re-upsert,
    # PERO el estado final debe seguir siendo idéntico (equivalencia preservada).
    assert {k: set(v) for k, v in full.items()} == {k: set(v) for k, v in inc.items()}


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    fns = [test_identical_rerun, test_value_change, test_new_rows,
           test_removed_from_source_keeps_parity, test_column_reorder_is_safe]
    for fn in fns:
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
        print(f"OK {fn.__name__}")
    print("\nTODOS LOS TESTS OK")
