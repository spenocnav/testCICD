"""Contract of change-driven scheduling.

The list endpoint reports ``updatedAt`` for 50 orders per request, so orders
that moved are known before spending a per-order tracking call.  These tests
pin the behaviour that follows from that: what moved is read in the cycle it
moved, the rotation survives as a safety net, and the assumption behind the
whole design is measured rather than trusted.
"""
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
from sync_order_catalog import sanitize_order  # noqa: E402
from tracking_label_worker import (  # noqa: E402
    WorkerPaths,
    administrative_closing_backlog,
    build_parser,
    choose_targets,
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


class AdvancingClock:
    def __init__(self, start: datetime, step_seconds: float = 1.0) -> None:
        self.current = start
        self.step = timedelta(seconds=step_seconds)

    def __call__(self) -> datetime:
        value = self.current
        self.current += self.step
        return value


def active_orders(count: int, first: int = 910001) -> dict[int, dict[str, Any]]:
    return {
        first + index: {
            "number": first + index,
            "status": "opened" if index % 2 else "onTechnicalCompletion",
            "updatedAt": "2021-01-01T00:00:00Z",
        }
        for index in range(count)
    }


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


def write_catalog(paths: WorkerPaths, orders: dict[int, dict[str, Any]]) -> None:
    paths.runtime.mkdir(parents=True, exist_ok=True)
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


def test_changed_active_order_is_polled_before_its_rotation_turn() -> None:
    orders = active_orders(100)
    numbers = sorted(orders)
    # The batch only covers a tenth of the universe, and the changed order sits
    # at the very tail of the deterministic rotation queue.
    late_in_queue = numbers[-1]
    state: dict[str, Any] = {}

    selected, active, _ = choose_targets(
        orders,
        state,
        set(),
        10,
        14,
        ACTIVE_STATUSES,
        {late_in_queue},
        NOW,
    )

    assert late_in_queue in selected
    assert len(active) == 100
    # It was covered for this round, so it does not repeat on the next cycle.
    following, _, _ = choose_targets(
        orders, state, set(), 10, 14, ACTIVE_STATUSES, set(), NOW
    )
    assert late_in_queue not in following


def test_rotation_floor_prevents_starvation_when_everything_changes() -> None:
    """A bulk update must not let churn monopolise the whole batch forever."""
    orders = active_orders(40)
    never_changes = sorted(orders)[-1]
    always_changes = set(sorted(orders)[:-1])
    state: dict[str, Any] = {}

    seen: set[int] = set()
    for _ in range(12):
        selected, _, _ = choose_targets(
            orders, state, set(), 8, 14, ACTIVE_STATUSES, always_changes, NOW
        )
        assert len(selected) <= 8
        seen.update(selected)

    assert never_changes in seen


def test_change_detection_does_not_relax_the_coverage_requirement(
    tmp_path: Path,
) -> None:
    """Measured on 2026-08-11: a new tracking row does NOT move ``updatedAt``.

    OT 5537 received four seguimientos while its ``updatedAt`` stayed frozen;
    in the same minute OT 5478 moved its ``updatedAt`` because
    ``maintenanceLabels`` changed.  So a label typed as a comment is invisible
    to the list, and only a per-order poll can find it.  Health must keep
    reporting the coverage gap instead of calling the cycle feasible.
    """
    paths = WorkerPaths.from_runtime(tmp_path)
    orders = active_orders(147)
    write_catalog(paths, orders)

    class QuietClient:
        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=2.1)
            self.tracking: list[int] = []

        def get(self, path: str, params: Any = None, timeout: Any = None) -> CallResult:
            del params, timeout
            if path == "work-orders":
                return call_result([])
            if path.endswith("/tracking"):
                self.tracking.append(int(path.split("/")[1]))
                return call_result([])
            raise AssertionError(path)

    cycle = run_once(
        worker_args(130),
        QuietClient(),
        paths=paths,
        clock=AdvancingClock(NOW, step_seconds=2.1),
        logger=OperationalLogger(stream=StringIO()),
    )

    assert cycle["changeDriven"] is True
    assert cycle["changedActiveOrders"] == 0
    assert cycle["changedTrackingBacklog"] == 0
    assert cycle["rotationOrdersPolled"] == 130
    # 17 active orders were not read this cycle. Any of them could carry a label
    # comment the list cannot report, so the gap is published as a metric...
    assert cycle["activeTrackingBacklog"] == 17
    assert cycle["allActiveScheduledThisCycle"] is False
    # ...but readiness is judged on coverage: a full round takes 2 cycles (600 s)
    # and fits inside the production default of 1.800 s.
    assert cycle["coverageSlaSeconds"] == 1800
    assert cycle["coverageSlaFeasible"] is True
    assert cycle["activePollSlaFeasible"] is True
    assert "active_tracking_backlog" not in cycle["healthReasons"]
    # Those 17 have never been read at all, which is still a real gap.
    assert "active_never_polled" in cycle["healthReasons"]
    assert cycle["ready"] is False


