"""Worker del ETL de reportes: atiende disparos manuales del Portal y,
opcionalmente, una corrida programada diaria.

Coordinación con el Portal (misma DB, `MASTER_DB_URL`):
  - Poll a `etl_trigger_request` cada `ETL_TRIGGER_POLL_SECONDS` (default 15s).
    El endpoint del portal inserta `pending`; acá se reclama con
    FOR UPDATE SKIP LOCKED y se corre el pipeline completo.
  - Cada corrida (manual o programada) queda registrada en `sync_run`
    (kind=reportes) con los deltas de filas por tabla `analytics.*`.
  - `master_state.run_lock` (advisory lock) evita corridas solapadas.

Schedule diario: `ETL_SCHEDULE_ENABLED=true` lo activa (default: APAGADO —
solo disparo manual). Intervalo en `ETL_SCHEDULE_INTERVAL_SECONDS` (default
86400 = diario).

Pipeline = mismo orden que la corrida manual documentada en `Orden ejecucion.md`:
  extract_dimensions → resto de extract_* → run_semantic_all (transform +
  validate + load a `analytics.*`).
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

import config
import master_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("etl-worker")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

POLL_SECONDS = float(os.environ.get("ETL_TRIGGER_POLL_SECONDS", "15"))
SCHEDULE_ENABLED = os.environ.get("ETL_SCHEDULE_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
}
SCHEDULE_INTERVAL_SECONDS = float(
    os.environ.get("ETL_SCHEDULE_INTERVAL_SECONDS", str(60 * 60 * 24))
)
FAULTS_REALTIME_ENABLED = os.environ.get(
    "ETL_FAULTS_REALTIME_ENABLED", "false"
).lower() in {"1", "true", "yes"}
FAULTS_REALTIME_INTERVAL_SECONDS = float(
    os.environ.get("ETL_FAULTS_INTERVAL_SECONDS", "300")
)
if FAULTS_REALTIME_INTERVAL_SECONDS <= 0:
    raise ValueError("ETL_FAULTS_INTERVAL_SECONDS debe ser mayor que cero")
ANALYTICS_SCHEMA = os.environ.get("ANALYTICS_DB_SCHEMA", "analytics")
STREAMING_DATASETS = {
    value.strip()
    for value in os.environ.get("ETL_STREAMING_DATASETS", "").split(",")
    if value.strip()
}
_UNKNOWN_STREAMING = STREAMING_DATASETS.difference({"factor_carga"})
if _UNKNOWN_STREAMING:
    raise ValueError(
        f"ETL_STREAMING_DATASETS contiene datasets no soportados: "
        f"{sorted(_UNKNOWN_STREAMING)}"
    )

# Orden del pipeline (relativo a _BASE_DIR). Dimensions SIEMPRE primero.
# Los dominios de ETL_DISABLED_DOMAINS quedan fuera de punta a punta (extract,
# transform y load): la lista sale de config para que los tres pasos no puedan
# divergir. Antes esta tupla estaba hardcodeada acá y el transform/load seguían
# procesando el dominio igual.
EXTRACT_STEPS = [
    "extract/extract_dimensions.py",
    "extract/extract_combustible.py",
    "extract/extract_habitos.py",
    # Solo consulta Geotab por vehículos de flotas con ralenti_analysis_enabled;
    # en las demás resuelve a cero dispositivos y sale enseguida. Escribe
    # directo a analytics.fact_ralenti_event (sin transform/load).
    "extract/extract_ralenti.py",
    "extract/extract_altimetria.py",
    "extract/extract_def.py",
    "extract/extract_ubicaciones.py",
    "extract/extract_pedal.py",
    # Solo hace trabajo real en flotas con range_mode='rpm'; en las demás resuelve
    # a cero dispositivos y sale enseguida.
    "extract/extract_rangos_rpm.py",
]
if not FAULTS_REALTIME_ENABLED:
    EXTRACT_STEPS.insert(3, "extract/extract_fallas.py")
if "factor_carga" not in STREAMING_DATASETS:
    EXTRACT_STEPS.insert(-1, "extract/extract_factor_carga.py")

DISABLED_EXTRACT_STEPS = config.DISABLED_EXTRACT_STEPS
EXTRACT_STEPS = [s for s in EXTRACT_STEPS if s not in DISABLED_EXTRACT_STEPS]

STREAM_STEPS = []
if "factor_carga" in STREAMING_DATASETS:
    STREAM_STEPS.append("streaming/factor_carga.py")

SEMANTIC_STEP = "run_semantic_all.py"

# Sin dimensiones no hay integridad referencial para ningún hecho, así que ese
# paso aborta la corrida. Los demás extractores alimentan un dominio cada uno:
# si uno falla, el resto de los reportes igual debe cargarse.
CRITICAL_STEPS = frozenset({"extract/extract_dimensions.py", SEMANTIC_STEP})

# Un paso colgado (Geotab sin responder, subproceso trabado) mantenía el worker
# bloqueado para siempre porque subprocess.run no tenía deadline.
STEP_TIMEOUT_SECONDS = float(os.environ.get("ETL_STEP_TIMEOUT_SECONDS", str(3 * 60 * 60)))

# Contrato con run_semantic_all.py: cargó el modelo, pero algún transform de
# dominio quedó fuera.
PARTIAL_EXIT_CODE = 3

# Tablas para el delta de filas que se reporta en sync_run.result.
FACT_TABLES = [
    "fact_combustible_daily",
    "fact_combustible_monthly",
    "fact_habito_event",
    "fact_fault_event",
    "fact_ralenti_event",
    "fact_pedal_reading",
    "fact_factor_carga_daily",
    "fact_altimetria_reading",
    "fact_def_daily",
    "fact_location_point",
]

_shutdown = False
_shutdown_event = threading.Event()
# El pipeline completo y FaultData comparten archivos silver y tablas analytics.
# El mutex cubre los dos hilos de ESTE worker; el advisory global cubre además
# otros contenedores/procesos. El orden obligatorio es local -> advisory.
_etl_execution_mutex = threading.Lock()


def _handle_signal(signum, _frame) -> None:
    global _shutdown
    log.info("señal %s recibida; termino tras la iteración actual", signum)
    _shutdown = True
    _shutdown_event.set()


def _run_faults_realtime_cycle() -> dict[str, int]:
    """Import tardío: el carril queda completamente apagado por defecto."""
    from streaming.fallas import run_cycle

    with _etl_execution_mutex:
        return run_cycle(stop_event=_shutdown_event)


def _faults_realtime_loop(
    stop_event: threading.Event,
    *,
    interval_seconds: float = FAULTS_REALTIME_INTERVAL_SECONDS,
) -> None:
    """Cadencia inicio-a-inicio; omite ticks vencidos sin hacer catch-up."""
    next_start = time.monotonic()
    while not stop_event.is_set():
        next_start += interval_seconds
        try:
            result = _run_faults_realtime_cycle()
            log.info(
                "fault_feed_cycle_done sources_ok=%d sources_failed=%d "
                "pages=%d facts=%d",
                result["sources_ok"],
                result["sources_failed"],
                result["pages"],
                result["fact_rows"],
            )
        except Exception as exc:
            # Nunca interpolar la excepción cruda: un error de proveedor puede
            # incluir parámetros de autenticación. El siguiente tick reintenta
            # desde el último token confirmado.
            log.error(
                "fault_feed_cycle_error code=%s",
                master_state.classify_extraction_error(exc),
            )
        now = time.monotonic()
        if now > next_start:
            missed = int((now - next_start) // interval_seconds) + 1
            next_start += missed * interval_seconds
        if stop_event.wait(max(0.0, next_start - now)):
            break


def _start_faults_realtime_worker() -> threading.Thread | None:
    if not FAULTS_REALTIME_ENABLED:
        return None
    thread = threading.Thread(
        target=_faults_realtime_loop,
        args=(_shutdown_event,),
        name="faultdata-getfeed",
        daemon=True,
    )
    thread.start()
    return thread


def _fact_counts(engine) -> dict[str, int]:
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in FACT_TABLES:
            try:
                counts[table] = int(
                    conn.execute(
                        text(f'SELECT count(*) FROM {ANALYTICS_SCHEMA}."{table}"')  # noqa: S608
                    ).scalar()
                    or 0
                )
            except Exception:
                # Tabla aún no creada (primer load): cuenta como 0.
                counts[table] = 0
    return counts


def _extraction_health(engine) -> dict[str, int]:
    """Salud de la extracción, para que una corrida degradada se note en la
    auditoría sin tener que leer los logs del worker.

    `vehicle_extraction_state` tiene una fila por (vehículo, dataset), así que
    contarla y llamar al resultado `vehiculos_*` engañaba dos veces:

    1. el número eran pares vehículo-dataset, no vehículos (16 vehículos × 8
       datasets = 128, que se leía como "128 vehículos");
    2. los dominios apagados a propósito (ETL_DISABLED_DOMAINS) nunca se
       extraen, así que sus filas quedan en `pending` para siempre y sumaban a
       un contador de "pendientes" que se lee como alarma.

    Se reporta entonces: vehículos DISTINTOS en alcance, el estado de los
    datasets que sí corren, y los apagados aparte como dato informativo.
    """
    disabled = tuple(sorted(config.DISABLED_DOMAINS))
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT s.dataset, s.status, count(*) "
                    "FROM vehicle_extraction_state s "
                    "JOIN vehicles v ON v.id = s.vehicle_id "
                    "JOIN fleets f ON f.id = v.fleet_id AND f.is_active "
                    "WHERE v.is_active AND v.geotab_device_id IS NOT NULL "
                    "GROUP BY s.dataset, s.status"
                )
            ).all()
            vehiculos = conn.execute(
                text(
                    "SELECT count(*) FROM vehicles v "
                    "JOIN fleets f ON f.id = v.fleet_id AND f.is_active "
                    "WHERE v.is_active AND v.geotab_device_id IS NOT NULL"
                )
            ).scalar()
    except Exception:
        return {}

    health = {"vehiculos_activos": int(vehiculos or 0), "datasets_apagados": 0}
    for dataset, status, count in rows:
        if dataset in disabled:
            health["datasets_apagados"] += int(count)
            continue
        health[f"datasets_{status}"] = health.get(f"datasets_{status}", 0) + int(count)
    return health


_MAX_TAIL_LINE_CHARS = 300
_MAX_TAIL_CHARS = 2000


def _error_tail(output: str | bytes | None) -> str:
    """Cola de salida legible: recorta cada línea ANTES de recortar el total.

    Una sola línea puede ser enorme (SQLAlchemy vuelca el lote de parámetros, un
    traceback puede traer un DataFrame entero). Recortando solo el total, esa
    línea se come la cola completa y el mensaje real —constraint, DETAIL, tipo de
    excepción— queda fuera del log y del error que ve el portal.
    """
    if isinstance(output, bytes):
        output = output.decode("utf-8", "replace")
    lines = []
    for line in (output or "").strip().splitlines():
        if len(line) > _MAX_TAIL_LINE_CHARS:
            line = f"{line[:_MAX_TAIL_LINE_CHARS]}… [línea de {len(line)} chars, recortada]"
        lines.append(line)
    return "\n".join(lines)[-_MAX_TAIL_CHARS:]


def _semantic_partial_summary(output: str) -> str:
    """Devuelve solo los módulos omitidos, nunca la salida cruda del proceso."""
    matches = re.findall(r"modelo semántico cargado sin: ([^\n]+)", output)
    if matches:
        return f"módulos semánticos omitidos: {matches[-1].strip()}"
    return "modelo semántico parcial"


def _run_step(rel_path: str) -> str | None:
    """Corre un paso como subproceso.

    Devuelve None si el paso terminó completo, o el detalle si terminó de forma
    parcial (`PARTIAL_EXIT_CODE`: hizo su trabajo pero algún dominio quedó
    fuera). Lanza RuntimeError si sale con otro código != 0 o si excede
    STEP_TIMEOUT_SECONDS."""
    log.info("paso: %s", rel_path)
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(_BASE_DIR, rel_path)],
            cwd=_BASE_DIR,
            capture_output=True,
            text=True,
            timeout=STEP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{rel_path} superó el límite de {int(STEP_TIMEOUT_SECONDS)}s y fue "
            f"cancelado: {_error_tail(exc.stderr or exc.stdout)}"
        ) from exc
    tail = _error_tail(proc.stderr or proc.stdout)
    if proc.returncode == PARTIAL_EXIT_CODE:
        summary = _semantic_partial_summary(tail)
        log.warning("paso %s terminó parcial: %s", rel_path, summary)
        return summary
    if proc.returncode != 0:
        raise RuntimeError(f"{rel_path} salió con código {proc.returncode}: {tail}")
    return None


def _record_sync_run(
    engine,
    *,
    trigger: str,
    status: str,
    started_at: datetime,
    finished_at: datetime,
    result: dict | None,
    error: str | None,
    actor_email: str | None,
) -> None:
    """Inserta la fila de auditoría en sync_run (misma DB del portal).

    Import json local: el resultado va como JSONB."""
    import json

    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO sync_run (id, kind, trigger, mode, status, started_at, "
                "finished_at, duration_ms, result, error, actor_email, created_at, "
                "updated_at) VALUES (:id, 'reportes', :trigger, 'incremental', "
                ":status, :started, :finished, :dur, CAST(:result AS jsonb), :error, "
                ":actor, :now, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "trigger": trigger,
                "status": status,
                "started": started_at,
                "finished": finished_at,
                "dur": duration_ms,
                "result": json.dumps(result) if result is not None else None,
                "error": (error or "")[:2000] or None,
                "actor": actor_email,
                "now": datetime.now(timezone.utc),
            },
        )


def run_pipeline(engine, *, trigger: str, actor_email: str | None = None) -> str:
    """Serializa el pipeline localmente antes de tomar el advisory global."""
    with _etl_execution_mutex:
        return _run_pipeline_serialized(
            engine,
            trigger=trigger,
            actor_email=actor_email,
        )


def _run_pipeline_serialized(
    engine,
    *,
    trigger: str,
    actor_email: str | None = None,
) -> str:
    """Una corrida completa del ETL con registro en sync_run.

    Devuelve el estado registrado: "success", "partial" (algún extractor no
    crítico falló pero el modelo semántico sí se cargó) o "error"."""
    started_at = datetime.now(timezone.utc)
    with master_state.run_lock() as acquired:
        if not acquired:
            _record_sync_run(
                engine,
                trigger=trigger,
                status="error",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                result=None,
                error="Otra corrida del ETL tiene el run_lock; se omitió esta.",
                actor_email=actor_email,
            )
            return "error"
        before = _fact_counts(engine)
        failed_steps: dict[str, str] = {}
        try:
            if DISABLED_EXTRACT_STEPS:
                log.info(
                    "dominios desactivados (sin Geotab, sin transform, sin load): %s",
                    ", ".join(sorted(config.DISABLED_DOMAINS)),
                )
            for step in [*EXTRACT_STEPS, *STREAM_STEPS]:
                try:
                    _run_step(step)
                except Exception as exc:
                    if step in CRITICAL_STEPS:
                        raise
                    # Un dominio caído no puede dejar sin reportes a los demás:
                    # el paso queda registrado y el pipeline continúa.
                    failed_steps[step] = master_state.classify_extraction_error(exc)
                    log.error(
                        "paso %s falló; sigo con el pipeline (%s)",
                        step,
                        failed_steps[step],
                    )
            partial = _run_step(SEMANTIC_STEP)
            if partial is not None:
                failed_steps[SEMANTIC_STEP] = partial
        except Exception as exc:
            log.exception("pipeline_error")
            _record_sync_run(
                engine,
                trigger=trigger,
                status="error",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                result={"pasos_fallidos": sorted(failed_steps)} if failed_steps else None,
                error=master_state.classify_extraction_error(exc),
                actor_email=actor_email,
            )
            raise
        after = _fact_counts(engine)
        # Delta neto de filas por tabla (updates in-place no cuentan; el load es
        # upsert incremental por hash, así que "0 nuevas" es una corrida válida).
        deltas = {t: after[t] - before.get(t, 0) for t in FACT_TABLES}
        result = {f"nuevas_{k.removeprefix('fact_')}": v for k, v in deltas.items()}
        extraction_health = _extraction_health(engine)
        result.update(extraction_health)
        state_errors = sum(
            extraction_health.get(key, 0)
            for key in ("datasets_error", "datasets_failed")
        )
        state_pending = extraction_health.get("datasets_pending", 0)
        if state_errors or state_pending:
            details = []
            if state_errors:
                details.append(f"{state_errors} estado(s) en error")
            if state_pending:
                details.append(f"{state_pending} estado(s) pendiente(s)")
            failed_steps["vehicle_extraction_state"] = ", ".join(details)
        status = "success"
        error = None
        if failed_steps:
            status = "partial"
            result["pasos_fallidos"] = sorted(failed_steps)
            error = "; ".join(f"{step}: {msg}" for step, msg in failed_steps.items())
        _record_sync_run(
            engine,
            trigger=trigger,
            status=status,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            result=result,
            error=error,
            actor_email=actor_email,
        )
        log.info("pipeline %s; deltas=%s", status, deltas)
        return status


def _claim_trigger(engine) -> dict | None:
    """Reclama la solicitud pendiente más vieja (SKIP LOCKED) o None."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "UPDATE etl_trigger_request SET status = 'running', "
                "started_at = :now, updated_at = :now WHERE id = ("
                "  SELECT id FROM etl_trigger_request WHERE status = 'pending' "
                "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                ") RETURNING id, requested_by_email"
            ),
            {"now": datetime.now(timezone.utc)},
        ).mappings().first()
    return dict(row) if row else None


