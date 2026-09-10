"""Módulo de reportes "Análisis Ralentí": /reportes/ralenti/*.

Contrato que fijan estos tests:

- el resumen publica SIEMPRE los 4 buckets de duración y los 6 de RPM, en el
  orden del servicio, con porcentajes sobre el total filtrado;
- `por-placa` cuenta y suma por bucket y `rango_dominante` es el bucket con
  más minutos (empate → el más largo);
- el mapa de calor agrega en servidor por coordenada redondeada: dos puntos
  cercanos colapsan a precisión 3 y se separan a precisión 4;
- la serie temporal agrega por día o por mes;
- el listado pagina, ordena con NULL al final y filtra por bucket;
- alcance de flota fail-closed: un usuario de la flota A no ve episodios de un
  vehículo de la flota B ni con el `vehicle_id` explícito;
- `date_from > date_to` responde 422.

Mismo patrón de seed que test_reportes_api.py: se escribe en el schema
`analytics` real con CREATE TABLE IF NOT EXISTS (el DDL lo posee el ETL; aquí
sólo se espeja) y el teardown borra únicamente las filas sembradas, por id.
"""

from __future__ import annotations

import asyncio
import uuid
from itertools import pairwise
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import column, delete, text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.fleet import Fleet
from app.models.master_data import GeotabDatabase, Vehicle
from app.schemas.ralenti import DurationBucketKey, RpmBucketKey
from app.services import ralenti_service
from app.services.ralenti_service import (
    DURATION_BUCKET_KEYS,
    DURATION_BUCKETS,
    RPM_BUCKET_KEYS,
    RPM_BUCKETS,
    dominant_duration_bucket,
)

# ---------------------------------------------------------------------------
# Unit — sin DB
# ---------------------------------------------------------------------------


def test_bucket_literals_match_service_constants() -> None:
    """Las claves del contrato (Literal) y las del servicio son el mismo conjunto."""
    assert set(get_args(DurationBucketKey)) == set(DURATION_BUCKET_KEYS)
    assert set(get_args(RpmBucketKey)) == set(RPM_BUCKET_KEYS)
    assert DURATION_BUCKET_KEYS == ("lt1", "1_5", "5_10", "gt10")
    assert RPM_BUCKET_KEYS == ("lt600", "600_800", "800_1000", "1000_1200", "gt1200", "sin_rpm")


def test_buckets_cover_the_axis_without_gaps() -> None:
    """Cada corte empieza donde termina el anterior; el último es abierto."""
    for buckets in (DURATION_BUCKETS, RPM_BUCKETS[:-1]):
        assert buckets[0].lower is None
        assert buckets[-1].upper is None
        for previous, current in pairwise(buckets):
            assert previous.upper == current.lower
    assert RPM_BUCKETS[-1].is_null_bucket


def test_bucket_condition_renders_half_open_intervals() -> None:
    col = column("x")
    assert str(DURATION_BUCKETS[0].condition(col)) == "x < :x_1"
    assert str(DURATION_BUCKETS[1].condition(col)) == "x >= :x_1 AND x < :x_2"
    assert str(DURATION_BUCKETS[-1].condition(col)) == "x >= :x_1"
    assert str(RPM_BUCKETS[-1].condition(col)) == "x IS NULL"


def test_dominant_duration_bucket_prefers_longer_on_tie() -> None:
    assert dominant_duration_bucket({"lt1": 1.0, "1_5": 5.0, "5_10": 2.0, "gt10": 0.0}) == "1_5"
    assert dominant_duration_bucket({"5_10": 10.0, "gt10": 10.0}) == "gt10"
    assert dominant_duration_bucket({"lt1": 3.0, "1_5": 3.0}) == "1_5"
    assert dominant_duration_bucket({}) is None
    assert dominant_duration_bucket({"lt1": 0.0}) is None


def test_event_order_by_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="sort_by"):
        ralenti_service._event_order_by("placa", "asc")
    with pytest.raises(ValueError, match="sort_dir"):
        ralenti_service._event_order_by("inicio", "sideways")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.integration

