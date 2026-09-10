"""Tests del módulo reportes: endpoints + service unit.

Los tests que requieren tablas analytics.* crean el schema con seed data en un
fixture de sesión y lo limpian al terminar. Marcan @pytest.mark.integration para
poder skipearlos si PG no está disponible.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.analytics import FactFaultEvent
from app.models.fleet import Fleet
from app.models.master_data import GeotabDatabase, Vehicle
from app.models.user import User
from app.services.analytics_service import _apply_fault_filters, _date_to_key

# ---------------------------------------------------------------------------
# Unit — sin DB
# ---------------------------------------------------------------------------


def test_date_to_key() -> None:
    assert _date_to_key(date(2026, 3, 19)) == 20260319
    assert _date_to_key(date(2025, 1, 1)) == 20250101
    assert _date_to_key(date(2024, 12, 31)) == 20241231


def test_fault_filters_exclude_unknown_diagnostics() -> None:
    stmt = _apply_fault_filters(
        select(FactFaultEvent),
        vehicle_id=None,
        date_from=None,
        date_to=None,
        severity=None,
        only_urgent=False,
    )

    compiled = stmt.compile()
    sql = str(compiled).lower()
    assert "diagnostico" in sql
    assert "not like" in sql
    assert "unknown%" in compiled.params.values()


def test_fault_filters_apply_selected_pareto_dimension() -> None:
    stmt = _apply_fault_filters(
        select(FactFaultEvent),
        vehicle_id=None,
        date_from=None,
        date_to=None,
        severity=None,
        only_urgent=False,
        fault_dimension="diagnostico",
        fault_value="Brake fault",
    )

    compiled = stmt.compile()
    assert "diagnostico" in str(compiled).lower()
    assert "Brake fault" in compiled.params.values()


# ---------------------------------------------------------------------------
# Fixture analytics seed (schema efímero)
# ---------------------------------------------------------------------------

_SEED_VEHICLE_ID = "TEST-VEH-001"
_SEED_DEVICE_ID = "TEST-DEVICE-001"
_SEED_FACT_ID = str(uuid.uuid4())
_SEED_GAS_VEHICLE_ID = "TEST-VEH-GAS-001"
_SEED_GAS_DEVICE_ID = "TEST-DEVICE-GAS-001"
_SEED_GAS_FACT_ID = str(uuid.uuid4())
_SEED_FLEET_CODE = f"TEST-REPORTES-{uuid.uuid4().hex[:8]}"
_SEED_RULE_SK = "TEST-RULE-001"
_SEED_EVENT_ID = str(uuid.uuid4())
_SEED_RPM_RULE_SK = "TEST-RULE-RPM-001"
_SEED_RPM_EVENT_ID = str(uuid.uuid4())
_SEED_FAULT_ID = str(uuid.uuid4())


@pytest_asyncio.fixture(scope="module")
async def analytics_seed():
    """Siembra una fila en las tablas analytics.* (dim_vehicle, dim_date,
    fact_combustible_daily, dim_rule, fact_habito_event, fact_fault_event).

    NOTA: NO usa un schema aislado — escribe en el schema `analytics` real
    (los modelos ORM tienen el schema hardcodeado). Usa CREATE TABLE IF NOT
    EXISTS, así que si el loader de InformesRendimiento ya creó las tablas
    reales se respetan sus constraints, incluida la FK fact.*.date_key ->
    dim_date.date_key (por eso se siembra dim_date antes de los facts).
    El teardown elimina solo las filas sembradas, por id.
    """
    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(code=_SEED_FLEET_CODE, name="Flota Test Reportes", is_active=True)
            session.add(fleet)
            await session.flush()
            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name="test_db",
                database_key="test_db",
                connection_type="geotab",
                is_active=True,
            )
            session.add(geotab_db)
            await session.flush()
            session.add_all(
                [
                    Vehicle(
                        plate="ABC123",
                        geotab_device_id=_SEED_DEVICE_ID,
                        geotab_customer_status="found",
                        fleet_id=fleet.id,
                        geotab_database_id=geotab_db.id,
                        is_active=True,
                    ),
                    Vehicle(
                        plate="GAS001",
                        geotab_device_id=_SEED_GAS_DEVICE_ID,
                        geotab_customer_status="found",
                        fleet_id=fleet.id,
                        geotab_database_id=geotab_db.id,
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
                ALTER TABLE analytics.dim_vehicle
                    ADD COLUMN IF NOT EXISTS fuel_type_raw TEXT,
                    ADD COLUMN IF NOT EXISTS fuel_kind TEXT,
                    ADD COLUMN IF NOT EXISTS fuel_unit TEXT,
                    ADD COLUMN IF NOT EXISTS fuel_classification_source TEXT,
                    ADD COLUMN IF NOT EXISTS fuel_classification_conflict BOOLEAN
            """)
            )
            # DDL completo (espeja FactCombustibleDaily). CREATE IF NOT EXISTS:
            # si el loader ya creó la tabla real, no la toca.
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
            # Evolución aditiva para bases de prueba creadas por una revisión
            # anterior del contrato analytics.
            await session.execute(
                text("""
                ALTER TABLE analytics.fact_combustible_daily
                    ADD COLUMN IF NOT EXISTS comb_ralenti DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS gal_hr_ralenti DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS fuel_kind TEXT,
                    ADD COLUMN IF NOT EXISTS fuel_unit TEXT,
                    ADD COLUMN IF NOT EXISTS km_m3 DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS m3_hr DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS m3_hr_ralenti DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS kms_effective DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS km_gal_effective DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS km_m3_effective DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS velocidad_promedio_effective DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS distance_source TEXT,
                    ADD COLUMN IF NOT EXISTS distance_quality_status TEXT,
                    ADD COLUMN IF NOT EXISTS distance_quality_reason TEXT,
                    ADD COLUMN IF NOT EXISTS distance_diff_km DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS distance_diff_pct DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS gps_quality_valid BOOLEAN,
                    ADD COLUMN IF NOT EXISTS gps_trip_count BIGINT,
                    ADD COLUMN IF NOT EXISTS ecm_reading_count BIGINT,
                    ADD COLUMN IF NOT EXISTS distance_quality_fingerprint TEXT,
                    ADD COLUMN IF NOT EXISTS distance_threshold_version TEXT,
                    ADD COLUMN IF NOT EXISTS horas_ralenti_base DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS fuente_ralenti TEXT
            """)
            )
            # dim_date: la tabla real define FK fact.*.date_key -> dim_date.date_key.
            # Sembramos las fechas usadas por los facts para no violar la FK.
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
                    (20260101, '2026-01-01', 2026, 1, 1, 1, 202601, '2026-01-01'),
                    (20260319, '2026-03-19', 2026, 3, 19, 1, 202603, '2026-03-01')
                ON CONFLICT (date_key) DO NOTHING
            """)
            )
            # Upsert seed para idempotencia si corre múltiples veces
            await session.execute(
                text("""
                INSERT INTO analytics.dim_vehicle
                    (vehicle_id, device_id, vehicle_label, database_name, motor_type,
                     fuel_type_raw, fuel_kind, fuel_unit,
                     fuel_classification_source, fuel_classification_conflict, is_active)
                VALUES
                    (:vid, :device_id, 'Vehículo Test', 'test_db', 'F2.8',
                     'DIESEL', 'liquid', 'gal', 'vehicle_master', false, true),
                    (:gas_vid, :gas_device_id, 'Vehículo Gas', 'test_db', 'N15',
                     NULL, 'gas', 'm3', 'motor_n15_fallback', false, true)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    vehicle_label = EXCLUDED.vehicle_label,
                    database_name = EXCLUDED.database_name,
                    motor_type = EXCLUDED.motor_type,
                    fuel_type_raw = EXCLUDED.fuel_type_raw,
                    fuel_kind = EXCLUDED.fuel_kind,
                    fuel_unit = EXCLUDED.fuel_unit,
                    fuel_classification_source = EXCLUDED.fuel_classification_source,
                    fuel_classification_conflict = EXCLUDED.fuel_classification_conflict,
                    is_active = EXCLUDED.is_active
            """),
                {
                    "vid": _SEED_VEHICLE_ID,
                    "device_id": _SEED_DEVICE_ID,
                    "gas_vid": _SEED_GAS_VEHICLE_ID,
                    "gas_device_id": _SEED_GAS_DEVICE_ID,
                },
            )
            await session.execute(
                text("""
                INSERT INTO analytics.fact_combustible_daily
                    (fact_row_id, vehicle_id, database_name, motor_type, date_key, fecha, placa,
                     kms_ecm, kms_gps, kms_effective, hrs_ecm, comb, comb_ralenti,
                     km_gal, gal_hr, gal_hr_ralenti,
                     fuel_kind, fuel_unit, km_m3, m3_hr, m3_hr_ralenti,
                     velocidad_promedio, km_gal_effective, km_m3_effective,
                     velocidad_promedio_effective, distance_source,
                     distance_quality_status, distance_quality_reason,
                     gps_quality_valid, distance_quality_fingerprint,
                     distance_threshold_version, ralenti_ecm, pct_ralenti, pct_exceso_rpm,
                     pct_rango_bajo, revision)
                VALUES
                    (:fid, :vid, 'test_db', 'F2.8', 20260101, '2026-01-01', 'ABC123',
                     250.5, 245.0, 250.5, 10.0, 30.2, 3.6, 8.3, 1.25, 1.2,
                     'liquid', 'gal', NULL, NULL, NULL,
                     42.0, 8.3, NULL, 25.05, 'ecm', 'ok', 'ok', true,
                     'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'v1',
                     3.0, 0.15, 0.02, 0.40, false),
                    (:gas_fid, :gas_vid, 'test_db', 'N15', 20260101, '2026-01-01', 'GAS001',
                     120.0, 118.0, 120.0, 6.0, 24.0, 3.0, NULL, NULL, NULL,
                     'gas', 'm3', 5.0, 4.0, 1.5,
                     20.0, NULL, 5.0, 20.0, 'ecm', 'ok', 'ok', true,
                     'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'v1',
                     2.0, 0.10, 0.01, 0.30, false)
                ON CONFLICT (fact_row_id) DO UPDATE SET
                    database_name = EXCLUDED.database_name,
                    motor_type = EXCLUDED.motor_type,
                    comb_ralenti = EXCLUDED.comb_ralenti,
                    gal_hr_ralenti = EXCLUDED.gal_hr_ralenti,
                    fuel_kind = EXCLUDED.fuel_kind,
                    fuel_unit = EXCLUDED.fuel_unit,
                    km_m3 = EXCLUDED.km_m3,
                    m3_hr = EXCLUDED.m3_hr,
                    m3_hr_ralenti = EXCLUDED.m3_hr_ralenti,
                    kms_gps = EXCLUDED.kms_gps,
                    kms_effective = EXCLUDED.kms_effective,
                    distance_source = EXCLUDED.distance_source,
                    distance_quality_status = EXCLUDED.distance_quality_status,
                    distance_quality_reason = EXCLUDED.distance_quality_reason,
                    gps_quality_valid = EXCLUDED.gps_quality_valid,
                    distance_quality_fingerprint = EXCLUDED.distance_quality_fingerprint,
                    distance_threshold_version = EXCLUDED.distance_threshold_version,
                    ralenti_ecm = EXCLUDED.ralenti_ecm
            """),
                {
                    "fid": _SEED_FACT_ID,
                    "vid": _SEED_VEHICLE_ID,
                    "gas_fid": _SEED_GAS_FACT_ID,
                    "gas_vid": _SEED_GAS_VEHICLE_ID,
                },
            )
            # dim_rule + fact_habito_event para los tests de hábitos seguros.
            await session.execute(
                text("""
                CREATE TABLE IF NOT EXISTS analytics.dim_rule (
                    rule_sk TEXT PRIMARY KEY,
                    rule_id TEXT,
                    rule_name TEXT,
                    source_script TEXT,
                    scope_type TEXT,
                    scope_value TEXT,
                    categoria TEXT
                )
            """)
            )
            await session.execute(
                text("""
                CREATE TABLE IF NOT EXISTS analytics.fact_habito_event (
                    event_sk TEXT PRIMARY KEY,
                    event_id TEXT,
                    vehicle_id TEXT,
                    database_name TEXT,
                    date_key BIGINT,
                    rule_sk TEXT,
                    rule_id TEXT,
                    event_type TEXT,
                    fecha DATE,
                    placa TEXT,
                    longitud DOUBLE PRECISION,
                    latitud DOUBLE PRECISION,
                    fecha_y_hora_del_evento TIMESTAMPTZ,
                    distancia_evento_mt DOUBLE PRECISION,
                    evento_resumen TEXT,
                    duracion_evento DOUBLE PRECISION,
                    observacion_corta TEXT
                )
            """)
            )
            # Métricas numéricas del evento (2026-08-27). El loader del ETL las
            # añade con ALTER TABLE ADD COLUMN IF NOT EXISTS y NO hay migración
            # Alembic (Alembic no gobierna `analytics`), así que el fixture debe
            # reproducir exactamente ese paso: el CREATE TABLE de arriba es
            # IF NOT EXISTS y no toca una tabla preexistente sin las columnas.
            # Sin esto, `select(FactHabitoEvent)` falla con UndefinedColumn en
            # toda la familia de hábitos.
            for _column, _type in (
                ("rpm", "DOUBLE PRECISION"),
                ("velocidad_kmh", "DOUBLE PRECISION"),
                ("carga_pct", "DOUBLE PRECISION"),
                ("g_force", "DOUBLE PRECISION"),
                ("g_axis", "TEXT"),
                ("event_value", "DOUBLE PRECISION"),
                ("event_value_unit", "TEXT"),
            ):
                await session.execute(
                    text(
                        "ALTER TABLE analytics.fact_habito_event "
                        f"ADD COLUMN IF NOT EXISTS {_column} {_type}"
                    )
                )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_rule (rule_sk, rule_id, rule_name, categoria)
                VALUES
                    (:rsk, 'R-001', 'Exceso Velocidad', 'Seguridad'),
                    (:rpm_rsk, 'R-RPM-001', 'Excesos de RPM', 'Seguridad')
                ON CONFLICT (rule_sk) DO UPDATE SET
                    rule_name = EXCLUDED.rule_name,
                    categoria = EXCLUDED.categoria
            """),
                {"rsk": _SEED_RULE_SK, "rpm_rsk": _SEED_RPM_RULE_SK},
            )
            await session.execute(
                text("""
                INSERT INTO analytics.fact_habito_event
                    (event_sk, event_id, vehicle_id, date_key, rule_sk, event_type,
                     fecha, placa, distancia_evento_mt, duracion_evento, observacion_corta,
                     fecha_y_hora_del_evento, latitud, longitud)
                VALUES
                    (:esk, :eid, :vid, 20260101, :rsk, 'Exceso Velocidad',
                     '2026-01-01', 'ABC123', 1500.0, 50.0, '80 km/h',
                     '2026-01-01T10:00:00Z', 4.6, -74.1),
                    (:rpm_esk, :rpm_eid, :vid, 20260101, :rpm_rsk, 'Excesos de RPM',
                     '2026-01-01', 'ABC123', 500.0, 20.0, '2500 RPM',
                     '2026-01-01T11:00:00Z', 4.6, -74.1)
                ON CONFLICT (event_sk) DO UPDATE SET
                    rule_sk = EXCLUDED.rule_sk,
                    event_type = EXCLUDED.event_type
            """),
                {
                    "esk": _SEED_EVENT_ID,
                    "eid": _SEED_EVENT_ID,
                    "rpm_esk": _SEED_RPM_EVENT_ID,
                    "rpm_eid": _SEED_RPM_EVENT_ID,
                    "vid": _SEED_VEHICLE_ID,
                    "rsk": _SEED_RULE_SK,
                    "rpm_rsk": _SEED_RPM_RULE_SK,
                },
            )
            # fact_fault_event para los tests de fallas.
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
                INSERT INTO analytics.fact_fault_event
                    (row_id, vehicle_id, date_key, fecha, movil, fecha_de_falla,
                     codigo_diagnostico, diagnostico, nombre_de_controlador, modo_de_falla,
                     estado_de_falla, recuento_de_fallos, tipo_de_atencion,
                     luz_de_parada_roja, luz_de_parada_amber, lampara_de_averia)
                VALUES (:rid, :vid, 20260101, '2026-01-01', 'ABC123',
                        '2026-01-01T08:00:00Z', 25795, 'Test Diagnostic',
                        'Brakes - system controller', 'Above range',
                        'Active', 12, 'Nivel 1 - Urgente', true, true, false)
                ON CONFLICT (row_id) DO NOTHING
            """),
                {"rid": _SEED_FAULT_ID, "vid": _SEED_VEHICLE_ID},
            )
            await session.execute(
                text(
                    "DELETE FROM distance_quality_decisions WHERE fact_row_id IN (:fid, :gas_fid)"
                ),
                {"fid": _SEED_FACT_ID, "gas_fid": _SEED_GAS_FACT_ID},
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM distance_quality_decisions WHERE fact_row_id IN (:fid, :gas_fid)"),
            {"fid": _SEED_FACT_ID, "gas_fid": _SEED_GAS_FACT_ID},
        )
        await session.execute(
            text("DELETE FROM analytics.fact_fault_event WHERE row_id = :rid"),
            {"rid": _SEED_FAULT_ID},
        )
        await session.execute(
            text("DELETE FROM analytics.fact_habito_event WHERE event_sk IN (:esk, :rpm_esk)"),
            {"esk": _SEED_EVENT_ID, "rpm_esk": _SEED_RPM_EVENT_ID},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_rule WHERE rule_sk IN (:rsk, :rpm_rsk)"),
            {"rsk": _SEED_RULE_SK, "rpm_rsk": _SEED_RPM_RULE_SK},
        )
        await session.execute(
            text(
                "DELETE FROM analytics.fact_combustible_daily WHERE fact_row_id IN (:fid, :gas_fid)"
            ),
            {"fid": _SEED_FACT_ID, "gas_fid": _SEED_GAS_FACT_ID},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id IN (:vid, :gas_vid)"),
            {"vid": _SEED_VEHICLE_ID, "gas_vid": _SEED_GAS_VEHICLE_ID},
        )
        await session.execute(
            delete(Vehicle).where(
                Vehicle.geotab_device_id.in_([_SEED_DEVICE_ID, _SEED_GAS_DEVICE_ID])
            )
        )
        await session.execute(delete(Fleet).where(Fleet.code == _SEED_FLEET_CODE))
        await session.commit()


# ---------------------------------------------------------------------------
# Fixtures HTTP
# ---------------------------------------------------------------------------


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
def viewer_client(admin_client: TestClient) -> TestClient:
    """Usuario sin reportes.view.

    No sirve el rol de sistema `viewer`: las migraciones le conceden
    `reportes.view`, así que un rol propio y vacío es lo único que garantiza la
    negativa que estas pruebas comprueban.
    """
    role_code = f"sin_permisos_{uuid.uuid4().hex[:8]}"
    resp = admin_client.post(
        "/api/v1/roles",
        json={"code": role_code, "name": "Sin permisos", "permission_codes": []},
    )
    assert resp.status_code == 201, resp.text
    email = f"noreport-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "NoReport123!"
    resp = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "No Report",
            "role_codes": [role_code],
        },
    )
    assert resp.status_code == 201, resp.text

    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


# ---------------------------------------------------------------------------
# Auth / permission tests (no necesitan analytics tables)
# ---------------------------------------------------------------------------


def test_vehiculos_no_permission(viewer_client: TestClient) -> None:
    resp = viewer_client.get("/api/v1/reportes/vehiculos")
    assert resp.status_code == 403


def test_motor_types_no_permission(viewer_client: TestClient) -> None:
    resp = viewer_client.get("/api/v1/reportes/motor-types")
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.integration
async def test_motor_types_admin_responds(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get("/api/v1/reportes/motor-types")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # El seed no setea motor_type en dim_vehicle, así que la lista puede ser [].
    # Validamos forma: cada item expone motor_type y n_vehiculos.
    for item in body:
        assert "motor_type" in item
        assert "n_vehiculos" in item


def test_vehiculos_unauthenticated() -> None:
    c = TestClient(app)
    resp = c.get("/api/v1/reportes/vehiculos")
    assert resp.status_code == 401


def test_calificacion_no_permission(viewer_client: TestClient) -> None:
    resp = viewer_client.get("/api/v1/reportes/calificacion")
    assert resp.status_code == 403


def test_calificacion_bad_date_range(admin_client: TestClient) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/calificacion",
        params={"date_from": "2026-02-01", "date_to": "2026-01-01"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_admin_responds(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get("/api/v1/reportes/calificacion")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "promedio_general",
        "estado",
        "umbrales",
        "metodologia",
        # La calibración con la que se calculó la respuesta viaja con ella:
        # sin este campo la pantalla no puede distinguir un umbral de la flota
        # de un default, ni avisar de un alcance con calibraciones mezcladas.
        "configuracion",
        "evolucion",
        "operativos",
        "vehiculos",
    }
    assert body["configuracion"]["origen"] in {"defecto", "flota", "mixto"}
    # La explicación que ve el usuario se sirve desde las constantes del cálculo,
    # para que no quede describiendo una calibración vieja.
    metodologia = body["metodologia"]
    assert metodologia["peso_qho"] + metodologia["peso_qhs"] == pytest.approx(1.0)
    assert metodologia["componentes_qho"]
    assert sum(c["peso"] for c in metodologia["componentes_qho"]) == pytest.approx(1.0)
    assert metodologia["eventos_tope_por_1000km"] > 0
    assert metodologia["eventos_excluidos"]  # los RPM no puntúan dos veces
    assert set(body["estado"]) == {"no_cumple", "en_riesgo", "cumple"}
    # Los umbrales se publican para que el gauge del front no los duplique:
    # cuando los tenía hardcodeados, pintaba "No cumple" sobre un valor que la
    # tabla clasificaba como "Cumple".
    umbrales = body["umbrales"]
    assert set(umbrales) == {"en_riesgo", "cumple"}
    assert 0 < umbrales["en_riesgo"] < umbrales["cumple"] <= 100
    assert isinstance(body["evolucion"], list)
    assert isinstance(body["operativos"], list)
    assert isinstance(body["vehiculos"], list)
    for point in body["operativos"]:
        assert {
            "periodo",
            "label",
            "pct_eficiente",
            "pct_ralenti",
            "eventos_rpm",
            # Renombrado el 2026-08-27: la agravación x2 se compara contra la
            # gobernada del motor de cada vehículo, no contra un 2100 fijo.
            "eventos_rpm_sobre_gobernada",
        } <= set(point)
    for v in body["vehiculos"]:
        assert {
            "vehicle_id",
            "placa",
            "vocacional",
            "eventos_rpm",
            "eventos_rpm_sobre_gobernada",
            "qhs",
            "qho",
            "qgen",
            "estado",
        } <= set(v)
        # QGen se clasifica de forma consistente con los umbrales publicados.
        if v["estado"] is not None:
            assert v["estado"] in {"No cumple", "En riesgo", "Cumple"}
        if v["qgen"] is not None:
            # Verde = cumple; el amarillo intermedio es "En riesgo".
            if v["qgen"] < umbrales["en_riesgo"]:
                assert v["estado"] == "No cumple"
            elif v["qgen"] >= umbrales["cumple"]:
                assert v["estado"] == "Cumple"
            else:
                assert v["estado"] == "En riesgo"


def test_combustible_daily_limit_cap(admin_client: TestClient) -> None:
    resp = admin_client.get("/api/v1/reportes/combustible/daily", params={"limit": 999})
    assert resp.status_code == 422


def test_combustible_daily_no_permission(viewer_client: TestClient) -> None:
    resp = viewer_client.get("/api/v1/reportes/combustible/daily")
    assert resp.status_code == 403


def test_summary_no_permission(viewer_client: TestClient) -> None:
    resp = viewer_client.get("/api/v1/reportes/combustible/summary")
    assert resp.status_code == 403


def test_timeseries_no_permission(viewer_client: TestClient) -> None:
    resp = viewer_client.get("/api/v1/reportes/combustible/timeseries")
    assert resp.status_code == 403


def test_ranking_no_permission(viewer_client: TestClient) -> None:
    resp = viewer_client.get("/api/v1/reportes/combustible/ranking")
    assert resp.status_code == 403


def test_timeseries_bad_granularity(admin_client: TestClient) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/combustible/timeseries", params={"granularity": "weekly"}
    )
    assert resp.status_code == 422


def test_combustible_bad_fuel_kind(admin_client: TestClient) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/combustible/summary",
        params={"fuel_kind": "mezclado"},
    )
    assert resp.status_code == 422


def test_combustible_rejects_ranking_metric_from_another_unit(
    admin_client: TestClient,
) -> None:
    gas_as_gallons = admin_client.get(
        "/api/v1/reportes/combustible/ranking",
        params={"fuel_kind": "gas", "metric": "km_gal"},
    )
    liquid_as_gas = admin_client.get(
        "/api/v1/reportes/combustible/ranking",
        params={"fuel_kind": "liquid", "metric": "km_m3"},
    )

    gas_gallons_per_hour = admin_client.get(
        "/api/v1/reportes/combustible/ranking",
        params={"fuel_kind": "gas", "metric": "gal_hr"},
    )
    liquid_m3_per_hour = admin_client.get(
        "/api/v1/reportes/combustible/ranking",
        params={"fuel_kind": "liquid", "metric": "m3_hr"},
    )

    assert gas_as_gallons.status_code == 422
    assert liquid_as_gas.status_code == 422
    assert gas_gallons_per_hour.status_code == 422
    assert liquid_m3_per_hour.status_code == 422


def test_ranking_limit_cap(admin_client: TestClient) -> None:
    resp = admin_client.get("/api/v1/reportes/combustible/ranking", params={"limit": 999})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/reportes/habitos/summary",
        "/api/v1/reportes/habitos/by-type",
        "/api/v1/reportes/habitos/timeseries",
        "/api/v1/reportes/habitos/ranking",
        "/api/v1/reportes/habitos/events",
    ],
)
def test_habitos_no_permission(viewer_client: TestClient, path: str) -> None:
    assert viewer_client.get(path).status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/reportes/habitos/summary",
        "/api/v1/reportes/habitos/events",
    ],
)
def test_habitos_unauthenticated(path: str) -> None:
    assert TestClient(app).get(path).status_code == 401


def test_habitos_events_limit_cap(admin_client: TestClient) -> None:
    resp = admin_client.get("/api/v1/reportes/habitos/events", params={"limit": 999})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/reportes/fallas/summary",
        "/api/v1/reportes/fallas/by-severity",
        "/api/v1/reportes/fallas/pareto",
        "/api/v1/reportes/fallas/timeseries",
        "/api/v1/reportes/fallas/ranking",
        "/api/v1/reportes/fallas/events",
    ],
)
def test_fallas_no_permission(viewer_client: TestClient, path: str) -> None:
    assert viewer_client.get(path).status_code == 403


def test_fallas_unauthenticated() -> None:
    assert TestClient(app).get("/api/v1/reportes/fallas/summary").status_code == 401


def test_fallas_pareto_bad_dimension(admin_client: TestClient) -> None:
    resp = admin_client.get("/api/v1/reportes/fallas/pareto", params={"dimension": "inexistente"})
    assert resp.status_code == 422


def test_fallas_events_limit_cap(admin_client: TestClient) -> None:
    resp = admin_client.get("/api/v1/reportes/fallas/events", params={"limit": 999})
    assert resp.status_code == 422


def test_combustible_daily_unauthenticated() -> None:
    c = TestClient(app)
    resp = c.get("/api/v1/reportes/combustible/daily")
    assert resp.status_code == 401


def test_distance_resolution_requires_platform_admin(viewer_client: TestClient) -> None:
    resp = viewer_client.post(
        "/api/v1/reportes/combustible/unknown/distance-resolution",
        json={
            "action": "exclude",
            "reason": "Dato anómalo confirmado",
            "expected_fingerprint": "a" * 64,
        },
    )
    assert resp.status_code == 403


def test_distance_anomalies_require_platform_admin(viewer_client: TestClient) -> None:
    resp = viewer_client.get("/api/v1/data-quality/distance-anomalies")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Integration — requieren analytics schema con seed data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_vehiculos_admin_responds(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get("/api/v1/reportes/vehiculos")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert any(v["vehicle_id"] == _SEED_VEHICLE_ID for v in body)
    gas = next(v for v in body if v["vehicle_id"] == _SEED_GAS_VEHICLE_ID)
    assert gas["motor_type"] == "N15"
    assert gas["fuel_kind"] == "gas"
    assert gas["fuel_unit"] == "m3"
    assert gas["fuel_classification_source"] == "motor_n15_fallback"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_combustible_daily_admin_responds(
    admin_client: TestClient, analytics_seed: None
) -> None:
    resp = admin_client.get("/api/v1/reportes/combustible/daily")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert body["total"] >= 1
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert all(item["fuel_kind"] == "liquid" for item in body["items"])


@pytest.mark.asyncio
@pytest.mark.integration
async def test_combustible_daily_pagination_params(
    admin_client: TestClient, analytics_seed: None
) -> None:
    resp = admin_client.get("/api/v1/reportes/combustible/daily", params={"limit": 5, "offset": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 5
    assert body["offset"] == 0
    assert len(body["items"]) <= 5


@pytest.mark.asyncio
@pytest.mark.integration
async def test_combustible_daily_vehicle_filter(
    admin_client: TestClient, analytics_seed: None
) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"vehicle_id": _SEED_VEHICLE_ID},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(item["vehicle_id"] == _SEED_VEHICLE_ID for item in body["items"])


@pytest.mark.asyncio
@pytest.mark.integration
async def test_combustible_daily_exposes_resolved_kpis_without_quality_metadata(
    admin_client: TestClient, analytics_seed: None
) -> None:
    """El schema ampliado expone los KPIs de rango RPM y ralentí."""
    resp = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"vehicle_id": _SEED_VEHICLE_ID},
    )
    assert resp.status_code == 200
    item = next(i for i in resp.json()["items"] if i["fact_row_id"] == _SEED_FACT_ID)
    # Campos clave presentes con los valores sembrados.
    assert item["motor_type"] == "F2.8"
    assert item["pct_ralenti"] == 0.15
    assert item["pct_exceso_rpm"] == 0.02
    assert item["pct_rango_bajo"] == 0.40
    assert item["gal_hr"] == 1.25
    assert item["comb_ralenti"] == 3.6
    assert item["gal_hr_ralenti"] == 1.2
    assert item["kms_ecm"] == pytest.approx(250.5)
    assert item["velocidad_promedio"] == pytest.approx(25.05)
    # El contrato público no revela fuentes, diagnósticos ni decisiones.
    for key in (
        "kms_gps",
        "kms_effective",
        "distance_source",
        "distance_quality_status",
        "distance_quality_reason",
        "distance_quality_fingerprint",
        "resolution_action",
        "km_gal_gps",
        "pct_ralenti_ecm",
        "revision",
    ):
        assert key not in item
    assert "pct_rango_economico" in item


@pytest.mark.asyncio
@pytest.mark.integration
async def test_distance_resolution_is_immediate_append_only(
    admin_client: TestClient, analytics_seed: None
) -> None:
    payload = {
        "reason": "Corrección verificada para la prueba",
        "expected_fingerprint": "a" * 64,
    }
    use_gps = admin_client.post(
        f"/api/v1/reportes/combustible/{_SEED_FACT_ID}/distance-resolution",
        json={**payload, "action": "use_gps"},
    )
    assert use_gps.status_code == 200
    detail = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"vehicle_id": _SEED_VEHICLE_ID},
    ).json()
    item = next(i for i in detail["items"] if i["fact_row_id"] == _SEED_FACT_ID)
    assert item["kms_ecm"] == pytest.approx(245.0)
    assert item["km_gal"] == pytest.approx(245.0 / 30.2)
    assert "distance_source" not in item

    excluded = admin_client.post(
        f"/api/v1/reportes/combustible/{_SEED_FACT_ID}/distance-resolution",
        json={**payload, "action": "exclude"},
    )
    assert excluded.status_code == 200
    excluded_detail = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"vehicle_id": _SEED_VEHICLE_ID},
    ).json()
    excluded_item = next(i for i in excluded_detail["items"] if i["fact_row_id"] == _SEED_FACT_ID)
    assert excluded_item["kms_ecm"] is None
    assert excluded_item["km_gal"] is None
    assert excluded_item["velocidad_promedio"] is None
    excluded_summary = admin_client.get(
        "/api/v1/reportes/combustible/summary",
        params={"vehicle_id": _SEED_VEHICLE_ID},
    ).json()
    assert excluded_summary["kms_ecm"] == 0
    assert excluded_summary["km_gal"] is None
    assert excluded_summary["comb"] == pytest.approx(30.2)
    restored = admin_client.post(
        f"/api/v1/reportes/combustible/{_SEED_FACT_ID}/distance-resolution",
        json={**payload, "action": "restore_auto"},
    )
    assert restored.status_code == 200
    restored_detail = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"vehicle_id": _SEED_VEHICLE_ID},
    ).json()
    restored_item = next(i for i in restored_detail["items"] if i["fact_row_id"] == _SEED_FACT_ID)
    assert restored_item["kms_ecm"] == pytest.approx(250.5)
    assert "resolution_action" not in restored_item

    async with AsyncSessionLocal() as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM distance_quality_decisions "
                "WHERE fact_row_id = :fact_row_id AND origin = 'manual'"
            ),
            {"fact_row_id": _SEED_FACT_ID},
        )
    assert count == 3


@pytest.mark.asyncio
@pytest.mark.integration
async def test_distance_resolution_rejects_stale_fingerprint(
    admin_client: TestClient, analytics_seed: None
) -> None:
    resp = admin_client.post(
        f"/api/v1/reportes/combustible/{_SEED_FACT_ID}/distance-resolution",
        json={
            "action": "exclude",
            "reason": "La observación mostrada ya cambió",
            "expected_fingerprint": "c" * 64,
        },
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_combustible_daily_date_filters(
    admin_client: TestClient, analytics_seed: None
) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"date_from": "2026-01-01", "date_to": "2026-12-31", "limit": 10},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_combustible_daily_date_filter_no_results(
    admin_client: TestClient, analytics_seed: None
) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"date_from": "2020-01-01", "date_to": "2020-12-31"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_combustible_daily_motor_type_filter(
    admin_client: TestClient, analytics_seed: None
) -> None:
    """Filtro por motor_type (lista) acepta múltiples valores y aplica intersección."""
    # Filtro por el motor del seed (F2.8) — debe devolver la fila sembrada.
    resp = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"motor_type": ["F2.8"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(item["motor_type"] == "F2.8" for item in body["items"])

    # Filtro por un motor inexistente — debe devolver vacío.
    resp_empty = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"motor_type": ["NOEXISTE"]},
    )
    assert resp_empty.status_code == 200
    assert resp_empty.json()["total"] == 0
    assert resp_empty.json()["items"] == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_combustible_daily_motor_type_combined_with_vehicle(
    admin_client: TestClient, analytics_seed: None
) -> None:
    """motor_type y vehicle_id se intersecan."""
    resp = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"motor_type": ["F2.8"], "vehicle_id": _SEED_VEHICLE_ID},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(item["vehicle_id"] == _SEED_VEHICLE_ID for item in body["items"])


@pytest.mark.asyncio
@pytest.mark.integration
async def test_summary_aggregates(admin_client: TestClient, analytics_seed: None) -> None:
    """km/gal global = sum(kms)/sum(comb), no promedio de promedios."""
    resp = admin_client.get(
        "/api/v1/reportes/combustible/summary",
        params={"vehicle_id": _SEED_VEHICLE_ID},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Fila seed: kms_ecm=250.5, comb=30.2
    assert body["kms_ecm"] == pytest.approx(250.5)
    assert body["comb"] == pytest.approx(30.2)
    assert body["comb_ralenti"] == pytest.approx(3.6)
    assert body["km_gal"] == pytest.approx(250.5 / 30.2)
    assert body["gal_hr_ralenti"] == pytest.approx(3.6 / 3.0)
    assert body["n_vehiculos"] == 1
    assert body["n_registros"] >= 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_gas_summary_uses_m3_without_mixing_liquid_rows(
    admin_client: TestClient, analytics_seed: None
) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/combustible/summary",
        params={
            "fuel_kind": "gas",
            "vehicle_id": _SEED_GAS_VEHICLE_ID,
            "date_from": "2026-01-01",
            "date_to": "2026-01-01",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["fuel_kind"] == "gas"
    assert body["fuel_unit"] == "m3"
    assert body["n_vehiculos"] == 1
    assert body["kms_ecm"] == pytest.approx(120.0)
    assert body["comb"] == pytest.approx(24.0)
    assert body["km_m3"] == pytest.approx(5.0)
    assert body["m3_hr"] == pytest.approx(4.0)
    assert body["m3_hr_ralenti"] == pytest.approx(1.5)
    assert body["km_gal"] is None
    assert body["gal_hr"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_liquid_summary_excludes_gas_even_on_same_day(
    admin_client: TestClient, analytics_seed: None
) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/combustible/summary",
        params={"date_from": "2026-01-01", "date_to": "2026-01-01"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["fuel_kind"] == "liquid"
    assert body["fuel_unit"] == "gal"
    assert body["n_vehiculos"] == 1
    assert body["comb"] == pytest.approx(30.2)
    assert body["km_m3"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_timeseries_daily(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/combustible/timeseries",
        params={"granularity": "daily", "vehicle_id": _SEED_VEHICLE_ID},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    point = next(p for p in body if p["periodo"] == 20260101)
    assert point["kms_ecm"] == pytest.approx(250.5)
    assert point["km_gal"] == pytest.approx(250.5 / 30.2)
    assert point["comb_ralenti"] == pytest.approx(3.6)
    assert point["gal_hr_ralenti"] == pytest.approx(3.6 / 3.0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ranking_returns_seed_vehicle(admin_client: TestClient, analytics_seed: None) -> None:
    # El schema analytics es compartido con datos reales del loader; se acota al
    # día del seed (2026-01-01), donde no hay datos reales, para aislar el ranking.
    resp = admin_client.get(
        "/api/v1/reportes/combustible/ranking",
        params={"metric": "comb", "date_from": "2026-01-01", "date_to": "2026-01-01"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert any(item["vehicle_id"] == _SEED_VEHICLE_ID for item in body)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ranking_gal_hr_is_ratio_of_sums(
    admin_client: TestClient, analytics_seed: None
) -> None:
    """gal/h del ranking = comb / hrs_ecm de los días con consumo (30.2 / 10.0)."""
    resp = admin_client.get(
        "/api/v1/reportes/combustible/ranking",
        params={
            "metric": "gal_hr",
            "vehicle_id": _SEED_VEHICLE_ID,
            "date_from": "2026-01-01",
            "date_to": "2026-01-01",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["vehicle_id"] == _SEED_VEHICLE_ID
    assert body[0]["value"] == pytest.approx(3.02)

    gas = admin_client.get(
        "/api/v1/reportes/combustible/ranking",
        params={
            "metric": "m3_hr",
            "fuel_kind": "gas",
            "date_from": "2026-01-01",
            "date_to": "2026-01-01",
        },
    )
    assert gas.status_code == 200
    gas_values = {item["vehicle_id"]: item["value"] for item in gas.json()}
    assert gas_values[_SEED_GAS_VEHICLE_ID] == pytest.approx(4.0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habitos_summary_aggregates(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/habitos/summary", params={"vehicle_id": _SEED_VEHICLE_ID}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_eventos"] >= 1
    assert body["n_vehiculos"] >= 1
    assert body["distancia_total_mt"] >= 1500.0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habitos_by_type_distribution(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/habitos/by-type", params={"vehicle_id": _SEED_VEHICLE_ID}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert any(b["event_type"] == "Exceso Velocidad" for b in body)
    assert any(b["event_type"] == "Excesos de RPM" for b in body)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habitos_events_detail(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/habitos/events", params={"vehicle_id": _SEED_VEHICLE_ID}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    item = next(i for i in body["items"] if i["event_sk"] == _SEED_EVENT_ID)
    assert item["event_type"] == "Exceso Velocidad"
    assert item["rule_name"] == "Exceso Velocidad"
    assert item["categoria"] == "Seguridad"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habitos_event_type_filter(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/habitos/summary",
        params={"vehicle_id": _SEED_VEHICLE_ID, "event_type": "Frenada Brusca"},
    )
    assert resp.status_code == 200
    # El seed solo tiene 'Exceso Velocidad', así que filtrar por otro tipo da 0.
    assert resp.json()["n_eventos"] == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habitos_rpm_min_filter(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/habitos/events",
        params={"vehicle_id": _SEED_VEHICLE_ID, "rpm_min": 2100},
    )
    assert resp.status_code == 200
    assert any(item["event_sk"] == _SEED_RPM_EVENT_ID for item in resp.json()["items"])

    above_seed = admin_client.get(
        "/api/v1/reportes/habitos/events",
        params={"vehicle_id": _SEED_VEHICLE_ID, "rpm_min": 2600},
    )
    assert above_seed.status_code == 200
    assert all(item["event_sk"] != _SEED_RPM_EVENT_ID for item in above_seed.json()["items"])


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fallas_summary_aggregates(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/fallas/summary", params={"vehicle_id": _SEED_VEHICLE_ID}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_fallas"] >= 1
    assert body["n_urgentes"] >= 1  # seed tiene luz_de_parada_roja = true


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fallas_by_severity(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/fallas/by-severity", params={"vehicle_id": _SEED_VEHICLE_ID}
    )
    assert resp.status_code == 200
    assert any(b["severity"] == "Nivel 1 - Urgente" for b in resp.json())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fallas_pareto_controlador(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/fallas/pareto",
        params={"dimension": "controlador", "vehicle_id": _SEED_VEHICLE_ID},
    )
    assert resp.status_code == 200
    assert any(p["label"] == "Brakes - system controller" for p in resp.json())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fallas_events_only_urgent(admin_client: TestClient, analytics_seed: None) -> None:
    resp = admin_client.get(
        "/api/v1/reportes/fallas/events",
        params={"vehicle_id": _SEED_VEHICLE_ID, "only_urgent": "true"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(item["luz_de_parada_roja"] for item in body["items"])
    item = next(i for i in body["items"] if i["row_id"] == _SEED_FAULT_ID)
    assert item["tipo_de_atencion"] == "Nivel 1 - Urgente"


@pytest.mark.asyncio
@pytest.mark.asyncio
def _async_result(rows):
    """Crea un mock con .all() síncrono para db.execute() (que es awaitable)."""
    result = MagicMock()
    result.all.return_value = rows
    return result


@pytest.mark.asyncio
async def test_fleet_analytics_vehicle_ids_none_returns_none_no_db() -> None:
    """fleet_ids=None → retorna None y NO toca la DB."""
    from app.services import analytics_service

    db = MagicMock()
    db.execute = AsyncMock()
    out = await analytics_service.fleet_analytics_vehicle_ids(db, None)
    assert out is None
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_fleet_analytics_vehicle_ids_empty_returns_empty_set_no_db() -> None:
    """fleet_ids=[] → retorna set() y NO toca la DB."""
    from app.services import analytics_service

    db = MagicMock()
    db.execute = AsyncMock()
    out = await analytics_service.fleet_analytics_vehicle_ids(db, [])
    assert out == set()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_fleet_analytics_vehicle_ids_returns_scoped_ids_in_one_query() -> None:
    """Devuelve IDs delimitados y distintos mediante una sola consulta."""
    from app.services import analytics_service

    db = MagicMock()
    db.execute = AsyncMock(return_value=_async_result([("veh-a",), ("veh-shared",)]))
    fleet = uuid.uuid4()
    out = await analytics_service.fleet_analytics_vehicle_ids(db, [fleet])
    assert out == {"veh-a", "veh-shared"}
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_fleet_analytics_vehicle_ids_no_matches_returns_empty() -> None:
    """Si dim_vehicle no devuelve filas (in_scope vacío o sin matches),
    retorna set() con la única ejecución ya realizada."""
    from app.services import analytics_service

    db = MagicMock()
    db.execute = AsyncMock(return_value=_async_result([]))
    out = await analytics_service.fleet_analytics_vehicle_ids(db, [uuid.uuid4()])
    assert out == set()
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_fleet_analytics_vehicle_ids_scopes_by_database_and_device() -> None:
    """La identidad de alcance es database_key + device_id, no device_id solo."""
    from sqlalchemy.dialects import postgresql

    from app.services import analytics_service

    fleet = uuid.uuid4()
    db = MagicMock()
    db.execute = AsyncMock(return_value=_async_result([]))
    await analytics_service.fleet_analytics_vehicle_ids(db, [fleet])

    # Capturar la sentencia exacta pasada a db.execute.
    assert db.execute.await_count == 1
    (stmt,), _ = db.execute.call_args
    compiled = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    compiled_lower = compiled.lower()
    # SQLAlchemy puede renderizar "NOT (EXISTS ...)" con saltos de línea y
    # paréntesis; colapsamos espacios y normalizamos para comprobar el
    # predicado ("not exists" sin paréntesis interpuestos).
    collapsed = " ".join(compiled_lower.split()).replace("( ", "(").replace(" )", ")")
    assert "not exists" in collapsed.replace("(", "").replace(")", "")
    assert "device_id" in compiled_lower
    assert "database_key" in compiled_lower
    assert "database_name" in compiled_lower
    assert "geotab_database_id" in compiled_lower

    # Sanity: las dos aliases (candidate, outsider) están en el FROM.
    assert "candidate" in compiled_lower
    assert "outsider" in compiled_lower


@pytest.mark.asyncio
async def test_business_scope_stays_set_based_inside_final_query() -> None:
    """La ruta de negocio no materializa IDs ni construye un IN gigante."""
    from sqlalchemy.dialects import postgresql

    from app.services import analytics_service

    result = MagicMock()
    result.all.return_value = []
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    fleet = uuid.uuid4()

    with patch.object(
        analytics_service,
        "fleet_analytics_vehicle_ids",
        AsyncMock(side_effect=AssertionError("no debe materializar el scope")),
    ):
        await analytics_service.list_motor_types(db, [fleet])

    (stmt,), _ = db.execute.call_args
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()
    assert "not exists" in sql.replace("(", "").replace(")", "")
    assert "candidate" in sql
    assert "outsider" in sql
    assert "device_id" in sql
    assert "database_key" in sql
    assert "database_name" in sql
    assert "geotab_database_id" in sql


# ---------------------------------------------------------------------------
# Atomic — exactitud/validación (no requieren DB)
# ---------------------------------------------------------------------------


# 1) motor_type: el ID resuelto por _apply_motor_type_filter debe propagarse
#    al filtro de Fact*.vehicle_id (IN de vehículos) en los 3 rankings.
#    Se valida parcheando los helpers de filtros para capturar el vehicle_id
#    que el ranking realmente pasa al filtro.


@pytest.mark.asyncio
async def test_combustible_ranking_propagates_resolved_vehicle_id() -> None:
    """get_vehicle_ranking pasa el vehicle_id resuelto al filtro diario."""
    from app.services import analytics_service

    resolved = ["V-001", "V-002"]
    captured: dict[str, object] = {}

    def fake_daily_filters(stmt, *, vehicle_id, **kw):
        captured["vehicle_id"] = vehicle_id
        return stmt

    result = MagicMock()
    result.all.return_value = []
    result.one.return_value = (None, None, None, None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    with (
        patch.object(
            analytics_service,
            "fleet_analytics_vehicle_ids",
            AsyncMock(return_value={"V-001", "V-002", "V-003"}),
        ),
        patch.object(
            analytics_service,
            "_apply_motor_type_filter",
            AsyncMock(return_value=(resolved, False)),
        ),
        patch.object(
            analytics_service,
            "_apply_daily_filters",
            fake_daily_filters,
        ),
    ):
        await analytics_service.get_vehicle_ranking(
            db,
            metric="comb",
            motor_type=["F2.8"],
            fleet_ids=None,
        )

    # El filtro diario (sobre FactCombustibleDaily.vehicle_id) recibe la lista
    # resuelta, no None.
    assert captured["vehicle_id"] == resolved
    assert isinstance(captured["vehicle_id"], list)


@pytest.mark.asyncio
async def test_habito_ranking_propagates_resolved_vehicle_id() -> None:
    """get_habito_ranking pasa el vehicle_id resuelto al filtro de hábitos."""
    from app.services import analytics_service

    resolved = ["H-001", "H-002"]
    captured: dict[str, object] = {}

    def fake_habito_filters(stmt, **kw):
        captured["vehicle_id"] = kw.get("vehicle_id")
        return stmt

    result = MagicMock()
    result.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    with (
        patch.object(
            analytics_service,
            "fleet_analytics_vehicle_ids",
            AsyncMock(return_value={"H-001", "H-002"}),
        ),
        patch.object(
            analytics_service,
            "_apply_motor_type_filter",
            AsyncMock(return_value=(resolved, False)),
        ),
        patch.object(
            analytics_service,
            "_apply_habito_filters",
            fake_habito_filters,
        ),
    ):
        await analytics_service.get_habito_ranking(
            db,
            motor_type=["F2.8"],
            fleet_ids=None,
        )

    assert captured["vehicle_id"] == resolved
    assert isinstance(captured["vehicle_id"], list)


@pytest.mark.asyncio
async def test_fault_ranking_propagates_resolved_vehicle_id() -> None:
    """get_fault_ranking pasa el vehicle_id resuelto al filtro de fallas."""
    from app.services import analytics_service

    resolved = ["F-001", "F-002"]
    captured: dict[str, object] = {}

    def fake_fault_filters(stmt, **kw):
        captured["vehicle_id"] = kw.get("vehicle_id")
        return stmt

    result = MagicMock()
    result.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    with (
        patch.object(
            analytics_service,
            "fleet_analytics_vehicle_ids",
            AsyncMock(return_value={"F-001", "F-002"}),
        ),
        patch.object(
            analytics_service,
            "_apply_motor_type_filter",
            AsyncMock(return_value=(resolved, False)),
        ),
        patch.object(
            analytics_service,
            "_apply_fault_filters",
            fake_fault_filters,
        ),
    ):
        await analytics_service.get_fault_ranking(
            db,
            motor_type=["F2.8"],
            fleet_ids=None,
        )

    assert captured["vehicle_id"] == resolved
    assert isinstance(captured["vehicle_id"], list)


def test_resolve_vehicle_filter_preserves_resolved_ids() -> None:
    """_resolve_vehicle_filter pasa la lista resuelta al filtro cuando no
    hay vehicle_id explícito."""
    from app.services.analytics_service import _resolve_vehicle_filter

    assert _resolve_vehicle_filter(None, ["V-A", "V-B"]) == ["V-A", "V-B"]
    assert _resolve_vehicle_filter(["V-A", "V-Z"], ["V-A", "V-B"]) == ["V-A"]
    assert _resolve_vehicle_filter(["V-X"], ["V-A", "V-B"]) == []


# 2) metric inválida => 422 en API, ValueError en service.


def test_combustible_ranking_invalid_metric_422(admin_client: TestClient) -> None:
    resp = admin_client.get("/api/v1/reportes/combustible/ranking", params={"metric": "no_existe"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_vehicle_ranking_invalid_metric_raises_value_error() -> None:
    """Llamada directa al service con metric inválida debe lanzar ValueError."""
    from app.services import analytics_service

    db = AsyncMock()
    with pytest.raises(ValueError):
        await analytics_service.get_vehicle_ranking(db, metric="no_existe")


# 3) fechas invertidas => 422 en al menos una ruta de cada familia.


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/reportes/combustible/daily",
        "/api/v1/reportes/habitos/summary",
        "/api/v1/reportes/fallas/summary",
    ],
)
def test_inverted_date_range_422(admin_client: TestClient, path: str) -> None:
    resp = admin_client.get(
        path,
        params={"date_from": "2026-12-31", "date_to": "2026-01-01"},
    )
    assert resp.status_code == 422


def test_valid_date_range_not_rejected(admin_client: TestClient) -> None:
    """Sanity: rango válido no devuelve 422 por validación de fechas."""
    resp = admin_client.get(
        "/api/v1/reportes/combustible/daily",
        params={"date_from": "2026-01-01", "date_to": "2026-12-31"},
    )
    # 200 si hay datos, 200 con items vacíos si no — nunca 422 por fecha.
    assert resp.status_code != 422


# 4) limit 0/negativo => 422 en los 4 endpoints afectados.


@pytest.mark.parametrize(
    "path",
    [
        ("/api/v1/reportes/combustible/daily", "limit"),
        ("/api/v1/reportes/habitos/events", "limit"),
        ("/api/v1/reportes/habitos/map", "limit"),
        ("/api/v1/reportes/fallas/events", "limit"),
    ],
)
@pytest.mark.parametrize("limit_value", [0, -1, -50])
def test_limit_lower_bound_422(
    admin_client: TestClient, path: tuple[str, str], limit_value: int
) -> None:
    endpoint, param = path
    resp = admin_client.get(endpoint, params={param: limit_value})
    assert resp.status_code == 422, f"expected 422 for {param}={limit_value} on {endpoint}"


# ---------------------------------------------------------------------------
# rpm_threshold: umbral propio del motor (public.motor_catalog)
#
# El filtro histórico era un escalar único (`rpm_min=2100`) para toda la flota.
# Es incorrecto: cada motor tiene su propia velocidad gobernada y su propia
# sobrevelocidad máxima. El seed de abajo monta motores con límites distintos
# a propósito, de modo que un umbral único no pueda pasar estas pruebas.
#
# Invariante de diseño fijado aquí: **fail-closed**. Un motor cuyo límite es
# NULL (no capturado en Navi Vehículos) queda EXCLUIDO, nunca incluido sin
# filtrar. Y el límite que falta es por columna: un motor puede tener
# gobernada y no sobrevelocidad, así que cada modo se prueba por separado.
# ---------------------------------------------------------------------------

# Flota/base propias: el scoping de reportes exige que
# `public.geotab_databases.database_key` coincida con
# `analytics.dim_vehicle.database_name`, así que este seed no puede reutilizar
# 'test_db' sin volverse indistinguible de la flota del seed principal.
_RPM_FLEET_CODE = f"TEST-RPMTHR-{uuid.uuid4().hex[:8]}"
_RPM_DB_KEY = "rpm_thr_test_db"

# Motores. Los límites están elegidos para que 2100 RPM —el escalar viejo—
# caiga ENTRE la gobernada del motor bajo y la del motor alto.
_RPM_MOTOR_LOW = "TEST-RPM-LOW"  # gobernada 1925, sobrevelocidad 2300
_RPM_MOTOR_HIGH = "TEST-RPM-HIGH"  # gobernada 2230, sobrevelocidad 2600
_RPM_MOTOR_NOGOV = "TEST-RPM-NOGOV"  # gobernada NULL, sobrevelocidad 2600
_RPM_MOTOR_NOOVER = "TEST-RPM-NOOVER"  # gobernada 1900, sobrevelocidad NULL
# Sin fila en motor_catalog: el vehículo existe, el motor no está catalogado.
_RPM_MOTOR_ABSENT = "TEST-RPM-ABSENT"

_RPM_MOTOR_LIMITS: dict[str, tuple[int | None, int | None]] = {
    _RPM_MOTOR_LOW: (1925, 2300),
    _RPM_MOTOR_HIGH: (2230, 2600),
    _RPM_MOTOR_NOGOV: (None, 2600),
    _RPM_MOTOR_NOOVER: (1900, None),
}

_RPM_VEH_LOW = "TEST-VEH-RPMTHR-LOW"
_RPM_VEH_HIGH = "TEST-VEH-RPMTHR-HIGH"
_RPM_VEH_NOGOV = "TEST-VEH-RPMTHR-NOGOV"
_RPM_VEH_NOOVER = "TEST-VEH-RPMTHR-NOOVER"
_RPM_VEH_NOMOTOR = "TEST-VEH-RPMTHR-NOMOTOR"

_RPM_ALL_VEHICLES = [
    _RPM_VEH_LOW,
    _RPM_VEH_HIGH,
    _RPM_VEH_NOGOV,
    _RPM_VEH_NOOVER,
    _RPM_VEH_NOMOTOR,
]

_RPM_DEVICES = {vid: f"DEV-{vid}" for vid in _RPM_ALL_VEHICLES}
_RPM_PLATES = {
    _RPM_VEH_LOW: "RPM001",
    _RPM_VEH_HIGH: "RPM002",
    _RPM_VEH_NOGOV: "RPM003",
    _RPM_VEH_NOOVER: "RPM004",
    _RPM_VEH_NOMOTOR: "RPM005",
}

_RPM_RULE_SK = "TEST-RULE-RPMTHR-RPM"
_RPM_RULE_OTHER_SK = "TEST-RULE-RPMTHR-FRENADA"

# event_sk -> (vehicle_id, rule_sk, event_type, observacion_corta)
# El valor de RPM va en la observación; el ETL no publica una columna numérica.
_RPM_EVENTS: dict[str, tuple[str, str, str, str]] = {
    # 2100 cae por ENCIMA de la gobernada 1925 → entra con `governed`,
    # y por DEBAJO de la sobrevelocidad 2300 → no entra con `overspeed`.
    "TEST-EV-RPMTHR-LOW": (_RPM_VEH_LOW, _RPM_RULE_SK, "Excesos de RPM", "2100 RPM"),
    # El mismo 2100 queda por DEBAJO de la gobernada 2230 → nunca entra.
    "TEST-EV-RPMTHR-HIGH": (_RPM_VEH_HIGH, _RPM_RULE_SK, "Excesos de RPM", "2100 RPM"),
    # Motor sin gobernada: fail-closed en `governed`; sí entra en `overspeed`.
    "TEST-EV-RPMTHR-NOGOV": (_RPM_VEH_NOGOV, _RPM_RULE_SK, "Excesos de RPM", "3200 RPM"),
    # Motor sin sobrevelocidad: entra en `governed`, fail-closed en `overspeed`.
    "TEST-EV-RPMTHR-NOOVER": (_RPM_VEH_NOOVER, _RPM_RULE_SK, "Excesos de RPM", "3200 RPM"),
    # No es evento de RPM aunque la observación mencione RPM y el motor tenga
    # límites: `event_type` no matchea '%rpm%'.
    "TEST-EV-RPMTHR-NOTRPM": (
        _RPM_VEH_LOW,
        _RPM_RULE_OTHER_SK,
        "Frenada Brusca",
        "2100 RPM en la observación, pero el evento no es de RPM",
    ),
    # Evento de RPM sin número parseable en la observación.
    "TEST-EV-RPMTHR-NOPARSE": (
        _RPM_VEH_LOW,
        _RPM_RULE_SK,
        "Excesos de RPM",
        "sin lectura disponible",
    ),
    # Vehículo cuyo motor no está en el catálogo: límites NULL en la fila.
    "TEST-EV-RPMTHR-NOMOTOR": (
        _RPM_VEH_NOMOTOR,
        _RPM_RULE_SK,
        "Excesos de RPM",
        "3200 RPM",
    ),
}

# Conteos esperados sobre los 5 vehículos del seed.
_RPM_TOTAL_SIN_FILTRO = 7
_RPM_TOTAL_GOVERNED = 2  # LOW (2100 > 1925) + NOOVER (3200 > 1900)
_RPM_TOTAL_OVERSPEED = 1  # NOGOV (3200 > 2600)


@pytest_asyncio.fixture(scope="module")
async def rpm_threshold_seed(analytics_seed: None):
    """Flota aparte con motores de límites distintos y eventos de RPM.

    Es aditivo respecto a `analytics_seed`: flota, base Geotab, vehículos,
    motores, reglas y eventos propios. Ninguna aserción existente filtra por
    esta flota, y todas las que cuentan eventos de hábitos lo hacen con
    `vehicle_id` del seed principal.
    """
    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(code=_RPM_FLEET_CODE, name="Flota Test RPM Threshold", is_active=True)
            session.add(fleet)
            await session.flush()
            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=_RPM_DB_KEY,
                database_key=_RPM_DB_KEY,
                connection_type="geotab",
                is_active=True,
            )
            session.add(geotab_db)
            await session.flush()
            fleet_id = fleet.id
            session.add_all(
                [
                    Vehicle(
                        plate=_RPM_PLATES[vid],
                        geotab_device_id=_RPM_DEVICES[vid],
                        geotab_customer_status="found",
                        fleet_id=fleet.id,
                        geotab_database_id=geotab_db.id,
                        is_active=True,
                    )
                    for vid in _RPM_ALL_VEHICLES
                ]
            )
            # public.motor_catalog: la fuente de los dos límites. El check
            # constraint exige >0 y max_overspeed >= governed cuando ambos
            # existen, así que los valores del seed son realistas.
            for motor_type, (governed, overspeed) in _RPM_MOTOR_LIMITS.items():
                await session.execute(
                    text("""
                    INSERT INTO motor_catalog
                        (motor_type, description, governed_speed_rpm, max_overspeed_rpm,
                         created_at, updated_at)
                    VALUES (:mt, 'seed rpm_threshold', :gov, :over, now(), now())
                    ON CONFLICT (motor_type) DO UPDATE SET
                        governed_speed_rpm = EXCLUDED.governed_speed_rpm,
                        max_overspeed_rpm = EXCLUDED.max_overspeed_rpm
                    """),
                    {"mt": motor_type, "gov": governed, "over": overspeed},
                )
            motor_by_vehicle = {
                _RPM_VEH_LOW: _RPM_MOTOR_LOW,
                _RPM_VEH_HIGH: _RPM_MOTOR_HIGH,
                _RPM_VEH_NOGOV: _RPM_MOTOR_NOGOV,
                _RPM_VEH_NOOVER: _RPM_MOTOR_NOOVER,
                _RPM_VEH_NOMOTOR: _RPM_MOTOR_ABSENT,
            }
            for vid, motor_type in motor_by_vehicle.items():
                await session.execute(
                    text("""
                    INSERT INTO analytics.dim_vehicle
                        (vehicle_id, device_id, vehicle_label, database_name, motor_type,
                         is_active)
                    VALUES (:vid, :dev, :label, :db, :mt, true)
                    ON CONFLICT (vehicle_id) DO UPDATE SET
                        device_id = EXCLUDED.device_id,
                        vehicle_label = EXCLUDED.vehicle_label,
                        database_name = EXCLUDED.database_name,
                        motor_type = EXCLUDED.motor_type,
                        is_active = EXCLUDED.is_active
                    """),
                    {
                        "vid": vid,
                        "dev": _RPM_DEVICES[vid],
                        "label": f"Vehículo {_RPM_PLATES[vid]}",
                        "db": _RPM_DB_KEY,
                        "mt": motor_type,
                    },
                )
            # Los endpoints de hábitos filtran por dim_rule.categoria='Seguridad':
            # sin estas reglas los eventos no serían visibles en ningún modo.
            await session.execute(
                text("""
                INSERT INTO analytics.dim_rule (rule_sk, rule_id, rule_name, categoria)
                VALUES
                    (:rpm_rsk, 'R-RPMTHR-001', 'Excesos de RPM', 'Seguridad'),
                    (:other_rsk, 'R-RPMTHR-002', 'Frenada Brusca', 'Seguridad')
                ON CONFLICT (rule_sk) DO UPDATE SET
                    rule_name = EXCLUDED.rule_name,
                    categoria = EXCLUDED.categoria
                """),
                {"rpm_rsk": _RPM_RULE_SK, "other_rsk": _RPM_RULE_OTHER_SK},
            )
            for idx, (event_sk, (vid, rule_sk, event_type, observacion)) in enumerate(
                _RPM_EVENTS.items()
            ):
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_habito_event
                        (event_sk, event_id, vehicle_id, database_name, date_key, rule_sk,
                         event_type, fecha, placa, distancia_evento_mt, duracion_evento,
                         observacion_corta, fecha_y_hora_del_evento, latitud, longitud)
                    VALUES (:esk, :esk, :vid, :db, 20260101, :rsk, :etype,
                            '2026-01-01', :placa, 1000.0, 30.0, :obs,
                            :ts, 4.7, -74.2)
                    ON CONFLICT (event_sk) DO UPDATE SET
                        vehicle_id = EXCLUDED.vehicle_id,
                        database_name = EXCLUDED.database_name,
                        rule_sk = EXCLUDED.rule_sk,
                        event_type = EXCLUDED.event_type,
                        observacion_corta = EXCLUDED.observacion_corta
                    """),
                    {
                        "esk": event_sk,
                        "vid": vid,
                        "db": _RPM_DB_KEY,
                        "rsk": rule_sk,
                        "etype": event_type,
                        "placa": _RPM_PLATES[vid],
                        "obs": observacion,
                        "ts": datetime(2026, 1, 1, 12, idx, tzinfo=UTC),
                    },
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield fleet_id

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM analytics.fact_habito_event WHERE event_sk = ANY(:esks)"),
            {"esks": list(_RPM_EVENTS)},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_rule WHERE rule_sk = ANY(:rsks)"),
            {"rsks": [_RPM_RULE_SK, _RPM_RULE_OTHER_SK]},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = ANY(:vids)"),
            {"vids": _RPM_ALL_VEHICLES},
        )
        await session.execute(
            delete(Vehicle).where(Vehicle.geotab_device_id.in_(list(_RPM_DEVICES.values())))
        )
        await session.execute(
            text("DELETE FROM motor_catalog WHERE motor_type = ANY(:mts)"),
            {"mts": list(_RPM_MOTOR_LIMITS)},
        )
        await session.execute(delete(Fleet).where(Fleet.code == _RPM_FLEET_CODE))
        await session.commit()