def _close_trigger(engine, request_id, *, status: str, error: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE etl_trigger_request SET status = :st, finished_at = :now, "
                "error = :err, updated_at = :now WHERE id = :id"
            ),
            {
                "st": status,
                "err": (error or "")[:2000] or None,
                "now": datetime.now(timezone.utc),
                "id": request_id,
            },
        )


def _recover_stuck_triggers(engine) -> None:
    """Cierra los disparos que quedaron en `running` por un worker caído.

    Solo hay un worker y el `run_lock` es un advisory lock que muere con la
    sesión: si al arrancar existe algo en `running`, es basura de un crash y el
    portal lo seguiría mostrando como corrida en curso para siempre.
    """
    with engine.begin() as conn:
        stuck = conn.execute(
            text(
                "UPDATE etl_trigger_request SET status = 'error', "
                "finished_at = :now, updated_at = :now, "
                "error = 'El worker se reinició durante la corrida.' "
                "WHERE status = 'running' RETURNING id"
            ),
            {"now": datetime.now(timezone.utc)},
        ).scalars().all()
    if stuck:
        log.warning("disparos huérfanos cerrados al arrancar: %s", list(stuck))


def _run_claimed_trigger(engine, claimed: dict) -> None:
    """Ejecuta y cierra un disparo manual respetando el resultado del pipeline."""
    log.info(
        "solicitud manual %s (por %s)",
        claimed["id"],
        claimed.get("requested_by_email"),
    )
    try:
        pipeline_status = run_pipeline(
            engine,
            trigger="manual",
            actor_email=claimed.get("requested_by_email"),
        )
        trigger_status = "error" if pipeline_status == "error" else "done"
        _close_trigger(engine, claimed["id"], status=trigger_status)
    except Exception as exc:
        _close_trigger(
            engine,
            claimed["id"],
            status="error",
            error=master_state.classify_extraction_error(exc),
        )


