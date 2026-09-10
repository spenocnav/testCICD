"""Agregación por grupo interno de vehículos: /reportes/*/por-grupo.

Contrato que fijan estos tests:

- los buckets agregan por el grupo HOJA (`vehicles.vehicle_group_id`) y los
  hechos de vehículos sin grupo caen al bucket `group_id=null`, no se pierden;
- `exclude_telematics=true` en fallas/por-grupo excluye las filas de
  `SourceGeotabGoId` y el default (false) no excluye nada;
- alcance de flota fail-closed: un usuario limitado a la flota A no recibe
  buckets con datos de la flota B, ni siquiera dentro del bucket "sin grupo".

Mismo patrón de seed que test_reportes_api.py: se escribe en el schema
`analytics` real con CREATE TABLE IF NOT EXISTS y el teardown borra solo las
filas sembradas, por id.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.fleet import Fleet
from app.models.master_data import FleetVehicleGroup, GeotabDatabase, Vehicle

pytestmark = pytest.mark.integration

_SUFFIX = uuid.uuid4().hex[:8]
_DB_NAME_A = f"grpagg_a_{_SUFFIX}"
_DB_NAME_B = f"grpagg_b_{_SUFFIX}"
_FLEET_CODE_A = f"GRPAGG-A-{_SUFFIX.upper()}"
_FLEET_CODE_B = f"GRPAGG-B-{_SUFFIX.upper()}"

# vehicle_id de analytics.dim_vehicle (texto libre del ETL, no UUID).
_VID_G1 = f"GRPAGG-V1-{_SUFFIX}"  # grupo A1
_VID_G2 = f"GRPAGG-V2-{_SUFFIX}"  # grupo A2
_VID_NOG = f"GRPAGG-V3-{_SUFFIX}"  # sin grupo
_VID_B = f"GRPAGG-VB-{_SUFFIX}"  # flota B (grupo B)

_DEV_G1 = f"grpagg-dev1-{_SUFFIX}"
_DEV_G2 = f"grpagg-dev2-{_SUFFIX}"
_DEV_NOG = f"grpagg-dev3-{_SUFFIX}"
_DEV_B = f"grpagg-devb-{_SUFFIX}"

_FACT_IDS = [f"grpagg-comb-{i}-{_SUFFIX}" for i in range(4)]
_FAULT_IDS = [f"grpagg-fault-{i}-{_SUFFIX}" for i in range(3)]

_DATE_PARAMS = {"date_from": "2026-01-01", "date_to": "2026-01-01"}


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


def _create_fleet(admin: TestClient, code: str) -> str:
    resp = admin.post("/api/v1/fleets", json={"code": code, "name": f"Flota {code}"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.fixture(scope="module")
def group_seed(admin_client: TestClient) -> dict[str, str]:
    """Dos flotas, tres grupos, cuatro vehículos y sus hechos analytics.

    Flota A: v1 (grupo A1), v2 (grupo A2), v3 (sin grupo).
    Flota B: vb (grupo B) — existe solo para la negativa de alcance.
    """
    fleet_a = _create_fleet(admin_client, _FLEET_CODE_A)
    fleet_b = _create_fleet(admin_client, _FLEET_CODE_B)

    async def _seed() -> dict[str, str]:
        async with AsyncSessionLocal() as session:
            group_a1 = FleetVehicleGroup(
                source_id=int(uuid.uuid4().int % 10**9),
                fleet_id=uuid.UUID(fleet_a),
                name="Regional A1",
                is_active=True,
            )
            group_a2 = FleetVehicleGroup(
                source_id=int(uuid.uuid4().int % 10**9),
                fleet_id=uuid.UUID(fleet_a),
                name="Regional A2",
                is_active=True,
            )
            group_b = FleetVehicleGroup(
                source_id=int(uuid.uuid4().int % 10**9),
                fleet_id=uuid.UUID(fleet_b),
                name="Regional Ajena",
                is_active=True,
            )
            session.add_all([group_a1, group_a2, group_b])
            await session.flush()

            gd_a = GeotabDatabase(
                fleet_id=uuid.UUID(fleet_a),
                database_name=_DB_NAME_A,
                database_key=_DB_NAME_A,
                is_active=True,
            )
            gd_b = GeotabDatabase(
                fleet_id=uuid.UUID(fleet_b),
                database_name=_DB_NAME_B,
                database_key=_DB_NAME_B,
                is_active=True,
            )
            session.add_all([gd_a, gd_b])
            await session.flush()

            session.add_all(
                [
                    Vehicle(
                        fleet_id=uuid.UUID(fleet_a),
                        geotab_database_id=gd_a.id,
                        geotab_device_id=_DEV_G1,
                        plate=f"GA1{_SUFFIX[:3].upper()}",
                        vehicle_group_id=group_a1.id,
                        is_active=True,
                    ),
                    Vehicle(
                        fleet_id=uuid.UUID(fleet_a),
                        geotab_database_id=gd_a.id,
                        geotab_device_id=_DEV_G2,
                        plate=f"GA2{_SUFFIX[:3].upper()}",
                        vehicle_group_id=group_a2.id,
                        is_active=True,
                    ),
                    Vehicle(
                        fleet_id=uuid.UUID(fleet_a),
                        geotab_database_id=gd_a.id,
                        geotab_device_id=_DEV_NOG,
                        plate=f"GA3{_SUFFIX[:3].upper()}",
                        vehicle_group_id=None,
                        is_active=True,
                    ),
                    Vehicle(
                        fleet_id=uuid.UUID(fleet_b),
                        geotab_database_id=gd_b.id,
                        geotab_device_id=_DEV_B,
                        plate=f"GB1{_SUFFIX[:3].upper()}",
                        vehicle_group_id=group_b.id,
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
            await session.execute(
                text("""
                CREATE TABLE IF NOT EXISTS analytics.dim_date (
                    date_key BIGINT PRIMARY KEY,
                    date DATE, year BIGINT, month BIGINT, day BIGINT, quarter BIGINT,
                    month_key BIGINT, month_start_date DATE, week_of_year BIGINT,
                    day_of_week BIGINT, is_weekend BOOLEAN
                )
            """)
            )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_date
                    (date_key, date, year, month, day, quarter, month_key, month_start_date)
                VALUES
                    (20260101, '2026-01-01', 2026, 1, 1, 1, 202601, '2026-01-01')
                ON CONFLICT (date_key) DO NOTHING
            """)
            )
            # Mismo DDL que test_reportes_api.py (IF NOT EXISTS: si el loader o
            # esa suite ya crearon la tabla real, se respeta).
            await session.execute(
                text("""
                CREATE TABLE IF NOT EXISTS analytics.fact_combustible_daily (
                    fact_row_id TEXT PRIMARY KEY,
                    vehicle_id TEXT,
                    database_name TEXT,
                    motor_type TEXT,
                    date_key BIGINT,
                    fecha DATE,
                    placa TEXT,
                    kms_ecm DOUBLE PRECISION,
                    kms_gps DOUBLE PRECISION,
                    kms_effective DOUBLE PRECISION,
                    hrs_ecm DOUBLE PRECISION,
                    hrs_gps DOUBLE PRECISION,
                    comb DOUBLE PRECISION,
                    comb_ralenti DOUBLE PRECISION,
                    fuel_kind TEXT,
                    fuel_unit TEXT,
                    km_gal DOUBLE PRECISION,
                    gal_hr DOUBLE PRECISION,
                    gal_hr_ralenti DOUBLE PRECISION,
                    km_m3 DOUBLE PRECISION,
                    m3_hr DOUBLE PRECISION,
                    m3_hr_ralenti DOUBLE PRECISION,
                    velocidad_promedio DOUBLE PRECISION,
                    km_gal_effective DOUBLE PRECISION,
                    km_m3_effective DOUBLE PRECISION,
                    velocidad_promedio_effective DOUBLE PRECISION,
                    distance_source TEXT,
                    distance_quality_status TEXT,
                    distance_quality_reason TEXT,
                    distance_diff_km DOUBLE PRECISION,
                    distance_diff_pct DOUBLE PRECISION,
                    gps_quality_valid BOOLEAN,
                    gps_trip_count BIGINT,
                    ecm_reading_count BIGINT,
                    distance_quality_fingerprint TEXT,
                    distance_threshold_version TEXT,
                    km_gal_gps DOUBLE PRECISION,
                    gal_hr_gps DOUBLE PRECISION,
                    velocidad_promedio_gps DOUBLE PRECISION,
                    pct_rango_bajo DOUBLE PRECISION,
                    pct_rango_economico DOUBLE PRECISION,
                    pct_rango_balanceado DOUBLE PRECISION,
                    pct_rango_potencia DOUBLE PRECISION,
                    pct_exceso_rpm DOUBLE PRECISION,
                    pct_rango_potencia_ineficiente DOUBLE PRECISION,
                    pct_ralenti_gps DOUBLE PRECISION,
                    pct_ralenti_ecm DOUBLE PRECISION,
                    pct_ralenti DOUBLE PRECISION,
                    horas_ralenti_base DOUBLE PRECISION,
                    fuente_ralenti TEXT,
                    tiempo_total_en_rango DOUBLE PRECISION,
                    ralenti DOUBLE PRECISION,
                    ralenti_ecm DOUBLE PRECISION,
                    tiempo_en_ralenti DOUBLE PRECISION,
                    pct_rango_bajo_descenso DOUBLE PRECISION,
                    pct_rango_economico_descenso DOUBLE PRECISION,
                    pct_rango_balanceado_descenso DOUBLE PRECISION,
                    pct_rango_potencia_descenso DOUBLE PRECISION,
                    pct_exceso_rpm_descenso DOUBLE PRECISION,
                    pct_rango_potencia_ineficiente_descenso DOUBLE PRECISION,
                    tiempo_total_en_rango_de_descenso DOUBLE PRECISION,
                    pct_rango_bajo_sin_descenso DOUBLE PRECISION,
                    pct_rango_economico_sin_descenso DOUBLE PRECISION,
                    pct_rango_balanceado_sin_descenso DOUBLE PRECISION,
                    pct_rango_potencia_sin_descenso DOUBLE PRECISION,
                    pct_exceso_rpm_sin_descenso DOUBLE PRECISION,
                    pct_rango_potencia_ineficiente_sin_descenso DOUBLE PRECISION,
                    tiempo_total_en_rango_sin_descenso DOUBLE PRECISION,
                    revision BOOLEAN
                )
            """)
            )
            await session.execute(
                text("""
                ALTER TABLE analytics.fact_combustible_daily
                    ADD COLUMN IF NOT EXISTS comb_ralenti DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS fuel_kind TEXT,
                    ADD COLUMN IF NOT EXISTS fuel_unit TEXT,
                    ADD COLUMN IF NOT EXISTS kms_effective DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS distance_source TEXT,
                    ADD COLUMN IF NOT EXISTS distance_quality_status TEXT,
                    ADD COLUMN IF NOT EXISTS distance_quality_fingerprint TEXT
            """)
            )
            await session.execute(
                text("""
                CREATE TABLE IF NOT EXISTS analytics.fact_fault_event (
                    row_id TEXT PRIMARY KEY,
                    vehicle_id TEXT,
                    database_name TEXT,
                    date_key BIGINT,
                    diagnostic_sk TEXT,
                    controller_sk TEXT,
                    failure_mode_sk TEXT,
                    fecha DATE,
                    movil TEXT,
                    fecha_de_falla TIMESTAMPTZ,
                    codigo_diagnostico BIGINT,
                    codigo_modo_de_falla DOUBLE PRECISION,
                    nombre_fuente_diagnostico TEXT,
                    codigo_controlador BIGINT,
                    estado_de_falla TEXT,
                    recuento_de_fallos BIGINT,
                    tipo_de_atencion TEXT,
                    luz_de_parada_amber BOOLEAN,
                    luz_de_parada_roja BOOLEAN,
                    lampara_de_averia BOOLEAN,
                    lampara_de_advertencia BOOLEAN,
                    nombre_de_controlador TEXT,
                    diagnostico TEXT,
                    modo_de_falla TEXT
                )
            """)
            )

            await session.execute(
                text("""
                INSERT INTO analytics.dim_vehicle
                    (vehicle_id, device_id, vehicle_label, database_name,
                     fuel_kind, fuel_unit, is_active)
                VALUES
                    (:v1, :d1, 'Grp A1', :dba, 'liquid', 'gal', true),
                    (:v2, :d2, 'Grp A2', :dba, 'liquid', 'gal', true),
                    (:v3, :d3, 'Sin grupo', :dba, 'liquid', 'gal', true),
                    (:vb, :db_dev, 'Flota B', :dbb, 'liquid', 'gal', true)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    database_name = EXCLUDED.database_name,
                    is_active = EXCLUDED.is_active
            """),
                {
                    "v1": _VID_G1,
                    "d1": _DEV_G1,
                    "v2": _VID_G2,
                    "d2": _DEV_G2,
                    "v3": _VID_NOG,
                    "d3": _DEV_NOG,
                    "vb": _VID_B,
                    "db_dev": _DEV_B,
                    "dba": _DB_NAME_A,
                    "dbb": _DB_NAME_B,
                },
            )
            # Un registro diario por vehículo con valores fáciles de sumar.
            comb_rows = [
                (_FACT_IDS[0], _VID_G1, _DB_NAME_A, 100.0, 10.0, 20.0, 2.0),
                (_FACT_IDS[1], _VID_G2, _DB_NAME_A, 200.0, 20.0, 40.0, 4.0),
                (_FACT_IDS[2], _VID_NOG, _DB_NAME_A, 50.0, 5.0, 10.0, 1.0),
                (_FACT_IDS[3], _VID_B, _DB_NAME_B, 999.0, 9.0, 111.0, 9.0),
            ]
            for fid, vid, dbname, kms, hrs, comb, ralenti in comb_rows:
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_combustible_daily
                        (fact_row_id, vehicle_id, database_name, date_key, fecha,
                         kms_ecm, kms_gps, kms_effective, hrs_ecm, comb, comb_ralenti,
                         fuel_kind, fuel_unit, distance_source,
                         distance_quality_status, distance_quality_fingerprint,
                         revision)
                    VALUES
                        (:fid, :vid, :dbname, 20260101, '2026-01-01',
                         :kms, :kms, :kms, :hrs, :comb, :ralenti,
                         'liquid', 'gal', 'ecm', 'ok', :fp, false)
                    ON CONFLICT (fact_row_id) DO UPDATE SET
                        kms_effective = EXCLUDED.kms_effective,
                        hrs_ecm = EXCLUDED.hrs_ecm,
                        comb = EXCLUDED.comb,
                        comb_ralenti = EXCLUDED.comb_ralenti
                """),
                    {
                        "fid": fid,
                        "vid": vid,
                        "dbname": dbname,
                        "kms": kms,
                        "hrs": hrs,
                        "comb": comb,
                        "ralenti": ralenti,
                        "fp": "c" * 64,
                    },
                )
            # Fallas: dos en v1 (una del bus, urgente; una del propio equipo
            # telemático, no urgente) y una en la flota B.
            fault_rows = [
                (_FAULT_IDS[0], _VID_G1, "Falla de frenos A", "SourceJ1939Id", True),
                (_FAULT_IDS[1], _VID_G1, "Reinicio del equipo", "SourceGeotabGoId", False),
                (_FAULT_IDS[2], _VID_B, "Falla ajena B", "SourceJ1939Id", True),
            ]
            for rid, vid, diag, fuente, roja in fault_rows:
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_fault_event
                        (row_id, vehicle_id, date_key, fecha, fecha_de_falla,
                         codigo_diagnostico, diagnostico, nombre_de_controlador,
                         modo_de_falla, nombre_fuente_diagnostico, estado_de_falla,
                         recuento_de_fallos, tipo_de_atencion,
                         luz_de_parada_roja, luz_de_parada_amber)
                    VALUES (:rid, :vid, 20260101, '2026-01-01',
                            '2026-01-01T08:00:00Z', 1234, :diag, 'Controller',
                            'Above range', :fuente, 'Active', 1,
                            'Nivel 1 - Urgente', :roja, false)
                    ON CONFLICT (row_id) DO NOTHING
                """),
                    {
                        "rid": rid,
                        "vid": vid,
                        "diag": diag,
                        "fuente": fuente,
                        "roja": roja,
                    },
                )
            await session.commit()
            return {
                "fleet_a": fleet_a,
                "fleet_b": fleet_b,
                "group_a1": str(group_a1.id),
                "group_a2": str(group_a2.id),
                "group_b": str(group_b.id),
            }

    ids = asyncio.run(_seed())

    yield ids

    async def _teardown() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM analytics.fact_fault_event WHERE row_id = ANY(:ids)"),
                {"ids": _FAULT_IDS},
            )
            await session.execute(
                text(
                    "DELETE FROM analytics.fact_combustible_daily "
                    "WHERE fact_row_id = ANY(:ids)"
                ),
                {"ids": _FACT_IDS},
            )
            await session.execute(
                text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = ANY(:ids)"),
                {"ids": [_VID_G1, _VID_G2, _VID_NOG, _VID_B]},
            )
            await session.execute(
                delete(Vehicle).where(
                    Vehicle.geotab_device_id.in_([_DEV_G1, _DEV_G2, _DEV_NOG, _DEV_B])
                )
            )
            await session.execute(
                delete(FleetVehicleGroup).where(
                    FleetVehicleGroup.id.in_(
                        [
                            uuid.UUID(ids["group_a1"]),
                            uuid.UUID(ids["group_a2"]),
                            uuid.UUID(ids["group_b"]),
                        ]
                    )
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


# ---------------------------------------------------------------------------
# Combustible
# ---------------------------------------------------------------------------


def test_combustible_por_grupo_buckets(
    admin_client: TestClient, group_seed: dict[str, str]
) -> None:
    """Dos grupos + un vehículo sin grupo → 3 buckets con sumas correctas."""
    resp = admin_client.get(
        "/api/v1/reportes/combustible/por-grupo",
        headers={"X-Fleet-Id": group_seed["fleet_a"]},
        params=_DATE_PARAMS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_gid = {b["group_id"]: b for b in body}
    assert set(by_gid) == {group_seed["group_a1"], group_seed["group_a2"], None}

    a1 = by_gid[group_seed["group_a1"]]
    assert a1["n_vehiculos"] == 1
    assert a1["n_registros"] == 1
    assert a1["kms"] == pytest.approx(100.0)
    assert a1["comb"] == pytest.approx(20.0)
    assert a1["hrs"] == pytest.approx(10.0)
    assert a1["comb_ralenti"] == pytest.approx(2.0)

    a2 = by_gid[group_seed["group_a2"]]
    assert a2["kms"] == pytest.approx(200.0)
    assert a2["comb"] == pytest.approx(40.0)

    # El bucket None existe: el hecho del vehículo sin grupo no se pierde.
    sin_grupo = by_gid[None]
    assert sin_grupo["kms"] == pytest.approx(50.0)
    assert sin_grupo["comb"] == pytest.approx(10.0)
    assert sin_grupo["n_vehiculos"] == 1

    # Orden estable: kms DESC → A2 primero.
    assert body[0]["group_id"] == group_seed["group_a2"]


# ---------------------------------------------------------------------------
# Fallas
# ---------------------------------------------------------------------------


def test_fallas_por_grupo_exclude_telematics_flag(
    admin_client: TestClient, group_seed: dict[str, str]
) -> None:
    """El flag excluye SourceGeotabGoId; sin el flag no se excluye nada."""
    headers = {"X-Fleet-Id": group_seed["fleet_a"]}

    resp = admin_client.get(
        "/api/v1/reportes/fallas/por-grupo",
        headers=headers,
        params=_DATE_PARAMS,
    )
    assert resp.status_code == 200, resp.text
    by_gid = {b["group_id"]: b for b in resp.json()}
    a1 = by_gid[group_seed["group_a1"]]
    assert a1["n_eventos"] == 2
    assert a1["n_fallas"] == 2
    assert a1["n_urgentes"] == 1
    assert a1["n_vehiculos"] == 1

    resp = admin_client.get(
        "/api/v1/reportes/fallas/por-grupo",
        headers=headers,
        params={**_DATE_PARAMS, "exclude_telematics": "true"},
    )
    assert resp.status_code == 200, resp.text
    by_gid = {b["group_id"]: b for b in resp.json()}
    a1 = by_gid[group_seed["group_a1"]]
    assert a1["n_eventos"] == 1
    assert a1["n_fallas"] == 1
    assert a1["n_urgentes"] == 1


# ---------------------------------------------------------------------------
# Negativa de alcance
# ---------------------------------------------------------------------------


def test_por_grupo_scope_negativo(
    admin_client: TestClient, group_seed: dict[str, str]
) -> None:
    """Un usuario de la flota A no recibe datos de la flota B en ningún bucket.

    Los hechos de B no aparecen ni bajo su group_id ni disfrazados de
    "sin grupo" (el bucket None solo suma vehículos de A).
    """
    suffix = uuid.uuid4().hex[:8]
    role_code = f"grp_reporter_{suffix}"
    role_resp = admin_client.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": f"Reportero grupos {suffix}",
            "permission_codes": ["reportes.view"],
        },
    )
    assert role_resp.status_code == 201, role_resp.text

    email = f"grpagg-{suffix}@portalclientes.test"
    password = "GrpAgg123!"
    created = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Usuario grupos A",
            "role_codes": [role_code],
            "fleet_ids": [group_seed["fleet_a"]],
        },
    )
    assert created.status_code == 201, created.text

    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text

    # Combustible: solo los 350 km de A; los 999 de B no entran a ningún bucket.
    resp = client.get("/api/v1/reportes/combustible/por-grupo", params=_DATE_PARAMS)
    assert resp.status_code == 200, resp.text
    buckets = resp.json()
    assert group_seed["group_b"] not in {b["group_id"] for b in buckets}
    assert sum(b["kms"] for b in buckets) == pytest.approx(350.0)

    # Fallas: solo los 2 eventos de v1 (flota A); la falla de B desaparece.
    resp = client.get("/api/v1/reportes/fallas/por-grupo", params=_DATE_PARAMS)
    assert resp.status_code == 200, resp.text
    buckets = resp.json()
    assert group_seed["group_b"] not in {b["group_id"] for b in buckets}
    assert sum(b["n_eventos"] for b in buckets) == 2

    # Hábitos: sin eventos sembrados salen buckets con kms para normalizar,
    # y esos kms tampoco incluyen la flota B.
    resp = client.get("/api/v1/reportes/habitos/por-grupo", params=_DATE_PARAMS)
    assert resp.status_code == 200, resp.text
    buckets = resp.json()
    assert group_seed["group_b"] not in {b["group_id"] for b in buckets}
    assert sum(b["n_eventos"] for b in buckets) == 0
    assert sum(b["kms"] for b in buckets) == pytest.approx(350.0)
