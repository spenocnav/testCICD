# ==============================================================================
# extract_ralenti.py — Episodios de ralentí para el módulo "Análisis Ralentí"
#
# Solo trabaja sobre vehículos de flotas con `fleets.ralenti_analysis_enabled`
# (se activa en /gestion/flotas del portal y llega vía config_source). Para las
# demás flotas resuelve a cero dispositivos y termina enseguida.
#
# Fuente y definición (decisión de este módulo):
#   1. ExceptionEvent de la regla de banda "Ralentí" del motor DENTRO de la base
#      Geotab del vehículo (la misma regla con la que Combustible arma la banda
#      Ralentí). Si el motor no tiene esa regla, cae a la regla nativa de Geotab
#      `RuleIdlingId` (`rule_source='geotab_idling'`).
#   2. Esa regla "parpadea": dispara un evento cada vez que las RPM entran en la
#      banda, así que un vehículo detenido con el motor encendido produce
#      cientos de eventos de pocos segundos por día (medido 2026-09-01: 533
#      eventos/día, mediana 8,8 s, para un ISG12 de Cementos San Marcos). Los
#      eventos consecutivos separados por menos de MERGE_GAP_SECONDS se
#      consolidan en UN episodio: es el episodio, no el evento crudo, lo que
#      responde "cuánto tiempo estuvo en ralentí y dónde".
#   3. Cada episodio se enriquece en un segundo paso (multicall por lotes):
#      RPM (StatusData del diagnóstico de RPM de hábitos, ~1 muestra/s),
#      posición (LogRecord más cercano al inicio) y velocidad máxima.
#
# Escribe DIRECTO en analytics.fact_ralenti_event (upsert por event_sk) en una
# transacción por (vehículo, chunk); no pasa por silver/semantic. Idempotente:
# reextraer una ventana reemplaza los episodios con la misma clave.
#
# Uso:
#   python extract/extract_ralenti.py                      # ventanas por vehículo (watermark)
#   python extract/extract_ralenti.py --from 2026-08-01 --to 2026-09-01 \
#       [--fleet <code>] [--db navitrans] [--device b349,b356] [--no-watermark]
#   Las fechas son días locales (America/Bogota); `--to` es exclusivo.
# ==============================================================================
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging
import statistics
import time as time_module
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

import pandas as pd

