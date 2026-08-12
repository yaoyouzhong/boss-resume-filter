from candidate_scan_policy import (
    build_ai_hard_conditions,
    build_rejection_reason_labels,
    normalize_rejection_reason,
)


def test_rejection_labels_prefer_explicit_recruitment_thresholds():
    labels = build_rejection_reason_labels(
        {
            "min_exp": 5,
            "edu": "本科",
            "max_age": 35,
            "gender": "男",
            "work_location": "上海",
            "salary_max": 30,
            "required_conditions": ["统招本科", "Spring Cloud"],
        }
    )

    assert labels == {
        "experience": "经验不足（要求5年以上）",
        "education": "学历不符/不足（要求统招本科）",
        "age": "年龄不符（要求≤35岁）",
        "gender": "性别不符（要求男）",
        "city": "地点不符（要求上海）",
        "salary": "薪资不匹配（岗位最高30K）",
        "technical": "技术条件不符",
    }


def test_rejection_reason_variants_collapse_to_stable_business_groups():
    labels = build_rejection_reason_labels({"min_exp": 3})

    assert normalize_rejection_reason("候选人经验不足", labels) == labels["experience"]
    assert normalize_rejection_reason("必要条件不满足：Java", labels) == labels["technical"]
    assert normalize_rejection_reason("薪资期望过高", labels) == labels["salary"]
    assert normalize_rejection_reason("自定义原因", labels) == "自定义原因"
    assert normalize_rejection_reason(None, labels) == "未知"


def test_ai_hard_conditions_preserve_all_configured_constraints():
    text = build_ai_hard_conditions(
        {
            "min_exp": 5,
            "edu": "本科",
            "max_age": 35,
            "gender": "女",
            "work_location": "上海",
            "salary_max": 30,
            "required_conditions": [
                "Spring Cloud",
                {"name": "证券经验"},
            ],
        }
    )

    assert text.startswith("## 筛选硬条件\n")
    assert "- 经验：要求≥5年" in text
    assert "- 学历：要求本科" in text
    assert "- 年龄：上限35岁" in text
    assert "- 性别：要求女" in text
    assert "- 地点：要求上海" in text
    assert "- 薪资：岗位最高30K" in text
    assert "- 必要条件：Spring Cloud、证券经验" in text
    assert text.endswith("\n\n")


def test_ai_hard_conditions_are_empty_for_unrestricted_rule():
    assert build_ai_hard_conditions({"edu": "不限"}) == ""
