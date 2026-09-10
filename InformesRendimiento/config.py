import os

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Destino analytics (capa load/): PostgreSQL leído por el Portal Clientes.
# Si ANALYTICS_DB_URL no está, la capa load/ se omite sin romper el pipeline.
# ---------------------------------------------------------------------------
ANALYTICS_DB_URL = os.environ.get('ANALYTICS_DB_URL')
ANALYTICS_DB_SCHEMA = os.environ.get('ANALYTICS_DB_SCHEMA', 'analytics')


# ---------------------------------------------------------------------------
# Data maestra: fuente de verdad en la DB del portal (config_source). Si no hay
# DB disponible se usa el fallback hardcoded de este archivo. El fallback NO es
# idéntico al estado de la DB: la DB renombra 'demo_fleet'->'fleet_colombia' y
# omite el vehículo inactivo TLK282 (deltas acordados).
# ---------------------------------------------------------------------------
import config_source  # noqa: E402

_master = config_source.load()


def _load_credentials(db_key: str) -> list:
    """
    Lee credenciales numeradas (USER_1/PASS_1, USER_2/PASS_2, ...) del .env.
    Retorna list[dict] con al menos 1 elemento.
    Lanza ValueError si no encuentra ninguna credencial.
    """
    prefix = db_key.upper().replace('-', '_')
    creds = []
    i = 1
    while True:
        user = os.environ.get(f'{prefix}_USER_{i}')
        pwd = os.environ.get(f'{prefix}_PASS_{i}')
        if not user or not pwd:
            break
        creds.append({'username': user, 'password': pwd})
        i += 1
    if not creds:
        raise ValueError(f"No se encontraron credenciales para '{db_key}' en .env")
    return creds


def _vehicle(
    database_name: str | None,
    device_id: str,
    placa: str,
    motor_type: str,
    group_key: str | None,
    rpm_class: str | None = None,
    tipo_combustible: str | None = None,
    range_mode: str = 'reglas',
    fleet_code: str | None = None,
    ralenti_analysis: bool = False,
) -> dict:
    return {
        'database_name': database_name,
        'device_id': device_id,
        'placa': placa,
        'motor_type': motor_type,
        'group_key': group_key,
        'rpm_class': rpm_class,
        'tipo_combustible': tipo_combustible,
        # De dónde salen las bandas de este vehículo: 'reglas' (ExceptionEvent
        # por regla Geotab) o 'rpm' (cortes del eje de revoluciones del motor).
        # Lo define la FLOTA en Navi Vehículos; el fallback hardcoded es 'reglas'.
        'range_mode': range_mode,
        'fleet_code': fleet_code,
        # "Análisis de Ralentí" contratado por la flota (fleets.ralenti_analysis_enabled).
        # El fallback hardcoded no lo tiene: sin data maestra no se extrae ralentí.
        'ralenti_analysis': ralenti_analysis,
    }


def _hardcoded_credentials() -> dict:
    return {
        'demo_fleet': _load_credentials('demo_fleet'),
        'navitrans': _load_credentials('navitrans'),
    }


# Las credenciales solo se leen del .env si no hay DB (evita exigir .env en
# modo DB). En modo DB vienen descifradas desde config_source.
CREDENTIALS = _master['credentials'] if _master else _hardcoded_credentials()