def _rpm_event_sks(client: TestClient, **params: object) -> set[str]:
    """event_sk devueltos por /habitos/events para el seed de rpm_threshold."""
    query: dict[str, object] = {"vehicle_id": _RPM_ALL_VEHICLES, "limit": 200}
    query.update(params)
    resp = client.get("/api/v1/reportes/habitos/events", params=query)
    assert resp.status_code == 200, resp.text
    return {item["event_sk"] for item in resp.json()["items"]}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rpm_threshold_uses_each_motor_own_governed_limit(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """El corazón del cambio: el umbral es del motor, no de la flota.

    Dos vehículos con el mismo evento de 2100 RPM. Con gobernada 1925 el
    evento es un exceso; con gobernada 2230 no lo es. Un `rpm_min` escalar
    único no puede distinguirlos: incluiría los dos o ninguno.
    """
    sks = _rpm_event_sks(admin_client, rpm_threshold="governed")
    assert "TEST-EV-RPMTHR-LOW" in sks
    assert "TEST-EV-RPMTHR-HIGH" not in sks

    # Control: sin el filtro los dos eventos existen y son idénticos en RPM.
    sin_filtro = _rpm_event_sks(admin_client)
    assert {"TEST-EV-RPMTHR-LOW", "TEST-EV-RPMTHR-HIGH"} <= sin_filtro


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rpm_threshold_overspeed_is_stricter_than_governed(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """2100 RPM supera la gobernada (1925) pero no la sobrevelocidad (2300)."""
    governed = _rpm_event_sks(admin_client, rpm_threshold="governed")
    overspeed = _rpm_event_sks(admin_client, rpm_threshold="overspeed")

    assert "TEST-EV-RPMTHR-LOW" in governed
    assert "TEST-EV-RPMTHR-LOW" not in overspeed


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rpm_threshold_fail_closed_when_governed_limit_missing(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """Motor sin `governed_speed_rpm`: excluido en `governed` aun con 3200 RPM.

    El mismo evento SÍ sale en `overspeed`, donde ese motor tiene el dato:
    fail-closed es por columna, no por motor.
    """
    governed = _rpm_event_sks(admin_client, rpm_threshold="governed")
    overspeed = _rpm_event_sks(admin_client, rpm_threshold="overspeed")

    assert "TEST-EV-RPMTHR-NOGOV" not in governed
    assert "TEST-EV-RPMTHR-NOGOV" in overspeed


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rpm_threshold_fail_closed_when_overspeed_limit_missing(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """Motor sin `max_overspeed_rpm`: excluido en `overspeed` aun con 3200 RPM."""
    governed = _rpm_event_sks(admin_client, rpm_threshold="governed")
    overspeed = _rpm_event_sks(admin_client, rpm_threshold="overspeed")

    assert "TEST-EV-RPMTHR-NOOVER" in governed
    assert "TEST-EV-RPMTHR-NOOVER" not in overspeed


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rpm_threshold_fail_closed_when_motor_absent_from_catalog(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """Motor sin fila en `motor_catalog`: excluido en los dos modos."""
    assert "TEST-EV-RPMTHR-NOMOTOR" not in _rpm_event_sks(
        admin_client, rpm_threshold="governed"
    )
    assert "TEST-EV-RPMTHR-NOMOTOR" not in _rpm_event_sks(
        admin_client, rpm_threshold="overspeed"
    )
    # Sin el filtro el evento existe: la exclusión es del filtro, no del seed.
    assert "TEST-EV-RPMTHR-NOMOTOR" in _rpm_event_sks(admin_client)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("mode", ["governed", "overspeed"])
async def test_rpm_threshold_only_matches_rpm_events(
    admin_client: TestClient, rpm_threshold_seed, mode: str
) -> None:
    """Un evento que no es de RPM nunca entra, aunque su texto diga 'RPM'."""
    sks = _rpm_event_sks(admin_client, rpm_threshold=mode)
    assert "TEST-EV-RPMTHR-NOTRPM" not in sks
    assert "TEST-EV-RPMTHR-NOTRPM" in _rpm_event_sks(admin_client)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("mode", ["governed", "overspeed"])
async def test_rpm_threshold_excludes_unparseable_observation(
    admin_client: TestClient, rpm_threshold_seed, mode: str
) -> None:
    """Evento de RPM sin número en la observación: no se puede comparar."""
    assert "TEST-EV-RPMTHR-NOPARSE" not in _rpm_event_sks(admin_client, rpm_threshold=mode)
    assert "TEST-EV-RPMTHR-NOPARSE" in _rpm_event_sks(admin_client)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (None, _RPM_TOTAL_SIN_FILTRO),
        ("governed", _RPM_TOTAL_GOVERNED),
        ("overspeed", _RPM_TOTAL_OVERSPEED),
    ],
)
async def test_rpm_threshold_consistent_across_endpoints(
    admin_client: TestClient, rpm_threshold_seed, mode: str | None, expected: int
) -> None:
    """summary, events, by-type, timeseries, map y ranking cuentan lo mismo.

    Si un endpoint se quedara sin el parámetro, su conteo se saldría del
    conjunto y esta prueba lo señalaría.
    """
    base: dict[str, object] = {"vehicle_id": _RPM_ALL_VEHICLES}
    if mode is not None:
        base["rpm_threshold"] = mode

    summary = admin_client.get("/api/v1/reportes/habitos/summary", params=base)
    assert summary.status_code == 200, summary.text
    assert summary.json()["n_eventos"] == expected

    events = admin_client.get(
        "/api/v1/reportes/habitos/events", params={**base, "limit": 200}
    )
    assert events.status_code == 200, events.text
    assert events.json()["total"] == expected

    by_type = admin_client.get("/api/v1/reportes/habitos/by-type", params=base)
    assert by_type.status_code == 200, by_type.text
    assert sum(b["n_eventos"] for b in by_type.json()) == expected

    timeseries = admin_client.get("/api/v1/reportes/habitos/timeseries", params=base)
    assert timeseries.status_code == 200, timeseries.text
    assert sum(p["n_eventos"] for p in timeseries.json()) == expected

    # Todos los eventos del seed tienen lat/lon, así que el mapa cuenta igual.
    mapa = admin_client.get("/api/v1/reportes/habitos/map", params={**base, "limit": 200})
    assert mapa.status_code == 200, mapa.text
    assert len(mapa.json()) == expected

    ranking = admin_client.get(
        "/api/v1/reportes/habitos/ranking", params={**base, "limit": 50}
    )
    assert ranking.status_code == 200, ranking.text
    assert sum(int(r["value"]) for r in ranking.json()) == expected


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habitos_event_row_exposes_motor_and_rpm_context(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """Las filas traen motor, RPM parseadas y los dos límites del catálogo."""
    resp = admin_client.get(
        "/api/v1/reportes/habitos/events",
        params={"vehicle_id": _RPM_ALL_VEHICLES, "limit": 200},
    )
    assert resp.status_code == 200, resp.text
    by_sk = {item["event_sk"]: item for item in resp.json()["items"]}

    low = by_sk["TEST-EV-RPMTHR-LOW"]
    assert low["motor_type"] == _RPM_MOTOR_LOW
    assert low["rpm_value"] == pytest.approx(2100.0)
    assert low["rpm_governed_limit"] == 1925
    assert low["rpm_overspeed_limit"] == 2300

    nogov = by_sk["TEST-EV-RPMTHR-NOGOV"]
    assert nogov["motor_type"] == _RPM_MOTOR_NOGOV
    assert nogov["rpm_governed_limit"] is None
    assert nogov["rpm_overspeed_limit"] == 2600

    noover = by_sk["TEST-EV-RPMTHR-NOOVER"]
    assert noover["rpm_governed_limit"] == 1900
    assert noover["rpm_overspeed_limit"] is None

    noparse = by_sk["TEST-EV-RPMTHR-NOPARSE"]
    assert noparse["rpm_value"] is None

    # Vehículo cuyo motor no está catalogado: la respuesta no se rompe y los
    # dos límites llegan en NULL.
    nomotor = by_sk["TEST-EV-RPMTHR-NOMOTOR"]
    assert nomotor["motor_type"] == _RPM_MOTOR_ABSENT
    assert nomotor["rpm_value"] == pytest.approx(3200.0)
    assert nomotor["rpm_governed_limit"] is None
    assert nomotor["rpm_overspeed_limit"] is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_motor_types_exposes_plate_limits(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """/reportes/motor-types publica los dos límites, NULL incluido."""
    resp = admin_client.get("/api/v1/reportes/motor-types")
    assert resp.status_code == 200, resp.text
    buckets = {b["motor_type"]: b for b in resp.json()}

    for motor_type, (governed, overspeed) in _RPM_MOTOR_LIMITS.items():
        bucket = buckets[motor_type]
        assert bucket["governed_speed_rpm"] == governed
        assert bucket["max_overspeed_rpm"] == overspeed

    absent = buckets[_RPM_MOTOR_ABSENT]
    assert absent["governed_speed_rpm"] is None
    assert absent["max_overspeed_rpm"] is None


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/reportes/habitos/summary",
        "/api/v1/reportes/habitos/by-type",
        "/api/v1/reportes/habitos/timeseries",
        "/api/v1/reportes/habitos/ranking",
        "/api/v1/reportes/habitos/map",
        "/api/v1/reportes/habitos/events",
    ],
)
def test_rpm_threshold_invalid_value_422(admin_client: TestClient, path: str) -> None:
    """Solo 'governed' y 'overspeed'. Un valor en español no se adivina."""
    resp = admin_client.get(path, params={"rpm_threshold": "gobernada"})
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rpm_threshold_respects_fleet_isolation(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """El filtro nuevo no abre un camino para ver eventos de otra flota.

    Regla operativa del repositorio: toda consulta multi-flota necesita prueba
    negativa. El actor tiene `reportes.view` y pide explícitamente los
    `vehicle_id` del seed, pero su única flota es otra: debe ver cero.
    """
    suffix = uuid.uuid4().hex[:8]
    role_code = f"rpm_reporter_{suffix}"
    role_resp = admin_client.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": f"Reportero RPM {suffix}",
            "permission_codes": ["reportes.view"],
        },
    )
    assert role_resp.status_code == 201, role_resp.text

    other_fleet = admin_client.post(
        "/api/v1/fleets",
        json={"code": f"TEST-RPMOUT-{suffix.upper()}", "name": "Flota Ajena RPM"},
    )
    assert other_fleet.status_code == 201, other_fleet.text
    other_fleet_id = other_fleet.json()["id"]

    email = f"rpmout-{suffix}@portalclientes.test"
    password = "RpmOutsider123!"
    created = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Outsider RPM",
            "role_codes": [role_code],
            "fleet_ids": [other_fleet_id],
        },
    )
    assert created.status_code == 201, created.text

    outsider = TestClient(app)
    login = outsider.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text

    for mode in (None, "governed", "overspeed"):
        params: dict[str, object] = {"vehicle_id": _RPM_ALL_VEHICLES, "limit": 200}
        if mode is not None:
            params["rpm_threshold"] = mode
        events = outsider.get("/api/v1/reportes/habitos/events", params=params)
        assert events.status_code == 200, events.text
        assert events.json()["total"] == 0, f"fuga de flota con rpm_threshold={mode}"

        summary = outsider.get(
            "/api/v1/reportes/habitos/summary",
            params={k: v for k, v in params.items() if k != "limit"},
        )
        assert summary.status_code == 200, summary.text
        assert summary.json()["n_eventos"] == 0

    # Contraprueba: el admin sí ve el evento, así que el 0 de arriba es
    # aislamiento y no un seed vacío.
    assert "TEST-EV-RPMTHR-LOW" in _rpm_event_sks(admin_client, rpm_threshold="governed")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rpm_min_and_rpm_threshold_combine_with_and(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """`rpm_min` sigue vivo por compatibilidad y se combina con AND."""
    solo_threshold = _rpm_event_sks(admin_client, rpm_threshold="governed")
    assert solo_threshold == {"TEST-EV-RPMTHR-LOW", "TEST-EV-RPMTHR-NOOVER"}

    # rpm_min=2500 descarta el evento de 2100 y deja el de 3200.
    combinado = _rpm_event_sks(admin_client, rpm_threshold="governed", rpm_min=2500)
    assert combinado == {"TEST-EV-RPMTHR-NOOVER"}

    # rpm_min solo, sin threshold: no aplica límites de motor.
    solo_rpm_min = _rpm_event_sks(admin_client, rpm_min=2000)
    assert {"TEST-EV-RPMTHR-LOW", "TEST-EV-RPMTHR-HIGH"} <= solo_rpm_min


# ---------------------------------------------------------------------------
# Métricas numéricas de fact_habito_event + ordenamiento de /habitos/events
#
# Contexto: hasta 2026-08-27 los números del evento vivían embebidos en el
# texto de `observacion_corta` (`1900.25 RPM, 1.8 km/h, Carga: 36.52%`) y el
# backend los sacaba con un regex en SQL. El ETL ahora los materializa en
# columnas (`rpm`, `velocidad_kmh`, `carga_pct`, `g_force`, `g_axis`,
# `event_value`, `event_value_unit`), pero las ~296k filas históricas siguen
# con la columna NULL hasta que el ETL reprocese.
#
# De ahí el invariante central de este bloque: `_rpm_value_expression` es
# `COALESCE(columna, regex)`. Los DOS caminos tienen que funcionar y, cuando
# ambos existen y difieren, gana la columna. El seed de abajo monta las tres
# situaciones a propósito, incluida una fila donde el texto dice 9999 y la
# columna 1000: si alguien invierte el COALESCE, esa fila cambia de lado.
#
# Este seed es ADITIVO sobre `rpm_threshold_seed`: reutiliza su flota, su base
# Geotab, sus motores y sus reglas, y añade vehículos NUEVOS. Ninguna aserción
# existente lo ve, porque todas filtran por `vehicle_id` y ninguna incluye
# estas placas.
# ---------------------------------------------------------------------------

_HM_VEH_COAL = "TEST-VEH-HMET-COAL"  # filas para el COALESCE columna/texto
_HM_VEH_SORT = "TEST-VEH-HMET-SORT"  # filas con event_value distinto + NULL
_HM_VEH_TIE = "TEST-VEH-HMET-TIE"  # filas empatadas: paginación estable

_HM_ALL_VEHICLES = [_HM_VEH_COAL, _HM_VEH_SORT, _HM_VEH_TIE]
_HM_DEVICES = {vid: f"DEV-{vid}" for vid in _HM_ALL_VEHICLES}
_HM_PLATES = {
    _HM_VEH_COAL: "HMET01",
    _HM_VEH_SORT: "HMET02",
    _HM_VEH_TIE: "HMET03",
}

# Los tres vehículos comparten el motor de gobernada BAJA del seed anterior
# (_RPM_MOTOR_LOW = 1925 gobernada / 2300 sobrevelocidad): así el filtro
# `rpm_threshold` tiene un límite conocido contra el que comparar.
_HM_MOTOR = _RPM_MOTOR_LOW

# event_sk -> dict de columnas. `obs` es `observacion_corta` (None = la fila
# no trae texto, el caso de una fila nueva del ETL); el resto son las columnas
# numéricas nuevas.
_HM_EVENTS: dict[str, dict[str, object]] = {
    # (1) Solo columna: el texto no existe. Antes del cambio esta fila era
    # invisible para el filtro de RPM; ahora la columna la resuelve.
    "TEST-EV-HMET-COLONLY": {
        "vehicle_id": _HM_VEH_COAL,
        "rule_sk": _RPM_RULE_SK,
        "event_type": "Excesos de RPM",
        "obs": None,
        "ts": datetime(2026, 1, 1, 15, 0, tzinfo=UTC),
        "rpm": 2000.0,
        "event_value": 2000.0,
        "event_value_unit": "RPM",
    },
    # (2) Solo texto: la regresión del histórico. 296k filas están así.
    "TEST-EV-HMET-TEXTONLY": {
        "vehicle_id": _HM_VEH_COAL,
        "rule_sk": _RPM_RULE_SK,
        "event_type": "Excesos de RPM",
        "obs": "2050 RPM, 1.8 km/h, Carga: 36.52%",
        "ts": datetime(2026, 1, 1, 15, 1, tzinfo=UTC),
    },
    # (3) Ambos y DISTINTOS, con la columna por DEBAJO de la gobernada y el
    # texto muy por encima: con el COALESCE correcto la fila NO es un exceso.
    "TEST-EV-HMET-BOTHLOW": {
        "vehicle_id": _HM_VEH_COAL,
        "rule_sk": _RPM_RULE_SK,
        "event_type": "Excesos de RPM",
        "obs": "9999 RPM",
        "ts": datetime(2026, 1, 1, 15, 2, tzinfo=UTC),
        "rpm": 1000.0,
    },
    # (4) La simétrica: columna por encima de las dos cotas, texto ridículo.
    # Entre (3) y (4) una inversión del COALESCE queda cazada en los dos
    # sentidos, no solo en uno.
    "TEST-EV-HMET-BOTHHIGH": {
        "vehicle_id": _HM_VEH_COAL,
        "rule_sk": _RPM_RULE_SK,
        "event_type": "Excesos de RPM",
        "obs": "10 RPM",
        "ts": datetime(2026, 1, 1, 15, 3, tzinfo=UTC),
        "rpm": 2900.0,
    },
    # --- Vehículo de ordenamiento: event_value 30 / 20 / 10 / NULL, con los
    # timestamps EN ORDEN INVERSO al event_value. Así, si `sort_by` se
    # ignorara y quedara el orden por fecha, las aserciones fallarían.
    # La fila 'A' es además la que trae las seis métricas pobladas.
    "TEST-EV-HMET-SORT-A": {
        "vehicle_id": _HM_VEH_SORT,
        "rule_sk": _RPM_RULE_OTHER_SK,
        "event_type": "Frenadas Bruscas",
        "obs": "95.5 km/h, Aceleración longitudinal: -3.0 G Force",
        "ts": datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        "rpm": 1750.25,
        "velocidad_kmh": 95.5,
        "carga_pct": 36.52,
        "g_force": -3.0,
        "g_axis": "longitudinal",
        "event_value": 30.0,
        "event_value_unit": "G",
    },
    "TEST-EV-HMET-SORT-B": {
        "vehicle_id": _HM_VEH_SORT,
        "rule_sk": _RPM_RULE_OTHER_SK,
        "event_type": "Frenadas Bruscas",
        "obs": "20 km/h",
        "ts": datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
        "event_value": 20.0,
        "event_value_unit": "G",
    },
    "TEST-EV-HMET-SORT-C": {
        "vehicle_id": _HM_VEH_SORT,
        "rule_sk": _RPM_RULE_OTHER_SK,
        "event_type": "Frenadas Bruscas",
        "obs": "10 km/h",
        "ts": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        "event_value": 10.0,
        "event_value_unit": "G",
    },
    # Fila con las siete columnas nuevas en NULL: `event_value` NULL es la que
    # tiene que quedar al final en asc Y en desc, y la que prueba que la
    # respuesta no se rompe cuando el ETL aún no pobló nada.
    "TEST-EV-HMET-SORT-NULL": {
        "vehicle_id": _HM_VEH_SORT,
        "rule_sk": _RPM_RULE_OTHER_SK,
        "event_type": "Frenadas Bruscas",
        "obs": "sin lectura disponible",
        "ts": datetime(2026, 1, 1, 13, 0, tzinfo=UTC),
    },
    # --- Vehículo de empates: cuatro filas con el MISMO event_value, en dos
    # pares con el MISMO timestamp. Sin desempate por `event_sk` el orden no
    # es total y la paginación puede repetir u omitir filas.
    "TEST-EV-HMET-TIE-1": {
        "vehicle_id": _HM_VEH_TIE,
        "rule_sk": _RPM_RULE_OTHER_SK,
        "event_type": "Frenadas Bruscas",
        "obs": "empate",
        "ts": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "event_value": 5.0,
        "event_value_unit": "G",
    },
    "TEST-EV-HMET-TIE-2": {
        "vehicle_id": _HM_VEH_TIE,
        "rule_sk": _RPM_RULE_OTHER_SK,
        "event_type": "Frenadas Bruscas",
        "obs": "empate",
        "ts": datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
        "event_value": 5.0,
        "event_value_unit": "G",
    },
    "TEST-EV-HMET-TIE-3": {
        "vehicle_id": _HM_VEH_TIE,
        "rule_sk": _RPM_RULE_OTHER_SK,
        "event_type": "Frenadas Bruscas",
        "obs": "empate",
        "ts": datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        "event_value": 5.0,
        "event_value_unit": "G",
    },
    "TEST-EV-HMET-TIE-4": {
        "vehicle_id": _HM_VEH_TIE,
        "rule_sk": _RPM_RULE_OTHER_SK,
        "event_type": "Frenadas Bruscas",
        "obs": "empate",
        "ts": datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        "event_value": 5.0,
        "event_value_unit": "G",
    },
}

_HM_SORT_EVENTS = [
    "TEST-EV-HMET-SORT-A",
    "TEST-EV-HMET-SORT-B",
    "TEST-EV-HMET-SORT-C",
    "TEST-EV-HMET-SORT-NULL",
]
_HM_TIE_EVENTS = [
    "TEST-EV-HMET-TIE-1",
    "TEST-EV-HMET-TIE-2",
    "TEST-EV-HMET-TIE-3",
    "TEST-EV-HMET-TIE-4",
]


@pytest_asyncio.fixture(scope="module")
async def habito_metrics_seed(rpm_threshold_seed):
    """Vehículos nuevos en la flota de `rpm_threshold_seed`, con las columnas
    numéricas del ETL pobladas, vacías o en conflicto con el texto.

    Aditivo por construcción: no modifica ninguna fila del seed anterior y sus
    vehículos no están en `_RPM_ALL_VEHICLES`, que es lo que filtran todas las
    aserciones existentes.
    """
    fleet_id = rpm_threshold_seed
    async with AsyncSessionLocal() as session:
        try:
            geotab_db_id = (
                await session.execute(
                    select(GeotabDatabase.id).where(
                        GeotabDatabase.fleet_id == fleet_id,
                        GeotabDatabase.database_key == _RPM_DB_KEY,
                    )
                )
            ).scalar_one()
            session.add_all(
                [
                    Vehicle(
                        plate=_HM_PLATES[vid],
                        geotab_device_id=_HM_DEVICES[vid],
                        geotab_customer_status="found",
                        fleet_id=fleet_id,
                        geotab_database_id=geotab_db_id,
                        is_active=True,
                    )
                    for vid in _HM_ALL_VEHICLES
                ]
            )
            for vid in _HM_ALL_VEHICLES:
                await session.execute(
                    text("""
                    INSERT INTO analytics.dim_vehicle
                        (vehicle_id, device_id, vehicle_label, database_name, motor_type,
                         is_active)
                    VALUES (:vid, :dev, :label, :db, :mt, true)
                    ON CONFLICT (vehicle_id) DO UPDATE SET
                        device_id = EXCLUDED.device_id,
                        database_name = EXCLUDED.database_name,
                        motor_type = EXCLUDED.motor_type,
                        is_active = EXCLUDED.is_active
                    """),
                    {
                        "vid": vid,
                        "dev": _HM_DEVICES[vid],
                        "label": f"Vehículo {_HM_PLATES[vid]}",
                        "db": _RPM_DB_KEY,
                        "mt": _HM_MOTOR,
                    },
                )
            for event_sk, row in _HM_EVENTS.items():
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_habito_event
                        (event_sk, event_id, vehicle_id, database_name, date_key, rule_sk,
                         event_type, fecha, placa, distancia_evento_mt, duracion_evento,
                         observacion_corta, fecha_y_hora_del_evento, latitud, longitud,
                         rpm, velocidad_kmh, carga_pct, g_force, g_axis,
                         event_value, event_value_unit)
                    VALUES (:esk, :esk, :vid, :db, 20260101, :rsk, :etype,
                            '2026-01-01', :placa, 1000.0, 30.0, :obs, :ts, 4.7, -74.2,
                            :rpm, :vel, :carga, :gf, :gaxis, :ev, :evu)
                    ON CONFLICT (event_sk) DO UPDATE SET
                        vehicle_id = EXCLUDED.vehicle_id,
                        observacion_corta = EXCLUDED.observacion_corta,
                        rpm = EXCLUDED.rpm,
                        velocidad_kmh = EXCLUDED.velocidad_kmh,
                        carga_pct = EXCLUDED.carga_pct,
                        g_force = EXCLUDED.g_force,
                        g_axis = EXCLUDED.g_axis,
                        event_value = EXCLUDED.event_value,
                        event_value_unit = EXCLUDED.event_value_unit
                    """),
                    {
                        "esk": event_sk,
                        "vid": row["vehicle_id"],
                        "db": _RPM_DB_KEY,
                        "rsk": row["rule_sk"],
                        "etype": row["event_type"],
                        "placa": _HM_PLATES[row["vehicle_id"]],
                        "obs": row.get("obs"),
                        "ts": row["ts"],
                        "rpm": row.get("rpm"),
                        "vel": row.get("velocidad_kmh"),
                        "carga": row.get("carga_pct"),
                        "gf": row.get("g_force"),
                        "gaxis": row.get("g_axis"),
                        "ev": row.get("event_value"),
                        "evu": row.get("event_value_unit"),
                    },
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield fleet_id

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM analytics.fact_habito_event WHERE event_sk = ANY(:esks)"),
            {"esks": list(_HM_EVENTS)},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = ANY(:vids)"),
            {"vids": _HM_ALL_VEHICLES},
        )
        await session.execute(
            delete(Vehicle).where(Vehicle.geotab_device_id.in_(list(_HM_DEVICES.values())))
        )
        await session.commit()


def _hm_items(client: TestClient, vehicles: list[str], **params: object) -> list[dict]:
    """items de /habitos/events para vehículos del seed de métricas."""
    query: dict[str, object] = {"vehicle_id": vehicles, "limit": 200}
    query.update(params)
    resp = client.get("/api/v1/reportes/habitos/events", params=query)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


def _hm_order(client: TestClient, vehicles: list[str], **params: object) -> list[str]:
    return [item["event_sk"] for item in _hm_items(client, vehicles, **params)]


def _hm_by_sk(client: TestClient, vehicles: list[str], **params: object) -> dict[str, dict]:
    return {item["event_sk"]: item for item in _hm_items(client, vehicles, **params)}


# --- A) COALESCE(columna, regex): los dos caminos y su precedencia ---------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rpm_column_alone_drives_the_threshold_filter(
    admin_client: TestClient, habito_metrics_seed
) -> None:
    """Fila nueva del ETL: `rpm` poblado y `observacion_corta` NULL.

    Antes del cambio el regex no tenía de dónde leer y la fila quedaba fuera
    del filtro. Ahora la columna la resuelve: 2000 > 1925 (gobernada) y
    < 2300 (sobrevelocidad).
    """
    row = _hm_by_sk(admin_client, [_HM_VEH_COAL])["TEST-EV-HMET-COLONLY"]
    assert row["observacion_corta"] is None
    assert row["rpm_value"] == pytest.approx(2000.0)

    gobernada = _hm_order(admin_client, [_HM_VEH_COAL], rpm_threshold="governed")
    sobrevel = _hm_order(admin_client, [_HM_VEH_COAL], rpm_threshold="overspeed")
    assert "TEST-EV-HMET-COLONLY" in gobernada
    assert "TEST-EV-HMET-COLONLY" not in sobrevel


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rpm_text_fallback_still_works_for_historic_rows(
    admin_client: TestClient, habito_metrics_seed
) -> None:
    """La regresión importante: 296k filas históricas tienen `rpm` NULL.

    Si alguien retira el regex del COALESCE, todo el histórico deja de
    filtrarse y esta prueba lo caza.
    """
    row = _hm_by_sk(admin_client, [_HM_VEH_COAL])["TEST-EV-HMET-TEXTONLY"]
    assert row["rpm_value"] == pytest.approx(2050.0)

    gobernada = _hm_order(admin_client, [_HM_VEH_COAL], rpm_threshold="governed")
    assert "TEST-EV-HMET-TEXTONLY" in gobernada
    # Y el escalar histórico también sigue leyendo del texto.
    assert "TEST-EV-HMET-TEXTONLY" in _hm_order(
        admin_client, [_HM_VEH_COAL], rpm_min=2000
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rpm_column_wins_over_observation_text(
    admin_client: TestClient, habito_metrics_seed
) -> None:
    """Columna y texto en conflicto: manda la columna, en los dos sentidos.

    BOTHLOW  tiene columna 1000 (bajo la gobernada 1925) y texto 9999.
    BOTHHIGH tiene columna 2900 (sobre las dos cotas) y texto 10.
    Invertir el COALESCE intercambia exactamente estas dos filas de lado, así
    que la prueba distingue el orden del COALESCE y no solo su existencia.
    """
    by_sk = _hm_by_sk(admin_client, [_HM_VEH_COAL])
    assert by_sk["TEST-EV-HMET-BOTHLOW"]["rpm_value"] == pytest.approx(1000.0)
    assert by_sk["TEST-EV-HMET-BOTHHIGH"]["rpm_value"] == pytest.approx(2900.0)

    gobernada = set(_hm_order(admin_client, [_HM_VEH_COAL], rpm_threshold="governed"))
    sobrevel = set(_hm_order(admin_client, [_HM_VEH_COAL], rpm_threshold="overspeed"))

    assert "TEST-EV-HMET-BOTHLOW" not in gobernada, "el texto 9999 no debe decidir"
    assert "TEST-EV-HMET-BOTHLOW" not in sobrevel
    assert "TEST-EV-HMET-BOTHHIGH" in gobernada
    assert "TEST-EV-HMET-BOTHHIGH" in sobrevel, "el texto 10 no debe decidir"

    # Conjunto completo del vehículo: fija el resultado de las cuatro filas de
    # una vez, no solo la pertenencia individual.
    assert gobernada == {
        "TEST-EV-HMET-COLONLY",
        "TEST-EV-HMET-TEXTONLY",
        "TEST-EV-HMET-BOTHHIGH",
    }
    assert sobrevel == {"TEST-EV-HMET-BOTHHIGH"}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habito_event_exposes_new_numeric_metrics(
    admin_client: TestClient, habito_metrics_seed
) -> None:
    """`HabitoEventRead` publica las seis métricas nuevas, y NULL no rompe.

    Las RPM no llevan campo propio: ya se publican como `rpm_value`, que es el
    COALESCE(columna, regex). Aquí la fila tiene la columna poblada y ningún
    número de RPM en el texto, así que `rpm_value` sale de la columna.
    """
    by_sk = _hm_by_sk(admin_client, [_HM_VEH_SORT])

    poblada = by_sk["TEST-EV-HMET-SORT-A"]
    assert poblada["rpm_value"] == pytest.approx(1750.25)
    assert poblada["velocidad_kmh"] == pytest.approx(95.5)
    assert poblada["carga_pct"] == pytest.approx(36.52)
    assert poblada["g_force"] == pytest.approx(-3.0), "la fuerza G conserva el signo"
    assert poblada["g_axis"] == "longitudinal"
    assert poblada["event_value"] == pytest.approx(30.0)
    assert poblada["event_value_unit"] == "G"

    # Fila histórica: las columnas nuevas en NULL y la respuesta intacta.
    vacia = by_sk["TEST-EV-HMET-SORT-NULL"]
    for campo in (
        "rpm_value",
        "velocidad_kmh",
        "carga_pct",
        "g_force",
        "g_axis",
        "event_value",
        "event_value_unit",
    ):
        assert vacia[campo] is None, campo
    assert vacia["event_sk"] == "TEST-EV-HMET-SORT-NULL"


# --- B) Ordenamiento de /habitos/events -----------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sort_by_event_value_orders_both_directions(
    admin_client: TestClient, habito_metrics_seed
) -> None:
    """desc = mayor a menor, asc = menor a mayor.

    Los timestamps del seed van en orden INVERSO al `event_value`, así que un
    `sort_by` ignorado (orden por fecha) no puede pasar esta prueba.
    """
    desc = _hm_order(admin_client, [_HM_VEH_SORT], sort_by="event_value", sort_dir="desc")
    asc = _hm_order(admin_client, [_HM_VEH_SORT], sort_by="event_value", sort_dir="asc")

    assert desc[:3] == [
        "TEST-EV-HMET-SORT-A",
        "TEST-EV-HMET-SORT-B",
        "TEST-EV-HMET-SORT-C",
    ]
    assert asc[:3] == [
        "TEST-EV-HMET-SORT-C",
        "TEST-EV-HMET-SORT-B",
        "TEST-EV-HMET-SORT-A",
    ]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sort_puts_nulls_last_in_both_directions(
    admin_client: TestClient, habito_metrics_seed
) -> None:
    """NULL al final SIEMPRE, no solo en desc.

    Es el defecto clásico: `NULLS LAST` puesto una sola vez deja los NULL
    primeros en la otra dirección y el usuario ve una página de filas sin
    dato al pedir "los más bajos".
    """
    for direccion in ("desc", "asc"):
        orden = _hm_order(
            admin_client, [_HM_VEH_SORT], sort_by="event_value", sort_dir=direccion
        )
        assert orden[-1] == "TEST-EV-HMET-SORT-NULL", f"NULL no quedó al final en {direccion}"
        # Y no se perdió ninguna fila por el camino: ordenar no es filtrar.
        assert set(orden) == set(_HM_SORT_EVENTS)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sort_pagination_is_stable_when_values_tie(
    admin_client: TestClient, habito_metrics_seed
) -> None:
    """Cuatro filas empatadas: dos páginas de 2 = las 4, sin repetir ni omitir.

    Sin desempate por `event_sk` el orden no es total y PostgreSQL puede
    devolver la misma fila en las dos páginas. Es un defecto de corrección,
    no de estética.
    """
    pagina1 = _hm_order(
        admin_client, [_HM_VEH_TIE], sort_by="event_value", sort_dir="desc",
        limit=2, offset=0,
    )
    pagina2 = _hm_order(
        admin_client, [_HM_VEH_TIE], sort_by="event_value", sort_dir="desc",
        limit=2, offset=2,
    )
    completa = _hm_order(
        admin_client, [_HM_VEH_TIE], sort_by="event_value", sort_dir="desc", limit=4
    )

    assert len(pagina1) == 2 and len(pagina2) == 2
    assert not set(pagina1) & set(pagina2), "una fila apareció en las dos páginas"
    assert pagina1 + pagina2 == completa, "paginar no reproduce el orden completo"
    assert set(completa) == set(_HM_TIE_EVENTS)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_default_sort_preserves_historic_order(
    admin_client: TestClient, habito_metrics_seed
) -> None:
    """Sin `sort_by`, el orden es el de siempre: fecha desc, `event_sk` desc.

    Fija el contrato byte a byte para que añadir ordenamiento no cambie en
    silencio la primera pantalla de nadie. El vehículo de empates tiene dos
    pares con el MISMO timestamp, así que el desempate por `event_sk` es
    observable y no una suposición.
    """
    esperado_empates = [
        "TEST-EV-HMET-TIE-2",
        "TEST-EV-HMET-TIE-1",
        "TEST-EV-HMET-TIE-4",
        "TEST-EV-HMET-TIE-3",
    ]
    assert _hm_order(admin_client, [_HM_VEH_TIE]) == esperado_empates
    # Y el default explícito no puede diferir del default implícito.
    assert (
        _hm_order(admin_client, [_HM_VEH_TIE], sort_by="fecha", sort_dir="desc")
        == esperado_empates
    )

    # Timestamps distintos: puro fecha desc.
    assert _hm_order(admin_client, [_HM_VEH_SORT]) == [
        "TEST-EV-HMET-SORT-NULL",
        "TEST-EV-HMET-SORT-C",
        "TEST-EV-HMET-SORT-B",
        "TEST-EV-HMET-SORT-A",
    ]


@pytest.mark.parametrize(
    "sort_by",
    [
        "event_sk",  # existe en la tabla pero no está en la allowlist
        "observacion_corta",
        "no_existe",
        "event_sk; DROP TABLE analytics.fact_habito_event",
        "1",
        "",
    ],
)
def test_sort_by_outside_allowlist_is_422(admin_client: TestClient, sort_by: str) -> None:
    """Allowlist cerrada: nada de nombres de columna arbitrarios.

    Cierra además la puerta a inyección por nombre de columna: el valor con
    `; DROP TABLE` tiene que morir en la validación del contrato, no llegar a
    construirse en SQL.
    """
    resp = admin_client.get(
        "/api/v1/reportes/habitos/events", params={"sort_by": sort_by}
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("sort_dir", ["ASC", "descending", "up", "asc; DROP TABLE x", ""])
def test_sort_dir_outside_allowlist_is_422(admin_client: TestClient, sort_dir: str) -> None:
    """Solo 'asc' y 'desc' en minúscula."""
    resp = admin_client.get(
        "/api/v1/reportes/habitos/events",
        params={"sort_by": "event_value", "sort_dir": sort_dir},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    "sort_by",
    [
        "fecha",
        "event_value",
        "rpm",
        "velocidad_kmh",
        "g_force",
        "duracion_evento",
        "distancia_evento_mt",
    ],
)
def test_sort_by_allowlist_is_accepted(admin_client: TestClient, sort_by: str) -> None:
    """Las siete claves del contrato existen y no devuelven 422."""
    for sort_dir in ("asc", "desc"):
        resp = admin_client.get(
            "/api/v1/reportes/habitos/events",
            params={"sort_by": sort_by, "sort_dir": sort_dir},
        )
        assert resp.status_code == 200, resp.text


# --- C) La calificación usa la gobernada del motor, no 2100 ---------------


def _calificacion(client: TestClient, vehicles: list[str]) -> dict:
    resp = client.get("/api/v1/reportes/calificacion", params={"vehicle_id": vehicles})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_aggravates_against_each_motor_governed_speed(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """El x2 se decide contra la gobernada del motor de cada vehículo.

    Dos vehículos con el MISMO evento de 2100 RPM: gobernada 1925 (LOW) y
    2230 (HIGH). Solo LOW recibe la agravación. Un umbral fijo de 2100 no
    puede pasar esto: 2100 > 2100 es falso, así que LOW quedaría en 0.
    """
    data = _calificacion(admin_client, _RPM_ALL_VEHICLES)
    por_vehiculo = {v["vehicle_id"]: v for v in data["vehiculos"]}

    low = por_vehiculo[_RPM_VEH_LOW]
    high = por_vehiculo[_RPM_VEH_HIGH]

    # LOW tiene dos eventos de RPM (2100 y uno sin lectura) y uno solo agrava.
    assert low["eventos_rpm"] == 2
    assert low["eventos_rpm_sobre_gobernada"] == 1

    assert high["eventos_rpm"] == 1
    assert high["eventos_rpm_sobre_gobernada"] == 0, (
        "2100 no supera la gobernada 2230 de este motor"
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_counts_events_without_governed_limit(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """Asimetría deliberada: en la calificación NO se descarta nada.

    En el filtro de hábitos un límite desconocido excluye el evento
    (fail-closed). En un puntaje eso sería falsear la nota: el evento ocurrió.
    Aquí el evento cuenta x1 y solo pierde la agravación x2.
    Se verifican las DOS mitades: que no desapareció y que no se agravó.
    """
    data = _calificacion(admin_client, _RPM_ALL_VEHICLES)
    por_vehiculo = {v["vehicle_id"]: v for v in data["vehiculos"]}

    # Motor sin `governed_speed_rpm` (3200 RPM, dato del límite ausente).
    nogov = por_vehiculo[_RPM_VEH_NOGOV]
    assert nogov["eventos_rpm"] == 1, "el evento no puede desaparecer del conteo"
    assert nogov["eventos_rpm_sobre_gobernada"] == 0, "sin límite no hay agravación"

    # Motor que no está en `motor_catalog`: mismo tratamiento.
    nomotor = por_vehiculo[_RPM_VEH_NOMOTOR]
    assert nomotor["eventos_rpm"] == 1
    assert nomotor["eventos_rpm_sobre_gobernada"] == 0

    # Contraste dentro de la misma respuesta: un motor CON gobernada sí agrava.
    noover = por_vehiculo[_RPM_VEH_NOOVER]
    assert noover["eventos_rpm"] == 1
    assert noover["eventos_rpm_sobre_gobernada"] == 1, "3200 > 1900 debe agravar"

    # Totales del mes: 6 eventos de RPM y 2 agravados (LOW + NOOVER). Con el
    # umbral fijo de 2100 serían 3 (NOGOV, NOOVER y NOMOTOR) y LOW quedaría
    # fuera: el total distingue las dos implementaciones por sí solo.
    operativos = {p["periodo"]: p for p in data["operativos"]}
    enero = operativos[202601]
    assert enero["eventos_rpm"] == 6
    assert enero["eventos_rpm_sobre_gobernada"] == 2


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_renames_rpm_field_in_both_schemas(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """`eventos_rpm_sobre_2100` desaparece del contrato en los dos esquemas.

    Estaba declarado en `CalificacionOperativoPoint` (serie mensual) y en
    `CalificacionVehiculo` (tabla por vehículo). Si solo se renombra uno, el
    front recibe dos nombres para el mismo concepto.
    """
    data = _calificacion(admin_client, _RPM_ALL_VEHICLES)

    assert data["vehiculos"], "el seed debe producir filas por vehículo"
    for vehiculo in data["vehiculos"]:
        assert "eventos_rpm_sobre_gobernada" in vehiculo
        assert "eventos_rpm_sobre_2100" not in vehiculo

    assert data["operativos"], "el seed debe producir la serie mensual"
    for punto in data["operativos"]:
        assert "eventos_rpm_sobre_gobernada" in punto
        assert "eventos_rpm_sobre_2100" not in punto


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_methodology_no_longer_mentions_2100(
    admin_client: TestClient, rpm_threshold_seed
) -> None:
    """El texto del criterio publicado no puede seguir prometiendo 2100 RPM.

    La metodología es el contrato que el front muestra al usuario: si dice
    2100 y el cálculo usa la gobernada, la pantalla miente.
    """
    data = _calificacion(admin_client, _RPM_ALL_VEHICLES)
    componentes = {c["nombre"]: c for c in data["metodologia"]["componentes_qho"]}
    detalle = componentes["Excesos de RPM"]["detalle"]

    assert "2100" not in detalle, detalle


# ---------------------------------------------------------------------------
# D) Penalización por sobrevelocidad + desglose por vehículo
#
# Dos cambios que comparten seed porque comparten insumos:
#
#   (A) UN solo evento de RPM por encima de `max_overspeed_rpm` del motor tumba
#       la calificación del vehículo: `qgen` efectivo 0 y estado "No cumple",
#       con `qgen_base` conservando el puntaje que habría tenido. Es una regla
#       de override, no un descuento: no toca `qhs` ni `qho`.
#
#       Lo que hace difícil de probar este cambio es distinguirlo de un
#       vehículo que simplemente puntúa 0. De ahí que el seed monte PAREJAS
#       idénticas que solo difieren en el RPM del evento: uno por encima de la
#       sobrevelocidad y otro entre la gobernada y la sobrevelocidad. Si el
#       override no existiera, los dos darían el mismo número.
#
#       Y como la agravación x2, la penalización es FAIL-OPEN: un motor sin
#       `max_overspeed_rpm` capturada no puede disparar nada, porque un exceso
#       sobre un límite desconocido no es demostrable.
#
#   (B) Cada fila de `vehiculos` gana `detalle`: los sumandos del QHO con lo
#       medido, la meta y los puntos perdidos; los eventos que pesan en el QHS;
#       la exposición; y `motivos` ordenados por impacto. El invariante que
#       importa —y lo que estas pruebas fijan— es que el desglose RECONSTRUYE
#       el puntaje. Un desglose que muestra números que no explican la nota es
#       peor que no tener desglose.
#
# El seed es ADITIVO sobre `rpm_threshold_seed`: reutiliza su flota, su base
# Geotab, sus motores (`TEST-RPM-LOW` 1925/2300, `TEST-RPM-HIGH` 2230/2600,
# `TEST-RPM-NOOVER` 1900/NULL) y sus dos reglas de Seguridad, y añade vehículos
# NUEVOS con filas propias de `fact_combustible_daily`. Sin combustible no hay
# km ni horas, y sin exposición el QHO/QHS no es evaluable: por eso los
# vehículos de `rpm_threshold_seed` no sirven para esto.
# ---------------------------------------------------------------------------

_CD_VEH_PENAL = "TEST-VEH-CALDET-PENAL"  # evento 2500 > sobrevelocidad 2300
_CD_VEH_AGGR = "TEST-VEH-CALDET-AGGR"  # gemelo con evento 2100: solo agrava
_CD_VEH_NOOVER = "TEST-VEH-CALDET-NOOVER"  # 3200 RPM, motor sin sobrevelocidad
_CD_VEH_OVERSET = "TEST-VEH-CALDET-OVERSET"  # 3200 RPM, motor CON sobrevelocidad
_CD_VEH_MOTIVOS = "TEST-VEH-CALDET-MOTIVOS"  # eficiente peor que ralentí
_CD_VEH_CLEAN = "TEST-VEH-CALDET-CLEAN"  # nada que reprochar
_CD_VEH_NOEXP = "TEST-VEH-CALDET-NOEXP"  # sin km ni horas: componente no evaluable
_CD_VEH_QHS = "TEST-VEH-CALDET-QHS"  # eventos de seguridad de tres tipos

_CD_ALL_VEHICLES = [
    _CD_VEH_PENAL,
    _CD_VEH_AGGR,
    _CD_VEH_NOOVER,
    _CD_VEH_OVERSET,
    _CD_VEH_MOTIVOS,
    _CD_VEH_CLEAN,
    _CD_VEH_NOEXP,
    _CD_VEH_QHS,
]

_CD_DEVICES = {vid: f"DEV-{vid}" for vid in _CD_ALL_VEHICLES}
_CD_PLATES = {
    _CD_VEH_PENAL: "CALDET1",
    _CD_VEH_AGGR: "CALDET2",
    _CD_VEH_NOOVER: "CALDET3",
    _CD_VEH_OVERSET: "CALDET4",
    _CD_VEH_MOTIVOS: "CALDET5",
    _CD_VEH_CLEAN: "CALDET6",
    _CD_VEH_NOEXP: "CALDET7",
    _CD_VEH_QHS: "CALDET8",
}

_CD_MOTORS = {
    # 1925 gobernada / 2300 sobrevelocidad: la pareja PENAL/AGGR se separa
    # justo entre esos dos límites.
    _CD_VEH_PENAL: _RPM_MOTOR_LOW,
    _CD_VEH_AGGR: _RPM_MOTOR_LOW,
    # 1900 gobernada / sobrevelocidad NULL: el caso fail-open.
    _CD_VEH_NOOVER: _RPM_MOTOR_NOOVER,
    # 2230 / 2600: la contraprueba del fail-open con el MISMO RPM.
    _CD_VEH_OVERSET: _RPM_MOTOR_HIGH,
    _CD_VEH_MOTIVOS: _RPM_MOTOR_HIGH,
    _CD_VEH_CLEAN: _RPM_MOTOR_HIGH,
    _CD_VEH_NOEXP: _RPM_MOTOR_HIGH,
    _CD_VEH_QHS: _RPM_MOTOR_HIGH,
}

# Perfil operativo "bueno": eficiente 0.65 (por debajo de la meta 0.70, así que
# el componente no queda clavado en 100 y los puntos perdidos son distintos de
# cero) y ralentí 0.12. Con 1000 km y 20 h por día el QGen sin penalizar queda
# por encima del umbral de "Cumple", que es lo que hace visible el override.
_CD_GOOD = {"econ": 0.40, "bal": 0.25, "ralenti": 0.12}
# Eficiente en 0 (pierde 0.5 x 100 = 50 puntos) y ralentí en 0.25 (pierde
# 0.3 x 75 = 22.5): dos componentes en rojo con impactos DISTINTOS, para que el
# orden de `motivos` no pueda pasar por casualidad.
_CD_BAD = {"econ": 0.0, "bal": 0.0, "ralenti": 0.25}
# Los tres componentes del QHO al 100: eficiente ≥0.70 y ralentí ≤0.10.
_CD_PERFECT = {"econ": 0.45, "bal": 0.30, "ralenti": 0.05}

# fact_row_id -> (vehicle_id, date_key, km, hrs_ecm, perfil)
# Febrero existe solo para PENAL y AGGR: es el mes SIN eventos, y sirve para
# probar que el override de la evolución mensual se aplica al vehículo-mes que
# tiene el exceso y no al vehículo entero.
_CD_FACTS: dict[str, tuple[str, int, float, float, dict[str, float]]] = {
    "TEST-FACT-CALDET-PENAL-JAN": (_CD_VEH_PENAL, 20260101, 1000.0, 20.0, _CD_GOOD),
    "TEST-FACT-CALDET-PENAL-FEB": (_CD_VEH_PENAL, 20260201, 1000.0, 20.0, _CD_GOOD),
    "TEST-FACT-CALDET-AGGR-JAN": (_CD_VEH_AGGR, 20260101, 1000.0, 20.0, _CD_GOOD),
    "TEST-FACT-CALDET-AGGR-FEB": (_CD_VEH_AGGR, 20260201, 1000.0, 20.0, _CD_GOOD),
    "TEST-FACT-CALDET-NOOVER-JAN": (_CD_VEH_NOOVER, 20260101, 1000.0, 20.0, _CD_GOOD),
    "TEST-FACT-CALDET-OVERSET-JAN": (_CD_VEH_OVERSET, 20260101, 1000.0, 20.0, _CD_GOOD),
    "TEST-FACT-CALDET-MOTIVOS-JAN": (_CD_VEH_MOTIVOS, 20260101, 1000.0, 20.0, _CD_BAD),
    "TEST-FACT-CALDET-CLEAN-JAN": (_CD_VEH_CLEAN, 20260101, 1000.0, 20.0, _CD_PERFECT),
    # km y horas en 0: la exposición de RPM no es calculable, así que el
    # componente de excesos queda `puntos=None` — no en 0.
    "TEST-FACT-CALDET-NOEXP-JAN": (_CD_VEH_NOEXP, 20260101, 0.0, 0.0, _CD_PERFECT),
    "TEST-FACT-CALDET-QHS-JAN": (_CD_VEH_QHS, 20260101, 1000.0, 20.0, _CD_GOOD),
}

# event_sk -> (vehicle_id, rule_sk, event_type, rpm)
_CD_EVENTS: dict[str, tuple[str, str, str, float | None]] = {
    # 2500 > 2300 (sobrevelocidad del motor LOW) → penaliza. Y también
    # > 1925, así que agrava: overspeed ⊇ governed por el check constraint.
    "TEST-EV-CALDET-PENAL-RPM": (_CD_VEH_PENAL, _RPM_RULE_SK, "Excesos de RPM", 2500.0),
    "TEST-EV-CALDET-PENAL-FRENADA": (
        _CD_VEH_PENAL,
        _RPM_RULE_OTHER_SK,
        "Frenadas Bruscas",
        None,
    ),
    # 2100: por encima de la gobernada 1925 y por DEBAJO de la sobrevelocidad
    # 2300. Agrava x2 y no penaliza. Es el caso que separa las dos reglas.
    "TEST-EV-CALDET-AGGR-RPM": (_CD_VEH_AGGR, _RPM_RULE_SK, "Excesos de RPM", 2100.0),
    "TEST-EV-CALDET-AGGR-FRENADA": (
        _CD_VEH_AGGR,
        _RPM_RULE_OTHER_SK,
        "Frenadas Bruscas",
        None,
    ),
    # Mismo 3200 RPM en los dos: la única diferencia es si el motor tiene
    # `max_overspeed_rpm` capturada.
    "TEST-EV-CALDET-NOOVER-RPM": (_CD_VEH_NOOVER, _RPM_RULE_SK, "Excesos de RPM", 3200.0),
    "TEST-EV-CALDET-OVERSET-RPM": (
        _CD_VEH_OVERSET,
        _RPM_RULE_SK,
        "Excesos de RPM",
        3200.0,
    ),
    # Vehículo de QHS: tres tipos con pesos distintos (1.0 / 0.5 / 0.25) más un
    # evento de RPM que NO debe aparecer en el desglose de seguridad.
    "TEST-EV-CALDET-QHS-FRENADA-1": (
        _CD_VEH_QHS,
        _RPM_RULE_OTHER_SK,
        "Frenadas Bruscas",
        None,
    ),
    "TEST-EV-CALDET-QHS-FRENADA-2": (
        _CD_VEH_QHS,
        _RPM_RULE_OTHER_SK,
        "Frenadas Bruscas",
        None,
    ),
    "TEST-EV-CALDET-QHS-ACEL-1": (
        _CD_VEH_QHS,
        _RPM_RULE_OTHER_SK,
        "Aceleraciones Bruscas",
        None,
    ),
    "TEST-EV-CALDET-QHS-ACEL-2": (
        _CD_VEH_QHS,
        _RPM_RULE_OTHER_SK,
        "Aceleraciones Bruscas",
        None,
    ),
    "TEST-EV-CALDET-QHS-BACHE": (
        _CD_VEH_QHS,
        _RPM_RULE_OTHER_SK,
        "Baches o Resaltos Fuertes",
        None,
    ),
    # 2100 con el motor HIGH (gobernada 2230): cuenta x1, no agrava y no
    # penaliza. Está aquí para probar que el QHS lo ignora sin que su ausencia
    # se confunda con "no hubo eventos de RPM".
    "TEST-EV-CALDET-QHS-RPM": (_CD_VEH_QHS, _RPM_RULE_SK, "Excesos de RPM", 2100.0),
}


@pytest_asyncio.fixture(scope="module")
async def calificacion_detalle_seed(rpm_threshold_seed):
    """Vehículos con combustible + eventos para la penalización y el desglose.

    Aditivo por construcción: no modifica ninguna fila de los seeds anteriores
    y sus `vehicle_id` no están en `_RPM_ALL_VEHICLES` ni en
    `_HM_ALL_VEHICLES`, que es lo que filtran todas las aserciones existentes.
    """
    fleet_id = rpm_threshold_seed
    async with AsyncSessionLocal() as session:
        try:
            geotab_db_id = (
                await session.execute(
                    select(GeotabDatabase.id).where(
                        GeotabDatabase.fleet_id == fleet_id,
                        GeotabDatabase.database_key == _RPM_DB_KEY,
                    )
                )
            ).scalar_one()
            session.add_all(
                [
                    Vehicle(
                        plate=_CD_PLATES[vid],
                        geotab_device_id=_CD_DEVICES[vid],
                        geotab_customer_status="found",
                        fleet_id=fleet_id,
                        geotab_database_id=geotab_db_id,
                        is_active=True,
                        vocacional=False,
                    )
                    for vid in _CD_ALL_VEHICLES
                ]
            )
            # `analytics_seed` solo sembró 20260101 y 20260319; febrero hace
            # falta para el segundo mes de la evolución y la tabla real define
            # FK fact.*.date_key -> dim_date.date_key. Se deja al terminar: una
            # fila de dimensión de más es inocua y borrarla es lo que sí puede
            # chocar con la FK de otro seed.
            await session.execute(
                text("""
                INSERT INTO analytics.dim_date
                    (date_key, date, year, month, day, quarter, month_key, month_start_date)
                VALUES (20260201, '2026-02-01', 2026, 2, 1, 1, 202602, '2026-02-01')
                ON CONFLICT (date_key) DO NOTHING
            """)
            )
            for vid in _CD_ALL_VEHICLES:
                await session.execute(
                    text("""
                    INSERT INTO analytics.dim_vehicle
                        (vehicle_id, device_id, vehicle_label, database_name, motor_type,
                         is_active)
                    VALUES (:vid, :dev, :label, :db, :mt, true)
                    ON CONFLICT (vehicle_id) DO UPDATE SET
                        device_id = EXCLUDED.device_id,
                        vehicle_label = EXCLUDED.vehicle_label,
                        database_name = EXCLUDED.database_name,
                        motor_type = EXCLUDED.motor_type,
                        is_active = EXCLUDED.is_active
                    """),
                    {
                        "vid": vid,
                        "dev": _CD_DEVICES[vid],
                        "label": f"Vehículo {_CD_PLATES[vid]}",
                        "db": _RPM_DB_KEY,
                        "mt": _CD_MOTORS[vid],
                    },
                )
            for fact_id, (vid, date_key, km, hrs, perfil) in _CD_FACTS.items():
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_combustible_daily
                        (fact_row_id, vehicle_id, database_name, motor_type, date_key,
                         fecha, placa, kms_ecm, kms_gps, kms_effective, hrs_ecm, hrs_gps,
                         fuel_kind, fuel_unit, distance_source, distance_quality_status,
                         gps_quality_valid,
                         pct_rango_economico, pct_rango_balanceado, tiempo_total_en_rango,
                         pct_ralenti, horas_ralenti_base, fuente_ralenti, revision)
                    VALUES (:fid, :vid, :db, :mt, :dk,
                            :fecha, :placa, :km, :km, :km,
                            :hrs, :hrs,
                            'liquid', 'gal', 'ecm', 'ok', true,
                            :econ, :bal, 20.0,
                            :ralenti, 20.0, 'ecm', false)
                    ON CONFLICT (fact_row_id) DO UPDATE SET
                        vehicle_id = EXCLUDED.vehicle_id,
                        date_key = EXCLUDED.date_key,
                        kms_ecm = EXCLUDED.kms_ecm,
                        kms_gps = EXCLUDED.kms_gps,
                        kms_effective = EXCLUDED.kms_effective,
                        hrs_ecm = EXCLUDED.hrs_ecm,
                        hrs_gps = EXCLUDED.hrs_gps,
                        pct_rango_economico = EXCLUDED.pct_rango_economico,
                        pct_rango_balanceado = EXCLUDED.pct_rango_balanceado,
                        tiempo_total_en_rango = EXCLUDED.tiempo_total_en_rango,
                        pct_ralenti = EXCLUDED.pct_ralenti,
                        horas_ralenti_base = EXCLUDED.horas_ralenti_base
                    """),
                    {
                        "fid": fact_id,
                        "vid": vid,
                        "db": _RPM_DB_KEY,
                        "mt": _CD_MOTORS[vid],
                        "dk": date_key,
                        "fecha": date(date_key // 10000, date_key // 100 % 100, date_key % 100),
                        "placa": _CD_PLATES[vid],
                        "km": km,
                        "hrs": hrs,
                        "econ": perfil["econ"],
                        "bal": perfil["bal"],
                        "ralenti": perfil["ralenti"],
                    },
                )
            for idx, (event_sk, (vid, rule_sk, event_type, rpm)) in enumerate(_CD_EVENTS.items()):
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_habito_event
                        (event_sk, event_id, vehicle_id, database_name, date_key, rule_sk,
                         event_type, fecha, placa, distancia_evento_mt, duracion_evento,
                         observacion_corta, fecha_y_hora_del_evento, latitud, longitud, rpm)
                    VALUES (:esk, :esk, :vid, :db, 20260101, :rsk, :etype,
                            '2026-01-01', :placa, 1000.0, 30.0,
                            'evento sembrado sin lectura en texto', :ts, 4.7, -74.2, :rpm)
                    ON CONFLICT (event_sk) DO UPDATE SET
                        vehicle_id = EXCLUDED.vehicle_id,
                        rule_sk = EXCLUDED.rule_sk,
                        event_type = EXCLUDED.event_type,
                        rpm = EXCLUDED.rpm
                    """),
                    {
                        "esk": event_sk,
                        "vid": vid,
                        "db": _RPM_DB_KEY,
                        "rsk": rule_sk,
                        "etype": event_type,
                        "placa": _CD_PLATES[vid],
                        "ts": datetime(2026, 1, 1, 16, idx, tzinfo=UTC),
                        "rpm": rpm,
                    },
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield fleet_id

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM analytics.fact_habito_event WHERE event_sk = ANY(:esks)"),
            {"esks": list(_CD_EVENTS)},
        )
        await session.execute(
            text("DELETE FROM analytics.fact_combustible_daily WHERE fact_row_id = ANY(:fids)"),
            {"fids": list(_CD_FACTS)},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = ANY(:vids)"),
            {"vids": _CD_ALL_VEHICLES},
        )
        await session.execute(
            delete(Vehicle).where(Vehicle.geotab_device_id.in_(list(_CD_DEVICES.values())))
        )
        await session.commit()


def _cd_por_vehiculo(client: TestClient, vehicles: list[str]) -> dict[str, dict]:
    return {v["vehicle_id"]: v for v in _calificacion(client, vehicles)["vehiculos"]}


def _cd_promedio(pares: list[tuple[float, float]]) -> float:
    """Réplica de `_promedio_ponderado`: media por exposición, simple si no hay peso."""
    total = sum(w for _, w in pares)
    if total <= 0:
        return sum(v for v, _ in pares) / len(pares)
    return sum(v * w for v, w in pares) / total


def _cd_numeros(texto: str) -> set[float]:
    """Números que aparecen en un texto, para comparar contra la metodología."""
    return {float(t.replace(",", ".")) for t in re.findall(r"\d+(?:[.,]\d+)?", texto)}


# --- A) Penalización por sobrevelocidad -----------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_overspeed_event_zeroes_qgen_and_keeps_base(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """Un solo exceso sobre la sobrevelocidad tumba la calificación.

    La aserción que hace la prueba útil es `qgen_base > 0`: sin ella, un
    vehículo que simplemente puntúa 0 pasaría igual y la prueba no probaría
    nada. Aquí `qgen_base` está además por encima del umbral de "Cumple", así
    que el estado pasa de verde a rojo por el override y solo por él.
    """
    data = _calificacion(admin_client, [_CD_VEH_PENAL])
    v = {x["vehicle_id"]: x for x in data["vehiculos"]}[_CD_VEH_PENAL]

    assert v["eventos_rpm_sobre_sobrevelocidad"] == 1
    assert v["penalizado_por_sobrevelocidad"] is True
    assert v["qgen"] == pytest.approx(0.0)
    assert v["estado"] == "No cumple"

    assert v["qgen_base"] is not None
    assert v["qgen_base"] > 0.0, "sin esto la prueba no distingue el override de un 0 real"
    assert v["qgen_base"] >= data["umbrales"]["cumple"], (
        "el seed debe dejar al vehículo en verde antes del override"
    )

    # `qhs` y `qho` NO se tocan: la penalización es un override del QGen.
    met = data["metodologia"]
    assert v["qhs"] is not None and v["qhs"] > 0.0
    assert v["qho"] is not None and v["qho"] > 0.0
    assert v["qgen_base"] == pytest.approx(met["peso_qhs"] * v["qhs"] + met["peso_qho"] * v["qho"])


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_event_between_governed_and_overspeed_only_aggravates(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """El caso que separa las dos reglas: agravación x2 sin penalización.

    Los dos vehículos comparten motor, combustible y eventos; solo cambia el
    RPM del exceso (2100 contra 2500, con gobernada 1925 y sobrevelocidad
    2300). Por eso el `qgen_base` del penalizado tiene que ser EXACTAMENTE el
    `qgen` del otro: si difieren, la penalización está tocando el puntaje en
    vez de sustituirlo.
    """
    por = _cd_por_vehiculo(admin_client, [_CD_VEH_PENAL, _CD_VEH_AGGR])
    aggr = por[_CD_VEH_AGGR]
    penal = por[_CD_VEH_PENAL]

    assert aggr["eventos_rpm"] == 1
    assert aggr["eventos_rpm_sobre_gobernada"] == 1, "2100 > 1925: sí agrava"
    assert aggr["eventos_rpm_sobre_sobrevelocidad"] == 0, "2100 < 2300: no penaliza"
    assert aggr["penalizado_por_sobrevelocidad"] is False
    assert aggr["qgen"] == pytest.approx(aggr["qgen_base"])
    assert aggr["estado"] != "No cumple"

    # El penalizado también agravó: `overspeed` ⊇ `governed` por construcción
    # del catálogo (max_overspeed_rpm >= governed_speed_rpm).
    assert penal["eventos_rpm_sobre_gobernada"] == 1
    assert penal["qgen_base"] == pytest.approx(aggr["qgen"])


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_overspeed_penalty_is_fail_open_without_limit(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """Fail-open: sin `max_overspeed_rpm` capturada no se puede penalizar.

    Mismo evento de 3200 RPM en los dos vehículos. La única diferencia es que
    uno tiene el límite en `motor_catalog` y el otro no. La contraprueba está
    en la MISMA respuesta, así que un 0 no puede pasar por "el seed no llegó".
    """
    por = _cd_por_vehiculo(admin_client, [_CD_VEH_NOOVER, _CD_VEH_OVERSET])

    noover = por[_CD_VEH_NOOVER]
    assert noover["eventos_rpm"] == 1, "el evento no puede desaparecer del conteo"
    assert noover["eventos_rpm_sobre_sobrevelocidad"] == 0
    assert noover["penalizado_por_sobrevelocidad"] is False
    assert noover["qgen"] == pytest.approx(noover["qgen_base"])
    assert noover["estado"] != "No cumple"

    over = por[_CD_VEH_OVERSET]
    assert over["eventos_rpm"] == 1
    assert over["eventos_rpm_sobre_sobrevelocidad"] == 1, "3200 > 2600 debe penalizar"
    assert over["penalizado_por_sobrevelocidad"] is True
    assert over["qgen"] == pytest.approx(0.0)
    assert over["estado"] == "No cumple"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_overspeed_override_reaches_the_aggregate(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """El `qgen` efectivo es el que alimenta donut y promedio, no `qgen_base`.

    Si el override se quedara en la fila del vehículo, el donut contaría un
    "Cumple" y el promedio general no bajaría. Las dos mitades se comprueban
    contra la misma respuesta.
    """
    data = _calificacion(admin_client, [_CD_VEH_PENAL, _CD_VEH_AGGR])
    vehiculos = data["vehiculos"]
    assert len(vehiculos) == 2

    assert data["estado"]["no_cumple"] == 1, "el penalizado tiene que entrar en rojo"
    assert data["estado"]["cumple"] == 1

    pesos = {v["vehicle_id"]: (v["detalle"]["exposicion_unidades"] or 0.0) for v in vehiculos}
    efectivo = _cd_promedio([(v["qgen"], pesos[v["vehicle_id"]]) for v in vehiculos])
    base = _cd_promedio([(v["qgen_base"], pesos[v["vehicle_id"]]) for v in vehiculos])

    assert data["promedio_general"] == pytest.approx(efectivo)
    assert data["promedio_general"] < base, "si el promedio no baja, el override no se propagó"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_monthly_evolution_applies_the_override(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """El override es por vehículo-MES, no por vehículo.

    El seed le da al vehículo penalizado dos meses de operación idéntica y el
    exceso solo en enero. Enero tiene que caer a 0 y febrero conservar su
    puntaje: un override aplicado al vehículo entero borraría los dos.
    """
    data = _calificacion(admin_client, [_CD_VEH_PENAL])
    por_mes = {p["periodo"]: p for p in data["evolucion"]}
    assert set(por_mes) == {202601, 202602}, por_mes

    enero = por_mes[202601]
    assert enero["qgen"] == pytest.approx(0.0)
    assert enero["qhs"] is not None and enero["qhs"] > 50.0, "el QHS del mes no se toca"
    assert enero["qho"] is not None and enero["qho"] > 50.0, "el QHO del mes no se toca"

    febrero = por_mes[202602]
    assert febrero["qgen"] is not None and febrero["qgen"] > 50.0, (
        "el mes sin excesos no se penaliza"
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_methodology_publishes_the_overspeed_penalty(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """La metodología es el contrato que el front muestra: tiene que decirlo.

    Se busca una subcadena estable, no el texto completo: la redacción puede
    cambiar sin que cambie la regla.
    """
    met = _calificacion(admin_client, [_CD_VEH_PENAL])["metodologia"]
    texto = met["penalizacion_sobrevelocidad"]

    assert isinstance(texto, str)
    assert texto.strip(), "el campo no puede publicarse vacío"
    assert "sobrevelocidad" in texto.lower(), texto


# --- B) Desglose por vehículo: el detalle reconstruye el puntaje ----------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_detalle_componentes_reconstruct_qho(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """Los aportes de `componentes_qho` reconstruyen el QHO del vehículo.

    La relación NO es una suma cruda: `_score_qho` normaliza por la suma de
    pesos de los componentes EVALUABLES, así que el QHO es
    Σaporte / Σpeso(evaluables). Con los tres componentes presentes esa suma
    vale 1 y parece una suma; con uno no evaluable, no. Se fija como es.
    """
    data = _calificacion(admin_client, _CD_ALL_VEHICLES)
    assert len(data["vehiculos"]) == len(_CD_ALL_VEHICLES)

    for v in data["vehiculos"]:
        detalle = v["detalle"]
        assert detalle is not None, v["vehicle_id"]
        comps = detalle["componentes_qho"]
        assert comps, v["vehicle_id"]

        for c in comps:
            if c["puntos"] is None:
                assert c["aporte"] is None, (v["vehicle_id"], c["nombre"])
                assert c["puntos_perdidos"] is None, (v["vehicle_id"], c["nombre"])
            else:
                assert c["aporte"] == pytest.approx(c["puntos"] * c["peso"]), (
                    v["vehicle_id"],
                    c["nombre"],
                )
                assert c["puntos_perdidos"] == pytest.approx(c["peso"] * (100.0 - c["puntos"])), (
                    v["vehicle_id"],
                    c["nombre"],
                )

        evaluables = [c for c in comps if c["puntos"] is not None]
        assert evaluables, v["vehicle_id"]
        peso = sum(c["peso"] for c in evaluables)
        aporte = sum(c["aporte"] for c in evaluables)
        assert v["qho"] == pytest.approx(aporte / peso), v["vehicle_id"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_detalle_eventos_reconstruct_qhs(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """`eventos_qhs` reconstruye el peso que produce el QHS, sin los RPM.

    Los excesos de RPM están excluidos del QHS a propósito (ya pesan dentro del
    QHO). El vehículo del seed tiene además un evento de RPM, así que la
    ausencia de RPM en el desglose no se puede confundir con "no hubo".
    """
    data = _calificacion(admin_client, [_CD_VEH_QHS])
    v = {x["vehicle_id"]: x for x in data["vehiculos"]}[_CD_VEH_QHS]
    d = v["detalle"]

    eventos = d["eventos_qhs"]
    assert eventos
    tipos = {e["event_type"].strip().lower() for e in eventos}
    assert not any("rpm" in t for t in tipos), f"los excesos de RPM no puntúan en QHS: {tipos}"
    assert len(tipos) == 3, tipos
    assert v["eventos_rpm"] == 1, "pero el evento de RPM sí se contó por su lado"
    assert sum(e["n_eventos"] for e in eventos) == 5

    for e in eventos:
        assert e["aporte_ponderado"] == pytest.approx(e["n_eventos"] * e["peso"]), e

    peso_total = sum(e["aporte_ponderado"] for e in eventos)
    km = d["km"]
    assert km is not None and km > 0
    assert d["eventos_qhs_por_1000km"] == pytest.approx(peso_total / (km / 1000.0))

    tope = d["eventos_qhs_tope"]
    esperado = 100.0 * min(1.0, max(0.0, 1.0 - d["eventos_qhs_por_1000km"] / tope))
    assert v["qhs"] == pytest.approx(esperado)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_motivos_ordered_by_points_lost(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """`motivos` va ordenado por impacto, y la sobrevelocidad siempre primera.

    El seed deja eficiente en 0 (pierde 50 puntos) y ralentí en 0.25 (pierde
    22.5): impactos distintos, así que el orden no puede acertar por
    casualidad. En el vehículo penalizado, en cambio, los componentes pierden
    poquísimo y la penalización va primera de todas formas: es una regla de
    precedencia, no de magnitud.
    """
    por = _cd_por_vehiculo(admin_client, [_CD_VEH_MOTIVOS, _CD_VEH_PENAL])

    d = por[_CD_VEH_MOTIVOS]["detalle"]
    perdidos = sorted(
        (c for c in d["componentes_qho"] if (c["puntos_perdidos"] or 0.0) > 0.0),
        key=lambda c: -c["puntos_perdidos"],
    )
    assert len(perdidos) >= 2, perdidos
    assert perdidos[0]["puntos_perdidos"] > perdidos[1]["puntos_perdidos"], (
        "el seed debe producir impactos distintos"
    )
    assert d["motivos"], "dos componentes en rojo tienen que producir motivos"
    assert perdidos[0]["nombre"].lower() in d["motivos"][0].lower(), d["motivos"]

    dp = por[_CD_VEH_PENAL]["detalle"]
    assert dp["motivos"]
    assert "sobrevelocidad" in dp["motivos"][0].lower(), dp["motivos"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_clean_vehicle_has_no_motivos(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """Sin nada que reprochar, `motivos` es la lista vacía, no un relleno."""
    v = _cd_por_vehiculo(admin_client, [_CD_VEH_CLEAN])[_CD_VEH_CLEAN]
    d = v["detalle"]

    assert all(c["puntos"] == pytest.approx(100.0) for c in d["componentes_qho"]), d[
        "componentes_qho"
    ]
    assert d["eventos_qhs"] == []
    assert v["qho"] == pytest.approx(100.0)
    assert v["qhs"] == pytest.approx(100.0)
    assert v["qgen"] == pytest.approx(100.0)
    assert d["motivos"] == [], d["motivos"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_non_evaluable_component_is_not_a_zero(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """Un componente sin insumos no puntúa 0: sale del denominador.

    El vehículo del seed no tiene km ni horas, así que la exposición de RPM no
    existe y el componente de excesos no es evaluable. Con los otros dos al
    100, el QHO tiene que ser 100 —Σaporte/Σpeso(evaluables)— y no 80, que es
    lo que daría tratar el `None` como un cero.
    """
    v = _cd_por_vehiculo(admin_client, [_CD_VEH_NOEXP])[_CD_VEH_NOEXP]
    d = v["detalle"]

    assert d["exposicion_unidades"] is None
    assert v["qhs"] is None, "sin km el QHS no es calculable"

    no_evaluables = [c for c in d["componentes_qho"] if c["puntos"] is None]
    assert len(no_evaluables) == 1, d["componentes_qho"]
    evaluables = [c for c in d["componentes_qho"] if c["puntos"] is not None]

    aporte = sum(c["aporte"] for c in evaluables)
    peso_evaluable = sum(c["peso"] for c in evaluables)
    peso_total = sum(c["peso"] for c in d["componentes_qho"])
    assert peso_evaluable < peso_total

    assert v["qho"] == pytest.approx(aporte / peso_evaluable)
    assert v["qho"] > aporte / peso_total, "un componente no evaluable no puede contar como 0"

    # Y genera su propia frase, en vez de desaparecer del desglose.
    assert any(no_evaluables[0]["nombre"].lower() in m.lower() for m in d["motivos"]), d["motivos"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_detalle_numbers_come_from_the_methodology(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """Ningún número del desglose está escrito a mano en el servicio.

    Pesos, tope y metas tienen que salir de las mismas constantes que publica
    `metodologia`. Si alguien copia un 70 o un 15 en el texto del desglose,
    deja de coincidir en cuanto se recalibre la constante — que es exactamente
    lo que ya pasó con los umbrales del gauge en el front.
    """
    data = _calificacion(admin_client, _CD_ALL_VEHICLES)
    met = data["metodologia"]

    pesos_met = {c["nombre"]: c["peso"] for c in met["componentes_qho"]}
    textos_met = {c["nombre"]: c["detalle"] for c in met["componentes_qho"]}
    pesos_evento = {e["evento"].strip().lower(): e["peso"] for e in met["eventos_qhs"]}

    for v in data["vehiculos"]:
        d = v["detalle"]
        assert d["eventos_qhs_tope"] == pytest.approx(met["eventos_tope_por_1000km"]), v[
            "vehicle_id"
        ]

        nombres = [c["nombre"] for c in d["componentes_qho"]]
        assert nombres == list(pesos_met), (v["vehicle_id"], nombres)
        assert sum(c["peso"] for c in d["componentes_qho"]) == pytest.approx(1.0)

        for c in d["componentes_qho"]:
            assert c["peso"] == pytest.approx(pesos_met[c["nombre"]]), (
                v["vehicle_id"],
                c["nombre"],
            )
            faltantes = _cd_numeros(c["objetivo_texto"]) - _cd_numeros(textos_met[c["nombre"]])
            assert not faltantes, (
                f"{v['vehicle_id']}/{c['nombre']}: {faltantes} no está en la metodología "
                f"({textos_met[c['nombre']]!r} vs objetivo {c['objetivo_texto']!r})"
            )

        for e in d["eventos_qhs"]:
            key = e["event_type"].strip().lower()
            assert key in pesos_evento, (v["vehicle_id"], key, sorted(pesos_evento))
            assert e["peso"] == pytest.approx(pesos_evento[key]), (v["vehicle_id"], key)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_calificacion_detalle_respects_fleet_isolation(
    admin_client: TestClient, calificacion_detalle_seed
) -> None:
    """El desglose nuevo no abre un camino para ver vehículos de otra flota.

    Regla operativa del repositorio: toda consulta multi-flota necesita
    prueba negativa. El actor tiene `reportes.view` y pide
    explícitamente los `vehicle_id` del seed, pero su única flota es otra:
    debe recibir cero filas y ningún `detalle`.
    """
    suffix = uuid.uuid4().hex[:8]
    role_code = f"caldet_reporter_{suffix}"
    role_resp = admin_client.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": f"Reportero Calificación {suffix}",
            "permission_codes": ["reportes.view"],
        },
    )
    assert role_resp.status_code == 201, role_resp.text

    other_fleet = admin_client.post(
        "/api/v1/fleets",
        json={"code": f"TEST-CALDETOUT-{suffix.upper()}", "name": "Flota Ajena Calificación"},
    )
    assert other_fleet.status_code == 201, other_fleet.text

    email = f"caldetout-{suffix}@portalclientes.test"
    password = "CalDetOutsider123!"
    created = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Outsider Calificación",
            "role_codes": [role_code],
            "fleet_ids": [other_fleet.json()["id"]],
        },
    )
    assert created.status_code == 201, created.text

    outsider = TestClient(app)
    login = outsider.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text

    resp = outsider.get("/api/v1/reportes/calificacion", params={"vehicle_id": _CD_ALL_VEHICLES})
    assert resp.status_code == 200, resp.text
    ajeno = resp.json()
    assert ajeno["vehiculos"] == []
    assert ajeno["promedio_general"] is None
    assert ajeno["estado"] == {"no_cumple": 0, "en_riesgo": 0, "cumple": 0}
    assert ajeno["evolucion"] == []

    # Contraprueba: el admin sí ve las filas con su desglose, así que el vacío
    # de arriba es aislamiento y no un seed que no llegó.
    propio = _cd_por_vehiculo(admin_client, _CD_ALL_VEHICLES)
    assert set(propio) == set(_CD_ALL_VEHICLES)
    assert propio[_CD_VEH_PENAL]["detalle"] is not None


# ===========================================================================
# El `total` de una página paginada, venga de donde venga
# ===========================================================================
#
# `list_fault_events` y `get_fault_timeline` dejaron de ejecutar un
# `SELECT count(*)` aparte: adjuntan el total como columna-ventana
# (`count(*) OVER ()`) a la MISMA sentencia de la página. Eso introduce un
# caso que no existía: si la página sale VACÍA la ventana no devuelve ninguna
# fila y el total no se puede leer. La implementación lo resuelve por el
# `offset`, y esa bifurcación es lo que se fija aquí:
#
#   página vacía + offset == 0  -> total 0 (el filtro no encontró nada)
#   página vacía + offset > 0   -> sentencia de conteo de respaldo
#   página no vacía             -> total = `window_total` de la primera fila
#
# La rama del respaldo es la frágil: quitarla no rompe ninguna prueba de
# contenido —los `items` siguen saliendo bien— y hace que un offset que
# desborda un conjunto no vacío reporte `total == 0`, con lo que el front
# pierde la paginación entera. Por eso cada función tiene aquí su caso 3.
#
# `list_habito_events` **conserva las dos sentencias**: al ser una selección
# plana con `ORDER BY` respaldado por índice, el planificador resuelve la
# página por top-N y se detiene en el `LIMIT`, mientras que la ventana lo
# obliga a materializar el conjunto entero (medido: 1,03x-1,54x más lenta).
# Sus pruebas se escriben igual porque los cuatro invariantes son de
# COMPORTAMIENTO, no de implementación: valen para las dos formas y sirven de
# guarda si alguien vuelve a aplicarle la ventana sin medir.
#
# Además, `list_fault_events` agrega con GROUP BY de nueve columnas: su
# ventana cuenta GRUPOS, no filas crudas. El seed hace colapsar cuatro filas
# en un grupo justamente para que ese número sea distinguible.

_WT_FLEET_CODE = f"TEST-WINTOT-{uuid.uuid4().hex[:8]}"
_WT_DB_KEY = "wintot_test_db"
_WT_VEH = "TEST-VEH-WINTOT"
_WT_DEVICE = "DEV-TEST-VEH-WINTOT"
_WT_PLATE = "WNT001"
_WT_RULE_SK = "TEST-RULE-WINTOT"

# Severidad y tipo de evento que no existen en el seed: filtro que no casa con
# nada, para el caso 4 (página vacía con offset 0 → total 0).
_WT_SEVERIDAD_INEXISTENTE = "Nivel 9 - Inexistente"
_WT_EVENTO_INEXISTENTE = "Evento Que No Existe"

# --- Fallas: 10 filas crudas que colapsan en 7 grupos ----------------------
#
# La clave de grupo son las nueve columnas de `group_cols`. Las seis primeras
# entradas difieren en `codigo_diagnostico` y `diagnostico`, así que cada una
# es su propio grupo. Las cuatro últimas comparten TODA la clave y solo varían
# en `row_id`, `fecha_de_falla` y `recuento_de_fallos`: son un único grupo.
#
# row_id -> (codigo_diagnostico, diagnostico, hora, recuento)
_WT_FAULTS: dict[str, tuple[int, str, int, int]] = {
    "TEST-FAULT-WINTOT-01": (9001, "Falla sembrada 01", 1, 1),
    "TEST-FAULT-WINTOT-02": (9002, "Falla sembrada 02", 2, 1),
    "TEST-FAULT-WINTOT-03": (9003, "Falla sembrada 03", 3, 1),
    "TEST-FAULT-WINTOT-04": (9004, "Falla sembrada 04", 4, 1),
    "TEST-FAULT-WINTOT-05": (9005, "Falla sembrada 05", 5, 1),
    "TEST-FAULT-WINTOT-06": (9006, "Falla sembrada 06", 6, 1),
    # Grupo repetido: mismo vehículo, mismo código, mismo modo de falla.
    "TEST-FAULT-WINTOT-07A": (9007, "Falla repetida", 7, 2),
    "TEST-FAULT-WINTOT-07B": (9007, "Falla repetida", 8, 3),
    "TEST-FAULT-WINTOT-07C": (9007, "Falla repetida", 9, 4),
    "TEST-FAULT-WINTOT-07D": (9007, "Falla repetida", 10, 5),
}
_WT_FAULT_FILAS = 10
_WT_FAULT_GRUPOS = 7

# --- Hábitos: 7 eventos planos, sin colapso posible ------------------------
_WT_HABITOS = [f"TEST-EV-WINTOT-{i:02d}" for i in range(1, 8)]
_WT_HABITO_FILAS = 7


@pytest_asyncio.fixture(scope="module")
async def window_total_seed(analytics_seed: None):
    """Flota propia con fallas y hábitos suficientes para paginar de verdad.

    Aditivo respecto a `analytics_seed`, que es quien crea el DDL de
    `analytics.*`: flota, base Geotab, vehículo, regla y eventos propios.
    Ninguna aserción existente toca este `vehicle_id` ni esta flota, y todas
    las consultas de esta sección filtran por ambos, así que el conteo no
    depende de lo que otros seeds hayan dejado en las tablas.
    """
    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(code=_WT_FLEET_CODE, name="Flota Test Window Total", is_active=True)
            session.add(fleet)
            await session.flush()
            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=_WT_DB_KEY,
                database_key=_WT_DB_KEY,
                connection_type="geotab",
                is_active=True,
            )
            session.add(geotab_db)
            await session.flush()
            fleet_id = fleet.id
            session.add(
                Vehicle(
                    plate=_WT_PLATE,
                    geotab_device_id=_WT_DEVICE,
                    geotab_customer_status="found",
                    fleet_id=fleet.id,
                    geotab_database_id=geotab_db.id,
                    is_active=True,
                )
            )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_vehicle
                    (vehicle_id, device_id, vehicle_label, database_name, is_active)
                VALUES (:vid, :dev, :label, :db, true)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    vehicle_label = EXCLUDED.vehicle_label,
                    database_name = EXCLUDED.database_name,
                    is_active = EXCLUDED.is_active
                """),
                {
                    "vid": _WT_VEH,
                    "dev": _WT_DEVICE,
                    "label": f"Vehículo {_WT_PLATE}",
                    "db": _WT_DB_KEY,
                },
            )
            for row_id, (codigo, diagnostico, hora, recuento) in _WT_FAULTS.items():
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_fault_event
                        (row_id, vehicle_id, database_name, date_key, fecha, movil,
                         fecha_de_falla, codigo_diagnostico, codigo_modo_de_falla,
                         codigo_controlador, nombre_fuente_diagnostico, diagnostico,
                         nombre_de_controlador, modo_de_falla, estado_de_falla,
                         recuento_de_fallos, tipo_de_atencion,
                         luz_de_parada_roja, luz_de_parada_amber, lampara_de_averia)
                    VALUES (:rid, :vid, :db, 20260101, '2026-01-01', :placa,
                            :ts, :codigo, 3.0,
                            128, 'Fuente sembrada', :diag,
                            'Controlador sembrado', 'Above range', 'Active',
                            :recuento, 'Nivel 1 - Urgente',
                            true, false, false)
                    ON CONFLICT (row_id) DO UPDATE SET
                        vehicle_id = EXCLUDED.vehicle_id,
                        codigo_diagnostico = EXCLUDED.codigo_diagnostico,
                        diagnostico = EXCLUDED.diagnostico,
                        fecha_de_falla = EXCLUDED.fecha_de_falla,
                        recuento_de_fallos = EXCLUDED.recuento_de_fallos
                    """),
                    {
                        "rid": row_id,
                        "vid": _WT_VEH,
                        "db": _WT_DB_KEY,
                        "placa": _WT_PLATE,
                        "ts": datetime(2026, 1, 1, hora, 0, tzinfo=UTC),
                        "codigo": codigo,
                        "diag": diagnostico,
                        "recuento": recuento,
                    },
                )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_rule (rule_sk, rule_id, rule_name, categoria)
                VALUES (:rsk, 'R-WINTOT-001', 'Exceso Velocidad', 'Seguridad')
                ON CONFLICT (rule_sk) DO UPDATE SET
                    rule_name = EXCLUDED.rule_name,
                    categoria = EXCLUDED.categoria
                """),
                {"rsk": _WT_RULE_SK},
            )
            for idx, event_sk in enumerate(_WT_HABITOS):
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_habito_event
                        (event_sk, event_id, vehicle_id, database_name, date_key, rule_sk,
                         event_type, fecha, placa, distancia_evento_mt, duracion_evento,
                         observacion_corta, fecha_y_hora_del_evento, latitud, longitud)
                    VALUES (:esk, :esk, :vid, :db, 20260101, :rsk, 'Exceso Velocidad',
                            '2026-01-01', :placa, 1000.0, 30.0,
                            '80 km/h', :ts, 4.7, -74.2)
                    ON CONFLICT (event_sk) DO UPDATE SET
                        vehicle_id = EXCLUDED.vehicle_id,
                        rule_sk = EXCLUDED.rule_sk,
                        event_type = EXCLUDED.event_type
                    """),
                    {
                        "esk": event_sk,
                        "vid": _WT_VEH,
                        "db": _WT_DB_KEY,
                        "rsk": _WT_RULE_SK,
                        "placa": _WT_PLATE,
                        "ts": datetime(2026, 1, 1, 12, idx, tzinfo=UTC),
                    },
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield fleet_id

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM analytics.fact_habito_event WHERE event_sk = ANY(:esks)"),
            {"esks": _WT_HABITOS},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_rule WHERE rule_sk = :rsk"),
            {"rsk": _WT_RULE_SK},
        )
        await session.execute(
            text("DELETE FROM analytics.fact_fault_event WHERE row_id = ANY(:rids)"),
            {"rids": list(_WT_FAULTS)},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = :vid"),
            {"vid": _WT_VEH},
        )
        await session.execute(delete(Vehicle).where(Vehicle.geotab_device_id == _WT_DEVICE))
        await session.execute(delete(Fleet).where(Fleet.code == _WT_FLEET_CODE))
        await session.commit()


async def _wt_fallas(fleet_id: uuid.UUID, **kw: object) -> tuple[list[dict], int]:
    """`list_fault_events` acotada al seed: una flota y un vehículo."""
    from app.services import analytics_service

    async with AsyncSessionLocal() as session:
        return await analytics_service.list_fault_events(
            session, fleet_ids=[fleet_id], vehicle_id=[_WT_VEH], **kw
        )


async def _wt_habitos(fleet_id: uuid.UUID, **kw: object) -> tuple[list[dict], int]:
    """`list_habito_events` acotada al seed."""
    from app.services import analytics_service

    async with AsyncSessionLocal() as session:
        return await analytics_service.list_habito_events(
            session, fleet_ids=[fleet_id], vehicle_id=[_WT_VEH], **kw
        )


async def _wt_timeline(fleet_id: uuid.UUID, **kw: object) -> tuple[list[dict], int]:
    """`get_fault_timeline` acotada al seed."""
    from app.services import analytics_service

    async with AsyncSessionLocal() as session:
        return await analytics_service.get_fault_timeline(
            session, fleet_ids=[fleet_id], vehicle_id=_WT_VEH, **kw
        )


# --- A) list_fault_events: la ventana cuenta GRUPOS ------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_events_window_total_counts_groups_not_rows(
    window_total_seed: uuid.UUID,
) -> None:
    """El invariante propio de esta agregación: 10 filas, 7 grupos.

    `count(*) OVER ()` se evalúa después del GROUP BY, así que tiene que
    devolver 7. Si alguien moviera la ventana a una subconsulta previa a la
    agregación —o volviera a contar filas crudas— saldría 10 y la paginación
    del front prometería tres páginas que no existen. La contraprueba de que
    las 10 filas SÍ están sembradas es `get_fault_timeline`, que sobre el
    mismo vehículo y sin filtro cuenta filas.
    """
    items, total = await _wt_fallas(window_total_seed, limit=50, offset=0)

    assert total == _WT_FAULT_GRUPOS
    assert len(items) == _WT_FAULT_GRUPOS

    _, filas = await _wt_timeline(window_total_seed, limit=50, offset=0)
    assert filas == _WT_FAULT_FILAS, "el seed debe tener más filas que grupos"
    assert total < filas, "si coinciden, la prueba no distingue grupos de filas"

    # Y el grupo que colapsa es exactamente uno, con las cuatro filas dentro.
    repetido = [i for i in items if i["codigo_diagnostico"] == 9007]
    assert len(repetido) == 1, repetido
    assert repetido[0]["recuento_de_fallos"] == 4


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_events_total_is_the_set_not_the_page(
    window_total_seed: uuid.UUID,
) -> None:
    """Casos 1 y 2: primera página y página intermedia reportan el mismo total.

    Con `limit=3` la primera página trae 3 de 7 y la segunda otros 3. Si el
    total viniera de la página valdría 3 en ambas.
    """
    primera, total_primera = await _wt_fallas(window_total_seed, limit=3, offset=0)
    assert len(primera) == 3
    assert total_primera == _WT_FAULT_GRUPOS

    intermedia, total_intermedia = await _wt_fallas(window_total_seed, limit=3, offset=3)
    assert len(intermedia) == 3
    assert total_intermedia == _WT_FAULT_GRUPOS

    ultima, total_ultima = await _wt_fallas(window_total_seed, limit=3, offset=6)
    assert len(ultima) == 1, "7 grupos en páginas de 3: la última trae uno"
    assert total_ultima == _WT_FAULT_GRUPOS

    # Orden total: las tres páginas no se pisan ni omiten grupos.
    vistos = [i["codigo_diagnostico"] for i in primera + intermedia + ultima]
    assert len(set(vistos)) == _WT_FAULT_GRUPOS, vistos


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_events_total_survives_an_offset_past_the_end(
    window_total_seed: uuid.UUID,
) -> None:
    """Caso 3, el que se rompe si se quita el conteo de respaldo.

    Un offset que desborda un conjunto NO vacío deja la ventana sin filas de
    donde leer el total. Devolver 0 ahí le diría al front que no hay nada,
    cuando lo que hay es una página fuera de rango.
    """
    items, total = await _wt_fallas(window_total_seed, limit=50, offset=1000)

    assert items == []
    assert total == _WT_FAULT_GRUPOS, "el respaldo por offset>0 no se ejecutó"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_events_empty_filter_totals_zero(
    window_total_seed: uuid.UUID,
) -> None:
    """Caso 4: filtro que no casa con nada y `offset=0` → total 0.

    Es la otra mitad de la bifurcación: aquí el vacío sí significa "no hay
    nada", y el servicio debe resolverlo sin pagar una segunda sentencia.
    """
    items, total = await _wt_fallas(
        window_total_seed, severity=_WT_SEVERIDAD_INEXISTENTE, limit=50, offset=0
    )

    assert items == []
    assert total == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_events_empty_filter_totals_zero_even_past_the_end(
    window_total_seed: uuid.UUID,
) -> None:
    """El respaldo tampoco puede inventar un total sobre un conjunto vacío.

    Mismo filtro imposible, pero con `offset > 0`: esta vez el servicio sí
    ejecuta la sentencia de conteo, y tiene que devolver 0 igual. Sin esta
    prueba, un respaldo que ignorara los filtros pasaría desapercibido.
    """
    items, total = await _wt_fallas(
        window_total_seed, severity=_WT_SEVERIDAD_INEXISTENTE, limit=50, offset=1000
    )

    assert items == []
    assert total == 0


# --- B) list_habito_events: la ventana cuenta FILAS ------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habito_events_total_is_the_set_not_the_page(
    window_total_seed: uuid.UUID,
) -> None:
    """Casos 1 y 2 en la consulta plana con LEFT JOIN.

    Los tres LEFT OUTER JOIN de `list_habito_events` (dim_rule, dim_vehicle,
    motor_catalog) no pueden multiplicar filas: si lo hicieran, el total
    saldría por encima de los 7 eventos sembrados.
    """
    primera, total_primera = await _wt_habitos(window_total_seed, limit=3, offset=0)
    assert len(primera) == 3
    assert total_primera == _WT_HABITO_FILAS

    intermedia, total_intermedia = await _wt_habitos(window_total_seed, limit=3, offset=3)
    assert len(intermedia) == 3
    assert total_intermedia == _WT_HABITO_FILAS

    vistos = [i["event_sk"] for i in primera + intermedia]
    assert len(set(vistos)) == 6, vistos


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habito_events_total_survives_an_offset_past_the_end(
    window_total_seed: uuid.UUID,
) -> None:
    """Caso 3 para hábitos: una página fuera de rango no vacía el total.

    Hoy lo cumple gratis, porque el conteo va en su propia sentencia y no
    depende de que la página traiga filas. La prueba se mantiene porque es el
    invariante que se rompería si esta función volviera a la ventana.
    """
    items, total = await _wt_habitos(window_total_seed, limit=50, offset=1000)

    assert items == []
    assert total == _WT_HABITO_FILAS, "un offset fuera de rango no reduce el total"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habito_events_empty_filter_totals_zero(
    window_total_seed: uuid.UUID,
) -> None:
    """Caso 4 para hábitos."""
    items, total = await _wt_habitos(
        window_total_seed, event_type=_WT_EVENTO_INEXISTENTE, limit=50, offset=0
    )

    assert items == []
    assert total == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_habito_events_empty_filter_totals_zero_even_past_the_end(
    window_total_seed: uuid.UUID,
) -> None:
    """Conjunto vacío y offset fuera de rango: el total sigue siendo 0.

    El conteo tiene que arrastrar los mismos filtros que la página; si los
    perdiera devolvería los 7 eventos del seed.
    """
    items, total = await _wt_habitos(
        window_total_seed, event_type=_WT_EVENTO_INEXISTENTE, limit=50, offset=1000
    )

    assert items == []
    assert total == 0


# --- C) get_fault_timeline: la ventana cuenta FILAS ------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_timeline_total_is_the_set_not_the_page(
    window_total_seed: uuid.UUID,
) -> None:
    """Casos 1 y 2 en la línea de tiempo: 10 filas, sin agregación."""
    primera, total_primera = await _wt_timeline(window_total_seed, limit=4, offset=0)
    assert len(primera) == 4
    assert total_primera == _WT_FAULT_FILAS

    intermedia, total_intermedia = await _wt_timeline(window_total_seed, limit=4, offset=4)
    assert len(intermedia) == 4
    assert total_intermedia == _WT_FAULT_FILAS

    ultima, total_ultima = await _wt_timeline(window_total_seed, limit=4, offset=8)
    assert len(ultima) == 2
    assert total_ultima == _WT_FAULT_FILAS

    vistos = [i["row_id"] for i in primera + intermedia + ultima]
    assert len(set(vistos)) == _WT_FAULT_FILAS, vistos


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_timeline_total_survives_an_offset_past_the_end(
    window_total_seed: uuid.UUID,
) -> None:
    """Caso 3 para la línea de tiempo."""
    items, total = await _wt_timeline(window_total_seed, limit=15, offset=1000)

    assert items == []
    assert total == _WT_FAULT_FILAS, "el respaldo por offset>0 no se ejecutó"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_timeline_empty_filter_totals_zero(
    window_total_seed: uuid.UUID,
) -> None:
    """Caso 4 para la línea de tiempo: código de diagnóstico inexistente."""
    items, total = await _wt_timeline(
        window_total_seed, codigo_diagnostico=999999, limit=15, offset=0
    )

    assert items == []
    assert total == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_timeline_filtered_total_counts_only_its_rows(
    window_total_seed: uuid.UUID,
) -> None:
    """Un filtro que sí casa acota el total, y el respaldo respeta ese filtro.

    El grupo repetido tiene 4 de las 10 filas. Con `codigo_diagnostico=9007`
    el total baja a 4 tanto por la ventana (offset 0) como por el respaldo
    (offset fuera de rango): si el conteo de respaldo perdiera los filtros
    devolvería 10 y solo se vería aquí.
    """
    items, total = await _wt_timeline(
        window_total_seed, codigo_diagnostico=9007, limit=15, offset=0
    )
    assert len(items) == 4
    assert total == 4

    vacio, total_desbordado = await _wt_timeline(
        window_total_seed, codigo_diagnostico=9007, limit=15, offset=1000
    )
    assert vacio == []
    assert total_desbordado == 4


# ---------------------------------------------------------------------------
# Fallas del DISPOSITIVO TELEMÁTICO: excluibles, pero solo si se piden fuera
# ---------------------------------------------------------------------------
#
# `_apply_fault_filters` sabe excluir `nombre_fuente_diagnostico =
# 'SourceGeotabGoId'` porque esas fallas las reporta el equipo Geotab sobre sí
# mismo —reinicios, pérdida de energía, desconexión, cámara, sensor de llanta—.
# El corte es OPT-IN (`exclude_telematics=True`) y el DEFAULT NO EXCLUYE NADA,
# porque los mismos ocho endpoints `/reportes/fallas/*` los consumen dos
# pantallas con propósitos distintos:
#
#   - el tab de Fallas de reportes quiere el histórico DEL VEHÍCULO y sí pide
#     el corte;
#   - el módulo Navifault trabaja sobre TODO lo que llega, incluido lo que
#     reporta el equipo.
#
# Excluir por defecto —como se hizo en `e48dfe4`— le quitó a Navifault el
# 15,4 % de su ventana de 24 h sin que nadie lo pidiera. Por eso esta sección
# se prueba en los dos sentidos: con el flag no salen, y SIN el flag salen. La
# asimetría es el arreglo; si alguien vuelve a poner la exclusión por defecto,
# los casos `*_por_defecto` fallan y dicen exactamente por qué.
#
# El corte alimenta las ocho consultas de fallas, así que fijarlo solo en el
# listado dejaría siete puertas abiertas: aquí se cubren listado, resumen,
# Pareto, severidad, ranking y línea de tiempo.
#
# El caso que más importa es la fila de fuente NULL. La implementación usa
# `is_distinct_from` y no `!=` justamente para que una fuente desconocida NO se
# confunda con una falla del dispositivo: con `!=` la comparación da UNKNOWN,
# el WHERE la descarta y la fila desaparece del reporte en silencio. Esa
# regresión no la caza ningún conteo de las fallas del vehículo, solo esta fila.

_TD_FLEET_CODE = f"TEST-TELEM-{uuid.uuid4().hex[:8]}"
_TD_DB_KEY = "telematica_test_db"
_TD_VEH = "TEST-VEH-TELEMATICA"
_TD_DEVICE = "DEV-TEST-VEH-TELEMATICA"
_TD_PLATE = "TLM001"

#: Fuente de un bus real del vehículo (J1939). Es lo que el filtro debe dejar pasar.
_TD_FUENTE_VEHICULO = "SourceJ1939Id"
#: Fuente del propio equipo telemático: lo que el filtro debe excluir.
_TD_FUENTE_TELEMATICA = "SourceGeotabGoId"
#: Nombre para mostrar que acompaña a la fuente telemática en el dato real.
_TD_CONTROLADOR_TELEMATICO = "Telematics device"
_TD_CONTROLADOR_VEHICULO = "Motor"
#: Controlador de la fila de fuente NULL: distinto de los otros dos para poder
#: afirmar que sobrevive al Pareto por controlador.
_TD_CONTROLADOR_DESCONOCIDO = "Controlador sin fuente"

_TD_URGENTE = "Nivel 1 - Urgente"
_TD_PROGRAMABLE = "Nivel 3 - Programable"

# row_id -> (codigo, diagnostico, fuente, controlador, severidad, luz_roja, hora)
_TD_FAULTS: dict[str, tuple[int, str, str | None, str, str, bool, int]] = {
    # --- Fallas del vehículo: deben aparecer siempre ---
    "TEST-FAULT-TELEM-V1": (
        7001,
        "Falla de vehículo 01",
        _TD_FUENTE_VEHICULO,
        _TD_CONTROLADOR_VEHICULO,
        _TD_URGENTE,
        True,
        1,
    ),
    "TEST-FAULT-TELEM-V2": (
        7002,
        "Falla de vehículo 02",
        _TD_FUENTE_VEHICULO,
        _TD_CONTROLADOR_VEHICULO,
        _TD_URGENTE,
        True,
        2,
    ),
    "TEST-FAULT-TELEM-V3": (
        7003,
        "Falla de vehículo 03",
        _TD_FUENTE_VEHICULO,
        _TD_CONTROLADOR_VEHICULO,
        _TD_PROGRAMABLE,
        False,
        3,
    ),
    # --- Fuente desconocida: NO es del dispositivo, tiene que sobrevivir ---
    "TEST-FAULT-TELEM-NULL": (
        7004,
        "Falla de fuente desconocida",
        None,
        _TD_CONTROLADOR_DESCONOCIDO,
        _TD_PROGRAMABLE,
        False,
        4,
    ),
    # --- Dispositivo telemático: fuera del reporte ---
    # Se siembran urgentes y con luz roja a propósito: si el filtro se cayera,
    # moverían `n_urgentes`, la severidad y el ranking, no solo el listado.
    "TEST-FAULT-TELEM-T1": (
        7101,
        "Reinicio del dispositivo telemático",
        _TD_FUENTE_TELEMATICA,
        _TD_CONTROLADOR_TELEMATICO,
        _TD_URGENTE,
        True,
        5,
    ),
    "TEST-FAULT-TELEM-T2": (
        7102,
        "Pérdida de energía del dispositivo",
        _TD_FUENTE_TELEMATICA,
        _TD_CONTROLADOR_TELEMATICO,
        _TD_URGENTE,
        True,
        6,
    ),
}

#: Las cuatro filas visibles: tres del vehículo más la de fuente NULL.
_TD_VISIBLES = {
    rid for rid, (_, _, fuente, *_) in _TD_FAULTS.items() if fuente != _TD_FUENTE_TELEMATICA
}
_TD_DIAGS_VISIBLES = {
    diag for _, (_, diag, fuente, *_) in _TD_FAULTS.items() if fuente != _TD_FUENTE_TELEMATICA
}
_TD_DIAGS_TELEMATICOS = {
    diag for _, (_, diag, fuente, *_) in _TD_FAULTS.items() if fuente == _TD_FUENTE_TELEMATICA
}


@pytest_asyncio.fixture(scope="module")
async def telematics_seed(analytics_seed: None):
    """Flota, base y vehículo propios con fallas de vehículo y de dispositivo.

    Aditivo respecto a `analytics_seed` —quien crea el DDL de `analytics.*`— y
    con identificadores exclusivos, igual que `window_total_seed`: ninguna otra
    aserción del módulo toca esta flota ni este `vehicle_id`, y todas las
    consultas de esta sección filtran por ambos, así que los conteos exactos no
    dependen de lo que otros seeds hayan dejado en las tablas.
    """
    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(code=_TD_FLEET_CODE, name="Flota Test Telemática", is_active=True)
            session.add(fleet)
            await session.flush()
            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=_TD_DB_KEY,
                database_key=_TD_DB_KEY,
                connection_type="geotab",
                is_active=True,
            )
            session.add(geotab_db)
            await session.flush()
            fleet_id = fleet.id
            session.add(
                Vehicle(
                    plate=_TD_PLATE,
                    geotab_device_id=_TD_DEVICE,
                    geotab_customer_status="found",
                    fleet_id=fleet.id,
                    geotab_database_id=geotab_db.id,
                    is_active=True,
                )
            )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_vehicle
                    (vehicle_id, device_id, vehicle_label, database_name, is_active)
                VALUES (:vid, :dev, :label, :db, true)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    vehicle_label = EXCLUDED.vehicle_label,
                    database_name = EXCLUDED.database_name,
                    is_active = EXCLUDED.is_active
                """),
                {
                    "vid": _TD_VEH,
                    "dev": _TD_DEVICE,
                    "label": f"Vehículo {_TD_PLATE}",
                    "db": _TD_DB_KEY,
                },
            )
            for row_id, (
                codigo,
                diagnostico,
                fuente,
                controlador,
                severidad,
                luz_roja,
                hora,
            ) in _TD_FAULTS.items():
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_fault_event
                        (row_id, vehicle_id, database_name, date_key, fecha, movil,
                         fecha_de_falla, codigo_diagnostico, codigo_modo_de_falla,
                         codigo_controlador, nombre_fuente_diagnostico, diagnostico,
                         nombre_de_controlador, modo_de_falla, estado_de_falla,
                         recuento_de_fallos, tipo_de_atencion,
                         luz_de_parada_roja, luz_de_parada_amber, lampara_de_averia)
                    VALUES (:rid, :vid, :db, 20260101, '2026-01-01', :placa,
                            :ts, :codigo, 3.0,
                            128, :fuente, :diag,
                            :controlador, 'Above range', 'Active',
                            1, :severidad,
                            :luz_roja, false, false)
                    ON CONFLICT (row_id) DO UPDATE SET
                        vehicle_id = EXCLUDED.vehicle_id,
                        codigo_diagnostico = EXCLUDED.codigo_diagnostico,
                        nombre_fuente_diagnostico = EXCLUDED.nombre_fuente_diagnostico,
                        diagnostico = EXCLUDED.diagnostico,
                        nombre_de_controlador = EXCLUDED.nombre_de_controlador,
                        tipo_de_atencion = EXCLUDED.tipo_de_atencion,
                        luz_de_parada_roja = EXCLUDED.luz_de_parada_roja,
                        fecha_de_falla = EXCLUDED.fecha_de_falla
                    """),
                    {
                        "rid": row_id,
                        "vid": _TD_VEH,
                        "db": _TD_DB_KEY,
                        "placa": _TD_PLATE,
                        "ts": datetime(2026, 1, 1, hora, 0, tzinfo=UTC),
                        "codigo": codigo,
                        "fuente": fuente,
                        "diag": diagnostico,
                        "controlador": controlador,
                        "severidad": severidad,
                        "luz_roja": luz_roja,
                    },
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield fleet_id

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM analytics.fact_fault_event WHERE row_id = ANY(:rids)"),
            {"rids": list(_TD_FAULTS)},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = :vid"),
            {"vid": _TD_VEH},
        )
        await session.execute(delete(Vehicle).where(Vehicle.geotab_device_id == _TD_DEVICE))
        await session.execute(delete(Fleet).where(Fleet.code == _TD_FLEET_CODE))
        await session.commit()


async def _td_call(fleet_id: uuid.UUID, nombre: str, **kw: object):
    """Llama una consulta de fallas del servicio acotada al seed.

    Se ataca el servicio y no el endpoint HTTP porque el invariante vive en
    `_apply_fault_filters`, compartido por las ocho consultas: varias de ellas
    (Pareto por fuente, ranking, severidad) no tienen una ruta propia donde
    afirmar conteos exactos, y pasar por HTTP añadiría RBAC y serialización sin
    aportar nada al invariante. Es además el patrón de `window_total_seed`.
    """
    from app.services import analytics_service

    async with AsyncSessionLocal() as session:
        fn = getattr(analytics_service, nombre)
        if nombre == "get_fault_timeline":
            return await fn(session, fleet_ids=[fleet_id], vehicle_id=_TD_VEH, **kw)
        return await fn(session, fleet_ids=[fleet_id], vehicle_id=[_TD_VEH], **kw)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_events_exclude_telematics_device(
    telematics_seed: uuid.UUID,
) -> None:
    """Con el flag, el listado no muestra fallas del equipo y sí la de fuente NULL."""
    items, _ = await _td_call(
        telematics_seed, "list_fault_events", limit=50, offset=0, exclude_telematics=True
    )

    fuentes = {i["nombre_fuente_diagnostico"] for i in items}
    controladores = {i["nombre_de_controlador"] for i in items}
    assert _TD_FUENTE_TELEMATICA not in fuentes
    assert _TD_CONTROLADOR_TELEMATICO not in controladores

    diagnosticos = {i["diagnostico"] for i in items}
    assert diagnosticos == _TD_DIAGS_VISIBLES
    assert not (diagnosticos & _TD_DIAGS_TELEMATICOS)
    # La fila sin fuente es la que distingue `is_distinct_from` de `!=`.
    assert None in fuentes


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_events_total_excludes_telematics_device(
    telematics_seed: uuid.UUID,
) -> None:
    """Con el flag, el total paginado cuenta solo lo visible: 4 de las 6 sembradas."""
    items, total = await _td_call(
        telematics_seed, "list_fault_events", limit=50, offset=0, exclude_telematics=True
    )

    assert total == len(_TD_VISIBLES) == 4
    assert len(items) == 4


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_summary_excludes_telematics_device(
    telematics_seed: uuid.UUID,
) -> None:
    """Con el flag, los KPIs se calculan sobre las cuatro fallas del vehículo.

    `n_urgentes` es el conteo revelador: las dos telemáticas se sembraron con
    luz de parada roja, así que sin el filtro serían 4 en vez de 2.
    """
    resumen = await _td_call(telematics_seed, "get_fault_summary", exclude_telematics=True)

    assert resumen["n_fallas"] == 4
    assert resumen["n_eventos"] == 4
    assert resumen["n_diagnosticos"] == 4
    assert resumen["n_vehiculos"] == 1
    assert resumen["n_urgentes"] == 2


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_pareto_excludes_telematics_device(
    telematics_seed: uuid.UUID,
) -> None:
    """Con el flag, ni la fuente ni el controlador del equipo llegan al Pareto."""
    por_fuente = await _td_call(
        telematics_seed, "get_fault_pareto", dimension="fuente", exclude_telematics=True
    )
    etiquetas_fuente = {p["label"]: p["n_fallas"] for p in por_fuente}
    assert _TD_FUENTE_TELEMATICA not in etiquetas_fuente
    assert etiquetas_fuente[_TD_FUENTE_VEHICULO] == 3
    # NULL se publica como "—"; el Pareto es donde se ve que la fila sobrevive.
    assert etiquetas_fuente["—"] == 1

    por_controlador = await _td_call(
        telematics_seed, "get_fault_pareto", dimension="controlador", exclude_telematics=True
    )
    etiquetas_ctrl = {p["label"]: p["n_fallas"] for p in por_controlador}
    assert _TD_CONTROLADOR_TELEMATICO not in etiquetas_ctrl
    assert etiquetas_ctrl[_TD_CONTROLADOR_VEHICULO] == 3
    assert etiquetas_ctrl[_TD_CONTROLADOR_DESCONOCIDO] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_timeline_excludes_telematics_device(
    telematics_seed: uuid.UUID,
) -> None:
    """Con el flag, la línea de tiempo del vehículo tampoco arrastra las del equipo.

    Hoy la línea de tiempo solo la consume Navifault, que no pide el corte; el
    caso se mantiene porque el parámetro existe en la firma y el invariante es
    de `_apply_fault_filters`, no de quién llame.
    """
    items, total = await _td_call(
        telematics_seed, "get_fault_timeline", limit=50, offset=0, exclude_telematics=True
    )

    assert total == 4
    assert {i["row_id"] for i in items} == _TD_VISIBLES


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_severity_and_ranking_exclude_telematics_device(
    telematics_seed: uuid.UUID,
) -> None:
    """Severidad y ranking con el flag: el invariante no es solo del listado.

    Las dos telemáticas son urgentes, así que sin el filtro la severidad daría
    4 urgentes y el ranking 6 fallas para un vehículo que solo tuvo 4.
    """
    severidad = await _td_call(telematics_seed, "get_fault_by_severity", exclude_telematics=True)
    conteos = {s["severity"]: s["n_fallas"] for s in severidad}
    assert conteos == {_TD_URGENTE: 2, _TD_PROGRAMABLE: 2}

    ranking = await _td_call(telematics_seed, "get_fault_ranking", exclude_telematics=True)
    assert len(ranking) == 1
    assert ranking[0]["vehicle_id"] == _TD_VEH
    assert ranking[0]["value"] == 4.0


# ---------------------------------------------------------------------------
# El DEFAULT no excluye nada: la garantía de Navifault
# ---------------------------------------------------------------------------
#
# Los tres casos siguientes son el reverso exacto de los anteriores, sobre el
# mismo seed y sin tocar el flag. No prueban que la exclusión funcione —eso ya
# está arriba—: prueban que NO se aplica sola. `e48dfe4` la había puesto
# incondicional y Navifault, que consume estos mismos endpoints y sí quiere lo
# que reporta el equipo, perdió el 15,4 % de su ventana de 24 h sin que nadie
# lo pidiera y sin que ninguna prueba lo notara.


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_events_incluyen_telematica_por_defecto(
    telematics_seed: uuid.UUID,
) -> None:
    """Sin el flag, el listado devuelve las 6 filas, telemáticas incluidas.

    Es lo que ve Navifault: el módulo trabaja sobre todo lo que llega, y un
    reinicio o una pérdida de energía del equipo es justamente un evento que
    le interesa.
    """
    items, total = await _td_call(telematics_seed, "list_fault_events", limit=50, offset=0)

    assert total == len(_TD_FAULTS) == 6
    assert len(items) == 6

    diagnosticos = {i["diagnostico"] for i in items}
    assert diagnosticos == _TD_DIAGS_VISIBLES | _TD_DIAGS_TELEMATICOS
    assert _TD_FUENTE_TELEMATICA in {i["nombre_fuente_diagnostico"] for i in items}
    assert _TD_CONTROLADOR_TELEMATICO in {i["nombre_de_controlador"] for i in items}
    # La fila sin fuente sobrevive en los dos modos: con el flag por
    # `is_distinct_from`, y aquí porque no hay corte que la pueda tocar.
    assert None in {i["nombre_fuente_diagnostico"] for i in items}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_summary_incluye_telematica_por_defecto(
    telematics_seed: uuid.UUID,
) -> None:
    """Sin el flag, los KPIs cuentan las seis fallas y los cuatro urgentes.

    `n_urgentes == 4` es el número que delata una exclusión que volviera a ser
    incondicional: las dos telemáticas se sembraron urgentes y con luz roja
    precisamente para que se note en un KPI y no solo en un listado.
    """
    resumen = await _td_call(telematics_seed, "get_fault_summary")

    assert resumen["n_fallas"] == 6
    assert resumen["n_eventos"] == 6
    assert resumen["n_diagnosticos"] == 6
    assert resumen["n_vehiculos"] == 1
    assert resumen["n_urgentes"] == 4


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fault_pareto_incluye_telematica_por_defecto(
    telematics_seed: uuid.UUID,
) -> None:
    """Sin el flag, `SourceGeotabGoId` es una fuente más del Pareto."""
    por_fuente = await _td_call(telematics_seed, "get_fault_pareto", dimension="fuente")
    etiquetas_fuente = {p["label"]: p["n_fallas"] for p in por_fuente}
    assert etiquetas_fuente[_TD_FUENTE_TELEMATICA] == 2
    assert etiquetas_fuente[_TD_FUENTE_VEHICULO] == 3
    # NULL se publica como "—" y aparece igual que en el modo con flag.
    assert etiquetas_fuente["—"] == 1

    por_controlador = await _td_call(telematics_seed, "get_fault_pareto", dimension="controlador")
    etiquetas_ctrl = {p["label"]: p["n_fallas"] for p in por_controlador}
    assert etiquetas_ctrl[_TD_CONTROLADOR_TELEMATICO] == 2
    assert etiquetas_ctrl[_TD_CONTROLADOR_VEHICULO] == 3
    assert etiquetas_ctrl[_TD_CONTROLADOR_DESCONOCIDO] == 1


# ---------------------------------------------------------------------------
# Calidad de distancia ECM-GPS: `use_ecm` y el estado `no_data`
# ---------------------------------------------------------------------------
#
# Dos invariantes distintos que comparten seed:
#
# 1. `use_ecm` estaba declarado en el `CheckConstraint` de
#    `distance_quality_decisions` pero no en `_effective_distance_expressions`:
#    caía al `else_` y devolvía `kms_effective`, que en una fila marcada es
#    NULL. El administrador resolvía y no pasaba nada. Se fija por donde el
#    dato se consume de verdad —detalle y resumen de combustible—, no sobre la
#    expresión SQL: es ahí donde el defecto era visible.
# 2. `no_data` es un estado terminal, no una tarea pendiente. Un día sin
#    distancia en NINGUNA de las dos fuentes no admite decisión humana, y antes
#    engrosaba el contador de pendientes. El ORDEN del `case` es lo delicado:
#    una decisión humana tiene que ganarle a `no_data`.
#
# Seed aditivo con flota, base, vehículo y fechas propias (2026-02-01..07), una
# fila de hecho por invariante, para que ningún test dependa del orden ni de lo
# que otro haya decidido. `analytics_seed` es quien crea el DDL de `analytics.*`.

_DQ_FLEET_CODE = f"TEST-DISTQ-{uuid.uuid4().hex[:8]}"
_DQ_DB_KEY = "distq_test_db"
_DQ_VEH = "TEST-VEH-DISTQ"
_DQ_DEVICE = "DEV-TEST-VEH-DISTQ"
_DQ_PLATE = "DQV001"

# Una fila por prueba: las decisiones son append-only y mutar una fila
# compartida haría que el orden de ejecución cambiara el resultado.
_DQ_FACT_ECM = "TEST-FACT-DISTQ-ECM"
_DQ_FACT_ESTADO = "TEST-FACT-DISTQ-ESTADO"
_DQ_FACT_SIN_ECM = "TEST-FACT-DISTQ-SIN-ECM"
_DQ_FACT_HUELLA = "TEST-FACT-DISTQ-HUELLA"
_DQ_FACT_SIN_DATO = "TEST-FACT-DISTQ-SIN-DATO"
_DQ_FACT_SIN_DATO_EXCLUIDO = "TEST-FACT-DISTQ-SIN-DATO-EXCL"
_DQ_FACT_PENDIENTE = "TEST-FACT-DISTQ-PENDIENTE"
_DQ_FACT_HTTP = "TEST-FACT-DISTQ-HTTP"

# fact_row_id -> (date_key, fecha, kms_ecm, kms_gps, hrs_ecm, hrs_gps, comb,
#                 gps_quality_valid, fingerprint)
#
# Todas nacen `warning` con `kms_effective` NULL: es la observación ambigua que
# el ETL deja sin resolver y que el administrador tiene que decidir. Las horas
# ECM y GPS difieren en cada fila para que la velocidad delate CUÁL de las dos
# publicó la decisión (300/10 = 30 con ECM contra 300/4 = 75 con GPS).
_DQ_FILAS: dict[str, tuple[int, str, float | None, float | None, float | None, float | None, float, bool, str]] = {
    _DQ_FACT_ECM: (20260201, "2026-02-01", 300.0, 120.0, 10.0, 4.0, 30.0, True, "d" * 64),
    _DQ_FACT_ESTADO: (20260202, "2026-02-02", 280.0, 100.0, 8.0, 3.0, 28.0, True, "1" * 64),
    _DQ_FACT_SIN_ECM: (20260203, "2026-02-03", None, 90.0, None, 3.0, 9.0, True, "2" * 64),
    _DQ_FACT_HUELLA: (20260204, "2026-02-04", 260.0, 110.0, 9.0, 4.0, 26.0, True, "3" * 64),
    # Sin dato por ceros y sin dato por NULL: `coalesce` tiene que tratar
    # ambas formas igual, y el corpus real trae las dos.
    _DQ_FACT_SIN_DATO: (20260205, "2026-02-05", 0.0, 0.0, 0.0, 0.0, 0.0, False, "4" * 64),
    _DQ_FACT_SIN_DATO_EXCLUIDO: (20260206, "2026-02-06", None, None, None, None, 0.0, False, "5" * 64),
    _DQ_FACT_PENDIENTE: (20260207, "2026-02-07", 240.0, 95.0, 8.0, 3.0, 24.0, True, "6" * 64),
    _DQ_FACT_HTTP: (20260208, "2026-02-08", 320.0, 130.0, 11.0, 5.0, 32.0, True, "7" * 64),
}


@pytest_asyncio.fixture(scope="module")
async def distance_quality_seed(analytics_seed: None):
    """Flota propia con siete días marcados como anomalía de distancia.

    Aditivo respecto a `analytics_seed`: no toca sus filas ni sus conteos.
    Todas las consultas de esta sección filtran por este vehículo o por esta
    flota, así que el resultado no depende de lo que otros seeds dejaron.
    """
    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(code=_DQ_FLEET_CODE, name="Flota Test Calidad Distancia", is_active=True)
            session.add(fleet)
            await session.flush()
            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=_DQ_DB_KEY,
                database_key=_DQ_DB_KEY,
                connection_type="geotab",
                is_active=True,
            )
            session.add(geotab_db)
            await session.flush()
            fleet_id = fleet.id
            session.add(
                Vehicle(
                    plate=_DQ_PLATE,
                    geotab_device_id=_DQ_DEVICE,
                    geotab_customer_status="found",
                    fleet_id=fleet.id,
                    geotab_database_id=geotab_db.id,
                    is_active=True,
                )
            )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_vehicle
                    (vehicle_id, device_id, vehicle_label, database_name, motor_type,
                     fuel_kind, fuel_unit, is_active)
                VALUES (:vid, :dev, :label, :db, 'F2.8', 'liquid', 'gal', true)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    vehicle_label = EXCLUDED.vehicle_label,
                    database_name = EXCLUDED.database_name,
                    motor_type = EXCLUDED.motor_type,
                    fuel_kind = EXCLUDED.fuel_kind,
                    fuel_unit = EXCLUDED.fuel_unit,
                    is_active = EXCLUDED.is_active
                """),
                {
                    "vid": _DQ_VEH,
                    "dev": _DQ_DEVICE,
                    "label": f"Vehículo {_DQ_PLATE}",
                    "db": _DQ_DB_KEY,
                },
            )
            # La tabla real define FK fact.*.date_key -> dim_date.date_key.
            for fact_row_id, fila in _DQ_FILAS.items():
                date_key, fecha = fila[0], fila[1]
                await session.execute(
                    text("""
                    INSERT INTO analytics.dim_date
                        (date_key, date, year, month, day, quarter, month_key, month_start_date)
                    VALUES (:dk, :fecha, 2026, 2, :dia, 1, 202602, '2026-02-01')
                    ON CONFLICT (date_key) DO NOTHING
                    """),
                    {"dk": date_key, "fecha": fecha, "dia": date_key % 100},
                )
                (
                    _dk,
                    _fecha,
                    kms_ecm,
                    kms_gps,
                    hrs_ecm,
                    hrs_gps,
                    comb,
                    gps_valido,
                    huella,
                ) = fila
                await session.execute(
                    text("""
                    INSERT INTO analytics.fact_combustible_daily
                        (fact_row_id, vehicle_id, database_name, motor_type, date_key, fecha,
                         placa, kms_ecm, kms_gps, kms_effective, hrs_ecm, hrs_gps, comb,
                         fuel_kind, fuel_unit, distance_source, distance_quality_status,
                         distance_quality_reason, gps_quality_valid,
                         distance_quality_fingerprint, distance_threshold_version, revision)
                    VALUES (:fid, :vid, :db, 'F2.8', :dk, :fecha,
                            :placa, :kms_ecm, :kms_gps, NULL, :hrs_ecm, :hrs_gps, :comb,
                            'liquid', 'gal', 'none', 'warning',
                            'ecm_gps_warning_mismatch', :gps_valido,
                            :huella, 'v1', false)
                    ON CONFLICT (fact_row_id) DO UPDATE SET
                        kms_ecm = EXCLUDED.kms_ecm,
                        kms_gps = EXCLUDED.kms_gps,
                        kms_effective = EXCLUDED.kms_effective,
                        hrs_ecm = EXCLUDED.hrs_ecm,
                        hrs_gps = EXCLUDED.hrs_gps,
                        comb = EXCLUDED.comb,
                        distance_source = EXCLUDED.distance_source,
                        distance_quality_status = EXCLUDED.distance_quality_status,
                        gps_quality_valid = EXCLUDED.gps_quality_valid,
                        distance_quality_fingerprint = EXCLUDED.distance_quality_fingerprint
                    """),
                    {
                        "fid": fact_row_id,
                        "vid": _DQ_VEH,
                        "db": _DQ_DB_KEY,
                        "dk": date_key,
                        "fecha": fecha,
                        "placa": _DQ_PLATE,
                        "kms_ecm": kms_ecm,
                        "kms_gps": kms_gps,
                        "hrs_ecm": hrs_ecm,
                        "hrs_gps": hrs_gps,
                        "comb": comb,
                        "gps_valido": gps_valido,
                        "huella": huella,
                    },
                )
            await session.execute(
                text("DELETE FROM distance_quality_decisions WHERE fact_row_id = ANY(:fids)"),
                {"fids": list(_DQ_FILAS)},
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield fleet_id

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM distance_quality_decisions WHERE fact_row_id = ANY(:fids)"),
            {"fids": list(_DQ_FILAS)},
        )
        await session.execute(
            text("DELETE FROM analytics.fact_combustible_daily WHERE fact_row_id = ANY(:fids)"),
            {"fids": list(_DQ_FILAS)},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = :vid"),
            {"vid": _DQ_VEH},
        )
        await session.execute(delete(Vehicle).where(Vehicle.geotab_device_id == _DQ_DEVICE))
        await session.execute(delete(Fleet).where(Fleet.code == _DQ_FLEET_CODE))
        await session.commit()


