"""Cliente de geocodificación inversa contra MyGeotab (informe de Ubicaciones).

No toca la base: todo se ejerce con un transporte httpx simulado.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services import geotab_service as svc


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_authenticate_usa_el_servidor_devuelto_por_geotab() -> None:
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((str(request.url), json.loads(request.content)))
        return httpx.Response(
            200,
            json={
                "result": {
                    "credentials": {"database": "db", "sessionId": "s", "userName": "u"},
                    "path": "my3.geotab.com",
                }
            },
        )

    async with _client(handler) as client:
        session = await svc.authenticate(client, database="db", username="u", password="secreto")

    assert session.server == "my3.geotab.com"
    assert session.credentials["sessionId"] == "s"
    url, body = calls[0]
    assert url.endswith("/apiv1")
    assert body["method"] == "Authenticate"


@pytest.mark.asyncio
async def test_get_addresses_manda_x_lon_y_lat_y_conserva_el_orden() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured["params"] = payload["params"]
        return httpx.Response(
            200,
            json={
                "result": [
                    {"formattedAddress": "Calle 1"},
                    {"formattedAddress": ""},
                ]
            },
        )

    session = svc.GeotabSession(server="my.geotab.com", credentials={"sessionId": "s"})
    async with _client(handler) as client:
        addresses = await svc.get_addresses(client, session, [(4.6, -74.1), (4.7, -74.2)])

    assert addresses == ["Calle 1", None]
    params = captured["params"]
    assert params["coordinates"] == [
        {"x": -74.1, "y": 4.6},
        {"x": -74.2, "y": 4.7},
    ]


@pytest.mark.asyncio
async def test_get_addresses_rellena_con_none_si_faltan_resultados() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": [{"formattedAddress": "Calle 1"}]})

    session = svc.GeotabSession(server="my.geotab.com", credentials={})
    async with _client(handler) as client:
        addresses = await svc.get_addresses(client, session, [(1.0, 2.0), (3.0, 4.0)])

    assert addresses == ["Calle 1", None]


@pytest.mark.asyncio
async def test_resolve_addresses_trocea_en_lotes() -> None:
    """No manda todas las coordenadas de golpe: una petición por lote."""
    batches: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        coordinates = payload["params"]["coordinates"]
        batches.append(len(coordinates))
        return httpx.Response(
            200,
            json={"result": [{"formattedAddress": "X"} for _ in coordinates]},
        )

    session = svc.GeotabSession(server="my.geotab.com", credentials={})
    coordinates = [(float(i), float(i)) for i in range(5)]
    async with _client(handler) as client:
        addresses = await svc.resolve_addresses_in_batches(
            client, session, coordinates, batch_size=2
        )

    assert addresses == ["X"] * 5
    assert batches == [2, 2, 1]


@pytest.mark.asyncio
async def test_error_de_geotab_se_traduce_sin_filtrar_credenciales() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"message": "InvalidUserException para u@x.com"}})

    async with _client(handler) as client:
        with pytest.raises(svc.GeotabError) as excinfo:
            await svc.authenticate(client, database="db", username="u", password="p")

    message = str(excinfo.value)
    assert "u@x.com" not in message
    assert message != "p"
    assert "MyGeotab" in message


@pytest.mark.asyncio
async def test_http_error_persistente_lanza_error_controlado() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    session = svc.GeotabSession(server="my.geotab.com", credentials={})
    async with _client(handler) as client:
        with pytest.raises(svc.GeotabError):
            await svc.get_addresses(client, session, [(1.0, 2.0)])


@pytest.mark.asyncio
async def test_sin_coordenadas_no_llama_al_proveedor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe pedir direcciones")

    session = svc.GeotabSession(server="my.geotab.com", credentials={})
    async with _client(handler) as client:
        assert await svc.resolve_addresses_in_batches(client, session, []) == []


@pytest.mark.asyncio
async def test_get_log_records_pide_la_ventana_y_ordena_por_tiempo() -> None:
    from datetime import UTC, datetime

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured["params"] = payload["params"]
        return httpx.Response(
            200,
            json={
                "result": [
                    {
                        "dateTime": "2026-01-01T10:05:00.000Z",
                        "latitude": 4.7,
                        "longitude": -74.2,
                        "speed": 30,
                    },
                    {
                        "dateTime": "2026-01-01T10:00:00.000Z",
                        "latitude": 4.6,
                        "longitude": -74.1,
                        "speed": 0,
                    },
                    {"dateTime": None, "latitude": 1.0, "longitude": 1.0},
                ]
            },
        )

    session = svc.GeotabSession(server="my.geotab.com", credentials={"sessionId": "s"})
    async with _client(handler) as client:
        records = await svc.get_log_records(
            client,
            session,
            device_id="b123",
            from_date=datetime(2026, 1, 1, 5, tzinfo=UTC),
            to_date=datetime(2026, 1, 2, 5, tzinfo=UTC),
        )

    # Sin dateTime no es una posición utilizable: se descarta en vez de romper.
    assert [r.date_time.minute for r in records] == [0, 5]
    assert records[0].latitude == 4.6
    params = captured["params"]
    assert params["typeName"] == "LogRecord"
    assert params["search"]["deviceSearch"] == {"id": "b123"}
    assert params["search"]["fromDate"].startswith("2026-01-01T05:00:00")
    assert params["search"]["toDate"].startswith("2026-01-02T05:00:00")
    assert params["resultsLimit"] > 0


@pytest.mark.asyncio
async def test_get_latest_meter_reading_uses_status_data_and_selects_latest_date() -> None:
    from datetime import UTC, datetime

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured["params"] = payload["params"]
        return httpx.Response(
            200,
            json={
                "result": [
                    {"dateTime": "2026-08-12T10:00:00Z", "data": 123000},
                    {"dateTime": "2026-08-12T11:00:00Z", "data": 124000},
                    {"dateTime": "2026-08-12T12:00:00Z", "data": "invalid"},
                ]
            },
        )

    session = svc.GeotabSession(server="my.geotab.com", credentials={"sessionId": "s"})
    async with _client(handler) as client:
        reading = await svc.get_latest_meter_reading(
            client,
            session,
            device_id="b123",
            diagnostic_id="DiagnosticOdometerId",
            from_date=datetime(2026, 8, 5, tzinfo=UTC),
        )

    assert reading is not None
    assert reading.value == 124000
    assert reading.date_time == datetime(2026, 8, 12, 11, tzinfo=UTC)
    params = captured["params"]
    assert params["typeName"] == "StatusData"
    assert params["search"]["diagnosticSearch"] == {"id": "DiagnosticOdometerId"}
    assert params["search"]["deviceSearch"] == {"id": "b123"}


@pytest.mark.asyncio
async def test_get_latest_meter_reading_discards_records_older_than_requested_window() -> None:
    from datetime import UTC, datetime

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "result": [
                    {"dateTime": "2025-10-24T16:35:00Z", "data": 999999},
                    {"dateTime": "2026-08-12T11:00:00Z", "data": 124000},
                ]
            },
        )

    session = svc.GeotabSession(server="my.geotab.com", credentials={"sessionId": "s"})
    async with _client(handler) as client:
        reading = await svc.get_latest_meter_reading(
            client,
            session,
            device_id="b123",
            diagnostic_id="DiagnosticOdometerId",
            from_date=datetime(2026, 8, 5, tzinfo=UTC),
        )

    assert reading is not None
    assert reading.value == 124000
