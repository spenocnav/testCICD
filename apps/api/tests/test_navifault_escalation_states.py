"""Los dos estados del escalamiento, y el modo de fallo que hay que impedir.

Escalar es cambiar el régimen de seguimiento cuando la falla NO se puede
resolver ya, así que un caso puede existir **sin ninguna gestión**. Eso obligó a
hacer nullable `last_managed_at`, y de ahí sale el peligro que fija este archivo:
que un `last_managed_at` NULL caiga en la rama `else -> managed` y una falla
escalada aparezca como gestionada. Es un modo de fallo silencioso — nadie
revisaría una falla que la bandeja da por atendida.

Se prueba contra la base y no por inspección de la expresión SQL: las ramas de
un `CASE` se leen bien y se evalúan distinto.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal
from app.models.analytics import AnalyticsBase, DimVehicle, FactFaultEvent
from app.models.fleet import Fleet
from app.models.master_data import GeotabDatabase, Vehicle
from app.models.navifault import NavifaultManagedFaultCase
from app.models.novedad import Novedad
from app.models.user import User
from app.services import navifault_management_service as svc

_PREFIJO = f"NFES{uuid.uuid4().hex[:8].upper()}"
_DB = f"nfes_{uuid.uuid4().hex[:8]}"
_DEV = f"bNFES{uuid.uuid4().hex[:6]}"
_VEH = f"nfes-veh-{uuid.uuid4().hex[:8]}"
_PLACA = f"{_PREFIJO[-6:]}E"
_CODIGO = 2976
_FMI = 3.0
_FUENTE = "SourceJ1939Id"

# Ancladas al reloj, no al calendario: el escalamiento ocurre "ahora" y las
# ocurrencias posteriores tienen que ser posteriores a ÉL. Con fechas fijas en
# el pasado el contador nunca se ejercitaría y la prueba pasaría sin probar.
_AHORA = datetime.now(UTC)
_BASE = _AHORA.replace(tzinfo=None) - timedelta(hours=6)
_ESCALADO_EN = _AHORA - timedelta(hours=3)


async def _insert_fault(session, row_id: str, cuando: datetime, recuento: int) -> None:
    await session.execute(
        text("""
        INSERT INTO analytics.fact_fault_event
            (row_id, vehicle_id, database_name, date_key, fecha, movil, fecha_de_falla,
             codigo_diagnostico, codigo_modo_de_falla, nombre_fuente_diagnostico,
             estado_de_falla, recuento_de_fallos, tipo_de_atencion,
             luz_de_parada_amber, luz_de_parada_roja, lampara_de_averia,
             lampara_de_advertencia, nombre_de_controlador, diagnostico, modo_de_falla)
        VALUES
            (:row_id, :veh, :db, :dk, :fecha, :placa, :cuando, :codigo, :fmi, :fuente,
             'Active', :recuento, 'Nivel 1 - Urgente', false, true, false, false,
             'Engine Control Module', 'Presion de aceite baja', 'Data valid but below normal')
        ON CONFLICT (row_id) DO NOTHING
    """),
        {
            "row_id": row_id,
            "veh": _VEH,
            "db": _DB,
            "dk": int(cuando.strftime("%Y%m%d")),
            "fecha": cuando.date(),
            "placa": _PLACA,
            "cuando": cuando,
            "codigo": _CODIGO,
            "fmi": _FMI,
            "fuente": _FUENTE,
            "recuento": recuento,
        },
    )


@pytest_asyncio.fixture(scope="module")
async def escalado_seed():
    """Un caso escalado y SIN gestión: `last_managed_at` en NULL.

    Se siembra directo el caso porque el endpoint de escalamiento aún no existe
    —es la capa siguiente— y lo que aquí se prueba es la máquina de estados.
    """

    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(code=_PREFIJO, name="Flota escalamiento", is_active=True)
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
            vehicle = Vehicle(
                plate=_PLACA,
                geotab_device_id=_DEV,
                geotab_customer_status="found",
                fleet_id=fleet.id,
                geotab_database_id=geotab_db.id,
                is_active=True,
            )
            actor = User(
                email=f"{_PREFIJO.lower()}@escala.local",
                full_name="Gestor que escaló",
                password_hash="x" * 60,
                is_active=True,
            )
            session.add_all([vehicle, actor])
            await session.flush()

            novedad = Novedad(
                vehicle_id=vehicle.id,
                fleet_id=fleet.id,
                vehicle_code=_PLACA,
                reported_at=_ESCALADO_EN,
                reported_by_id=23,
                priority="high",
                comment="Escalada desde Navifault",
                send_mail=False,
                cloudfleet_status="sent",
                cloudfleet_issue_number=910_001,
            )
            session.add(novedad)
            await session.flush()

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
                VALUES (:v, :d, 'Escalamiento', :db, true)
                ON CONFLICT (vehicle_id) DO UPDATE SET device_id = EXCLUDED.device_id
            """),
                {"v": _VEH, "d": _DEV, "db": _DB},
            )
            # Antes del escalamiento, y DESPUÉS: lo segundo es lo que el
            # contador tiene que ver.
            await _insert_fault(session, f"{_PREFIJO}-r0", _BASE, 2)
            await _insert_fault(
                session, f"{_PREFIJO}-r1", _ESCALADO_EN.replace(tzinfo=None) + timedelta(minutes=30), 5
            )

            context = await svc.get_management_context(session, f"{_PREFIJO}-r0")
            assert context is not None, "el seed no resolvió el contexto de la falla"
            session.add(
                NavifaultManagedFaultCase(
                    vehicle_id=vehicle.id,
                    signature_sha256=context.signature.sha256,
                    source=context.signature.source,
                    diagnostic_code=context.signature.diagnostic_code,
                    failure_mode=context.signature.failure_mode,
                    stop_amber=context.signature.stop_amber,
                    stop_red=context.signature.stop_red,
                    malfunction=context.signature.malfunction,
                    warning=context.signature.warning,
                    managed_through_at=None,
                    last_managed_at=None,
                    last_managed_by_user_id=None,
                    escalated_novedad_id=novedad.id,
                    escalated_at=_ESCALADO_EN,
                    escalated_by_user_id=actor.id,
                )
            )
            await session.commit()
            ids = (fleet.id, novedad.id)
        except Exception:
            await session.rollback()
            raise

    yield ids

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM analytics.fact_fault_event WHERE row_id LIKE :p"),
            {"p": f"{_PREFIJO}%"},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = :v"), {"v": _VEH}
        )
        # El caso se borra a propósito: es un caso SIN gestión, y el `downgrade`
        # de `e8f9a0b10057` se niega a restaurar el NOT NULL mientras exista uno.
        # La suite hace `downgrade base` por módulo, así que dejarlo la rompería
        # — y esa negativa es correcta, no un defecto de la migración.
        await session.execute(
            text(
                "DELETE FROM navifault.managed_fault_cases"
                " WHERE escalated_novedad_id IN (SELECT id FROM novedades WHERE vehicle_code = :p)"
            ),
            {"p": _PLACA},
        )
        await session.commit()


