"""La bandeja de gestión: dos caminos que tienen que decir lo mismo.

`list_managed_fault_cases` se resolvía con UNA consulta que partía de
`analytics.fact_fault_event`, agrupaba el histórico completo por firma y sólo
después descartaba en un `HAVING` los estados que no se habían pedido. Medido
contra `portal_clientes` (1.077.908 filas de hecho, 0 casos gestionados) costaba
20,2 s por página, con `statement_timeout` en 30 s.

Ahora hay dos caminos, porque los estados no son simétricos:

- `managed` y `repeated` EXIGEN que el caso exista —en cuanto `case_id` es NULL
  el estado es `pending`—, así que se entra por `managed_fault_cases`, que está
  acotada, y cada caso resuelve sus ocurrencias con un LATERAL. 20,2 s -> 0,5 s;
- `pending` es, por definición, una firma SIN caso: hay que recorrer el hecho.

La equivalencia se verificó una vez contra la base real sembrando casos en una
transacción revertida (14 casos, dos páginas, los tres conjuntos de estados:
filas, totales, orden y campo por campo idénticos). Este archivo es la guarda
permanente de eso, y su prueba central es `test_los_dos_caminos_coinciden`: dos
implementaciones del mismo estado divergen en silencio, porque ningún tipo ni
contrato las obliga a coincidir.

Convenciones y forma del seed tomadas de `test_calificacion_penalizaciones.py`.
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
from app.models.user import User
from app.schemas.navifault import NavifaultManagedFaultCaseRead
from app.services import navifault_management_service as svc

_PREFIJO = f"NFMC{uuid.uuid4().hex[:8].upper()}"
_DB = f"nfmc_{uuid.uuid4().hex[:8]}"
_DEV = f"bNFMC{uuid.uuid4().hex[:6]}"
_VEH = f"nfmc-veh-{uuid.uuid4().hex[:8]}"
_PLACA = f"{_PREFIJO[-6:]}M"

_DB_AJENA = f"nfmcx_{uuid.uuid4().hex[:8]}"
_DEV_AJENO = f"bNFMCX{uuid.uuid4().hex[:6]}"
_VEH_AJENO = f"nfmc-vehx-{uuid.uuid4().hex[:8]}"
_PLACA_AJENA = f"{_PREFIJO[-6:]}X"

_FUENTE = "SourceJ1939Id"
_CODIGO = 2976
_FMI = 3.0
# Segunda firma del MISMO vehículo: un caso no debe arrastrar las ocurrencias
# de otra falla del mismo móvil.
_CODIGO_OTRA = 1694

# Las fechas se ancoran al reloj y NO a una constante del calendario. Una
# reaparición tiene que ser posterior a `last_managed_at`, que es la hora real
# en que alguien gestionó; con hechos fijos en el pasado nunca hay reaparición
# posible y `repeated` quedaría sin probar. Los márgenes son holgados (±12 h) a
# propósito: `fecha_de_falla` es un timestamp SIN zona y el servidor lo
# interpreta con su `TimeZone`, así que un desfase de horas no debe decidir el
# resultado de la prueba.
_BASE = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
_FILAS = [
    (f"{_PREFIJO}-r1", _CODIGO, _BASE, 2),
    (f"{_PREFIJO}-r2", _CODIGO, _BASE + timedelta(hours=4), 3),
    (f"{_PREFIJO}-r3", _CODIGO, _BASE + timedelta(hours=8), 5),
    (f"{_PREFIJO}-otra", _CODIGO_OTRA, _BASE + timedelta(hours=1), 7),
]
# Doce horas en el futuro: después de la gestión (que ocurre "ahora") y muy
# dentro de la ventana de reincidencia de 30 días.
_REAPARICION_EN = _BASE + timedelta(hours=36)
_OCURRENCIAS_FIRMA = 3
_REPORTADAS_FIRMA = 2 + 3 + 5
_ROW_REAPARICION = f"{_PREFIJO}-r4"


async def _insert_fault(session, row_id: str, codigo: int, cuando: datetime, recuento: int) -> None:
    await session.execute(
        text("""
        INSERT INTO analytics.fact_fault_event
            (row_id, vehicle_id, database_name, date_key, fecha, movil, fecha_de_falla,
             codigo_diagnostico, codigo_modo_de_falla, nombre_fuente_diagnostico,
             estado_de_falla, recuento_de_fallos, tipo_de_atencion,
             luz_de_parada_amber, luz_de_parada_roja, lampara_de_averia,
             lampara_de_advertencia, nombre_de_controlador, diagnostico, modo_de_falla)
        VALUES
            (:row_id, :veh, :db, :dk, :fecha, :placa, :cuando,
             :codigo, :fmi, :fuente,
             'Active', :recuento, 'Nivel 1 - Urgente',
             false, true, false,
             false, 'Engine Control Module', 'Presion de aceite baja', 'Data valid but below normal')
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
            "codigo": codigo,
            "fmi": _FMI,
            "fuente": _FUENTE,
            "recuento": recuento,
        },
    )


