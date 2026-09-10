"""Build a clean, developer-facing ZIP from an explicit package allowlist."""

from __future__ import annotations

import argparse
import re
import stat
import sys
import zipfile
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_ROOT = "seguimiento_etiquetas_produccion"
ROOT_FILES = {
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "compose.yaml",
    "Dockerfile",
    "INSTRUCCIONES_TRANSFERENCIA.md",
    "README.md",
    "requirements-dev.txt",
    "requirements.txt",
}
ALLOWED_DIRECTORIES = {"dashboard", "deploy", "docs", "scripts", "seed", "tests"}
FORBIDDEN_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv", ".git"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".zip", ".jsonl", ".parquet", ".csv", ".log", ".db", ".sqlite", ".sqlite3"}
SIGNED_URL = re.compile(
    rb"https?://[^\s\"']+\?[^\s\"']*(?:x-amz-signature|x-amz-security-token|x-amz-credential|x-amz-algorithm|x-amz-expires|x-goog-signature|signature|token|sig|se)=",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT = re.compile(rb"(?mi)^\s*CLOUDFLEET_API_KEY\s*=\s*([^\s#]+)")
BEARER_CREDENTIAL = re.compile(
    rb"(?i)Authorization\s*:\s*Bearer\s+(?![<{][$A-Z_][^>}]*[>}])([A-Za-z0-9._~+/=-]{8,})"
)


class PackageError(RuntimeError):
    pass


def write_portable_file(archive: zipfile.ZipFile, path: Path, archive_name: str) -> None:
    """Write deterministic Unix permissions even when packaging on Windows."""
    info = zipfile.ZipInfo(archive_name, date_time=(2020, 1, 1, 0, 0, 0))
    info.create_system = 3
    permissions = 0o755 if path.suffix == ".sh" else 0o644
    info.external_attr = (stat.S_IFREG | permissions) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def write_empty_marker(archive: zipfile.ZipFile, archive_name: str) -> None:
    info = zipfile.ZipInfo(archive_name, date_time=(2020, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, b"", compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def is_forbidden(path: Path) -> bool:
    relative = path.relative_to(PACKAGE_ROOT)
    if any(part in FORBIDDEN_PARTS for part in relative.parts):
        return True
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        return True
    return path.suffix.lower() in FORBIDDEN_SUFFIXES


def selected_files() -> list[Path]:
    candidates: list[Path] = []
    for path in PACKAGE_ROOT.iterdir():
        if path.is_file() and (path.name in ROOT_FILES or path.suffix == ".py"):
            candidates.append(path)
    for directory_name in sorted(ALLOWED_DIRECTORIES):
        directory = PACKAGE_ROOT / directory_name
        if directory.exists():
            candidates.extend(path for path in directory.rglob("*") if path.is_file())
    files = sorted({path.resolve() for path in candidates if not is_forbidden(path)})
    for path in files:
        try:
            path.relative_to(PACKAGE_ROOT.resolve())
        except ValueError as exc:
            raise PackageError(f"Archivo fuera del paquete: {path}") from exc
    return files


def validate_file(path: Path) -> None:
    data = path.read_bytes()
    if SECRET_ASSIGNMENT.search(data):
        raise PackageError(f"Credencial asignada en {path.relative_to(PACKAGE_ROOT)}")
    if BEARER_CREDENTIAL.search(data):
        raise PackageError(f"Bearer token incrustado en {path.relative_to(PACKAGE_ROOT)}")
    if SIGNED_URL.search(data):
        raise PackageError(f"Posible URL firmada en {path.relative_to(PACKAGE_ROOT)}")


def validate(files: list[Path]) -> None:
    if not (PACKAGE_ROOT / "dashboard" / "sample-data.json").exists():
        raise PackageError("Falta dashboard/sample-data.json sanitizado para el modo demo")
    required = {PACKAGE_ROOT / name for name in ROOT_FILES}
    missing = sorted(path.name for path in required if not path.exists())
    if missing:
        raise PackageError(f"Faltan archivos requeridos: {', '.join(missing)}")
    for path in files:
        validate_file(path)
    sys.path.insert(0, str(PACKAGE_ROOT))
    try:
        from historical_seed import SeedValidationError, load_certified_seed

        load_certified_seed(PACKAGE_ROOT / "seed")
    except (ImportError, SeedValidationError) as exc:
        raise PackageError(f"Seed histórico certificado inválido: {exc}") from exc


def build(output: Path, force: bool) -> None:
    files = selected_files()
    validate(files)
    output = output.resolve()
    if output == PACKAGE_ROOT or PACKAGE_ROOT in output.parents:
        raise PackageError("El ZIP debe escribirse fuera del directorio fuente")
    if output.exists() and not force:
        raise PackageError(f"El destino ya existe: {output}. Use --force para reemplazarlo.")
    output.parent.mkdir(parents=True, exist_ok=True)
    mode = "w"  # zipfile truncates only after all validations have passed
    with zipfile.ZipFile(output, mode=mode, compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(PACKAGE_ROOT).as_posix()
            write_portable_file(archive, path, f"{ARCHIVE_ROOT}/{relative}")
        for empty_dir in ("runtime", "output", "logs"):
            write_empty_marker(archive, f"{ARCHIVE_ROOT}/{empty_dir}/.gitkeep")
    print(f"ZIP listo: {output} ({len(files)} archivos)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PACKAGE_ROOT.parent / f"{ARCHIVE_ROOT}.zip",
        help="ruta del ZIP; debe estar fuera de la carpeta fuente",
    )
    parser.add_argument("--force", action="store_true", help="reemplaza un ZIP existente")
    parser.add_argument("--verify-only", action="store_true", help="valida sin crear el ZIP")
    args = parser.parse_args()
    try:
        files = selected_files()
        validate(files)
        if args.verify_only:
            print(f"Paquete válido: {len(files)} archivos seleccionados")
            return
        build(args.output, args.force)
    except PackageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
