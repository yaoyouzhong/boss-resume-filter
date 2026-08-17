"""Pure presentation helpers for candidate result and review views."""
from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from candidate_workflow import derive_candidate_decision, format_followup_due_at
from filtering import (
    _parse_candidate_salary_range,
    normalize_candidate_gender,
    parse_experience_years,
)


@dataclass(frozen=True)
class CandidateStatusView:
    """Transient result-table status without Tk or persistence side effects."""

    display: str
    detail: str
    expired_evaluation_id: str = ""


def external_profile_note(candidate: Mapping[str, Any]) -> str:
    """Build the single-import AI profile audit note."""
    filled = candidate.get("profile_ai_filled") or []
    conflicts = candidate.get("profile_conflicts") or []
    error = str(candidate.get("profile_ai_error") or "")
    rejected = candidate.get("qualification_status") == "rejected"
    lines: list[str] = []
    if filled:
        labels = "、".join(
            str(item.get("label") or item.get("field") or "画像字段")
            for item in filled
            if isinstance(item, dict)
        )
        lines.append(f"· 补全：{labels}")
    if conflicts:
        labels = "、".join(
            str(item.get("label") or item.get("field") or "画像字段")
            for item in conflicts
            if isinstance(item, dict)
        )
        action = (
            "已保留规则值，淘汰结论请人工核对"
            if rejected
            else "已保留规则值，转待复核"
        )
        lines.append(f"· 冲突：{labels}（{action}）")
    if error:
        lines.append(f"· 调用未完成：{error}")
    return "AI 增强识别\n" + "\n".join(lines) if lines else ""


def external_profile_batch_note(summary: object | None) -> str:
    """Build the batch-import AI profile audit suffix."""
    if summary is None:
        return ""
    items = getattr(summary, "items", ())
    filled_n = sum(
        1
        for item in items
        if getattr(item, "candidate", None)
        and item.candidate.get("profile_ai_filled")
    )
    conflict_n = sum(
        1
        for item in items
        if getattr(item, "candidate", None)
        and item.candidate.get("profile_conflicts")
    )
    error_n = sum(
        1
        for item in items
        if getattr(item, "candidate", None)
        and item.candidate.get("profile_ai_error")
    )
    parts: list[str] = []
    if filled_n:
        parts.append(f"AI 补全 {filled_n} 人")
    if conflict_n:
        parts.append(f"AI 冲突转待复核 {conflict_n} 人")
    if error_n:
        parts.append(f"AI 增强失败 {error_n} 人")
    return "；" + "，".join(parts) if parts else ""


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


