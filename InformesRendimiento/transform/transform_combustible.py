import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import hashlib

import numpy as np
import pandas as pd

from config import (
    ECM_COUNTER_DIAGNOSTICS,
    DIMS_PATH,
    FACTS_PATH,
    SEMANTIC_FACTS_PATH,
    VEHICLE_BY_DB_DEVICE,
    rules_for,
    rpm_bands_for,
    uses_rpm_ranges,
)
from fuel import FUEL_KIND_GAS, FUEL_UNIT_GAS, FUEL_UNIT_LIQUID, classify_fuel
from utils import date_to_key, make_row_id, make_vehicle_id, month_to_key, read_facts

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

FACT_STATUS_FILE = os.path.join(FACTS_PATH, 'fact_status_readings.parquet')
FACT_TRIPS_FILE = os.path.join(FACTS_PATH, 'fact_trips.parquet')
FACT_EVENTS_FILE = os.path.join(FACTS_PATH, 'fact_exception_events.parquet')
# Bandas ya agregadas por día para las flotas en modo 'rpm' (extract_rangos_rpm).
FACT_RPM_BANDS_FILE = os.path.join(FACTS_PATH, 'fact_rpm_band_daily.parquet')
DIM_VEH_FILE = os.path.join(DIMS_PATH, 'dim_vehiculos.parquet')
OUTPUT_DAILY = os.path.join(SEMANTIC_FACTS_PATH, 'fact_combustible_daily.parquet')
OUTPUT_MONTHLY = os.path.join(SEMANTIC_FACTS_PATH, 'fact_combustible_monthly.parquet')

COLOMBIA_OFFSET = timedelta(hours=5)
MAX_HORAS_REALISTAS = 24.0
ODO_REVISION_THRESHOLD_KM = 20.0
MAX_ECM_KM_DAY = 1500.0
MAX_SPEED_NORMAL_KMH = 70.0
MAX_SPEED_F28_KMH = 95.0
MAX_PHYSICAL_SPEED_KMH = 130.0
GPS_ECM_WARNING_DIFF_KM = 30.0
GPS_ECM_WARNING_DIFF_PCT = 0.25
GPS_ECM_CRITICAL_DIFF_KM = 100.0
GPS_ECM_CRITICAL_DIFF_PCT = 0.50
HISTORICAL_WINDOW_DAYS = 30
HISTORICAL_MIN_DAYS = 14
DISTANCE_THRESHOLD_VERSION = 'v1'
ECM_ID = ECM_COUNTER_DIAGNOSTICS
COMBUSTIBLE_DEFAULT_START_DATE_STR = '2024-09-01'
COMBUSTIBLE_HISTORY_START_UTC = (
    pd.Timestamp(f'{COMBUSTIBLE_DEFAULT_START_DATE_STR}T00:00:00Z') + pd.Timedelta(hours=5)
)


def process_ecm_counters(veh_status: pd.DataFrame) -> pd.DataFrame:
    """`veh_status` ya viene filtrado al vehículo (ver `partition_by_vehicle`)."""
    device_df = veh_status.copy()
    device_df['dateTime'] = pd.to_datetime(device_df['dateTime'])
    device_df['Fecha'] = (device_df['dateTime'] - COLOMBIA_OFFSET).dt.date

    name_map = {
        ECM_ID['odometro']: 'Odometro',
        ECM_ID['combustible_usado']: 'Combustible',
        ECM_ID['combustible_dispositivo']: 'Combustible_Device',
        ECM_ID['combustible_ralenti']: 'Combustible Ralentí',
        ECM_ID['combustible_ralenti_dispositivo']: 'Combustible Ralentí Device',
        ECM_ID['horas_motor']: 'Horas',
        ECM_ID['horas_ralenti_ecm']: 'Ralentí ECM',
    }

    merged = None
    for diag_id, col_name in name_map.items():
        sub = device_df[device_df['diagnostic_id'] == diag_id]
        if sub.empty:
            tmp = pd.DataFrame(columns=['Fecha', f'{col_name} Inicio', f'{col_name} Fin'])
        else:
            # Son contadores acumulativos: "Inicio" y "Fin" significan la
            # primera y la última lectura cronológicas, no mínimo/máximo. Un
            # único valor espurio intermedio no puede inflar todo el día.
            ordered = sub.sort_values('dateTime')
            grp = ordered.groupby('Fecha')['data'].agg(['first', 'last']).reset_index()
            grp.columns = ['Fecha', f'{col_name} Inicio', f'{col_name} Fin']
            tmp = grp.drop_duplicates(subset=['Fecha'], keep='last')
        availability_column = {
            'Combustible': 'Combustible Disponible',
            'Combustible Ralentí': 'Combustible Ralentí Disponible',
            'Combustible Ralentí Device': 'Combustible Ralentí Device Disponible',
        }.get(col_name)
        if availability_column:
            # Distingue un contador realmente medido con delta cero de un
            # vehículo/día que no publica este diagnóstico. Una sola lectura no
            # basta para calcular el delta diario del contador acumulativo.
            counts = sub.groupby('Fecha')['data'].count() if not sub.empty else pd.Series(dtype=int)
            tmp[availability_column] = tmp['Fecha'].map(counts).fillna(0).ge(2)
        merged = tmp if merged is None else pd.merge(merged, tmp, on='Fecha', how='outer')

    return merged if merged is not None else pd.DataFrame()


def fix_fuel_data(df_ecm: pd.DataFrame) -> pd.DataFrame:
    if df_ecm.empty:
        return df_ecm

    cols_needed = [
        'Combustible Inicio',
        'Combustible Fin',
        'Combustible_Device Inicio',
        'Combustible_Device Fin',
    ]
    for column in cols_needed:
        if column not in df_ecm.columns:
            df_ecm[column] = 0

    for idx, row in df_ecm.iterrows():
        if (row['Combustible Fin'] - row['Combustible Inicio']) == 0:
            df_ecm.at[idx, 'Combustible Inicio'] = row['Combustible_Device Inicio']
            df_ecm.at[idx, 'Combustible Fin'] = row['Combustible_Device Fin']
            if idx > 0 and idx - 1 in df_ecm.index:
                prev = df_ecm.loc[idx - 1]
                df_ecm.at[idx - 1, 'Combustible Inicio'] = prev['Combustible_Device Inicio']
                df_ecm.at[idx - 1, 'Combustible Fin'] = prev['Combustible_Device Fin']

    return df_ecm


def compute_revision_flag(veh_status: pd.DataFrame) -> pd.DataFrame:
    """`veh_status` ya viene filtrado al vehículo (ver `partition_by_vehicle`)."""
    odo = veh_status[veh_status['diagnostic_id'] == ECM_ID['odometro']].copy()
    if odo.empty:
        return pd.DataFrame(columns=['Fecha', 'Revision'])

    odo['dateTime'] = pd.to_datetime(odo['dateTime'])
    odo['Fecha'] = (odo['dateTime'] - COLOMBIA_OFFSET).dt.date

    rows = []
    for fecha, grp in odo.groupby('Fecha'):
        vals_km = grp.sort_values('dateTime')['data'].values / 1000.0
        n = len(vals_km)
        ecm_km_dia = vals_km[-1] - vals_km[0] if n >= 2 else 0
        porcion_interpolada = (vals_km[-1] - vals_km[0]) - (vals_km[-2] - vals_km[1]) if n >= 3 else 0
        revision = (
            (n == 2) or
            (n >= 3 and abs(porcion_interpolada) > ODO_REVISION_THRESHOLD_KM) or
            (n >= 2 and ecm_km_dia < 0) or
            (ecm_km_dia > MAX_ECM_KM_DAY)
        )
        rows.append({'Fecha': fecha, 'Revision': revision})
    return pd.DataFrame(rows)


