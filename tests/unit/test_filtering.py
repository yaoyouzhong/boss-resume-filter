"""filtering 模块完整单元测试 — 覆盖 parse_experience_years、filter_candidate、
check_required_condition、_extract_city、evaluate_candidate 等核心筛选逻辑。"""

from filtering import (
    _calc_edu_bonus,
    _extract_city,
    _keyword_found,
    _parse_candidate_salary_range,
    check_required_condition,
    evaluate_candidate,
    filter_candidate,
    normalize_candidate_gender,
    normalize_gender,
    parse_experience_months,
    parse_experience_years,
)
from constants import SCORE_BASE, SCORE_SKILL_MAX, SCORE_EXP_MAX


# ========== parse_experience_years ==========

def test_parse_experience_years_arabic_numbers():
    assert parse_experience_years("3 年经验") == 3
    assert parse_experience_years("10年经验") == 10
    assert parse_experience_years("0年工作经验") == 0


def test_parse_experience_years_chinese_numbers():
    cases = {
        "一年经验": 1,
        "两年工作经验": 2,
        "三年以上 Java 经验": 3,
        "五年以上 Java 经验": 5,
        "八年后端经验": 8,
        "十年经验": 10,
        "十二年开发经验": 12,
        "十五年后端经验": 15,
        "二十年工作经验": 20,
    }
    for text, expected in cases.items():
        assert parse_experience_years(text) == expected, f"failed on: {text}"


def test_parse_experience_years_no_match_returns_none():
    assert parse_experience_years("没有年限描述") is None
    assert parse_experience_years("") is None


def test_parse_experience_years_no_cross_line_matching():
    """回归：API 提取模式下 '性别：0\\n经验：8年' 不应跨行匹配 '0\\n年' 返回 0。"""
    api_summary = "期望薪资：13-15K\n姓名：张亚星\n性别：0\n年龄：30岁\n学历：本科\n经验：8年\n期望城市：南京"
    assert parse_experience_years(api_summary) == 8

    api_summary_gender1 = "性别：1\n年龄：28岁\n经验：5年"
    assert parse_experience_years(api_summary_gender1) == 5

    # DOM 格式不受影响（无换行分隔）
    assert parse_experience_years("31岁8年本科") == 8


def test_parse_experience_years_arabic_takes_precedence():
    assert parse_experience_years("3年经验，工作过五年") == 3


def test_parse_experience_years_does_not_treat_age_as_long_experience():
    text = "12-16K\n谭听瑞\n26年\n本科\nAI Agent Java"
    assert parse_experience_years(text) is None


def test_parse_experience_years_prefers_labeled_experience_over_age_like_text():
    text = "期望薪资：15-20K\n年龄：27岁\n学历：本科\n经验：8年\n个人优势：熟悉 AI Agent"
    assert parse_experience_years(text) == 8


def test_parse_experience_years_allows_high_years_with_explicit_context():
    assert parse_experience_years("26年工作经验，熟悉 Java") == 26


# ========== _extract_city ==========

def test_extract_city_explicit_label():
    assert _extract_city("意向：南京，5 年 Java") == "南京"
    assert _extract_city("城市: 上海") == "上海"
    assert _extract_city("地点 北京") == "北京"


def test_extract_city_with_shi_suffix():
    assert _extract_city("意向：南京市") == "南京"
    assert _extract_city("城市: 深圳市") == "深圳"


def test_extract_city_mentioned_in_text():
    assert _extract_city("目前在上海工作，5 年 Java 经验") == "上海"
    assert _extract_city("现居杭州") == "杭州"


def test_extract_city_longer_name_matched_first():
    assert _extract_city("意向：哈尔滨") == "哈尔滨"


def test_extract_city_no_city_returns_empty():
    assert _extract_city("5 年 Java 开发经验") == ""
    assert _extract_city("") == ""


# ========== _parse_candidate_salary_range ==========

def test_parse_salary_range_with_k():
    assert _parse_candidate_salary_range("15-20K\n本科") == (15, 20)
    assert _parse_candidate_salary_range("15-20k\n本科") == (15, 20)