def test_an_active_order_left_stale_past_the_coverage_sla_breaks_readiness(
    tmp_path: Path,
) -> None:
    """The coverage SLA must still catch a genuinely abandoned order."""
    paths = WorkerPaths.from_runtime(tmp_path)
    orders = active_orders(4)
    write_catalog(paths, orders)
    # The rotation drains `sorted(active)`, so with a batch of one the highest
    # number stays queued and can be aged without being refreshed first.
    stale = sorted(orders)[-1]

    class OneOrderClient:
        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=0.0)
            self.polled: list[int] = []

        def get(self, path: str, params: Any = None, timeout: Any = None) -> CallResult:
            del params, timeout
            if path == "work-orders":
                return call_result([])
            if path.endswith("/tracking"):
                self.polled.append(int(path.split("/")[1]))
                return call_result([])
            raise AssertionError(path)

    client = OneOrderClient()
    args = worker_args(1)
    args.coverage_sla_seconds = 900
    run_once(
        args,
        client,
        paths=paths,
        clock=AdvancingClock(NOW),
        logger=OperationalLogger(stream=StringIO()),
    )
    # Give it a successful read far in the past: known, but long unattended.
    state = json.loads(paths.state.read_text(encoding="utf-8"))
    state["ot"][str(stale)] = {
        "last_checked_at": "2026-08-11T00:00:00+00:00",
        "last_successful_check_at": "2026-08-11T00:00:00+00:00",
        "last_status_code": 200,
    }
    paths.state.write_text(json.dumps(state), encoding="utf-8")

    later = run_once(
        args,
        client,
        paths=paths,
        clock=AdvancingClock(NOW + timedelta(seconds=300)),
        logger=OperationalLogger(stream=StringIO()),
    )

    assert stale not in client.polled
    assert later["ordersOutsideCoverageSla"] >= 1
    assert "active_coverage_sla_breached" in later["healthReasons"]
    assert later["ready"] is False


def test_rows_found_without_updatedat_change_are_counted(tmp_path: Path) -> None:
    """The design assumption is measured in production, not trusted."""
    paths = WorkerPaths.from_runtime(tmp_path)
    orders = active_orders(2)
    write_catalog(paths, orders)
    target = sorted(orders)[0]

    class SilentUpdateClient:
        """Adds tracking without ever moving ``updatedAt`` in the list."""

        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=0.0)
            self.rows: list[dict[str, Any]] = []

        def get(self, path: str, params: Any = None, timeout: Any = None) -> CallResult:
            del params, timeout
            if path == "work-orders":
                return call_result([])
            if path.endswith("/tracking"):
                if int(path.split("/")[1]) == target:
                    return call_result(list(self.rows))
                return call_result([])
            raise AssertionError(path)

    client = SilentUpdateClient()
    first = run_once(
        worker_args(10),
        client,
        paths=paths,
        clock=AdvancingClock(NOW),
        logger=OperationalLogger(stream=StringIO()),
    )
    assert first["trackingRowsFoundWithoutUpdatedAtChange"] == 0

    # A label lands, but the list keeps reporting the same updatedAt.
    client.rows.append(
        {
            "id": 900,
            "trackingDate": "2026-08-11T05:00:00Z",
            "comment": "3",
            "createdBy": None,
            "files": None,
        }
    )
    second = run_once(
        worker_args(10),
        client,
        paths=paths,
        clock=AdvancingClock(NOW + timedelta(seconds=300)),
        logger=OperationalLogger(stream=StringIO()),
    )

    assert second["trackingRowsFoundWithoutUpdatedAtChange"] == 1
    assert second["ordersWithUndetectedChanges"] == 1


