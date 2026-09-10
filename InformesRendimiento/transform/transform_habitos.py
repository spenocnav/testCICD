import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pandas as pd

from config import (
    ACCEL_DIAGNOSTICS,
    DIAGNOSTIC_IDS,
    DIMS_PATH,
    EVENT_RULES,
    EVENT_RULES_BY_MOTOR,
    FACTS_PATH,
    RPM_RULES,
    SEMANTIC_FACTS_PATH,
)
from utils import date_to_key, read_facts

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

FACT_EVENTS_FILE = os.path.join(FACTS_PATH, 'fact_exception_events.parquet')
FACT_LOGS_FILE = os.path.join(FACTS_PATH, 'fact_log_records.parquet')
FACT_STATUS_FILE = os.path.join(FACTS_PATH, 'fact_status_readings.parquet')
DIM_VEH_FILE = os.path.join(DIMS_PATH, 'dim_vehiculos.parquet')
OUTPUT_FILE = os.path.join(SEMANTIC_FACTS_PATH, 'fact_habito_event.parquet')

RPM_DIAG_ID = DIAGNOSTIC_IDS['rpm_habitos']
LOAD_DIAG_ID = DIAGNOSTIC_IDS['load_factor_habitos']
ACCEL_FWD_ID = ACCEL_DIAGNOSTICS['forward_braking']
ACCEL_SIDE_ID = ACCEL_DIAGNOSTICS['side_to_side']
ACCEL_UP_ID = ACCEL_DIAGNOSTICS['up_down']
COLOMBIA_OFFSET = timedelta(hours=5)


def _build_rule_id_to_name() -> dict:
    mapping = {}
    for database_rules in EVENT_RULES.values():
        for name, rule_id in database_rules.items():
            mapping[rule_id] = name
    for database_motors in EVENT_RULES_BY_MOTOR.values():
        for motor_rules in database_motors.values():
            for name, rule_id in motor_rules.items():
                mapping[rule_id] = name
    for rpm_list in RPM_RULES.values():
        if len(rpm_list) > 0:
            mapping.setdefault(rpm_list[0], 'Exceso RPM')
        if len(rpm_list) > 1:
            mapping.setdefault(rpm_list[1], 'Exceso RPM sin Carga')
    return mapping


def _event_type_from_rule(rule_id: str, rule_id_to_name: dict) -> str:
    return rule_id_to_name.get(rule_id, f'RPM_{rule_id[:6]}')


def _build_rule_sk_lookup(dim_rule: pd.DataFrame) -> dict:
    lookup = {}
    for _, row in dim_rule.iterrows():
        lookup[(row['rule_id'], row['scope_type'], row['scope_value'])] = row['rule_sk']
    return lookup


def _resolve_rule_sk(row: pd.Series, rule_sk_lookup: dict) -> str | None:
    database_name = row.get('database_name')
    rule_id = row.get('rule_id')
    motor_type = row.get('motor_type')
    rpm_class = row.get('rpm_class')

    scoped_rules = EVENT_RULES_BY_MOTOR.get(database_name, {}).get(motor_type, {})
    if rule_id in set(scoped_rules.values()):
        return rule_sk_lookup.get(
            (rule_id, 'database_motor', f'{database_name}::{motor_type}')
        )

    if database_name in EVENT_RULES and rule_id in set(EVENT_RULES[database_name].values()):
        return rule_sk_lookup.get((rule_id, 'database_name', database_name))

    if rpm_class and rpm_class in RPM_RULES and rule_id in set(RPM_RULES[rpm_class]):
        return rule_sk_lookup.get((rule_id, 'rpm_class', rpm_class))

    matches = [key for key in rule_sk_lookup if key[0] == rule_id]
    if len(matches) == 1:
        return rule_sk_lookup[matches[0]]
    return None


# Alias de `event_type` por eje del acelerómetro. Los nombres llegan de master
# data (plural: "Frenadas bruscas") o del fallback hardcodeado de config
# (singular: "Frenada Brusca"), así que ambos deben estar. Se comparan siempre
# con `casefold()`.
LONGITUDINAL_EVENT_TYPES = (
    'frenada brusca',
    'frenadas bruscas',
    'aceleracion brusca',
    'aceleración brusca',
    'aceleraciones bruscas',
)
LATERAL_EVENT_TYPES = ('giro brusco', 'giros bruscos')
VERTICAL_EVENT_TYPES = ('bache fuerte', 'baches o resaltos fuertes')

