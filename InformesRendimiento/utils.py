# ==============================================================================
# utils.py — Utilidades compartidas por todos los scripts de extracción
# ==============================================================================
import os
import json
import time
import shutil
import logging
import ssl
import threading
import hashlib
import requests
import requests.exceptions
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import mygeotab
from mygeotab import AuthenticationException
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

# Directorio de estado local (caché de sesiones y watermark heredado). Por
# defecto es el del código; en producción ETL_STATE_DIR lo lleva a un volumen
# para que la imagen pueda correr con el código de sólo lectura y sin root.
_STATE_DIR = os.environ.get('ETL_STATE_DIR') or os.path.dirname(os.path.abspath(__file__))
if os.environ.get('ETL_STATE_DIR'):
    os.makedirs(_STATE_DIR, exist_ok=True)

# Caché de sesiones Geotab (session_id válido ~1 semana).
# Evita llamadas de autenticación en cada ejecución de script.
SESSION_CACHE_FILE = os.path.join(_STATE_DIR, '.session_cache.json')

# Serializa el read-modify-write del cache de sesión entre workers concurrentes.
# Sin esto, dos hilos que renuevan sesión a la vez pueden pisarse la entrada del
# otro al reescribir el JSON completo (carrera benigna pero causa re-auth extra).
_SESSION_CACHE_LOCK = threading.Lock()
_SESSION_MAX_AGE_SECONDS = int(
    os.environ.get('GEOTAB_SESSION_MAX_AGE_SECONDS', str(6 * 24 * 60 * 60))
)

# ------------------------------------------------------------------------------
# Fix Brotli: eliminar 'br' para evitar el error de descompresión en la API
# Se aplica al importar este módulo
# ------------------------------------------------------------------------------
if 'br' in requests.utils.DEFAULT_ACCEPT_ENCODING:
    requests.utils.DEFAULT_ACCEPT_ENCODING = (
        requests.utils.DEFAULT_ACCEPT_ENCODING.replace(', br', '')
    )

# ------------------------------------------------------------------------------
# Zona horaria Colombia
# ------------------------------------------------------------------------------
COLOMBIA_TZ     = timezone(timedelta(hours=-5))
COLOMBIA_OFFSET = timedelta(hours=5)   # UTC - 5h = hora Colombia


def make_row_id(*parts) -> str:
    """MD5 hash estable de los campos que componen la clave natural de una fila.
    Usar para tablas sin un ID único de fila nativo (fact_status_readings, fact_fault_data).
    """
    key = '|'.join('' if p is None else str(p) for p in parts)
    return hashlib.md5(key.encode()).hexdigest()


def make_vehicle_id(database_name: str | None, device_id: str) -> str:
    """Clave estable del vehiculo. Scopea el device_id por base de datos."""
    return make_row_id(database_name or 'UNKNOWN', device_id)


def make_event_sk(database_name: str | None, device_id: str, event_id: str | None) -> str:
    """Clave tecnica estable para eventos Geotab."""
    return make_row_id(database_name or 'UNKNOWN', device_id, event_id)


def normalize_instant(value) -> str:
    """Representacion canonica de un instante para claves tecnicas.

    Normaliza a UTC e ISO-8601 con microsegundos, de modo que un mismo
    timestamp produzca el mismo hash venga como `datetime` (extraccion) o
    como `pandas.Timestamp` (parquet releido). Un valor no interpretable se
    devuelve como texto crudo, nunca vacio."""
    if value is None:
        return ''
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        return ''
    stamp = stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')
    return stamp.isoformat(timespec='microseconds')


def make_trip_row_id(
    database_name: str | None,
    device_id: str,
    start,
) -> str:
    """Clave tecnica para viajes: (base, device, inicio del viaje).

    NO incluye el `id` de Geotab a proposito. Un Trip es un dato CALCULADO:
    mientras el viaje esta en curso su `stop`/`distance` se actualizan y el
    servidor lo reemplaza por un registro NUEVO con OTRO `id`; lo mismo ocurre
    cuando reprocesa datos historicos. La clave estable documentada por Geotab
    es `deviceId` + fecha de inicio del viaje. Usar el `id` hacia que una
    reextraccion insertara el mismo viaje dos veces y duplicara `Kms GPS`.
    Ver https://developers.geotab.com/myGeotab/guides/dataFeed/"""
    return make_row_id(database_name or 'UNKNOWN', device_id, normalize_instant(start), 'trip')