_SUFFIX = uuid.uuid4().hex[:8]
_DB_NAME_A = f"ral_a_{_SUFFIX}"
_DB_NAME_B = f"ral_b_{_SUFFIX}"
_FLEET_CODE_A = f"RAL-A-{_SUFFIX.upper()}"
_FLEET_CODE_B = f"RAL-B-{_SUFFIX.upper()}"

_VID_A1 = f"RAL-VA1-{_SUFFIX}"
_VID_A2 = f"RAL-VA2-{_SUFFIX}"
_VID_A3 = f"RAL-VA3-{_SUFFIX}"
_VID_B = f"RAL-VB-{_SUFFIX}"
_DEV_A1 = f"ral-dev-a1-{_SUFFIX}"
_DEV_A2 = f"ral-dev-a2-{_SUFFIX}"
_DEV_A3 = f"ral-dev-a3-{_SUFFIX}"
_DEV_B = f"ral-dev-b-{_SUFFIX}"

_PLACA_A1 = f"RA1{_SUFFIX[:3].upper()}"
_PLACA_A2 = f"RA2{_SUFFIX[:3].upper()}"
_PLACA_A3 = f"RA3{_SUFFIX[:3].upper()}"
_PLACA_B = f"RB1{_SUFFIX[:3].upper()}"


def _sk(n: int) -> str:
    return f"ral-ev-{n}-{_SUFFIX}"


# (event_sk, vehicle_id, database, inicio UTC, duración s, rpm_promedio, lat, lon, fecha)
# Flota A: 8 episodios que cubren los 4 buckets de duración y 6 de RPM.
# Flota B: 1 episodio en la MISMA celda que e1 para la negativa del mapa.
_EVENTS: list[tuple[str, str, str, str, float, float | None, float | None, float | None, str]] = [
    (_sk(1), _VID_A1, _DB_NAME_A, "2026-01-10T08:00:00Z", 30.0, 550.0, 4.6001, -74.0801, "2026-01-10"),
    (_sk(2), _VID_A1, _DB_NAME_A, "2026-01-10T09:00:00Z", 120.0, 700.0, 4.6004, -74.0804, "2026-01-10"),
    (_sk(3), _VID_A1, _DB_NAME_A, "2026-01-11T08:00:00Z", 400.0, 900.0, 4.7, -74.1, "2026-01-11"),
    (_sk(4), _VID_A1, _DB_NAME_A, "2026-02-01T08:00:00Z", 900.0, None, None, None, "2026-02-01"),
    (_sk(5), _VID_A2, _DB_NAME_A, "2026-01-10T10:00:00Z", 600.0, 1300.0, None, None, "2026-01-10"),
    (_sk(7), _VID_A2, _DB_NAME_A, "2026-01-10T11:00:00Z", 300.0, 1100.0, None, None, "2026-01-10"),
    (_sk(8), _VID_A2, _DB_NAME_A, "2026-01-10T12:00:00Z", 300.0, 999.9, None, None, "2026-01-10"),
    (_sk(9), _VID_A3, _DB_NAME_A, "2026-01-11T09:00:00Z", 45.0, 620.0, None, None, "2026-01-11"),
    (_sk(6), _VID_B, _DB_NAME_B, "2026-01-10T08:30:00Z", 100.0, 800.0, 4.6001, -74.0801, "2026-01-10"),
]
_EVENT_SKS = [e[0] for e in _EVENTS]

_TOTAL_A = 8
_SECONDS_A = 30 + 120 + 400 + 900 + 600 + 300 + 300 + 45  # 2695


