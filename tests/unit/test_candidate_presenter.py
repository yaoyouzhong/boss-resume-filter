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