def make_log_row_id(
    database_name: str | None,
    device_id: str,
    geotab_id: str | None,
    event_id: str | None,
    date_time,
) -> str:
    """Clave tecnica para puntos GPS y detalles de eventos."""
    return make_row_id(database_name or 'UNKNOWN', device_id, geotab_id, event_id, date_time, 'log')


def normalize_date(value) -> date | None:
    """Convierte timestamps o strings a date sin zona horaria."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return None


def date_to_key(value) -> int | None:
    """Convierte una fecha a entero YYYYMMDD para relaciones de modelo."""
    dt = normalize_date(value)
    if dt is None:
        return None
    return int(dt.strftime('%Y%m%d'))


def month_to_key(value) -> int | None:
    """Convierte una fecha a entero YYYYMM para agregados mensuales."""
    dt = normalize_date(value)
    if dt is None:
        return None
    return int(dt.strftime('%Y%m'))


# ------------------------------------------------------------------------------
# Gestión de estado incremental
# ------------------------------------------------------------------------------
ESTADO_FILE = os.path.join(_STATE_DIR, 'estado_ejecucion.json')


def get_date_range_for_run(
    script_id: str,
    default_start_date_str: str = '2024-01-01',
) -> Optional[Tuple[Tuple[datetime, datetime], str]]:
    """
    Devuelve ((from_date_utc, to_date_utc), next_start_date_str) o None si
    no hay nuevos días completos para procesar.

    - from_date_utc: última ejecución guardada en estado_ejecucion.json
    - to_date_utc:   medianoche de hoy (hora Colombia) convertida a UTC
      → solo se procesan días COMPLETOS ya terminados
    """
    try:
        with open(ESTADO_FILE, 'r') as f:
            estado = json.load(f)
        last_run_str = estado.get(script_id)
        if not last_run_str:
            raise FileNotFoundError
        from_date_utc = datetime.fromisoformat(last_run_str.replace('Z', '+00:00'))
    except (FileNotFoundError, ValueError, TypeError):
        start_local = datetime.strptime(default_start_date_str, '%Y-%m-%d').replace(
            tzinfo=COLOMBIA_TZ
        )
        from_date_utc = start_local.astimezone(timezone.utc)
        logging.info(
            f"No se encontró estado para '{script_id}'. "
            f"Iniciando desde: {_fmt(from_date_utc)}"
        )

    today_local = datetime.now(COLOMBIA_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    to_date_utc = today_local.astimezone(timezone.utc)

    if from_date_utc >= to_date_utc:
        logging.info(f"No hay nuevos días completos para '{script_id}'.")
        return None

    next_start_str = _fmt(to_date_utc)
    logging.info(
        f"[{script_id}] Rango: {_fmt(from_date_utc)} → {_fmt(to_date_utc)}"
    )
    return (from_date_utc, to_date_utc), next_start_str


def save_next_start_date(script_id: str, next_start_date_str: str) -> None:
    """Persiste la próxima fecha de inicio en estado_ejecucion.json."""
    estado: dict = {}
    if os.path.exists(ESTADO_FILE):
        try:
            with open(ESTADO_FILE, 'r') as f:
                estado = json.load(f)
        except (ValueError, TypeError):
            pass
    estado[script_id] = next_start_date_str
    with open(ESTADO_FILE, 'w') as f:
        json.dump(estado, f, indent=4)
    logging.info(f"[{script_id}] Próxima ejecución desde: {next_start_date_str}")


# ------------------------------------------------------------------------------
# Formateo de fechas
# ------------------------------------------------------------------------------
def _fmt(dt: datetime) -> str:
    """Formatea un datetime a cadena ISO-8601 con milisegundos y sufijo Z."""
    return dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def fmt_date(dt: datetime) -> str:
    """Alias público de _fmt."""
    return _fmt(dt)


def chunk_date_range(
    start: datetime, end: datetime, chunk_days: int
):
    """
    Genera tuplas (from_str, to_str) segmentando [start, end) en trozos de
    chunk_days días.  to_str es el instante anterior al inicio del siguiente
    chunk (– 1 ms) para evitar solapamientos.
    """
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        yield (
            _fmt(current),
            _fmt(chunk_end - timedelta(milliseconds=1)),
        )
        current = chunk_end


# ------------------------------------------------------------------------------
# Caché de sesión Geotab
# ------------------------------------------------------------------------------
def _load_session_cache() -> dict:
    if os.path.exists(SESSION_CACHE_FILE):
        try:
            with open(SESSION_CACHE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_session_to_cache(database: str, api: mygeotab.API) -> None:
    """Persiste session_id, username y server en .session_cache.json."""
    creds = getattr(api, 'credentials', None)
    if not creds or not getattr(creds, 'session_id', None):
        return
    cache_key = f"{database}_{creds.username}"
    entry = {
        'username':   creds.username,
        'session_id': creds.session_id,
        'server':     creds.server,
        'saved_at':   datetime.now(timezone.utc).isoformat(),
    }
    # Lock: re-lee dentro de la sección crítica para no perder entradas de otros
    # hilos. Escritura atómica (.tmp → os.replace) para que un lector concurrente
    # nunca vea un JSON parcial.
    with _SESSION_CACHE_LOCK:
        cache = _load_session_cache()
        cache[cache_key] = entry
        try:
            tmp_path = SESSION_CACHE_FILE + '.tmp'
            with open(tmp_path, 'w') as f:
                json.dump(cache, f, indent=2)
            os.replace(tmp_path, SESSION_CACHE_FILE)
        except Exception as e:
            logging.warning(f"[auth] No se pudo guardar session cache: {e}")


def _remove_session_from_cache(database: str, username: str) -> None:
    """Elimina una sesión inválida para que no vuelva a reutilizarse."""
    cache_key = f"{database}_{username}"
    with _SESSION_CACHE_LOCK:
        cache = _load_session_cache()
        if cache_key not in cache:
            return
        cache.pop(cache_key, None)
        try:
            tmp_path = SESSION_CACHE_FILE + '.tmp'
            with open(tmp_path, 'w') as f:
                json.dump(cache, f, indent=2)
            os.replace(tmp_path, SESSION_CACHE_FILE)
        except Exception as e:
            logging.warning(f"[auth] No se pudo invalidar session cache: {e}")


def _session_is_fresh(entry: dict) -> bool:
    """Las entradas antiguas sin timestamp se consideran inválidas."""
    saved_at = entry.get('saved_at')
    if not saved_at:
        return False
    try:
        saved_dt = datetime.fromisoformat(str(saved_at).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return False
    age = (datetime.now(timezone.utc) - saved_dt).total_seconds()
    return 0 <= age < _SESSION_MAX_AGE_SECONDS


# ------------------------------------------------------------------------------
# Autenticación
# ------------------------------------------------------------------------------
def authenticate(
    database: str, credentials: dict, *, force: bool = False
) -> mygeotab.API:
    """
    Devuelve mygeotab.API lista para usar.
    - Si existe session_id cacheado: lo reutiliza (0 llamadas API).
    - Si no: autentica con username/password y guarda la sesión.
    El password siempre se pasa al objeto para permitir renovación automática
    ante SessionExpiredException (ver safe_api_call / safe_multi_call).
    """
    cache = _load_session_cache()
    cache_key = f"{database}_{credentials['username']}"
    cached = cache.get(cache_key)

    if cached and not force and _session_is_fresh(cached):
        api = mygeotab.API(
            username=credentials['username'],
            password=credentials['password'],   # necesario para renovación
            database=database,
            session_id=cached['session_id'],
            server=cached.get('server', 'my.geotab.com'),
        )
        logging.info("[auth] '%s': sesión reutilizada", database)
        return api

    if cached:
        _remove_session_from_cache(database, credentials['username'])
        logging.info("[auth] '%s': sesión cacheada vencida; autenticando de nuevo", database)

    # Sin caché — autenticar y guardar
    api = mygeotab.API(
        username=credentials['username'],
        password=credentials['password'],
        database=database,
    )
    api.authenticate()
    _save_session_to_cache(database, api)
    logging.info("[auth] '%s': autenticado; sesión guardada en caché", database)
    return api


# ------------------------------------------------------------------------------
# Filtrado de credenciales válidas para paralelismo multi-credencial
# ------------------------------------------------------------------------------
def get_working_credentials(database: str, creds_list: list) -> list:
    """
    Intenta autenticar cada credencial de la lista secuencialmente.
    Retorna solo las que funcionan. Las sesiones exitosas quedan cacheadas,
    por lo que los workers las reutilizan sin nueva llamada a la API.

    Si una credencial falla, sus devices serán reasignados automáticamente
    a las credenciales restantes por el round-robin del caller.
    """
    working = []
    for creds in creds_list:
        try:
            authenticate(database, creds)
            working.append(creds)
        except Exception:
            logging.warning(
                "[auth] '%s': una credencial falló; sus dispositivos serán reasignados",
                database,
            )
    if not working:
        logging.error(f"[auth] '{database}': ninguna credencial disponible")
    return working


# ------------------------------------------------------------------------------
# retry_call: reintentos para errores transitorios a nivel de chunk
# Complementa safe_multi_call (que cubre OverLimit/SessionExpired internamente)
# ------------------------------------------------------------------------------
CHUNK_MAX_RETRIES = 3
CHUNK_RETRY_WAIT_SECONDS = 10

# Errores de red detectados por tipo (robusto ante cambios de versión de urllib3)
_NETWORK_ERRORS = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    ssl.SSLError,
)

NET_MAX_RETRIES = 5
NET_RETRY_WAIT_SECONDS = 45


def retry_call(func, *args, _script_id='', _label='', _reauth=None):
    """
    Reintenta func(*args) ante errores transitorios con estrategia diferenciada:
    - Errores de red (SSL/Connection/Timeout): hasta NET_MAX_RETRIES intentos,
      espera NET_RETRY_WAIT_SECONDS * intento segundos. Re-autentica antes del reintento.
    - Errores de API (otros): hasta CHUNK_MAX_RETRIES intentos,
      espera CHUNK_RETRY_WAIT_SECONDS * intento segundos.

    _reauth: callable sin argumentos que devuelve un nuevo mygeotab.API.
    Se invoca en errores de red para reemplazar args[0] (el objeto api) antes del reintento.
    """
    mutable_args = list(args)
    attempt = 0
    while True:
        try:
            return func(*mutable_args)
        except Exception as e:
            is_net = isinstance(e, _NETWORK_ERRORS)
            is_auth = isinstance(e, AuthenticationException) or any(
                token in f"{type(e).__name__} {e}".casefold()
                for token in ('sessionexpired', 'authentication', 'invaliduser')
            )
            max_retries = NET_MAX_RETRIES if is_net else CHUNK_MAX_RETRIES
            wait_base = NET_RETRY_WAIT_SECONDS if is_net else CHUNK_RETRY_WAIT_SECONDS
            attempt += 1
            if attempt < max_retries:
                wait = wait_base * attempt if not is_auth else 0
                tipo = "red" if is_net else "autenticación" if is_auth else "api"
                logging.warning(
                    f"[{_script_id}] {_label} — error {tipo}, "
                    f"reintento {attempt}/{max_retries}: {e}. "
                    + (f"Esperando {wait}s…" if wait else "reautenticando…")
                )
                if wait:
                    time.sleep(wait)
                if (is_net or is_auth) and _reauth is not None and mutable_args:
                    try:
                        mutable_args[0] = _reauth()
                        logging.info(f"[{_script_id}] Re-autenticado exitosamente")
                    except Exception as re_err:
                        logging.warning(f"[{_script_id}] Re-auth falló: {re_err}")
            else:
                logging.error(
                    f"[{_script_id}] {_label} — error definitivo tras {attempt} intentos: {e}"
                )
                raise


# ------------------------------------------------------------------------------
# safe_api_call: reintentos ante OverLimitException (heredado de Habitos seguros)
# INMUTABLE: ver plan §5.8 — parámetros calibrados para límites de la API
# ------------------------------------------------------------------------------
MAX_RETRIES = 5
RETRY_WAIT_SECONDS = 65


def _renew_session(api: mygeotab.API) -> None:
    """
    Re-autentica con username+password y actualiza el caché.
    Llamado automáticamente cuando se detecta SessionExpiredException.
    """
    creds = getattr(api, 'credentials', None)
    db = getattr(creds, 'database', None) or getattr(api, '_database', 'unknown')
    logging.warning(f"[auth] '{db}': sesión expirada — renovando…")
    api.authenticate()
    _save_session_to_cache(db, api)
    logging.info(f"[auth] '{db}': sesión renovada y guardada en caché")


def safe_api_call(api: mygeotab.API, method: str, **kwargs):
    """
    Llama a api.get(method, **kwargs) con reintentos ante OverLimitException.
    Renueva la sesión automáticamente si ocurre SessionExpiredException.
    Usa string matching igual que el original (plan §5.8 — INMUTABLE).
    """
    session_renewed = False
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(0.1)
            return api.get(method, **kwargs)
        except Exception as e:
            e_str = str(e)
            if 'SessionExpiredException' in e_str and not session_renewed:
                _renew_session(api)
                session_renewed = True
                continue   # reintentar sin consumir cuota de MAX_RETRIES
            elif 'OverLimitException' in e_str and attempt < MAX_RETRIES:
                logging.warning(
                    f"safe_api_call: OverLimitException en {method} "
                    f"(intento {attempt}/{MAX_RETRIES}). "
                    f"Esperando {RETRY_WAIT_SECONDS}s…"
                )
                time.sleep(RETRY_WAIT_SECONDS)
            else:
                raise


def safe_multi_call(api: mygeotab.API, requests_list: list):
    """
    Envía api.multi_call(requests_list) con reintentos ante OverLimitException.
    Renueva la sesión automáticamente si ocurre SessionExpiredException.
    Usa string matching igual que el original (plan §5.8 — INMUTABLE).
    """
    session_renewed = False
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(0.1)
            return api.multi_call(requests_list)
        except Exception as e:
            e_str = str(e)
            if 'SessionExpiredException' in e_str and not session_renewed:
                _renew_session(api)
                session_renewed = True
                continue   # reintentar sin consumir cuota de MAX_RETRIES
            elif 'OverLimitException' in e_str and attempt < MAX_RETRIES:
                logging.warning(
                    f"safe_multi_call: OverLimitException "
                    f"(intento {attempt}/{MAX_RETRIES}). "
                    f"Esperando {RETRY_WAIT_SECONDS}s…"
                )
                time.sleep(RETRY_WAIT_SECONDS)
            else:
                raise


# ------------------------------------------------------------------------------
# Escritura incremental a Parquet
# ------------------------------------------------------------------------------
def append_to_parquet(
    df_new: pd.DataFrame,
    file_path: str,
    dedup_subset: Optional[list] = None,
) -> None:
    """
    Carga el parquet existente (si hay), concatena df_new, deduplica por
    dedup_subset y reescribe. Si df_new está vacío, no hace nada.

    Usa snappy como compresor (compatible con DirectLake en Power BI Fabric).
    """
    if df_new.empty:
        return

    if os.path.exists(file_path):
        try:
            df_existing = pd.read_parquet(file_path)
        except Exception:
            df_existing = pd.DataFrame()
    else:
        df_existing = pd.DataFrame()

    df_combined = pd.concat([df_existing, df_new], ignore_index=True)

    if dedup_subset:
        # Conservar el primer registro (df_existing tiene prioridad sobre df_new)
        df_combined = df_combined.drop_duplicates(subset=dedup_subset, keep='first')

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    df_combined.to_parquet(file_path, index=False, compression='snappy')
    logging.info(
        f"Parquet actualizado: {os.path.basename(file_path)} "
        f"({len(df_combined):,} filas totales)"
    )


# ------------------------------------------------------------------------------
# Staging helpers — escritura por worker + merge final atómico (.tmp → replace)
# ------------------------------------------------------------------------------
def write_staging(df: pd.DataFrame, staging_path: str) -> None:
    """Escribe el DataFrame acumulado del worker a su parquet de staging."""
    if df.empty:
        return
    os.makedirs(os.path.dirname(staging_path), exist_ok=True)
    df.to_parquet(staging_path, index=False, compression='snappy')
    logging.info(f"Staging escrito: {os.path.basename(staging_path)} ({len(df):,} filas)")


# ------------------------------------------------------------------------------
# Particionado por mes del data lake (optimización interna, sin cambiar el
# contrato de datos hacia transforms/load).
# ------------------------------------------------------------------------------
# Un "main file" de facts puede materializarse como:
#   - archivo único legacy:   fact_X.parquet
#   - directorio particionado: fact_X.parquet/AAAA-MM.parquet (+ sin_fecha.parquet)
# El layout NO usa nombres hive (clave=valor), por lo que pd.read_parquet(dir)
# devuelve EXACTAMENTE las mismas columnas que el archivo único (sin columna
# sintética de partición): los lectores no cambian. pyarrow ignora archivos que
# empiezan con '.'/'_' al leer un directorio → el bucket de fechas nulas se llama
# 'sin_fecha' (sin guion bajo) y los temporales de escritura empiezan con '.'.
_PART_DATE_COLS = ('fecha_colombia', 'dateTime', 'Fecha')
_NULL_PARTITION = 'sin_fecha'


def read_facts(path: str) -> pd.DataFrame:
    """Lee un 'main file' de facts (archivo único o directorio particionado por
    mes) unificando el schema de forma PERMISIVA.

    Motivo: una partición mensual sin valores en una columna (p. ej. `event_id`
    en un mes sin eventos) se infiere tipo `null`, mientras otros meses la traen
    como `string`. `pd.read_parquet(dir)` intenta castear string→null y revienta
    con 'Unsupported cast from string to null'. `promote_options='permissive'`
    unifica null↔tipo concreto (y columnas faltantes) sin romper.

    Devuelve DataFrame vacío si el directorio no tiene parquets. Ignora archivos
    ocultos ('.'/'_'), igual que el layout de escritura."""
    if os.path.isdir(path):
        files = sorted(
            f
            for f in os.listdir(path)
            if f.endswith('.parquet') and not f.startswith(('.', '_'))
        )
        if not files:
            return pd.DataFrame()
        tables = [pq.read_table(os.path.join(path, f)) for f in files]
        return pa.concat_tables(tables, promote_options='permissive').to_pandas()
    return pd.read_parquet(path)


def _month_keys(df: pd.DataFrame):
    """(columna_usada, Series 'AAAA-MM') para particionar, o (None, None) si el
    DataFrame no tiene ninguna columna de fecha reconocible."""
    for col in _PART_DATE_COLS:
        if col in df.columns:
            s = pd.to_datetime(df[col], errors='coerce', utc=False)
            keys = s.dt.strftime('%Y-%m').where(s.notna(), _NULL_PARTITION)
            return col, keys
    return None, None


def _write_parquet_atomic(df: pd.DataFrame, file_path: str) -> None:
    """Escribe df a file_path de forma atómica (.tmp en el mismo dir → os.replace)."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    d, base = os.path.split(file_path)
    tmp_path = os.path.join(d, f'.{base}.tmp')
    df.to_parquet(tmp_path, index=False, compression='snappy')
    os.replace(tmp_path, file_path)