@pytest.fixture(scope="module")
def admin_client() -> TestClient:
    c = TestClient(app)
    resp = c.post(
        "/api/v1/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert resp.status_code == 200, resp.text
    return c


@pytest.fixture(scope="module")
def ralenti_seed() -> dict[str, str]:
    """Dos flotas, cuatro vehículos y nueve episodios de ralentí."""

    async def _seed() -> dict[str, str]:
        async with AsyncSessionLocal() as session:
            fleet_a = Fleet(code=_FLEET_CODE_A, name="Flota Ralentí A", is_active=True)
            fleet_b = Fleet(code=_FLEET_CODE_B, name="Flota Ralentí B", is_active=True)
            session.add_all([fleet_a, fleet_b])
            await session.flush()
            gd_a = GeotabDatabase(
                fleet_id=fleet_a.id,
                database_name=_DB_NAME_A,
                database_key=_DB_NAME_A,
                is_active=True,
            )
            gd_b = GeotabDatabase(
                fleet_id=fleet_b.id,
                database_name=_DB_NAME_B,
                database_key=_DB_NAME_B,
                is_active=True,
            )
            session.add_all([gd_a, gd_b])
            await session.flush()
            session.add_all(
                [
                    Vehicle(
                        fleet_id=fleet_a.id,
                        geotab_database_id=gd_a.id,
                        geotab_device_id=_DEV_A1,
                        plate=_PLACA_A1,
                        is_active=True,
                    ),
                    Vehicle(
                        fleet_id=fleet_a.id,
                        geotab_database_id=gd_a.id,
                        geotab_device_id=_DEV_A2,
                        plate=_PLACA_A2,
                        is_active=True,
                    ),
                    Vehicle(
                        fleet_id=fleet_a.id,
                        geotab_database_id=gd_a.id,
                        geotab_device_id=_DEV_A3,
                        plate=_PLACA_A3,
                        is_active=True,
                    ),
                    Vehicle(
                        fleet_id=fleet_b.id,
                        geotab_database_id=gd_b.id,
                        geotab_device_id=_DEV_B,
                        plate=_PLACA_B,
                        is_active=True,
                    ),
                ]
            )

            await session.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
            await session.execute(
                text("""
                CREATE TABLE IF NOT EXISTS analytics.dim_vehicle (
                    vehicle_id TEXT PRIMARY KEY,
                    database_name TEXT,
                    device_id TEXT,
                    vehicle_label TEXT,
                    motor_type TEXT,
                    group_key TEXT,
                    rpm_class TEXT,
                    fuel_type_raw TEXT,
                    fuel_kind TEXT,
                    fuel_unit TEXT,
                    fuel_classification_source TEXT,
                    fuel_classification_conflict BOOLEAN,
                    is_active BOOLEAN
                )
            """)
            )
            # Espejo del DDL del ETL para `fact_ralenti_event` (IF NOT EXISTS:
            # si el loader ya creó la tabla real, se respeta).
            await session.execute(
                text("""
                CREATE TABLE IF NOT EXISTS analytics.fact_ralenti_event (
                    event_sk TEXT PRIMARY KEY,
                    vehicle_id TEXT,
                    database_name TEXT,
                    device_id TEXT,
                    rule_id TEXT,
                    rule_source TEXT,
                    inicio TIMESTAMPTZ,
                    fin TIMESTAMPTZ,
                    duracion_segundos DOUBLE PRECISION,
                    eventos_fuente BIGINT,
                    rpm_promedio DOUBLE PRECISION,
                    rpm_maximo DOUBLE PRECISION,
                    rpm_minimo DOUBLE PRECISION,
                    rpm_muestras BIGINT,
                    velocidad_maxima_kmh DOUBLE PRECISION,
                    latitud DOUBLE PRECISION,
                    longitud DOUBLE PRECISION,
                    date_key BIGINT,
                    fecha DATE,
                    hora_local BIGINT,
                    extracted_at TIMESTAMPTZ
                )
            """)
            )
            await session.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_fact_ralenti_event_vehicle_id "
                    "ON analytics.fact_ralenti_event (vehicle_id)"
                )
            )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_vehicle
                    (vehicle_id, device_id, vehicle_label, database_name, motor_type,
                     fuel_kind, fuel_unit, is_active)
                VALUES
                    (:va1, :da1, :pa1, :dba, 'X15', 'liquid', 'gal', true),
                    (:va2, :da2, :pa2, :dba, 'ISX', 'liquid', 'gal', true),
                    (:va3, :da3, :pa3, :dba, 'ISX', 'liquid', 'gal', true),
                    (:vb, :db_dev, :pb, :dbb, 'X15', 'liquid', 'gal', true)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    vehicle_label = EXCLUDED.vehicle_label,
                    database_name = EXCLUDED.database_name,
                    motor_type = EXCLUDED.motor_type,
                    is_active = EXCLUDED.is_active
            """),
                {
                    "va1": _VID_A1,
                    "da1": _DEV_A1,
                    "pa1": _PLACA_A1,
                    "va2": _VID_A2,
                    "da2": _DEV_A2,
                    "pa2": _PLACA_A2,
                    "va3": _VID_A3,
                    "da3": _DEV_A3,
                    "pa3": _PLACA_A3,
                    "vb": _VID_B,
                    "db_dev": _DEV_B,
                    "pb": _PLACA_B,
                    "dba": _DB_NAME_A,
                    "dbb": _DB_NAME_B,
                },
            )
            for sk, vid, dbname, inicio, dur, rpm, lat, lon, fecha in _EVENTS:
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_ralenti_event
                        (event_sk, vehicle_id, database_name, device_id, rule_source,
                         inicio, fin, duracion_segundos, eventos_fuente,
                         rpm_promedio, rpm_maximo, rpm_minimo, rpm_muestras,
                         latitud, longitud, date_key, fecha, hora_local, extracted_at)
                    VALUES
                        (:sk, :vid, :dbname, 'dev', 'geotab_idling',
                         CAST(:inicio AS timestamptz),
                         CAST(:inicio AS timestamptz) + make_interval(secs => :dur),
                         :dur, 2,
                         :rpm, :rpm, :rpm, :muestras,
                         :lat, :lon,
                         CAST(to_char(CAST(:fecha AS date), 'YYYYMMDD') AS bigint),
                         CAST(:fecha AS date), 3, now())
                    ON CONFLICT (event_sk) DO NOTHING
                """),
                    {
                        "sk": sk,
                        "vid": vid,
                        "dbname": dbname,
                        "inicio": inicio,
                        "dur": dur,
                        "rpm": rpm,
                        "muestras": 0 if rpm is None else 3,
                        "lat": lat,
                        "lon": lon,
                        "fecha": fecha,
                    },
                )
            await session.commit()
            return {"fleet_a": str(fleet_a.id), "fleet_b": str(fleet_b.id)}

    ids = asyncio.run(_seed())

    yield ids

    async def _teardown() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM analytics.fact_ralenti_event WHERE event_sk = ANY(:ids)"),
                {"ids": _EVENT_SKS},
            )
            await session.execute(
                text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = ANY(:ids)"),
                {"ids": [_VID_A1, _VID_A2, _VID_A3, _VID_B]},
            )
            await session.execute(
                delete(Vehicle).where(
                    Vehicle.geotab_device_id.in_([_DEV_A1, _DEV_A2, _DEV_A3, _DEV_B])
                )
            )
            await session.execute(
                delete(GeotabDatabase).where(
                    GeotabDatabase.database_key.in_([_DB_NAME_A, _DB_NAME_B])
                )
            )
            await session.execute(
                delete(Fleet).where(Fleet.code.in_([_FLEET_CODE_A, _FLEET_CODE_B]))
            )
            await session.commit()

    asyncio.run(_teardown())


