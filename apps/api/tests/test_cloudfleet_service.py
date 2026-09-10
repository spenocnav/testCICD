from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.services import cloudfleet_service


@pytest.fixture(autouse=True)
def _configured_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cloudfleet_service.settings, "cloudfleet_api_key", "test-key")
    cloudfleet_service._last_request_monotonic = None
    cloudfleet_service._rate_limit_until_monotonic = None

    async def no_throttle() -> None:
        return None

    monkeypatch.setattr(cloudfleet_service, "_throttle", no_throttle)


@pytest.mark.asyncio
async def test_get_all_pages_retries_transient_5xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = iter([500, 502, 200])
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        status = next(statuses)
        return httpx.Response(
            status,
            json=[] if status == 200 else {"error": {"message": "temporary"}},
            request=request,
        )

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(cloudfleet_service.asyncio, "sleep", fake_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await cloudfleet_service.get_all_pages("vehicles/", client=client)

    assert result == []
    assert calls == 3
    assert sleeps == [2.0, 4.0]


@pytest.mark.asyncio
async def test_get_all_pages_stops_after_three_5xx_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            500,
            json={"error": {"message": "still broken"}},
            request=request,
        )

    async def no_wait(_: float) -> None:
        return None

    monkeypatch.setattr(cloudfleet_service.asyncio, "sleep", no_wait)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            cloudfleet_service.CloudfleetError,
            match=r"Cloudfleet respondió 500",
        ):
            await cloudfleet_service.get_all_pages("vehicles/", client=client)

    assert calls == 4


@pytest.mark.asyncio
async def test_get_all_pages_retries_transient_network_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json=[], request=request)

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(cloudfleet_service.asyncio, "sleep", fake_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await cloudfleet_service.get_all_pages("vehicles/", client=client)

    assert result == []
    assert calls == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_get_all_pages_does_not_retry_non_transient_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "unauthorized"}, request=request)

    async def unexpected_sleep(_: float) -> None:
        pytest.fail("401 no debe reintentarse")

    monkeypatch.setattr(cloudfleet_service.asyncio, "sleep", unexpected_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            cloudfleet_service.CloudfleetError,
            match=r"Cloudfleet respondió 401",
        ):
            await cloudfleet_service.get_all_pages("vehicles/", client=client)

    assert calls == 1


