"""Candidate result view-state controller without Tk dependencies."""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import candidate_presenter
from candidate_workflow import derive_candidate_decision, filter_candidates_by_result_view
from constants import (
    SCORE_THRESHOLD_PASS,
    SCORE_THRESHOLD_RECOMMEND,
    SCORE_THRESHOLD_STRONG,
)
from job_identity import normalize_job_name


Candidate = dict[str, Any]
CandidateLoader = Callable[[Path], list[Candidate]]


@dataclass(frozen=True)
class ResultQuery:
    """Stable inputs that determine the result-page data view."""

    selected_job: str = "全部岗位"
    date_start: str | None = None
    date_end: str | None = None
    show_blacklist: bool = False
    result_view: str = "全部记录"
    evaluating_ids: frozenset[str] = frozenset()
    evaluation_results: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    now: float | None = None


@dataclass(frozen=True)
class ResultMetrics:
    """Counts shown by the result-page summary cards."""

    strong: int = 0
    strong_greeted: int = 0
    recommended: int = 0
    recommended_greeted: int = 0
    pending: int = 0
    pending_greeted: int = 0
    greeted: int = 0


@dataclass(frozen=True)
class ResultRow:
    """One fully prepared result-table row and its source candidate."""

    candidate: Candidate
    values: tuple[Any, ...]
    tag: str
    status_display: str
    status_detail: str
    extra_fields: tuple[str, str, str, str, str]
    expired_evaluation_id: str = ""


@dataclass(frozen=True)
class ResultViewState:
    """Complete non-Tk state needed to rebuild the result page."""

    all_candidates: tuple[Candidate, ...]
    view_candidates: tuple[Candidate, ...]
    rows: tuple[ResultRow, ...]
    metrics: ResultMetrics
    expired_evaluation_ids: tuple[str, ...] = ()

    @property
    def visible_count(self) -> int:
        return len(self.rows)

    @property
    def total_count(self) -> int:
        return len(self.view_candidates)


class ResultController:
    """Load candidate snapshots and derive result-page view state."""

    def __init__(self, loader: CandidateLoader) -> None:
        self._loader = loader

    def load(self, path: Path, query: ResultQuery) -> ResultViewState:
        """Load one snapshot and derive a complete view without touching Tk."""
        candidates = self._loader(path) if path.exists() else []
        return prepare_result_view(candidates, query)


def candidate_result_score(candidate: Mapping[str, Any]) -> int:
    """Return the score shown, sorted, and searched in the result table."""
    try:
        match_score = int(candidate.get("match_score") or 0)
    except (TypeError, ValueError):
        match_score = 0
    if candidate.get("qualification_status") == "rejected" and not match_score:
        try:
            reference_score = int(candidate.get("rule_score") or 0)
        except (TypeError, ValueError):
            reference_score = 0
        if reference_score > 0:
            return reference_score
    return match_score


def result_cache_key(path: Path, query: ResultQuery) -> tuple[Any, ...]:
    """Return the file and filter fingerprint used to skip unchanged rebuilds."""
    fingerprint: tuple[float, int] | None = None
    if path.exists():
        stat = path.stat()
        fingerprint = stat.st_mtime, stat.st_size
    return (
        fingerprint,
        query.selected_job,
        query.date_start,
        query.date_end,
        query.show_blacklist,
        query.result_view,
    )