@pytest.fixture(scope="module")
def fleet_a_client(admin_client: TestClient, ralenti_seed: dict[str, str]) -> TestClient:
    """Usuario con `reportes.view` limitado a la flota A."""
    role_code = f"ral_reporter_{_SUFFIX}"
    role_resp = admin_client.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": f"Reportero ralentí {_SUFFIX}",
            "permission_codes": ["reportes.view"],
        },
    )
    assert role_resp.status_code == 201, role_resp.text
    email = f"ralenti-{_SUFFIX}@portalclientes.test"
    password = "Ralenti123!"
    created = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Usuario ralentí A",
            "role_codes": [role_code],
            "fleet_ids": [ralenti_seed["fleet_a"]],
        },
    )
    assert created.status_code == 201, created.text
    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    return client


def _headers_a(seed: dict[str, str]) -> dict[str, str]:
    return {"X-Fleet-Id": seed["fleet_a"]}


def _by_key(items: list[dict], key: str = "bucket") -> dict[str, dict]:
    return {item[key]: item for item in items}


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------


def test_summary_buckets_and_percentages(
    admin_client: TestClient, ralenti_seed: dict[str, str]
) -> None:
    resp = admin_client.get("/api/v1/reportes/ralenti/summary", headers=_headers_a(ralenti_seed))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_eventos"] == _TOTAL_A
    assert body["total_minutos"] == pytest.approx(round(_SECONDS_A / 60, 2))
    assert body["duracion_promedio_min"] == pytest.approx(round(_SECONDS_A / 60 / _TOTAL_A, 2))
    assert body["vehiculos"] == 3

    # Los 4 buckets, en orden, aunque alguno pudiera estar vacío.
    assert [b["bucket"] for b in body["por_duracion"]] == list(DURATION_BUCKET_KEYS)
    assert [b["label"] for b in body["por_duracion"]] == [b.label for b in DURATION_BUCKETS]
    dur = _by_key(body["por_duracion"])
    assert dur["lt1"]["eventos"] == 2 and dur["lt1"]["minutos"] == pytest.approx(1.25)
    assert dur["1_5"]["eventos"] == 1 and dur["1_5"]["minutos"] == pytest.approx(2.0)
    assert dur["5_10"]["eventos"] == 3 and dur["5_10"]["minutos"] == pytest.approx(16.67)
    assert dur["gt10"]["eventos"] == 2 and dur["gt10"]["minutos"] == pytest.approx(25.0)
    assert dur["lt1"]["pct_eventos"] == pytest.approx(25.0)
    assert dur["5_10"]["pct_eventos"] == pytest.approx(37.5)
    assert dur["gt10"]["pct_minutos"] == pytest.approx(round(1500 / _SECONDS_A * 100, 1))
    assert dur["5_10"]["duracion_promedio_min"] == pytest.approx(5.56)
    assert sum(b["eventos"] for b in body["por_duracion"]) == _TOTAL_A

    # Los 6 buckets de RPM, en orden; `sin_rpm` recoge el episodio sin muestras.
    assert [b["bucket"] for b in body["por_rpm"]] == list(RPM_BUCKET_KEYS)
    rpm = _by_key(body["por_rpm"])
    assert rpm["lt600"]["eventos"] == 1
    assert rpm["600_800"]["eventos"] == 2
    assert rpm["800_1000"]["eventos"] == 2  # 900 y 999.9: el borde 1.000 es exclusivo
    assert rpm["1000_1200"]["eventos"] == 1
    assert rpm["gt1200"]["eventos"] == 1
    assert rpm["sin_rpm"]["eventos"] == 1
    assert rpm["sin_rpm"]["minutos"] == pytest.approx(15.0)
    assert rpm["gt1200"]["pct_eventos"] == pytest.approx(12.5)
    assert sum(b["eventos"] for b in body["por_rpm"]) == _TOTAL_A


