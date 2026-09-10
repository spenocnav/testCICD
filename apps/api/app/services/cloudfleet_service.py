from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from time import monotonic
from typing import Any, NamedTuple
from urllib.parse import parse_qs, urlsplit

import httpx

from app.core.config import settings
from app.core.logging import get_logger


class CloudfleetError(Exception):
    """Error controlado al crear novedades en Cloudfleet."""


class CloudfleetVehicleNotFoundError(CloudfleetError):
    """Cloudfleet no reconoce la placa enviada al crear una novedad."""


class CloudfleetMeterError(CloudfleetError):
    """Fallo al registrar una lectura de odómetro u horómetro.

    ``uncertain`` indica que no es seguro reintentar automáticamente porque
    CloudFleet pudo haber persistido la lectura antes de cortar la respuesta.
    """

    def __init__(self, message: str, *, uncertain: bool, retryable: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain
        self.retryable = retryable


@dataclass(frozen=True)
class CloudfleetCreateResult:
    issue_number: int | None
    response: dict[str, Any]


_DEFAULT_RATE_LIMIT_COOLDOWN_S = 60.0
_MAX_GET_ATTEMPTS = 4
_RETRYABLE_GET_STATUSES = frozenset({429, 500, 502, 503, 504})
_throttle_lock = asyncio.Lock()
_last_request_monotonic: float | None = None
_rate_limit_until_monotonic: float | None = None
log = get_logger("cloudfleet-client")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def build_issue_payload(
    *,
    vehicle_code: str,
    reported_at: str,
    reported_by_id: int,
    priority: str,
    odometer: Decimal | None,
    comment: str | None,
    send_mail: bool,
) -> dict[str, Any]:
    return {
        "vehicleCode": vehicle_code,
        "reportedAt": reported_at,
        "reportedById": reported_by_id,
        "priority": priority,
        "odometer": _json_safe(odometer),
        "comment": comment,
        "sendMail": send_mail,
    }


async def _throttle() -> None:
    """Respeta el ritmo base y cualquier enfriamiento impuesto por CloudFleet."""
    global _last_request_monotonic, _rate_limit_until_monotonic
    async with _throttle_lock:
        now = monotonic()
        next_request_at = now
        if _last_request_monotonic is not None:
            # La guía de CloudFleet recomienda al menos dos segundos entre
            # llamadas. El margen evita rozar 30/min por jitter del runtime.
            interval = max(60.0 / settings.cloudfleet_requests_per_minute, 2.05)
            next_request_at = max(next_request_at, _last_request_monotonic + interval)
        if _rate_limit_until_monotonic is not None:
            next_request_at = max(next_request_at, _rate_limit_until_monotonic)
        wait = next_request_at - now
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_monotonic = monotonic()
        if (
            _rate_limit_until_monotonic is not None
            and _last_request_monotonic >= _rate_limit_until_monotonic
        ):
            _rate_limit_until_monotonic = None


async def _defer_after_rate_limit(response: httpx.Response) -> None:
    """Bloquea temporalmente todas las solicitudes tras un ``429`` confirmado."""
    global _rate_limit_until_monotonic
    delay = _retry_delay_seconds(response, attempt=1)
    if "Retry-After" not in response.headers:
        delay = _DEFAULT_RATE_LIMIT_COOLDOWN_S
    async with _throttle_lock:
        _rate_limit_until_monotonic = max(
            _rate_limit_until_monotonic or 0.0,
            monotonic() + delay,
        )
    log.warning(
        "cloudfleet_rate_limit_cooldown",
        retry_in_seconds=delay,
        path=response.request.url.path,
    )


def _header_nonnegative_number(response: httpx.Response, name: str) -> float | None:
    raw = response.headers.get(name)
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


async def _observe_rate_limit(response: httpx.Response) -> None:
    """Incorpora los headers de cuota de CloudFleet al limitador local."""
    global _rate_limit_until_monotonic
    if response.status_code == 429:
        await _defer_after_rate_limit(response)
        return

    remaining = _header_nonnegative_number(response, "X-RateLimit-Remaining")
    reset = _header_nonnegative_number(response, "X-RateLimit-Reset")
    if remaining is None or remaining > 0 or reset is None:
        return

    async with _throttle_lock:
        _rate_limit_until_monotonic = max(
            _rate_limit_until_monotonic or 0.0,
            monotonic() + reset,
        )
    log.info(
        "cloudfleet_rate_limit_budget_exhausted",
        reset_in_seconds=reset,
        path=response.request.url.path,
    )


def _require_api_key() -> None:
    if not settings.cloudfleet_api_key:
        raise CloudfleetError("CLOUDFLEET_API_KEY no está configurada")


def _build_url(path: str) -> str:
    base_url = settings.cloudfleet_base_url.rstrip("/")
    return f"{base_url}/{path.lstrip('/')}"


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.cloudfleet_api_key}",
        "Accept": "application/json",
    }


