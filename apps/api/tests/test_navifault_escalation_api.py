"""Escalar una falla crea la issue, la novedad y ata el caso — y NO la gestiona.

CloudFleet se simula: crear issues reales es irreversible —el proveedor responde
405 a `DELETE`— así que una prueba contra el proveedor dejaría basura que hay que
cerrar a mano desde su UI.

Lo que estas pruebas protegen, en orden de gravedad:

1. que escalar **no** deje la falla gestionada. Si lo hiciera, arrancaría la
   verificación de 30 días sobre una reparación que no ocurrió y falsearía la
   efectividad de los desenlaces;
2. que un rechazo del proveedor **no** deje rastro local: ni novedad, ni caso
   escalado. El orden CloudFleet-primero sólo es defendible si el rollback
   funciona;
3. que dos escalamientos del mismo ciclo no creen dos issues.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.analytics import AnalyticsBase, DimVehicle, FactFaultEvent
from app.models.fleet import Fleet
from app.models.master_data import GeotabDatabase, Vehicle
from app.models.navifault import NavifaultManagedFaultAction, NavifaultManagedFaultCase
from app.models.novedad import Novedad
from app.services import cloudfleet_service
from app.services.cloudfleet_service import CloudfleetError, CloudfleetVehicleNotFoundError

_PREFIJO = f"NFEA{uuid.uuid4().hex[:8].upper()}"
_DB = f"nfea_{uuid.uuid4().hex[:8]}"
_DEV = f"bNFEA{uuid.uuid4().hex[:6]}"
_VEH = f"nfea-veh-{uuid.uuid4().hex[:8]}"
_PLACA = f"{_PREFIJO[-6:]}A"
_ROW = f"{_PREFIJO}-r0"
_CODIGO = 2976
_FMI = 3.0
_BASE = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=4)

URL = "/api/v1/navifault/management/escalate"


@pytest.fixture(autouse=True)
def _cloudfleet_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Escalar exige CLOUDFLEET_ID_REPORTEDBY (503 sin él) y la API key.

    Las pruebas simulan `create_issue`, así que ningún valor sale a la red;
    fijarlos aquí evita depender del `.env` de la máquina.
    """
    monkeypatch.setattr(settings, "cloudfleet_api_key", "test-key")
    monkeypatch.setattr(settings, "cloudfleet_id_reportedby", 23)


def _admin() -> TestClient:
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


