from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.cloudfleet import CloudfleetVehicle
from app.models.etl_trigger import EtlTriggerRequest
from app.models.fleet import Fleet
from app.models.master_data import (
    RPM_RANGE_BANDS,
    GeotabCredential,
    GeotabDatabase,
    GeotabRule,
    GeotabRuleApplication,
    MotorCatalog,
    MotorRpmBand,
    SyncState,
    Vehicle,
    VehicleExtractionState,
)
from app.models.sync_run import SyncRun
from app.services.data_quality_service import get_summary
from app.services.master_data_service import EXTRACTION_DATASETS


def test_empty_scope_is_not_reported_as_perfect_health() -> None:
    db = AsyncMock()
    summary = asyncio.run(get_summary(db, fleet_ids=[], stale_after_hours=48))
    assert summary.active_vehicles == 0
    assert summary.health_score == 0.0
    assert all(metric.count == 0 for metric in summary.metrics)
    db.execute.assert_not_awaited()


def _admin_client() -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert response.status_code == 200, response.text
    return client


def _create_fleet(client: TestClient, prefix: str) -> str:
    response = client.post(
        "/api/v1/fleets",
        json={
            "code": f"{prefix}-{uuid.uuid4().hex[:8]}",
            "name": f"Flota {prefix}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _delete_quality_fixtures() -> None:
    """Elimina las flotas y vehículos sintéticos creados por este módulo.

    Este módulo se ejecuta contra una base de pruebas cuando se usa la
    configuración normal de pytest. La limpieza explícita también evita dejar
    basura si una prueba se ejecuta de forma aislada.
    """

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            fleet_ids = list(
                (await session.scalars(select(Fleet.id).where(Fleet.code.like("QUALITY-%")))).all()
            )
            if not fleet_ids:
                return

            vehicle_rows = list(
                (
                    await session.execute(
                        select(Vehicle.id, Vehicle.plate).where(Vehicle.fleet_id.in_(fleet_ids))
                    )
                ).all()
            )
            vehicle_ids = [row.id for row in vehicle_rows]
            vehicle_codes = [row.plate for row in vehicle_rows]
            if vehicle_codes:
                await session.execute(
                    delete(CloudfleetVehicle).where(CloudfleetVehicle.code.in_(vehicle_codes))
                )
            if vehicle_ids:
                await session.execute(delete(Vehicle).where(Vehicle.id.in_(vehicle_ids)))
            await session.execute(delete(Fleet).where(Fleet.id.in_(fleet_ids)))
            await session.commit()

    asyncio.run(_run())


@pytest.fixture(autouse=True)
def clean_quality_fixtures() -> None:
    _delete_quality_fixtures()
    yield
    _delete_quality_fixtures()


def _seed_quality_scenario(
    fleet_a: str, fleet_b: str, *, include_unresolved_device: bool = False
) -> None:
    async def _seed() -> None:
        now = datetime.now(UTC)
        motor_type = f"MOTOR-{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as db:
            db.add(MotorCatalog(motor_type=motor_type, description="Test"))
            database = GeotabDatabase(
                fleet_id=uuid.UUID(fleet_a),
                database_name=f"db-{uuid.uuid4().hex[:8]}",
                database_key=f"key-{uuid.uuid4().hex[:8]}",
                is_active=True,
                synced_at=now,
            )
            db.add(database)
            await db.flush()
            db.add(
                GeotabCredential(
                    geotab_database_id=database.id,
                    username="quality-test",
                    password_enc=b"encrypted-test-value",
                    is_active=True,
                    synced_at=now,
                )
            )
            # Reglas: solo la de operación activa SIN banda cuenta como hallazgo.
            rule_with_band = GeotabRule(
                geotab_database_id=database.id,
                rule_id=f"R-band-{uuid.uuid4().hex[:6]}",
                name="Rango Bajo",
                is_active=True,
            )
            rule_without_band = GeotabRule(
                geotab_database_id=database.id,
                rule_id=f"R-noband-{uuid.uuid4().hex[:6]}",
                name="Regla renombrada por el cliente",
                is_active=True,
            )
            rule_habito = GeotabRule(
                geotab_database_id=database.id,
                rule_id=f"R-habito-{uuid.uuid4().hex[:6]}",
                name="Frenada brusca",
                is_active=True,
            )
            db.add_all([rule_with_band, rule_without_band, rule_habito])
            await db.flush()
            db.add_all(
                [
                    GeotabRuleApplication(
                        geotab_rule_id=rule_with_band.id,
                        category="operacion",
                        motor_type=motor_type,
                        band="rango_bajo",
                        is_active=True,
                    ),
                    GeotabRuleApplication(
                        geotab_rule_id=rule_without_band.id,
                        category="operacion",
                        motor_type=motor_type,
                        band=None,
                        is_active=True,
                    ),
                    # habito_seguro no tiene banda: no es un hallazgo.
                    GeotabRuleApplication(
                        geotab_rule_id=rule_habito.id,
                        category="habito_seguro",
                        motor_type=None,
                        band=None,
                        is_active=True,
                    ),
                ]
            )

            healthy = Vehicle(
                fleet_id=uuid.UUID(fleet_a),
                geotab_database_id=database.id,
                geotab_device_id=f"device-{uuid.uuid4().hex[:8]}",
                geotab_customer_status="found",
                motor_type=motor_type,
                plate=f"QA{uuid.uuid4().hex[:6].upper()}",
                synced_at=now,
                is_active=True,
            )
            issue = Vehicle(
                fleet_id=uuid.UUID(fleet_a),
                geotab_customer_status="unknown",
                plate=f"QB{uuid.uuid4().hex[:6].upper()}",
                synced_at=now - timedelta(days=10),
                is_active=True,
            )
            unresolved = Vehicle(
                fleet_id=uuid.UUID(fleet_a),
                geotab_database_id=database.id,
                geotab_device_id=f"device-{uuid.uuid4().hex[:8]}",
                geotab_customer_status="unknown",
                motor_type=motor_type,
                plate=f"QD{uuid.uuid4().hex[:6].upper()}",
                synced_at=now,
                is_active=True,
            )
            not_found = Vehicle(
                fleet_id=uuid.UUID(fleet_a),
                geotab_database_id=database.id,
                geotab_customer_status="not_found",
                motor_type=motor_type,
                plate=f"QE{uuid.uuid4().hex[:6].upper()}",
                synced_at=now,
                is_active=True,
            )
            not_applicable = Vehicle(
                fleet_id=uuid.UUID(fleet_a),
                geotab_database_id=database.id,
                geotab_customer_status="not_applicable",
                motor_type=motor_type,
                plate=f"QF{uuid.uuid4().hex[:6].upper()}",
                synced_at=now,
                is_active=True,
            )
            legacy_connection_status = Vehicle(
                fleet_id=uuid.UUID(fleet_a),
                geotab_database_id=database.id,
                geotab_device_id=f"device-{uuid.uuid4().hex[:8]}",
                geotab_customer_status="disconnected",
                motor_type=motor_type,
                plate=f"QG{uuid.uuid4().hex[:6].upper()}",
                synced_at=now,
                is_active=True,
            )
            other_fleet_issue = Vehicle(
                fleet_id=uuid.UUID(fleet_b),
                geotab_customer_status="unknown",
                plate=f"QC{uuid.uuid4().hex[:6].upper()}",
                synced_at=None,
                is_active=True,
            )
            db.add_all(
                [healthy, issue, other_fleet_issue]
                + (
                    [unresolved, not_found, not_applicable, legacy_connection_status]
                    if include_unresolved_device
                    else []
                )
            )
            await db.flush()
            db.add(
                CloudfleetVehicle(
                    code=healthy.plate,
                    is_in_master=True,
                    synced_at=now,
                )
            )
            sync_state = await db.get(SyncState, "cloudfleet_vehicles")
            if sync_state is None:
                db.add(SyncState(key="cloudfleet_vehicles", watermark=now))
            else:
                sync_state.watermark = now
            db.add_all(
                [
                    VehicleExtractionState(
                        vehicle_id=healthy.id,
                        dataset=dataset,
                        status="ok",
                        last_run_at=now,
                    )
                    for dataset in EXTRACTION_DATASETS
                ]
            )
            db.add(
                VehicleExtractionState(
                    vehicle_id=issue.id,
                    dataset="analisis_combustible",
                    status="error",
                    last_run_at=now,
                    last_error="synthetic failure",
                )
            )
            await db.commit()

    asyncio.run(_seed())


def test_summary_is_scoped_and_reports_actionable_issues() -> None:
    client = _admin_client()
    fleet_a = _create_fleet(client, "QUALITY-A")
    fleet_b = _create_fleet(client, "QUALITY-B")
    _seed_quality_scenario(fleet_a, fleet_b)

    response = client.get(
        "/api/v1/data-quality/summary",
        headers={"X-Fleet-Id": fleet_a},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fleet_count"] == 1
    assert body["active_vehicles"] == 2
    assert body["healthy_vehicles"] == 1
    assert body["health_score"] == 50.0
    assert body["geotab_databases"] == 1
    assert body["latest_master_sync_at"] is not None
    metrics = {metric["key"]: metric["count"] for metric in body["metrics"]}
    assert metrics == {
        "missing_device": 1,
        "device_status_unresolved": 0,
        "device_not_found": 0,
        "missing_database": 1,
        "missing_motor": 1,
        "stale_vehicle": 1,
        "failed_extraction": 1,
        "incomplete_extraction_state": 1,
        "missing_cloudfleet": 1,
        "cloudfleet_meter_errors": 0,
        "stale_cloudfleet": 0,
        "without_credentials": 0,
        "stale_database": 0,
        "rules_without_band": 1,
        "missing_rpm_bands": 12,
        "missing_rpm_thresholds": 0,
        "distance_ecm_gps_anomalies": 0,
    }
    metric_details = {metric["key"]: metric for metric in body["metrics"]}
    assert metric_details["failed_extraction"]["examples"][0]["label"].startswith("QB")
    assert metric_details["failed_extraction"]["examples"][0]["detail"] == (
        "0 dataset(s) OK; falló: analisis_combustible (error: motivo no registrado; reintentar)"
    )
    assert "synthetic failure" not in response.text
    rpm_examples = metric_details["missing_rpm_bands"]["examples"]
    assert len(rpm_examples) == 12
    assert rpm_examples[0]["detail"] == "Ascenso: Rango económico"
    assert any(example["detail"] == "Descenso: Ralentí" for example in rpm_examples) is False
    assert any(example["detail"].startswith("Descenso:") for example in rpm_examples)


def test_summary_reports_motors_without_rpm_thresholds() -> None:
    """Flota en modo 'rpm': la cobertura de reglas no aplica; los rangos sí.

    Sin los cortes del motor el ETL no puede calcular las bandas de esos
    vehículos, y ese vacío tiene que ser visible en vez de silencioso.
    """
    client = _admin_client()
    fleet_a = _create_fleet(client, "QUALITY-RPM")
    fleet_b = _create_fleet(client, "QUALITY-RPM-OTHER")
    _seed_quality_scenario(fleet_a, fleet_b)

    async def _switch_to_rpm() -> None:
        async with AsyncSessionLocal() as db:
            fleet = await db.get(Fleet, uuid.UUID(fleet_a))
            fleet.range_mode = "rpm"
            await db.commit()

    asyncio.run(_switch_to_rpm())

    body = client.get(
        "/api/v1/data-quality/summary",
        headers={"X-Fleet-Id": fleet_a},
    ).json()
    metrics = {metric["key"]: metric for metric in body["metrics"]}

    # 2 vehículos activos en la flota: uno con motor sin rangos y otro sin motor.
    assert metrics["missing_rpm_thresholds"]["count"] == 2
    details = {
        example["detail"] for example in metrics["missing_rpm_thresholds"]["examples"]
    }
    assert details == {"1 vehículo sin rangos de RPM"}
    # Las métricas de reglas quedan fuera: esta flota no arma bandas con reglas.
    assert metrics["missing_rpm_bands"]["count"] == 0
    assert metrics["rules_without_band"]["count"] == 0

    async def _configure_bands() -> None:
        async with AsyncSessionLocal() as db:
            motor_type = (
                await db.scalars(
                    select(Vehicle.motor_type)
                    .where(Vehicle.fleet_id == uuid.UUID(fleet_a))
                    .where(Vehicle.motor_type.is_not(None))
                )
            ).first()
            edges = [600, 1100, 1450, 1800, 2300, 2750, None]
            db.add_all(
                [
                    MotorRpmBand(
                        motor_type=motor_type,
                        band=band,
                        rpm_min=edges[index],
                        rpm_max=edges[index + 1],
                    )
                    for index, band in enumerate(RPM_RANGE_BANDS)
                ]
            )
            await db.commit()

    asyncio.run(_configure_bands())

    body = client.get(
        "/api/v1/data-quality/summary",
        headers={"X-Fleet-Id": fleet_a},
    ).json()
    metrics = {metric["key"]: metric for metric in body["metrics"]}
    # Queda solo el vehículo sin motor: sin motor no hay rangos posibles.
    assert metrics["missing_rpm_thresholds"]["count"] == 1


def test_summary_requires_session() -> None:
    response = TestClient(app).get("/api/v1/data-quality/summary")
    assert response.status_code == 401


def test_summary_separates_present_device_with_unconfirmed_status() -> None:
    client = _admin_client()
    fleet_a = _create_fleet(client, "QUALITY-DEVICE")
    fleet_b = _create_fleet(client, "QUALITY-DEVICE-OTHER")
    _seed_quality_scenario(fleet_a, fleet_b, include_unresolved_device=True)

    body = client.get(
        "/api/v1/data-quality/summary",
        headers={"X-Fleet-Id": fleet_a},
    ).json()
    metrics = {metric["key"]: metric for metric in body["metrics"]}

    assert body["active_vehicles"] == 6
    assert body["healthy_vehicles"] == 3
    assert metrics["missing_device"]["count"] == 1
    assert metrics["device_status_unresolved"]["count"] == 1
    assert metrics["device_not_found"]["count"] == 1
    assert metrics["device_not_found"]["examples"][0]["detail"].startswith(
        "Estado confirmado: not_found"
    )
    assert metrics["device_status_unresolved"]["examples"][0]["detail"] == (
        "geotab_device_id presente, pero estado recibido: unknown"
    )


def test_summary_requires_data_quality_permission() -> None:
    admin = _admin_client()
    role_code = f"no_quality_{uuid.uuid4().hex[:8]}"
    role_response = admin.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": "Sin calidad de datos",
            "permission_codes": [],
        },
    )
    assert role_response.status_code == 201, role_response.text

    email = f"no-quality-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "NoQuality123!"
    user_response = admin.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Sin Calidad",
            "role_codes": [role_code],
            "fleet_ids": [],
        },
    )
    assert user_response.status_code == 201, user_response.text

    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    response = client.get("/api/v1/data-quality/summary")
    assert response.status_code == 403


