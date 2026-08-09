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
REASON_SUGGESTIONS = {
    "规则过宽": "补充硬性约束或提高核心技能关键词质量。",
    "规则过窄": "放宽必要条件，长句条件拆成短关键词。",
    "技能不匹配": "复核关键词是否过泛、权重是否偏高。",
    "行业经验不符": "把行业经验放入优先项或必要条件，取决于是否硬性要求。",
    "AI 高估": "复核 AI 评估提示词和硬条件复核证据。",
    "AI 低估": "检查简历摘要是否信息不足，必要时使用完整简历二次评估。",
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
    text = str(suggestion or "").lstrip("- ").strip()
    for delimiter in ("：", ":", "；", ";"):
        if delimiter not in text:
            continue
        title, detail = text.split(delimiter, 1)
        if title.strip() and detail.strip():
            return title.strip().rstrip("。"), detail.strip()
    return text.rstrip("。"), ""


def build_job_review_suggestions(
    status_counts: Counter[str],
    reason_counts: Counter[str],
    feedback_count: int,
) -> list[str]:
    """Build guarded job-rule suggestions from sufficient feedback evidence."""
    if feedback_count == 0:
        return ["- 先积累反馈样本；没有结构化反馈时不建议调整岗位规则。"]
    if feedback_count < 5:
        observed = "、".join(
            f"{reason} {count} 条" for reason, count in reason_counts.most_common(5)
        ) or "暂无结构化原因"
        return [
            f"- 当前只有 {feedback_count} 条反馈，样本不足 5 条，不建议据此修改岗位规则。",
            f"- 已记录原因：{observed}。",
        ]

    suggestions = []
    false_positive = status_counts.get("误推", 0)
    false_negative = status_counts.get("误杀", 0)
    if false_positive * 2 >= feedback_count and false_positive > 0:
        suggestions.append("- 误推占比较高：优先检查核心技能是否过泛、必要条件是否缺失。")
    if false_negative * 2 >= feedback_count and false_negative > 0:
        suggestions.append("- 误杀占比较高：优先检查必要条件是否过严、简单关键词是否写成长句。")
    for reason, suggestion in REASON_SUGGESTIONS.items():
        count = reason_counts.get(reason, 0)
        if count > 0:
            suggestions.append(
                f"- {reason}：{count}/{feedback_count} 条；{suggestion}"
            )
    return suggestions or ["- 暂无明确规则调整方向；继续积累结构化反馈后再复盘。"]


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
        "suggestions": build_job_review_suggestions(
            status_counts,
            reason_counts,
            len(feedback_candidates),
        ),
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