def build_meter_payload(
    *,
    vehicle_code: str,
    meter_date: datetime,
    meter_type: str,
    meter_value: Decimal,
    source_code: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    if meter_type not in {"distance", "hours"}:
        raise ValueError("meter_type debe ser distance u hours")
    if meter_value < 0:
        raise ValueError("meter_value no puede ser negativo")
    normalized_date = (
        meter_date.replace(tzinfo=UTC) if meter_date.tzinfo is None else meter_date.astimezone(UTC)
    )
    payload: dict[str, Any] = {
        "vehicleCode": vehicle_code,
        "meterDate": normalized_date.isoformat().replace("+00:00", "Z"),
        "meterType": meter_type,
        "meterValue": float(meter_value),
    }
    if source_code:
        payload["sourceCode"] = source_code
    if comment:
        payload["comment"] = comment[:100]
    return payload


def _truncate(text: str, limit: int = 500) -> str:
    return text if len(text) <= limit else text[:limit]


def _retry_delay_seconds(response: httpx.Response, attempt: int) -> float:
    """Espera indicada por el proveedor o backoff 2/4/8 para fallos transitorios."""
    retry_after_raw = response.headers.get("Retry-After")
    if retry_after_raw:
        try:
            return max(0.0, float(retry_after_raw))
        except ValueError:
            pass
    return float(2**attempt)


async def _get_with_retries(
    http: httpx.AsyncClient,
    target: str,
    *,
    params: dict[str, Any] | None,
) -> httpx.Response:
    """GET idempotente con hasta tres reintentos para 429/5xx transitorios."""
    response: httpx.Response | None = None
    for attempt in range(_MAX_GET_ATTEMPTS):
        await _throttle()
        try:
            response = await http.get(target, params=params, headers=_auth_headers())
        except httpx.HTTPError as exc:
            if attempt == _MAX_GET_ATTEMPTS - 1:
                raise CloudfleetError(f"No se pudo conectar con Cloudfleet: {exc}") from exc
            delay = float(2**attempt)
            log.warning(
                "cloudfleet_get_transport_retry",
                attempt=attempt + 1,
                max_retries=_MAX_GET_ATTEMPTS - 1,
                retry_in_seconds=delay,
            )
            await asyncio.sleep(delay)
            continue

        await _observe_rate_limit(response)

        if response.status_code not in _RETRYABLE_GET_STATUSES or attempt == _MAX_GET_ATTEMPTS - 1:
            return response

        # El 429 ya dejó una pausa global mediante Retry-After. Para los 5xx
        # usamos backoff y conservamos el ritmo base de la API.
        delay = 0.0 if response.status_code == 429 else _retry_delay_seconds(response, attempt + 1)
        log.warning(
            "cloudfleet_get_retry",
            status_code=response.status_code,
            attempt=attempt + 1,
            max_retries=_MAX_GET_ATTEMPTS - 1,
            retry_in_seconds=delay,
            path=response.request.url.path,
        )
        if delay > 0:
            await asyncio.sleep(delay)

    assert response is not None  # el rango siempre ejecuta al menos una vez
    return response


async def _get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    client: httpx.AsyncClient | None = None,
) -> httpx.Response:
    _require_api_key()

    close_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.cloudfleet_timeout_seconds)
    try:
        url = _build_url(path)
        response = await _get_with_retries(http, url, params=params)

        if response.status_code >= 400:
            text = response.text or response.reason_phrase or ""
            raise CloudfleetError(f"Cloudfleet respondió {response.status_code}: {_truncate(text)}")

        return response
    finally:
        if close_client:
            await http.aclose()