import config
import master_state
from config import CHUNK_SIZE_DAYS, CREDENTIALS, DIAGNOSTIC_IDS, VEHICLE_CATALOG, rules_for
from utils import (
    COLOMBIA_TZ,
    authenticate,
    chunk_date_range,
    get_working_credentials,
    make_row_id,
    make_vehicle_id,
    retry_call,
    safe_multi_call,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger('ralenti')

SCRIPT_ID = 'ralenti'
TABLE = 'fact_ralenti_event'

# Regla nativa de Geotab para "vehículo detenido con motor encendido". Se usa
# solo cuando el motor no tiene regla de banda Ralentí en su base.
GEOTAB_IDLING_RULE_ID = 'RuleIdlingId'
BAND_RULE_NAME = 'Ralentí'

RPM_DIAG_ID = DIAGNOSTIC_IDS['rpm_habitos']

# Dos eventos crudos separados por menos de esto son el MISMO episodio. 60 s
# cubre el parpadeo de la regla (RPM que sale y vuelve a la banda al acelerar
# en neutro) sin fundir dos paradas distintas.
MERGE_GAP_SECONDS = float(os.environ.get('ETL_RALENTI_MERGE_GAP_SECONDS', '60'))
# Episodios más cortos que esto se descartan como ruido de la regla. 0 = nada.
MIN_EPISODE_SECONDS = float(os.environ.get('ETL_RALENTI_MIN_SECONDS', '0'))
# Margen alrededor del episodio para buscar la posición: un episodio de pocos
# segundos puede no tener LogRecord propio.
POSITION_MARGIN_SECONDS = 90
# Episodios por multicall de enriquecimiento. Cada uno trae ~1 muestra de RPM
# por segundo, así que 40 episodios de 10 min son ~24.000 puntos por respuesta.
ENRICH_BATCH_SIZE = int(os.environ.get('ETL_RALENTI_ENRICH_BATCH', '40'))
MAX_WORKERS = int(os.environ.get('ETL_RALENTI_MAX_WORKERS', '4'))


# ------------------------------------------------------------------------------
# Funciones puras (probadas en tests/test_extract_ralenti.py)
# ------------------------------------------------------------------------------
@dataclass
class Episode:
    """Un episodio de ralentí: eventos crudos consecutivos fundidos."""

    first_event_id: str
    rule_id: str
    inicio: datetime
    fin: datetime
    eventos_fuente: int = 1
    rpm: list[float] = field(default_factory=list)
    latitud: float | None = None
    longitud: float | None = None
    velocidad_maxima_kmh: float | None = None

    @property
    def duracion_segundos(self) -> float:
        return max(0.0, (self.fin - self.inicio).total_seconds())


def _as_utc(value: Any) -> datetime:
    stamp = pd.Timestamp(value)
    stamp = stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')
    return stamp.to_pydatetime()


def merge_episodes(
    events: Iterable[dict],
    *,
    gap_seconds: float = MERGE_GAP_SECONDS,
    min_seconds: float = MIN_EPISODE_SECONDS,
) -> list[Episode]:
    """Funde ExceptionEvents consecutivos de UN vehículo en episodios.

    Ordena por `activeFrom`; un evento que empieza a menos de `gap_seconds` del
    fin del episodio abierto lo extiende (el fin es el máximo `activeTo`). El
    `first_event_id` es el id Geotab del primer evento: es la identidad estable
    del episodio y entra en `event_sk`, así reextraer la misma ventana reemplaza
    la fila en vez de duplicarla.
    """
    ordered = sorted(
        (e for e in events if e.get('activeFrom') and e.get('activeTo')),
        key=lambda e: _as_utc(e['activeFrom']),
    )
    episodes: list[Episode] = []
    for event in ordered:
        start = _as_utc(event['activeFrom'])
        end = _as_utc(event['activeTo'])
        if end < start:
            continue
        rule_id = (event.get('rule') or {}).get('id') or ''
        current = episodes[-1] if episodes else None
        if current is not None and (start - current.fin).total_seconds() <= gap_seconds:
            current.fin = max(current.fin, end)
            current.eventos_fuente += 1
            continue
        episodes.append(Episode(str(event.get('id')), rule_id, start, end))
    if min_seconds > 0:
        episodes = [ep for ep in episodes if ep.duracion_segundos >= min_seconds]
    return episodes


def rpm_stats(values: Iterable[Any]) -> tuple[float | None, float | None, float | None, int]:
    """(promedio, máximo, mínimo, n) de las lecturas de RPM válidas (> 0)."""
    clean = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0 and f == f:  # descarta NaN
            clean.append(f)
    if not clean:
        return None, None, None, 0
    return statistics.fmean(clean), max(clean), min(clean), len(clean)


def pick_position(
    records: Iterable[dict], inicio: datetime, fin: datetime
) -> tuple[float | None, float | None, float | None]:
    """Posición del episodio y velocidad máxima observada durante él.

    La posición es la del LogRecord más cercano a `inicio` (dentro del episodio
    si lo hay; si no, el más cercano del margen). La velocidad máxima se toma
    solo de los registros DENTRO del episodio: sirve para detectar un episodio
    que no fue una parada real."""
    best: tuple[float, float, float] | None = None
    max_speed: float | None = None
    for r in records:
        dt = r.get('dateTime')
        lat, lon = r.get('latitude'), r.get('longitude')
        if dt is None or lat is None or lon is None:
            continue
        stamp = _as_utc(dt)
        inside = inicio <= stamp <= fin
        distance = abs((stamp - inicio).total_seconds())
        # Un registro dentro del episodio siempre gana a uno del margen.
        rank = (0 if inside else 1, distance)
        if best is None or rank < (best[0], best[1]):
            best = (rank[0], rank[1], 0.0)
            chosen = (float(lat), float(lon))
        if inside and r.get('speed') is not None:
            speed = float(r['speed'])
            max_speed = speed if max_speed is None else max(max_speed, speed)
    if best is None:
        return None, None, max_speed
    return chosen[0], chosen[1], max_speed


def resolve_rule(database_name: str, motor_type: str | None) -> tuple[str, str]:
    """(rule_id, rule_source) para un vehículo: banda Ralentí del motor en su
    base, o la regla nativa de Geotab si el motor no la tiene."""
    band_rule = rules_for(database_name, motor_type or '').get(BAND_RULE_NAME)
    if band_rule:
        return band_rule, 'banda'
    return GEOTAB_IDLING_RULE_ID, 'geotab_idling'


def episode_row(
    ep: Episode,
    *,
    database_name: str,
    device_id: str,
    rule_source: str,
    extracted_at: datetime,
) -> dict:
    local = ep.inicio.astimezone(COLOMBIA_TZ)
    avg, mx, mn, n = rpm_stats(ep.rpm)
    return {
        'event_sk': make_row_id(database_name, device_id, ep.first_event_id, 'ralenti'),
        'vehicle_id': make_vehicle_id(database_name, device_id),
        'database_name': database_name,
        'device_id': device_id,
        'rule_id': ep.rule_id or None,
        'rule_source': rule_source,
        'inicio': ep.inicio,
        'fin': ep.fin,
        'duracion_segundos': ep.duracion_segundos,
        'eventos_fuente': ep.eventos_fuente,
        'rpm_promedio': avg,
        'rpm_maximo': mx,
        'rpm_minimo': mn,
        'rpm_muestras': n,
        'velocidad_maxima_kmh': ep.velocidad_maxima_kmh,
        'latitud': ep.latitud,
        'longitud': ep.longitud,
        'date_key': int(local.strftime('%Y%m%d')),
        'fecha': local.date(),
        'hora_local': local.hour,
        'extracted_at': extracted_at,
    }


COLUMNS: dict[str, str] = {
    'event_sk': 'string', 'vehicle_id': 'string', 'database_name': 'string',
    'device_id': 'string', 'rule_id': 'string', 'rule_source': 'string',
    'inicio': 'datetime64[ns, UTC]', 'fin': 'datetime64[ns, UTC]',
    'duracion_segundos': 'float64', 'eventos_fuente': 'Int64',
    'rpm_promedio': 'float64', 'rpm_maximo': 'float64', 'rpm_minimo': 'float64',
    'rpm_muestras': 'Int64', 'velocidad_maxima_kmh': 'float64',
    'latitud': 'float64', 'longitud': 'float64',
    'date_key': 'Int64', 'fecha': 'object', 'hora_local': 'Int64',
    'extracted_at': 'datetime64[ns, UTC]',
}


def rows_frame(rows: list[dict]) -> pd.DataFrame:
    """DataFrame con dtypes FIJOS. `create_table` deduce el tipo PG del dtype:
    un lote sin RPM haría nacer la columna como TEXT para siempre."""
    df = pd.DataFrame(rows, columns=list(COLUMNS))
    for col, dtype in COLUMNS.items():
        if dtype == 'string':
            df[col] = df[col].astype('string')
        elif dtype.startswith('datetime'):
            df[col] = pd.to_datetime(df[col], utc=True).astype(dtype)
        elif dtype == 'object':
            continue
        else:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype(dtype)
    return df


# ------------------------------------------------------------------------------
# Geotab
# ------------------------------------------------------------------------------
def fetch_episodes(
    api, device_id: str, rule_id: str, from_str: str, to_str: str
) -> list[Episode]:
    """Paso 1: ExceptionEvents de la regla en la ventana → episodios."""
    result = safe_multi_call(api, [(
        'Get', {
            'typeName': 'ExceptionEvent',
            'search': {
                'fromDate': from_str,
                'toDate': to_str,
                'deviceSearch': {'id': device_id},
                'ruleSearch': {'id': rule_id},
            },
        },
    )])
    events = result[0] or []
    return merge_episodes(events)


def enrich_episodes(api, device_id: str, episodes: list[Episode]) -> None:
    """Paso 2: RPM, posición y velocidad por episodio, en lotes de multicall."""
    margin = timedelta(seconds=POSITION_MARGIN_SECONDS)
    for start in range(0, len(episodes), ENRICH_BATCH_SIZE):
        batch = episodes[start:start + ENRICH_BATCH_SIZE]
        requests: list[tuple[str, dict]] = []
        for ep in batch:
            requests.append(('Get', {
                'typeName': 'LogRecord',
                'search': {
                    'fromDate': ep.inicio - margin,
                    'toDate': ep.fin + margin,
                    'deviceSearch': {'id': device_id},
                },
            }))
            requests.append(('Get', {
                'typeName': 'StatusData',
                'search': {
                    'fromDate': ep.inicio,
                    'toDate': ep.fin,
                    'deviceSearch': {'id': device_id},
                    'diagnosticSearch': {'id': RPM_DIAG_ID},
                },
            }))
        results = safe_multi_call(api, requests)
        for idx, ep in enumerate(batch):
            logs = results[2 * idx] or []
            status = results[2 * idx + 1] or []
            ep.latitud, ep.longitud, ep.velocidad_maxima_kmh = pick_position(
                logs, ep.inicio, ep.fin
            )
            ep.rpm = [r.get('data') for r in status]


def process_device_chunk(
    api, device_id: str, rule_id: str, from_str: str, to_str: str
) -> list[Episode]:
    episodes = fetch_episodes(api, device_id, rule_id, from_str, to_str)
    if episodes:
        enrich_episodes(api, device_id, episodes)
    return episodes


# ------------------------------------------------------------------------------
# Persistencia
# ------------------------------------------------------------------------------
def _spec():
    from load.schema import TABLES
    return next(t for t in TABLES if t.pg_table == TABLE)


def ensure_table(engine) -> None:
    """DDL idempotente: tabla, índices y FK NOT VALID a las dimensiones."""
    from load import db
    spec = _spec()
    empty = rows_frame([])
    db.ensure_schema(engine)
    db.create_table(engine, spec, empty)
    db.create_indexes(engine, spec, empty)
    db.add_fk_constraints(engine, spec)


def persist(engine, rows: list[dict], vehicle: dict) -> int:
    """Upsert de los episodios de un (vehículo, chunk) en UNA transacción,
    sembrando antes la FK del vehículo y de los días (DO NOTHING: el ETL diario
    es dueño de esas dimensiones)."""
    if not rows:
        return 0
    from load import db
    from load.microbatch import build_date_dimension
    from load.schema import TABLES

    dim_vehicle_spec = next(t for t in TABLES if t.pg_table == 'dim_vehicle')
    dim_date_spec = next(t for t in TABLES if t.pg_table == 'dim_date')
    df = rows_frame(rows)
    vehicle_dim = pd.DataFrame([{
        'vehicle_id': rows[0]['vehicle_id'],
        'database_name': vehicle['database_name'],
        'device_id': vehicle['device_id'],
        'vehicle_label': vehicle.get('placa'),
        'motor_type': vehicle.get('motor_type'),
        'group_key': vehicle.get('group_key'),
        'rpm_class': vehicle.get('rpm_class'),
        'is_active': True,
    }])
    days = sorted({r['fecha'] for r in rows})
    date_dim = pd.concat([build_date_dimension(d) for d in days], ignore_index=True)
    with engine.begin() as conn:
        db.insert_dataframe_ignore_conn(conn, dim_vehicle_spec, vehicle_dim)
        db.insert_dataframe_ignore_conn(conn, dim_date_spec, date_dim)
        return db.upsert_dataframe_conn(conn, _spec(), df)


# ------------------------------------------------------------------------------
# Orquestación
# ------------------------------------------------------------------------------
def _worker(
    db_name: str,
    credentials: dict,
    specs: list[tuple[dict, datetime, datetime, str | None]],
    chunk_days: int,
    engine,
    advance: bool,
    slot: int = 0,
) -> dict:
    """Procesa una partición de vehículos con UNA credencial. Devuelve conteos.

    `slot` identifica la credencial en el log; el usuario Geotab no se imprime."""
    summary = {'episodes': 0, 'ok': 0, 'failed': 0}
    try:
        api = authenticate(db_name, credentials)
    except Exception as exc:
        log.error("[%s] %s: credencial no autentica (%s)", SCRIPT_ID, db_name, type(exc).__name__)
        for vehicle, _f, _t, vid in specs:
            summary['failed'] += 1
            if vid and advance:
                master_state.mark_error(vid, SCRIPT_ID, exc)
        return summary

    for idx, (vehicle, from_date, to_date, vehicle_uuid) in enumerate(specs, 1):
        device_id = vehicle['device_id']
        rule_id, rule_source = resolve_rule(db_name, vehicle.get('motor_type'))
        failed = False
        for from_str, to_str in chunk_date_range(from_date, to_date, chunk_days):
            log.info(
                "    [%s/cred#%d] %d/%d %s (%s, regla %s) %s → %s",
                db_name, slot, idx, len(specs), vehicle.get('placa'),
                device_id, rule_source, from_str, to_str,
            )
            try:
                episodes = retry_call(
                    process_device_chunk, api, device_id, rule_id, from_str, to_str,
                    _script_id=SCRIPT_ID,
                    _label=f"[{db_name}/{device_id}] {from_str}",
                    _reauth=lambda: authenticate(db_name, credentials, force=True),
                )
                extracted_at = datetime.now(timezone.utc)
                rows = [
                    episode_row(
                        ep, database_name=db_name, device_id=device_id,
                        rule_source=rule_source, extracted_at=extracted_at,
                    )
                    for ep in episodes
                ]
                written = persist(engine, rows, vehicle)
                summary['episodes'] += written
                log.info("      %d episodios escritos", written)
            except Exception as exc:
                failed = True
                log.error(
                    "[%s] %s/%s %s: chunk fallido (%s); no se avanza el watermark",
                    SCRIPT_ID, db_name, device_id, from_str, type(exc).__name__,
                )
                break
        if failed:
            summary['failed'] += 1
            if vehicle_uuid and advance:
                master_state.mark_error(vehicle_uuid, SCRIPT_ID, 'api_request_error')
        else:
            summary['ok'] += 1
            if vehicle_uuid and advance:
                master_state.advance_watermark(vehicle_uuid, SCRIPT_ID, to_date)
    return summary


def _enabled_vehicles(fleet_code: str | None, db_filter: str | None, devices: set[str] | None):
    for vehicle in VEHICLE_CATALOG:
        if not vehicle.get('ralenti_analysis'):
            continue
        if vehicle['database_name'] not in CREDENTIALS:
            continue
        if fleet_code and (vehicle.get('fleet_code') or '').lower() != fleet_code.lower():
            continue
        if db_filter and vehicle['database_name'] != db_filter:
            continue
        if devices and vehicle['device_id'] not in devices:
            continue
        yield vehicle


def _local_midnight_utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=COLOMBIA_TZ).astimezone(timezone.utc)