async def _dq_decidir(fleet_id: uuid.UUID, fact_row_id: str, action: str):
    """Registra una decisión manual llamando al servicio.

    Deliberadamente sin pasar por el router: aísla la regla del servicio del
    validador del schema, para que un 422 de Pydantic no pueda hacerse pasar
    por la guarda de `create_manual_decision`. El borde router→servicio se
    cubre aparte, por HTTP, en `test_endpoint_de_resolucion_acepta_use_ecm`.
    """
    from app.services import distance_quality_service

    huella = _DQ_FILAS[fact_row_id][8]
    async with AsyncSessionLocal() as session:
        actor = (
            await session.execute(select(User).where(User.email == settings.bootstrap_admin_email))
        ).scalar_one()
        return await distance_quality_service.create_manual_decision(
            session,
            fact_row_id=fact_row_id,
            action=action,
            justification="Decisión verificada para la prueba",
            expected_fingerprint=huella,
            actor=actor,
            fleet_ids=[fleet_id],
        )


async def _dq_anomalias(fleet_id: uuid.UUID, **kw: object) -> dict[str, dict]:
    """`list_distance_anomalies` de la flota del seed, indexada por fila."""
    from app.services import distance_quality_service

    async with AsyncSessionLocal() as session:
        items, _total = await distance_quality_service.list_distance_anomalies(
            session, fleet_ids=[fleet_id], limit=200, **kw
        )
    return {item["fact_row_id"]: item for item in items}


