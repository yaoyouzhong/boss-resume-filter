"""Tests for doc_parser.parse_job_requirements() — the requirement parsing engine."""
from doc_parser import (
    parse_job_requirements, _resolve_city, _extract_work_location,
    _extract_salary_range, generate_config_from_text, _preprocess_text
)


# ========== 城市提取 ==========

def test_resolve_city_with_suffix():
    assert _resolve_city("南京市雨花区") == "南京"
    assert _resolve_city("上海市浦东") == "上海"
    assert _resolve_city("哈尔滨市") == "哈尔滨"


def test_resolve_city_without_suffix():
    assert _resolve_city("深圳南山") == "深圳"
    assert _resolve_city("成都高新区") == "成都"


def test_resolve_city_unknown_returns_empty():
    assert _resolve_city("吉林市") == ""
    assert _resolve_city("未知地区") == ""
    assert _resolve_city("") == ""


def test_extract_work_location_patterns():
    assert _extract_work_location("工作地点：南京市雨花区凯润大厦") == "南京"
    assert _extract_work_location("base：上海浦东") == "上海"
    assert _extract_work_location("坐标：成都高新区") == "成都"
    assert _extract_work_location("Base地：深圳南山") == "深圳"


def test_extract_work_location_multiple_cities():
    assert _extract_work_location("工作地点：南京/上海/杭州") == "南京/上海/杭州"
    assert _extract_work_location("base 南京，可接受上海") == "南京/上海"


def test_extract_work_location_remote_or_nationwide_disables_filter():
    assert _extract_work_location("工作地点：远程办公") == ""
    assert _extract_work_location("办公地点不限，全国远程") == ""


def test_extract_work_location_fallback_scans_full_text():
    assert _extract_work_location("我们在杭州招聘优秀人才") == "杭州"
    assert _extract_work_location("无地点信息") == ""


# ========== 职位名称提取 ==========

def test_job_title_markdown_heading():
    result = parse_job_requirements("# 高级 Java 开发工程师\n## 硬性条件\n本科")
    assert result["job_title"] == "高级 Java 开发工程师"


def test_job_title_bracket_format():
    result = parse_job_requirements("【高级 Python 工程师】\n职位要求\n本科")
    assert result["job_title"] == "高级 Python 工程师"


def test_job_title_colon_format():
    result = parse_job_requirements("岗位：全栈工程师\n职位要求\n本科")
    assert result["job_title"] == "全栈工程师"


def test_job_title_default_when_not_found():
    result = parse_job_requirements("需要3年以上开发经验，熟悉Java")
    assert result["job_title"] == "Java 工程师"


# ========== 经验提取 ==========

def test_experience_explicit_field():
    result = parse_job_requirements("## 硬性条件\n工作年限：5 年以上")
    assert result["min_exp"] == 5


def test_experience_range_takes_minimum():
    result = parse_job_requirements("## 硬性条件\n工作年限：3-5 年")
    assert result["min_exp"] == 3


def test_experience_markdown_bold():
    result = parse_job_requirements("## 硬性条件\n**工作年限**：5 年以上")
    assert result["min_exp"] == 5


def test_experience_from_general_text():
    result = parse_job_requirements("职位要求\n3 年以上 Java 开发经验")
    assert result["min_exp"] == 3


def test_experience_default_zero():
    result = parse_job_requirements("招聘开发人员")
    assert result["min_exp"] == 0


# ========== 学历提取 ==========

def test_education_explicit_field():
    result = parse_job_requirements("## 硬性条件\n最低学历：本科")
    assert result["edu"] == "本科"


def test_education_master():
    result = parse_job_requirements("## 硬性条件\n学历要求：硕士")
    assert result["edu"] == "硕士"


def test_education_phd_priority_excluded():
    """博士优先 不应被当作最低学历门槛"""
    result = parse_job_requirements("## 硬性条件\n本科，博士优先")
    assert result["edu"] == "本科"


def test_education_from_low_to_high():
    """从低到高判断：大专 < 本科 < 硕士 < 博士"""
    result = parse_job_requirements("## 硬性条件\n大专以上")
    assert result["edu"] == "大专"


def test_education_default_unlimited():
    result = parse_job_requirements("招聘开发人员")
    assert result["edu"] == "不限"


# ========== 必要条件提取 ==========

def test_required_conditions_tongzhao():
    result = parse_job_requirements("## 硬性条件\n统招本科，5 年经验")
    assert "统招本科" in result["required_conditions"]


def test_required_conditions_quanrizhi():
    result = parse_job_requirements("## 硬性条件\n全日制本科")
    assert "全日制" in result["required_conditions"]


def test_required_conditions_985_211():
    result = parse_job_requirements("## 硬性条件\n985/211 院校")
    school_or = next((c for c in result["required_conditions"] if isinstance(c, dict) and c.get("category") == "院校背景"), None)
    assert school_or is not None
    assert school_or["type"] == "or"
    assert "985 院校" in school_or["items"]
    assert "211 院校" in school_or["items"]


