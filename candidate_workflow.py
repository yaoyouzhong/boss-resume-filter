"""Candidate action queue helpers for daily recruiting work."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from constants import SCORE_THRESHOLD_PASS, SCORE_THRESHOLD_RECOMMEND, SCORE_THRESHOLD_STRONG


REVIEW_CATEGORY_ORDER = (
    "学历形式待确认",
    "工作经验待确认",
    "年龄待确认",
    "薪资待确认",
    "工作地点待确认",
    "求职状态待确认",
    "必要条件待确认",
    "AI评估失败",
    "评分待确认",
    "其他待确认",
)

ACTION_TIMING_ORDER = (
    "立即处理",
    "已逾期",
    "今天",
    "待安排",
    "以后",
)

FOLLOWUP_TERMINAL_STATUSES = {"未沟通", "不合适", "已归档"}
FOLLOWUP_SCHEDULED_STATUSES = {"已打招呼", "待约面", "已约面"}


@dataclass(frozen=True)
class CandidateActionItem:
    priority: int
    group: str
    name: str
    job_name: str
    score: int
    reason: str
    action: str
    candidate: dict[str, Any]
    timing_group: str = "待安排"
    due_at: str = ""


@dataclass(frozen=True)
class CandidateDecision:
    """User-facing screening, review, and communication state."""

    screening_result: str
    result_view: str
    review_reasons: tuple[str, ...]
    communication_status: str
    next_action: str

    @property
    def primary_review_reason(self) -> str:
        return self.review_reasons[0] if self.review_reasons else ""


def derive_candidate_decision(candidate: dict[str, Any]) -> CandidateDecision:
    """Derive orthogonal decision states without changing persisted candidate data."""
    score_value = _as_int(candidate.get("match_score"))
    score = score_value or 0
    qualification = str(candidate.get("qualification_status") or "qualified")
    communication = _communication_status(candidate)

    if qualification == "rejected":
        reasons = tuple(_unique_texts(candidate.get("qualification_reasons") or []))
        return CandidateDecision(
            screening_result="淘汰",
            result_view="淘汰记录",
            review_reasons=reasons,
            communication_status=communication,
            next_action="核对淘汰依据；如判断有误，标记为误杀并补充原因。",
        )

    screening_result = _screening_result(score)
    review_reasons: list[str] = []
    if qualification == "manual_review" or candidate.get("manual_review_required"):
        review_reasons.extend(_manual_review_reasons(candidate))
    if candidate.get("llm_error"):
        review_reasons.append("AI 评估失败，需人工判断或重试")
    if score_value is not None and score < SCORE_THRESHOLD_RECOMMEND:
        if score >= SCORE_THRESHOLD_PASS:
            review_reasons.append(f"评分处于待定区间（{score} 分）")
        else:
            review_reasons.append(f"评分低于通过线（{score} 分）")
    review_reasons = _unique_texts(review_reasons)

    if review_reasons:
        next_action = _review_next_action(candidate, review_reasons)
        result_view = "待复核"
    else:
        result_view = "推荐候选人"
        next_action = _recommended_next_action(candidate, communication)

    return CandidateDecision(
        screening_result=screening_result,
        result_view=result_view,
        review_reasons=tuple(review_reasons),
        communication_status=communication,
        next_action=next_action,
    )


def filter_candidates_by_result_view(
    candidates: list[dict[str, Any]], view: str
) -> list[dict[str, Any]]:
    """Filter candidates by their derived decision view."""
    if view not in {"推荐候选人", "待复核", "淘汰记录"}:
        return list(candidates)
    return [
        candidate for candidate in candidates
        if derive_candidate_decision(candidate).result_view == view
    ]


def candidate_review_category(candidate: dict[str, Any]) -> str:
    """Return one stable business category for a pending-review candidate."""
    decision = derive_candidate_decision(candidate)
    if decision.result_view != "待复核":
        return ""

    reason = decision.primary_review_reason
    category_keywords = (
        ("学历形式待确认", ("学历", "统招", "全日制", "非全日制")),
        ("工作经验待确认", ("工作经验", "工作年限", "经验")),
        ("年龄待确认", ("年龄",)),
        ("薪资待确认", ("薪资", "期望薪资")),
        ("工作地点待确认", ("工作地点", "地点", "城市")),
        ("求职状态待确认", ("求职状态", "在职状态", "到岗")),
        ("必要条件待确认", ("必要条件", "技能", "关键词")),
        ("AI评估失败", ("AI 评估失败", "AI评估失败")),
        ("评分待确认", ("评分",)),
    )
    for category, keywords in category_keywords:
        if any(keyword in reason for keyword in keywords):
            return category
    return "其他待确认"


def candidate_greet_skip_reason(candidate: dict[str, Any]) -> str:
    """Return why a candidate cannot enter the manual greeting queue."""
    if not candidate.get("geek_id"):
        return "缺少候选人标识"
    if candidate.get("blacklisted"):
        return "已加入黑名单"
    if candidate.get("greet_sent"):
        return "已打招呼"
    if candidate.get("greet_confirmation_pending"):
        return "发送结果待核实"
    followup = _followup_status(candidate)
    if followup in {"已打招呼", "已回复", "待约面", "已约面", "不合适", "已归档"}:
        return f"跟进状态为{followup}"
    decision = derive_candidate_decision(candidate)
    if decision.result_view == "淘汰记录":
        return "已淘汰"
    if decision.result_view == "待复核":
        return decision.primary_review_reason or "需先完成复核"
    score = _as_int(candidate.get("match_score"))
    if score is None or score < SCORE_THRESHOLD_RECOMMEND:
        return "评分未达到推荐标准"
    return ""


def build_daily_candidate_actions(
    candidates: list[dict[str, Any]],
    limit_per_group: int | None = None,
    today: date | datetime | str | None = None,
) -> list[CandidateActionItem]:
    """Build one highest-priority action for each candidate record.

    Business groups explain what to do. ``timing_group`` independently says
    whether the item is immediate, overdue, due today, unscheduled, or future.
    """
    current_date = _coerce_date(today) or date.today()
    buckets: dict[str, list[CandidateActionItem]] = {
        "发送结果待核实": [],
        "已回复待推进": [],
        "待复核": [],
        "待完成简历评估": [],
        "待打招呼": [],
        "已打招呼待跟进": [],
        "待约面待推进": [],
        "面试后待反馈": [],
    }
    for candidate in candidates:
        if candidate.get("blacklisted"):
            continue
        score = _as_int(candidate.get("match_score")) or 0
        if score < SCORE_THRESHOLD_PASS and not _has_business_state(candidate):
            continue

        if candidate.get("greet_confirmation_pending"):
            buckets["发送结果待核实"].append(_item(
                10, "发送结果待核实", candidate,
                candidate.get("greet_confirmation_reason") or "上次点击后没有明确成功状态",
                "先去 BOSS 沟通列表核实，确认后再继续发送。",
                timing_group="立即处理",
            ))
            continue

        followup = _followup_status(candidate)
        if followup == "已回复":
            buckets["已回复待推进"].append(_item(
                20, "已回复待推进", candidate,
                "候选人已经回复，尚未记录后续处理结果",
                "查看回复，决定继续沟通、推进约面或标记不合适，并更新跟进状态。",
                timing_group="立即处理",
            ))
            continue

        decision = derive_candidate_decision(candidate)
        if decision.result_view == "待复核":
            buckets["待复核"].append(_item(
                30, "待复核", candidate,
                decision.primary_review_reason or "需要人工复核",
                decision.next_action,
            ))
            continue

        if _has_resume_file(candidate) and candidate.get("resume_eval_adjustment") is None:
            buckets["待完成简历评估"].append(_item(
                40, "待完成简历评估", candidate,
                "已导入简历，但还没有基于完整简历重新评分",
                "打开“查看与复核”，完成简历评估并确认是否改变判断。",
            ))
            continue

        if _should_greet(candidate):
            has_context = bool((candidate.get("greet_context") or {}).get("chat_start"))
            if has_context:
                reason = _score_reason(score)
                action = "加入联系清单。"
            else:
                reason = "候选人已通过筛选，尚未联系"
                action = "打开对应岗位的推荐牛人页面并重新扫描，再加入联系清单。"
            buckets["待打招呼"].append(_item(50, "待打招呼", candidate, reason, action))
            continue

        if followup in FOLLOWUP_SCHEDULED_STATUSES:
            if followup == "已约面" and not normalize_followup_at(
                candidate.get("next_followup_at")
            ):
                continue
            timing_group, due_at = classify_followup_timing(candidate, current_date)
            group, priority, reason, action = _scheduled_followup_action(followup, due_at)
            buckets[group].append(_item(
                priority, group, candidate, reason, action,
                timing_group=timing_group,
                due_at=due_at,
            ))

    ordered_groups = [
        "发送结果待核实",
        "已回复待推进",
        "待复核",
        "待完成简历评估",
        "待打招呼",
        "已打招呼待跟进",
        "待约面待推进",
        "面试后待反馈",
    ]
    result: list[CandidateActionItem] = []
    for group in ordered_groups:
        items = sorted(
            buckets[group],
            key=lambda item: (-item.score, item.name, item.job_name),
        )
        result.extend(items if limit_per_group is None else items[:limit_per_group])
    return result


def summarize_daily_candidate_actions(items: list[CandidateActionItem]) -> str:
    """Format action items for plain text export."""
    if not items:
        return "今日待办\n\n暂无需要优先处理的候选人。"
    lines = ["今日待办", ""]
    current_timing = None
    current_group = None
    timing_rank = {name: index for index, name in enumerate(ACTION_TIMING_ORDER)}
    ordered_items = sorted(
        items,
        key=lambda item: (
            timing_rank.get(item.timing_group, len(timing_rank)),
            item.priority,
            item.group,
            -item.score,
        ),
    )
    for item in ordered_items:
        if item.timing_group != current_timing:
            current_timing = item.timing_group
            current_group = None
            lines.extend([f"# {current_timing}", ""])
        if item.group != current_group:
            current_group = item.group
            lines.extend([f"## {current_group}", ""])
        lines.extend([
            f"- {item.name or '未命名候选人'} / {item.job_name or '未知岗位'} / {item.score} 分",
            f"  原因：{item.reason}",
            f"  动作：{item.action}",
            *([f"  到期：{format_followup_due_at(item.due_at)}"] if item.due_at else []),
        ])
    return "\n".join(lines).rstrip()


def _item(
    priority: int,
    group: str,
    candidate: dict[str, Any],
    reason: str,
    action: str,
    *,
    timing_group: str = "待安排",
    due_at: str = "",
) -> CandidateActionItem:
    return CandidateActionItem(
        priority=priority,
        group=group,
        name=str(candidate.get("name") or ""),
        job_name=str(candidate.get("job_name") or ""),
        score=_as_int(candidate.get("match_score")) or 0,
        reason=reason,
        action=action,
        candidate=candidate,
        timing_group=timing_group,
        due_at=due_at,
    )


def classify_followup_timing(
    candidate: dict[str, Any],
    today: date | datetime | str | None = None,
) -> tuple[str, str]:
    """Return the user-facing timing group and normalized due timestamp."""
    current_date = _coerce_date(today) or date.today()
    due_at = normalize_followup_at(candidate.get("next_followup_at"))
    due_date = _coerce_date(due_at)
    if due_date is None:
        return "待安排", ""
    if due_date < current_date:
        return "已逾期", due_at
    if due_date == current_date:
        return "今天", due_at
    return "以后", due_at


def default_next_followup_at(
    status: str,
    timestamp: str | datetime | None = None,
) -> str:
    """Return the deterministic default reminder for a follow-up transition."""
    base = _coerce_datetime(timestamp) or datetime.now()
    days = {
        "已打招呼": 1,
        "已回复": 0,
        "待约面": 1,
    }.get(str(status or "").strip())
    if days is None:
        return ""
    return (base + timedelta(days=days)).strftime("%Y%m%d_%H%M%S")


def normalize_followup_at(value: Any) -> str:
    """Normalize supported date/timestamp strings to the project timestamp format."""
    parsed = _coerce_datetime(value)
    return parsed.strftime("%Y%m%d_%H%M%S") if parsed else ""


def format_followup_due_at(value: Any) -> str:
    """Format a stored reminder for user-facing text."""
    parsed = _coerce_datetime(value)
    return parsed.strftime("%Y-%m-%d") if parsed else "未安排"


def apply_followup_state(
    candidate: dict[str, Any],
    status: str,
    note: str = "",
    *,
    timestamp: str | datetime | None = None,
    next_followup_at: str | datetime | None = None,
) -> str:
    """Apply one deterministic follow-up transition and return its timestamp."""
    updated_at = _coerce_datetime(timestamp) or datetime.now()
    updated_text = updated_at.strftime("%Y%m%d_%H%M%S")
    clean_status = str(status or "").strip()
    candidate["followup_status"] = clean_status
    candidate["followup_note"] = str(note or "").strip()
    candidate["followup_updated_at"] = updated_text

    if clean_status in FOLLOWUP_TERMINAL_STATUSES:
        candidate.pop("next_followup_at", None)
    else:
        if next_followup_at is None:
            due_at = default_next_followup_at(clean_status, updated_at)
        else:
            due_at = normalize_followup_at(next_followup_at)
        if due_at:
            candidate["next_followup_at"] = due_at
        else:
            candidate.pop("next_followup_at", None)
    return updated_text


def _scheduled_followup_action(status: str, due_at: str) -> tuple[str, int, str, str]:
    due_text = format_followup_due_at(due_at)
    if status == "待约面":
        return (
            "待约面待推进",
            55,
            f"候选人等待约面安排；下次处理时间：{due_text}",
            "确认面试时间，或调整下次跟进日期。",
        )
    if status == "已约面":
        return (
            "面试后待反馈",
            58,
            f"候选人已约面；下次处理时间：{due_text}",
            "确认面试结果，补充反馈并决定继续推进或归档。",
        )
    return (
        "已打招呼待跟进",
        60,
        (
            f"已完成初次沟通；下次处理时间：{due_text}"
            if due_at else "已完成初次沟通，但尚未安排下次跟进时间"
        ),
        "查看 BOSS 沟通记录；已回复时更新状态，暂未回复可调整下次跟进日期。",
    )


def _coerce_date(value: date | datetime | str | None) -> date | None:
    parsed = _coerce_datetime(value)
    return parsed.date() if parsed else None


def _coerce_datetime(value: date | datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value or "").strip()
    for fmt in ("%Y%m%d_%H%M%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _should_greet(candidate: dict[str, Any]) -> bool:
    return not candidate_greet_skip_reason(candidate)


def _score_reason(score: int) -> str:
    if score >= SCORE_THRESHOLD_STRONG:
        return "强烈推荐，尚未打招呼"
    if score >= SCORE_THRESHOLD_RECOMMEND:
        return "推荐，尚未打招呼"
    return "通过筛选，尚未打招呼"


def _screening_result(score: int) -> str:
    if score >= SCORE_THRESHOLD_STRONG:
        return "强烈推荐"
    if score >= SCORE_THRESHOLD_RECOMMEND:
        return "推荐"
    if score >= SCORE_THRESHOLD_PASS:
        return "待定"
    return "未通过"


def _communication_status(candidate: dict[str, Any]) -> str:
    if candidate.get("greet_confirmation_pending"):
        return "发送待核实"
    followup = str(candidate.get("followup_status") or "").strip()
    if followup:
        return followup
    return "已打招呼" if candidate.get("greet_sent") else "未沟通"


def _manual_review_reasons(candidate: dict[str, Any]) -> list[str]:
    reasons = _unique_texts(candidate.get("qualification_reasons") or [])
    if reasons:
        return reasons
    blocked_reason = str(candidate.get("auto_greet_blocked_reason") or "").strip()
    if blocked_reason:
        return [blocked_reason]
    risk_flags = _unique_texts(candidate.get("risk_flags") or [])
    if risk_flags:
        return risk_flags
    return ["硬性条件需要人工确认"]


def _review_next_action(candidate: dict[str, Any], reasons: list[str]) -> str:
    if candidate.get("llm_error"):
        return "先查看现有证据；必要时重试 AI 评估，再确认是否通过。"
    if candidate.get("manual_review_required") or candidate.get("qualification_status") == "manual_review":
        return "核对硬性条件证据；确认无误后通过，否则标记反馈。"
    return "查看匹配证据；可导入完整简历复评，或记录人工反馈。"


def _recommended_next_action(candidate: dict[str, Any], communication: str) -> str:
    if communication == "发送待核实":
        return "先在 BOSS 沟通列表核实发送结果，避免重复联系。"
    if candidate.get("greet_sent"):
        if communication == "已回复":
            return "查看回复并推进约面，随后更新跟进状态。"
        return "查看 BOSS 会话；有回复后更新跟进状态。"
    return "加入联系清单，统一确认后发送。"


def _unique_texts(values: Any) -> list[str]:
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        values = [values]
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _followup_status(candidate: dict[str, Any]) -> str:
    return str(candidate.get("followup_status") or ("已打招呼" if candidate.get("greet_sent") else "未沟通"))


def _has_resume_file(candidate: dict[str, Any]) -> bool:
    return bool(candidate.get("resume_file") or candidate.get("resume_imported_at"))


def _has_business_state(candidate: dict[str, Any]) -> bool:
    return bool(
        candidate.get("feedback_status")
        or candidate.get("followup_status")
        or candidate.get("greet_sent")
        or candidate.get("greet_confirmation_pending")
        or candidate.get("llm_evaluated")
        or candidate.get("resume_eval_adjustment") is not None
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
