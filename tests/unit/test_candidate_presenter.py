import candidate_presenter


def test_candidate_gender_display_supports_structured_and_legacy_summary():
    assert candidate_presenter.candidate_gender_display(
        {"structured": {"gender": "女"}}
    ) == "女"
    assert candidate_presenter.candidate_gender_display(
        {"summary": "年龄：28\n性别：男"}
    ) == "男"


def test_extract_summary_display_fields_returns_result_table_values():
    result = candidate_presenter.extract_summary_display_fields(
        "本科\n年龄：29\n求职状态：离职"
    )
    assert result == {
        "education": "本科",
        "age": "29",
        "job_status": "离职",
    }


def test_latest_history_value_prefers_current_entry_and_falls_back_to_summary():
    entries = [
        {"school": "旧学校", "end": "2020-06"},
        {"school": "当前学校", "end": "至今"},
    ]
    assert candidate_presenter.latest_history_value(
        entries,
        "school",
        "",
        "毕业学校：",
    ) == "当前学校"
    assert candidate_presenter.latest_history_value(
        [],
        "school",
        "毕业学校：备用学校 本科",
        "毕业学校：",
    ) == "备用学校"


def test_latest_history_value_uses_latest_end_date_not_list_order():
    entries = [
        {"school": "较早学校", "end": "2018.06"},
        {"school": "最近学校", "end": "2022.06"},
    ]

    assert candidate_presenter.latest_history_value(
        entries,
        "school",
        "",
        "教育经历：",
    ) == "最近学校"


def test_latest_history_value_treats_present_as_latest_for_work_history():
    works = [
        {"company": "上一家公司", "end": "2024.01"},
        {"company": "当前公司", "end": "至今"},
    ]
    assert candidate_presenter.latest_history_value(
        works,
        "company",
        "",
        "工作经历：",
    ) == "当前公司"
    assert candidate_presenter.latest_history_value(
        [],
        "company",
        "工作经历：摘要公司 高级工程师 2022 至今",
        "工作经历：",
    ) == "摘要公司"


def test_extract_candidate_extra_fields_falls_back_to_record_school_company():
    """外部导入候选人没有 API 画像：学校/公司回退到导入时写入的记录级字段。"""
    education, age, job_status, school, company = (
        candidate_presenter.extract_candidate_extra_fields(
            {
                "summary": "整份简历全文，没有“教育经历：”前缀行",
                "school": "江南大学",
                "company": "亚信科技",
            }
        )
    )
    assert school == "江南大学"
    assert company == "亚信科技"


def test_extract_candidate_extra_fields_api_profile_wins_over_record_fields():
    """API 画像存在时优先于记录级字段，扫描候选人行为不变。"""
    *_head, school, company = candidate_presenter.extract_candidate_extra_fields(
        {
            "_api_profile": {
                "educations": [{"school": "画像学校", "end": "至今"}],
                "works": [{"company": "画像公司", "end": "至今"}],
            },
            "school": "记录学校",
            "company": "记录公司",
        }
    )
    assert school == "画像学校"
    assert company == "画像公司"


def test_extract_candidate_extra_fields_external_uses_pinned_record_fields():
    """外部导入候选人学历/年龄/求职状态以记录字段为准，不回退简历全文。

    简历全文的"在校情况"板块标题会被摘要正则误命中为求职状态"在校"。
    """
    education, age, job_status, school, company = (
        candidate_presenter.extract_candidate_extra_fields(
            {
                "source": "external",
                "summary": "技能清单\n在校情况\n▲校内荣誉 2013/7 国家励志奖学金",
                "education": "本科",
                "age": "23",
                "job_status": "在职",
                "school": "中华女子学院",
                "company": "湖南德成鸿业咨询服务有限公司",
            }
        )
    )
    assert (education, age, job_status) == ("本科", "23岁", "在职")
    assert school == "中华女子学院"
    assert company == "湖南德成鸿业咨询服务有限公司"


def test_extract_candidate_extra_fields_external_blank_status_stays_blank():
    """外部记录求职状态未识别时保持空白，不被简历全文的"在校情况"误报。"""
    _education, _age, job_status, _school, _company = (
        candidate_presenter.extract_candidate_extra_fields(
            {
                "source": "external",
                "summary": "课程清单\n在校情况\n▲校内荣誉 国家励志奖学金",
                "job_status": "",
            }
        )
    )
    assert job_status == ""


def test_format_ai_hard_conditions_and_batch_summary_are_bounded():
    conditions = candidate_presenter.format_ai_hard_conditions(
        {
            "min_exp": 3,
            "edu": "本科",
            "required_conditions": [{"name": "Java"}, "SQL"],
        }
    )
    assert "经验：要求≥3年" in conditions
    assert "必要条件：Java、SQL" in conditions

    title, message, has_failure = candidate_presenter.format_ai_eval_batch_summary(
        {
            "selected_count": 10,
            "success": [],
            "failed": [
                {"name": f"候选人{i}", "reason": "模型返回内容无法识别"}
                for i in range(8)
            ],
            "skipped": [],
        }
    )
    assert title == "AI 评估完成"
    assert has_failure is True
    assert "另有 2 人失败" in message


