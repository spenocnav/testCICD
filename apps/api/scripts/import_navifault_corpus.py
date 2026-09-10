#!/usr/bin/env python3
"""Importa un paquete ``navifault-source-v1`` al dominio privado Navifault.

El paquete se construye en el repositorio del scraper. Este importador vive en
Portal porque usa su migración Alembic, su configuración y el bucket MinIO
privado. Por defecto sólo valida el paquete; ``--execute`` es obligatorio para
subir objetos y escribir en PostgreSQL.

No borra datos ni usa el schema ``public`` o ``analytics``. El único dato de
``public`` que usará el paso posterior de dateplates es el inventario de
vehículos; este script todavía no lo toca.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import urlparse

from sqlalchemy import Table, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import settings
from app.db.advisory_lock import session_advisory_lock
from app.db.base import Base
from app.db.session import engine
from app.models.navifault import NAVIFAULT_SCHEMA, NavifaultCorpusImportRun
from app.services.object_storage import put_object

if sys.platform == "win32":
    cast(Any, sys.stdout).reconfigure(encoding="utf-8")
    cast(Any, sys.stderr).reconfigure(encoding="utf-8")


CORPUS_VERSION = "navifault-source-v1"
BLOB_PREFIX = "corpus"
BATCH_SIZE = 500

# Respeta las dependencias FK. Las tablas de runtime (descripciones, enlaces de
# idioma y mapa dateplate) no forman parte de un paquete fuente y se preservan.
SOURCE_TABLES: tuple[str, ...] = (
    "manuals",
    "engine_manual_candidates",
    "fault_pages",
    "fault_protocol_keys",
    "fault_page_tables",
    "fault_analyses",
    "fault_page_analyses",
    "fault_analysis_tables",
    "technical_documents",
    "technical_document_tables",
    "assets",
    "fault_analysis_documents",
    "fault_page_documents",
    "technical_document_links",
    "fault_page_assets",
    "fault_analysis_assets",
    "technical_document_assets",
)
RAW_BLOB_TABLES: dict[str, tuple[str, str]] = {
    "fault_pages": ("raw_html_object_key", "raw_html_sha256"),
    "fault_analyses": ("raw_html_object_key", "raw_html_sha256"),
    "technical_documents": ("raw_html_object_key", "raw_html_sha256"),
    "assets": ("object_key", "sha256"),
}


class CorpusValidationError(ValueError):
    """El paquete no cumple el contrato; no debe importarse."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(source_dir: Path) -> tuple[dict[str, Any], str]:
    path = source_dir / "manifest.json"
    if not path.is_file():
        raise CorpusValidationError(f"Falta manifest.json en {source_dir}")
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusValidationError(f"No se puede leer {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise CorpusValidationError("manifest.json debe ser un objeto JSON")
    if manifest.get("corpus_version") != CORPUS_VERSION:
        raise CorpusValidationError(
            f"Corpus no soportado: {manifest.get('corpus_version')!r}; se esperaba {CORPUS_VERSION!r}"
        )
    if not isinstance(manifest.get("audit"), dict):
        raise CorpusValidationError("manifest.audit debe ser un objeto")
    return manifest, hashlib.sha256(raw).hexdigest()


def _expected_rows(manifest: dict[str, Any], table_name: str) -> int:
    value = manifest["audit"].get(f"rows_{table_name}", 0)
    if not isinstance(value, int) or value < 0:
        raise CorpusValidationError(f"Conteo inválido para {table_name}: {value!r}")
    return value


def _table_path(source_dir: Path, table_name: str) -> Path:
    return source_dir / "tables" / f"{table_name}.jsonl"


def _iter_rows(
    source_dir: Path, manifest: dict[str, Any], table_name: str
) -> Iterator[dict[str, Any]]:
    path = _table_path(source_dir, table_name)
    expected = _expected_rows(manifest, table_name)
    if not path.is_file():
        if expected == 0:
            return
        raise CorpusValidationError(
            f"Falta {path.relative_to(source_dir)} ({expected} filas esperadas)"
        )

    actual = 0
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            payload = line.strip()
            if not payload:
                continue
            try:
                row = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise CorpusValidationError(
                    f"JSON inválido en {path.relative_to(source_dir)}:{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise CorpusValidationError(
                    f"Fila no objeto en {path.relative_to(source_dir)}:{line_number}"
                )
            actual += 1
            yield row
    if actual != expected:
        raise CorpusValidationError(
            f"{path.relative_to(source_dir)} contiene {actual} filas; manifest declara {expected}"
        )


def _source_blob_path(source_dir: Path, object_key: str) -> Path:
    posix_key = PurePosixPath(object_key)
    if not object_key or posix_key.is_absolute() or ".." in posix_key.parts:
        raise CorpusValidationError(f"Clave de blob inválida: {object_key!r}")
    candidate = (source_dir / Path(*posix_key.parts)).resolve()
    try:
        candidate.relative_to(source_dir.resolve())
    except ValueError as exc:
        raise CorpusValidationError(f"Blob fuera del paquete: {object_key!r}") from exc
    return candidate


def _collect_blobs(source_dir: Path, manifest: dict[str, Any]) -> dict[str, str]:
    """Verifica cada blob referenciado antes de tocar MinIO o PostgreSQL."""
    blobs: dict[str, str] = {}
    for table_name, (key_name, digest_name) in RAW_BLOB_TABLES.items():
        for row in _iter_rows(source_dir, manifest, table_name):
            object_key = row.get(key_name)
            digest = row.get(digest_name)
            if object_key is None and digest is None:
                continue
            if not isinstance(object_key, str) or not isinstance(digest, str):
                raise CorpusValidationError(
                    f"{table_name} debe declarar juntos {key_name} y {digest_name}"
                )
            path = _source_blob_path(source_dir, object_key)
            if not path.is_file():
                raise CorpusValidationError(f"Falta blob {object_key}")
            actual = _sha256(path)
            if actual != digest:
                raise CorpusValidationError(
                    f"Hash no coincide para {object_key}: manifest={digest}, actual={actual}"
                )
            previous = blobs.setdefault(object_key, digest)
            if previous != digest:
                raise CorpusValidationError(f"Un blob tiene hashes incompatibles: {object_key}")
    return blobs


def _collect_asset_blobs(
    source_dir: Path,
    assets: Sequence[dict[str, Any]],
) -> dict[str, str]:
    """Verifica sólo los binarios de los assets que una reparación añadirá."""
    blobs: dict[str, str] = {}
    for row in assets:
        object_key = row.get("object_key")
        digest = row.get("sha256")
        if object_key is None and digest is None:
            continue
        if not isinstance(object_key, str) or not isinstance(digest, str):
            raise CorpusValidationError("assets debe declarar juntos object_key y sha256")
        previous = blobs.setdefault(object_key, digest)
        if previous != digest:
            raise CorpusValidationError(f"Un blob tiene hashes incompatibles: {object_key}")

    for object_key, digest in blobs.items():
        path = _source_blob_path(source_dir, object_key)
        if not path.is_file():
            raise CorpusValidationError(f"Falta blob {object_key}")
        actual = _sha256(path)
        if actual != digest:
            raise CorpusValidationError(
                f"Hash no coincide para {object_key}: manifest={digest}, actual={actual}"
            )
    return blobs


def _table_by_name(table_name: str) -> Table:
    table = Base.metadata.tables.get(f"{NAVIFAULT_SCHEMA}.{table_name}")
    if table is None:
        raise RuntimeError(f"Modelo Navifault no registrado: {table_name}")
    return table


def _validate_rows_against_models(
    source_dir: Path,
    manifest: dict[str, Any],
    tables: Sequence[str] = SOURCE_TABLES,
) -> None:
    """Rechaza columnas o campos requeridos antes de subir un solo blob."""
    for table_name in tables:
        table = _table_by_name(table_name)
        allowed = {column.name for column in table.columns}
        required = {
            column.name
            for column in table.columns
            if not column.nullable and column.default is None and column.server_default is None
        }
        for row in _iter_rows(source_dir, manifest, table_name):
            unknown = set(row) - allowed
            if unknown:
                raise CorpusValidationError(
                    f"{table_name} contiene columnas no permitidas: {sorted(unknown)}"
                )
            missing = required - set(row)
            if missing:
                raise CorpusValidationError(
                    f"{table_name} omite columnas requeridas: {sorted(missing)}"
                )
    if "fault_page_assets" in tables:
        _validate_fault_page_asset_bindings(source_dir, manifest)


def _asset_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return PurePosixPath(urlparse(value).path).name.casefold()


def _asset_matches_display(asset_source_url: object, display_url: object) -> bool:
    """Confirma que un binario pertenece al img exacto que se va a renderizar."""
    source_name = _asset_name(asset_source_url)
    display_name = _asset_name(display_url)
    if not source_name or not display_name:
        return False
    source_stem = PurePosixPath(source_name).stem
    display_stem = PurePosixPath(display_name).stem
    return source_stem == display_stem or source_stem.endswith(f"_{display_stem}")


def _validate_fault_page_asset_bindings(source_dir: Path, manifest: dict[str, Any]) -> None:
    """Rechaza un corpus que intentaría mostrar un gráfico ajeno en una FC."""
    assets: dict[str, dict[str, Any]] = {}
    for row in _iter_rows(source_dir, manifest, "assets"):
        asset_id = row.get("asset_id")
        if isinstance(asset_id, str):
            assets[asset_id] = row

    invalid: list[str] = []
    for row in _iter_rows(source_dir, manifest, "fault_page_assets"):
        display_url = row.get("display_url")
        if not isinstance(display_url, str) or not display_url:
            continue
        asset = assets.get(str(row.get("asset_id") or ""))
        if asset is None:
            invalid.append(f"asset_id inexistente: {row.get('asset_id')!r}")
        elif asset.get("local_status") == "available" and not _asset_matches_display(
            asset.get("source_url"), display_url
        ):
            invalid.append(
                f"FC {row.get('fault_page_id')} ordinal {row.get('ordinal')}: "
                f"{asset.get('source_url')!r} no corresponde a {display_url!r}"
            )
        if len(invalid) >= 5:
            break
    if invalid:
        raise CorpusValidationError(
            "fault_page_assets contiene imágenes enlazadas a un img distinto. "
            "Regenera el corpus con el exportador corregido. Ejemplos: "
            + " | ".join(invalid)
        )


def _storage_key(corpus_version: str, source_object_key: str) -> str:
    return f"{BLOB_PREFIX}/{corpus_version}/{source_object_key}"


def _content_type(path: Path) -> str:
    if path.suffix.lower() in {".html", ".htm"}:
        return "text/html; charset=utf-8"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _rewrite_object_keys(row: dict[str, Any], corpus_version: str) -> dict[str, Any]:
    result = dict(row)
    for field in ("raw_html_object_key", "object_key"):
        value = result.get(field)
        if value is not None:
            if not isinstance(value, str):
                raise CorpusValidationError(f"{field} debe ser texto o null")
            result[field] = _storage_key(corpus_version, value)
    return result


async def _upsert_rows(
    connection: AsyncConnection,
    table_name: str,
    rows: Sequence[dict[str, Any]],
) -> None:
    if not rows:
        return
    table = _table_by_name(table_name)
    statement = insert(table).values(list(rows))
    updates = {
        column.name: statement.excluded[column.name]
        for column in table.columns
        if not column.primary_key
    }
    conflict_columns = [column.name for column in table.primary_key.columns]
    if not updates:
        # Las tablas puente puras sólo contienen claves primarias compuestas.
        # No existe nada que actualizar cuando ya se conoce la relación.
        await connection.execute(statement.on_conflict_do_nothing(index_elements=conflict_columns))
        return
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=conflict_columns,
            set_=updates,
        )
    )


