from __future__ import annotations

import json
from pathlib import Path

from app.main import app


def test_openapi_snapshot_is_current() -> None:
    snapshot = Path(__file__).resolve().parents[1] / "openapi.json"
    assert json.loads(snapshot.read_text(encoding="utf-8")) == app.openapi()


def test_openapi_operation_ids_are_unique() -> None:
    operation_ids = [
        operation["operationId"]
        for path_item in app.openapi()["paths"].values()
        for operation in path_item.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