def compute_speed_revision_flag(ecm_df: pd.DataFrame, is_f28: bool) -> pd.DataFrame:
    if ecm_df.empty:
        return pd.DataFrame(columns=['Fecha', 'Revision'])

    needed = ['Odometro Inicio', 'Odometro Fin', 'Horas Inicio', 'Horas Fin']
    if not all(column in ecm_df.columns for column in needed):
        return pd.DataFrame(columns=['Fecha', 'Revision'])

    df = ecm_df[['Fecha'] + needed].copy()
    ecm_km = (df['Odometro Fin'] - df['Odometro Inicio']) / 1000.0
    ecm_hrs = (df['Horas Fin'] - df['Horas Inicio']) / 3600.0
    speed = (ecm_km / ecm_hrs).replace([np.inf, -np.inf], 0).fillna(0)
    threshold = MAX_SPEED_F28_KMH if is_f28 else MAX_SPEED_NORMAL_KMH
    df['Revision'] = speed > threshold
    return df[['Fecha', 'Revision']]


def process_gps_trips(veh_trips: pd.DataFrame) -> pd.DataFrame:
    """`veh_trips` ya viene filtrado al vehículo (ver `partition_by_vehicle`)."""
    if veh_trips.empty or 'vehicle_id' not in veh_trips.columns:
        return pd.DataFrame(
            columns=[
                'Fecha', 'Hrs GPS', 'Tiempo en ralentí', 'Kms GPS',
                'GPS Trip Count', 'GPS Extraction Valid',
            ]
        )
    device_trips = veh_trips.copy()

    device_trips['stop'] = pd.to_datetime(device_trips['stop'])
    device_trips['Fecha'] = (device_trips['stop'] - COLOMBIA_OFFSET).dt.date
    distance = pd.to_numeric(device_trips['distance'], errors='coerce')
    idling_seconds = pd.to_numeric(device_trips['idling_duration_s'], errors='coerce')
    driving_seconds = pd.to_numeric(device_trips['driving_duration_s'], errors='coerce')
    device_trips['gps_row_valid'] = (
        distance.notna()
        & np.isfinite(distance)
        & (distance >= 0)
        & idling_seconds.notna()
        & np.isfinite(idling_seconds)
        & (idling_seconds >= 0)
        & driving_seconds.notna()
        & np.isfinite(driving_seconds)
        & (driving_seconds >= 0)
    )
    device_trips['distance'] = distance
    device_trips['idling_h'] = idling_seconds / 3600
    device_trips['driving_h'] = driving_seconds / 3600
    device_trips['total_h'] = device_trips['idling_h'] + device_trips['driving_h']

    grouped = device_trips.groupby('Fecha').agg(
        Hrs_GPS=('total_h', 'sum'),
        Tiempo_ralenti=('idling_h', 'sum'),
        Kms_GPS=('distance', lambda values: values.sum(min_count=1)),
        GPS_Trip_Count=('distance', 'size'),
        GPS_Extraction_Valid=('gps_row_valid', 'all'),
    ).reset_index()
    grouped.columns = [
        'Fecha', 'Hrs GPS', 'Tiempo en ralentí', 'Kms GPS', 'GPS Trip Count',
        'GPS Extraction Valid',
    ]
    return grouped.drop_duplicates(subset=['Fecha'], keep='last')


