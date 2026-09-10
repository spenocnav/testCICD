from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from cloudfleet_api import CallResult  # noqa: E402
from operational_logging import OperationalLogger  # noqa: E402
from sync_order_catalog import sync  # noqa: E402
from tracking_label_worker import (  # noqa: E402
    WorkerPaths,
    append_ledger,
    build_parser,
    choose_targets,
    compact_ledger,
    ledger_identity,
    read_ledger,
    run_once,
)


NOW = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)
ACTIVE_STATUSES = {"opened", "ontechnicalcompletion"}


def call_result(data: Any) -> CallResult:
    return CallResult(
        ok=True,
        status_code=200,
        duration_ms=1.0,
        data=data,
        error=None,
        rate_limit_remaining=29,
        rate_limit_reset=60,
        url="https://cloudfleet.invalid/api/v1/work-orders",
        utc_iso="2026-08-11T17:00:00Z",
        attempts=1,
        headers={},
    )


def active_orders(count: int) -> dict[int, dict[str, Any]]:
    return {
        910000 + index: {
            "number": 910000 + index,
            "status": "opened" if index % 2 else "onTechnicalCompletion",
            # Deliberately old: active eligibility must not depend on updatedAt.
            "updatedAt": "2021-01-01T00:00:00Z",
        }
        for index in range(1, count + 1)
    }


class AdvancingClock:
    def __init__(self, start: datetime, step_seconds: float = 1.0) -> None:
        self.current = start
        self.step = timedelta(seconds=step_seconds)

    def __call__(self) -> datetime:
        value = self.current
        self.current += self.step
        return value


def worker_args(max_ots: int, change_driven: bool = True) -> Any:
    args = build_parser().parse_args([])
    args.interval = 300
    args.delay = 0.0
    args.max_ots_per_cycle = max_ots
    args.discovery_overlap_seconds = 120
    args.catalog_from_date = "2021-01-01"
    args.active_statuses = "opened,onTechnicalCompletion"
    args.skip_seed = True
    args.log_level = "INFO"
    args.change_driven = change_driven
    return args


def test_active_round_covers_all_old_orders_despite_cap_and_priority_churn() -> None:
    orders = active_orders(147)
    active_numbers = set(orders)
    recent_closed = 920001
    stale_closed = 920002
    orders[recent_closed] = {
        "number": recent_closed,
        "status": "closed",
        "updatedAt": "2026-08-11T16:59:00Z",
    }
    orders[stale_closed] = {
        "number": stale_closed,
        "status": "closed",
        "updatedAt": "2021-01-01T00:00:00Z",
    }
    state: dict[str, Any] = {}
    continuously_prioritized = set(sorted(active_numbers)[:130])

    first, active, eligible = choose_targets(
        orders,
        state,
        set(),
        130,
        14,
        ACTIVE_STATUSES,
        continuously_prioritized,
        NOW,
        change_driven=False,
    )
    second, active_after, eligible_after = choose_targets(
        orders,
        state,
        set(),
        130,
        14,
        ACTIVE_STATUSES,
        continuously_prioritized,
        NOW,
        change_driven=False,
    )

    assert active == active_after == active_numbers
    assert active_numbers <= eligible
    assert active_numbers <= eligible_after
    assert recent_closed in eligible
    assert stale_closed not in eligible
    assert len(first) <= 130
    assert len(second) <= 130
    assert active_numbers <= set(first) | set(second)
    assert state["scheduler"]["completedActiveRounds"] >= 1


def test_failed_attempts_advance_round_and_new_active_joins_without_starvation() -> None:
    orders = active_orders(4)
    state: dict[str, Any] = {}
    hot = set(sorted(orders)[:2])

    first, _, _ = choose_targets(
        orders, state, set(), 2, 14, ACTIVE_STATUSES, hot, NOW, change_driven=False
    )
    # No successful timestamps are written: selection still represents an
    # attempted turn and must not pin the same failed OTs forever.
    orders.update(active_orders(5))
    second, active, _ = choose_targets(
        orders, state, set(), 2, 14, ACTIVE_STATUSES, hot, NOW, change_driven=False
    )
    third, _, _ = choose_targets(
        orders, state, set(), 2, 14, ACTIVE_STATUSES, hot, NOW, change_driven=False
    )

    assert set(orders) == active
    assert set(orders) <= set(first) | set(second) | set(third)


def test_forced_ot_is_additive_and_does_not_consume_active_round_capacity() -> None:
    orders = active_orders(4)
    forced = 999999
    state: dict[str, Any] = {}

    selected, active, eligible = choose_targets(
        orders,
        state,
        {forced},
        2,
        14,
        ACTIVE_STATUSES,
        set(),
        NOW,
    )

    assert forced in selected
    assert len(set(selected) & active) == 2
    assert len(selected) == 3
    assert set(orders) == active
    assert active | {forced} <= eligible


