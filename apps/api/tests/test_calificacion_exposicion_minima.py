"""Exposición mínima: un vehículo que casi no operó no recibe puntaje.

QHS y QHO son TASAS —eventos por 1000 km, tiempo sobre tiempo—. Con un
denominador minúsculo dejan de describir cómo se conduce y describen que el
vehículo no se movió: es imposible registrar un evento de seguridad en 200
metros, así que el QHS de un vehículo con 0,2 km sale 100 por construcción.

El caso que motivó la regla (Cementos San Marcos, julio de 2026): tres de cuatro
vehículos penalizados por sobrevelocidad con QGen 0, y el cuarto con 93,5 sobre
0,2 km recorridos. El promedio de la flota daba 0,003 y parecía ignorar al
vehículo sano; en realidad lo ponderaba por su exposición, que era el 0,003 % de
la flota. Las dos lecturas eran confusas y la segunda no era una medición.

Estas pruebas fijan los cuatro invariantes que no se deben revertir:

- por debajo del mínimo el vehículo queda SIN calificar (`qgen = None`), NO en
  0: no operar no es operar mal, y un 0 lo mezclaría con los penalizados;
- `qgen_base` conserva lo que habría puntuado, y la fila sigue en la tabla;
- **la penalización GANA** sobre la exposición insuficiente: el exceso ocurrió,
  y dejar de reportarlo porque el vehículo rodó poco sería premiar la falta de
  kilómetros;
- el mínimo es POR DÍA del periodo consultado y 0 lo desactiva.

El mismo archivo cubre el otro parámetro que salió de ese diagnóstico:
`promedio_ponderado`, el modo de agregación de la cifra de flota. Ponderar por
exposición y promediar a un vehículo un voto responden preguntas distintas
—"¿cómo se condujo esta flota?" contra "¿cómo van mis vehículos?"— y ninguno de
los dos es el correcto, así que la elección es del cliente. El default conserva
el ponderado, que es el comportamiento histórico.

Bloque A sin base (configuración y funciones puras), bloque B contra la base.
Convenciones y forma del seed tomadas de `test_calificacion_penalizaciones.py`;
fixture propio con sufijos aleatorios para no alterar los conteos de ese módulo.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
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
    CalificacionConfigError,
    config_from_mapping,
    config_to_mapping,
    validate_config,
)

# ===========================================================================
# A. Configuración y funciones puras — sin base
# ===========================================================================


def test_el_default_trae_un_minimo_y_es_por_dia() -> None:
    """La regla nace ENCENDIDA y con la unidad de cada tipo de operación.

    Si naciera en 0, el defecto que motivó el cambio seguiría vivo para toda
    flota sin calibración propia, que son todas menos una.
    """
    config = DEFAULT_CALIFICACION_CONFIG
    assert config.exposicion_minima_km_dia > 0
    assert config.exposicion_minima_horas_dia > 0
    # Un comercial se mide en km y un vocacional en horas ECM, igual que en
    # `rpm_cap`: mezclar las unidades es el error que la separación evita.
    assert config.exposicion_minima_dia(vocacional=False) == config.exposicion_minima_km_dia
    assert config.exposicion_minima_dia(vocacional=True) == config.exposicion_minima_horas_dia


def test_cero_es_valido_y_significa_sin_minimo() -> None:
    """Cero no es un valor inválido: es la salida explícita para una flota.

    Rechazarlo dejaría a un cliente sin forma de volver al comportamiento
    anterior salvo pidiendo un cambio de código.
    """
    apagada = DEFAULT_CALIFICACION_CONFIG.with_overrides(
        exposicion_minima_km_dia=0.0, exposicion_minima_horas_dia=0.0
    )
    validate_config(apagada)
    assert (
        analytics_service._exposicion_minima(
            vocacional=False, dias=30, config=apagada
        )
        is None
    )


def test_un_minimo_negativo_se_rechaza_con_mensaje_util() -> None:
    for campo in ("exposicion_minima_km_dia", "exposicion_minima_horas_dia"):
        invalida = DEFAULT_CALIFICACION_CONFIG.with_overrides(**{campo: -1.0})
        with pytest.raises(CalificacionConfigError) as exc:
            validate_config(invalida)
        mensaje = str(exc.value)
        assert campo in mensaje, mensaje
        # El texto lo lee quien está calibrando: tiene que decir cómo apagarla.
        assert "0" in mensaje, mensaje


def test_roundtrip_conserva_los_dos_minimos() -> None:
    """Se escriben y se leen por el camino real (`config_to_mapping`/`_from_`).

    Si se perdieran en el trayecto, una flota que subió el mínimo lo vería
    revertido al recargar, sin que nadie lo hubiera decidido.
    """
    config = DEFAULT_CALIFICACION_CONFIG.with_overrides(
        exposicion_minima_km_dia=12.5, exposicion_minima_horas_dia=1.25
    )
    mapping = config_to_mapping(config)
    assert mapping["exposicion_minima_km_dia"] == 12.5
    assert mapping["exposicion_minima_horas_dia"] == 1.25
    assert config_from_mapping(mapping) == config


def test_clave_ausente_cae_al_default_no_a_cero() -> None:
    """Una fila escrita antes de esta columna sigue siendo legible.

    Y cae al default del código, no a 0: si cayera a 0, aplicar la migración
    habría dejado la regla apagada en toda flota ya calibrada.
    """
    config = config_from_mapping({})
    assert config.exposicion_minima_km_dia == DEFAULT_CALIFICACION_CONFIG.exposicion_minima_km_dia
    assert (
        config.exposicion_minima_horas_dia
        == DEFAULT_CALIFICACION_CONFIG.exposicion_minima_horas_dia
    )


def test_el_minimo_escala_con_los_dias_del_periodo() -> None:
    """Por día y no absoluto: la pantalla consulta rangos de largo variable.

    Un mínimo fijo en kilómetros sería indulgente en tres meses y absurdo en
    una semana.
    """
    config = DEFAULT_CALIFICACION_CONFIG.with_overrides(exposicion_minima_km_dia=5.0)
    assert analytics_service._exposicion_minima(vocacional=False, dias=1, config=config) == 5.0
    assert analytics_service._exposicion_minima(vocacional=False, dias=30, config=config) == 150.0
    # Sin días conocidos la regla no se puede enunciar y no se aplica.
    for dias in (None, 0, -3):
        assert (
            analytics_service._exposicion_minima(
                vocacional=False, dias=dias, config=config
            )
            is None
        )


@pytest.mark.parametrize(
    ("inicio", "fin", "desde", "hasta", "esperado"),
    [
        # Mes entero, sin recorte.
        pytest.param(date(2026, 7, 1), date(2026, 7, 31), None, None, 31, id="mes_completo"),
        # El rango por defecto de la pestaña son "tres meses atrás → hoy", así
        # que el primer y el último mes casi siempre vienen a medias.
        pytest.param(
            date(2026, 9, 1), date(2026, 9, 30), date(2026, 6, 15), date(2026, 9, 1), 1,
            id="ultimo_mes_de_un_dia",
        ),
        pytest.param(
            date(2026, 6, 1), date(2026, 6, 30), date(2026, 6, 15), date(2026, 9, 1), 16,
            id="primer_mes_recortado",
        ),
        # Un mes fuera del rango no aporta días (y así no exige exposición).
        pytest.param(
            date(2026, 5, 1), date(2026, 5, 31), date(2026, 6, 1), date(2026, 8, 31), 0,
            id="mes_fuera_del_rango",
        ),
    ],
)
def test_dias_en_rango_recorta_los_meses_de_los_bordes(
    inicio: date, fin: date, desde: date | None, hasta: date | None, esperado: int
) -> None:
    """Cobrarle a un mes de un día la exposición de un mes entero dejaría sin
    calificar a un vehículo que sí operó."""
    assert analytics_service._dias_en_rango(inicio, fin, desde, hasta) == esperado


@pytest.mark.parametrize(
    ("km", "horas", "vocacional", "minimo", "esperado"),
    [
        pytest.param(1000.0, 20.0, False, 150.0, True, id="comercial_suficiente"),
        pytest.param(0.2, 0.9, False, 150.0, False, id="comercial_insuficiente"),
        # Justo en el mínimo cuenta: el umbral es inclusivo.
        pytest.param(150.0, 3.0, False, 150.0, True, id="comercial_en_el_limite"),
        # El vocacional mira HORAS, no kilómetros: con muchos km y pocas horas
        # sigue siendo insuficiente, y al revés.
        pytest.param(5000.0, 1.0, True, 15.0, False, id="vocacional_mira_horas"),
        pytest.param(0.0, 40.0, True, 15.0, True, id="vocacional_sin_km_pero_con_horas"),
        # Fail-open: sin mínimo aplicable todo vehículo se califica. La regla
        # existe para no publicar un puntaje indefinido, no para esconder filas.
        pytest.param(0.2, 0.9, False, None, True, id="sin_minimo_todo_pasa"),
        pytest.param(None, None, False, 150.0, False, id="sin_datos"),
    ],
)
def test_exposicion_suficiente(
    km: float | None,
    horas: float | None,
    vocacional: bool,
    minimo: float | None,
    esperado: bool,
) -> None:
    assert (
        analytics_service._exposicion_suficiente(
            km=km, horas=horas, vocacional=vocacional, minimo=minimo
        )
        is esperado
    )


# ===========================================================================
# B. El cálculo — contra la base
# ===========================================================================

_PREFIJO = f"CALEXP-{uuid.uuid4().hex[:8].upper()}"
_DB = f"calexp_{uuid.uuid4().hex[:8]}"
_MOTOR = f"CALEXP-M-{uuid.uuid4().hex[:6]}"
_RULE_SEG = f"CALEXP-RS-{uuid.uuid4().hex[:8]}"
_RULE_RPM = f"CALEXP-RR-{uuid.uuid4().hex[:8]}"

_GOBERNADA = 2000
_SOBREVELOCIDAD = 2500

# Junio completo: 30 días. Con el default de 5 km/día el mínimo son 150 km.
_DATE_KEY = 20260601
_RANGO = {"date_from": date(2026, 6, 1), "date_to": date(2026, 6, 30)}
_MINIMO_ESPERADO = 150.0

# Tres vehículos, uno por invariante.
_VEHS = {
    # Opera de sobra: es la referencia del promedio.
    "activo": {"km": 1000.0, "hrs": 20.0, "sobre": False, "evento_seguridad": True},
    # 5 km en 30 días = 0,17 km/día: por debajo del mínimo y SIN penalización.
    # Es el vehículo del caso real, con su puntaje base inflado por no tener
    # ningún evento en 5 km.
    "parado": {"km": 5.0, "hrs": 0.3, "sobre": False, "evento_seguridad": False},
    # Poca exposición Y un exceso sobre la sobrevelocidad: la penalización gana.
    "parado_penalizado": {
        "km": 5.0,
        "hrs": 0.3,
        "sobre": True,
        "evento_seguridad": False,
    },
}


def _ids(nombre: str) -> dict[str, str]:
    corto = uuid.uuid5(uuid.NAMESPACE_OID, f"{_PREFIJO}-{nombre}").hex[:8]
    return {
        "vehicle": f"CALEXP-V-{corto}",
        "device": f"CALEXP-D-{corto}",
        "plate": f"CXP{corto[:5].upper()}",
        "fact": str(uuid.uuid5(uuid.NAMESPACE_OID, f"{_PREFIJO}-f-{nombre}")),
        "evt_seg": str(uuid.uuid5(uuid.NAMESPACE_OID, f"{_PREFIJO}-es-{nombre}")),
        "evt_sobre": str(uuid.uuid5(uuid.NAMESPACE_OID, f"{_PREFIJO}-eo-{nombre}")),
    }


_IDS = {nombre: _ids(nombre) for nombre in _VEHS}


@pytest_asyncio.fixture(scope="module")
async def flota_con_exposicion_dispar() -> Any:
    """Una flota con un vehículo que operó y dos que apenas se movieron.

    Los datos de combustible del vehículo activo lo hacen puntuar ALTO (75 % del
    tiempo en rango eficiente contra una meta de 70 %, 10 % de ralentí, un solo
    evento de seguridad en 1000 km). Los dos parados llevan los MISMOS
    porcentajes y ningún evento: su puntaje base sale igual de alto o más, que es
    justo la distorsión que la regla ataja —con 5 km de denominador el QHS no
    puede salir de 100—.
    """
    async with AsyncSessionLocal() as session:
        try:
            fleet = Fleet(code=_PREFIJO, name="Flota exposición mínima", is_active=True)
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
            for nombre in _VEHS:
                ids = _IDS[nombre]
                session.add(
                    Vehicle(
                        plate=ids["plate"],
                        geotab_device_id=ids["device"],
                        geotab_customer_status="found",
                        fleet_id=fleet_id,
                        geotab_database_id=geotab_db.id,
                        is_active=True,
                    )
                )
            # El límite de sobrevelocidad NO es calibrable: viene del maestro.
            # Sin esta fila el fail-open deja al tercer vehículo sin penalizar y
            # la prueba de precedencia mediría otra cosa.
            session.add(
                MotorCatalog(
                    motor_type=_MOTOR,
                    description="Motor de prueba de exposición mínima",
                    governed_speed_rpm=_GOBERNADA,
                    max_overspeed_rpm=_SOBREVELOCIDAD,
                )
            )

            await session.execute(text("CREATE SCHEMA IF NOT EXISTS analytics"))
            # Alembic no gobierna `analytics`: las tablas las crea el loader del
            # ETL y aquí se crean desde los mismos modelos, con `checkfirst`.
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
                INSERT INTO analytics.dim_rule (rule_sk, rule_id, rule_name, categoria)
                VALUES
                    (:rs, 'CALEXP-R1', 'Frenadas Bruscas', 'Seguridad'),
                    (:rr, 'CALEXP-R2', 'Excesos de RPM', 'Seguridad')
                ON CONFLICT (rule_sk) DO NOTHING
            """),
                {"rs": _RULE_SEG, "rr": _RULE_RPM},
            )

            for nombre, perfil in _VEHS.items():
                ids = _IDS[nombre]
                await session.execute(
                    text("""
                    INSERT INTO analytics.dim_vehicle
                        (vehicle_id, device_id, vehicle_label, database_name, motor_type,
                         fuel_kind, fuel_unit, is_active)
                    VALUES (:v, :d, 'Cal Exp', :db, :motor, 'liquid', 'gal', true)
                    ON CONFLICT (vehicle_id) DO UPDATE SET
                        device_id = EXCLUDED.device_id,
                        database_name = EXCLUDED.database_name,
                        motor_type = EXCLUDED.motor_type
                """),
                    {"v": ids["vehicle"], "d": ids["device"], "db": _DB, "motor": _MOTOR},
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
                         :km, :km, :km, :hrs, :hrs, 120.0,
                         'ecm', 'ok', 'ok', true,
                         0.50, 0.25, 0.0,
                         0.10, :hrs, 'ecm',
                         15.0, false)
                    ON CONFLICT (fact_row_id) DO NOTHING
                """),
                    {
                        "f": ids["fact"],
                        "v": ids["vehicle"],
                        "db": _DB,
                        "motor": _MOTOR,
                        "p": ids["plate"],
                        "dk": _DATE_KEY,
                        "km": perfil["km"],
                        "hrs": perfil["hrs"],
                    },
                )
                # La frenada brusca va SÓLO en el vehículo activo: con 1000 km
                # su QHS no sale saturado en 100. Los parados no llevan ninguno
                # a propósito, que es el caso real —es imposible registrar un
                # evento en 5 km, así que su QHS sale 100 por construcción y su
                # puntaje base queda inflado—.
                if perfil["evento_seguridad"]:
                    await session.execute(
                        text("""
                        INSERT INTO analytics.fact_habito_event
                            (event_sk, event_id, vehicle_id, database_name, date_key, rule_sk,
                             event_type, fecha, placa, fecha_y_hora_del_evento,
                             rpm, observacion_corta)
                        VALUES
                            (:es, :es, :v, :db, :dk, :rs, 'Frenadas Bruscas',
                             '2026-06-01', :p, '2026-06-01T10:00:00Z', NULL, NULL)
                        ON CONFLICT (event_sk) DO NOTHING
                    """),
                        {
                            "es": ids["evt_seg"],
                            "v": ids["vehicle"],
                            "db": _DB,
                            "dk": _DATE_KEY,
                            "rs": _RULE_SEG,
                            "p": ids["plate"],
                        },
                    )
                if perfil["sobre"]:
                    await session.execute(
                        text("""
                        INSERT INTO analytics.fact_habito_event
                            (event_sk, event_id, vehicle_id, database_name, date_key, rule_sk,
                             event_type, fecha, placa, fecha_y_hora_del_evento,
                             rpm, observacion_corta)
                        VALUES
                            (:eo, :eo, :v, :db, :dk, :rr, 'Excesos de RPM',
                             '2026-06-01', :p, '2026-06-01T12:00:00Z',
                             :rpm, :obs)
                        ON CONFLICT (event_sk) DO NOTHING
                    """),
                        {
                            "eo": ids["evt_sobre"],
                            "v": ids["vehicle"],
                            "db": _DB,
                            "dk": _DATE_KEY,
                            "rr": _RULE_RPM,
                            "p": ids["plate"],
                            "rpm": float(_SOBREVELOCIDAD + 100),
                            "obs": f"{_SOBREVELOCIDAD + 100}.0 RPM, 80.0 km/h, Carga: 60.0%",
                        },
                    )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    yield fleet_id

    async with AsyncSessionLocal() as session:
        for ids in _IDS.values():
            await session.execute(
                text(
                    "DELETE FROM analytics.fact_habito_event "
                    "WHERE event_sk IN (:es, :eo)"
                ),
                {"es": ids["evt_seg"], "eo": ids["evt_sobre"]},
            )
            await session.execute(
                text("DELETE FROM analytics.fact_combustible_daily WHERE fact_row_id = :f"),
                {"f": ids["fact"]},
            )
            await session.execute(
                text("DELETE FROM analytics.dim_vehicle WHERE vehicle_id = :v"),
                {"v": ids["vehicle"]},
            )
            await session.execute(
                delete(Vehicle).where(Vehicle.geotab_device_id == ids["device"])
            )
        await session.execute(
            text("DELETE FROM analytics.dim_rule WHERE rule_sk IN (:rs, :rr)"),
            {"rs": _RULE_SEG, "rr": _RULE_RPM},
        )
        await session.execute(delete(MotorCatalog).where(MotorCatalog.motor_type == _MOTOR))
        await session.execute(delete(Fleet).where(Fleet.code == _PREFIJO))
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def sin_calibraciones_previas(request: pytest.FixtureRequest) -> Any:
    """La tabla es append-only y la flota es de alcance módulo: sin esto el
    orden de ejecución decidiría qué calibración mide cada prueba."""
    if "flota_con_exposicion_dispar" in request.fixturenames:
        fleet_id = request.getfixturevalue("flota_con_exposicion_dispar")
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("DELETE FROM calificacion_config_versions WHERE fleet_id = :fid"),
                {"fid": fleet_id},
            )
            await session.commit()
    yield


@pytest_asyncio.fixture
async def actor() -> Any:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(
                select(User).where(User.email == settings.bootstrap_admin_email)
            )
        ).scalar_one()
        yield user


async def _calificacion(fleet_id: uuid.UUID, **kwargs: Any) -> dict[str, Any]:
    rango = {**_RANGO, **kwargs}
    async with AsyncSessionLocal() as session:
        return await analytics_service.get_calificacion(
            session, fleet_ids=[fleet_id], **rango
        )


def _por_placa(respuesta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    filas = {v["placa"]: v for v in respuesta["vehiculos"]}
    assert len(filas) == len(_VEHS), f"el seed no produjo los 3 vehículos: {list(filas)}"
    return filas


def _fila(respuesta: dict[str, Any], nombre: str) -> dict[str, Any]:
    return _por_placa(respuesta)[_IDS[nombre]["plate"]]


async def _calibrar(fleet_id: uuid.UUID, actor: Any, **overrides: Any) -> None:
    """Calibra por el camino real: `get_calificacion` resuelve la calibración
    desde el alcance de flotas, no la recibe como parámetro."""
    async with AsyncSessionLocal() as session:
        await ccs.save_fleet_config(
            session,
            fleet_id=fleet_id,
            config=DEFAULT_CALIFICACION_CONFIG.with_overrides(**overrides),
            actor_user_id=actor.id,
            actor_email=actor.email,
        )
        await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_el_vehiculo_parado_no_se_califica_pero_conserva_su_base(
    flota_con_exposicion_dispar: uuid.UUID,
) -> None:
    """`qgen = None`, NO 0, y `qgen_base` intacto.

    Un 0 lo mezclaría en el donut con los penalizados, que es una falta; esto
    es ausencia de datos. Y la fila se queda en la tabla: el usuario tiene que
    poder ver que el vehículo existe y por qué no tiene nota.
    """
    respuesta = await _calificacion(flota_con_exposicion_dispar)
    parado = _fila(respuesta, "parado")

    assert parado["exposicion_suficiente"] is False
    assert parado["qgen"] is None
    assert parado["estado"] is None
    assert parado["penalizado_por_sobrevelocidad"] is False
    # Su base es ALTA, y por la razón equivocada: con 5 km de denominador el
    # QHS no puede salir de 100. Es exactamente el 93,5 del caso real.
    assert parado["qgen_base"] is not None
    assert parado["qgen_base"] > DEFAULT_CALIFICACION_CONFIG.umbral_cumple

    detalle = parado["detalle"]
    assert detalle["exposicion_suficiente"] is False
    assert detalle["exposicion_minima"] == pytest.approx(_MINIMO_ESPERADO)
    assert detalle["dias_periodo"] == 30
    # El motivo va PRIMERO: es la razón de que no haya nota.
    assert detalle["motivos"], "un vehículo sin calificar tiene que decir por qué"
    assert "No se califica" in detalle["motivos"][0], detalle["motivos"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_el_promedio_es_el_del_vehiculo_que_si_opero(
    flota_con_exposicion_dispar: uuid.UUID,
) -> None:
    """El defecto original: el gauge en 0 con un vehículo sano en la tabla.

    Antes, el vehículo parado entraba al promedio ponderado con un peso
    minúsculo y los penalizados se llevaban todo el peso con 0 puntos, así que
    la flota mostraba ~0 y parecía ignorar al vehículo sano. Ahora el promedio
    lo forman sólo los vehículos que se pueden calificar.
    """
    respuesta = await _calificacion(flota_con_exposicion_dispar)
    filas = _por_placa(respuesta)
    activo = filas[_IDS["activo"]["plate"]]
    penalizado = filas[_IDS["parado_penalizado"]["plate"]]

    evaluados = [v for v in respuesta["vehiculos"] if v["qgen"] is not None]
    assert {v["placa"] for v in evaluados} == {activo["placa"], penalizado["placa"]}

    # El penalizado aporta 0 con el peso de sus 5 km; el activo aporta su nota
    # con el peso de sus 1000 km. El promedio queda pegado al activo, no a 0.
    assert respuesta["promedio_general"] is not None
    assert respuesta["promedio_general"] > DEFAULT_CALIFICACION_CONFIG.umbral_en_riesgo
    assert respuesta["promedio_general"] < activo["qgen"]

    # El donut cuenta al activo y al penalizado, nunca al parado.
    estado = respuesta["estado"]
    assert estado["no_cumple"] + estado["en_riesgo"] + estado["cumple"] == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_la_penalizacion_gana_sobre_la_exposicion_insuficiente(
    flota_con_exposicion_dispar: uuid.UUID,
) -> None:
    """Un exceso ocurrido se reporta aunque el vehículo casi no haya rodado.

    Invertir este orden premiaría la falta de kilómetros: bastaría con no operar
    para que la sobrevelocidad dejara de contar. El booleano de exposición sigue
    diciendo la verdad —rodó poco— y no se falsea para dar la precedencia.
    """
    respuesta = await _calificacion(flota_con_exposicion_dispar)
    fila = _fila(respuesta, "parado_penalizado")

    assert fila["penalizado_por_sobrevelocidad"] is True
    assert fila["qgen"] == pytest.approx(0.0)
    assert fila["estado"] == "No cumple"
    assert fila["eventos_rpm_sobre_sobrevelocidad"] == 1
    # El hecho de la exposición se publica tal cual: no se maquilla para que la
    # penalización tenga precedencia.
    assert fila["exposicion_suficiente"] is False
    # Pero el motivo del puntaje es la penalización, no la exposición.
    assert "sobrevelocidad" in fila["detalle"]["motivos"][0]
    assert not any("No se califica" in m for m in fila["detalle"]["motivos"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_el_peso_en_el_promedio_se_publica_y_suma_uno(
    flota_con_exposicion_dispar: uuid.UUID,
) -> None:
    """El gauge tiene que ser auditable desde la tabla.

    Es la mitad explicativa del arreglo: el usuario que ve un puntaje alto y un
    promedio bajo necesita el número que los reconcilia.
    """
    respuesta = await _calificacion(flota_con_exposicion_dispar)
    filas = _por_placa(respuesta)

    assert filas[_IDS["parado"]["plate"]]["peso_en_promedio"] == pytest.approx(0.0)
    pesos = [v["peso_en_promedio"] for v in respuesta["vehiculos"] if v["qgen"] is not None]
    assert sum(pesos) == pytest.approx(1.0)
    # 1000 km contra 5 km: el activo domina el promedio.
    assert filas[_IDS["activo"]["plate"]]["peso_en_promedio"] > 0.99


@pytest.mark.integration
@pytest.mark.asyncio
async def test_la_serie_mensual_aplica_la_misma_regla(
    flota_con_exposicion_dispar: uuid.UUID,
) -> None:
    """Si sólo se aplicara en la tabla, el gauge y la evolución se
    contradirían: es el mismo invariante que fija la penalización."""
    respuesta = await _calificacion(flota_con_exposicion_dispar)
    assert len(respuesta["evolucion"]) == 1
    mes = respuesta["evolucion"][0]
    assert mes["qgen"] == pytest.approx(respuesta["promedio_general"])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_minimo_en_cero_devuelve_el_comportamiento_anterior(
    flota_con_exposicion_dispar: uuid.UUID, actor: Any
) -> None:
    """Apagar la regla es una decisión disponible para la flota, y funciona.

    Con 0 el vehículo parado vuelve a puntuar y a entrar al promedio, que es
    exactamente el comportamiento que tenía la calificación antes.
    """
    await _calibrar(
        flota_con_exposicion_dispar,
        actor,
        exposicion_minima_km_dia=0.0,
        exposicion_minima_horas_dia=0.0,
    )
    respuesta = await _calificacion(flota_con_exposicion_dispar)
    parado = _fila(respuesta, "parado")

    assert parado["exposicion_suficiente"] is True
    assert parado["qgen"] == pytest.approx(parado["qgen_base"])
    assert parado["estado"] is not None
    assert parado["detalle"]["exposicion_minima"] is None
    # Y la metodología publicada lo dice: el texto describe la flota que se ve.
    assert "Sin exposición mínima" in respuesta["metodologia"]["exposicion_minima"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_un_minimo_alto_deja_fuera_incluso_al_activo(
    flota_con_exposicion_dispar: uuid.UUID, actor: Any
) -> None:
    """La calibración manda de verdad, y en los dos sentidos.

    Con 100 km/día el mínimo de junio son 3000 km y ni el vehículo de 1000 km
    llega: el promedio se queda sólo con el penalizado, en 0. Es el caso que
    justifica el texto de advertencia de la pantalla de calibración.
    """
    await _calibrar(flota_con_exposicion_dispar, actor, exposicion_minima_km_dia=100.0)
    respuesta = await _calificacion(flota_con_exposicion_dispar)
    activo = _fila(respuesta, "activo")

    assert activo["exposicion_suficiente"] is False
    assert activo["qgen"] is None
    assert activo["detalle"]["exposicion_minima"] == pytest.approx(3000.0)
    assert respuesta["promedio_general"] == pytest.approx(0.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sin_extremos_de_fecha_la_regla_no_se_aplica(
    flota_con_exposicion_dispar: uuid.UUID,
) -> None:
    """Sin periodo conocido no hay forma de decir cuánto es "poco".

    Una regla que no se puede enunciar no se aplica: un consumidor del API que
    no manda fechas conserva el comportamiento anterior en vez de recibir un
    puntaje recortado por un mínimo implícito.
    """
    respuesta = await _calificacion(
        flota_con_exposicion_dispar, date_from=None, date_to=None
    )
    parado = _fila(respuesta, "parado")

    assert parado["exposicion_suficiente"] is True
    assert parado["qgen"] is not None
    assert parado["detalle"]["exposicion_minima"] is None
    assert parado["detalle"]["dias_periodo"] is None


# ---------------------------------------------------------------------------
# Modo de agregación: ponderado por exposición o simple
# ---------------------------------------------------------------------------


def test_el_default_agrega_ponderado_por_exposicion() -> None:
    """El comportamiento histórico es el default, y es un bool de verdad.

    `float(True)` daría 1.0 y el dataclass quedaría con un float donde declara
    un bool: la igualdad entre dos calibraciones equivalentes empezaría a
    depender de por dónde se construyó cada una, y `_comparable` de la regla
    multi-flota declararía "mixto" sin que hubiera nada mezclado.
    """
    assert DEFAULT_CALIFICACION_CONFIG.promedio_ponderado is True
    desde_mapa = config_from_mapping({"promedio_ponderado": False})
    assert desde_mapa.promedio_ponderado is False
    assert isinstance(desde_mapa.promedio_ponderado, bool)
    # Y sobrevive el viaje de escritura y lectura de la fila.
    assert config_from_mapping(config_to_mapping(desde_mapa)) == desde_mapa


def test_el_modo_simple_es_un_vehiculo_un_voto() -> None:
    """La función pura, sin base: el ponderado sigue a los pesos y el simple no."""
    pares = [(100.0, 1000.0), (0.0, 1.0)]
    ponderado = analytics_service._promedio_ponderado(pares, ponderado=True)
    simple = analytics_service._promedio_ponderado(pares, ponderado=False)
    assert ponderado is not None and ponderado > 99.0
    assert simple == pytest.approx(50.0)
    # Sin pesos, el ponderado cae al simple en vez de descartar filas.
    assert analytics_service._promedio_ponderado(
        [(80.0, 0.0), (60.0, 0.0)], ponderado=True
    ) == pytest.approx(70.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_el_modo_simple_cambia_el_gauge_y_la_serie_a_la_vez(
    flota_con_exposicion_dispar: uuid.UUID, actor: Any
) -> None:
    """La bandera se aplica en los DOS agregados o en ninguno.

    Con el ponderado el activo (1000 km) domina y el penalizado (5 km) casi no
    pesa; con el simple los dos valen igual y la cifra se desploma. Si sólo se
    aplicara en el gauge, la evolución mensual mostraría otra cosa en la misma
    pantalla.
    """
    ponderada = await _calificacion(flota_con_exposicion_dispar)
    await _calibrar(flota_con_exposicion_dispar, actor, promedio_ponderado=False)
    simple = await _calificacion(flota_con_exposicion_dispar)

    activo = _fila(ponderada, "activo")
    evaluados = [v["qgen"] for v in simple["vehiculos"] if v["qgen"] is not None]
    assert len(evaluados) == 2, "la bandera no debe cambiar QUIÉN entra al promedio"

    assert simple["promedio_general"] == pytest.approx(sum(evaluados) / len(evaluados))
    assert simple["promedio_general"] < ponderada["promedio_general"]
    # El gauge y la serie del mismo periodo tienen que coincidir en los dos modos.
    assert simple["evolucion"][0]["qgen"] == pytest.approx(simple["promedio_general"])
    assert ponderada["evolucion"][0]["qgen"] == pytest.approx(
        ponderada["promedio_general"]
    )

    # Un vehículo un voto también en el peso publicado, que es lo que la ficha
    # muestra para explicar el promedio.
    for fila in simple["vehiculos"]:
        esperado = 0.0 if fila["qgen"] is None else 0.5
        assert fila["peso_en_promedio"] == pytest.approx(esperado)
    assert _fila(ponderada, "activo")["peso_en_promedio"] > 0.99

    # El puntaje por vehículo NO se toca: es una decisión de agregación.
    assert _fila(simple, "activo")["qgen"] == pytest.approx(activo["qgen"])
    # Y la metodología publicada describe el modo efectivo.
    assert "promedio SIMPLE" in simple["metodologia"]["agregacion"]
    assert "según exposición" in ponderada["metodologia"]["agregacion"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_el_modo_simple_no_resucita_al_vehiculo_sin_exposicion(
    flota_con_exposicion_dispar: uuid.UUID, actor: Any
) -> None:
    """Las dos reglas son independientes y no se deben acoplar.

    Con el promedio simple sería tentador dejar entrar al vehículo parado —"si
    todos valen igual, ¿por qué excluirlo?"—, y sería el peor de los dos mundos:
    su puntaje sigue siendo una tasa sin denominador, y ahora pesaría tanto como
    el de un vehículo que sí operó.
    """
    await _calibrar(flota_con_exposicion_dispar, actor, promedio_ponderado=False)
    respuesta = await _calificacion(flota_con_exposicion_dispar)
    parado = _fila(respuesta, "parado")

    assert parado["exposicion_suficiente"] is False
    assert parado["qgen"] is None
    assert parado["peso_en_promedio"] == pytest.approx(0.0)
