from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from cloudfleet_api import ClientConfig, CloudfleetClient  # noqa: E402
from build_label_history import build_transitions  # noqa: E402
from tracking_label_worker import (  # noqa: E402
    WorkerPaths,
    compact_ledger,
    read_ledger,
    seed_ledger_from_parquet,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.headers = headers or {}
        self.url = "https://cloudfleet.invalid/api/v1/work-orders"
        self.text = '{"ok": true}' if status < 400 else '{"error": "rate"}'

    def json(self):
        return {"ok": self.status_code < 400}


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.headers = {}
        self.responses = responses

    def get(self, *args, **kwargs):
        del args, kwargs
        return self.responses.pop(0)

    def close(self) -> None:
        return None


def test_429_without_headers_waits_safe_fallback_instead_of_short_backoff() -> None:
    clock = FakeClock()
    session = FakeSession([FakeResponse(429), FakeResponse(200)])
    client = CloudfleetClient(
        ClientConfig(
            api_key="secret-for-test",
            min_request_interval_seconds=0,
            max_retries=1,
            rate_limit_fallback_seconds=60,
        ),
        session=session,  # type: ignore[arg-type]
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    result = client.get("work-orders")

    assert result.ok is True
    assert result.attempts == 2
    assert clock.sleeps == [60]


def test_low_remaining_blocks_next_request_until_reset() -> None:
    clock = FakeClock()
    session = FakeSession(
        [
            FakeResponse(200, {"X-RateLimit-Remaining": "1", "X-RateLimit-Reset": "12"}),
            FakeResponse(200, {"X-RateLimit-Remaining": "20"}),
        ]
    )
    client = CloudfleetClient(
        ClientConfig(api_key="secret-for-test", min_request_interval_seconds=0),
        session=session,  # type: ignore[arg-type]
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert client.get("work-orders").ok is True
    assert client.get("work-orders").ok is True
    assert clock.sleeps == [12.25]


def test_historical_seed_preserves_identical_null_id_rows(tmp_path: Path) -> None:
    seed_path = tmp_path / "seed.parquet"
    row = {
        "work_order_number": 44,
        "tracking_id": None,
        "tracking_date": "2026-08-11T05:00:00Z",
        "tracking_comment": "9",
        "created_by_id": 7,
    }
    pd.DataFrame([row, dict(row)]).to_parquet(seed_path, index=False)
    paths = WorkerPaths.from_runtime(tmp_path / "runtime")
    state: dict = {}

    assert seed_ledger_from_parquet(paths, state, seed_path, []) == 2
    stored, rejected, _ = read_ledger(paths)
    assert rejected == 0
    assert len(stored) == 2
    assert stored[0]["tracking_key"] != stored[1]["tracking_key"]


def test_mixed_string_and_integer_ids_share_one_transition_stream() -> None:
    events = pd.DataFrame(
        [
            {
                "work_order_number": 9001,
                "tracking_id": 1,
                "label_id": 3,
                "label_name": "En diagnóstico",
                "event_at": "2026-08-11T10:00:00Z",
                "event_time_quality": "live_window",
            },
            {
                "work_order_number": "9001",
                "tracking_id": "event-2",
                "label_id": 9,
                "label_name": "En intervención",
                "event_at": "2026-08-11T11:00:00Z",
                "event_time_quality": "live_window",
            },
        ]
    )

    transitions, durations = build_transitions(events)

    assert len(transitions) == 2
    assert transitions.iloc[1]["previous_label_id"] == 3
    assert durations.iloc[0]["duration_hours"] == 1.0


def test_compaction_repairs_truncated_jsonl_tail(tmp_path: Path) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    paths.ledger.write_text(
        '{"work_order_number":1,"tracking_id":2,"tracking_comment":"9"}\n{"truncated"',
        encoding="utf-8",
    )

    rows, rejected, needs_compaction = read_ledger(paths)
    assert rejected == 1
    assert needs_compaction is True
    compact_ledger(paths, rows)

    repaired, rejected_after, needs_after = read_ledger(paths)
    assert len(repaired) == 1
    assert rejected_after == 0
    assert needs_after is False
