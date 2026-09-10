# ==============================================================================
# _runner.py — Runner común de extracción con estado por vehículo (Fase 3)
#
# Centraliza la lógica de "desde cuándo extraer cada vehículo" para que TODOS los
# extractores usen el estado por vehículo de Postgres (vehicle_extraction_state)
# en vez del watermark global de estado_ejecucion.json.
#
# Fuente de la ventana: master_state.get_vehicle_windows (Postgres). Se INTERSECTA
# con el catálogo activo (config.VEHICLE_BY_DB_DEVICE) para conservar EXACTAMENTE
# el mismo scope de vehículos que hoy (no extraer filas de prueba que el catálogo
# excluye). Si no hay MASTER_DB_URL, cae al watermark global por dataset
# (get_date_range_for_run) sin romperse.
#
# Los vehículos se agrupan por (base, from_date) para preservar el batching en un
# solo multi_call por chunk: en el caso común todos comparten watermark y caen en
# el mismo grupo; un vehículo recién backfilleado cae en su propio grupo con su
# rango. Tras un merge OK se avanza el watermark SOLO de los vehículos que
# cerraron bien (los fallidos se reintentan en la próxima corrida).
# ==============================================================================
import os
import sys
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import master_state
from config import CREDENTIALS, DEVICES_BY_DB, FACTS_PATH, VEHICLE_BY_DB_DEVICE
from utils import (
    authenticate, clear_staging, fmt_date, get_date_range_for_run,
    get_working_credentials, merge_staging_to_main, retry_call, save_next_start_date,
    write_staging, COLOMBIA_TZ,
)

# Serializa el merge al parquet principal entre los hilos de una misma corrida:
# `merge_staging_to_main` reescribe particiones mensuales completas y dos hilos
# sobre el mismo mes se pisarían.
_CHECKPOINT_LOCK = threading.Lock()


def chunk_windows(start: datetime, end: datetime, chunk_days: int):
    """`(from_str, to_str, chunk_end)` por trozo de `[start, end)`.

    Misma partición que `utils.chunk_date_range` (to_str = fin - 1 ms), más el
    instante de cierre del trozo como datetime: es el valor al que avanza el
    watermark cuando el trozo se persiste."""
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        yield fmt_date(current), fmt_date(chunk_end - timedelta(milliseconds=1)), chunk_end
        current = chunk_end