async def _caso(fleet_id, states):
    async with AsyncSessionLocal() as session:
        filas, _total = await svc.list_managed_fault_cases(
            session, fleet_ids=[fleet_id], states=states, limit=25, offset=0
        )
    return filas


@pytest.mark.asyncio
async def test_un_caso_sin_gestion_es_escalada_y_nunca_gestionada(escalado_seed) -> None:
    """El modo de fallo peligroso: `last_managed_at` NULL cayendo en `else`.

    Si la rama `escalada` se moviera de sitio, o si alguien "simplificara" el
    `CASE`, esta falla aparecería como gestionada y saldría de la bandeja activa
    sin que nadie la hubiera atendido.
    """

    fleet_id, _novedad_id = escalado_seed

    escaladas = await _caso(fleet_id, {"escalada"})
    assert len(escaladas) == 1, "el caso escalado debe salir en su propio segmento"
    assert escaladas[0]["status"] == "escalada"
    assert escaladas[0]["last_managed_at"] is None

    gestionadas = await _caso(fleet_id, {"managed"})
    assert gestionadas == [], "una falla escalada NO está gestionada"


@pytest.mark.asyncio
async def test_la_reincidencia_tras_escalar_es_un_dato_y_no_un_estado(escalado_seed) -> None:
    """Escalada que sigue disparándose: el estado no cambia, el contador sí.

    Sin este número, una falla escalada que suena todos los días se ve idéntica
    a una que se calló al escalarla — y ésa es justo la que hay que perseguir.
    """

    fleet_id, _novedad_id = escalado_seed
    filas = await _caso(fleet_id, {"escalada"})
    caso = filas[0]

    assert caso["status"] == "escalada", "la reincidencia NO es un estado propio"
    assert caso["occurrences_since_escalation"] == 5, (
        "debe pesar por recuento_de_fallos la ocurrencia posterior al escalamiento, "
        "y sólo ésa"
    )


