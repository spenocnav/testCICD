"""El corpus Cummins es de Navitrans; la comunicación redactada es del cliente.

Hasta la revisión `a4b5c6d70053` bastaba `navifault.view` para pedir el
documento original, el resumen oficial y las candidatas de resolución — y
`navifault.view` lo tienen los roles de cliente (`admin_flota_cliente`,
`viewer`). Es decir: el cliente veía el manual de Cummins.

Estas pruebas fijan la frontera en las dos direcciones. La negativa importa
tanto como la positiva: si alguien devuelve esos endpoints a `navifault.view`
"para simplificar", el cliente vuelve a ver el manual y nada más lo detecta.

Y fijan la asimetría del 403: aquí un 403 es correcto, a diferencia del alcance
de flota, donde se responde 404 para no confirmar que un recurso existe. La
falla no es secreta —el cliente la está viendo en su pantalla—; lo reservado es
el contenido del manual, así que un 404 mentiría sobre algo que el usuario tiene
delante.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.v1.navifault import CORPUS_PERMISSION, build_client_description_response
from app.core.config import settings
from app.main import app

CORPUS_ENDPOINTS = (
    "/api/v1/navifault/original-document/{row}",
    "/api/v1/navifault/fault-candidates/{row}",
)


def _admin_client() -> TestClient:
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


def _client_role_client(admin: TestClient) -> TestClient:
    """Un usuario con EXACTAMENTE `navifault.view` y nada más.

    El rol se crea aquí en vez de reutilizar `viewer` a propósito. Los grants de
    los roles de sistema en la base viva **no coinciden** con los que producen
    las migraciones: `viewer` y `admin_flota_cliente` tienen hoy `navifault.view`
    concedido desde la matriz de roles, no desde una migración, así que una base
    recién migrada no lo trae. Anclar la prueba a `viewer` la haría depender de
    en qué entorno corre.
    """

    # `CODE_PATTERN` sólo admite [a-z0-9_]: un guion da 422.
    role_code = f"corpus_test_{uuid.uuid4().hex[:8]}"
    resp = admin.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": "Cliente de prueba del corpus",
            "permission_codes": ["navifault.view"],
        },
    )
    assert resp.status_code == 201, resp.text

    email = f"corpus-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "CorpusPass123!"
    resp = admin.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Usuario Cliente",
            "role_codes": [role_code],
            "fleet_ids": [],
        },
    )
    assert resp.status_code == 201, resp.text
    client = TestClient(app)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return client


def test_el_permiso_existe_y_solo_lo_tienen_los_roles_internos() -> None:
    admin = _admin_client()
    roles = admin.get("/api/v1/roles").json()
    by_code = {r["code"]: r for r in roles}

    def permisos(code: str) -> set[str]:
        role = by_code.get(code)
        assert role is not None, f"el rol {code} debe existir"
        return {p["code"] if isinstance(p, dict) else p for p in role["permissions"]}

    for interno in ("admin", "admin_flota_navitrans"):
        assert CORPUS_PERMISSION in permisos(interno), (
            f"{interno} es un rol de Navitrans y debe poder consultar el manual"
        )

    for cliente in ("admin_flota_cliente", "viewer"):
        assert CORPUS_PERMISSION not in permisos(cliente), (
            f"{cliente} es un rol de cliente y no debe ver el corpus Cummins"
        )

    # No se afirma que esos roles CONSERVEN `navifault.view`: sus grants vienen
    # de la matriz de roles y no de una migración, así que una base recién
    # migrada no los trae. Que el módulo siga abierto se prueba en
    # `test_el_cliente_conserva_la_comunicacion_redactada_para_el`, con un rol
    # construido para eso.


def test_un_rol_de_cliente_no_alcanza_el_documento_ni_las_candidatas() -> None:
    admin = _admin_client()
    cliente = _client_role_client(admin)
    row = f"corpus-{uuid.uuid4().hex[:8]}"

    for template in CORPUS_ENDPOINTS:
        resp = cliente.get(template.format(row=row))
        assert resp.status_code == 403, (
            f"{template} debe exigir {CORPUS_PERMISSION}; devolvió {resp.status_code}"
        )
        assert CORPUS_PERMISSION in resp.json()["detail"]


def test_el_cliente_conserva_la_comunicacion_redactada_para_el() -> None:
    """`client-description` sigue abierta a `navifault.view`.

    No se afirma un 2xx: sin datos sembrados el handler responde 404, 409 o 503
    según el estado del proveedor. Lo que se fija es que **no** sea 403, o sea
    que la compuerta de permiso no se movió junto con la del corpus.
    """

    admin = _admin_client()
    cliente = _client_role_client(admin)
    resp = cliente.post(f"/api/v1/navifault/client-description/corpus-{uuid.uuid4().hex[:8]}")
    assert resp.status_code != 403, resp.text


def _fake_row(**overrides: object) -> SimpleNamespace:
    base = {
        "descripcion_correo_cliente": "Texto redactado para enviar por correo.",
        "descripcion_plataforma_cliente": "Texto que se muestra en la plataforma.",
        "prompt_version": "v1",
        "model_version": "m1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


_FAKE_RESOLUTION = SimpleNamespace(
    page=SimpleNamespace(fault_page_id="es5488909", pub_id="pub", fault_code=2976, variant="")
)


def test_la_variante_de_correo_solo_viaja_a_usuarios_internos() -> None:
    """El borrador de correo no es lo que la plataforma le comunica al cliente.

    `descripcion_correo_cliente` está redactada para un envío por correo que
    todavía no existe. Mostrársela al cliente dentro del portal le entrega un
    texto pensado para otro medio, así que el campo se omite para quien no es
    interno. No es un permiso aparte: es la misma frontera del corpus.
    """

    interno = build_client_description_response(
        status="ready",
        row=_fake_row(),
        resolution=_FAKE_RESOLUTION,
        cached=True,
        include_email_variant=True,
    )
    assert interno.descripcion_correo_cliente == "Texto redactado para enviar por correo."
    assert interno.descripcion_plataforma_cliente == "Texto que se muestra en la plataforma."

    cliente = build_client_description_response(
        status="ready",
        row=_fake_row(),
        resolution=_FAKE_RESOLUTION,
        cached=True,
        include_email_variant=False,
    )
    assert cliente.descripcion_correo_cliente is None, (
        "el cliente no debe recibir el borrador de correo"
    )
    assert cliente.descripcion_plataforma_cliente == "Texto que se muestra en la plataforma.", (
        "omitir la variante de correo no puede llevarse por delante la de plataforma"
    )
def test_la_descripcion_tecnica_ya_no_existe() -> None:
    """No era una función del producto: se retiró en `b5c6d7e80054`.

    Su endpoint no tenía un solo consumidor en `apps/web` y su tabla estaba en
    cero mientras el worker seguía corriendo. Esta prueba impide que vuelva por
    la puerta de atrás —un `git revert` parcial, un merge— sin que nadie lo
    decida: lo que el producto genera es UNA comunicación, la redactada para el
    cliente, y vive en `navifault_client_description_service`.
    """

    admin = _admin_client()
    resp = admin.post("/api/v1/navifault/technical-description", json={})
    assert resp.status_code == 404, resp.text

    paths = app.openapi()["paths"]
    assert "/api/v1/navifault/technical-description" not in paths
    assert "/api/v1/navifault/client-description/{fault_row_id}" in paths, (
        "la comunicación al cliente NO se retira: es lo único que el producto redacta"
    )
