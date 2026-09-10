"""Tests de GET /fleets/{id}/motors: motores de la flota con sus datos de placa.

Réplica de solo lectura del contrato §2.4: las velocidades salen del snapshot de
Navi Vehículos y `null` significa "aún no capturadas allá", nunca 0.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.master_data import MotorCatalog, MotorRpmBand, Vehicle


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


def _seed_motors(fleet_id: str) -> tuple[str, str]:
    """Dos motores en la flota: uno con velocidades y rangos, otro sin nada.

    El segundo lleva dos vehículos para comprobar el orden por cobertura.
    """

    async def _do() -> tuple[str, str]:
        configured = f"CFG{uuid.uuid4().hex[:6].upper()}"
        bare = f"BARE{uuid.uuid4().hex[:5].upper()}"
        async with AsyncSessionLocal() as db:
            db.add(
                MotorCatalog(
                    motor_type=configured,
                    description="motor con placa",
                    governed_speed_rpm=2100,
                    max_overspeed_rpm=2250,
                )
            )
            db.add(MotorCatalog(motor_type=bare))
            await db.flush()
            db.add(
                MotorRpmBand(
                    motor_type=configured, band="rango_bajo", rpm_min=600, rpm_max=1100
                )
            )
            db.add_all(
                [
                    Vehicle(
                        fleet_id=uuid.UUID(fleet_id),
                        plate=f"CFG{uuid.uuid4().hex[:4].upper()}",
                        motor_type=configured,
                        is_active=True,
                    ),
                    Vehicle(
                        fleet_id=uuid.UUID(fleet_id),
                        plate=f"BR1{uuid.uuid4().hex[:4].upper()}",
                        motor_type=bare,
                        is_active=True,
                    ),
                    Vehicle(
                        fleet_id=uuid.UUID(fleet_id),
                        plate=f"BR2{uuid.uuid4().hex[:4].upper()}",
                        motor_type=bare,
                        is_active=True,
                    ),
                ]
            )
            await db.commit()
        return configured, bare

    return asyncio.run(_do())


def _new_fleet(admin: TestClient, name: str) -> str:
    return admin.post(
        "/api/v1/fleets",
        json={"code": f"FLEET-{uuid.uuid4().hex[:6].upper()}", "name": name},
    ).json()["id"]


def test_list_fleet_motors_shape() -> None:
    admin = _admin_client()
    fleet_id = _new_fleet(admin, "Flota motores")
    configured, bare = _seed_motors(fleet_id)

    resp = admin.get(f"/api/v1/fleets/{fleet_id}/motors")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    by_type = {row["motor_type"]: row for row in rows}
    assert set(by_type) == {configured, bare}

    with_plate = by_type[configured]
    assert with_plate["governed_speed_rpm"] == 2100
    assert with_plate["max_overspeed_rpm"] == 2250
    assert with_plate["description"] == "motor con placa"
    assert with_plate["vehicle_count"] == 1
    assert with_plate["rpm_band_count"] == 1

    # Sin capturar viaja como null (no 0): el portal lo pinta como "—".
    without_plate = by_type[bare]
    assert without_plate["governed_speed_rpm"] is None
    assert without_plate["max_overspeed_rpm"] is None
    assert without_plate["rpm_band_count"] == 0
    assert without_plate["vehicle_count"] == 2

    # Orden por cobertura descendente.
    assert rows[0]["motor_type"] == bare


def test_list_fleet_motors_only_returns_motors_of_that_fleet() -> None:
    """El catálogo es global; el endpoint solo muestra lo que la flota usa."""
    admin = _admin_client()
    fleet_id = _new_fleet(admin, "Flota con motores")
    other_fleet_id = _new_fleet(admin, "Flota vecina")
    configured, bare = _seed_motors(fleet_id)

    rows = admin.get(f"/api/v1/fleets/{other_fleet_id}/motors").json()
    assert [row["motor_type"] for row in rows if row["motor_type"] in {configured, bare}] == []


def test_fleet_motors_404_unknown_fleet() -> None:
    admin = _admin_client()
    assert admin.get(f"/api/v1/fleets/{uuid.uuid4()}/motors").status_code == 404
