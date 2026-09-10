"""Calibración de la calificación por flota: validación, persistencia y scope.

Cuatro bloques, en orden de dependencia creciente:

- **A. Validación** — sin base. Es la red de seguridad del resto: si
  `validate_config` deja pasar una calibración absurda, todo lo demás mide
  puntajes que no significan nada. Corre siempre.
- **B. Persistencia** — append-only, la última fila por flota gana, y la regla
  de qué pasa cuando el alcance cruza flotas con calibraciones distintas.
- **C. El puntaje** — la invariante que protege a los clientes que no
  calibraron nada, y que un cambio de calibración mueve el puntaje de SU flota
  y de ninguna otra.
- **D. Autorización** — leer exige `reportes.view`, escribir exige
  `reportes.edit`, y siempre acceso a esa flota.

`get_calificacion` NO recibe la calibración como parámetro: la resuelve una vez
desde el alcance de flotas. Por eso el bloque C calibra escribiendo en la base
—el camino real— en lugar de inyectar un objeto de configuración.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.analytics import (
    AnalyticsBase,
    DimRule,
    DimVehicle,
    FactCombustibleDaily,
    FactHabitoEvent,
)
from app.models.fleet import Fleet
from app.models.master_data import GeotabDatabase, Vehicle
from app.models.user import User
from app.services import analytics_service
from app.services import calificacion_config_service as ccs
from app.services.calificacion_config import (
    DEFAULT_CALIFICACION_CONFIG,
    EDITABLE_FIELDS,
    MAX_QHS_EVENT_WEIGHTS,
    CalificacionConfig,
    CalificacionConfigError,
    config_from_mapping,
    config_to_mapping,
    validate_config,
)

_CONFIG_URL = "/api/v1/reportes/calificacion/config"
_HISTORY_URL = f"{_CONFIG_URL}/history"


# ===========================================================================
# A. Validación — sin base
# ===========================================================================


def test_default_config_es_valido() -> None:
    """La calibración vigente tiene que pasar su propio validador.

    Si esto falla, `DEFAULT_CALIFICACION_CONFIG` y `validate_config` se
    separaron y ninguna flota sin calibración propia puede leerse.
    """
    validate_config(DEFAULT_CALIFICACION_CONFIG)


_INVALIDAS: tuple[tuple[str, dict[str, Any], str], ...] = (
    # (id, overrides, fragmento esperado del mensaje)
    ("qhs_qho_no_suman_1", {"peso_qhs": 0.6, "peso_qho": 0.5}, "sumar 1"),
    ("qhs_qho_suman_menos", {"peso_qhs": 0.2, "peso_qho": 0.2}, "sumar 1"),
    (
        "peso_fuera_de_rango_pero_suma_1",
        {"peso_qhs": 1.5, "peso_qho": -0.5},
        "entre 0 y 1",
    ),
    (
        "componentes_qho_no_suman_1",
        {"peso_eficiente": 0.6, "peso_ralenti": 0.3, "peso_exceso_rpm": 0.2},
        "deben sumar 1",
    ),
    ("efic_target_en_cero", {"efic_target": 0.0}, "rango eficiente"),
    ("efic_target_mayor_que_1", {"efic_target": 1.5}, "rango eficiente"),
    ("ralenti_target_igual_al_max", {"ralenti_target": 0.3, "ralenti_max": 0.3}, "ralentí"),
    ("ralenti_target_mayor_que_max", {"ralenti_target": 0.4, "ralenti_max": 0.2}, "ralentí"),
    (
        "umbral_en_riesgo_igual_a_cumple",
        {"umbral_en_riesgo": 85.0, "umbral_cumple": 85.0},
        "umbrales",
    ),
    ("umbral_en_riesgo_mayor", {"umbral_en_riesgo": 90.0, "umbral_cumple": 85.0}, "umbrales"),
    ("umbral_cumple_sobre_100", {"umbral_cumple": 120.0}, "umbrales"),
    ("umbral_en_riesgo_negativo", {"umbral_en_riesgo": -1.0}, "umbrales"),
    ("eventos_cap_en_cero", {"eventos_cap": 0.0}, "eventos_cap"),
    ("eventos_cap_negativo", {"eventos_cap": -3.0}, "eventos_cap"),
    ("rpm_cap_comercial_en_cero", {"rpm_cap_comercial_1000km": 0.0}, "rpm_cap_comercial_1000km"),
    ("rpm_cap_vocacional_negativo", {"rpm_cap_vocacional_100h": -5.0}, "rpm_cap_vocacional_100h"),
    ("rpm_high_weight_menor_que_1", {"rpm_high_weight": 0.5}, "gobernada"),
    (
        "peso_de_evento_negativo",
        {"qhs_event_weights": {"frenadas bruscas": -1.0}},
        "no puede ser negativa",
    ),
    ("nombre_de_evento_vacio", {"qhs_event_weights": {"": 1.0}}, "vacío"),
    ("nombre_de_evento_en_blanco", {"qhs_event_weights": {"   ": 1.0}}, "vacío"),
    (
        "demasiados_tipos_de_evento",
        {"qhs_event_weights": {f"evento {i}": 1.0 for i in range(MAX_QHS_EVENT_WEIGHTS + 1)}},
        "tipos de evento",
    ),
    (
        "peso_por_defecto_negativo",
        {"qhs_default_weight": -0.5},
        "no puede ser negativa",
    ),
)


@pytest.mark.parametrize(
    ("overrides", "fragmento"),
    [pytest.param(o, f, id=i) for i, o, f in _INVALIDAS],
)
def test_validate_config_rechaza(overrides: dict[str, Any], fragmento: str) -> None:
    """Cada regla cruzada rechaza, y el mensaje es el que verá el usuario.

    Se comprueba el texto y no sólo el tipo de excepción porque este mensaje
    viaja tal cual al 400 del endpoint: es la única explicación que recibe
    quien está calibrando.
    """
    config = DEFAULT_CALIFICACION_CONFIG.with_overrides(**overrides)
    with pytest.raises(CalificacionConfigError) as exc:
        validate_config(config)
    mensaje = str(exc.value)
    assert fragmento in mensaje, mensaje
    # En español y con contenido: ni un `assert` desnudo ni un texto en inglés.
    assert mensaje.strip().endswith("."), mensaje
    assert not {"must", "should", "invalid", "error:"} & set(mensaje.lower().split()), mensaje


def test_config_from_mapping_tambien_valida() -> None:
    """El camino de lectura no puede ser más permisivo que el de escritura.

    Una fila de la base entra por `config_from_mapping`; si ahí no se valida,
    una calibración inválida escrita por fuera del servicio produciría puntajes
    silenciosamente absurdos en vez de fallar.
    """
    with pytest.raises(CalificacionConfigError):
        config_from_mapping({"peso_qhs": 0.9, "peso_qho": 0.9})


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(DEFAULT_CALIFICACION_CONFIG, id="default"),
        pytest.param(
            DEFAULT_CALIFICACION_CONFIG.with_overrides(
                peso_qhs=0.4,
                peso_qho=0.6,
                peso_eficiente=0.4,
                peso_ralenti=0.4,
                peso_exceso_rpm=0.2,
                efic_target=0.55,
                ralenti_target=0.05,
                ralenti_max=0.45,
                eventos_cap=8.0,
                rpm_cap_comercial_1000km=90.0,
                rpm_cap_vocacional_100h=800.0,
                rpm_high_weight=3.0,
                umbral_en_riesgo=60.0,
                umbral_cumple=80.0,
                qhs_event_weights={"frenadas bruscas": 2.5, "giros bruscos": 0.75},
                qhs_default_weight=0.5,
            ),
            id="modificada",
        ),
    ],
)
def test_roundtrip_mapping(config: CalificacionConfig) -> None:
    """Serializar y volver a construir devuelve la MISMA calibración.

    Es el viaje real: la fila se escribe con `config_to_mapping` y se lee con
    `config_from_mapping`. Un campo que se pierda en el trayecto cambia el
    puntaje sin que nadie lo haya decidido.
    """
    assert config_from_mapping(config_to_mapping(config)) == config


def test_clave_ausente_o_none_cae_al_default_no_a_cero() -> None:
    """Una fila escrita antes de añadir un campo sigue siendo legible.

    Todos los parámetros son nullable a propósito. Si un `None` cayera a 0, un
    `eventos_cap` ausente dejaría el QHS en 0 para cualquier vehículo con un
    solo evento: exactamente el modo de falla que la nullabilidad evita.
    """
    assert config_from_mapping({}) == DEFAULT_CALIFICACION_CONFIG

    solo_nones = dict.fromkeys(EDITABLE_FIELDS)
    assert config_from_mapping(solo_nones) == DEFAULT_CALIFICACION_CONFIG

    parcial = config_from_mapping({"eventos_cap": None, "efic_target": 0.6})
    assert parcial.eventos_cap == DEFAULT_CALIFICACION_CONFIG.eventos_cap
    assert parcial.eventos_cap != 0
    assert parcial.efic_target == pytest.approx(0.6)
    # Una clave desconocida no se cuela como atributo ni rompe la lectura.
    assert config_from_mapping({"campo_inexistente": 1.0}) == DEFAULT_CALIFICACION_CONFIG


def test_claves_de_evento_se_normalizan() -> None:
    """`  Frenadas Bruscas  ` y `frenadas bruscas` son el mismo tipo.

    El nombre llega de la UI y del ETL con mayúsculas y espacios distintos; si
    no se normaliza, calibrar un tipo no afecta a sus propios eventos.
    """
    config = config_from_mapping(
        {"qhs_event_weights": {"  Frenadas Bruscas  ": 2.0, "GIROS BRUSCOS": 0.5}}
    )
    assert dict(config.qhs_event_weights) == {"frenadas bruscas": 2.0, "giros bruscos": 0.5}
    assert config.event_weight("Frenadas Bruscas") == pytest.approx(2.0)
    assert config.event_weight(" giros BRUSCOS ") == pytest.approx(0.5)
    # Un tipo no calibrado cuenta con el peso por defecto, no con 0.
    assert config.event_weight("baches o resaltos fuertes") == pytest.approx(
        config.qhs_default_weight
    )
    assert config.event_weight(None) == pytest.approx(config.qhs_default_weight)


# ===========================================================================
# Fixtures compartidos por B, C y D
# ===========================================================================

_PREFIJO = f"CALCFG-{uuid.uuid4().hex[:8].upper()}"
_DB_A = f"calcfg_a_{uuid.uuid4().hex[:8]}"
_DB_B = f"calcfg_b_{uuid.uuid4().hex[:8]}"
_VEH_A = f"CALCFG-VEH-A-{uuid.uuid4().hex[:8]}"
_VEH_B = f"CALCFG-VEH-B-{uuid.uuid4().hex[:8]}"
_DEV_A = f"CALCFG-DEV-A-{uuid.uuid4().hex[:8]}"
_DEV_B = f"CALCFG-DEV-B-{uuid.uuid4().hex[:8]}"
_RULE_SK = f"CALCFG-RULE-{uuid.uuid4().hex[:8]}"
_EVT_A = str(uuid.uuid4())
_EVT_B = str(uuid.uuid4())
_FACT_A = str(uuid.uuid4())
_FACT_B = str(uuid.uuid4())
_DATE_KEY = 20260601


@pytest_asyncio.fixture(scope="module")
async def dos_flotas() -> Any:
    """Dos flotas con datos analíticos IDÉNTICOS.

    Es la única forma honesta de probar que la calibración está scopeada: si
    las dos flotas parten del mismo puntaje, cualquier diferencia posterior
    viene de la calibración y de nada más.
    """
    async with AsyncSessionLocal() as session:
        try:
            fleets: dict[str, uuid.UUID] = {}
            for sufijo, db_name, dev in (
                ("A", _DB_A, _DEV_A),
                ("B", _DB_B, _DEV_B),
            ):
                fleet = Fleet(
                    code=f"{_PREFIJO}-{sufijo}",
                    name=f"Flota calibración {sufijo}",
                    is_active=True,
                )
                session.add(fleet)
                await session.flush()
                fleets[sufijo] = fleet.id
                geotab_db = GeotabDatabase(
                    fleet_id=fleet.id,
                    database_name=db_name,
                    database_key=db_name,
                    connection_type="geotab",
                    is_active=True,
                )
                session.add(geotab_db)
                await session.flush()
                session.add(
                    Vehicle(
                        plate=f"{_PREFIJO[-6:]}{sufijo}",
                        geotab_device_id=dev,
                        geotab_customer_status="found",
                        fleet_id=fleet.id,
                        geotab_database_id=geotab_db.id,
                        is_active=True,
                    )
                )

            await session.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
            # Alembic no gobierna `analytics`: las tablas las crea el loader del
            # ETL. Se crean desde los MISMOS modelos que la aplicación consulta
            # —con `checkfirst`, así que si el loader ya las creó no se tocan— en
            # vez de repetir el DDL a mano: una tabla mínima escrita a mano
            # rompería `select(FactCombustibleDaily)` con UndefinedColumn en la
            # primera columna que faltara.
            await session.run_sync(
                lambda sync_session: AnalyticsBase.metadata.create_all(
                    sync_session.connection(),
                    tables=[
                        DimVehicle.__table__,
                        FactCombustibleDaily.__table__,
                        DimRule.__table__,
                        FactHabitoEvent.__table__,
                    ],
                    checkfirst=True,
                )
            )
            # `dim_date` no tiene modelo y la tabla real del loader es el destino
            # de un FK `date_key` desde los hechos: hay que sembrar la fecha
            # antes que ellos.
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
                VALUES (:dk, '2026-06-01', 2026, 6, 1, 2, 202606, '2026-06-01')
                ON CONFLICT (date_key) DO NOTHING
            """),
                {"dk": _DATE_KEY},
            )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_vehicle
                    (vehicle_id, device_id, vehicle_label, database_name, motor_type,
                     fuel_kind, fuel_unit, is_active)
                VALUES
                    (:va, :da, 'Cal A', :dba, 'F2.8', 'liquid', 'gal', true),
                    (:vb, :db, 'Cal B', :dbb, 'F2.8', 'liquid', 'gal', true)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    database_name = EXCLUDED.database_name
            """),
                {
                    "va": _VEH_A,
                    "da": _DEV_A,
                    "dba": _DB_A,
                    "vb": _VEH_B,
                    "db": _DEV_B,
                    "dbb": _DB_B,
                },
            )
            # Mismo día, mismas cifras en las dos flotas. Los valores se eligen
            # para caer en la zona SENSIBLE de la calibración: 50 % del tiempo
            # en rango eficiente contra una meta de 70 %, y 20 % de ralentí
            # dentro de la ventana 10 %-30 %. Un valor saturado (0 o 100) no
            # detectaría un cambio de parámetro.
            await session.execute(
                text("""
                INSERT INTO analytics.fact_combustible_daily
                    (fact_row_id, vehicle_id, database_name, motor_type, date_key, fecha, placa,
                     kms_ecm, kms_gps, kms_effective, hrs_ecm, hrs_gps, comb,
                     distance_source, distance_quality_status, distance_quality_reason,
                     gps_quality_valid,
                     pct_rango_economico, pct_rango_balanceado, pct_exceso_rpm,
                     pct_ralenti, horas_ralenti_base, fuente_ralenti,
                     tiempo_total_en_rango, revision)
                VALUES
                    (:fa, :va, :dba, 'F2.8', :dk, '2026-06-01', :pa,
                     1000.0, 1000.0, 1000.0, 20.0, 20.0, 120.0,
                     'ecm', 'ok', 'ok', true,
                     0.30, 0.20, 0.0,
                     0.20, 20.0, 'ecm',
                     15.0, false),
                    (:fb, :vb, :dbb, 'F2.8', :dk, '2026-06-01', :pb,
                     1000.0, 1000.0, 1000.0, 20.0, 20.0, 120.0,
                     'ecm', 'ok', 'ok', true,
                     0.30, 0.20, 0.0,
                     0.20, 20.0, 'ecm',
                     15.0, false)
                ON CONFLICT (fact_row_id) DO NOTHING
            """),
                {
                    "fa": _FACT_A,
                    "va": _VEH_A,
                    "dba": _DB_A,
                    "pa": f"{_PREFIJO[-6:]}A",
                    "fb": _FACT_B,
                    "vb": _VEH_B,
                    "dbb": _DB_B,
                    "pb": f"{_PREFIJO[-6:]}B",
                    "dk": _DATE_KEY,
                },
            )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_rule (rule_sk, rule_id, rule_name, categoria)
                VALUES (:rsk, 'CALCFG-R1', 'Frenadas Bruscas', 'Seguridad')
                ON CONFLICT (rule_sk) DO NOTHING
            """),
                {"rsk": _RULE_SK},
            )
            # Un evento de seguridad por flota: densidad 1 por 1000 km contra un
            # tope de 15, o sea un QHS alto pero NO saturado en 100. Así, subir
            # el peso del tipo mueve el número.
            await session.execute(
                text("""
                INSERT INTO analytics.fact_habito_event
                    (event_sk, event_id, vehicle_id, database_name, date_key, rule_sk,
                     event_type, fecha, placa, fecha_y_hora_del_evento)
                VALUES
                    (:ea, :ea, :va, :dba, :dk, :rsk, 'Frenadas Bruscas',
                     '2026-06-01', :pa, '2026-06-01T10:00:00Z'),
                    (:eb, :eb, :vb, :dbb, :dk, :rsk, 'Frenadas Bruscas',
                     '2026-06-01', :pb, '2026-06-01T10:00:00Z')
                ON CONFLICT (event_sk) DO NOTHING
            """),
                {
                    "ea": _EVT_A,
                    "va": _VEH_A,
                    "dba": _DB_A,
                    "pa": f"{_PREFIJO[-6:]}A",
                    "eb": _EVT_B,
                    "vb": _VEH_B,
                    "dbb": _DB_B,
                    "pb": f"{_PREFIJO[-6:]}B",
                    "dk": _DATE_KEY,
                    "rsk": _RULE_SK,
                },
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield fleets

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM analytics.fact_habito_event WHERE event_sk IN (:ea, :eb)"),
            {"ea": _EVT_A, "eb": _EVT_B},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_rule WHERE rule_sk = :rsk"), {"rsk": _RULE_SK}
        )
        await session.execute(
            text("DELETE FROM analytics.fact_combustible_daily WHERE fact_row_id IN (:fa, :fb)"),
            {"fa": _FACT_A, "fb": _FACT_B},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id IN (:va, :vb)"),
            {"va": _VEH_A, "vb": _VEH_B},
        )
        await session.execute(delete(Vehicle).where(Vehicle.geotab_device_id.in_([_DEV_A, _DEV_B])))
        await session.execute(delete(Fleet).where(Fleet.code.like(f"{_PREFIJO}%")))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def sin_calibraciones_previas(request: pytest.FixtureRequest) -> Any:
    """Borra las calibraciones de las flotas del módulo antes de cada prueba.

    La tabla es append-only y las flotas son de alcance módulo, así que sin esto
    el orden de ejecución decidiría el resultado: "flota sin calibración" pasaría
    o fallaría según qué prueba escribió antes. Es la única limpieza que se hace
    sobre la tabla, y se hace por `fleet_id` de este módulo.
    """
    if "dos_flotas" in request.fixturenames:
        flotas = request.getfixturevalue("dos_flotas")
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM calificacion_config_versions WHERE fleet_id = ANY(:ids)"),
                {"ids": list(flotas.values())},
            )
            await session.commit()
    yield


