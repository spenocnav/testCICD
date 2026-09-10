from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pandas as pd


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from cloudfleet_api import CallResult  # noqa: E402
from build_label_history import build_events  # noqa: E402
import tracking_label_worker as worker_module  # noqa: E402
from tracking_label_worker import (  # noqa: E402
    WorkerPaths,
    bootstrap_all_tracking,
    build_parser,
    read_ledger,
)


NOW = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)


def result(data: Any, status: int = 200) -> CallResult:
    return CallResult(
        ok=200 <= status < 400,
        status_code=status,
        duration_ms=1.0,
        data=data,
        error=None if status < 400 else f"HTTP {status}",
        rate_limit_remaining=29,
        rate_limit_reset=60,
        url="https://cloudfleet.invalid/api/v1/test",
        utc_iso=NOW.isoformat(),
        attempts=1,
        headers={},
    )


def args():
    parsed = build_parser().parse_args(["--bootstrap-all-tracking"])
    parsed.interval = 300
    parsed.delay = 0.0
    # The complete historical bootstrap must ignore the live batch cap.
    parsed.max_ots_per_cycle = 1
    parsed.discovery_overlap_seconds = 120
    parsed.catalog_from_date = "2021-01-01"
    parsed.active_statuses = "opened,onTechnicalCompletion"
    parsed.skip_seed = True
    return parsed


class ResumableBootstrapClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(min_request_interval_seconds=0.0)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_second = True
        self.interrupt_second = False

    def get(self, path: str, params=None, timeout=None) -> CallResult:
        del timeout
        parameters = dict(params or {})
        self.calls.append((path, parameters))
        if path == "work-orders":
            # Full and resumed incremental catalog calls both use this endpoint.
            if len([call for call, _ in self.calls if call == "work-orders"]) == 1:
                return result(
                    [
                        {
                            "number": 700001,
                            "status": "closed",
                            "updatedAt": "2021-03-01T00:00:00Z",
                        },
                        {
                            "number": 700002,
                            "status": "opened",
                            "updatedAt": "2026-08-11T16:59:00Z",
                        },
                    ]
                )
            return result(None, 404)
        if path == "work-orders/700001/tracking":
            return result(
                [
                    {
                        "id": 1,
                        "trackingDate": "2021-03-01T05:00:00Z",
                        "comment": "1",
                        "createdBy": {"id": 10, "name": "private"},
                        "files": None,
                    }
                ]
            )
        if path == "work-orders/700002/tracking":
            if self.interrupt_second:
                raise KeyboardInterrupt()
            if self.fail_second:
                return result(None, 500)
            return result(None, 404)
        raise AssertionError(f"unexpected path: {path}")


def test_all_tracking_bootstrap_is_resumable_and_does_not_repeat_successes(
    tmp_path: Path,
) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    client = ResumableBootstrapClient()

    first = bootstrap_all_tracking(args(), client, paths=paths, clock=lambda: NOW)

    assert first["status"] == "incomplete"
    assert first["totalOrders"] == 2
    assert first["completedOrders"] == 1
    assert first["pendingOrders"] == 1
    assert [path for path, _ in client.calls if path.endswith("/tracking")] == [
        "work-orders/700001/tracking",
        "work-orders/700002/tracking",
    ]
    first_catalog_params = next(params for path, params in client.calls if path == "work-orders")
    assert "status" not in first_catalog_params
    assert [path for path, _ in client.calls if path.endswith("/tracking")] == [
        "work-orders/700001/tracking",  # terminal/historical first
        "work-orders/700002/tracking",  # active orders finish the baseline
    ]
    catalog = json.loads(paths.catalog.read_text(encoding="utf-8"))
    assert catalog["pendingEnrichment"] == []
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    assert state["bootstrapAllTracking"]["catalogComplete"] is True
    assert state["ot"]["700001"]["historical_bootstrap_complete"] is True
    assert not state["ot"]["700002"].get("historical_bootstrap_complete")

    calls_before_resume = list(client.calls)
    client.fail_second = False
    second = bootstrap_all_tracking(args(), client, paths=paths, clock=lambda: NOW)

    resumed_calls = client.calls[len(calls_before_resume) :]
    assert second["status"] == "complete"
    assert second["completedOrders"] == 2
    assert [path for path, _ in resumed_calls] == [
        "work-orders",
        "work-orders/700002/tracking",
    ]
    ledger, rejected, _ = read_ledger(paths)
    assert rejected == 0
    assert len(ledger) == 1
    assert ledger[0]["tracking_observation_kind"] == "historical_bootstrap"
    events, _, _ = build_events(pd.DataFrame(ledger))
    assert len(events) == 1
    assert events.iloc[0]["event_at_source"] == "tracking_date_baseline"

    calls_before_idempotent_run = len(client.calls)
    third = bootstrap_all_tracking(args(), client, paths=paths, clock=lambda: NOW)
    repeated_paths = [path for path, _ in client.calls[calls_before_idempotent_run:]]
    ledger_after, rejected_after, _ = read_ledger(paths)
    assert third["status"] == "complete"
    assert third["addedRows"] == 0
    assert repeated_paths == ["work-orders"]
    assert rejected_after == 0
    assert len(ledger_after) == 1


