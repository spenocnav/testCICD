"""Durable local persistence primitives used by the production worker."""
from __future__ import annotations

import json
import os
import shutil
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, fallback: Any, *, recover_backup: bool = True) -> Any:
    """Read JSON, falling back to the last atomic-write backup when necessary."""
    candidates = [path]
    backup = path.with_suffix(path.suffix + ".bak")
    if recover_backup:
        candidates.append(backup)
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
    return fallback


def atomic_write_json(path: Path, payload: Any, *, backup: bool = True) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    atomic_write_text(path, text, backup=backup)


def atomic_write_text(path: Path, text: str, *, backup: bool = True) -> None:
    """Write and fsync a sibling temp file before atomically replacing target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if backup and path.is_file():
            backup_path = path.with_suffix(path.suffix + ".bak")
            try:
                shutil.copy2(path, backup_path)
            except OSError:
                # The new primary remains atomic even if the convenience backup fails.
                pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def append_json_lines(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    encoded = [json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows]
    if not encoded:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.seek(0, os.SEEK_END)
                handle.write(b"\n")
        handle.seek(0, os.SEEK_END)
        handle.write(("\n".join(encoded) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    return len(encoded)


def read_json_lines(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    rejected = 0
    if not path.is_file():
        return rows, rejected
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                rejected += 1
                continue
            if isinstance(value, dict):
                rows.append(value)
            else:
                rejected += 1
    return rows, rejected


class InstanceLock(AbstractContextManager["InstanceLock"]):
    """Cross-platform advisory lock preventing two writers in one runtime dir."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None

    def acquire(self) -> "InstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise RuntimeError(
                f"Ya existe un worker usando el runtime ({self.path})."
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} acquired_at={utc_iso()}\n".encode("utf-8"))
        handle.flush()
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "InstanceLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.release()