def _delete_trigger_requests() -> None:
    """Deja la cola del ETL vacía: `active` es estado global, no por flota."""

    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(EtlTriggerRequest))
            await session.commit()

    asyncio.run(_run())


def _insert_trigger_request(
    *, status: str, created_at: datetime, started_at: datetime | None
) -> None:
    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            session.add(
                EtlTriggerRequest(
                    status=status,
                    created_at=created_at,
                    updated_at=created_at,
                    started_at=started_at,
                    requested_by_email="etl@portalclientes.local",
                )
            )
            await session.commit()

    asyncio.run(_run())


def test_sync_runs_active_is_empty_without_pending_work() -> None:
    _delete_trigger_requests()
    _delete_sync_runs()
    response = _admin_client().get("/api/v1/data-quality/sync-runs")
    assert response.status_code == 200, response.text
    assert response.json()["active"] == []


def test_sync_runs_reports_running_etl_with_elapsed_time() -> None:
    """El ETL externo conserva su estado en la cola hasta cerrar."""
    _delete_trigger_requests()
    started = datetime.now(UTC) - timedelta(minutes=3)
    _insert_trigger_request(status="running", created_at=started, started_at=started)

    body = _admin_client().get("/api/v1/data-quality/sync-runs").json()
    _delete_trigger_requests()

    assert len(body["active"]) == 1
    active = body["active"][0]
    assert active["kind"] == "reportes"
    assert active["status"] == "running"
    assert active["actor_email"] == "etl@portalclientes.local"
    # Se cuenta desde started_at; tolerancia amplia para no depender del reloj.
    assert 170_000 <= active["elapsed_ms"] <= 200_000


