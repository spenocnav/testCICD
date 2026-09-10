"""Penalizaciones calibrables: apagarlas cambia el castigo, no el hecho.

La anulación del puntaje por superar la sobrevelocidad máxima del motor era
incondicional y estaba documentada como NO calibrable. Desde 2026-08-28 es una
entrada del registro `PENALIZACIONES` que cada flota puede apagar. Estas
pruebas fijan las dos mitades de esa decisión:

- **La configuración** (bloque A, sin base): una penalización nace ACTIVA y una
  clave ausente cae a ese default, no a "apagada". Es lo que hace que una
  penalización futura aplique sobre las calibraciones ya guardadas sin tener
  que reescribirlas.
- **El cálculo** (bloque B, contra la base): con la penalización activa el
  puntaje se anula; apagada vuelve a ser `qgen_base`; y en los dos estados el
  conteo de excesos es el mismo. Ese último es el invariante más importante del
  archivo: la flota puede decidir que un exceso no invalide la calificación,
  pero no que el exceso no haya ocurrido.

La coherencia entre la tabla por vehículo y la serie mensual se prueba en los
dos estados a propósito: la bandera se resuelve una vez y se aplica en DOS
lugares del servicio, así que un cambio que arregle uno y olvide el otro deja
el gauge y la evolución contando historias distintas.

Convenciones y forma del seed tomadas de `test_calificacion_config.py`. El
módulo tiene fixture propio —flota, motor y eventos propios, con sufijos
aleatorios— para no alterar los conteos del seed de aquel.
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
from app.models.master_data import GeotabDatabase, MotorCatalog, Vehicle
from app.models.user import User
from app.services import analytics_service
from app.services import calificacion_config_service as ccs
from app.services.calificacion_config import (
    DEFAULT_CALIFICACION_CONFIG,
    PENALIZACION_CODES,
    PENALIZACION_SOBREVELOCIDAD_RPM,
    PENALIZACIONES,
    CalificacionConfig,
    CalificacionConfigError,
    config_from_mapping,
    config_to_mapping,
    validate_config,
)

# ===========================================================================
# A. La configuración — sin base
# ===========================================================================


def test_el_registro_declara_la_sobrevelocidad_y_nace_activa() -> None:
    """El default de una penalización es ACTIVA, y el default global lo refleja.

    Si `default_activa` naciera en False, añadir una penalización nueva no
    haría nada hasta que cada flota la encendiera una por una: el registro
    existe justamente para lo contrario.
    """
    registro = {p.code: p for p in PENALIZACIONES}
    assert PENALIZACION_SOBREVELOCIDAD_RPM in registro
    assert registro[PENALIZACION_SOBREVELOCIDAD_RPM].default_activa is True
    assert frozenset(registro) == PENALIZACION_CODES

    assert (
        DEFAULT_CALIFICACION_CONFIG.penalizacion_activa(PENALIZACION_SOBREVELOCIDAD_RPM)
        is True
    )


@pytest.mark.parametrize(
    "values",
    [
        pytest.param({}, id="sin_la_clave"),
        pytest.param({"penalizaciones": {}}, id="mapa_vacio"),
        pytest.param({"penalizaciones": None}, id="clave_en_none"),
    ],
)
def test_clave_ausente_cae_al_default_del_registro_no_a_apagado(
    values: dict[str, Any],
) -> None:
    """Una calibración que no menciona la penalización la deja ACTIVA.

    Es la regla que hace posible añadir una penalización sin migrar filas: las
    calibraciones guardadas antes de que existiera no la nombran, y tienen que
    resolverse al default del registro. Caer a "apagada" dejaría a esas flotas
    sin la regla nueva en silencio, que es justo lo contrario de lo decidido.
    """
    config = config_from_mapping(values)
    assert config.penalizacion_activa(PENALIZACION_SOBREVELOCIDAD_RPM) is True

    # Y lo mismo construyendo el objeto directamente, no sólo por el mapeo: el
    # servicio arma `CalificacionConfig` por los dos caminos.
    directo = CalificacionConfig(penalizaciones={})
    assert directo.penalizacion_activa(PENALIZACION_SOBREVELOCIDAD_RPM) is True


def test_false_explicito_apaga_la_penalizacion() -> None:
    """Sólo un False explícito la apaga, y sobrevive al viaje por el mapeo."""
    config = config_from_mapping(
        {"penalizaciones": {PENALIZACION_SOBREVELOCIDAD_RPM: False}}
    )
    assert config.penalizacion_activa(PENALIZACION_SOBREVELOCIDAD_RPM) is False
    assert dict(config.penalizaciones) == {PENALIZACION_SOBREVELOCIDAD_RPM: False}

    encendida = config_from_mapping(
        {"penalizaciones": {PENALIZACION_SOBREVELOCIDAD_RPM: True}}
    )
    assert encendida.penalizacion_activa(PENALIZACION_SOBREVELOCIDAD_RPM) is True


def test_codigo_desconocido_es_rechazado_y_nombra_los_validos() -> None:
    """Un código mal escrito no se guarda como si nada.

    Ignorarlo sería el peor de los dos males: el cliente creería haber apagado
    la penalización y el puntaje seguiría anulándose sin explicación. El
    mensaje nombra los códigos válidos porque es el único texto que ve quien
    está calibrando.
    """
    invalida = DEFAULT_CALIFICACION_CONFIG.with_overrides(
        penalizaciones={"sobrevelocidad_rpms": False}
    )
    with pytest.raises(CalificacionConfigError) as exc:
        validate_config(invalida)

    mensaje = str(exc.value)
    assert "sobrevelocidad_rpms" in mensaje, mensaje
    for code in PENALIZACION_CODES:
        assert code in mensaje, mensaje
    # Mismas convenciones que el resto de los mensajes de calibración.
    assert mensaje.strip().endswith("."), mensaje
    assert not {"must", "should", "invalid", "error:"} & set(mensaje.lower().split()), mensaje

    # El camino de lectura no puede ser más permisivo que el de escritura: una
    # fila con un código basura tiene que fallar al leerse, no aplicarse a medias.
    with pytest.raises(CalificacionConfigError):
        config_from_mapping({"penalizaciones": {"no_existe": True}})


@pytest.mark.parametrize(
    "penalizaciones",
    [
        pytest.param(None, id="default"),
        pytest.param({PENALIZACION_SOBREVELOCIDAD_RPM: False}, id="apagada"),
        pytest.param({PENALIZACION_SOBREVELOCIDAD_RPM: True}, id="encendida"),
        pytest.param({}, id="vacio"),
    ],
)
def test_roundtrip_conserva_el_mapa_de_penalizaciones(
    penalizaciones: dict[str, bool] | None,
) -> None:
    """Serializar y volver a construir devuelve la MISMA decisión.

    Es el viaje real: la fila se escribe con `config_to_mapping` y se lee con
    `config_from_mapping`. Si el mapa se pierde en el trayecto, una flota que
    apagó la penalización volvería a verse penalizada tras recargar, y al revés.
    """
    config = (
        DEFAULT_CALIFICACION_CONFIG
        if penalizaciones is None
        else DEFAULT_CALIFICACION_CONFIG.with_overrides(penalizaciones=penalizaciones)
    )
    mapping = config_to_mapping(config)
    assert "penalizaciones" in mapping, "el mapa no se publica y no se podría persistir"

    vuelta = config_from_mapping(mapping)
    assert vuelta == config
    assert dict(vuelta.penalizaciones) == dict(config.penalizaciones)
    assert vuelta.penalizacion_activa(
        PENALIZACION_SOBREVELOCIDAD_RPM
    ) == config.penalizacion_activa(PENALIZACION_SOBREVELOCIDAD_RPM)


# ===========================================================================
# B. El cálculo — contra la base
# ===========================================================================

_PREFIJO = f"CALPEN-{uuid.uuid4().hex[:8].upper()}"
_DB = f"calpen_{uuid.uuid4().hex[:8]}"
_VEH = f"CALPEN-VEH-{uuid.uuid4().hex[:8]}"
_DEV = f"CALPEN-DEV-{uuid.uuid4().hex[:8]}"
_MOTOR = f"CALPEN-M-{uuid.uuid4().hex[:6]}"
_RULE_SEG = f"CALPEN-RS-{uuid.uuid4().hex[:8]}"
_RULE_RPM = f"CALPEN-RR-{uuid.uuid4().hex[:8]}"
_EVT_SEG = str(uuid.uuid4())
_EVT_RPM_ALTO = str(uuid.uuid4())
_EVT_RPM_SOBRE = str(uuid.uuid4())
_FACT = str(uuid.uuid4())
_DATE_KEY = 20260601

# Velocidades de placa del motor de prueba. La sobrevelocidad manda al vehículo
# a 0 con UN evento; la gobernada sólo agrava, y está aquí para probar que las
# dos cuentas siguen siendo distintas cuando la penalización se apaga.
_GOBERNADA = 2000
_SOBREVELOCIDAD = 2500


@pytest_asyncio.fixture(scope="module")
async def flota_con_sobrevelocidad() -> Any:
    """Una flota con un vehículo que EXCEDIÓ la sobrevelocidad máxima.

    Los datos de combustible se eligen para que el vehículo puntúe ALTO sin la
    penalización (75 % del tiempo en rango eficiente contra una meta de 70 %,
    10 % de ralentí, un solo evento de seguridad en 1000 km). Un vehículo que ya
    puntuara mal no probaría nada: el 0 podría venir de su operación y no del
    override, y apagar la penalización no se notaría.

    Tres eventos, todos en la misma ventana:

    - una frenada brusca, para que el QHS no salga saturado en 100;
    - un exceso de RPM por encima de la gobernada pero por debajo de la
      sobrevelocidad: agrava, no penaliza;
    - un exceso por encima de la sobrevelocidad: el que dispara la anulación.
    """
    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(
                code=_PREFIJO,
                name="Flota penalizaciones",
                is_active=True,
            )
            session.add(fleet)
            await session.flush()
            fleet_id = fleet.id

            geotab_db = GeotabDatabase(
                fleet_id=fleet_id,
                database_name=_DB,
                database_key=_DB,
                connection_type="geotab",
                is_active=True,
            )
            session.add(geotab_db)
            await session.flush()
            session.add(
                Vehicle(
                    plate=f"{_PREFIJO[-6:]}P",
                    geotab_device_id=_DEV,
                    geotab_customer_status="found",
                    fleet_id=fleet_id,
                    geotab_database_id=geotab_db.id,
                    is_active=True,
                )
            )
            # El límite NO es calibrable: viene de Navi Vehículos por motor. Sin
            # esta fila el fail-open deja al vehículo sin penalización y la
            # prueba mediría otra cosa.
            session.add(
                MotorCatalog(
                    motor_type=_MOTOR,
                    description="Motor de prueba de penalizaciones",
                    governed_speed_rpm=_GOBERNADA,
                    max_overspeed_rpm=_SOBREVELOCIDAD,
                )
            )

            await session.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
            # Alembic no gobierna `analytics`: las tablas las crea el loader del
            # ETL. Se crean desde los MISMOS modelos que la aplicación consulta,
            # con `checkfirst`, igual que en `test_calificacion_config.py`.
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
                VALUES (:v, :d, 'Cal Pen', :db, :motor, 'liquid', 'gal', true)
                ON CONFLICT (vehicle_id) DO UPDATE SET
                    device_id = EXCLUDED.device_id,
                    database_name = EXCLUDED.database_name,
                    motor_type = EXCLUDED.motor_type
            """),
                {"v": _VEH, "d": _DEV, "db": _DB, "motor": _MOTOR},
            )
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
                    (:f, :v, :db, :motor, :dk, '2026-06-01', :p,
                     1000.0, 1000.0, 1000.0, 20.0, 20.0, 120.0,
                     'ecm', 'ok', 'ok', true,
                     0.50, 0.25, 0.0,
                     0.10, 20.0, 'ecm',
                     15.0, false)
                ON CONFLICT (fact_row_id) DO NOTHING
            """),
                {
                    "f": _FACT,
                    "v": _VEH,
                    "db": _DB,
                    "motor": _MOTOR,
                    "p": f"{_PREFIJO[-6:]}P",
                    "dk": _DATE_KEY,
                },
            )
            await session.execute(
                text("""
                INSERT INTO analytics.dim_rule (rule_sk, rule_id, rule_name, categoria)
                VALUES
                    (:rs, 'CALPEN-R1', 'Frenadas Bruscas', 'Seguridad'),
                    (:rr, 'CALPEN-R2', 'Excesos de RPM', 'Seguridad')
                ON CONFLICT (rule_sk) DO NOTHING
            """),
                {"rs": _RULE_SEG, "rr": _RULE_RPM},
            )
            # Las RPM van en la columna numérica del ETL y también en el texto:
            # `_rpm_value_expression` hace COALESCE entre las dos y así el seed
            # no depende de cuál de las dos ramas esté vigente.
            await session.execute(
                text("""
                INSERT INTO analytics.fact_habito_event
                    (event_sk, event_id, vehicle_id, database_name, date_key, rule_sk,
                     event_type, fecha, placa, fecha_y_hora_del_evento,
                     rpm, observacion_corta)
                VALUES
                    (:es, :es, :v, :db, :dk, :rs, 'Frenadas Bruscas',
                     '2026-06-01', :p, '2026-06-01T10:00:00Z', NULL, NULL),
                    (:ea, :ea, :v, :db, :dk, :rr, 'Excesos de RPM',
                     '2026-06-01', :p, '2026-06-01T11:00:00Z',
                     :rpm_alto, :obs_alto),
                    (:eo, :eo, :v, :db, :dk, :rr, 'Excesos de RPM',
                     '2026-06-01', :p, '2026-06-01T12:00:00Z',
                     :rpm_sobre, :obs_sobre)
                ON CONFLICT (event_sk) DO NOTHING
            """),
                {
                    "es": _EVT_SEG,
                    "ea": _EVT_RPM_ALTO,
                    "eo": _EVT_RPM_SOBRE,
                    "v": _VEH,
                    "db": _DB,
                    "dk": _DATE_KEY,
                    "rs": _RULE_SEG,
                    "rr": _RULE_RPM,
                    "p": f"{_PREFIJO[-6:]}P",
                    "rpm_alto": float(_GOBERNADA + 100),
                    "obs_alto": f"{_GOBERNADA + 100}.0 RPM, 60.0 km/h, Carga: 40.0%",
                    "rpm_sobre": float(_SOBREVELOCIDAD + 100),
                    "obs_sobre": f"{_SOBREVELOCIDAD + 100}.0 RPM, 80.0 km/h, Carga: 60.0%",
                },
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield fleet_id

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "DELETE FROM analytics.fact_habito_event "
                "WHERE event_sk IN (:es, :ea, :eo)"
            ),
            {"es": _EVT_SEG, "ea": _EVT_RPM_ALTO, "eo": _EVT_RPM_SOBRE},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_rule WHERE rule_sk IN (:rs, :rr)"),
            {"rs": _RULE_SEG, "rr": _RULE_RPM},
        )
        await session.execute(
            text("DELETE FROM analytics.fact_combustible_daily WHERE fact_row_id = :f"),
            {"f": _FACT},
        )
        await session.execute(
            text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = :v"), {"v": _VEH}
        )
        await session.execute(delete(Vehicle).where(Vehicle.geotab_device_id == _DEV))
        await session.execute(
            delete(MotorCatalog).where(MotorCatalog.motor_type == _MOTOR)
        )
        await session.execute(delete(Fleet).where(Fleet.code == _PREFIJO))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def sin_calibraciones_previas(request: pytest.FixtureRequest) -> Any:
    """Borra la calibración de la flota del módulo antes de cada prueba.

    La tabla es append-only y la flota es de alcance módulo: sin esto, el orden
    de ejecución decidiría si "penalización activa" mide el default o la
    calibración que dejó la prueba anterior.
    """
    if "flota_con_sobrevelocidad" in request.fixturenames:
        fleet_id = request.getfixturevalue("flota_con_sobrevelocidad")
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM calificacion_config_versions WHERE fleet_id = :fid"),
                {"fid": fleet_id},
            )
            await session.commit()
    yield


@pytest_asyncio.fixture
async def actor() -> Any:
    """El admin bootstrap como actor de las escrituras del servicio."""
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(
                select(User).where(User.email == settings.bootstrap_admin_email)
            )
        ).scalar_one()
        yield user


_RANGO = {"date_from": date(2026, 6, 1), "date_to": date(2026, 6, 30)}


async def _calificacion(fleet_id: uuid.UUID) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        return await analytics_service.get_calificacion(
            session, fleet_ids=[fleet_id], **_RANGO
        )


async def _apagar_penalizacion(fleet_id: uuid.UUID, actor: Any) -> None:
    """Calibra la flota con la penalización apagada, por el camino real.

    `get_calificacion` NO recibe la calibración como parámetro: la resuelve
    desde el alcance de flotas. Inyectar un objeto probaría una ruta que en
    producción no existe.
    """
    async with AsyncSessionLocal() as session:
        await ccs.save_fleet_config(
            session,
            fleet_id=fleet_id,
            config=DEFAULT_CALIFICACION_CONFIG.with_overrides(
                penalizaciones={PENALIZACION_SOBREVELOCIDAD_RPM: False}
            ),
            actor_user_id=actor.id,
            actor_email=actor.email,
        )
        await session.commit()


def _vehiculo(respuesta: dict[str, Any]) -> dict[str, Any]:
    vehiculos = respuesta["vehiculos"]
    assert vehiculos, "el seed no produjo vehículos calificados"
    return vehiculos[0]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_penalizacion_activa_anula_el_puntaje_y_conserva_el_base(
    flota_con_sobrevelocidad: uuid.UUID,
) -> None:
    """Sin calibración propia, un solo exceso deja el QGen en 0.

    `qgen_base` tiene que conservar el puntaje que el vehículo habría tenido:
    el override es explícito y no destruye información. Si `qgen_base` también
    saliera en 0, apagar la penalización no tendría a qué volver.
    """
    respuesta = await _calificacion(flota_con_sobrevelocidad)
    veh = _vehiculo(respuesta)

    assert veh["penalizado_por_sobrevelocidad"] is True
    assert veh["qgen"] == pytest.approx(0.0)
    assert veh["estado"] == "No cumple"
    assert veh["qgen_base"] is not None
    # El seed puntúa alto a propósito: el 0 es del override y de nada más.
    assert veh["qgen_base"] > DEFAULT_CALIFICACION_CONFIG.umbral_cumple, veh["qgen_base"]
    # Los componentes NO se tocan: la penalización es del puntaje general.
    assert veh["qhs"] is not None and veh["qhs"] > 0
    assert veh["qho"] is not None and veh["qho"] > 0
    # El donut y el promedio consumen el valor EFECTIVO, no el base.
    assert respuesta["estado"]["no_cumple"] == 1
    assert respuesta["promedio_general"] == pytest.approx(0.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_penalizacion_apagada_devuelve_el_puntaje_base(
    flota_con_sobrevelocidad: uuid.UUID, actor: Any
) -> None:
    """Mismo dato, penalización apagada: el QGen vuelve a ser `qgen_base`.

    Se compara contra el `qgen_base` de la corrida penalizada y no contra un
    número escrito a mano: lo que se prueba es que apagar la penalización
    devuelve EXACTAMENTE el puntaje que el override estaba tapando, sin
    recalcular nada más por el camino.
    """
    fleet_id = flota_con_sobrevelocidad
    penalizada = _vehiculo(await _calificacion(fleet_id))

    await _apagar_penalizacion(fleet_id, actor)
    respuesta = await _calificacion(fleet_id)
    libre = _vehiculo(respuesta)

    assert libre["penalizado_por_sobrevelocidad"] is False
    assert libre["qgen"] == pytest.approx(penalizada["qgen_base"])
    assert libre["qgen_base"] == pytest.approx(penalizada["qgen_base"])
    assert libre["estado"] != "No cumple"
    assert libre["estado"] == "Cumple", libre["qgen"]
    # Los componentes siguen siendo los mismos: la penalización nunca los tocó.
    assert libre["qhs"] == pytest.approx(penalizada["qhs"])
    assert libre["qho"] == pytest.approx(penalizada["qho"])
    # El donut deja de contarlo en rojo: el estado efectivo es el del puntaje.
    assert dict(respuesta["estado"]) == {"no_cumple": 0, "en_riesgo": 0, "cumple": 1}
    assert respuesta["promedio_general"] == pytest.approx(penalizada["qgen_base"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apagarla_no_oculta_los_excesos(
    flota_con_sobrevelocidad: uuid.UUID, actor: Any
) -> None:
    """EL invariante: apagar la penalización cambia el castigo, no el hecho.

    El conteo de excesos sobre la sobrevelocidad se publica siempre. Una flota
    puede decidir que un exceso no invalide su calificación; no puede decidir
    que el exceso no ocurrió, porque entonces la calibración dejaría de ser una
    perilla de puntaje y se volvería una forma de borrar evidencia mecánica.

    Se comprueban también los otros dos conteos de RPM: la penalización y la
    agravación son cuentas distintas contra columnas distintas del motor, y
    apagar una no puede mover la otra.
    """
    fleet_id = flota_con_sobrevelocidad
    penalizada = _vehiculo(await _calificacion(fleet_id))

    await _apagar_penalizacion(fleet_id, actor)
    libre = _vehiculo(await _calificacion(fleet_id))

    assert penalizada["eventos_rpm_sobre_sobrevelocidad"] == 1
    assert libre["eventos_rpm_sobre_sobrevelocidad"] == 1

    # Los dos eventos de RPM superan la gobernada; sólo uno la sobrevelocidad.
    assert penalizada["eventos_rpm"] == 2
    assert penalizada["eventos_rpm_sobre_gobernada"] == 2
    assert libre["eventos_rpm"] == penalizada["eventos_rpm"]
    assert libre["eventos_rpm_sobre_gobernada"] == penalizada["eventos_rpm_sobre_gobernada"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("apagar", [False, True], ids=["activa", "apagada"])
async def test_la_serie_mensual_dice_lo_mismo_que_la_tabla(
    flota_con_sobrevelocidad: uuid.UUID, actor: Any, apagar: bool
) -> None:
    """El gauge y la evolución tienen que contar la misma historia.

    La bandera se resuelve una vez pero se aplica en DOS lugares: la tabla por
    vehículo y el QGen de cada vehículo-mes de la serie. Un arreglo que toque
    uno y olvide el otro deja la pantalla mostrando 0 en el gauge y el puntaje
    completo en la gráfica, o al revés. Con un solo vehículo en la ventana el
    promedio ponderado del mes es su propio QGen, así que la comparación es
    exacta.
    """
    fleet_id = flota_con_sobrevelocidad
    if apagar:
        await _apagar_penalizacion(fleet_id, actor)

    respuesta = await _calificacion(fleet_id)
    veh = _vehiculo(respuesta)
    meses = respuesta["evolucion"]
    assert len(meses) == 1, meses
    mes = meses[0]

    assert mes["qgen"] == pytest.approx(veh["qgen"])
    assert respuesta["promedio_general"] == pytest.approx(veh["qgen"])
    if apagar:
        assert mes["qgen"] > 0
    else:
        assert mes["qgen"] == pytest.approx(0.0)
    # El QHS del mes no lo toca la penalización en ninguno de los dos estados.
    assert mes["qhs"] == pytest.approx(veh["qhs"])

    # Y el hecho sigue publicado en la serie operativa, penalice o no.
    assert respuesta["operativos"][0]["eventos_rpm"] == 2
    assert respuesta["operativos"][0]["eventos_rpm_sobre_gobernada"] == 2


def test_un_codigo_huerfano_no_cuesta_la_calibracion_entera() -> None:
    """Leer una fila con una penalización retirada conserva el resto.

    `_resolve_row` sólo atrapa `CalificacionConfigError` y, cuando lo hace, cae
    a los defaults COMPLETOS. Si un código desconocido invalidara la fila, el
    día que se retire una penalización del registro cada flota que la tuviera
    anotada perdería en silencio sus pesos, umbrales y topes, y su puntaje
    cambiaría sin que nadie lo hubiera decidido. Una clave huérfana no puede
    costar tanto.

    Por eso la asimetría: al ESCRIBIR se rechaza el código desconocido —el
    cliente no debe inventar códigos y creer que apagó algo—, y al LEER se
    descarta con aviso. La aserción sobre `peso_qhs` es la que caza una
    reversión: si el filtro desaparece, vuelve al 0.5 por defecto.
    """
    from datetime import UTC, datetime

    from app.models.calificacion_config import CalificacionConfigVersion
    from app.services.calificacion_config_service import _resolve_row

    fila = CalificacionConfigVersion(
        fleet_id=uuid.uuid4(),
        is_reset=False,
        peso_qhs=0.7,
        peso_qho=0.3,
        penalizaciones={
            PENALIZACION_SOBREVELOCIDAD_RPM: False,
            "penalizacion_retirada_del_registro": True,
        },
    )
    fila.id = uuid.uuid4()
    fila.created_at = datetime.now(UTC)

    resuelto = _resolve_row(fila)

    assert resuelto.config.peso_qhs == pytest.approx(0.7), (
        "la calibración se perdió entera por una sola clave desconocida"
    )
    assert resuelto.config.peso_qho == pytest.approx(0.3)
    assert resuelto.config.penalizacion_activa(PENALIZACION_SOBREVELOCIDAD_RPM) is False
    assert "penalizacion_retirada_del_registro" not in resuelto.config.penalizaciones


# ===========================================================================
# C. Escribir sigue siendo estricto (el contraste por HTTP)
# ===========================================================================

_CONFIG_URL = "/api/v1/reportes/calificacion/config"
_CODIGO_RETIRADO = "codigo_retirado"


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


def _payload(**overrides: Any) -> dict[str, Any]:
    """La calibración completa, como la envía el formulario."""
    return {**config_to_mapping(DEFAULT_CALIFICACION_CONFIG), **overrides}


@pytest.mark.integration
def test_por_http_un_codigo_desconocido_sigue_siendo_error(
    admin_client: TestClient, flota_con_sobrevelocidad: uuid.UUID
) -> None:
    """La otra mitad de la asimetría: escribir un código inventado se rechaza.

    Tolerar al leer no es tolerar al escribir. Un código mal escrito que se
    aceptara dejaría al cliente creyendo que apagó una penalización mientras el
    puntaje se sigue anulando, y la fila nacería ya huérfana.

    Se comprueba además que la escritura no ocurrió: un 400 que igual guarda es
    peor que no tener el control.
    """
    fleet_id = flota_con_sobrevelocidad
    antes = admin_client.get(_CONFIG_URL, params={"fleet_id": str(fleet_id)})
    assert antes.status_code == 200, antes.text

    resp = admin_client.put(
        _CONFIG_URL,
        params={"fleet_id": str(fleet_id)},
        json=_payload(
            eventos_cap=12.0,
            penalizaciones={
                PENALIZACION_SOBREVELOCIDAD_RPM: False,
                _CODIGO_RETIRADO: True,
            },
        ),
    )
    assert resp.status_code == 400, (
        f"el contrato pide 400 con el mensaje tal cual; llegó {resp.status_code}: {resp.text}"
    )
    assert _CODIGO_RETIRADO in resp.text, resp.text

    despues = admin_client.get(_CONFIG_URL, params={"fleet_id": str(fleet_id)})
    assert despues.json() == antes.json(), "el 400 escribió de todas formas"