def _distance_fingerprint(row: pd.Series) -> str:
    values = (
        row.get('Fecha'),
        row.get('Kms ECM'),
        row.get('Kms GPS'),
        row.get('Hrs ECM'),
        row.get('Hrs GPS'),
        row.get('ECM Reading Count'),
        row.get('GPS Trip Count'),
        DISTANCE_THRESHOLD_VERSION,
    )
    payload = '|'.join('null' if pd.isna(value) else str(value) for value in values)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def evaluate_distance_quality(
    veh_status: pd.DataFrame,
    ecm_df: pd.DataFrame,
    gps_df: pd.DataFrame,
    *,
    is_f28: bool,
) -> pd.DataFrame:
    """Clasifica la distancia diaria sin alterar las dos fuentes crudas.

    Los fallos inequívocos del ECM pueden caer automáticamente a GPS. Una
    discrepancia donde ambas fuentes siguen siendo físicamente posibles queda
    sin distancia efectiva hasta que una persona la resuelva.
    """
    odo = veh_status[veh_status['diagnostic_id'] == ECM_ID['odometro']].copy()
    odo_rows: list[dict] = []
    if not odo.empty:
        odo['dateTime'] = pd.to_datetime(odo['dateTime'], utc=True, errors='coerce')
        odo['data'] = pd.to_numeric(odo['data'], errors='coerce')
        odo = odo.dropna(subset=['dateTime', 'data']).sort_values('dateTime')
        odo['Fecha'] = (odo['dateTime'] - COLOMBIA_OFFSET).dt.date
        for fecha, group in odo.groupby('Fecha'):
            values = group['data'].to_numpy(dtype=float) / 1000.0
            deltas = np.diff(values)
            outer = values[-1] - values[0] if len(values) >= 2 else np.nan
            boundary_gap = (
                abs(outer - (values[-2] - values[1])) if len(values) >= 4 else 0.0
            )
            odo_rows.append(
                {
                    'Fecha': fecha,
                    'ECM Reading Count': len(values),
                    'ECM Negative Step': bool((deltas < 0).any()),
                    'ECM Internal Jump': bool((np.abs(deltas) > MAX_ECM_KM_DAY).any()),
                    'ECM Boundary Gap': float(boundary_gap),
                }
            )

    odo_quality = pd.DataFrame(odo_rows)
    frames = [frame for frame in (ecm_df, gps_df, odo_quality) if not frame.empty]
    if not frames:
        return pd.DataFrame()
    quality = frames[0].copy()
    for frame in frames[1:]:
        quality = pd.merge(quality, frame, on='Fecha', how='outer')

    def number(name: str) -> pd.Series:
        return pd.to_numeric(
            quality.get(name, pd.Series(np.nan, index=quality.index)), errors='coerce'
        )

    kms_ecm = (number('Odometro Fin') - number('Odometro Inicio')) / 1000.0
    hrs_ecm = (number('Horas Fin') - number('Horas Inicio')) / 3600.0
    kms_gps = number('Kms GPS')
    hrs_gps = number('Hrs GPS')
    readings = number('ECM Reading Count').fillna(0).astype(int)
    trips = number('GPS Trip Count').fillna(0).astype(int)
    gps_extraction_valid = quality.get(
        'GPS Extraction Valid', pd.Series(True, index=quality.index, dtype=bool)
    ).fillna(False).astype(bool)
    speed_ecm = kms_ecm / hrs_ecm.where(hrs_ecm > 0)
    speed_gps = kms_gps / hrs_gps.where(hrs_gps > 0)

    gps_valid = (
        gps_extraction_valid
        & (trips >= 1)
        & kms_gps.notna()
        & (kms_gps >= 0)
        & (kms_gps <= MAX_ECM_KM_DAY)
        & hrs_gps.notna()
        & (hrs_gps > 0)
        & speed_gps.between(0, MAX_PHYSICAL_SPEED_KMH, inclusive='both')
    )

    records: list[dict] = []
    for idx, row in quality.iterrows():
        ecm_invalid_reason: str | None = None
        warning_reason: str | None = None
        if readings.iat[idx] < 3:
            ecm_invalid_reason = 'insufficient_ecm_readings'
        elif bool(row.get('ECM Negative Step', False)):
            ecm_invalid_reason = 'ecm_reset_or_negative_step'
        elif bool(row.get('ECM Internal Jump', False)):
            ecm_invalid_reason = 'ecm_internal_jump'
        elif float(row.get('ECM Boundary Gap') or 0.0) > ODO_REVISION_THRESHOLD_KM:
            ecm_invalid_reason = 'ecm_boundary_interpolation'
        elif pd.isna(kms_ecm.iat[idx]) or kms_ecm.iat[idx] < 0:
            ecm_invalid_reason = 'ecm_negative_or_missing_delta'
        elif kms_ecm.iat[idx] > MAX_ECM_KM_DAY:
            ecm_invalid_reason = 'ecm_daily_limit'
        elif pd.notna(speed_ecm.iat[idx]) and speed_ecm.iat[idx] > MAX_PHYSICAL_SPEED_KMH:
            ecm_invalid_reason = 'ecm_physical_speed'
        else:
            operational_limit = MAX_SPEED_F28_KMH if is_f28 else MAX_SPEED_NORMAL_KMH
            if pd.notna(speed_ecm.iat[idx]) and speed_ecm.iat[idx] > operational_limit:
                warning_reason = 'ecm_operational_speed'

        diff_km = (
            abs(kms_ecm.iat[idx] - kms_gps.iat[idx])
            if pd.notna(kms_ecm.iat[idx]) and pd.notna(kms_gps.iat[idx])
            else np.nan
        )
        diff_base = max(kms_ecm.iat[idx], kms_gps.iat[idx]) if pd.notna(diff_km) else np.nan
        diff_pct = diff_km / diff_base if pd.notna(diff_base) and diff_base > 0 else 0.0
        mismatch_reason: str | None = None
        mismatch_severity: str | None = None
        if ecm_invalid_reason is None and gps_valid.iat[idx]:
            if diff_km > GPS_ECM_CRITICAL_DIFF_KM and diff_pct > GPS_ECM_CRITICAL_DIFF_PCT:
                mismatch_reason = 'ecm_gps_critical_mismatch'
                mismatch_severity = 'critical'
            elif diff_km > GPS_ECM_WARNING_DIFF_KM and diff_pct > GPS_ECM_WARNING_DIFF_PCT:
                mismatch_reason = 'ecm_gps_warning_mismatch'
                mismatch_severity = 'warning'

        reason = ecm_invalid_reason or mismatch_reason or warning_reason
        if ecm_invalid_reason:
            status = 'critical'
            source = 'gps_auto' if gps_valid.iat[idx] else 'none'
            effective = kms_gps.iat[idx] if gps_valid.iat[idx] else np.nan
        elif mismatch_reason or warning_reason:
            status = mismatch_severity or 'warning'
            source = 'none'
            effective = np.nan
        else:
            status = 'ok'
            source = 'ecm'
            effective = kms_ecm.iat[idx]

        records.append(
            {
                'Fecha': row['Fecha'],
                'Kms Effective': effective,
                'Distance Source': source,
                'Distance Quality Status': status,
                'Distance Quality Reason': reason or 'ok',
                'Distance Diff Km': diff_km,
                'Distance Diff Pct': diff_pct if pd.notna(diff_km) else np.nan,
                'GPS Quality Valid': bool(gps_valid.iat[idx]),
                'GPS Trip Count': int(trips.iat[idx]),
                'ECM Reading Count': int(readings.iat[idx]),
                'Revision': status != 'ok',
            }
        )

    result = pd.DataFrame(records).sort_values('Fecha').reset_index(drop=True)

    # Baseline robusto sin mirar el día actual ni fechas futuras.
    accepted_ratios: list[tuple[pd.Timestamp, float]] = []
    for idx, row in result.iterrows():
        fecha = pd.Timestamp(row['Fecha'])
        raw_row = quality[quality['Fecha'] == row['Fecha']].iloc[0]
        raw_ecm = (
            (float(raw_row.get('Odometro Fin', np.nan)) - float(raw_row.get('Odometro Inicio', np.nan))) / 1000.0
            if pd.notna(raw_row.get('Odometro Fin')) and pd.notna(raw_row.get('Odometro Inicio'))
            else np.nan
        )
        raw_gps = float(raw_row.get('Kms GPS', np.nan))
        prior = [
            ratio for prior_date, ratio in accepted_ratios
            if fecha - pd.Timedelta(days=HISTORICAL_WINDOW_DAYS) <= prior_date < fecha
        ]
        if (
            row['Distance Quality Status'] == 'ok'
            and len(prior) >= HISTORICAL_MIN_DAYS
            and pd.notna(raw_ecm)
            and pd.notna(raw_gps)
            and raw_gps > 0
            and abs(raw_ecm - raw_gps) > GPS_ECM_WARNING_DIFF_KM
        ):
            median = float(np.median(prior))
            mad = float(np.median(np.abs(np.asarray(prior) - median)))
            ratio = raw_ecm / raw_gps
            if abs(ratio - median) > max(abs(median) * 0.25, 5.0 * mad):
                result.at[idx, 'Kms Effective'] = np.nan
                result.at[idx, 'Distance Source'] = 'none'
                result.at[idx, 'Distance Quality Status'] = 'warning'
                result.at[idx, 'Distance Quality Reason'] = 'historical_ecm_gps_deviation'
                result.at[idx, 'Revision'] = True
        if result.at[idx, 'Distance Quality Status'] == 'ok' and raw_gps > 0:
            accepted_ratios.append((fecha, raw_ecm / raw_gps))

    result['Distance Threshold Version'] = DISTANCE_THRESHOLD_VERSION
    fingerprint_source = pd.merge(
        result,
        quality[['Fecha'] + [
            column for column in ('Odometro Inicio', 'Odometro Fin', 'Horas Inicio', 'Horas Fin', 'Kms GPS', 'Hrs GPS')
            if column in quality.columns
        ]],
        on='Fecha',
        how='left',
    )
    fingerprint_source['Kms ECM'] = (
        pd.to_numeric(fingerprint_source.get('Odometro Fin'), errors='coerce')
        - pd.to_numeric(fingerprint_source.get('Odometro Inicio'), errors='coerce')
    ) / 1000.0
    fingerprint_source['Kms GPS'] = pd.to_numeric(fingerprint_source.get('Kms GPS'), errors='coerce')
    fingerprint_source['Hrs ECM'] = (
        pd.to_numeric(fingerprint_source.get('Horas Fin'), errors='coerce')
        - pd.to_numeric(fingerprint_source.get('Horas Inicio'), errors='coerce')
    ) / 3600.0
    fingerprint_source['Hrs GPS'] = pd.to_numeric(fingerprint_source.get('Hrs GPS'), errors='coerce')
    result['Distance Quality Fingerprint'] = fingerprint_source.apply(_distance_fingerprint, axis=1)
    return result