def test_sync_runs_pending_counts_from_queued_at() -> None:
    """`pending` todavía no fue reclamada por el worker: el tiempo que le
    importa al usuario es el que lleva esperando en la cola."""
    _delete_trigger_requests()
    queued = datetime.now(UTC) - timedelta(seconds=45)
    _insert_trigger_request(status="pending", created_at=queued, started_at=None)

    body = _admin_client().get("/api/v1/data-quality/sync-runs").json()
    _delete_trigger_requests()

    assert len(body["active"]) == 1
    active = body["active"][0]
    assert active["status"] == "pending"
    assert active["started_at"] is None
    assert 40_000 <= active["elapsed_ms"] <= 60_000


def test_sync_runs_active_is_filtered_by_kind() -> None:
    """Filtrar por otro tipo no debe mostrar el ETL de reportes en vuelo."""
    _delete_trigger_requests()
    now = datetime.now(UTC)
    _insert_trigger_request(status="running", created_at=now, started_at=now)

    client = _admin_client()
    reportes = client.get("/api/v1/data-quality/sync-runs", params={"kind": "reportes"}).json()
    cloudfleet = client.get("/api/v1/data-quality/sync-runs", params={"kind": "cloudfleet"}).json()
    _delete_trigger_requests()

    assert len(reportes["active"]) == 1
    assert cloudfleet["active"] == []