def _page_signature(items: list[Any]) -> str:
    head = items[:3]
    try:
        return json.dumps(head, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(head)


def _next_page_number(header: str, current_page: int) -> int:
    """Número de la página siguiente a partir del header ``X-NextPage``.

    Solo se lee el parámetro ``page``; el resto del enlace se ignora a
    propósito (ver ``get_all_pages``). Si no trae un número mayor al actual se
    avanza uno.
    """
    try:
        query = parse_qs(urlsplit(header).query)
        candidate = int(query.get("page", [""])[0])
    except (ValueError, TypeError, IndexError):
        return current_page + 1
    return candidate if candidate > current_page else current_page + 1


async def get_all_pages(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    page_size: int = 50,
    max_pages: int | None = None,
    client: httpx.AsyncClient | None = None,
    empty_on_404: bool = False,
) -> list[dict[str, Any]]:
    """Recorre todas las páginas de un listado.

    **El enlace de ``X-NextPage`` nunca se sigue tal cual.** CloudFleet lo
    emite a veces sin los filtros de la petición original —observado el
    2026-09-01 en ``maintenance-schedules``: tras 12 páginas con el rango de
    fechas, el header de la última traía solo ``?page=13&pageSize=50`` y el
    proveedor respondía 409 ``Must specify at least one date range``. Falló
    así el sync diario seis veces entre el 16-08 y el 01-09. Por eso cada
    página se construye aquí con el ``path`` y los ``params`` originales, y del
    header solo se toma el número de página. Además, así el Bearer nunca viaja
    a un host que no sea el configurado (SEC-029).

    ``empty_on_404``: los listados filtrados de CloudFleet (issues, OTs,
    schedules) responden 404 con ``{"error":{"message":"No ... found..."}}``
    cuando el filtro no encuentra nada. Eso es una lista vacía, no un error, y
    tratarlo como fallo marcaría en rojo un sync que simplemente no tenía
    trabajo. Se deja en ``False`` para ``vehicles/``: ahí un 404 sería una URL
    mal configurada y una lista vacía marcaría ausente a toda la flota.
    """
    _require_api_key()
    close_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.cloudfleet_timeout_seconds)
    results: list[dict[str, Any]] = []
    base_params: dict[str, Any] = dict(params) if params else {}
    page = 1
    seen_signatures: set[str] = set()
    url = _build_url(path)

    try:
        while True:
            if max_pages is not None and page > max_pages:
                break

            request_params = dict(base_params)
            request_params["page"] = page
            request_params.setdefault("pageSize", page_size)

            response = await _get_with_retries(http, url, params=request_params)

            # Un 404 es "sin resultados" y no un error en dos casos: cuando el
            # listado lo declara (`empty_on_404`) y SIEMPRE a partir de la
            # segunda página. CloudFleet responde 404 "No ... found with the
            # specified filters" a la página siguiente de la última llena
            # (visto en `maintenance-schedules` el 2026-09-01), y eso es el fin
            # del recorrido, no un fallo del sync.
            if response.status_code == 404 and (empty_on_404 or page > 1):
                break

            if response.status_code >= 400:
                text = response.text or response.reason_phrase or ""
                raise CloudfleetError(
                    f"Cloudfleet respondió {response.status_code}: {_truncate(text)}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise CloudfleetError(
                    f"Cloudfleet devolvió JSON inválido: {_truncate(str(exc))}"
                ) from exc

            items: list[Any]
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                items = [payload]
            else:
                items = []

            if items:
                sig = _page_signature(items)
                if sig in seen_signatures:
                    break
                seen_signatures.add(sig)
                for item in items:
                    if isinstance(item, dict):
                        results.append(item)
                    elif isinstance(item, list):
                        results.extend(x for x in item if isinstance(x, dict))

            next_page_header = response.headers.get("X-NextPage") or response.headers.get(
                "x-next-page"
            )
            if next_page_header:
                page = _next_page_number(next_page_header, page)
                continue

            if len(items) < page_size:
                break
            page += 1

        return results
    finally:
        if close_client:
            await http.aclose()


