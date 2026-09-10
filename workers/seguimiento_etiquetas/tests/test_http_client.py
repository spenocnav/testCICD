from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from cloudfleet_api import ClientConfig, CloudfleetClient  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.current = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


class FakeResponse:
    def __init__(self, status: int, *, headers: dict[str, str] | None = None, data: Any = None) -> None:
        self.status_code = status
        self.headers = headers or {}
        self.url = "https://cloudfleet.invalid/api/v1/work-orders"
        self.text = json.dumps([] if data is None else data)

    def json(self) -> Any:
        return json.loads(self.text)


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self.headers: dict[str, str] = {}
        self.responses = list(responses)

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> FakeResponse:
        del url, params, timeout
        return self.responses.pop(0)

    def close(self) -> None:
        pass


def config(**overrides: Any) -> ClientConfig:
    values = {
        "api_key": "fake-for-unit-test",
        "min_request_interval_seconds": 0.0,
        "max_retries": 1,
        "backoff_base_seconds": 1.0,
        "rate_limit_fallback_seconds": 60.0,
    }
    values.update(overrides)
    return ClientConfig(**values)


def test_429_without_reset_header_waits_full_fallback_before_retry() -> None:
    clock = FakeClock()
    session = FakeSession(FakeResponse(429), FakeResponse(200))
    client = CloudfleetClient(
        config(),
        session=session,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    result = client.get("work-orders")

    assert result.ok is True
    assert result.attempts == 2
    assert clock.sleeps == [60.0]


def test_remaining_one_blocks_next_request_preemptively() -> None:
    clock = FakeClock()
    session = FakeSession(
        FakeResponse(200, headers={"X-RateLimit-Remaining": "1"}),
        FakeResponse(200, headers={"X-RateLimit-Remaining": "29"}),
    )
    client = CloudfleetClient(
        config(max_retries=0),
        session=session,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    first = client.get("work-orders")
    second = client.get("work-orders")

    assert first.ok and second.ok
    assert clock.sleeps == [60.25]


def test_lowercase_rate_limit_headers_are_parsed_and_pause_next_request() -> None:
    clock = FakeClock()
    session = FakeSession(
        FakeResponse(
            200,
            headers={
                "x-ratelimit-limit": "30",
                "x-ratelimit-remaining": "1",
                "x-ratelimit-reset": "12",
            },
        ),
        FakeResponse(
            200,
            headers={
                "x-ratelimit-limit": "30",
                "x-ratelimit-remaining": "29",
                "x-ratelimit-reset": "60",
            },
        ),
    )
    client = CloudfleetClient(
        config(max_retries=0),
        session=session,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    first = client.get("work-orders")
    second = client.get("work-orders")

    assert first.ok and second.ok
    assert first.rate_limit_remaining == 1
    assert first.rate_limit_reset == 12
    assert first.headers is not None
    assert first.headers["x-ratelimit-limit"] == "30"
    assert clock.sleeps == [12.25]


def test_lowercase_429_reset_header_controls_retry_wait() -> None:
    clock = FakeClock()
    session = FakeSession(
        FakeResponse(
            429,
            headers={
                "x-ratelimit-limit": "30",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "18",
            },
        ),
        FakeResponse(
            200,
            headers={
                "x-ratelimit-limit": "30",
                "x-ratelimit-remaining": "29",
                "x-ratelimit-reset": "60",
            },
        ),
    )
    client = CloudfleetClient(
        config(max_retries=1),
        session=session,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    result = client.get("work-orders")

    assert result.ok is True
    assert result.attempts == 2
    assert clock.sleeps == [18.25]