def test_maintenance_labels_survive_catalog_sanitization() -> None:
    cleaned = sanitize_order(
        {
            "number": 5547,
            "status": "opened",
            "maintenanceLabels": ["En diagnóstico", "  En intervención  "],
            "comments": "texto libre que no debe persistirse",
        }
    )

    assert cleaned["maintenanceLabels"] == ["En diagnóstico", "En intervención"]
    assert "comments" not in cleaned


def test_administrative_closing_backlog_ages_unclosed_orders() -> None:
    orders = {
        1: {
            "number": 1,
            "status": "onTechnicalCompletion",
            "technicalCompletionDate": "2025-11-13T00:00:00Z",
            "costCenter": {"name": "Bav-La Arenosa"},
        },
        2: {
            "number": 2,
            "status": "onTechnicalCompletion",
            "technicalCompletionDate": "2026-08-08T00:00:00Z",
            "costCenter": {"name": "Emvarias"},
        },
        3: {
            "number": 3,
            "status": "onTechnicalCompletion",
            "costCenter": {"name": "Emvarias"},
        },
        4: {"number": 4, "status": "opened"},
        5: {"number": 5, "status": "closed"},
    }

    backlog = administrative_closing_backlog(orders, NOW)

    assert backlog["orders"] == 3
    assert backlog["withoutCompletionDate"] == 1
    assert backlog["over90Days"] == 1
    assert backlog["buckets"]["<=7d"] == 1
    assert backlog["buckets"]["<=365d"] == 1
    assert backlog["oldest"][0]["ot"] == 1
    assert backlog["oldest"][0]["cd"] == "Bav-La Arenosa"
    assert backlog["maxDays"] is not None and backlog["maxDays"] > 270


def test_both_label_channels_are_recorded_and_the_comment_wins(tmp_path: Path) -> None:
    """Comment is authoritative; the field is context and a discipline signal."""
    paths = WorkerPaths.from_runtime(tmp_path)
    orders = {
        910001: {
            "number": 910001,
            "status": "opened",
            "updatedAt": "2026-08-11T17:00:00Z",
            # Contradicts the comment below, exactly like real OT 5547.
            "maintenanceLabels": ["En diagnóstico"],
        },
        910002: {
            "number": 910002,
            "status": "opened",
            "updatedAt": "2026-08-11T17:00:00Z",
            "maintenanceLabels": ["Backup en préstamo"],
        },
    }
    write_catalog(paths, orders)

    class LabelClient:
        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=0.0)

        def get(self, path: str, params: Any = None, timeout: Any = None) -> CallResult:
            del params, timeout
            if path == "work-orders":
                return call_result([])
            if path.endswith("/tracking"):
                number = int(path.split("/")[1])
                comment = "9" if number == 910001 else "3"
                return call_result(
                    [
                        {
                            "id": number,
                            "trackingDate": "2026-08-11T05:00:00Z",
                            "comment": comment,
                            "createdBy": None,
                            "files": None,
                        }
                    ]
                )
            raise AssertionError(path)

    run_once(
        worker_args(10),
        LabelClient(),
        paths=paths,
        clock=AdvancingClock(NOW),
        logger=OperationalLogger(stream=StringIO()),
    )
    dashboard = json.loads(paths.dashboard.read_text(encoding="utf-8"))
    by_ot = {row["ot"]: row for row in dashboard["orders"]}

    # 910001: comment says 9 (En intervención), field says En diagnóstico.
    assert by_ot[910001]["currentLabelId"] == 9
    assert by_ot[910001]["fieldLabelId"] == 3
    assert by_ot[910001]["labelSourceAgreement"] == "disagree"
    # 910002: comment says 3, field carries a value outside the ten labels.
    assert by_ot[910002]["currentLabelId"] == 3
    assert by_ot[910002]["fieldLabelId"] is None
    assert by_ot[910002]["fieldLabelUncontrolled"] is True
    assert by_ot[910002]["labelSourceAgreement"] == "comment_only_uncontrolled_field"

    agreement = dashboard["operations"]["labelSourceAgreement"]
    assert agreement["authoritativeSource"] == "tracking_comment"
    assert agreement["counts"]["disagree"] == 1
    assert agreement["disagreementRate"] == 1.0
    assert agreement["ordersWithUncontrolledFieldLabel"] == 1
    assert agreement["disagreements"][0]["ot"] == 910001


