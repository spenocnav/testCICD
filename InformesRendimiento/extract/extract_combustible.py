# ==============================================================================
# extract_combustible.py — Extrae datos de combustible a las fact tables
#
# Produce 3 fact tables:
#   fact_status_readings   (contadores ECM — event_id NULL)
#   fact_trips             (viajes GPS)
#   fact_exception_events  (rangos RPM/ralentí — source='combustible')
#
# Estrategia híbrida para contadores ECM y trips (plan §6):
#   - Meses anteriores al mes actual: 1 chunk por mes calendario
#   - Mes actual:                     1 ventana por día Colombia (más precisión)
#
# ExceptionEvent (rangos RPM): chunks de 3 días estándar (plan §6 última nota).
# Multi-call por device: 7 diagnostic IDs + Trip en 1 sola llamada por ventana.
# ==============================================================================
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # para importar _runner

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, time as datetime_time
from typing import List, Dict, Tuple, Iterator
import pandas as pd

import master_state
from _runner import catalog_windows, today_midnight_utc, finalize_watermarks
from utils import (
    get_date_range_for_run, save_next_start_date,
    authenticate, safe_multi_call,
    chunk_date_range,
    fmt_date, COLOMBIA_TZ,
    get_working_credentials, retry_call,
    write_staging, merge_staging_to_main, clear_staging,
    COLOMBIA_OFFSET, make_event_sk, make_row_id, make_trip_row_id, make_vehicle_id,
)
from config import (
    CREDENTIALS, DEVICE_GROUPS, rules_for, uses_rpm_ranges,
    ECM_COUNTER_DIAGNOSTICS, CHUNK_SIZE_DAYS, FACTS_PATH,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SCRIPT_ID = 'analisis_combustible'

FACT_STATUS_FILE = os.path.join(FACTS_PATH, 'fact_status_readings.parquet')
FACT_TRIPS_FILE  = os.path.join(FACTS_PATH, 'fact_trips.parquet')
FACT_EVENTS_FILE = os.path.join(FACTS_PATH, 'fact_exception_events.parquet')

DEDUP_STATUS = ['row_id']
# trip_row_id = hash(base, device, start). El `id` de Geotab NO entra en la
# clave: el servidor reemplaza el Trip por otro id al recalcularlo (ver
# make_trip_row_id en utils.py).
DEDUP_TRIPS  = ['trip_row_id']
DEDUP_EVENTS = ['event_sk', 'source']  # mismo evento puede venir de 2 fuentes (combustible/habitos)

# Los IDs de diagnóstico ECM configurados para combustible y operación.
ECM_DIAG_IDS = list(ECM_COUNTER_DIAGNOSTICS.values())

MAX_DURATION_HOURS = 24.0  # filtro de anomalías de duración (plan §5.6 implícito)
EXCEPTION_EVENT_LOOKBACK_DAYS = 1


# ------------------------------------------------------------------------------
# Helpers de tiempo
# ------------------------------------------------------------------------------
def _duration_to_seconds(value) -> float:
    """Convierte duration de Geotab (time, timedelta, str) a segundos."""
    if value is None:
        return 0.0
    if isinstance(value, datetime_time):
        return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, str):
        try:
            days = 0
            if '.' in value:
                parts = value.split('.', 1)
                days = int(parts[0])
                value = parts[1]
            h, m, s = map(int, value.split(':'))
            return days * 86400 + h * 3600 + m * 60 + s
        except (ValueError, IndexError):
            return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------------------
