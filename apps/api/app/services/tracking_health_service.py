"""Semáforo operativo del pipeline de etiquetas (worker sidecar + ingestor).

El estado se deriva **en el servidor** y no en el navegador: la UI solo pinta
el color que recibe. Así el criterio vive en un solo lugar y es testeable.

Fuente única: la fila `sync_state` que publica el ingestor en cada ciclo. El
API no lee el runtime privado del sidecar — eso lo prohíbe INTEGRATION.md y el
contenedor del API ni siquiera lo tiene montado.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cloudfleet_tracking_ingest_service import TRACKING_HEALTH_STATE_KEY

# El ingestor late cada 30 s por defecto. Tres ciclos perdidos ya es una caída,
# no una demora.
INGEST_HEARTBEAT_MAX_AGE_SECONDS = 180
# El worker publica salud una vez por ciclo (300 s). Se tolera un ciclo perdido.
WORKER_HEALTH_MAX_AGE_SECONDS = 900

# Razones del worker que NO degradan el semáforo porque son una decisión
# tomada, no una falla: se optó por arrancar el histórico desde cero en vez de
# gastar ~4 h de cuota en un bootstrap que no produce eventos de etiqueta.
# Se siguen publicando en `notes` para que queden a la vista.
ACCEPTED_WORKER_REASONS = frozenset(
    {"historical_bootstrap_required", "historical_bootstrap_incomplete"}
)

REASON_LABELS: dict[str, str] = {
    "active_never_polled": "Hay OTs activas que aún no se han sondeado por primera vez",
    "active_coverage_sla_breached": "Algunas OTs activas superaron su ventana de sondeo",
    "coverage_sla_infeasible": "La capacidad por ciclo no alcanza para el SLA configurado",
    "insufficient_cycle_capacity": "El ciclo no alcanza a cubrir todas las OTs activas",
    "changed_tracking_backlog": "Quedaron cambios detectados sin leer en este ciclo",
    "catalog_full_reconciliation_pending": "Falta una reconciliación completa del catálogo",
    "rotation_coverage_stale": "La rotación de cobertura se atrasó",
    "observation_windows_over_sla": "Ventanas de observación por encima del SLA",
    "invalid_ledger_lines": "El ledger trae líneas inválidas",
    "api_errors": "CloudFleet devolvió errores en el último ciclo",
    "historical_bootstrap_required": "Histórico sin bootstrap (decisión: arranque desde cero)",
    "historical_bootstrap_incomplete": "Bootstrap histórico a medias",
    "health_stale": "El archivo de salud del worker está vencido",
    "health_file_missing": "El worker todavía no publicó salud",
}


def _age_seconds(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max(0.0, (now - value).total_seconds())


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def build_status(
    row: dict[str, Any] | None,
    *,
    events_total: int,
    events_with_label: int,
    last_event_at: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    """Derive the traffic light from one published heartbeat row."""
    notes: list[str] = []

    if row is None:
        return {
            "status": "unknown",
            "headline": "El pipeline de etiquetas nunca reportó",
            "notes": ["No hay heartbeat: el ingestor no ha corrido todavía."],
            "ingest_ok": None,
            "ingest_age_seconds": None,
            "worker_status": None,
            "worker_age_seconds": None,
            "worker_reasons": [],
            "events_total": events_total,
            "events_with_label": events_with_label,
            "last_event_at": last_event_at,
            "active_orders": None,
            "tracking_backlog": None,
        }

    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    ingest = detail.get("ingest") if isinstance(detail.get("ingest"), dict) else {}
    worker = detail.get("worker") if isinstance(detail.get("worker"), dict) else None
    metrics = (worker or {}).get("metrics")
    metrics = metrics if isinstance(metrics, dict) else {}

    ingest_age = _age_seconds(_parse(row.get("watermark")), now)
    worker_age = _age_seconds(_parse((worker or {}).get("generatedAt")), now)
    ingest_ok = bool(ingest.get("ok"))
    raw_reasons = (worker or {}).get("reasons")
    reasons = [str(r) for r in raw_reasons] if isinstance(raw_reasons, list) else []
    blocking = [reason for reason in reasons if reason not in ACCEPTED_WORKER_REASONS]

    status = "ok"
    headline = "Registrando etiquetas con normalidad"

    if ingest_age is None or ingest_age > INGEST_HEARTBEAT_MAX_AGE_SECONDS:
        status = "down"
        headline = "El ingestor no está reportando"
        notes.append(
            "El último heartbeat es demasiado viejo; el contenedor "
            "tracking-ingest-worker puede estar caído."
        )
    elif not ingest_ok:
        status = "down"
        headline = "El ingestor está fallando"
        if ingest.get("error"):
            notes.append(str(ingest["error"]))
    elif worker is None:
        status = "degraded"
        headline = "El worker no publicó salud"
        notes.append("El ingestor corre, pero no encuentra el archivo de salud del worker.")
    elif worker_age is None or worker_age > WORKER_HEALTH_MAX_AGE_SECONDS:
        status = "degraded"
        headline = "El worker dejó de publicar salud"
        notes.append("Se ingiere lo ya descargado, pero puede no estar entrando nada nuevo.")
    elif blocking:
        status = "degraded"
        headline = "El worker reporta condiciones degradadas"

    notes.extend(REASON_LABELS.get(reason, reason) for reason in reasons)

    if status == "ok" and events_total and not events_with_label:
        # Todo el pipeline funciona; lo que falta es proceso humano. Merece un
        # aviso explícito, no un color rojo que culpe a la infraestructura.
        notes.append(
            "Se están registrando seguimientos, pero ninguno usa todavía el "
            "vocabulario de etiquetas controladas."
        )

    return {
        "status": status,
        "headline": headline,
        "notes": notes[:10],
        "ingest_ok": ingest_ok,
        "ingest_age_seconds": ingest_age,
        "worker_status": (worker or {}).get("status"),
        "worker_age_seconds": worker_age,
        "worker_reasons": reasons,
        "events_total": events_total,
        "events_with_label": events_with_label,
        "last_event_at": last_event_at,
        "active_orders": metrics.get("activeOrders"),
        "tracking_backlog": metrics.get("activeTrackingBacklog"),
    }


async def get_tracking_health(db: AsyncSession, *, now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    row = (
        (
            await db.execute(
                text("SELECT watermark, detail FROM sync_state WHERE key = :k"),
                {"k": TRACKING_HEALTH_STATE_KEY},
            )
        )
        .mappings()
        .first()
    )
    counts = (
        (
            await db.execute(
                text(
                    "SELECT count(*) AS total, "
                    "count(*) FILTER (WHERE label_id IS NOT NULL) AS with_label, "
                    "max(event_at) AS last_event_at "
                    "FROM cloudfleet_tracking_events"
                )
            )
        )
        .mappings()
        .one()
    )
    return build_status(
        dict(row) if row else None,
        events_total=int(counts["total"] or 0),
        events_with_label=int(counts["with_label"] or 0),
        last_event_at=counts["last_event_at"],
        now=moment,
    )
