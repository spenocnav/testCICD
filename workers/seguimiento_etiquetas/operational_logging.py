"""Safe JSON Lines operational logging for the tracking package."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, TextIO


LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
FORBIDDEN_FIELD_PARTS = {
    "api_key",
    "authorization",
    "bearer",
    "header",
    "url",
    "comment",
    "plate",
    "author",
    "payload",
    "files",
    "client",
    "vehicle_code",
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_level(value: str | None) -> str:
    level = str(value or "INFO").strip().upper()
    if level not in LEVELS:
        raise ValueError(f"Nivel de log invalido: {value!r}")
    return level


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (list, tuple, set)):
        return [item for item in (_safe_value(item) for item in value) if item is not None]
    # Unknown containers/objects are intentionally omitted rather than risking
    # accidental serialization of an API payload.
    return None


class OperationalLogger:
    """Small stdout logger with a denylist for sensitive field names."""

    def __init__(self, level: str | None = None, stream: TextIO | None = None) -> None:
        self.level = _safe_level(level or os.getenv("TRACKING_LOG_LEVEL", "INFO"))
        self.stream = stream or sys.stdout

    def enabled(self, level: str) -> bool:
        return LEVELS[_safe_level(level)] >= LEVELS[self.level]

    def log(self, event: str, *, level: str = "INFO", **fields: Any) -> None:
        normalized_level = _safe_level(level)
        if not self.enabled(normalized_level):
            return
        record: dict[str, Any] = {
            "timestamp": _utc_iso(),
            "level": normalized_level,
            "event": str(event),
        }
        for key, value in fields.items():
            normalized_key = str(key).strip()
            lowered = normalized_key.casefold()
            if any(part in lowered for part in FORBIDDEN_FIELD_PARTS):
                continue
            record[normalized_key] = _safe_value(value)
        self.stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.stream.flush()


def endpoint_category(path: str) -> tuple[str, int | None]:
    """Return a non-sensitive endpoint category and optional numeric OT."""
    clean = str(path).split("?", 1)[0].strip("/")
    parts = clean.split("/")
    if parts[:1] == ["work-orders"]:
        if len(parts) >= 3 and parts[2] == "tracking":
            try:
                return "work_order_tracking", int(parts[1])
            except (TypeError, ValueError):
                return "work_order_tracking", None
        if len(parts) >= 2:
            try:
                return "work_order_detail", int(parts[1])
            except (TypeError, ValueError):
                return "work_order_detail", None
        return "work_orders", None
    return "other", None