def prepare_result_view(
    candidates: Iterable[Candidate],
    query: ResultQuery,
) -> ResultViewState:
    """Filter, summarize, sort, and format a candidate snapshot."""
    all_candidates = list(candidates)
    scoped = [
        candidate
        for candidate in all_candidates
        if query.show_blacklist or not candidate.get("blacklisted")
    ]
    if query.selected_job != "全部岗位":
        normalized_job = normalize_job_name(query.selected_job)
        scoped = [
            candidate
            for candidate in scoped
            if normalize_job_name(candidate.get("job_name")) == normalized_job
        ]
    if query.date_start or query.date_end:
        scoped = [
            candidate
            for candidate in scoped
            if _candidate_in_date_range(
                candidate,
                query.date_start,
                query.date_end,
            )
        ]

    metrics = _result_metrics(scoped)
    view_candidates = filter_candidates_by_result_view(scoped, query.result_view)
    sorted_candidates = sorted(
        view_candidates,
        key=candidate_result_score,
        reverse=True,
    )
    rows: list[ResultRow] = []
    expired_ids: list[str] = []
    evaluation_ids = set(query.evaluating_ids)
    evaluation_results = query.evaluation_results or {}
    now = time.time() if query.now is None else query.now
    for candidate in sorted_candidates:
        row = _build_result_row(
            candidate,
            evaluating_ids=evaluation_ids,
            evaluation_results=evaluation_results,
            now=now,
        )
        if row is None:
            continue
        rows.append(row)
        if row.expired_evaluation_id:
            expired_ids.append(row.expired_evaluation_id)
    return ResultViewState(
        all_candidates=tuple(all_candidates),
        view_candidates=tuple(sorted_candidates),
        rows=tuple(rows),
        metrics=metrics,
        expired_evaluation_ids=tuple(dict.fromkeys(expired_ids)),
    )


def result_sort_value(column: str, value: object) -> tuple[bool, float | str]:
    """Return a typed sort value for one result-table cell."""
    text = str(value or "").strip()
    if not text or text in {"—", "-"}:
        return False, 0.0
    if column not in {"exp", "salary", "skills", "score", "ai_eval", "age"}:
        return True, text.casefold()
    if column in {"exp", "salary"}:
        numbers = [float(number) for number in re.findall(r"\d+(?:\.\d+)?", text)]
        if not numbers:
            return False, 0.0
        if len(numbers) >= 2:
            return True, (numbers[0] + numbers[1]) / 2
        return True, numbers[0]
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return (True, float(match.group())) if match else (False, 0.0)


def candidate_query_match(
    candidate: Mapping[str, Any],
    query: str,
    *,
    status_display: str = "",
    status_detail: str = "",
) -> str | None:
    """Return the result-search match class for one candidate."""
    normalized = str(query or "").strip().lower()
    if not normalized:
        return None
    name = str(candidate.get("name") or "").lower()
    gender = candidate_presenter.candidate_gender_display(candidate).lower()
    score_text = str(candidate_result_score(candidate)).lower()
    level = str(candidate.get("recommend_level") or "").lower()
    status = " ".join(
        filter(
            None,
            (
                str(status_display or candidate.get("followup_status") or ""),
                str(status_detail or ""),
            ),
        )
    ).lower()
    if normalized == name:
        return "exact_name"
    if normalized in name:
        return "partial_name"
    if normalized in gender:
        return "gender"
    if normalized in level:
        return "level"
    if normalized in status:
        return "status"
    match = re.fullmatch(r"(>=|>|=)?\s*(\d{1,3})", normalized)
    if match is None:
        return None
    operator = match.group(1) or ">="
    threshold = int(match.group(2))
    try:
        score = int(score_text) if score_text else 0
    except (TypeError, ValueError):
        score = 0
    if operator == ">=" and score >= threshold:
        return "score"
    if operator == ">" and score > threshold:
        return "score"
    if operator == "=" and score == threshold:
        return "score"
    return None


def _candidate_in_date_range(
    candidate: Mapping[str, Any],
    date_start: str | None,
    date_end: str | None,
) -> bool:
    timestamp = str(
        candidate.get("first_seen_at") or candidate.get("batch_timestamp") or ""
    )
    if len(timestamp) < 8:
        return False
    day = timestamp[:8]
    return not (
        (date_start and day < date_start)
        or (date_end and day > date_end)
    )