def test_parse_salary_range_separator_variants():
    assert _parse_candidate_salary_range("10~15K\n本科") == (10, 15)
    assert _parse_candidate_salary_range("10～15K\n本科") == (10, 15)
    assert _parse_candidate_salary_range("10-15K\n本科") == (10, 15)


def test_parse_salary_range_single_value():
    assert _parse_candidate_salary_range("18K\n本科") == (18, 18)


def test_parse_salary_range_negotiable():
    assert _parse_candidate_salary_range("面议\n本科") == (None, None)


def test_parse_salary_range_api_format():
    """回归：API 格式 '期望薪资：15K以上' 不再返回 (None, None)。"""
    assert _parse_candidate_salary_range("期望薪资：15K以上") == (15, 15)
    assert _parse_candidate_salary_range("期望薪资：15K") == (15, 15)
    assert _parse_candidate_salary_range("期望薪资：13-15K") == (13, 15)
    assert _parse_candidate_salary_range("期望薪资：面议") == (None, None)


def test_parse_salary_range_no_salary():
    assert _parse_candidate_salary_range("5 年 Java 经验") == (None, None)
    assert _parse_candidate_salary_range("") == (None, None)


# ========== _keyword_found ==========

def test_keyword_found_english_word_boundary():
    assert _keyword_found("AI Agent platform", "AI") is True
    assert _keyword_found("email platform", "AI") is False
    assert _keyword_found("Java developer", "Java") is True
    assert _keyword_found("JavaScript developer", "Java") is False


def test_keyword_found_chinese_substring():
    assert _keyword_found("熟悉智能体和知识库", "智能体") is True
    assert _keyword_found("熟悉大模型", "大模型") is True


def test_keyword_found_case_insensitive():
    assert _keyword_found("java developer", "Java") is True
    assert _keyword_found("SPRING BOOT", "Spring Boot") is True


def test_keyword_found_ai_agent_aliases():
    assert _keyword_found("有 Agent 开发经验", "AI Agent") is True
    assert _keyword_found("熟悉智能体应用开发", "AI Agent") is True
    assert _keyword_found("大模型Agent项目经验", "AI Agent") is True
    assert _keyword_found("普通大模型应用经验", "AI Agent") is False


# ========== _calc_edu_bonus ==========

def test_calc_edu_bonus_tiers():
    assert _calc_edu_bonus("博士学历") == 10
    assert _calc_edu_bonus("985 硕士") == 9
    assert _calc_edu_bonus("硕士") == 7
    assert _calc_edu_bonus("985 本科") == 6
    assert _calc_edu_bonus("211 本科") == 6
    assert _calc_edu_bonus("双一流 本科") == 6
    assert _calc_edu_bonus("统招本科") == 3
    assert _calc_edu_bonus("5 年工作经验") == 0


# ========== check_required_condition ==========

def test_check_required_condition_regular_bachelor():
    assert check_required_condition("统招本科，5 年 Java", "统招本科")["passed"] is True
    assert check_required_condition("全日制本科，5 年 Java", "统招本科")["passed"] is True


def test_check_required_condition_explicit_non_regular_is_rejected():
    for text in ["成教本科，5 年 Java", "自考本科，5 年 Java"]:
        result = check_required_condition(text, "统招本科")
        assert result["passed"] is False
        assert "非统招本科" in result["reason"]


def test_check_required_condition_master_satisfies_bachelor():
    assert check_required_condition("硕士，5 年 Java", "统招本科")["passed"] is True


def test_check_required_condition_985_bachelor_no_non_regular_mark():
    assert check_required_condition("985 本科，5 年 Java", "统招本科")["passed"] is True


def test_check_required_condition_or_match():
    cond = {"type": "or", "items": ["activiti", "camunda", "flowable"]}
    assert check_required_condition("有 Camunda 项目经验", cond)["passed"] is True
    assert check_required_condition("有 activiti 经验", cond)["passed"] is True
    assert check_required_condition("只有 Spring Boot", cond)["passed"] is False


def test_check_required_condition_and_match():
    cond = {"type": "and", "items": ["Java", "MySQL", "Redis"]}
    assert check_required_condition("Java MySQL Redis", cond)["passed"] is True
    assert check_required_condition("Java MySQL", cond)["passed"] is False


