from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from cloudfleet_api import CallResult  # noqa: E402
from tracking_label_worker import (  # noqa: E402
    WorkerPaths,
    append_ledger,
    build_parser,
    ledger_identity,
    read_ledger,
    run_once,
    seed_ledger_from_parquet,
    tracking_rows,
)
from sync_order_catalog import sync  # noqa: E402


def response(data: Any, status: int = 200) -> CallResult:
    return CallResult(
        ok=200 <= status < 400,
        status_code=status,
        duration_ms=1.0,
        data=data,
        error=None if status < 400 else f"HTTP {status}",
        rate_limit_remaining=50,
        rate_limit_reset=None,
        url="https://cloudfleet.invalid/api/v1/test",
        utc_iso="2026-08-11T17:00:00Z",
        attempts=1,
        headers={},
    )


class FakeClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(min_request_interval_seconds=0.0)
        self.calls: list[str] = []
        self.discovery_responses = [
            [
                {
                    "number": 900123,
                    "status": "opened",
                    "updatedAt": "2026-08-11T17:04:30Z",
                }
            ],
            [],
        ]
        self.tracking_payload = [
            {
                "id": 800123,
                "trackingDate": "2026-08-11T05:00:00Z",
                "comment": "9",
                "createdBy": {"id": 77, "name": "Usuario de prueba"},
                "files": [],
            }
        ]

    def get(self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> CallResult:
        del params, timeout
        self.calls.append(path)
        if path == "work-orders":
            return response(self.discovery_responses.pop(0))
        if path == "work-orders/900123":
            return response(
                {
                    "number": 900123,
                    "status": "opened",
                    "type": "Correctivo",
                    "vehicleCode": "DEM123",
                    "updatedAt": "2026-08-11T17:04:30Z",
                    "primaryGroup": {"id": 10, "name": "Cliente de prueba"},
                    "costCenter": {"id": 20, "name": "CD de prueba"},
                }
            )
        if path == "work-orders/900123/tracking":
            return response(self.tracking_payload)
        raise AssertionError(f"Llamada inesperada: {path}")


class SequenceClock:
    def __init__(self, *values: str) -> None:
        self.values = iter(datetime.fromisoformat(value.replace("Z", "+00:00")) for value in values)

    def __call__(self) -> datetime:
        return next(self.values).astimezone(timezone.utc)


def worker_args():
    args = build_parser().parse_args([])
    args.interval = 300
    args.delay = 0.0
    args.max_ots_per_cycle = 1
    args.discovery_overlap_seconds = 120
    args.catalog_from_date = "2021-01-01"
    args.active_statuses = "opened,onTechnicalCompletion"
    args.skip_seed = True
    return args


def test_new_order_is_discovered_enriched_tracked_live_and_idempotent(tmp_path: Path) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    paths.catalog.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "generatedAt": "2026-08-11T17:00:30Z",
                "discoveryCursorAt": "2026-08-11T17:00:30Z",
                "lastFullReconciliationAt": "2026-08-11T16:00:00Z",
                "orders": [
                    {
                        "number": 800001,
                        "status": "closed",
                        "updatedAt": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    client = FakeClient()
    args = worker_args()

    first = run_once(
        args,
        client,
        paths=paths,
        clock=SequenceClock(
            "2026-08-11T17:05:00Z",
            "2026-08-11T17:05:30Z",
            "2026-08-11T17:05:31Z",
        ),
    )

    assert client.calls == [
        "work-orders",
        "work-orders/900123",
        "work-orders/900123/tracking",
    ]
    assert first["newOrders"] == 1
    assert first["newTrackingRows"] == 1
    catalog = json.loads(paths.catalog.read_text(encoding="utf-8"))
    new_order = next(order for order in catalog["orders"] if order["number"] == 900123)
    assert new_order["vehicleCode"] == "DEM123"
    assert new_order["primaryGroup"]["name"] == "Cliente de prueba"

    ledger, rejected, _ = read_ledger(paths)
    assert rejected == 0
    assert len(ledger) == 1
    row = ledger[0]
    assert row["tracking_observation_kind"] == "live_new_order"
    assert row["tracking_observed_from"] == "2026-08-11T17:00:30+00:00"
    assert row["tracking_observed_at"] == "2026-08-11T17:05:30+00:00"
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    ot_state = state["ot"]["900123"]
    assert ot_state["tracking_reconciled_at"] == "2026-08-11T17:05:30+00:00"
    assert ot_state["tracking_reconciliation_source"] == "live"
    assert ot_state["tracking_reconciliation_required"] is False

    dashboard = json.loads(paths.dashboard.read_text(encoding="utf-8"))
    current = next(order for order in dashboard["orders"] if order["ot"] == 900123)
    assert current["currentTimeSource"] == "observation_window_end"
    assert current["currentObservationTo"] == row["tracking_observed_at"]
    assert current["currentObservationWindowMinutes"] == 5.0
    assert current["currentWindowWithinSla"] is True

    second = run_once(
        args,
        client,
        paths=paths,
        clock=SequenceClock(
            "2026-08-11T17:10:00Z",
            "2026-08-11T17:10:30Z",
            "2026-08-11T17:10:31Z",
        ),
    )
    assert client.calls[-2:] == ["work-orders", "work-orders/900123/tracking"]
    assert second["newOrders"] == 0
    assert second["newTrackingRows"] == 0
    ledger_after, rejected_after, _ = read_ledger(paths)
    assert rejected_after == 0
    assert len(ledger_after) == 1


def test_identical_rows_without_tracking_id_get_distinct_stable_keys(tmp_path: Path) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    item = {
        "id": None,
        "trackingDate": "2026-08-11T05:00:00Z",
        "comment": "9",
        "createdBy": {"id": 77, "name": "Usuario de prueba"},
        "files": [],
    }
    rows = tracking_rows(
        {"number": 900123, "status": "opened", "vehicleCode": "DEM123"},
        [item, dict(item)],
        "2026-08-11T17:05:00Z",
        "2026-08-11T17:00:00Z",
        "live",
    )

    assert len(rows) == 2
    assert rows[0]["tracking_key"] != rows[1]["tracking_key"]
    known: set[tuple[str, str]] = set()
    assert append_ledger(paths, rows, known) == 2
    assert append_ledger(paths, rows, known) == 0
    stored, rejected, _ = read_ledger(paths)
    assert rejected == 0
    assert len(stored) == 2
    assert len({ledger_identity(row) for row in stored}) == 2


def test_historical_seed_preserves_identical_null_id_rows(tmp_path: Path) -> None:
    paths = WorkerPaths.from_runtime(tmp_path / "runtime")
    seed_path = tmp_path / "seed.parquet"
    duplicate = {
        "work_order_number": 900123,
        "tracking_id": None,
        "tracking_date": "2026-08-11T05:00:00Z",
        "tracking_comment": "9",
        "created_by_id": 77,
        "created_by_name": "Usuario de prueba",
    }
    pd.DataFrame([duplicate, dict(duplicate)]).to_parquet(seed_path, index=False)
    state: dict[str, Any] = {}

    added = seed_ledger_from_parquet(paths, state, seed_path, [])
    stored, rejected, _ = read_ledger(paths)

    assert added == 2
    assert rejected == 0
    assert len(stored) == 2
    assert len({ledger_identity(row) for row in stored}) == 2
    assert seed_ledger_from_parquet(paths, state, seed_path, stored) == 0


def test_full_reconciliation_defers_new_order_detail_to_next_incremental(tmp_path: Path) -> None:
    catalog_file = tmp_path / "order_catalog.json"
    catalog_file.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "generatedAt": "2026-08-11T16:00:00Z",
                "discoveryCursorAt": "2026-08-11T16:00:00Z",
                "orders": [{"number": 800001, "status": "closed"}],
            }
        ),
        encoding="utf-8",
    )

    class CatalogClient:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.discovery = [
                [{"number": 900123, "status": "opened", "updatedAt": "2026-08-11T16:59:00Z"}],
                [],
            ]

        def get(self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> CallResult:
            del params, timeout
            self.calls.append(path)
            if path == "work-orders":
                return response(self.discovery.pop(0))
            if path == "work-orders/900123":
                return response(
                    {
                        "number": 900123,
                        "status": "opened",
                        "vehicleCode": "DEM123",
                        "primaryGroup": {"name": "Cliente de prueba"},
                    }
                )
            raise AssertionError(f"Llamada inesperada: {path}")

    client = CatalogClient()
    full = sync(
        "opened",
        0.0,
        10,
        "2026-08-11",
        "2026-08-11",
        mode="full",
        client=client,
        catalog_file=catalog_file,
        now=datetime.fromisoformat("2026-08-11T17:00:00+00:00"),
    )
    after_full = json.loads(catalog_file.read_text(encoding="utf-8"))

    assert full["newOrders"] == 1
    assert client.calls == ["work-orders"]
    assert after_full["pendingEnrichment"] == [900123]

    incremental = sync(
        "opened",
        0.0,
        10,
        "2026-08-11",
        mode="incremental",
        client=client,
        catalog_file=catalog_file,
        now=datetime.fromisoformat("2026-08-11T17:05:00+00:00"),
    )
    after_incremental = json.loads(catalog_file.read_text(encoding="utf-8"))
    recovered = next(order for order in after_incremental["orders"] if order["number"] == 900123)

    assert incremental["enrichedOrders"] == 1
    assert client.calls[-2:] == ["work-orders", "work-orders/900123"]
    assert after_incremental["pendingEnrichment"] == []
    assert recovered["vehicleCode"] == "DEM123"
