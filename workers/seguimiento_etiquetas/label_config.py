"""Catalog and parser for the controlled work-order label vocabulary."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo


BOGOTA = ZoneInfo("America/Bogota")

LABELS: dict[int, str] = {
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

_ALIASES = {
    "en intevencion": 9,
    "en intervencion": 9,
}


def fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", value.strip().lower())


LABEL_BY_FOLDED_TEXT = {fold(name): label_id for label_id, name in LABELS.items()}
LABEL_BY_FOLDED_TEXT.update({fold(name): label_id for name, label_id in _ALIASES.items()})


def normalize_label(value: str | int | None) -> tuple[int, str] | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit() and int(text) in LABELS:
        label_id = int(text)
        return label_id, LABELS[label_id]
    label_id = LABEL_BY_FOLDED_TEXT.get(fold(text))
    return (label_id, LABELS[label_id]) if label_id else None


def parse_explicit_datetime(comment: str) -> tuple[datetime | None, str | None]:
    """Parse a Colombia-local date, treating it as precise only with HH:MM.

    A date without a time is useful display metadata, but it must never become
    an invented midnight. Live events then use the observation-window end and
    historical baselines retain their date-only source.
    """
    match = re.search(
        r"(?<!\d)(\d{2}[/-]\d{2}[/-]\d{4})(?:\s+(\d{2}:\d{2}))?(?!\d)",
        comment,
    )
    if not match:
        return None, None
    date_text = match.group(1).replace("-", "/")
    time_text = match.group(2)
    if not time_text:
        return None, date_text
    try:
        local = datetime.strptime(f"{date_text} {time_text}", "%d/%m/%Y %H:%M").replace(tzinfo=BOGOTA)
    except ValueError:
        return None, None
    return local.astimezone(ZoneInfo("UTC")), f"{date_text} {time_text}"


def parse_label_comment(comment: str | None) -> tuple[int, str, datetime | None, str | None] | None:
    """Parse a controlled tracking comment.

    Accepted forms:
      3
      En diagnostico
      Etiqueta: 3
      Etiqueta: En diagnostico Fecha: 05/08/2026 14:30
      9 05/08/2026 14:30
    """
    if not comment:
        return None
    raw = " ".join(str(comment).strip().split())
    explicit_at, explicit_display = parse_explicit_datetime(raw)
    label_text = re.sub(r"\bfecha\s*:\s*\d{2}[/-]\d{2}[/-]\d{4}(?:\s+\d{2}:\d{2})?", "", raw, flags=re.IGNORECASE)
    label_text = re.sub(r"\b(?:fecha|at|date)\s*=\s*\d{2}[/-]\d{2}[/-]\d{4}(?:\s+\d{2}:\d{2})?", "", label_text, flags=re.IGNORECASE)
    label_text = re.sub(r"(?<!\d)\d{2}[/-]\d{2}[/-]\d{4}(?:\s+\d{2}:\d{2})?(?!\d)", "", label_text)
    label_text = re.sub(r"\s+(?:fecha|el\s+d[ií]a|a\s+las)\s*$", "", label_text, flags=re.IGNORECASE)
    label_text = re.sub(r"\b(etiqueta|label|estado)\s*:\s*", "", label_text, flags=re.IGNORECASE)
    label_text = re.sub(r"\s*[|;,:-]\s*$", "", label_text).strip()
    normalized = normalize_label(label_text)
    if not normalized:
        return None
    label_id, label_name = normalized
    return label_id, label_name, explicit_at, explicit_display


def display_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(BOGOTA).strftime("%d/%m/%Y %H:%M")