def test_check_required_condition_empty_items_pass():
    cond = {"type": "or", "items": []}
    assert check_required_condition("任意文本", cond)["passed"] is True


def test_check_required_condition_plain_string():
    assert check_required_condition("有 Kubernetes 经验", "Kubernetes")["passed"] is True
    assert check_required_condition("没有相关经验", "Kubernetes")["passed"] is False


def test_check_required_condition_industry_alias_bond():
    """'债券'必要条件：'固收'/'固定收益'等别名应通过"""
    assert check_required_condition("5年固收经验", "债券")["passed"] is True
    assert check_required_condition("固定收益投资", "债券")["passed"] is True
    assert check_required_condition("利率债研究", "债券")["passed"] is True
    assert check_required_condition("纯股票投资", "债券")["passed"] is False


def test_check_required_condition_industry_alias_fund():
    """'基金'必要条件：'公募'/'私募'等别名应通过"""
    assert check_required_condition("公募基金运营", "基金")["passed"] is True
    assert check_required_condition("私募股权", "基金")["passed"] is True
    assert check_required_condition("ETF产品设计", "基金")["passed"] is True
    assert check_required_condition("银行理财", "基金")["passed"] is False


def test_check_required_condition_industry_alias_quant():
    """'量化'必要条件：别名匹配"""
    assert check_required_condition("量化交易策略", "量化")["passed"] is True
    assert check_required_condition("因子模型开发", "量化")["passed"] is True
    assert check_required_condition("纯主观交易", "量化")["passed"] is False


def test_check_required_condition_or_uses_industry_aliases():
    """OR 必要条件应支持行业别名：固收满足债券方向。"""
    condition = {"type": "or", "items": ["债券", "基金", "期货", "期权"]}
    assert check_required_condition("5年固收研究经验", condition)["passed"] is True
    assert check_required_condition("商品期货策略经验", condition)["passed"] is True
    assert check_required_condition("纯股票投研", condition)["passed"] is False


def test_check_required_condition_or_uses_school_aliases():
    """985/211 OR 必要条件应按院校标记别名匹配。"""
    condition = {"type": "or", "items": ["985 院校", "211 院校"], "category": "院校背景"}
    assert check_required_condition("985 本科，5 年 Java", condition)["passed"] is True
    assert check_required_condition("211 硕士，5 年 Java", condition)["passed"] is True
    assert check_required_condition("普通本科，5 年 Java", condition)["passed"] is False


def test_check_required_condition_specialized_experience_years():
    """专项经验≥N年 条件：方向命中且年限达标才通过。"""
    assert check_required_condition("3年 Python 开发经验", "Python经验≥2年")["passed"] is True
    assert check_required_condition("1年 Python 开发经验", "Python经验≥2年")["passed"] is False
    assert check_required_condition("3年 Java 开发经验", "Python经验≥2年")["passed"] is False


# ========== filter_candidate ==========

def _java_rule(**overrides):
    rule = {
        "min_exp": 4,
        "edu": "本科",
        "work_location": "南京",
        "salary_min": 12,
        "salary_max": 15,
        "required_conditions": ["统招本科"],
        "keywords": [
            {"name": "Java", "weight": 2},
            {"name": "Spring Cloud", "weight": 2},
            {"name": "MySQL", "weight": 1},
            {"name": "Redis", "weight": 1},
        ],
    }
    rule.update(overrides)
    return rule


def test_filter_candidate_strong_passes():
    passed, score, details = filter_candidate(
        "15-16K\n南京，统招本科，6 年 Java 经验，熟悉 Spring Cloud、MySQL、Redis",
        _java_rule(),
    )
    assert passed is True
    assert score >= 75
    assert details["skill_matched_count"] == 4


def test_filter_candidate_salary_too_high_rejected():
    passed, _, details = filter_candidate(
        "18-22K\n南京，统招本科，6 年 Java 经验",
        _java_rule(),
    )
    assert passed is False
    assert "薪资不匹配" in details["reason"]


def test_filter_candidate_salary_ignores_negotiable_on_other_lines():
    """其他字段出现“面议”不应覆盖明确的期望薪资。"""
    passed, _, details = filter_candidate(
        "期望薪资：18-22K\n当前薪资：面议\n南京，统招本科，6 年 Java 经验",
        _java_rule(),
    )
    assert passed is False
    assert "薪资不匹配" in details["reason"]