AXIS_LONGITUDINAL = 'longitudinal'
AXIS_LATERAL = 'lateral'
AXIS_VERTICAL = 'vertical'

# diagnostic_id de MyGeotab → eje. El eje es un atributo DEL DATO, no del tipo
# de evento: un evento puede traer lecturas de los tres ejes a la vez.
AXIS_BY_DIAGNOSTIC = {
    ACCEL_FWD_ID: AXIS_LONGITUDINAL,
    ACCEL_SIDE_ID: AXIS_LATERAL,
    ACCEL_UP_ID: AXIS_VERTICAL,
}

# Divisor heredado de `Originals/Habitos seguros.py:267`, sin justificación en el
# repositorio ni en la documentación de MyGeotab consultada. Los valores
# resultantes (0,2–1,0 G) son coherentes con lecturas crudas expresadas en
# décimas de G; NO se pudo confirmar la unidad real del diagnóstico. Se conserva
# tal cual para no alterar la historia ya publicada.
ACCEL_G_DIVISOR = 10

UNIT_RPM = 'RPM'
UNIT_KMH = 'km/h'
UNIT_G = 'G'


def _build_observacion(event_type: str, vel, rpm, load, g_force) -> str:
    normalized = event_type.casefold()
    if 'rpm' in normalized:
        return f"{rpm or 'N/A'} RPM, {vel or 'N/A'} km/h, Carga: {load or 'N/A'}%"

    observacion = f"{vel or 'N/A'} km/h"
    if g_force is not None:
        if normalized in LONGITUDINAL_EVENT_TYPES:
            observacion += f", Aceleración hacia delante o frenado: {g_force} G Force"
        elif normalized in LATERAL_EVENT_TYPES:
            observacion += f", Aceleración de lado a lado: {g_force} G Force"
        elif normalized in VERTICAL_EVENT_TYPES:
            observacion += f", Aceleración vertical: {g_force} G Force"
    return observacion


def axis_for_event_type(event_type: str) -> str | None:
    """Eje del acelerómetro que DEFINE ese tipo de evento, o None."""
    normalized = event_type.casefold()
    if normalized in LONGITUDINAL_EVENT_TYPES:
        return AXIS_LONGITUDINAL
    if normalized in LATERAL_EVENT_TYPES:
        return AXIS_LATERAL
    if normalized in VERTICAL_EVENT_TYPES:
        return AXIS_VERTICAL
    return None


def peak_signed(readings) -> float | None:
    """Pico por magnitud absoluta CONSERVANDO EL SIGNO.

    `max()` no sirve para el acelerómetro: en una frenada las lecturas son
    negativas y `max()` devuelve la MENOS negativa, es decir la más suave. El
    pico real de una frenada es el valor más negativo.

    Empates de magnitud (+4,0 y -4,0) se resuelven por el valor mayor, para que
    el resultado no dependa del orden de llegada de las lecturas.
    """
    values = [float(value) for value in readings if value is not None and value == value]
    if not values:
        return None
    return max(values, key=lambda value: (abs(value), value))


def to_g(raw_reading) -> float | None:
    """Lectura cruda del acelerómetro → G, con el redondeo vigente."""
    if raw_reading is None or raw_reading != raw_reading:
        return None
    return round(float(raw_reading) / ACCEL_G_DIVISOR, 2)


def build_event_metrics(event_type: str, vel, rpm, load, g_by_axis: dict) -> dict:
    """Columnas numéricas del evento a partir de las lecturas ya resueltas.

    `g_by_axis` mapea eje → fuerza G con signo (ya convertida por `to_g`), con
    None donde no hubo lectura. Función pura: no lee Parquet ni base.

    `event_value` es la métrica que DEFINE el tipo de evento. Un tipo
    desconocido deja `event_value`/`event_value_unit` en None: no se inventa una
    métrica para un evento cuya semántica no se conoce.
    """
    normalized = event_type.casefold()
    axis = axis_for_event_type(event_type)
    g_force = g_by_axis.get(axis) if axis else None
    if g_force is None:
        # Sin lectura en el eje del evento no hay eje que reportar.
        axis = None

    if 'rpm' in normalized:
        event_value, event_value_unit = rpm, UNIT_RPM
    elif 'velocidad' in normalized:
        # `_build_observacion` no distingue este tipo (cae al formato genérico
        # "# km/h"), así que aquí se identifica por subcadena: ningún otro tipo
        # del corpus contiene "velocidad".
        event_value, event_value_unit = vel, UNIT_KMH
    elif axis_for_event_type(event_type) is not None:
        event_value, event_value_unit = g_force, UNIT_G
    else:
        event_value, event_value_unit = None, None

    return {
        'rpm': rpm,
        'velocidad_kmh': vel,
        'carga_pct': load,
        'g_force': g_force,
        'g_axis': axis,
        'event_value': event_value,
        'event_value_unit': event_value_unit,
    }


