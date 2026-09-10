"""Portable, retrying HTTP client for the Cloudfleet API.

This module deliberately has no dependency on the parent repository.  The
client is injectable so the worker and catalog synchronizer can be embedded in
another application and tested without network access.
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

try:  # Package import when embedded; fallback keeps direct CLI execution.
    from .operational_logging import OperationalLogger, endpoint_category
except ImportError:  # pragma: no cover
    from operational_logging import OperationalLogger, endpoint_category


DEFAULT_BASE_URL = "https://fleet.cloudfleet.com/api/v1/"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser numerico; valor recibido: {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} debe ser >= {minimum}; valor recibido: {value}")
    return value


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} debe ser entero; valor recibido: {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} debe ser >= {minimum}; valor recibido: {value}")
    return value


def load_environment(explicit_path: str | Path | None = None) -> list[Path]:
    """Load conventional .env locations without overriding process variables."""
    here = Path(__file__).resolve().parent
    candidates: list[Path] = []
    configured = explicit_path or os.getenv("CLOUDFLEET_ENV_FILE")
    if configured:
        candidates.append(Path(configured).expanduser().resolve())
    candidates.extend([here / ".env", Path.cwd() / ".env", here.parent / ".env"])
    seen: set[Path] = set()
    checked: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        checked.append(resolved)
        if resolved.is_file():
            load_dotenv(resolved, override=False)
    return checked


@dataclass(frozen=True)
class ClientConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 45.0
    min_request_interval_seconds: float = 2.5
    max_retries: int = 4
    backoff_base_seconds: float = 1.0
    max_retry_wait_seconds: float = 300.0
    rate_limit_fallback_seconds: float = 60.0

    @classmethod
    def from_environment(cls, env_path: str | Path | None = None) -> "ClientConfig":
        checked = load_environment(env_path)
        api_key = (os.getenv("CLOUDFLEET_API_KEY") or "").strip()
        if not api_key:
            locations = ", ".join(str(path) for path in checked)
            raise RuntimeError(
                "CLOUDFLEET_API_KEY no esta configurada. "
                f"Variables de proceso y archivos revisados: {locations}"
            )
        base_url = (os.getenv("CLOUDFLEET_BASE_URL") or DEFAULT_BASE_URL).strip()
        if not base_url.endswith("/"):
            base_url += "/"
        return cls(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=_env_float("CLOUDFLEET_TIMEOUT_SECONDS", 45.0, 1.0),
            min_request_interval_seconds=_env_float(
                "CLOUDFLEET_MIN_REQUEST_INTERVAL_SECONDS", 2.5, 0.0
            ),
            max_retries=_env_int("CLOUDFLEET_MAX_RETRIES", 4, 0),
            backoff_base_seconds=_env_float("CLOUDFLEET_BACKOFF_BASE_SECONDS", 1.0, 0.0),
            rate_limit_fallback_seconds=_env_float(
                "CLOUDFLEET_RATE_LIMIT_FALLBACK_SECONDS", 60.0, 1.0
            ),
        )


@dataclass
class CallResult:
    ok: bool
    status_code: int
    duration_ms: float
    data: Any
    error: str | None
    rate_limit_remaining: int | None
    rate_limit_reset: int | None
    url: str
    utc_iso: str
    attempts: int = 1
    headers: dict[str, str] | None = None


class CloudfleetClientProtocol(Protocol):
    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> CallResult: ...


class CloudfleetClient:
    """Rate-aware requests client with bounded exponential retries."""

    def __init__(
        self,
        config: ClientConfig,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        logger: OperationalLogger | None = None,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "cloudfleet-label-tracking-worker/1.0",
            }
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_started_at: float | None = None
        self._blocked_until: float = 0.0
        self.request_attempts: int = 0
        self.logger = logger

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> CallResult:
        url = path if path.startswith(("http://", "https://")) else urljoin(
            self.config.base_url, path.lstrip("/")
        )
        started = self._monotonic()
        attempts = 0
        last_error: str | None = None
        last_status = 0
        last_url = url
        last_headers: dict[str, str] = {}
        last_data: Any = None
        last_error_type: str | None = None

        for attempt in range(self.config.max_retries + 1):
            attempts = attempt + 1
            self._throttle()
            request_started = self._monotonic()
            self._last_request_started_at = request_started
            utc_iso = _utc_iso()
            try:
                self.request_attempts += 1
                response = self.session.get(
                    url,
                    params=params,
                    timeout=timeout or self.config.timeout_seconds,
                )
                last_status = response.status_code
                last_url = response.url
                # ``requests`` exposes a case-insensitive mapping, but casting it
                # to ``dict`` preserves the wire casing.  Cloudfleet currently
                # sends lower-case rate-limit headers, so normalize once and keep
                # every subsequent lookup deterministic.
                last_headers = _normalize_headers(response.headers)
                last_data = _decode_response(response)
                last_error = None if response.status_code < 400 else _response_error(response, last_data)
                last_error_type = None if response.status_code < 400 else "http_error"
                remaining = _optional_int(last_headers.get("x-ratelimit-remaining"))
                if response.status_code < 400 and remaining is not None and remaining <= 1:
                    reset_seconds = _reset_seconds(last_headers)
                    wait_seconds = (
                        float(reset_seconds)
                        if reset_seconds is not None
                        else self.config.rate_limit_fallback_seconds
                    )
                    self._blocked_until = max(
                        self._blocked_until, self._monotonic() + wait_seconds + 0.25
                    )
                    if self.logger:
                        category, ot = endpoint_category(path)
                        self.logger.log(
                            "api_rate_limit_pause_scheduled",
                            level="WARNING",
                            endpoint=category,
                            ot=ot,
                            remaining=remaining,
                            wait_seconds=round(wait_seconds + 0.25, 2),
                        )
            except requests.RequestException as exc:
                last_status = 0
                last_error = f"{type(exc).__name__}: {exc}"
                last_error_type = type(exc).__name__
                last_data = None
                last_headers = {}

            if last_status not in RETRYABLE_STATUS_CODES and last_status != 0:
                break
            if attempt >= self.config.max_retries:
                break
            wait_seconds = self._retry_wait(last_headers, attempt, last_status)
            if self.logger:
                category, ot = endpoint_category(path)
                self.logger.log(
                    "api_retry",
                    level="WARNING",
                    endpoint=category,
                    ot=ot,
                    status=last_status,
                    attempt=attempts,
                    next_attempt=attempts + 1,
                    wait_seconds=round(wait_seconds, 2),
                    error_type=last_error_type,
                )
            self._sleep(wait_seconds)

        remaining = _optional_int(last_headers.get("x-ratelimit-remaining"))
        reset = _reset_seconds(last_headers)
        return CallResult(
            ok=200 <= last_status < 400,
            status_code=last_status,
            duration_ms=round((self._monotonic() - started) * 1000, 2),
            data=last_data,
            error=last_error,
            rate_limit_remaining=remaining,
            rate_limit_reset=reset,
            url=last_url,
            utc_iso=utc_iso,
            attempts=attempts,
            headers=last_headers,
        )

    def close(self) -> None:
        self.session.close()

    def _throttle(self) -> None:
        current = self._monotonic()
        wait_for_floor = max(0.0, self._blocked_until - current)
        wait_for_interval = 0.0
        if self._last_request_started_at is not None:
            elapsed = current - self._last_request_started_at
            wait_for_interval = max(0.0, self.config.min_request_interval_seconds - elapsed)
        wait_seconds = max(wait_for_floor, wait_for_interval)
        if wait_seconds > 0:
            self._sleep(wait_seconds)

    def _retry_wait(self, headers: dict[str, str], attempt: int, status_code: int) -> float:
        server_wait = _reset_seconds(headers)
        if server_wait is not None and server_wait >= 0:
            return min(float(server_wait) + 0.25, self.config.max_retry_wait_seconds)
        if status_code == 429:
            return min(
                self.config.rate_limit_fallback_seconds,
                self.config.max_retry_wait_seconds,
            )
        exponential = self.config.backoff_base_seconds * (2**attempt)
        jitter = random.uniform(0.0, min(1.0, exponential * 0.25))
        return min(exponential + jitter, self.config.max_retry_wait_seconds)


def _utc_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _decode_response(response: requests.Response) -> Any:
    if not response.text.strip():
        return None
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError):
        return response.text


def _response_error(response: requests.Response, data: Any) -> str:
    body = json.dumps(data, ensure_ascii=False, default=str) if not isinstance(data, str) else data
    return f"{response.status_code}: {body[:500]}"


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _normalize_headers(headers: Any) -> dict[str, str]:
    """Return a plain, case-insensitive-by-construction header mapping."""
    try:
        items = headers.items()
    except AttributeError:
        return {}
    return {str(key).strip().casefold(): str(value) for key, value in items}


def _reset_seconds(headers: dict[str, str]) -> int | None:
    normalized = _normalize_headers(headers)
    raw = normalized.get("retry-after") or normalized.get("x-ratelimit-reset")
    if raw is None or not str(raw).strip():
        return None
    value = str(raw).strip()
    try:
        numeric = float(value)
        # Cloudfleet currently returns a countdown.  Also accept epoch seconds.
        if numeric > time.time() + 1:
            numeric -= time.time()
        return max(0, int(numeric))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            return max(0, int(parsed.timestamp() - time.time()))
        except (TypeError, ValueError, OverflowError):
            return None


_default_client: CloudfleetClient | None = None


def get(path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> CallResult:
    """Compatibility facade for earlier scripts; initialized lazily."""
    global _default_client
    if _default_client is None:
        _default_client = CloudfleetClient(
            ClientConfig.from_environment(), logger=OperationalLogger()
        )
    return _default_client.get(path, params=params, timeout=timeout)