# Catálogo de vehículos hardcoded (fallback). En modo DB la fuente es
# config_source (sin TLK282, con 'fleet_colombia').
_VEHICLE_CATALOG_HARDCODED = [
    _vehicle(None, 'b91D', 'TLK282', 'ISD', 'isdBV', 'isdBV'),
    _vehicle('navitrans', 'b39D', 'TLK250', 'ISDNV', 'isdNV', 'isdNV'),
    _vehicle('navitrans', 'b432', 'NOY042', 'S13', 's13', 's13'),
    _vehicle('navitrans', 'b430', 'NOY043', 'S13', 's13', 's13'),
    _vehicle('navitrans', 'b42A', 'NLW548', 'X13', 'x13', 'x13'),
    _vehicle('navitrans', 'b42D', 'NLW549', 'X13', 'x13', 'x13'),
    _vehicle('navitrans', 'b382', 'TLK286', 'L9', 'l9', 'l9'),
    _vehicle('demo_fleet', 'b41', 'NNL336', 'X11', 'x11', 'x11'),
    _vehicle('navitrans', 'b538', 'LVCB2NBC9SS240368', 'F2.8', 'f2.8', 'f28'),
    _vehicle('navitrans', 'b54A', 'YHL582', 'F2.8', 'f2.8', 'f28'),
    _vehicle('navitrans', 'b54C', 'YHL583', 'F2.8', 'f2.8', 'f28'),
    _vehicle('navitrans', 'b54D', 'YHL584', 'F2.8', 'f2.8', 'f28'),
    _vehicle('navitrans', 'b54B', 'YHL586', 'F2.8', 'f2.8', 'f28'),
    _vehicle('navitrans', 'b582', 'LJO428', 'F4.5', 'f4.5', 'f45'),
    _vehicle('navitrans', 'b561', 'LJO429', 'F4.5', 'f4.5', 'f45'),
    _vehicle('navitrans', 'b604', 'NLX561', 'F2.8', 'f2.8', 'f28'),
    _vehicle('navitrans', 'b615', 'NLX540', 'D6.7', 'd6.7', 'd67'),
    _vehicle('navitrans', 'b6B5', 'NOS502', 'A26-370', 'a26_370', 'a26-370'),
    _vehicle('navitrans', 'b6D6', 'NYK373', 'F2.8', 'f2.8', 'f28'),
    _vehicle('navitrans', 'b5D3', 'LJO357', 'F4.5', 'f4.5', 'f45'),
    _vehicle('navitrans', 'b5D4', 'LJO356', 'F4.5', 'f4.5', 'f45'),
    _vehicle('navitrans', 'b6A6', 'NIN670', 'D6.7', 'd6.7', 'd67'),
    _vehicle('navitrans', 'b6A7', 'NIN671', 'D6.7', 'd6.7', 'd67'),
    _vehicle('navitrans', 'b6A3', 'NIN672', 'D6.7', 'd6.7', 'd67'),
    _vehicle('navitrans', 'b6A2', 'NIN673', 'D6.7', 'd6.7', 'd67'),
    _vehicle('navitrans', 'b731', 'NNN497', 'F2.8', 'f2.8', 'f28'),
    _vehicle('navitrans', 'b753', 'TLK545', 'X13', 'x13', 'x13'),
    _vehicle('navitrans', 'b644', 'NUZ331', 'D6.7', 'd6.7', 'd67'),
    _vehicle('navitrans', 'b6D1', 'NNM671', 'F3.8', 'f3.8', 'f38'),
]

VEHICLE_CATALOG = _master['vehicle_catalog'] if _master else _VEHICLE_CATALOG_HARDCODED


ACTIVE_VEHICLE_CATALOG = [
    vehicle
    for vehicle in VEHICLE_CATALOG
    if vehicle['database_name'] in CREDENTIALS
]


VEHICLE_BY_DB_DEVICE = {
    (vehicle['database_name'], vehicle['device_id']): vehicle
    for vehicle in ACTIVE_VEHICLE_CATALOG
}

VEHICLE_BY_DEVICE = {}
for vehicle in VEHICLE_CATALOG:
    VEHICLE_BY_DEVICE.setdefault(vehicle['device_id'], []).append(vehicle)


def get_vehicle_record(database_name: str | None, device_id: str) -> dict | None:
    return VEHICLE_BY_DB_DEVICE.get((database_name, device_id))


def get_vehicle_records(database_name: str | None = None) -> list[dict]:
    if database_name is None:
        return list(VEHICLE_CATALOG)
    return [vehicle for vehicle in ACTIVE_VEHICLE_CATALOG if vehicle['database_name'] == database_name]


# Compatibilidad temporal con scripts legacy. No deben usarse como fuente de verdad.
MAPEO = {}
for vehicle in VEHICLE_CATALOG:
    MAPEO.setdefault(vehicle['device_id'], vehicle['placa'])

DEVICE_GROUPS = {}
for vehicle in ACTIVE_VEHICLE_CATALOG:
    # group_key es una extensión local del ETL; los vehículos que entran por el
    # sync de Navi no lo traen. Fallback determinístico por (base, motor) para que
    # igual entren en los grupos (si no, los extractores por grupo los ignoran).
    group_key = vehicle['group_key'] or f"{vehicle['database_name']}:{vehicle['motor_type']}"
    group = DEVICE_GROUPS.setdefault(
        group_key,
        {
            'db': vehicle['database_name'],
            'devices': [],
            'motor': vehicle['motor_type'],
        },
    )
    group['devices'].append(vehicle['device_id'])

