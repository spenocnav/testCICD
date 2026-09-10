"""Cliente MyGeotab bajo demanda (`Authenticate`, `Get LogRecord`, `GetAddresses`).

Lo usa el informe de Ubicaciones: consulta el rastro del vehículo directamente
al proveedor, en el momento en que el usuario lo pide, con la credencial de la
base Geotab de ese vehículo.

Reglas del módulo:

- **No se persiste nada.** Ni posiciones ni direcciones tocan la base, el disco
  o una caché: se piden por ventana, viajan en la respuesta HTTP y se olvidan.
- **No se registran credenciales.** Ni usuario, ni contraseña, ni `sessionId`
  entran a los logs (ver SEC-013).
- Una dirección que no se puede resolver devuelve `None`, no rompe el informe:
  el punto sigue siendo válido con sus coordenadas.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("geotab-client")

_MAX_ATTEMPTS = 3
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_RETRY_BASE_DELAY_S = 1.0


class GeotabError(Exception):
    """Error controlado al hablar con MyGeotab."""


@dataclass(frozen=True)
class GeotabSession:
    """Sesión autenticada: servidor efectivo y credenciales de sesión."""

    server: str
    credentials: dict[str, Any]


def _api_url(server: str) -> str:
    host = server.strip().strip("/")
    if not host or host.lower() == "thisserver":
        host = settings.geotab_api_host
    if host.startswith("http://") or host.startswith("https://"):
        return f"{host.rstrip('/')}/apiv1"
    return f"https://{host}/apiv1"


async def _call(
    client: httpx.AsyncClient,
    *,
    server: str,
    method: str,
    params: dict[str, Any],
) -> Any:
    """Una llamada JSON a `/apiv1` con reintentos acotados para 429/5xx."""
    last_error: str | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = await client.post(
                _api_url(server),
                json={"method": method, "params": params},
            )
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
            if attempt == _MAX_ATTEMPTS:
                break
            await asyncio.sleep(_RETRY_BASE_DELAY_S * attempt)
            continue

        if response.status_code in _RETRYABLE_STATUSES and attempt < _MAX_ATTEMPTS:
            await asyncio.sleep(_RETRY_BASE_DELAY_S * attempt)
            continue
        if response.status_code >= 400:
            last_error = f"HTTP {response.status_code}"
            break

        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            # El mensaje de Geotab puede incluir el usuario; no se propaga.
            log.warning("geotab_api_error", extra={"method": method})
            raise GeotabError(f"MyGeotab rechazó la operación ({method})")
        return payload.get("result") if isinstance(payload, dict) else None

    log.warning("geotab_api_unreachable", extra={"method": method, "reason": last_error})
    raise GeotabError(f"MyGeotab no respondió ({method})")


async def authenticate(
    client: httpx.AsyncClient, *, database: str, username: str, password: str
) -> GeotabSession:
    """Abre sesión y devuelve el servidor efectivo que indique Geotab."""
    result = await _call(
        client,
        server=settings.geotab_api_host,
        method="Authenticate",
        params={"userName": username, "password": password, "database": database},
    )
    if not isinstance(result, dict) or not result.get("credentials"):
        raise GeotabError("MyGeotab no devolvió credenciales de sesión")
    return GeotabSession(
        server=str(result.get("path") or settings.geotab_api_host),
        credentials=result["credentials"],
    )


async def get_addresses(
    client: httpx.AsyncClient,
    session: GeotabSession,
    coordinates: Sequence[tuple[float, float]],
) -> list[str | None]:
    """Resuelve un lote `(lat, lon)` conservando el orden de entrada."""
    if not coordinates:
        return []
    result = await _call(
        client,
        server=session.server,
        method="GetAddresses",
        params={
            "credentials": session.credentials,
            # Geotab usa x = longitud, y = latitud.
            "coordinates": [{"x": lon, "y": lat} for lat, lon in coordinates],
        },
    )
    if not isinstance(result, list):
        return [None] * len(coordinates)
    addresses: list[str | None] = []
    for index in range(len(coordinates)):
        item = result[index] if index < len(result) else None
        if isinstance(item, dict):
            formatted = item.get("formattedAddress")
            addresses.append(str(formatted) if formatted else None)
        else:
            addresses.append(None)
    return addresses


async def resolve_addresses_in_batches(
    client: httpx.AsyncClient,
    session: GeotabSession,
    coordinates: Sequence[tuple[float, float]],
    batch_size: int | None = None,
) -> list[str | None]:
    """Resuelve todas las coordenadas por lotes sobre una sesión ya abierta.

    Los lotes evitan mandar miles de coordenadas en una sola petición: acotan
    memoria, tiempo de respuesta e impacto sobre el proveedor.
    """
    if not coordinates:
        return []
    size = batch_size or settings.geotab_address_batch_size
    addresses: list[str | None] = []
    for start in range(0, len(coordinates), size):
        chunk = coordinates[start : start + size]
        addresses.extend(await get_addresses(client, session, chunk))
    return addresses


@dataclass(frozen=True)
class GeotabLogRecord:
    """Posición cruda tal como la devuelve MyGeotab."""

    date_time: datetime
    latitude: float
    longitude: float
    speed: float | None


@dataclass(frozen=True)
class GeotabMeterReading:
    """Última lectura acumulada de un diagnóstico Geotab."""

    date_time: datetime
    value: Decimal


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


async def get_latest_meter_reading(
    client: httpx.AsyncClient,
    session: GeotabSession,
    *,
    device_id: str,
    diagnostic_id: str,
    from_date: datetime,
    results_limit: int = 20_000,
) -> GeotabMeterReading | None:
    """Obtiene la lectura más reciente de un contador acumulativo.

    MyGeotab entrega el historial de ``StatusData`` dentro de una ventana; se
    selecciona por fecha en el cliente en vez de asumir el orden del proveedor.
    """
    result = await _call(
        client,
        server=session.server,
        method="Get",
        params={
            "credentials": session.credentials,
            "typeName": "StatusData",
            "search": {
                "deviceSearch": {"id": device_id},
                "diagnosticSearch": {"id": diagnostic_id},
                "fromDate": from_date.astimezone(UTC).isoformat(),
            },
            "resultsLimit": results_limit,
        },
    )
    if not isinstance(result, list):
        return None
    latest: GeotabMeterReading | None = None
    normalized_from_date = from_date.astimezone(UTC)
    for item in result:
        if not isinstance(item, dict):
            continue
        date_time = _parse_datetime(item.get("dateTime"))
        value = _decimal(item.get("data"))
        if date_time is None or value is None:
            continue
        # Algunas cuentas Geotab pueden devolver registros fuera del filtro
        # ``fromDate``. Nunca propagar una medición histórica a CloudFleet.
        if date_time < normalized_from_date:
            continue
        candidate = GeotabMeterReading(date_time=date_time, value=value)
        if latest is None or candidate.date_time > latest.date_time:
            latest = candidate
    return latest


async def get_log_records(
    client: httpx.AsyncClient,
    session: GeotabSession,
    *,
    device_id: str,
    from_date: datetime,
    to_date: datetime,
    results_limit: int | None = None,
) -> list[GeotabLogRecord]:
    """Rastro GPS de un dispositivo en una ventana de tiempo.

    La ventana la acota el llamante (un día por petición): un `LogRecord` cada
    pocos segundos hace que un rango largo en una sola llamada sea inmanejable
    tanto para el proveedor como para este proceso.
    """
    result = await _call(
        client,
        server=session.server,
        method="Get",
        params={
            "credentials": session.credentials,
            "typeName": "LogRecord",
            "search": {
                "deviceSearch": {"id": device_id},
                "fromDate": from_date.isoformat(),
                "toDate": to_date.isoformat(),
            },
            "resultsLimit": results_limit or settings.geotab_log_results_limit,
        },
    )
    if not isinstance(result, list):
        return []
    records: list[GeotabLogRecord] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        moment = _parse_datetime(item.get("dateTime"))
        lat = item.get("latitude")
        lon = item.get("longitude")
        if moment is None or lat is None or lon is None:
            continue
        speed = item.get("speed")
        records.append(
            GeotabLogRecord(
                date_time=moment,
                latitude=float(lat),
                longitude=float(lon),
                speed=float(speed) if speed is not None else None,
            )
        )
    # Geotab no garantiza orden; el muestreo por ventana sí lo necesita.
    records.sort(key=lambda record: record.date_time)
    return records