@pytest.mark.asyncio
async def test_el_enlace_a_la_novedad_viaja_con_el_caso(escalado_seed) -> None:
    fleet_id, novedad_id = escalado_seed
    caso = (await _caso(fleet_id, {"escalada"}))[0]

    assert caso["escalated_novedad_id"] == novedad_id
    assert caso["escalated_issue_number"] == 910_001
    assert caso["escalated_by"] == "Gestor que escaló"
    # NULL es "nunca verificado contra CloudFleet", no "abierta".
    assert caso["escalated_external_is_done"] is None


@pytest.mark.asyncio
async def test_cerrar_la_novedad_no_gestiona_la_falla(escalado_seed) -> None:
    """El estado intermedio: el taller cerró, Navifault todavía no.

    Cerrar la issue NO gestiona la falla. El detalle de la orden sí publica el
    trabajo, su sistema y sus repuestos, pero una orden cubre varios trabajos y
    sólo el que el taller ató a la novedad es de esta falla; y el desenlace es
    una decisión de persona, no una derivación. La falla sigue sin gestionar y
    visible, esperando que alguien lo declare.
    """

    fleet_id, novedad_id = escalado_seed
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE novedades SET external_is_done = true,"
                " external_work_order_number = 5619,"
                # El estado de la ORDEN es lo que dice que el taller terminó;
                # `external_is_done` solo dice que la novedad llegó a una orden.
                " external_work_order_status = 'closed'"
                " WHERE id = :id"
            ),
            {"id": novedad_id},
        )
        await session.commit()

    try:
        pendientes = await _caso(fleet_id, {"pendiente_registro"})
        assert len(pendientes) == 1
        assert pendientes[0]["escalated_work_order_number"] == 5619
        assert pendientes[0]["last_managed_at"] is None, "sigue SIN gestionar"

        assert await _caso(fleet_id, {"managed"}) == [], "cerrar la OT no gestiona la falla"
        assert await _caso(fleet_id, {"escalada"}) == [], "ya no espera al taller"
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE novedades SET external_is_done = NULL,"
                    " external_work_order_number = NULL,"
                    " external_work_order_status = NULL WHERE id = :id"
                ),
                {"id": novedad_id},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_los_dos_caminos_coinciden_con_escalada(escalado_seed) -> None:
    """`{'escalada'}` entra acotado; añadir `pending` fuerza el barrido del hecho.

    Es el mismo invariante que ya protege a `repeated`: dos implementaciones del
    mismo estado y nada más las obliga a coincidir.
    """

    fleet_id, _novedad_id = escalado_seed
    acotado = await _caso(fleet_id, {"escalada"})
    barrido = await _caso(fleet_id, {"pending", "escalada"})

    assert len(acotado) == 1
    caso = acotado[0]
    equivalente = [row for row in barrido if row["case_id"] == caso["case_id"]]
    assert len(equivalente) == 1, "el barrido perdió el caso que el acotado encontró"

    for clave in caso:
        esperado, obtenido = caso[clave], equivalente[0][clave]
        if clave == "source":
            esperado = (esperado or "").strip()
            obtenido = (obtenido or "").strip()
        assert esperado == obtenido, f"los dos caminos difieren en {clave}"