# Estrategia híbrida de ventanas temporales para ECM counters y Trips
# ------------------------------------------------------------------------------
def hybrid_date_windows(start: datetime, end: datetime) -> Iterator[Tuple[str, str]]:
    """
    Genera (from_str, to_str) con la estrategia híbrida (plan §6):
    - Meses anteriores al mes actual: 1 ventana por mes calendario
    - Mes actual: 1 ventana por día Colombia (05:00 UTC → 04:59 UTC siguiente día)

    Ambas ventanas están expresadas en UTC tal como requiere la API.
    Los límites de mes se calculan en Colombia (UTC-5) para evitar desfases
    entre UTC midnight y Colombia midnight.
    """
    # Inicio del mes actual: medianoche Colombia del día 1 → UTC
    today_col = datetime.now(COLOMBIA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_current_month_col = today_col.replace(day=1)
    start_of_current_month_utc = start_of_current_month_col.astimezone(timezone.utc)

    # --- Meses anteriores (mes a mes) ---
    # next_month_first se calcula en Colombia (no UTC) para que la ventana
    # de cada mes respete exactamente los límites del día en Colombia.
    cursor = start
    while cursor < start_of_current_month_utc and cursor < end:
        cursor_col = cursor.astimezone(COLOMBIA_TZ)
        if cursor_col.month == 12:
            next_month_col = cursor_col.replace(year=cursor_col.year + 1, month=1, day=1,
                                                hour=0, minute=0, second=0, microsecond=0)
        else:
            next_month_col = cursor_col.replace(month=cursor_col.month + 1, day=1,
                                                hour=0, minute=0, second=0, microsecond=0)
        next_month_utc = next_month_col.astimezone(timezone.utc)

        month_end = min(next_month_utc, start_of_current_month_utc, end)
        yield fmt_date(cursor), fmt_date(month_end - timedelta(milliseconds=1))
        cursor = month_end   # avanza al límite real, no a next_month_first

    # --- Mes actual: día a día Colombia ---
    # Usar start_of_current_month_utc como referencia fija, no cursor
    # (cursor puede haber avanzado a un mes futuro en el bucle anterior).
    if start_of_current_month_utc < end:
        day_start_utc = max(start, start_of_current_month_utc)
        day_cursor_col = day_start_utc.astimezone(COLOMBIA_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end_col = end.astimezone(COLOMBIA_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        while day_cursor_col < day_end_col:
            day_from_utc = day_cursor_col.astimezone(timezone.utc)
            next_day_col = day_cursor_col + timedelta(days=1)
            day_to_utc = next_day_col.astimezone(timezone.utc) - timedelta(milliseconds=1)
            yield fmt_date(day_from_utc), fmt_date(day_to_utc)
            day_cursor_col = next_day_col


# ------------------------------------------------------------------------------
# Extracción de contadores ECM + Trips (estrategia híbrida)
# ------------------------------------------------------------------------------
def extract_ecm_and_trips(
    api, device_id: str, from_str: str, to_str: str, db_name: str
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    1 multi-call: 7 requests de StatusData (ECM) + 1 request de Trip.
    Devuelve (df_status, df_trips).
    """
    requests_list = []
    # Diagnósticos ECM de combustible y operación.
    for diag_id in ECM_DIAG_IDS:
        requests_list.append(('Get', {
            'typeName': 'StatusData',
            'search': {
                'deviceSearch':    {'id': device_id},
                'diagnosticSearch': {'id': diag_id},
                'fromDate': from_str,
                'toDate':   to_str,
            }
        }))
    # Trip
    requests_list.append(('Get', {
        'typeName': 'Trip',
        'search': {
            'deviceSearch': {'id': device_id},
            'fromDate': from_str,
            'toDate':   to_str,
        }
    }))

    results = safe_multi_call(api, requests_list)
    vid = make_vehicle_id(db_name, device_id)

    # Status readings
    status_rows = []
    for i, diag_id in enumerate(ECM_DIAG_IDS):
        for r in (results[i] or []):
            dt = r.get('dateTime')
            status_rows.append({
                'database_name':  db_name,
                'vehicle_id':     vid,
                'device_id':      device_id,
                'diagnostic_id':  diag_id,
                'dateTime':       dt,
                'data':           r.get('data'),
                'event_id':       None,
                'fecha_colombia': (dt - COLOMBIA_OFFSET).date() if dt else None,
                'row_id':         make_row_id(db_name, device_id, diag_id, dt, None),
            })

    # Trips
    trip_rows = []
    for r in (results[len(ECM_DIAG_IDS)] or []):
        st = r.get('start')
        trip_rows.append({
            'database_name':     db_name,
            'vehicle_id':         vid,
            'device_id':          device_id,
            'geotab_id':          r.get('id'),
            'trip_row_id':        make_trip_row_id(db_name, device_id, st),
            'start':              st,
            'stop':               r.get('stop'),
            'driving_duration_s': _duration_to_seconds(r.get('drivingDuration')),
            'idling_duration_s':  _duration_to_seconds(r.get('idlingDuration')),
            'distance':           r.get('distance'),
            'fecha_colombia':     (st - COLOMBIA_OFFSET).date() if st else None,
        })

    return pd.DataFrame(status_rows), pd.DataFrame(trip_rows)


# ------------------------------------------------------------------------------
# Extracción de ExceptionEvents (rangos RPM/ralentí) — chunks de 3 días
# ------------------------------------------------------------------------------
def _expand_exception_event_window(
    from_str: str,
    to_str: str,
    carryover_days: int = EXCEPTION_EVENT_LOOKBACK_DAYS,
) -> tuple[str, str]:
    """
    Extiende la consulta hacia atrás para no perder eventos que empezaron antes
    del chunk pero continúan dentro del rango consultado.
    """
    query_from = datetime.fromisoformat(from_str.replace('Z', '+00:00')) - timedelta(days=carryover_days)
    query_to = datetime.fromisoformat(to_str.replace('Z', '+00:00'))
    return fmt_date(query_from), fmt_date(query_to)


def extract_exception_events(
    api, device_id: str, rules: Dict[str, str], from_str: str, to_str: str, db_name: str
) -> pd.DataFrame:
    """
    1 multi-call con 1 request de ExceptionEvent por regla.
    Aplica el filtro de duración máxima (anomalías).
    """
    if not rules:
        return pd.DataFrame()

    requests_list = [
        ('Get', {
            'typeName': 'ExceptionEvent',
            'search': {
                'deviceSearch': {'id': device_id},
                'ruleSearch':   {'id': rule_id},
                'fromDate': from_str,
                'toDate':   to_str,
            }
        })
        for rule_id in rules.values()
    ]
    results = safe_multi_call(api, requests_list)

    rows = []
    for rule_id, result in zip(rules.values(), results):
        for event in (result or []):
            dur_s = _duration_to_seconds(
                event.get('duration') or (event['activeTo'] - event['activeFrom'])
            )
            # Filtro de anomalía: descartar eventos que excedan MAX_DURATION_HOURS
            if dur_s / 3600 > MAX_DURATION_HOURS:
                logging.warning(
                    f"[{SCRIPT_ID}] Evento anomalía descartado: device={device_id} "
                    f"rule={rule_id} duration={dur_s/3600:.2f}h"
                )
                continue

            if dur_s <= 0:
                continue

            af = event['activeFrom']
            rows.append({
                'event_id':        event['id'],
                'event_sk':        make_event_sk(db_name, device_id, event['id']),
                'database_name':   db_name,
                'vehicle_id':      make_vehicle_id(db_name, device_id),
                'device_id':       device_id,
                'rule_id':         rule_id,
                'activeFrom':      af,
                'activeTo':        event['activeTo'],
                'duration_seconds': dur_s,
                'distance':        event.get('distance'),
                'source':          'combustible',
                'fecha_colombia':  (af - COLOMBIA_OFFSET).date() if af else None,
            })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------
# Construcción de lista (device_id, rules) por DB
# ------------------------------------------------------------------------------
def _build_device_rules_by_db() -> Dict[str, List[Tuple[str, dict]]]:
    """
    Retorna {db_name: [(device_id, rules_dict), ...]} para todos los grupos.
    """
    db_items: Dict[str, List[Tuple[str, dict]]] = {}
    for group_config in DEVICE_GROUPS.values():
        db = group_config['db']
        # Las reglas se piden a la API de ESTA base: sus rule_id solo existen
        # aquí. Con el catálogo por motor se pedían ids de otra base y Geotab
        # devolvía vacío, dejando bandas en cero.
        rules = rules_for(db, group_config['motor'])
        if not rules:
            logging.warning(
                f"[{SCRIPT_ID}] sin reglas de banda para db={db} "
                f"motor={group_config['motor']}: sus rangos quedarán vacíos"
            )
        if db not in db_items:
            db_items[db] = []
        for dev in group_config['devices']:
            # Flota en modo 'rpm': sus bandas las produce extract_rangos_rpm.
            # Pedir igual los ExceptionEvent de las reglas gastaría llamadas y
            # dejaría eventos que el transform no va a usar para ese vehículo.
            device_rules = {} if uses_rpm_ranges(db, dev) else rules
            db_items[db].append((dev, device_rules))
    return db_items


# ------------------------------------------------------------------------------
# Worker — procesa una partición de (device_id, rules)
# ------------------------------------------------------------------------------
def _worker(db_name, credentials, device_rules, to_date, chunk_days_exc, staging_paths):
    # staging_paths = {'status': '...', 'trips': '...', 'events': '...'}
    # device_rules = [(device_id, rules, from_date, vehicle_id_or_None), ...]
    real_vids = [vid for (_, _, _, vid) in device_rules if vid is not None]
    try:
        api = authenticate(db_name, credentials)
    except Exception as e:
        code = master_state.classify_extraction_error(e)
        logging.error(
            "[%s] Error autenticando %s/%s (%s)",
            SCRIPT_ID, db_name, credentials["username"], code,
        )
        return [], real_vids, {vid: code for vid in real_vids}, True

    status_acc, trips_acc, events_acc = [], [], []
    ok_ids, failed_ids, failed_errors, had_error = [], [], {}, False

    for device_id, rules, from_date, vehicle_id in device_rules:
        logging.info(f"  [{SCRIPT_ID}] [{db_name}/{credentials['username']}] Device: {device_id}")
        dev_failed = False

        # --- ECM counters + Trips: estrategia híbrida ---
        for from_str, to_str in hybrid_date_windows(from_date, to_date):
            try:
                df_s, df_t = retry_call(
                    extract_ecm_and_trips, api, device_id, from_str, to_str, db_name,
                    _script_id=SCRIPT_ID,
                    _label=f"ECM/Trips [{db_name}/{device_id}] {from_str}",
                    _reauth=lambda: authenticate(db_name, credentials, force=True),
                )
                if not df_s.empty:
                    status_acc.append(df_s)
                if not df_t.empty:
                    trips_acc.append(df_t)
            except Exception as exc:
                if vehicle_id is not None:
                    failed_errors[vehicle_id] = master_state.classify_extraction_error(exc)
                dev_failed = True  # ya logueado por retry_call
                break  # no avanzar watermark de este vehículo

        # --- ExceptionEvents: chunks de 3 días (estándar, sin lógica híbrida) ---
        if not dev_failed and rules:
            for from_str, to_str in chunk_date_range(
                from_date, to_date, chunk_days_exc
            ):
                query_from_str, query_to_str = _expand_exception_event_window(from_str, to_str)
                try:
                    df_ev = retry_call(
                        extract_exception_events, api, device_id, rules, query_from_str, query_to_str, db_name,
                        _script_id=SCRIPT_ID,
                        _label=f"ExcEvent [{db_name}/{device_id}] {from_str}",
                        _reauth=lambda: authenticate(db_name, credentials, force=True),
                    )
                    if not df_ev.empty:
                        events_acc.append(df_ev)
                except Exception as exc:
                    if vehicle_id is not None:
                        failed_errors[vehicle_id] = master_state.classify_extraction_error(exc)
                    dev_failed = True  # ya logueado por retry_call
                    break

        if vehicle_id is not None:
            (failed_ids if dev_failed else ok_ids).append(vehicle_id)
        had_error = had_error or dev_failed

    if status_acc:
        write_staging(pd.concat(status_acc, ignore_index=True), staging_paths['status'])
    if trips_acc:
        write_staging(pd.concat(trips_acc, ignore_index=True), staging_paths['trips'])
    if events_acc:
        write_staging(pd.concat(events_acc, ignore_index=True), staging_paths['events'])

    return ok_ids, failed_ids, failed_errors, had_error


# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
def main():
    with master_state.run_lock(SCRIPT_ID) as got_lock:
        if not got_lock:
            logging.warning(f"[{SCRIPT_ID}] Otra corrida en curso; se omite.")
            return
        _run()


def _run():
    to_date = today_midnight_utc()
    # Estado por vehículo (Postgres); fallback al watermark global JSON.
    windows = catalog_windows(SCRIPT_ID, to_date)
    per_vehicle = windows is not None
    overall_start = None
    next_start_str = None
    if not per_vehicle:
        result = get_date_range_for_run(SCRIPT_ID, default_start_date_str='2024-09-01')
        if result is None:
            logging.info(f"[{SCRIPT_ID}] No hay nuevos días completos para procesar.")
            return
        (overall_start, _end), next_start_str = result

    chunk_days_exc = CHUNK_SIZE_DAYS['analisis_combustible']  # 3 días para ExceptionEvent
    staging_dir = os.path.join(FACTS_PATH, '_staging')
    clear_staging(FACTS_PATH, SCRIPT_ID)

    db_device_rules = _build_device_rules_by_db()
    status_staging, trips_staging, events_staging = [], [], []
    all_tasks: list = []
    ok_ids, failed_ids, failed_errors, any_error = [], [], {}, False

    for db_name, device_rules in db_device_rules.items():
        # Añade (from_date, vehicle_id) por device. En modo por-vehículo, omite los
        # devices sin días nuevos (no están en windows).
        items = []
        for dev, rules in device_rules:
            if per_vehicle:
                w = windows.get((db_name, dev))
                if w is None:
                    continue
                frm, vid = w
            else:
                frm, vid = overall_start, None
            items.append((dev, rules, frm, vid))
        if not items:
            continue
        creds_list = CREDENTIALS.get(db_name)
        if not creds_list:
            logging.warning(f"[{SCRIPT_ID}] Sin credenciales configuradas para '{db_name}'")
            missing_ids = [vid for _, _, _, vid in items if vid is not None]
            failed_ids.extend(missing_ids)
            failed_errors.update({vid: "credentials_unavailable" for vid in missing_ids})
            any_error = True
            continue
        logging.info(f"[{SCRIPT_ID}] DB: {db_name} ({len(items)} devices)")
        working_creds = get_working_credentials(db_name, creds_list)
        if not working_creds:
            missing_ids = [vid for _, _, _, vid in items if vid is not None]
            failed_ids.extend(missing_ids)
            failed_errors.update({vid: "credentials_unavailable" for vid in missing_ids})
            any_error = True
            continue
        n = len(working_creds)
        for i in range(n):
            part = items[i::n]
            if not part:
                continue
            sp = {
                'status': os.path.join(staging_dir, f"{SCRIPT_ID}_{db_name}_{i}_status.parquet"),
                'trips':  os.path.join(staging_dir, f"{SCRIPT_ID}_{db_name}_{i}_trips.parquet"),
                'events': os.path.join(staging_dir, f"{SCRIPT_ID}_{db_name}_{i}_events.parquet"),
            }
            status_staging.append(sp['status'])
            trips_staging.append(sp['trips'])
            events_staging.append(sp['events'])
            all_tasks.append((db_name, working_creds[i], part, sp))

    if all_tasks:
        with ThreadPoolExecutor(max_workers=len(all_tasks)) as executor:
            futures = {
                executor.submit(_worker, db_name, creds, part, to_date, chunk_days_exc, sp): part
                for db_name, creds, part, sp in all_tasks
            }
            for f, task_items in futures.items():
                try:
                    w_ok, w_failed, errors, w_err = f.result()
                    ok_ids.extend(w_ok)
                    failed_ids.extend(w_failed)
                    failed_errors.update(errors)
                    any_error = any_error or w_err
                except Exception as e:
                    any_error = True
                    fallback_ids = [vid for _, _, _, vid in task_items if vid is not None]
                    failed_ids.extend(fallback_ids)
                    failed_errors.update({vid: "worker_error" for vid in fallback_ids})
                    logging.error(
                        "[%s] Worker falló de forma inesperada (%s)",
                        SCRIPT_ID, master_state.classify_extraction_error(e),
                    )

    try:
        merge_staging_to_main(status_staging, FACT_STATUS_FILE, DEDUP_STATUS)
        # prefer_new: un viaje en curso se reextrae luego con su `stop`/`distance`
        # definitivos bajo la MISMA clave; la version nueva debe ganar.
        merge_staging_to_main(trips_staging,  FACT_TRIPS_FILE,  DEDUP_TRIPS, prefer_new=True)
        merge_staging_to_main(events_staging, FACT_EVENTS_FILE, DEDUP_EVENTS)
    except Exception as e:
        logging.error(f"[{SCRIPT_ID}] Merge falló; NO se avanza watermark: {e}")
        raise

    finalize_watermarks(
        SCRIPT_ID, per_vehicle, ok_ids, failed_ids, any_error, next_start_str, to_date,
        failed_errors,
    )


if __name__ == '__main__':
    main()
