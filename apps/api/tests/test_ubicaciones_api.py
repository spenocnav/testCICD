"""Informe de Ubicaciones: consulta viva a MyGeotab, sin persistir nada.

MyGeotab siempre está simulado: los tests verifican qué se le pide, cómo se
muestrea lo que devuelve y que el alcance de flota se aplique antes de salir a
la red.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import geotab_service, ubicaciones_service


def _record(minute: int, *, day: str = "2026-01-01") -> geotab_service.GeotabLogRecord:
    return geotab_service.GeotabLogRecord(
        date_time=datetime.fromisoformat(f"{day}T10:{minute:02d}:00+00:00"),
        latitude=4.6 + minute / 1000,
        longitude=-74.1 - minute / 1000,
        speed=float(minute),
    )


# ---------------------------------------------------------------------------
# Muestreo y ventanas (puras, sin DB ni red)
# ---------------------------------------------------------------------------


def test_sample_records_deja_uno_por_ventana() -> None:
    records = [_record(m) for m in (0, 1, 9, 10, 12, 21)]
    sampled = ubicaciones_service.sample_records(records, 10)
    assert [r.date_time.minute for r in sampled] == [0, 10, 21]


def test_sample_records_media_hora() -> None:
    records = [_record(m) for m in (0, 5, 29, 30, 59)]
    sampled = ubicaciones_service.sample_records(records, 30)
    assert [r.date_time.minute for r in sampled] == [0, 30]


def test_sample_records_sin_datos() -> None:
    assert ubicaciones_service.sample_records([], 10) == []


def test_day_bounds_usa_la_zona_del_informe() -> None:
    """Un día de Bogotá son 05:00Z a 05:00Z del día siguiente."""
    assert settings.reportes_timezone == "America/Bogota"
    start, end = ubicaciones_service.day_bounds_utc(date(2026, 1, 1))
    assert start == datetime(2026, 1, 1, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 1, 2, 5, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# fetch_day: qué se le pide a Geotab y qué se devuelve
# ---------------------------------------------------------------------------


def _target() -> ubicaciones_service.VehicleGeotabTarget:
    return ubicaciones_service.VehicleGeotabTarget(
        plate="ABC123",
        device_id="b123",
        database_name="basedb",
        database_key="basekey",
    )


@pytest.mark.asyncio
async def test_fetch_day_consulta_el_dia_muestrea_y_geocodifica() -> None:
    db = MagicMock()
    db.commit = AsyncMock()
    captured: dict[str, object] = {}

    async def fake_logs(client, session, *, device_id, from_date, to_date, **kw):
        captured["device_id"] = device_id
        captured["from_date"] = from_date
        captured["to_date"] = to_date
        return [_record(m) for m in (0, 3, 10, 11, 20)]

    async def fake_addresses(client, session, coordinates, batch_size=None):
        captured["coordinates"] = list(coordinates)
        return [f"Calle {i}" for i in range(len(coordinates))]

    with (
        patch.object(
            ubicaciones_service,
            "resolve_vehicle_target",
            AsyncMock(return_value=_target()),
        ),
        patch.object(
            ubicaciones_service.master_data_service,
            "acquire_geotab_credential",
            AsyncMock(return_value=SimpleNamespace(username="u", password="p")),
        ),
        patch.object(
            ubicaciones_service.geotab_service,
            "authenticate",
            AsyncMock(return_value=geotab_service.GeotabSession("s", {})),
        ),
        patch.object(
            ubicaciones_service.geotab_service,
            "get_log_records",
            AsyncMock(side_effect=fake_logs),
        ),
        patch.object(
            ubicaciones_service.geotab_service,
            "resolve_addresses_in_batches",
            AsyncMock(side_effect=fake_addresses),
        ),
    ):
        items = await ubicaciones_service.fetch_day(
            db,
            vehicle_id=uuid.uuid4(),
            fleet_ids=[uuid.uuid4()],
            day=date(2026, 1, 1),
            sample_minutes=10,
        )

    assert captured["device_id"] == "b123"
    assert captured["from_date"] == datetime(2026, 1, 1, 5, 0, tzinfo=UTC)
    assert captured["to_date"] == datetime(2026, 1, 2, 5, 0, tzinfo=UTC)
    # Se geocodifica solo lo muestreado, no las 5 posiciones crudas.
    assert len(captured["coordinates"]) == 3
    assert items is not None
    assert [item["fecha_y_hora"].minute for item in items] == [0, 10, 20]
    assert [item["direccion"] for item in items] == ["Calle 0", "Calle 1", "Calle 2"]
    assert {item["placa"] for item in items} == {"ABC123"}
    # La rotación LRU de la credencial se confirma antes de salir a la red.
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_day_sin_geocodificar_no_llama_addresses() -> None:
    db = MagicMock()
    db.commit = AsyncMock()
    addresses = AsyncMock()

    with (
        patch.object(
            ubicaciones_service,
            "resolve_vehicle_target",
            AsyncMock(return_value=_target()),
        ),
        patch.object(
            ubicaciones_service.master_data_service,
            "acquire_geotab_credential",
            AsyncMock(return_value=SimpleNamespace(username="u", password="p")),
        ),
        patch.object(
            ubicaciones_service.geotab_service,
            "authenticate",
            AsyncMock(return_value=geotab_service.GeotabSession("s", {})),
        ),
        patch.object(
            ubicaciones_service.geotab_service,
            "get_log_records",
            AsyncMock(return_value=[_record(0)]),
        ),
        patch.object(
            ubicaciones_service.geotab_service,
            "resolve_addresses_in_batches",
            addresses,
        ),
    ):
        items = await ubicaciones_service.fetch_day(
            db,
            vehicle_id=uuid.uuid4(),
            fleet_ids=None,
            day=date(2026, 1, 1),
            with_address=False,
        )

    addresses.assert_not_awaited()
    assert items is not None and items[0]["direccion"] is None


@pytest.mark.asyncio
async def test_fetch_day_sin_flotas_no_toca_geotab() -> None:
    """Fail closed: sin alcance no se resuelve vehículo ni se abre sesión."""
    db = MagicMock()
    db.execute = AsyncMock(side_effect=AssertionError("no debe consultar"))

    with patch.object(
        ubicaciones_service.geotab_service,
        "authenticate",
        AsyncMock(side_effect=AssertionError("no debe autenticar")),
    ):
        items = await ubicaciones_service.fetch_day(
            db, vehicle_id=uuid.uuid4(), fleet_ids=[], day=date(2026, 1, 1)
        )
    assert items is None


@pytest.mark.asyncio
async def test_fetch_day_sin_credencial_es_error_controlado() -> None:
    db = MagicMock()
    db.commit = AsyncMock()

    with (
        patch.object(
            ubicaciones_service,
            "resolve_vehicle_target",
            AsyncMock(return_value=_target()),
        ),
        patch.object(
            ubicaciones_service.master_data_service,
            "acquire_geotab_credential",
            AsyncMock(return_value=None),
        ),
        pytest.raises(ubicaciones_service.UbicacionesUnavailableError),
    ):
        await ubicaciones_service.fetch_day(
            db, vehicle_id=uuid.uuid4(), fleet_ids=None, day=date(2026, 1, 1)
        )


# ---------------------------------------------------------------------------
# Endpoint HTTP
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def admin_client() -> TestClient:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert resp.status_code == 200, resp.text
    return client


def _params(**extra: object) -> dict[str, object]:
    return {
        "vehicle_id": str(uuid.uuid4()),
        "date_from": "2026-01-01",
        "date_to": "2026-01-03",
        **extra,
    }


@pytest.mark.integration
def test_ubicaciones_sirve_un_dia_y_encadena_el_siguiente(
    admin_client: TestClient,
) -> None:
    day = date(2026, 1, 1)
    captured: dict[str, object] = {}

    async def fake_fetch(db, *, vehicle_id, fleet_ids, day, sample_minutes, with_address):
        captured["day"] = day
        captured["sample_minutes"] = sample_minutes
        return [
            {
                "fecha_y_hora": datetime(2026, 1, 1, 15, 0, tzinfo=UTC),
                "placa": "ABC123",
                "latitude": 4.6,
                "longitude": -74.1,
                "speed": 42.0,
                "direccion": "Calle 1",
            }
        ]

    with patch.object(
        ubicaciones_service, "fetch_day", AsyncMock(side_effect=fake_fetch)
    ):
        resp = admin_client.get("/api/v1/reportes/ubicaciones", params=_params())

    assert resp.status_code == 200
    body = resp.json()
    assert captured["day"] == day
    assert captured["sample_minutes"] == 10
    assert body["day"] == "2026-01-01"
    assert body["next_cursor"] == "2026-01-02"
    assert body["items"][0]["direccion"] == "Calle 1"


@pytest.mark.integration
def test_ubicaciones_ultimo_dia_cierra_el_recorrido(admin_client: TestClient) -> None:
    with patch.object(ubicaciones_service, "fetch_day", AsyncMock(return_value=[])):
        resp = admin_client.get(
            "/api/v1/reportes/ubicaciones", params=_params(cursor="2026-01-03")
        )
    assert resp.status_code == 200
    assert resp.json()["next_cursor"] is None


@pytest.mark.integration
def test_ubicaciones_vehiculo_fuera_de_alcance_es_404(admin_client: TestClient) -> None:
    with patch.object(ubicaciones_service, "fetch_day", AsyncMock(return_value=None)):
        resp = admin_client.get("/api/v1/reportes/ubicaciones", params=_params())
    assert resp.status_code == 404


@pytest.mark.integration
def test_ubicaciones_proveedor_caido_es_502(admin_client: TestClient) -> None:
    """Sin proveedor no hay informe: se dice, no se devuelve vacío."""
    with patch.object(
        ubicaciones_service,
        "fetch_day",
        AsyncMock(side_effect=geotab_service.GeotabError("MyGeotab no respondió")),
    ):
        resp = admin_client.get("/api/v1/reportes/ubicaciones", params=_params())
    assert resp.status_code == 502


@pytest.mark.integration
def test_ubicaciones_sin_dispositivo_es_409(admin_client: TestClient) -> None:
    with patch.object(
        ubicaciones_service,
        "fetch_day",
        AsyncMock(
            side_effect=ubicaciones_service.UbicacionesUnavailableError("sin device")
        ),
    ):
        resp = admin_client.get("/api/v1/reportes/ubicaciones", params=_params())
    assert resp.status_code == 409


@pytest.mark.integration
@pytest.mark.parametrize(
    "extra",
    [
        {"date_from": "2026-01-05", "date_to": "2026-01-01"},
        {"date_to": "2026-03-01"},
        {"sample_minutes": 7},
        {"cursor": "2026-02-01"},
        {"vehicle_id": "no-es-uuid"},
    ],
)
def test_ubicaciones_valida_parametros(
    admin_client: TestClient, extra: dict[str, object]
) -> None:
    resp = admin_client.get("/api/v1/reportes/ubicaciones", params=_params(**extra))
    assert resp.status_code == 422


@pytest.mark.integration
def test_ubicaciones_acepta_los_muestreos_publicados(admin_client: TestClient) -> None:
    for minutes in ubicaciones_service.SAMPLE_MINUTES_CHOICES:
        with patch.object(ubicaciones_service, "fetch_day", AsyncMock(return_value=[])):
            resp = admin_client.get(
                "/api/v1/reportes/ubicaciones", params=_params(sample_minutes=minutes)
            )
        assert resp.status_code == 200, minutes
        assert resp.json()["sample_minutes"] == minutes


@pytest.mark.integration
def test_ubicaciones_rango_maximo_es_de_31_dias(admin_client: TestClient) -> None:
    start = date(2026, 1, 1)
    with patch.object(ubicaciones_service, "fetch_day", AsyncMock(return_value=[])):
        resp = admin_client.get(
            "/api/v1/reportes/ubicaciones",
            params={
                "vehicle_id": str(uuid.uuid4()),
                "date_from": start.isoformat(),
                "date_to": (start + timedelta(days=30)).isoformat(),
            },
        )
    assert resp.status_code == 200
