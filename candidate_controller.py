"""Persistence-backed candidate action controller without Tk dependencies."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from candidate_workflow import (
    CONTACTED_FOLLOWUP_STATUSES,
    apply_followup_state,
    derive_candidate_decision,
)
from constants import SCORE_THRESHOLD_PASS, SCORE_THRESHOLD_RECOMMEND
from data_schema import (
    canonical_candidate_identity,
    candidate_identity_from_values,
)


Candidate = dict[str, Any]


@dataclass(frozen=True)
class CandidatePersistence:
    """Explicit storage operations required by candidate actions."""

    update_records: Callable[..., int]
    mutate_all: Callable[..., Any]
    mutate_with_resume_cleanup: Callable[..., Any]
    remove_with_resume_cleanup: Callable[..., Any]
    mark_greeted: Callable[..., None]
    mark_not_greeted: Callable[..., None]


@dataclass(frozen=True)
class ResumeImportOutcome:
    """Plain result of parsing and atomically attaching one resume."""

    resume_text: str
    cleanup: Any


@dataclass(frozen=True)
class ResumeRevertOutcome:
    """Plain result of reverting one persisted resume evaluation."""

    updated: bool
    score: int
    cleanup: Any


class CandidateController:
    """Apply candidate decisions atomically and mirror them to active snapshots."""

    def __init__(
        self,
        candidate_path: Path,
        base_dir: Path,
        persistence: CandidatePersistence,
    ) -> None:
        self._candidate_path = candidate_path
        self._base_dir = base_dir
        self._persistence = persistence

    @staticmethod
    def identity(candidate: Candidate) -> tuple[str, str]:
        """Return the canonical candidate/job identity."""
        return canonical_candidate_identity(candidate)

    def import_resume(
        self,
        candidate: Candidate,
        source_path: str | Path,
        *,
        parser: Callable[[str | Path], str],
        persister: Callable[..., Any],
        imported_at: str | None = None,
    ) -> ResumeImportOutcome:
        """Parse and atomically attach a resume using explicit services."""
        resume_text = parser(source_path)
        persistence = persister(
            source_path,
            identity=self.identity(candidate),
            candidates_path=self._candidate_path,
            base_dir=self._base_dir,
            imported_at=imported_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        candidate.clear()
        candidate.update(persistence.candidate)
        return ResumeImportOutcome(resume_text, persistence.cleanup)

    @staticmethod
    def evaluate_resume(
        candidate: Candidate,
        resume_text: str,
        job_requirement: str,
        api_config: dict[str, Any],
        api_key: str,
        hard_conditions: str,
        *,
        evaluator: Callable[..., Any],
    ) -> Any:
        """Run resume evaluation through an explicitly supplied evaluator."""
        return evaluator(
            candidate,
            resume_text,
            job_requirement,
            api_config,
            api_key,
            hard_conditions=hard_conditions,
        )

    def persist_resume_evaluation(self, candidate: Candidate) -> bool:
        """Persist only fields produced by the second resume evaluation."""
        identity = self.identity(candidate)

        def mutate(persisted: Candidate) -> None:
            for field in _RESUME_EVALUATION_FIELDS:
                if field in candidate:
                    persisted[field] = candidate[field]

        updated = self._persistence.update_records(
            lambda persisted: self.identity(persisted) == identity,
            mutate,
            self._candidate_path,
        )
        return bool(updated)

    def revert_resume_evaluation(
        self,
        candidate: Candidate,
        *,
        resolve_rule_score: Callable[[Candidate], int],
        recalc_recommend_level: Callable[[int], str],
        resume_state_fields: Iterable[str],
    ) -> ResumeRevertOutcome:
        """Remove resume state and restore the score before resume adjustment."""
        identity = self.identity(candidate)
        updated_snapshot: Candidate = {}
        reverted_score = _coerce_score(candidate.get("match_score", 0))

        def revert_resume(persisted: Candidate) -> None:
            nonlocal reverted_score
            rule_score = resolve_rule_score(persisted)
            llm_adjustment = _coerce_score(persisted.get("llm_adjustment", 0))
            reverted_score = max(0, min(100, rule_score + llm_adjustment))
            for field in resume_state_fields:
                persisted.pop(field, None)
            persisted["rule_score"] = rule_score
            persisted["match_score"] = reverted_score
            persisted["recommend_level"] = recalc_recommend_level(reverted_score)
            breakdown = persisted.get("score_breakdown")
            if isinstance(breakdown, dict):
                breakdown.pop("resume_adjustment", None)
                breakdown["total"] = reverted_score
            updated_snapshot.update(persisted)

        def mutate_all(records: list[Candidate]) -> int:
            for persisted in records:
                if self.identity(persisted) != identity:
                    continue
                revert_resume(persisted)
                return 1
            return 0

        updated, cleanup = self._persistence.mutate_with_resume_cleanup(
            mutate_all,
            self._candidate_path,
            base_dir=self._base_dir,
        )
        if updated:
            candidate.clear()
            candidate.update(updated_snapshot)
        return ResumeRevertOutcome(bool(updated), reverted_score, cleanup)

    def blacklist(
        self,
        geek_id: object,
        reason: str,
        *,
        timestamp: str | None = None,
        candidate: Candidate | None = None,
    ) -> int:
        """Blacklist every record for one candidate identity."""
        if not self._candidate_path.exists():
            return 0
        changed_at = timestamp or _timestamp()

        def mutate(record: Candidate) -> None:
            _apply_blacklist(record, reason, changed_at)

        updated = self._persistence.update_records(
            lambda record: str(record.get("geek_id")) == str(geek_id),
            mutate,
            self._candidate_path,
            update_all=True,
        )
        if updated and candidate is not None:
            mutate(candidate)
        return int(updated or 0)

    def unblacklist(
        self,
        geek_id: object,
        *,
        candidate: Candidate | None = None,
    ) -> int:
        """Remove blacklist state from every record for one candidate."""
        if not self._candidate_path.exists():
            return 0
        updated = self._persistence.update_records(
            lambda record: (
                str(record.get("geek_id")) == str(geek_id)
                and bool(record.get("blacklisted"))
            ),
            _apply_unblacklist,
            self._candidate_path,
            update_all=True,
        )
        if updated and candidate is not None:
            _apply_unblacklist(candidate)
        return int(updated or 0)

    def update_followup(
        self,
        geek_id: object,
        job_name: object,
        status: str,
        note: str,
        next_followup_at: str | None = None,
        timestamp: str | None = None,
        *,
        job_uuid: object = None,
        candidate: Candidate | None = None,
    ) -> bool:
        """Persist one candidate/job follow-up transition."""
        if not self._candidate_path.exists():
            return False
        changed_at = timestamp or _timestamp()

        def mutate(record: Candidate) -> None:
            _apply_followup(
                record,
                status,
                note,
                next_followup_at,
                changed_at,
                self._persistence,
            )

        target_identity = _requested_identity(
            geek_id,
            job_name,
            job_uuid=job_uuid,
            candidate=candidate,
        )
        updated = self._persistence.update_records(
            lambda record: self.identity(record) == target_identity,
            mutate,
            self._candidate_path,
        )
        if updated and candidate is not None:
            mutate(candidate)
        return bool(updated)

    def complete_review(
        self,
        geek_id: object,
        job_name: object,
        *,
        job_uuid: object = None,
        contact_approval_reason: str = "",
        review_passed_reasons: Iterable[str] | None = None,
        timestamp: str | None = None,
        candidate: Candidate | None = None,
    ) -> int:
        """Persist one completed manual review and optional contact approval."""
        if not geek_id or not self._candidate_path.exists():
            return 0
        changed_at = timestamp or _timestamp()
        reasons = list(review_passed_reasons or [])
        target_identity = _requested_identity(
            geek_id,
            job_name,
            job_uuid=job_uuid,
            candidate=candidate,
        )

        def mutate_all(records: list[Candidate]) -> int:
            updated = 0
            for record in records:
                if self.identity(record) != target_identity:
                    continue
                if not (
                    record.get("manual_review_required")
                    or record.get("qualification_status") == "manual_review"
                ):
                    continue
                _apply_review_passed(
                    record,
                    reasons or list(
                        derive_candidate_decision(record).review_reasons
                        or ("人工复核",)
                    ),
                    changed_at,
                    contact_approval_reason,
                )
                updated += 1
            return updated

        updated = int(
            self._persistence.mutate_all(mutate_all, self._candidate_path) or 0
        )
        if updated and candidate is not None:
            _apply_review_passed(
                candidate,
                reasons or list(
                    derive_candidate_decision(candidate).review_reasons
                    or ("人工复核",)
                ),
                changed_at,
                contact_approval_reason,
            )
        return updated

    def reject_review(
        self,
        geek_id: object,
        job_name: object,
        *,
        job_uuid: object = None,
        review_rejected_reasons: Iterable[str] | None = None,
        timestamp: str | None = None,
        candidate: Candidate | None = None,
    ) -> int:
        """Persist one explicit human decision that review did not pass."""
        if not geek_id or not self._candidate_path.exists():
            return 0
        changed_at = timestamp or _timestamp()
        requested_reasons = list(review_rejected_reasons or [])
        target_identity = _requested_identity(
            geek_id,
            job_name,
            job_uuid=job_uuid,
            candidate=candidate,
        )

        def mutate_all(records: list[Candidate]) -> int:
            for record in records:
                if self.identity(record) != target_identity:
                    continue
                decision = derive_candidate_decision(record)
                if decision.review_status != "pending":
                    continue
                _apply_review_rejected(
                    record,
                    requested_reasons
                    or list(decision.review_reasons)
                    or ["人工复核不通过"],
                    changed_at,
                )
                return 1
            return 0

        updated = int(
            self._persistence.mutate_all(mutate_all, self._candidate_path) or 0
        )
        if updated and candidate is not None:
            decision = derive_candidate_decision(candidate)
            _apply_review_rejected(
                candidate,
                requested_reasons
                or list(decision.review_reasons)
                or ["人工复核不通过"],
                changed_at,
            )
        return updated

    def approve_contact(
        self,
        geek_id: object,
        job_name: object,
        reason: str,
        *,
        job_uuid: object = None,
        timestamp: str | None = None,
        candidate: Candidate | None = None,
    ) -> bool:
        """Persist explicit approval to contact one pending candidate."""
        if not geek_id or not self._candidate_path.exists():
            return False
        changed_at = timestamp or _timestamp()
        target_identity = _requested_identity(
            geek_id,
            job_name,
            job_uuid=job_uuid,
            candidate=candidate,
        )

        def mutate(record: Candidate) -> None:
            _apply_contact_approval(record, reason, changed_at)

        updated = self._persistence.update_records(
            lambda record: self.identity(record) == target_identity,
            mutate,
            self._candidate_path,
        )
        if updated and candidate is not None:
            mutate(candidate)
        return bool(updated)

    def update_feedback(
        self,
        geek_id: object,
        job_name: object,
        status: str,
        reasons: Iterable[str],
        note: str,
        *,
        job_uuid: object = None,
        timestamp: str | None = None,
        candidate: Candidate | None = None,
    ) -> bool:
        """Persist human feedback and any implied review state."""
        if not self._candidate_path.exists():
            return False
        changed_at = timestamp or _timestamp()
        reason_list = list(reasons)
        target_identity = _requested_identity(
            geek_id,
            job_name,
            job_uuid=job_uuid,
            candidate=candidate,
        )

        def mutate(record: Candidate) -> None:
            _apply_feedback(record, status, reason_list, note, changed_at)

        updated = self._persistence.update_records(
            lambda record: self.identity(record) == target_identity,
            mutate,
            self._candidate_path,
        )
        if updated and candidate is not None:
            mutate(candidate)
        return bool(updated)

    def save_ai_evaluations(
        self,
        candidates: Iterable[Candidate],
    ) -> dict[tuple[str, str], Candidate]:
        """Merge AI evaluation fields into the latest persisted snapshot."""
        evaluation_map = {
            self.identity(candidate): candidate
            for candidate in candidates
            if self.identity(candidate)[0]
        }

        def mutate_all(records: list[Candidate]) -> int:
            updated = 0
            for record in records:
                result = evaluation_map.get(self.identity(record))
                if result is None:
                    continue
                record.update(
                    {field: result.get(field) for field in _AI_EVALUATION_FIELDS}
                )
                updated += 1
            return updated

        self._persistence.mutate_all(mutate_all, self._candidate_path)
        return evaluation_map

    def remove_records(self, predicate: Callable[[Candidate], bool]) -> tuple[Any, Any]:
        """Remove matching records and reclaim only unreferenced managed resumes."""
        return self._persistence.remove_with_resume_cleanup(
            predicate,
            self._candidate_path,
            base_dir=self._base_dir,
        )


_AI_EVALUATION_FIELDS = (
    "llm_evaluated",
    "llm_adjustment",
    "llm_reason",
    "llm_model",
    "llm_error",
    "match_score",
    "recommend_level",
    "rule_score",
    "score_breakdown",
    "llm_hard_condition_verdict",
    "llm_hard_condition_findings",
    "llm_dimension_scores",
    "qualification_status",
    "qualification_reasons",
    "qualification_evidence",
    "manual_review_required",
    "auto_greet_blocked_reason",
)

_RESUME_EVALUATION_FIELDS = (
    "resume_eval_adjustment",
    "resume_eval_reason",
    "resume_eval_model",
    "resume_eval_at",
    "resume_eval_dimension_scores",
    "rule_score",
    "match_score",
    "recommend_level",
    "score_breakdown",
)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _coerce_score(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _requested_identity(
    geek_id: object,
    job_name: object,
    *,
    job_uuid: object = None,
    candidate: Candidate | None = None,
) -> tuple[str, str]:
    if candidate is not None:
        return canonical_candidate_identity(candidate)
    return candidate_identity_from_values(geek_id, job_uuid, job_name)


def _apply_blacklist(candidate: Candidate, reason: str, changed_at: str) -> None:
    candidate["blacklisted"] = True
    candidate["blacklist_reason"] = reason.strip()
    candidate["blacklisted_at"] = changed_at
    if candidate.get("followup_status") not in {"不合适", "已归档"}:
        apply_followup_state(
            candidate,
            "不合适",
            candidate.get("followup_note", ""),
            timestamp=changed_at,
        )


def _apply_unblacklist(candidate: Candidate) -> None:
    candidate.pop("blacklisted", None)
    candidate.pop("blacklist_reason", None)
    candidate.pop("blacklisted_at", None)


def _apply_followup(
    candidate: Candidate,
    status: str,
    note: str,
    next_followup_at: str | None,
    changed_at: str,
    persistence: CandidatePersistence,
) -> None:
    if status == "未沟通":
        persistence.mark_not_greeted(candidate, changed_at)
    elif status in CONTACTED_FOLLOWUP_STATUSES and not candidate.get("greet_sent"):
        persistence.mark_greeted(candidate, "manual_status", changed_at)
    apply_followup_state(
        candidate,
        status,
        note,
        timestamp=changed_at,
        next_followup_at=next_followup_at,
    )


def _apply_review_passed(
    candidate: Candidate,
    reasons: list[str],
    changed_at: str,
    contact_approval_reason: str,
) -> None:
    candidate["manual_review_required"] = False
    candidate["qualification_status"] = "qualified"
    candidate["qualification_reasons"] = []
    candidate.pop("auto_greet_blocked_reason", None)
    candidate["review_passed_at"] = changed_at
    candidate["review_passed_reasons"] = reasons
    candidate.pop("review_rejected_at", None)
    candidate.pop("review_rejected_reasons", None)
    if contact_approval_reason:
        candidate["contact_approved_at"] = changed_at
        candidate["contact_approval_reason"] = contact_approval_reason


def _apply_review_rejected(
    candidate: Candidate,
    reasons: list[str],
    changed_at: str,
) -> None:
    candidate["manual_review_required"] = False
    candidate["qualification_status"] = "rejected"
    candidate["qualification_reasons"] = reasons
    candidate["review_rejected_at"] = changed_at
    candidate["review_rejected_reasons"] = reasons
    candidate["recommend_level"] = "未通过"
    candidate.pop("review_passed_at", None)
    candidate.pop("review_passed_reasons", None)
    candidate.pop("contact_approved_at", None)
    candidate.pop("contact_approval_reason", None)
    candidate.pop("auto_greet_blocked_reason", None)


def _apply_contact_approval(
    candidate: Candidate,
    reason: str,
    changed_at: str,
) -> None:
    review_reasons = list(
        derive_candidate_decision(candidate).review_reasons
        or [f"评分处于待定区间（{candidate.get('match_score', 0)} 分）"]
    )
    candidate["contact_approved_at"] = changed_at
    candidate["contact_approval_reason"] = str(reason or "").strip()
    candidate["review_passed_at"] = changed_at
    candidate["review_passed_reasons"] = review_reasons
    candidate.pop("review_rejected_at", None)
    candidate.pop("review_rejected_reasons", None)


def _apply_feedback(
    candidate: Candidate,
    status: str,
    reasons: list[str],
    note: str,
    changed_at: str,
) -> None:
    candidate["feedback_status"] = status
    candidate["feedback_reasons"] = reasons
    candidate["feedback_note"] = note.strip()
    candidate["feedback_updated_at"] = changed_at
    try:
        score = int(candidate.get("match_score", 0) or 0)
    except (TypeError, ValueError):
        score = 0
    if status == "合适" and SCORE_THRESHOLD_PASS <= score < SCORE_THRESHOLD_RECOMMEND:
        candidate["review_passed_at"] = changed_at
        candidate["review_passed_reasons"] = [f"评分处于待定区间（{score} 分）"]
    if status in {"误推", "放弃"}:
        candidate.pop("contact_approved_at", None)
        candidate.pop("contact_approval_reason", None)