def _today_midnight_utc() -> datetime:
    return _local_midnight_utc(datetime.now(COLOMBIA_TZ).date())


def build_specs(args) -> tuple[dict[str, list], bool]:
    """(specs por base, ¿avanzar watermark?).

    Con `--from/--to` la ventana es explícita para todos los vehículos habilitados
    y el watermark no se toca salvo que la ventana termine en hoy. Sin fechas,
    cada vehículo trae su propia ventana desde `vehicle_extraction_state`."""
    devices = set(args.device.split(',')) if args.device else None
    enabled = list(_enabled_vehicles(args.fleet, args.db, devices))
    by_db: dict[str, list] = defaultdict(list)
    if args.date_from:
        from_date = _local_midnight_utc(args.date_from)
        to_date = _local_midnight_utc(args.date_to) if args.date_to else _today_midnight_utc()
        to_date = min(to_date, _today_midnight_utc())
        if from_date >= to_date:
            return {}, False
        advance = (not args.no_watermark) and to_date == _today_midnight_utc()
        windows = master_state.get_vehicle_windows(SCRIPT_ID, to_date) if advance else None
        uuid_by_key = {
            (w['database_name'], w['device_id']): str(w['vehicle_id']) for w in (windows or [])
        }
        for vehicle in enabled:
            key = (vehicle['database_name'], vehicle['device_id'])
            by_db[vehicle['database_name']].append(
                (vehicle, from_date, to_date, uuid_by_key.get(key))
            )
        return by_db, advance

    to_date = _today_midnight_utc()
    windows = master_state.get_vehicle_windows(SCRIPT_ID, to_date)
    if windows is None:
        log.warning("[%s] sin DB del portal: no hay ventanas por vehículo; nada que hacer", SCRIPT_ID)
        return {}, False
    enabled_keys = {(v['database_name'], v['device_id']): v for v in enabled}
    for w in windows:
        vehicle = enabled_keys.get((w['database_name'], w['device_id']))
        if vehicle is None:
            continue
        from_date = _as_utc(w['from_date'])
        if from_date >= to_date:
            continue
        by_db[w['database_name']].append((vehicle, from_date, to_date, str(w['vehicle_id'])))
    return by_db, not args.no_watermark