def parse_salary_experience(
    summary: object,
    structured: Mapping[str, Any] | None = None,
    record: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Return compact salary and experience text for a result-table row."""
    # 外部候选人：画像字段由导入时的简历全文提取钉定，summary 是简历原文，
    # BOSS 摘要正则会把时间段（2017-2019）和规模数字（50-150人）误当薪资，
    # 展示一律以记录字段为准，不回退摘要文本
    record = record or {}
    if str(record.get("source") or "").strip() == "external":
        salary = str(record.get("salary") or "").strip()
        exp_raw = str(record.get("exp_years") or "").strip()
        experience = exp_raw if (not exp_raw or exp_raw.endswith("年")) else f"{exp_raw}年"
        return salary, experience
    salary = ""
    experience = ""
    structured = structured or {}
    if structured.get("salary_min") is not None:
        salary_min = structured["salary_min"]
        salary_max = structured.get("salary_max")
        salary = (
            f"{salary_min}-{salary_max}K"
            if salary_max and salary_max != salary_min
            else f"{salary_min}K"
        )
    if structured.get("exp_years") is not None:
        experience = f"{structured['exp_years']}年"

    summary_text = str(summary or "")
    if not salary:
        salary_min, salary_max = _parse_candidate_salary_range(summary_text)
        if salary_min is not None:
            salary = (
                f"{salary_min}-{salary_max}K"
                if salary_max is not None and salary_max != salary_min
                else f"{salary_min}K"
            )
        elif "面议" in summary_text:
            salary = "面议"
    if not experience:
        years = parse_experience_years(summary_text)
        experience = f"{years}年" if years is not None else ""
    return salary, experience


def extract_candidate_extra_fields(
    candidate: Mapping[str, Any],
) -> tuple[str, str, str, str, str]:
    """Return education, age, job status, school, and company display text."""
    structured = candidate.get("structured") or {}
    education = str(structured.get("degree") or "")
    age = str(structured.get("age") or "")
    job_status = str(structured.get("job_status") or "")
    api_profile = candidate.get("_api_profile") or {}
    summary = candidate.get("summary") or ""
    school = latest_history_value(
        api_profile.get("educations"),
        "school",
        summary,
        "教育经历：",
    )
    company = latest_history_value(
        api_profile.get("works"),
        "company",
        summary,
        "工作经历：",
    )
    # 外部导入候选人没有 API 画像：回退到导入时从简历提取的记录级字段
    if not school:
        school = str(candidate.get("school") or "").strip()
    if not company:
        company = str(candidate.get("company") or "").strip()
    # 外部导入候选人：学历/年龄/求职状态在导入时已从简历全文钉定到记录级字段，
    # summary 是简历原文，摘要正则会误命中"在校情况"等教育板块标题——
    # 一律以记录字段为准，不回退摘要文本
    if str(candidate.get("source") or "").strip() == "external":
        education = education or str(candidate.get("education") or "").strip()
        age = age or str(candidate.get("age") or "").strip()
        job_status = job_status or str(candidate.get("job_status") or "").strip()
    elif not education or not age or not job_status:
        fallback = extract_summary_display_fields(summary)
        education = education or fallback["education"]
        age = age or fallback["age"]
        job_status = job_status or fallback["job_status"]
    if age:
        age = f"{age}岁"
    return education, age, job_status, school, company


def format_candidate_status(
    candidate: Mapping[str, Any],
    *,
    evaluating_ids: set[str] | frozenset[str] = frozenset(),
    evaluation_results: Mapping[str, Mapping[str, Any]] | None = None,
    now: float | None = None,
) -> CandidateStatusView:
    """Build transient candidate status and report expired AI feedback."""
    geek_id = str(candidate.get("geek_id") or "")
    if geek_id in evaluating_ids:
        return CandidateStatusView("AI评估中...", "AI评估中...")

    result = (evaluation_results or {}).get(geek_id)
    if result is not None:
        current_time = time.time() if now is None else now
        if current_time - float(result.get("timestamp") or 0) < 3:
            prefix = "✓" if result.get("status") == "success" else "✗"
            display = f"{prefix} {result.get('message') or ''}".rstrip()
            return CandidateStatusView(display, display)
        expired_id = geek_id
    else:
        expired_id = ""

    decision = derive_candidate_decision(dict(candidate))
    status_parts = [decision.communication_status]
    detail = ""
    if decision.review_status == "pending":
        status_parts.append("待复核")
        detail = "复核原因：" + "；".join(
            decision.review_reasons or ("请人工确认",)
        )
    elif decision.review_status == "passed":
        status_parts.append("复核通过")
        passed_reasons = [
            str(reason).strip()
            for reason in (candidate.get("review_passed_reasons") or [])
            if str(reason).strip()
        ]
        reason_text = (
            f"复核事项：{'；'.join(passed_reasons)}\n" if passed_reasons else ""
        )
        detail = (
            f"{reason_text}人工复核结论已通过；原评分和推荐指数不变。"
            "是否可联系仍以当前沟通、反馈和屏蔽状态为准。"
        )
    elif decision.review_status == "cancelled":
        status_parts.append("复核已结束")
        detail = (
            "候选人已因放弃、不合适或屏蔽结束处理；"
            "如需恢复，请先调整对应的反馈、跟进或屏蔽状态。"
        )
    elif candidate.get("review_rejected_at"):
        status_parts.append("复核未通过")
        rejected_reasons = [
            str(reason).strip()
            for reason in (candidate.get("review_rejected_reasons") or [])
            if str(reason).strip()
        ]
        detail = "复核结论：不通过" + (
            f"\n复核事项：{'；'.join(rejected_reasons)}"
            if rejected_reasons
            else ""
        )
    elif decision.result_view == "淘汰记录":
        status_parts.append(decision.primary_review_reason or "淘汰记录")
    if candidate.get("feedback_status"):
        status_parts.append(str(candidate.get("feedback_status")))
    if candidate.get("blacklisted"):
        status_parts.append("已屏蔽")
    display = "｜".join(status_parts)
    return CandidateStatusView(display, detail or display, expired_id)


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

def format_candidate_detail(
    c: Mapping[str, Any],
    *,
    summary_info: Mapping[str, Any],
    feedback_reasons: Sequence[str],
    dimension_labels: Mapping[str, str],
) -> str:
    """Format the complete candidate detail without reading external state."""
    summary = c.get('summary', '')
    info = summary_info

    lines = []
    lines.append("═" * 50)
    lines.append(f"  姓名：{c.get('name', '未知')}")
    lines.append(f"  岗位：{c.get('job_name', '未知')}")

    # 核心信息速览
    core_parts = []
    gender = candidate_gender_display(c)
    if gender != "—":
        core_parts.append(gender)
    age = info.get('age')
    if age:
        core_parts.append(f"{age} 岁")
    exp = info.get('exp_years')
    if exp:
        core_parts.append(f"{exp} 年")
    salary = info.get('salary')
    if salary:
        core_parts.append(f"期望薪资 {salary}")
    status = info.get('job_status')
    if status:
        core_parts.append(status)
    if core_parts:
        lines.append(f"  {'｜'.join(core_parts)}")

    # 学历/学校/专业 — 支持多学历，每条一行
    edu_entries: list[str] = []
    edu = info.get('education')
    api_profile = c.get('_api_profile')

    # API 结构化画像优先
    if api_profile and api_profile.get('educations'):
        for entry in api_profile['educations']:
            parts = [entry.get(k, '') for k in ('school', 'major', 'degree')]
            parts = [p for p in parts if p]
            start = entry.get('start', '')
            end = entry.get('end', '')
            if start or end:
                parts.append(f"{start}-{end}")
            if parts:
                edu_entries.append("·".join(parts))
    else:
        # 优先从 "教育经历：" 标签行解析（API 格式，可能有多条）
        # 格式："教育经历：清华大学 计算机 本科 2015.09 2018.06"
        api_edu_found = False
        for sline in summary.split('\n'):
            sline = sline.strip()
            if sline.startswith("教育经历："):
                api_edu_found = True
                val = sline[len("教育经历："):].strip()
                parts = val.split()
                if len(parts) >= 3:
                    # 学校 专业 学历 [起始] [结束]
                    entry_parts = [parts[0], parts[1], parts[2]]
                    if len(parts) >= 4:
                        entry_parts.append("-".join(parts[3:5]))
                    edu_entries.append("·".join(entry_parts))
                elif len(parts) == 2:
                    edu_entries.append("·".join(parts))

        # DOM 格式兜底（无标签，学校名+专业+学历连写，可能有多条）
        if not api_edu_found:
            edu_entry_pat = re.compile(r'(.+(?:大学|学院))(.+?)(本科|硕士|博士|大专|MBA|EMBA)')
            edu_nopat = re.compile(r'(.+(?:大学|学院))(本科|硕士|博士|大专|MBA|EMBA)')
            for sline in summary.split('\n'):
                sline = sline.strip()
                m = edu_entry_pat.match(sline)
                if m:
                    entry_parts = [m.group(1)]
                    if m.group(2):
                        entry_parts.append(m.group(2))
                    edu_entries.append("·".join(entry_parts))
                    continue
                m2 = edu_nopat.match(sline)
                if m2:
                    edu_entries.append(m2.group(1))

    # 展示多学历
    if edu_entries:
        lines.append(f"  最高学历：{edu}" if edu else "  学历信息")
        for entry in edu_entries:
            lines.append(f"    📚 {entry}")
    elif edu:
        lines.append(f"  {edu}")

    lines.append(f"  geek_id：{c.get('geek_id', '')}")
    if str(c.get('source') or '').strip() == 'external':
        source_parts = [f"来源渠道：{c.get('source_channel') or '其他'}"]
        if c.get('source_note'):
            source_parts.append(f"备注：{c.get('source_note')}")
        lines.append(f"  {'｜'.join(source_parts)}")
    lines.append("═" * 50)

    # 评分信息
    lines.append("")
    lines.append("【评分信息】")
    score = c.get('match_score', 0)
    level = derive_candidate_decision(c).screening_result
    lines.append(f"  匹配分：{score}（{level}）")
    # 淘汰记录的匹配分固定为 0；参考匹配分（剔除硬条件的规则分）供误杀核对
    if c.get('qualification_status') == 'rejected':
        reference_score = c.get('rule_score')
        if isinstance(reference_score, int) and reference_score > 0:
            lines.append(f"  参考匹配分：{reference_score}（剔除硬条件后估算，不影响淘汰结论）")
    lines.append(f"  技能匹配：{c.get('skill_match_ratio', '—')}")
    breakdown = c.get('score_breakdown') or {}
    if breakdown:
        parts = [
            f"基础{breakdown.get('base', 0)}",
            f"技能{breakdown.get('skill', 0)}",
            f"经验{breakdown.get('experience', 0)}",
            f"学历{breakdown.get('education', 0)}",
            f"优先项{breakdown.get('preferred', 0)}",
        ]
        ai_adj = breakdown.get('ai_adjustment')
        resume_adj = breakdown.get('resume_adjustment')
        if resume_adj is not None:
            # 有简历评估时只显示简历调整值（替代一次评估）
            # resume_adj=0 时不追加任何项，保证拆解各项合计 = 总分（total 仅含 resume_adjustment）
            if resume_adj != 0:
                sign = "+" if resume_adj > 0 else ""
                parts.append(f"简历{sign}{resume_adj}")
        elif ai_adj is not None and ai_adj != 0:
            sign = "+" if ai_adj > 0 else ""
            parts.append(f"AI{sign}{ai_adj}")
        lines.append(f"  评分拆解：{' + '.join(parts)}")
    if c.get('greet_sent'):
        lines.append("  状态：已打招呼")
    else:
        lines.append("  状态：未打招呼")
    if c.get('manual_review_required'):
        lines.append("  沟通限制：需人工确认后再打招呼")
    if c.get('blacklisted'):
        lines.append("  屏蔽状态：已加入黑名单")

    risk_flags = c.get('risk_flags') or []
    if risk_flags:
        lines.append("")
        lines.append("【风险提示】")
        for flag in risk_flags:
            lines.append(f"  - {flag}")
        blocked_reason = c.get('auto_greet_blocked_reason')
        if blocked_reason:
            lines.append(f"  自动打招呼阻断原因：{blocked_reason}")

    followup_status = c.get('followup_status') or ("已打招呼" if c.get('greet_sent') else "未沟通")
    if followup_status or c.get('followup_note'):
        lines.append("")
        lines.append("【跟进状态】")
        lines.append(f"  状态：{followup_status}")
        if c.get('followup_updated_at'):
            lines.append(f"  时间：{c.get('followup_updated_at')}")
        if c.get('next_followup_at'):
            lines.append(
                f"  下次跟进：{format_followup_due_at(c.get('next_followup_at'))}"
            )
        if c.get('followup_note'):
            lines.append("  备注：")
            for note_line in str(c.get('followup_note', '')).split('\n'):
                lines.append(f"    {note_line}")

    if c.get('feedback_status'):
        lines.append("")
        lines.append("【人工反馈】")
        lines.append(f"  状态：{c.get('feedback_status')}")
        reasons = feedback_reasons
        if reasons:
            lines.append(f"  原因：{'、'.join(reasons)}")
        if c.get('feedback_updated_at'):
            lines.append(f"  时间：{c.get('feedback_updated_at')}")
        if c.get('feedback_note'):
            lines.append("  备注：")
            for note_line in str(c.get('feedback_note', '')).split('\n'):
                lines.append(f"    {note_line}")

    if c.get('blacklisted'):
        lines.append("")
        lines.append("【黑名单】")
        lines.append("  状态：已屏蔽")
        if c.get('blacklisted_at'):
            lines.append(f"  时间：{c.get('blacklisted_at')}")
        if c.get('blacklist_reason'):
            lines.append("  原因：")
            for note_line in str(c.get('blacklist_reason', '')).split('\n'):
                lines.append(f"    {note_line}")

    explanation = c.get('score_explanation') or []
    if explanation:
        lines.append("")
        lines.append("【评分解释】")
        for item in explanation:
            lines.append(f"  - {item}")

    evidence_items = c.get('keyword_evidence') or []
    if evidence_items:
        lines.append("")
        lines.append("【命中证据】")
        for item in evidence_items:
            if not isinstance(item, dict):
                continue
            name = item.get('name', '')
            weight = item.get('weight', 1)
            evidence = item.get('evidence', '')
            label = "优先项" if item.get('type') == 'preferred' else "技能"
            if evidence:
                lines.append(f"  ✓ [{label}] {name}（权重{weight}）：{evidence}")
            else:
                lines.append(f"  ✓ [{label}] {name}（权重{weight}）")

    # AI 评估信息
    lines.append("")
    if c.get('llm_evaluated'):
        lines.append("【AI 一次评估】")
        lines.append(f"  原始规则分：{c.get('rule_score', '—')}")
        adj = c.get('llm_adjustment', 0)
        sign = "+" if adj > 0 else ""
        lines.append(f"  AI 调整值：{sign}{adj}")
        # 调整后分数 = 规则分 + 一次评估调整值；不读 match_score（简历二次评估已替代为 rule+resume_adj）
        r1_score = max(0, min(100, (c.get('rule_score', 0) or 0) + adj))
        r1_suffix = "（仅参考，不改变淘汰结论）" if c.get('qualification_status') == 'rejected' else ""
        lines.append(f"  调整后分数：{r1_score}{r1_suffix}")
        lines.append(f"  评估模型：{c.get('llm_model', '未知')}")
        lines.append("")
        lines.append("  AI评估：")
        reason = c.get('llm_reason', '无').replace('\n', ' ').replace('\r', '').strip()
        lines.append(f"    {reason}")

        # AI 硬条件复核详情
        hc_verdict = c.get('llm_hard_condition_verdict', 'unknown')
        hc_findings = c.get('llm_hard_condition_findings') or []
        if hc_verdict != 'unknown' or hc_findings:
            verdict_label = {'pass': '通过', 'fail': '不通过', 'unknown': '未判定'}.get(hc_verdict, hc_verdict)
            lines.append("")
            lines.append(f"  硬条件复核：{verdict_label}")
            for finding in hc_findings:
                if not isinstance(finding, dict):
                    continue
                cond = finding.get('condition', '')
                f_verdict = finding.get('verdict', 'unknown')
                f_conf = finding.get('confidence', 'low')
                evidence = finding.get('evidence', '')
                icon = {'pass': '✓', 'fail': '✗', '?': '?'}.get(f_verdict, '?')
                conf_label = {'high': '高置信', 'medium': '中置信', 'low': '低置信'}.get(f_conf, f_conf)
                lines.append(f"    {icon} {cond}（{conf_label}）")
                if evidence:
                    lines.append(f"      证据：{evidence}")

        # 资格审查状态
        qual_status = c.get('qualification_status', 'qualified')
        qual_reasons = c.get('qualification_reasons') or []
        if qual_status != 'qualified' or qual_reasons:
            status_label = {'qualified': '合格', 'rejected': '淘汰', 'manual_review': '待人工确认'}.get(qual_status, qual_status)
            lines.append("")
            lines.append(f"  资格审查：{status_label}")
            for reason_item in qual_reasons:
                lines.append(f"    - {reason_item}")

        # AI 维度评估（有简历二次评估时优先显示简历评估的维度评分）
        dim_scores = c.get('resume_eval_dimension_scores') or c.get('llm_dimension_scores') or {}
        if dim_scores:
            lines.append("")
            lines.append("  维度评估：")
            for key in ('skill_depth', 'experience_quality', 'industry_fit', 'growth_potential'):
                val = dim_scores.get(key)
                if val is None:
                    continue
                label = dimension_labels.get(key, key)
                filled = round(val / 10 * 8)
                bar = "█" * filled + "░" * (8 - filled)
                lines.append(f"    {label}：{val}/10 {bar}")
    elif c.get('llm_error'):
        error = str(c.get('llm_error')).replace('\n', ' ').replace('\r', '').strip()
        lines.append("【AI 一次评估】")
        lines.append("  状态：评估失败，当前分数仍为规则评分")
        lines.append(f"  失败原因：{error or '未知原因'}")
    else:
        lines.append("【AI 一次评估】未启用")

    # 二次评估（基于导入简历）
    if c.get('resume_eval_adjustment') is not None:
        lines.append("")
        lines.append("【AI 二次评估（简历）】")
        r_adj = c.get('resume_eval_adjustment', 0)
        r_sign = "+" if r_adj > 0 else ""
        lines.append(f"  调整值：{r_sign}{r_adj}")
        lines.append(f"  评估时间：{c.get('resume_eval_at', '—')}")
        lines.append(f"  评估模型：{c.get('resume_eval_model', '未知')}")
        r_reason = c.get('resume_eval_reason', '无').replace('\n', ' ').replace('\r', '').strip()
        lines.append("  评估理由：")
        lines.append(f"    {r_reason}")
        if c.get('resume_file'):
            resume_name = (
                c.get('resume_original_name')
                or os.path.basename(c.get('resume_file', ''))
            )
            lines.append(f"  简历文件：{resume_name}")
        if c.get('resume_imported_at'):
            lines.append(f"  导入时间：{c.get('resume_imported_at')}")

    # AI 画像增强痕迹（外部导入的开关制增强；标签在落库时已写入记录，
    # 展示层直接读取，不反向依赖提取模块）
    profile_filled = [
        item for item in (c.get('profile_ai_filled') or []) if isinstance(item, dict)
    ]
    profile_conflicts = [
        item for item in (c.get('profile_conflicts') or []) if isinstance(item, dict)
    ]
    profile_error = str(c.get('profile_ai_error') or '').strip()
    if profile_filled or profile_conflicts or profile_error:
        lines.append("")
        lines.append("【AI 画像增强】")
        for item in profile_filled:
            label = item.get('label') or item.get('field') or '画像字段'
            lines.append(f"  补全：{label} = {item.get('value', '')}")
        for item in profile_conflicts:
            label = item.get('label') or item.get('field') or '画像字段'
            lines.append(
                f"  冲突：{label} 规则值 {item.get('rule', '')}"
                f" / AI 值 {item.get('ai', '')}（已保留规则值）"
            )
        if profile_error:
            lines.append(f"  增强未完成：{profile_error}")

    # 技能匹配详情
    skill_matches = c.get('skill_matches', [])
    if skill_matches:
        lines.append("")
        ratio = c.get('skill_match_ratio', '')
        lines.append(f"【技能匹配详情 {ratio}】")
        for sm in skill_matches:
            if isinstance(sm, dict):
                sname = sm.get('name', '')
                sweight = sm.get('weight', 1)
                lines.append(f"  ✓ {sname}（权重{sweight}）")
            else:
                lines.append(f"  ✓ {sm}")

    structured_summary: dict[str, list[str]] = {
        "教育经历": [],
        "工作经历": [],
        "工作职责": [],
        "技能标签": [],
    }

    # API 结构化画像优先
    if api_profile:
        for edu in (api_profile.get('educations') or []):
            parts = [edu.get(k, '') for k in ('school', 'major', 'degree')]
            parts = [p for p in parts if p]
            if parts:
                structured_summary["教育经历"].append(" ".join(parts))
        for work in (api_profile.get('works') or []):
            parts = [work.get(k, '') for k in ('company', 'position', 'category', 'start', 'end')]
            parts = [p for p in parts if p]
            if parts:
                structured_summary["工作经历"].append(" ".join(parts))
            resp = work.get('responsibility', '')
            if resp:
                structured_summary["工作职责"].append(resp)
            skills = work.get('skills') or []
            if skills:
                structured_summary["技能标签"].append("、".join(skills))
        # 个人优势
        personal = api_profile.get('personal_summary', '')
        if personal:
            structured_summary.setdefault("个人优势", []).append(personal)
    else:
        for sline in summary.split('\n'):
            text = sline.strip()
            for label in structured_summary:
                prefix = f"{label}："
                if text.startswith(prefix):
                    value = text[len(prefix):].strip()
                    if value:
                        structured_summary[label].append(value)
                    break

    if any(structured_summary.values()):
        section_titles = {
            "教育经历": "【教育经历】",
            "工作经历": "【工作经历】",
            "工作职责": "【工作职责】",
            "技能标签": "【技能标签】",
            "个人优势": "【个人优势】",
        }
        for label in ("教育经历", "工作经历", "工作职责", "技能标签", "个人优势"):
            items = structured_summary.get(label) or []
            if not items:
                continue
            lines.append("")
            lines.append(section_titles[label])
            for idx, item in enumerate(items, 1):
                if label in ("工作职责", "技能标签", "个人优势"):
                    lines.append(f"  {idx}. {item}")
                else:
                    lines.append(f"  - {item}")

    # 候选人摘要
    if summary:
        lines.append("")
        lines.append("【候选人摘要】")
        for sline in summary.split('\n'):
            lines.append(f"  {sline}")

    return '\n'.join(lines)