def test_education_985_211_preferred_not_required():
    config = generate_config_from_text("## 硬性条件\n本科，985/211 优先", merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    assert job["edu"] == "本科"
    assert not any(
        isinstance(c, dict) and c.get("category") == "院校背景"
        for c in job["required_conditions"]
    )
    preferred_names = [k["name"] for k in job["preferred_keywords"]]
    assert "985 院校" in preferred_names
    assert "211 院校" in preferred_names


def test_education_preferred_school_keeps_hard_regular_bachelor_without_markdown():
    config = generate_config_from_text("任职资格\n统招本科，985/211 优先", merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    assert job["edu"] == "本科"
    assert "统招本科" in job["required_conditions"]
    preferred_names = [k["name"] for k in job["preferred_keywords"]]
    assert "985 院校" in preferred_names
    assert "211 院校" in preferred_names


def test_required_conditions_age_limit():
    result = parse_job_requirements("## 硬性条件\n年龄 35 岁以下")
    assert result["max_age"] == 35
    assert "年龄≤35岁" in result["required_conditions"]


def test_required_conditions_shuangzheng():
    result = parse_job_requirements("## 硬性条件\n双证齐全")
    assert "双证齐全" in result["required_conditions"]


# ========== 技术条件关键词 ==========

def test_tech_conditions_extracted_from_required_section():
    text = "## 硬性条件\n必须熟悉 Java 或 Python"
    result = parse_job_requirements(text)
    tech_or = next((c for c in result["required_conditions"] if isinstance(c, dict) and c.get("category") == "技术硬性条件"), None)
    assert tech_or is not None
    assert tech_or["type"] == "or"
    assert "Java" in tech_or["items"] or "Python" in tech_or["items"]


def test_tech_conditions_spring_ecosystem():
    text = "## 硬性条件\nSpring Cloud 微服务经验"
    result = parse_job_requirements(text)
    assert any("Spring" in tc for tc in result["tech_conditions"])


def test_hard_line_or_condition_from_slash_or_hint():
    text = "任职资格\n必须熟悉 Java/Python 至少一种"
    config = generate_config_from_text(text, merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    tech_or = next((c for c in job["required_conditions"] if isinstance(c, dict) and c.get("category") == "技术硬性条件"), None)
    assert tech_or is not None
    assert tech_or["type"] == "or"
    assert "Java" in tech_or["items"]
    assert "Python" in tech_or["items"]


def test_hard_line_and_condition_from_simultaneous_hint():
    text = "任职资格\n必须同时具备 Java 和 MySQL 经验"
    config = generate_config_from_text(text, merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    tech_and = next((c for c in job["required_conditions"] if isinstance(c, dict) and c.get("category") == "技术硬性条件"), None)
    assert tech_and is not None
    assert tech_and["type"] == "and"
    assert "Java" in tech_and["items"]
    assert "MySQL" in tech_and["items"]


def test_generated_config_moves_tech_conditions_to_required_or_only():
    text = "## 硬性条件\n必须熟悉 Java 或 Python"
    config = generate_config_from_text(text, merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    required = job["required_conditions"]
    tech_or = next((c for c in required if isinstance(c, dict) and c.get("category") == "技术硬性条件"), None)
    assert tech_or is not None
    assert tech_or["type"] == "or"
    assert "Java" in tech_or["items"] or "Python" in tech_or["items"]
    assert "tech_conditions" not in job


# ========== 软技能提取 ==========

def test_soft_skills_basic_extraction():
    text = "职位描述\n熟悉 Java、Spring Boot、MySQL、Redis"
    result = parse_job_requirements(text)
    skill_names = [s for s in result["soft_skills"]]
    assert "Java" in skill_names
    assert "MySQL" in skill_names
    assert "Redis" in skill_names


def test_soft_skills_spring_cloud_normalized():
    text = "职位描述\n精通 Spring Cloud 微服务"
    result = parse_job_requirements(text)
    skill_names_lower = [s.lower() for s in result["soft_skills"]]
    assert "spring cloud" in skill_names_lower


def test_soft_skills_deduplication():
    text = "职位描述\nJava 开发，Java 经验"
    result = parse_job_requirements(text)
    java_count = sum(1 for s in result["soft_skills"] if s.lower() == "java")
    assert java_count == 1


def test_soft_skills_ai_keywords():
    text = "职位描述\nLLM 大模型开发，RAG 知识库"
    result = parse_job_requirements(text)
    skill_names_lower = [s.lower() for s in result["soft_skills"]]
    assert "llm" in skill_names_lower
    assert "rag" in skill_names_lower


def test_skill_aliases_are_canonicalized():
    text = "职位要求\n熟悉 K8s、Postgres、智能体开发"
    result = parse_job_requirements(text)
    assert "Kubernetes" in result["soft_skills"]
    assert "PostgreSQL" in result["soft_skills"]
    assert "AI Agent" in result["soft_skills"]


# ========== 薪资范围 ==========

def test_salary_range_extracted():
    text = "薪资范围：15k-25k\n职位要求\n本科"
    result = parse_job_requirements(text)
    assert result["salary_min"] == 15
    assert result["salary_max"] == 25


def test_salary_negotiable_returns_none():
    text = "薪资面议\n职位要求\n本科"
    result = parse_job_requirements(text)
    assert result["salary_min"] is None
    assert result["salary_max"] is None


# ========== 综合集成测试 ==========

def test_full_requirement_parsing():
    """完整的招聘需求文本解析"""
    text = """# 高级 Java 开发工程师

职位描述
负责公司核心系统的后端开发，使用 Spring Cloud 微服务架构。
需要熟悉 MySQL、Redis、Kafka。

职位要求
1. 5 年以上 Java 开发经验
2. 精通 Spring Cloud、Spring Boot
3. 熟悉 Docker、Kubernetes
4. 有 LLM、大模型经验优先

## 硬性条件
**工作年限**：5 年以上
**最低学历**：本科
统招本科，985/211 优先
年龄 35 岁
工作地点：南京

薪资范围：20k-35k
"""
    result = parse_job_requirements(text)

    assert result["job_title"] == "高级 Java 开发工程师"
    assert result["min_exp"] == 5
    assert result["edu"] == "本科"
    assert result["work_location"] == "南京"
    assert result["salary_min"] == 20
    assert result["salary_max"] == 35
    assert result["max_age"] == 35
    assert "统招本科" in result["required_conditions"]

    # 软技能应包含主要技术栈
    skill_names_lower = [s.lower() for s in result["soft_skills"]]
    assert "java" in skill_names_lower
    assert "mysql" in skill_names_lower


def test_empty_text_returns_defaults():
    result = parse_job_requirements("")
    assert result["job_title"] == "Java 工程师"
    assert result["min_exp"] == 0
    assert result["edu"] == "不限"
    assert result["soft_skills"] == []
    assert result["required_conditions"] == []


def test_markdown_format_full():
    """纯 markdown 格式的招聘需求"""
    text = """# Python 后端工程师

## 硬性条件

### 工作经验
3 年以上 Python 开发

### 学历要求
本科

## 软性条件
- 熟悉 Django、Flask
- 了解 Docker
"""
    result = parse_job_requirements(text)
    assert result["job_title"] == "Python 后端工程师"
    assert result["min_exp"] == 3
    assert result["edu"] == "本科"


# ========== P0 薪资解析增强 ==========

def test_salary_no_label_range():
    """无标签但有薪资上下文：15-25K"""
    assert _extract_salary_range("我们提供15-25K的薪资") == (15, 25)


def test_salary_zhi_separator():
    """薪资：15k至25k"""
    assert _extract_salary_range("薪资：15k至25k") == (15, 25)


def test_salary_annual_range():
    """年薪20-35万 → 转月薪"""
    result = _extract_salary_range("年薪20-35万")
    assert result[0] == 20 * 10 // 12  # 16
    assert result[1] == 35 * 10 // 12  # 29


def test_salary_annual_single():
    """年薪30万 → 转月薪"""
    result = _extract_salary_range("年薪30万")
    assert result[0] == 30 * 10 // 12  # 25
    assert result[1] == 25


def test_salary_no_k_suffix():
    """薪资：15000-25000（无K后缀）"""
    assert _extract_salary_range("薪资：15000-25000") == (15000, 25000)


def test_salary_min_only_qi():
    """15K起"""
    assert _extract_salary_range("15K起") == (15, None)


def test_salary_min_only_yishang():
    """15000以上"""
    assert _extract_salary_range("15000以上") == (15000, None)


def test_salary_budi():
    """不低于15K"""
    assert _extract_salary_range("不低于15K") == (15, None)


def test_salary_miantan():
    """面谈"""
    assert _extract_salary_range("薪资面谈") == (None, None)


def test_salary_daiyu_congyou():
    """待遇从优"""
    assert _extract_salary_range("待遇从优") == (None, None)


def test_salary_xinzi_miantan():
    """薪资可谈"""
    assert _extract_salary_range("薪资可谈") == (None, None)


def test_salary_xinchou_prefix():
    """薪酬：15k-25k"""
    assert _extract_salary_range("薪酬：15k-25k") == (15, 25)


def test_salary_no_false_positive_experience():
    """不应把经验年限误匹配为薪资"""
    assert _extract_salary_range("要求3-5年经验") == (None, None)


# ========== P0 年龄提取增强 ==========

def test_age_yixia():
    """35岁以下"""
    result = parse_job_requirements("## 硬性条件\n35岁以下")
    assert result["max_age"] == 35


def test_age_buchaoguo():
    """不超过40岁"""
    result = parse_job_requirements("## 硬性条件\n不超过40岁")
    assert result["max_age"] == 40


def test_age_zhousui():
    """年龄不超过 35 周岁"""
    result = parse_job_requirements("## 硬性条件\n年龄不超过 35 周岁")
    assert result["max_age"] == 35


def test_age_zhousui_yinei():
    """35周岁以内"""
    result = parse_job_requirements("## 硬性条件\n35周岁以内")
    assert result["max_age"] == 35


def test_age_yinei_suffix():
    """40 岁以内"""
    result = parse_job_requirements("## 硬性条件\n40 岁以内")
    assert result["max_age"] == 40


def test_age_le_symbol():
    """≤35岁"""
    result = parse_job_requirements("## 硬性条件\n≤35岁")
    assert result["max_age"] == 35


def test_age_le_ascii():
    """<=35岁"""
    result = parse_job_requirements("## 硬性条件\n<=35岁")
    assert result["max_age"] == 35


def test_age_range_takes_max():
    """年龄25-35岁 → 取上限"""
    result = parse_job_requirements("## 硬性条件\n年龄25-35岁")
    assert result["max_age"] == 35


def test_age_nianling_yixia_no_sui():
    """年龄35以下（无岁字）"""
    result = parse_job_requirements("## 硬性条件\n年龄35以下")
    assert result["max_age"] == 35


def test_age_no_false_positive_experience():
    """不应把经验年限误匹配为年龄"""
    result = parse_job_requirements("## 硬性条件\n5年经验")
    assert result["max_age"] is None


def test_generated_config_defaults_max_age_to_none_when_missing():
    """岗位解析未明确年龄上限时，配置不限制年龄（None）。"""
    config = generate_config_from_text("职位要求\n本科\n3年以上Java经验", merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    assert job["max_age"] is None


def test_generated_config_keeps_explicit_max_age():
    """岗位解析有明确年龄上限时，配置使用原文限制。"""
    config = generate_config_from_text("职位要求\n本科\n年龄40岁以内\n3年以上Java经验", merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    assert job["max_age"] == 40


# ========== P0 权重正则空格修复 ==========

def test_skill_weight_no_space_chinese():
    """中文无空格：Spring熟悉 → 权重 2"""
    text = "职位要求\nSpring熟悉\nDocker了解"
    config = generate_config_from_text(text, merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    spring_kw = next((k for k in job["keywords"] if "Spring" in k["name"] and "Cloud" not in k["name"]
                       and "Boot" not in k["name"] and "MVC" not in k["name"]
                       and "AI" not in k["name"]), None)
    assert spring_kw is not None
    assert spring_kw["weight"] == 2


def test_skill_weight_youxian_no_space():
    """Java优先 → 优先项 bonus 2，不进入普通关键词分母"""
    text = "职位要求\nJava优先"
    config = generate_config_from_text(text, merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    java_kw = next((k for k in job["preferred_keywords"] if k["name"] == "Java"), None)
    assert java_kw is not None
    assert java_kw["bonus"] == 2
    assert not any(k["name"] == "Java" for k in job["keywords"])


def test_skill_weight_jingtong_no_space():
    """精通MySQL → 权重 3"""
    text = "职位要求\n精通MySQL"
    config = generate_config_from_text(text, merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    mysql_kw = next((k for k in job["keywords"] if k["name"] == "MySQL"), None)
    assert mysql_kw is not None
    assert mysql_kw["weight"] == 3


def test_skill_weight_same_line_youxian():
    """同行"优先"关键词：有 Redis 经验优先 → preferred_keywords"""
    text = "职位要求\n有 Redis 经验优先"
    config = generate_config_from_text(text, merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    redis_kw = next((k for k in job["preferred_keywords"] if k["name"] == "Redis"), None)
    assert redis_kw is not None
    assert redis_kw["bonus"] == 2


def test_golden_sample_mid_senior_ai_engineer_template():
    """Golden sample: 中高级 AI 工程师，局部优先项不污染整行技能。"""
    text = """职位描述【中高级AI工程师】：
1. Java/Python基础扎实，具有实际开发经验，能够独立完成开发任务；
2. 熟悉mysql、oracle其中一种数据库的使用及优化；
3. 熟练使用Spring Cloud、Dubbo或类似的微服务框架,Dubbo优先；
4. 熟练使用SpringBoot/Spring Mvc/Mybatis等常用java框架；
5. 了解缓存技术Redis、消息中间件Kafka；
6. 熟悉activiti、camunda、flowable等相关技术；
7. 有AI开发背景（LLM、Al Agent、智能体、Spring AI、Langchain、智能问答、知识库）的优先；

职位要求：
1. 4-10年工作经验
2. 本科学历
3. 薪资范围：12k-15k
4. 工作地点：南京市雨花区凯润大厦2号楼5层

必要条件（硬性约束）：
1. 具有4年以上工作经验
2. 统招本科学历；"""

    config = generate_config_from_text(text, merge_existing=False)
    job = config["job_requirements"]["中高级AI工程师"]
    keywords = {item["name"]: item["weight"] for item in job["keywords"]}
    preferred = {item["name"]: item["bonus"] for item in job["preferred_keywords"]}

    assert job["min_exp"] == 4
    assert job["edu"] == "本科"
    assert job["salary_min"] == 12
    assert job["salary_max"] == 15
    assert job["work_location"] == "南京"
    assert "统招本科" in job["required_conditions"]

    assert keywords["Java"] == 2
    assert keywords["Python"] == 1
    assert keywords["MySQL"] == 2
    assert keywords["Oracle"] == 2
    assert keywords["Spring Cloud"] == 2
    assert keywords["Spring Boot"] == 2
    assert keywords["Spring MVC"] == 2
    assert keywords["MyBatis"] == 2
    assert keywords["activiti"] == 2
    assert keywords["camunda"] == 2
    assert keywords["flowable"] == 2

    assert "LangChain" in preferred
    assert "AI Agent" in preferred
    assert "Spring AI" in preferred
    assert "Dubbo" in preferred
    assert "Spring Cloud" not in preferred
    assert "微服务" not in preferred
    assert "Langchain" not in preferred
    assert "Al Agent" not in preferred


# ========== 死代码清理 ==========

def test_dead_code_removed():
    """experience_keywords 和 education_keywords 字典已删除"""
    import doc_parser
    import inspect
    src = inspect.getsource(doc_parser.parse_job_requirements)
    assert 'experience_keywords' not in src
    assert 'education_keywords' not in src


# ========== 职位名称后缀扩展 ==========

def test_job_title_yanfa():
    """资深后端研发"""
    result = parse_job_requirements("岗位：资深后端研发\n职位要求\n本科")
    assert result["job_title"] == "资深后端研发"


def test_job_title_fuzeren():
    """技术负责人"""
    result = parse_job_requirements("招聘：技术负责人\n职位要求\n本科")
    assert result["job_title"] == "技术负责人"


def test_job_title_analyst():
    """数据分析师"""
    result = parse_job_requirements("职位：数据分析师\n职位要求\n本科")
    assert result["job_title"] == "数据分析师"


def test_job_title_dba():
    """DBA"""
    result = parse_job_requirements("岗位名称：DBA\n职位要求\n本科")
    assert result["job_title"] == "DBA"


def test_job_title_yunwei():
    """运维"""
    result = parse_job_requirements("诚聘：高级运维\n职位要求\n本科")
    assert result["job_title"] == "高级运维"


def test_job_title_ceishi():
    """测试"""
    result = parse_job_requirements("招聘：自动化测试\n职位要求\n本科")
    assert result["job_title"] == "自动化测试"


# ========== 技能子串误匹配防护 ==========

def test_skill_go_no_false_match_google():
    """Go 不应匹配 Google"""
    result = parse_job_requirements("职位要求\n使用 Google Cloud 服务")
    skill_names_lower = [s.lower() for s in result["soft_skills"]]
    assert "go" not in skill_names_lower


def test_skill_go_real_match():
    """Go 应该匹配真实的 Go 技能"""
    result = parse_job_requirements("职位要求\n熟悉 Go 语言开发")
    skill_names_lower = [s.lower() for s in result["soft_skills"]]
    assert "go" in skill_names_lower


def test_skill_csharp_no_false_match():
    """C# 不应匹配 CSharpDoc"""
    result = parse_job_requirements("职位要求\n使用 CSharpDoc 生成文档")
    # C# 应不在结果中（CSharpDoc 不是 C#）
    skill_names_lower = [s.lower() for s in result["soft_skills"]]
    assert "c#" not in skill_names_lower


def test_skill_java_in_chinese_context():
    """Java 在中文上下文中应正常匹配（中文旁无空格）"""
    result = parse_job_requirements("职位要求\n精通Java开发")
    skill_names_lower = [s.lower() for s in result["soft_skills"]]
    assert "java" in skill_names_lower


# ========== 学历语境排除补全 ==========

def test_education_youboshi_exclude():
    """有博士学历者加分 不应被当作最低学历门槛"""
    result = parse_job_requirements("## 硬性条件\n本科，有博士学历者加分")
    assert result["edu"] == "本科"


def test_education_boshi_xueli_youxian_exclude():
    """博士学历优先 不应被当作最低学历门槛"""
    result = parse_job_requirements("## 硬性条件\n本科，博士学历优先")
    assert result["edu"] == "本科"


def test_education_shuoshi_xueli_youxian_exclude():
    """硕士学历优先 不应被当作最低学历门槛"""
    result = parse_job_requirements("## 硬性条件\n本科，硕士学历优先")
    assert result["edu"] == "本科"


def test_education_boshi_xueli_zhe_exclude():
    """博士学历者 不应被当作最低学历门槛"""
    result = parse_job_requirements("## 硬性条件\n本科，博士学历者优先考虑")
    assert result["edu"] == "本科"


# ========== P3: 全角/emoji 预处理 ==========

def test_preprocess_fullwidth_digits():
    """全角数字转半角"""
    assert '15' in _preprocess_text('薪资１５Ｋ')


def test_preprocess_fullwidth_letters():
    """全角英文转半角"""
    assert 'Java' in _preprocess_text('精通Ｊａｖａ开发')


def test_preprocess_emoji_stripped():
    """emoji 被移除"""
    result = _preprocess_text('✅ Java ✅ Python ✅ MySQL')
    assert '✅' not in result
    assert 'Java' in result


def test_preprocess_zero_width_stripped():
    """零宽字符被移除"""
    # ​ = zero-width space
    result = _preprocess_text('Java​开发')
    assert result == 'Java开发'


def test_preprocess_fullwidth_salary_parses():
    """全角薪资格式能被正确解析"""
    result = _extract_salary_range('薪资：１５Ｋ－２５Ｋ')
    assert result == (15, 25)


def test_preprocess_emoji_in_requirement():
    """含 emoji 的招聘需求仍能解析"""
    text = "## 硬性条件\n工作年限：3 年\n✅ 本科\n最低学历：本科"
    result = parse_job_requirements(text)
    assert result["edu"] == "本科"
    assert result["min_exp"] == 3


# ========== P1: 经验提取增强（中文数字 + 至少/不低于）==========

def test_experience_chinese_san_nian():
    """三年以上"""
    result = parse_job_requirements("## 硬性条件\n三年以上开发经验")
    assert result["min_exp"] == 3


def test_experience_chinese_wu_nian():
    """五年以上"""
    result = parse_job_requirements("## 硬性条件\n五年以上Java开发经验")
    assert result["min_exp"] == 5


def test_experience_chinese_liang_nian():
    """两年以上"""
    result = parse_job_requirements("## 硬性条件\n两年以上")
    assert result["min_exp"] == 2


def test_experience_zhishao():
    """至少3年"""
    result = parse_job_requirements("## 硬性条件\n至少3年相关经验")
    assert result["min_exp"] == 3


def test_experience_budiyu():
    """不低于5年"""
    result = parse_job_requirements("## 硬性条件\n不低于5年开发经验")
    assert result["min_exp"] == 5


def test_experience_bushaoyu():
    """不少于3年"""
    result = parse_job_requirements("## 硬性条件\n不少于3年")
    assert result["min_exp"] == 3


def test_experience_juyou():
    """具有5年以上"""
    result = parse_job_requirements("## 硬性条件\n具有5年以上相关经验")
    assert result["min_exp"] == 5


def test_experience_chinese_zhishao():
    """至少三年年（中文数字+至少）"""
    result = parse_job_requirements("## 硬性条件\n至少三年开发经验")
    assert result["min_exp"] == 3


def test_specialized_experience_conditions_are_extracted():
    text = "任职资格\n5年以上工作经验，其中3年以上金融行业经验，2年以上Python经验"
    config = generate_config_from_text(text, merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    assert job["min_exp"] == 5
    special = [c for c in job["required_conditions"] if isinstance(c, dict) and c.get("category") == "专项经验"]
    flat_items = [item for cond in special for item in cond["items"]]
    assert "金融行业经验≥3年" in flat_items
    assert "Python经验≥2年" in flat_items


# ========== P1: 段落分离增强（非标准标题词）==========

def test_section_gangwei_zhize():
    """'岗位职责' 替代 '职位描述'"""
    text = "岗位职责\n负责后端开发\n\n任职要求\n3年以上Java经验"
    result = parse_job_requirements(text)
    assert result["min_exp"] == 3


def test_section_renzhi_yaoqiu():
    """'任职要求' 替代 '职位要求'"""
    text = "职位描述\n做后端\n\n任职要求\n5年以上经验"
    result = parse_job_requirements(text)
    assert result["min_exp"] == 5


def test_section_gangwei_yaoqiu():
    """'岗位要求' 替代 '职位要求'"""
    text = "岗位职责\n写代码\n\n岗位要求\n3年以上"
    result = parse_job_requirements(text)
    assert result["min_exp"] == 3


def test_section_yingpin_yaoqiu():
    """'应聘要求' 替代 '职位要求'"""
    text = "工作内容\n开发系统\n\n应聘要求\n5年经验"
    result = parse_job_requirements(text)
    assert result["min_exp"] == 5


def test_section_nengli_yaoqiu():
    """'能力要求' 替代 '职位要求'"""
    text = "主要职责\n后端开发\n\n能力要求\n3年以上经验"
    result = parse_job_requirements(text)
    assert result["min_exp"] == 3


def test_section_yingxing_yaoqiu():
    """'硬性要求' 替代 '硬性条件'（markdown 格式）"""
    text = "# Java工程师\n\n## 硬性要求\n工作年限：5 年\n最低学历：本科"
    result = parse_job_requirements(text)
    assert result["min_exp"] == 5
    assert result["edu"] == "本科"


def test_section_bibe_tiaojian():
    """'必备条件' 替代 '硬性条件'"""
    text = "# Python工程师\n\n## 必备条件\n工作年限：3 年"
    result = parse_job_requirements(text)
    assert result["min_exp"] == 3


def test_section_jiafen_xiang():
    """'加分项' 作为软性条件标题"""
    text = "# Java工程师\n\n## 硬性条件\n本科\n\n## 加分项\n有AI经验优先"
    result = parse_job_requirements(text)
    assert result["edu"] == "本科"


def test_section_women_xiwang():
    """'我们希望你' 替代 '职位要求'"""
    text = "岗位职责\n做开发\n\n我们希望你\n有3年以上经验"
    result = parse_job_requirements(text)
    assert result["min_exp"] == 3


# ========== P2: 地点兜底误匹配修复 ==========

def test_location_hq_excluded_fallback():
    """总部城市不应被兜底匹配为工作地点"""
    text = "公司总部在上海\n我们在成都招聘Java工程师"
    result = _extract_work_location(text)
    assert result == "成都"


def test_location_hq_with_explicit_workplace():
    """有明确工作地点时，总部城市不影响"""
    text = "公司总部在北京\n工作地点：深圳南山"
    result = _extract_work_location(text)
    assert result == "深圳"


def test_location_work_city_keyword():
    """'工作城市' 关键词"""
    assert _extract_work_location("工作城市：杭州") == "杭州"


def test_location_office_keyword():
    """'办公地' 关键词"""
    assert _extract_work_location("办公地：武汉光谷") == "武汉"


def test_location_multiple_cities_first_non_hq():
    """多城市文本，排除总部后取招聘城市"""
    text = "总部在广州\n本岗位base杭州\n欢迎加入"
    result = _extract_work_location(text)
    assert result == "杭州"


# ========== P2: 证券固收分析师场景 ==========

def test_title_fallback_analyst():
    """无标准前缀时，首行含"分析师"应被识别为岗位名"""
    text = "证券固收业务python分析师\n岗位职责\n负责量化系统开发"
    result = parse_job_requirements(text)
    assert result["job_title"] == "证券固收业务python分析师"


def test_title_fallback_strips_numbered_prefix():
    """岗位1: 前缀不应进入岗位名称"""
    text = "岗位1:证券固收业务python分析师\n岗位职责\n负责量化系统开发"
    result = parse_job_requirements(text)
    assert result["job_title"] == "证券固收业务python分析师"


def test_title_fallback_no_false_positive_on_kai_fa():
    """含"开发"的正文行不应触发兜底（开发是动词性词尾）"""
    result = parse_job_requirements("需要3年以上开发经验，熟悉Java")
    assert result["job_title"] == "Java 工程师"


def test_title_fallback_researcher():
    """"量化策略研究员" 应被兜底匹配"""
    result = parse_job_requirements("量化策略研究员\n职位要求\n本科")
    assert result["job_title"] == "量化策略研究员"


def test_salary_multi_month_format():
    """多月薪格式 20-35K·15薪"""
    assert _extract_salary_range("薪资：20-35K·15薪") == (20, 35)
    assert _extract_salary_range("薪资范围：20-35k·16薪") == (20, 35)


def test_salary_multi_month_lowercase_k():
    """多月薪格式小写k"""
    assert _extract_salary_range("薪资：15k-25k·14薪") == (15, 25)


def test_tech_skills_sql_agent_crawler():
    """SQL、Agent、爬虫应被识别为技能关键词，Agent 归一为 AI Agent。"""
    text = "职位要求\n熟悉SQL数据库\n有Agent开发经验\n会爬虫技术"
    result = parse_job_requirements(text)
    skill_names_lower = [s.lower() for s in result["soft_skills"]]
    assert "sql" in skill_names_lower
    assert "ai agent" in skill_names_lower
    assert "agent" not in skill_names_lower
    assert "爬虫" in result["soft_skills"]


def test_required_condition_certification():
    """证券从业资格应被识别为必要条件"""
    text = "职位要求\n本科\n必要条件\n1. 具有证券从业资格\n2. 3年以上经验"
    result = parse_job_requirements(text)
    assert "证券从业资格" in result["required_conditions"]


def test_required_condition_cfa():
    """CFA 应被识别为必要条件"""
    text = "必要条件\nCFA持证者优先\n本科"
    result = parse_job_requirements(text)
    assert "CFA" in result["required_conditions"]


def test_bonus_keywords_from_youxi_lines():
    """"有证券行业经验者优先" 应提取"证券"为优先加分项"""
    text = "职位描述【证券固收分析师】\n岗位职责\n数据分析\n\n有证券行业经验者优先\n有固收经验优先"
    config = generate_config_from_text(text, merge_existing=False)
    job_title = list(config["job_requirements"].keys())[0]
    keywords = config["job_requirements"][job_title]["preferred_keywords"]
    keyword_names_lower = [k["name"].lower() for k in keywords]
    assert "证券" in keyword_names_lower or any("证券" in name for name in keyword_names_lower)


def test_preferred_experience_phrase_fullstack_and_dba():
    """有 X 经验优先 应把 X 提取为优先加分项"""
    text = (
        "3.有Python语言开发经验；精通python语言，有全栈开发经验优先。\n"
        "4.熟练掌握sql处理数据经验；精通数据库技术、有数据库运维经验优先。"
    )
    config = generate_config_from_text(text, merge_existing=False)
    job = list(config["job_requirements"].values())[0]
    preferred_names = [k["name"] for k in job["preferred_keywords"]]
    assert "全栈开发" in preferred_names
    assert "数据库运维" in preferred_names


def test_securities_fixed_income_python_analyst_real_template():
    """真实固收 Python 分析师模板：数据源/业务方向不应污染技能关键词。"""
    text = """岗位1：证券固收业务python分析师
一、岗位职责：
1.数据分析，数据挖掘，将市场多种来源数据处理成投研需要的数据类型及因子结果。
2.使用数据开发工具处理数据，实现业务逻辑和数据处理，形成报表。
3.使用git对项目代码进行管理。
4.使用python获取数据库、万得API、彭博等数据，对数据进行清洗加工。
5.通过爬虫等技术获取数据，使用大模型AI技术进行代码优化
二、任职要求：
1.本科及以上学历，三年以上相关工作经验。
2.有证券行业从业经验者优先。
3.有Python语言开发经验；精通python语言，有全栈开发经验优先。
4.熟练掌握sql处理数据经验；精通数据库技术、有数据库运维经验优先。
5.有使用大模型Agent经验优先；
6.具备较强的服务意识和团队精神，较强的学习能力和执行能力。
三、薪资范围：中级：14K-17K 高级：18K-22K
四、工作地点：北京市丰台区西营街青海大厦银河证券

必要条件（硬性约束）
1、统招本科及以上学历，3年以上工作经验
2、金融投资行业经验，债券、基金、期货、期权等
"""
    config = generate_config_from_text(text, merge_existing=False)
    job = config["job_requirements"]["证券固收业务python分析师"]

    keyword_names = [k["name"] for k in job["keywords"]]
    preferred_names = [k["name"] for k in job["preferred_keywords"]]

    assert job["min_exp"] == 3
    assert job["edu"] == "本科"
    assert job["work_location"] == "北京"
    assert job["salary_min"] == 14
    assert job["salary_max"] == 22

    assert "Python" in keyword_names
    assert "SQL" in keyword_names
    assert "API" not in keyword_names
    assert "AI" not in keyword_names
    assert "人工智能" not in keyword_names
    assert "Wind" not in keyword_names
    assert "Bloomberg" not in keyword_names
    assert "固收" not in keyword_names

    assert "证券" in preferred_names
    assert "全栈开发" in preferred_names
    assert "数据库运维" in preferred_names
    assert "AI Agent" in preferred_names
    assert "大模型 Agent" not in preferred_names
    assert "Agent" not in preferred_names
    assert "大模型" not in preferred_names
    assert all(not name.startswith(("2.", "5.")) for name in preferred_names)

    assert "统招本科" in job["required_conditions"]
    finance_or = next(
        c for c in job["required_conditions"]
        if isinstance(c, dict) and c.get("category") == "金融投资行业经验"
    )
    assert finance_or["type"] == "or"
    assert "债券" in finance_or["items"]
    assert "基金" in finance_or["items"]
    assert "期货" in finance_or["items"]
    assert "期权" in finance_or["items"]
    assert "量化" not in finance_or["items"]
    assert "证券" not in finance_or["items"]


# ========== P2: 多等级薪资合并解析 ==========

def test_salary_multi_level_ranges():
    """薪资范围：中级：14K-17K 高级：18K-22K → 取全局 min/max = 14-22K"""
    assert _extract_salary_range("薪资范围：中级：14K-17K 高级：18K-22K") == (14, 22)


def test_salary_multi_level_ranges_three_tiers():
    """三级薪资：初级10K-13K 中级14K-17K 高级18K-25K → 10-25K"""
    assert _extract_salary_range("薪资范围：初级10K-13K 中级14K-17K 高级18K-25K") == (10, 25)


def test_salary_single_range_unchanged():
    """单范围薪资不应受影响"""
    assert _extract_salary_range("薪资范围：15K-25K") == (15, 25)


# ========== P2: 行业经验必要条件 ==========

def test_industry_experience_bond_fund_futures():
    """必要条件中'债券、基金、期货、期权'应被解析为 OR 必要条件"""
    text = (
        "必要条件（硬性约束）：\n"
        "1. 具有3年以上工作经验\n"
        "2. 金融投资行业经验，债券、基金、期货、期权等\n"
    )
    result = parse_job_requirements(text)
    rc = result["required_conditions"]
    industry_or = next((c for c in rc if isinstance(c, dict) and c.get("category") == "金融投资行业经验"), None)
    assert industry_or is not None, f"金融投资行业经验 OR 条件缺失: {rc}"
    assert industry_or["type"] == "or"
    assert industry_or["items"] == ["债券", "基金", "期货", "期权"]


def test_industry_experience_no_trigger_no_detection():
    """必要条件中没有'行业经验'触发词时，不应激活行业检测"""
    text = "必要条件\n本科\n3年以上经验\n熟悉债券市场"
    result = parse_job_requirements(text)
    # "债券"不应作为必要条件（只是熟悉，不是行业经验要求）
    # 但"债券"可能作为 tech_condition 被提取，这是正常的
    # 关键是不应因行业检测逻辑而额外添加
    rc = result["required_conditions"]
    # 必要条件应只含学历/经验类条件，不应含债券/基金等
    assert "基金" not in rc
    assert "期货" not in rc


def test_industry_experience_partial_sub_keywords():
    """只提及部分子类别时，只提取出现的那些"""
    text = "必要条件\n1. 3年以上经验\n2. 固收行业经验，熟悉债券、利率债"
    result = parse_job_requirements(text)
    rc = result["required_conditions"]
    # "债券"主词应被提取（因为"债券"出现在文本中，且是_bond的别名之一）
    # 固收是债券的别名，所以"债券"主词会匹配
    assert "债券" in rc or "固收" in [k for k in rc], f"债券或固收应在必要条件中: {rc}"


# ========== 行分类器重构：强/弱硬条件指示词 ==========

def test_weak_hard_hint_in_req_section_as_skill():
    """req section 中 '需要/要求' + 技能词 → skill，不是 hard"""
    from doc_parser import _classify_requirement_line
    # "需要有良好的沟通能力" — 含"需要"但无技能词 → hard（弱指示词 + 无技能词）
    assert _classify_requirement_line("需要有良好的沟通能力", "req") == "hard"
    # "要求熟悉 Java 开发" — 含"要求" + 技能词 → skill
    assert _classify_requirement_line("要求熟悉 Java 开发", "req") == "skill"
    # "需要掌握 Spring Boot" — 含"需要" + 技能词 → skill
    assert _classify_requirement_line("需要掌握 Spring Boot", "req") == "skill"


def test_strong_hard_hint_in_body_section():
    """body section 中只有强指示词才判 hard"""
    from doc_parser import _classify_requirement_line
    # "必须本科以上学历" — 强指示词 → hard
    assert _classify_requirement_line("必须本科以上学历", "body") == "hard"
    # "要求本科以上学历" — 弱指示词在 body 中 → 不判 hard
    assert _classify_requirement_line("要求本科以上学历", "body") == "other"


def test_hard_section_always_hard():
    """hard section 内的行一律判 hard"""
    from doc_parser import _classify_requirement_line
    assert _classify_requirement_line("本科以上学历", "hard") == "hard"
    assert _classify_requirement_line("熟悉 Java", "hard") == "hard"
    assert _classify_requirement_line("有良好的沟通能力", "hard") == "hard"


# ========== 段落切分增强 ==========

def test_fuzzy_section_heading_yingpin_tiaojian():
    """'应聘条件' 应被识别为 hard section"""
    text = "应聘条件\n统招本科，3年以上经验"
    result = parse_job_requirements(text)
    assert result["edu"] == "本科"
    assert result["min_exp"] == 3


def test_sub_heading_jingyan_in_body():
    """'工作经验' 子标题在 body 中应映射到 hard section"""
    text = "职位描述\n负责核心开发\n工作经验\n5年以上Java经验"
    result = parse_job_requirements(text)
    assert result["min_exp"] == 5


# ========== 学历解析上下文隔离 ==========

def test_education_same_sentence_hard_and_preferred():
    """同一句中 '本科'（硬门槛）和 '博士优先'（偏好）共存"""
    text = "## 硬性条件\n本科，博士优先"
    result = parse_job_requirements(text)
    assert result["edu"] == "本科"


def test_education_xueli_youxian_whole_sentence():
    """'硕士及以上学历优先考虑' 整句排除"""
    text = "## 硬性条件\n硕士及以上学历优先考虑"
    result = parse_job_requirements(text)
    assert result["edu"] == "不限"


# ========== 溯源数据 ==========

def test_source_map_contains_edu_evidence():
    """_source_map 包含学历的原文出处"""
    text = "## 硬性条件\n统招本科，3年以上经验"
    result = parse_job_requirements(text)
    sm = result.get("_source_map", {})
    assert "edu" in sm
    assert "本科" in sm["edu"]


def test_source_map_contains_skills_evidence():
    """_source_map 包含技能的原文出处"""
    text = "## 职位要求\n熟悉 Spring Boot、MySQL"
    result = parse_job_requirements(text)
    sm = result.get("_source_map", {})
    skills = sm.get("skills", [])
    names = [s["name"] for s in skills]
    assert "Spring Boot" in names
    # 找到 Spring Boot 的 evidence
    sb = next(s for s in skills if s["name"] == "Spring Boot")
    assert "Spring Boot" in sb["evidence"]


def test_source_map_contains_exp_evidence():
    """_source_map 包含经验的原文出处"""
    text = "## 硬性条件\n5年以上 Java 开发经验"
    result = parse_job_requirements(text)
    sm = result.get("_source_map", {})
    assert "min_exp" in sm
    assert "5" in sm["min_exp"]