async def _dq_contar_decisiones(fact_row_id: str) -> int:
    async with AsyncSessionLocal() as session:
        return int(
            await session.scalar(
                text("SELECT count(*) FROM distance_quality_decisions WHERE fact_row_id = :fid"),
                {"fid": fact_row_id},
            )
        )


def _dq_detalle(client: TestClient, fact_row_id: str) -> dict:
    """Fila del detalle de combustible del día sembrado (contrato público)."""
    fecha = _DQ_FILAS[fact_row_id][1]
    resp = client.get(
        "/api/v1/reportes/combustible/daily",
        params={"vehicle_id": _DQ_VEH, "date_from": fecha, "date_to": fecha},
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1, items
    return items[0]


def _dq_resumen(client: TestClient, fact_row_id: str) -> dict:
    fecha = _DQ_FILAS[fact_row_id][1]
    resp = client.get(
        "/api/v1/reportes/combustible/summary",
        params={"vehicle_id": _DQ_VEH, "date_from": fecha, "date_to": fecha},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- A) `use_ecm` publica la distancia y las horas del ECM -----------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_use_ecm_publica_la_distancia_y_las_horas_del_ecm(
    admin_client: TestClient, distance_quality_seed: uuid.UUID
) -> None:
    """La acción tiene que mover el número que ve el usuario, no solo la tabla.

    Antes del arreglo `use_ecm` caía al `else_` y devolvía `kms_effective`, que
    en una fila marcada es NULL: el detalle seguía vacío después de resolver.
    La velocidad es lo que distingue qué horas se publicaron: 300/10 con ECM
    contra 300/4 si se hubieran tomado las de GPS.
    """
    antes = _dq_detalle(admin_client, _DQ_FACT_ECM)
    assert antes["kms_ecm"] is None
    assert antes["km_gal"] is None
    assert antes["velocidad_promedio"] is None

    await _dq_decidir(distance_quality_seed, _DQ_FACT_ECM, "use_ecm")

    despues = _dq_detalle(admin_client, _DQ_FACT_ECM)
    assert despues["kms_ecm"] == pytest.approx(300.0)
    assert despues["km_gal"] == pytest.approx(300.0 / 30.0)
    assert despues["velocidad_promedio"] == pytest.approx(30.0)
    # El contrato público sigue sin revelar fuente ni decisión.
    assert "distance_source" not in despues
    assert "resolution_action" not in despues

    resumen = _dq_resumen(admin_client, _DQ_FACT_ECM)
    assert resumen["kms_ecm"] == pytest.approx(300.0)
    assert resumen["km_gal"] == pytest.approx(300.0 / 30.0)
    assert resumen["velocidad_promedio"] == pytest.approx(30.0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_use_ecm_saca_la_fila_de_pendientes(
    distance_quality_seed: uuid.UUID,
) -> None:
    """El síntoma que reportó el defecto: la fila resuelta seguía pendiente."""
    antes = await _dq_anomalias(distance_quality_seed)
    assert antes[_DQ_FACT_ESTADO]["review_status"] == "pending"
    assert antes[_DQ_FACT_ESTADO]["resolution_action"] is None

    await _dq_decidir(distance_quality_seed, _DQ_FACT_ESTADO, "use_ecm")

    despues = await _dq_anomalias(distance_quality_seed)
    fila = despues[_DQ_FACT_ESTADO]
    assert fila["review_status"] == "resolved"
    assert fila["resolution_action"] == "use_ecm"
    assert fila["distance_source"] == "ecm_manual"
    assert fila["distance_quality_status"] == "resolved"
    assert fila["kms_effective"] == pytest.approx(280.0)

    # Y deja de contarse al pedir explícitamente lo pendiente.
    pendientes = await _dq_anomalias(distance_quality_seed, review_status="pending")
    assert _DQ_FACT_ESTADO not in pendientes


@pytest.mark.asyncio
@pytest.mark.integration
async def test_use_ecm_sin_kms_ecm_es_422_y_no_deja_decision(
    distance_quality_seed: uuid.UUID,
) -> None:
    """Guarda simétrica a la de GPS: no se publica una distancia que no existe.

    Sin ella la fila quedaría marcada como `resolved` publicando NULL, que es
    exactamente el estado que el arreglo vino a eliminar. Y el rechazo tiene
    que ser total: una decisión escrita antes de fallar dejaría la fila
    resuelta a medias, porque la tabla es append-only y la última gana.
    """
    with pytest.raises(HTTPException) as exc:
        await _dq_decidir(distance_quality_seed, _DQ_FACT_SIN_ECM, "use_ecm")
    assert exc.value.status_code == 422

    assert await _dq_contar_decisiones(_DQ_FACT_SIN_ECM) == 0
    anomalias = await _dq_anomalias(distance_quality_seed)
    assert anomalias[_DQ_FACT_SIN_ECM]["review_status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_use_ecm_deja_de_aplicar_si_cambia_la_huella(
    admin_client: TestClient, distance_quality_seed: uuid.UUID
) -> None:
    """La decisión vale para la observación que el humano vio, no para otra.

    Mismo invariante que ya tenía `use_gps`: si el ETL recalcula el día, la
    huella cambia y la decisión anterior deja de corresponder. Aplicarla igual
    sería publicar el juicio de ayer sobre los datos de hoy.
    """
    await _dq_decidir(distance_quality_seed, _DQ_FACT_HUELLA, "use_ecm")
    aplicada = _dq_detalle(admin_client, _DQ_FACT_HUELLA)
    assert aplicada["kms_ecm"] == pytest.approx(260.0)

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE analytics.fact_combustible_daily "
                "SET distance_quality_fingerprint = :huella WHERE fact_row_id = :fid"
            ),
            {"huella": "9" * 64, "fid": _DQ_FACT_HUELLA},
        )
        await session.commit()

    caducada = _dq_detalle(admin_client, _DQ_FACT_HUELLA)
    assert caducada["kms_ecm"] is None
    assert caducada["velocidad_promedio"] is None

    # La decisión sigue en la auditoría; solo dejó de aplicarse.
    assert await _dq_contar_decisiones(_DQ_FACT_HUELLA) == 1
    anomalias = await _dq_anomalias(distance_quality_seed)
    assert anomalias[_DQ_FACT_HUELLA]["review_status"] == "pending"
    assert anomalias[_DQ_FACT_HUELLA]["resolution_action"] is None


