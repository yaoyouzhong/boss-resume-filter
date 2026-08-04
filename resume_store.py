"""Managed resume-file storage.

This module is the only place allowed to create, resolve, or delete the
application's managed resume copies. Encryption will be added behind this
boundary without exposing raw filesystem paths to GUI code.
"""
from __future__ import annotations

import os
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from paths import get_base_dir


class UnmanagedResumePathError(ValueError):
    """Raised when a resume reference escapes the managed resume directory."""


@dataclass(frozen=True)
class ManagedResume:
    """Metadata for one newly stored managed resume copy."""

    artifact_id: str
    reference: str
    original_name: str


@dataclass(frozen=True)
class ResumeStorageAudit:
    """Privacy-safe summary of managed resume references and stored files."""

    reference_count: int
    valid_reference_count: int
    missing_reference_count: int
    unmanaged_reference_count: int
    stale_metadata_count: int
    managed_file_count: int
    referenced_file_count: int
    orphan_file_count: int
    shared_file_count: int
    managed_bytes: int
    orphan_bytes: int

    @property
    def issue_count(self) -> int:
        """Return the number of references or files requiring attention."""
        return (
            self.missing_reference_count
            + self.unmanaged_reference_count
            + self.stale_metadata_count
            + self.orphan_file_count
        )


@dataclass(frozen=True)
class ResumeCleanupResult:
    """Privacy-safe result of one reference-aware managed-file cleanup."""

    target_file_count: int = 0
    deleted_file_count: int = 0
    retained_file_count: int = 0
    missing_file_count: int = 0
    unmanaged_reference_count: int = 0
    failed_file_count: int = 0
    scan_error_count: int = 0
    reclaimed_bytes: int = 0

    @property
    def failure_count(self) -> int:
        """Return file-deletion and directory-scan failures."""
        return self.failed_file_count + self.scan_error_count


@dataclass(frozen=True)
class ResumeReferenceRepair:
    """Summary of invalid candidate resume state removed in memory."""

    repaired_candidate_count: int = 0
    missing_reference_count: int = 0
    unmanaged_reference_count: int = 0
    stale_metadata_count: int = 0


RESUME_STATE_FIELDS = (
    "resume_file",
    "resume_artifact_id",
    "resume_original_name",
    "resume_imported_at",
    "resume_eval_adjustment",
    "resume_eval_reason",
    "resume_eval_model",
    "resume_eval_at",
    "resume_eval_dimension_scores",
)


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


def _managed_resume_files(resumes_dir: Path) -> dict[Path, int]:
    """Return non-symlink regular files and sizes without following links."""
    if not resumes_dir.exists():
        return {}
    if not resumes_dir.is_dir():
        raise NotADirectoryError(resumes_dir)

    managed_files: dict[Path, int] = {}

    def raise_scan_error(error: OSError) -> None:
        raise error

    for current_dir, dir_names, file_names in os.walk(
        resumes_dir,
        topdown=True,
        onerror=raise_scan_error,
        followlinks=False,
    ):
        current_path = Path(current_dir)
        dir_names[:] = [
            name for name in dir_names
            if not (current_path / name).is_symlink()
        ]
        for file_name in file_names:
            file_path = current_path / file_name
            if file_path.is_symlink() or not file_path.is_file():
                continue
            resolved = file_path.resolve(strict=True)
            if not _is_within(resolved, resumes_dir):
                continue
            managed_files[resolved] = file_path.stat().st_size
    return managed_files


def _resume_reference(candidate: Mapping[str, Any]) -> object:
    return candidate.get("resume_file")


def _has_stale_resume_metadata(candidate: Mapping[str, Any]) -> bool:
    return any(
        field != "resume_file" and candidate.get(field) is not None
        for field in RESUME_STATE_FIELDS
    )


