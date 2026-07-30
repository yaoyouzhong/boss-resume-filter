"""Validated JSON snapshots with atomic replacement and recoverable backups."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable


JsonValidator = Callable[[Any], None]


class JsonSnapshotError(RuntimeError):
    """Raised when neither the primary JSON snapshot nor its backup is usable."""


def _read_validated(path: Path, validator: JsonValidator) -> Any:
    with open(path, "r", encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    validator(payload)
    return payload


def _sync_file(file_obj: Any) -> None:
    file_obj.flush()
    os.fsync(file_obj.fileno())


def _sync_parent_directory(path: Path) -> None:
    if os.name == "nt":
        return
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_copy(source: Path, target: Path) -> None:
    tmp_target = Path(str(target) + ".tmp")
    try:
        shutil.copy2(source, tmp_target)
        with open(tmp_target, "r+b") as copied:
            os.fsync(copied.fileno())
        os.replace(tmp_target, target)
        _sync_parent_directory(target)
    finally:
        if tmp_target.exists():
            try:
                tmp_target.unlink()
            except OSError:
                pass


def load_json_snapshot(
    path: str | Path,
    validator: JsonValidator,
    *,
    backup_path: str | Path | None = None,
    default: Any = None,
) -> Any:
    """Load a validated snapshot, atomically restoring a valid backup if needed."""
    primary = Path(path)
    backup = Path(backup_path) if backup_path is not None else Path(str(primary) + ".bak")
    primary_error: Exception | None = None

    if primary.exists():
        try:
            return _read_validated(primary, validator)
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            primary_error = exc

    if backup.exists():
        try:
            payload = _read_validated(backup, validator)
            primary.parent.mkdir(parents=True, exist_ok=True)
            _atomic_copy(backup, primary)
            return payload
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            if primary_error is None:
                primary_error = exc

    if not primary.exists() and not backup.exists():
        return default

    raise JsonSnapshotError(
        f"JSON 主文件和备份均不可用：{primary_error or '未知错误'}"
    ) from primary_error


def save_json_snapshot(
    payload: Any,
    path: str | Path,
    validator: JsonValidator,
    *,
    backup_path: str | Path | None = None,
    indent: int = 2,
) -> None:
    """Validate, fsync and atomically replace a JSON snapshot.

    The previous primary is rotated to ``.bak`` only after it has passed the
    same validator, so a corrupt primary can never overwrite a good backup.
    """
    primary = Path(path)
    backup = Path(backup_path) if backup_path is not None else Path(str(primary) + ".bak")
    primary.parent.mkdir(parents=True, exist_ok=True)
    validator(payload)
    tmp_path = Path(str(primary) + ".tmp")

    try:
        with open(tmp_path, "w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=indent)
            _sync_file(file_obj)
        _read_validated(tmp_path, validator)

        if primary.exists():
            try:
                _read_validated(primary, validator)
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                pass
            else:
                _atomic_copy(primary, backup)

        os.replace(tmp_path, primary)
        _sync_parent_directory(primary)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