# --- B) `no_data` es un estado terminal, no una tarea ----------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_dia_sin_distancia_en_ninguna_fuente_es_no_data(
    distance_quality_seed: uuid.UUID,
) -> None:
    """No hay decisión humana posible: no es elegir entre ECM y GPS.

    El día se sembró con las dos fuentes en 0. Antes caía en `pending` e
    inflaba el contador de la tarjeta de Calidad de datos con trabajo que no
    existe (1.619 filas del hecho).
    """
    anomalias = await _dq_anomalias(distance_quality_seed)
    assert anomalias[_DQ_FACT_SIN_DATO]["review_status"] == "no_data"

    pendientes = await _dq_anomalias(distance_quality_seed, review_status="pending")
    assert _DQ_FACT_SIN_DATO not in pendientes


@pytest.mark.asyncio
@pytest.mark.integration
async def test_una_decision_humana_le_gana_a_no_data(
    distance_quality_seed: uuid.UUID,
) -> None:
    """Fija el ORDEN del `case`, que es lo único delicado del cambio.

    Este día tampoco tiene distancia (ambas fuentes NULL), pero alguien lo
    excluyó explícitamente. Si `no_data` se evaluara primero, ese registro
    desaparecería y la exclusión dejaría de constar.
    """
    sin_decision = await _dq_anomalias(distance_quality_seed)
    assert sin_decision[_DQ_FACT_SIN_DATO_EXCLUIDO]["review_status"] == "no_data"

    await _dq_decidir(distance_quality_seed, _DQ_FACT_SIN_DATO_EXCLUIDO, "exclude")

    decidido = await _dq_anomalias(distance_quality_seed)
    fila = decidido[_DQ_FACT_SIN_DATO_EXCLUIDO]
    assert fila["review_status"] == "excluded"
    assert fila["resolution_action"] == "exclude"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_dia_con_dato_en_alguna_fuente_sigue_pendiente(
    distance_quality_seed: uuid.UUID,
) -> None:
    """La regla nueva no se puede tragar el trabajo real.

    Este día tiene 240 km de ECM contra 95 de GPS: es exactamente la anomalía
    que alguien debe resolver, y tiene que seguir apareciendo en `pending`.
    """
    anomalias = await _dq_anomalias(distance_quality_seed)
    assert anomalias[_DQ_FACT_PENDIENTE]["review_status"] == "pending"

    pendientes = await _dq_anomalias(distance_quality_seed, review_status="pending")
    assert _DQ_FACT_PENDIENTE in pendientes