def _remove_staging(staging_paths: list) -> None:
    for path in staging_paths:
        try:
            os.remove(path)
        except Exception:
            pass


def _merge_to_single_file(
    df_new: pd.DataFrame, main_file: str, dedup_subset: Optional[list],
    keep: str = 'first',
) -> None:
    """Comportamiento legacy: lee el archivo completo, concatena, deduplica y
    reescribe entero. Fallback cuando los datos no tienen columna de fecha."""
    dfs = []
    if os.path.exists(main_file):
        try:
            dfs.append(pd.read_parquet(main_file))
        except Exception:
            logging.warning(f"No se pudo leer archivo principal: {main_file}")
    dfs.append(df_new)
    df_combined = pd.concat(dfs, ignore_index=True)
    if dedup_subset:
        df_combined = df_combined.drop_duplicates(subset=dedup_subset, keep=keep)
    _write_parquet_atomic(df_combined, main_file)
    logging.info(
        f"Parquet consolidado: {os.path.basename(main_file)} "
        f"({len(df_combined):,} filas totales)"
    )


def _migrate_file_to_dir(main_file: str) -> bool:
    """Convierte un archivo único legacy en directorio particionado por mes.
    Devuelve True si tras la llamada main_file es (o será) un directorio
    particionado; False si no se pudo particionar (sin columna de fecha) y debe
    seguir tratándose como archivo único."""
    if os.path.isdir(main_file):
        return True
    if not os.path.exists(main_file):
        return True  # aún no existe: el caller creará el directorio
    df_old = pd.read_parquet(main_file)
    col, keys = _month_keys(df_old)
    if col is None:
        return False
    tmp_dir = main_file + '.migrating'
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)
    for key, grp in df_old.groupby(keys):
        _write_parquet_atomic(grp, os.path.join(tmp_dir, f'{key}.parquet'))
    os.remove(main_file)
    os.replace(tmp_dir, main_file)
    logging.info(f"Migrado a particionado por mes: {os.path.basename(main_file)}")
    return True