def test_tracking_bootstrap_checkpoints_real_interruption_and_resumes_current_ot(
    tmp_path: Path,
) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    client = ResumableBootstrapClient()
    client.fail_second = False
    client.interrupt_second = True

    with pytest.raises(KeyboardInterrupt):
        bootstrap_all_tracking(args(), client, paths=paths, clock=lambda: NOW)

    interrupted = json.loads(paths.state.read_text(encoding="utf-8"))
    progress = interrupted["bootstrapAllTracking"]
    assert progress["status"] == "incomplete"
    assert progress["phase"] == "tracking"
    assert progress["catalogComplete"] is True
    assert progress["currentOrder"] == 700002
    assert interrupted["ot"]["700001"]["historical_bootstrap_complete"] is True
    assert not interrupted["ot"].get("700002", {}).get(
        "historical_bootstrap_complete"
    )

    calls_before_resume = len(client.calls)
    client.interrupt_second = False
    resumed = bootstrap_all_tracking(args(), client, paths=paths, clock=lambda: NOW)
    resumed_paths = [path for path, _ in client.calls[calls_before_resume:]]

    assert resumed["status"] == "complete"
    assert resumed_paths == ["work-orders", "work-orders/700002/tracking"]


def test_incomplete_catalog_phase_cannot_promote_legacy_active_catalog(
    tmp_path: Path,
) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    paths.catalog.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "discoveryCursorAt": "2026-08-11T16:00:00Z",
                "orders": [
                    {
                        "number": 700001,
                        "status": "opened",
                        "updatedAt": "2026-08-11T16:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    paths.state.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "bootstrapAllTracking": {
                    "status": "running",
                    "phase": "catalog",
                    "catalogComplete": False,
                },
            }
        ),
        encoding="utf-8",
    )
    client = ResumableBootstrapClient()
    client.fail_second = False

    bootstrap = bootstrap_all_tracking(args(), client, paths=paths, clock=lambda: NOW)

    assert bootstrap["catalogSync"]["mode"] == "full"
    catalog = json.loads(paths.catalog.read_text(encoding="utf-8"))
    assert catalog["catalogScope"] == "historical_all_statuses"
    assert catalog["catalogFullConfirmedAt"]
    assert catalog["pendingEnrichment"] == []
    assert {int(order["number"]) for order in catalog["orders"]} == {700001, 700002}
    first_params = next(params for path, params in client.calls if path == "work-orders")
    assert "status" not in first_params


