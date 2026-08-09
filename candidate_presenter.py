"""Pure presentation helpers for candidate result and review views."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from candidate_workflow import derive_candidate_decision, format_followup_due_at
from filtering import normalize_candidate_gender


def candidate_gender_display(candidate: Mapping[str, Any]) -> str:
    """Return normalized candidate gender from current or legacy records."""
    structured = candidate.get("structured") or {}
    for value in (candidate.get("gender"), structured.get("gender")):
        gender = normalize_candidate_gender(value)
        if gender:
            return gender
    for line in str(candidate.get("summary") or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("性别：") or stripped.startswith("性别:"):
            gender = normalize_candidate_gender(stripped)
            if gender:
                return gender
            break
    return "—"


def extract_summary_display_fields(summary: object) -> dict[str, str]:
    """Extract education, age, and job status needed by the result table."""
    text = str(summary or "")
    education = next(
        (
            value
            for value in ("博士", "硕士", "本科", "大专", "高中", "中专")
            if value in text
        ),
        "",
    )
    age_match = re.search(r"年龄[：:]\s*(\d+)|(\d+)\s*岁", text)
    status_match = re.search(r"(?:求职状态[：:]\s*)?(离职|在职|在校|应届)", text)
    return {
        "education": education,
        "age": (
            next((group for group in age_match.groups() if group), "")
            if age_match
            else ""
        ),
        "job_status": status_match.group(1) if status_match else "",
    }


def latest_history_value(
    entries: Sequence[Mapping[str, Any]] | None,
    field: str,
    summary: object,
    summary_prefix: str,
) -> str:
    """Return the latest history value, with a summary-line fallback."""
    valid_entries = [
        entry
        for entry in (entries or [])
        if isinstance(entry, dict) and str(entry.get(field, "")).strip()
    ]
    if valid_entries:

        def date_key(entry: Mapping[str, Any]) -> tuple[int, int]:
            end = str(entry.get("end", "")).strip()
            if any(marker in end for marker in ("至今", "现在", "今")):
                return 2, 99999999
            digits = re.sub(r"\D", "", end)
            if digits:
                return 1, int(digits[:8])
            return 0, 0

        dated_entries = [entry for entry in valid_entries if date_key(entry)[0] > 0]
        latest = max(dated_entries, key=date_key) if dated_entries else valid_entries[0]
        return str(latest.get(field, "")).strip()

    for line in str(summary or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(summary_prefix):
            value = stripped[len(summary_prefix) :].strip()
            return value.split()[0] if value else ""
    return ""


def format_ai_hard_conditions(rule: Mapping[str, Any]) -> str:
    """Format a saved job rule for first and resume AI reviews."""
    hard_parts = []
    if rule.get("min_exp"):
        hard_parts.append(f"- 经验：要求≥{rule['min_exp']}年，候选人需满足")
    if rule.get("edu") and rule.get("edu") != "不限":
        hard_parts.append(f"- 学历：要求{rule['edu']}")
    if rule.get("max_age"):
        hard_parts.append(f"- 年龄：上限{rule['max_age']}岁")
    if rule.get("gender") in {"男", "女"}:
        hard_parts.append(f"- 性别：要求{rule['gender']}")
    if rule.get("work_location"):
        hard_parts.append(f"- 地点：要求{rule['work_location']}，候选人期望城市需匹配")
    if rule.get("salary_max"):
        hard_parts.append(f"- 薪资：岗位最高{rule['salary_max']}K，候选人期望不应超过")
    required_conditions = rule.get("required_conditions", [])
    if required_conditions:
        condition_names = [
            condition
            if isinstance(condition, str)
            else condition.get("name", str(condition))
            for condition in required_conditions
        ]
        hard_parts.append(f"- 必要条件：{'、'.join(condition_names)}")
    if not hard_parts:
        return ""
    return "## 筛选硬条件\n" + "\n".join(hard_parts) + "\n\n"


def format_ai_eval_batch_summary(
    summary: Mapping[str, Any],
) -> tuple[str, str, bool]:
    """Build the bounded completion dialog for one batch AI evaluation."""

    def compact_text(value: object, max_chars: int) -> str:
        text = str(value or "").strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1] + "…"

    success = summary.get("success") or []
    failed = summary.get("failed") or []
    skipped = summary.get("skipped") or []
    selected_count = summary.get("selected_count") or (
        len(success) + len(failed) + len(skipped)
    )
    lines = [
        f"本次共选择 {selected_count} 人",
        f"成功 {len(success)} 人",
        f"失败 {len(failed)} 人",
        f"跳过 {len(skipped)} 人",
    ]
    if failed:
        lines.extend(["", "失败候选人："])
        for item in failed[:6]:
            name = compact_text(item.get("name") or "?", 12)
            reason = compact_text(item.get("reason") or "评估失败", 36)
            lines.append(f"- {name}：{reason}")
        if len(failed) > 6:
            lines.append(f"- 另有 {len(failed) - 6} 人失败，请在状态列查看")
    if skipped:
        lines.extend(["", "已跳过："])
        for item in skipped[:3]:
            name = compact_text(item.get("name") or "?", 12)
            reason = compact_text(item.get("reason") or "已跳过", 24)
            lines.append(f"- {name}：{reason}")
        if len(skipped) > 3:
            lines.append(f"- 另有 {len(skipped) - 3} 人已跳过")
    return "AI 评估完成", "\n".join(lines), bool(failed)


def format_display_datetime(value: object, missing: str = "未知") -> str:
    """Format persisted timestamps for compact user-facing display."""
    text = str(value or "").strip()
    if not text:
        return missing
    for date_format, output_format in (
        ("%Y%m%d_%H%M%S", "%Y-%m-%d %H:%M"),
        ("%Y%m%dT%H%M%S", "%Y-%m-%d %H:%M"),
        ("%Y%m%d", "%Y-%m-%d"),
    ):
        try:
            return datetime.strptime(text, date_format).strftime(output_format)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def format_candidate_decision_summary(candidate: dict[str, Any]) -> str:
    """Format the first-screen information needed for a candidate decision."""
    decision = derive_candidate_decision(candidate)
    score = candidate.get("match_score", 0)
    lines = [
        "下一步",
        f"  {decision.next_action}",
        "",
        "判断依据",
        f"  筛选结论：{decision.screening_result}（{score} 分）",
    ]
    if decision.review_status == "pending":
        lines.append("  复核原因：")
        lines.extend(f"  - {reason}" for reason in decision.review_reasons)
    elif decision.review_status == "passed":
        lines.append("  复核状态：已通过")
        passed_reasons = [
            str(reason).strip()
            for reason in (candidate.get("review_passed_reasons") or [])
            if str(reason).strip()
        ]
        if passed_reasons:
            lines.append("  通过前复核事项：")
            lines.extend(f"  - {reason}" for reason in passed_reasons)
    elif decision.review_status == "cancelled":
        lines.append("  复核状态：已结束")
        if decision.review_reasons:
            lines.append("  结束前待复核事项：")
            lines.extend(f"  - {reason}" for reason in decision.review_reasons)
    elif candidate.get("review_rejected_at"):
        lines.append("  复核状态：未通过")
        rejected_reasons = candidate.get("review_rejected_reasons") or []
        if rejected_reasons:
            lines.append("  未通过事项：")
            lines.extend(f"  - {reason}" for reason in rejected_reasons)
    elif decision.result_view == "淘汰记录":
        lines.append("  复核状态：无需复核（筛选未通过）")
    else:
        lines.append("  复核状态：无需复核")

    breakdown = candidate.get("score_breakdown") or {}
    if breakdown:
        score_parts = [
            f"基础 {breakdown.get('base', 0)}",
            f"技能 {breakdown.get('skill', 0)}",
            f"经验 {breakdown.get('experience', 0)}",
            f"学历 {breakdown.get('education', 0)}",
            f"优先项 {breakdown.get('preferred', 0)}",
        ]
        adjustment = (
            breakdown.get("resume_adjustment")
            if breakdown.get("resume_adjustment") is not None
            else breakdown.get("ai_adjustment")
        )
        if adjustment:
            try:
                score_parts.append(f"AI 调整 {int(adjustment):+d}")
            except (TypeError, ValueError):
                score_parts.append(f"AI 调整 {adjustment}")
        lines.append(f"  评分拆解：{'｜'.join(score_parts)}")

    evidence_items = [
        item
        for item in (candidate.get("keyword_evidence") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    skill_matches = candidate.get("skill_matches") or []
    if evidence_items or skill_matches:
        lines.extend(["", "关键匹配"])
        if evidence_items:
            for item in evidence_items[:5]:
                evidence = str(item.get("evidence") or "").strip()
                suffix = f"：{evidence}" if evidence else ""
                lines.append(f"  - {item.get('name')}{suffix}")
        else:
            for item in skill_matches[:5]:
                name = item.get("name") if isinstance(item, dict) else str(item)
                if name:
                    lines.append(f"  - {name}")

    risks = []
    risk_sources = []
    for value in (candidate.get("risk_flags"), candidate.get("qualification_reasons")):
        risk_sources.extend(value if isinstance(value, (list, tuple)) else [value])
    for risk in risk_sources:
        text = str(risk or "").strip()
        if text and text not in risks and text not in decision.review_reasons:
            risks.append(text)
    if risks:
        lines.extend(["", "风险与不足"])
        lines.extend(f"  - {risk}" for risk in risks[:6])

    if candidate.get("llm_evaluated") or candidate.get("llm_error"):
        lines.extend(["", "AI 评估"])
        if candidate.get("llm_error"):
            error = str(candidate.get("llm_error")).replace("\n", " ").strip()
            lines.append(f"  评估失败：{error}")
        else:
            adjustment = candidate.get("llm_adjustment", 0) or 0
            reason = (
                str(candidate.get("llm_reason") or "未提供评估理由")
                .replace("\n", " ")
                .strip()
            )
            try:
                lines.append(f"  调整分：{int(adjustment):+d}")
            except (TypeError, ValueError):
                lines.append(f"  调整分：{adjustment}")
            lines.append(f"  {reason}")
    lines.extend(
        [
            "",
            "当前状态",
            f"  沟通：{decision.communication_status}",
            f"  下次跟进：{format_followup_due_at(candidate.get('next_followup_at'))}",
            f"  人工反馈：{candidate.get('feedback_status') or '未标记'}",
        ]
    )
    return "\n".join(lines)
