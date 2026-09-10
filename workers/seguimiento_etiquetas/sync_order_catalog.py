"""Persistent work-order discovery and reconciliation for the tracking worker.

The five-minute worker calls incremental mode only.  Full reconciliation is an
independent schedulable entrypoint so a historical scan never blocks tracking.
"""
from __future__ import annotations

import argparse
import os
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:  # Package import when embedded; fallback keeps direct CLI execution.
    from .cloudfleet_api import ClientConfig, CloudfleetClient, CloudfleetClientProtocol, load_environment
    from .operational_logging import OperationalLogger
    from .production_io import InstanceLock, atomic_write_json, read_json, utc_iso
except ImportError:  # pragma: no cover
    from cloudfleet_api import ClientConfig, CloudfleetClient, CloudfleetClientProtocol, load_environment
    from operational_logging import OperationalLogger
    from production_io import InstanceLock, atomic_write_json, read_json, utc_iso


HERE = Path(__file__).resolve().parent
DEFAULT_FROM_DATE = "2021-01-01"
DEFAULT_ACTIVE_STATUSES = "opened,onTechnicalCompletion"
PAGE_SIZE = 50
# Cloudfleet rejects an ``updatedAt`` range wider than 180 days with 409.
MAX_WINDOW_DAYS = 175

SCALAR_ORDER_FIELDS = {
    "number",
    "vehicleCode",
    "status",
    "type",
    "updatedAt",
    "startDate",
    "workshopDate",
    "technicalCompletionDate",
    "finalCompletionDate",
    "affectsVehicleAvailability",
}
NAMED_ORDER_FIELDS = {
    "primaryGroup",
    "group1",
    "secundaryGroup",
    "group2",
    "costCenter",
    "city",
    "vendor",
}
# Closed vocabulary emitted by Cloudfleet on the work order itself.  It is not
# the controlled label catalog and never produces a label event; it is kept as
# a cheap fleet-wide change signal and as context for the consumer.
STRING_LIST_ORDER_FIELDS = {"maintenanceLabels"}


def sanitize_named(value: Any) -> dict[str, Any] | str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    cleaned = {key: value.get(key) for key in ("id", "code", "name") if value.get(key) is not None}
    return cleaned or None


def sanitize_string_list(value: Any) -> list[str] | None:
    """Keep a bounded list of short labels; reject free text and blobs."""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return None
    cleaned = [
        " ".join(str(item).split())[:120]
        for item in value[:20]
        if item is not None and str(item).strip()
    ]
    return cleaned or None


def sanitize_order(order: dict[str, Any]) -> dict[str, Any]:
    """Persist an allowlist only: never attachments, URLs, blobs or free notes."""
    cleaned = {field: order.get(field) for field in SCALAR_ORDER_FIELDS if order.get(field) is not None}
    for field in NAMED_ORDER_FIELDS:
        value = sanitize_named(order.get(field))
        if value is not None:
            cleaned[field] = value
    for field in STRING_LIST_ORDER_FIELDS:
        value = sanitize_string_list(order.get(field))
        if value is not None:
            cleaned[field] = value
    return cleaned


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _api_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _response_rows(response: Any, endpoint: str) -> list[dict[str, Any]]:
    if response.status_code == 404:
        return []
    if not response.ok:
        raise RuntimeError(f"{endpoint} fallo: status={response.status_code}")
    if not isinstance(response.data, list):
        raise RuntimeError(f"{endpoint} devolvio un contrato inesperado: se esperaba una lista")
    return [row for row in response.data if isinstance(row, dict)]