def _split_exception_events_by_day(dev_ev: pd.DataFrame) -> pd.DataFrame:
    """
    Replica la lógica del script original:
    - descarta anomalías (>24h, <=0h o timestamps inválidos)
    - recorta el borde inferior al inicio histórico del pipeline
    - distribuye duración y distancia por día Colombia
    """
    if dev_ev.empty:
        return pd.DataFrame(columns=['Fecha', 'rule_name', 'duration_hours', 'distance'])

    active_from = pd.to_datetime(dev_ev['activeFrom'], utc=True, errors='coerce')
    active_to = pd.to_datetime(dev_ev['activeTo'], utc=True, errors='coerce')
    raw_duration_seconds = pd.to_numeric(dev_ev['duration_seconds'], errors='coerce')
    distance = pd.to_numeric(dev_ev.get('distance', 0), errors='coerce').fillna(0.0)
    timestamp_duration_seconds = (active_to - active_from).dt.total_seconds()
    source_duration_seconds = raw_duration_seconds.where(raw_duration_seconds > 0, timestamp_duration_seconds)

    clipped_start = active_from.where(active_from >= COMBUSTIBLE_HISTORY_START_UTC, COMBUSTIBLE_HISTORY_START_UTC)
    valid = (
        active_from.notna() &
        active_to.notna() &
        source_duration_seconds.notna() &
        (source_duration_seconds > 0) &
        (source_duration_seconds <= MAX_HORAS_REALISTAS * 3600) &
        (active_to > clipped_start)
    )
    if not valid.any():
        return pd.DataFrame(columns=['Fecha', 'rule_name', 'duration_hours', 'distance'])

    work = dev_ev.loc[valid, ['rule_name']].copy()
    work['distance'] = distance.loc[valid].astype(float).values
    work['start_utc'] = clipped_start.loc[valid].values
    work['end_utc'] = active_to.loc[valid].values
    work['duration_seconds'] = (work['end_utc'] - work['start_utc']).dt.total_seconds()
    work = work[work['duration_seconds'] > 0].copy()
    if work.empty:
        return pd.DataFrame(columns=['Fecha', 'rule_name', 'duration_hours', 'distance'])

    start_local = work['start_utc'] - COLOMBIA_OFFSET
    end_local = work['end_utc'] - COLOMBIA_OFFSET
    end_local_last_instant = end_local - pd.Timedelta(microseconds=1)
    same_day = start_local.dt.date == end_local_last_instant.dt.date

    segments = []

    same_rows = work.loc[same_day].copy()
    if not same_rows.empty:
        same_local = same_rows['start_utc'] - COLOMBIA_OFFSET
        segments.append(pd.DataFrame({
            'Fecha': same_local.dt.date,
            'rule_name': same_rows['rule_name'].values,
            'duration_hours': same_rows['duration_seconds'].values / 3600.0,
            'distance': same_rows['distance'].values,
        }))

    cross_rows = work.loc[~same_day].copy()
    if not cross_rows.empty:
        cross_start_local = cross_rows['start_utc'] - COLOMBIA_OFFSET
        midnight_after_start_local = cross_start_local.dt.normalize() + pd.Timedelta(days=1)
        first_segment_seconds = (midnight_after_start_local - cross_start_local).dt.total_seconds()
        first_segment_seconds = np.minimum(first_segment_seconds, cross_rows['duration_seconds'].values)
        second_segment_seconds = cross_rows['duration_seconds'].values - first_segment_seconds
        first_segment_distance = np.divide(
            cross_rows['distance'].values * first_segment_seconds,
            cross_rows['duration_seconds'].values,
            out=np.zeros(len(cross_rows), dtype=float),
            where=cross_rows['duration_seconds'].values > 0,
        )
        second_segment_distance = cross_rows['distance'].values - first_segment_distance
        cross_end_local_last = (cross_rows['end_utc'] - COLOMBIA_OFFSET - pd.Timedelta(microseconds=1)).dt.date

        first_mask = first_segment_seconds > 0
        if first_mask.any():
            segments.append(pd.DataFrame({
                'Fecha': cross_start_local.dt.date[first_mask],
                'rule_name': cross_rows.loc[first_mask, 'rule_name'].values,
                'duration_hours': first_segment_seconds[first_mask] / 3600.0,
                'distance': first_segment_distance[first_mask],
            }))

        second_mask = second_segment_seconds > 0
        if second_mask.any():
            segments.append(pd.DataFrame({
                'Fecha': cross_end_local_last[second_mask],
                'rule_name': cross_rows.loc[second_mask, 'rule_name'].values,
                'duration_hours': second_segment_seconds[second_mask] / 3600.0,
                'distance': second_segment_distance[second_mask],
            }))

    if not segments:
        return pd.DataFrame(columns=['Fecha', 'rule_name', 'duration_hours', 'distance'])
    return pd.concat(segments, ignore_index=True)


def process_exception_events(veh_events: pd.DataFrame, rules: dict) -> pd.DataFrame:
    """`veh_events` ya viene filtrado al vehículo (ver `partition_by_vehicle`)."""
    if veh_events.empty or not rules:
        return pd.DataFrame(columns=['Fecha'])

    rule_id_to_name = {value: key for key, value in rules.items()}
    dev_ev = veh_events.copy()

    dev_ev['activeFrom'] = pd.to_datetime(dev_ev['activeFrom'])
    dev_ev['rule_name'] = dev_ev['rule_id'].map(rule_id_to_name)
    dev_ev = dev_ev.dropna(subset=['rule_name'])
    segments = _split_exception_events_by_day(dev_ev)
    if segments.empty:
        return pd.DataFrame(columns=['Fecha'])

    grp = segments.groupby(['Fecha', 'rule_name']).agg(
        duration_sum=('duration_hours', 'sum'),
        distance_sum=('distance', 'sum'),
    ).reset_index()

    dur_pivot = grp.pivot_table(index='Fecha', columns='rule_name', values='duration_sum', aggfunc='sum').reset_index()
    dur_pivot.columns.name = None
    dist_pivot = grp.pivot_table(index='Fecha', columns='rule_name', values='distance_sum', aggfunc='sum').reset_index()
    dist_pivot.columns.name = None
    dist_pivot = dist_pivot.rename(columns={column: f'Distancia {column}' for column in dist_pivot.columns if column != 'Fecha'})
    return pd.merge(dur_pivot, dist_pivot, on='Fecha', how='outer')


def process_rpm_bands(veh_bands: pd.DataFrame) -> pd.DataFrame:
    """Horas por banda de un vehículo en modo 'rpm', con el mismo shape que
    `process_exception_events`.

    El extractor ya entregó el tiempo repartido por (día Colombia, banda), así
    que acá solo se pivota: de ahí en adelante el cálculo de porcentajes,
    descensos y agregado mensual es idéntico al del modo por reglas. Sin columna
    de distancia: los RPM no la traen, y ninguna métrica publicada la usa.
    """
    if veh_bands.empty:
        return pd.DataFrame(columns=['Fecha'])

    frame = veh_bands.copy()
    frame['Fecha'] = pd.to_datetime(frame['fecha_colombia']).dt.date
    pivot = frame.pivot_table(
        index='Fecha', columns='band', values='duration_hours', aggfunc='sum'
    ).reset_index()
    pivot.columns.name = None
    return pivot