@pytest_asyncio.fixture(scope="module")
async def falla_seed():
    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(code=_PREFIJO, name="Flota escalamiento API", is_active=True)
            session.add(fleet)
            await session.flush()
            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=_DB,
                database_key=_DB,
                connection_type="geotab",
                is_active=True,
            )
            session.add(geotab_db)
            await session.flush()
            session.add(
                Vehicle(
                    plate=_PLACA,
                    geotab_device_id=_DEV,
                    geotab_customer_status="found",
                    fleet_id=fleet.id,
                    geotab_database_id=geotab_db.id,
                    is_active=True,
                )
            )
            await session.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
            await session.run_sync(
                lambda sync_session: AnalyticsBase.metadata.create_all(
                    sync_session.connection(),
                    tables=[DimVehicle.__table__, FactFaultEvent.__table__],
                    checkfirst=True,
                )
            )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_vehicle
                    (vehicle_id, device_id, vehicle_label, database_name, is_active)
                VALUES (:v, :d, 'Escalamiento API', :db, true)
                ON CONFLICT (vehicle_id) DO UPDATE SET device_id = EXCLUDED.device_id
            """),
                {"v": _VEH, "d": _DEV, "db": _DB},
            )
            await session.execute(
                text("""
                INSERT INTO analytics.fact_fault_event
                    (row_id, vehicle_id, database_name, date_key, fecha, movil, fecha_de_falla,
                     codigo_diagnostico, codigo_modo_de_falla, nombre_fuente_diagnostico,
                     estado_de_falla, recuento_de_fallos, tipo_de_atencion,
                     luz_de_parada_amber, luz_de_parada_roja, lampara_de_averia,
                     lampara_de_advertencia, nombre_de_controlador, diagnostico, modo_de_falla)
                VALUES
                    (:row_id, :veh, :db, :dk, :fecha, :placa, :cuando, :codigo, :fmi,
                     'SourceJ1939Id', 'Active', 7, 'Nivel 1 - Urgente', false, true, false,
                     false, 'Engine Control Module', 'Presion de aceite baja',
                     'Data valid but below normal')
                ON CONFLICT (row_id) DO NOTHING
            """),
                {
                    "row_id": _ROW,
                    "veh": _VEH,
                    "db": _DB,
                    "dk": int(_BASE.strftime("%Y%m%d")),
                    "fecha": _BASE.date(),
                    "placa": _PLACA,
                    "cuando": _BASE,
                    "codigo": _CODIGO,
                    "fmi": _FMI,
                },
            )
            await session.commit()
            fleet_id = fleet.id
        except Exception:
            await session.rollback()
            raise

    yield fleet_id

    async with AsyncSessionLocal() as session:
        # El caso queda SIN gestión, y el `downgrade` de `e8f9a0b10057` se niega
        # —con razón— a restaurar el NOT NULL mientras exista uno. La suite hace
        # `downgrade base` por módulo, así que hay que borrarlo aquí.
        await session.execute(
            text("DELETE FROM navifault.managed_fault_cases WHERE vehicle_id IN"
                 " (SELECT id FROM vehicles WHERE plate = :p)"),
            {"p": _PLACA},
        )
        await session.execute(
            text("DELETE FROM analytics.fact_fault_event WHERE row_id LIKE :p"),
            {"p": f"{_PREFIJO}%"},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = :v"), {"v": _VEH}
        )
        await session.commit()


def _fake_issue(numero: int):
    llamadas = {"n": 0}

    async def _crear(payload, *, client=None):
        llamadas["n"] += 1
        return cloudfleet_service.CloudfleetCreateResult(
            issue_number=numero, response={"number": numero}
        )

    return _crear, llamadas


async def _estado_del_caso():
    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(NavifaultManagedFaultCase).where(
                NavifaultManagedFaultCase.diagnostic_code == _CODIGO
            )
        )


@pytest.mark.asyncio
async def test_escalar_crea_la_novedad_y_no_gestiona_la_falla(falla_seed) -> None:
    crear, llamadas = _fake_issue(920_001)
    with patch.object(cloudfleet_service, "create_issue", new=crear):
        resp = _admin().post(
            URL,
            json={
                "fault_row_id": _ROW,
                "priority": "high",
                "comment": "El taller debe revisarla en la próxima entrada.",
            },
        )
    assert resp.status_code == 201, resp.text
    cuerpo = resp.json()
    assert cuerpo["cloudfleet_issue_number"] == 920_001
    assert cuerpo["status"] == "escalada"
    assert cuerpo["created"] is True
    assert llamadas["n"] == 1

    caso = await _estado_del_caso()
    assert caso is not None
    assert caso.escalated_novedad_id is not None
    assert caso.escalated_at is not None
    # Lo esencial: escalar NO gestiona.
    assert caso.managed_through_at is None
    assert caso.last_managed_at is None
    assert caso.last_managed_by_user_id is None

    async with AsyncSessionLocal() as session:
        novedad = await session.get(Novedad, caso.escalated_novedad_id)
        assert novedad is not None
        assert novedad.cloudfleet_status == "sent"
        # El texto del usuario va primero; la firma resuelta, debajo.
        assert novedad.comment.startswith("El taller debe revisarla")
        assert f"Código: {_CODIGO}" in novedad.comment
        assert "Engine Control Module" in novedad.comment
        assert "parada roja" in novedad.comment

        accion = await session.scalar(
            select(NavifaultManagedFaultAction).where(
                NavifaultManagedFaultAction.case_id == caso.case_id
            )
        )
        assert accion is not None
        assert accion.action_type == "escalated"
        assert accion.managed_through_at is None, "la bitácora no debe fijar checkpoint"


@pytest.mark.asyncio
async def test_no_se_escala_dos_veces_el_mismo_ciclo(falla_seed) -> None:
    """Segunda llamada con la novedad abierta: 409 y NINGUNA issue nueva."""

    crear, llamadas = _fake_issue(920_002)
    with patch.object(cloudfleet_service, "create_issue", new=crear):
        resp = _admin().post(URL, json={"fault_row_id": _ROW, "priority": "medium"})
    assert resp.status_code == 409, resp.text
    assert llamadas["n"] == 0, "no se puede llamar al proveedor para después rechazar"


@pytest.mark.asyncio
async def test_un_rechazo_del_proveedor_no_deja_rastro_local(falla_seed) -> None:
    """El orden CloudFleet-primero sólo se sostiene si el rollback funciona."""

    async with AsyncSessionLocal() as session:
        # Se libera el ciclo dejando la novedad como queda cuando el taller
        # TERMINA: cerrada y con su orden cerrada. `external_is_done` por sí
        # solo no libera nada —se activa al asignar la novedad a una orden, con
        # la orden todavía abierta—, así que sin el estado de la orden el
        # escalamiento seguiría bloqueado y el rechazo del proveedor, que es lo
        # que esta prueba ejercita, no llegaría a ocurrir.
        await session.execute(
            text(
                "UPDATE novedades SET external_is_done = true,"
                " external_work_order_status = 'closed' WHERE vehicle_code = :p"
            ),
            {"p": _PLACA},
        )
        await session.commit()
    novedades_antes = await _contar_novedades()

    async def _rechaza(payload, *, client=None):
        raise CloudfleetVehicleNotFoundError("placa desconocida")

    with patch.object(cloudfleet_service, "create_issue", new=_rechaza):
        resp = _admin().post(URL, json={"fault_row_id": _ROW})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "cloudfleet_vehicle_not_found"
    assert "placa desconocida" not in resp.text

    async def _cae(payload, *, client=None):
        raise CloudfleetError("500 del proveedor con texto interno")

    with patch.object(cloudfleet_service, "create_issue", new=_cae):
        resp = _admin().post(URL, json={"fault_row_id": _ROW})
    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"]["code"] == "cloudfleet_error"
    # El texto crudo del proveedor no sale al cliente (SEC-023). Se comprueba la
    # AUSENCIA de ese texto y no la forma exacta del diccionario: un mensaje
    # escrito para la persona que ve el error es legítimo y no debe romper esta
    # prueba; lo que nunca puede salir es lo que dijo el proveedor.
    assert "texto interno" not in resp.text
    assert "500 del proveedor" not in resp.text

    assert await _contar_novedades() == novedades_antes, "un rechazo no debe crear novedades"


async def _contar_novedades() -> int:
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(
                text("SELECT count(*) FROM novedades WHERE vehicle_code = :p"), {"p": _PLACA}
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_escalar_es_solo_del_rol_admin(falla_seed) -> None:
    """`navifault.edit` + `novedades.edit` no alcanzan: la issue es irreversible."""

    admin = _admin()
    role_code = f"escala_test_{uuid.uuid4().hex[:8]}"
    resp = admin.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": "Gestor sin admin",
            "permission_codes": ["navifault.edit", "novedades.edit"],
        },
    )
    assert resp.status_code == 201, resp.text
    email = f"escala-{uuid.uuid4().hex[:8]}@portalclientes.test"
    resp = admin.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": "EscalaPass123!",
            "full_name": "Gestor sin admin",
            "role_codes": [role_code],
            "fleet_ids": [],
        },
    )
    assert resp.status_code == 201, resp.text
    cliente = TestClient(app)
    assert (
        cliente.post(
            "/api/v1/auth/login", json={"email": email, "password": "EscalaPass123!"}
        ).status_code
        == 200
    )
    assert cliente.post(URL, json={"fault_row_id": _ROW}).status_code == 403


@pytest.mark.asyncio
async def test_una_falla_inexistente_es_404(falla_seed) -> None:
    resp = _admin().post(URL, json={"fault_row_id": f"{_PREFIJO}-no-existe"})
    assert resp.status_code == 404, resp.text
