"""Estado de extracción por (vehículo, dataset) leído/escrito en la DB del Portal.

Reemplaza el watermark global por dataset de `estado_ejecucion.json` por uno por
vehículo, para backfillear un vehículo recién registrado sin re-procesar a toda
la flota. Es el lado worker del plan; ver
`Navi-Portal-Clientes/Docs/PLAN_ESTADO_EJECUCION_POR_VEHICULO.md`.

Conexión igual que `config_source.py`: usa `MASTER_DB_URL`. Degrada a None si la
DB no está disponible para que los extractores caigan al watermark global
(`get_date_range_for_run`) sin romperse.
"""

from __future__ import annotations

import contextlib
import logging
import os
import zlib
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

log = logging.getLogger("master_state")

# Mismos script_id del estado_ejecucion.json / EXTRACTION_DATASETS del Portal.
DATASETS: tuple[str, ...] = (
    "analisis_combustible",
    "factor_carga",
    "posicion_pedal",
    "habito_seguro",
    "alertas",
    "altimetria",
    "consumo_def",
    "ubicaciones",
    "rangos_rpm",
    # Episodios de ralentí (extract_ralenti.py). Solo produce datos en flotas
    # con `ralenti_analysis_enabled`; el estado se siembra igual para todos.
    "ralenti",
)

# Estos códigos son deliberadamente estables y no contienen el texto de la
# excepción. Las excepciones de Geotab pueden incluir URLs, nombres de usuario
# u otros detalles que no deben persistirse en vehicle_extraction_state.
EXTRACTION_ERROR_CODES: frozenset[str] = frozenset(
    {
        "authentication_error",
        "network_error",
        "rate_limit_error",
        "permission_error",
        "api_request_error",
        "worker_error",
        "credentials_unavailable",
    }
)


def classify_extraction_error(error: BaseException | str) -> str:
    """Convierte una excepción de extracción en un código seguro y acotado."""
    error_type = type(error).__name__.casefold() if isinstance(error, BaseException) else ""
    message = str(error).casefold()
    haystack = f"{error_type} {message}"

    if any(
        token in haystack
        for token in (
            "sessionexpired",
            "authentication",
            "unauthorized",
            "invalid credentials",
            "login",
        )
    ):
        return "authentication_error"
    if any(
        token in haystack
        for token in ("overlimit", "rate limit", "too many requests", "429")
    ):
        return "rate_limit_error"
    if any(
        token in haystack
        for token in ("permission", "forbidden", "access denied", "403")
    ):
        return "permission_error"
    if any(
        token in haystack
        for token in (
            "timeout",
            "timed out",
            "connection",
            "ssl",
            "temporarily unavailable",
            "502",
            "503",
            "504",
        )
    ):
        return "network_error"
    return "api_request_error"


def safe_extraction_error_code(error: BaseException | str) -> str:
    """Conserva códigos ya normalizados y clasifica cualquier texto legado."""
    if isinstance(error, str) and error in EXTRACTION_ERROR_CODES:
        return error
    return classify_extraction_error(error)

# Margen de días ANTES del día de registro (vehicles.created_at). 0 = arranca el
# día que se registra; la historia previa se pide con "Reprocesar" en el portal.
_DEFAULT_LOOKBACK_DAYS = int(os.environ.get("EXTRACTION_DEFAULT_LOOKBACK_DAYS", "0"))

_ENGINE: Any = None
_ENGINE_TRIED = False


def _engine():
    """Engine sync (cacheado) hacia la DB del Portal, o None si no hay MASTER_DB_URL."""
    global _ENGINE, _ENGINE_TRIED
    if _ENGINE is not None:
        return _ENGINE
    if _ENGINE_TRIED:
        return None
    _ENGINE_TRIED = True
    url = os.environ.get("MASTER_DB_URL")
    if not url:
        return None
    try:
        from sqlalchemy import create_engine

        # hide_parameters: las sentencias de estado/credenciales no deben
        # aparecer con sus valores en el texto de una excepción.
        _ENGINE = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    except Exception as exc:  # pragma: no cover - degradación
        log.warning("master_state: no se pudo crear engine (%s)", exc)
        _ENGINE = None
    return _ENGINE