# --- C) El borde router -> servicio ----------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_endpoint_de_resolucion_acepta_use_ecm(
    admin_client: TestClient, distance_quality_seed: uuid.UUID
) -> None:
    """El defecto vivía en el borde: el schema de la PETICIÓN no la declaraba.

    `DistanceResolutionCreate.action` era `Literal["use_gps", "exclude",
    "restore_auto"]`, así que el 422 salía del validador de Pydantic antes de
    llegar al servicio. Ninguna prueba que llame a `create_manual_decision`
    directamente puede ver eso: hay que atravesar el router.
    """
    huella = _DQ_FILAS[_DQ_FACT_HTTP][8]
    resp = admin_client.post(
        f"/api/v1/reportes/combustible/{_DQ_FACT_HTTP}/distance-resolution",
        json={
            "action": "use_ecm",
            "reason": "El ECM es coherente y el GPS perdió viajes",
            "expected_fingerprint": huella,
        },
    )
    # El 422 sería el síntoma exacto del defecto: la petición rechazada por el
    # contrato, no por una regla de negocio.
    assert resp.status_code == 200, resp.text
    cuerpo = resp.json()
    assert cuerpo["status"] == "applied"
    assert cuerpo["decision_id"]

    assert await _dq_contar_decisiones(_DQ_FACT_HTTP) == 1
    async with AsyncSessionLocal() as session:
        accion = await session.scalar(
            text("SELECT action FROM distance_quality_decisions WHERE fact_row_id = :fid"),
            {"fid": _DQ_FACT_HTTP},
        )
    assert accion == "use_ecm"

    detalle = _dq_detalle(admin_client, _DQ_FACT_HTTP)
    assert detalle["kms_ecm"] == pytest.approx(320.0)
    assert detalle["velocidad_promedio"] == pytest.approx(320.0 / 11.0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_endpoint_propaga_la_guarda_de_use_ecm_sin_kms_ecm(
    admin_client: TestClient, distance_quality_seed: uuid.UUID
) -> None:
    """El 422 que queda tiene que ser el del servicio, no el del schema.

    Con `use_ecm` ya declarado en el Literal, la petición llega al servicio y
    es su guarda la que rechaza. Se distingue por el cuerpo: Pydantic responde
    una lista de errores en `detail`, el servicio un mensaje escrito para la
    persona que decidió.
    """
    huella = _DQ_FILAS[_DQ_FACT_SIN_ECM][8]
    resp = admin_client.post(
        f"/api/v1/reportes/combustible/{_DQ_FACT_SIN_ECM}/distance-resolution",
        json={
            "action": "use_ecm",
            "reason": "Intento de publicar un ECM que no existe",
            "expected_fingerprint": huella,
        },
    )
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)

    assert await _dq_contar_decisiones(_DQ_FACT_SIN_ECM) == 0


