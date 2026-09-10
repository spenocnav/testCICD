"""Equivalencia y eficiencia del merge particionado de utils.merge_staging_to_main.

Verifica que el resultado leído (pd.read_parquet) es IDÉNTICO al comportamiento
legacy de archivo único (concat + dedup keep='first'), que solo se reescriben las
particiones del mes tocado, que migra un archivo legacy existente, y que sin
columna de fecha cae al modo archivo único.
"""
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import utils  # noqa: E402


def _legacy_expected(frames, dedup_subset):
    """Reproduce la lógica legacy: concat en orden, dedup keep='first'."""
    combined = pd.concat(frames, ignore_index=True)
    if dedup_subset:
        combined = combined.drop_duplicates(subset=dedup_subset, keep='first')
    return combined


def _sorted(df):
    return df.sort_values('row_id').reset_index(drop=True)[sorted(df.columns)]


def _mk(rows):
    return pd.DataFrame(rows)


def _write_staging(tmp, name, df):
    p = os.path.join(tmp, '_staging', name)
    utils.write_staging(df, p)
    return p


def test_equivalence_and_incremental(tmp_path):
    tmp = str(tmp_path)
    main = os.path.join(tmp, 'fact_x.parquet')

    # --- Run 1: dos meses distintos ---
    s1 = _write_staging(tmp, 'r1_a.parquet', _mk([
        {'row_id': 'a', 'fecha_colombia': pd.Timestamp('2024-09-05').date(), 'v': 1},
        {'row_id': 'b', 'fecha_colombia': pd.Timestamp('2024-09-20').date(), 'v': 2},
    ]))
    s2 = _write_staging(tmp, 'r1_b.parquet', _mk([
        {'row_id': 'c', 'fecha_colombia': pd.Timestamp('2024-10-01').date(), 'v': 3},
        {'row_id': 'd', 'fecha_colombia': None, 'v': 4},  # fecha nula → sin_fecha
    ]))
    run1_frames = [pd.read_parquet(s1), pd.read_parquet(s2)]
    utils.merge_staging_to_main([s1, s2], main, dedup_subset=['row_id'])

    # main es directorio, lectura idéntica al legacy
    assert os.path.isdir(main)
    got = pd.read_parquet(main)
    exp = _legacy_expected(run1_frames, ['row_id'])
    pd.testing.assert_frame_equal(_sorted(got), _sorted(exp), check_dtype=False)

    # particiones esperadas en disco
    parts = sorted(f for f in os.listdir(main) if not f.startswith('.'))
    assert parts == ['2024-09.parquet', '2024-10.parquet', 'sin_fecha.parquet'], parts

    # staging eliminado
    assert not os.path.exists(s1) and not os.path.exists(s2)

    # --- Run 2: incrementalidad — solo toca 2024-11, NO reescribe 2024-09 ---
    sep_mtime = os.path.getmtime(os.path.join(main, '2024-09.parquet'))
    time.sleep(0.02)
    s3 = _write_staging(tmp, 'r2.parquet', _mk([
        {'row_id': 'e', 'fecha_colombia': pd.Timestamp('2024-11-10').date(), 'v': 5},
        # duplicado de 'a' con v distinto: el EXISTENTE debe ganar (keep='first')
        {'row_id': 'a', 'fecha_colombia': pd.Timestamp('2024-09-05').date(), 'v': 999},
    ]))
    run2_new = pd.read_parquet(s3)
    utils.merge_staging_to_main([s3], main, dedup_subset=['row_id'])

    # 2024-09 fue tocado (llegó duplicado de 'a') → su mtime cambia, pero 'a' conserva v=1
    got2 = pd.read_parquet(main)
    assert got2.loc[got2.row_id == 'a', 'v'].iloc[0] == 1, "el existente debe ganar"
    assert got2.loc[got2.row_id == 'e', 'v'].iloc[0] == 5

    # 2024-10 NO fue tocado → mtime intacto (eficiencia)
    # (lo verificamos con un mes realmente no tocado)
    oct_file = os.path.join(main, '2024-10.parquet')
    oct_mtime_before = os.path.getmtime(oct_file)
    s4 = _write_staging(tmp, 'r3.parquet', _mk([
        {'row_id': 'f', 'fecha_colombia': pd.Timestamp('2024-12-01').date(), 'v': 6},
    ]))
    utils.merge_staging_to_main([s4], main, dedup_subset=['row_id'])
    assert os.path.getmtime(oct_file) == oct_mtime_before, "no debe reescribir meses no tocados"

    # equivalencia total acumulada vs legacy (run1 + run2 + run3)
    run3_new = _mk([
        {'row_id': 'f', 'fecha_colombia': pd.Timestamp('2024-12-01').date(), 'v': 6}])
    all_frames = run1_frames + [run2_new, run3_new]
    exp_all = _legacy_expected(all_frames, ['row_id'])
    pd.testing.assert_frame_equal(
        _sorted(pd.read_parquet(main)), _sorted(exp_all), check_dtype=False
    )
    print("OK equivalence + incremental")


def test_migrates_legacy_single_file(tmp_path):
    tmp = str(tmp_path)
    main = os.path.join(tmp, 'fact_legacy.parquet')
    # archivo único legacy preexistente
    legacy = _mk([
        {'row_id': 'a', 'fecha_colombia': pd.Timestamp('2024-09-05').date(), 'v': 1},
        {'row_id': 'b', 'fecha_colombia': pd.Timestamp('2024-10-05').date(), 'v': 2},
    ])
    legacy.to_parquet(main, index=False)
    assert os.path.isfile(main)

    s1 = _write_staging(tmp, 'm1.parquet', _mk([
        {'row_id': 'c', 'fecha_colombia': pd.Timestamp('2024-10-20').date(), 'v': 3},
    ]))
    new = pd.read_parquet(s1)
    utils.merge_staging_to_main([s1], main, dedup_subset=['row_id'])

    assert os.path.isdir(main), "debe migrar archivo→directorio"
    exp = _legacy_expected([legacy, new], ['row_id'])
    pd.testing.assert_frame_equal(
        _sorted(pd.read_parquet(main)), _sorted(exp), check_dtype=False
    )
    print("OK migración legacy")


def test_fallback_no_date_column(tmp_path):
    tmp = str(tmp_path)
    main = os.path.join(tmp, 'dim_nodate.parquet')
    s1 = _write_staging(tmp, 'd1.parquet', _mk([
        {'row_id': 'a', 'v': 1}, {'row_id': 'b', 'v': 2}]))
    f1 = pd.read_parquet(s1)
    utils.merge_staging_to_main([s1], main, dedup_subset=['row_id'])
    assert os.path.isfile(main), "sin fecha → archivo único"

    s2 = _write_staging(tmp, 'd2.parquet', _mk([
        {'row_id': 'b', 'v': 999}, {'row_id': 'c', 'v': 3}]))
    f2 = pd.read_parquet(s2)
    utils.merge_staging_to_main([s2], main, dedup_subset=['row_id'])
    exp = _legacy_expected([f1, f2], ['row_id'])
    pd.testing.assert_frame_equal(
        _sorted(pd.read_parquet(main)), _sorted(exp), check_dtype=False
    )
    print("OK fallback archivo único")


if __name__ == '__main__':
    import tempfile
    from pathlib import Path
    for fn in (test_equivalence_and_incremental, test_migrates_legacy_single_file,
               test_fallback_no_date_column):
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
    print("\nTODOS LOS TESTS OK")