@pytest.mark.asyncio
async def test_get_all_pages_keeps_filters_when_next_page_header_drops_them() -> None:
    """Regresión del 409 "Must specify at least one date range" (2026-09-01).

    CloudFleet emitió un ``X-NextPage`` sin el rango de fechas de la petición
    original. Seguirlo tal cual perdía los filtros y el proveedor rechazaba la
    página; el sync diario falló seis veces así.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params.get("page", "1"))
        if "dateToExecuteFrom" not in request.url.params:
            return httpx.Response(
                409,
                json={"error": {"message": "Must specify at least one date range"}},
                request=request,
            )
        if page == 1:
            return httpx.Response(
                200,
                json=[{"id": n} for n in range(1, 51)],
                headers={
                    "X-NextPage": "https://fleet.cloudfleet.com/api/v1/maintenance-schedules/?page=2&pageSize=50"
                },
                request=request,
            )
        return httpx.Response(200, json=[{"id": 51}], request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await cloudfleet_service.get_all_pages(
            "maintenance-schedules/",
            params={
                "dateToExecuteFrom": "2024-09-01T00:00:00Z",
                "dateToExecuteTo": "2025-01-31T23:59:59Z",
            },
            client=client,
        )

    assert [r["id"] for r in result] == list(range(1, 52))
    assert len(seen) == 2
    second = seen[1].url.params
    assert second["page"] == "2"
    assert second["pageSize"] == "50"
    assert second["dateToExecuteFrom"] == "2024-09-01T00:00:00Z"
    assert second["dateToExecuteTo"] == "2025-01-31T23:59:59Z"


@pytest.mark.asyncio
async def test_get_all_pages_never_follows_next_page_header_to_another_host() -> None:
    """SEC-029: el Bearer no viaja al host que diga el header, solo al configurado."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if int(request.url.params.get("page", "1")) == 1:
            return httpx.Response(
                200,
                json=[{"id": n} for n in range(1, 51)],
                headers={"X-NextPage": "https://evil.example/steal?page=7"},
                request=request,
            )
        return httpx.Response(200, json=[], request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await cloudfleet_service.get_all_pages("vehicles/", client=client)

    assert len(result) == 50
    assert {r.url.host for r in seen} == {"fleet.cloudfleet.com"}
    assert seen[1].url.path == "/api/v1/vehicles/"
    # Del header solo se toma el número de página.
    assert seen[1].url.params["page"] == "7"


@pytest.mark.asyncio
async def test_get_all_pages_treats_404_after_first_page_as_end_of_list() -> None:
    """La página siguiente a la última llena responde 404 en CloudFleet (2026-09-01)."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        if page == 1:
            return httpx.Response(
                200,
                json=[{"id": n} for n in range(1, 51)],
                headers={"X-NextPage": "https://fleet.cloudfleet.com/api/v1/vehicles/?page=2"},
                request=request,
            )
        return httpx.Response(
            404,
            json={"error": {"message": "No Vehicles found with the specified filters"}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await cloudfleet_service.get_all_pages("vehicles/", client=client)

    assert len(result) == 50


@pytest.mark.asyncio
async def test_get_all_pages_first_page_404_is_still_an_error_unless_declared() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "not found"}}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(cloudfleet_service.CloudfleetError):
            await cloudfleet_service.get_all_pages("vehicles/", client=client)
        assert (
            await cloudfleet_service.get_all_pages("issues/", client=client, empty_on_404=True)
            == []
        )


def test_next_page_number_only_trusts_a_greater_page() -> None:
    assert cloudfleet_service._next_page_number("https://x/?page=5", 4) == 5
    assert cloudfleet_service._next_page_number("https://x/?page=4", 4) == 5
    assert cloudfleet_service._next_page_number("https://x/?page=abc", 4) == 5
    assert cloudfleet_service._next_page_number("https://x/", 4) == 5


def test_build_meter_payload_normalizes_date_and_omits_optional_fields() -> None:
    payload = cloudfleet_service.build_meter_payload(
        vehicle_code="ABC236",
        meter_date=datetime(2026, 8, 12, 14, 30, tzinfo=UTC),
        meter_type="distance",
        meter_value=Decimal("986275.25"),
    )

    assert payload == {
        "vehicleCode": "ABC236",
        "meterDate": "2026-08-12T14:30:00Z",
        "meterType": "distance",
        "meterValue": 986275.25,
    }


@pytest.mark.asyncio
async def test_create_meter_requires_cloudfleet_204() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["json"] = json.loads(request.content)
        return httpx.Response(204, request=request)

    payload = cloudfleet_service.build_meter_payload(
        vehicle_code="ABC236",
        meter_date=datetime(2026, 8, 12, 14, 30, tzinfo=UTC),
        meter_type="hours",
        meter_value=Decimal("1234.5"),
        source_code="GPS",
        comment="Lectura automática",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await cloudfleet_service.create_meter(payload, client=client)

    assert captured["url"] == "https://fleet.cloudfleet.com/api/v1/meters/"
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["headers"]["content-type"] == "application/json; charset=utf-8"
    assert captured["json"]["sourceCode"] == "GPS"


@pytest.mark.asyncio
async def test_create_meter_marks_5xx_as_ambiguous_without_retrying() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, text="temporary", request=request)

    payload = cloudfleet_service.build_meter_payload(
        vehicle_code="ABC236",
        meter_date=datetime(2026, 8, 12, 14, 30, tzinfo=UTC),
        meter_type="distance",
        meter_value=Decimal("1"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(cloudfleet_service.CloudfleetMeterError) as exc_info:
            await cloudfleet_service.create_meter(payload, client=client)

    assert calls == 1
    assert exc_info.value.uncertain is True


@pytest.mark.asyncio
async def test_create_meter_defers_following_requests_after_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deferred: list[httpx.Response] = []

    async def record_defer(response: httpx.Response) -> None:
        deferred.append(response)

    monkeypatch.setattr(cloudfleet_service, "_defer_after_rate_limit", record_defer)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited", request=request)

    payload = cloudfleet_service.build_meter_payload(
        vehicle_code="ABC236",
        meter_date=datetime(2026, 8, 12, 14, 30, tzinfo=UTC),
        meter_type="distance",
        meter_value=Decimal("1"),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(cloudfleet_service.CloudfleetMeterError) as exc_info:
            await cloudfleet_service.create_meter(payload, client=client)

    assert exc_info.value.uncertain is True
    assert len(deferred) == 1
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_observe_rate_limit_pauses_after_budget_is_exhausted() -> None:
    request = httpx.Request("GET", "https://fleet.cloudfleet.com/api/v1/vehicles/")
    response = httpx.Response(
        200,
        headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "12"},
        request=request,
    )

    await cloudfleet_service._observe_rate_limit(response)

    assert cloudfleet_service._rate_limit_until_monotonic is not None