def test_seed_reconciliation_migrates_false_completion_and_forces_full_catalog(
    tmp_path: Path,
) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    paths.catalog.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "catalogScope": "historical_all_statuses",
                "catalogFullConfirmedAt": "2026-06-25T23:59:59Z",
                "discoveryCursorAt": "2026-06-25T00:00:00Z",
                "pendingFullReconciliation": True,
                "orders": [
                    {"number": 700001, "status": "closed"},
                    {"number": 700002, "status": "opened"},
                ],
            }
        ),
        encoding="utf-8",
    )
    paths.state.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "certifiedHistoricalSeed": {
                    "status": "imported",
                    "signature": "sha256:test-seed",
                    "importedAt": "2026-08-11T16:00:00Z",
                },
                "bootstrapAllTracking": {
                    "status": "complete",
                    "catalogComplete": True,
                },
                "ot": {
                    # This is real post-import API evidence and must not repeat.
                    "700001": {
                        "baseline_complete": True,
                        "historical_seed_certified": True,
                        "historical_bootstrap_complete": True,
                        "last_successful_check_at": "2026-08-11T16:30:00Z",
                    },
                    # Old releases marked this complete from the seed alone.
                    "700002": {
                        "baseline_complete": True,
                        "historical_seed_certified": True,
                        "historical_bootstrap_complete": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    client = ResumableBootstrapClient()
    client.fail_second = False

    bootstrap = bootstrap_all_tracking(args(), client, paths=paths, clock=lambda: NOW)

    assert bootstrap["status"] == "complete"
    assert bootstrap["catalogSync"]["mode"] == "full"
    tracking_calls = [path for path, _ in client.calls if path.endswith("/tracking")]
    assert tracking_calls == ["work-orders/700002/tracking"]
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    assert state["ot"]["700001"]["tracking_reconciliation_source"] == (
        "legacy_checkpoint_migration"
    )
    assert state["ot"]["700001"]["tracking_reconciliation_seed_signature"] == (
        "sha256:test-seed"
    )
    assert state["ot"]["700002"]["tracking_reconciliation_source"] == (
        "historical_bootstrap"
    )
    assert state["ot"]["700002"]["tracking_reconciliation_required"] is False
    catalog = json.loads(paths.catalog.read_text(encoding="utf-8"))
    assert catalog["pendingFullReconciliation"] is False


def test_seed_reconciliation_rejects_legacy_evidence_from_before_import() -> None:
    state = {
        "certifiedHistoricalSeed": {
            "status": "imported",
            "signature": "sha256:test-seed",
            "importedAt": "2026-08-11T16:00:00Z",
        },
        "ot": {
            "700001": {
                "baseline_complete": True,
                "historical_seed_certified": True,
                "historical_bootstrap_complete": True,
                "last_successful_check_at": "2026-08-11T15:59:59Z",
            }
        },
    }

    migrated = worker_module._normalize_certified_seed_reconciliation(state)

    assert migrated == {
        "total": 1,
        "completed": 0,
        "pending": 1,
        "changed": True,
    }
    ot_state = state["ot"]["700001"]
    assert ot_state["historical_bootstrap_complete"] is False
    assert ot_state["tracking_reconciliation_required"] is True


def test_bootstrap_publishes_lightweight_progress_every_ten_orders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)

    class BatchClient:
        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=0.0)

        def get(self, path: str, params=None, timeout=None) -> CallResult:
            del params, timeout
            if path == "work-orders":
                return result(
                    [
                        {
                            "number": 800000 + index,
                            "status": "closed",
                            "updatedAt": "2026-08-11T16:00:00Z",
                            "vehicleCode": "QA_PRIVATE_PLATE",
                            "comment": "QA_PRIVATE_COMMENT",
                            "createdBy": {"name": "QA_PRIVATE_AUTHOR"},
                            "payload": "QA_PRIVATE_PAYLOAD",
                        }
                        for index in range(1, 13)
                    ]
                )
            if path.endswith("/tracking"):
                return result(None, 404)
            raise AssertionError(f"unexpected path: {path}")

    snapshots: list[dict[str, Any]] = []
    original_publish = worker_module._publish_bootstrap_progress_snapshot
    original_build = worker_module.build_dashboard
    build_calls = 0

    def capture_publish(*publish_args, **publish_kwargs):
        response = original_publish(*publish_args, **publish_kwargs)
        snapshots.append(
            json.loads(paths.dashboard.read_text(encoding="utf-8"))["worker"]
        )
        return response

    def capture_build(*build_args, **build_kwargs):
        nonlocal build_calls
        build_calls += 1
        return original_build(*build_args, **build_kwargs)

    monkeypatch.setattr(
        worker_module, "_publish_bootstrap_progress_snapshot", capture_publish
    )
    monkeypatch.setattr(worker_module, "build_dashboard", capture_build)

    bootstrap = bootstrap_all_tracking(
        args(), BatchClient(), paths=paths, clock=lambda: NOW
    )

    assert bootstrap["status"] == "complete"
    assert [item["historicalBootstrapAttemptedThisRun"] for item in snapshots] == [
        0,
        10,
        12,
    ]
    assert snapshots[1]["historicalBootstrapCompletedOrders"] == 10
    assert snapshots[1]["historicalBootstrapPendingOrders"] == 2
    assert snapshots[1]["historicalBootstrapOrdersPerMinute"] is not None
    assert snapshots[1]["historicalBootstrapEtaSeconds"] is not None
    assert all(item["status"] == "bootstrap_running" for item in snapshots)
    assert all("ot" not in item and "state" not in item for item in snapshots)
    public_progress = json.dumps(snapshots, ensure_ascii=False)
    assert "QA_PRIVATE_PLATE" not in public_progress
    assert "QA_PRIVATE_COMMENT" not in public_progress
    assert "QA_PRIVATE_AUTHOR" not in public_progress
    assert "QA_PRIVATE_PAYLOAD" not in public_progress
    assert build_calls == 1

    health = json.loads(paths.health.read_text(encoding="utf-8"))
    assert health["status"] == "bootstrap_complete"
    assert health["metrics"]["historicalBootstrapCompletedOrders"] == 12
    assert health["metrics"]["historicalBootstrapPendingOrders"] == 0