def today_midnight_utc() -> datetime:
    """Medianoche de hoy (hora Colombia) en UTC. Igual que el `to_date` de
    get_date_range_for_run: solo se procesan días COMPLETOS ya terminados."""
    today_local = datetime.now(COLOMBIA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    return today_local.astimezone(timezone.utc)


def catalog_windows(script_id: str, to_date: datetime):
    """dict[(db, device)] -> (from_date, vehicle_id) para los vehículos del
    catálogo ACTIVO con días nuevos. None si no hay DB (el caller hace fallback).

    Restringe al catálogo activo (VEHICLE_BY_DB_DEVICE) para que el scope sea
    idéntico al de hoy: get_vehicle_windows puede devolver filas de prueba que el
    catálogo excluye, y no queremos extraerlas."""
    windows = master_state.get_vehicle_windows(script_id, to_date)
    if windows is None:
        return None
    out: dict = {}
    for w in windows:
        key = (w["database_name"], w["device_id"])
        if key in VEHICLE_BY_DB_DEVICE:
            out[key] = (w["from_date"], w["vehicle_id"])
    return out


def resolve_groups(script_id: str, default_start: str, to_date: datetime):
    """Devuelve (groups, per_vehicle, next_start_str).

    groups = [(db_name, from_date, [(device_id, vehicle_id), ...]), ...]
    - per_vehicle=True: ventanas por vehículo (Postgres), agrupadas por (db, from_date).
    - per_vehicle=False: fallback global (DEVICES_BY_DB + watermark JSON); vehicle_id=None.
    - groups=None: no hay nada nuevo que procesar.
    """
    win = catalog_windows(script_id, to_date)
    if win is not None:
        grouped: dict = defaultdict(list)
        for (db_name, device_id), (from_date, vehicle_id) in win.items():
            grouped[(db_name, from_date)].append((device_id, vehicle_id))
        groups = [(db_name, frm, devs) for (db_name, frm), devs in grouped.items()]
        return groups, True, None

    # Fallback global por dataset (sin MASTER_DB_URL).
    result = get_date_range_for_run(script_id, default_start_date_str=default_start)
    if result is None:
        return None, False, None
    (start, _end), next_start_str = result
    groups = [
        (db_name, start, [(dev, None) for dev in devices])
        for db_name, devices in DEVICES_BY_DB.items()
        if devices
    ]
    return groups, False, next_start_str


def _status_worker(script_id, extract_chunk, db_name, credentials, devices,
                   vehicle_ids, from_date, to_date, chunk_days, staging_path,
                   checkpoint=None, checkpoint_every: int = 0):
    """Extrae una partición y devuelve ``ok, fallidos, errores, hubo_error``.

    La llamada agrupada es el camino rápido. Si falla, se reintenta cada
    dispositivo por separado para que el fallo de una respuesta no contamine a
    toda la partición. Los vehículos que fallan una ventana se retiran de las
    siguientes y conservan su watermark para reintentar desde el mismo punto.

    `checkpoint(paths, vehicle_ids, through)`: si se recibe, cada
    `checkpoint_every` trozos lo acumulado se escribe a un staging propio y se
    entrega para que el runner lo consolide y avance el watermark de los
    vehículos vivos hasta `through`. Sin esto, un backfill largo que el worker
    cancela por tiempo (3 h) no persiste NADA y al día siguiente vuelve a
    empezar desde el mismo punto: así se quedó Bavaria desde el 18-08-2026,
    117 vehículos `pending` tras 14 corridas de 3 h.
    """
    device_to_vehicle = dict(zip(devices, vehicle_ids))
    real_vids = [v for v in vehicle_ids if v is not None]
    try:
        api = authenticate(db_name, credentials)
    except Exception as exc:
        code = master_state.classify_extraction_error(exc)
        logging.error(
            "[%s] Error autenticando %s/%s (%s)",
            script_id, db_name, credentials["username"], code,
        )
        return [], real_vids, {vid: code for vid in real_vids}, True

    active_devices = list(devices)
    failed_by_device: dict[str, str] = {}
    accumulated = []
    chunks_done = 0
    for from_str, to_str, chunk_end in chunk_windows(from_date, to_date, chunk_days):
        if not active_devices:
            break
        logging.info(f"[{script_id}] [{db_name}/{credentials['username']}] {from_str} → {to_str}")
        try:
            df = retry_call(
                extract_chunk, api, active_devices, from_str, to_str, db_name,
                _script_id=script_id, _label=f"[{db_name}] {from_str}",
                _reauth=lambda: authenticate(db_name, credentials, force=True),
            )
            if not df.empty:
                accumulated.append(df)
            chunks_done += 1
            if not _checkpoint_if_due(
                script_id, checkpoint, checkpoint_every, chunks_done, accumulated,
                staging_path, active_devices, device_to_vehicle, chunk_end,
            ):
                failed_by_device.update({d: "worker_error" for d in active_devices})
                active_devices = []
            continue
        except Exception as exc:
            batch_code = master_state.classify_extraction_error(exc)
            logging.warning(
                "[%s] Falló lote %s (%s); reintento por dispositivo",
                script_id, from_str, batch_code,
            )

        # Un multi_call puede fallar por un solo dispositivo. La recuperación
        # individual conserva los datos de los demás y deja trazabilidad solo
        # para los que realmente no pudieron responder.
        surviving_devices = []
        for device_id in active_devices:
            try:
                df = retry_call(
                    extract_chunk, api, [device_id], from_str, to_str, db_name,
                    _script_id=script_id,
                    _label=f"[{db_name}/{device_id}] {from_str}",
                    _reauth=lambda: authenticate(db_name, credentials, force=True),
                )
                if not df.empty:
                    accumulated.append(df)
                surviving_devices.append(device_id)
            except Exception as exc:
                code = master_state.classify_extraction_error(exc)
                failed_by_device[device_id] = code
                logging.error(
                    "[%s] Falló device %s en %s (%s)",
                    script_id, device_id, from_str, code,
                )
        active_devices = surviving_devices
        chunks_done += 1
        if not _checkpoint_if_due(
            script_id, checkpoint, checkpoint_every, chunks_done, accumulated,
            staging_path, active_devices, device_to_vehicle, chunk_end,
        ):
            failed_by_device.update({d: "worker_error" for d in active_devices})
            active_devices = []

    if accumulated:
        write_staging(pd.concat(accumulated, ignore_index=True), staging_path)

    ok_ids = [
        device_to_vehicle[device]
        for device in active_devices
        if device_to_vehicle[device] is not None
    ]
    failed_ids = [
        device_to_vehicle[device]
        for device in failed_by_device
        if device_to_vehicle[device] is not None
    ]
    failed_errors = {
        device_to_vehicle[device]: code
        for device, code in failed_by_device.items()
        if device_to_vehicle[device] is not None
    }
    return ok_ids, failed_ids, failed_errors, bool(failed_ids)


def _checkpoint_if_due(script_id, checkpoint, every, chunks_done, accumulated,
                       staging_path, active_devices, device_to_vehicle, through) -> bool:
    """Persiste lo acumulado si toca. Devuelve False si el checkpoint falló:
    el caller debe dar por perdida la partición (los trozos ya persistidos
    conservan su watermark; los siguientes se reintentan mañana)."""
    if checkpoint is None or every <= 0 or chunks_done % every != 0:
        return True
    ck_path = f"{staging_path}.ck{chunks_done}.parquet"
    frames = [f for f in accumulated if not f.empty]
    accumulated.clear()
    if frames:
        write_staging(pd.concat(frames, ignore_index=True), ck_path)
    vids = [device_to_vehicle[d] for d in active_devices if device_to_vehicle[d] is not None]
    try:
        checkpoint([ck_path] if frames else [], vids, through)
    except Exception as exc:
        logging.error(
            "[%s] Checkpoint falló en %s (%s); la partición se detiene aquí",
            script_id, through, master_state.classify_extraction_error(exc),
        )
        return False
    return True


def finalize_watermarks(
    script_id, per_vehicle, ok_ids, failed_ids, any_error, next_start_str, to_date,
    failed_errors: dict[str, str] | None = None,
):
    """Avanza watermarks tras un merge OK. Por vehículo (Postgres) o global (JSON)."""
    if per_vehicle:
        for vid in ok_ids:
            master_state.advance_watermark(vid, script_id, to_date)
        for vid in failed_ids:
            master_state.mark_error(
                vid,
                script_id,
                (failed_errors or {}).get(vid, "worker_error"),
            )
        logging.info(f"[{script_id}] Completado. {len(ok_ids)} OK, {len(failed_ids)} con error.")
    else:
        if any_error:
            logging.warning(f"[{script_id}] Hubo fallas; watermark global NO avanza (se reintenta).")
        elif next_start_str:
            save_next_start_date(script_id, next_start_str)
        logging.info(f"[{script_id}] Completado.")


def run_status_extraction(*, script_id, extract_chunk, fact_file, dedup_subset,
                          chunk_days, default_start, device_filter=None,
                          prefer_new=False, checkpoint_every: int = 0):
    """Flujo completo para extractores de salida única (StatusData/LogRecord/
    FaultData batched por multi_call). Usa estado por vehículo (Fase 3).

    `device_filter(db_name, device_id) -> bool` restringe qué vehículos se
    extraen sin tocar el estado de los demás: los excluidos no se consultan y
    conservan su watermark (si el filtro cambia, retoman desde donde iban).
    `prefer_new` se propaga al merge para hechos agregados (ver
    `utils.merge_staging_to_main`).
    `checkpoint_every` (trozos): con estado por vehículo, cada tantos trozos se
    consolida lo extraído y se avanza el watermark de los vehículos vivos. Es lo
    que hace reanudable un backfill que el worker cancela por tiempo. 0 = solo
    al final (comportamiento histórico).
    """
    with master_state.run_lock(script_id) as got_lock:
        if not got_lock:
            logging.warning(f"[{script_id}] Otra corrida en curso; se omite.")
            return
        to_date = today_midnight_utc()
        groups, per_vehicle, next_start_str = resolve_groups(script_id, default_start, to_date)
        if groups is None:
            logging.info(f"[{script_id}] No hay nuevos días completos para procesar.")
            return

        staging_dir = os.path.join(FACTS_PATH, "_staging")
        clear_staging(FACTS_PATH, script_id)

        checkpoint = None
        if per_vehicle and checkpoint_every > 0:
            def checkpoint(paths, vehicle_ids, through):
                with _CHECKPOINT_LOCK:
                    if paths:
                        merge_staging_to_main(paths, fact_file, dedup_subset, prefer_new=prefer_new)
                    for vid in vehicle_ids:
                        master_state.advance_watermark(vid, script_id, through)
                logging.info(
                    "[%s] checkpoint: %d vehículos avanzados hasta %s",
                    script_id, len(vehicle_ids), through,
                )

        all_staging: list = []
        all_tasks: list = []
        ok_ids, failed_ids, failed_errors, any_error = [], [], {}, False
        for gi, (db_name, from_date, dev_specs) in enumerate(groups):
            if device_filter is not None:
                dev_specs = [
                    spec for spec in dev_specs if device_filter(db_name, spec[0])
                ]
            if not dev_specs:
                continue
            creds_list = CREDENTIALS.get(db_name)
            if not creds_list:
                logging.warning(f"[{script_id}] Sin credenciales configuradas para '{db_name}'")
                missing_ids = [vid for _, vid in dev_specs if vid is not None]
                failed_ids.extend(missing_ids)
                failed_errors.update({vid: "credentials_unavailable" for vid in missing_ids})
                any_error = True
                continue
            working_creds = get_working_credentials(db_name, creds_list)
            if not working_creds:
                missing_ids = [vid for _, vid in dev_specs if vid is not None]
                failed_ids.extend(missing_ids)
                failed_errors.update({vid: "credentials_unavailable" for vid in missing_ids})
                any_error = True
                continue
            n = len(working_creds)
            for i in range(n):
                part = dev_specs[i::n]
                if not part:
                    continue
                devices = [d for d, _ in part]
                vids = [v for _, v in part]
                sp = os.path.join(staging_dir, f"{script_id}_{db_name}_{gi}_{i}.parquet")
                all_staging.append(sp)
                all_tasks.append((db_name, working_creds[i], devices, vids, from_date, sp))

        if all_tasks:
            with ThreadPoolExecutor(max_workers=len(all_tasks)) as executor:
                futures = {
                    executor.submit(
                        _status_worker, script_id, extract_chunk, db_name, creds,
                        devices, vids, from_date, to_date, chunk_days, sp,
                        checkpoint, checkpoint_every,
                    ): vids
                    for db_name, creds, devices, vids, from_date, sp in all_tasks
                }
                for f, task_vids in futures.items():
                    try:
                        o, fa, errors, er = f.result()
                        ok_ids.extend(o)
                        failed_ids.extend(fa)
                        failed_errors.update(errors)
                        any_error = any_error or er
                    except Exception as e:
                        any_error = True
                        fallback_ids = [vid for vid in task_vids if vid is not None]
                        failed_ids.extend(fallback_ids)
                        failed_errors.update({vid: "worker_error" for vid in fallback_ids})
                        logging.error(
                            "[%s] Worker falló de forma inesperada (%s)",
                            script_id, master_state.classify_extraction_error(e),
                        )

        try:
            merge_staging_to_main(
                all_staging, fact_file, dedup_subset, prefer_new=prefer_new
            )
        except Exception as e:
            logging.error(f"[{script_id}] Merge falló; NO se avanza watermark: {e}")
            raise

        finalize_watermarks(
            script_id, per_vehicle, ok_ids, failed_ids, any_error, next_start_str,
            to_date, failed_errors,
        )