PEAK_COLUMN_BY_AXIS = {
    AXIS_LONGITUDINAL: 'accel_peak_longitudinal',
    AXIS_LATERAL: 'accel_peak_lateral',
    AXIS_VERTICAL: 'accel_peak_vertical',
}

# Columnas numéricas nuevas. Se castean a float64 explícitamente antes de
# escribir: si un lote no trae ninguna lectura, pandas infiere `object` y
# `load/db.infer_pg_type` crearía la columna como TEXT en lugar de
# DOUBLE PRECISION.
NUMERIC_METRIC_COLUMNS = ('rpm', 'velocidad_kmh', 'carga_pct', 'g_force', 'event_value')


def _accel_peaks_by_axis(df_accel: pd.DataFrame) -> list[pd.DataFrame]:
    """Un DataFrame por eje con el pico con signo por `event_sk`.

    Agregar por eje es lo que permite que `g_axis` salga del dato y no de una
    deducción a partir del tipo de evento.
    """
    aggs = []
    for diagnostic_id, axis in AXIS_BY_DIAGNOSTIC.items():
        column = PEAK_COLUMN_BY_AXIS[axis]
        subset = (
            df_accel[df_accel['diagnostic_id'] == diagnostic_id]
            if not df_accel.empty else df_accel
        )
        if subset.empty:
            aggs.append(pd.DataFrame(columns=['event_sk', column]))
            continue
        numeric = pd.to_numeric(subset['data'], errors='coerce')
        subset = subset.assign(_value=numeric, _magnitude=numeric.abs())
        subset = subset[subset['_value'].notna()]
        if subset.empty:
            aggs.append(pd.DataFrame(columns=['event_sk', column]))
            continue
        aggs.append(
            subset.sort_values(
                ['event_sk', '_magnitude', '_value'], ascending=[True, False, False]
            )
            .drop_duplicates(subset=['event_sk'], keep='first')
            [['event_sk', '_value']]
            .rename(columns={'_value': column})
        )
    return aggs


