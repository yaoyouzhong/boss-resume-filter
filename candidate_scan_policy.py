"""Pure candidate-scan labels and AI hard-condition formatting."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_rejection_reason_labels(rule: Mapping[str, Any]) -> dict[str, str]:
    """Build user-facing rejection labels from one job rule."""
    education = rule.get("edu", "不限")
    for condition in rule.get("required_conditions", []):
        if isinstance(condition, str) and "统招" in condition:
            education = condition
            break

    max_age = rule.get("max_age")
    required_gender = rule.get("gender", "不限")
    work_location = rule.get("work_location", "")
    salary_max = rule.get("salary_max")
    return {
        "experience": f"经验不足（要求{rule.get('min_exp', 0)}年以上）",
        "education": f"学历不符/不足（要求{education}）",
        "age": f"年龄不符（要求≤{max_age}岁）" if max_age else "年龄不符",
        "gender": (
            f"性别不符（要求{required_gender}）"
            if required_gender in {"男", "女"}
            else "性别不符"
        ),
        "city": f"地点不符（要求{work_location}）" if work_location else "地点不符",
        "salary": f"薪资不匹配（岗位最高{salary_max}K）" if salary_max else "薪资不匹配",
        "technical": "技术条件不符",
    }


def normalize_rejection_reason(
    reason: object,
    labels: Mapping[str, str],
) -> str:
    """Collapse low-level filter messages into stable business reason groups."""
    text = str(reason or "未知")
    if "经验不足" in text:
        return labels["experience"]
    if "学历不足" in text or "学历不符" in text:
        return labels["education"]
    if "年龄不符" in text or "年龄超限" in text:
        return labels["age"]
    if "性别不符" in text:
        return labels["gender"]
    if "地点不符" in text or "城市不符" in text:
        return labels["city"]
    if "薪资不匹配" in text or "薪资期望过高" in text:
        return labels["salary"]
    if "技术不匹配" in text or "必要条件不满足" in text:
        return labels["technical"]
    if "筛选异常" in text:
        return "筛选异常"
    return text


def build_ai_hard_conditions(rule: Mapping[str, Any]) -> str:
    """Format concrete job constraints for the optional AI evaluation prompt."""
    parts: list[str] = []
    if rule.get("min_exp"):
        parts.append(f"- 经验：要求≥{rule['min_exp']}年，候选人需满足")
    if rule.get("edu") and rule.get("edu") != "不限":
        parts.append(f"- 学历：要求{rule['edu']}")
    if rule.get("max_age"):
        parts.append(f"- 年龄：上限{rule['max_age']}岁")
    if rule.get("gender") in {"男", "女"}:
        parts.append(f"- 性别：要求{rule['gender']}")
    if rule.get("work_location"):
        parts.append(
            f"- 地点：要求{rule['work_location']}，候选人期望城市需匹配"
        )
    if rule.get("salary_max"):
        parts.append(
            f"- 薪资：岗位最高{rule['salary_max']}K，候选人期望不应超过"
        )
    required_conditions = rule.get("required_conditions", [])
    if required_conditions:
        names = [
            condition
            if isinstance(condition, str)
            else condition.get("name", str(condition))
            for condition in required_conditions
        ]
        parts.append(f"- 必要条件：{'、'.join(names)}")
    if not parts:
        return ""
    return "## 筛选硬条件\n" + "\n".join(parts) + "\n\n"