def _active_managed_reference_paths(
    candidates: Iterable[Mapping[str, Any]],
    root: Path,
) -> set[Path]:
    active: set[Path] = set()
    for candidate in candidates:
        reference = _resume_reference(candidate)
        if not isinstance(reference, (str, os.PathLike)):
            continue
        if not str(reference).strip():
            continue
        try:
            active.add(resolve_managed_resume(reference, base_dir=root))
        except (
            OSError,
            RuntimeError,
            TypeError,
            UnmanagedResumePathError,
            ValueError,
        ):
            continue
    return active


def clear_candidate_resume_state(candidate: dict[str, Any]) -> None:
    """Remove resume state and restore the pre-resume candidate score."""
    from llm_eval import _recalc_recommend_level, _resolve_rule_score

    rule_score = _resolve_rule_score(candidate)
    llm_adjustment = candidate.get("llm_adjustment", 0) or 0
    try:
        reverted_score = max(0, min(100, rule_score + int(llm_adjustment)))
    except (TypeError, ValueError):
        reverted_score = rule_score
    for field in RESUME_STATE_FIELDS:
        candidate.pop(field, None)
    candidate["rule_score"] = rule_score
    candidate["match_score"] = reverted_score
    candidate["recommend_level"] = _recalc_recommend_level(reverted_score)
    breakdown = candidate.get("score_breakdown")
    if isinstance(breakdown, dict):
        breakdown.pop("resume_adjustment", None)
        breakdown["total"] = reverted_score


def repair_invalid_resume_references(
    candidates: Iterable[dict[str, Any]],
    *,
    base_dir: Path | None = None,
) -> ResumeReferenceRepair:
    """Clear missing, unmanaged, or detached resume state in memory."""
    root = (Path(base_dir) if base_dir is not None else get_base_dir()).resolve()
    managed_files = _managed_resume_files(get_managed_resumes_dir(root))
    repaired = 0
    missing = 0
    unmanaged = 0
    stale = 0

    for candidate in candidates:
        reference = _resume_reference(candidate)
        if reference is None or (
            isinstance(reference, (str, os.PathLike))
            and not str(reference).strip()
        ):
            if _has_stale_resume_metadata(candidate):
                stale += 1
                repaired += 1
                clear_candidate_resume_state(candidate)
            continue
        if not isinstance(reference, (str, os.PathLike)):
            unmanaged += 1
            repaired += 1
            clear_candidate_resume_state(candidate)
            continue
        try:
            resolved = resolve_managed_resume(reference, base_dir=root)
        except (
            OSError,
            RuntimeError,
            TypeError,
            UnmanagedResumePathError,
            ValueError,
        ):
            unmanaged += 1
            repaired += 1
            clear_candidate_resume_state(candidate)
            continue
        if not resolved.exists():
            missing += 1
            repaired += 1
            clear_candidate_resume_state(candidate)
            continue
        if resolved not in managed_files:
            unmanaged += 1
            repaired += 1
            clear_candidate_resume_state(candidate)

    return ResumeReferenceRepair(
        repaired_candidate_count=repaired,
        missing_reference_count=missing,
        unmanaged_reference_count=unmanaged,
        stale_metadata_count=stale,
    )


def cleanup_unreferenced_managed_resumes(
    references: Iterable[object],
    active_candidates: Iterable[Mapping[str, Any]],
    *,
    base_dir: Path | None = None,
) -> ResumeCleanupResult:
    """Delete target managed files only when no active candidate references them."""
    root = (Path(base_dir) if base_dir is not None else get_base_dir()).resolve()
    active = _active_managed_reference_paths(active_candidates, root)
    targets: set[Path] = set()
    unmanaged = 0
    for reference in references:
        if not isinstance(reference, (str, os.PathLike)):
            if reference is not None:
                unmanaged += 1
            continue
        if not str(reference).strip():
            continue
        try:
            targets.add(resolve_managed_resume(reference, base_dir=root))
        except (
            OSError,
            RuntimeError,
            TypeError,
            UnmanagedResumePathError,
            ValueError,
        ):
            unmanaged += 1

    deleted = 0
    retained = 0
    missing = 0
    failed = 0
    reclaimed_bytes = 0
    for target in targets:
        if target in active:
            retained += 1
            continue
        if not target.exists():
            missing += 1
            continue
        if target.is_symlink() or not target.is_file():
            unmanaged += 1
            continue
        try:
            size = target.stat().st_size
            target.unlink()
        except OSError:
            failed += 1
            continue
        deleted += 1
        reclaimed_bytes += size

    return ResumeCleanupResult(
        target_file_count=len(targets),
        deleted_file_count=deleted,
        retained_file_count=retained,
        missing_file_count=missing,
        unmanaged_reference_count=unmanaged,
        failed_file_count=failed,
        reclaimed_bytes=reclaimed_bytes,
    )