def test_redacted_seed_rows_do_not_count_as_unrecognized_labels(tmp_path: Path) -> None:
    """Measured on the real runtime: 9.385 of 9.432 ledger rows are seed evidence.

    Counting them as unrecognized reported a 0,06% recognition rate and hid the
    figure that actually matters: 6 labels out of 47 real comments.
    """
    from tracking_label_worker import append_ledger, build_dashboard, read_ledger

    paths = WorkerPaths.from_runtime(tmp_path)
    rows = [
        {
            "work_order_number": 910001,
            "tracking_id": index,
            "tracking_date": "2025-01-01T05:00:00Z",
            "tracking_comment": None,
            "tracking_observation_kind": "historical_certified_seed",
        }
        for index in range(1, 51)
    ]
    rows.append(
        {
            "work_order_number": 910001,
            "tracking_id": 900,
            "tracking_date": "2026-08-11T05:00:00Z",
            "tracking_comment": "Engrase general",
            "tracking_observation_kind": "live",
            "tracking_observed_from": "2026-08-11T16:55:00Z",
            "tracking_observed_at": "2026-08-11T17:00:00Z",
        }
    )
    rows.append(
        {
            "work_order_number": 910001,
            "tracking_id": 901,
            "tracking_date": "2026-08-11T05:00:00Z",
            "tracking_comment": "3",
            "tracking_observation_kind": "live",
            "tracking_observed_from": "2026-08-11T16:55:00Z",
            "tracking_observed_at": "2026-08-11T17:00:00Z",
        }
    )
    append_ledger(paths, rows, set())
    ledger, _, _ = read_ledger(paths)

    dashboard = build_dashboard(ledger, {}, {"pollSlaSeconds": 300}, None)
    quality = dashboard["quality"]

    assert quality["trackingRows"] == 52
    assert quality["redactedEvidenceRows"] == 50
    assert quality["labelCandidateRows"] == 2
    assert quality["recognizedEvents"] == 1
    assert quality["unrecognizedRows"] == 1


def _event(rows: list[dict[str, Any]], sla_minutes: float = 5.0) -> dict[str, Any]:
    import pandas as pd

    from build_label_history import build_events

    events, _, stats = build_events(pd.DataFrame(rows), window_sla_minutes=sla_minutes)
    assert len(events) == 1
    return {**events.iloc[0].to_dict(), "_stats": stats}


def test_wide_window_on_an_earlier_day_falls_back_to_tracking_date() -> None:
    """Observed on the real cycle: a 19-hour window sealed the hour as "now".

    The five labels landed on 11/08 but were read on 12/08 after a pause, so
    the ceiling claimed 12/08 09:00 and every duration collapsed to zero.
    """
    row = {
        "work_order_number": 5546,
        "tracking_id": 1,
        "tracking_date": "2026-08-11T05:00:00Z",
        "tracking_comment": "3",
        "tracking_observation_kind": "live_new_order",
        "tracking_observed_from": "2026-08-11T19:08:57Z",
        "tracking_observed_at": "2026-08-12T14:00:52Z",
    }

    event = _event([row])

    assert event["event_at_source"] == "tracking_date_baseline"
    assert event["event_time_quality"] == "historical_date"
    assert event["event_at_display"] == "11/08/2026"
    assert event["_stats"]["stale_window_downgrades"] == 1


def test_a_tight_window_across_midnight_keeps_its_precision() -> None:
    """The date differs but nothing was lost: 3 minutes is still 3 minutes."""
    row = {
        "work_order_number": 5546,
        "tracking_id": 1,
        "tracking_date": "2026-08-11T05:00:00Z",
        "tracking_comment": "3",
        "tracking_observation_kind": "live",
        # 23:58 -> 00:01 Bogotá, straddling the local date boundary.
        "tracking_observed_from": "2026-08-12T04:58:00Z",
        "tracking_observed_at": "2026-08-12T05:01:00Z",
    }

    event = _event([row])

    assert event["event_at_source"] == "observation_window_end"
    assert event["event_time_quality"] == "live_window"
    assert event["_stats"]["stale_window_downgrades"] == 0


def test_same_day_wide_window_keeps_the_ceiling() -> None:
    """A wide window alone is not proof the event happened on another day."""
    row = {
        "work_order_number": 5546,
        "tracking_id": 1,
        "tracking_date": "2026-08-12T05:00:00Z",
        "tracking_comment": "3",
        "tracking_observation_kind": "live",
        "tracking_observed_from": "2026-08-12T12:00:00Z",
        "tracking_observed_at": "2026-08-12T14:00:00Z",
    }

    event = _event([row])

    assert event["event_at_source"] == "observation_window_end"
    assert event["_stats"]["stale_window_downgrades"] == 0