def run(args) -> dict:
    from load import db

    specs_by_db, advance = build_specs(args)
    total_vehicles = sum(len(v) for v in specs_by_db.values())
    if not total_vehicles:
        log.info("[%s] Ningún vehículo con Análisis de Ralentí habilitado en la ventana.", SCRIPT_ID)
        return {'episodes': 0, 'ok': 0, 'failed': 0}

    engine = db.get_engine()
    ensure_table(engine)
    chunk_days = CHUNK_SIZE_DAYS['ralenti']
    totals = {'episodes': 0, 'ok': 0, 'failed': 0}
    started = time_module.time()

    for db_name, specs in specs_by_db.items():
        creds = get_working_credentials(db_name, CREDENTIALS.get(db_name, []))
        if not creds:
            log.error("[%s] %s: sin credenciales operativas; %d vehículos omitidos", SCRIPT_ID, db_name, len(specs))
            totals['failed'] += len(specs)
            continue
        # Round-robin: cada credencial atiende una partición de vehículos en
        # paralelo. Más credenciales = más cupo de API, no más carga por sesión.
        workers = min(len(creds), max(1, MAX_WORKERS), len(specs))
        partitions: list[list] = [[] for _ in range(workers)]
        for i, spec in enumerate(specs):
            partitions[i % workers].append(spec)
        log.info(
            "[%s] %s: %d vehículos, %d credenciales operativas, %d hilos",
            SCRIPT_ID, db_name, len(specs), len(creds), workers,
        )
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_worker, db_name, creds[i], part, chunk_days, engine, advance, i + 1)
                for i, part in enumerate(partitions) if part
            ]
            for fut in futures:
                result = fut.result()
                for k in totals:
                    totals[k] += result[k]

    log.info(
        "[%s] Fin: %d episodios, %d vehículos OK, %d con error, %.0f s",
        SCRIPT_ID, totals['episodes'], totals['ok'], totals['failed'], time_module.time() - started,
    )
    return totals


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Extrae episodios de ralentí (flotas con el análisis activo).')
    parser.add_argument('--from', dest='date_from', type=date.fromisoformat, default=None,
                        help='Día local inicial (YYYY-MM-DD).')
    parser.add_argument('--to', dest='date_to', type=date.fromisoformat, default=None,
                        help='Día local final EXCLUSIVO (YYYY-MM-DD); default hoy.')
    parser.add_argument('--fleet', default=None, help='Código de flota (fleets.code).')
    parser.add_argument('--db', default=None, help='Base Geotab (database_name).')
    parser.add_argument('--device', default=None, help='Lista de device_id separados por coma.')
    parser.add_argument('--no-watermark', action='store_true',
                        help='No tocar vehicle_extraction_state.')
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)
    log.info("[%s] Iniciando extracción de episodios de ralentí", SCRIPT_ID)
    with master_state.run_lock(SCRIPT_ID) as got_lock:
        if not got_lock:
            log.warning("[%s] Otra corrida en curso; se omite.", SCRIPT_ID)
            return
        totals = run(args)
    if totals['failed'] and not totals['ok']:
        sys.exit(1)


if __name__ == '__main__':
    main()