DEMO_FLEET_DEVICES = [
    vehicle['device_id']
    for vehicle in ACTIVE_VEHICLE_CATALOG
    if vehicle['database_name'] == 'demo_fleet'
]

NAVITRANS_DEVICES = [
    vehicle['device_id']
    for vehicle in ACTIVE_VEHICLE_CATALOG
    if vehicle['database_name'] == 'navitrans'
]

# Mapa genérico base -> devices con TODAS las bases activas del catálogo (DB).
# Los extractores deben recorrer esto en vez de las listas fijas demo_fleet/
# navitrans, para no ignorar bases nuevas que entran por el sync (p. ej. alion).
DEVICES_BY_DB: dict[str, list[str]] = {}
for vehicle in ACTIVE_VEHICLE_CATALOG:
    DEVICES_BY_DB.setdefault(vehicle['database_name'], []).append(vehicle['device_id'])

DEVICE_TO_MOTOR = {
    (vehicle['database_name'], vehicle['device_id']): vehicle['motor_type']
    for vehicle in ACTIVE_VEHICLE_CATALOG
}

DEVICE_TO_DB = {
    (vehicle['database_name'], vehicle['device_id']): vehicle['database_name']
    for vehicle in ACTIVE_VEHICLE_CATALOG
}

DEVICE_TO_PLACA = {
    (vehicle['database_name'], vehicle['device_id']): vehicle['placa']
    for vehicle in ACTIVE_VEHICLE_CATALOG
}

RPM_CLASS_BY_DB_DEVICE = {
    (vehicle['database_name'], vehicle['device_id']): vehicle['rpm_class']
    for vehicle in ACTIVE_VEHICLE_CATALOG
    if vehicle['rpm_class']
}


