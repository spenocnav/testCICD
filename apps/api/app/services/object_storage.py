from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import anyio

from app.core.config import settings


class ObjectStorageError(Exception):
    """Error controlado al leer o escribir evidencias."""


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    object_key: str
    size_bytes: int


def _client() -> Any:
    try:
        from minio import Minio
    except ImportError as exc:
        raise ObjectStorageError("La dependencia 'minio' no está instalada") from exc

    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def _ensure_bucket(client: Any, bucket: str) -> None:
    try:
        exists = client.bucket_exists(bucket)
        if not exists:
            client.make_bucket(bucket)
    except Exception as exc:
        raise ObjectStorageError(f"No se pudo preparar el bucket MinIO: {exc}") from exc


def _put_object_sync(
    object_key: str,
    data: bytes,
    content_type: str,
    bucket: str | None,
) -> StoredObject:
    client = _client()
    target_bucket = bucket or settings.minio_bucket
    _ensure_bucket(client, target_bucket)
    try:
        client.put_object(
            target_bucket,
            object_key,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
    except Exception as exc:
        raise ObjectStorageError(f"No se pudo guardar la evidencia en MinIO: {exc}") from exc
    return StoredObject(
        bucket=target_bucket,
        object_key=object_key,
        size_bytes=len(data),
    )


def _get_object_sync(bucket: str, object_key: str) -> bytes:
    client = _client()
    response = None
    try:
        response = client.get_object(bucket, object_key)
        return bytes(response.read())
    except Exception as exc:
        raise ObjectStorageError(f"No se pudo leer la evidencia desde MinIO: {exc}") from exc
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def _delete_object_sync(bucket: str, object_key: str) -> None:
    """Best-effort delete: log y NO propaga excepción.

    Usado para compensar uploads parciales cuando el commit local falla. Una
    vez que el commit local es exitoso, los objetos NO se borran aunque el
    envío a Cloudfleet falle: ya son parte del registro de la Novedad.
    """
    try:
        client = _client()
        client.remove_object(bucket, object_key)
    except Exception as exc:  # best-effort por contrato
        import logging

        logging.getLogger(__name__).warning(
            "compensación MinIO falló al borrar %s/%s: %s",
            bucket,
            object_key,
            exc,
        )


async def put_object(
    object_key: str,
    data: bytes,
    content_type: str,
    *,
    bucket: str | None = None,
) -> StoredObject:
    """Guarda un objeto en un bucket privado.

    Sin ``bucket`` conserva la compatibilidad con Novedades. Los nuevos
    dominios deben declarar su bucket explícitamente para no mezclar activos.
    """
    return await anyio.to_thread.run_sync(
        _put_object_sync,
        object_key,
        data,
        content_type,
        bucket,
    )


async def get_object(bucket: str, object_key: str) -> bytes:
    return await anyio.to_thread.run_sync(_get_object_sync, bucket, object_key)


async def delete_object(bucket: str, object_key: str) -> None:
    """Borrado best-effort: nunca lanza. Para compensación post-fallo.

    Captura cualquier excepción del threadpool o de la lógica interna para
    que el caller (compensación de uploads) no propague fallos de storage.
    """
    try:
        await anyio.to_thread.run_sync(_delete_object_sync, bucket, object_key)
    except Exception as exc:  # best-effort por contrato
        import logging

        logging.getLogger(__name__).warning(
            "delete_object falló para %s/%s: %s", bucket, object_key, exc
        )