@pytest_asyncio.fixture(scope="module")
async def bandeja_seed():
    """Un vehículo con tres ocurrencias de la misma firma, más ruido a propósito.

    El ruido importa: otra firma del mismo vehículo y otro vehículo en OTRA
    flota. Sin ellos, una consulta que se olvide de la firma o del alcance
    pasaría la prueba igual.
    """
    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(code=_PREFIJO, name="Flota bandeja Navifault", is_active=True)
            fleet_ajena = Fleet(code=f"{_PREFIJO}X", name="Flota ajena", is_active=True)
            session.add_all([fleet, fleet_ajena])
            await session.flush()

            geotab_db = GeotabDatabase(
                fleet_id=fleet.id,
                database_name=_DB,
                database_key=_DB,
                connection_type="geotab",
                is_active=True,
            )
            geotab_db_ajena = GeotabDatabase(
                fleet_id=fleet_ajena.id,
                database_name=_DB_AJENA,
                database_key=_DB_AJENA,
                connection_type="geotab",
                is_active=True,
            )
            session.add_all([geotab_db, geotab_db_ajena])
            await session.flush()

            session.add_all(
                [
                    Vehicle(
                        plate=_PLACA,
                        geotab_device_id=_DEV,
                        geotab_customer_status="found",
                        fleet_id=fleet.id,
                        geotab_database_id=geotab_db.id,
                        is_active=True,
                    ),
                    Vehicle(
                        plate=_PLACA_AJENA,
                        geotab_device_id=_DEV_AJENO,
                        geotab_customer_status="found",
                        fleet_id=fleet_ajena.id,
                        geotab_database_id=geotab_db_ajena.id,
                        is_active=True,
                    ),
                ]
            )

            actor = User(
                email=f"{_PREFIJO.lower()}@bandeja.local",
                full_name="Gestor de bandeja",
                password_hash="x" * 60,
                is_active=True,
            )
            session.add(actor)
            await session.flush()

            await session.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
            # Alembic no gobierna `analytics`: las tablas las crea el loader del
            # ETL. Se crean desde los MISMOS modelos que la aplicación consulta.
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
                VALUES (:v, :d, 'Bandeja', :db, true), (:vx, :dx, 'Bandeja ajena', :dbx, true)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    database_name = EXCLUDED.database_name
            """),
                {
                    "v": _VEH,
                    "d": _DEV,
                    "db": _DB,
                    "vx": _VEH_AJENO,
                    "dx": _DEV_AJENO,
                    "dbx": _DB_AJENA,
                },
            )
            for row_id, codigo, cuando, recuento in _FILAS:
                await _insert_fault(session, row_id, codigo, cuando, recuento)

            await session.commit()
            ids = (fleet.id, fleet_ajena.id, actor.id)
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
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id IN (:v, :vx)"),
            {"v": _VEH, "vx": _VEH_AJENO},
        )
        await session.commit()


async def _gestionar(session, fault_row_id: str, actor_id: uuid.UUID, note: str | None = None):
    actor = await session.get(User, actor_id)
    context = await svc.get_management_context(session, fault_row_id)
    assert context is not None, "el seed no resolvió el contexto de la falla"
    managed_case, _action = await svc.mark_fault_managed(
        session, context=context, actor=actor, note=note
    )
    await session.commit()
    return managed_case


@pytest.mark.asyncio
async def test_sin_gestion_la_bandeja_esta_vacia(bandeja_seed) -> None:
    """El camino acotado no puede inventar casos: sin filas, no hay bandeja."""
    fleet_id, _ajena, _actor = bandeja_seed
    async with AsyncSessionLocal() as session:
        rows, total = await svc.list_managed_fault_cases(
            session, fleet_ids=[fleet_id], states={"managed"}, limit=25, offset=0
        )
    assert (rows, total) == ([], 0)


@pytest.mark.asyncio
async def test_pendiente_sigue_viendo_las_firmas_sin_caso(bandeja_seed) -> None:
    """`pending` NO puede resolverse con un join interno.

    Es el contrapeso del cambio: una firma pendiente es justamente la que no
    tiene caso, así que si alguien "unifica" los dos caminos con el join
    acotado, esta prueba se cae en vez de que la bandeja quede muda.
    """
    fleet_id, _ajena, _actor = bandeja_seed
    async with AsyncSessionLocal() as session:
        rows, total = await svc.list_managed_fault_cases(
            session, fleet_ids=[fleet_id], states={"pending"}, limit=25, offset=0
        )
    codigos = {row["diagnostic_code"] for row in rows}
    assert total >= 2, "las dos firmas del vehículo deberían estar pendientes"
    assert {_CODIGO, _CODIGO_OTRA} <= codigos
    assert all(row["case_id"] is None for row in rows)
    assert all(row["status"] == "pending" for row in rows)


@pytest.mark.asyncio
async def test_una_falla_gestionada_entra_con_sus_agregados(bandeja_seed) -> None:
    """La bandeja agrega SÓLO las ocurrencias de la firma gestionada.

    `reported_occurrences` pesa por `recuento_de_fallos`, no cuenta filas: son
    10 ocurrencias en 3 registros. Si arrastrara la otra firma del vehículo
    saldrían 17 en 4.
    """
    fleet_id, _ajena, actor_id = bandeja_seed
    async with AsyncSessionLocal() as session:
        await _gestionar(session, f"{_PREFIJO}-r3", actor_id, note="cambio de sensor")

    async with AsyncSessionLocal() as session:
        rows, total = await svc.list_managed_fault_cases(
            session, fleet_ids=[fleet_id], states={"managed"}, limit=25, offset=0
        )
    assert total == 1
    row = rows[0]
    assert row["status"] == "managed"
    assert row["plate"] == _PLACA
    assert row["diagnostic_code"] == _CODIGO
    assert row["analytics_records"] == _OCURRENCIAS_FIRMA
    assert row["reported_occurrences"] == _REPORTADAS_FIRMA
    assert row["repeated_occurrences"] == 0
    assert row["last_managed_by"] == "Gestor de bandeja"
    assert row["last_note"] == "cambio de sensor"
    assert row["sample_fault_row_id"].startswith(_PREFIJO)

    # El router valida cada fila con este schema antes de responder: si el
    # camino acotado dejara de publicar una clave, el 500 aparecería en la
    # pantalla y no acá. `_case_row` es compartido justamente para eso.
    publicado = NavifaultManagedFaultCaseRead.model_validate(row)
    assert publicado.status == "managed"
    assert publicado.plate == _PLACA
    assert publicado.reported_occurrences == _REPORTADAS_FIRMA


@pytest.mark.asyncio
async def test_la_bandeja_respeta_el_alcance_de_flota(bandeja_seed) -> None:
    """Un caso de otra flota no se filtra: es la prueba negativa del alcance."""
    _fleet_id, ajena_id, _actor = bandeja_seed
    async with AsyncSessionLocal() as session:
        rows, total = await svc.list_managed_fault_cases(
            session, fleet_ids=[ajena_id], states={"managed", "repeated"}, limit=25, offset=0
        )
    assert (rows, total) == ([], 0)


@pytest.mark.asyncio
async def test_una_ocurrencia_posterior_la_vuelve_repetida(bandeja_seed) -> None:
    """Pasar el checkpoint la mueve de `managed` a `repeated`, con su conteo."""
    fleet_id, _ajena, _actor = bandeja_seed
    async with AsyncSessionLocal() as session:
        await _insert_fault(session, _ROW_REAPARICION, _CODIGO, _REAPARICION_EN, 4)
        await session.commit()

    async with AsyncSessionLocal() as session:
        repetidas, total_rep = await svc.list_managed_fault_cases(
            session, fleet_ids=[fleet_id], states={"repeated"}, limit=25, offset=0
        )
        _gestionadas, total_ges = await svc.list_managed_fault_cases(
            session, fleet_ids=[fleet_id], states={"managed"}, limit=25, offset=0
        )
    assert total_rep == 1
    assert total_ges == 0, "una falla que reapareció ya no está gestionada"
    assert repetidas[0]["status"] == "repeated"
    assert repetidas[0]["repeated_occurrences"] == 4
    assert repetidas[0]["reported_occurrences"] == _REPORTADAS_FIRMA + 4


@pytest.mark.asyncio
async def test_los_dos_caminos_coinciden(bandeja_seed) -> None:
    """El invariante del archivo: el camino acotado y el que recorre el hecho.

    `{'repeated'}` entra por `managed_fault_cases`; añadir `'pending'` fuerza el
    barrido del hecho. Los dos tienen que devolver el mismo caso con los mismos
    agregados: son dos implementaciones del mismo estado y nada más las obliga
    a coincidir.
    """
    fleet_id, _ajena, _actor = bandeja_seed
    async with AsyncSessionLocal() as session:
        acotado, _ = await svc.list_managed_fault_cases(
            session, fleet_ids=[fleet_id], states={"repeated"}, limit=25, offset=0
        )
        barrido, _ = await svc.list_managed_fault_cases(
            session,
            fleet_ids=[fleet_id],
            states={"pending", "repeated"},
            limit=25,
            offset=0,
        )

    assert len(acotado) == 1
    caso = acotado[0]
    equivalente = [row for row in barrido if row["case_id"] == caso["case_id"]]
    assert len(equivalente) == 1, "el barrido perdió el caso que el acotado encontró"

    # `source` se compara normalizado: el camino acotado publica el valor del
    # caso y el barrido el crudo del hecho. Cualquier otro campo debe ser igual.
    for clave in caso:
        esperado, obtenido = caso[clave], equivalente[0][clave]
        if clave == "source":
            esperado = (esperado or "").strip()
            obtenido = (obtenido or "").strip()
        assert esperado == obtenido, f"los dos caminos difieren en {clave}"


@pytest.mark.asyncio
async def test_un_caso_sin_ocurrencias_en_analytics_no_aparece(bandeja_seed) -> None:
    """Sin ocurrencias el caso NO entra, y por eso los dos caminos coinciden.

    Un agregado sin GROUP BY devuelve una fila aunque no haya nada que agregar,
    así que el camino acotado publicaría un caso con 0 registros mientras el
    barrido lo omitiría por no tener grupo. Lo evita `analytics_records > 0`.
    """
    fleet_id, _ajena, _actor = bandeja_seed
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM analytics.fact_fault_event WHERE row_id LIKE :p"),
            {"p": f"{_PREFIJO}-r%"},
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        caso_sigue = await session.scalar(
            select(NavifaultManagedFaultCase).limit(1)
        )
        rows, total = await svc.list_managed_fault_cases(
            session,
            fleet_ids=[fleet_id],
            states={"managed", "repeated"},
            limit=25,
            offset=0,
        )
    assert caso_sigue is not None, "el caso privado no se borra al perder el hecho"
    assert (rows, total) == ([], 0)
