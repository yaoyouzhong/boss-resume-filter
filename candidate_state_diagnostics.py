"""Deterministic consistency checks for persisted candidate states."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from candidate_workflow import normalize_followup_at
from constants import SCORE_THRESHOLD_PASS


FOLLOWUP_STATUS_OPTIONS = {"未沟通", "已打招呼", "已回复", "待约面", "已约面", "不合适", "已归档"}
FEEDBACK_STATUS_OPTIONS = {"合适", "误推", "误杀", "放弃"}
QUALIFICATION_STATUS_OPTIONS = {"qualified", "rejected", "manual_review"}
ACTIVE_FOLLOWUP_STATUSES = {"已打招呼", "已回复", "待约面", "已约面"}


@dataclass(frozen=True)
class CandidateStateIssue:
    severity: str
    candidate_key: str
    name: str
    job_name: str
    title: str
    detail: str
    suggestion: str


def diagnose_candidate_states(candidates: list[dict[str, Any]]) -> list[CandidateStateIssue]:
    """Return deterministic state consistency issues for candidate records."""
    issues: list[CandidateStateIssue] = []
    issues.extend(_diagnose_individual_candidates(candidates))
    issues.extend(_diagnose_duplicate_records(candidates))
    issues.extend(_diagnose_blacklist_consistency(candidates))
    return issues


def summarize_candidate_state_diagnostics(
    candidates: list[dict[str, Any]],
    issues: list[CandidateStateIssue] | None = None,
) -> str:
    """Format candidate-state diagnostics for plain-text dialogs or exports."""
    if issues is None:
        issues = diagnose_candidate_states(candidates)
    counts = {
        "error": sum(1 for item in issues if item.severity == "error"),
        "warning": sum(1 for item in issues if item.severity == "warning"),
        "info": sum(1 for item in issues if item.severity == "info"),
    }
    lines = [
        "候选人状态体检",
        f"候选人：{len(candidates)} 人",
        f"发现问题：{len(issues)} 项（严重 {counts['error']}，提醒 {counts['warning']}，建议 {counts['info']}）",
    ]
    if not issues:
        lines.extend(["", "未发现明显状态冲突。"])
        return "\n".join(lines)

    lines.append("")
    for idx, issue in enumerate(issues, 1):
        label = {"error": "严重", "warning": "提醒", "info": "建议"}.get(issue.severity, "提醒")
        person = issue.name or issue.candidate_key or "未知候选人"
        job = issue.job_name or "未知岗位"
        lines.extend([
            f"{idx}. [{label}] {person} / {job} / {issue.title}",
            f"   问题：{issue.detail}",
            f"   建议：{issue.suggestion}",
            "",
        ])
    return "\n".join(lines).rstrip()


def _diagnose_individual_candidates(candidates: Iterable[dict[str, Any]]) -> list[CandidateStateIssue]:
    issues: list[CandidateStateIssue] = []
    for candidate in candidates:
        followup_status = _clean_text(candidate.get("followup_status"))
        feedback_status = _clean_text(candidate.get("feedback_status"))
        qualification_status = _clean_text(candidate.get("qualification_status")) or "qualified"
        match_score = _as_int(candidate.get("match_score")) or 0
        next_followup_raw = _clean_text(candidate.get("next_followup_at"))
        next_followup_at = normalize_followup_at(next_followup_raw)

        if followup_status and followup_status not in FOLLOWUP_STATUS_OPTIONS:
            issues.append(_issue(
                candidate, "warning", "未知跟进状态",
                f"当前跟进状态为“{followup_status}”，不在系统支持范围内。",
                "打开“更新跟进”，重新选择一个列表里已有的状态。",
            ))
        if feedback_status and feedback_status not in FEEDBACK_STATUS_OPTIONS:
            issues.append(_issue(
                candidate, "warning", "未知人工反馈",
                f"当前人工反馈为“{feedback_status}”，不在系统支持范围内。",
                "打开“标记反馈”，重新选择一个列表里已有的反馈。",
            ))
        if qualification_status not in QUALIFICATION_STATUS_OPTIONS:
            issues.append(_issue(
                candidate, "warning", "未知资格审查状态",
                f"当前资格审查状态为“{qualification_status}”。",
                "重新做 AI 评估，或人工确认这个人到底是通过、淘汰还是待确认。",
            ))

        if next_followup_raw and not next_followup_at:
            issues.append(_issue(
                candidate, "warning", "下次跟进日期无效",
                f"当前值为“{next_followup_raw}”，系统无法识别。",
                "打开“更新跟进”，重新选择今天、明天或填写 YYYY-MM-DD。",
            ))
        if next_followup_at and followup_status in {"未沟通", "不合适", "已归档"}:
            issues.append(_issue(
                candidate, "warning", "结束状态仍有跟进提醒",
                f"跟进状态为“{followup_status}”，但仍安排了下次跟进。",
                "确认状态；如果已经结束，将下次跟进日期设为不设置。",
            ))
        if followup_status == "待约面" and not next_followup_at:
            issues.append(_issue(
                candidate, "warning", "待约面未安排时间",
                "候选人已进入待约面，但没有下次跟进日期。",
                "打开“更新跟进”，安排下一次确认面试时间的日期。",
            ))
        elif followup_status == "已打招呼" and not next_followup_at:
            issues.append(_issue(
                candidate, "info", "跟进时间待安排",
                f"候选人状态为“{followup_status}”，尚未安排下次处理日期。",
                "可在“今日待办”的待安排分组中设置日期；旧记录无需立即处理。",
            ))

        if candidate.get("greet_sent") is True:
            if followup_status in ("", "未沟通"):
                issues.append(_issue(
                    candidate, "error", "已打招呼但跟进状态未更新",
                    "候选人已标记为已打招呼，但跟进状态仍是未沟通或空。",
                    "将跟进状态更新为“已打招呼”，并保留发送时间。",
                ))
            if not candidate.get("greet_sent_at") or not candidate.get("greet_method"):
                issues.append(_issue(
                    candidate, "warning", "缺少打招呼审计信息",
                    "候选人已打招呼，但缺少发送时间或发送方式。",
                    "如果只是旧数据，可以先忽略；以后新发送成功时会自动补齐。",
                ))

        if candidate.get("greet_confirmation_pending") and candidate.get("greet_sent") is True:
            issues.append(_issue(
                candidate, "error", "已发送与待核实并存",
                "同一候选人同时标记为已发送成功和发送结果待核实。",
                "核实 BOSS 沟通列表后，只保留一个确定状态。",
            ))

        if candidate.get("manual_review_required") and qualification_status != "manual_review":
            issues.append(_issue(
                candidate, "warning", "需要人工确认",
                "系统标记这个人需要人工确认，但候选人结论没有显示为待人工确认。",
                "如果已经看过并认可，右键点“确认通过”；如果还没确认，先不要自动打招呼。",
            ))

        if qualification_status == "rejected":
            if candidate.get("greet_sent") or followup_status in ACTIVE_FOLLOWUP_STATUSES:
                issues.append(_issue(
                    candidate, "error", "淘汰候选人仍处于沟通状态",
                    "候选人资格审查已淘汰，但仍显示已打招呼或后续跟进状态。",
                    "先核实 BOSS 沟通情况；如果确实淘汰，把跟进状态改为“不合适”或“已归档”。",
                ))
            if candidate.get("greet_context"):
                issues.append(_issue(
                    candidate, "info", "淘汰候选人仍保留打招呼上下文",
                    "候选人已淘汰，但仍保存可用于发送的上下文信息。",
                    "通常不用处理；只要不对这个人打招呼即可。",
                ))

        if match_score < SCORE_THRESHOLD_PASS and not _has_low_score_retention_reason(candidate):
            issues.append(_issue(
                candidate, "warning", "低分候选人缺少保留理由",
                f"匹配分 {match_score} 低于通过线，但没有反馈、AI、简历评估或黑名单等保留理由。",
                "如果这个人不需要继续看，后续可以清理；如果要保留，请补充反馈或跟进状态。",
            ))

        if candidate.get("resume_eval_adjustment") is not None:
            missing = [
                label for label, value in (
                    ("评估理由", candidate.get("resume_eval_reason")),
                    ("评估时间", candidate.get("resume_eval_at")),
                )
                if not value
            ]
            if missing:
                issues.append(_issue(
                    candidate, "warning", "简历评估信息不完整",
                    "缺少" + "、".join(missing) + "。",
                    "重新导入并评估简历，或撤销这次不完整的简历评估。",
                ))

        context = candidate.get("greet_context")
        if context and not ((context or {}).get("chat_start")):
            issues.append(_issue(
                candidate, "warning", "打招呼上下文不完整",
                "候选人保存了打招呼上下文，但缺少直发所需信息。",
                "重新扫描这个岗位；在补到上下文前，打招呼可能需要先打开对应推荐页。",
            ))

        if candidate.get("blacklisted") and followup_status in ACTIVE_FOLLOWUP_STATUSES:
            issues.append(_issue(
                candidate, "warning", "已屏蔽候选人仍是活跃跟进",
                f"候选人已屏蔽，但跟进状态仍为“{followup_status}”。",
                "如果已经决定屏蔽，把跟进状态改为“不合适”或“已归档”。",
            ))

    return issues


def _diagnose_duplicate_records(candidates: Iterable[dict[str, Any]]) -> list[CandidateStateIssue]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        geek_id = _clean_text(candidate.get("geek_id"))
        job_name = _clean_text(candidate.get("job_name"))
        if geek_id and job_name:
            groups[(geek_id, job_name)].append(candidate)

    issues: list[CandidateStateIssue] = []
    for (_geek_id, _job_name), records in groups.items():
        if len(records) <= 1:
            continue
        scores = {str(item.get("match_score", "")) for item in records}
        followups = {_clean_text(item.get("followup_status")) for item in records if _clean_text(item.get("followup_status"))}
        detail = f"同一候选人在同一岗位下存在 {len(records)} 条记录。"
        if len(scores) > 1 or len(followups) > 1:
            detail += " 分数或跟进状态不完全一致。"
        issues.append(_issue(
            records[0], "error", "重复候选人记录",
            detail,
            "先不要批量操作这个人；建议刷新保存一次，仍重复再人工保留最新的一条。",
        ))
    return issues


def _diagnose_blacklist_consistency(candidates: Iterable[dict[str, Any]]) -> list[CandidateStateIssue]:
    by_geek: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        geek_id = _clean_text(candidate.get("geek_id"))
        if geek_id:
            by_geek[geek_id].append(candidate)

    issues: list[CandidateStateIssue] = []
    for _geek_id, records in by_geek.items():
        states = {bool(item.get("blacklisted")) for item in records}
        if len(states) <= 1:
            continue
        names = "、".join(dict.fromkeys(_clean_text(item.get("job_name")) or "未知岗位" for item in records))
        issues.append(_issue(
            records[0], "warning", "跨岗位黑名单状态不一致",
            f"同一候选人在不同岗位下的屏蔽状态不一致，涉及岗位：{names}。",
            "确认这个人是否应该被屏蔽；如果应该屏蔽，就在所有岗位里保持一致。",
        ))
    return issues


def _has_low_score_retention_reason(candidate: dict[str, Any]) -> bool:
    return bool(
        candidate.get("feedback_status")
        or candidate.get("blacklisted")
        or candidate.get("llm_evaluated")
        or candidate.get("resume_eval_adjustment") is not None
    )


def _issue(
    candidate: dict[str, Any],
    severity: str,
    title: str,
    detail: str,
    suggestion: str,
) -> CandidateStateIssue:
    return CandidateStateIssue(
        severity=severity,
        candidate_key=_candidate_key(candidate),
        name=_clean_text(candidate.get("name")),
        job_name=_clean_text(candidate.get("job_name")),
        title=title,
        detail=detail,
        suggestion=suggestion,
    )


def _candidate_key(candidate: dict[str, Any]) -> str:
    geek_id = _clean_text(candidate.get("geek_id"))
    job_name = _clean_text(candidate.get("job_name"))
    if geek_id and job_name:
        return f"{geek_id}:{job_name}"
    return geek_id or _clean_text(candidate.get("name")) or "unknown"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