# ---------------------------------------------------------------------------
# `all_vocacional` con claves de base Geotab repetidas
#
# `geotab_databases` repite la misma `database_key` en una fila por flota (13
# filas `navitrans` en producción el 2026-09-01). El join por clave que usaba
# el resumen multiplicaba cada vehículo por esas filas y las que no traían su
# `Vehicle` caían a False, así que una flota 100 % vocacional (Cementos San
# Marcos) seguía viéndose como comercial.
# ---------------------------------------------------------------------------

_AV_FLEET_CODE = "TEST-ALLVOC"
_AV_OTHER_FLEET_CODE = "TEST-ALLVOC-OTRA"
_AV_DB_KEY = "test_allvoc_db"
_AV_DEVICE = "TEST-DEVICE-ALLVOC"
_AV_VEH = "TEST-VEH-ALLVOC"
_AV_PLATE = "AVC001"
_AV_FACT_ID = str(uuid.uuid4())


@pytest_asyncio.fixture(scope="module")
async def all_vocacional_seed(analytics_seed: None):
    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(code=_AV_FLEET_CODE, name="Flota Test All Vocacional", is_active=True)
            other = Fleet(code=_AV_OTHER_FLEET_CODE, name="Otra flota misma base", is_active=True)
            session.add_all([fleet, other])
            await session.flush()
            # Dos filas con la MISMA clave: la del vehículo y otra de otra flota.
            own_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=_AV_DB_KEY,
                database_key=_AV_DB_KEY,
                connection_type="geotab",
                is_active=True,
            )
            other_db = GeotabDatabase(
                fleet_id=other.id,
                database_name=_AV_DB_KEY,
                database_key=_AV_DB_KEY,
                connection_type="geotab",
                is_active=True,
            )
            session.add_all([own_db, other_db])
            await session.flush()
            session.add(
                Vehicle(
                    plate=_AV_PLATE,
                    geotab_device_id=_AV_DEVICE,
                    geotab_customer_status="found",
                    fleet_id=fleet.id,
                    geotab_database_id=own_db.id,
                    is_active=True,
                    vocacional=True,
                )
            )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_vehicle
                    (vehicle_id, device_id, vehicle_label, database_name, is_active)
                VALUES (:vid, :dev, :label, :db, true)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    database_name = EXCLUDED.database_name,
                    is_active = EXCLUDED.is_active
                """),
                {"vid": _AV_VEH, "dev": _AV_DEVICE, "label": _AV_PLATE, "db": _AV_DB_KEY},
            )
            await session.execute(
                text("""
                INSERT INTO analytics.fact_combustible_daily
                    (fact_row_id, vehicle_id, database_name, date_key, fecha, placa,
                     kms_ecm, kms_effective, hrs_ecm, comb, fuel_kind, fuel_unit,
                     distance_source, distance_quality_status, gps_quality_valid, revision)
                VALUES (:fid, :vid, :db, 20260102, '2026-01-02', :placa,
                        40.0, 40.0, 8.0, 20.0, 'liquid', 'gal', 'ecm', 'ok', true, false)
                ON CONFLICT (fact_row_id) DO NOTHING
                """),
                {"fid": _AV_FACT_ID, "vid": _AV_VEH, "db": _AV_DB_KEY, "placa": _AV_PLATE},
            )
            await session.commit()
            fleet_id = fleet.id
        except Exception:
            await session.rollback()
            raise
    yield fleet_id
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM analytics.fact_combustible_daily WHERE fact_row_id = :fid"),
            {"fid": _AV_FACT_ID},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = :vid"), {"vid": _AV_VEH}
        )
        await session.execute(delete(Vehicle).where(Vehicle.geotab_device_id == _AV_DEVICE))
        await session.execute(
            delete(GeotabDatabase).where(GeotabDatabase.database_key == _AV_DB_KEY)
        )
        await session.execute(
            delete(Fleet).where(Fleet.code.in_([_AV_FLEET_CODE, _AV_OTHER_FLEET_CODE]))
        )
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_all_vocacional_survives_duplicated_database_keys(
    all_vocacional_seed: uuid.UUID,
) -> None:
    from app.services import analytics_service

    async with AsyncSessionLocal() as session:
        summary = await analytics_service.get_combustible_summary(
            session,
            fleet_ids=[all_vocacional_seed],
            date_from=date(2026, 1, 2),
            date_to=date(2026, 1, 2),
        )
    assert summary["n_vehiculos"] == 1
    assert summary["all_vocacional"] is True
    assert summary["gal_hr"] == pytest.approx(2.5)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_all_vocacional_is_false_when_one_vehicle_is_comercial(
    all_vocacional_seed: uuid.UUID, analytics_seed: None
) -> None:
    """Sin alcance de flota entra también el vehículo comercial del seed general."""
    from app.services import analytics_service

    async with AsyncSessionLocal() as session:
        summary = await analytics_service.get_combustible_summary(
            session,
            fleet_ids=None,
            vehicle_id=[_AV_VEH, _SEED_VEHICLE_ID],
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 2),
        )
    assert summary["n_vehiculos"] == 2
    assert summary["all_vocacional"] is False