async def _set_import_run(
    connection: AsyncConnection,
    *,
    import_id: str,
    corpus_version: str,
    status: str,
    manifest: dict[str, Any],
    audit: dict[str, Any],
    finished: bool,
) -> None:
    table = cast(Table, NavifaultCorpusImportRun.__table__)
    now = datetime.now(UTC)
    row: dict[str, Any] = {
        "import_id": import_id,
        "corpus_version": corpus_version,
        "status": status,
        "source_manifest": manifest,
        "audit": audit,
        "created_at": now,
        "finished_at": now if finished else None,
    }
    statement = insert(table).values(row)
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=["import_id"],
            set_={
                "status": statement.excluded.status,
                "source_manifest": statement.excluded.source_manifest,
                "audit": statement.excluded.audit,
                "finished_at": statement.excluded.finished_at,
            },
        )
    )


def _base_audit(manifest: dict[str, Any], blobs: dict[str, str]) -> dict[str, Any]:
    return {
        "source_rows": {table: _expected_rows(manifest, table) for table in SOURCE_TABLES},
        "verified_blobs": len(blobs),
        "target_bucket": settings.navifault_minio_bucket,
    }


async def _reconcile_fault_page_assets(
    connection: AsyncConnection,
    source_dir: Path,
    manifest: dict[str, Any],
    batch_size: int,
) -> int:
    """Reemplaza sólo relaciones de imágenes de las FC incluidas en el paquete.

    La PK histórica incluye asset_id. Por eso un upsert normal no elimina el
    enlace previo cuando una FC ahora apunta al binario correcto. Esta
    reconciliación conserva HTML, manuales, IA, comentarios y cualquier dato
    operativo; toca exclusivamente navifault.fault_page_assets.
    """
    table = _table_by_name("fault_page_assets")
    page_ids: list[str] = []
    deleted = 0

    async def flush() -> None:
        nonlocal deleted
        if not page_ids:
            return
        result = await connection.execute(
            delete(table).where(table.c.fault_page_id.in_(page_ids))
        )
        deleted += max(result.rowcount or 0, 0)
        page_ids.clear()

    for row in _iter_rows(source_dir, manifest, "fault_pages"):
        fault_page_id = row.get("fault_page_id")
        if not isinstance(fault_page_id, str) or not fault_page_id:
            raise CorpusValidationError("fault_pages contiene un fault_page_id inválido")
        page_ids.append(fault_page_id)
        if len(page_ids) >= batch_size:
            await flush()
    await flush()
    return deleted