def _result_metrics(candidates: Iterable[Candidate]) -> ResultMetrics:
    groups = {"强烈推荐": [], "推荐": [], "待定": []}
    for candidate in candidates:
        level = derive_candidate_decision(candidate).screening_result
        if level in groups:
            groups[level].append(candidate)
    passed = [candidate for values in groups.values() for candidate in values]
    return ResultMetrics(
        strong=len(groups["强烈推荐"]),
        strong_greeted=sum(bool(candidate.get("greet_sent")) for candidate in groups["强烈推荐"]),
        recommended=len(groups["推荐"]),
        recommended_greeted=sum(bool(candidate.get("greet_sent")) for candidate in groups["推荐"]),
        pending=len(groups["待定"]),
        pending_greeted=sum(bool(candidate.get("greet_sent")) for candidate in groups["待定"]),
        greeted=sum(bool(candidate.get("greet_sent")) for candidate in passed),
    )


def _build_result_row(
    candidate: Candidate,
    *,
    evaluating_ids: set[str],
    evaluation_results: Mapping[str, Mapping[str, Any]],
    now: float,
) -> ResultRow | None:
    score = candidate.get("match_score", 0)
    geek_id = str(candidate.get("geek_id") or "")
    keep_low_score = bool(
        geek_id in evaluating_ids
        or geek_id in evaluation_results
        or candidate.get("llm_evaluated")
        or candidate.get("llm_error")
        or candidate.get("resume_file")
    )
    rejected = candidate.get("qualification_status") == "rejected"
    if score < SCORE_THRESHOLD_PASS and not keep_low_score and not rejected:
        return None

    decision = derive_candidate_decision(candidate)
    status = candidate_presenter.format_candidate_status(
        candidate,
        evaluating_ids=evaluating_ids,
        evaluation_results=evaluation_results,
        now=now,
    )
    salary, experience = candidate_presenter.parse_salary_experience(
        candidate.get("summary"),
        candidate.get("structured"),
        record=candidate,
    )
    education, age, job_status, school, company = (
        candidate_presenter.extract_candidate_extra_fields(candidate)
    )
    extra_fields = education, age, job_status, school, company
    # 淘汰记录的 match_score 按存储约定固定为 0；有参考规则分时展示参考分，
    # 让淘汰行的技能/经验匹配度可见；排序和搜索复用同一展示分。
    display_score = candidate_result_score(candidate)
    return ResultRow(
        candidate=candidate,
        values=(
            candidate.get("name", ""),
            candidate_presenter.candidate_gender_display(candidate),
            experience,
            salary,
            candidate.get("skill_match_ratio", ""),
            display_score,
            _ai_adjustment_text(candidate),
            decision.screening_result,
            status.display,
            age,
            education,
            job_status,
            school,
            company,
        ),
        tag=_result_row_tag(candidate, score, rejected),
        status_display=status.display,
        status_detail=status.detail,
        extra_fields=extra_fields,
        expired_evaluation_id=status.expired_evaluation_id,
    )


def _ai_adjustment_text(candidate: Mapping[str, Any]) -> str:
    resume_adjustment = candidate.get("resume_eval_adjustment")
    ai_adjustment = candidate.get("llm_adjustment")
    if resume_adjustment is not None:
        return f"+{resume_adjustment}" if resume_adjustment > 0 else str(resume_adjustment)
    if ai_adjustment is not None and candidate.get("llm_evaluated"):
        return f"+{ai_adjustment}" if ai_adjustment > 0 else str(ai_adjustment)
    return "失败" if candidate.get("llm_error") else "—"


def _result_row_tag(candidate: Mapping[str, Any], score: Any, rejected: bool) -> str:
    if candidate.get("blacklisted"):
        return "blacklisted"
    if rejected:
        return "rejected"
    if score >= SCORE_THRESHOLD_STRONG:
        return "strong_recommend"
    if score >= SCORE_THRESHOLD_RECOMMEND:
        return "recommend"
    return "pending"