@pytest.mark.asyncio
async def test_una_issue_borrada_no_bloquea_un_escalamiento_nuevo(escalado_seed) -> None:
    """CloudFleet permite borrar la issue desde su UI; el portal debe heredarlo.

    Sin esto la falla queda "Escalada" para siempre contra un número que ya no
    existe, y `active_escalation` impide volver a escalarla: nadie persigue la
    falla y nadie puede hacerlo.
    """

    from app.services.navifault_escalation_service import active_escalation

    fleet_id, novedad_id = escalado_seed

    async def _bloquea() -> bool:
        async with AsyncSessionLocal() as session:
            caso = (
                await session.execute(
                    select(NavifaultManagedFaultCase).where(
                        NavifaultManagedFaultCase.escalated_novedad_id
                        == uuid.UUID(str(novedad_id))
                    )
                )
            ).scalar_one()
            return await active_escalation(session, case=caso) is not None

    assert await _bloquea() is True, "una issue viva sí bloquea"

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("UPDATE novedades SET external_deleted_at = now() WHERE id = :id"),
            {"id": novedad_id},
        )
        await session.commit()
    try:
        assert await _bloquea() is False, "una issue borrada no puede seguir bloqueando"

        # Y deja de aparecer como escalada: la insignia no puede decir "Escalada"
        # mientras el guard permite volver a escalar.
        assert await _caso(fleet_id, {"escalada"}) == []
        caso = (await _caso(fleet_id, {"pending"}))[0]
        assert caso["escalated_deleted_at"] is not None, "el borrado se publica"
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("UPDATE novedades SET external_deleted_at = NULL WHERE id = :id"),
                {"id": novedad_id},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_el_trabajo_asociado_viaja_con_el_caso(escalado_seed) -> None:
    """Sin el trabajo, la orden no dice cuál de sus trabajos es de esta falla."""

    fleet_id, novedad_id = escalado_seed
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "UPDATE novedades SET associated_labor_id = 88,"
                " associated_labor_name = 'Cambio sensor NOx' WHERE id = :id"
            ),
            {"id": novedad_id},
        )
        await session.commit()
    try:
        caso = (await _caso(fleet_id, {"escalada"}))[0]
        assert caso["escalated_labor_id"] == 88
        assert caso["escalated_labor_name"] == "Cambio sensor NOx"

        # Los dos caminos publican el mismo contrato o dejan de coincidir.
        barrido = await _caso(fleet_id, {"pending", "escalada"})
        gemelo = next(r for r in barrido if r["case_id"] == caso["case_id"])
        assert gemelo["escalated_labor_id"] == 88
        assert gemelo["escalated_labor_name"] == "Cambio sensor NOx"
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE novedades SET associated_labor_id = NULL,"
                    " associated_labor_name = NULL WHERE id = :id"
                ),
                {"id": novedad_id},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_la_orden_abierta_no_es_pendiente_de_registro(escalado_seed) -> None:
    """`isDone` se activa al ASIGNAR la novedad a una orden, no al atenderla.

    Comprobado contra el proveedor el 2026-09-05 con la novedad #706: quedó
    `isDone: true` con la orden 5733 todavía `opened` y el vehículo sin tocar.
    Preguntar sólo por ese campo pedía declarar un desenlace que no había
    ocurrido, y además desbloqueaba el re-escalamiento mientras el taller
    trabajaba.
    """

    from app.services.navifault_escalation_service import active_escalation

    fleet_id, novedad_id = escalado_seed

    async def _estado(orden: str | None) -> tuple[list[str], bool]:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE novedades SET external_is_done = true,"
                    " external_work_order_number = 5733,"
                    " external_work_order_status = :st WHERE id = :id"
                ),
                {"id": novedad_id, "st": orden},
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            caso = (
                await session.execute(
                    select(NavifaultManagedFaultCase).where(
                        NavifaultManagedFaultCase.escalated_novedad_id
                        == uuid.UUID(str(novedad_id))
                    )
                )
            ).scalar_one()
            bloquea = await active_escalation(session, case=caso) is not None
        estados = [
            e
            for e in ("escalada", "pendiente_registro")
            if await _caso(fleet_id, {e})
        ]
        return estados, bloquea

    try:
        # El taller la tiene y no ha terminado: sigue escalada y sigue bloqueada.
        assert await _estado("opened") == (["escalada"], True)
        # Cierre técnico: el trabajo se hizo, falta declarar el desenlace.
        assert await _estado("onTechnicalCompletion") == (["pendiente_registro"], False)
        assert await _estado("closed") == (["pendiente_registro"], False)
        # Sin estado de orden conocido no se puede afirmar que terminó.
        assert await _estado(None) == (["escalada"], True)
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text(
                    "UPDATE novedades SET external_is_done = NULL,"
                    " external_work_order_number = NULL,"
                    " external_work_order_status = NULL WHERE id = :id"
                ),
                {"id": novedad_id},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_una_novedad_borrada_deja_de_gobernar_el_estado(escalado_seed) -> None:
    """La insignia no puede decir "Escalada" mientras el guard deja re-escalar."""

    fleet_id, novedad_id = escalado_seed
    assert await _caso(fleet_id, {"escalada"}), "de partida está escalada"

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("UPDATE novedades SET external_deleted_at = now() WHERE id = :id"),
            {"id": novedad_id},
        )
        await session.commit()
    try:
        assert await _caso(fleet_id, {"escalada"}) == [], "ya no gobierna el estado"
        vuelta = await _caso(fleet_id, {"pending"})
        assert len(vuelta) == 1, "la falla vuelve al ciclo normal"
        # El enlace se conserva: la falla SÍ fue escalada y eso es historia.
        assert vuelta[0]["escalated_novedad_id"] is None or vuelta[0]["escalated_deleted_at"]
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("UPDATE novedades SET external_deleted_at = NULL WHERE id = :id"),
                {"id": novedad_id},
            )
            await session.commit()