async def _repair_fault_page_assets_only(
    source_dir: Path,
    manifest: dict[str, Any],
    manifest_sha256: str,
    batch_size: int,
) -> Counter[str]:
    """Aplica un overlay de imágenes sin reimportar el resto del corpus.

    Está pensado para una corrección append-only de assets: sube sólo los
    asset_id ausentes, valida sus blobs y reemplaza exclusivamente los enlaces
    de imagen de las páginas FC presentes en el paquete.
    """
    corpus_version = str(manifest["corpus_version"])
    asset_rows = list(_iter_rows(source_dir, manifest, "assets"))
    audit: dict[str, Any] = {
        "mode": "fault_page_assets_only",
        "source_rows": {
            "assets": _expected_rows(manifest, "assets"),
            "fault_pages": _expected_rows(manifest, "fault_pages"),
            "fault_page_assets": _expected_rows(manifest, "fault_page_assets"),
        },
        "target_bucket": settings.navifault_minio_bucket,
    }
    counts: Counter[str] = Counter()
    import_id = f"repair_{manifest_sha256[:24]}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"

    async with session_advisory_lock(engine, "navifault-corpus-import") as connection:
        assets_table = _table_by_name("assets")
        existing_asset_ids = set(
            (await connection.execute(select(assets_table.c.asset_id))).scalars().all()
        )
        assets_to_upsert = [
            row
            for row in asset_rows
            if isinstance(row.get("asset_id"), str) and row["asset_id"] not in existing_asset_ids
        ]
        blobs = _collect_asset_blobs(source_dir, assets_to_upsert)
        audit["new_assets"] = len(assets_to_upsert)
        audit["verified_blobs"] = len(blobs)
        await _set_import_run(
            connection,
            import_id=import_id,
            corpus_version=corpus_version,
            status="processing",
            manifest=manifest,
            audit=audit,
            finished=False,
        )
        await connection.commit()
        try:
            for source_key in sorted(blobs):
                path = _source_blob_path(source_dir, source_key)
                await put_object(
                    _storage_key(corpus_version, source_key),
                    path.read_bytes(),
                    _content_type(path),
                    bucket=settings.navifault_minio_bucket,
                )
                counts["uploaded_blobs"] += 1

            for start in range(0, len(assets_to_upsert), batch_size):
                batch = [
                    _rewrite_object_keys(row, corpus_version)
                    for row in assets_to_upsert[start : start + batch_size]
                ]
                await _upsert_rows(connection, "assets", batch)
                await connection.commit()
                counts["rows_assets"] += len(batch)

            counts["deleted_fault_page_assets"] = await _reconcile_fault_page_assets(
                connection, source_dir, manifest, batch_size
            )
            await connection.commit()

            batch: list[dict[str, Any]] = []
            for row in _iter_rows(source_dir, manifest, "fault_page_assets"):
                batch.append(_rewrite_object_keys(row, corpus_version))
                if len(batch) == batch_size:
                    await _upsert_rows(connection, "fault_page_assets", batch)
                    await connection.commit()
                    counts["rows_fault_page_assets"] += len(batch)
                    batch.clear()
            if batch:
                await _upsert_rows(connection, "fault_page_assets", batch)
                await connection.commit()
                counts["rows_fault_page_assets"] += len(batch)

            completed_audit = {**audit, "imported_rows": dict(sorted(counts.items()))}
            await _set_import_run(
                connection,
                import_id=import_id,
                corpus_version=corpus_version,
                status="ready",
                manifest=manifest,
                audit=completed_audit,
                finished=True,
            )
            await connection.commit()
        except Exception as exc:
            if connection.in_transaction():
                await connection.rollback()
            failed_audit = {
                **audit,
                "partial_rows": dict(sorted(counts.items())),
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            }
            await _set_import_run(
                connection,
                import_id=import_id,
                corpus_version=corpus_version,
                status="failed",
                manifest=manifest,
                audit=failed_audit,
                finished=True,
            )
            await connection.commit()
            raise
    return counts