def test_filter_candidate_wrong_city_rejected():
    passed, _, details = filter_candidate(
        "15-16K\n上海，统招本科，6 年 Java 经验",
        _java_rule(),
    )
    assert passed is False
    assert "地点不符" in details["reason"]


def test_filter_candidate_age_over_limit_rejected():
    rule = {"min_exp": 0, "edu": "不限", "max_age": 35, "keywords": ["Java"]}
    passed, _, details = filter_candidate("年龄：36 岁，Java 开发", rule)
    assert passed is False
    assert "年龄不符" in details["reason"]


def test_filter_candidate_age_at_limit_passes():
    rule = {"min_exp": 0, "edu": "不限", "max_age": 35, "keywords": ["Java"]}
    passed, _, _ = filter_candidate("35岁，Java 开发", rule)
    assert passed is True


def test_filter_candidate_max_age_none_means_unlimited():
    """手动清空最大年龄保存为 None 时，筛选不启用年龄限制。"""
    rule = {"min_exp": 0, "edu": "不限", "max_age": None, "keywords": ["Java"]}
    passed, _, _ = filter_candidate("年龄：99 岁，Java 开发", rule)
    assert passed is True


def test_filter_candidate_experience_insufficient():
    rule = {"min_exp": 5, "edu": "不限", "keywords": ["Java"]}
    passed, _, details = filter_candidate("3 年 Java 经验", rule)
    assert passed is False
    assert "经验不足" in details["reason"]


def test_parse_experience_months_handles_fractional_and_entry_level_cases():
    assert parse_experience_months("1年半 Java 开发经验") == 18
    assert parse_experience_months("仅有2个月实习经验") == 2
    assert parse_experience_months("2026届应届生") == 0


def test_filter_candidate_unknown_experience_requires_manual_review():
    rule = {"min_exp": 4, "edu": "不限", "keywords": ["Java"]}
    passed, _, details = filter_candidate("Java 开发，未提供工作年限", rule)
    assert passed is True
    assert details["qualification_status"] == "manual_review"
    assert details["manual_review_required"] is True
    assert details["auto_greet_blocked_reason"] == "工作经验待确认"


def test_rule_gender_normalization_only_accepts_explicit_text_values():
    assert normalize_gender("男") == "男"
    assert normalize_gender("性别：女性") == "女"
    assert normalize_gender("male") == "男"
    assert normalize_gender("F") == "女"
    assert normalize_gender("0") is None
    assert normalize_gender("1") is None
    assert normalize_gender("不限") is None


def test_candidate_gender_normalization_supports_boss_numeric_codes():
    assert normalize_candidate_gender(1) == "男"
    assert normalize_candidate_gender("性别：1") == "男"
    assert normalize_candidate_gender(0) == "女"
    assert normalize_candidate_gender("性别：0") == "女"
    assert normalize_candidate_gender(2) is None
    assert normalize_candidate_gender("不限") is None


def test_filter_candidate_gender_match_passes():
    rule = {"min_exp": 0, "edu": "不限", "gender": "女", "keywords": ["Java"]}
    passed, _, details = filter_candidate(
        "Java 开发",
        rule,
        structured_fields={"gender": "女"},
    )
    assert passed is True
    assert details["qualification_status"] == "qualified"
    assert "性别：通过，要求女，实际女" in details["score_explanation"]


def test_filter_candidate_gender_mismatch_rejected():
    rule = {"min_exp": 0, "edu": "不限", "gender": "女", "keywords": ["Java"]}
    passed, _, details = filter_candidate("性别：男\nJava 开发", rule)
    assert passed is False
    assert details["qualification_status"] == "rejected"
    assert details["reason"] == "性别不符：要求女，实际男"