def _iso_start(d: date) -> str:
    return f"{d.isoformat()}T00:00:00Z"


def _iso_end(d: date) -> str:
    return f"{d.isoformat()}T23:59:59Z"


async def list_vehicles() -> list[dict[str, Any]]:
    return await get_all_pages("vehicles/")


async def list_work_orders(updated_from: date, updated_to: date) -> list[dict[str, Any]]:
    return await get_all_pages(
        "work-orders/",
        params={
            "updatedAtFrom": _iso_start(updated_from),
            "updatedAtTo": _iso_end(updated_to),
        },
        empty_on_404=True,
    )


async def list_maintenance_schedules(due_from: date, due_to: date) -> list[dict[str, Any]]:
    return await get_all_pages(
        "maintenance-schedules/",
        params={
            "dateToExecuteFrom": _iso_start(due_from),
            "dateToExecuteTo": _iso_end(due_to),
        },
        empty_on_404=True,
    )


async def list_issues(
    *,
    reported_by: int | None = None,
    include_done: bool = True,
    created_from: date | None = None,
    created_to: date | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Lista issues (novedades) de CloudFleet.

    Sin ``includeDone`` el proveedor omite las resueltas, que son justo las
    que el sync de estado quiere ver. Un 404 "No Issues found" es lista vacía.
    """
    params: dict[str, Any] = {}
    if reported_by is not None:
        params["reportedBy"] = reported_by
    if include_done:
        params["includeDone"] = "true"
    if created_from is not None:
        params["createdAtFrom"] = _iso_start(created_from)
    if created_to is not None:
        params["createdAtTo"] = _iso_end(created_to)
    return await get_all_pages("issues/", params=params, client=client, empty_on_404=True)


class IssueLookup(NamedTuple):
    """Resultado de consultar una issue por número.

    ``exists=False`` sólo con un **404 explícito** del proveedor, que es la
    única evidencia de que la issue ya no está. Cualquier otra cosa que impida
    leerla —un payload que no es objeto— deja ``exists=True, payload=None``:
    no poder leerla no prueba que no exista, y confundir las dos cosas haría
    que un fallo nuestro se registrara como un borrado en CloudFleet.
    """

    exists: bool
    payload: dict[str, Any] | None


async def lookup_issue(
    number: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> IssueLookup:
    """Consulta una issue por número distinguiendo "no existe" de "no legible"."""
    _require_api_key()
    close_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.cloudfleet_timeout_seconds)
    try:
        response = await _get_with_retries(http, _build_url(f"issues/{number}"), params=None)
    finally:
        if close_client:
            await http.aclose()

    if response.status_code == 404:
        return IssueLookup(exists=False, payload=None)
    if response.status_code >= 400:
        text = response.text or response.reason_phrase or ""
        raise CloudfleetError(f"Cloudfleet respondió {response.status_code}: {_truncate(text)}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise CloudfleetError(f"Cloudfleet devolvió JSON inválido: {_truncate(str(exc))}") from exc
    return IssueLookup(exists=True, payload=payload if isinstance(payload, dict) else None)


async def get_issue(
    number: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Devuelve una issue por número, o ``None`` si CloudFleet no la conoce."""
    return (await lookup_issue(number, client=client)).payload


async def get_work_order(
    number: int,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Detalle de una orden de trabajo, o ``None`` si CloudFleet no la conoce.

    El **listado** de órdenes no trae los ítems de línea: publica
    ``totalCostLabors`` y ``totalCostParts`` agregados y nada más. Los trabajos
    con su sistema y su tipo de mantenimiento, y los repuestos con el trabajo al
    que pertenecen (``parts[].laborId``), sólo aparecen aquí.
    """

    _require_api_key()
    close_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.cloudfleet_timeout_seconds)
    try:
        response = await _get_with_retries(
            http, _build_url(f"work-orders/{number}"), params=None
        )
    finally:
        if close_client:
            await http.aclose()

    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        text = response.text or response.reason_phrase or ""
        raise CloudfleetError(f"Cloudfleet respondió {response.status_code}: {_truncate(text)}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise CloudfleetError(f"Cloudfleet devolvió JSON inválido: {_truncate(str(exc))}") from exc
    return payload if isinstance(payload, dict) else None


async def create_meter(
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Registra una lectura en CloudFleet y exige su respuesta ``204``.

    No reintentamos POSTs de medidores: el proveedor no ofrece una clave de
    idempotencia y un timeout/5xx puede haber creado la lectura. El llamante
    persiste ese caso como incierto y lo reconcilia contra el siguiente GET de
    vehículos antes de considerar otro envío.
    """
    _require_api_key()
    close_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.cloudfleet_timeout_seconds)
    response: httpx.Response | None = None
    try:
        await _throttle()
        response = await http.post(
            _build_url("meters/"),
            json=payload,
            headers={
                **_auth_headers(),
                "Content-Type": "application/json; charset=utf-8",
            },
        )
    except httpx.HTTPError as exc:
        raise CloudfleetMeterError(
            "No se pudo confirmar la lectura en Cloudfleet",
            uncertain=True,
        ) from exc
    finally:
        if close_client:
            await http.aclose()

    assert response is not None
    await _observe_rate_limit(response)
    if response.status_code == 204:
        return

    body = response.text or response.reason_phrase or "respuesta sin detalle"
    raise CloudfleetMeterError(
        f"Cloudfleet respondió {response.status_code}: {_truncate(body)}",
        uncertain=response.status_code in {408, 429, 500, 502, 503, 504},
        retryable=response.status_code == 429,
    )


async def create_issue(
    payload: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> CloudfleetCreateResult:
    if not settings.cloudfleet_api_key:
        raise CloudfleetError("CLOUDFLEET_API_KEY no está configurada")

    close_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.cloudfleet_timeout_seconds)
    try:
        await _throttle()
        response = await http.post(
            _build_url("issues"),
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.cloudfleet_api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )
    except httpx.HTTPError as exc:
        raise CloudfleetError(f"No se pudo conectar con Cloudfleet: {exc}") from exc
    finally:
        if close_client:
            await http.aclose()

    await _observe_rate_limit(response)

    body: dict[str, Any] = {}
    if response.content:
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            body = {"raw": response.text}

    if response.status_code not in {200, 201}:
        message = (
            body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else None
        )
        detail = message or body.get("detail") or body.get("raw") or response.reason_phrase
        normalized_detail = str(detail).lower()
        is_vehicle_error = any(
            word in normalized_detail for word in ("vehicle", "vehículo", "vehiculo", "placa")
        )
        is_missing_vehicle = any(
            phrase in normalized_detail
            for phrase in (
                "not found",
                "does not exist",
                "not registered",
                "not correct",
                "no encontrado",
                "no existe",
                "no registrado",
            )
        )
        if is_vehicle_error and is_missing_vehicle:
            raise CloudfleetVehicleNotFoundError(
                f"Cloudfleet respondió {response.status_code}: {detail}"
            )
        raise CloudfleetError(f"Cloudfleet respondió {response.status_code}: {detail}")

    number = body.get("number")
    return CloudfleetCreateResult(
        issue_number=number if isinstance(number, int) else None,
        response=body,
    )
