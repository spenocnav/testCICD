from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from cloudfleet_api import CallResult  # noqa: E402
from operational_logging import OperationalLogger  # noqa: E402
from sync_order_catalog import sync  # noqa: E402
from tracking_label_worker import WorkerPaths, build_parser, run_once  # noqa: E402


def response(data: Any, status: int = 200, error: str | None = None) -> CallResult:
    return CallResult(
        ok=200 <= status < 400,
        status_code=status,
        duration_ms=1,
        data=data,
        error=error,
        rate_limit_remaining=20,
        rate_limit_reset=None,
        url="https://should-never-be-logged.invalid/private",
        utc_iso="2026-08-11T17:00:00Z",
        attempts=1,
        headers={"Authorization": "Bearer should-never-be-logged"},
    )


class LoggingFakeClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(min_request_interval_seconds=0.0)

    def get(self, path: str, params=None, timeout=None) -> CallResult:
        del params, timeout
        if path == "work-orders":
            return response([])
        if path.endswith("/tracking"):
            ot = int(path.split("/")[1])
            if ot == 101:
                return response(
                    None,
                    500,
                    "Bearer secret-token comment ABC123 author payload https://private.invalid",
                )
            return response(None, 404, "No Trackings")
        raise AssertionError(f"unexpected fake path: {path}")


class BootstrapFakeClient:
    def get(self, path: str, params=None, timeout=None) -> CallResult:
        del timeout
        assert path == "work-orders"
        assert "status" not in (params or {})
        return response(
            [
                {
                    "number": 500,
                    "status": "opened",
                    "updatedAt": "2026-08-11T17:00:00Z",
                }
            ]
        )


def fixed_clock() -> datetime:
    return datetime(2026, 8, 11, 17, 5, tzinfo=timezone.utc)


def test_logger_drops_sensitive_fields() -> None:
    stream = io.StringIO()
    logger = OperationalLogger(stream=stream)
    logger.log(
        "safe_event",
        rows=3,
        api_key="secret-key",
        headers={"Authorization": "Bearer secret"},
        url="https://private.invalid",
        comment="private comment",
        plate="ABC123",
        author="private author",
        payload={"secret": True},
    )

    record = json.loads(stream.getvalue())
    assert record["event"] == "safe_event"
    assert record["rows"] == 3
    assert set(record) == {"timestamp", "level", "event", "rows"}


def test_run_once_emits_parseable_progress_without_sensitive_api_data(tmp_path: Path) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    paths.catalog.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "generatedAt": "2026-08-11T17:00:00Z",
                "discoveryCursorAt": "2026-08-11T17:00:00Z",
                "orders": [
                    {
                        "number": number,
                        "status": "opened",
                        "vehicleCode": f"PRIVATE-{number}",
                        "updatedAt": "2026-08-11T17:00:00Z",
                    }
                    for number in range(100, 112)
                ],
            }
        ),
        encoding="utf-8",
    )
    args = build_parser().parse_args([])
    args.interval = 300
    args.delay = 0
    args.max_ots_per_cycle = 12
    args.discovery_overlap_seconds = 120
    args.catalog_from_date = "2021-01-01"
    args.active_statuses = "opened,onTechnicalCompletion"
    args.skip_seed = True
    stream = io.StringIO()
    logger = OperationalLogger(stream=stream)

    run_once(
        args,
        LoggingFakeClient(),
        paths=paths,
        clock=fixed_clock,
        logger=logger,
    )

    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    events = [record["event"] for record in records]
    for required in (
        "cycle_start",
        "ledger_loaded",
        "catalog_sync_start",
        "catalog_sync_done",
        "tracking_batch_start",
        "tracking_ot_error",
        "publish_done",
        "cycle_complete",
    ):
        assert required in events
    progress = [record for record in records if record["event"] == "tracking_progress"]
    assert [record["attempted"] for record in progress] == [10, 12]
    assert progress[-1]["succeeded"] == 11
    assert progress[-1]["failed"] == 1
    raw = stream.getvalue().casefold()
    for forbidden in (
        "secret-token",
        "private.invalid",
        "comment abc123",
        "private-100",
        "authorization",
        "payload",
    ):
        assert forbidden not in raw


def test_full_sync_reports_all_status_catalog_progress_by_chunk(tmp_path: Path) -> None:
    stream = io.StringIO()
    logger = OperationalLogger(stream=stream)

    result = sync(
        "opened,onTechnicalCompletion",
        0,
        2,
        "2026-08-11",
        "2026-08-11",
        mode="full",
        client=BootstrapFakeClient(),
        catalog_file=tmp_path / "order_catalog.json",
        now=fixed_clock(),
        logger=logger,
    )

    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    progress = [record for record in records if record["event"] == "catalog_bootstrap_progress"]
    assert result["orders"] == 1
    assert len(progress) == 1
    assert progress[0]["chunk"] == 1
    assert progress[0]["chunks_total"] == 1
    assert progress[0]["catalog_scope"] == "all_statuses"
    assert records[0]["event"] == "catalog_sync_start"
    assert records[-1]["event"] == "catalog_sync_done"
