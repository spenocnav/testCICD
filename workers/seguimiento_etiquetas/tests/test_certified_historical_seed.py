from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from historical_seed import (
    SEED_ROW_FIELDS,
    SeedValidationError,
    load_certified_seed,
)
from tracking_label_worker import (
    WorkerPaths,
    import_certified_historical_seed,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
BUILDER = PACKAGE_ROOT / "scripts" / "build_historical_seed.py"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def source_fixture(tmp_path: Path, rows: list[dict] | None = None) -> tuple[Path, Path, Path, Path]:
    tracking_dir = tmp_path / "source" / "tracking"
    orders_dir = tmp_path / "source" / "orders"
    progress_file = tmp_path / "source" / "progress.json"
    output_dir = tmp_path / "seed"
    private_url = (
        "https"
        + "://storage.invalid/private?"
        + "X-Amz-"
        + "Signature=not-a-real-signature"
    )
    default_rows = [
        {
            "id": 5001,
            "trackingDate": "2025-01-02T05:00:00Z",
            "comment": "QA_PRIVATE_COMMENT",
            "createdBy": {"id": 91, "name": "QA_PRIVATE_AUTHOR"},
            "files": [{"fileUrl": private_url, "fileName": "private.pdf"}],
        },
        {
            "id": 5002,
            "trackingDate": "2025-01-03T05:00:00Z",
            "comment": "",
            "createdBy": {"id": 92, "name": "SECOND_PRIVATE_AUTHOR"},
            "files": [],
        },
    ]
    write_json(tracking_dir / "1001.json", default_rows if rows is None else rows)
    write_json(tracking_dir / "1002.json", [])
    write_json(
        orders_dir / "catalog.json",
        [
            {
                "number": 1001,
                "vehicleCode": "QA_PRIVATE_PLATE",
                "status": "closed",
                "updatedAt": "2025-01-04T12:00:00Z",
                "primaryGroup": {
                    "id": 7,
                    "name": "Cliente QA",
                    "privateNote": "REMOVE_ME",
                },
                "reason": "QA_PRIVATE_REASON",
                "files": [{"fileUrl": private_url}],
            },
            {
                "number": 1002,
                "vehicleCode": "QA_EMPTY_PLATE",
                "status": "closed",
                "updatedAt": "2025-01-05T12:00:00Z",
            },
        ],
    )
    write_json(
        progress_file,
        {
            "done": [1001, 1002],
            "errors": [],
            "last_run": "2026-08-11T15:53:47.420019Z",
        },
    )
    return tracking_dir, orders_dir, progress_file, output_dir


def run_builder(
    tracking_dir: Path,
    orders_dir: Path,
    progress_file: Path,
    output_dir: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(BUILDER),
            "--tracking-dir",
            str(tracking_dir),
            "--orders-dir",
            str(orders_dir),
            "--progress-file",
            str(progress_file),
            "--output-dir",
            str(output_dir),
            "--snapshot-from",
            "2021-01-01",
            "--snapshot-through",
            "2026-06-25",
        ],
        cwd=tmp_path_parent(output_dir),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def tmp_path_parent(output_dir: Path) -> Path:
    return output_dir.parent


def rebuild_descriptor(seed_dir: Path, descriptor_name: str, data: bytes) -> None:
    manifest_path = seed_dir / "historical_seed_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = manifest[descriptor_name]
    (seed_dir / descriptor["file"]).write_bytes(data)
    descriptor["bytes"] = len(data)
    descriptor["sha256"] = hashlib.sha256(data).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_builder_reconciles_complete_universe_and_redacts_raw_payload(
    tmp_path: Path,
) -> None:
    inputs = source_fixture(tmp_path)
    result = run_builder(*inputs)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    seed = load_certified_seed(inputs[-1])
    assert seed.manifest["sourceEvidence"] == {
        "catalogRange": {"from": "2021-01-01", "to": "2026-06-25"},
        "done": 2,
        "errors": 0,
        "lastRun": "2026-08-11T15:53:47.420019Z",
        "workOrderUniverseSha256": seed.manifest["sourceEvidence"][
            "workOrderUniverseSha256"
        ],
    }
    assert seed.manifest["totals"] == {
        "workOrders": 2,
        "trackingRows": 2,
        "emptyWorkOrders": 1,
        "recognizedLabelRows": 0,
        "redactedFreeTextRows": 1,
    }
    assert seed.manifest["workOrders"] == [
        {"empty": False, "number": 1001, "trackingRows": 2},
        {"empty": True, "number": 1002, "trackingRows": 0},
    ]
    assert len(seed.tracking_rows) == 2
    assert all(set(row) == SEED_ROW_FIELDS for row in seed.tracking_rows)
    assert all(row["tracking_comment"] is None for row in seed.tracking_rows)
    assert all(
        row["tracking_observation_kind"] == "historical_certified_seed"
        for row in seed.tracking_rows
    )
    assert [row["tracking_date"] for row in seed.tracking_rows] == [
        "2025-01-02T05:00:00Z",
        "2025-01-03T05:00:00Z",
    ]
    assert {order["number"] for order in seed.catalog_payload["orders"]} == {
        1001,
        1002,
    }
    assert "reason" not in seed.catalog_payload["orders"][0]
    assert seed.catalog_payload["orders"][0]["primaryGroup"] == {
        "id": 7,
        "name": "Cliente QA",
    }

    delivered = b"\n".join(path.read_bytes() for path in inputs[-1].iterdir())
    for canary in (
        b"QA_PRIVATE_COMMENT",
        b"QA_PRIVATE_AUTHOR",
        b"SECOND_PRIVATE_AUTHOR",
        b"QA_PRIVATE_REASON",
        b"REMOVE_ME",
        b"not-a-real-signature",
    ):
        assert canary not in delivered


def test_imported_seed_is_baseline_but_requires_a_real_api_reconciliation(
    tmp_path: Path,
) -> None:
    inputs = source_fixture(tmp_path)
    result = run_builder(*inputs)
    assert result.returncode == 0, result.stderr
    paths = WorkerPaths.from_runtime(tmp_path / "runtime")
    state = {
        "schemaVersion": 2,
        "ot": {},
        "bootstrapAllTracking": {
            "status": "complete",
            "catalogComplete": True,
        },
    }

    imported = import_certified_historical_seed(
        paths, state, [], seed_dir=inputs[-1]
    )

    assert imported["workOrders"] == 2
    assert imported["trackingRows"] == 2
    progress = state["bootstrapAllTracking"]
    assert progress["status"] == "reconciliation_required"
    assert progress["catalogComplete"] is False
    assert progress["completedOrders"] == 0
    assert progress["pendingOrders"] == 2
    assert state["certifiedHistoricalSeed"]["status"] == "imported"
    for number in (1001, 1002):
        ot_state = state["ot"][str(number)]
        assert ot_state["baseline_complete"] is True
        assert ot_state["historical_seed_certified"] is True
        assert ot_state["historical_bootstrap_complete"] is False
        assert ot_state["tracking_reconciliation_required"] is True
        assert "tracking_reconciled_at" not in ot_state


@pytest.mark.parametrize(
    "faulty_rows",
    [
        [
            {
                "id": None,
                "trackingDate": "2025-01-02T05:00:00Z",
                "comment": None,
            }
        ],
        [
            {
                "id": 5001,
                "trackingDate": "2025-01-02T05:00:00Z",
                "comment": None,
            },
            {
                "id": 5001,
                "trackingDate": "2025-01-03T05:00:00Z",
                "comment": None,
            },
        ],
        [{"id": 5001, "trackingDate": "not-a-timestamp", "comment": None}],
    ],
    ids=["null-id", "duplicate-ot-id", "invalid-timestamp"],
)
def test_builder_rejects_uncertifiable_tracking_rows(
    tmp_path: Path, faulty_rows: list[dict]
) -> None:
    inputs = source_fixture(tmp_path, faulty_rows)
    result = run_builder(*inputs)
    assert result.returncode != 0


def test_loader_rejects_tamper_even_when_descriptor_is_rehashed(tmp_path: Path) -> None:
    inputs = source_fixture(tmp_path)
    result = run_builder(*inputs)
    assert result.returncode == 0, result.stderr
    seed_dir = inputs[-1]
    seed = load_certified_seed(seed_dir)
    altered = [dict(row) for row in seed.tracking_rows]
    altered[0]["tracking_comment"] = "TEXT_MUST_NOT_SURVIVE"
    data = b"".join(
        (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        for row in altered
    )
    rebuild_descriptor(seed_dir, "trackingSeed", data)

    with pytest.raises(SeedValidationError):
        load_certified_seed(seed_dir)


def test_loader_rejects_artifact_checksum_tamper(tmp_path: Path) -> None:
    inputs = source_fixture(tmp_path)
    result = run_builder(*inputs)
    assert result.returncode == 0, result.stderr
    seed_dir = inputs[-1]
    tracking_path = seed_dir / "historical_tracking_seed.ndjson"
    tracking_path.write_bytes(tracking_path.read_bytes() + b"\n")

    with pytest.raises(SeedValidationError):
        load_certified_seed(seed_dir)


def test_loader_rejects_manifest_universe_or_catalog_allowlist_drift(
    tmp_path: Path,
) -> None:
    inputs = source_fixture(tmp_path)
    result = run_builder(*inputs)
    assert result.returncode == 0, result.stderr
    seed_dir = inputs[-1]
    catalog_path = seed_dir / "order_catalog_seed.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["orders"][0]["privatePayload"] = "MUST_BE_REJECTED"
    data = (json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    rebuild_descriptor(seed_dir, "orderCatalogSeed", data)

    with pytest.raises(SeedValidationError):
        load_certified_seed(seed_dir)


@pytest.mark.parametrize(
    "fault",
    ["universe-hash", "snapshot-cutoff", "nested-manifest-field"],
)
def test_loader_rejects_manifest_provenance_drift(
    tmp_path: Path, fault: str
) -> None:
    inputs = source_fixture(tmp_path)
    result = run_builder(*inputs)
    assert result.returncode == 0, result.stderr
    seed_dir = inputs[-1]
    manifest_path = seed_dir / "historical_seed_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if fault == "universe-hash":
        manifest["sourceEvidence"]["workOrderUniverseSha256"] = "0" * 64
    elif fault == "snapshot-cutoff":
        manifest["sourceSnapshotThrough"] = "2026-06-24"
    else:
        manifest["sanitization"]["privateComment"] = "MUST_BE_REJECTED"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SeedValidationError):
        load_certified_seed(seed_dir)