def calculate_metrics(
    merged_data: pd.DataFrame,
    placa: str,
    fuel_kind: str = "liquid",
) -> pd.DataFrame:
    def col(column_name):
        values = merged_data.get(
            column_name, pd.Series(0.0, index=merged_data.index, dtype="float64")
        )
        # Los diagnósticos no soportados crean columnas object vacías durante los
        # outer joins. Normalizarlas evita divisiones Python 0/0 y permite que un
        # vehículo sin el nuevo contador de combustible en ralentí siga cargando.
        return pd.to_numeric(values, errors="coerce").fillna(0.0).astype(float)

    is_gas = fuel_kind == FUEL_KIND_GAS
    fuel_unit = FUEL_UNIT_GAS if is_gas else FUEL_UNIT_LIQUID
    volume_divisor = 1.0 if is_gas else 3.7854118
    idle_prefix = 'Combustible Ralentí Device' if is_gas else 'Combustible Ralentí'
    idle_availability_column = f'{idle_prefix} Disponible'

    combustible_disponible = merged_data.get(
        'Combustible Disponible',
        pd.Series(False, index=merged_data.index, dtype=bool),
    ).fillna(False).astype(bool)
    combustible_ralenti_disponible = merged_data.get(
        idle_availability_column,
        pd.Series(False, index=merged_data.index, dtype=bool),
    ).fillna(False).astype(bool)

    merged_data['Horas Inicio'] = col('Horas Inicio') / 3600
    merged_data['Horas Fin'] = col('Horas Fin') / 3600
    merged_data['Odometro Inicio'] = col('Odometro Inicio') / 1000
    merged_data['Odometro Fin'] = col('Odometro Fin') / 1000
    merged_data['Combustible Inicio'] = col('Combustible Inicio') / volume_divisor
    merged_data['Combustible Fin'] = col('Combustible Fin') / volume_divisor
    merged_data['Ralentí ECM Inicio'] = col('Ralentí ECM Inicio') / 3600
    merged_data['Ralentí ECM Fin'] = col('Ralentí ECM Fin') / 3600

    merged_data['Kms ECM'] = col('Odometro Fin') - col('Odometro Inicio')
    merged_data['Hrs ECM'] = col('Horas Fin') - col('Horas Inicio')
    kms_effective = pd.to_numeric(
        merged_data.get('Kms Effective', merged_data['Kms ECM']), errors='coerce'
    )
    distance_source = merged_data.get(
        'Distance Source', pd.Series('ecm', index=merged_data.index, dtype=object)
    ).fillna('none').astype(str)
    distance_diff_km = pd.to_numeric(
        merged_data.get('Distance Diff Km', pd.Series(np.nan, index=merged_data.index)),
        errors='coerce',
    )
    distance_diff_pct = pd.to_numeric(
        merged_data.get('Distance Diff Pct', pd.Series(np.nan, index=merged_data.index)),
        errors='coerce',
    )
    effective_hours = pd.Series(np.nan, index=merged_data.index, dtype='float64')
    effective_hours = effective_hours.mask(distance_source.eq('ecm'), col('Hrs ECM'))
    effective_hours = effective_hours.mask(distance_source.eq('gps_auto'), col('Hrs GPS'))
    comb = col('Combustible Fin') - col('Combustible Inicio')
    # Para gas no se usa el fallback DiagnosticDeviceTotalFuelId: el contrato
    # acordado replica TotalFuelUsed del legacy, expresado en m³. Una sola lectura
    # o un delta negativo significa dato no calculable, no consumo cero.
    if is_gas:
        comb = comb.where(combustible_disponible & (comb >= 0))
    merged_data['Comb'] = comb
    comb_ralenti = (
        (
            col(f'{idle_prefix} Fin') - col(f'{idle_prefix} Inicio')
        ) / volume_divisor
    ).where(combustible_ralenti_disponible)
    if is_gas:
        comb_ralenti = comb_ralenti.where(comb_ralenti >= 0)
    merged_data['Comb Ralentí'] = comb_ralenti
    merged_data['Ralentí ECM'] = col('Ralentí ECM Fin') - col('Ralentí ECM Inicio')

    valid_comb = comb.where(comb > 0)
    valid_hours = col('Hrs ECM').where(col('Hrs ECM') > 0)
    efficiency = (col('Kms ECM') / valid_comb).replace([np.inf, -np.inf], np.nan)
    fuel_per_hour = (comb / valid_hours).replace([np.inf, -np.inf], np.nan)
    if not is_gas:
        efficiency = efficiency.fillna(0.0)
        fuel_per_hour = fuel_per_hour.fillna(0.0)
    idle_fuel_per_hour = (
        comb_ralenti / col('Ralentí ECM').where(col('Ralentí ECM') > 0)
    ).replace([np.inf, -np.inf], np.nan)
    merged_data['km/gal'] = efficiency if not is_gas else np.nan
    merged_data['gal/hr'] = fuel_per_hour if not is_gas else np.nan
    merged_data['gal/hr Ralentí'] = idle_fuel_per_hour if not is_gas else np.nan
    merged_data['km/m3'] = efficiency if is_gas else np.nan
    merged_data['m3/hr'] = fuel_per_hour if is_gas else np.nan
    merged_data['m3/hr Ralentí'] = idle_fuel_per_hour if is_gas else np.nan
    merged_data['Velocidad Promedio'] = (col('Kms ECM') / col('Hrs ECM')).replace([np.inf, -np.inf], 0)
    effective_efficiency = (kms_effective / valid_comb).replace([np.inf, -np.inf], np.nan)
    effective_speed = (kms_effective / effective_hours.where(effective_hours > 0)).replace(
        [np.inf, -np.inf], np.nan
    )
    merged_data['km/gal Effective'] = effective_efficiency if not is_gas else np.nan
    merged_data['km/m3 Effective'] = effective_efficiency if is_gas else np.nan
    merged_data['Velocidad Promedio Effective'] = effective_speed

    merged_data['km/gal gps'] = (col('Kms GPS') / col('Comb')).replace([np.inf, -np.inf], 0)
    merged_data['gal/hr gps'] = (col('Comb') / col('Hrs GPS')).replace([np.inf, -np.inf], 0)
    merged_data['Velocidad Promedio GPS'] = (col('Kms GPS') / col('Hrs GPS')).replace([np.inf, -np.inf], 0)

    rangos = [
        'Rango Bajo',
        'Rango Economico',
        'Rango Balanceado',
        'Rango Potencia',
        'Exceso RPM',
        'Rango Potencia Ineficiente',
    ]
    merged_data['Tiempo Total en Rango'] = sum(col(rango) for rango in rangos)
    for rango in rangos:
        merged_data[f'% {rango}'] = (col(rango) / col('Tiempo Total en Rango')).replace([np.inf, -np.inf], 0)

    rangos_descenso = [f'{rango} Descenso' for rango in rangos]
    merged_data['Tiempo Total en Rango de Descenso'] = sum(col(rango) for rango in rangos_descenso)
    for rango in rangos_descenso:
        merged_data[f'% {rango}'] = (col(rango) / col('Tiempo Total en Rango de Descenso')).replace([np.inf, -np.inf], 0)

    merged_data['Tiempo Total en Rango Sin Descenso'] = (
        col('Tiempo Total en Rango') - col('Tiempo Total en Rango de Descenso')
    )
    for rango in rangos:
        rango_descenso = f'{rango} Descenso'
        merged_data[f'% {rango} Sin Descenso'] = (
            (col(rango) - col(rango_descenso)) / col('Tiempo Total en Rango Sin Descenso')
        ).replace([np.inf, -np.inf], 0)

    merged_data['% Ralentí ECM'] = (col('Ralentí ECM') / col('Hrs ECM')).replace([np.inf, -np.inf], 0)
    merged_data['% Ralentí GPS'] = (col('Tiempo en ralentí') / col('Hrs GPS')).replace([np.inf, -np.inf], 0)
    max_hrs = pd.concat([col('Hrs GPS'), col('Hrs ECM')], axis=1).max(axis=1)

    # `% Ralentí` publicado: UNA sola definición para todas las flotas, con
    # numerador y denominador de la MISMA fuente y en cascada por disponibilidad.
    #
    # 1) contador ECM: es la medición del propio ECU y la que ya usa la tarjeta
    #    vecina de consumo en ralentí (gal/h = Comb Ralentí / Ralentí ECM), así
    #    que ambas quedan sobre la misma base;
    # 2) banda Ralentí (regla Geotab o, en modo por RPM, velocidad 0 con motor
    #    encendido) para las bases sin contador ECM;
    # 3) ralentí de los viajes GPS como último recurso.
    #
    # Mezclar fuentes entre numerador y denominador era lo que producía lecturas
    # contradictorias entre gráficas y porcentajes por encima del 100 %. El
    # resultado se acota: una fracción de tiempo no puede pasar de 1.
    idle_sources = (
        (col('Ralentí ECM'), col('Hrs ECM'), 'ecm'),
        (col('Ralentí'), max_hrs, 'banda'),
        (col('Tiempo en ralentí'), col('Hrs GPS'), 'gps'),
    )
    idle_ratio = pd.Series(0.0, index=merged_data.index, dtype='float64')
    idle_base = pd.Series(0.0, index=merged_data.index, dtype='float64')
    idle_source = pd.Series('', index=merged_data.index, dtype=object)
    pending = pd.Series(True, index=merged_data.index)
    for numerator, denominator, source_name in idle_sources:
        usable = pending & (denominator > 0) & (numerator > 0)
        if not usable.any():
            continue
        ratio = (numerator / denominator.where(denominator > 0)).replace(
            [np.inf, -np.inf], np.nan
        )
        idle_ratio = idle_ratio.mask(usable, ratio.fillna(0.0))
        idle_base = idle_base.mask(usable, denominator)
        idle_source = idle_source.mask(usable, source_name)
        pending &= ~usable

    merged_data['% Ralentí'] = idle_ratio.clip(lower=0.0, upper=1.0)
    # Denominador efectivo y fuente elegida. El primero es el peso correcto para
    # reagregar el porcentaje por flota/periodo (antes se asumía
    # max(Hrs GPS, Hrs ECM), que ya no siempre es el divisor usado); el segundo
    # deja rastro de por qué un vehículo mide como mide.
    merged_data['Horas Ralentí Base'] = idle_base
    merged_data['Fuente Ralentí'] = idle_source

    gps_quality_valid = merged_data.get(
        'GPS Quality Valid',
        pd.Series(False, index=merged_data.index, dtype=bool),
    ).fillna(False).astype(bool)
    merged_data = merged_data.fillna(0)
    # En consumo/ratios de combustible, null significa “diagnóstico no
    # disponible”; cero queda reservado para una medición válida cuyo delta fue
    # realmente cero. Las columnas de la otra unidad también permanecen null.
    merged_data['Comb'] = comb
    merged_data['Comb Ralentí'] = comb_ralenti
    merged_data['km/gal'] = efficiency if not is_gas else np.nan
    merged_data['gal/hr'] = fuel_per_hour if not is_gas else np.nan
    merged_data['gal/hr Ralentí'] = idle_fuel_per_hour if not is_gas else np.nan
    merged_data['km/m3'] = efficiency if is_gas else np.nan
    merged_data['m3/hr'] = fuel_per_hour if is_gas else np.nan
    merged_data['m3/hr Ralentí'] = idle_fuel_per_hour if is_gas else np.nan
    merged_data['Kms Effective'] = kms_effective
    merged_data['km/gal Effective'] = effective_efficiency if not is_gas else np.nan
    merged_data['km/m3 Effective'] = effective_efficiency if is_gas else np.nan
    merged_data['Velocidad Promedio Effective'] = effective_speed
    merged_data['Distance Source'] = distance_source
    merged_data['Distance Diff Km'] = distance_diff_km
    merged_data['Distance Diff Pct'] = distance_diff_pct
    merged_data['GPS Quality Valid'] = gps_quality_valid
    merged_data['Placa'] = placa
    merged_data['Fuel Kind'] = fuel_kind
    merged_data['Fuel Unit'] = fuel_unit

    final_columns = [
        'Fecha',
        'Placa',
        'Kms ECM',
        'Kms GPS',
        'Kms Effective',
        'Hrs ECM',
        'Hrs GPS',
        'Comb',
        'Comb Ralentí',
        'Fuel Kind',
        'Fuel Unit',
        'km/gal',
        'gal/hr',
        'gal/hr Ralentí',
        'km/m3',
        'm3/hr',
        'm3/hr Ralentí',
        'Velocidad Promedio',
        'km/gal Effective',
        'km/m3 Effective',
        'Velocidad Promedio Effective',
        'km/gal gps',
        'gal/hr gps',
        'Velocidad Promedio GPS',
        '% Rango Bajo',
        '% Rango Economico',
        '% Rango Balanceado',
        '% Rango Potencia',
        '% Exceso RPM',
        '% Rango Potencia Ineficiente',
        '% Ralentí GPS',
        '% Ralentí ECM',
        '% Ralentí',
        'Horas Ralentí Base',
        'Fuente Ralentí',
        'Tiempo Total en Rango',
        'Ralentí',
        'Ralentí ECM',
        'Tiempo en ralentí',
        '% Rango Bajo Descenso',
        '% Rango Economico Descenso',
        '% Rango Balanceado Descenso',
        '% Rango Potencia Descenso',
        '% Exceso RPM Descenso',
        '% Rango Potencia Ineficiente Descenso',
        'Tiempo Total en Rango de Descenso',
        '% Rango Bajo Sin Descenso',
        '% Rango Economico Sin Descenso',
        '% Rango Balanceado Sin Descenso',
        '% Rango Potencia Sin Descenso',
        '% Exceso RPM Sin Descenso',
        '% Rango Potencia Ineficiente Sin Descenso',
        'Tiempo Total en Rango Sin Descenso',
        'Revision',
        'Distance Source',
        'Distance Quality Status',
        'Distance Quality Reason',
        'Distance Diff Km',
        'Distance Diff Pct',
        'GPS Quality Valid',
        'GPS Trip Count',
        'ECM Reading Count',
        'Distance Quality Fingerprint',
        'Distance Threshold Version',
    ]
    for column_name in final_columns:
        if column_name not in merged_data.columns:
            merged_data[column_name] = False if column_name == 'Revision' else 0
    return merged_data[final_columns]