def main() -> None:
    global _shutdown
    _shutdown = False
    _shutdown_event.clear()
    engine = master_state._engine()
    if engine is None:
        log.error("MASTER_DB_URL no configurada; el worker no puede coordinar. Salgo.")
        sys.exit(1)

    _recover_stuck_triggers(engine)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    faults_thread = _start_faults_realtime_worker()

    log.info(
        "etl-worker arriba: poll=%ss, schedule=%s (interval=%ss), "
        "fault_feed=%s (interval=%ss)",
        POLL_SECONDS,
        "ON" if SCHEDULE_ENABLED else "OFF",
        int(SCHEDULE_INTERVAL_SECONDS),
        "ON" if FAULTS_REALTIME_ENABLED else "OFF",
        int(FAULTS_REALTIME_INTERVAL_SECONDS),
    )

    last_scheduled = time.monotonic() if SCHEDULE_ENABLED else 0.0
    while not _shutdown:
        claimed = None
        try:
            claimed = _claim_trigger(engine)
            if claimed:
                _run_claimed_trigger(engine, claimed)
            elif (
                SCHEDULE_ENABLED
                and time.monotonic() - last_scheduled >= SCHEDULE_INTERVAL_SECONDS
            ):
                last_scheduled = time.monotonic()
                try:
                    run_pipeline(engine, trigger="worker")
                except Exception:
                    pass  # ya quedó en sync_run; el loop sigue vivo
        except Exception:
            log.exception("iteración con error; sigo")
        _shutdown_event.wait(POLL_SECONDS)

    _shutdown_event.set()
    if faults_thread is not None:
        faults_thread.join(timeout=5)
    log.info("etl-worker shutdown limpio")


if __name__ == "__main__":
    main()
