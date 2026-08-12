"""Pure aggregation and presentation models for recruitment statistics."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from constants import (
    SCORE_THRESHOLD_PASS,
    SCORE_THRESHOLD_RECOMMEND,
    SCORE_THRESHOLD_STRONG,
)


FEEDBACK_STATUSES = ("合适", "误推", "误杀", "放弃")
JOB_REVIEW_FEEDBACK_MINIMUM = 5
REASON_RECOMMENDATIONS: dict[str, dict[str, str]] = {
    "技能不匹配": {
        "detail": "复核核心与优先技能是否过泛、缺失或权重偏高。",
        "config_target": "skills",
        "action_label": "定位技能评分",
    },
    "行业经验不符": {
        "detail": "核对行业经验应作为优先技能还是必要条件；只有硬性要求才直接淘汰。",
        "config_target": "skills",
        "action_label": "定位技能评分",
    },
    "年限判断偏差": {
        "detail": "核对最低经验是否符合岗位实际要求，避免把偏好写成硬门槛。",
        "config_target": "minimum_experience",
        "action_label": "定位经验年限",
    },
    "学历/学校不符": {
        "detail": "核对最低学历；如学校层次属于硬性要求，应在必要条件中明确表达。",
        "config_target": "education",
        "action_label": "定位学历要求",
    },
    "薪资不合适": {
        "detail": "核对岗位薪资上下限是否与实际招聘预算一致。",
        "config_target": "salary",
        "action_label": "定位薪资范围",
    },
    "地点不合适": {
        "detail": "核对工作地点及多地点写法，避免遗漏可接受地点。",
        "config_target": "work_location",
        "action_label": "定位工作地点",
    },
    "求职状态不合适": {
        "detail": "当前没有独立的求职状态配置；如属于岗位硬性要求，可在必要条件中明确。",
        "config_target": "required_conditions",
        "action_label": "定位必要条件",
    },
    "AI 高估": {
        "detail": "核对招聘需求和硬性条件是否表达完整，再检查 AI 评估证据。",
        "config_target": "requirement",
        "action_label": "定位招聘需求",
    },
    "AI 低估": {
        "detail": "先检查简历证据是否充分，再核对招聘需求是否遗漏可接受条件。",
        "config_target": "requirement",
        "action_label": "定位招聘需求",
    },
    "规则过宽": {
        "detail": "补充确属硬性的约束，并检查核心技能关键词是否过泛。",
        "config_target": "required_conditions",
        "action_label": "定位必要条件",
    },
    "规则过窄": {
        "detail": "放宽不必要的硬性条件，并将长句拆成可核对的短条件。",
        "config_target": "required_conditions",
        "action_label": "定位必要条件",
    },
    "简历信息不足": {
        "detail": "优先补充完整简历或人工核实，不要仅凭信息不足放宽岗位规则。",
        "config_target": "",
        "action_label": "",
    },
    "其他": {
        "detail": "先查看反馈备注并细化原因，证据不足时不建议调整岗位规则。",
        "config_target": "",
        "action_label": "",
    },
}
REASON_SUGGESTIONS = {
    reason: recommendation["detail"]
    for reason, recommendation in REASON_RECOMMENDATIONS.items()
}


def stats_time_cutoff(
    time_range: str,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return the inclusive local-time cutoff for one statistics range."""
    current = now or datetime.now()
    if time_range == "今天":
        return current.replace(hour=0, minute=0, second=0, microsecond=0)
    if time_range == "本周":
        return (current - timedelta(days=current.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    if time_range == "本月":
        return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def build_stats_dashboard(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate summary cards and ordered table rows from filtered candidates."""
    qualified = [
        candidate
        for candidate in candidates
        if candidate.get("match_score", 0) >= SCORE_THRESHOLD_PASS
    ]
    summary = {
        "total": len(qualified),
        "strong": sum(
            1
            for candidate in qualified
            if candidate.get("match_score", 0) >= SCORE_THRESHOLD_STRONG
        ),
        "recommended": sum(
            1
            for candidate in qualified
            if SCORE_THRESHOLD_RECOMMEND
            <= candidate.get("match_score", 0)
            < SCORE_THRESHOLD_STRONG
        ),
        "greeted": sum(
            1 for candidate in qualified if candidate.get("greet_sent", False)
        ),
    }
    job_stats = defaultdict(
        lambda: {
            "total": 0,
            "strong": 0,
            "recommended": 0,
            "pending": 0,
            "greeted": 0,
            "feedback_count": 0,
            "suitable": 0,
            "false_positive": 0,
            "contacted": 0,
            "replied": 0,
            "interviewed": 0,
            "scores": [],
        }
    )
    valid_feedback_statuses = set(FEEDBACK_STATUSES)
    contacted_statuses = {
        "已打招呼",
        "已回复",
        "待约面",
        "已约面",
        "不合适",
        "已归档",
    }
    replied_statuses = {"已回复", "待约面", "已约面"}

    for candidate in candidates:
        job_name = candidate.get("job_name", "未知")
        score = candidate.get("match_score", 0)
        if score < SCORE_THRESHOLD_PASS:
            continue
        stats = job_stats[job_name]
        stats["total"] += 1
        if score >= SCORE_THRESHOLD_STRONG:
            stats["strong"] += 1
        elif score >= SCORE_THRESHOLD_RECOMMEND:
            stats["recommended"] += 1
        else:
            stats["pending"] += 1
        if candidate.get("greet_sent", False):
            stats["greeted"] += 1

        feedback_status = candidate.get("feedback_status")
        if feedback_status in valid_feedback_statuses:
            stats["feedback_count"] += 1
            if feedback_status == "合适":
                stats["suitable"] += 1
            elif feedback_status == "误推":
                stats["false_positive"] += 1

        followup_status = candidate.get("followup_status") or (
            "已打招呼" if candidate.get("greet_sent", False) else "未沟通"
        )
        if candidate.get("greet_sent", False) or followup_status in contacted_statuses:
            stats["contacted"] += 1
        if followup_status in replied_statuses:
            stats["replied"] += 1
        if followup_status == "已约面":
            stats["interviewed"] += 1
        stats["scores"].append(score)

    rows = []
    ordered = sorted(job_stats.items(), key=lambda item: item[1]["total"], reverse=True)
    for job_name, stats in ordered:
        total = stats["total"]
        greeted = stats["greeted"]
        feedback_count = stats["feedback_count"]
        contacted = stats["contacted"]
        replied = stats["replied"]
        interviewed = stats["interviewed"]
        rows.append(
            (
                job_name,
                (
                    f"{total} (强{stats['strong']}/推{stats['recommended']}"
                    f"/待{stats['pending']})"
                ),
                f"{greeted} ({greeted * 100 // total}%)" if total > 0 else str(greeted),
                feedback_count,
                (
                    f"{stats['suitable'] * 100 // feedback_count}%"
                    if feedback_count > 0
                    else "—"
                ),
                (
                    f"{stats['false_positive'] * 100 // feedback_count}%"
                    if feedback_count > 0
                    else "—"
                ),
                (
                    f"{replied} ({replied * 100 // contacted}%)"
                    if contacted > 0
                    else str(replied)
                ),
                (
                    f"{interviewed} ({interviewed * 100 // replied}%)"
                    if replied > 0
                    else str(interviewed)
                ),
                (
                    f"{sum(stats['scores']) / len(stats['scores']):.1f}"
                    if stats["scores"]
                    else "—"
                ),
            )
        )
    return {"summary": summary, "rows": rows}


def feedback_reasons(candidate: Mapping[str, Any]) -> list[str]:
    """Normalize one candidate's structured feedback reasons."""
    reasons = candidate.get("feedback_reasons") or []
    if isinstance(reasons, str):
        reasons = [
            reason.strip()
            for reason in re.split(r"[,，、/;；]", reasons)
            if reason.strip()
        ]
    if not isinstance(reasons, list):
        return []
    return [str(reason).strip() for reason in reasons if str(reason).strip()]


def format_job_review_suggestion(suggestion: object) -> tuple[str, str]:
    """Split one suggestion into a short heading and supporting detail."""
    if isinstance(suggestion, Mapping):
        return (
            str(suggestion.get("title") or "").strip(),
            str(suggestion.get("detail") or "").strip(),
        )
    text = str(suggestion or "").lstrip("- ").strip()
    for delimiter in ("：", ":", "；", ";"):
        if delimiter not in text:
            continue
        title, detail = text.split(delimiter, 1)
        if title.strip() and detail.strip():
            return title.strip().rstrip("。"), detail.strip()
    return text.rstrip("。"), ""


def _job_review_recommendation(
    title: str,
    detail: str,
    *,
    text: str,
    evidence: str = "",
    config_target: str = "",
    action_label: str = "",
) -> dict[str, str]:
    """Build one plain-data recommendation for text and Tk consumers."""
    return {
        "title": title,
        "detail": detail,
        "text": text,
        "evidence": evidence,
        "config_target": config_target,
        "action_label": action_label,
    }


def build_job_review_recommendations(
    status_counts: Counter[str],
    reason_counts: Counter[str],
    feedback_count: int,
) -> list[dict[str, str]]:
    """Build evidence-backed suggestions with optional job-config targets."""
    if feedback_count == 0:
        return [
            _job_review_recommendation(
                "先积累反馈样本",
                "没有结构化反馈时不建议调整岗位规则。",
                text="- 先积累反馈样本；没有结构化反馈时不建议调整岗位规则。",
            )
        ]
    if feedback_count < JOB_REVIEW_FEEDBACK_MINIMUM:
        observed = "、".join(
            f"{reason} {count} 条" for reason, count in reason_counts.most_common(5)
        ) or "暂无结构化原因"
        return [
            _job_review_recommendation(
                "样本不足",
                (
                    f"当前只有 {feedback_count} 条反馈，样本不足 "
                    f"{JOB_REVIEW_FEEDBACK_MINIMUM} 条，不建议据此修改岗位规则。"
                ),
                text=(
                    f"- 当前只有 {feedback_count} 条反馈，样本不足 "
                    f"{JOB_REVIEW_FEEDBACK_MINIMUM} 条，不建议据此修改岗位规则。"
                ),
            ),
            _job_review_recommendation(
                "已记录原因",
                f"{observed}。",
                text=f"- 已记录原因：{observed}。",
            ),
        ]

    recommendations: list[dict[str, str]] = []
    false_positive = status_counts.get("误推", 0)
    false_negative = status_counts.get("误杀", 0)
    if false_positive * 2 >= feedback_count and false_positive > 0:
        recommendations.append(
            _job_review_recommendation(
                "误推占比较高",
                "优先检查核心技能是否过泛、必要条件是否缺失。",
                text="- 误推占比较高：优先检查核心技能是否过泛、必要条件是否缺失。",
                evidence=f"{feedback_count} 条反馈中 {false_positive} 条为误推",
                config_target="skills",
                action_label="定位技能评分",
            )
        )
    if false_negative * 2 >= feedback_count and false_negative > 0:
        recommendations.append(
            _job_review_recommendation(
                "误杀占比较高",
                "优先检查必要条件是否过严、简单关键词是否写成长句。",
                text="- 误杀占比较高：优先检查必要条件是否过严、简单关键词是否写成长句。",
                evidence=f"{feedback_count} 条反馈中 {false_negative} 条为误杀",
                config_target="required_conditions",
                action_label="定位必要条件",
            )
        )
    for reason, count in reason_counts.most_common():
        template = REASON_RECOMMENDATIONS.get(reason)
        if template is None:
            template = {
                "detail": "结合反馈备注人工核对；没有明确证据时不建议调整岗位规则。",
                "config_target": "",
                "action_label": "",
            }
        detail = template["detail"]
        recommendations.append(
            _job_review_recommendation(
                reason,
                detail,
                text=f"- {reason}：{count}/{feedback_count} 条；{detail}",
                evidence=f"{feedback_count} 条反馈中 {count} 条标记为“{reason}”",
                config_target=template["config_target"],
                action_label=template["action_label"],
            )
        )
    return recommendations or [
        _job_review_recommendation(
            "暂无明确调整方向",
            "继续积累结构化反馈后再复盘。",
            text="- 暂无明确规则调整方向；继续积累结构化反馈后再复盘。",
        )
    ]


def build_job_review_suggestions(
    status_counts: Counter[str],
    reason_counts: Counter[str],
    feedback_count: int,
) -> list[str]:
    """Build compatibility text suggestions from structured recommendations."""
    return [
        recommendation["text"]
        for recommendation in build_job_review_recommendations(
            status_counts,
            reason_counts,
            feedback_count,
        )
    ]


def build_job_review_model(
    job_name: str,
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the shared job-review model without changing candidate data."""
    qualified = [
        candidate
        for candidate in candidates
        if candidate.get("match_score", 0) >= SCORE_THRESHOLD_PASS
    ]
    feedback_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("feedback_status") in FEEDBACK_STATUSES
    ]
    status_counts = Counter(
        candidate.get("feedback_status") for candidate in feedback_candidates
    )
    reason_counts: Counter[str] = Counter()
    false_positive_reasons: Counter[str] = Counter()
    false_negative_reasons: Counter[str] = Counter()
    ai_bias_counts: Counter[str] = Counter()
    for candidate in feedback_candidates:
        reasons = feedback_reasons(candidate)
        reason_counts.update(reasons)
        if candidate.get("feedback_status") == "误推":
            false_positive_reasons.update(reasons)
        elif candidate.get("feedback_status") == "误杀":
            false_negative_reasons.update(reasons)
        for reason in reasons:
            if reason in {"AI 高估", "AI 低估"}:
                ai_bias_counts[reason] += 1

    greeted = sum(1 for candidate in candidates if candidate.get("greet_sent"))
    replied_statuses = {"已回复", "待约面", "已约面"}
    replied = sum(
        1
        for candidate in candidates
        if candidate.get("followup_status") in replied_statuses
    )
    interviewed = sum(
        1 for candidate in candidates if candidate.get("followup_status") == "已约面"
    )
    avg_score = (
        sum(candidate.get("match_score", 0) for candidate in qualified) / len(qualified)
        if qualified
        else 0
    )
    recommendations = build_job_review_recommendations(
        status_counts,
        reason_counts,
        len(feedback_candidates),
    )
    return {
        "job_name": job_name,
        "candidate_count": len(candidates),
        "qualified_count": len(qualified),
        "greeted_count": greeted,
        "replied_count": replied,
        "interviewed_count": interviewed,
        "avg_score": avg_score if qualified else None,
        "feedback_count": len(feedback_candidates),
        "status_counts": status_counts,
        "reason_counts": reason_counts,
        "false_positive_reasons": false_positive_reasons,
        "false_negative_reasons": false_negative_reasons,
        "ai_bias_counts": ai_bias_counts,
        "recommendations": recommendations,
        "suggestions": [recommendation["text"] for recommendation in recommendations],
    }


def build_job_review_text(
    job_name: str,
    candidates: Sequence[Mapping[str, Any]],
) -> str:
    """Build the compatibility text report from the shared review model."""
    review = build_job_review_model(job_name, candidates)

    def format_counter(counter: Counter[str], empty: str = "暂无") -> list[str]:
        if not counter:
            return [f"- {empty}"]
        return [f"- {name}: {count}" for name, count in counter.most_common(8)]

    lines = [
        f"{job_name} 岗位复盘",
        "",
        "【样本概览】",
        f"- 通过筛选：{review['qualified_count']} 人",
        f"- 已打招呼：{review['greeted_count']} 人",
        f"- 已回复：{review['replied_count']} 人",
        f"- 已约面：{review['interviewed_count']} 人",
        (
            f"- 平均分：{review['avg_score']:.1f}"
            if review["avg_score"] is not None
            else "- 平均分：暂无"
        ),
        f"- 已反馈：{review['feedback_count']} 人",
        f"- 反馈覆盖：{review['feedback_count']}/{review['candidate_count']} 人",
        "",
        "【反馈分布】",
        *format_counter(review["status_counts"], "暂无反馈状态"),
        "",
        "【结构化原因 Top】",
        *format_counter(review["reason_counts"], "暂无结构化原因"),
        "",
        "【误推原因】",
        *format_counter(review["false_positive_reasons"], "暂无误推原因"),
        "",
        "【误杀原因】",
        *format_counter(review["false_negative_reasons"], "暂无误杀原因"),
        "",
        "【AI 偏差】",
        *format_counter(review["ai_bias_counts"], "暂无 AI 偏差反馈"),
        "",
        "【建议调整方向】",
        *review["suggestions"],
    ]
    return "\n".join(lines)