def aggregate_monthly_from_daily(daily_metrics: pd.DataFrame, placa: str) -> pd.DataFrame:
    """Mensual = suma de los días ya calculados, NO un recálculo del contador.

    La versión anterior tomaba `max(contador) - min(contador)` del mes sobre el
    diagnóstico crudo de combustible, sin pasar por `fix_fuel_data`. Los saltos
    del contador `TotalFuelUsed` (lecturas escasas, huecos entre días) entraban
    íntegros al mes: en WPK740/2026-06 sumaban 115 gal contra los ~4 gal del
    contador del dispositivo, con el odómetro prácticamente sin brecha. El
    resultado era combustible inflado y km/gal ~12 % por debajo del mismo cálculo
    hecho sobre la tabla diaria, que es la que alimenta KPIs, detalle y ranking.

    Agregando el diario, mes y día quedan por construcción consistentes.
    """
    if daily_metrics.empty:
        return pd.DataFrame()

    df = daily_metrics.copy()
    fuel_kind = (
        str(df['Fuel Kind'].dropna().iloc[0])
        if 'Fuel Kind' in df.columns and not df['Fuel Kind'].dropna().empty
        else 'liquid'
    )
    fuel_unit = FUEL_UNIT_GAS if fuel_kind == FUEL_KIND_GAS else FUEL_UNIT_LIQUID
    is_gas = fuel_kind == FUEL_KIND_GAS
    df['Anio-Mes'] = pd.to_datetime(df['Fecha']).dt.to_period('M').astype(str)
    columns = [
        'Kms ECM', 'Kms Effective', 'Hrs ECM', 'Comb', 'Comb Ralentí', 'Ralentí ECM'
    ]
    for column_name in columns:
        if column_name not in df.columns:
            df[column_name] = np.nan if column_name == 'Comb Ralentí' else 0.0
    if is_gas:
        # Si falta TotalFuelUsed ese día, sus km/horas siguen contando en los
        # totales operativos, pero no pueden entrar al denominador de una razón
        # de combustible: inflarían km/m³ y reducirían m³/h.
        valid_fuel = pd.to_numeric(df['Comb'], errors='coerce') > 0
        df['_Kms ECM con Comb'] = df['Kms ECM'].where(valid_fuel)
        df['_Hrs ECM con Comb'] = df['Hrs ECM'].where(valid_fuel)
        columns.extend(['_Kms ECM con Comb', '_Hrs ECM con Comb'])
    # La hora que pondera gal/h debe pertenecer a un día donde el contador de
    # combustible en ralentí sí estuvo disponible.
    df['Ralentí ECM'] = df['Ralentí ECM'].where(df['Comb Ralentí'].notna())
    monthly = df.groupby('Anio-Mes', as_index=False)[columns].sum(min_count=1)

    # Razón de sumas, nunca promedio de promedios: un día de baja operación no
    # puede pesar lo mismo que una jornada completa.
    ratio_kms = monthly['_Kms ECM con Comb'] if is_gas else monthly['Kms ECM']
    ratio_hours = monthly['_Hrs ECM con Comb'] if is_gas else monthly['Hrs ECM']
    efficiency = (ratio_kms / monthly['Comb'].where(monthly['Comb'] > 0)).replace(
        [np.inf, -np.inf], np.nan
    )
    fuel_per_hour = (
        monthly['Comb'] / ratio_hours.where(ratio_hours > 0)
    ).replace([np.inf, -np.inf], np.nan)
    if not is_gas:
        efficiency = efficiency.fillna(0.0)
        fuel_per_hour = fuel_per_hour.fillna(0.0)
    idle_fuel_per_hour = (
        monthly['Comb Ralentí'] / monthly['Ralentí ECM']
    ).replace([np.inf, -np.inf], np.nan)
    monthly['km/gal'] = efficiency if not is_gas else np.nan
    monthly['gal/hr'] = fuel_per_hour if not is_gas else np.nan
    monthly['gal/hr Ralentí'] = idle_fuel_per_hour if not is_gas else np.nan
    monthly['km/m3'] = efficiency if is_gas else np.nan
    monthly['m3/hr'] = fuel_per_hour if is_gas else np.nan
    monthly['m3/hr Ralentí'] = idle_fuel_per_hour if is_gas else np.nan
    monthly['Fuel Kind'] = fuel_kind
    monthly['Fuel Unit'] = fuel_unit
    monthly['Velocidad Promedio'] = (
        monthly['Kms ECM'] / monthly['Hrs ECM']
    ).replace([np.inf, -np.inf], 0).fillna(0)
    monthly['Placa'] = placa

    return monthly[[
        'Placa',
        'Anio-Mes',
        'Kms ECM',
        'Kms Effective',
        'Hrs ECM',
        'Comb',
        'Comb Ralentí',
        'Ralentí ECM',
        'Fuel Kind',
        'Fuel Unit',
        'km/gal',
        'gal/hr',
        'gal/hr Ralentí',
        'km/m3',
        'm3/hr',
        'm3/hr Ralentí',
        'Velocidad Promedio',
    ]]