_RULES_BY_MOTOR_HARDCODED = {
    'F4.5': {
        'Rango Bajo': 'ayDYl-So7Fk2E3thcS3LQVA',
        'Rango Economico': 'aNYYMyTlJlEavxeQ6hHCk3Q',
        'Rango Balanceado': 'axey6REHREk6wtO5iv6uqQw',
        'Rango Potencia': 'ayhqKEKN4-UCQs5n7s4L0JA',
        'Exceso RPM': 'a859krXC3e0i9y2u1iqFa7w',
        'Rango Potencia Ineficiente': 'aQjDiHI-Ar0yRFfZwdyJS0A',
        'Ralentí': 'almm_jw2wNkGw7zoNkunfkg',
        'Rango Bajo Descenso': 'a74Szd3dOpkiWsXFD-x9Y_g',
        'Rango Economico Descenso': 'awB96pO1ixU6pNi2tLtqBPQ',
        'Rango Balanceado Descenso': 'aXLPoxWnzMEOvtDZLRLs0mw',
        'Rango Potencia Descenso': 'aSnCDYPEXUkOd20ppQPK91g',
        'Exceso RPM Descenso': 'ayCylTeuTKEK1dqcc7WTkEg',
        'Rango Potencia Ineficiente Descenso': 'axtREUtxRVEapqPlX5A7oCA',
    },
    'F2.8': {
        'Rango Bajo': 'a7V_SZk9wPEucTBCInC22Gw',
        'Rango Economico': 'agEcdViAVw0-LVugr8Q1wug',
        'Rango Balanceado': 'a0ANDVvpojU-smi5kf3Pi5g',
        'Rango Potencia': 'aBTxzkCUHBEeEPIAG-hl32A',
        'Exceso RPM': 'aKc_ETY-BmEeT9PN5QgwMag',
        'Rango Potencia Ineficiente': 'ay8Q_kFXUHUiOJNFnJfFRYA',
        'Ralentí': 'azNRTlWxn_UKotNZCqZzkvg',
        'Rango Bajo Descenso': 'a5U5UdJPoK0m6YwYByzGKrg',
        'Rango Economico Descenso': 'anHt90GaJIkGc-AGhzTAzXg',
        'Rango Balanceado Descenso': 'aj67RRs70IU-SNbPMy1LJ_g',
        'Rango Potencia Descenso': 'aTWBK4Y6vpUy8rdzUHFs2gA',
        'Exceso RPM Descenso': 'a5VitH6EnlUGg4Psaqj1JdA',
        'Rango Potencia Ineficiente Descenso': 'ahFy22Io0QkaIkXecAcHo7A',
    },
    'ISD': {
        'Rango Bajo': 'avU_DMqAK_UmqkTdCANWa5Q',
        'Rango Economico': 'aYHfKXOXlE0yHCnZ7mJ6izA',
        'Rango Balanceado': 'abxvj_1lD3k6qinuWt9X5KQ',
        'Rango Potencia': 'a7cC1bztr6E2u86cdQ7pKBQ',
        'Exceso RPM': 'aNWnChHMYvkiiOSCudBhXzg',
        'Rango Potencia Ineficiente': 'agu5kEOSzVkiSbEkzvEVJwQ',
        'Ralentí': 'aLZ9DfZ_X40eSixq8W536SQ',
    },
    'S13': {
        'Rango Bajo': 'a9LXvvBoXEkGrNAsIVYcA8w',
        'Rango Economico': 'a8mBPgitqZUeNMKhAgEK6CA',
        'Rango Balanceado': 'a3_awaTXcLk6sP2DOYJsVcw',
        'Rango Potencia': 'anVAK4pHzzEK533qrXMBKNw',
        'Exceso RPM': 'aQ6SSA4EIpk2DI7oZlws4XQ',
        'Rango Potencia Ineficiente': 'aWI7KLvoFVUucFy3Q0Ho1ug',
        'Ralentí': 'aGnmtpEmrLU2oPfE6JoiKnA',
        'Rango Bajo Descenso': 'a9yQceN_Tu0KnbdN-C01DUg',
        'Rango Economico Descenso': 'aMMSygfOuq0OztNpF6i5gqQ',
        'Rango Balanceado Descenso': 'aKo-sF-3fO0SndjEKfePsTQ',
        'Rango Potencia Descenso': 'amAo3mQBXYkO8ot2AnJQV3A',
        'Exceso RPM Descenso': 'ahE_AXz6dqUuk3Umy26__QQ',
        'Rango Potencia Ineficiente Descenso': 'aqAi07fuwDEqvp1F4HTbXwg',
    },
    'ISDNV': {
        'Rango Bajo': 'aZ_KKPQdww0WCx_fjFoZcDA',
        'Rango Economico': 'ayPiOJ7cpUkqkXvmg590SVg',
        'Rango Balanceado': 'aOtEvkeQ3t0Gqv-H_dMn-Cg',
        'Rango Potencia': 'aq7WHDzSIVEqNnhuFySO_xw',
        'Exceso RPM': 'ayOWlyhuoyEm-YPgFZWb6gw',
        'Rango Potencia Ineficiente': 'a7ZCL60tiU0-sbxTzcfn0ew',
        'Ralentí': 'aJKKprVn18kWh5TGr9U_5zA',
    },
    'X13': {
        'Rango Bajo': 'aMHdo5IqUFky2GwwM5gQMUQ',
        'Rango Economico': 'aBwz22ARVtEaSRCJ3XGaWHw',
        'Rango Balanceado': 'aYcA8sWd2DUSeddtSw_oQeQ',
        'Rango Potencia': 'aBYDDo2enrEOi1fy4Gg1zDw',
        'Exceso RPM': 'amZZwY9RkNE-5p9I_SdQ3lg',
        'Rango Potencia Ineficiente': 'aqoapa4o1kEyzwJSwA1cOew',
        'Ralentí': 'aN4EwjG-3mUmYNPLbE-wMPg',
        'Rango Bajo Descenso': 'atRZ22q-SgEKQftMmURtHLQ',
        'Rango Economico Descenso': 'aNZeiBflfPUufasPsj8LHLQ',
        'Rango Balanceado Descenso': 'aktpmkkPd7ECSXrsZUReH1g',
        'Rango Potencia Descenso': 'azD2OKiR4VUi8MSyIbWdEOQ',
        'Exceso RPM Descenso': 'apn3Hjq9LD0GeXqveis_3kg',
        'Rango Potencia Ineficiente Descenso': 'aOV9mgvLE3kefOEzVd3ctww',
    },
    'L9': {
        'Rango Bajo': 'ayxDBO3Q0mEyEsmIzsMlebw',
        'Rango Economico': 'aJtSAAEKXgEijm9dXBDb8Bg',
        'Rango Balanceado': 'aac6z_9E5jEexKtAJvBnlKQ',
        'Rango Potencia': 'aLfMu51ZLpkGDCP4FBQIQpw',
        'Exceso RPM': 'aNCGtDLORz0mF4aYOIZhphA',
        'Rango Potencia Ineficiente': 'arV471qDl5kmKyGmEpTjPiA',
        'Ralentí': 'au8Y-y5wL40WSyDClo7GFqA',
    },
    'X11': {
        'Rango Bajo': 'a8Yfz3vyuVkOkBMXMzInlpw',
        'Rango Economico': 'a3R667Bfxb0STh3qDNDzbIg',
        'Rango Balanceado': 'aBTQGGeI5QkORD16aBkYPuw',
        'Rango Potencia': 'a0hevDcmJL0ugsqi1l3LIJg',
        'Exceso RPM': 'aUI6EhWPt_US5wbh2B6AuoQ',
        'Rango Potencia Ineficiente': 'abV6b9IduY0un5_GKET-Nog',
        'Ralentí': 'a9t8Z0Mn4x02KXt0slK02Dw',
    },
    'D6.7': {
        'Rango Bajo': 'a0qoVnUuusUqeqqkb4ThTkA',
        'Rango Economico': 'aG7y3vIWZFkGAcGwozhNTCw',
        'Rango Balanceado': 'aWHfSP7LJFkmMXyJkeaS8-w',
        'Rango Potencia': 'alxlK998CbUe094Z5rD6QYQ',
        'Exceso RPM': 'aclzrWa11-kSs7sKUm00BLw',
        'Rango Potencia Ineficiente': 'aGhYb2bMw6Ei33mntDtZK6Q',
        'Ralentí': 'aUMVOJ_75bUW5khq0kDhMYQ',
    },
    'A26-370': {
        'Rango Bajo': 'aMtnawQmlc0GAUG7LIkN6gQ',
        'Rango Economico': 'azcm9eBpOn0e2djwuNdkP0g',
        'Rango Balanceado': 'axYWJfVcalkaTIDN27eOI3w',
        'Rango Potencia': 'aMp4begsDxkuc2CSqJlqZaw',
        'Exceso RPM': 'a3faWPvSQJUG0kqmWg4UDBg',
        'Rango Potencia Ineficiente': 'aGl7O3CHfsUqhfj8vTzfmgA',
        'Ralentí': 'arhafODpA-UivF-hQ4UmI6w',
    },
    'F3.8': {
        'Rango Bajo': 'aVSj3Kxivbkqhcj6-f15qLw',
        'Rango Economico': 'abDO8n1A1-Ey1WstfxKNzbw',
        'Rango Balanceado': 'agikugEs7A0WzfP69_2AjrA',
        'Rango Potencia': 'aj35W9vhxkUmtAUHcIKwnIQ',
        'Exceso RPM': 'aV4hCqfLkYUuL-odhVRtTAA',
        'Rango Potencia Ineficiente': 'aBEDyoHw8mkeY_JGRXiecAA',
        'Ralentí': 'aUzkwLxRhh06YaKvqbbJ3rQ',
        'Rango Bajo Descenso': 'a_vvhtvKB6Ee-ZYi1rbVzqA',
        'Rango Economico Descenso': 'a--JpmUsEzUuXqg84pXEfag',
        'Rango Balanceado Descenso': 'aOXAWUF3AskyE5J6vtByk7w',
        'Rango Potencia Descenso': 'avSHywylEWkaygaswdegAOg',
        'Exceso RPM Descenso': 'aOL047zZnT0qSLZHArW7k8w',
        'Rango Potencia Ineficiente Descenso': 'avQOVkul87USuN7aBeXvZTA',
    },
}

