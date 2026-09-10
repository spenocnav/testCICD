"""Réplica del estado real (`isDone`) de las novedades desde CloudFleet.

Fija lo que no se debe romper:

- el 404 "No Issues found" del listado es lista vacía, no error;
- la fecha de CloudFleet trae siete decimales y se parsea igual;
- una issue que no aparece en el listado se consulta una a una, con techo, y
  si sigue sin aparecer queda en `missing` sin tocar la fila;
- sólo las novedades `sent` no resueltas son candidatas; una ya resuelta no
  vuelve a costar llamadas;
- `external_synced_at` se escribe aunque el estado no cambie.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.master_data import GeotabDatabase, Vehicle
from app.models.navifault import NavifaultManagedFaultCase
from app.models.novedad import Novedad
from app.services import cloudfleet_service
from app.services import novedad_status_sync_service as svc

# ---------------------------------------------------------------------------
# Puro: parseo
# ---------------------------------------------------------------------------


def test_parse_cloudfleet_datetime_accepts_seven_decimals() -> None:
    parsed = svc.parse_cloudfleet_datetime("2026-08-21T20:00:00.0000000Z")
    assert parsed == datetime(2026, 8, 21, 20, 0, tzinfo=UTC)


def test_parse_cloudfleet_datetime_handles_offsets_and_garbage() -> None:
    assert svc.parse_cloudfleet_datetime("2026-08-21T15:00:00-05:00") == datetime(
        2026, 8, 21, 20, 0, tzinfo=UTC
    )
    assert svc.parse_cloudfleet_datetime("2026-08-21T20:00:00") == datetime(
        2026, 8, 21, 20, 0, tzinfo=UTC
    )
    assert svc.parse_cloudfleet_datetime(None) is None
    assert svc.parse_cloudfleet_datetime("") is None
    assert svc.parse_cloudfleet_datetime("ayer") is None
    assert svc.parse_cloudfleet_datetime(12345) is None


def test_issue_state_requires_boolean_is_done() -> None:
    assert svc.issue_state(None) is None
    assert svc.issue_state({"number": 1}) is None
    assert svc.issue_state({"isDone": "true"}) is None
    state = svc.issue_state(
        {"isDone": True, "doneAt": "2026-08-21T20:00:00.0000000Z", "workOrderDoneNumber": 5619}
    )
    assert state == svc.IssueState(
        is_done=True,
        done_at=datetime(2026, 8, 21, 20, 0, tzinfo=UTC),
        work_order_number=5619,
    )
    open_state = svc.issue_state({"isDone": False, "doneAt": None, "workOrderDoneNumber": None})
    assert open_state == svc.IssueState(is_done=False, done_at=None, work_order_number=None)


# ---------------------------------------------------------------------------
# Cliente CloudFleet (transporte simulado)
# ---------------------------------------------------------------------------


async def _orden_abierta(number: int, **_kwargs: Any) -> dict[str, Any]:
    """Doble de `get_work_order`: la orden existe y sigue abierta.

    El sync consulta la orden de toda novedad que declara una. Sin este doble
    la prueba salía a CloudFleet de verdad y sólo pasaba donde el `.env` traía
    la API key real.
    """
    return {"number": number, "status": "opened"}


@pytest.fixture
def _cloudfleet_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cloudfleet_service.settings, "cloudfleet_api_key", "test-key")
    cloudfleet_service._last_request_monotonic = None
    cloudfleet_service._rate_limit_until_monotonic = None

    async def no_throttle() -> None:
        return None

    monkeypatch.setattr(cloudfleet_service, "_throttle", no_throttle)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_cloudfleet_configured")
async def test_list_issues_treats_404_as_empty() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(
            404,
            json={"error": {"message": "No Issues found with the specified filters"}},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await cloudfleet_service.list_issues(
            reported_by=23, include_done=True, client=client
        )

    assert result == []
    assert len(seen) == 1
    params = dict(seen[0].params)
    assert params["reportedBy"] == "23"
    assert params["includeDone"] == "true"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_cloudfleet_configured")
async def test_list_issues_follows_next_page_header() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json=[{"number": n, "isDone": False} for n in range(1, 51)],
                headers={"X-NextPage": "https://fleet.cloudfleet.com/api/v1/issues/?page=2"},
                request=request,
            )
        return httpx.Response(200, json=[{"number": 51, "isDone": True}], request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await cloudfleet_service.list_issues(reported_by=23, client=client)

    assert calls == 2
    assert [i["number"] for i in result][-2:] == [50, 51]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_cloudfleet_configured")
async def test_get_issue_returns_none_on_404_and_raises_on_5xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(cloudfleet_service.asyncio, "sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/issues/404"):
            return httpx.Response(
                404, json={"error": {"message": "No Issue found"}}, request=request
            )
        if request.url.path.endswith("/issues/500"):
            return httpx.Response(500, json={"error": {"message": "boom"}}, request=request)
        return httpx.Response(200, json={"number": 698, "isDone": False}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await cloudfleet_service.get_issue(404, client=client) is None
        assert (await cloudfleet_service.get_issue(698, client=client)) == {
            "number": 698,
            "isDone": False,
        }
        with pytest.raises(cloudfleet_service.CloudfleetError):
            await cloudfleet_service.get_issue(500, client=client)


# ---------------------------------------------------------------------------
# Servicio contra la base desechable
# ---------------------------------------------------------------------------


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


def _create_fleet(admin: TestClient) -> str:
    resp = admin.post(
        "/api/v1/fleets",
        json={"code": f"NST-{uuid.uuid4().hex[:8].upper()}", "name": "Flota estado"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _seed(
    fleet_id: str,
    *,
    issue_number: int | None,
    cloudfleet_status: str = "sent",
    external_is_done: bool | None = None,
    created_days_ago: int = 0,
) -> str:
    async def _do() -> str:
        async with AsyncSessionLocal() as db:
            db_name = f"db-{uuid.uuid4().hex[:8]}"
            gd = GeotabDatabase(
                fleet_id=uuid.UUID(fleet_id),
                database_name=db_name,
                database_key=db_name.lower(),
                is_active=True,
            )
            db.add(gd)
            await db.flush()
            plate = f"TST{uuid.uuid4().hex[:4].upper()}"
            vehicle = Vehicle(
                fleet_id=uuid.UUID(fleet_id),
                geotab_database_id=gd.id,
                geotab_device_id=f"dev-{uuid.uuid4().hex[:6]}",
                plate=plate,
                motor_type=None,
                is_active=True,
            )
            db.add(vehicle)
            await db.flush()
            novedad = Novedad(
                vehicle_id=vehicle.id,
                fleet_id=uuid.UUID(fleet_id),
                vehicle_code=plate,
                reported_at=datetime.now(UTC),
                reported_by_id=23,
                priority="low",
                comment="novedad de prueba",
                send_mail=False,
                cloudfleet_status=cloudfleet_status,
                cloudfleet_issue_number=issue_number,
                external_is_done=external_is_done,
            )
            db.add(novedad)
            await db.flush()
            if created_days_ago:
                novedad.created_at = datetime.now(UTC) - timedelta(days=created_days_ago)
            await db.commit()
            return str(novedad.id)

    return asyncio.run(_do())


def _read(novedad_id: str) -> dict[str, Any]:
    async def _do() -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            n = await db.get(Novedad, uuid.UUID(novedad_id))
            assert n is not None
            return {
                "is_done": n.external_is_done,
                "done_at": n.external_done_at,
                "wo": n.external_work_order_number,
                "synced_at": n.external_synced_at,
                "labor_id": n.associated_labor_id,
                "labor_name": n.associated_labor_name,
                "deleted_at": n.external_deleted_at,
                "wo_status": n.external_work_order_status,
            }

    return asyncio.run(_do())


def _link_case_to_novedad(fleet_id: str, novedad_id: str) -> None:
    """Ata un caso de Navifault a la novedad, con el ciclo ABIERTO.

    `managed_through_at` en NULL es lo que define "ciclo abierto": la falla
    todavía espera que alguien declare el desenlace.
    """

    async def _do() -> None:
        async with AsyncSessionLocal() as db:
            n = await db.get(Novedad, uuid.UUID(novedad_id))
            assert n is not None
            db.add(
                NavifaultManagedFaultCase(
                    vehicle_id=n.vehicle_id,
                    signature_sha256=uuid.uuid4().hex,
                    source="SourceJ1939Id",
                    diagnostic_code=3031,
                    failure_mode=9.0,
                    escalated_novedad_id=n.id,
                    escalated_at=datetime.now(UTC),
                    managed_through_at=None,
                )
            )
            await db.commit()

    asyncio.run(_do())


def _clear_cases(fleet_id: str) -> None:
    async def _do() -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(NavifaultManagedFaultCase))
            await db.commit()

    asyncio.run(_do())


def _resolve_all_existing() -> None:
    """Deja sin candidatas lo sembrado por otros tests del módulo."""

    async def _do() -> None:
        from sqlalchemy import update

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(Novedad)
                .where(Novedad.cloudfleet_status == "sent")
                .values(external_is_done=True)
            )
            await db.commit()

    asyncio.run(_do())


def _last_sync_run() -> dict[str, Any]:
    async def _do() -> dict[str, Any]:
        from sqlalchemy import select

        from app.models.sync_run import SyncRun

        async with AsyncSessionLocal() as db:
            run = (
                await db.execute(
                    select(SyncRun)
                    .where(SyncRun.kind == svc.SYNC_KIND)
                    .order_by(SyncRun.started_at.desc())
                    .limit(1)
                )
            ).scalar_one()
            return {"kind": run.kind, "status": run.status, "result": run.result}

    return asyncio.run(_do())


def test_sync_below_threshold_queries_each_issue_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con pocas candidatas no se recorre el listado: una consulta exacta por issue."""
    _resolve_all_existing()
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    a_id = _seed(fleet_id, issue_number=301)
    b_id = _seed(fleet_id, issue_number=302)
    single_calls: list[int] = []

    async def explode(**kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("bajo el umbral no debe recorrer el listado")

    async def fake_lookup(number: int, **kwargs: Any) -> Any:
        single_calls.append(number)
        return cloudfleet_service.IssueLookup(
            exists=True,
            payload={"number": number, "isDone": number == 301, "workOrderDoneNumber": 7},
        )

    monkeypatch.setattr(svc.cloudfleet_service, "list_issues", explode)
    monkeypatch.setattr(svc.cloudfleet_service, "lookup_issue", fake_lookup)
    monkeypatch.setattr(svc.cloudfleet_service, "get_work_order", _orden_abierta)
    monkeypatch.setattr(svc.settings, "cloudfleet_id_reportedby", 23)
    # Deja sólo estas dos como candidatas aunque otros tests hayan sembrado más.
    monkeypatch.setattr(svc.settings, "novedad_status_sync_list_threshold", 1_000)

    summary = asyncio.run(svc.run_novedad_status_sync(trigger="cli"))

    assert summary["listed"] == 0
    assert summary["missing"] == 0
    assert single_calls == [301, 302]
    assert summary["single_lookups"] == summary["candidates"] == 2
    assert _read(a_id)["is_done"] is True
    assert _read(a_id)["wo"] == 7
    assert _read(b_id)["is_done"] is False
    assert _last_sync_run()["status"] == "success"


def test_sync_marks_done_and_leaves_missing_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_all_existing()
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    done_id = _seed(fleet_id, issue_number=101, created_days_ago=10)
    open_id = _seed(fleet_id, issue_number=102)
    missing_id = _seed(fleet_id, issue_number=103)
    # No candidatas: fallida sin número, y ya resuelta.
    failed_id = _seed(fleet_id, issue_number=None, cloudfleet_status="failed")
    already_id = _seed(fleet_id, issue_number=104, external_is_done=True)

    list_calls: list[dict[str, Any]] = []
    single_calls: list[int] = []

    async def fake_list_issues(**kwargs: Any) -> list[dict[str, Any]]:
        list_calls.append(kwargs)
        return [
            {
                "number": 101,
                "isDone": True,
                "doneAt": "2026-08-21T20:00:00.0000000Z",
                "workOrderDoneNumber": 5619,
            },
            {"number": 102, "isDone": False, "doneAt": None, "workOrderDoneNumber": None},
            # Ruido: una issue del mismo reportante que no es del portal.
            {"number": 999, "isDone": True},
        ]

    async def fake_lookup(number: int, **kwargs: Any) -> Any:
        # `exists=True` con payload ilegible: la issue está, pero no se pudo
        # leer. NO es un borrado, y por eso la fila no se toca.
        single_calls.append(number)
        return cloudfleet_service.IssueLookup(exists=True, payload=None)

    monkeypatch.setattr(svc.cloudfleet_service, "list_issues", fake_list_issues)
    monkeypatch.setattr(svc.cloudfleet_service, "lookup_issue", fake_lookup)
    monkeypatch.setattr(svc.cloudfleet_service, "get_work_order", _orden_abierta)
    monkeypatch.setattr(svc.settings, "cloudfleet_id_reportedby", 23)
    # Modo listado: por encima del umbral se recorre el listado completo.
    monkeypatch.setattr(svc.settings, "novedad_status_sync_list_threshold", 0)

    summary = asyncio.run(svc.run_novedad_status_sync(trigger="cli"))

    assert summary["candidates"] == 3
    assert summary["listed"] == 3
    assert summary["resolved_now"] == 1
    assert summary["still_open"] == 1
    assert summary["missing"] == 1
    assert summary["missing_numbers"] == [103]
    assert summary["single_lookups"] == 1
    assert single_calls == [103]

    # Una sola pasada del listado, completa y con las resueltas incluidas: los
    # filtros por fecha o reportante del proveedor no acotan (los ignora).
    assert list_calls == [{"include_done": True}]

    # La corrida queda auditada; con faltantes es `partial`.
    run = _last_sync_run()
    assert run["kind"] == "novedades"
    assert run["status"] == "partial"
    assert run["result"]["missing_numbers"] == [103]

    # El detalle y el listado publican el estado replicado: el detalle se arma a
    # mano en `novedad_read` y omitirlo ahí dejaba la ficha en "Sin verificar"
    # mientras la lista decía "Resuelta".
    detail = admin.get(f"/api/v1/novedades/{done_id}").json()
    assert detail["external_is_done"] is True
    assert detail["external_work_order_number"] == 5619
    assert detail["external_done_at"] is not None
    listed = admin.get("/api/v1/novedades", params={"external_done": "true"}).json()["items"]
    assert done_id in {item["id"] for item in listed}
    assert all(item["external_is_done"] is True for item in listed)

    done = _read(done_id)
    assert done["is_done"] is True
    assert done["done_at"] == datetime(2026, 8, 21, 20, 0, tzinfo=UTC)
    assert done["wo"] == 5619
    assert done["synced_at"] is not None

    still_open = _read(open_id)
    assert still_open["is_done"] is False
    assert still_open["wo"] is None
    assert still_open["synced_at"] is not None

    missing = _read(missing_id)
    # Intacta de verdad: ninguna de las columnas replicadas se escribió.
    assert missing == {
        "is_done": None,
        "done_at": None,
        "wo": None,
        "synced_at": None,
        "labor_id": None,
        "labor_name": None,
        "deleted_at": None,
        "wo_status": None,
    }
    assert _read(failed_id)["synced_at"] is None
    # Ya resuelta: no fue candidata, no se tocó.
    assert _read(already_id)["synced_at"] is None


def test_sync_without_candidates_makes_no_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def explode(**kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("no debería llamar a CloudFleet sin candidatas")

    monkeypatch.setattr(svc.cloudfleet_service, "list_issues", explode)
    monkeypatch.setattr(svc.settings, "cloudfleet_id_reportedby", 23)

    _resolve_all_existing()
    summary = asyncio.run(svc.run_novedad_status_sync(trigger="cli"))
    assert summary["candidates"] == 0
    assert summary["listed"] == 0


def test_sync_single_lookups_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    _resolve_all_existing()
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    ids = [_seed(fleet_id, issue_number=200 + i) for i in range(3)]
    single_calls: list[int] = []

    async def fake_list_issues(**kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def fake_lookup(number: int, **kwargs: Any) -> Any:
        single_calls.append(number)
        return cloudfleet_service.IssueLookup(
            exists=True, payload={"number": number, "isDone": False}
        )

    monkeypatch.setattr(svc.cloudfleet_service, "list_issues", fake_list_issues)
    monkeypatch.setattr(svc.cloudfleet_service, "lookup_issue", fake_lookup)
    monkeypatch.setattr(svc.cloudfleet_service, "get_work_order", _orden_abierta)
    monkeypatch.setattr(svc.settings, "cloudfleet_id_reportedby", 23)
    monkeypatch.setattr(svc.settings, "novedad_status_sync_list_threshold", 0)
    monkeypatch.setattr(svc.settings, "novedad_status_sync_max_single_lookups", 1)

    summary = asyncio.run(svc.run_novedad_status_sync(trigger="cli"))

    # Sólo la primera se consultó; las otras dos quedan como faltantes, sin tocar.
    assert len(single_calls) == 1
    assert summary["single_lookups"] == 1
    assert summary["still_open"] == 1
    assert summary["missing"] == 2
    assert _read(ids[0])["synced_at"] is not None
    assert _read(ids[1])["synced_at"] is None
    assert _read(ids[2])["synced_at"] is None


def test_sync_requires_reported_by_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc.settings, "cloudfleet_id_reportedby", None)
    with pytest.raises(cloudfleet_service.CloudfleetError):
        asyncio.run(svc.run_novedad_status_sync(trigger="cli"))

def test_issue_state_extrae_el_trabajo_asociado() -> None:
    """`associatedLabor` es lo que ata la novedad a UN trabajo de la orden.

    Una orden puede tener varios trabajos y no todos son de esta novedad, así
    que sin este campo no se puede atribuir el desenlace al trabajo correcto.
    """
    state = svc.issue_state(
        {
            "isDone": True,
            "workOrderDoneNumber": 5619,
            "associatedLabor": {"id": 88, "name": "  Cambio sensor NOx  "},
        }
    )
    assert state is not None
    assert state.associated_labor_id == 88
    assert state.associated_labor_name == "Cambio sensor NOx"


def test_issue_state_sin_trabajo_asociado_no_inventa_uno() -> None:
    """El taller puede cerrar sin asociar: eso es ausencia de dato, no un cero.

    Un `associatedLabor` sin `id` tampoco asocia: el nombre solo no permite
    ubicar el trabajo en la orden ni sus repuestos, y publicarlo mostraría un
    trabajo que nada puede resolver.
    """
    for issue in (
        {"isDone": True},
        {"isDone": True, "associatedLabor": None},
        {"isDone": True, "associatedLabor": {"name": "sin id"}},
        {"isDone": True, "associatedLabor": "no es un objeto"},
    ):
        state = svc.issue_state(issue)
        assert state is not None, issue
        assert state.associated_labor_id is None, issue
        assert state.associated_labor_name is None, issue

    # Con `id` y sin nombre legible sí hay enlace: el id es lo que resuelve.
    state = svc.issue_state({"isDone": True, "associatedLabor": {"id": 5, "name": "   "}})
    assert state is not None
    assert state.associated_labor_id == 5
    assert state.associated_labor_name is None


def test_sync_guarda_el_trabajo_asociado(monkeypatch: pytest.MonkeyPatch) -> None:
    """El sync ya lee estas issues: guardar el enlace no cuesta una petición más."""
    _resolve_all_existing()
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    con_labor = _seed(fleet_id, issue_number=501)
    sin_labor = _seed(fleet_id, issue_number=502)

    async def fake_lookup(number: int, **kwargs: Any) -> Any:
        payload: dict[str, Any] = {
            "number": number,
            "isDone": True,
            "workOrderDoneNumber": 5619,
        }
        if number == 501:
            payload["associatedLabor"] = {"id": 88, "name": "Cambio sensor NOx"}
        return cloudfleet_service.IssueLookup(exists=True, payload=payload)

    monkeypatch.setattr(svc.cloudfleet_service, "lookup_issue", fake_lookup)
    monkeypatch.setattr(svc.cloudfleet_service, "get_work_order", _orden_abierta)
    monkeypatch.setattr(svc.settings, "cloudfleet_api_key", "test-key")
    monkeypatch.setattr(svc.settings, "cloudfleet_id_reportedby", 23)
    monkeypatch.setattr(svc.settings, "novedad_status_sync_list_threshold", 1_000)

    asyncio.run(svc.run_novedad_status_sync(trigger="cli"))

    assert _read(con_labor)["labor_id"] == 88
    assert _read(con_labor)["labor_name"] == "Cambio sensor NOx"
    assert _read(sin_labor)["labor_id"] is None

    # El contrato lo publica: el detalle se arma a mano y omitirlo ahí es
    # exactamente cómo `external_*` quedó invisible en su momento.
    detail = admin.get(f"/api/v1/novedades/{con_labor}").json()
    assert detail["associated_labor_id"] == 88
    assert detail["associated_labor_name"] == "Cambio sensor NOx"
    listado = admin.get("/api/v1/novedades", params={"external_done": "true"}).json()
    fila = next(i for i in listado["items"] if i["id"] == con_labor)
    assert fila["associated_labor_id"] == 88


def test_sync_marca_borrada_con_404_y_la_desmarca_si_reaparece(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un 404 por número es la ÚNICA evidencia de borrado, y es reversible.

    Sin esto, una issue borrada desde la UI de CloudFleet deja la novedad
    persiguiendo un número que ya no existe. Y como es observación y no
    decisión, un 404 transitorio se corrige en la corrida siguiente.
    """
    _resolve_all_existing()
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    borrada = _seed(fleet_id, issue_number=601)
    viva = _seed(fleet_id, issue_number=602)
    existe = {601: False, 602: True}

    async def fake_lookup(number: int, **kwargs: Any) -> Any:
        if not existe[number]:
            return cloudfleet_service.IssueLookup(exists=False, payload=None)
        return cloudfleet_service.IssueLookup(
            exists=True, payload={"number": number, "isDone": False}
        )

    monkeypatch.setattr(svc.cloudfleet_service, "lookup_issue", fake_lookup)
    monkeypatch.setattr(svc.cloudfleet_service, "get_work_order", _orden_abierta)
    monkeypatch.setattr(svc.settings, "cloudfleet_id_reportedby", 23)
    monkeypatch.setattr(svc.settings, "novedad_status_sync_list_threshold", 1_000)

    summary = asyncio.run(svc.run_novedad_status_sync(trigger="cli"))

    assert summary["deleted"] == 1
    assert summary["deleted_numbers"] == [601]
    # Una borrada NO es una faltante: la faltante deja la fila intacta.
    assert summary["missing"] == 0
    assert _read(borrada)["deleted_at"] is not None
    assert _read(viva)["deleted_at"] is None
    # El estado verificado no se pisa con NULL al marcar el borrado.
    assert _read(viva)["is_done"] is False
    assert admin.get(f"/api/v1/novedades/{borrada}").json()["external_deleted_at"]

    # Reaparece: se desmarca sola.
    existe[601] = True
    asyncio.run(svc.run_novedad_status_sync(trigger="cli"))
    assert _read(borrada)["deleted_at"] is None


def test_sync_consulta_la_orden_solo_cuando_la_novedad_declara_una(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`isDone` no distingue "el taller la tiene" de "el taller terminó".

    Se activa al ASIGNAR la novedad a una orden, con la orden todavía abierta,
    así que el estado real hay que leerlo de la orden. Se consulta sólo cuando
    la novedad declara un número: una petición por novedad enlazada y ninguna
    por las demás.
    """
    _resolve_all_existing()
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    con_orden = _seed(fleet_id, issue_number=701)
    sin_orden = _seed(fleet_id, issue_number=702)
    ordenes_pedidas: list[int] = []

    async def fake_lookup(number: int, **kwargs: Any) -> Any:
        payload: dict[str, Any] = {"number": number, "isDone": number == 701}
        if number == 701:
            payload["workOrderDoneNumber"] = 5733
        return cloudfleet_service.IssueLookup(exists=True, payload=payload)

    async def fake_work_order(number: int, **kwargs: Any) -> dict[str, Any] | None:
        ordenes_pedidas.append(number)
        return {"number": number, "status": "opened"}

    monkeypatch.setattr(svc.cloudfleet_service, "lookup_issue", fake_lookup)
    monkeypatch.setattr(svc.cloudfleet_service, "get_work_order", _orden_abierta)
    monkeypatch.setattr(svc.cloudfleet_service, "get_work_order", fake_work_order)
    monkeypatch.setattr(svc.settings, "cloudfleet_id_reportedby", 23)
    monkeypatch.setattr(svc.settings, "novedad_status_sync_list_threshold", 1_000)

    resumen = asyncio.run(svc.run_novedad_status_sync(trigger="cli"))

    assert ordenes_pedidas == [5733], "sólo la novedad con orden cuesta una consulta"
    assert resumen["work_orders_checked"] == 1
    assert _read(con_orden)["wo_status"] == "opened"
    assert _read(sin_orden)["wo_status"] is None
    # `isDone` en true con la orden abierta: el taller la tiene, no terminó.
    assert _read(con_orden)["is_done"] is True


def test_una_escalada_se_vigila_aunque_conste_resuelta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El supuesto "una issue resuelta no se reabre" es falso, y costaba caro.

    Borrar el trabajo de la orden, o anular la orden, devuelve la novedad a
    abierta. Con el filtro histórico —que dejaba de mirar al marcarla resuelta—
    la novedad quedaba congelada en el portal para siempre.
    """
    _resolve_all_existing()
    admin = _admin_client()
    fleet_id = _create_fleet(admin)
    # Marcada resuelta: bajo el filtro histórico NO sería candidata.
    escalada_id = _seed(fleet_id, issue_number=801, external_is_done=True)
    suelta_id = _seed(fleet_id, issue_number=802, external_is_done=True)
    _link_case_to_novedad(fleet_id, escalada_id)
    consultadas: list[int] = []

    async def fake_lookup(number: int, **kwargs: Any) -> Any:
        consultadas.append(number)
        # Reabierta: el taller borró el trabajo.
        return cloudfleet_service.IssueLookup(
            exists=True, payload={"number": number, "isDone": False}
        )

    monkeypatch.setattr(svc.cloudfleet_service, "lookup_issue", fake_lookup)
    monkeypatch.setattr(svc.cloudfleet_service, "get_work_order", _orden_abierta)
    monkeypatch.setattr(svc.settings, "cloudfleet_id_reportedby", 23)
    monkeypatch.setattr(svc.settings, "novedad_status_sync_list_threshold", 1_000)

    try:
        asyncio.run(svc.run_novedad_status_sync(trigger="cli"))
        assert consultadas == [801], "sólo la escalada con ciclo abierto se vuelve a mirar"
        assert _read(escalada_id)["is_done"] is False, "la reapertura se replicó"
        # La novedad suelta conserva la regla histórica: resuelta, no se mira.
        assert _read(suelta_id)["is_done"] is True
    finally:
        _clear_cases(fleet_id)