def test_sync_runs_reports_running_cloudfleet_from_sync_run() -> None:
    """Worker y trigger manual publican CloudFleet en `sync_run` al comenzar."""
    _delete_trigger_requests()
    _delete_sync_runs()
    started = datetime.now(UTC) - timedelta(seconds=75)

    async def _insert() -> None:
        async with AsyncSessionLocal() as session:
            session.add(
                SyncRun(
                    kind="cloudfleet",
                    trigger="worker",
                    mode="incremental",
                    status="running",
                    started_at=started,
                    finished_at=None,
                    duration_ms=None,
                )
            )
            await session.commit()

    asyncio.run(_insert())
    client = _admin_client()
    body = client.get("/api/v1/data-quality/sync-runs", params={"kind": "cloudfleet"}).json()
    _delete_sync_runs()

    assert body["items"] == []
    assert body["total"] == 0
    assert len(body["active"]) == 1
    active = body["active"][0]
    assert active["kind"] == "cloudfleet"
    assert active["status"] == "running"
    assert 70_000 <= active["elapsed_ms"] <= 90_000


def _insert_sync_runs(count: int, *, kind: str = "cloudfleet") -> None:
    """`count` corridas cerradas, cada una un minuto más vieja que la anterior."""

    async def _run() -> None:
        base = datetime.now(UTC)
        async with AsyncSessionLocal() as session:
            for i in range(count):
                started = base - timedelta(minutes=i)
                session.add(
                    SyncRun(
                        kind=kind,
                        trigger="worker",
                        mode="incremental",
                        status="success",
                        started_at=started,
                        finished_at=started + timedelta(seconds=5),
                        duration_ms=5000,
                        result={"vehicles_upserted": i},
                    )
                )
            await session.commit()

    asyncio.run(_run())