def partition_by_vehicle(df: pd.DataFrame) -> dict:
    """Índice `vehicle_id -> sub-DataFrame`, construido en UNA pasada.

    Antes cada función hacía `df[df['vehicle_id'] == vid]` dentro del loop de
    vehículos: con 250 vehículos y ~10,6M filas de status eran cinco barridos
    completos por vehículo. `groupby` conserva el orden original de las filas
    dentro de cada grupo y su índice, así que el sub-frame es idéntico al que
    devolvía la máscara booleana.
    """
    if df.empty or 'vehicle_id' not in df.columns:
        return {}
    return {vid: group for vid, group in df.groupby('vehicle_id', sort=False)}


def _slice_for(index: dict, df: pd.DataFrame, vehicle_id) -> pd.DataFrame:
    """Sub-frame del vehículo, o uno VACÍO con las mismas columnas y dtypes.

    El frame vacío tiene que conservar el schema: aguas abajo se consultan
    columnas (`diagnostic_id`, `vehicle_id`) incluso cuando no hay filas."""
    found = index.get(vehicle_id)
    if found is not None:
        return found
    return df.iloc[0:0]


def main():
    df_status = read_facts(FACT_STATUS_FILE)
    df_status_ecm = df_status[
        df_status['diagnostic_id'].isin(ECM_COUNTER_DIAGNOSTICS.values()) &
        df_status['event_id'].isna()
    ].copy()

    df_trips = read_facts(FACT_TRIPS_FILE) if os.path.exists(FACT_TRIPS_FILE) else pd.DataFrame()
    df_events = read_facts(FACT_EVENTS_FILE) if os.path.exists(FACT_EVENTS_FILE) else pd.DataFrame()
    df_rpm_bands = (
        read_facts(FACT_RPM_BANDS_FILE) if os.path.exists(FACT_RPM_BANDS_FILE) else pd.DataFrame()
    )
    df_events_comb = df_events[df_events['source'] == 'combustible'].copy() if not df_events.empty else pd.DataFrame()
    dim_veh = pd.read_parquet(DIM_VEH_FILE)
    for column_name in ('fuel_type_raw', 'fuel_kind', 'fuel_unit'):
        if column_name not in dim_veh.columns:
            dim_veh[column_name] = None
    dim_veh = dim_veh[
        [
            'vehicle_id',
            'database_name',
            'placa',
            'motor_type',
            'fuel_type_raw',
            'fuel_kind',
            'fuel_unit',
        ]
    ]

    # Una sola pasada por frame en vez de una por vehículo.
    status_by_veh = partition_by_vehicle(df_status_ecm)
    trips_by_veh = partition_by_vehicle(df_trips)
    events_by_veh = partition_by_vehicle(df_events_comb)
    rpm_bands_by_veh = partition_by_vehicle(df_rpm_bands)

    # El modo lo define la FLOTA (config: `range_mode`), pero el transform razona
    # por `vehicle_id`; se traduce una sola vez con la misma clave que usa la
    # extracción.
    rpm_mode_vehicle_ids = {
        make_vehicle_id(database_name, device_id)
        for (database_name, device_id) in VEHICLE_BY_DB_DEVICE
        if uses_rpm_ranges(database_name, device_id)
    }

    daily_results = []
    monthly_results = []

    for _, veh_row in dim_veh.iterrows():
        vehicle_id = veh_row['vehicle_id']
        database_name = veh_row['database_name']
        placa = veh_row['placa']
        motor = veh_row['motor_type']
        classified_kind, classified_unit, _source, _conflict = classify_fuel(
            veh_row.get('fuel_type_raw'),
            motor,
        )
        fuel_kind_value = veh_row.get('fuel_kind')
        fuel_unit_value = veh_row.get('fuel_unit')
        fuel_kind = (
            str(fuel_kind_value)
            if pd.notna(fuel_kind_value) and str(fuel_kind_value).strip()
            else classified_kind
        )
        fuel_unit = (
            str(fuel_unit_value)
            if pd.notna(fuel_unit_value) and str(fuel_unit_value).strip()
            else classified_unit
        )
        # Mismo scope que la extracción: las reglas viven en la base del
        # cliente, no en el motor. Cruzarlas deja bandas en cero y desvía
        # el reparto de porcentajes hacia las que sí matchearon.
        uses_rpm = vehicle_id in rpm_mode_vehicle_ids
        # En modo 'rpm' las reglas de banda de esa base no describen a esta
        # flota: mezclarlas repartiría tiempo dos veces.
        rules = {} if uses_rpm else rules_for(database_name, motor)
        if uses_rpm and not rpm_bands_for(motor):
            logging.warning(
                "%s (motor=%s) está en modo RPM sin rangos configurados; "
                "queda sin distribución por banda",
                placa,
                motor,
            )
        is_f28 = motor == 'F2.8'

        veh_status = _slice_for(status_by_veh, df_status_ecm, vehicle_id)
        veh_trips = _slice_for(trips_by_veh, df_trips, vehicle_id)
        veh_events = _slice_for(events_by_veh, df_events_comb, vehicle_id)
        veh_rpm_bands = _slice_for(rpm_bands_by_veh, df_rpm_bands, vehicle_id)

        try:
            ecm_df = process_ecm_counters(veh_status)
            if not ecm_df.empty and fuel_kind != FUEL_KIND_GAS:
                ecm_df = fix_fuel_data(ecm_df)

            gps_df = process_gps_trips(veh_trips)
            exc_df = (
                process_rpm_bands(veh_rpm_bands)
                if uses_rpm
                else process_exception_events(veh_events, rules)
            )
            quality_df = evaluate_distance_quality(
                veh_status,
                ecm_df,
                gps_df,
                is_f28=is_f28,
            ).drop(columns=['GPS Trip Count'], errors='ignore')

            dataframes = [
                frame for frame in [gps_df, ecm_df, exc_df, quality_df]
                if frame is not None and not frame.empty
            ]
            if dataframes:
                merged = dataframes[0]
                for frame in dataframes[1:]:
                    merged = pd.merge(merged, frame, on='Fecha', how='outer')
                revision_col = merged['Revision'].fillna(False).astype(bool) if 'Revision' in merged.columns else pd.Series(False, index=merged.index)
                if 'Distance Quality Status' not in merged.columns:
                    merged['Distance Quality Status'] = 'critical'
                missing_quality = merged['Distance Quality Status'].isna()
                merged.loc[missing_quality, 'Distance Quality Status'] = 'critical'
                if 'Distance Quality Reason' not in merged.columns:
                    merged['Distance Quality Reason'] = 'no_distance_data'
                merged.loc[missing_quality, 'Distance Quality Reason'] = 'no_distance_data'
                if 'Distance Source' not in merged.columns:
                    merged['Distance Source'] = 'none'
                merged.loc[missing_quality, 'Distance Source'] = 'none'
                if 'Kms Effective' not in merged.columns:
                    merged['Kms Effective'] = np.nan
                nullable_effective_kms = pd.to_numeric(
                    merged['Kms Effective'], errors='coerce'
                ).copy()
                if 'Distance Threshold Version' not in merged.columns:
                    merged['Distance Threshold Version'] = DISTANCE_THRESHOLD_VERSION
                merged['Distance Threshold Version'] = merged[
                    'Distance Threshold Version'
                ].fillna(DISTANCE_THRESHOLD_VERSION)
                if 'Distance Quality Fingerprint' not in merged.columns:
                    merged['Distance Quality Fingerprint'] = None
                merged.loc[missing_quality, 'Distance Quality Fingerprint'] = merged.loc[
                    missing_quality, 'Fecha'
                ].apply(
                    lambda value: hashlib.sha256(
                        f'{vehicle_id}|{value}|no-distance|{DISTANCE_THRESHOLD_VERSION}'.encode(
                            'utf-8'
                        )
                    ).hexdigest()
                )
                revision_col |= missing_quality
                merged = merged.fillna(0)
                # Null significa exclusión deliberada, no cero kilómetros.
                merged['Kms Effective'] = nullable_effective_kms
                merged['Revision'] = revision_col
                merged = merged.drop_duplicates(subset=['Fecha'], keep='last')

                metrics = calculate_metrics(merged.copy(), placa, fuel_kind=fuel_kind)
                if not metrics.empty:
                    metrics['Fuel Unit'] = fuel_unit
                    # El mensual se deriva de ESTE diario, antes de anexarle las
                    # columnas técnicas, para que ambos granos no puedan divergir.
                    monthly_df = aggregate_monthly_from_daily(metrics, placa)

                    metrics['vehicle_id'] = vehicle_id
                    metrics['database_name'] = database_name
                    metrics['motor_type'] = motor
                    metrics['date_key'] = pd.to_datetime(metrics['Fecha']).dt.date.apply(date_to_key)
                    metrics['fact_row_id'] = metrics['Fecha'].apply(
                        lambda value: make_row_id(vehicle_id, value, 'combustible_daily')
                    )
                    daily_results.append(metrics)

                    if not monthly_df.empty:
                        monthly_df['vehicle_id'] = vehicle_id
                        monthly_df['database_name'] = database_name
                        monthly_df['motor_type'] = motor
                        monthly_df['month_key'] = monthly_df['Anio-Mes'].apply(month_to_key)
                        monthly_df['month_start_date_key'] = monthly_df['Anio-Mes'].apply(
                            lambda value: date_to_key(pd.Period(value, freq='M').start_time.date())
                        )
                        monthly_df['fact_row_id'] = monthly_df['Anio-Mes'].apply(
                            lambda value: make_row_id(vehicle_id, value, 'combustible_monthly')
                        )
                        monthly_results.append(monthly_df)
        except Exception as exc:
            logging.error(f"Error procesando {placa}: {exc}")

    os.makedirs(SEMANTIC_FACTS_PATH, exist_ok=True)

    if daily_results:
        daily_df = pd.concat(daily_results, ignore_index=True)
        business_columns = [column for column in daily_df.columns if column not in {'fact_row_id', 'vehicle_id', 'database_name', 'motor_type', 'date_key'}]
        daily_df = daily_df[['fact_row_id', 'vehicle_id', 'database_name', 'motor_type', 'date_key'] + business_columns]
        daily_df = daily_df.drop_duplicates(subset=['fact_row_id']).sort_values(['vehicle_id', 'Fecha']).reset_index(drop=True)
        daily_df.to_parquet(OUTPUT_DAILY, index=False, compression='snappy')
        logging.info(f"Guardado: {OUTPUT_DAILY} ({len(daily_df):,} registros)")
    else:
        logging.info('No se generaron datos diarios de combustible.')

    if monthly_results:
        monthly_df = pd.concat(monthly_results, ignore_index=True)
        business_columns = [column for column in monthly_df.columns if column not in {'fact_row_id', 'vehicle_id', 'database_name', 'motor_type', 'month_key', 'month_start_date_key'}]
        monthly_df = monthly_df[['fact_row_id', 'vehicle_id', 'database_name', 'motor_type', 'month_key', 'month_start_date_key'] + business_columns]
        monthly_df = monthly_df.drop_duplicates(subset=['fact_row_id']).sort_values(['vehicle_id', 'Anio-Mes']).reset_index(drop=True)
        monthly_df.to_parquet(OUTPUT_MONTHLY, index=False, compression='snappy')
        logging.info(f"Guardado: {OUTPUT_MONTHLY} ({len(monthly_df):,} registros)")
    else:
        logging.info('No se generaron datos mensuales de combustible.')


if __name__ == '__main__':
    main()