async def _import(
    source_dir: Path,
    manifest: dict[str, Any],
    manifest_sha256: str,
    blobs: dict[str, str],
    batch_size: int,
    upload_blobs: bool,
    reconcile_fault_page_assets: bool,
) -> Counter[str]:
    corpus_version = str(manifest["corpus_version"])
    import_id = f"import_{manifest_sha256[:24]}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    audit = _base_audit(manifest, blobs)
    counts: Counter[str] = Counter()

    async with session_advisory_lock(engine, "navifault-corpus-import") as connection:
        await _set_import_run(
            connection,
            import_id=import_id,
            corpus_version=corpus_version,
            status="processing",
            manifest=manifest,
            audit=audit,
            finished=False,
        )
        await connection.commit()
        try:
            # La subida se hace fuera de una transacción. Si se interrumpe, al
            # reintentar se reescriben bytes idénticos bajo claves deterministas.
            if upload_blobs:
                for source_key in sorted(blobs):
                    path = _source_blob_path(source_dir, source_key)
                    await put_object(
                        _storage_key(corpus_version, source_key),
                        path.read_bytes(),
                        _content_type(path),
                        bucket=settings.navifault_minio_bucket,
                    )
                    counts["uploaded_blobs"] += 1
            else:
                # Recuperación explícita: el intento anterior alcanzó el paso
                # de tablas, por lo que ya terminó de subir todos los blobs.
                counts["reused_blobs"] = len(blobs)

            for table_name in SOURCE_TABLES:
                if table_name == "fault_page_assets" and reconcile_fault_page_assets:
                    counts["deleted_fault_page_assets"] += await _reconcile_fault_page_assets(
                        connection, source_dir, manifest, batch_size
                    )
                batch: list[dict[str, Any]] = []
                for row in _iter_rows(source_dir, manifest, table_name):
                    batch.append(_rewrite_object_keys(row, corpus_version))
                    if len(batch) == batch_size:
                        await _upsert_rows(connection, table_name, batch)
                        await connection.commit()
                        counts[f"rows_{table_name}"] += len(batch)
                        batch.clear()
                if batch:
                    await _upsert_rows(connection, table_name, batch)
                    await connection.commit()
                    counts[f"rows_{table_name}"] += len(batch)

            completed_audit = {**audit, "imported_rows": dict(sorted(counts.items()))}
            await _set_import_run(
                connection,
                import_id=import_id,
                corpus_version=corpus_version,
                status="ready",
                manifest=manifest,
                audit=completed_audit,
                finished=True,
            )
            await connection.commit()
        except Exception as exc:
            if connection.in_transaction():
                await connection.rollback()
            failed_audit = {
                **audit,
                "partial_rows": dict(sorted(counts.items())),
                "error_type": type(exc).__name__,
                "error": str(exc)[:1000],
            }
            await _set_import_run(
                connection,
                import_id=import_id,
                corpus_version=corpus_version,
                status="failed",
                manifest=manifest,
                audit=failed_audit,
                finished=True,
            )
            await connection.commit()
            raise
    return counts