def cleanup_orphan_managed_resumes(
    active_candidates: Iterable[Mapping[str, Any]],
    *,
    base_dir: Path | None = None,
) -> ResumeCleanupResult:
    """Delete every regular managed file not referenced by active candidates."""
    root = (Path(base_dir) if base_dir is not None else get_base_dir()).resolve()
    candidates = list(active_candidates)
    try:
        managed_files = _managed_resume_files(get_managed_resumes_dir(root))
    except (OSError, RuntimeError):
        return ResumeCleanupResult(scan_error_count=1)
    active = _active_managed_reference_paths(candidates, root)
    orphan_files = set(managed_files) - active
    return cleanup_unreferenced_managed_resumes(
        orphan_files,
        candidates,
        base_dir=root,
    )


def audit_managed_resumes(
    candidates: Iterable[Mapping[str, Any]],
    *,
    base_dir: Path | None = None,
) -> ResumeStorageAudit:
    """Read candidate references and managed files without changing storage."""
    root = (Path(base_dir) if base_dir is not None else get_base_dir()).resolve()
    resumes_dir = get_managed_resumes_dir(root)
    managed_files = _managed_resume_files(resumes_dir)
    valid_references: Counter[Path] = Counter()
    reference_count = 0
    valid_reference_count = 0
    missing_reference_count = 0
    unmanaged_reference_count = 0
    stale_metadata_count = 0

    for candidate in candidates:
        reference = candidate.get("resume_file")
        if not isinstance(reference, (str, os.PathLike)):
            if reference is not None:
                unmanaged_reference_count += 1
                reference_count += 1
            elif _has_stale_resume_metadata(candidate):
                stale_metadata_count += 1
            continue
        if not str(reference).strip():
            if _has_stale_resume_metadata(candidate):
                stale_metadata_count += 1
            continue
        reference_count += 1
        try:
            resolved = resolve_managed_resume(reference, base_dir=root)
        except (
            OSError,
            RuntimeError,
            TypeError,
            UnmanagedResumePathError,
            ValueError,
        ):
            unmanaged_reference_count += 1
            continue
        if not resolved.exists():
            missing_reference_count += 1
            continue
        if resolved.is_symlink() or not resolved.is_file():
            unmanaged_reference_count += 1
            continue
        resolved = resolved.resolve(strict=True)
        if resolved not in managed_files:
            unmanaged_reference_count += 1
            continue
        valid_reference_count += 1
        valid_references[resolved] += 1

    referenced_files = set(valid_references)
    orphan_files = set(managed_files) - referenced_files
    return ResumeStorageAudit(
        reference_count=reference_count,
        valid_reference_count=valid_reference_count,
        missing_reference_count=missing_reference_count,
        unmanaged_reference_count=unmanaged_reference_count,
        stale_metadata_count=stale_metadata_count,
        managed_file_count=len(managed_files),
        referenced_file_count=len(referenced_files),
        orphan_file_count=len(orphan_files),
        shared_file_count=sum(count > 1 for count in valid_references.values()),
        managed_bytes=sum(managed_files.values()),
        orphan_bytes=sum(managed_files[path] for path in orphan_files),
    )


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