# Agregado por motor. NO usar para extraer ni transformar: colapsa las bases
# (dos clientes con el mismo motor tienen reglas distintas y la clave se pisa).
# Sobrevive solo para seed_master_data/verify_master_data, que comparan catálogos.
RULES_BY_MOTOR = _master['rules_by_motor'] if _master else _RULES_BY_MOTOR_HARDCODED

# Fuente correcta: {database_name: {motor_type: {banda: rule_id}}}.
RULES_BY_DB_MOTOR = _master.get('rules_by_db_motor', {}) if _master else {}


# ---------------------------------------------------------------------------
# Modo de rangos por flota (contrato de integración, `customers.range_mode`)
# ---------------------------------------------------------------------------
RANGE_MODE_BY_DB_DEVICE = {
    (vehicle['database_name'], vehicle['device_id']): (vehicle.get('range_mode') or 'reglas')
    for vehicle in ACTIVE_VEHICLE_CATALOG
}

# Cortes del eje de RPM por motor. Vacío sin data maestra: el fallback hardcoded
# no tiene rangos y, sin ellos, el modo por RPM no calcula nada (fail-closed).
RPM_BANDS_BY_MOTOR: dict = _master.get('rpm_bands_by_motor', {}) if _master else {}


def rpm_bands_for(motor_type: str | None) -> list:
    """Partición del eje de RPM del motor, o [] si no está configurada.

    Lista ordenada de `{'band', 'rpm_min', 'rpm_max'}`; `rpm_max=None` en la
    banda más alta. Vacía = motor sin rangos: el llamador DEBE saltarse el
    vehículo, nunca asumir los cortes de otro motor."""
    if not motor_type:
        return []
    return RPM_BANDS_BY_MOTOR.get(motor_type, [])


