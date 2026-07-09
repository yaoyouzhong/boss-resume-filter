"""Candidate action queue helpers for daily recruiting work."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from constants import SCORE_THRESHOLD_PASS, SCORE_THRESHOLD_RECOMMEND, SCORE_THRESHOLD_STRONG


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


def build_daily_candidate_actions(
    candidates: list[dict[str, Any]],
    limit_per_group: int = 10,
) -> list[CandidateActionItem]:
    """Build a deterministic daily action list from candidate records."""
    buckets: dict[str, list[CandidateActionItem]] = {
        "待人工确认": [],
        "高分未打招呼": [],
        "缺少打招呼上下文": [],
        "已打招呼未回复": [],
        "已回复待约面": [],
        "有简历未二次评估": [],
        "发送结果待确认": [],
    }
    for candidate in candidates:
        if candidate.get("blacklisted"):
            continue
        score = _as_int(candidate.get("match_score")) or 0
        if score < SCORE_THRESHOLD_PASS and not _has_business_state(candidate):
            continue

        if candidate.get("greet_confirmation_pending"):
            buckets["发送结果待确认"].append(_item(
                10, "发送结果待确认", candidate,
                candidate.get("greet_confirmation_reason") or "上次点击后没有明确成功状态",
                "先去 BOSS 沟通列表核实，确认后再继续发送。",
            ))
        if candidate.get("manual_review_required") or candidate.get("qualification_status") == "manual_review":
            buckets["待人工确认"].append(_item(
                20, "待人工确认", candidate,
                candidate.get("auto_greet_blocked_reason") or "规则或 AI 认为需要人工看一眼",
                "查看详情；认可后右键点“确认通过”，否则标记反馈或归档。",
            ))
        if _has_resume_file(candidate) and candidate.get("resume_eval_adjustment") is None:
            buckets["有简历未二次评估"].append(_item(
                30, "有简历未二次评估", candidate,
                "已导入简历，但还没有基于完整简历重新评分",
                "右键做简历评估，确认完整简历是否改变判断。",
            ))
        if _should_greet(candidate):
            group = "高分未打招呼" if score >= SCORE_THRESHOLD_RECOMMEND else "缺少打招呼上下文"
            if group == "高分未打招呼":
                reason = _score_reason(score)
                action = "加入打招呼队列；有上下文的候选人可直接发送。"
            else:
                reason = "通过筛选但缺少可直发上下文"
                action = "重新扫描对应岗位，或先打开推荐牛人页面再发送。"
            buckets[group].append(_item(40 if group == "高分未打招呼" else 50, group, candidate, reason, action))
        followup = _followup_status(candidate)
        if candidate.get("greet_sent") and followup == "已打招呼":
            buckets["已打招呼未回复"].append(_item(
                60, "已打招呼未回复", candidate,
                "已完成初次沟通，本地还没有后续回复状态",
                "查看 BOSS 会话；已回复就更新跟进状态。",
            ))
        if followup == "已回复":
            buckets["已回复待约面"].append(_item(
                70, "已回复待约面", candidate,
                "候选人已回复，但还没有进入约面状态",
                "推进约面；约好后更新为“待约面”或“已约面”。",
            ))

    ordered_groups = [
        "发送结果待确认",
        "待人工确认",
        "高分未打招呼",
        "缺少打招呼上下文",
        "已回复待约面",
        "有简历未二次评估",
        "已打招呼未回复",
    ]
    result: list[CandidateActionItem] = []
    for group in ordered_groups:
        items = sorted(
            buckets[group],
            key=lambda item: (-item.score, item.name, item.job_name),
        )
        result.extend(items[:limit_per_group])
    return result


def summarize_daily_candidate_actions(items: list[CandidateActionItem]) -> str:
    """Format action items for plain text export."""
    if not items:
        return "今日候选人待办\n\n暂无需要优先处理的候选人。"
    lines = ["今日候选人待办", ""]
    current_group = None
    for item in items:
        if item.group != current_group:
            current_group = item.group
            lines.extend([f"## {current_group}", ""])
        lines.extend([
            f"- {item.name or '未命名候选人'} / {item.job_name or '未知岗位'} / {item.score} 分",
            f"  原因：{item.reason}",
            f"  动作：{item.action}",
        ])
    return "\n".join(lines).rstrip()


def _item(priority: int, group: str, candidate: dict[str, Any], reason: str, action: str) -> CandidateActionItem:
    return CandidateActionItem(
        priority=priority,
        group=group,
        name=str(candidate.get("name") or ""),
        job_name=str(candidate.get("job_name") or ""),
        score=_as_int(candidate.get("match_score")) or 0,
        reason=reason,
        action=action,
        candidate=candidate,
    )


def _should_greet(candidate: dict[str, Any]) -> bool:
    score = _as_int(candidate.get("match_score")) or 0
    if candidate.get("greet_sent") or candidate.get("greet_confirmation_pending"):
        return False
    if candidate.get("manual_review_required") or candidate.get("qualification_status") == "manual_review":
        return False
    if candidate.get("qualification_status") == "rejected":
        return False
    return score >= SCORE_THRESHOLD_PASS


def _score_reason(score: int) -> str:
    if score >= SCORE_THRESHOLD_STRONG:
        return "强烈推荐，尚未打招呼"
    if score >= SCORE_THRESHOLD_RECOMMEND:
        return "推荐，尚未打招呼"
    return "通过筛选，尚未打招呼"


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
