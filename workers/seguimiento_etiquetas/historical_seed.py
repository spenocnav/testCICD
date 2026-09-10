"""Verification for the portable, redacted historical tracking seed."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    from .sync_order_catalog import NAMED_ORDER_FIELDS, sanitize_order
except ImportError:  # pragma: no cover
    from sync_order_catalog import NAMED_ORDER_FIELDS, sanitize_order


HERE = Path(__file__).resolve().parent
DEFAULT_SEED_DIR = HERE / "seed"
MANIFEST_NAME = "historical_seed_manifest.json"
SEED_ROW_FIELDS = {
    "work_order_number",
    "tracking_id",
    "tracking_date",
    "tracking_comment",
    "tracking_observation_kind",
}
FORBIDDEN_BYTES = re.compile(
    rb"(?i)(?:authorization\s*[:=]\s*bearer|x-amz-signature|x-amz-security-token|"
    rb"x-goog-signature|fileurl|CLOUDFLEET_API_KEY\s*[:=]|https?://)"
)
MANIFEST_FIELDS = {
    "schemaVersion",
    "snapshotComplete",
    "certifiedAt",
    "sourceSnapshotThrough",
    "discoveryCursorAt",
    "sourceEvidence",
    "trackingSeed",
    "orderCatalogSeed",
    "totals",
    "sanitization",
    "catalogStatusCounts",
    "workOrders",
}
CATALOG_PAYLOAD_FIELDS = {
    "schemaVersion",
    "generatedAt",
    "catalogScope",
    "catalogFullConfirmedAt",
    "catalogSnapshotThrough",
    "retentionPolicy",
    "discoveryCursorAt",
    "lastIncrementalSyncAt",
    "lastFullReconciliationAt",
    "statusFilter",
    "dateRange",
    "pendingEnrichment",
    "orders",
}


class SeedValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CertifiedHistoricalSeed:
    directory: Path
    manifest: dict[str, Any]
    tracking_rows: list[dict[str, Any]]
    catalog_payload: dict[str, Any]
    signature: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _required_int(mapping: dict[str, Any], key: str, *, minimum: int = 0) -> int:
    if key not in mapping or isinstance(mapping[key], bool):
        raise SeedValidationError(f"Falta entero requerido: {key}")
    try:
        value = int(mapping[key])
    except (TypeError, ValueError) as exc:
        raise SeedValidationError(f"Entero invalido: {key}") from exc
    if value < minimum:
        raise SeedValidationError(f"Entero fuera de rango: {key}")
    return value


def _parse_iso(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SeedValidationError(f"Timestamp ISO invalido: {field}") from exc
    if parsed.tzinfo is None:
        raise SeedValidationError(f"Timestamp sin zona horaria: {field}")
    return parsed


def _seed_file(directory: Path, descriptor: dict[str, Any]) -> tuple[Path, bytes]:
    name = descriptor.get("file")
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise SeedValidationError("Nombre de archivo seed invalido")
    path = directory / name
    if not path.is_file():
        raise SeedValidationError(f"Falta archivo seed: {name}")
    data = path.read_bytes()
    if len(data) != _required_int(descriptor, "bytes"):
        raise SeedValidationError(f"Tamano no coincide para {name}")
    if _sha256(data) != descriptor.get("sha256"):
        raise SeedValidationError(f"SHA-256 no coincide para {name}")
    if FORBIDDEN_BYTES.search(data):
        raise SeedValidationError(f"Contenido sensible/URL detectado en {name}")
    return path, data


def load_certified_seed(seed_dir: str | Path | None = None) -> CertifiedHistoricalSeed:
    directory = Path(seed_dir or DEFAULT_SEED_DIR).expanduser().resolve()
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SeedValidationError(f"Falta manifiesto certificado: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    if FORBIDDEN_BYTES.search(manifest_bytes):
        raise SeedValidationError("Contenido sensible/URL detectado en manifiesto")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeedValidationError("Manifiesto seed invalido") from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != MANIFEST_FIELDS
        or manifest.get("schemaVersion") != 1
    ):
        raise SeedValidationError("schemaVersion de seed no soportada")
    if manifest.get("snapshotComplete") is not True:
        raise SeedValidationError("El manifiesto no certifica snapshot completo")
    evidence = manifest.get("sourceEvidence") or {}
    totals = manifest.get("totals") or {}
    if not isinstance(evidence, dict) or set(evidence) != {
        "done", "errors", "lastRun", "workOrderUniverseSha256", "catalogRange"
    }:
        raise SeedValidationError("Evidencia fuente fuera de contrato")
    if not isinstance(totals, dict) or set(totals) != {
        "workOrders", "trackingRows", "emptyWorkOrders", "recognizedLabelRows", "redactedFreeTextRows"
    }:
        raise SeedValidationError("Totales fuera de contrato")
    if _required_int(evidence, "errors") != 0:
        raise SeedValidationError("La extraccion fuente reporta errores")
    if _required_int(evidence, "done", minimum=1) != _required_int(
        totals, "workOrders", minimum=1
    ):
        raise SeedValidationError("Cobertura done no coincide con universo de OTs")
    _parse_iso(evidence.get("lastRun"), "sourceEvidence.lastRun")
    _parse_iso(manifest.get("certifiedAt"), "certifiedAt")
    _parse_iso(manifest.get("discoveryCursorAt"), "discoveryCursorAt")
    catalog_range = evidence.get("catalogRange")
    if not isinstance(catalog_range, dict) or set(catalog_range) != {"from", "to"}:
        raise SeedValidationError("Rango fuente fuera de contrato")
    try:
        range_from = date.fromisoformat(str(catalog_range["from"]))
        range_to = date.fromisoformat(str(catalog_range["to"]))
        snapshot_through = date.fromisoformat(str(manifest.get("sourceSnapshotThrough")))
    except ValueError as exc:
        raise SeedValidationError("Fecha de corte seed invalida") from exc
    if range_from > range_to or snapshot_through != range_to:
        raise SeedValidationError("Corte seed no coincide con evidencia fuente")
    sanitization = manifest.get("sanitization")
    if not isinstance(sanitization, dict) or set(sanitization) != {
        "trackingAllowlist", "trackingComment", "removed"
    }:
        raise SeedValidationError("Politica de saneamiento fuera de contrato")
    allowlist = sanitization.get("trackingAllowlist")
    if not isinstance(allowlist, list) or len(allowlist) != len(SEED_ROW_FIELDS) or set(allowlist) != SEED_ROW_FIELDS:
        raise SeedValidationError("Allowlist declarada no coincide")
    if sanitization.get("trackingComment") != "redacted_to_null":
        raise SeedValidationError("Politica de comentario seed invalida")
    if set(sanitization.get("removed") or []) != {
        "authors", "attachments", "free_text", "request_metadata", "credentials"
    }:
        raise SeedValidationError("Politica de campos removidos invalida")

    tracking_descriptor = manifest.get("trackingSeed") or {}
    if not isinstance(tracking_descriptor, dict) or set(tracking_descriptor) != {
        "file", "sha256", "bytes", "rows"
    }:
        raise SeedValidationError("Descriptor tracking fuera de contrato")
    _, tracking_bytes = _seed_file(directory, tracking_descriptor)
    tracking_rows: list[dict[str, Any]] = []
    counts: dict[int, int] = {}
    identities: set[tuple[int, str]] = set()
    for line_number, raw_line in enumerate(tracking_bytes.splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise SeedValidationError(f"NDJSON invalido en linea {line_number}") from exc
        if not isinstance(row, dict) or set(row) != SEED_ROW_FIELDS:
            raise SeedValidationError(f"Allowlist invalida en seed, linea {line_number}")
        try:
            number = int(row["work_order_number"])
        except (TypeError, ValueError) as exc:
            raise SeedValidationError(f"OT invalida en linea {line_number}") from exc
        tracking_id = row.get("tracking_id")
        if tracking_id is None or not str(tracking_id).strip():
            raise SeedValidationError(f"tracking_id nulo en linea {line_number}")
        if row.get("tracking_comment") is not None:
            raise SeedValidationError("El seed distribuible debe redactar texto libre")
        if row.get("tracking_observation_kind") != "historical_certified_seed":
            raise SeedValidationError("Tipo de observacion seed invalido")
        _parse_iso(row.get("tracking_date"), f"tracking_date linea {line_number}")
        identity = (number, str(tracking_id))
        if identity in identities:
            raise SeedValidationError(f"Tracking duplicado en OT {number}")
        identities.add(identity)
        counts[number] = counts.get(number, 0) + 1
        tracking_rows.append(row)
    if len(tracking_rows) != _required_int(tracking_descriptor, "rows"):
        raise SeedValidationError("Conteo de filas tracking no coincide")

    catalog_descriptor = manifest.get("orderCatalogSeed") or {}
    if not isinstance(catalog_descriptor, dict) or set(catalog_descriptor) != {
        "file", "sha256", "bytes", "orders"
    }:
        raise SeedValidationError("Descriptor catalogo fuera de contrato")
    _, catalog_bytes = _seed_file(directory, catalog_descriptor)
    try:
        catalog_payload = json.loads(catalog_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeedValidationError("Catalogo seed invalido") from exc
    if (
        not isinstance(catalog_payload, dict)
        or set(catalog_payload) != CATALOG_PAYLOAD_FIELDS
        or not isinstance(catalog_payload.get("orders"), list)
    ):
        raise SeedValidationError("Contrato de catalogo seed invalido")
    catalog_numbers: set[int] = set()
    for raw in catalog_payload["orders"]:
        if not isinstance(raw, dict) or sanitize_order(raw) != raw:
            raise SeedValidationError("Catalogo contiene campos fuera de allowlist")
        for field in NAMED_ORDER_FIELDS & set(raw):
            value = raw[field]
            if not isinstance(value, dict) or not set(value) <= {"id", "code", "name"}:
                raise SeedValidationError("Campo named invalido en catalogo seed")
        try:
            number = int(raw["number"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SeedValidationError("Numero de OT invalido en catalogo") from exc
        if number in catalog_numbers:
            raise SeedValidationError(f"OT duplicada en catalogo: {number}")
        catalog_numbers.add(number)

    work_orders = manifest.get("workOrders")
    if not isinstance(work_orders, list):
        raise SeedValidationError("Falta cobertura por OT en manifiesto")
    manifest_counts: dict[int, int] = {}
    empty_count = 0
    for item in work_orders:
        if not isinstance(item, dict) or set(item) != {"number", "trackingRows", "empty"}:
            raise SeedValidationError("Cobertura por OT invalida")
        number = _required_int(item, "number", minimum=1)
        row_count = _required_int(item, "trackingRows")
        if number in manifest_counts or bool(item.get("empty")) != (row_count == 0):
            raise SeedValidationError("Cobertura/empty inconsistente")
        manifest_counts[number] = row_count
        empty_count += int(row_count == 0)
    universe = set(manifest_counts)
    if universe != catalog_numbers or universe != set(counts) | {
        number for number, count in manifest_counts.items() if count == 0
    }:
        raise SeedValidationError("Universos de OT no coinciden")
    if any(counts.get(number, 0) != expected for number, expected in manifest_counts.items()):
        raise SeedValidationError("Conteo tracking por OT no coincide")
    if len(universe) != _required_int(totals, "workOrders", minimum=1):
        raise SeedValidationError("Total de OTs no coincide")
    if len(tracking_rows) != _required_int(totals, "trackingRows"):
        raise SeedValidationError("Total tracking no coincide")
    if empty_count != _required_int(totals, "emptyWorkOrders"):
        raise SeedValidationError("Total de OTs vacias no coincide")
    if len(catalog_numbers) != _required_int(catalog_descriptor, "orders", minimum=1):
        raise SeedValidationError("Total de catalogo no coincide")
    universe_hash = hashlib.sha256(
        "\n".join(str(number) for number in sorted(universe)).encode("ascii")
    ).hexdigest()
    if evidence.get("workOrderUniverseSha256") != universe_hash:
        raise SeedValidationError("Hash del universo de OTs no coincide")
    if (
        catalog_payload.get("catalogSnapshotThrough") != str(snapshot_through)
        or catalog_payload.get("dateRange") != {
            "from": str(range_from),
            "to": str(range_to),
        }
        or catalog_payload.get("discoveryCursorAt") != manifest.get("discoveryCursorAt")
    ):
        raise SeedValidationError("Corte/cursor del catalogo no coincide con manifiesto")
    status_counts: dict[str, int] = {}
    for order in catalog_payload["orders"]:
        status = str(order.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    if manifest.get("catalogStatusCounts") != dict(sorted(status_counts.items())):
        raise SeedValidationError("Conteos de estado del catalogo no coinciden")
    _required_int(totals, "recognizedLabelRows")
    _required_int(totals, "redactedFreeTextRows")

    signature = _sha256(
        (manifest_bytes + tracking_descriptor["sha256"].encode() + catalog_descriptor["sha256"].encode())
    )
    return CertifiedHistoricalSeed(
        directory=directory,
        manifest=manifest,
        tracking_rows=tracking_rows,
        catalog_payload=catalog_payload,
        signature=signature,
    )
