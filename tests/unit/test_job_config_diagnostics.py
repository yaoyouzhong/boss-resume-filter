from job_config_diagnostics import diagnose_job_config, summarize_job_config_diagnostics


def test_diagnose_empty_job_config_reports_missing_basis():
    issues = diagnose_job_config("", {})

    titles = {issue.title for issue in issues}
    assert "岗位名称为空" in titles
    assert "缺少筛选依据" in titles


def test_diagnose_salary_range_reversed_is_error():
    issues = diagnose_job_config("Java 工程师", {
        "min_exp": 3,
        "max_age": 35,
        "salary_min": 30,
        "salary_max": 20,
        "keywords": [{"name": "Java", "weight": 2}, {"name": "Spring", "weight": 2}, {"name": "MySQL", "weight": 1}],
    })

    assert any(issue.severity == "error" and issue.title == "薪资范围倒挂" for issue in issues)


def test_diagnose_keyword_overlap_and_soft_quality():
    issues = diagnose_job_config("客户成功经理", {
        "min_exp": 3,
        "keywords": [
            {"name": "SaaS", "weight": 2},
            {"name": "沟通能力", "weight": 2},
            {"name": "SQL", "weight": 1},
        ],
        "preferred_keywords": [{"name": "SaaS", "bonus": 1}],
    })

    titles = {issue.title for issue in issues}
    assert "核心技能与优先项重叠" in titles
    assert "软素质被放入关键词" in titles


def test_diagnose_keyword_count_and_shape_risks():
    issues = diagnose_job_config("平台工程师", {
        "min_exp": 3,
        "keywords": [
            {"name": "开发", "weight": 1},
            {"name": "Java/Spring/MySQL", "weight": 2},
            {"name": "负责大型分布式系统架构设计与性能优化", "weight": 2},
        ],
    })

    titles = {issue.title for issue in issues}
    assert "关键词疑似打包填写" in titles
    assert "关键词过长" in titles
    assert "核心关键词过泛" in titles


def test_diagnose_preferred_without_core_keywords():
    issues = diagnose_job_config("数据分析师", {
        "min_exp": 3,
        "preferred_keywords": [{"name": "证券行业", "bonus": 1}],
    })

    assert any(issue.title == "只有优先项没有核心技能" for issue in issues)


def test_diagnose_required_conditions_warns_for_basic_fields():
    issues = diagnose_job_config("Python 工程师", {
        "min_exp": 5,
        "keywords": [{"name": "Python", "weight": 2}, {"name": "Django", "weight": 1}, {"name": "MySQL", "weight": 1}],
        "required_conditions": ["本科", {"type": "or", "items": ["责任心强", "抗压能力强"]}],
    })

    titles = {issue.title for issue in issues}
    assert "必要条件可能重复基础字段" in titles
    assert "必要条件包含软素质" in titles


def test_diagnose_long_simple_required_condition_warns_about_strict_match():
    issues = diagnose_job_config("Java 工程师", {
        "min_exp": 5,
        "keywords": [{"name": "Java", "weight": 2}, {"name": "Spring", "weight": 1}, {"name": "MySQL", "weight": 1}],
        "required_conditions": ["具备大型互联网公司微服务架构设计经验"],
    })

    assert any(issue.title == "简单必要条件过长" for issue in issues)


def test_diagnose_long_or_item_warns_without_simple_condition_warning():
    issues = diagnose_job_config("Java 工程师", {
        "min_exp": 5,
        "keywords": [{"name": "Java", "weight": 2}, {"name": "Spring", "weight": 1}, {"name": "MySQL", "weight": 1}],
        "required_conditions": [{"type": "or", "items": ["基金行业交易系统建设经验", "债券投研平台建设经验"]}],
    })

    titles = {issue.title for issue in issues}
    assert "必要条件子项过长" in titles
    assert "简单必要条件过长" not in titles


def test_summarize_job_config_diagnostics_reports_clean_config():
    text = summarize_job_config_diagnostics("数据工程师", {
        "min_exp": 3,
        "max_age": 35,
        "salary_min": 15,
        "salary_max": 25,
        "keywords": [{"name": "Python", "weight": 2}, {"name": "SQL", "weight": 2}, {"name": "ETL", "weight": 1}],
        "original_requirement": "负责数据开发工作",
    })

    assert "配置体检通过" in text
