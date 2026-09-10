"""Auditoría de uso: registro por middleware y endpoints admin de resumen.

Fija los invariantes del módulo:

- el middleware registra la petición autenticada con la PLANTILLA de la ruta
  (`/api/v1/fleets/{fleet_id}`), nunca el path crudo con IDs;
- una petición sin sesión no registra nada;
- `/usage/*` exige rol admin (403 para un usuario de módulo, 401 sin sesión);
- el resumen excluye la fontanería (`/me`, `/auth/*`) de las agregaciones,
  pero `last_seen` sí la cuenta;
- las flotas del header X-Fleet-Id se agregan por `unnest`;
- un rango invertido responde 400.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.usage import UsageEvent
from app.services.usage_tracker import tracker


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


def _reportante_client(admin: TestClient, fleet_id: str) -> TestClient:
    email = f"uso-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "UsoPass123!"
    resp = admin.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Usuario Uso",
            "role_codes": ["reportante_novedades"],
            "fleet_ids": [fleet_id],
        },
    )
    assert resp.status_code == 201, resp.text
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def _create_fleet(admin: TestClient) -> str:
    resp = admin.post(
        "/api/v1/fleets",
        json={"code": f"USO-{uuid.uuid4().hex[:8].upper()}", "name": "Flota uso"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _flush() -> None:
    asyncio.run(tracker._flush())


def _seed_events(rows: list[dict]) -> None:
    async def _do() -> None:
        async with AsyncSessionLocal() as db:
            for row in rows:
                db.add(UsageEvent(**row))
            await db.commit()

    asyncio.run(_do())


def _base_event(user_id: uuid.UUID, **overrides) -> dict:
    row = {
        "ts": datetime.now(UTC),
        "user_id": user_id,
        "method": "GET",
        "route": "/api/v1/reportes/combustible/summary",
        "status_code": 200,
        "duration_ms": 120,
        "fleet_ids": None,
    }
    row.update(overrides)
    return row


def test_middleware_records_route_template_not_raw_path() -> None:
    admin = _admin_client()
    probe_id = uuid.uuid4()

    resp = admin.get(f"/api/v1/usage/users/{probe_id}")
    assert resp.status_code == 200, resp.text
    _flush()

    async def _routes() -> list[str]:
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(UsageEvent.route))).scalars().all()
            return list(rows)

    routes = asyncio.run(_routes())
    assert "/api/v1/usage/users/{user_id}" in routes
    assert not any(str(probe_id) in r for r in routes), "el path crudo con el UUID no se persiste"


def test_unauthenticated_request_records_nothing() -> None:
    client = TestClient(app)
    marker_route = "/api/v1/reportes/combustible/summary"
    resp = client.get(marker_route)
    assert resp.status_code == 401
    _flush()

    async def _count() -> int:
        from sqlalchemy import func, select

        async with AsyncSessionLocal() as db:
            stmt = select(func.count()).select_from(UsageEvent).where(
                UsageEvent.status_code == 401
            )
            return (await db.execute(stmt)).scalar_one()

    assert asyncio.run(_count()) == 0


def test_usage_endpoints_require_admin() -> None:
    anonymous = TestClient(app)
    assert anonymous.get("/api/v1/usage/summary").status_code == 401

    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    reportante = _reportante_client(admin, fleet_id)
    resp = reportante.get("/api/v1/usage/summary")
    assert resp.status_code == 403
    resp = reportante.get(f"/api/v1/usage/users/{uuid.uuid4()}")
    assert resp.status_code == 403


def test_summary_aggregates_and_excludes_plumbing() -> None:
    admin = _admin_client()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    fleet_x = uuid.uuid4()
    now = datetime.now(UTC)
    _seed_events(
        [
            _base_event(user_a, fleet_ids=[fleet_x]),
            _base_event(user_a, route="/api/v1/novedades", fleet_ids=[fleet_x]),
            _base_event(user_a, route="/api/v1/me"),  # fontanería: no cuenta
            _base_event(
                user_a, route="/api/v1/auth/refresh", method="POST", ts=now + timedelta(hours=1)
            ),
            _base_event(user_b, duration_ms=300),
        ]
    )

    resp = admin.get("/api/v1/usage/summary")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    users = {u["user_id"]: u for u in data["users"]}
    assert users[str(user_a)]["requests"] == 2, "me/auth quedan fuera del conteo"
    assert users[str(user_a)]["email"] is None, "usuario borrado: la auditoría lo sobrevive"
    # last_seen SÍ cuenta la fontanería: el refresh es la última actividad.
    assert users[str(user_a)]["last_seen"] > users[str(user_b)]["last_seen"]

    sections = {s["section"]: s for s in data["sections"]}
    assert "me" not in sections and "auth" not in sections
    assert sections["reportes"]["requests"] >= 2
    assert sections["novedades"]["requests"] >= 1

    fleets = {f["fleet_id"]: f for f in data["fleets"]}
    assert fleets[str(fleet_x)]["requests"] == 2
    assert fleets[str(fleet_x)]["fleet_name"] is None, "flota desconocida no rompe el resumen"

    routes = {(r["method"], r["route"]) for r in data["routes"]}
    assert ("GET", "/api/v1/reportes/combustible/summary") in routes
    assert not any(r[1].startswith("/api/v1/auth/") for r in routes)


def test_user_detail_scopes_to_that_user() -> None:
    admin = _admin_client()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    _seed_events(
        [
            _base_event(user_a),
            _base_event(user_a, route="/api/v1/vehiculos", duration_ms=50),
            _base_event(user_b, duration_ms=999),
        ]
    )

    resp = admin.get(f"/api/v1/usage/users/{user_a}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_requests"] == 2
    assert data["last_seen"] is not None
    assert all(r["route"] != "/api/v1/vehiculos" or r["requests"] == 1 for r in data["routes"])
    assert not any(r["requests"] > 2 for r in data["routes"]), "no mezcla eventos de otro usuario"


def test_invalid_range_rejected() -> None:
    admin = _admin_client()
    resp = admin.get(
        "/api/v1/usage/summary",
        params={"start_date": "2026-08-31", "end_date": "2026-08-01"},
    )
    assert resp.status_code == 400