RALENTI_ANALYSIS_BY_DB_DEVICE: dict[tuple[str | None, str], bool] = {
    (vehicle['database_name'], vehicle['device_id']): bool(vehicle.get('ralenti_analysis'))
    for vehicle in ACTIVE_VEHICLE_CATALOG
}


def ralenti_analysis_enabled(database_name: str | None, device_id: str) -> bool:
    """¿La flota de este vehículo tiene contratado el Análisis de Ralentí?

    Es decisión de la FLOTA (`fleets.ralenti_analysis_enabled`, activable en
    /gestion/flotas del portal), replicada por vehículo porque el ETL razona por
    (base, device). Fail-closed: sin dato maestro o con el flag apagado, el
    extractor de ralentí no consulta Geotab por ese vehículo."""
    return RALENTI_ANALYSIS_BY_DB_DEVICE.get((database_name, device_id), False)


def uses_rpm_ranges(database_name: str | None, device_id: str) -> bool:
    """¿Este vehículo arma sus bandas desde los RPM en vez de las reglas?

    Es decisión de la flota, no del vehículo ni del motor. Un vehículo en modo
    'rpm' cuyo motor no tenga rangos igual devuelve True: no debe extraerse por
    reglas (esas reglas no describen su flota) y el extractor por RPM lo omite
    dejando el hueco visible en calidad de datos."""
    return RANGE_MODE_BY_DB_DEVICE.get((database_name, device_id), 'reglas') == 'rpm'


def rules_for(database_name: str, motor_type: str) -> dict:
    """Reglas de banda de RPM de un motor DENTRO de una base Geotab.

    Cada cliente tiene su propia base y crea sus propias reglas por motor: los
    rule_id no se comparten entre bases. Pedirle a una base los ids de otra
    devuelve vacío y hunde a cero esas bandas, inflando el resto del reparto.

    Sin data maestra (fallback hardcoded de config.py) no hay dimensión de base
    y se cae al catálogo por motor, que es el comportamiento legado.
    """
    if RULES_BY_DB_MOTOR:
        return RULES_BY_DB_MOTOR.get(database_name, {}).get(motor_type, {})
    return RULES_BY_MOTOR.get(motor_type, {})


_EVENT_RULES_HARDCODED = {
    'demo_fleet': {
        'Exceso Velocidad': 'a5YKI9t_MC0mfj_CDHA_dgA',
        'Frenada Brusca': 'a19bfhh_sNU6ucMD_N8CGKA',
        'Giro Brusco': 'ay3zxnj1rXkGY8lp8bWAMBA',
        'Aceleracion Brusca': 'aLYftthw0REqMsRmviSeuFQ',
        'Bache Fuerte': 'aHviPdbKqq0qhPAvJWqzacg',
    },
    'navitrans': {
        'Exceso Velocidad': 'alR-KVPrVvkKjgXtVLPINaA',
        'Frenada Brusca': 'ab_y2W7C8NE2aPkeYH5sGlw',
        'Giro Brusco': 'aNYNf3svKpU2NbK99v-lVeg',
        'Aceleracion Brusca': 'aEiec4AMHv0q4m2nCDjgrZw',
        'Bache Fuerte': 'aaQhaaUILyEqRj9Ta4C05DA',
    },
}

EVENT_RULES = _master['event_rules'] if _master else _EVENT_RULES_HARDCODED
EVENT_RULES_BY_MOTOR = _master.get('event_rules_by_motor', {}) if _master else {}