def test_filter_candidate_unknown_gender_requires_manual_review():
    rule = {"min_exp": 0, "edu": "不限", "gender": "女", "keywords": ["Java"]}
    passed, _, details = filter_candidate(
        "性别：2\nJava 开发",
        rule,
        structured_fields={"gender": "2"},
    )
    assert passed is True
    assert details["qualification_status"] == "manual_review"
    assert details["manual_review_required"] is True
    assert details["auto_greet_blocked_reason"] == "性别待确认"
    assert details["risk_flags"] == [
        "性别待确认：岗位要求女，候选人性别未识别"
    ]


def test_filter_candidate_boss_numeric_gender_is_enforced():
    rule = {"min_exp": 0, "edu": "不限", "gender": "女", "keywords": ["Java"]}
    passed, _, details = filter_candidate(
        "性别：0\nJava 开发",
        rule,
        structured_fields={"gender": 0},
    )
    assert passed is True
    assert details["qualification_status"] == "qualified"

    passed, _, details = filter_candidate(
        "性别：1\nJava 开发",
        rule,
        structured_fields={"gender": 1},
    )
    assert passed is False
    assert details["reason"] == "性别不符：要求女，实际男"


def test_filter_candidate_missing_gender_rule_remains_backward_compatible():
    rule = {"min_exp": 0, "edu": "不限", "keywords": ["Java"]}
    passed, _, details = filter_candidate("性别：男\nJava 开发", rule)
    assert passed is True
    assert details["qualification_status"] == "qualified"


def test_filter_candidate_entry_level_experience_is_rejected():
    rule = {"min_exp": 4, "edu": "不限", "keywords": ["Java"]}
    passed, _, details = filter_candidate("2026届应届生，2个月实习经验，Java", rule)
    assert passed is False
    assert details["qualification_status"] == "rejected"
    assert "经验不足" in details["reason"]


def test_filter_candidate_non_regular_bachelor_is_rejected():
    rule = {"min_exp": 0, "edu": "本科", "required_conditions": ["统招本科"], "keywords": ["Java"]}
    passed, _, details = filter_candidate("专升本，5 年 Java", rule)
    assert passed is False
    assert details["qualification_status"] == "rejected"
    assert "非统招本科" in details["reason"]


def test_filter_candidate_first_degree_junior_college_then_bachelor_is_rejected():
    rule = {"min_exp": 0, "edu": "本科", "required_conditions": ["统招本科"], "keywords": ["Java"]}
    passed, _, details = filter_candidate(
        "第一学历为大专，后修本科学历，6年 Java 开发及 AI 落地经验",
        rule,
    )
    assert passed is False
    assert details["qualification_status"] == "rejected"
    assert "第一学历为大专" in details["reason"]


def test_filter_candidate_two_year_bachelor_is_rejected():
    rule = {"min_exp": 0, "edu": "本科", "required_conditions": ["统招本科"], "keywords": ["Java"]}
    passed, _, details = filter_candidate("2年制本科，9年 Java/Python 开发", rule)
    assert passed is False
    assert details["qualification_status"] == "rejected"
    assert "本科教育年限不超过3年" in details["reason"]


def test_filter_candidate_two_year_bachelor_date_range_is_rejected():
    rule = {"min_exp": 0, "edu": "本科", "required_conditions": ["统招本科"], "keywords": ["Java"]}
    passed, _, details = filter_candidate(
        "教育经历：南京大学 计算机 本科 2021 2023\n9年 Java 开发",
        rule,
    )
    assert passed is False
    assert details["qualification_status"] == "rejected"
    assert "本科教育经历不超过3年" in details["reason"]


def test_filter_candidate_three_year_bachelor_is_rejected():
    rule = {"min_exp": 0, "edu": "本科", "required_conditions": ["统招本科"], "keywords": ["Java"]}
    passed, _, details = filter_candidate("3年制本科，9年 Java 开发", rule)
    assert passed is False
    assert details["qualification_status"] == "rejected"
    assert "本科教育年限不超过3年" in details["reason"]


def test_filter_candidate_three_year_bachelor_date_range_is_rejected():
    rule = {"min_exp": 0, "edu": "本科", "required_conditions": ["统招本科"], "keywords": ["Java"]}
    passed, _, details = filter_candidate(
        "教育经历：南京大学 计算机 本科 2021 2024\n9年 Java 开发",
        rule,
    )
    assert passed is False
    assert details["qualification_status"] == "rejected"
    assert "本科教育经历不超过3年" in details["reason"]