def get_vehicle_windows(dataset: str, to_date: datetime) -> Optional[list[dict[str, Any]]]:
    """Ventana `[from_date, to_date)` por vehículo extraíble para `dataset`.

    Devuelve list[dict] con `vehicle_id, database_name, device_id, plate,
    motor_type, from_date`. `from_date = COALESCE(watermark, backfill_from,
    created_at - lookback)`. Omite vehículos sin días nuevos (`from_date >=
    to_date`). Siembra el estado faltante. None si la DB no está disponible.

    Solo se extraen vehículos de flotas ACTIVAS (`fleets.is_active`): apagar una
    flota en el portal la saca de la UI, pero sus vehículos conservan
    `vehicles.is_active = true` uno por uno, así que sin este filtro el ETL
    seguía consultando Geotab por ellos. El watermark de una flota apagada queda
    congelado donde estaba; al reactivarla, la siguiente corrida retoma desde ahí
    (backfill del período apagado).
    """
    if dataset not in DATASETS:
        raise ValueError(f"dataset desconocido: {dataset}")
    engine = _engine()
    if engine is None:
        return None
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    params = {"ds": dataset, "lookback": _DEFAULT_LOOKBACK_DAYS, "to_date": to_date, "now": now}
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO vehicle_extraction_state "
                    "(id, vehicle_id, dataset, backfill_from, status, created_at, updated_at) "
                    "SELECT gen_random_uuid(), v.id, :ds, "
                    "v.created_at - make_interval(days => :lookback), 'pending', :now, :now "
                    "FROM vehicles v "
                    "JOIN fleets f ON f.id = v.fleet_id AND f.is_active "
                    "WHERE v.is_active AND v.geotab_device_id IS NOT NULL "
                    "ON CONFLICT (vehicle_id, dataset) DO NOTHING"
                ),
                params,
            )
            rows = (
                conn.execute(
                    text(
                        "SELECT v.id AS vehicle_id, v.fleet_id, d.database_name, "
                        "v.geotab_device_id AS device_id, v.plate, v.motor_type, "
                        "v.group_key, v.rpm_class, s.version, "
                        "COALESCE(s.watermark, s.backfill_from, "
                        "v.created_at - make_interval(days => :lookback)) AS from_date "
                        "FROM vehicles v "
                        "JOIN fleets f ON f.id = v.fleet_id AND f.is_active "
                        "JOIN geotab_databases d ON d.id = v.geotab_database_id "
                        "LEFT JOIN vehicle_extraction_state s "
                        "ON s.vehicle_id = v.id AND s.dataset = :ds "
                        "WHERE v.is_active AND v.geotab_device_id IS NOT NULL "
                        "AND COALESCE(s.watermark, s.backfill_from, "
                        "v.created_at - make_interval(days => :lookback)) < :to_date "
                        "ORDER BY d.database_name, v.plate"
                    ),
                    params,
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]
    except Exception as exc:  # pragma: no cover - degradación
        log.warning("master_state: fallo leyendo ventanas (%s); uso fallback", exc)
        return None


def advance_watermark(vehicle_id: str, dataset: str, to_date: datetime) -> None:
    """Avanza el watermark de un vehículo tras extraer OK hasta `to_date`."""
    engine = _engine()
    if engine is None:
        return
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE vehicle_extraction_state SET watermark = :to, status = 'ok', "
                "last_run_at = :now, last_error = NULL, updated_at = :now, "
                "version = version + 1 "
                "WHERE vehicle_id = :vid AND dataset = :ds"
            ),
            {"to": to_date, "now": now, "vid": vehicle_id, "ds": dataset},
        )


def mark_error(vehicle_id: str, dataset: str, error: str) -> None:
    """Marca un fallo de extracción (el watermark NO avanza)."""
    engine = _engine()
    if engine is None:
        return
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    safe_error = safe_extraction_error_code(error)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE vehicle_extraction_state SET status = 'error', last_error = :err, "
                "last_run_at = :now, updated_at = :now, version = version + 1 "
                "WHERE vehicle_id = :vid AND dataset = :ds"
            ),
            {"err": safe_error, "now": now, "vid": vehicle_id, "ds": dataset},
        )


@contextlib.contextmanager
def run_lock(name: str = "informes_etl") -> Iterator[bool]:
    """Lock de corrida (advisory) en la DB del Portal para evitar solapamiento de
    cron. Cede True si se obtuvo el lock (o si no hay DB → no bloquea), False si
    otra corrida lo tiene tomado (el caller debe omitir su ejecución)."""
    engine = _engine()
    if engine is None:
        yield True
        return
    from sqlalchemy import text

    key = zlib.crc32(name.encode("utf-8"))
    conn = engine.connect()
    try:
        got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar())
        if not got:
            yield False
            return
        try:
            yield True
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            conn.commit()
    finally:
        conn.close()