def fetch_pages(
    client: CloudfleetClientProtocol,
    *,
    params: dict[str, Any],
    max_pages: int,
    logger: OperationalLogger | None = None,
    progress_fields: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    signatures: set[tuple[str, ...]] = set()
    pages = 0
    for page in range(1, max_pages + 1):
        response = client.get("work-orders", params={**params, "page": page})
        page_rows = _response_rows(response, "GET /work-orders")
        pages += 1
        if logger and progress_fields is not None:
            logger.log(
                "catalog_bootstrap_progress",
                **progress_fields,
                page=page,
                page_records=len(page_rows),
                accumulated_records=len(rows) + len(page_rows),
            )
        if not page_rows:
            break
        signature = tuple(str(item.get("number")) for item in page_rows[:5])
        if signature in signatures:
            raise RuntimeError("La paginacion de /work-orders repitio una pagina; cursor no avanzado")
        signatures.add(signature)
        rows.extend(item for item in page_rows if item.get("number") is not None)
        if len(page_rows) < PAGE_SIZE:
            break
        if page == max_pages:
            raise RuntimeError(
                f"/work-orders alcanzo max_pages={max_pages} con pagina completa; cursor no avanzado"
            )
    return rows, pages


def fetch_window(
    client: CloudfleetClientProtocol,
    window_from: datetime,
    window_to: datetime,
    max_pages: int,
    logger: OperationalLogger | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch an arbitrary ``updatedAt`` window, honoring the 180-day API cap.

    Cloudfleet rejects a range wider than 180 days with ``409``.  A worker that
    was down longer than that would otherwise fail discovery on every cycle
    forever, because the cursor never advances past a failing request.
    """
    rows: list[dict[str, Any]] = []
    pages = 0
    cursor = window_from
    while cursor <= window_to:
        chunk_end = min(cursor + timedelta(days=MAX_WINDOW_DAYS), window_to)
        fetched, fetched_pages = fetch_pages(
            client,
            params={
                "updatedAtFrom": _api_timestamp(cursor),
                "updatedAtTo": _api_timestamp(chunk_end),
            },
            max_pages=max_pages,
            logger=logger,
        )
        rows.extend(fetched)
        pages += fetched_pages
        if chunk_end >= window_to:
            break
        cursor = chunk_end + timedelta(seconds=1)
    return rows, pages


def fetch_full_catalog(
    client: CloudfleetClientProtocol,
    date_from: str,
    date_to: str,
    max_pages: int,
    logger: OperationalLogger | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch every work order updated in the configured historical range.

    The list endpoint is intentionally queried without ``status``.  Filtering
    by the currently active statuses would make the persisted catalog an
    active-only cache and lose the historical mapping requested by consumers.
    Existing catalog rows are merged later and are never pruned.
    """
    rows: list[dict[str, Any]] = []
    pages = 0
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if start > end:
        raise ValueError("date_from debe ser <= date_to")
    date_chunks = max(1, ((end - start).days // MAX_WINDOW_DAYS) + 1)
    chunk_number = 0
    while start <= end:
        chunk_end = min(start + timedelta(days=MAX_WINDOW_DAYS), end)
        chunk_number += 1
        fetched, fetched_pages = fetch_pages(
            client,
            params={
                "updatedAtFrom": f"{start.isoformat()}T00:00:00Z",
                "updatedAtTo": f"{chunk_end.isoformat()}T23:59:59Z",
            },
            max_pages=max_pages,
            logger=logger,
            progress_fields={
                "chunk": chunk_number,
                "chunks_total": date_chunks,
                "catalog_scope": "all_statuses",
            },
        )
        rows.extend(fetched)
        pages += fetched_pages
        start = chunk_end + timedelta(days=1)
    return rows, pages


def _catalog_payload(path: Path) -> dict[str, Any]:
    payload = read_json(path, {})
    return payload if isinstance(payload, dict) else {}


def _orders_by_number(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for raw in payload.get("orders", []):
        if not isinstance(raw, dict) or raw.get("number") is None:
            continue
        try:
            result[int(raw["number"])] = sanitize_order(raw)
        except (TypeError, ValueError):
            continue
    return result


def _merge_order(current: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    incoming = sanitize_order(incoming)
    if not current:
        return incoming
    # Detail endpoints sometimes omit list-only ownership fields; retain both.
    current_updated = _parse_utc(current.get("updatedAt"))
    incoming_updated = _parse_utc(incoming.get("updatedAt"))
    if current_updated and incoming_updated and current_updated > incoming_updated:
        return sanitize_order({**incoming, **current})
    return sanitize_order({**current, **incoming})


def _enrich_new_orders(
    client: CloudfleetClientProtocol,
    orders: dict[int, dict[str, Any]],
    numbers: Iterable[int],
) -> tuple[int, list[int]]:
    enriched = 0
    failed: list[int] = []
    for number in sorted(set(numbers)):
        response = client.get(f"work-orders/{number}")
        if response.ok and isinstance(response.data, dict):
            orders[number] = _merge_order(orders.get(number), response.data)
            enriched += 1
        else:
            # The list row is a valid fallback. A future incremental pass retries
            # enrichment when this OT changes again.
            failed.append(number)
    return enriched, failed


def sync(
    status_filter: str | None = None,
    delay: float | None = None,
    max_pages: int = 200,
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    mode: str = "incremental",
    client: CloudfleetClientProtocol | None = None,
    catalog_file: Path | None = None,
    overlap_seconds: int = 120,
    enrich_new: bool = True,
    max_enrichments: int = 5,
    now: datetime | None = None,
    logger: OperationalLogger | None = None,
) -> dict[str, Any]:
    """Synchronize the allowlisted catalog and return discovery metadata.

    This is a stable importable API. ``client`` can be a fake in tests or an
    application-owned HTTP adapter implementing ``get``.
    """
    if mode not in {"incremental", "full"}:
        raise ValueError("mode debe ser 'incremental' o 'full'")
    if max_pages <= 0:
        raise ValueError("max_pages debe ser > 0")
    if overlap_seconds < 0:
        raise ValueError("overlap_seconds debe ser >= 0")
    if delay is not None and delay < 0:
        raise ValueError("delay debe ser >= 0")
    if max_enrichments < 0:
        raise ValueError("max_enrichments debe ser >= 0")
    if client is None:
        # Resolve .env before selecting runtime/status/date defaults.
        load_environment()
    status_filter = status_filter or os.getenv("TRACKING_ACTIVE_STATUSES", DEFAULT_ACTIVE_STATUSES)
    date_from = date_from or os.getenv("TRACKING_CATALOG_FROM_DATE", DEFAULT_FROM_DATE)
    runtime = Path(os.getenv("TRACKING_RUNTIME_DIR", str(HERE / "runtime"))).expanduser().resolve()
    target = (catalog_file or (runtime / "order_catalog.json")).resolve()
    owned_client = client is None
    if client is None:
        config = ClientConfig.from_environment()
        if delay is not None:
            config = replace(config, min_request_interval_seconds=max(0.0, delay))
        client = CloudfleetClient(config, logger=logger)

    upper = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    existing_payload = _catalog_payload(target)
    orders = _orders_by_number(existing_payload)
    before = {number: dict(order) for number, order in orders.items()}
    statuses = [value.strip() for value in status_filter.split(",") if value.strip()]
    if not statuses:
        raise ValueError("status_filter debe contener al menos un estado")
    pages = 0
    bootstrap = mode == "full" or not orders or not existing_payload.get("discoveryCursorAt")
    logical_from: datetime | None = None
    query_from: datetime | None = None
    sync_started = time.monotonic()
    if logger:
        logger.log(
            "catalog_sync_start",
            requested_mode=mode,
            effective_mode="full" if bootstrap else "incremental",
            existing_orders=len(orders),
        )

    try:
        if bootstrap:
            fetched, pages = fetch_full_catalog(
                client,
                date_from,
                date_to or upper.date().isoformat(),
                max_pages,
                logger=logger,
            )
        else:
            logical_from = _parse_utc(existing_payload.get("discoveryCursorAt"))
            if logical_from is None:
                raise RuntimeError("Catalogo sin discoveryCursorAt valido")
            if logical_from > upper:
                logical_from = upper
            query_from = logical_from - timedelta(seconds=max(0, overlap_seconds))
            # Chunked: an outage longer than the API's 180-day cap must not turn
            # discovery into a permanently failing request.
            fetched, pages = fetch_window(
                client, query_from, upper, max_pages, logger=logger
            )

        for raw in fetched:
            try:
                number = int(raw["number"])
            except (KeyError, TypeError, ValueError):
                continue
            orders[number] = _merge_order(orders.get(number), raw)

        new_numbers = sorted(number for number in orders if number not in before)
        updated_numbers = sorted(
            number for number in orders if number in before and orders[number] != before[number]
        )
        enriched, enrichment_failed = (0, [])
        pending_enrichment = {
            int(value)
            for value in existing_payload.get("pendingEnrichment", [])
            if str(value).isdigit()
        }
        # A full reconciliation may discover an OT absent from an established
        # catalog. Defer its detail call to the next lightweight incremental
        # cycle instead of extending the maintenance scan or losing enrichment.
        active_status_values = {value.casefold() for value in statuses}
        ordered_new = sorted(
            new_numbers,
            key=lambda number: (
                0
                if str(orders[number].get("status") or "").casefold()
                in active_status_values
                else 1,
                number,
            ),
        )
        deferred_full_new = (
            set(ordered_new)
            if bootstrap and bool(before) and enrich_new
            else set()
        )
        enrichment_candidates = pending_enrichment | (
            set(new_numbers) if not bootstrap else set()
        )
        ordered_enrichment = sorted(
            enrichment_candidates,
            key=lambda number: (
                0
                if str(orders.get(number, {}).get("status") or "").casefold()
                in active_status_values
                else 1,
                number,
            ),
        )
        to_enrich = (
            set(ordered_enrichment[:max_enrichments])
            if enrich_new
            else set()
        )
        deferred_incremental = enrichment_candidates - to_enrich
        if enrich_new and to_enrich:
            enriched, enrichment_failed = _enrich_new_orders(client, orders, to_enrich)

        generated_at = utc_iso()
        with InstanceLock(target.with_suffix(target.suffix + ".lock")):
            # Re-read at commit so a long full reconciliation cannot overwrite
            # incremental discoveries made while it was fetching history.
            latest_payload = _catalog_payload(target)
            latest_orders = _orders_by_number(latest_payload)
            for number, order in orders.items():
                latest_orders[number] = _merge_order(latest_orders.get(number), order)
            orders = latest_orders
            latest_cursor = _parse_utc(latest_payload.get("discoveryCursorAt"))
            committed_cursor = max(value for value in (latest_cursor, upper) if value is not None)
            latest_pending = {
                int(value)
                for value in latest_payload.get("pendingEnrichment", [])
                if str(value).isdigit()
            }
            enriched_successfully = to_enrich - set(enrichment_failed)
            pending_after_commit = (
                latest_pending
                | deferred_full_new
                | deferred_incremental
                | set(enrichment_failed)
            ) - enriched_successfully
            if bootstrap:
                catalog_scope = "historical_all_statuses"
                catalog_full_confirmed_at = generated_at
            else:
                # Incremental discovery can preserve a prior full-catalog
                # guarantee, but can never create that guarantee for a legacy
                # active-only/unknown catalog by itself.
                catalog_scope = (
                    latest_payload.get("catalogScope")
                    or existing_payload.get("catalogScope")
                    or "incremental_partial_unknown"
                )
                prior_full_confirmed_at = (
                    latest_payload.get("catalogFullConfirmedAt")
                    or existing_payload.get("catalogFullConfirmedAt")
                )
                if catalog_scope == "historical_all_statuses":
                    prior_confirmed = _parse_utc(prior_full_confirmed_at)
                    catalog_full_confirmed_at = max(
                        value for value in (prior_confirmed, upper) if value is not None
                    ).isoformat()
                else:
                    catalog_full_confirmed_at = prior_full_confirmed_at
            # Only a full sweep re-reads `status` for every known OT, so only a
            # full sweep may clear the flag a certified-seed import raises.
            still_pending = (
                False
                if bootstrap
                else bool(
                    latest_payload.get("pendingFullReconciliation")
                    or existing_payload.get("pendingFullReconciliation")
                )
            )
            payload = {
                "schemaVersion": 2,
                "generatedAt": generated_at,
                "catalogScope": catalog_scope,
                "pendingFullReconciliation": still_pending,
                "statusesConfirmedThrough": (
                    generated_at
                    if bootstrap
                    else latest_payload.get("statusesConfirmedThrough")
                    or existing_payload.get("statusesConfirmedThrough")
                ),
                "catalogFullConfirmedAt": catalog_full_confirmed_at,
                "retentionPolicy": "indefinite_merge_only",
                "discoveryCursorAt": committed_cursor.isoformat(),
                "lastIncrementalSyncAt": (
                    latest_payload.get("lastIncrementalSyncAt")
                    if bootstrap and latest_payload.get("lastIncrementalSyncAt")
                    else generated_at
                ),
                "lastFullReconciliationAt": (
                    generated_at
                    if bootstrap
                    else latest_payload.get("lastFullReconciliationAt")
                    or existing_payload.get("lastFullReconciliationAt")
                ),
                "statusFilter": status_filter,
                "dateRange": {"from": date_from, "to": date_to or upper.date().isoformat()},
                "pendingEnrichment": sorted(pending_after_commit),
                "orders": [orders[number] for number in sorted(orders)],
                "lastSync": {
                    "mode": "full" if bootstrap else "incremental",
                    "windowFrom": logical_from.isoformat() if logical_from else None,
                    "queryFrom": query_from.isoformat() if query_from else None,
                    "windowTo": upper.isoformat(),
                    "pages": pages,
                    "records": len(fetched),
                    "newOrders": len(new_numbers),
                    "updatedOrders": len(updated_numbers),
                    "enrichedOrders": enriched,
                    "enrichmentErrors": len(enrichment_failed),
                    "deferredEnrichment": len(pending_after_commit),
                },
            }
            atomic_write_json(target, payload)
        result = {
            "catalogFile": str(target),
            "orders": len(orders),
            "generatedAt": generated_at,
            "catalogScope": payload["catalogScope"],
            "catalogFullConfirmedAt": payload["catalogFullConfirmedAt"],
            "mode": payload["lastSync"]["mode"],
            "windowFrom": payload["lastSync"]["windowFrom"],
            "windowTo": payload["lastSync"]["windowTo"],
            "newOrders": len(new_numbers),
            "newOrderNumbers": new_numbers,
            "updatedOrders": len(updated_numbers),
            "updatedOrderNumbers": updated_numbers,
            "enrichedOrders": enriched,
            "enrichmentErrors": len(enrichment_failed),
            "deferredEnrichment": payload["lastSync"]["deferredEnrichment"],
            "pages": pages,
        }
        if logger:
            logger.log(
                "catalog_sync_done",
                mode=result["mode"],
                orders=result["orders"],
                pages=result["pages"],
                new_orders=result["newOrders"],
                updated_orders=result["updatedOrders"],
                enriched_orders=result["enrichedOrders"],
                enrichment_errors=result["enrichmentErrors"],
                elapsed_seconds=round(time.monotonic() - sync_started, 2),
            )
        return result
    except Exception as exc:
        if logger:
            logger.log(
                "catalog_sync_error",
                level="ERROR",
                requested_mode=mode,
                error_type=type(exc).__name__,
                elapsed_seconds=round(time.monotonic() - sync_started, 2),
            )
        raise
    finally:
        if owned_client and isinstance(client, CloudfleetClient):
            client.close()


def main() -> None:
    load_environment()
    parser = argparse.ArgumentParser(description="Sincroniza el catalogo portable de OTs")
    parser.add_argument("--mode", choices=("incremental", "full"), default="incremental")
    parser.add_argument("--status", default=None)
    parser.add_argument("--delay", type=float, default=None, help="override del intervalo start-to-start")
    parser.add_argument("--max-pages", type=int, default=200)
    parser.add_argument("--from", dest="date_from", default=None)
    parser.add_argument("--to", dest="date_to", default=None)
    parser.add_argument("--overlap-seconds", type=int, default=120)
    parser.add_argument("--no-enrich-new", action="store_true")
    parser.add_argument(
        "--max-enrichments",
        type=int,
        default=int(os.getenv("TRACKING_MAX_ENRICHMENTS_PER_CYCLE", "5")),
    )
    parser.add_argument("--log-level", default=os.getenv("TRACKING_LOG_LEVEL", "INFO"))
    args = parser.parse_args()
    logger = OperationalLogger(args.log_level)
    try:
        sync(
            args.status,
            args.delay,
            args.max_pages,
            args.date_from,
            args.date_to,
            mode=args.mode,
            overlap_seconds=args.overlap_seconds,
            enrich_new=not args.no_enrich_new,
            max_enrichments=args.max_enrichments,
            logger=logger,
        )
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