async def _main_async(args: argparse.Namespace) -> int:
    source_dir = args.source.resolve()
    manifest, manifest_sha256 = _read_manifest(source_dir)
    if args.repair_fault_page_assets_only:
        _validate_rows_against_models(
            source_dir,
            manifest,
            tables=("assets", "fault_page_assets"),
        )
        counts = await _repair_fault_page_assets_only(
            source_dir,
            manifest,
            manifest_sha256,
            args.batch_size,
        )
        print(
            json.dumps(
                {"status": "ready", **dict(sorted(counts.items()))},
                ensure_ascii=False,
                indent=2,
            )
        )
        await engine.dispose()
        return 0
    _validate_rows_against_models(source_dir, manifest)
    blobs = _collect_blobs(source_dir, manifest)
    audit = _base_audit(manifest, blobs)
    print(
        json.dumps(
            {"mode": "execute" if args.execute else "dry-run", **audit},
            ensure_ascii=False,
            indent=2,
        )
    )
    if not args.execute:
        return 0

    counts = await _import(
        source_dir,
        manifest,
        manifest_sha256,
        blobs,
        args.batch_size,
        upload_blobs=not args.skip_blobs,
        reconcile_fault_page_assets=args.reconcile_fault_page_assets,
    )
    print(
        json.dumps(
            {"status": "ready", **dict(sorted(counts.items()))}, ensure_ascii=False, indent=2
        )
    )
    await engine.dispose()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Directorio navifault-source-v1")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Confirma subida al bucket privado y upserts en navifault",
    )
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Filas por transacción")
    parser.add_argument(
        "--skip-blobs",
        action="store_true",
        help="Sólo recuperación: conserva blobs que un intento previo ya cargó",
    )
    parser.add_argument(
        "--reconcile-fault-page-assets",
        action="store_true",
        help=(
            "Reemplaza las relaciones de imágenes de las FC incluidas en el paquete. "
            "Úselo al corregir o refrescar sus binarios."
        ),
    )
    parser.add_argument(
        "--repair-fault-page-assets-only",
        action="store_true",
        help=(
            "Repara assets nuevos y reemplaza enlaces FC sin reimportar HTML, "
            "manuales ni documentos técnicos."
        ),
    )
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 5_000:
        parser.error("--batch-size debe estar entre 1 y 5000")
    if args.skip_blobs and not args.execute:
        parser.error("--skip-blobs requiere --execute")
    if args.reconcile_fault_page_assets and not args.execute:
        parser.error("--reconcile-fault-page-assets requiere --execute")
    if args.repair_fault_page_assets_only and not args.execute:
        parser.error("--repair-fault-page-assets-only requiere --execute")
    if args.repair_fault_page_assets_only and args.skip_blobs:
        parser.error("--repair-fault-page-assets-only no se combina con --skip-blobs")
    if args.repair_fault_page_assets_only and args.reconcile_fault_page_assets:
        parser.error(
            "--repair-fault-page-assets-only no se combina con "
            "--reconcile-fault-page-assets"
        )
    try:
        exit_code = asyncio.run(_main_async(args))
    except CorpusValidationError as exc:
        print(f"Paquete Navifault inválido: {exc}", file=sys.stderr)
        exit_code = 2
    except Exception as exc:
        print(f"Importación Navifault falló: {type(exc).__name__}: {exc}", file=sys.stderr)
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
