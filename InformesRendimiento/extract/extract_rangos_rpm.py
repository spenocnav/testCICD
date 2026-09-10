# ==============================================================================
# extract_rangos_rpm.py — Bandas de RPM calculadas desde la telemetría
#
# Solo para vehículos de flotas con `range_mode = 'rpm'` (contrato de
# integración §2.5). En vez de leer ExceptionEvent por regla Geotab, corta el eje
# de revoluciones con los rangos del motor (`motor_rpm_bands`, §2.6) y reparte el
# tiempo entre muestras consecutivas de RPM.
#
# Fuentes por vehículo (1 multi_call por lote de dispositivos):
#   StatusData rpm (aW3Nmy…)      -> la muestra y su duración
#   StatusData ignición           -> descartar motor apagado
#   StatusData factor de carga    -> marcar descenso
#   LogRecord                     -> velocidad (ralentí y descenso)
#
# Reglas del cálculo (replican el reporte de referencia):
#   - la duración de una muestra es el delta hasta la muestra ANTERIOR;
#   - se descartan deltas >= 30 s (hueco de telemetría, no operación continua);
#   - se descartan RPM por debajo del piso del motor (primer corte) y el motor
#     apagado (ignición != 1);
#   - velocidad 0 con el motor encendido es Ralentí, no una banda de RPM;
#   - descenso = factor de carga < 5 % con el vehículo en movimiento. Es un
#     SUBCONJUNTO de la banda, igual que en el modo por reglas: la fila de banda
#     lleva todo el tiempo y la fila '… Descenso' la porción en descenso.
#
# Salida AGREGADA (una fila por vehículo/día/banda), no cruda: guardar cada
# muestra de RPM serían millones de filas por vehículo-mes. Por eso el merge usa
# `prefer_new=True`: recalcular un día debe corregirlo.
# ==============================================================================
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # para importar _runner

import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from _runner import run_status_extraction
from config import (
    DIAGNOSTIC_IDS,
    FACTS_PATH,
    rpm_bands_for,
    uses_rpm_ranges,
    VEHICLE_BY_DB_DEVICE,
)
from utils import COLOMBIA_OFFSET, fmt_date, make_row_id, make_vehicle_id, safe_multi_call

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SCRIPT_ID = 'rangos_rpm'
FACT_FILE = os.path.join(FACTS_PATH, 'fact_rpm_band_daily.parquet')
DEDUP_SUBSET = ['row_id']

RPM_DIAG_ID = DIAGNOSTIC_IDS['rpm_habitos']
LOAD_DIAG_ID = DIAGNOSTIC_IDS['factor_carga']
IGNITION_DIAG_ID = 'DiagnosticIgnitionId'

IDLE_BAND = 'Ralentí'
DESCENSO_SUFFIX = ' Descenso'

# Hueco máximo entre muestras que se considera operación continua. Un delta
# mayor significa que el datalogger dejó de reportar, no que el motor estuvo
# ese tiempo en esa banda.
MAX_SAMPLE_GAP_SECONDS = float(os.environ.get('RPM_MAX_SAMPLE_GAP_SECONDS', '30'))
# Descenso: motor girando con carga casi nula mientras el vehículo se mueve.
DESCENSO_MAX_LOAD_FACTOR = float(os.environ.get('RPM_DESCENSO_MAX_LOAD_FACTOR', '5'))
DESCENSO_MIN_SPEED_KMH = float(os.environ.get('RPM_DESCENSO_MIN_SPEED_KMH', '1'))

# Cuánto se mira ANTES de la ventana para poder resolver cada señal en su primer
# instante: el delta de la primera muestra de RPM, y el último valor conocido de
# ignición / carga / velocidad (que cambian con mucha menos frecuencia).
RPM_LOOKBACK = timedelta(minutes=2)
IGNITION_LOOKBACK = timedelta(hours=12)
LOAD_LOOKBACK = timedelta(hours=2)
SPEED_LOOKBACK = timedelta(hours=1)

# Dispositivos por multi_call. Cada uno aporta 4 requests y las respuestas de RPM
# son grandes: lotes chicos evitan respuestas de cientos de MB.
DEVICES_PER_CALL = int(os.environ.get('RPM_DEVICES_PER_CALL', '4'))


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _colombia_midnight_floor(moment: datetime) -> datetime:
    """Baja `moment` al inicio (en Colombia) de su propio día, en UTC.

    La salida es por día Colombia: si la ventana empezara a mitad de un día, ese
    día se guardaría incompleto. Extender hacia atrás hasta la medianoche local
    hace que cada día publicado esté siempre completo; el solape con el chunk
    anterior lo resuelve el merge quedándose con el cálculo más reciente.
    """
    local = moment - COLOMBIA_OFFSET
    local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight + COLOMBIA_OFFSET