def test_full_catalog_reconciliation_has_no_status_filter_and_never_prunes(
    tmp_path: Path,
) -> None:
    catalog_file = tmp_path / "order_catalog.json"
    retained_number = 930001
    discovered_closed_number = 930002
    catalog_file.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "generatedAt": "2026-08-10T00:00:00Z",
                "discoveryCursorAt": "2026-08-10T00:00:00Z",
                "orders": [
                    {
                        "number": retained_number,
                        "status": "closed",
                        "updatedAt": "2022-01-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class FullCatalogClient:
        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=0.0)
            self.params: list[dict[str, Any]] = []

        def get(
            self,
            path: str,
            params: dict[str, Any] | None = None,
            timeout: float | None = None,
        ) -> CallResult:
            del timeout
            assert path == "work-orders"
            self.params.append(dict(params or {}))
            return call_result(
                [
                    {
                        "number": discovered_closed_number,
                        "status": "closed",
                        "updatedAt": "2026-08-11T12:00:00Z",
                    }
                ]
            )

    client = FullCatalogClient()
    sync(
        "opened,onTechnicalCompletion",
        0.0,
        10,
        "2026-08-11",
        "2026-08-11",
        mode="full",
        client=client,
        catalog_file=catalog_file,
        now=NOW,
    )

    catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    numbers = {int(order["number"]) for order in catalog["orders"]}
    assert client.params
    assert all("status" not in params for params in client.params)
    assert numbers == {retained_number, discovered_closed_number}


def test_ledger_keeps_old_and_new_history_idempotently_through_compaction(
    tmp_path: Path,
) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    historical = {
        "work_order_number": 940001,
        "tracking_id": 1,
        "tracking_date": "2021-01-01T00:00:00Z",
        "tracking_comment": "1",
        "tracking_observation_kind": "baseline",
    }
    current = {
        "work_order_number": 940001,
        "tracking_id": 2,
        "tracking_date": "2026-08-11T17:00:00Z",
        "tracking_comment": "2",
        "tracking_observation_kind": "live",
        "tracking_observed_from": "2026-08-11T16:55:00Z",
        "tracking_observed_at": "2026-08-11T17:00:00Z",
    }
    known: set[tuple[str, str]] = set()

    assert append_ledger(paths, [historical, current], known) == 2
    assert append_ledger(paths, [historical, current], known) == 0
    rows, rejected, _ = read_ledger(paths)
    assert rejected == 0
    compact_ledger(paths, rows)
    compacted, rejected_after, _ = read_ledger(paths)

    assert rejected_after == 0
    assert len(compacted) == 2
    assert {ledger_identity(row) for row in compacted} == {
        ledger_identity(historical),
        ledger_identity(current),
    }


def test_new_order_is_tracked_immediately_without_displacing_active_capacity_or_history(
    tmp_path: Path,
) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    existing = active_orders(4)
    paths.catalog.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "generatedAt": "2026-08-11T17:00:00Z",
                "discoveryCursorAt": "2026-08-11T17:00:00Z",
                "catalogScope": "historical_all_statuses",
                "catalogFullConfirmedAt": "2026-08-11T16:00:00Z",
                "orders": list(existing.values()),
            }
        ),
        encoding="utf-8",
    )
    historical = {
        "work_order_number": 910001,
        "tracking_id": 111,
        "tracking_date": "2021-01-01T05:00:00Z",
        "tracking_comment": "1",
        "tracking_observation_kind": "historical_bootstrap",
    }
    assert append_ledger(paths, [historical], set()) == 1

    class NewOrderClient:
        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=0.0)
            self.calls: list[str] = []

        def get(
            self,
            path: str,
            params: dict[str, Any] | None = None,
            timeout: float | None = None,
        ) -> CallResult:
            del params, timeout
            self.calls.append(path)
            if path == "work-orders":
                return call_result(
                    [
                        {
                            "number": 910005,
                            "status": "opened",
                            "updatedAt": "2026-08-11T17:04:30Z",
                        }
                    ]
                )
            if path == "work-orders/910005":
                return call_result(
                    {
                        "number": 910005,
                        "status": "opened",
                        "vehicleCode": "SYN005",
                        "updatedAt": "2026-08-11T17:04:30Z",
                    }
                )
            if path == "work-orders/910005/tracking":
                return call_result(
                    [
                        {
                            "id": 500,
                            "trackingDate": "2026-08-11T05:00:00Z",
                            "comment": "2",
                            "createdBy": None,
                            "files": None,
                        }
                    ]
                )
            if path == "work-orders/910001/tracking":
                return call_result(
                    [
                        {
                            "id": 111,
                            "trackingDate": "2021-01-01T05:00:00Z",
                            "comment": "1",
                            "createdBy": None,
                            "files": None,
                        },
                        {
                            "id": 112,
                            "trackingDate": "2026-08-11T05:00:00Z",
                            "comment": "2",
                            "createdBy": None,
                            "files": None,
                        },
                    ]
                )
            if path == "work-orders/910002/tracking":
                return call_result([])
            raise AssertionError(f"Unexpected synthetic endpoint: {path}")

    client = NewOrderClient()
    cycle = run_once(
        worker_args(2),
        client,
        paths=paths,
        clock=AdvancingClock(NOW),
        logger=OperationalLogger(stream=StringIO()),
    )

    tracking_calls = [path for path in client.calls if path.endswith("/tracking")]
    assert client.calls[:2] == ["work-orders", "work-orders/910005"]
    assert tracking_calls == [
        "work-orders/910005/tracking",
        "work-orders/910001/tracking",
        "work-orders/910002/tracking",
    ]
    assert cycle["activeOrders"] == 5
    assert cycle["activeOrdersPolled"] == 3
    assert cycle["activeTrackingBacklog"] == 2
    assert cycle["newTrackingRows"] == 2

    state = json.loads(paths.state.read_text(encoding="utf-8"))
    pending = set(state["scheduler"]["activePending"])
    assert 910005 not in pending
    assert pending == {910003, 910004}

    ledger, rejected, _ = read_ledger(paths)
    assert rejected == 0
    assert len(ledger) == 3
    identities = {ledger_identity(row) for row in ledger}
    assert ledger_identity(historical) in identities
    assert ("910001", "id:112") in identities
    assert ("910005", "id:500") in identities


