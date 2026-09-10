from collections.abc import Mapping
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app

client = TestClient(app)

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


def _assert_security_headers(response_headers: Mapping[str, str]) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert response_headers.get(name) == value


def test_health_returns_simple_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "x-request-id" in {key.lower() for key in response.headers}
    _assert_security_headers(response.headers)
    assert "strict-transport-security" not in response.headers


def test_liveness_returns_simple_ok() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_version() -> None:
    response = client.get("/version")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"]
    assert payload["version"]


def test_readiness_success_returns_200_with_status_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "_check_db", AsyncMock(return_value="ok"))
    monkeypatch.setattr(main_module, "_check_redis", AsyncMock(return_value="ok"))

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "checks": {"database": {"status": "ok"}, "redis": {"status": "ok"}},
    }
    assert "error" not in body["checks"]["database"]
    assert "error" not in body["checks"]["redis"]
    main_module._check_db.assert_awaited_once()
    main_module._check_redis.assert_awaited_once()


def test_readiness_db_failure_returns_503_without_error_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "_check_db", AsyncMock(return_value="fail"))
    monkeypatch.setattr(main_module, "_check_redis", AsyncMock(return_value="ok"))

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "fail"
    assert body["checks"]["database"] == {"status": "fail"}
    assert body["checks"]["redis"] == {"status": "ok"}
    assert "error" not in body["checks"]["database"]


def test_readiness_redis_failure_returns_503_without_error_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "_check_db", AsyncMock(return_value="ok"))
    monkeypatch.setattr(main_module, "_check_redis", AsyncMock(return_value="fail"))

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "fail"
    assert body["checks"]["database"] == {"status": "ok"}
    assert body["checks"]["redis"] == {"status": "fail"}
    assert "error" not in body["checks"]["redis"]


def test_readiness_payload_never_leaks_exception_or_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenConnection:
        async def __aenter__(self) -> None:
            raise RuntimeError("postgresql://user:boom-secret@internal-db/portal_clientes")

        async def __aexit__(self, *args: object) -> None:
            return None

    class BrokenEngine:
        @staticmethod
        def connect() -> BrokenConnection:
            return BrokenConnection()

    monkeypatch.setattr(main_module, "engine", BrokenEngine())
    monkeypatch.setattr(main_module, "_check_redis", AsyncMock(return_value="ok"))

    response = client.get("/health/ready")

    assert response.status_code == 503
    raw = response.text
    assert "boom-secret" not in raw
    assert "RuntimeError" not in raw
    body = response.json()
    assert body["checks"]["database"] == {"status": "fail"}
    assert set(body["checks"]["database"].keys()) == {"status"}


def test_cors_preflight_from_web_origin() -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert response.headers.get("access-control-allow-credentials") == "true"
    _assert_security_headers(response.headers)


def test_api_responses_are_not_cacheable() -> None:
    response = client.get("/api/not-found")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    _assert_security_headers(response.headers)