def test_mixed_timestamp_precision_does_not_break_transitions() -> None:
    """Regression: a live cycle died in publish with ValueError.

    `event_at` mixes sources by design — a window ceiling carries microseconds,
    a trackingDate baseline does not.  pandas inferred the format from the first
    row and raised on the rest, so the whole cycle failed the moment a live
    label coexisted with a historical one on the same order.
    """
    import pandas as pd

    from build_label_history import build_events, build_transitions

    rows = [
        {
            "work_order_number": 5553,
            "tracking_id": 1,
            "tracking_date": "2026-08-11T05:00:00Z",
            "tracking_comment": "3",
            "tracking_observation_kind": "historical_bootstrap",
        },
        {
            "work_order_number": 5553,
            "tracking_id": 2,
            "tracking_date": "2026-08-12T05:00:00Z",
            "tracking_comment": "6",
            "tracking_observation_kind": "live",
            "tracking_observed_from": "2026-08-12T14:00:46.701591+00:00",
            "tracking_observed_at": "2026-08-12T16:18:56.587994+00:00",
        },
    ]
    events, _, _ = build_events(pd.DataFrame(rows))
    assert set(events["event_at_source"]) == {
        "tracking_date_baseline",
        "observation_window_end",
    }
    # Both precisions present: the exact condition that used to raise.
    assert any("." in value for value in events["event_at"])
    assert any("." not in value for value in events["event_at"])

    transitions, durations = build_transitions(events)

    assert len(transitions) == 2
    segment = durations.iloc[0]
    assert segment["label_id"] == 3
    assert segment["duration_hours"] > 0
    # Day precision on one end, live window on the other: never "precise".
    assert segment["time_quality"] == "mixed"
    assert bool(segment["duration_is_precise"]) is False


def test_bootstrap_status_recovers_after_a_new_order_is_polled(tmp_path: Path) -> None:
    """Regression: the flag latched to `incomplete` and never came back.

    Any newly discovered OT leaves `pending` at 1 for the cycle that finds it.
    With a one-way latch that single event pinned readiness to false forever,
    even though a new OT receives its whole tracking history on its first poll.
    """
    paths = WorkerPaths.from_runtime(tmp_path)
    orders = active_orders(2)
    write_catalog(paths, orders)
    state = {
        "schemaVersion": 2,
        "ot": {
            str(number): {"historical_bootstrap_complete": True} for number in orders
        },
        "bootstrapAllTracking": {"status": "complete", "totalOrders": 2},
    }
    paths.state.write_text(json.dumps(state), encoding="utf-8")

    class DiscoveringClient:
        """Reveals one brand-new OT on the first cycle and nothing after."""

        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=0.0)
            self.cycles = 0

        def get(self, path: str, params: Any = None, timeout: Any = None) -> CallResult:
            del params, timeout
            if path == "work-orders":
                self.cycles += 1
                if self.cycles == 1:
                    return call_result(
                        [{"number": 999001, "status": "opened", "updatedAt": "2026-08-11T17:04:00Z"}]
                    )
                return call_result([])
            if path == "work-orders/999001":
                return call_result(
                    {"number": 999001, "status": "opened", "updatedAt": "2026-08-11T17:04:00Z"}
                )
            if path.endswith("/tracking"):
                return call_result([])
            raise AssertionError(path)

    client = DiscoveringClient()
    first = run_once(
        worker_args(10),
        client,
        paths=paths,
        clock=AdvancingClock(NOW),
        logger=OperationalLogger(stream=StringIO()),
    )
    # The new OT was polled in the same cycle that discovered it.
    assert first["catalogOrders"] == 3
    assert first["historicalBootstrapPendingOrders"] == 0
    assert first["historicalBootstrapStatus"] == "complete"
    assert "historical_bootstrap_incomplete" not in first["healthReasons"]

    second = run_once(
        worker_args(10),
        client,
        paths=paths,
        clock=AdvancingClock(NOW + timedelta(seconds=300)),
        logger=OperationalLogger(stream=StringIO()),
    )

    assert second["historicalBootstrapStatus"] == "complete"
    assert "historical_bootstrap_incomplete" not in second["healthReasons"]