_RPM_RULES_HARDCODED = {
    'isdBV': ['aNWnChHMYvkiiOSCudBhXzg'],
    'isdNV': ['ayOWlyhuoyEm-YPgFZWb6gw'],
    's13': ['aQ6SSA4EIpk2DI7oZlws4XQ', 'ahE_AXz6dqUuk3Umy26__QQ'],
    'x13': ['amZZwY9RkNE-5p9I_SdQ3lg', 'apn3Hjq9LD0GeXqveis_3kg'],
    'l9': ['aNCGtDLORz0mF4aYOIZhphA', 'aiEOo_aGyI0m7AqPHiR-YYQ'],
    'f28': ['aKc_ETY-BmEeT9PN5QgwMag', 'a5VitH6EnlUGg4Psaqj1JdA'],
    'f45': ['a859krXC3e0i9y2u1iqFa7w', 'ayCylTeuTKEK1dqcc7WTkEg'],
    'x11': ['aUI6EhWPt_US5wbh2B6AuoQ'],
    'd67': ['aclzrWa11-kSs7sKUm00BLw'],
    'a26-370': ['a3faWPvSQJUG0kqmWg4UDBg'],
    'f38': ['aV4hCqfLkYUuL-odhVRtTAA', 'aOL047zZnT0qSLZHArW7k8w'],
}

RPM_RULES = _master['rpm_rules'] if _master else _RPM_RULES_HARDCODED


HABITOS_RPM_DEVICE_CLASS = {
    (vehicle['database_name'], vehicle['device_id']): vehicle['rpm_class']
    for vehicle in ACTIVE_VEHICLE_CATALOG
    if vehicle['rpm_class']
}


VOLUMEN_POR_DEFECTO = 28
_VOLUMEN_TANQUES_HARDCODED = {
    'NNL336': 28,
    'TLK282': 28,
    'TLK250': 28,
    'NOY042': 28,
    'NOY043': 28,
    'NLW548': 28,
    'NLW549': 28,
    'TLK286': 28,
    'TLK545': 27.7381,
    'LVCB2NBC9SS240368': 28,
    'YHL582': 28,
    'YHL583': 28,
    'YHL584': 28,
    'YHL586': 28,
    'LJO428': 28,
    'LJO429': 28,
    'NLX561': 28,
    'NLX540': 28,
    'NOS502': 28,
    'NYK373': 28,
    'LJO357': 28,
    'LJO356': 28,
    'NIN670': 28,
    'NIN671': 28,
    'NIN672': 28,
    'NIN673': 28,
    'NNN497': 28,
    'NUZ331': 28,
    'NNM671': 28,
}

VOLUMEN_TANQUES = _master['volumen_tanques'] if _master else _VOLUMEN_TANQUES_HARDCODED


ECM_COUNTER_DIAGNOSTICS = {
    'odometro': 'DiagnosticOdometerId',
    'combustible_usado': 'DiagnosticTotalFuelUsedId',
    'combustible_dispositivo': 'DiagnosticDeviceTotalFuelId',
    'combustible_ralenti': 'DiagnosticTotalIdleFuelUsedId',
    # Los N15 a gas usan este contador, como el reporte legacy.
    'combustible_ralenti_dispositivo': 'DiagnosticDeviceTotalIdleFuelId',
    'horas_motor': 'DiagnosticEngineHoursId',
    'horas_ralenti_ecm': 'DiagnosticTotalIdleHoursId',
}

DIAGNOSTIC_IDS = {
    'altimetria': 'aZ_PCPTFQJUWGgwTodd5nhA',
    'factor_carga': 'a1Q7QlJDugkq92LxZ12KY1A',
    'pedal': 'a8E-MlXuWg0-3oH2dO424Qw',
    'def': 'DiagnosticDieselExhaustFluidId',
    'rpm_habitos': 'aW3Nmy-ktfEuvrdkya4z0yg',
    'load_factor_habitos': 'a1Q7QlJDugkq92LxZ12KY1A',
}

ACCEL_DIAGNOSTICS = {
    'forward_braking': 'DiagnosticAccelerationForwardBrakingId',
    'side_to_side': 'DiagnosticAccelerationSideToSideId',
    'up_down': 'DiagnosticAccelerationUpDownId',
}


