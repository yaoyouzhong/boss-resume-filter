"""Pure table presentation helpers for candidate-state diagnostics."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class StateIssueLike(Protocol):
    title: str
    detail: str


def clip_table_text(text: object, limit: int) -> str:
    """Normalize and bound text for compact diagnostic table cells."""
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)] + "…"


def format_state_issue_key_info(
    issue: StateIssueLike,
    candidate: Mapping[str, Any] | None,
) -> str:
    """Return the candidate-specific fact that distinguishes one issue row."""
    candidate = candidate or {}
    title = str(issue.title or "").strip()
    followup_status = str(candidate.get("followup_status") or "未设置")
    qualification_labels = {
        "qualified": "通过",
        "rejected": "淘汰",
        "manual_review": "待人工确认",
    }
    if title == "打招呼记录不完整":
        missing = []
        if not candidate.get("greet_sent_at"):
            missing.append("发送时间")
        if not candidate.get("greet_method"):
            missing.append("发送方式")
        return f"缺少：{'、'.join(missing)}" if missing else "发送记录待核对"
    if title == "低分候选人缺少保留理由":
        return f"匹配分：{candidate.get('match_score', 0)}"
    if title in {"待约面未安排时间", "跟进时间待安排"}:
        return "未设置下次跟进日期"
    if title == "下次跟进日期无效":
        return f"当前值：{candidate.get('next_followup_at') or '空'}"
    if title in {
        "结束状态仍有跟进提醒",
        "已打招呼但跟进状态未更新",
        "已屏蔽候选人仍是活跃跟进",
    }:
        return f"跟进状态：{followup_status}"
    if title == "需要人工确认":
        reason = str(candidate.get("auto_greet_blocked_reason") or "").strip()
        if not reason:
            for field in ("qualification_reasons", "risk_flags"):
                values = candidate.get(field) or []
                if isinstance(values, str):
                    values = [values]
                reason = next(
                    (str(value).strip() for value in values if str(value).strip()),
                    "",
                )
                if reason:
                    break
        return clip_table_text(
            f"待确认：{reason or '尚未形成明确资格结论'}",
            38,
        )
    if title in {"未知资格审查状态", "淘汰候选人仍处于沟通状态"}:
        qualification = str(candidate.get("qualification_status") or "未设置")
        return f"资格结论：{qualification_labels.get(qualification, qualification)}"
    if title == "未知人工反馈":
        return f"人工反馈：{candidate.get('feedback_status') or '未设置'}"
    if title == "未知跟进状态":
        return f"跟进状态：{followup_status}"
    return clip_table_text(issue.detail, 38)