def _status_request(device_id: str, diagnostic_id: str, from_str: str, to_str: str) -> list:
    return ['Get', {
        'typeName': 'StatusData',
        'search': {
            'deviceSearch': {'id': device_id},
            'diagnosticSearch': {'id': diagnostic_id},
            'fromDate': from_str,
            'toDate': to_str,
        },
    }]


def _series(rows, value_column: str) -> pd.DataFrame:
    """`dateTime`+valor ordenados; DataFrame vacío tipado si no hay datos."""
    frame = pd.DataFrame(rows or [])
    if frame.empty or 'dateTime' not in frame.columns:
        return pd.DataFrame({'dateTime': pd.Series(dtype='datetime64[ns, UTC]'),
                             value_column: pd.Series(dtype='float64')})
    source = 'data' if 'data' in frame.columns else 'speed'
    out = pd.DataFrame({
        'dateTime': pd.to_datetime(frame['dateTime'], utc=True, errors='coerce'),
        value_column: pd.to_numeric(frame[source], errors='coerce'),
    })
    return out.dropna(subset=['dateTime']).sort_values('dateTime').reset_index(drop=True)


def _assign_bands(rpm: pd.Series, bands: list) -> pd.Series:
    """Banda canónica de cada muestra; NaN por debajo del piso o fuera del eje."""
    edges = [band['rpm_min'] for band in bands]
    top = bands[-1]['rpm_max']
    edges.append(np.inf if top is None else top)
    labels = [band['band'] for band in bands]
    # right=False -> [min, max): los cortes son inclusivos abajo y exclusivos
    # arriba, igual que los declara la fuente maestra.
    return pd.cut(rpm, bins=edges, labels=labels, right=False, ordered=False).astype(object)


def compute_daily_bands(
    rpm_df: pd.DataFrame,
    ignition_df: pd.DataFrame,
    load_df: pd.DataFrame,
    speed_df: pd.DataFrame,
    bands: list,
    window_start,
) -> pd.DataFrame:
    """Horas por (día Colombia, banda) para UN vehículo. Función pura y testeable."""
    empty = pd.DataFrame(columns=['fecha_colombia', 'band', 'duration_hours'])
    if rpm_df.empty or not bands:
        return empty

    df = rpm_df.sort_values('dateTime').reset_index(drop=True)
    df['duration_s'] = df['dateTime'].diff().dt.total_seconds()
    # La ventana se consultó con lookback: esas muestras solo existen para darle
    # duración a la primera muestra real, no se contabilizan.
    df = df[df['dateTime'] >= window_start]
    df = df[df['duration_s'].notna() & (df['duration_s'] > 0)]
    df = df[df['duration_s'] < MAX_SAMPLE_GAP_SECONDS]
    if df.empty:
        return empty

    for other, column in (
        (ignition_df, 'ignition'),
        (load_df, 'load_factor'),
        (speed_df, 'speed'),
    ):
        if other.empty:
            df[column] = np.nan
        else:
            df = pd.merge_asof(
                df.sort_values('dateTime'),
                other.sort_values('dateTime'),
                on='dateTime',
                direction='backward',
            )

    # Motor apagado no consume tiempo de banda. Sin lectura de ignición se
    # descarta la muestra: es la señal que separa operación de vehículo quieto.
    df = df[df['ignition'] == 1]
    if df.empty:
        return empty

    df['band'] = _assign_bands(df['rpm'], bands)
    df = df[df['band'].notna()]
    if df.empty:
        return empty

    speed = df['speed']
    df.loc[speed.fillna(-1) == 0, 'band'] = IDLE_BAND
    df['fecha_colombia'] = (df['dateTime'] - COLOMBIA_OFFSET).dt.date
    df['duration_hours'] = df['duration_s'] / 3600.0

    totals = (
        df.groupby(['fecha_colombia', 'band'], as_index=False)['duration_hours'].sum()
    )

    # Descenso: subconjunto de la misma banda (no una banda aparte), igual que en
    # el modo por reglas, donde `Rango X` ya incluye su tiempo en descenso.
    descenso = df[
        (df['band'] != IDLE_BAND)
        & (df['load_factor'] < DESCENSO_MAX_LOAD_FACTOR)
        & (df['speed'] > DESCENSO_MIN_SPEED_KMH)
    ]
    if not descenso.empty:
        descenso_totals = (
            descenso.groupby(['fecha_colombia', 'band'], as_index=False)['duration_hours'].sum()
        )
        descenso_totals['band'] = descenso_totals['band'] + DESCENSO_SUFFIX
        totals = pd.concat([totals, descenso_totals], ignore_index=True)

    return totals


