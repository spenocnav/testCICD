from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from cloudfleet_api import CallResult  # noqa: E402
from operational_logging import OperationalLogger  # noqa: E402
from tracking_label_worker import WorkerPaths, build_parser, run_once  # noqa: E402


API_KEY_CANARY = "qa-api-key-never-log"
BEARER_CANARY = "Bearer qa-bearer-never-log"
COMMENT_CANARY = "QA_SECRET_COMMENT_NEVER_LOG"
PLATE_CANARY = "QA_SECRET_PLATE_NEVER_LOG"
AUTHOR_CANARY = "QA_SECRET_AUTHOR_NEVER_LOG"
PAYLOAD_CANARY = "QA_SECRET_PAYLOAD_NEVER_LOG"
AMZ_SIGNATURE = "X-Amz-" + "Signature"
AMZ_SECURITY_TOKEN = "X-Amz-" + "Security-Token"


def response(data: Any) -> CallResult:
    return CallResult(
        ok=True,
        status_code=200,
        duration_ms=1.0,
        data=data,
        error=None,
        rate_limit_remaining=20,
        rate_limit_reset=None,
        url=f"https://signed.invalid/object?{AMZ_SIGNATURE}=qa-never-log",
        utc_iso="2026-08-11T17:00:00Z",
        attempts=1,
        headers={"Authorization": BEARER_CANARY},
    )


class SyntheticClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            min_request_interval_seconds=0.0,
            api_key=API_KEY_CANARY,
            authorization=BEARER_CANARY,
        )

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> CallResult:
        del params, timeout
        if path == "work-orders":
            return response(
                [
                    {
                        "number": 900777,
                        "status": "opened",
                        "updatedAt": "2026-08-11T17:04:30Z",
                        "payloadMarker": PAYLOAD_CANARY,
                    }
                ]
            )
        if path == "work-orders/900777":
            return response(
                {
                    "number": 900777,
                    "status": "opened",
                    "vehicleCode": PLATE_CANARY,
                    "primaryGroup": {"name": PAYLOAD_CANARY},
                    "updatedAt": "2026-08-11T17:04:30Z",
                }
            )
        if path == "work-orders/900777/tracking":
            return response(
                [
                    {
                        "id": 800777,
                        "trackingDate": "2026-08-11T05:00:00Z",
                        "comment": COMMENT_CANARY,
                        "createdBy": {"name": AUTHOR_CANARY},
                        "files": [
                            {
                                "url": f"https://signed.invalid/object?"
                                f"{AMZ_SECURITY_TOKEN}=qa-never-log"
                            }
                        ],
                        "payloadMarker": PAYLOAD_CANARY,
                    }
                ]
            )
        raise AssertionError(f"Synthetic endpoint not configured: {path}")


class SequenceClock:
    def __init__(self, *values: str) -> None:
        self.values = iter(
            datetime.fromisoformat(value.replace("Z", "+00:00")) for value in values
        )

    def __call__(self) -> datetime:
        return next(self.values).astimezone(timezone.utc)


class FlushCountingStream(StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


def worker_args() -> Any:
    args = build_parser().parse_args([])
    args.interval = 300
    args.delay = 0.0
    args.max_ots_per_cycle = 1
    args.discovery_overlap_seconds = 120
    args.catalog_from_date = "2021-01-01"
    args.active_statuses = "opened,onTechnicalCompletion"
    args.skip_seed = True
    args.log_level = "INFO"
    return args


def test_mock_cycle_emits_parseable_progress_and_completion_without_sensitive_data(
    tmp_path: Path,
) -> None:
    paths = WorkerPaths.from_runtime(tmp_path)
    paths.runtime.mkdir(parents=True, exist_ok=True)
    paths.catalog.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "generatedAt": "2026-08-11T17:00:30Z",
                "discoveryCursorAt": "2026-08-11T17:00:30Z",
                "orders": [
                    {
                        "number": 900001,
                        "status": "closed",
                        "updatedAt": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    stream = FlushCountingStream()
    logger = OperationalLogger("INFO", stream=stream)

    run_once(
        worker_args(),
        SyntheticClient(),
        paths=paths,
        clock=SequenceClock(
            "2026-08-11T17:05:00Z",
            "2026-08-11T17:05:30Z",
            "2026-08-11T17:05:31Z",
        ),
        logger=logger,
    )

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    records = [json.loads(line) for line in lines]
    assert records
    assert stream.flushes == len(records)
    assert all({"timestamp", "level", "event"} <= record.keys() for record in records)

    events = [record["event"] for record in records]
    expected = {
        "cycle_start",
        "ledger_loaded",
        "catalog_sync_start",
        "catalog_sync_done",
        "tracking_batch_start",
        "tracking_progress",
        "publish_done",
        "cycle_complete",
    }
    assert expected <= set(events)
    assert events.index("cycle_start") < events.index("tracking_progress")
    assert events.index("tracking_progress") < events.index("cycle_complete")

    final_progress = [record for record in records if record["event"] == "tracking_progress"][-1]
    assert final_progress == {
        **{key: final_progress[key] for key in ("timestamp", "level", "event")},
        "attempted": 1,
        "succeeded": 1,
        "failed": 0,
        "added": 1,
        "total": 1,
        "elapsed_seconds": final_progress["elapsed_seconds"],
    }
    assert isinstance(final_progress["elapsed_seconds"], (int, float))

    forbidden_key_parts = {
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
    for record in records:
        assert not any(
            part in str(key).casefold()
            for key in record
            for part in forbidden_key_parts
        )

    serialized = "\n".join(lines).casefold()
    for canary in (
        API_KEY_CANARY,
        BEARER_CANARY,
        COMMENT_CANARY,
        PLATE_CANARY,
        AUTHOR_CANARY,
        PAYLOAD_CANARY,
        AMZ_SIGNATURE,
        AMZ_SECURITY_TOKEN,
    ):
        assert canary.casefold() not in serialized


def test_logger_drops_sensitive_fields_and_flushes_each_json_line() -> None:
    stream = FlushCountingStream()
    logger = OperationalLogger("INFO", stream=stream)

    logger.log(
        "qa_probe",
        safe_count=1,
        api_key=API_KEY_CANARY,
        authorization=BEARER_CANARY,
        comment=COMMENT_CANARY,
        plate=PLATE_CANARY,
        author=AUTHOR_CANARY,
        payload={"marker": PAYLOAD_CANARY},
    )

    record = json.loads(stream.getvalue())
    assert record["event"] == "qa_probe"
    assert record["safe_count"] == 1
    assert set(record) == {"timestamp", "level", "event", "safe_count"}
    assert stream.flushes == 1
