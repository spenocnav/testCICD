"""Ingestion and normalization of CloudFleet tracking observations.

The sidecar publishes an append-only tracking contract. This module is the
application boundary: it validates the bounded fields, derives controlled label
metadata, and inserts each logical observation at most once.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cloudfleet import CloudfleetTrackingEvent
from app.services.cloudfleet_sync_service import parse_cf_datetime

BOGOTA = ZoneInfo("America/Bogota")
TRACKING_COMMENT_MAX_LENGTH = 4000

CONTROLLED_LABELS: dict[int, str] = {
    1: "Pendiente de bahía",
    2: "Sin asignación de técnico",
    3: "En diagnóstico",
    4: "Pendiente informe técnico",
    5: "Pendiente de cotización",
    6: "Pendiente aprobación cliente",
    7: "Pendiente aprobación interna",
    8: "Pendiente de repuestos",
    9: "En intervención",
    10: "En pruebas finales",
}

LABEL_ALIASES = {"en intevencion": 9, "en intervencion": 9}


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.sub(r"\s+", " ", without_accents.strip().lower())


_LABEL_BY_TEXT = {_fold(name): label_id for label_id, name in CONTROLLED_LABELS.items()}
_LABEL_BY_TEXT.update({_fold(name): label_id for name, label_id in LABEL_ALIASES.items()})


def normalize_label(value: str | int | None) -> tuple[int, str] | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit() and int(text) in CONTROLLED_LABELS:
        label_id = int(text)
        return label_id, CONTROLLED_LABELS[label_id]
    label_id = _LABEL_BY_TEXT.get(_fold(text))
    return (label_id, CONTROLLED_LABELS[label_id]) if label_id else None


def parse_explicit_datetime(comment: str) -> datetime | None:
    """Parse ``dd/mm/yyyy HH:MM`` as a Bogotá-local explicit timestamp."""
    match = re.search(
        r"(?<!\d)(\d{2}[/-]\d{2}[/-]\d{4})\s+(\d{2}:\d{2})(?!\d)",
        comment,
    )
    if not match:
        return None
    date_text = match.group(1).replace("-", "/")
    try:
        local = datetime.strptime(
            f"{date_text} {match.group(2)}", "%d/%m/%Y %H:%M"
        ).replace(tzinfo=BOGOTA)
    except ValueError:
        return None
    return local.astimezone(UTC)


def parse_label_comment(comment: str | None) -> tuple[int, str, datetime | None] | None:
    """Parse a controlled label while retaining an optional explicit time."""
    if not comment:
        return None
    raw = " ".join(str(comment).strip().split())
    explicit_at = parse_explicit_datetime(raw)
    label_text = re.sub(
        r"\bfecha\s*:\s*\d{2}[/-]\d{2}[/-]\d{4}(?:\s+\d{2}:\d{2})?",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    label_text = re.sub(
        r"\b(?:fecha|at|date)\s*=\s*\d{2}[/-]\d{2}[/-]\d{4}(?:\s+\d{2}:\d{2})?",
        "",
        label_text,
        flags=re.IGNORECASE,
    )
    label_text = re.sub(
        r"(?<!\d)\d{2}[/-]\d{2}[/-]\d{4}(?:\s+\d{2}:\d{2})?(?!\d)",
        "",
        label_text,
    )
    label_text = re.sub(
        r"\b(etiqueta|label|estado)\s*:\s*",
        "",
        label_text,
        flags=re.IGNORECASE,
    )
    label_text = re.sub(
        r"\s+(?:fecha|el\s+d[ií]a|a\s+las)\s*$",
        "",
        label_text,
        flags=re.IGNORECASE,
    )
    label_text = re.sub(r"\s*[|;,:-]\s*$", "", label_text).strip()
    normalized = normalize_label(label_text)
    if not normalized:
        return None
    label_id, label_name = normalized
    return label_id, label_name, explicit_at


def _canonical_identifier(value: Any) -> str:
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]
    return text


def _nested_name(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("name", "code"):
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return str(candidate).strip()
        return None
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def _order_client(order: Mapping[str, Any]) -> str | None:
    return (
        _nested_name(order.get("client"))
        or _nested_name(order.get("primaryGroup"))
        or _nested_name(order.get("group1"))
        or _nested_name(order.get("costCenter"))
    )


def _order_cd(order: Mapping[str, Any]) -> str | None:
    return _nested_name(order.get("cd")) or _nested_name(order.get("costCenter")) or _nested_name(
        order.get("city")
    )


def _tracking_key(row: Mapping[str, Any], ordinal: int = 0) -> str:
    existing = row.get("tracking_key")
    if existing is not None and str(existing).strip():
        return str(existing).strip()[:128]
    tracking_id = row.get("tracking_id", row.get("trackingId"))
    if tracking_id is not None and str(tracking_id).strip():
        return f"id:{_canonical_identifier(tracking_id)}"[:128]
    stable = {
        "tracking_date": row.get("tracking_date", row.get("trackingDate")),
        "tracking_comment": row.get("tracking_comment", row.get("comment")),
        "created_by_id": row.get("created_by_id"),
        "ordinal": ordinal,
    }
    digest = hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _created_by(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    author = row.get("createdBy")
    if isinstance(author, Mapping):
        return (
            str(author["id"]) if author.get("id") is not None else None,
            str(author["name"])[:320] if author.get("name") is not None else None,
        )
    created_by_id = row.get("created_by_id")
    created_by_name = row.get("created_by_name")
    return (
        str(created_by_id) if created_by_id is not None else None,
        str(created_by_name)[:320] if created_by_name is not None else None,
    )


def normalize_tracking_row(
    row: Mapping[str, Any],
    *,
    order: Mapping[str, Any] | None = None,
    ordinal: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Convert one sidecar ledger row into the DB tracking contract."""
    order = order or {}
    extracted_at = parse_cf_datetime(row.get("extracted_at")) or now or datetime.now(UTC)
    observed_at = parse_cf_datetime(
        row.get("tracking_observed_at", row.get("observation_to"))
    )
    observed_from = parse_cf_datetime(
        row.get("tracking_observed_from", row.get("observation_from"))
    )
    tracking_date = parse_cf_datetime(row.get("tracking_date", row.get("trackingDate")))
    comment_raw = row.get("tracking_comment", row.get("comment"))
    comment = str(comment_raw)[:TRACKING_COMMENT_MAX_LENGTH] if comment_raw is not None else None
    parsed = parse_label_comment(comment)

    kind = row.get("tracking_observation_kind") or row.get("observation_kind") or ""
    kind = str(kind)[:64] or None
    is_live = bool(kind and kind.startswith("live")) or (not kind and observed_at is not None)
    window_minutes = (
        round((observed_at - observed_from).total_seconds() / 60.0, 2)
        if observed_at and observed_from
        else None
    )
    window_lost = observed_from is None or (
        window_minutes is not None and window_minutes > 5.0
    )
    stale_live = bool(
        is_live
        and window_lost
        and observed_at
        and tracking_date
        and tracking_date.astimezone(BOGOTA).date()
        < observed_at.astimezone(BOGOTA).date()
    )

    label_id: int | None = None
    label_name: str | None = None
    event_at: datetime | None = None
    event_at_source: str | None = None
    event_time_quality: str | None = None
    if parsed:
        label_id, label_name, explicit_at = parsed
        if explicit_at:
            event_at = explicit_at
            event_at_source = "comment_explicit"
            event_time_quality = "explicit"
        elif observed_at and is_live and not stale_live:
            event_at = observed_at
            event_at_source = "observation_window_end"
            event_time_quality = "live_window" if observed_from else "live_window_unbounded"
        elif tracking_date:
            event_at = tracking_date
            event_at_source = "tracking_date_baseline"
            event_time_quality = "historical_date"

    created_by_id, created_by_name = _created_by(row)
    work_order_number = row.get("work_order_number", order.get("number"))
    if work_order_number is None:
        raise ValueError("El tracking no tiene work_order_number")
    vehicle_code = row.get("vehicle_code") or order.get("vehicleCode") or order.get("vehicle_code")
    return {
        "work_order_number": int(work_order_number),
        "tracking_key": _tracking_key(row, ordinal),
        "tracking_id": (
            str(row.get("tracking_id", row.get("trackingId")))
            if row.get("tracking_id", row.get("trackingId")) is not None
            else None
        ),
        "tracking_date": tracking_date,
        "tracking_comment": comment,
        "created_by_id": created_by_id,
        "created_by_name": created_by_name,
        "vehicle_code": str(vehicle_code) if vehicle_code is not None else None,
        "client": row.get("client") or _order_client(order),
        "cd": row.get("cd") or _order_cd(order),
        "work_order_status": row.get("work_order_status") or order.get("status"),
        "work_order_type": row.get("work_order_type") or order.get("type"),
        "label_id": label_id,
        "label_name": label_name,
        "event_at": event_at,
        "event_at_source": event_at_source,
        "event_time_quality": event_time_quality,
        "tracking_observed_from": observed_from,
        "tracking_observed_at": observed_at,
        "tracking_observation_kind": kind,
        "observation_window_minutes": window_minutes,
        "file_count": max(0, min(int(row.get("file_count") or 0), 100)),
        "extracted_at": extracted_at,
    }


async def upsert_tracking_events(
    db: AsyncSession,
    rows: Sequence[Mapping[str, Any]],
    *,
    batch_size: int = 500,
) -> int:
    """Insert normalized observations without ever duplicating the ledger."""
    if not rows:
        return 0
    values = [dict(row) for row in rows]
    inserted = 0
    for start in range(0, len(values), batch_size):
        batch = values[start : start + batch_size]
        statement = pg_insert(CloudfleetTrackingEvent).values(batch)
        statement = statement.on_conflict_do_nothing(
            index_elements=["work_order_number", "tracking_key"]
        ).returning(CloudfleetTrackingEvent.id)
        # RETURNING, not ``rowcount``: asyncpg reports -1 for an INSERT without
        # it, so the counter silently stayed at zero on every cycle and could
        # not distinguish "already ingested" from "ingesting nothing". With
        # ON CONFLICT DO NOTHING only the rows actually written come back.
        result = await db.execute(statement)
        inserted += len(result.fetchall())
    return inserted
