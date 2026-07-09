"""Deterministic health checks for one job requirement config."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class JobConfigIssue:
    severity: str
    title: str
    detail: str
    suggestion: str
    penalty: int = 0


@dataclass(frozen=True)
class JobConfigQuality:
    score: int
    verdict: str
    issues: tuple[JobConfigIssue, ...]


@dataclass(frozen=True)
class RequiredCondition:
    kind: str
    text: str
    items: tuple[str, ...]


SOFT_QUALITY_TERMS = (
    "沟通", "学习能力", "团队", "责任心", "抗压", "执行力", "主动",
    "认真", "细心", "踏实", "稳定性", "积极", "服务意识", "主人翁",
)

BASIC_CONDITION_TERMS = (
    "本科", "大专", "硕士", "博士", "学历", "年龄", "岁", "经验",
    "年经验", "薪资", "工资", "k", "K", "统招",
)

PACKED_KEYWORD_SEPARATORS = ("/", "、", "，", ",", "；", ";", "或", "和")
LONG_SIMPLE_CONDITION_CHARS = 12
LONG_CONDITION_ITEM_CHARS = 10
LONG_KEYWORD_CHARS = 16


def diagnose_job_config(job_name: str, rule: dict[str, Any]) -> list[JobConfigIssue]:
    """Return deterministic issues for a single job config."""
    issues: list[JobConfigIssue] = []
    title = _clean_text(job_name) or _clean_text(rule.get("job_title")) or "未命名岗位"
    keywords = _normalize_keywords(rule.get("keywords"))
    preferred = _normalize_keywords(rule.get("preferred_keywords"), bonus_key="bonus")
    required = list(_iter_required_conditions(rule.get("required_conditions")))

    if not title or title == "未命名岗位":
        issues.append(_issue(
            "error",
            "岗位名称为空",
            "保存后难以区分岗位，也会影响按岗位去重和统计。",
            "填写清晰的岗位名称，例如“Java 后端工程师”。",
        ))

    if not keywords and not required:
        issues.append(_issue(
            "error",
            "缺少筛选依据",
            "没有技能关键词，也没有必要条件，候选人只能按基础字段粗筛。",
            "至少配置 3 个核心技能关键词，硬性约束再放入必要条件。",
        ))

    issues.extend(_diagnose_basic_fields(rule))
    issues.extend(_diagnose_keywords(keywords, preferred))
    issues.extend(_diagnose_required_conditions(required))

    if not _clean_text(rule.get("original_requirement")):
        issues.append(_issue(
            "info",
            "缺少原始招聘需求",
            "不影响筛选，但后续复盘配置来源时证据不足。",
            "如果这是从 JD 解析来的岗位，建议保留原始招聘需求文本。",
        ))

    return issues


def summarize_job_config_diagnostics(
    job_name: str,
    rule: dict[str, Any],
    issues: list[JobConfigIssue] | None = None,
) -> str:
    """Format diagnosis result for GUI display.

    When *issues* is provided the expensive ``diagnose_job_config`` call is
    skipped — useful when the caller already computed the issues list.
    """
    if issues is None:
        issues = diagnose_job_config(job_name, rule)
    quality = score_job_config_quality(issues)
    title = _clean_text(job_name) or "当前岗位"
    if not issues:
        return (
            f"{title} 配置体检通过。\n"
            f"配置质量：{quality.score}/100，{quality.verdict}\n\n"
            "未发现明显配置冲突。"
        )

    counts = {
        "error": sum(1 for item in issues if item.severity == "error"),
        "warning": sum(1 for item in issues if item.severity == "warning"),
        "info": sum(1 for item in issues if item.severity == "info"),
    }
    header = (
        f"{title} 配置体检结果\n"
        f"配置质量：{quality.score}/100，{quality.verdict}\n"
        f"严重 {counts['error']} 项，提醒 {counts['warning']} 项，建议 {counts['info']} 项"
    )
    lines = [header, ""]
    for idx, issue in enumerate(issues, 1):
        label = {"error": "严重", "warning": "提醒", "info": "建议"}.get(issue.severity, "提醒")
        penalty_text = f"（-{issue.penalty}）" if issue.penalty else ""
        lines.extend([
            f"{idx}. [{label}] {issue.title}{penalty_text}",
            f"   问题：{issue.detail}",
            f"   建议：{issue.suggestion}",
            "",
        ])
    return "\n".join(lines).rstrip()


def score_job_config_quality(issues: list[JobConfigIssue]) -> JobConfigQuality:
    """Return a 0-100 quality score from deterministic diagnosis issues."""
    penalty = sum(max(0, issue.penalty) for issue in issues)
    score = max(0, min(100, 100 - penalty))
    has_error = any(issue.severity == "error" for issue in issues)
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    if has_error:
        verdict = "存在阻断项，建议先修正再保存"
    elif score >= 90:
        verdict = "配置成熟，可直接使用"
    elif score >= 75:
        verdict = "配置可用，仍有少量优化空间"
    elif score >= 60:
        verdict = "配置勉强可用，筛选结果可能偏宽或偏窄"
    else:
        verdict = "配置风险较高，建议明显收敛后再使用"
    if not issues and warnings == 0:
        verdict = "配置成熟，可直接使用"
    return JobConfigQuality(score=score, verdict=verdict, issues=tuple(issues))


def _diagnose_basic_fields(rule: dict[str, Any]) -> list[JobConfigIssue]:
    issues: list[JobConfigIssue] = []

    min_exp = rule.get("min_exp")
    if min_exp is None:
        issues.append(_issue(
            "warning",
            "最低经验未设置",
            "经验不限制会放宽候选人范围，后续需要靠关键词承担更多筛选压力。",
            "如果岗位不是应届或不限经验，建议填写最低经验年限。",
        ))
    elif _as_int(min_exp) is None:
        issues.append(_issue(
            "error",
            "最低经验不是数字",
            f"当前值为“{min_exp}”，保存或筛选时可能被拒绝。",
            "填写整数年限，例如 3、5、8。",
        ))

    max_age = rule.get("max_age")
    if max_age is not None:
        age_value = _as_int(max_age)
        if age_value is None:
            issues.append(_issue(
                "error",
                "最大年龄不是数字",
                f"当前值为“{max_age}”，年龄过滤无法稳定执行。",
                "填写整数年龄，或留空表示不限制。",
            ))
        elif age_value and age_value < 24:
            issues.append(_issue(
                "warning",
                "最大年龄过低",
                f"当前最大年龄为 {age_value} 岁，可能误杀大多数有经验候选人。",
                "确认这是否真的是硬性条件；不是硬约束就留空。",
            ))

    salary_min = _as_int(rule.get("salary_min"))
    salary_max = _as_int(rule.get("salary_max"))
    raw_salary_min = rule.get("salary_min")
    raw_salary_max = rule.get("salary_max")
    if raw_salary_min not in (None, "") and salary_min is None:
        issues.append(_issue("error", "最低薪资不是数字", f"当前值为“{raw_salary_min}”。", "填写整数 K 值，例如 15。"))
    if raw_salary_max not in (None, "") and salary_max is None:
        issues.append(_issue("error", "最高薪资不是数字", f"当前值为“{raw_salary_max}”。", "填写整数 K 值，例如 25。"))
    if salary_min is not None and salary_max is not None:
        if salary_min > salary_max:
            issues.append(_issue(
                "error",
                "薪资范围倒挂",
                f"最低薪资 {salary_min}K 高于最高薪资 {salary_max}K。",
                "交换两个值，或重新填写岗位薪资范围。",
            ))
        elif salary_max - salary_min <= 2:
            issues.append(_issue(
                "warning",
                "薪资区间过窄",
                f"当前区间为 {salary_min}-{salary_max}K，候选人期望薪资稍有偏差就可能被过滤。",
                "如果不是严格预算，建议适当放宽薪资上限或留空。",
            ))
    elif (salary_min is None) ^ (salary_max is None):
        issues.append(_issue(
            "warning",
            "薪资范围只填了一端",
            "单边薪资会让过滤含义不够直观，复盘时也难判断预算边界。",
            "建议同时填写最低和最高薪资；不想限制则两项都留空。",
        ))

    return issues


def _diagnose_keywords(
    keywords: list[tuple[str, int | None]],
    preferred: list[tuple[str, int | None]],
) -> list[JobConfigIssue]:
    issues: list[JobConfigIssue] = []
    keyword_names = [name for name, _ in keywords]
    preferred_names = [name for name, _ in preferred]
    all_names = keyword_names + preferred_names

    if not keyword_names and preferred_names:
        issues.append(_issue(
            "warning",
            "只有优先项没有核心技能",
            "优先项只负责加分，缺少核心技能会让评分模型失去主轴。",
            "把必须体现岗位能力的 3-8 个技能放入核心技能；加分项再放优先项。",
        ))
    if 0 < len(keyword_names) < 3:
        issues.append(_issue(
            "warning",
            "核心技能关键词偏少",
            f"当前只有 {len(keyword_names)} 个核心技能，评分区分度会偏弱。",
            "补足岗位最能区分候选人的 3-8 个技能关键词。",
        ))
    if len(keyword_names) > 15:
        issues.append(_issue(
            "warning",
            "核心技能关键词过多",
            f"当前有 {len(keyword_names)} 个核心技能，容易把普通 JD 词堆当成能力模型。",
            "保留真正能区分候选人的技能，其余放到优先项或删掉。",
        ))
    if len(preferred_names) > 10:
        issues.append(_issue(
            "warning",
            "优先项过多",
            f"当前有 {len(preferred_names)} 个优先项，可能稀释主评分。",
            "只保留加分但非硬性的行业、业务或工具经验。",
        ))

    packed_keywords = [name for name in all_names if _looks_packed_keyword(name)]
    if packed_keywords:
        issues.append(_issue(
            "warning",
            "关键词疑似打包填写",
            "疑似打包项：" + "、".join(packed_keywords[:8]),
            "每个关键词应只表达一个技能或经验；多个技能请拆成多行，避免匹配和计分失真。",
        ))

    long_keywords = [name for name in all_names if _effective_len(name) > LONG_KEYWORD_CHARS]
    if long_keywords:
        issues.append(_issue(
            "warning",
            "关键词过长",
            "疑似长关键词：" + "、".join(long_keywords[:8]),
            "关键词越长越依赖简历原文完全包含，建议改成短技能名、工具名或业务名。",
        ))

    broad_keywords = [name for name in keyword_names if _looks_too_broad_keyword(name)]
    if broad_keywords:
        issues.append(_issue(
            "warning",
            "核心关键词过泛",
            "疑似过泛项：" + "、".join(broad_keywords[:8]),
            "过泛关键词容易人人命中，建议换成具体技术、工具、业务场景或证书。",
        ))

    duplicates = _duplicates(all_names)
    if duplicates:
        issues.append(_issue(
            "warning",
            "关键词重复",
            "重复项：" + "、".join(duplicates[:8]),
            "合并重复关键词，避免同一能力被重复计分。",
        ))

    overlap = sorted(set(_norm(name) for name in keyword_names) & set(_norm(name) for name in preferred_names))
    if overlap:
        display = [name for name in all_names if _norm(name) in overlap]
        issues.append(_issue(
            "warning",
            "核心技能与优先项重叠",
            "重叠项：" + "、".join(dict.fromkeys(display).keys()),
            "同一项只保留在一个位置：硬技能放核心技能，非必要加分项放优先项。",
        ))

    invalid_weight = [name for name, weight in keywords + preferred if weight is None or weight < 1 or weight > 3]
    if invalid_weight:
        issues.append(_issue(
            "error",
            "关键词权重超出范围",
            "异常项：" + "、".join(invalid_weight[:8]),
            "权重只使用 1-3，1 表示普通，3 表示关键。",
        ))

    soft_terms = [name for name in all_names if _contains_any(name, SOFT_QUALITY_TERMS)]
    if soft_terms:
        issues.append(_issue(
            "warning",
            "软素质被放入关键词",
            "疑似软素质：" + "、".join(soft_terms[:8]),
            "关键词应优先放可从简历稳定识别的技能、工具、行业经验；软素质适合人工面试判断。",
        ))

    return issues


def _diagnose_required_conditions(required: list[RequiredCondition]) -> list[JobConfigIssue]:
    issues: list[JobConfigIssue] = []
    if len(required) > 6:
        issues.append(_issue(
            "warning",
            "必要条件过多",
            f"当前有 {len(required)} 条必要条件，硬过滤过多会显著提高误杀概率。",
            "只保留不满足就一定不看的条件；一般控制在 1-5 条。",
        ))

    required_texts = [item.text for item in required]
    duplicates = _duplicates(required_texts)
    if duplicates:
        issues.append(_issue(
            "warning",
            "必要条件重复",
            "重复项：" + "、".join(duplicates[:8]),
            "删除重复条件，避免配置难以维护。",
        ))

    long_simple = [
        item.text for item in required
        if item.kind == "simple" and _effective_len(item.text) > LONG_SIMPLE_CONDITION_CHARS
    ]
    if long_simple:
        issues.append(_issue(
            "warning",
            "简单必要条件过长",
            "疑似长句：" + "、".join(long_simple[:5]),
            "简单必要条件会按整段文字严格匹配；建议拆成短关键词，或改成满足任一/全部满足。",
        ))

    long_items = [
        part
        for item in required
        if item.kind in {"or", "and"}
        for part in item.items
        if _effective_len(part) > LONG_CONDITION_ITEM_CHARS
    ]
    if long_items:
        issues.append(_issue(
            "warning",
            "必要条件子项过长",
            "疑似长子项：" + "、".join(long_items[:5]),
            "满足任一/全部满足里的每个子项也会按关键词匹配，建议保留最短稳定表述。",
        ))

    soft_conditions = [item.text for item in required if _contains_any(item.text, SOFT_QUALITY_TERMS)]
    if soft_conditions:
        issues.append(_issue(
            "warning",
            "必要条件包含软素质",
            "疑似软素质：" + "、".join(soft_conditions[:5]),
            "不要把沟通、责任心、抗压等软素质设为硬过滤条件。",
        ))

    basic_conditions = [item.text for item in required if _contains_any(item.text, BASIC_CONDITION_TERMS)]
    if basic_conditions:
        issues.append(_issue(
            "info",
            "必要条件可能重复基础字段",
            "疑似重复：" + "、".join(basic_conditions[:5]),
            "学历、经验、年龄、薪资优先填写到基础字段；必要条件只放特殊硬约束。",
        ))

    return issues


def _normalize_keywords(value: Any, bonus_key: str = "weight") -> list[tuple[str, int | None]]:
    result: list[tuple[str, int | None]] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if isinstance(item, dict):
            name = _clean_text(item.get("name"))
            weight = _as_int(item.get(bonus_key, item.get("weight")))
        else:
            name = _clean_text(item)
            weight = 1
        if name:
            result.append((name, weight))
    return result


def _iter_required_conditions(value: Any) -> Iterable[RequiredCondition]:
    if not isinstance(value, list):
        return []
    result: list[RequiredCondition] = []
    for cond in value:
        if isinstance(cond, dict):
            kind = _clean_text(cond.get("type")) or "or"
            items = cond.get("items")
            if isinstance(items, list):
                clean_items = tuple(_clean_text(item) for item in items if _clean_text(item))
                text = " / ".join(clean_items)
            else:
                clean_items = ()
                text = _clean_text(cond.get("condition"))
        else:
            kind = "simple"
            clean_items = ()
            text = _clean_text(cond)
        if text:
            result.append(RequiredCondition(kind=kind, text=text, items=clean_items))
    return result


def _issue(severity: str, title: str, detail: str, suggestion: str) -> JobConfigIssue:
    penalty = _issue_penalty(severity, title)
    return JobConfigIssue(
        severity=severity,
        title=title,
        detail=detail,
        suggestion=suggestion,
        penalty=penalty,
    )


def _issue_penalty(severity: str, title: str) -> int:
    if severity == "error":
        return 25
    warning_penalties = {
        "缺少筛选依据": 35,
        "只有优先项没有核心技能": 18,
        "核心技能关键词偏少": 12,
        "核心技能关键词过多": 10,
        "必要条件过多": 12,
        "软素质被放入关键词": 10,
        "必要条件包含软素质": 12,
        "关键词疑似打包填写": 8,
        "关键词过长": 8,
        "核心关键词过泛": 10,
        "核心技能与优先项重叠": 8,
        "关键词重复": 6,
        "薪资范围只填了一端": 5,
        "薪资区间过窄": 6,
        "最低经验未设置": 8,
        "最大年龄过低": 10,
        "简单必要条件过长": 8,
        "必要条件子项过长": 6,
        "必要条件重复": 6,
        "优先项过多": 6,
    }
    if severity == "warning":
        return warning_penalties.get(title, 5)
    return 0


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm(value: str) -> str:
    return _clean_text(value).replace(" ", "").lower()


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for value in values:
        key = _norm(value)
        if not key:
            continue
        if key in seen and seen[key] not in dupes:
            dupes.append(seen[key])
        else:
            seen[key] = value
    return dupes


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    haystack = _clean_text(text).lower()
    return any(str(term).lower() in haystack for term in terms)


def _effective_len(text: str) -> int:
    return len(_clean_text(text).replace(" ", ""))


def _looks_packed_keyword(text: str) -> bool:
    cleaned = _clean_text(text)
    if any(sep in cleaned for sep in PACKED_KEYWORD_SEPARATORS):
        return True
    parts = [part for part in cleaned.split() if part]
    return len(parts) >= 4


def _looks_too_broad_keyword(text: str) -> bool:
    cleaned = _clean_text(text)
    if not cleaned:
        return False
    broad_terms = {"开发", "设计", "管理", "业务", "系统", "平台", "项目", "工具", "经验", "能力"}
    return cleaned in broad_terms