def test_filter_candidate_four_year_bachelor_date_range_is_not_rejected_by_duration():
    rule = {"min_exp": 0, "edu": "本科", "required_conditions": ["统招本科"], "keywords": ["Java"]}
    passed, _, details = filter_candidate(
        "教育经历：南京大学 计算机 本科 2020 2024\n9年 Java 开发",
        rule,
    )
    assert passed is True
    assert details["qualification_status"] == "manual_review"


def test_filter_candidate_fulltime_bachelor_passes():
    rule = {"min_exp": 0, "edu": "本科", "required_conditions": ["统招本科"], "keywords": ["Java"]}
    passed, _, _ = filter_candidate("全日制本科，5 年 Java", rule)
    assert passed is True


def test_regular_bachelor_requires_evidence_and_explicit_non_regular_fails():
    rule = {"min_exp": 0, "edu": "本科", "required_conditions": ["统招本科"], "keywords": ["Java"]}
    passed, _, details = filter_candidate("本科，5年 Java", rule)
    assert passed is True
    assert details["qualification_status"] == "manual_review"

    passed, _, details = filter_candidate("自考本科，5年 Java", rule)
    assert passed is False
    assert "非统招本科" in details["reason"]


def test_filter_candidate_tech_conditions_or():
    rule = {
        "min_exp": 0, "edu": "不限",
        "tech_conditions": ["activiti", "camunda", "flowable"],
        "keywords": ["Java"],
    }
    passed, _, _ = filter_candidate("有 Camunda 项目经验，Java 开发", rule)
    assert passed is True

    passed, _, details = filter_candidate("没有工作流经验，Java 开发", rule)
    assert passed is False
    assert "技术不匹配" in details["reason"]


def test_filter_candidate_no_keywords_full_skill_score():
    rule = {"min_exp": 0, "edu": "不限", "keywords": []}
    passed, score, _ = filter_candidate("5 年经验", rule)
    assert passed is True
    assert score == SCORE_BASE + SCORE_SKILL_MAX


def test_filter_candidate_weighted_skill_scoring():
    rule = {
        "min_exp": 0, "edu": "不限",
        "keywords": [
            {"name": "Java", "weight": 3},
            {"name": "Python", "weight": 1},
        ],
    }
    _, score, details = filter_candidate("5 年 Java 经验", rule)
    expected_skill = int((3 / 4) * SCORE_SKILL_MAX)
    assert score == SCORE_BASE + expected_skill
    assert details["skill_matched_count"] == 1
    assert details["skill_total"] == 4


def test_filter_candidate_preferred_keywords_add_bonus_without_skill_denominator():
    rule = {
        "min_exp": 0,
        "edu": "不限",
        "keywords": [{"name": "Java", "weight": 2}],
        "preferred_keywords": [{"name": "证券", "bonus": 4}],
    }

    _, score_without_preferred, details_without = filter_candidate("5年 Java 开发", rule)
    _, score_with_preferred, details_with = filter_candidate("5年 Java 开发，证券行业经验", rule)

    assert details_without["skill_total"] == 2
    assert details_without["preferred_bonus"] == 0
    assert details_with["skill_total"] == 2
    assert details_with["preferred_matches"] == ["证券"]
    assert score_with_preferred == score_without_preferred + 4


def test_filter_candidate_returns_score_explanation_and_breakdown():
    rule = _java_rule(preferred_keywords=[{"name": "证券", "bonus": 3}])
    passed, score, details = filter_candidate(
        "15-16K\n南京，统招本科，6 年 Java 经验，熟悉 Spring Cloud、MySQL、Redis，证券行业经验",
        rule,
    )

    assert passed is True
    assert details["score_breakdown"]["total"] == score
    assert details["score_breakdown"]["base"] == SCORE_BASE
    assert details["score_breakdown"]["preferred"] == 3
    assert any("技能分" in line for line in details["score_explanation"])
    assert any("学历：通过" in line for line in details["score_explanation"])
    assert any("经验：通过" in line for line in details["score_explanation"])