def test_147_active_orders_publish_two_cycle_coverage_and_infeasible_five_minute_sla(
    tmp_path: Path,
) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    orders = active_orders(147)
    paths.catalog.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "generatedAt": "2026-08-11T17:00:00Z",
                "discoveryCursorAt": "2026-08-11T17:00:00Z",
                "catalogScope": "historical_all_statuses",
                "catalogFullConfirmedAt": "2026-08-11T16:00:00Z",
                "orders": list(orders.values()),
            }
        ),
        encoding="utf-8",
    )

    class CapacityClient:
        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=2.1)
            self.tracking_numbers: list[int] = []

        def get(
            self,
            path: str,
            params: dict[str, Any] | None = None,
            timeout: float | None = None,
        ) -> CallResult:
            del params, timeout
            if path == "work-orders":
                return call_result([])
            if path.endswith("/tracking"):
                self.tracking_numbers.append(int(path.split("/")[1]))
                return call_result([])
            raise AssertionError(f"Unexpected synthetic endpoint: {path}")

    client = CapacityClient()
    first = run_once(
        worker_args(130, change_driven=False),
        client,
        paths=paths,
        clock=AdvancingClock(NOW, step_seconds=2.1),
        logger=OperationalLogger(stream=StringIO()),
    )

    assert first["catalogOrders"] == 147
    assert first["catalogRetention"] == "indefinite"
    assert first["activeUniverse"] == 147
    assert first["activeOrdersPolled"] == 130
    assert first["activeTrackingBacklog"] == 17
    assert first["activeNeverAttempted"] == 17
    assert first["activeOrdersRemainingInRound"] == 17
    assert first["completedActiveCoverageRounds"] == 0
    assert first["fullSweepCycles"] == 2
    assert first["fullSweepSeconds"] == 600
    assert first["estimatedActiveCoverageCycles"] == 2
    assert first["estimatedActiveCoverageSeconds"] == 600
    assert first["theoreticalRequestCapacityPerCycle"] == 142
    # Reading all 147 actives in one 300 s cycle would need 148 requests against
    # a 30 req/min quota: still impossible, still published as a metric.
    assert first["activeSlaRequiredRequestsPerCycle"] == 148
    assert first["allActiveScheduledThisCycle"] is False
    assert first["activeTrackingBacklog"] == 17
    # Readiness is judged on coverage instead: one batch fits in one cycle and a
    # full round closes in 2 cycles (600 s), inside the 900 s coverage SLA.
    assert first["cycleRequestsRequired"] == 131
    assert first["coverageSlaFeasible"] is True
    assert first["activePollSlaFeasible"] is True
    # Not ready yet: 17 actives have never been polled at all.
    assert "active_never_polled" in first["healthReasons"]
    assert first["ready"] is False

    second = run_once(
        worker_args(130, change_driven=False),
        client,
        paths=paths,
        clock=AdvancingClock(NOW + timedelta(seconds=300), step_seconds=2.1),
        logger=OperationalLogger(stream=StringIO()),
    )

    assert len(client.tracking_numbers) == 147
    assert set(client.tracking_numbers) == set(orders)
    assert second["activeOrdersPolled"] == 17
    assert second["activeNeverAttempted"] == 0
    assert second["activeOrdersRemainingInRound"] == 0
    assert second["completedActiveCoverageRounds"] == 1
