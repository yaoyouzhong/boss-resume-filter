"""Pure candidate collection cleanup rules."""
from __future__ import annotations

from collections.abc import MutableSequence
from dataclasses import dataclass
from typing import Any

from job_identity import normalize_job_name


@dataclass(frozen=True)
class CandidateCleanupOutcome:
    """Counts produced while applying one candidate cleanup request."""

    removed_count: int
    greeted_kept_count: int
    blacklist_kept_count: int


def clear_candidates_in_place(
    candidates: MutableSequence[dict[str, Any]],
    *,
    scope: str,
    selected_job: str,
    keep_greeted: bool,
) -> CandidateCleanupOutcome:
    """Clear one job or all jobs while always preserving blacklist records."""
    if scope not in {"current", "all"}:
        raise ValueError(f"Unsupported candidate cleanup scope: {scope}")

    if scope == "current":
        normalized_job = normalize_job_name(selected_job)
        outside_scope = [
            candidate
            for candidate in candidates
            if normalize_job_name(candidate.get("job_name")) != normalized_job
        ]
        target_candidates = [
            candidate
            for candidate in candidates
            if normalize_job_name(candidate.get("job_name")) == normalized_job
        ]
    else:
        outside_scope = []
        target_candidates = list(candidates)

    if keep_greeted:
        kept = [
            candidate
            for candidate in target_candidates
            if candidate.get("greet_sent") or candidate.get("blacklisted")
        ]
        removed = [
            candidate
            for candidate in target_candidates
            if not candidate.get("greet_sent")
            and not candidate.get("blacklisted")
        ]
        greeted_kept_count = sum(
            1 for candidate in kept if candidate.get("greet_sent")
        )
    else:
        kept = [
            candidate
            for candidate in target_candidates
            if candidate.get("blacklisted")
        ]
        removed = [
            candidate
            for candidate in target_candidates
            if not candidate.get("blacklisted")
        ]
        greeted_kept_count = 0

    blacklist_kept_count = sum(
        1 for candidate in kept if candidate.get("blacklisted")
    )
    candidates[:] = outside_scope + kept
    return CandidateCleanupOutcome(
        removed_count=len(removed),
        greeted_kept_count=greeted_kept_count,
        blacklist_kept_count=blacklist_kept_count,
    )
