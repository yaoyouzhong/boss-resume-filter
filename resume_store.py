"""Managed resume-file storage.

This module is the only place allowed to create, resolve, or delete the
application's managed resume copies. Encryption will be added behind this
boundary without exposing raw filesystem paths to GUI code.
"""
from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from paths import get_base_dir


class UnmanagedResumePathError(ValueError):
    """Raised when a resume reference escapes the managed resume directory."""


@dataclass(frozen=True)
class ManagedResume:
    """Metadata for one newly stored managed resume copy."""

    artifact_id: str
    reference: str
    original_name: str


def get_managed_resumes_dir(base_dir: Path | None = None) -> Path:
    """Return the canonical directory containing application-managed resumes."""
    root = Path(base_dir) if base_dir is not None else get_base_dir()
    return (root / "resumes").resolve()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_managed_resume(
    reference: str | os.PathLike[str],
    *,
    base_dir: Path | None = None,
    require_exists: bool = False,
) -> Path:
    """Resolve a stored reference and reject paths outside managed storage."""
    root = (Path(base_dir) if base_dir is not None else get_base_dir()).resolve()
    resumes_dir = get_managed_resumes_dir(root)
    raw_path = Path(reference)
    candidate_path = raw_path if raw_path.is_absolute() else root / raw_path
    resolved = candidate_path.resolve(strict=False)
    if not _is_within(resolved, resumes_dir) or resolved == resumes_dir:
        raise UnmanagedResumePathError("简历文件不在程序受管目录内")
    if require_exists and not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def store_resume_copy(
    source: str | os.PathLike[str],
    *,
    base_dir: Path | None = None,
) -> ManagedResume:
    """Atomically copy a resume into managed storage using a non-identifying name."""
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    root = (Path(base_dir) if base_dir is not None else get_base_dir()).resolve()
    resumes_dir = get_managed_resumes_dir(root)
    resumes_dir.mkdir(parents=True, exist_ok=True)
    artifact_id = uuid.uuid4().hex
    extension = source_path.suffix.lower()
    destination = resumes_dir / f"{artifact_id}{extension}"
    tmp_path = resumes_dir / f".{artifact_id}.tmp"

    try:
        shutil.copy2(source_path, tmp_path)
        with open(tmp_path, "r+b") as copied:
            os.fsync(copied.fileno())
        os.replace(tmp_path, destination)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

    return ManagedResume(
        artifact_id=artifact_id,
        reference=destination.relative_to(root).as_posix(),
        original_name=source_path.name,
    )


def delete_managed_resume(
    reference: str | os.PathLike[str],
    *,
    base_dir: Path | None = None,
) -> bool:
    """Delete one managed resume; never follow a reference outside managed storage."""
    resume_path = resolve_managed_resume(reference, base_dir=base_dir)
    if not resume_path.exists():
        return False
    if not resume_path.is_file():
        raise UnmanagedResumePathError("受管简历引用不是普通文件")
    resume_path.unlink()
    return True