def test_background_order_windows_do_not_break_active_readiness(tmp_path: Path) -> None:
    """Regression: readiness flapped on every round-closing cycle.

    Cycles that finish a rotation round have spare capacity and fill it with
    closed orders. Those are polled opportunistically, so their observation
    windows are naturally long — and counting them against the coverage SLA,
    which is a promise about ACTIVE orders only, turned readiness off every
    third cycle for no operational reason.
    """
    paths = WorkerPaths.from_runtime(tmp_path)
    active = 910001
    closed = 920001
    orders = {
        active: {"number": active, "status": "opened", "updatedAt": "2026-08-12T16:00:00Z"},
        closed: {"number": closed, "status": "closed", "updatedAt": "2026-08-12T16:00:00Z"},
    }
    write_catalog(paths, orders)
    # Both were read before; the closed one a very long time ago.
    state = {
        "schemaVersion": 2,
        "ot": {
            str(active): {
                "last_checked_at": "2026-08-12T16:55:00+00:00",
                "last_successful_check_at": "2026-08-12T16:55:00+00:00",
                "historical_bootstrap_complete": True,
            },
            str(closed): {
                "last_checked_at": "2026-08-11T00:00:00+00:00",
                "last_successful_check_at": "2026-08-11T00:00:00+00:00",
                "historical_bootstrap_complete": True,
            },
        },
        "bootstrapAllTracking": {"status": "complete"},
    }
    paths.state.write_text(json.dumps(state), encoding="utf-8")

    class BothClient:
        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=0.0)
            self.polled: list[int] = []

        def get(self, path: str, params: Any = None, timeout: Any = None) -> CallResult:
            del params, timeout
            if path == "work-orders":
                return call_result([])
            if path.endswith("/tracking"):
                self.polled.append(int(path.split("/")[1]))
                return call_result([])
            raise AssertionError(path)

    client = BothClient()
    args = worker_args(10)
    args.coverage_sla_seconds = 1800
    cycle = run_once(
        args,
        client,
        paths=paths,
        clock=AdvancingClock(NOW + timedelta(seconds=3600)),
        logger=OperationalLogger(stream=StringIO()),
    )

    # The closed order was polled as background fill, with a window far beyond
    # the coverage SLA...
    assert closed in client.polled
    assert cycle["backgroundObservationWindows"] == 1
    assert cycle["maxBackgroundObservationWindowMinutes"] > 1800 / 60
    # ...yet it must not count against the active-order promise.
    assert cycle["lateObservationWindows"] == 0
    assert "observation_windows_over_sla" not in cycle["healthReasons"]


def test_backlog_is_empty_without_catalog() -> None:
    assert administrative_closing_backlog(None, NOW)["orders"] == 0
    assert administrative_closing_backlog({}, NOW)["medianDays"] is None


def test_operations_keys_exist_before_the_first_label_event(tmp_path: Path) -> None:
    """The current real state of this deployment: tracking rows, zero labels."""
    paths = WorkerPaths.from_runtime(tmp_path)
    orders = {
        910001: {
            "number": 910001,
            "status": "onTechnicalCompletion",
            "updatedAt": "2026-08-11T17:00:00Z",
            "technicalCompletionDate": "2026-05-01T00:00:00Z",
        }
    }
    write_catalog(paths, orders)

    class FreeTextClient:
        def __init__(self) -> None:
            self.config = SimpleNamespace(min_request_interval_seconds=0.0)

        def get(self, path: str, params: Any = None, timeout: Any = None) -> CallResult:
            del params, timeout
            if path == "work-orders":
                return call_result([])
            if path.endswith("/tracking"):
                return call_result(
                    [
                        {
                            "id": 1,
                            "trackingDate": "2026-08-11T05:00:00Z",
                            "comment": "Engrase general",
                            "createdBy": None,
                            "files": None,
                        }
                    ]
                )
            raise AssertionError(path)

    run_once(
        worker_args(10),
        FreeTextClient(),
        paths=paths,
        clock=AdvancingClock(NOW),
        logger=OperationalLogger(stream=StringIO()),
    )
    dashboard = json.loads(paths.dashboard.read_text(encoding="utf-8"))

    assert dashboard["kpis"]["labelEvents"] == 0
    operations = dashboard["operations"]
    assert operations["labelSourceAgreement"]["authoritativeSource"] == "tracking_comment"
    assert operations["labelSourceAgreement"]["comparableOrders"] == 0
    assert operations["administrativeClosingBacklog"]["orders"] == 1