def merge_staging_to_main(
    staging_paths: list,
    main_file: str,
    dedup_subset: Optional[list] = None,
    prefer_new: bool = False,
) -> None:
    """
    Consolida los staging del run en el main, particionando por mes (AAAA-MM):
    solo reescribe las particiones (meses) afectadas por los datos nuevos, no el
    histórico completo. Deduplica por dedup_subset DENTRO de cada partición — los
    row_id/* son estables por fecha, así que no hay colisiones entre meses, y la
    prioridad (existente > staging) se conserva. Migra el archivo único legacy a
    directorio la primera vez. Si los datos no traen columna de fecha, cae al
    comportamiento legacy de archivo único. Borra los staging al finalizar.

    `prefer_new=True` invierte la prioridad: gana la fila recién extraída. Es
    para hechos AGREGADOS (una fila por vehículo/día/banda, no por evento), donde
    volver a calcular un día debe corregir el valor anterior; con la prioridad
    normal un día calculado con datos incompletos quedaría congelado para siempre.
    """
    new_dfs = []
    for path in staging_paths:
        if os.path.exists(path):
            try:
                new_dfs.append(pd.read_parquet(path))
            except Exception:
                logging.warning(f"No se pudo leer staging: {path}")
    df_new = pd.concat(new_dfs, ignore_index=True) if new_dfs else pd.DataFrame()

    if df_new.empty:
        _remove_staging(staging_paths)
        return

    keep = 'last' if prefer_new else 'first'
    col, _ = _month_keys(df_new)
    if col is None or not _migrate_file_to_dir(main_file):
        _merge_to_single_file(df_new, main_file, dedup_subset, keep)
        _remove_staging(staging_paths)
        return

    os.makedirs(main_file, exist_ok=True)
    # Recalcula las claves sobre df_new ya concatenado (índice 0..n-1).
    _, keys = _month_keys(df_new)
    df_new = df_new.assign(_part=keys.values)
    total = 0
    for key, grp in df_new.groupby('_part'):
        grp = grp.drop(columns='_part')
        part_file = os.path.join(main_file, f'{key}.parquet')
        existing = pd.DataFrame()
        if os.path.exists(part_file):
            try:
                existing = pd.read_parquet(part_file)
            except Exception:
                existing = pd.DataFrame()
        combined = pd.concat([existing, grp], ignore_index=True)
        if dedup_subset:
            combined = combined.drop_duplicates(subset=dedup_subset, keep=keep)
        _write_parquet_atomic(combined, part_file)
        total += len(combined)
    logging.info(
        f"Parquet consolidado (particionado por mes): "
        f"{os.path.basename(main_file)} "
        f"({total:,} filas en {df_new['_part'].nunique()} partición(es) tocada(s))"
    )
    _remove_staging(staging_paths)


def clear_staging(facts_path: str, script_id: str) -> None:
    """Elimina archivos de staging del run anterior para este script."""
    staging_dir = os.path.join(facts_path, '_staging')
    if not os.path.exists(staging_dir):
        return
    prefix = f"{script_id}_"
    for fname in os.listdir(staging_dir):
        if fname.startswith(prefix):
            try:
                os.remove(os.path.join(staging_dir, fname))
            except Exception:
                pass
