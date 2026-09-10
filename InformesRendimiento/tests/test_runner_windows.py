"""Test de extract/_runner.resolve_groups SIN Geotab ni DB.

Verifica:
- modo por-vehículo: filtra al catálogo activo (descarta filas de prueba) y agrupa
  por (db, from_date) preservando el batching.
- modo fallback (sin DB): usa DEVICES_BY_DB + watermark global, vehicle_id=None.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extract"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _runner  # noqa: E402
import master_state  # noqa: E402

TO = datetime(2026, 6, 26, 5, 0, 0, tzinfo=timezone.utc)
D1 = datetime(2026, 5, 1, 5, 0, 0, tzinfo=timezone.utc)
D2 = datetime(2026, 6, 10, 5, 0, 0, tzinfo=timezone.utc)


def _restore(saved):
    for obj, attr, val in saved:
        setattr(obj, attr, val)


def test_per_vehicle_filters_catalog_and_groups_by_date():
    saved = [
        (master_state, "get_vehicle_windows", master_state.get_vehicle_windows),
        (_runner, "VEHICLE_BY_DB_DEVICE", _runner.VEHICLE_BY_DB_DEVICE),
    ]
    try:
        windows = [
            {"database_name": "alion", "device_id": "d1", "vehicle_id": "v1", "from_date": D1},
            {"database_name": "alion", "device_id": "d2", "vehicle_id": "v2", "from_date": D1},
            {"database_name": "alion", "device_id": "d3", "vehicle_id": "v3", "from_date": D2},
            {"database_name": "db-x", "device_id": "dz", "vehicle_id": "vz", "from_date": D1},  # fuera de catálogo
        ]
        master_state.get_vehicle_windows = lambda ds, to: windows
        _runner.VEHICLE_BY_DB_DEVICE = {("alion", "d1"): {}, ("alion", "d2"): {}, ("alion", "d3"): {}}

        groups, per_vehicle, nxt = _runner.resolve_groups("ubicaciones", "2024-09-01", TO)

        assert per_vehicle is True and nxt is None
        # db-x/dz excluido (no está en catálogo)
        all_devs = {dev for _, _, specs in groups for dev, _ in specs}
        assert all_devs == {"d1", "d2", "d3"}, all_devs
        # agrupado por (db, from_date): {(alion,D1):[d1,d2], (alion,D2):[d3]}
        by_key = {(db, frm): sorted(d for d, _ in specs) for db, frm, specs in groups}
        assert by_key == {("alion", D1): ["d1", "d2"], ("alion", D2): ["d3"]}, by_key
        # vehicle_id presente
        vids = {vid for _, _, specs in groups for _, vid in specs}
        assert vids == {"v1", "v2", "v3"}
        print("OK per-vehicle scope + grouping")
    finally:
        _restore(saved)


def test_fallback_uses_devices_by_db_global():
    saved = [
        (master_state, "get_vehicle_windows", master_state.get_vehicle_windows),
        (_runner, "DEVICES_BY_DB", _runner.DEVICES_BY_DB),
        (_runner, "get_date_range_for_run", _runner.get_date_range_for_run),
    ]
    try:
        master_state.get_vehicle_windows = lambda ds, to: None  # sin DB
        _runner.DEVICES_BY_DB = {"alion": ["d1", "d2"], "vacia": []}
        _runner.get_date_range_for_run = lambda sid, default_start_date_str=None: ((D1, TO), "NEXT")

        groups, per_vehicle, nxt = _runner.resolve_groups("ubicaciones", "2024-09-01", TO)

        assert per_vehicle is False and nxt == "NEXT"
        # solo db no vacía, from_date global, vehicle_id None
        assert len(groups) == 1
        db, frm, specs = groups[0]
        assert db == "alion" and frm == D1
        assert sorted(d for d, _ in specs) == ["d1", "d2"]
        assert all(vid is None for _, vid in specs)
        print("OK fallback global")
    finally:
        _restore(saved)


def test_no_db_no_json_returns_none():
    saved = [
        (master_state, "get_vehicle_windows", master_state.get_vehicle_windows),
        (_runner, "get_date_range_for_run", _runner.get_date_range_for_run),
    ]
    try:
        master_state.get_vehicle_windows = lambda ds, to: None
        _runner.get_date_range_for_run = lambda sid, default_start_date_str=None: None  # nada nuevo
        groups, per_vehicle, nxt = _runner.resolve_groups("ubicaciones", "2024-09-01", TO)
        assert groups is None and per_vehicle is False
        print("OK nada que procesar")
    finally:
        _restore(saved)


if __name__ == "__main__":
    test_per_vehicle_filters_catalog_and_groups_by_date()
    test_fallback_uses_devices_by_db_global()
    test_no_db_no_json_returns_none()
    print("\nTODOS LOS TESTS OK")