def test_filter_candidate_returns_keyword_evidence_snippets():
    rule = _java_rule(preferred_keywords=[{"name": "证券", "bonus": 2}])
    passed, _, details = filter_candidate(
        "南京，统招本科，6 年 Java 经验\n项目：Spring Cloud 微服务，MySQL，证券交易系统",
        rule,
    )

    assert passed is True
    evidence = details["keyword_evidence"]
    java_item = next(item for item in evidence if item["name"] == "Java")
    preferred_item = next(item for item in evidence if item["name"] == "证券")
    assert "Java" in java_item["evidence"]
    assert "证券交易系统" in preferred_item["evidence"]
    assert preferred_item["type"] == "preferred"


def test_filter_candidate_chinese_experience_years():
    rule = {"min_exp": 3, "edu": "不限", "keywords": ["Java"]}
    passed, _, _ = filter_candidate("五年 Java 经验", rule)
    assert passed is True


def test_filter_candidate_multi_city_work_location():
    rule = {"min_exp": 0, "edu": "不限", "work_location": "南京/上海", "keywords": ["Java"]}
    passed, _, _ = filter_candidate("意向：上海，Java 开发", rule)
    assert passed is True
    passed, _, _ = filter_candidate("意向：南京，Java 开发", rule)
    assert passed is True
    passed, _, details = filter_candidate("意向：北京，Java 开发", rule)
    assert passed is False


def test_filter_candidate_multi_city_structured_fields():
    """回归：结构化多城市字段 '南京/上海' 匹配岗位要求 '上海' 应通过。"""
    rule = {"min_exp": 0, "edu": "不限", "work_location": "上海", "keywords": ["Java"]}
    candidate_text = "期望薪资：15K\n姓名：张三\n经验：5年\n年龄：28岁\n学历：本科\n期望城市：北京"
    # 无结构化字段：_extract_city 从文本取，应取到"北京"，不匹配"上海"
    passed, _, details = filter_candidate(candidate_text, rule)
    assert passed is False
    assert "地点不符" in details["reason"]
    # 结构化多城市：南京/上海 应匹配"上海"
    structured = {"city": "南京/上海"}
    passed, _, _ = filter_candidate(candidate_text, rule, structured)
    assert passed is True
    # 结构化多城市全部不匹配
    structured2 = {"city": "北京/广州"}
    passed, _, details2 = filter_candidate(candidate_text, rule, structured2)
    assert passed is False
    assert "地点不符" in details2["reason"]


def test_filter_candidate_salary_structured_fields():
    """回归：结构化薪资字段优先于文本正则解析。"""
    rule = {"min_exp": 0, "edu": "不限", "salary_min": 12, "salary_max": 15, "keywords": ["Java"]}
    candidate_text = "期望薪资：面议\nJava 开发 5年"
    # 文本说"面议"→ 跳过薪资检查 → 通过
    passed, _, _ = filter_candidate(candidate_text, rule)
    assert passed is True
    # 结构化说最低 20K → 超过岗位上限 15K → 拒绝
    structured = {"salary_min": 20, "salary_max": 25}
    passed, _, details = filter_candidate(candidate_text, rule, structured)
    assert passed is False
    assert "薪资不匹配" in details["reason"]


def test_filter_candidate_negotiable_salary_skips_check():
    rule = {"min_exp": 0, "edu": "不限", "salary_min": 12, "salary_max": 15, "keywords": ["Java"]}
    passed, _, _ = filter_candidate("面议\n5 年 Java", rule)
    assert passed is True


def test_filter_candidate_empty_rule_passes_everyone():
    """空规则意味着不检查任何条件，所有人都通过且技能满分。"""
    passed, score, _ = filter_candidate("任意文本", {})
    assert passed is True
    assert score == SCORE_BASE + SCORE_SKILL_MAX


# ========== evaluate_candidate (compatibility) ==========

def test_evaluate_candidate_returns_bool():
    rule = {"min_exp": 5, "edu": "不限", "keywords": ["Java"]}
    # keywords 只用于算分，不影响通过/淘汰；经验不足才会被淘汰
    assert evaluate_candidate("10 年 Java", rule) is True
    assert evaluate_candidate("2 年 Java", rule) is False


# ========== Score breakdown ==========