CHUNK_SIZE_DAYS = {
    'alertas': 3,
    # Episodios de ralentí: 3 días por chunk. Más corto parte más episodios en
    # la frontera del chunk; más largo engorda el multicall de ExceptionEvent
    # (~500 eventos/día/vehículo con la regla de banda).
    'ralenti': 3,
    'altimetria': 10,
    'analisis_combustible': 3,
    'consumo_def': 3,
    'factor_carga': 3,
    'habito_seguro': 3,
    'posicion_pedal': 3,
    'ubicaciones': 3,
}


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Raíz del lago Parquet. Por defecto vive junto al código (desarrollo con bind
# mount); en producción se apunta a un volumen con ETL_DATA_LAKE_DIR, porque
# la imagen no debe escribir sobre su propio código.
DATA_LAKE_PATH = os.environ.get('ETL_DATA_LAKE_DIR') or os.path.join(_BASE_DIR, 'data_lake')

SILVER_PATH = os.path.join(DATA_LAKE_PATH, 'silver')
SILVER_DIMS_PATH = os.path.join(SILVER_PATH, 'dims')
SILVER_FACTS_PATH = os.path.join(SILVER_PATH, 'facts')

SEMANTIC_PATH = os.path.join(DATA_LAKE_PATH, 'semantic')
SEMANTIC_DIMS_PATH = os.path.join(SEMANTIC_PATH, 'dims')
SEMANTIC_FACTS_PATH = os.path.join(SEMANTIC_PATH, 'facts')

QA_PATH = os.path.join(DATA_LAKE_PATH, 'qa')
QA_REPORTS_PATH = os.path.join(QA_PATH, 'reports')

# Manifiestos de carga incremental (PK -> hash de la última fila cargada a PG).
# Permiten al loader subir solo filas nuevas/cambiadas.
LOAD_MANIFEST_PATH = os.path.join(DATA_LAKE_PATH, 'load_manifest')

# Si es truthy, el loader re-upsertea TODO (comportamiento previo / reconciliación).
LOAD_FULL_UPSERT = os.environ.get('LOAD_FULL_UPSERT', '').lower() in ('1', 'true', 'yes')

# ---------------------------------------------------------------------------
# Dominios desactivados (ETL_DISABLED_DOMAINS)
# ---------------------------------------------------------------------------
# Apagar la EXTRACCIÓN de un dominio no bastaba: su transform seguía en
# run_semantic_all y su fact seguía pasando por el loader. Como varios dominios
# comparten el mismo silver (p. ej. extract_habitos también escribe
# fact_log_records, que es la entrada de transform_ubicaciones), el transform
# regeneraba el semántico igual, el hash de fila cambiaba y el loader
# re-upserteaba la tabla completa para producir CERO filas nuevas.
#
# Esta variable apaga el dominio de punta a punta: extract + transform + load.
# Los facts ya cargados en `analytics` quedan congelados donde estaban.
DOMAIN_PIPELINE = {
    'ubicaciones': {
        'extract': 'extract/extract_ubicaciones.py',
        'transform': 'transform.transform_ubicaciones',
        'table': 'fact_location_point',
    },
    'altimetria': {
        'extract': 'extract/extract_altimetria.py',
        'transform': 'transform.transform_altimetria',
        'table': 'fact_altimetria_reading',
    },
    'consumo_def': {
        'extract': 'extract/extract_def.py',
        'transform': 'transform.transform_def',
        'table': 'fact_def_daily',
    },
}

DISABLED_DOMAINS = {
    value.strip()
    for value in os.environ.get('ETL_DISABLED_DOMAINS', '').split(',')
    if value.strip()
}
_UNKNOWN_DOMAINS = DISABLED_DOMAINS.difference(DOMAIN_PIPELINE)
if _UNKNOWN_DOMAINS:
    raise ValueError(
        f'ETL_DISABLED_DOMAINS contiene dominios no soportados: '
        f'{sorted(_UNKNOWN_DOMAINS)}. Válidos: {sorted(DOMAIN_PIPELINE)}'
    )

DISABLED_EXTRACT_STEPS = tuple(
    DOMAIN_PIPELINE[d]['extract'] for d in sorted(DISABLED_DOMAINS)
)
DISABLED_TRANSFORMS = frozenset(
    DOMAIN_PIPELINE[d]['transform'] for d in DISABLED_DOMAINS
)
DISABLED_TABLES = frozenset(
    DOMAIN_PIPELINE[d]['table'] for d in DISABLED_DOMAINS
)

# Alias temporales para no romper imports antiguos.
DIMS_PATH = SILVER_DIMS_PATH
FACTS_PATH = SILVER_FACTS_PATH
OUTPUT_DIR = QA_REPORTS_PATH