def test_summary_empty_scope_returns_zeroed_buckets(
    admin_client: TestClient, ralenti_seed: dict[str, str]
) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/summary",
        headers=_headers_a(ralenti_seed),
        params={"date_from": "2030-01-01", "date_to": "2030-01-31"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_eventos"] == 0
    assert body["duracion_promedio_min"] is None
    assert len(body["por_duracion"]) == 4 and len(body["por_rpm"]) == 6
    assert all(b["eventos"] == 0 and b["pct_eventos"] == 0.0 for b in body["por_duracion"])
    assert all(b["duracion_promedio_min"] is None for b in body["por_duracion"])


def test_summary_motor_type_filter_respects_scope(
    admin_client: TestClient, ralenti_seed: dict[str, str]
) -> None:
    """X15 lo tienen VA1 (flota A) y VB (flota B): sólo cuentan los 4 de VA1."""
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/summary",
        headers=_headers_a(ralenti_seed),
        params={"motor_type": "X15"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_eventos"] == 4
    assert resp.json()["vehiculos"] == 1


def test_summary_bucket_filters(admin_client: TestClient, ralenti_seed: dict[str, str]) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/summary",
        headers=_headers_a(ralenti_seed),
        params={"duration_bucket": "gt10"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_eventos"] == 2
    assert _by_key(body["por_duracion"])["gt10"]["pct_eventos"] == pytest.approx(100.0)

    resp = admin_client.get(
        "/api/v1/reportes/ralenti/summary",
        headers=_headers_a(ralenti_seed),
        params={"rpm_bucket": "sin_rpm"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_eventos"] == 1

    resp = admin_client.get(
        "/api/v1/reportes/ralenti/summary",
        headers=_headers_a(ralenti_seed),
        params={"rpm_bucket": "inventado"},
    )
    assert resp.status_code == 422


def test_bad_date_range_is_422(admin_client: TestClient, ralenti_seed: dict[str, str]) -> None:
    for path in ("summary", "por-placa", "heatmap", "timeseries", "events"):
        resp = admin_client.get(
            f"/api/v1/reportes/ralenti/{path}",
            headers=_headers_a(ralenti_seed),
            params={"date_from": "2026-02-01", "date_to": "2026-01-01"},
        )
        assert resp.status_code == 422, (path, resp.text)


# ---------------------------------------------------------------------------
# Por placa
# ---------------------------------------------------------------------------


def test_por_placa_counts_and_dominant_bucket(
    admin_client: TestClient, ralenti_seed: dict[str, str]
) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/por-placa", headers=_headers_a(ralenti_seed)
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["vehicle_id"] for r in rows] == [_VID_A1, _VID_A2, _VID_A3]
    by_vid = _by_key(rows, "vehicle_id")

    a1 = by_vid[_VID_A1]
    assert a1["placa"] == _PLACA_A1
    assert (a1["eventos_lt1"], a1["eventos_1_5"], a1["eventos_5_10"], a1["eventos_gt10"]) == (
        1,
        1,
        1,
        1,
    )
    assert a1["minutos_lt1"] == pytest.approx(0.5)
    assert a1["minutos_1_5"] == pytest.approx(2.0)
    assert a1["minutos_5_10"] == pytest.approx(6.67)
    assert a1["minutos_gt10"] == pytest.approx(15.0)
    assert a1["total_eventos"] == 4
    assert a1["total_minutos"] == pytest.approx(24.17)
    assert a1["rango_dominante"] == "gt10"

    # VA2 empata 10 min en 5_10 (300+300) y 10 min en gt10 (600): gana el largo.
    a2 = by_vid[_VID_A2]
    assert a2["minutos_5_10"] == pytest.approx(10.0)
    assert a2["minutos_gt10"] == pytest.approx(10.0)
    assert a2["rango_dominante"] == "gt10"

    a3 = by_vid[_VID_A3]
    assert a3["total_eventos"] == 1
    assert a3["rango_dominante"] == "lt1"


# ---------------------------------------------------------------------------
# Mapa de calor
# ---------------------------------------------------------------------------


def test_heatmap_precision_collapses_and_separates(
    admin_client: TestClient, ralenti_seed: dict[str, str]
) -> None:
    # Precisión 3: e1 y e2 caen en la misma celda; e4..e9 no tienen coordenadas.
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/heatmap",
        headers=_headers_a(ralenti_seed),
        params={"precision": 3},
    )
    assert resp.status_code == 200, resp.text
    cells = resp.json()
    assert len(cells) == 2
    # Orden por minutos descendente: la celda de e3 (6,67 min) va primero.
    assert cells[0]["latitud"] == pytest.approx(4.7)
    assert cells[0]["eventos"] == 1
    assert cells[0]["minutos"] == pytest.approx(6.67)
    merged = cells[1]
    assert merged["latitud"] == pytest.approx(4.6)
    assert merged["longitud"] == pytest.approx(-74.08)
    assert merged["eventos"] == 2  # el episodio de la flota B en la misma celda NO entra
    assert merged["minutos"] == pytest.approx(2.5)

    # Precisión 4: los dos puntos se separan.
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/heatmap",
        headers=_headers_a(ralenti_seed),
        params={"precision": 4},
    )
    assert resp.status_code == 200, resp.text
    cells = resp.json()
    assert len(cells) == 3
    lats = sorted(c["latitud"] for c in cells)
    assert lats == pytest.approx([4.6001, 4.6004, 4.7])
    assert all(c["eventos"] == 1 for c in cells)

    # Fuera de rango: 422.
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/heatmap",
        headers=_headers_a(ralenti_seed),
        params={"precision": 5},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Serie temporal
# ---------------------------------------------------------------------------


def test_timeseries_daily_and_monthly(
    admin_client: TestClient, ralenti_seed: dict[str, str]
) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/timeseries", headers=_headers_a(ralenti_seed)
    )
    assert resp.status_code == 200, resp.text
    daily = resp.json()
    assert [p["bucket"] for p in daily] == ["2026-01-10", "2026-01-11", "2026-02-01"]
    assert [p["eventos"] for p in daily] == [5, 2, 1]
    assert daily[0]["minutos"] == pytest.approx(22.5)
    assert daily[1]["minutos"] == pytest.approx(7.42)
    assert daily[2]["minutos"] == pytest.approx(15.0)

    resp = admin_client.get(
        "/api/v1/reportes/ralenti/timeseries",
        headers=_headers_a(ralenti_seed),
        params={"granularity": "monthly"},
    )
    assert resp.status_code == 200, resp.text
    monthly = resp.json()
    assert [p["bucket"] for p in monthly] == ["2026-01", "2026-02"]
    assert [p["eventos"] for p in monthly] == [7, 1]
    assert monthly[0]["minutos"] == pytest.approx(29.92)

    # El rango es inclusivo sobre `fecha`.
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/timeseries",
        headers=_headers_a(ralenti_seed),
        params={"date_from": "2026-01-11", "date_to": "2026-01-11"},
    )
    assert resp.status_code == 200, resp.text
    assert [(p["bucket"], p["eventos"]) for p in resp.json()] == [("2026-01-11", 2)]

    resp = admin_client.get(
        "/api/v1/reportes/ralenti/timeseries",
        headers=_headers_a(ralenti_seed),
        params={"granularity": "weekly"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Listado
# ---------------------------------------------------------------------------


def test_events_pagination_sort_and_bucket_filter(
    admin_client: TestClient, ralenti_seed: dict[str, str]
) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/events",
        headers=_headers_a(ralenti_seed),
        params={"limit": 3, "sort_by": "duracion_segundos", "sort_dir": "desc"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == _TOTAL_A
    assert body["limit"] == 3 and body["offset"] == 0
    assert [i["event_sk"] for i in body["items"]] == [_sk(4), _sk(5), _sk(3)]
    first = body["items"][0]
    assert first["placa"] == _PLACA_A1
    assert first["duracion_min"] == pytest.approx(15.0)
    assert first["duration_bucket"] == "gt10"
    assert first["rpm_bucket"] == "sin_rpm"
    assert first["rpm_promedio"] is None
    assert first["eventos_fuente"] == 2
    assert first["inicio"].startswith("2026-02-01T08:00:00")
    assert first["fin"].startswith("2026-02-01T08:15:00")

    # Empate de duración (e7 y e8, 300 s): el desempate por event_sk es estable
    # entre páginas — la unión de dos páginas de 4 devuelve las 8 sin repetir.
    seen: list[str] = []
    for offset in (0, 4):
        page = admin_client.get(
            "/api/v1/reportes/ralenti/events",
            headers=_headers_a(ralenti_seed),
            params={"limit": 4, "offset": offset, "sort_by": "duracion_segundos"},
        )
        assert page.status_code == 200, page.text
        seen.extend(i["event_sk"] for i in page.json()["items"])
    assert len(seen) == _TOTAL_A and len(set(seen)) == _TOTAL_A

    # NULL al final en ambas direcciones.
    for sort_dir in ("asc", "desc"):
        resp = admin_client.get(
            "/api/v1/reportes/ralenti/events",
            headers=_headers_a(ralenti_seed),
            params={"limit": 200, "sort_by": "rpm_promedio", "sort_dir": sort_dir},
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert items[-1]["event_sk"] == _sk(4)
        expected_first = _sk(1) if sort_dir == "asc" else _sk(5)
        assert items[0]["event_sk"] == expected_first

    # Filtro por bucket.
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/events",
        headers=_headers_a(ralenti_seed),
        params={"duration_bucket": "gt10"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert {i["event_sk"] for i in body["items"]} == {_sk(4), _sk(5)}

    resp = admin_client.get(
        "/api/v1/reportes/ralenti/events",
        headers=_headers_a(ralenti_seed),
        params={"rpm_bucket": "800_1000", "vehicle_id": _VID_A2},
    )
    assert resp.status_code == 200, resp.text
    assert [i["event_sk"] for i in resp.json()["items"]] == [_sk(8)]

    # Offset más allá del final: página vacía con el total intacto.
    resp = admin_client.get(
        "/api/v1/reportes/ralenti/events",
        headers=_headers_a(ralenti_seed),
        params={"offset": 50},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == [] and resp.json()["total"] == _TOTAL_A

    for bad in ({"limit": 0}, {"limit": 201}, {"sort_by": "placa"}, {"sort_dir": "up"}):
        resp = admin_client.get(
            "/api/v1/reportes/ralenti/events", headers=_headers_a(ralenti_seed), params=bad
        )
        assert resp.status_code == 422, bad


# ---------------------------------------------------------------------------
# Alcance de flota
# ---------------------------------------------------------------------------


def test_fleet_scope_hides_other_fleet_rows(
    fleet_a_client: TestClient, ralenti_seed: dict[str, str]
) -> None:
    """Un usuario de la flota A no ve el episodio de VB ni pidiéndolo por id."""
    resp = fleet_a_client.get("/api/v1/reportes/ralenti/summary", params={"vehicle_id": _VID_B})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_eventos"] == 0

    resp = fleet_a_client.get("/api/v1/reportes/ralenti/events", params={"vehicle_id": _VID_B})
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == [] and resp.json()["total"] == 0

    resp = fleet_a_client.get("/api/v1/reportes/ralenti/por-placa")
    assert resp.status_code == 200, resp.text
    assert _VID_B not in {r["vehicle_id"] for r in resp.json()}

    # Sin filtros, su alcance es exactamente la flota A: 8 episodios, y la celda
    # compartida del mapa no suma el punto de B.
    resp = fleet_a_client.get("/api/v1/reportes/ralenti/summary")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_eventos"] == _TOTAL_A

    resp = fleet_a_client.get("/api/v1/reportes/ralenti/heatmap")
    assert resp.status_code == 200, resp.text
    merged = [c for c in resp.json() if c["latitud"] == pytest.approx(4.6)]
    assert len(merged) == 1 and merged[0]["eventos"] == 2

    # Pedir la flota B por header la rechaza la dependencia de flotas
    # seleccionadas (`get_selected_fleets`), como en el resto de reportes.
    resp = fleet_a_client.get(
        "/api/v1/reportes/ralenti/summary", headers={"X-Fleet-Id": ralenti_seed["fleet_b"]}
    )
    assert resp.status_code == 403, resp.text


def test_unauthenticated_is_401() -> None:
    resp = TestClient(app).get("/api/v1/reportes/ralenti/summary")
    assert resp.status_code == 401
