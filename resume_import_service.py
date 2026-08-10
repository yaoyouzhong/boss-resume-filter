"""Transactional persistence for imported candidate resume references."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_identity import normalize_job_name
from resume_store import (
    ResumeCleanupResult,
    UnmanagedResumePathError,
    clear_candidate_resume_state,
    delete_managed_resume,
    store_resume_copy,
)
from storage import mutate_candidates_with_resume_cleanup, read_candidates_snapshot


class ResumeCopyError(RuntimeError):
    """Raised when a selected resume cannot enter managed storage."""


class ResumePersistenceError(RuntimeError):
    """Raised when candidate reference persistence ends in an uncertain state."""

    def __init__(self, message: str, *, copy_retained: bool) -> None:
        super().__init__(message)
        self.copy_retained = copy_retained


class ResumeCandidateNotFoundError(RuntimeError):
    """Raised when the target candidate vanished before reference replacement."""


@dataclass(frozen=True)
class CandidateResumePersistenceResult:
    """Persisted candidate snapshot and obsolete-copy cleanup result."""

    candidate: dict[str, Any]
    cleanup: ResumeCleanupResult


def _candidate_identity(candidate: dict[str, Any]) -> tuple[str, str]:
    return (
        str(candidate.get("geek_id") or ""),
        normalize_job_name(candidate.get("job_name")),
    )


def persist_candidate_resume(
    source_path: str | Path,
    *,
    identity: tuple[str, str],
    candidates_path: str | Path,
    base_dir: str | Path,
    imported_at: str,
) -> CandidateResumePersistenceResult:
    """Store a new resume and atomically replace one candidate reference."""
    root = Path(base_dir)
    target_identity = (
        str(identity[0] or ""),
        normalize_job_name(identity[1]),
    )
    try:
        managed_resume = store_resume_copy(source_path, base_dir=root)
    except Exception as exc:
        raise ResumeCopyError(str(exc)) from exc

    updated_snapshot: dict[str, Any] = {}

    def replace_resume_reference(candidates: list[dict[str, Any]]) -> int:
        for persisted in candidates:
            if _candidate_identity(persisted) != target_identity:
                continue
            clear_candidate_resume_state(persisted)
            persisted["resume_file"] = managed_resume.reference
            persisted["resume_artifact_id"] = managed_resume.artifact_id
            persisted["resume_original_name"] = managed_resume.original_name
            persisted["resume_imported_at"] = imported_at
            updated_snapshot.update(persisted)
            return 1
        return 0

    try:
        saved, cleanup = mutate_candidates_with_resume_cleanup(
            replace_resume_reference,
            candidates_path,
            base_dir=root,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        copy_retained = True
        try:
            latest_candidates = read_candidates_snapshot(candidates_path)
            copy_retained = any(
                _candidate_identity(persisted) == target_identity
                and persisted.get("resume_file") == managed_resume.reference
                for persisted in latest_candidates
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
        if not copy_retained:
            try:
                delete_managed_resume(
                    managed_resume.reference,
                    base_dir=root,
                )
            except (OSError, UnmanagedResumePathError):
                pass
        raise ResumePersistenceError(
            str(exc),
            copy_retained=copy_retained,
        ) from exc

    if not saved:
        try:
            delete_managed_resume(
                managed_resume.reference,
                base_dir=root,
            )
        except (OSError, UnmanagedResumePathError):
            pass
        raise ResumeCandidateNotFoundError(
            "本地候选人记录已发生变化，本次导入没有保存。"
        )

    return CandidateResumePersistenceResult(
        candidate=updated_snapshot,
        cleanup=cleanup,
    )