def extract_chunk(api, devices: list, from_str: str, to_str: str, db_name: str) -> pd.DataFrame:
    window_start = _colombia_midnight_floor(_parse(from_str))
    window_end = _parse(to_str)
    rows = []

    for offset in range(0, len(devices), DEVICES_PER_CALL):
        batch = devices[offset:offset + DEVICES_PER_CALL]
        requests_list = []
        for device_id in batch:
            requests_list.append(_status_request(
                device_id, RPM_DIAG_ID,
                fmt_date(window_start - RPM_LOOKBACK), fmt_date(window_end),
            ))
            requests_list.append(_status_request(
                device_id, IGNITION_DIAG_ID,
                fmt_date(window_start - IGNITION_LOOKBACK), fmt_date(window_end),
            ))
            requests_list.append(_status_request(
                device_id, LOAD_DIAG_ID,
                fmt_date(window_start - LOAD_LOOKBACK), fmt_date(window_end),
            ))
            requests_list.append(['Get', {
                'typeName': 'LogRecord',
                'search': {
                    'deviceSearch': {'id': device_id},
                    'fromDate': fmt_date(window_start - SPEED_LOOKBACK),
                    'toDate': fmt_date(window_end),
                },
            }])

        results = safe_multi_call(api, requests_list)
        if len(results) != len(requests_list):
            raise RuntimeError(
                f"{SCRIPT_ID}: respuesta multi_call incompleta "
                f"({len(results)} resultados para {len(requests_list)} peticiones)"
            )

        for index, device_id in enumerate(batch):
            base = index * 4
            vehicle = VEHICLE_BY_DB_DEVICE.get((db_name, device_id)) or {}
            bands = rpm_bands_for(vehicle.get('motor_type'))
            if not bands:
                # Ya se avisó al construir la lista de dispositivos; acá solo se
                # evita repartir tiempo con cortes que no son de este motor.
                continue
            totals = compute_daily_bands(
                _series(results[base], 'rpm'),
                _series(results[base + 1], 'ignition'),
                _series(results[base + 2], 'load_factor'),
                _series(results[base + 3], 'speed'),
                bands,
                window_start,
            )
            if totals.empty:
                continue
            vehicle_id = make_vehicle_id(db_name, device_id)
            for record in totals.to_dict('records'):
                rows.append({
                    'database_name': db_name,
                    'vehicle_id': vehicle_id,
                    'device_id': device_id,
                    'fecha_colombia': record['fecha_colombia'],
                    'band': record['band'],
                    'duration_hours': float(record['duration_hours']),
                    'row_id': make_row_id(
                        vehicle_id, record['fecha_colombia'], record['band'], 'rpm_band_daily'
                    ),
                })

    return pd.DataFrame(rows)


def _device_filter(db_name: str, device_id: str) -> bool:
    """Solo vehículos en modo 'rpm' cuyo motor tenga rangos configurados."""
    if not uses_rpm_ranges(db_name, device_id):
        return False
    vehicle = VEHICLE_BY_DB_DEVICE.get((db_name, device_id)) or {}
    if rpm_bands_for(vehicle.get('motor_type')):
        return True
    logging.warning(
        "[%s] %s/%s (motor=%s) está en modo RPM pero su motor no tiene rangos "
        "configurados en la fuente maestra; se omite",
        SCRIPT_ID, db_name, vehicle.get('placa') or device_id, vehicle.get('motor_type'),
    )
    return False


def main():
    run_status_extraction(
        script_id=SCRIPT_ID,
        extract_chunk=extract_chunk,
        fact_file=FACT_FILE,
        dedup_subset=DEDUP_SUBSET,
        # 1 día por chunk: la salida es diaria y las respuestas de RPM son
        # grandes. La ventana se extiende a la medianoche local en extract_chunk.
        chunk_days=1,
        default_start='2024-09-01',
        device_filter=_device_filter,
        # Hecho agregado: recalcular un día debe corregir el valor anterior.
        prefer_new=True,
        # Persistir cada día extraído: el hecho es diminuto (una fila por
        # vehículo/día/banda) y el backfill de un año de telemetría densa dura
        # más que las 3 h que el worker le concede a un paso. Sin checkpoint,
        # Bavaria repitió el mismo trabajo desde el 1 de enero en cada corrida
        # diaria y nunca avanzó.
        checkpoint_every=1,
    )


if __name__ == '__main__':
    main()
