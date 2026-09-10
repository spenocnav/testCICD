"""Build the deterministic, redacted historical seed shipped with the package."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PACKAGE_ROOT.parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from historical_seed import FORBIDDEN_BYTES  # noqa: E402
from label_config import parse_label_comment  # noqa: E402
from sync_order_catalog import sanitize_order  # noqa: E402


DEFAULT_TRACKING_DIR = (
    WORKSPACE_ROOT / "cloudfleet_kpi_package" / "data" / "cloudfleet_raw" / "work_order_tracking"
)
DEFAULT_ORDERS_DIR = (
    WORKSPACE_ROOT / "cloudfleet_kpi_package" / "data" / "cloudfleet_raw" / "work_orders"
)
DEFAULT_PROGRESS = WORKSPACE_ROOT / "prueba_actualizacion_api" / "output" / "_progress_all.json"
DEFAULT_OUTPUT = PACKAGE_ROOT / "seed"
TRACKING_FILE = "historical_tracking_seed.ndjson"
CATALOG_FILE = "order_catalog_seed.json"
MANIFEST_FILE = "historical_seed_manifest.json"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _descriptor(name: str, data: bytes, **counts: int) -> dict[str, Any]:
    return {
        "file": name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        **counts,
    }


def _validated_iso(value: Any, *, context: str) -> str:
    if value is None:
        raise RuntimeError(f"Timestamp ausente: {context}")
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"Timestamp invalido: {context}") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"Timestamp sin zona: {context}")
    return text


def build_seed(
    tracking_dir: Path,
    orders_dir: Path,
    progress_file: Path,
    output_dir: Path,
    *,
    snapshot_from: str = "2021-01-01",
    snapshot_through: str = "2026-06-25",
) -> dict[str, Any]:
    progress = json.loads(progress_file.read_text(encoding="utf-8-sig"))
    done = {int(value) for value in progress.get("done", [])}
    errors = progress.get("errors") or []
    last_run = str(progress.get("last_run") or "")
    if errors or not done or not last_run:
        raise RuntimeError("La evidencia _progress_all no certifica una extraccion completa")

    tracking_files = {
        int(path.stem): path
        for path in tracking_dir.glob("*.json")
        if path.stem.isdigit()
    }
    if set(tracking_files) != done:
        raise RuntimeError("Los JSON tracking no coinciden exactamente con progress.done")

    catalog_orders: dict[int, dict[str, Any]] = {}
    for path in sorted(orders_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list):
            raise RuntimeError(f"Catalogo raw invalido: {path.name}")
        for raw in payload:
            if not isinstance(raw, dict) or raw.get("number") is None:
                raise RuntimeError(f"Orden raw invalida: {path.name}")
            number = int(raw["number"])
            if number in catalog_orders:
                raise RuntimeError(f"OT duplicada en catalogo raw: {number}")
            catalog_orders[number] = sanitize_order(raw)
    if set(catalog_orders) != done:
        raise RuntimeError("Universo tracking no coincide con catalogo raw")

    seed_lines: list[bytes] = []
    work_orders: list[dict[str, Any]] = []
    tracking_rows = 0
    empty_orders = 0
    recognized_labels = 0
    redacted_text = 0
    identities: set[tuple[int, str]] = set()
    for number in sorted(done):
        payload = json.loads(tracking_files[number].read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list):
            raise RuntimeError(f"Tracking raw no es lista para OT {number}")
        empty_orders += int(not payload)
        work_orders.append(
            {"number": number, "trackingRows": len(payload), "empty": not payload}
        )
        for raw in payload:
            if not isinstance(raw, dict):
                raise RuntimeError(f"Fila tracking invalida para OT {number}")
            tracking_id = raw.get("id")
            if tracking_id is None or not str(tracking_id).strip():
                raise RuntimeError(f"tracking_id nulo para OT {number}")
            identity = (number, str(tracking_id))
            if identity in identities:
                raise RuntimeError(f"Tracking duplicado para OT {number}")
            identities.add(identity)
            comment = raw.get("comment")
            recognized_labels += int(parse_label_comment(comment) is not None)
            redacted_text += int(bool(str(comment or "").strip()))
            row = {
                "work_order_number": number,
                "tracking_id": tracking_id,
                "tracking_date": _validated_iso(
                    raw.get("trackingDate"), context=f"OT {number} tracking {tracking_id}"
                ),
                "tracking_comment": None,
                "tracking_observation_kind": "historical_certified_seed",
            }
            seed_lines.append(
                (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            )
            tracking_rows += 1

    tracking_bytes = b"".join(seed_lines)
    discovery_cursor = f"{snapshot_through}T00:00:00+00:00"
    catalog_confirmed_through = f"{snapshot_through}T23:59:59+00:00"
    catalog_payload = {
        "schemaVersion": 3,
        "generatedAt": last_run,
        "catalogScope": "historical_all_statuses",
        "catalogFullConfirmedAt": catalog_confirmed_through,
        "catalogSnapshotThrough": snapshot_through,
        "retentionPolicy": "indefinite_merge_only",
        "discoveryCursorAt": discovery_cursor,
        "lastIncrementalSyncAt": None,
        "lastFullReconciliationAt": last_run,
        "statusFilter": "all_statuses_certified_seed",
        "dateRange": {"from": snapshot_from, "to": snapshot_through},
        "pendingEnrichment": [],
        "orders": [catalog_orders[number] for number in sorted(catalog_orders)],
    }
    catalog_bytes = _json_bytes(catalog_payload)
    if FORBIDDEN_BYTES.search(tracking_bytes) or FORBIDDEN_BYTES.search(catalog_bytes):
        raise RuntimeError("El seed saneado contiene URL, header o credencial prohibida")

    universe_hash = hashlib.sha256(
        "\n".join(str(number) for number in sorted(done)).encode("ascii")
    ).hexdigest()
    status_counts = Counter(str(order.get("status") or "unknown") for order in catalog_orders.values())
    manifest = {
        "schemaVersion": 1,
        "snapshotComplete": True,
        "certifiedAt": last_run,
        "sourceSnapshotThrough": snapshot_through,
        "discoveryCursorAt": discovery_cursor,
        "sourceEvidence": {
            "done": len(done),
            "errors": 0,
            "lastRun": last_run,
            "workOrderUniverseSha256": universe_hash,
            "catalogRange": {"from": snapshot_from, "to": snapshot_through},
        },
        "trackingSeed": _descriptor(TRACKING_FILE, tracking_bytes, rows=tracking_rows),
        "orderCatalogSeed": _descriptor(CATALOG_FILE, catalog_bytes, orders=len(catalog_orders)),
        "totals": {
            "workOrders": len(done),
            "trackingRows": tracking_rows,
            "emptyWorkOrders": empty_orders,
            "recognizedLabelRows": recognized_labels,
            "redactedFreeTextRows": redacted_text,
        },
        "sanitization": {
            "trackingAllowlist": [
                "work_order_number",
                "tracking_id",
                "tracking_date",
                "tracking_comment",
                "tracking_observation_kind",
            ],
            "trackingComment": "redacted_to_null",
            "removed": ["authors", "attachments", "free_text", "request_metadata", "credentials"],
        },
        "catalogStatusCounts": dict(sorted(status_counts.items())),
        "workOrders": work_orders,
    }
    manifest_bytes = _json_bytes(manifest)
    if FORBIDDEN_BYTES.search(manifest_bytes):
        raise RuntimeError("El manifiesto contiene URL, header o credencial prohibida")

    _atomic_write(output_dir / TRACKING_FILE, tracking_bytes)
    _atomic_write(output_dir / CATALOG_FILE, catalog_bytes)
    _atomic_write(output_dir / MANIFEST_FILE, manifest_bytes)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracking-dir", type=Path, default=DEFAULT_TRACKING_DIR)
    parser.add_argument("--orders-dir", type=Path, default=DEFAULT_ORDERS_DIR)
    parser.add_argument("--progress-file", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--snapshot-from", default="2021-01-01")
    parser.add_argument("--snapshot-through", default="2026-06-25")
    args = parser.parse_args()
    manifest = build_seed(
        args.tracking_dir.resolve(),
        args.orders_dir.resolve(),
        args.progress_file.resolve(),
        args.output_dir.resolve(),
        snapshot_from=args.snapshot_from,
        snapshot_through=args.snapshot_through,
    )
    print(
        json.dumps(
            {"status": "ok", "totals": manifest["totals"], "output": str(args.output_dir.resolve())},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
