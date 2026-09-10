"""Tests del endpoint GET /fleets/{id}/databases: bases con credenciales (sin
password) y reglas, más conteo de vehículos."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.fleet import Fleet
from app.models.master_data import (
    GeotabCredential,
    GeotabDatabase,
    GeotabRule,
    GeotabRuleApplication,
    MotorCatalog,
    Vehicle,
)
from app.services.master_data_service import (
    EXTRACTION_DATASETS,
    acquire_geotab_credential,
    advance_vehicle_watermark,
    get_vehicle_extraction_windows,
    list_vehicle_extraction_state,
    list_vehicle_rule_plans,
    request_bulk_backfill,
    request_vehicle_backfill,
)


def _admin_client() -> TestClient:
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


def _seed_database(fleet_id: str) -> str:
    """Base geotab con 1 credencial, 2 reglas y 1 vehículo; devuelve db_id."""

    async def _do() -> str:
        async with AsyncSessionLocal() as db:
            name = f"db-{uuid.uuid4().hex[:8]}"
            gd = GeotabDatabase(
                fleet_id=uuid.UUID(fleet_id),
                database_name=name,
                database_key=name.lower(),
                plate_prefix="TS",
                is_active=True,
            )
            db.add(gd)
            motor = f"M{uuid.uuid4().hex[:6].upper()}"
            db.add(MotorCatalog(motor_type=motor, description="motor test"))
            await db.flush()
            db.add(
                GeotabCredential(
                    geotab_database_id=gd.id,
                    username=f"user-{uuid.uuid4().hex[:6]}@test.co",
                    password_enc=b"gAAAAA-fake-token",
                    label="cuenta test",
                    is_active=True,
                )
            )
            op_rule = GeotabRule(
                geotab_database_id=gd.id,
                rule_id=f"r{uuid.uuid4().hex[:10]}",
                name="RPM > 2200",
            )
            hab_rule = GeotabRule(
                geotab_database_id=gd.id,
                rule_id=f"r{uuid.uuid4().hex[:10]}",
                name="Frenada brusca",
            )
            db.add_all([op_rule, hab_rule])
            await db.flush()
            db.add_all(
                [
                    GeotabRuleApplication(
                        geotab_rule_id=op_rule.id,
                        category="operacion",
                        motor_type=motor,
                        band="exceso_rpm",
                        is_descenso=False,
                    ),
                    GeotabRuleApplication(
                        geotab_rule_id=hab_rule.id,
                        category="habito_seguro",
                        description="Frenadas bruscas",
                    ),
                ]
            )
            db.add(
                Vehicle(
                    fleet_id=uuid.UUID(fleet_id),
                    geotab_database_id=gd.id,
                    geotab_device_id=f"dev-{uuid.uuid4().hex[:6]}",
                    plate=f"TST{uuid.uuid4().hex[:4].upper()}",
                    is_active=True,
                )
            )
            await db.commit()
            return str(gd.id)

    return asyncio.run(_do())


def test_list_fleet_databases_shape_and_no_password() -> None:
    admin = _admin_client()
    code = f"FLEET-{uuid.uuid4().hex[:6].upper()}"
    fleet_id = admin.post("/api/v1/fleets", json={"code": code, "name": "Flota DB"}).json()["id"]
    db_id = _seed_database(fleet_id)

    resp = admin.get(f"/api/v1/fleets/{fleet_id}/databases")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    row = next(d for d in rows if d["id"] == db_id)

    assert row["database_key"] == row["database_name"].lower()
    assert row["connection_type"] == "geotab"
    assert row["plate_prefix"] == "TS"
    assert row["vehicle_count"] == 1

    assert len(row["credentials"]) == 1
    cred = row["credentials"][0]
    assert cred["label"] == "cuenta test"
    # El password jamás viaja al frontend, ni cifrado.
    assert "password" not in cred
    assert "password_enc" not in cred

    categories = {r["category"] for r in row["rules"]}
    assert categories == {"operacion", "habito_seguro"}
    operation = next(r for r in row["rules"] if r["category"] == "operacion")
    assert operation["band"] == "exceso_rpm"
    assert operation["is_descenso"] is False
    assert operation["description"] is None
    safe_habit = next(r for r in row["rules"] if r["category"] == "habito_seguro")
    assert safe_habit["description"] == "Frenadas bruscas"
    assert safe_habit["band"] is None


def test_fleet_databases_404_unknown_fleet() -> None:
    admin = _admin_client()
    assert admin.get(f"/api/v1/fleets/{uuid.uuid4()}/databases").status_code == 404


def test_list_fleet_databases_includes_rules_from_shared_database_key() -> None:
    admin = _admin_client()
    fleet_id = admin.post(
        "/api/v1/fleets",
        json={"code": f"FLEET-{uuid.uuid4().hex[:6].upper()}", "name": "Flota compartida"},
    ).json()["id"]
    db_id = _seed_database(fleet_id)

    async def _add_shared_rule() -> None:
        async with AsyncSessionLocal() as db:
            database = await db.get(GeotabDatabase, uuid.UUID(db_id))
            assert database is not None
            sibling_fleet = Fleet(
                code=f"FLEET-{uuid.uuid4().hex[:6].upper()}", name="Cliente hermano"
            )
            db.add(sibling_fleet)
            await db.flush()
            sibling_database = GeotabDatabase(
                fleet_id=sibling_fleet.id,
                database_name="shared-rules",
                database_key=database.database_key,
                is_active=True,
            )
            db.add(sibling_database)
            await db.flush()
            shared_rule = GeotabRule(
                geotab_database_id=sibling_database.id,
                rule_id=f"shared-{uuid.uuid4().hex[:10]}",
                name="Regla compartida",
                is_active=True,
            )
            db.add(shared_rule)
            await db.flush()
            db.add(
                GeotabRuleApplication(
                    geotab_rule_id=shared_rule.id,
                    category="habito_seguro",
                    description="Frenadas bruscas",
                    is_active=True,
                )
            )
            await db.commit()

    asyncio.run(_add_shared_rule())

    resp = admin.get(f"/api/v1/fleets/{fleet_id}/databases")
    assert resp.status_code == 200, resp.text
    row = next(database for database in resp.json() if database["id"] == db_id)
    assert any(rule["name"] == "Regla compartida" for rule in row["rules"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vehicle_rule_plan_uses_database_key_siblings() -> None:
    async with AsyncSessionLocal() as db:
        try:
            shared_key = f"shared-{uuid.uuid4().hex[:8]}"
            motor = f"M{uuid.uuid4().hex[:6].upper()}"
            other_motor = f"M{uuid.uuid4().hex[:6].upper()}"

            fleet_a = Fleet(code=f"FA-{uuid.uuid4().hex[:8]}", name="Flota A")
            fleet_b = Fleet(code=f"FB-{uuid.uuid4().hex[:8]}", name="Flota B")
            db.add_all(
                [
                    fleet_a,
                    fleet_b,
                    MotorCatalog(motor_type=motor),
                    MotorCatalog(motor_type=other_motor),
                ]
            )
            await db.flush()

            vehicle_db = GeotabDatabase(
                fleet_id=fleet_a.id,
                database_name=f"{shared_key}-veh",
                database_key=shared_key,
                is_active=True,
            )
            rules_db = GeotabDatabase(
                fleet_id=fleet_b.id,
                database_name=f"{shared_key}-rules",
                database_key=shared_key,
                is_active=True,
            )
            db.add_all([vehicle_db, rules_db])
            await db.flush()

            op_rule = GeotabRule(
                geotab_database_id=rules_db.id,
                rule_id=f"op-{uuid.uuid4().hex[:8]}",
                name="RPM motor test",
                is_active=True,
            )
            hab_rule = GeotabRule(
                geotab_database_id=rules_db.id,
                rule_id=f"hab-{uuid.uuid4().hex[:8]}",
                name="Frenada brusca",
                is_active=True,
            )
            other_rule = GeotabRule(
                geotab_database_id=rules_db.id,
                rule_id=f"other-{uuid.uuid4().hex[:8]}",
                name="Otro motor",
                is_active=True,
            )
            db.add_all([op_rule, hab_rule, other_rule])
            await db.flush()
            db.add_all(
                [
                    GeotabRuleApplication(
                        geotab_rule_id=op_rule.id,
                        category="operacion",
                        motor_type=motor,
                        is_active=True,
                    ),
                    GeotabRuleApplication(
                        geotab_rule_id=hab_rule.id,
                        category="habito_seguro",
                        is_active=True,
                    ),
                    GeotabRuleApplication(
                        geotab_rule_id=other_rule.id,
                        category="operacion",
                        motor_type=other_motor,
                        is_active=True,
                    ),
                ]
            )
            vehicle = Vehicle(
                fleet_id=fleet_a.id,
                geotab_database_id=vehicle_db.id,
                geotab_device_id=f"dev-{uuid.uuid4().hex[:8]}",
                geotab_customer_status="found",
                plate=f"TST{uuid.uuid4().hex[:5].upper()}",
                motor_type=motor,
                is_active=True,
            )
            db.add(vehicle)
            await db.flush()

            plans = await list_vehicle_rule_plans(db, fleet_ids=[fleet_a.id], only_with_rules=True)
            plan = next(p for p in plans if p.vehicle_id == vehicle.id)

            assert plan.database_key == shared_key
            assert {rule.category for rule in plan.rules} == {"operacion", "habito_seguro"}
            assert {rule.name for rule in plan.rules} == {"RPM motor test", "Frenada brusca"}
        finally:
            await db.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_physical_rule_can_have_operation_and_habito_applications() -> None:
    async with AsyncSessionLocal() as db:
        try:
            motor = f"M{uuid.uuid4().hex[:6].upper()}"
            other_motor = f"M{uuid.uuid4().hex[:6].upper()}"
            fleet = Fleet(code=f"FR-{uuid.uuid4().hex[:8]}", name="Flota RPM")
            db.add_all(
                [
                    fleet,
                    MotorCatalog(motor_type=motor),
                    MotorCatalog(motor_type=other_motor),
                ]
            )
            await db.flush()

            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=f"rpm-{uuid.uuid4().hex[:8]}",
                database_key=f"rpm-{uuid.uuid4().hex[:8]}",
                is_active=True,
            )
            db.add(geotab_db)
            await db.flush()

            rule = GeotabRule(
                geotab_database_id=geotab_db.id,
                rule_id=f"rpm-{uuid.uuid4().hex[:8]}",
                name="Exceso de RPM",
                is_active=True,
            )
            db.add(rule)
            await db.flush()
            db.add_all(
                [
                    GeotabRuleApplication(
                        geotab_rule_id=rule.id,
                        category="operacion",
                        motor_type=motor,
                        band="exceso_rpm",
                        is_active=True,
                    ),
                    GeotabRuleApplication(
                        geotab_rule_id=rule.id,
                        category="habito_seguro",
                        event_type="exceso_rpm",
                        motor_type=motor,
                        description="Excesos de RPM",
                        is_active=True,
                    ),
                ]
            )
            vehicle = Vehicle(
                fleet_id=fleet.id,
                geotab_database_id=geotab_db.id,
                geotab_device_id=f"dev-{uuid.uuid4().hex[:8]}",
                geotab_customer_status="found",
                plate=f"RPM{uuid.uuid4().hex[:4].upper()}",
                motor_type=motor,
                is_active=True,
            )
            other_vehicle = Vehicle(
                fleet_id=fleet.id,
                geotab_database_id=geotab_db.id,
                geotab_device_id=f"dev-{uuid.uuid4().hex[:8]}",
                geotab_customer_status="found",
                plate=f"OTR{uuid.uuid4().hex[:4].upper()}",
                motor_type=other_motor,
                is_active=True,
            )
            db.add_all([vehicle, other_vehicle])
            await db.flush()

            plans = await list_vehicle_rule_plans(db, fleet_ids=[fleet.id], only_with_rules=True)
            plan = next(p for p in plans if p.vehicle_id == vehicle.id)

            assert len({rule.geotab_rule_id for rule in plan.rules}) == 1
            assert {rule.category for rule in plan.rules} == {"operacion", "habito_seguro"}
            operation = next(rule for rule in plan.rules if rule.category == "operacion")
            habit = next(rule for rule in plan.rules if rule.category == "habito_seguro")
            assert operation.band == "exceso_rpm"
            assert operation.motor_type == motor
            assert habit.event_type == "exceso_rpm"
            assert habit.description == "Excesos de RPM"
            assert habit.motor_type == motor
            assert len(plan.physical_rules) == 1
            assert len(plan.physical_rules[0].applications) == 2
            assert all(p.vehicle_id != other_vehicle.id for p in plans)
        finally:
            await db.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_acquire_geotab_credential_rotates_by_database_key() -> None:
    async with AsyncSessionLocal() as db:
        try:
            shared_key = f"cred-{uuid.uuid4().hex[:8]}"
            fleet = Fleet(code=f"FC-{uuid.uuid4().hex[:8]}", name="Flota Cred")
            db.add(fleet)
            await db.flush()

            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=f"{shared_key}-db",
                database_key=shared_key,
                is_active=True,
            )
            db.add(geotab_db)
            await db.flush()

            credential = GeotabCredential(
                geotab_database_id=geotab_db.id,
                username=f"user-{uuid.uuid4().hex[:8]}@test.co",
                password_enc=encrypt_secret("real-password"),
                label="principal",
                is_active=True,
            )
            db.add(credential)
            await db.flush()

            lease = await acquire_geotab_credential(db, shared_key)

            assert lease is not None
            assert lease.id == credential.id
            assert lease.database_name == geotab_db.database_name
            assert lease.username == credential.username
            assert lease.password == "real-password"
            assert lease.leased_at is not None

            # Un pool real: otra flota registra la MISMA base física (mismo
            # database_key, otra fila de geotab_databases) con otra cuenta, y una
            # tercera cuenta inactiva que nunca debe salir. La rotación LRU debe
            # alternar entre las dos activas cruzando las filas de flota.
            other_fleet = Fleet(code=f"FC-{uuid.uuid4().hex[:8]}", name="Flota Cred 2")
            db.add(other_fleet)
            await db.flush()
            other_db = GeotabDatabase(
                fleet_id=other_fleet.id,
                database_name=f"{shared_key}-db",
                database_key=shared_key,
                is_active=True,
            )
            db.add(other_db)
            await db.flush()
            second = GeotabCredential(
                geotab_database_id=other_db.id,
                username=f"user-{uuid.uuid4().hex[:8]}@test.co",
                password_enc=encrypt_secret("second-password"),
                label="secundaria",
                is_active=True,
            )
            inactive = GeotabCredential(
                geotab_database_id=other_db.id,
                username=f"user-{uuid.uuid4().hex[:8]}@test.co",
                password_enc=encrypt_secret("inactive-password"),
                label="baja",
                is_active=False,
            )
            db.add_all([second, inactive])
            await db.flush()

            lease2 = await acquire_geotab_credential(db, shared_key)
            lease3 = await acquire_geotab_credential(db, shared_key)
            lease4 = await acquire_geotab_credential(db, shared_key)
            assert lease2 is not None and lease3 is not None and lease4 is not None
            # La nunca usada sale primero; después alterna por last_used_at.
            assert lease2.id == second.id
            assert lease2.password == "second-password"
            assert lease3.id == credential.id
            assert lease4.id == second.id
            assert inactive.id not in {lease.id, lease2.id, lease3.id, lease4.id}
        finally:
            await db.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vehicle_extraction_state_backfill_and_windows() -> None:
    from datetime import UTC, datetime

    async with AsyncSessionLocal() as db:
        try:
            fleet = Fleet(code=f"FE-{uuid.uuid4().hex[:8]}", name="Flota Extrac")
            db.add(fleet)
            await db.flush()
            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=f"ext-{uuid.uuid4().hex[:8]}",
                database_key=f"ext-{uuid.uuid4().hex[:8]}",
                is_active=True,
            )
            db.add(geotab_db)
            await db.flush()
            vehicle = Vehicle(
                fleet_id=fleet.id,
                geotab_database_id=geotab_db.id,
                geotab_device_id=f"dev-{uuid.uuid4().hex[:8]}",
                geotab_customer_status="found",
                plate=f"EXT{uuid.uuid4().hex[:4].upper()}",
                is_active=True,
            )
            db.add(vehicle)
            await db.flush()

            # 1ra lectura: siembra 8 datasets, todos pending sin watermark.
            state = await list_vehicle_extraction_state(db, vehicle.id)
            assert {s.dataset for s in state} == set(EXTRACTION_DATASETS)
            assert all(s.status == "pending" and s.watermark is None for s in state)
            assert all(s.from_date == s.backfill_from for s in state)  # default lookback

            # Backfill de un dataset desde una fecha explícita.
            backfill_from = datetime(2026, 5, 1, tzinfo=UTC)
            affected = await request_vehicle_backfill(
                db, vehicle.id, backfill_from, dataset="habito_seguro"
            )
            assert affected == 1
            hs = next(
                s
                for s in await list_vehicle_extraction_state(db, vehicle.id)
                if s.dataset == "habito_seguro"
            )
            assert hs.backfill_from == backfill_from
            assert hs.watermark is None
            assert hs.from_date == backfill_from

            # El worker ve la ventana [backfill_from, to_date) para ese dataset.
            to_date = datetime(2026, 6, 24, tzinfo=UTC)
            windows = await get_vehicle_extraction_windows(
                db, "habito_seguro", to_date, fleet_ids=[fleet.id]
            )
            win = next(w for w in windows if w.vehicle_id == vehicle.id)
            assert win.from_date == backfill_from
            assert win.to_date == to_date

            # Tras avanzar el watermark, ya no hay días nuevos antes de to_date.
            await advance_vehicle_watermark(db, vehicle.id, "habito_seguro", to_date)
            windows2 = await get_vehicle_extraction_windows(
                db, "habito_seguro", to_date, fleet_ids=[fleet.id]
            )
            assert all(w.vehicle_id != vehicle.id for w in windows2)

            # dataset desconocido -> ValueError.
            with pytest.raises(ValueError):
                await request_vehicle_backfill(db, vehicle.id, backfill_from, dataset="nope")
        finally:
            await db.rollback()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_request_bulk_backfill_scopes_and_counts() -> None:
    from datetime import UTC, datetime

    async with AsyncSessionLocal() as db:
        try:
            fleet = Fleet(code=f"FB-{uuid.uuid4().hex[:8]}", name="Flota Bulk")
            db.add(fleet)
            await db.flush()
            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=f"bulk-{uuid.uuid4().hex[:8]}",
                database_key=f"bulk-{uuid.uuid4().hex[:8]}",
                is_active=True,
            )
            db.add(geotab_db)
            await db.flush()
            v1 = Vehicle(
                fleet_id=fleet.id,
                geotab_database_id=geotab_db.id,
                geotab_device_id=f"dev-{uuid.uuid4().hex[:8]}",
                geotab_customer_status="found",
                plate=f"BLK{uuid.uuid4().hex[:4].upper()}",
                is_active=True,
            )
            v2 = Vehicle(
                fleet_id=fleet.id,
                geotab_database_id=geotab_db.id,
                geotab_device_id=f"dev-{uuid.uuid4().hex[:8]}",
                geotab_customer_status="found",
                plate=f"BLK{uuid.uuid4().hex[:4].upper()}",
                is_active=True,
            )
            db.add_all([v1, v2])
            await db.flush()

            from_date = datetime(2026, 4, 1, tzinfo=UTC)

            # Todas las de la flota, un dataset -> 2 vehículos x 1 = 2 filas.
            r = await request_bulk_backfill(
                db,
                fleet_ids=[fleet.id],
                vehicle_ids=None,
                from_date=from_date,
                datasets=["habito_seguro"],
            )
            assert r == {"vehicles": 2, "datasets": 1, "rows": 2}
            hs = next(
                s
                for s in await list_vehicle_extraction_state(db, v1.id)
                if s.dataset == "habito_seguro"
            )
            assert hs.backfill_from == from_date and hs.watermark is None

            # Un vehículo, todos los datasets -> 1 x 8 filas.
            r2 = await request_bulk_backfill(
                db,
                fleet_ids=[fleet.id],
                vehicle_ids=[v1.id],
                from_date=from_date,
                datasets=None,
            )
            assert r2["vehicles"] == 1
            assert r2["rows"] == r2["datasets"]

            # Otra flota no toca estos vehículos.
            r3 = await request_bulk_backfill(
                db,
                fleet_ids=[uuid.uuid4()],
                vehicle_ids=None,
                from_date=from_date,
                datasets=["habito_seguro"],
            )
            assert r3["rows"] == 0

            with pytest.raises(ValueError):
                await request_bulk_backfill(
                    db,
                    fleet_ids=[fleet.id],
                    vehicle_ids=None,
                    from_date=from_date,
                    datasets=["nope"],
                )
        finally:
            await db.rollback()