def main():
    df_events = read_facts(FACT_EVENTS_FILE)
    df_events = df_events[df_events['source'] == 'habitos'].copy()
    if df_events.empty:
        logging.info("No hay eventos de habitos.")
        return

    df_logs = read_facts(FACT_LOGS_FILE)
    df_logs = df_logs[df_logs['event_sk'].notna()].copy()

    df_status = read_facts(FACT_STATUS_FILE)
    df_status = df_status[df_status['event_sk'].notna()].copy()

    dim_vehicle = pd.read_parquet(DIM_VEH_FILE)[
        ['vehicle_id', 'database_name', 'placa', 'motor_type', 'rpm_class']
    ]
    dim_rule = pd.read_parquet(os.path.join(DIMS_PATH, 'dim_reglas.parquet'))[
        ['rule_sk', 'rule_id', 'scope_type', 'scope_value']
    ]
    df_events = df_events.merge(dim_vehicle, on=['vehicle_id', 'database_name'], how='left')
    rule_sk_lookup = _build_rule_sk_lookup(dim_rule)

    rule_id_to_name = _build_rule_id_to_name()

    if not df_logs.empty:
        df_logs['speed'] = pd.to_numeric(df_logs['speed'], errors='coerce')
        log_agg = (
            df_logs.assign(_sort_speed=df_logs['speed'].fillna(float('-inf')))
            .sort_values(['event_sk', '_sort_speed', 'dateTime'], ascending=[True, False, True])
            .drop_duplicates(subset=['event_sk'], keep='first')
            [['event_sk', 'speed', 'longitude', 'latitude']]
            .rename(columns={'speed': 'max_speed', 'longitude': 'lon', 'latitude': 'lat'})
        )
    else:
        log_agg = pd.DataFrame(columns=['event_sk', 'max_speed', 'lon', 'lat'])

    df_rpm = df_status[df_status['diagnostic_id'] == RPM_DIAG_ID].copy()
    rpm_agg = (
        df_rpm.groupby('event_sk')['data'].max().reset_index().rename(columns={'data': 'max_rpm'})
        if not df_rpm.empty else pd.DataFrame(columns=['event_sk', 'max_rpm'])
    )

    df_load = df_status[df_status['diagnostic_id'] == LOAD_DIAG_ID].copy()
    load_agg = (
        df_load.groupby('event_sk')['data'].max().reset_index().rename(columns={'data': 'max_load'})
        if not df_load.empty else pd.DataFrame(columns=['event_sk', 'max_load'])
    )

    accel_ids = {ACCEL_FWD_ID, ACCEL_SIDE_ID, ACCEL_UP_ID}
    df_accel = df_status[df_status['diagnostic_id'].isin(accel_ids)].copy()
    # `max_accel` alimenta EXCLUSIVAMENTE `Observacion Corta`. Mezcla los tres
    # ejes y usa `max()`, así que subestima las frenadas (devuelve la lectura
    # menos negativa). Se conserva sin cambios: ese string ya está publicado en
    # cientos de miles de filas y en la UI; recalcularlo reescribiría historia.
    # Las columnas numéricas nuevas usan el pico por eje con signo (`*_peak`).
    accel_agg = (
        df_accel.groupby('event_sk')['data'].max().reset_index().rename(columns={'data': 'max_accel'})
        if not df_accel.empty else pd.DataFrame(columns=['event_sk', 'max_accel'])
    )
    axis_aggs = _accel_peaks_by_axis(df_accel)

    df_events = df_events.merge(log_agg, on='event_sk', how='left')
    df_events = df_events.merge(rpm_agg, on='event_sk', how='left')
    df_events = df_events.merge(load_agg, on='event_sk', how='left')
    df_events = df_events.merge(accel_agg, on='event_sk', how='left')
    for axis_agg in axis_aggs:
        df_events = df_events.merge(axis_agg, on='event_sk', how='left')
    df_events['rule_sk'] = df_events.apply(lambda row: _resolve_rule_sk(row, rule_sk_lookup), axis=1)

    df_events['activeFrom'] = pd.to_datetime(df_events['activeFrom'])

    records = []
    for _, row in df_events.iterrows():
        event_type = _event_type_from_rule(row['rule_id'], rule_id_to_name)
        vel = round(row['max_speed'], 1) if pd.notna(row.get('max_speed')) else None
        rpm = round(row['max_rpm'], 2) if pd.notna(row.get('max_rpm')) else None
        load = round(row['max_load'], 2) if pd.notna(row.get('max_load')) else None
        g_force = round(row['max_accel'] / ACCEL_G_DIVISOR, 2) if pd.notna(row.get('max_accel')) else None
        fecha = (row['activeFrom'] - COLOMBIA_OFFSET).date()

        g_by_axis = {
            axis: to_g(row.get(column))
            for axis, column in PEAK_COLUMN_BY_AXIS.items()
        }
        metrics = build_event_metrics(event_type, vel, rpm, load, g_by_axis)

        records.append({
            'event_sk': row['event_sk'],
            'event_id': row['event_id'],
            'vehicle_id': row['vehicle_id'],
            'database_name': row['database_name'],
            'date_key': date_to_key(fecha),
            'rule_sk': row.get('rule_sk'),
            'rule_id': row['rule_id'],
            'event_type': event_type,
            'Fecha': fecha,
            'Placa': row['placa'],
            'Longitud': row.get('lon'),
            'Latitud': row.get('lat'),
            'Fecha y hora del evento': row['activeFrom'],
            'Distancia Evento (mt)': (row['distance'] or 0) * 1000,
            'Evento- Resumen': event_type,
            'Duracion Evento': row.get('duration_seconds'),
            'Observacion Corta': _build_observacion(event_type, vel, rpm, load, g_force),
            **metrics,
        })

    if not records:
        logging.info("No se generaron registros de habitos.")
        return

    result = pd.DataFrame(records)
    result = result.drop_duplicates(subset=['event_sk']).sort_values(['vehicle_id', 'Fecha y hora del evento'])
    for column in NUMERIC_METRIC_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors='coerce').astype('float64')

    os.makedirs(SEMANTIC_FACTS_PATH, exist_ok=True)
    result.to_parquet(OUTPUT_FILE, index=False, compression='snappy')
    logging.info(f"Guardado: {OUTPUT_FILE} ({len(result):,} registros)")


if __name__ == '__main__':
    main()