@pytest.mark.asyncio
async def test_una_falla_gestionada_solo_se_reescala_si_vuelve(escalado_seed) -> None:
    """Escalar es perseguir algo que sigue pasando.

    Una falla atendida que se calló no tiene nada que perseguir, y la novedad
    nueva sólo le duplicaría el trabajo al taller — sin vuelta atrás, porque
    CloudFleet responde 405 a DELETE. En cuanto vuelve a aparecer se abre sola:
    el listado la llama `repeated` dentro de los 30 días y abre ciclo nuevo como
    `pending` después, y las dos son escalables.

    El modo de fallo que fija: la guarda vive detrás de un `managed_through_at
    IS NOT NULL` que corta antes de mirar la novedad, así que sin esta prueba
    volver a abrir el re-escalamiento sobre una falla gestionada pasaría sin que
    nada lo detectara.
    """

    fleet_id, novedad_id = escalado_seed
    del fleet_id
    reaparicion = f"{_PREFIJO}-r2"
    checkpoint = datetime.now(UTC)

    async def _caso_y_contexto(session):
        caso = (
            await session.execute(
                select(NavifaultManagedFaultCase).where(
                    NavifaultManagedFaultCase.escalated_novedad_id == uuid.UUID(str(novedad_id))
                )
            )
        ).scalar_one()
        contexto = await svc.get_management_context(session, f"{_PREFIJO}-r0")
        assert contexto is not None
        return caso, contexto

    try:
        # Gestionada AHORA: toda ocurrencia del seed es anterior al checkpoint.
        async with AsyncSessionLocal() as session:
            caso, contexto = await _caso_y_contexto(session)
            caso.managed_through_at = checkpoint
            caso.last_managed_at = checkpoint
            await session.commit()

        async with AsyncSessionLocal() as session:
            caso, contexto = await _caso_y_contexto(session)
            assert (
                await svc.has_reappeared_since_management(session, context=contexto, case=caso)
                is False
            ), "sin ocurrencias posteriores la falla está gestionada y no se re-escala"

        # La falla vuelve a dispararse después de la gestión.
        async with AsyncSessionLocal() as session:
            await _insert_fault(
                session,
                reaparicion,
                checkpoint.replace(tzinfo=None) + timedelta(minutes=5),
                1,
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            caso, contexto = await _caso_y_contexto(session)
            assert (
                await svc.has_reappeared_since_management(session, context=contexto, case=caso)
                is True
            ), "una ocurrencia posterior al checkpoint reabre el escalamiento"
    finally:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM analytics.fact_fault_event WHERE row_id = :r"),
                {"r": reaparicion},
            )
            caso, _ctx = await _caso_y_contexto(session)
            caso.managed_through_at = None
            caso.last_managed_at = None
            await session.commit()