def test_score_base_only():
    rule = {"min_exp": 0, "edu": "不限", "keywords": []}
    _, score, _ = filter_candidate("任意文本", rule)
    assert score == SCORE_BASE + SCORE_SKILL_MAX


def test_score_exp_bonus_capped():
    rule = {"min_exp": 2, "edu": "不限", "keywords": []}
    _, score, details = filter_candidate("22年工作经验", rule)
    assert details["exp_bonus"] == SCORE_EXP_MAX
    assert score == SCORE_BASE + SCORE_SKILL_MAX + SCORE_EXP_MAX


def test_score_edu_bonus_added():
    # edu_bonus 只在 rule.edu != "不限" 时计算
    rule = {"min_exp": 0, "edu": "硕士", "keywords": []}
    _, score_985, d1 = filter_candidate("985 硕士", rule)
    _, score_plain, d2 = filter_candidate("普通硕士", rule)
    assert d1["edu_bonus"] == 9
    assert d2["edu_bonus"] == 7
    assert score_985 - score_plain == 2


# ========== filter_candidate 边界场景 ==========

def test_filter_candidate_salary_exact_boundary_passes():
    """候选人期望最低薪资 == 岗位最高薪资时应通过（检查条件是 >= max+1）。"""
    rule = {"min_exp": 0, "edu": "不限", "salary_min": 10, "salary_max": 15, "keywords": ["Java"]}
    # 候选人期望 15K，岗位最高 15K → 15 < 15+1 → 通过
    passed, _, _ = filter_candidate("15K\n5 年 Java", rule)
    assert passed is True

    # 候选人期望 16K，岗位最高 15K → 16 >= 16 → 淘汰
    passed, _, details = filter_candidate("16K\n5 年 Java", rule)
    assert passed is False
    assert "薪资不匹配" in details["reason"]


def test_filter_candidate_master_rejected_when_rule_requires_master():
    """要求硕士时，本科应被淘汰。"""
    rule = {"min_exp": 0, "edu": "硕士", "keywords": ["Java"]}
    passed, _, details = filter_candidate("统招本科，5 年 Java", rule)
    assert passed is False
    assert "学历不足" in details["reason"]


def test_filter_candidate_no_age_in_text_passes():
    """候选人文本中没有年龄信息时，max_age 检查应跳过（不淘汰）。"""
    rule = {"min_exp": 0, "edu": "不限", "max_age": 35, "keywords": ["Java"]}
    passed, _, _ = filter_candidate("5 年 Java 开发经验", rule)
    assert passed is True


def test_filter_candidate_keyword_with_special_chars():
    """re.escape 确保正则特殊字符不抛异常。
    \\b 对含非单词字符的关键词（如 C++）有已知局限：
    中文紧邻时 通→C 是词→词无边界，空格紧邻时 +→空格是非→非无边界。
    此测试验证函数对这些边界情况至少不抛异常。"""
    # C++ 周围都是中文或空格时，\b 均无法匹配 — 这是已知局限
    assert _keyword_found("精通C++编程", "C++") is False
    assert _keyword_found("精通 C++ 编程", "C++") is False
    # 普通英文关键词正常匹配（Java 前后都是空格/中文，\b 正常工作）
    assert _keyword_found("精通 Java 编程", "Java") is True
    # 带空格的多词英文关键词
    assert _keyword_found("熟悉 Spring Boot", "Spring Boot") is True


def test_filter_candidate_exception_returns_graceful():
    """触发异常时应返回 (False, 0, reason) 而非抛出异常。"""
    # 传入一个会触发异常的 rule（edu 为非字符串类型导致比较异常）
    # 实际上 filter_candidate 的 except 兜底了所有异常
    # 用一个会触发 max() 空序列异常的场景：edu_keywords 里没有匹配的词
    # 但 max 有 default=0 不会异常。用更直接的方式：mock 一个内部函数抛异常
    import unittest.mock as mock
    with mock.patch('filtering._calc_edu_bonus', side_effect=RuntimeError("test")):
        rule = {"min_exp": 0, "edu": "本科", "keywords": []}
        passed, score, details = filter_candidate("统招本科", rule)
    assert passed is False
    assert score == 0
    assert "筛选异常" in details["reason"]