@pytest_asyncio.fixture
async def actor() -> Any:
    """El admin bootstrap como actor de las escrituras del servicio."""
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.email == settings.bootstrap_admin_email))
        ).scalar_one()
        yield user


_MODIFICADA = DEFAULT_CALIFICACION_CONFIG.with_overrides(
    ralenti_max=0.50,
    eventos_cap=9.0,
    umbral_cumple=80.0,
    qhs_event_weights={"frenadas bruscas": 2.0},
)
_OTRA = DEFAULT_CALIFICACION_CONFIG.with_overrides(efic_target=0.55, umbral_en_riesgo=60.0)


async def _guardar(session: Any, fleet_id: uuid.UUID, config: CalificacionConfig, user: Any) -> Any:
    return await ccs.save_fleet_config(
        session,
        fleet_id=fleet_id,
        config=config,
        actor_user_id=user.id,
        actor_email=user.email,
    )


async def _leer(session: Any, fleet_id: uuid.UUID) -> Any:
    return await ccs.get_fleet_config(session, fleet_id)


# ===========================================================================
# B. Persistencia y regla multi-flota
# ===========================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_guardar_y_leer_devuelve_la_misma_calibracion(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    fleet_id = dos_flotas["A"]
    async with AsyncSessionLocal() as session:
        await _guardar(session, fleet_id, _MODIFICADA, actor)
        await session.commit()
    async with AsyncSessionLocal() as session:
        efectiva = await _leer(session, fleet_id)

    assert efectiva.origen == "flota"
    assert efectiva.config.ralenti_max == pytest.approx(0.50)
    assert efectiva.config.eventos_cap == pytest.approx(9.0)
    assert dict(efectiva.config.qhs_event_weights) == {"frenadas bruscas": 2.0}
    # Lo no enviado conserva el default, no cae a 0.
    assert efectiva.config.peso_qhs == pytest.approx(DEFAULT_CALIFICACION_CONFIG.peso_qhs)
    assert efectiva.actualizado_por
    assert efectiva.actualizado_en is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_append_only_la_ultima_gana_y_el_historial_conserva_las_dos(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    """Dos guardados seguidos: la lectura ve el segundo, el historial ve los dos.

    Este dato cambia el puntaje de toda una flota. Sobrescribir la fila borraría
    la única evidencia de quién lo cambió y a qué; es el mismo motivo por el que
    `distance_quality_decisions` es append-only.
    """
    fleet_id = dos_flotas["B"]
    async with AsyncSessionLocal() as session:
        await _guardar(
            session, fleet_id, DEFAULT_CALIFICACION_CONFIG.with_overrides(eventos_cap=11.0), actor
        )
        await session.commit()
    async with AsyncSessionLocal() as session:
        await _guardar(
            session, fleet_id, DEFAULT_CALIFICACION_CONFIG.with_overrides(eventos_cap=7.0), actor
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        efectiva = await _leer(session, fleet_id)
        historial = list(await ccs.config_history(session, fleet_id=fleet_id))

    assert efectiva.config.eventos_cap == pytest.approx(7.0)
    assert len(historial) == 2, historial
    # La más reciente primero: es el orden en el que se lee una bitácora.
    assert historial[0]["config"]["eventos_cap"] == pytest.approx(7.0)
    assert historial[1]["config"]["eventos_cap"] == pytest.approx(11.0)
    assert historial[0]["creado_en"] >= historial[1]["creado_en"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reset_devuelve_defaults_pero_conserva_quien_lo_decidio(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    """Volver a los defaults es una decisión, no un borrado.

    Los parámetros quedan en NULL y la lectura devuelve los defaults, pero el
    actor y la fecha de la fila de reset tienen que sobrevivir: alguien decidió
    que esta flota se calificara con la calibración general.
    """
    fleet_id = dos_flotas["A"]
    async with AsyncSessionLocal() as session:
        await _guardar(session, fleet_id, _MODIFICADA, actor)
        await session.commit()
    async with AsyncSessionLocal() as session:
        await ccs.reset_fleet_config(
            session,
            fleet_id=fleet_id,
            actor_user_id=actor.id,
            actor_email=actor.email,
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        efectiva = await _leer(session, fleet_id)
        historial = list(await ccs.config_history(session, fleet_id=fleet_id))

    assert efectiva.config == DEFAULT_CALIFICACION_CONFIG
    assert efectiva.origen == "defecto"
    assert efectiva.actualizado_por, "el reset perdió al actor que lo decidió"
    assert efectiva.actualizado_en is not None
    assert historial[0]["es_reset"] is True
    assert historial[0]["config"] is None, "un reset no publica parámetros propios"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_flota_sin_calibracion_lee_defaults(dos_flotas: dict[str, uuid.UUID]) -> None:
    async with AsyncSessionLocal() as session:
        efectiva = await _leer(session, dos_flotas["B"])
    assert efectiva.config == DEFAULT_CALIFICACION_CONFIG
    assert efectiva.origen == "defecto"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_effective_config_una_flota_calibrada(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    fleet_id = dos_flotas["A"]
    async with AsyncSessionLocal() as session:
        await _guardar(session, fleet_id, _MODIFICADA, actor)
        await session.commit()
    async with AsyncSessionLocal() as session:
        efectiva = await ccs.effective_config(session, [fleet_id])
    assert efectiva.origen == "flota"
    assert efectiva.config.ralenti_max == pytest.approx(0.50)
    assert efectiva.fleet_id == fleet_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_effective_config_dos_flotas_distintas_es_mixto(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    """Con dos calibraciones en conflicto no se elige una: se cae a los defaults.

    Aplicar la de una de las dos produciría un puntaje que ninguna de las dos
    flotas reconoce como el suyo. `origen == "mixto"` es lo que le permite al
    front decir por qué el número no coincide con la calibración que ve.
    """
    async with AsyncSessionLocal() as session:
        await _guardar(session, dos_flotas["A"], _MODIFICADA, actor)
        await _guardar(session, dos_flotas["B"], _OTRA, actor)
        await session.commit()
    async with AsyncSessionLocal() as session:
        efectiva = await ccs.effective_config(session, [dos_flotas["A"], dos_flotas["B"]])
    assert efectiva.origen == "mixto"
    assert efectiva.config == DEFAULT_CALIFICACION_CONFIG


@pytest.mark.integration
@pytest.mark.asyncio
async def test_effective_config_dos_flotas_iguales_no_es_mixto(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    """Misma calibración en las dos flotas: no hay conflicto que resolver."""
    async with AsyncSessionLocal() as session:
        await _guardar(session, dos_flotas["A"], _MODIFICADA, actor)
        await _guardar(session, dos_flotas["B"], _MODIFICADA, actor)
        await session.commit()
    async with AsyncSessionLocal() as session:
        efectiva = await ccs.effective_config(session, [dos_flotas["A"], dos_flotas["B"]])
    assert efectiva.origen != "mixto"
    assert efectiva.config.ralenti_max == pytest.approx(0.50)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("alcance", [[], None], ids=["vacio", "none"])
async def test_effective_config_sin_alcance_cae_a_defaults(alcance: Any) -> None:
    async with AsyncSessionLocal() as session:
        efectiva = await ccs.effective_config(session, alcance)
    assert efectiva.config == DEFAULT_CALIFICACION_CONFIG
    assert efectiva.origen == "defecto"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fila_invalida_en_la_base_no_tumba_el_reporte(
    dos_flotas: dict[str, uuid.UUID],
) -> None:
    """Una calibración imposible en la base degrada a los defaults, no explota.

    Los `CheckConstraint` acotan cada campo por separado pero no pueden
    expresar barato que los pesos sumen 1, así que una escritura hecha por
    fuera del servicio puede dejar una fila que sólo Python rechaza. El reporte
    de calificación es la pantalla de un cliente: tiene que seguir abriendo.
    """
    fleet_id = dos_flotas["B"]
    async with AsyncSessionLocal() as session:
        # peso_qhs y peso_qho están dentro de [0, 1] uno por uno —los checks
        # pasan—, pero suman 1,2: sólo `validate_config` lo ve.
        await session.execute(
            text("""
            INSERT INTO calificacion_config_versions
                (id, fleet_id, peso_qhs, peso_qho, is_reset, created_at, updated_at)
            VALUES (gen_random_uuid(), :fid, 0.6, 0.6, false, now(), now())
        """),
            {"fid": fleet_id},
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        efectiva = await ccs.effective_config(session, [fleet_id])
    assert efectiva.config == DEFAULT_CALIFICACION_CONFIG
    assert efectiva.origen == "defecto"


# ===========================================================================
# C. La calibración manda sobre el puntaje
# ===========================================================================

# `get_calificacion` NO recibe la calibración: la resuelve una vez desde el
# alcance de flotas y la pasa hacia abajo. Por eso estas pruebas calibran
# escribiendo en la base, que es el camino real, y no inyectando un objeto.

_RANGO = {"date_from": date(2026, 6, 1), "date_to": date(2026, 6, 30)}


async def _calificacion(*fleet_ids: uuid.UUID, **extra: Any) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        return await analytics_service.get_calificacion(
            session, fleet_ids=list(fleet_ids), **_RANGO, **extra
        )


async def _calibrar(fleet_id: uuid.UUID, actor: Any, **overrides: Any) -> None:
    async with AsyncSessionLocal() as session:
        await _guardar(
            session, fleet_id, DEFAULT_CALIFICACION_CONFIG.with_overrides(**overrides), actor
        )
        await session.commit()


def _vehiculo(respuesta: dict[str, Any]) -> dict[str, Any]:
    vehiculos = respuesta["vehiculos"]
    assert vehiculos, "el seed no produjo vehículos calificados"
    return vehiculos[0]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_calibracion_igual_al_default_no_mueve_nada(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    """LA invariante: nadie que no haya calibrado ve su puntaje moverse.

    `DEFAULT_CALIFICACION_CONFIG` reproduce EXACTAMENTE las constantes con las
    que se calificó hasta el refactor. Guardar esa misma calibración de forma
    explícita tiene que producir una respuesta idéntica a no tener ninguna: si
    difiere, el refactor cambió el puntaje de todas las flotas sin calibración
    propia, que son casi todas.

    Se compara la respuesta COMPLETA —umbrales, evolución, detalle por vehículo,
    metodología— y no sólo el promedio: un promedio igual con un detalle distinto
    sigue siendo una regresión que el cliente ve. Sólo se excluye
    `configuracion`, que por definición cambia (`origen` pasa de "defecto" a
    "flota" y trae actor y fecha).
    """
    fleet_id = dos_flotas["A"]
    sin_fila = await _calificacion(fleet_id)
    await _calibrar(fleet_id, actor)
    con_default_explicito = await _calificacion(fleet_id)

    assert con_default_explicito["configuracion"]["origen"] == "flota"
    assert sin_fila["configuracion"]["origen"] == "defecto"

    del sin_fila["configuracion"], con_default_explicito["configuracion"]
    assert sin_fila == con_default_explicito


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ralenti_max_mueve_el_puntaje_solo_de_la_flota_calibrada(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    """La prueba de que la calibración está scopeada y no es global.

    Las dos flotas tienen exactamente los mismos datos. Ampliar la ventana de
    ralentí de la flota A sube su puntaje —20 % de ralentí queda más lejos del
    máximo— y no puede tocar el de B.
    """
    base_a = _vehiculo(await _calificacion(dos_flotas["A"]))["qgen"]
    base_b = _vehiculo(await _calificacion(dos_flotas["B"]))["qgen"]
    assert base_a == pytest.approx(base_b), "el seed no dejó las dos flotas iguales"

    await _calibrar(dos_flotas["A"], actor, ralenti_max=0.60)

    nuevo_a = _vehiculo(await _calificacion(dos_flotas["A"]))["qgen"]
    nuevo_b = _vehiculo(await _calificacion(dos_flotas["B"]))["qgen"]

    assert nuevo_a > base_a, (base_a, nuevo_a)
    assert nuevo_b == pytest.approx(base_b), "la calibración de A se filtró a B"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_umbral_cumple_reclasifica_sin_mover_el_qgen(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    """Los umbrales son de lectura: cambian el semáforo, no el puntaje."""
    fleet_id = dos_flotas["A"]
    base = await _calificacion(fleet_id)
    qgen_base = _vehiculo(base)["qgen"]
    estado_base = _vehiculo(base)["estado"]

    # Cortes por debajo del puntaje medido: el mismo qgen tiene que leerse ahora
    # como "Cumple".
    en_riesgo = max(0.0, qgen_base - 20.0)
    cumple = max(en_riesgo + 1.0, qgen_base - 10.0)
    await _calibrar(fleet_id, actor, umbral_en_riesgo=en_riesgo, umbral_cumple=cumple)
    reclasificado = await _calificacion(fleet_id)

    assert _vehiculo(reclasificado)["qgen"] == pytest.approx(qgen_base)
    assert reclasificado["umbrales"]["cumple"] == pytest.approx(cumple)
    assert _vehiculo(reclasificado)["estado"] == "Cumple"
    assert _vehiculo(reclasificado)["estado"] != estado_base


@pytest.mark.integration
@pytest.mark.asyncio
async def test_peso_de_evento_mueve_el_qhs_y_con_el_el_qgen(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    """Subir la severidad de un tipo de evento penaliza más ese evento.

    El seed tiene un evento 'Frenadas Bruscas' por vehículo. Con peso 4 pesa
    cuatro veces en la densidad de eventos, así que el QHS baja; y como el QHS
    es la mitad del QGen, el general baja con él.
    """
    fleet_id = dos_flotas["A"]
    base = _vehiculo(await _calificacion(fleet_id))

    pesos = {**dict(DEFAULT_CALIFICACION_CONFIG.qhs_event_weights), "frenadas bruscas": 4.0}
    await _calibrar(fleet_id, actor, qhs_event_weights=pesos)
    nuevo = _vehiculo(await _calificacion(fleet_id))

    assert nuevo["qhs"] < base["qhs"], (base["qhs"], nuevo["qhs"])
    assert nuevo["qgen"] < base["qgen"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_metodologia_publica_la_calibracion_de_la_flota(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    """La explicación tiene que derivar de los MISMOS números que calculan.

    Escribirla a mano en el front fue lo que descuadró el gauge de los
    umbrales. Si `metodologia` sigue publicando los defaults cuando la flota
    calibró, la razón de existir de esa función desaparece: el cliente lee una
    fórmula que no es la que produjo su puntaje.
    """
    fleet_id = dos_flotas["A"]
    await _calibrar(fleet_id, actor, ralenti_max=0.55)
    respuesta = await _calificacion(fleet_id)

    textos = " ".join(
        str(c.get("detalle", "")) for c in respuesta["metodologia"]["componentes_qho"]
    )
    assert "55%" in textos, textos
    assert "30% o más → 0" not in textos, "la metodología sigue publicando el ralentí por defecto"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_respuesta_expone_el_origen_de_la_calibracion(
    dos_flotas: dict[str, uuid.UUID], actor: Any
) -> None:
    """`configuracion.origen` en los tres casos: defecto, flota y mixto."""
    sin_calibrar = await _calificacion(dos_flotas["B"])
    assert sin_calibrar["configuracion"]["origen"] == "defecto"

    async with AsyncSessionLocal() as session:
        await _guardar(session, dos_flotas["A"], _MODIFICADA, actor)
        await session.commit()
    propia = await _calificacion(dos_flotas["A"])
    assert propia["configuracion"]["origen"] == "flota"
    assert propia["configuracion"]["ralenti_max"] == pytest.approx(0.50)
    assert propia["configuracion"]["fleet_id"] == str(dos_flotas["A"])

    async with AsyncSessionLocal() as session:
        await _guardar(session, dos_flotas["B"], _OTRA, actor)
        await session.commit()
    mezclada = await _calificacion(dos_flotas["A"], dos_flotas["B"])
    assert mezclada["configuracion"]["origen"] == "mixto"
    # Con calibraciones en conflicto el puntaje se calcula con los defaults: no
    # se puede presentar como el de ninguna de las dos flotas.
    assert mezclada["configuracion"]["ralenti_max"] == pytest.approx(
        DEFAULT_CALIFICACION_CONFIG.ralenti_max
    )


# ===========================================================================
# D. Autorización
# ===========================================================================


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


def _crear_cliente_con_rol(
    admin_client: TestClient, permisos: list[str], fleet_ids: list[str]
) -> TestClient:
    """Usuario con un rol de permisos exactos y alcance de flota exacto.

    El rol seeded `viewer` ya trae `reportes.view` en la matriz migrada, así que
    no sirve para separar `view` de `edit`: hay que crear el rol.
    """
    role_code = f"calcfg_{uuid.uuid4().hex[:8]}"
    resp = admin_client.post(
        "/api/v1/roles",
        json={
            "code": role_code,
            "name": "Calibración test",
            "description": None,
            "permission_codes": permisos,
        },
    )
    assert resp.status_code == 201, resp.text

    email = f"calcfg-{uuid.uuid4().hex[:8]}@portalclientes.test"
    password = "CalCfgPass123!"
    resp = admin_client.post(
        "/api/v1/users",
        json={
            "email": email,
            "password": password,
            "full_name": "Calibración test",
            "role_codes": [role_code],
            "fleet_ids": fleet_ids,
        },
    )
    assert resp.status_code == 201, resp.text

    c = TestClient(app)
    resp = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return c


def _http(client: TestClient, metodo: str, url: str, fleet_id: uuid.UUID, **kw: Any):
    """Las cuatro operaciones direccionan la flota por `?fleet_id=`, obligatorio.

    No hay "todas las flotas" aquí: escribir la misma fórmula en N flotas de una
    petición es una decisión que se toma flota por flota, y leer "la de todas"
    no significa nada cuando difieren.
    """
    return client.request(metodo, url, params={"fleet_id": str(fleet_id)}, **kw)


def _payload(**overrides: Any) -> dict[str, Any]:
    """Los 16 parámetros completos, como los envía el formulario.

    El payload es total y no parcial a propósito: un campo ausente dejaría en
    duda si significa "no cambiar" o "volver al default", y este dato cambia el
    puntaje de una flota entera.
    """
    return {**config_to_mapping(DEFAULT_CALIFICACION_CONFIG), **overrides}


@pytest.mark.integration
def test_put_sin_reportes_edit_es_403_y_no_escribe(
    admin_client: TestClient, dos_flotas: dict[str, uuid.UUID]
) -> None:
    """403 y, sobre todo, la calibración sigue siendo la anterior.

    Un 403 que igual escribió es peor que no tener el control: verificar el
    efecto y no sólo el código es el punto de la prueba.
    """
    fleet_id = dos_flotas["B"]
    antes = _http(admin_client, "GET", _CONFIG_URL, fleet_id)
    assert antes.status_code == 200, antes.text

    lector = _crear_cliente_con_rol(admin_client, ["reportes.view"], [str(fleet_id)])
    resp = _http(lector, "PUT", _CONFIG_URL, fleet_id, json=_payload(eventos_cap=3.0))
    assert resp.status_code == 403, resp.text

    despues = _http(admin_client, "GET", _CONFIG_URL, fleet_id)
    assert despues.status_code == 200, despues.text
    assert despues.json() == antes.json(), "el 403 escribió de todas formas"


@pytest.mark.integration
def test_put_sobre_flota_ajena_no_escribe(
    admin_client: TestClient, dos_flotas: dict[str, uuid.UUID]
) -> None:
    """Tener `reportes.edit` no da acceso a la flota de otro cliente.

    Regla operativa 7: toda consulta multi-flota intersecta el alcance
    solicitado con las flotas autorizadas en backend.
    """
    propia, ajena = dos_flotas["A"], dos_flotas["B"]
    antes = _http(admin_client, "GET", _CONFIG_URL, ajena)
    assert antes.status_code == 200, antes.text

    editor = _crear_cliente_con_rol(admin_client, ["reportes.view", "reportes.edit"], [str(propia)])
    resp = _http(editor, "PUT", _CONFIG_URL, ajena, json=_payload(eventos_cap=4.0))
    assert resp.status_code in (403, 404), resp.text

    despues = _http(admin_client, "GET", _CONFIG_URL, ajena)
    assert despues.json() == antes.json(), "se escribió en una flota fuera de alcance"


@pytest.mark.integration
def test_get_de_flota_ajena_no_se_lee(
    admin_client: TestClient, dos_flotas: dict[str, uuid.UUID]
) -> None:
    lector = _crear_cliente_con_rol(admin_client, ["reportes.view"], [str(dos_flotas["A"])])
    resp = _http(lector, "GET", _CONFIG_URL, dos_flotas["B"])
    assert resp.status_code in (403, 404), resp.text


@pytest.mark.integration
def test_history_exige_reportes_edit(
    admin_client: TestClient, dos_flotas: dict[str, uuid.UUID]
) -> None:
    """El historial nombra a quién cambió qué: es información administrativa."""
    fleet_id = dos_flotas["A"]
    lector = _crear_cliente_con_rol(admin_client, ["reportes.view"], [str(fleet_id)])
    assert _http(lector, "GET", _HISTORY_URL, fleet_id).status_code == 403

    editor = _crear_cliente_con_rol(
        admin_client, ["reportes.view", "reportes.edit"], [str(fleet_id)]
    )
    resp = _http(editor, "GET", _HISTORY_URL, fleet_id)
    assert resp.status_code == 200, resp.text


@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "fragmento"),
    [
        pytest.param({"peso_qhs": 0.6, "peso_qho": 0.4999}, "sumar 1", id="pesos_top"),
        pytest.param(
            {"peso_eficiente": 0.6, "peso_ralenti": 0.3, "peso_exceso_rpm": 0.2},
            "deben sumar 1",
            id="pesos_qho",
        ),
        pytest.param(
            {"ralenti_target": 0.4, "ralenti_max": 0.5, "umbral_en_riesgo": 90.0},
            "umbrales",
            id="umbrales_invertidos",
        ),
    ],
)
def test_put_con_invariante_cruzada_violada_es_400_en_espanol(
    admin_client: TestClient,
    dos_flotas: dict[str, uuid.UUID],
    overrides: dict[str, Any],
    fragmento: str,
) -> None:
    """400 con el motivo, no 500 ni un error genérico de esquema.

    Son las invariantes que sólo `validate_config` puede expresar: cada campo
    está dentro de su rango, lo que no cuadra es la RELACIÓN entre varios. El
    router tiene un `except CalificacionConfigError -> 400` escrito justo para
    esto, y el mensaje es la única explicación que recibe quien calibró.

    Un 422 aquí significa que el `model_validator` del payload convirtió el
    `CalificacionConfigError` en un `ValueError` de Pydantic y se adelantó al
    router: el mensaje llega, pero envuelto en un error de esquema y el `except`
    del router queda inalcanzable.
    """
    resp = _http(admin_client, "PUT", _CONFIG_URL, dos_flotas["A"], json=_payload(**overrides))
    assert fragmento in resp.text, resp.text
    assert resp.status_code == 400, (
        f"el contrato pide 400 con el mensaje tal cual; llegó {resp.status_code}: {resp.text}"
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"eventos_cap": 0.0}, id="tope_en_cero"),
        pytest.param({"rpm_high_weight": 0.5}, id="agravacion_bajo_1"),
        pytest.param({"umbral_cumple": 120.0}, id="umbral_sobre_100"),
    ],
)
def test_put_con_campo_fuera_de_rango_no_es_500(
    admin_client: TestClient, dos_flotas: dict[str, uuid.UUID], overrides: dict[str, Any]
) -> None:
    """Un rango por campo puede salir como 422 (error por campo) o como 400.

    Lo que no puede pasar es que salga un 500 o, peor, que se acepte: un
    `eventos_cap` de 0 dejaría el QHS en 0 para cualquier vehículo con un solo
    evento.
    """
    resp = _http(admin_client, "PUT", _CONFIG_URL, dos_flotas["A"], json=_payload(**overrides))
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.integration
def test_put_no_aplica_campos_no_editables(
    admin_client: TestClient, dos_flotas: dict[str, uuid.UUID]
) -> None:
    """La frontera motor/operación, llevada a HTTP.

    La velocidad gobernada y la sobrevelocidad máxima vienen del motor, no de la
    operación: un cliente no puede declarar que su motor gira más de lo que
    gira. El campo puede rechazarse o ignorarse, pero nunca aplicarse ni
    corromper el resto de la calibración enviada en el mismo body.

    Lo que SÍ puede el cliente desde el 2026-08-28 es apagar la penalización que
    ese límite dispara, por la vía de `penalizaciones`. Son cosas distintas y
    esta prueba sólo cubre la primera: el límite contra el que se mide no se
    negocia; que anule el puntaje, sí.
    """
    fleet_id = dos_flotas["A"]
    prohibidos = ("governed_speed_rpm", "max_overspeed_rpm", "qgen_penalizado")
    assert not set(prohibidos) & set(EDITABLE_FIELDS)

    payload = _payload(
        eventos_cap=12.0,
        governed_speed_rpm=4000,
        max_overspeed_rpm=5000,
        qgen_penalizado=0.0,
    )
    resp = _http(admin_client, "PUT", _CONFIG_URL, fleet_id, json=payload)
    assert resp.status_code in (200, 400, 422), resp.text

    despues = _http(admin_client, "GET", _CONFIG_URL, fleet_id)
    assert despues.status_code == 200, despues.text
    config = despues.json()["config"]
    for prohibido in prohibidos:
        assert prohibido not in config, f"{prohibido} entró en la calibración"

    if resp.status_code == 200:
        # Si el endpoint aceptó el body, el campo editable sí se aplicó y el no
        # editable se descartó: ignorar no puede significar descartar todo.
        assert config["eventos_cap"] == pytest.approx(12.0)
