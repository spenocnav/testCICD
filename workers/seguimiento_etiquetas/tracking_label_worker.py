"""Production worker for Cloudfleet work-order tracking labels.

Stable integration APIs:

* :func:`run_once` executes one discovery/tracking/publication transaction.
* :func:`build_dashboard` converts the durable ledger into the UI contract.
* :class:`WorkerPaths` lets a host application isolate runtime persistence.

Nothing performs network or filesystem writes merely by importing this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

try:  # Package import when embedded; fallback keeps direct CLI execution.
    from .build_label_history import (
        build_events,
        build_transitions,
        normalize_identifier,
        stable_identifier_sort_key,
    )
    from .cloudfleet_api import ClientConfig, CloudfleetClient, CloudfleetClientProtocol, load_environment
    from .historical_seed import DEFAULT_SEED_DIR, SeedValidationError, load_certified_seed
    from .label_config import LABELS, normalize_label
    from .operational_logging import OperationalLogger
    from .production_io import (
        InstanceLock,
        append_json_lines,
        atomic_write_json,
        atomic_write_text,
        read_json,
        read_json_lines,
    )
    from .sync_order_catalog import sanitize_order, sync
except ImportError:  # pragma: no cover
    from build_label_history import (
        build_events,
        build_transitions,
        normalize_identifier,
        stable_identifier_sort_key,
    )
    from cloudfleet_api import ClientConfig, CloudfleetClient, CloudfleetClientProtocol, load_environment
    from historical_seed import DEFAULT_SEED_DIR, SeedValidationError, load_certified_seed
    from label_config import LABELS, normalize_label
    from operational_logging import OperationalLogger
    from production_io import (
        InstanceLock,
        append_json_lines,
        atomic_write_json,
        atomic_write_text,
        read_json,
        read_json_lines,
    )
    from sync_order_catalog import sanitize_order, sync


HERE = Path(__file__).resolve().parent
BOGOTA = ZoneInfo("America/Bogota")
DEFAULT_SEED_PARQUET = HERE / "output" / "tracking_all.parquet"
ACTIVE_STATUSES_DEFAULT = "opened,onTechnicalCompletion"
FULL_CATALOG_STATUSES = "opened,onTechnicalCompletion,closed,voided"
DEFAULT_MAX_OTS_PER_CYCLE = 60
DEFAULT_COVERAGE_SLA_SECONDS = 1800
DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 2.5

LEDGER_FIELDS = {
    "work_order_number",
    "vehicle_code",
    "vehicle_type",
    "vehicle_brand",
    "vehicle_line",
    "client",
    "cd",
    "work_order_status",
    "work_order_type",
    "work_order_updated_at",
    "tracking_id",
    "tracking_key",
    "tracking_date",
    "tracking_comment",
    "created_by_id",
    "created_by_name",
    "file_count",
    "tracking_observed_at",
    "tracking_observed_from",
    "tracking_observation_kind",
    "extracted_at",
}


@dataclass(frozen=True)
class WorkerPaths:
    runtime: Path
    state: Path
    catalog: Path
    ledger: Path
    dashboard: Path
    health: Path
    lock: Path

    @classmethod
    def from_runtime(cls, runtime: str | Path | None = None) -> "WorkerPaths":
        selected = runtime or os.getenv("TRACKING_RUNTIME_DIR") or (HERE / "runtime")
        root = Path(selected).expanduser().resolve()
        return cls(
            runtime=root,
            state=root / "tracking_worker_state.json",
            catalog=root / "order_catalog.json",
            ledger=root / "tracking_ledger.jsonl",
            dashboard=root / "label_dashboard.json",
            health=root / "worker_health.json",
            lock=root / "tracking_worker.lock",
        )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or now_utc()).astimezone(timezone.utc).isoformat()


def parse_utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def nested_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name")
    return value if isinstance(value, str) else None


def client_from_record(record: dict[str, Any]) -> str:
    return (
        nested_name(record.get("primaryGroup"))
        or nested_name(record.get("group1"))
        or nested_name(record.get("costCenter"))
        or "Sin cliente"
    )


def cd_from_record(record: dict[str, Any]) -> str:
    return nested_name(record.get("costCenter")) or nested_name(record.get("city")) or "Sin CD"


def load_state(paths: WorkerPaths) -> dict[str, Any]:
    state = read_json(paths.state, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("schemaVersion", 2)
    state.setdefault("last_run_at", None)
    state.setdefault("last_successful_cycle_at", None)
    state.setdefault("consecutive_failed_cycles", 0)
    state.setdefault("ot", {})
    state.setdefault("stats", {})
    state.setdefault("seed", {})
    state.setdefault("scheduler", {})
    return state


def save_state(paths: WorkerPaths, state: dict[str, Any]) -> None:
    atomic_write_json(paths.state, state)


def load_catalog(paths: WorkerPaths) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    payload = read_json(paths.catalog, {})
    payload = payload if isinstance(payload, dict) else {}
    orders: dict[int, dict[str, Any]] = {}
    for raw in payload.get("orders", []):
        if not isinstance(raw, dict) or raw.get("number") is None:
            continue
        try:
            orders[int(raw["number"])] = sanitize_order(raw)
        except (TypeError, ValueError):
            continue
    return orders, payload


def sanitize_ledger_row(row: dict[str, Any]) -> dict[str, Any]:
    cleaned = {key: row.get(key) for key in LEDGER_FIELDS if key in row}
    cleaned["tracking_key"] = tracking_identity(cleaned)
    kind = cleaned.get("tracking_observation_kind") or row.get("observation_kind")
    if kind:
        cleaned["tracking_observation_kind"] = kind
    return cleaned


def read_ledger(paths: WorkerPaths) -> tuple[list[dict[str, Any]], int, bool]:
    raw_rows, rejected = read_json_lines(paths.ledger)
    requires_compaction = rejected > 0 or any(
        set(row) - LEDGER_FIELDS or "tracking_key" not in row for row in raw_rows
    )
    return [sanitize_ledger_row(row) for row in _assign_synthetic_keys(raw_rows)], rejected, requires_compaction


def _assign_synthetic_keys(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give repeated null-ID records deterministic per-payload ordinals."""
    counts: dict[str, int] = {}
    prepared: list[dict[str, Any]] = []
    for original in rows:
        row = dict(original)
        if row.get("tracking_id") is None:
            canonical = json.dumps(
                {
                    "work_order_number": _canonical_identifier(row.get("work_order_number")),
                    "tracking_date": row.get("tracking_date"),
                    "tracking_comment": row.get("tracking_comment"),
                    "created_by_id": row.get("created_by_id"),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            ordinal = counts.get(canonical, 0)
            counts[canonical] = ordinal + 1
            if not row.get("tracking_key"):
                row["synthetic_ordinal"] = ordinal
                row["tracking_key"] = tracking_identity(row)
                row.pop("synthetic_ordinal", None)
        prepared.append(row)
    return prepared


def compact_ledger(paths: WorkerPaths, rows: list[dict[str, Any]]) -> None:
    """Atomically remove legacy URL/blob/redundant fields from the ledger."""
    text = "".join(
        json.dumps(sanitize_ledger_row(row), ensure_ascii=False, separators=(",", ":"), default=str)
        + "\n"
        for row in rows
    )
    # A legacy backup would retain signed URLs and defeat the security cleanup.
    atomic_write_text(paths.ledger, text, backup=False)


def tracking_identity(row: dict[str, Any]) -> str:
    if row.get("tracking_key"):
        return str(row["tracking_key"])
    tracking_id = row.get("tracking_id")
    if tracking_id is not None and str(tracking_id).strip():
        return f"id:{_canonical_identifier(tracking_id)}"
    stable = {
        "tracking_date": row.get("tracking_date"),
        "tracking_comment": row.get("tracking_comment"),
        "created_by_id": row.get("created_by_id"),
        "synthetic_ordinal": row.get("synthetic_ordinal", 0),
    }
    digest = hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def ledger_identity(row: dict[str, Any]) -> tuple[str, str]:
    return _canonical_identifier(row.get("work_order_number")), tracking_identity(row)


def _canonical_identifier(value: Any) -> str:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    text = str(value)
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]
    return text


def append_ledger(
    paths: WorkerPaths,
    rows: Iterable[dict[str, Any]],
    existing_keys: set[tuple[str, str]],
) -> int:
    pending: list[dict[str, Any]] = []
    for raw in rows:
        row = sanitize_ledger_row(raw)
        key = ledger_identity(row)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        pending.append(row)
    return append_json_lines(paths.ledger, pending)


def seed_ledger_from_parquet(
    paths: WorkerPaths,
    state: dict[str, Any],
    seed_path: Path | None,
    existing_rows: list[dict[str, Any]],
) -> int:
    if seed_path is None or not seed_path.is_file():
        return 0
    signature = f"{seed_path.resolve()}:{seed_path.stat().st_size}:{seed_path.stat().st_mtime_ns}"
    if state.get("seed", {}).get("signature") == signature:
        return 0
    try:
        frame = pd.read_parquet(seed_path)
    except Exception as exc:
        state["seed"] = {"signature": signature, "status": "error", "error": str(exc)[:300]}
        return 0
    existing_keys = {ledger_identity(row) for row in existing_rows}
    raw_rows = frame.where(pd.notna(frame), None).to_dict("records")
    prepared: list[dict[str, Any]] = []
    for raw in raw_rows:
        row = dict(raw)
        row["tracking_observation_kind"] = "historical_seed"
        row["tracking_observed_at"] = None
        row["tracking_observed_from"] = None
        prepared.append(row)
    prepared = _assign_synthetic_keys(prepared)
    added = append_ledger(paths, prepared, existing_keys)
    state["seed"] = {
        "signature": signature,
        "status": "complete",
        "sourceRows": len(raw_rows),
        "addedRows": added,
        "completedAt": iso(),
    }
    return added


def _certified_seed_dir(args: argparse.Namespace) -> Path:
    raw = _arg(
        args,
        "certified_seed_dir",
        os.getenv("TRACKING_CERTIFIED_SEED_DIR", str(DEFAULT_SEED_DIR)),
    )
    return Path(raw).expanduser().resolve()


def _latest_iso(*values: Any) -> str | None:
    parsed = [parse_utc(value) for value in values]
    valid = [value for value in parsed if value is not None]
    return max(valid).isoformat() if valid else None


def _certified_seed_signature(state: dict[str, Any]) -> str | None:
    seed_state = state.get("certifiedHistoricalSeed")
    if not isinstance(seed_state, dict):
        return None
    signature = seed_state.get("signature")
    return str(signature) if signature else None


def _post_seed_reconciliation_evidence(
    state: dict[str, Any], ot_state: dict[str, Any]
) -> str | None:
    """Return proof that a seeded OT was read from the API after import.

    Releases before this guard marked every certified-seed OT as historically
    complete without calling ``/tracking``.  New checkpoints are tied to the
    exact seed signature.  For an existing runtime, a real successful request
    at or after ``importedAt`` is accepted once and upgraded to that stronger
    checkpoint.
    """
    if not ot_state.get("historical_seed_certified"):
        return None

    signature = _certified_seed_signature(state)
    reconciled_at = parse_utc(ot_state.get("tracking_reconciled_at"))
    if (
        signature
        and ot_state.get("tracking_reconciliation_seed_signature") == signature
        and reconciled_at is not None
    ):
        return reconciled_at.isoformat()

    seed_state = state.get("certifiedHistoricalSeed")
    imported_at = parse_utc(
        seed_state.get("importedAt") if isinstance(seed_state, dict) else None
    )
    legacy_at = _latest_iso(
        ot_state.get("historical_bootstrap_completed_at"),
        ot_state.get("last_successful_check_at"),
    )
    legacy_dt = parse_utc(legacy_at)
    if imported_at is not None and legacy_dt is not None and legacy_dt >= imported_at:
        return legacy_dt.isoformat()
    return None


def _mark_tracking_reconciled(
    state: dict[str, Any],
    ot_state: dict[str, Any],
    observed_at: str,
    source: str,
) -> None:
    """Seal one complete per-OT API response as reconciliation evidence."""
    ot_state.update(
        {
            "baseline_complete": True,
            "historical_bootstrap_complete": True,
            "tracking_reconciliation_required": False,
            "tracking_reconciled_at": observed_at,
            "tracking_reconciliation_source": source,
        }
    )
    signature = _certified_seed_signature(state)
    if signature and ot_state.get("historical_seed_certified"):
        ot_state["tracking_reconciliation_seed_signature"] = signature


def _normalize_certified_seed_reconciliation(
    state: dict[str, Any],
) -> dict[str, int | bool]:
    """Migrate false legacy completions and count post-seed API evidence."""
    total = 0
    completed = 0
    changed = False
    signature = _certified_seed_signature(state)
    ot_states = state.setdefault("ot", {})
    for raw_ot_state in ot_states.values():
        if not isinstance(raw_ot_state, dict) or not raw_ot_state.get(
            "historical_seed_certified"
        ):
            continue
        total += 1
        evidence_at = _post_seed_reconciliation_evidence(state, raw_ot_state)
        if evidence_at is None:
            desired = {
                "historical_bootstrap_complete": False,
                "tracking_reconciliation_required": True,
            }
        else:
            completed += 1
            desired = {
                "historical_bootstrap_complete": True,
                "tracking_reconciliation_required": False,
                "tracking_reconciled_at": evidence_at,
            }
            if signature:
                desired["tracking_reconciliation_seed_signature"] = signature
            if not raw_ot_state.get("tracking_reconciliation_source"):
                desired["tracking_reconciliation_source"] = (
                    "legacy_checkpoint_migration"
                )
        for key, value in desired.items():
            if raw_ot_state.get(key) != value:
                raw_ot_state[key] = value
                changed = True
    return {
        "total": total,
        "completed": completed,
        "pending": max(0, total - completed),
        "changed": changed,
    }


def import_certified_historical_seed(
    paths: WorkerPaths,
    state: dict[str, Any],
    ledger: list[dict[str, Any]],
    seed_dir: str | Path | None = None,
    logger: OperationalLogger | None = None,
) -> dict[str, Any]:
    """Verify and idempotently import the packaged historical baseline."""
    started = time.monotonic()
    if logger:
        logger.log("certified_seed_import_start")
    try:
        seed = load_certified_seed(seed_dir)
        manifest = seed.manifest
        totals = manifest["totals"]
        known = {ledger_identity(row) for row in ledger}
        added_rows = append_ledger(paths, seed.tracking_rows, known)

        existing_orders, existing_payload = load_catalog(paths)
        seed_orders = {
            int(raw["number"]): sanitize_order(raw)
            for raw in seed.catalog_payload["orders"]
        }
        catalog_added = 0
        for number, seed_order in seed_orders.items():
            current = existing_orders.get(number)
            if current is None:
                existing_orders[number] = seed_order
                catalog_added += 1
            else:
                # Runtime discovery/detail fields win; the certified snapshot
                # fills only fields absent from the newer local representation.
                existing_orders[number] = sanitize_order({**seed_order, **current})
        catalog_payload = {
            **seed.catalog_payload,
            **{
                key: value
                for key, value in existing_payload.items()
                if key not in {"orders", "discoveryCursorAt", "catalogFullConfirmedAt"}
            },
            "schemaVersion": max(
                int(seed.catalog_payload.get("schemaVersion") or 0),
                int(existing_payload.get("schemaVersion") or 0),
            ),
            "generatedAt": iso(),
            "catalogScope": "historical_all_statuses",
            "catalogFullConfirmedAt": _latest_iso(
                seed.catalog_payload.get("catalogFullConfirmedAt"),
                existing_payload.get("catalogFullConfirmedAt"),
            ),
            "discoveryCursorAt": _latest_iso(
                seed.catalog_payload.get("discoveryCursorAt"),
                existing_payload.get("discoveryCursorAt"),
            ),
            "retentionPolicy": "indefinite_merge_only",
            "pendingEnrichment": sorted(
                {
                    int(value)
                    for value in existing_payload.get("pendingEnrichment", [])
                    if str(value).isdigit()
                }
            ),
            "certifiedSeedSignature": seed.signature,
            # The snapshot proves which OTs existed, but its `status` values are
            # as old as the snapshot itself.  Importing it therefore revives
            # orders that closed since, inflating the active universe and the
            # administrative-closing KPI until a full sweep refreshes them.
            # Measured: 149 live actives became 213 right after the import.
            "statusesConfirmedThrough": seed.catalog_payload.get("catalogSnapshotThrough")
            or manifest.get("sourceSnapshotThrough"),
            "pendingFullReconciliation": True,
            "pendingFullReconciliationReason": "certified_seed_statuses_are_snapshot_aged",
            "orders": [existing_orders[number] for number in sorted(existing_orders)],
        }
        atomic_write_json(paths.catalog, catalog_payload)

        completed_at = iso()
        manifest_counts = {
            int(item["number"]): int(item["trackingRows"])
            for item in manifest["workOrders"]
        }
        state["certifiedHistoricalSeed"] = {
            "schemaVersion": 1,
            "status": "imported",
            "signature": seed.signature,
            "sourceSnapshotThrough": manifest["sourceSnapshotThrough"],
            "discoveryCursorAt": manifest["discoveryCursorAt"],
            "workOrders": int(totals["workOrders"]),
            "trackingRows": int(totals["trackingRows"]),
            "emptyWorkOrders": int(totals["emptyWorkOrders"]),
            "recognizedLabelRows": int(totals["recognizedLabelRows"]),
            "redactedFreeTextRows": int(totals["redactedFreeTextRows"]),
            "addedRows": added_rows,
            "catalogOrdersAdded": catalog_added,
            "importedAt": completed_at,
        }
        for number, row_count in manifest_counts.items():
            ot_state = state.setdefault("ot", {}).setdefault(str(number), {})
            ot_state.update(
                {
                    "baseline_complete": True,
                    "historical_seed_certified": True,
                    "tracking_count": max(int(ot_state.get("tracking_count") or 0), row_count),
                }
            )
        reconciliation = _normalize_certified_seed_reconciliation(state)
        bootstrap = state.setdefault("bootstrapAllTracking", {})
        bootstrap.update(
            {
                "schemaVersion": 1,
                "status": "reconciliation_required",
                "phase": "catalog_reconciliation",
                "source": "certified_seed",
                # The packaged catalog is complete only at its historical
                # cutoff.  A current all-status scan is still required.
                "catalogComplete": False,
                "catalogCompletedAt": None,
                "totalOrders": int(totals["workOrders"]),
                "completedOrders": int(reconciliation["completed"]),
                "pendingOrders": int(reconciliation["pending"]),
                "failedOrders": 0,
                "addedRows": int(totals["trackingRows"]),
            }
        )
        save_state(paths, state)
        result = {
            "status": "imported",
            "signature": seed.signature,
            "workOrders": int(totals["workOrders"]),
            "trackingRows": int(totals["trackingRows"]),
            "emptyWorkOrders": int(totals["emptyWorkOrders"]),
            "addedRows": added_rows,
            "catalogOrdersAdded": catalog_added,
        }
        if logger:
            logger.log(
                "certified_seed_import_done",
                work_orders=result["workOrders"],
                tracking_rows=result["trackingRows"],
                empty_orders=result["emptyWorkOrders"],
                added=result["addedRows"],
                catalog_added=result["catalogOrdersAdded"],
                elapsed_seconds=round(time.monotonic() - started, 2),
            )
        return result
    except Exception as exc:
        if logger:
            logger.log(
                "certified_seed_import_error",
                level="ERROR",
                error_type=type(exc).__name__,
                elapsed_seconds=round(time.monotonic() - started, 2),
            )
        raise


def _tracking_local_date(value: Any) -> Any:
    parsed = parse_utc(value)
    return parsed.astimezone(BOGOTA).date() if parsed else None


def tracking_rows(
    order: dict[str, Any],
    payload: list[dict[str, Any]],
    observed_at: str,
    observed_from: str | None,
    observation_kind: str,
    window_sla_minutes: float | None = None,
) -> list[dict[str, Any]]:
    client = client_from_record(order)
    cd = cd_from_record(order)
    base = {
        "work_order_number": order.get("number"),
        "vehicle_code": order.get("vehicleCode"),
        "client": client,
        "cd": cd,
        "work_order_status": order.get("status"),
        "work_order_type": order.get("type"),
        "work_order_updated_at": order.get("updatedAt"),
    }
    lower = parse_utc(observed_from)
    lower_local_date = lower.astimezone(BOGOTA).date() if lower else None
    duplicate_ordinals: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        author = item.get("createdBy") if isinstance(item.get("createdBy"), dict) else {}
        kind = observation_kind
        tracking_local_date = _tracking_local_date(item.get("trackingDate"))
        if kind == "live_new_order" and lower_local_date and tracking_local_date:
            if tracking_local_date < lower_local_date:
                kind = "baseline_before_discovery_window"
        # The ledger records what was observed, not how to interpret it: the
        # choice between the window ceiling and ``trackingDate`` lives in
        # build_events so it applies to rows already sealed and can change
        # without rewriting an append-only file.
        row = {
            **base,
            "tracking_id": item.get("id"),
            "tracking_date": item.get("trackingDate"),
            "tracking_comment": item.get("comment"),
            "created_by_id": author.get("id"),
            "created_by_name": author.get("name"),
            "file_count": len(item.get("files") or []) if isinstance(item.get("files") or [], list) else 0,
            "tracking_observed_at": observed_at,
            "tracking_observed_from": observed_from,
            "tracking_observation_kind": kind,
            "extracted_at": observed_at,
        }
        if row["tracking_id"] is None:
            canonical = json.dumps(
                {
                    "tracking_date": row["tracking_date"],
                    "tracking_comment": row["tracking_comment"],
                    "created_by_id": row["created_by_id"],
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            ordinal = duplicate_ordinals.get(canonical, 0)
            duplicate_ordinals[canonical] = ordinal + 1
            row["synthetic_ordinal"] = ordinal
        row["tracking_key"] = tracking_identity(row)
        row.pop("synthetic_ordinal", None)
        rows.append(row)
    return rows


def choose_targets(
    orders: dict[int, dict[str, Any]],
    state: dict[str, Any],
    forced: set[int],
    max_ots: int,
    catalog_days: int,
    active_statuses: set[str],
    priority_numbers: set[int],
    current_time: datetime,
    interval_seconds: int = 300,
    change_driven: bool = True,
) -> tuple[list[int], set[int], set[int]]:
    """Choose a bounded batch: changed orders first, fair rotation after.

    The list endpoint reports ``updatedAt`` for 50 orders per request, so the
    orders that actually moved are known before spending any per-order call.
    Those are polled in the same cycle as the change, which keeps the
    observation window — and therefore the recorded event hour — bounded by the
    interval regardless of how large the active universe grows.

    The rotation is retained as a safety net rather than the primary mechanism:
    correctness must not depend on Cloudfleet always advancing ``updatedAt``.
    Active work orders stay in a durable per-round queue under
    ``state.scheduler``.  A request failure still advances that queue (success
    freshness is tracked separately), preventing a permanently failing OT,
    newly updated orders, or repeated ``--ot`` diagnostics from starving the
    rest of the active universe.
    """
    cutoff = current_time - timedelta(days=max(0, catalog_days))
    active = {
        number
        for number, order in orders.items()
        if str(order.get("status") or "").casefold() in active_statuses
    }
    forced_active = forced & active
    # Changed active orders are polled this cycle. The batch size still caps the
    # work: a bulk update in Cloudfleet must not blow the request budget, so the
    # overflow is promoted to the head of the rotation queue instead.
    changed_active: set[int] = (priority_numbers & active) - forced_active if change_driven else set()
    # A bulk update in Cloudfleet could mark most of the universe as changed.
    # Reserving a rotation floor keeps the safety net advancing, so an order
    # that never changes cannot be starved by orders that change constantly.
    rotation_floor = min(len(active), max(1, max_ots // 8)) if active and change_driven else 0
    changed_cap = max(0, max_ots - rotation_floor)
    changed_selected = sorted(changed_active)[:changed_cap]
    changed_overflow = changed_active - set(changed_selected)
    rotation_capacity = max(0, max_ots - len(changed_selected))
    active_selected = _schedule_active_round(
        active,
        state,
        max_ots=rotation_capacity,
        forced_active=forced_active | set(changed_selected),
        current_time=current_time,
        interval_seconds=interval_seconds,
        promote=changed_overflow,
        unpromoted_reserve=rotation_floor,
    )
    selected = list(active_selected)
    selected_set = set(selected)

    # Explicit diagnostics are an additive exception to the configured batch
    # size.  They never replace or consume the fair active-order allocation.
    for number in sorted(forced - selected_set):
        selected.append(number)
        selected_set.add(number)

    eligible: set[int] = set(active) | set(forced)
    background: list[tuple[int, str, int]] = []
    for number, order in orders.items():
        if number in active or number in forced:
            continue
        status = str(order.get("status") or "").casefold()
        updated = parse_utc(order.get("updatedAt"))
        is_recent = bool(updated and updated >= cutoff)
        if not (is_recent or number in priority_numbers):
            continue
        eligible.add(number)
        ot_state = state.get("ot", {}).get(str(number), {})
        last_attempt = ot_state.get("last_checked_at") or ""
        priority = 0 if number in priority_numbers else 1
        background.append((priority, last_attempt, number))

    # Fill only unused regular capacity.  Active coverage always has precedence;
    # forced diagnostics may make the final list larger than ``max_ots``.
    remaining_capacity = max(0, max_ots - len(active_selected))
    background.sort(key=lambda item: (item[0], item[1], item[2]))
    for _, _, number in background:
        if remaining_capacity <= 0:
            break
        if number not in selected_set:
            selected.append(number)
            selected_set.add(number)
            remaining_capacity -= 1
    return selected, active, eligible


def _state_order_numbers(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for raw in value:
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number in seen:
            continue
        seen.add(number)
        result.append(number)
    return result


def _schedule_active_round(
    active: set[int],
    state: dict[str, Any],
    *,
    max_ots: int,
    forced_active: set[int],
    current_time: datetime,
    interval_seconds: int,
    promote: set[int] | None = None,
    unpromoted_reserve: int = 0,
) -> list[int]:
    scheduler = state.setdefault("scheduler", {})
    if not isinstance(scheduler, dict):
        scheduler = {}
        state["scheduler"] = scheduler
    scheduler["schemaVersion"] = 1
    scheduler["coverageMode"] = "rotating_complete"

    if not active:
        scheduler.update(
            {
                "activeRoundMembers": [],
                "activePending": [],
                "activeRoundSelected": [],
                "activeRoundRemaining": 0,
                "lastSelectionAt": iso(current_time),
                "activeRoundDeadlineAt": None,
            }
        )
        return []

    members = set(_state_order_numbers(scheduler.get("activeRoundMembers"))) & active
    pending = [
        number
        for number in _state_order_numbers(scheduler.get("activePending"))
        if number in active
    ]
    pending_set = set(pending)
    round_number = max(0, int(scheduler.get("activeRound") or 0))

    # An empty queue means the prior round was fully scheduled.  Start a new
    # deterministic round on the next cycle, never twice in the same cycle.
    if not pending:
        round_number += 1
        members = set(active)
        pending = sorted(active)
        pending_set = set(pending)
        scheduler["activeRoundStartedAt"] = iso(current_time)
    else:
        # Orders discovered while a round is in flight join its tail.  Existing
        # pending work therefore retains its position under continuous churn.
        new_active = sorted(active - members)
        pending.extend(new_active)
        pending_set.update(new_active)
        members.update(new_active)

    selected: list[int] = []
    # Forced active diagnostics count as attempted coverage for this round but
    # do not consume normal capacity and cannot starve other active orders.
    for number in sorted(forced_active & active):
        if number in pending_set:
            pending.remove(number)
            pending_set.remove(number)
        selected.append(number)

    # Changed orders that did not fit this cycle's batch move to the head of the
    # queue instead of waiting a full round: they have a pending observation.
    # Only orders still queued are reordered; re-adding an order already served
    # in this round would keep the round from ever completing.
    promoted_set = {number for number in (promote or set()) if number in pending_set}
    if promoted_set:
        pending = sorted(promoted_set) + [
            number for number in pending if number not in promoted_set
        ]

    # Reserve part of the batch for orders that are NOT being promoted. Without
    # this, constant churn would postpone a quiet order indefinitely, which is
    # the exact starvation the round queue exists to prevent.
    plain_available = sum(1 for number in pending if number not in promoted_set)
    reserved = min(max(0, unpromoted_reserve), plain_available, max_ots)
    promoted_quota = max(0, max_ots - reserved)
    regular: list[int] = []
    promoted_taken = 0
    for number in pending:
        if len(regular) >= max_ots:
            break
        if number in promoted_set:
            if promoted_taken >= promoted_quota:
                continue
            promoted_taken += 1
        regular.append(number)
    taken = set(regular)
    selected.extend(number for number in regular if number not in forced_active)
    pending = [number for number in pending if number not in taken]

    completed_rounds = max(0, int(scheduler.get("completedActiveRounds") or 0))
    if not pending:
        completed_rounds += 1
        scheduler["lastCompletedActiveRoundAt"] = iso(current_time)

    started = parse_utc(scheduler.get("activeRoundStartedAt")) or current_time
    round_cycles = max(1, math.ceil(len(members) / max(1, max_ots)))
    scheduler.update(
        {
            "activeRound": round_number,
            "activeRoundMembers": sorted(members),
            "activePending": pending,
            "activeRoundSelected": selected,
            "activeRoundRemaining": len(pending),
            "completedActiveRounds": completed_rounds,
            "lastSelectionAt": iso(current_time),
            "activeRoundDeadlineAt": iso(
                started + timedelta(seconds=round_cycles * max(1, interval_seconds))
            ),
        }
    )
    return selected


def safe(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    # A numpy scalar is not JSON serializable, so it would reach the encoder's
    # ``default=str`` and be published as a quoted string.  That silently broke
    # the contract: ``orders[].currentLabelId`` came out as "9" while
    # ``labels[].id`` was 9, so a consumer joining the two found nothing.
    item = getattr(value, "item", None)
    if callable(item) and hasattr(value, "dtype"):
        try:
            return item()
        except (AttributeError, ValueError):
            return value
    return value


def enrich_ledger_rows(
    ledger: list[dict[str, Any]], orders: dict[int, dict[str, Any]] | None
) -> list[dict[str, Any]]:
    if not orders:
        return ledger
    enriched: list[dict[str, Any]] = []
    for original in ledger:
        row = dict(original)
        try:
            order = orders.get(int(row.get("work_order_number")))
        except (TypeError, ValueError):
            order = None
        if order:
            if order.get("vehicleCode"):
                row["vehicle_code"] = order["vehicleCode"]
            row["work_order_status"] = order.get("status") or row.get("work_order_status")
            row["work_order_type"] = order.get("type") or row.get("work_order_type")
            row["work_order_updated_at"] = order.get("updatedAt") or row.get("work_order_updated_at")
            current_client = client_from_record(order)
            current_cd = cd_from_record(order)
            if current_client != "Sin cliente":
                row["client"] = current_client
            if current_cd != "Sin CD":
                row["cd"] = current_cd
        enriched.append(row)
    return enriched


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def administrative_closing_backlog(
    orders: dict[int, dict[str, Any]] | None,
    current_time: datetime,
    status: str = "ontechnicalcompletion",
) -> dict[str, Any]:
    """Age work orders that are technically finished but not yet closed.

    These orders are already done in the workshop; they stay in the active
    universe only because nobody closed them administratively.  Growth here is
    an operational finding in its own right, not merely worker load, so it is
    published instead of being absorbed silently.  It costs no extra request:
    ``status`` and ``technicalCompletionDate`` are already in the catalog.
    """
    empty = {
        "orders": 0,
        "withoutCompletionDate": 0,
        "medianDays": None,
        "p90Days": None,
        "maxDays": None,
        "over90Days": 0,
        "buckets": {},
        "byCd": [],
        "oldest": [],
    }
    if not orders:
        return empty
    ages: list[float] = []
    per_cd: dict[str, list[float]] = {}
    oldest: list[tuple[float, Any, Any, str]] = []
    missing = 0
    for number, order in orders.items():
        if str(order.get("status") or "").casefold() != status:
            continue
        completed = parse_utc(order.get("technicalCompletionDate"))
        if completed is None:
            missing += 1
            continue
        days = max(0.0, (current_time - completed).total_seconds() / 86400.0)
        ages.append(days)
        cd = cd_from_record(order)
        per_cd.setdefault(cd, []).append(days)
        oldest.append((days, number, order.get("vehicleCode"), cd))
    if not ages and not missing:
        return empty
    buckets = {"<=7d": 0, "<=30d": 0, "<=90d": 0, "<=180d": 0, "<=365d": 0, ">365d": 0}
    for days in ages:
        for label, limit in (("<=7d", 7), ("<=30d", 30), ("<=90d", 90), ("<=180d", 180), ("<=365d", 365)):
            if days <= limit:
                buckets[label] += 1
                break
        else:
            buckets[">365d"] += 1
    oldest.sort(reverse=True)
    return {
        "orders": len(ages) + missing,
        "withoutCompletionDate": missing,
        "medianDays": round(_percentile(ages, 0.5), 1) if ages else None,
        "p90Days": round(_percentile(ages, 0.9), 1) if ages else None,
        "maxDays": round(max(ages), 1) if ages else None,
        "over90Days": sum(1 for days in ages if days > 90),
        "buckets": buckets,
        "byCd": sorted(
            (
                {
                    "cd": cd,
                    "orders": len(values),
                    "medianDays": round(_percentile(values, 0.5), 1),
                    "maxDays": round(max(values), 1),
                }
                for cd, values in per_cd.items()
            ),
            key=lambda row: (-row["orders"], -(row["maxDays"] or 0)),
        )[:15],
        "oldest": [
            {"ot": safe(number), "plate": safe(plate), "cd": cd, "days": round(days, 1)}
            for days, number, plate, cd in oldest[:10]
        ],
    }


def order_field_labels(order: dict[str, Any] | None) -> tuple[list[str], int | None, str | None, bool]:
    """Read ``maintenanceLabels`` from the work order itself.

    This is a second, independent label channel.  It is never the source of a
    label event — the tracking comment is (see docs/PIPELINE.md D1/D2) — but it
    is the only one the list endpoint exposes in bulk, so it is published as
    context and as a process-discipline signal.  Values outside the controlled
    catalog are reported as uncontrolled instead of being invented into it.
    """
    raw = (order or {}).get("maintenanceLabels")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return [], None, None, False
    values = [" ".join(str(item).split()) for item in raw if item is not None and str(item).strip()]
    uncontrolled = False
    label_id: int | None = None
    label_name: str | None = None
    for value in values:
        normalized = normalize_label(value)
        if normalized and label_id is None:
            label_id, label_name = normalized
        elif not normalized:
            uncontrolled = True
    return values, label_id, label_name, uncontrolled


def _agreement(comment_label_id: Any, field_label_id: int | None, has_field: bool) -> str:
    comment_id = None
    try:
        comment_id = int(comment_label_id) if comment_label_id is not None else None
    except (TypeError, ValueError):
        comment_id = None
    if comment_id is not None and field_label_id is not None:
        return "agree" if comment_id == field_label_id else "disagree"
    if comment_id is not None:
        return "comment_only_uncontrolled_field" if has_field else "comment_only"
    if field_label_id is not None:
        return "field_only"
    return "none"


def _empty_dashboard(worker_meta: dict[str, Any]) -> dict[str, Any]:
    health = {
        "status": worker_meta.get("status", "starting"),
        "ready": bool(worker_meta.get("ready", False)),
        "reasons": worker_meta.get("healthReasons", []),
    }
    return {
        "schemaVersion": 2,
        "generatedAt": iso(),
        "worker": worker_meta,
        "health": health,
        "kpis": {
            "trackingRows": 0,
            "labelEvents": 0,
            "otsWithLabels": 0,
            "transitions": 0,
            "averagePreciseClosedHours": None,
            "preciseClosedSegments": 0,
            "averageAllClosedHours": None,
            "allClosedSegments": 0,
            "openSegments": 0,
        },
        "labels": [],
        "transitions": [],
        "orders": [],
        "recentEvents": [],
        "quality": {
            "trackingRows": 0,
            "redactedEvidenceRows": 0,
            "labelCandidateRows": 0,
            "recognizedEvents": 0,
            "unrecognizedRows": 0,
            "explicitDates": 0,
            "eventTimeSources": {},
            "observationWindows": {
                "withWindow": 0,
                "withinSla": 0,
                "overSla": 0,
                "maxMinutes": None,
                "averageMinutes": None,
            },
        },
    }


def build_dashboard(
    ledger: list[dict[str, Any]],
    state: dict[str, Any],
    worker_meta: dict[str, Any],
    orders: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current_clock = parse_utc(worker_meta.get("cycleFinishedAt")) or now_utc()
    # Every key is present from the start: an integrator must be able to read
    # `operations.labelSourceAgreement` before the first label event exists,
    # which is the normal state of this deployment for now.
    operations: dict[str, Any] = {
        "administrativeClosingBacklog": administrative_closing_backlog(orders, current_clock),
        "labelSourceAgreement": {
            "authoritativeSource": "tracking_comment",
            "counts": {},
            "comparableOrders": 0,
            "disagreementRate": None,
            "ordersWithUncontrolledFieldLabel": 0,
            "disagreements": [],
        },
    }
    source = pd.DataFrame(enrich_ledger_rows(ledger, orders))
    if source.empty:
        return {**_empty_dashboard(worker_meta), "operations": operations}
    source["work_order_number"] = source["work_order_number"].map(normalize_identifier)
    events, rejected, event_stats = build_events(
        source, window_sla_minutes=float(worker_meta.get("pollSlaSeconds") or 300) / 60.0
    )
    transitions, durations = build_transitions(events)
    if events.empty:
        dashboard = {**_empty_dashboard(worker_meta), "operations": operations}
        dashboard["kpis"]["trackingRows"] = len(source)
        dashboard["quality"].update(
            {
                "trackingRows": event_stats["tracking_rows"],
                "redactedEvidenceRows": event_stats.get("redacted_evidence", 0),
                "labelCandidateRows": event_stats["tracking_rows"]
                - event_stats.get("redacted_evidence", 0),
                "recognizedEvents": 0,
                "unrecognizedRows": event_stats["unrecognized"],
                "explicitDates": event_stats["explicit_dates"],
            }
        )
        return dashboard

    events = events.copy()
    # Explicit ISO8601: `event_at` mixes precisions by design (see build_events).
    events["event_at_dt"] = pd.to_datetime(events["event_at"], utc=True, format="ISO8601")
    events["_tracking_sort_key"] = events["tracking_id"].map(stable_identifier_sort_key)
    event_count_by_ot = source.groupby("work_order_number").size().to_dict()
    order_rows: list[dict[str, Any]] = []
    poll_sla_minutes = float(worker_meta.get("pollSlaSeconds") or 300) / 60.0
    for ot, group in events.sort_values(["event_at_dt", "_tracking_sort_key"], na_position="last").groupby(
        "work_order_number"
    ):
        latest = group.iloc[-1]
        current_at = latest["event_at_dt"]
        current_hours = round((pd.Timestamp(current_clock) - current_at).total_seconds() / 3600, 2)
        ot_durations = durations[durations["work_order_number"] == ot] if not durations.empty else pd.DataFrame()
        segments: list[dict[str, Any]] = []
        for _, segment in ot_durations.iterrows():
            segments.append(
                {
                    "label": safe(segment.get("label_name")),
                    "hours": safe(segment.get("duration_hours")),
                    "open": bool(segment.get("duration_is_open")),
                    "start": safe(segment.get("segment_start_display")),
                    "end": safe(segment.get("segment_end_display")),
                    "timeQuality": safe(segment.get("time_quality")),
                    "precise": bool(segment.get("duration_is_precise")),
                }
            )
        window_minutes = safe(latest.get("observation_window_minutes"))
        catalog_order = None
        if orders:
            try:
                catalog_order = orders.get(int(ot))
            except (TypeError, ValueError):
                catalog_order = None
        field_values, field_label_id, field_label_name, field_uncontrolled = order_field_labels(
            catalog_order
        )
        agreement = _agreement(latest.get("label_id"), field_label_id, bool(field_values))
        order_rows.append(
            {
                "ot": safe(ot),
                "fieldLabels": field_values,
                "fieldLabelId": field_label_id,
                "fieldLabel": field_label_name,
                "fieldLabelUncontrolled": field_uncontrolled,
                "labelSourceAgreement": agreement,
                "plate": safe(latest.get("vehicle_code")),
                "client": safe(latest.get("client")),
                "cd": safe(latest.get("cd")),
                "status": safe(latest.get("work_order_status")),
                "type": safe(latest.get("work_order_type")),
                "currentLabelId": safe(latest.get("label_id")),
                "currentLabel": safe(latest.get("label_name")),
                "currentSince": safe(latest.get("event_at_display")),
                "currentHours": current_hours,
                "currentTimeSource": safe(latest.get("event_at_source")),
                "currentObservationFrom": safe(latest.get("observation_from")),
                "currentObservationTo": safe(latest.get("observation_to")),
                "currentObservationWindowMinutes": window_minutes,
                "currentWindowWithinSla": (
                    bool(window_minutes <= poll_sla_minutes) if window_minutes is not None else None
                ),
                "trackingCount": int(event_count_by_ot.get(ot, 0)),
                "labelEventCount": len(group),
                "lastEventAt": safe(latest.get("event_at_display")),
                "segments": segments,
            }
        )

    label_rows: list[dict[str, Any]] = []
    for label_id, label_name in LABELS.items():
        label_events = events[events["label_id"] == label_id]
        label_durations = durations[
            (durations["label_id"] == label_id) & durations["duration_hours"].notna()
        ] if not durations.empty else pd.DataFrame()
        precise = label_durations[label_durations["duration_is_precise"]] if not label_durations.empty else pd.DataFrame()
        label_rows.append(
            {
                "id": label_id,
                "name": label_name,
                "events": len(label_events),
                "segments": len(label_durations),
                "preciseSegments": len(precise),
                "averageHours": round(float(precise["duration_hours"].mean()), 2) if not precise.empty else None,
                "medianHours": round(float(precise["duration_hours"].median()), 2) if not precise.empty else None,
                "averageAllHours": round(float(label_durations["duration_hours"].mean()), 2) if not label_durations.empty else None,
                "openSegments": int(
                    ((durations["label_id"] == label_id) & durations["duration_is_open"]).sum()
                ) if not durations.empty else 0,
            }
        )

    transition_rows: list[dict[str, Any]] = []
    if not transitions.empty:
        valid = transitions[transitions["previous_label_id"].notna()].copy()
        valid["duration_is_precise"] = valid.apply(
            lambda row: row.get("event_time_quality") in {"explicit", "live_window"}
            and row.get("previous_event_time_quality") in {"explicit", "live_window"},
            axis=1,
        )
        for (from_id, to_id), group in valid.groupby(["previous_label_id", "label_id"]):
            all_hours = group["time_to_label_minutes"].dropna().div(60)
            precise_hours = group[group["duration_is_precise"]]["time_to_label_minutes"].dropna().div(60)
            transition_rows.append(
                {
                    "fromId": int(from_id),
                    "from": LABELS.get(int(from_id), str(from_id)),
                    "toId": int(to_id),
                    "to": LABELS.get(int(to_id), str(to_id)),
                    "count": len(group),
                    "preciseCount": int(group["duration_is_precise"].sum()),
                    "averageHours": round(float(precise_hours.mean()), 2) if not precise_hours.empty else None,
                    "averageAllHours": round(float(all_hours.mean()), 2) if not all_hours.empty else None,
                }
            )

    order_rows.sort(key=lambda row: row.get("currentHours") or 0, reverse=True)
    # Both channels are recorded and the comment wins; the gap between them is
    # published as a process-discipline indicator rather than hidden by the
    # precedence rule.
    agreement_counts: dict[str, int] = {}
    for row in order_rows:
        key = str(row.get("labelSourceAgreement") or "none")
        agreement_counts[key] = agreement_counts.get(key, 0) + 1
    comparable = agreement_counts.get("agree", 0) + agreement_counts.get("disagree", 0)
    operations["labelSourceAgreement"] = {
        "authoritativeSource": "tracking_comment",
        "counts": agreement_counts,
        "comparableOrders": comparable,
        "disagreementRate": (
            round(agreement_counts.get("disagree", 0) / comparable, 4) if comparable else None
        ),
        "ordersWithUncontrolledFieldLabel": sum(
            1 for row in order_rows if row.get("fieldLabelUncontrolled")
        ),
        "disagreements": [
            {
                "ot": row.get("ot"),
                "plate": row.get("plate"),
                "cd": row.get("cd"),
                "comment": row.get("currentLabel"),
                "field": row.get("fieldLabel"),
            }
            for row in order_rows
            if row.get("labelSourceAgreement") == "disagree"
        ][:25],
    }
    recent = events.sort_values("event_at_dt", ascending=False).head(50)
    recent_out = [
        {
            "ot": safe(row.get("work_order_number")),
            "plate": safe(row.get("vehicle_code")),
            "client": safe(row.get("client")),
            "label": safe(row.get("label_name")),
            "at": safe(row.get("event_at_display")),
            "by": safe(row.get("created_by_name")),
            "comment": safe(row.get("tracking_comment")),
            "timeSource": safe(row.get("event_at_source")),
            "observationWindowMinutes": safe(row.get("observation_window_minutes")),
        }
        for _, row in recent.iterrows()
    ]
    closed = durations[durations["duration_hours"].notna()] if not durations.empty else pd.DataFrame()
    precise_closed = closed[closed["duration_is_precise"]] if not closed.empty else pd.DataFrame()
    source_counts = {str(key): int(value) for key, value in events["event_at_source"].value_counts().items()}
    windows = events["observation_window_minutes"].dropna().astype(float)
    health = {
        "status": worker_meta.get("status", "unknown"),
        "ready": bool(worker_meta.get("ready", False)),
        "reasons": worker_meta.get("healthReasons", []),
    }
    return {
        "schemaVersion": 2,
        "generatedAt": iso(),
        "worker": worker_meta,
        "health": health,
        "kpis": {
            "trackingRows": len(source),
            "labelEvents": len(events),
            "otsWithLabels": len(order_rows),
            "transitions": int(transitions["previous_label_id"].notna().sum()) if not transitions.empty else 0,
            "averagePreciseClosedHours": round(float(precise_closed["duration_hours"].mean()), 2) if not precise_closed.empty else None,
            "preciseClosedSegments": len(precise_closed),
            "averageAllClosedHours": round(float(closed["duration_hours"].mean()), 2) if not closed.empty else None,
            "allClosedSegments": len(closed),
            "averageClosedHours": round(float(precise_closed["duration_hours"].mean()), 2) if not precise_closed.empty else None,
            "openSegments": int(durations["duration_is_open"].sum()) if not durations.empty else 0,
        },
        "labels": label_rows,
        "transitions": sorted(transition_rows, key=lambda row: row["count"], reverse=True),
        "orders": order_rows,
        "recentEvents": recent_out,
        "operations": operations,
        "quality": {
            "trackingRows": event_stats["tracking_rows"],
            # Rows whose comment was redacted before distribution are evidence
            # of coverage, not label candidates. Keeping them in the denominator
            # would report ~9.4k "unrecognized" rows that never claimed to be
            # labels and would hide the real parser quality.
            "redactedEvidenceRows": event_stats.get("redacted_evidence", 0),
            "labelCandidateRows": event_stats["tracking_rows"]
            - event_stats.get("redacted_evidence", 0),
            "recognizedEvents": event_stats["recognized"],
            "unrecognizedRows": event_stats["unrecognized"],
            "explicitDates": event_stats["explicit_dates"],
            "rejectedRows": len(rejected),
            "eventTimeSources": source_counts,
            "observationWindows": {
                "withWindow": len(windows),
                "withinSla": int((windows <= poll_sla_minutes).sum()),
                "overSla": int((windows > poll_sla_minutes).sum()),
                "maxMinutes": round(float(windows.max()), 2) if not windows.empty else None,
                "averageMinutes": round(float(windows.mean()), 2) if not windows.empty else None,
            },
        },
    }


def _arg(args: argparse.Namespace, name: str, default: Any) -> Any:
    value = getattr(args, name, None)
    return default if value is None else value


def validate_args(args: argparse.Namespace) -> None:
    exclusive_modes = sum(
        bool(_arg(args, name, False))
        for name in ("once", "seed_only", "healthcheck", "bootstrap_all_tracking")
    )
    if exclusive_modes > 1:
        raise ValueError(
            "--once, --seed-only, --healthcheck y --bootstrap-all-tracking son excluyentes"
        )
    if int(_arg(args, "interval", 300)) <= 0:
        raise ValueError("--interval debe ser > 0")
    if int(_arg(args, "max_ots_per_cycle", DEFAULT_MAX_OTS_PER_CYCLE)) <= 0:
        raise ValueError("--max-ots-per-cycle debe ser > 0")
    if int(_arg(args, "catalog_max_pages", 200)) <= 0:
        raise ValueError("--catalog-max-pages debe ser > 0")
    if int(_arg(args, "max_enrichments", 5)) < 0:
        raise ValueError("--max-enrichments debe ser >= 0")
    if int(_arg(args, "discovery_overlap_seconds", 120)) < 0:
        raise ValueError("--discovery-overlap-seconds debe ser >= 0")
    coverage_sla = int(
        _arg(args, "coverage_sla_seconds", DEFAULT_COVERAGE_SLA_SECONDS)
    )
    if coverage_sla < int(_arg(args, "interval", 300)):
        raise ValueError("--coverage-sla-seconds debe ser >= --interval")
    delay = _arg(args, "delay", None)
    if delay is not None and float(delay) < 0:
        raise ValueError("--delay debe ser >= 0")
    if not _active_statuses(args):
        raise ValueError("--active-statuses debe contener al menos un estado")


def _active_statuses(args: argparse.Namespace) -> set[str]:
    raw = str(_arg(args, "active_statuses", ACTIVE_STATUSES_DEFAULT))
    return {value.strip().casefold() for value in raw.split(",") if value.strip()}


def _seed_path(args: argparse.Namespace) -> Path | None:
    raw = _arg(args, "seed_parquet", os.getenv("TRACKING_SEED_PARQUET", str(DEFAULT_SEED_PARQUET)))
    return Path(raw).expanduser().resolve() if raw else None


def _write_health(paths: WorkerPaths, worker_meta: dict[str, Any]) -> None:
    payload = {
        "schemaVersion": 1,
        "generatedAt": iso(),
        "status": worker_meta.get("status"),
        "ready": worker_meta.get("ready"),
        "reasons": worker_meta.get("healthReasons", []),
        "lastSuccessfulCycleAt": worker_meta.get("lastSuccessfulCycleAt"),
        "nextCycleAt": worker_meta.get("nextCycleAt"),
        "metrics": {
            key: worker_meta.get(key)
            for key in (
                "intervalSeconds",
                "cycleDurationSeconds",
                "activeOrders",
                "activeUniverse",
                "activeOrdersPolled",
                "activeOrdersSucceeded",
                "activeEverAttempted",
                "activeNeverAttempted",
                "activeTrackingBacklog",
                "changeDriven",
                "changedActiveOrders",
                "changedActivePolled",
                "changedTrackingBacklog",
                "rotationOrdersPolled",
                "rotationCoverageAgeSeconds",
                "trackingRowsFoundWithoutUpdatedAtChange",
                "ordersWithUndetectedChanges",
                "coverageMode",
                "activeCoverageRound",
                "activeOrdersRemainingInRound",
                "completedActiveCoverageRounds",
                "fullSweepCycles",
                "fullSweepSeconds",
                "allActiveScheduledThisCycle",
                "activePollSlaFeasible",
                "historicalBootstrapStatus",
                "historicalBootstrapTotalOrders",
                "historicalBootstrapCompletedOrders",
                "historicalBootstrapPendingOrders",
                "historicalBootstrapFailedOrders",
                "historicalBootstrapAttemptedThisRun",
                "historicalBootstrapOrdersPerMinute",
                "historicalBootstrapEtaSeconds",
                "historicalBootstrapProgressUpdatedAt",
                "eligibleOrders",
                "polledOrders",
                "trackingBacklog",
                "priorityTrackingBacklog",
                "capacitySufficient",
                "ordersWithinSla",
                "ordersOutsideSla",
                "coverageSlaSeconds",
                "ordersWithinCoverageSla",
                "ordersOutsideCoverageSla",
                "coverageSlaFeasible",
                "cycleRequestsRequired",
                "oldestSuccessfulPollAgeSeconds",
                "maxObservationWindowMinutes",
                "lateObservationWindows",
                "backgroundObservationWindows",
                "maxBackgroundObservationWindowMinutes",
                "observationWindowsOverInterval",
                "errors",
                "consecutiveFailedCycles",
            )
        },
    }
    atomic_write_json(paths.health, payload)


def _publish_bootstrap_progress_snapshot(
    paths: WorkerPaths,
    progress: dict[str, Any],
    *,
    elapsed_seconds: float,
    orders_per_minute: float | None,
    eta_seconds: float | None,
    updated_at: str,
) -> dict[str, Any]:
    """Publish bootstrap health without rebuilding labels, events or orders."""
    status = str(progress.get("status") or "running")
    worker_status = {
        "running": "bootstrap_running",
        "complete": "bootstrap_complete",
        "incomplete": "bootstrap_incomplete",
    }.get(status, "bootstrap_running")
    reasons = ["live_worker_not_started"]
    if status == "running":
        reasons.append("historical_bootstrap_in_progress")
    elif status != "complete":
        reasons.append("historical_bootstrap_incomplete")
    meta = {
        "status": worker_status,
        "ready": False,
        "healthReasons": reasons,
        "mode": "bootstrap_all_tracking",
        "lastRunAt": updated_at,
        "lastSuccessfulCycleAt": None,
        "cycleStartedAt": progress.get("startedAt"),
        "cycleDurationSeconds": round(max(0.0, elapsed_seconds), 2),
        "catalogOrders": int(progress.get("catalogOrders") or progress.get("totalOrders") or 0),
        "catalogRetention": "indefinite",
        "historicalBootstrapStatus": status,
        "historicalBootstrapTotalOrders": int(progress.get("totalOrders") or 0),
        "historicalBootstrapCompletedOrders": int(progress.get("completedOrders") or 0),
        "historicalBootstrapPendingOrders": int(progress.get("pendingOrders") or 0),
        "historicalBootstrapFailedOrders": int(progress.get("failedOrders") or 0),
        "historicalBootstrapAttemptedThisRun": int(progress.get("attemptedThisRun") or 0),
        "historicalBootstrapOrdersPerMinute": (
            round(orders_per_minute, 2) if orders_per_minute is not None else None
        ),
        "historicalBootstrapEtaSeconds": (
            round(eta_seconds, 2) if eta_seconds is not None else None
        ),
        "historicalBootstrapProgressUpdatedAt": updated_at,
        "errors": int(progress.get("failedOrders") or 0),
        "consecutiveFailedCycles": 0,
    }
    existing = read_json(paths.dashboard, {})
    if not isinstance(existing, dict) or not existing:
        existing = _empty_dashboard(meta)
    existing["generatedAt"] = updated_at
    existing["worker"] = meta
    existing["health"] = {
        "status": worker_status,
        "ready": False,
        "reasons": reasons,
    }
    atomic_write_json(paths.dashboard, existing)
    _write_health(paths, meta)
    return meta


def run_once(
    args: argparse.Namespace,
    client: CloudfleetClientProtocol | None = None,
    *,
    paths: WorkerPaths | None = None,
    clock: Callable[[], datetime] = now_utc,
    logger: OperationalLogger | None = None,
) -> dict[str, Any]:
    """Run one cycle. The injectable client/clock make this API network-free in tests."""
    logger = logger or OperationalLogger(_arg(args, "log_level", None))
    validate_args(args)
    cycle_started = clock().astimezone(timezone.utc)
    monotonic_started = time.monotonic()
    logger.log(
        "cycle_start",
        interval_seconds=int(_arg(args, "interval", 300)),
        max_orders=int(
            _arg(args, "max_ots_per_cycle", DEFAULT_MAX_OTS_PER_CYCLE)
        ),
    )
    paths = paths or WorkerPaths.from_runtime(_arg(args, "runtime_dir", None))
    paths.runtime.mkdir(parents=True, exist_ok=True)
    state = load_state(paths)
    ledger, rejected_ledger_lines, needs_compaction = read_ledger(paths)
    compacted = False
    if needs_compaction and bool(_arg(args, "compact_ledger", True)):
        compact_ledger(paths, ledger)
        rejected_ledger_lines = 0
        compacted = True
    logger.log(
        "ledger_loaded",
        rows=len(ledger),
        rejected_lines=rejected_ledger_lines,
        compacted=compacted,
    )
    seeded = 0
    if not bool(_arg(args, "skip_seed", False)):
        seed_state = state.get("certifiedHistoricalSeed")
        seed_manifest = _certified_seed_dir(args) / "historical_seed_manifest.json"
        seed_needs_import = (
            not isinstance(seed_state, dict) or seed_state.get("status") != "imported"
        )
        if seed_manifest.is_file() and seed_needs_import:
            import_certified_historical_seed(
                paths,
                state,
                ledger,
                _certified_seed_dir(args),
                logger,
            )
            ledger, _, _ = read_ledger(paths)
        seeded = seed_ledger_from_parquet(paths, state, _seed_path(args), ledger)
        if seeded:
            ledger, _, _ = read_ledger(paths)

    reconciliation = _normalize_certified_seed_reconciliation(state)
    if reconciliation["changed"]:
        # Persist the one-time migration immediately.  A legacy seed flag is
        # not allowed to remain a false completion if this cycle fails later.
        save_state(paths, state)

    owned_client = client is None
    if client is None:
        config = ClientConfig.from_environment(_arg(args, "env_file", None))
        delay = _arg(args, "delay", None)
        if delay is not None:
            config = replace(config, min_request_interval_seconds=float(delay))
        client = CloudfleetClient(config, logger=logger)
    client_requests_before = getattr(client, "request_attempts", None)

    errors = 0
    error_details: list[str] = []
    discovery: dict[str, Any] = {}
    if not bool(_arg(args, "skip_catalog_refresh", False)):
        try:
            discovery = sync(
                str(_arg(args, "active_statuses", ACTIVE_STATUSES_DEFAULT)),
                None,
                int(_arg(args, "catalog_max_pages", 200)),
                str(_arg(args, "catalog_from_date", "2021-01-01")),
                mode="incremental",
                client=client,
                catalog_file=paths.catalog,
                overlap_seconds=int(_arg(args, "discovery_overlap_seconds", 120)),
                enrich_new=True,
                max_enrichments=int(_arg(args, "max_enrichments", 5)),
                now=cycle_started,
                logger=logger,
            )
        except Exception as exc:
            errors += 1
            error_details.append(f"catalog_discovery: {type(exc).__name__}")
    else:
        logger.log("catalog_sync_start", requested_mode="skipped", effective_mode="skipped")
        logger.log("catalog_sync_done", mode="skipped", orders=0, pages=0, new_orders=0)

    orders, catalog_payload = load_catalog(paths)
    pending_full_reconciliation = bool(catalog_payload.get("pendingFullReconciliation"))
    forced = {
        int(value)
        for value in str(_arg(args, "ot", "")).split(",")
        if value.strip()
    }
    for number in forced:
        orders.setdefault(number, {"number": number, "status": "unknown"})

    new_numbers = set(discovery.get("newOrderNumbers", [])) if discovery.get("mode") == "incremental" else set()
    priority_numbers = new_numbers | set(discovery.get("updatedOrderNumbers", []))
    # A newly discovered OT must be read immediately because the per-OT
    # endpoint returns its complete tracking history.  Treat it like an
    # additive diagnostic so it cannot consume or starve the fair active batch.
    immediate_numbers = forced | new_numbers
    change_driven = bool(_arg(args, "change_driven", True))
    targets, active_numbers, eligible_numbers = choose_targets(
        orders,
        state,
        immediate_numbers,
        int(_arg(args, "max_ots_per_cycle", DEFAULT_MAX_OTS_PER_CYCLE)),
        int(_arg(args, "catalog_days", 14)),
        _active_statuses(args),
        priority_numbers,
        cycle_started,
        int(_arg(args, "interval", 300)),
        change_driven=change_driven,
    )
    changed_active = (priority_numbers | new_numbers) & active_numbers
    # Persist the fair-scheduler reservation before network I/O.  A process
    # crash or a repeatedly failing OT can delay a reserved tail until the next
    # round, but can never reset the queue and starve it indefinitely.
    save_state(paths, state)
    scheduler = state.get("scheduler", {}) if isinstance(state.get("scheduler"), dict) else {}
    known = {ledger_identity(row) for row in ledger}
    tracking_calls = 0
    api_requests = int(discovery.get("pages", 0)) + int(discovery.get("enrichedOrders", 0)) + int(
        discovery.get("enrichmentErrors", 0)
    )
    added = 0
    min_remaining: int | None = None
    successful_targets: set[int] = set()
    # Split by population.  The coverage SLA is a promise about ACTIVE orders.
    # Closed orders polled opportunistically with leftover capacity carry no
    # such promise, and their naturally long windows made readiness flap on
    # every round-closing cycle.
    observation_windows: list[float] = []
    background_observation_windows: list[float] = []
    # Falsification counter for the assumption that drives E2: rows discovered
    # by the rotation on orders the list did NOT report as changed.  While this
    # stays at zero, ``updatedAt`` is a sufficient trigger and the rotation can
    # be spaced out.  If it grows, the rotation is what sustains correctness.
    undetected_change_rows = 0
    undetected_change_orders: set[int] = set()
    tracking_attempted = 0
    tracking_failed = 0
    tracking_batch_started = time.monotonic()
    last_progress_attempted = -1
    logger.log(
        "tracking_batch_start",
        total=len(targets),
        active_orders=len(active_numbers),
        eligible_orders=len(eligible_numbers),
        coverage_mode=scheduler.get("coverageMode", "rotating_complete"),
        active_round=scheduler.get("activeRound"),
        active_round_remaining=scheduler.get("activeRoundRemaining"),
    )

    def emit_tracking_progress(*, force: bool = False) -> None:
        nonlocal last_progress_attempted
        should_emit = force or (
            tracking_attempted > 0
            and (tracking_attempted % 10 == 0 or tracking_attempted == len(targets))
        )
        if not should_emit or last_progress_attempted == tracking_attempted:
            return
        last_progress_attempted = tracking_attempted
        logger.log(
            "tracking_progress",
            attempted=tracking_attempted,
            succeeded=len(successful_targets),
            failed=tracking_failed,
            added=added,
            total=len(targets),
            elapsed_seconds=round(time.monotonic() - tracking_batch_started, 2),
        )

    try:
        for number in targets:
            tracking_attempted += 1
            order = orders.get(number, {"number": number, "status": "unknown"})
            try:
                response = client.get(f"work-orders/{number}/tracking")
            except Exception as exc:
                checked_at = iso(clock().astimezone(timezone.utc))
                ot_state = state.setdefault("ot", {}).setdefault(str(number), {})
                ot_state.update(
                    {
                        "last_checked_at": checked_at,
                        "last_status_code": 0,
                        "last_error": type(exc).__name__,
                    }
                )
                errors += 1
                tracking_failed += 1
                error_details.append(
                    f"tracking OT {number}: {type(exc).__name__}"
                )
                logger.log(
                    "tracking_ot_error",
                    level="ERROR",
                    ot=number,
                    status=0,
                    error_type=type(exc).__name__,
                )
                emit_tracking_progress()
                continue
            tracking_calls += 1
            api_requests += max(1, int(getattr(response, "attempts", 1)))
            if response.rate_limit_remaining is not None:
                min_remaining = (
                    response.rate_limit_remaining
                    if min_remaining is None
                    else min(min_remaining, response.rate_limit_remaining)
                )
            observed_dt = clock().astimezone(timezone.utc)
            observed_at = iso(observed_dt)
            ot_state = state.setdefault("ot", {}).setdefault(str(number), {})
            previous_success = ot_state.get("last_successful_check_at")
            ot_state["last_checked_at"] = observed_at
            ot_state["last_status_code"] = response.status_code
            ot_state["last_error"] = (
                None if response.ok else f"http_status_{response.status_code}"
            )

            if response.status_code == 404:
                ot_state.update(
                    {
                        "tracking_count": 0,
                        "last_successful_check_at": observed_at,
                    }
                )
                _mark_tracking_reconciled(state, ot_state, observed_at, "live")
                successful_targets.add(number)
                emit_tracking_progress()
                continue
            if not response.ok or not isinstance(response.data, list):
                errors += 1
                tracking_failed += 1
                error_details.append(
                    f"tracking OT {number}: status={response.status_code}"
                )
                logger.log(
                    "tracking_ot_error",
                    level="ERROR",
                    ot=number,
                    status=response.status_code,
                    error_type="http_error" if not response.ok else "contract_error",
                )
                emit_tracking_progress()
                continue

            previous_success_dt = parse_utc(previous_success)
            if previous_success_dt and previous_success_dt <= observed_dt:
                observed_from = previous_success
                observation_kind = "live"
            elif number in new_numbers and discovery.get("windowFrom"):
                observed_from = discovery["windowFrom"]
                observation_kind = "live_new_order"
            else:
                observed_from = None
                observation_kind = (
                    "baseline_clock_regression" if previous_success_dt else "baseline"
                )
            lower_dt = parse_utc(observed_from)
            if lower_dt:
                window_minutes = (observed_dt - lower_dt).total_seconds() / 60.0
                if number in active_numbers:
                    observation_windows.append(window_minutes)
                else:
                    background_observation_windows.append(window_minutes)
            rows = tracking_rows(
                order,
                response.data,
                observed_at,
                observed_from,
                observation_kind,
                window_sla_minutes=int(_arg(args, "interval", 300)) / 60.0,
            )
            ot_added = append_ledger(paths, rows, known)
            added += ot_added
            # Rows found on an order the list did not flag as changed mean the
            # cheap detector missed a real event.  Only meaningful once the OT
            # has a baseline: the first read always adds its whole history.
            if ot_added and previous_success_dt and number not in changed_active:
                undetected_change_rows += ot_added
                undetected_change_orders.add(number)
                logger.log(
                    "tracking_change_undetected_by_list",
                    level="WARNING",
                    ot=number,
                    rows=ot_added,
                )
            ot_state.update(
                {
                    "last_successful_check_at": observed_at,
                    "tracking_count": len(response.data),
                }
            )
            _mark_tracking_reconciled(state, ot_state, observed_at, "live")
            if response.data:
                last_item = response.data[-1] if isinstance(response.data[-1], dict) else {}
                ot_state["last_tracking_id"] = last_item.get("id")
                ot_state["last_tracking_date"] = last_item.get("trackingDate")
            successful_targets.add(number)
            emit_tracking_progress()
    finally:
        if owned_client and isinstance(client, CloudfleetClient):
            client.close()
    emit_tracking_progress(force=True)

    cycle_finished = clock().astimezone(timezone.utc)
    client_requests_after = getattr(client, "request_attempts", None)
    if isinstance(client_requests_before, int) and isinstance(client_requests_after, int):
        api_requests = max(0, client_requests_after - client_requests_before)
    interval = int(_arg(args, "interval", 300))
    poll_sla_seconds = interval
    active_polled = len(active_numbers & set(targets))
    active_backlog = max(0, len(active_numbers) - active_polled)
    tracking_backlog = max(0, len(eligible_numbers) - len(set(targets) & eligible_numbers))
    # Two different questions, deliberately separated.  The per-cycle SLA asks
    # "was every active order read in this cycle", which the request quota makes
    # impossible for a universe larger than the batch; it is kept as a metric
    # only.  The coverage SLA asks "was every active order read recently
    # enough", which is the guarantee the rotation actually provides and the one
    # readiness is judged on.
    coverage_sla_seconds = int(
        _arg(args, "coverage_sla_seconds", DEFAULT_COVERAGE_SLA_SECONDS)
    )
    within_sla = 0
    outside_sla = 0
    within_coverage = 0
    outside_coverage = 0
    ages: list[float] = []
    for number in active_numbers:
        last_success = parse_utc(state.get("ot", {}).get(str(number), {}).get("last_successful_check_at"))
        if last_success is None:
            outside_sla += 1
            continue
        age = max(0.0, (cycle_finished - last_success).total_seconds())
        ages.append(age)
        if age <= poll_sla_seconds:
            within_sla += 1
        else:
            outside_sla += 1
        if age <= coverage_sla_seconds:
            within_coverage += 1
        else:
            outside_coverage += 1

    cycle_duration = max(
        (cycle_finished - cycle_started).total_seconds(), time.monotonic() - monotonic_started
    )
    min_interval = float(
        getattr(
            getattr(client, "config", None),
            "min_request_interval_seconds",
            _arg(args, "delay", DEFAULT_MIN_REQUEST_INTERVAL_SECONDS)
            or DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        )
    )
    average_request_seconds = cycle_duration / max(1, api_requests)
    seconds_per_request = max(min_interval, average_request_seconds)
    safe_capacity = max(0, math.floor((interval * 0.95) / max(seconds_per_request, 0.001)))
    theoretical_request_capacity = max(
        0, math.floor(interval / max(min_interval, 0.001))
    )
    projected_catalog_requests = (
        max(1, int(discovery.get("pages", 1)))
        + int(discovery.get("enrichedOrders", 0))
        + int(discovery.get("enrichmentErrors", 0))
    )
    changed_backlog = len(changed_active - set(targets))
    # MEASURED 2026-08-11: adding a tracking row does NOT advance the work
    # order's ``updatedAt``.  OT 5537 received four seguimientos while its
    # ``updatedAt`` stayed frozen, and in the same minute OT 5478 moved its
    # ``updatedAt`` because ``maintenanceLabels`` changed.  The list therefore
    # detects new orders, status changes and label-field changes, but NEVER a
    # new comment.  Coverage of the whole active universe stays the binding
    # requirement for label events; change detection only improves latency for
    # the signals it can actually see.
    critical_numbers = active_numbers | priority_numbers
    active_required_requests = len(active_numbers) + projected_catalog_requests
    critical_backlog = len(critical_numbers - set(targets))
    projected_requests = len(critical_numbers) + projected_catalog_requests
    max_ots = int(_arg(args, "max_ots_per_cycle", DEFAULT_MAX_OTS_PER_CYCLE))
    full_sweep_cycles = math.ceil(len(active_numbers) / max_ots) if active_numbers else 0
    full_sweep_seconds = full_sweep_cycles * interval
    all_active_scheduled = active_backlog == 0
    never_attempted = sorted(
        number
        for number in active_numbers
        if not state.get("ot", {}).get(str(number), {}).get("last_checked_at")
    )
    rotation_age_seconds = None
    round_started = parse_utc(scheduler.get("activeRoundStartedAt"))
    if round_started:
        rotation_age_seconds = max(0.0, (cycle_finished - round_started).total_seconds())
    rotation_max_age = _env_int("TRACKING_ROTATION_MAX_AGE_SECONDS", 86400)
    # Feasibility now asks the answerable question: does one batch fit in one
    # cycle, and does the rotation close a full round inside the coverage SLA.
    # A label typed as a comment is invisible to the list (see §E2), so only a
    # per-order poll finds it — the rotation must complete, just not every cycle.
    cycle_requests_required = (
        min(len(active_numbers), max_ots) + projected_catalog_requests
    )
    coverage_feasible = (
        full_sweep_seconds <= coverage_sla_seconds if active_numbers else True
    )
    active_poll_sla_feasible = (
        coverage_feasible and cycle_requests_required <= safe_capacity
    )
    capacity_sufficient = (
        active_poll_sla_feasible
        and changed_backlog == 0
        and cycle_duration <= interval
    )
    # An observation window is bounded by how often the rotation revisits an
    # order, not by the cycle interval: with 149 actives and a batch of 95 a
    # window of ~10 minutes is the designed outcome. Judging it against the
    # interval would keep health red by construction, the same defect the
    # coverage SLA fixed for polling freshness.
    late_windows = sum(
        window > coverage_sla_seconds / 60.0 for window in observation_windows
    )
    windows_over_interval = sum(window > interval / 60.0 for window in observation_windows)
    bootstrap_progress = state.get("bootstrapAllTracking")
    if not isinstance(bootstrap_progress, dict):
        bootstrap_progress = {}
    historical_bootstrap_status = str(bootstrap_progress.get("status") or "required")
    historical_total = len(orders)
    historical_completed = sum(
        1
        for number in orders
        if state.get("ot", {})
        .get(str(number), {})
        .get("historical_bootstrap_complete")
    )
    historical_pending = max(0, historical_total - historical_completed)
    # Recompute in both directions.  Latching only complete -> incomplete made
    # the flag a one-way trip: any newly discovered OT leaves `pending` at 1 for
    # the cycle that discovers it, and readiness could never recover afterwards
    # even though that OT gets its full tracking history on its first poll.
    if historical_bootstrap_status in {"complete", "incomplete"}:
        historical_bootstrap_status = "incomplete" if historical_pending else "complete"
        bootstrap_progress["status"] = historical_bootstrap_status
    if bootstrap_progress:
        bootstrap_progress.update(
            {
                "totalOrders": historical_total,
                "completedOrders": historical_completed,
                "pendingOrders": historical_pending,
            }
        )
        state["bootstrapAllTracking"] = bootstrap_progress
    reasons: list[str] = []
    if errors:
        reasons.append("api_errors")
    # `active_backlog` and `outside_sla` stay published as metrics but are not
    # health reasons: with 149 actives and a 30 req/min quota they are on by
    # design, and a permanently red readiness probe reports nothing.
    if outside_coverage:
        reasons.append("active_coverage_sla_breached")
    if not coverage_feasible:
        reasons.append("coverage_sla_infeasible")
    if change_driven and changed_backlog:
        # A signal the list DID surface and the cycle still failed to read is a
        # distinct, more serious miss than an unchanged order awaiting rotation.
        reasons.append("changed_tracking_backlog")
    if never_attempted:
        reasons.append("active_never_polled")
    if pending_full_reconciliation:
        # Statuses came from a snapshot, so the active universe and the
        # administrative-closing KPI are inflated until a full sweep runs.
        reasons.append("catalog_full_reconciliation_pending")
    if rotation_age_seconds is not None and rotation_age_seconds > rotation_max_age:
        reasons.append("rotation_coverage_stale")
    if not capacity_sufficient:
        reasons.append("insufficient_cycle_capacity")
    if late_windows:
        reasons.append("observation_windows_over_sla")
    if rejected_ledger_lines:
        reasons.append("invalid_ledger_lines")
    if historical_bootstrap_status != "complete":
        reasons.append(
            "historical_bootstrap_required"
            if historical_bootstrap_status == "required"
            else "historical_bootstrap_incomplete"
        )
    ready = not reasons
    status = "ready" if ready else "degraded"
    state["last_run_at"] = iso(cycle_finished)
    state["last_successful_cycle_at"] = iso(cycle_finished)
    state["consecutive_failed_cycles"] = 0
    elapsed_wall_seconds = max(0.0, (cycle_finished - cycle_started).total_seconds())
    if elapsed_wall_seconds <= interval:
        next_cycle = cycle_started + timedelta(seconds=interval)
    elif elapsed_wall_seconds < interval * 2:
        # The scheduler permits one immediate catch-up cycle.
        next_cycle = cycle_finished
    else:
        elapsed_periods = math.floor(elapsed_wall_seconds / interval) + 1
        next_cycle = cycle_started + timedelta(seconds=elapsed_periods * interval)
    worker_meta = {
        "status": status,
        "ready": ready,
        "healthReasons": reasons,
        "lastRunAt": state["last_run_at"],
        "lastSuccessfulCycleAt": state["last_successful_cycle_at"],
        "cycleStartedAt": iso(cycle_started),
        "cycleFinishedAt": iso(cycle_finished),
        "cycleDurationSeconds": round(cycle_duration, 2),
        "nextCycleAt": iso(next_cycle),
        "intervalSeconds": interval,
        "pollSlaSeconds": poll_sla_seconds,
        "activeOrders": len(active_numbers),
        "activeUniverse": len(active_numbers),
        "activeOrdersPolled": active_polled,
        "activeOrdersSucceeded": len(active_numbers & successful_targets),
        "activeEverAttempted": sum(
            1
            for number in active_numbers
            if state.get("ot", {}).get(str(number), {}).get("last_checked_at")
        ),
        "activeNeverAttempted": sum(
            1
            for number in active_numbers
            if not state.get("ot", {}).get(str(number), {}).get("last_checked_at")
        ),
        "activeTrackingBacklog": active_backlog,
        "changeDriven": change_driven,
        "changedActiveOrders": len(changed_active),
        "changedActivePolled": len(changed_active & set(targets)),
        "changedTrackingBacklog": changed_backlog,
        "rotationOrdersPolled": max(0, active_polled - len(changed_active & set(targets))),
        "rotationCoverageAgeSeconds": (
            round(rotation_age_seconds, 2) if rotation_age_seconds is not None else None
        ),
        # Falsification of the change-detection assumption. While this is 0,
        # `updatedAt` is a sufficient trigger and rotation can be spaced out.
        "trackingRowsFoundWithoutUpdatedAtChange": undetected_change_rows,
        "ordersWithUndetectedChanges": len(undetected_change_orders),
        "coverageMode": scheduler.get("coverageMode", "rotating_complete"),
        "activeCoverageRound": int(scheduler.get("activeRound") or 0),
        "activeOrdersRemainingInRound": int(scheduler.get("activeRoundRemaining") or 0),
        "completedActiveCoverageRounds": int(scheduler.get("completedActiveRounds") or 0),
        "activeCoverageDeadlineAt": scheduler.get("activeRoundDeadlineAt"),
        "fullSweepCycles": full_sweep_cycles,
        "fullSweepSeconds": full_sweep_seconds,
        "estimatedActiveCoverageCycles": full_sweep_cycles,
        "estimatedActiveCoverageSeconds": full_sweep_seconds,
        "allActiveScheduledThisCycle": all_active_scheduled,
        "activePollSlaFeasible": active_poll_sla_feasible,
        "historicalBootstrapStatus": historical_bootstrap_status,
        "historicalBootstrapTotalOrders": historical_total,
        "historicalBootstrapCompletedOrders": historical_completed,
        "historicalBootstrapPendingOrders": historical_pending,
        "historicalBootstrapFailedOrders": int(
            bootstrap_progress.get("failedOrders") or 0
        ),
        "eligibleOrders": len(eligible_numbers),
        "polledOrders": len(targets),
        "trackingBacklog": tracking_backlog,
        "priorityTrackingBacklog": critical_backlog,
        "capacitySufficient": capacity_sufficient,
        "theoreticalRequestCapacityPerCycle": theoretical_request_capacity,
        "estimatedSafeRequestsPerCycle": safe_capacity,
        "projectedRequestsPerCycle": projected_requests,
        "activeSlaRequiredRequestsPerCycle": active_required_requests,
        "catalogOrders": len(orders),
        "catalogRetention": "indefinite",
        "pendingFullReconciliation": pending_full_reconciliation,
        "catalogStatusesConfirmedThrough": catalog_payload.get("statusesConfirmedThrough"),
        "ordersWithinSla": within_sla,
        "ordersOutsideSla": outside_sla,
        "coverageSlaSeconds": coverage_sla_seconds,
        "ordersWithinCoverageSla": within_coverage,
        "ordersOutsideCoverageSla": outside_coverage,
        "coverageSlaFeasible": coverage_feasible,
        "cycleRequestsRequired": cycle_requests_required,
        "oldestSuccessfulPollAgeSeconds": round(max(ages), 2) if ages else None,
        "maxObservationWindowMinutes": round(max(observation_windows), 2) if observation_windows else None,
        "lateObservationWindows": late_windows,
        "observationWindowsOverInterval": windows_over_interval,
        "backgroundObservationWindows": len(background_observation_windows),
        "maxBackgroundObservationWindowMinutes": (
            round(max(background_observation_windows), 2)
            if background_observation_windows
            else None
        ),
        "apiCalls": api_requests,
        "trackingCalls": tracking_calls,
        "newOrders": int(discovery.get("newOrders", 0)),
        "updatedOrders": int(discovery.get("updatedOrders", 0)),
        "newTrackingRows": added,
        "seededRows": seeded,
        "errors": errors,
        "errorDetails": error_details[:20],
        "minRemaining": min_remaining,
        "consecutiveFailedCycles": 0,
        "catalogSync": discovery,
    }
    publish_started = time.monotonic()
    state["stats"] = worker_meta
    save_state(paths, state)
    ledger, _, _ = read_ledger(paths)
    dashboard = build_dashboard(ledger, state, worker_meta, orders)
    atomic_write_json(paths.dashboard, dashboard)
    _write_health(paths, worker_meta)
    logger.log(
        "publish_done",
        ledger_rows=len(ledger),
        label_events=int(dashboard.get("kpis", {}).get("labelEvents", 0)),
        dashboard_orders=len(dashboard.get("orders", [])),
        elapsed_seconds=round(time.monotonic() - publish_started, 2),
    )
    logger.log(
        "cycle_complete",
        level="INFO" if ready else "WARNING",
        status=status,
        ready=ready,
        duration_seconds=round(cycle_duration, 2),
        attempted=tracking_attempted,
        succeeded=len(successful_targets),
        failed=tracking_failed,
        added=added,
        active_orders=len(active_numbers),
        active_backlog=active_backlog,
        api_calls=api_requests,
        errors=errors,
        reasons=reasons,
    )
    return worker_meta


def bootstrap_all_tracking(
    args: argparse.Namespace,
    client: CloudfleetClientProtocol | None = None,
    *,
    paths: WorkerPaths | None = None,
    clock: Callable[[], datetime] = now_utc,
    logger: OperationalLogger | None = None,
) -> dict[str, Any]:
    """Build the complete historical tracking baseline, resumably.

    The work-order date range is used only to discover OT numbers.  For every
    catalogued OT this function calls the unfiltered per-order tracking endpoint,
    whose observed contract returns the complete history.  Each OT is
    checkpointed before and after its request; successful and 404 responses are
    not requested again on a resumed bootstrap.
    """
    logger = logger or OperationalLogger(_arg(args, "log_level", None))
    validate_args(args)
    paths = paths or WorkerPaths.from_runtime(_arg(args, "runtime_dir", None))
    paths.runtime.mkdir(parents=True, exist_ok=True)
    started_at = clock().astimezone(timezone.utc)
    monotonic_started = time.monotonic()
    state = load_state(paths)
    ledger, rejected_ledger_lines, needs_compaction = read_ledger(paths)
    compacted = False
    if needs_compaction and bool(_arg(args, "compact_ledger", True)):
        compact_ledger(paths, ledger)
        rejected_ledger_lines = 0
        compacted = True
    logger.log(
        "ledger_loaded",
        rows=len(ledger),
        rejected_lines=rejected_ledger_lines,
        compacted=compacted,
    )
    if not bool(_arg(args, "skip_seed", False)):
        seed_state = state.get("certifiedHistoricalSeed")
        seed_manifest = _certified_seed_dir(args) / "historical_seed_manifest.json"
        if seed_manifest.is_file() and (
            not isinstance(seed_state, dict) or seed_state.get("status") != "imported"
        ):
            import_certified_historical_seed(
                paths,
                state,
                ledger,
                _certified_seed_dir(args),
                logger,
            )
            ledger, _, _ = read_ledger(paths)
    progress = state.get("bootstrapAllTracking")
    if not isinstance(progress, dict):
        progress = {}
    previous_status = str(progress.get("status") or "")
    _, existing_catalog_payload = load_catalog(paths)
    catalog_full_confirmed = (
        existing_catalog_payload.get("catalogScope") == "historical_all_statuses"
        and bool(existing_catalog_payload.get("catalogFullConfirmedAt"))
        and not bool(existing_catalog_payload.get("pendingFullReconciliation"))
        and bool(progress.get("catalogComplete"))
    )
    resume = (
        previous_status
        in {"running", "incomplete", "complete", "reconciliation_required"}
        and catalog_full_confirmed
    )
    cumulative_bootstrap_rows = sum(
        1
        for row in ledger
        if row.get("tracking_observation_kind")
        in {"historical_bootstrap", "historical_certified_seed"}
    )

    owned_client = client is None
    if client is None:
        config = ClientConfig.from_environment(_arg(args, "env_file", None))
        delay = _arg(args, "delay", None)
        if delay is not None:
            config = replace(config, min_request_interval_seconds=float(delay))
        client = CloudfleetClient(config, logger=logger)

    progress.update(
        {
            "schemaVersion": 1,
            "status": "running",
            "phase": "catalog",
            "startedAt": progress.get("startedAt") or iso(started_at),
            "lastRunAt": iso(started_at),
            "completedAt": None,
            "currentOrder": None,
            "attemptedThisRun": 0,
            "addedRowsThisRun": 0,
            "addedRows": cumulative_bootstrap_rows,
        }
    )
    state["bootstrapAllTracking"] = progress
    save_state(paths, state)

    try:
        # A resumed tracking pass only needs the cheap incremental discovery.
        # The first invocation performs the all-status historical catalog scan.
        catalog_sync = sync(
            FULL_CATALOG_STATUSES,
            None,
            int(_arg(args, "catalog_max_pages", 200)),
            str(_arg(args, "catalog_from_date", "2021-01-01")),
            mode="incremental" if resume else "full",
            client=client,
            catalog_file=paths.catalog,
            overlap_seconds=int(_arg(args, "discovery_overlap_seconds", 120)),
            # Historical catalog rows already contain the identifiers/fields
            # needed for tracking.  Enriching thousands of newly discovered
            # closed OTs would create a detail-call backlog ahead of live polls.
            enrich_new=False,
            max_enrichments=0,
            now=started_at,
            logger=logger,
        )
        orders, catalog_payload = load_catalog(paths)
        progress["phase"] = "tracking"
        progress["catalogScope"] = catalog_payload.get(
            "catalogScope", "historical_all_statuses"
        )
        progress["catalogComplete"] = (
            catalog_payload.get("catalogScope") == "historical_all_statuses"
            and bool(catalog_payload.get("catalogFullConfirmedAt"))
            and not bool(catalog_payload.get("pendingFullReconciliation"))
        )
        progress["catalogCompletedAt"] = catalog_payload.get("catalogFullConfirmedAt")
        progress["catalogOrders"] = len(orders)

        # A successful earlier live request already read the complete per-OT
        # response.  For a certified-seed OT, the baseline alone is explicitly
        # insufficient: it needs proof tied to the imported seed (or a migrated
        # successful checkpoint after that import).
        _normalize_certified_seed_reconciliation(state)
        for number in orders:
            ot_state = state.setdefault("ot", {}).setdefault(str(number), {})
            if (
                not ot_state.get("historical_seed_certified")
                and ot_state.get("baseline_complete")
            ):
                ot_state.setdefault("historical_bootstrap_complete", True)

        active_statuses = _active_statuses(args)
        pending = sorted(
            (
                number
                for number in orders
                if not state.get("ot", {})
                .get(str(number), {})
                .get("historical_bootstrap_complete")
            ),
            key=lambda number: (
                1
                if str(orders[number].get("status") or "").casefold()
                in active_statuses
                else 0,
                number,
            ),
        )
        total_orders = len(orders)
        progress.update(
            {
                "totalOrders": total_orders,
                "completedOrders": total_orders - len(pending),
                "pendingOrders": len(pending),
                "failedOrders": 0,
                "ordersPerMinute": None,
                "etaSeconds": None,
                "progressUpdatedAt": iso(started_at),
            }
        )
        save_state(paths, state)
        logger.log(
            "bootstrap_tracking_start",
            total=total_orders,
            pending=len(pending),
            completed=total_orders - len(pending),
            resumed=resume,
        )
        _publish_bootstrap_progress_snapshot(
            paths,
            progress,
            elapsed_seconds=max(0.0, time.monotonic() - monotonic_started),
            orders_per_minute=None,
            eta_seconds=None,
            updated_at=str(progress["progressUpdatedAt"]),
        )

        known = {ledger_identity(row) for row in ledger}
        attempted = 0
        succeeded = 0
        failed = 0
        added = 0
        last_progress_attempted = -1

        def emit_progress(*, force: bool = False) -> None:
            nonlocal last_progress_attempted
            should_emit = force or (
                attempted > 0 and (attempted % 10 == 0 or attempted == len(pending))
            )
            if not should_emit or last_progress_attempted == attempted:
                return
            last_progress_attempted = attempted
            elapsed = max(0.0, time.monotonic() - monotonic_started)
            completed_global = int(progress.get("completedOrders") or 0)
            remaining_global = max(0, total_orders - completed_global)
            orders_per_minute = (
                attempted * 60.0 / elapsed if attempted > 0 and elapsed > 0 else None
            )
            eta_seconds = (
                remaining_global * 60.0 / orders_per_minute
                if orders_per_minute and orders_per_minute > 0
                else None
            )
            progress_updated_at = iso(clock().astimezone(timezone.utc))
            progress.update(
                {
                    "ordersPerMinute": (
                        round(orders_per_minute, 2)
                        if orders_per_minute is not None
                        else None
                    ),
                    "etaSeconds": (
                        round(eta_seconds, 2) if eta_seconds is not None else None
                    ),
                    "progressUpdatedAt": progress_updated_at,
                }
            )
            save_state(paths, state)
            logger.log(
                "bootstrap_tracking_progress",
                attempted=attempted,
                succeeded=succeeded,
                failed=failed,
                added=added,
                completed=completed_global,
                remaining=remaining_global,
                pending_at_start=len(pending),
                total=total_orders,
                orders_per_minute=(
                    round(orders_per_minute, 2) if orders_per_minute is not None else None
                ),
                eta_seconds=round(eta_seconds, 2) if eta_seconds is not None else None,
                elapsed_seconds=round(elapsed, 2),
            )
            _publish_bootstrap_progress_snapshot(
                paths,
                progress,
                elapsed_seconds=elapsed,
                orders_per_minute=orders_per_minute,
                eta_seconds=eta_seconds,
                updated_at=progress_updated_at,
            )

        for number in pending:
            attempted += 1
            attempt_started = clock().astimezone(timezone.utc)
            progress.update(
                {
                    "currentOrder": number,
                    "lastAttemptAt": iso(attempt_started),
                    "attemptedThisRun": attempted,
                }
            )
            # Required pre-request checkpoint. If the process dies after this
            # write, the OT remains pending and is retried safely next run.
            save_state(paths, state)
            ot_state = state.setdefault("ot", {}).setdefault(str(number), {})
            try:
                response = client.get(f"work-orders/{number}/tracking")
            except Exception as exc:
                failed += 1
                checked_at = iso(clock().astimezone(timezone.utc))
                ot_state.update(
                    {
                        "last_checked_at": checked_at,
                        "last_status_code": 0,
                        "last_error": type(exc).__name__,
                    }
                )
                progress.update(
                    {
                        "failedOrders": failed,
                        "pendingOrders": total_orders
                        - int(progress.get("completedOrders") or 0),
                        "lastErrorType": type(exc).__name__,
                    }
                )
                save_state(paths, state)
                logger.log(
                    "bootstrap_tracking_ot_error",
                    level="ERROR",
                    ot=number,
                    status=0,
                    error_type=type(exc).__name__,
                )
                emit_progress()
                continue

            observed_at = iso(clock().astimezone(timezone.utc))
            ot_state.update(
                {
                    "last_checked_at": observed_at,
                    "last_status_code": response.status_code,
                    "last_error": None
                    if response.ok or response.status_code == 404
                    else f"http_status_{response.status_code}",
                }
            )
            if response.status_code == 404:
                tracking_payload: list[dict[str, Any]] = []
            elif response.ok and isinstance(response.data, list):
                tracking_payload = response.data
            else:
                failed += 1
                progress.update(
                    {
                        "failedOrders": failed,
                        "pendingOrders": total_orders
                        - int(progress.get("completedOrders") or 0),
                        "lastErrorType": "http_error"
                        if not response.ok
                        else "contract_error",
                    }
                )
                save_state(paths, state)
                logger.log(
                    "bootstrap_tracking_ot_error",
                    level="ERROR",
                    ot=number,
                    status=response.status_code,
                    error_type="http_error" if not response.ok else "contract_error",
                )
                emit_progress()
                continue

            rows = tracking_rows(
                orders[number],
                tracking_payload,
                observed_at,
                None,
                "historical_bootstrap",
            )
            added += append_ledger(paths, rows, known)
            succeeded += 1
            ot_state.update(
                {
                    "last_successful_check_at": observed_at,
                    "tracking_count": len(tracking_payload),
                    "historical_bootstrap_completed_at": observed_at,
                }
            )
            _mark_tracking_reconciled(
                state, ot_state, observed_at, "historical_bootstrap"
            )
            progress.update(
                {
                    "completedOrders": int(progress.get("completedOrders") or 0) + 1,
                    "pendingOrders": total_orders
                    - (int(progress.get("completedOrders") or 0) + 1),
                    "failedOrders": failed,
                    "currentOrder": None,
                    "addedRowsThisRun": added,
                    "addedRows": cumulative_bootstrap_rows + added,
                }
            )
            # Required post-response checkpoint, after the durable ledger append.
            save_state(paths, state)
            emit_progress()

        emit_progress(force=True)
        remaining = [
            number
            for number in orders
            if not state.get("ot", {})
            .get(str(number), {})
            .get("historical_bootstrap_complete")
        ]
        complete = not remaining and bool(progress.get("catalogComplete"))
        finished_at = clock().astimezone(timezone.utc)
        progress.update(
            {
                "status": "complete" if complete else "incomplete",
                "phase": "publish",
                "completedAt": iso(finished_at) if complete else None,
                "lastRunFinishedAt": iso(finished_at),
                "currentOrder": None,
                "totalOrders": total_orders,
                "completedOrders": total_orders - len(remaining),
                "pendingOrders": len(remaining),
                "failedOrders": failed,
                "attemptedThisRun": attempted,
                "addedRowsThisRun": added,
                "addedRows": cumulative_bootstrap_rows + added,
            }
        )
        save_state(paths, state)

        worker_meta = {
            "status": "bootstrap_complete" if complete else "bootstrap_incomplete",
            "ready": False,
            "healthReasons": [
                "live_worker_not_started"
            ]
            + ([] if complete else ["historical_bootstrap_incomplete"]),
            "mode": "bootstrap_all_tracking",
            "lastRunAt": iso(finished_at),
            "lastSuccessfulCycleAt": state.get("last_successful_cycle_at"),
            "cycleStartedAt": iso(started_at),
            "cycleFinishedAt": iso(finished_at),
            "cycleDurationSeconds": round(time.monotonic() - monotonic_started, 2),
            "intervalSeconds": int(_arg(args, "interval", 300)),
            "catalogOrders": total_orders,
            "catalogRetention": "indefinite",
            "historicalBootstrapStatus": progress["status"],
            "historicalBootstrapTotalOrders": total_orders,
            "historicalBootstrapCompletedOrders": progress["completedOrders"],
            "historicalBootstrapPendingOrders": progress["pendingOrders"],
            "historicalBootstrapFailedOrders": failed,
            "historicalBootstrapAttemptedThisRun": attempted,
            "historicalBootstrapOrdersPerMinute": progress.get("ordersPerMinute"),
            "historicalBootstrapEtaSeconds": progress.get("etaSeconds"),
            "historicalBootstrapProgressUpdatedAt": progress.get("progressUpdatedAt"),
            "trackingCalls": attempted,
            "newTrackingRows": added,
            "errors": failed,
            "consecutiveFailedCycles": 0,
            "catalogSync": catalog_sync,
        }
        state["stats"] = worker_meta
        save_state(paths, state)
        refreshed_ledger, _, _ = read_ledger(paths)
        dashboard = build_dashboard(refreshed_ledger, state, worker_meta, orders)
        atomic_write_json(paths.dashboard, dashboard)
        _write_health(paths, worker_meta)
        progress["phase"] = "complete" if complete else "incomplete"
        save_state(paths, state)
        logger.log(
            "publish_done",
            ledger_rows=len(refreshed_ledger),
            label_events=int(dashboard.get("kpis", {}).get("labelEvents", 0)),
            dashboard_orders=len(dashboard.get("orders", [])),
            elapsed_seconds=round(time.monotonic() - monotonic_started, 2),
        )
        logger.log(
            "bootstrap_tracking_complete",
            level="INFO" if complete else "WARNING",
            status=progress["status"],
            total=total_orders,
            completed=progress["completedOrders"],
            pending=progress["pendingOrders"],
            failed=failed,
            attempted=attempted,
            added=added,
            elapsed_seconds=round(time.monotonic() - monotonic_started, 2),
        )
        return {
            "status": progress["status"],
            "totalOrders": total_orders,
            "completedOrders": progress["completedOrders"],
            "pendingOrders": progress["pendingOrders"],
            "failedOrders": failed,
            "attemptedThisRun": attempted,
            "addedRows": added,
            "catalogSync": catalog_sync,
        }
    except BaseException as exc:
        # Keep a resumable marker even on Ctrl+C/SystemExit; do not persist API
        # bodies or credentials in the checkpoint.
        progress.update(
            {
                "status": "incomplete",
                "lastRunFinishedAt": iso(clock().astimezone(timezone.utc)),
                "lastErrorType": type(exc).__name__,
            }
        )
        save_state(paths, state)
        raise
    finally:
        if owned_client and isinstance(client, CloudfleetClient):
            client.close()


def record_cycle_failure(
    paths: WorkerPaths,
    args: argparse.Namespace,
    exc: BaseException,
    cycle_started: datetime,
    logger: OperationalLogger | None = None,
) -> dict[str, Any]:
    state = load_state(paths)
    failures = int(state.get("consecutive_failed_cycles", 0)) + 1
    finished = now_utc()
    state["last_run_at"] = iso(finished)
    state["consecutive_failed_cycles"] = failures
    state["last_error"] = {
        "at": iso(finished),
        "type": type(exc).__name__,
        "message": type(exc).__name__,
    }
    meta = {
        "status": "error",
        "ready": False,
        "healthReasons": ["cycle_failed"],
        "lastRunAt": state["last_run_at"],
        "lastSuccessfulCycleAt": state.get("last_successful_cycle_at"),
        "cycleStartedAt": iso(cycle_started),
        "cycleFinishedAt": iso(finished),
        "cycleDurationSeconds": round((finished - cycle_started).total_seconds(), 2),
        "nextCycleAt": iso(cycle_started + timedelta(seconds=int(_arg(args, "interval", 300)))),
        "intervalSeconds": int(_arg(args, "interval", 300)),
        "errors": 1,
        "errorDetails": [type(exc).__name__],
        "consecutiveFailedCycles": failures,
    }
    state["stats"] = meta
    save_state(paths, state)
    _write_health(paths, meta)
    if logger:
        logger.log(
            "cycle_complete",
            level="ERROR",
            status="error",
            ready=False,
            duration_seconds=meta["cycleDurationSeconds"],
            errors=1,
            error_type=type(exc).__name__,
            reasons=["cycle_failed"],
        )
    return meta


def healthcheck(paths: WorkerPaths, max_age_seconds: int) -> tuple[bool, dict[str, Any]]:
    health = read_json(paths.health, {})
    if not isinstance(health, dict) or not health:
        return False, {"ready": False, "reasons": ["health_file_missing"]}
    generated = parse_utc(health.get("generatedAt"))
    reasons = list(health.get("reasons") or [])
    if generated is None:
        reasons.append("health_timestamp_invalid")
    else:
        age = (now_utc() - generated).total_seconds()
        health["ageSeconds"] = round(age, 2)
        if age > max_age_seconds:
            reasons.append("health_stale")
    ready = bool(health.get("ready")) and not reasons
    health["ready"] = ready
    health["reasons"] = sorted(set(reasons))
    return ready, health


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Worker productivo de etiquetas Cloudfleet")
    parser.add_argument("--once", action="store_true", help="ejecuta un ciclo y termina")
    parser.add_argument("--seed-only", action="store_true", help="carga seed y reconstruye dashboard sin API")
    parser.add_argument("--healthcheck", action="store_true", help="probe file-based; exit 0/1")
    parser.add_argument(
        "--bootstrap-all-tracking",
        action="store_true",
        help="cataloga todas las OTs y reanuda su baseline completo de tracking",
    )
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--interval", type=int, default=None)
    parser.add_argument("--delay", type=float, default=None, help="override start-to-start del cliente")
    parser.add_argument("--max-ots-per-cycle", type=int, default=None)
    parser.add_argument("--catalog-max-pages", type=int, default=200)
    parser.add_argument("--max-enrichments", type=int, default=None)
    parser.add_argument("--catalog-from-date", default=None)
    parser.add_argument("--skip-catalog-refresh", action="store_true")
    parser.add_argument("--discovery-overlap-seconds", type=int, default=None)
    parser.add_argument("--catalog-days", type=int, default=14)
    parser.add_argument(
        "--coverage-sla-seconds",
        type=int,
        default=None,
        help="edad maxima aceptable del ultimo sondeo exitoso de una OT activa",
    )
    parser.add_argument("--active-statuses", default=None)
    parser.add_argument("--ot", default="", help="OTs forzadas separadas por coma")
    parser.add_argument("--seed-parquet", default=None)
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--no-compact-ledger", dest="compact_ledger", action="store_false")
    parser.add_argument(
        "--no-change-driven",
        dest="change_driven",
        action="store_false",
        help="desactiva la prioridad por cambio y vuelve a rotacion pura",
    )
    parser.set_defaults(compact_ledger=True, change_driven=None)
    return parser


def apply_environment_defaults(args: argparse.Namespace) -> argparse.Namespace:
    load_environment(args.env_file)
    if args.interval is None:
        args.interval = _env_int("TRACKING_POLL_INTERVAL_SECONDS", 300)
    if args.max_ots_per_cycle is None:
        args.max_ots_per_cycle = _env_int(
            "TRACKING_MAX_ORDERS_PER_CYCLE", DEFAULT_MAX_OTS_PER_CYCLE
        )
    if args.coverage_sla_seconds is None:
        args.coverage_sla_seconds = _env_int(
            "TRACKING_COVERAGE_SLA_SECONDS", DEFAULT_COVERAGE_SLA_SECONDS
        )
    if args.discovery_overlap_seconds is None:
        args.discovery_overlap_seconds = _env_int("TRACKING_DISCOVERY_OVERLAP_SECONDS", 120)
    if args.max_enrichments is None:
        args.max_enrichments = _env_int("TRACKING_MAX_ENRICHMENTS_PER_CYCLE", 5)
    if args.catalog_from_date is None:
        args.catalog_from_date = os.getenv("TRACKING_CATALOG_FROM_DATE", "2021-01-01")
    if args.active_statuses is None:
        args.active_statuses = os.getenv("TRACKING_ACTIVE_STATUSES", ACTIVE_STATUSES_DEFAULT)
    if args.log_level is None:
        args.log_level = os.getenv("TRACKING_LOG_LEVEL", "INFO")
    if getattr(args, "change_driven", None) is None:
        args.change_driven = (
            os.getenv("TRACKING_CHANGE_DRIVEN", "true").strip().casefold()
            not in {"0", "false", "no", "off"}
        )
    # Validate the level before the long-running process starts.
    OperationalLogger(args.log_level)
    validate_args(args)
    return args


def _seed_only(
    args: argparse.Namespace, paths: WorkerPaths, logger: OperationalLogger
) -> int:
    started = time.monotonic()
    logger.log("cycle_start", mode="seed_only", interval_seconds=args.interval)
    state = load_state(paths)
    ledger, rejected, needs_compaction = read_ledger(paths)
    compacted = False
    if needs_compaction and args.compact_ledger:
        compact_ledger(paths, ledger)
        rejected = 0
        compacted = True
    logger.log(
        "ledger_loaded",
        rows=len(ledger),
        rejected_lines=rejected,
        compacted=compacted,
    )
    seeded = seed_ledger_from_parquet(paths, state, _seed_path(args), ledger)
    save_state(paths, state)
    ledger, _, _ = read_ledger(paths)
    orders, _ = load_catalog(paths)
    worker_meta = {
        "status": "seed_only",
        "ready": False,
        "healthReasons": ["worker_not_running"],
        "lastRunAt": iso(),
        "lastSuccessfulCycleAt": state.get("last_successful_cycle_at"),
        "intervalSeconds": args.interval,
        "pollSlaSeconds": args.interval,
        "newTrackingRows": seeded,
    }
    dashboard = build_dashboard(ledger, state, worker_meta, orders)
    atomic_write_json(paths.dashboard, dashboard)
    logger.log(
        "publish_done",
        ledger_rows=len(ledger),
        label_events=int(dashboard.get("kpis", {}).get("labelEvents", 0)),
        dashboard_orders=len(dashboard.get("orders", [])),
        elapsed_seconds=round(time.monotonic() - started, 2),
    )
    logger.log(
        "cycle_complete",
        status="seed_only",
        ready=False,
        duration_seconds=round(time.monotonic() - started, 2),
        added=seeded,
        reasons=["worker_not_running"],
    )
    return 0


def main() -> None:
    args = apply_environment_defaults(build_parser().parse_args())
    paths = WorkerPaths.from_runtime(args.runtime_dir)
    if args.healthcheck:
        ready, payload = healthcheck(
            paths, _env_int("TRACKING_HEALTH_MAX_AGE_SECONDS", 900)
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(0 if ready else 1)
    logger = OperationalLogger(args.log_level)
    logger.log(
        "worker_start",
        mode=(
            "bootstrap_all_tracking"
            if args.bootstrap_all_tracking
            else ("seed_only" if args.seed_only else ("once" if args.once else "continuous"))
        ),
        interval_seconds=args.interval,
        max_orders=args.max_ots_per_cycle,
    )
    paths.runtime.mkdir(parents=True, exist_ok=True)
    with InstanceLock(paths.lock):
        if args.bootstrap_all_tracking:
            result = bootstrap_all_tracking(args, paths=paths, logger=logger)
            raise SystemExit(0 if result.get("status") == "complete" else 1)
        if args.seed_only:
            raise SystemExit(_seed_only(args, paths, logger))
        next_tick = time.monotonic()
        immediate_catchup_used = False
        while True:
            cycle_started = now_utc()
            try:
                run_once(args, paths=paths, logger=logger)
            except KeyboardInterrupt:
                return
            except Exception as exc:
                record_cycle_failure(paths, args, exc, cycle_started, logger=logger)
                if args.once:
                    raise SystemExit(1) from exc
            if args.once:
                return
            next_tick += args.interval
            current = time.monotonic()
            lateness = current - next_tick
            if 0 < lateness < args.interval and not immediate_catchup_used:
                # One immediate cycle can recover a small overrun without
                # waiting almost a full extra period.
                immediate_catchup_used = True
            elif lateness > 0:
                # Skip expired ticks after the one catch-up allowance; never
                # burst repeatedly against the API quota.
                missed = math.floor((current - next_tick) / args.interval) + 1
                next_tick += missed * args.interval
                immediate_catchup_used = False
            else:
                immediate_catchup_used = False
            sleep_for = max(0.0, next_tick - time.monotonic())
            logger.log(
                "sleep_scheduled",
                sleep_seconds=round(sleep_for, 2),
                next_cycle_at=iso(now_utc() + timedelta(seconds=sleep_for)),
                immediate_catchup=bool(sleep_for == 0),
            )
            try:
                time.sleep(sleep_for)
            except KeyboardInterrupt:
                return


if __name__ == "__main__":
    main()