def _delete_sync_runs() -> None:
    async def _run() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(SyncRun))
            await session.commit()

    asyncio.run(_run())


def test_sync_runs_pagina_y_reporta_el_total() -> None:
    _delete_sync_runs()
    _insert_sync_runs(25)
    client = _admin_client()

    first = client.get("/api/v1/data-quality/sync-runs", params={"limit": 10}).json()
    second = client.get("/api/v1/data-quality/sync-runs", params={"limit": 10, "offset": 10}).json()
    last = client.get("/api/v1/data-quality/sync-runs", params={"limit": 10, "offset": 20}).json()
    _delete_sync_runs()

    assert first["total"] == 25
    assert first["limit"] == 10 and first["offset"] == 0
    assert len(first["items"]) == 10
    assert len(second["items"]) == 10
    assert len(last["items"]) == 5  # la última página va incompleta

    # Sin solapamiento entre páginas: el orden tiene que ser total.
    ids = [r["id"] for r in first["items"] + second["items"] + last["items"]]
    assert len(set(ids)) == 25


def test_sync_runs_ordena_de_mas_reciente_a_mas_vieja() -> None:
    _delete_sync_runs()
    _insert_sync_runs(5)
    body = _admin_client().get("/api/v1/data-quality/sync-runs").json()
    _delete_sync_runs()

    fechas = [r["started_at"] for r in body["items"]]
    assert fechas == sorted(fechas, reverse=True)


def test_sync_runs_el_total_respeta_el_filtro_de_tipo() -> None:
    _delete_sync_runs()
    _insert_sync_runs(7, kind="cloudfleet")
    _insert_sync_runs(3, kind="master")
    client = _admin_client()

    todos = client.get("/api/v1/data-quality/sync-runs").json()
    solo_master = client.get("/api/v1/data-quality/sync-runs", params={"kind": "master"}).json()
    _delete_sync_runs()

    assert todos["total"] == 10
    assert solo_master["total"] == 3
    assert {r["kind"] for r in solo_master["items"]} == {"master"}


def test_sync_runs_offset_mas_alla_del_total_devuelve_pagina_vacia() -> None:
    """No debe romper: la UI puede pedir una página que ya no existe si otro
    actor borró filas entre dos refetches."""
    _delete_sync_runs()
    _insert_sync_runs(3)
    body = (
        _admin_client()
        .get("/api/v1/data-quality/sync-runs", params={"limit": 10, "offset": 500})
        .json()
    )
    _delete_sync_runs()

    assert body["items"] == []
    assert body["total"] == 3
