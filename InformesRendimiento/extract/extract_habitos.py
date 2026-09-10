# ==============================================================================
# extract_habitos.py — Extrae eventos de seguridad y sus detalles sensoriales
#
# Produce 3 fact tables:
#   fact_exception_events  (source='habitos')
#   fact_log_records       (event_id IS NOT NULL)
#   fact_status_readings   (event_id IS NOT NULL)
#
# Patrón en 2 pasos — INMUTABLE (plan §5.1, §5.2):
#   Paso 1: ExceptionEvent → obtiene ventanas de tiempo de cada evento
#   Paso 2: Para cada evento, LogRecord y StatusData usan activeFrom/activeTo
#           del evento (NO las fechas del chunk)
#
# Chunk: 3 días INMUTABLE (plan §5.8)
# ==============================================================================
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import time as time_module
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from datetime import datetime, timedelta, timezone, time as datetime_time
from typing import Dict, List

from utils import (
    get_date_range_for_run, save_next_start_date,
    authenticate,
    chunk_date_range,
    MAX_RETRIES, RETRY_WAIT_SECONDS,
    get_working_credentials, retry_call,
    write_staging, merge_staging_to_main, clear_staging,
    COLOMBIA_OFFSET, make_event_sk, make_log_row_id, make_row_id, make_vehicle_id,
)
import master_state
import mygeotab
from config import (
    CREDENTIALS, EVENT_RULES, EVENT_RULES_BY_MOTOR, RPM_RULES, HABITOS_RPM_DEVICE_CLASS,
    VEHICLE_CATALOG,
    DIAGNOSTIC_IDS, ACCEL_DIAGNOSTICS,
    DEMO_FLEET_DEVICES, NAVITRANS_DEVICES,
    CHUNK_SIZE_DAYS, FACTS_PATH,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SCRIPT_ID       = 'habito_seguro'
EVENT_BATCH_SIZE = 100   # INMUTABLE (plan §5.8) — tamaño de lote para detalles

FACT_EVENTS_FILE   = os.path.join(FACTS_PATH, 'fact_exception_events.parquet')
FACT_LOGS_FILE     = os.path.join(FACTS_PATH, 'fact_log_records.parquet')
FACT_STATUS_FILE   = os.path.join(FACTS_PATH, 'fact_status_readings.parquet')

DEDUP_EVENTS  = ['event_sk', 'source']  # mismo evento puede venir de 2 fuentes (combustible/habitos)
DEDUP_LOGS    = ['log_row_id']
DEDUP_STATUS  = ['row_id']

RPM_DIAG_ID    = DIAGNOSTIC_IDS['rpm_habitos']
LOAD_DIAG_ID   = DIAGNOSTIC_IDS['load_factor_habitos']
ACCEL_FWD_ID   = ACCEL_DIAGNOSTICS['forward_braking']
ACCEL_SIDE_ID  = ACCEL_DIAGNOSTICS['side_to_side']
ACCEL_UP_ID    = ACCEL_DIAGNOSTICS['up_down']


def _duration_to_seconds(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, datetime_time):
        return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6
    if isinstance(value, timedelta):
        return value.total_seconds()
    return None


def _safe_multicall(api: mygeotab.API, requests_list: list) -> list:
    """Reintentos ante OverLimitException — INMUTABLE (plan §5.8)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time_module.sleep(0.1)  # pequeña pausa cortesía
            return api.multi_call(requests_list)
        except Exception as e:
            if 'OverLimitException' in str(e) and attempt < MAX_RETRIES:
                logging.warning(
                    f"OverLimitException (intento {attempt}/{MAX_RETRIES}). "
                    f"Esperando {RETRY_WAIT_SECONDS}s…"
                )
                time_module.sleep(RETRY_WAIT_SECONDS)
            else:
                raise
    raise RuntimeError("Fallo tras máximos reintentos.")


_MOTOR_BY_DEVICE = {
    (vehicle['database_name'], vehicle['device_id']): vehicle.get('motor_type')
    for vehicle in VEHICLE_CATALOG
}


def _merge_unique_rules(target: Dict[str, str], candidates: Dict[str, str]) -> None:
    """Agrega semánticas sin repetir una consulta física por `rule_id`."""
    seen_rule_ids = set(target.values())
    for description, rule_id in candidates.items():
        if rule_id not in seen_rule_ids:
            target[description] = rule_id
            seen_rule_ids.add(rule_id)


def _get_rules_for_device(device_id: str, db_name: str) -> Dict[str, str]:
    """
    Devuelve {rule_name: rule_id} con todas las reglas aplicables
    al device: seguridad (EVENT_RULES) + RPM (RPM_RULES según clasificación).
    Preserva exactamente qué devices aplican a qué reglas RPM (HABITOS_RPM_DEVICE_CLASS).
    """
    rules: Dict[str, str] = {}
    # Reglas de seguridad globales (compatibilidad con snapshots antiguos).
    if db_name in EVENT_RULES:
        _merge_unique_rules(rules, EVENT_RULES[db_name])

    # Aplicaciones derivadas nuevas: solo aplican al motor del vehículo.
    motor_type = _MOTOR_BY_DEVICE.get((db_name, device_id))
    if motor_type:
        scoped_rules = EVENT_RULES_BY_MOTOR.get(db_name, {}).get(motor_type, {})
        _merge_unique_rules(rules, scoped_rules)

    # Fallback histórico por clase RPM. Si Navi ya envió la aplicación derivada,
    # `_merge_unique_rules` evita consultar dos veces el mismo rule_id.
    device_class = HABITOS_RPM_DEVICE_CLASS.get((db_name, device_id))
    if device_class and device_class in RPM_RULES:
        rpm_list = RPM_RULES[device_class]
        _merge_unique_rules(rules, {'Exceso RPM': rpm_list[0]})
        if len(rpm_list) > 1:
            _merge_unique_rules(rules, {'Exceso RPM sin Carga': rpm_list[1]})

    return rules


def _diags_for_event_type(event_type: str) -> List[str]:
    """
    Devuelve los diagnostic IDs que se deben solicitar para un tipo de evento.
    INMUTABLE — preserva el patrón exacto del script original (plan §5.1).
    """
    normalized = event_type.casefold()
    if 'rpm' in normalized:
        return [RPM_DIAG_ID, LOAD_DIAG_ID]
    elif normalized in (
        'frenada brusca',
        'frenadas bruscas',
        'aceleracion brusca',
        'aceleración brusca',
        'aceleraciones bruscas',
    ):
        return [ACCEL_FWD_ID]
    elif normalized in ('giro brusco', 'giros bruscos'):
        return [ACCEL_SIDE_ID]
    elif normalized in ('bache fuerte', 'baches o resaltos fuertes'):
        return [ACCEL_UP_ID]
    return []  # Exceso Velocidad: solo LogRecord


def process_device_chunk(
    api: mygeotab.API,
    device_id: str,
    from_str: str,
    to_str: str,
    db_name: str,
) -> tuple[List[dict], List[dict], List[dict]]:
    """
    Procesa un dispositivo dentro de un chunk de fechas.
    Devuelve (events_rows, log_rows, status_rows) sin persistir.
    """
    rules = _get_rules_for_device(device_id, db_name)
    if not rules:
        return [], [], []

    rule_id_to_name = {v: k for k, v in rules.items()}

    # --- Paso 1: obtener todos los ExceptionEvents (1 multi_call) ---
    event_requests = [
        ('Get', {
            'typeName': 'ExceptionEvent',
            'search': {
                'fromDate': from_str,
                'toDate':   to_str,
                'deviceSearch': {'id': device_id},
                'ruleSearch': {'id': rule_id},
            }
        })
        for rule_id in rules.values()
    ]
    event_results = _safe_multicall(api, event_requests)
    all_events = [ev for sublist in event_results for ev in sublist]
    if not all_events:
        return [], [], []

    logging.info(f"      {len(all_events)} eventos → lotes de {EVENT_BATCH_SIZE}")

    events_rows, log_rows, status_rows = [], [], []

    # --- Paso 2: detalles por lote de EVENT_BATCH_SIZE ---
    for batch_start in range(0, len(all_events), EVENT_BATCH_SIZE):
        batch = all_events[batch_start: batch_start + EVENT_BATCH_SIZE]
        detail_requests = []
        request_map = []  # (event_idx, diag_ids_list)

        for event in batch:
            event_type = rule_id_to_name.get(event['rule']['id'], '')
            diag_ids = _diags_for_event_type(event_type)

            # LogRecord — INMUTABLE: usa activeFrom/activeTo del evento (plan §5.2)
            detail_requests.append(('Get', {
                'typeName': 'LogRecord',
                'search': {
                    'fromDate': event['activeFrom'],
                    'toDate':   event['activeTo'],
                    'deviceSearch': {'id': device_id},
                }
            }))

            # StatusData — INMUTABLE: usa activeFrom/activeTo del evento (plan §5.1)
            for diag_id in diag_ids:
                detail_requests.append(('Get', {
                    'typeName': 'StatusData',
                    'search': {
                        'fromDate': event['activeFrom'],
                        'toDate':   event['activeTo'],
                        'deviceSearch': {'id': device_id},
                        'diagnosticSearch': {'id': diag_id},
                    }
                }))

            request_map.append(diag_ids)

        detail_results = _safe_multicall(api, detail_requests)

        # --- Ensamblar resultados ---
        pointer = 0
        for event, diag_ids in zip(batch, request_map):
            event_id = event['id']  # ID nativo de Geotab (UUID string)

            # fact_exception_events
            duration_secs = _duration_to_seconds(
                event.get('duration') or (event['activeTo'] - event['activeFrom'])
            )
            af  = event['activeFrom']
            vid = make_vehicle_id(db_name, device_id)
            event_sk = make_event_sk(db_name, device_id, event_id)
            events_rows.append({
                'event_id':       event_id,
                'event_sk':       event_sk,
                'database_name':  db_name,
                'vehicle_id':     vid,
                'device_id':      device_id,
                'rule_id':        event['rule']['id'],
                'activeFrom':     af,
                'activeTo':       event['activeTo'],
                'duration_seconds': duration_secs,
                'distance':       event.get('distance'),  # km, como devuelve Geotab
                'source':         'habitos',
                'fecha_colombia': (af - COLOMBIA_OFFSET).date() if af else None,
            })

            # fact_log_records (con event_id)
            log_records = detail_results[pointer]
            pointer += 1
            for r in (log_records or []):
                dt = r.get('dateTime')
                log_rows.append({
                    'event_id':       event_id,
                    'event_sk':       event_sk,
                    'geotab_id':      r.get('id'),
                    'log_row_id':     make_log_row_id(db_name, device_id, r.get('id'), event_id, dt),
                    'database_name':  db_name,
                    'vehicle_id':     vid,
                    'device_id':      device_id,
                    'dateTime':       dt,
                    'speed':          r.get('speed'),
                    'longitude':      r.get('longitude'),
                    'latitude':       r.get('latitude'),
                    'fecha_colombia': (dt - COLOMBIA_OFFSET).date() if dt else None,
                })

            # fact_status_readings (con event_id)
            for diag_id in diag_ids:
                sd_records = detail_results[pointer]
                pointer += 1
                for r in (sd_records or []):
                    dt = r.get('dateTime')
                    status_rows.append({
                        'event_id':       event_id,
                        'event_sk':       event_sk,
                        'database_name':  db_name,
                        'vehicle_id':     vid,
                        'device_id':      device_id,
                        'diagnostic_id':  diag_id,
                        'dateTime':       dt,
                        'data':           r.get('data'),
                        'fecha_colombia': (dt - COLOMBIA_OFFSET).date() if dt else None,
                        'row_id':         make_row_id(db_name, device_id, diag_id, dt, event_id),
                    })

    return events_rows, log_rows, status_rows


# ------------------------------------------------------------------------------
# Worker — procesa una partición de devices para una DB y credential
# ------------------------------------------------------------------------------
def _worker(db_name, credentials, device_specs, to_date, chunk_days, staging_paths):
    # device_specs = [(device_id, from_date, vehicle_id_or_None), ...]
    # Devuelve (ok_vehicle_ids, failed_vehicle_ids, had_error). had_error cubre el
    # modo global (vehicle_id None): indica si ALGÚN device falló, para no avanzar
    # el watermark global ante fallos parciales.
    try:
        api = authenticate(db_name, credentials)
    except Exception as e:
        logging.error(f"[{SCRIPT_ID}] Error autenticando {db_name}/{credentials['username']}: {e}")
        failed = [vid for (_, _, vid) in device_specs if vid is not None]
        return [], failed, True

    events_acc, logs_acc, status_acc = [], [], []
    ok_ids, failed_ids, had_error = [], [], False

    for idx, (device_id, from_date, vehicle_id) in enumerate(device_specs, 1):
        device_failed = False
        for from_str, to_str in chunk_date_range(from_date, to_date, chunk_days):
            logging.info(
                f"    [{db_name}/{credentials['username']}] "
                f"Vehículo {idx}/{len(device_specs)}: {device_id} {from_str} → {to_str}"
            )
            try:
                ev, lg, st = retry_call(
                    process_device_chunk, api, device_id, from_str, to_str, db_name,
                    _script_id=SCRIPT_ID,
                    _label=f"[{db_name}/{device_id}] {from_str}",
                    _reauth=lambda: authenticate(db_name, credentials, force=True),
                )
                events_acc.extend(ev)
                logs_acc.extend(lg)
                status_acc.extend(st)
            except Exception:
                device_failed = True  # ya logueado por retry_call
                had_error = True
                break  # no seguir chunkeando este device; no se avanza su watermark
        if vehicle_id is not None:
            (failed_ids if device_failed else ok_ids).append(vehicle_id)

    if events_acc:
        write_staging(pd.DataFrame(events_acc), staging_paths['events'])
    if logs_acc:
        write_staging(pd.DataFrame(logs_acc), staging_paths['logs'])
    if status_acc:
        write_staging(pd.DataFrame(status_acc), staging_paths['status'])

    return ok_ids, failed_ids, had_error


def _today_midnight_utc() -> datetime:
    """Medianoche de hoy (hora Colombia) en UTC — solo días completos."""
    co_tz = timezone(timedelta(hours=-5))
    today_local = datetime.now(co_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return today_local.astimezone(timezone.utc)


def _build_db_specs(to_date):
    """Devuelve (db_specs, per_vehicle, next_start_str).

    Modo por vehículo (si la DB del Portal está disponible): cada device trae su
    propia ventana `from_date` y su `vehicle_id`. Modo global (fallback): ventana
    única desde el watermark de estado_ejecucion.json para todos los devices.
    """
    windows = master_state.get_vehicle_windows(SCRIPT_ID, to_date)
    if windows is not None:
        by_db: dict = defaultdict(list)
        for w in windows:
            by_db[w['database_name']].append((w['device_id'], w['from_date'], w['vehicle_id']))
        return list(by_db.items()), True, None

    # Fallback global.
    result = get_date_range_for_run(SCRIPT_ID, default_start_date_str='2024-01-01')
    if result is None:
        return None, False, None
    (overall_start, _overall_end), next_start_str = result
    db_specs = [
        ('demo_fleet', [(d, overall_start, None) for d in DEMO_FLEET_DEVICES]),
        ('navitrans',  [(d, overall_start, None) for d in NAVITRANS_DEVICES]),
    ]
    return db_specs, False, next_start_str


# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------
def main():
    logging.info(f"[{SCRIPT_ID}] Iniciando extracción de hábitos de conducción")
    with master_state.run_lock(SCRIPT_ID) as got_lock:
        if not got_lock:
            logging.warning(f"[{SCRIPT_ID}] Otra corrida en curso; se omite.")
            return
        _run()


def _run():
    to_date = _today_midnight_utc()
    db_specs, per_vehicle, next_start_str = _build_db_specs(to_date)
    if db_specs is None:
        logging.info(f"[{SCRIPT_ID}] No hay nuevos días completos para procesar.")
        return

    chunk_days = CHUNK_SIZE_DAYS['habito_seguro']  # 3 días INMUTABLE
    staging_dir = os.path.join(FACTS_PATH, '_staging')
    clear_staging(FACTS_PATH, SCRIPT_ID)

    events_staging, logs_staging, status_staging = [], [], []
    all_tasks: list = []

    for db_name, specs in db_specs:
        if not specs:
            continue
        creds_list = CREDENTIALS.get(db_name)
        if not creds_list:
            logging.warning(f"[{SCRIPT_ID}] Sin credenciales configuradas para '{db_name}'")
            continue
        working_creds = get_working_credentials(db_name, creds_list)
        if not working_creds:
            continue
        n = len(working_creds)
        for i in range(n):
            part = specs[i::n]
            if not part:
                continue
            sp = {
                'events': os.path.join(staging_dir, f"{SCRIPT_ID}_{db_name}_{i}_events.parquet"),
                'logs':   os.path.join(staging_dir, f"{SCRIPT_ID}_{db_name}_{i}_logs.parquet"),
                'status': os.path.join(staging_dir, f"{SCRIPT_ID}_{db_name}_{i}_status.parquet"),
            }
            events_staging.append(sp['events'])
            logs_staging.append(sp['logs'])
            status_staging.append(sp['status'])
            all_tasks.append((db_name, working_creds[i], part, sp))

    ok_ids, failed_ids, any_error = [], [], False

    if all_tasks:
        with ThreadPoolExecutor(max_workers=len(all_tasks)) as executor:
            futures = [
                executor.submit(_worker, db_name, creds, part, to_date, chunk_days, sp)
                for db_name, creds, part, sp in all_tasks
            ]
            for f in futures:
                # Recolectar resultados sin abortar el merge si un worker reventó.
                try:
                    w_ok, w_failed, w_err = f.result()
                    ok_ids.extend(w_ok)
                    failed_ids.extend(w_failed)
                    any_error = any_error or w_err
                except Exception as e:
                    any_error = True
                    logging.error(f"[{SCRIPT_ID}] Worker falló de forma inesperada: {e}")

    # El merge debe cerrar bien antes de avanzar cualquier watermark.
    try:
        merge_staging_to_main(events_staging, FACT_EVENTS_FILE, DEDUP_EVENTS)
        merge_staging_to_main(logs_staging,   FACT_LOGS_FILE,   DEDUP_LOGS)
        merge_staging_to_main(status_staging, FACT_STATUS_FILE, DEDUP_STATUS)
    except Exception as e:
        logging.error(f"[{SCRIPT_ID}] Merge falló; NO se avanza watermark: {e}")
        raise

    if per_vehicle:
        # Avanza solo los vehículos que cerraron OK; los fallidos se reintentan
        # en la próxima corrida (su watermark no avanza).
        for vid in ok_ids:
            master_state.advance_watermark(vid, SCRIPT_ID, to_date)
        for vid in failed_ids:
            master_state.mark_error(vid, SCRIPT_ID, "extracción incompleta")
        logging.info(
            f"[{SCRIPT_ID}] Completado. {len(ok_ids)} OK, {len(failed_ids)} con error."
        )
    else:
        # Global: avanzar solo si NO hubo fallas (evita huecos de datos).
        if any_error:
            logging.warning(
                f"[{SCRIPT_ID}] Hubo fallas; watermark global NO avanza (se reintenta)."
            )
        elif next_start_str:
            save_next_start_date(SCRIPT_ID, next_start_str)
        logging.info(f"[{SCRIPT_ID}] Completado.")


if __name__ == '__main__':
    main()