def test_format_display_datetime_and_candidate_decision_summary():
    assert candidate_presenter.format_display_datetime("20260809_143000") == (
        "2026-08-09 14:30"
    )
    summary = candidate_presenter.format_candidate_decision_summary(
        {"match_score": 70, "recommend_level": "推荐"}
    )
    assert "下一步" in summary
    assert "筛选结论" in summary
    assert "当前状态" in summary


def test_format_candidate_detail_uses_prepared_data_without_external_access():
    detail = candidate_presenter.format_candidate_detail(
        {
            "name": "候选人甲",
            "job_name": "数据分析师",
            "geek_id": "candidate-1",
            "match_score": 70,
            "summary": "教育经历：南京大学 计算机 本科 2015 2019",
            "feedback_status": "合适",
            "feedback_note": "业务经验匹配",
            "llm_error": "请求超时\n请稍后重试",
        },
        summary_info={
            "age": "29",
            "exp_years": "6",
            "salary": "15-20K",
            "job_status": "离职",
            "education": "本科",
        },
        feedback_reasons=["技能匹配"],
        dimension_labels={"skill_depth": "技能深度"},
    )

    assert "候选人甲" in detail
    assert "29 岁｜6 年｜期望薪资 15-20K｜离职" in detail
    assert "【人工反馈】" in detail
    assert "原因：技能匹配" in detail
    assert "失败原因：请求超时 请稍后重试" in detail
    assert "【教育经历】" in detail


def test_parse_salary_experience_external_uses_pinned_record_fields():
    """外部候选人的 summary 是简历全文，展示薪资/年限以记录字段为准，
    不得用 BOSS 摘要正则重解析（会把时间段、规模数字误当薪资）。"""
    resume_summary = "个人简历\n姓名：丁小飞\n出生日期：1993年11月\n2017.9-2019.12 南京理工大学"
    salary, exp = candidate_presenter.parse_salary_experience(
        resume_summary,
        None,
        record={"source": "external", "salary": "", "exp_years": "2"},
    )
    assert salary == ""  # 不是“2017-2019K”
    assert exp == "2年"
    # 记录有值时直接用记录值（“规模50-150人”不得覆盖 12.5K）
    salary, exp = candidate_presenter.parse_salary_experience(
        "谢小为 规模50-150人 目前年收入：15万元",
        None,
        record={"source": "external", "salary": "12.5K", "exp_years": "3"},
    )
    assert salary == "12.5K"
    assert exp == "3年"
    # 面议原样展示
    salary, exp = candidate_presenter.parse_salary_experience(
        "简历全文",
        None,
        record={"source": "external", "salary": "面议", "exp_years": ""},
    )
    assert salary == "面议"
    assert exp == ""


def test_parse_salary_experience_boss_path_unchanged_without_record():
    """BOSS 候选人没有记录级钉定字段，保持摘要解析行为不变。"""
    salary, exp = candidate_presenter.parse_salary_experience("15-20K\n30 岁，6 年经验")
    assert salary == "15-20K"
    assert exp == "6年"
    # BOSS 记录即使带 record 参数（source 非 external）也不走钉定分支
    salary, exp = candidate_presenter.parse_salary_experience(
        "10-15K\n5 年经验",
        None,
        record={"source": "boss", "salary": "99K"},
    )
    assert salary == "10-15K"


def test_format_candidate_detail_shows_ai_profile_enhancement_section():
    candidate = {
        "name": "张三",
        "summary": "5年Python开发经验",
        "match_score": 72,
        "recommend_level": "推荐",
        "profile_ai_filled": [{"field": "age", "label": "年龄", "value": "45"}],
        "profile_conflicts": [
            {"field": "city", "label": "工作地点", "rule": "上海", "ai": "北京"}
        ],
        "profile_ai_error": "",
    }
    detail = candidate_presenter.format_candidate_detail(
        candidate, summary_info={}, feedback_reasons=(), dimension_labels={}
    )
    assert "【AI 画像增强】" in detail
    assert "补全：年龄 = 45" in detail
    assert "冲突：工作地点 规则值 上海 / AI 值 北京（已保留规则值）" in detail


def test_format_candidate_detail_hides_ai_profile_section_without_traces():
    candidate = {
        "name": "张三",
        "summary": "5年Python开发经验",
        "match_score": 72,
        "recommend_level": "推荐",
    }
    assert "【AI 画像增强】" not in candidate_presenter.format_candidate_detail(
        candidate, summary_info={}, feedback_reasons=(), dimension_labels={}
    )


def test_format_candidate_detail_shows_ai_profile_error():
    candidate = {
        "name": "张三",
        "summary": "5年Python开发经验",
        "match_score": 72,
        "recommend_level": "推荐",
        "profile_ai_error": "AI 读取超时：模型服务 60 秒内未返回响应",
    }
    detail = candidate_presenter.format_candidate_detail(
        candidate, summary_info={}, feedback_reasons=(), dimension_labels={}
    )
    assert "【AI 画像增强】" in detail
    assert "增强未完成：AI 读取超时" in detail
