from collections import Counter
from datetime import datetime

import stats_presenter


def test_stats_time_cutoff_uses_local_day_week_and_month_boundaries():
    now = datetime(2026, 8, 9, 15, 30, 45)
    assert stats_presenter.stats_time_cutoff("今天", now=now) == datetime(
        2026, 8, 9
    )
    assert stats_presenter.stats_time_cutoff("本周", now=now) == datetime(
        2026, 8, 3
    )
    assert stats_presenter.stats_time_cutoff("本月", now=now) == datetime(
        2026, 8, 1
    )
    assert stats_presenter.stats_time_cutoff("全部", now=now) is None


def test_stats_dashboard_preserves_summary_and_job_rate_denominators():
    dashboard = stats_presenter.build_stats_dashboard(
        [
            {
                "job_name": "Java",
                "match_score": 80,
                "greet_sent": True,
                "feedback_status": "合适",
                "followup_status": "已回复",
            },
            {
                "job_name": "Java",
                "match_score": 68,
                "feedback_status": "误推",
                "followup_status": "未沟通",
            },
            {"job_name": "Java", "match_score": 58},
        ]
    )
    assert dashboard["summary"] == {
        "total": 3,
        "strong": 1,
        "recommended": 1,
        "greeted": 1,
    }
    assert dashboard["rows"] == [
        (
            "Java",
            "3 (强1/推1/待1)",
            "1 (33%)",
            2,
            "50%",
            "50%",
            "1 (100%)",
            "0 (0%)",
            "68.7",
        )
    ]


def test_job_review_keeps_low_score_false_negative_feedback():
    review = stats_presenter.build_job_review_model(
        "Java",
        [
            {
                "match_score": 40,
                "feedback_status": "误杀",
                "feedback_reasons": ["规则过窄"],
            },
            {
                "match_score": 80,
                "feedback_status": "合适",
                "feedback_reasons": [],
            },
        ],
    )
    assert review["qualified_count"] == 1
    assert review["feedback_count"] == 2
    assert review["false_negative_reasons"] == Counter({"规则过窄": 1})


def test_job_review_suggestions_require_enough_feedback():
    suggestions = stats_presenter.build_job_review_suggestions(
        Counter({"误推": 1}),
        Counter({"规则过宽": 1}),
        1,
    )
    assert "样本不足 5 条" in suggestions[0]

    title, detail = stats_presenter.format_job_review_suggestion(
        "- 规则过宽：补充硬性约束。"
    )
    assert title == "规则过宽"
    assert detail == "补充硬性约束。"


def test_job_review_recommendations_map_every_feedback_reason_to_safe_action():
    reason_counts = Counter(
        {
            "技能不匹配": 2,
            "行业经验不符": 1,
            "年限判断偏差": 1,
            "学历/学校不符": 1,
            "薪资不合适": 1,
            "地点不合适": 1,
            "求职状态不合适": 1,
            "AI 高估": 1,
            "AI 低估": 1,
            "规则过宽": 1,
            "规则过窄": 1,
            "简历信息不足": 1,
            "其他": 1,
        }
    )

    recommendations = stats_presenter.build_job_review_recommendations(
        Counter({"误推": 3, "误杀": 2}),
        reason_counts,
        5,
    )
    by_title = {item["title"]: item for item in recommendations}

    assert by_title["技能不匹配"]["config_target"] == "skills"
    assert by_title["年限判断偏差"]["config_target"] == "minimum_experience"
    assert by_title["学历/学校不符"]["config_target"] == "education"
    assert by_title["薪资不合适"]["config_target"] == "salary"
    assert by_title["地点不合适"]["config_target"] == "work_location"
    assert by_title["规则过窄"]["config_target"] == "required_conditions"
    assert by_title["AI 高估"]["config_target"] == "requirement"
    assert by_title["简历信息不足"]["config_target"] == ""
    assert by_title["其他"]["action_label"] == ""
    assert by_title["技能不匹配"]["evidence"] == (
        "5 条反馈中 2 条标记为“技能不匹配”"
    )


def test_job_review_recommendations_never_offer_config_actions_below_sample_gate():
    recommendations = stats_presenter.build_job_review_recommendations(
        Counter({"误推": 3}),
        Counter({"规则过宽": 3}),
        3,
    )

    assert all(not item["config_target"] for item in recommendations)
    assert all(not item["action_label"] for item in recommendations)
    assert "样本不足 5 条" in recommendations[0]["detail"]


def test_job_review_suggestion_format_accepts_structured_recommendation():
    assert stats_presenter.format_job_review_suggestion(
        {"title": "规则过宽", "detail": "核对必要条件。"}
    ) == ("规则过宽", "核对必要条件。")


def test_job_review_text_aggregates_structured_feedback_reasons():
    candidates = [
        {
            "job_name": "Java",
            "match_score": 80,
            "greet_sent": True,
            "followup_status": "已回复",
            "feedback_status": "误推",
            "feedback_reasons": ["技能不匹配", "规则过宽"],
        },
        {
            "job_name": "Java",
            "match_score": 40,
            "qualification_status": "rejected",
            "feedback_status": "误杀",
            "feedback_reasons": ["规则过窄", "AI 低估"],
        },
        {
            "job_name": "Java",
            "match_score": 60,
            "feedback_status": "合适",
            "feedback_reasons": ["行业经验不符"],
        },
    ]

    text = stats_presenter.build_job_review_text("Java", candidates)

    assert "Java 岗位复盘" in text
    assert "- 已反馈：3 人" in text
    assert "- 技能不匹配: 1" in text
    assert "- 规则过宽: 1" in text
    assert "- 规则过窄: 1" in text
    assert "- 误杀: 1" in text
    assert "- 反馈覆盖：3/3 人" in text
    assert "误推占比较高" not in text
    assert "规则过宽" in text
    assert "样本不足 5 条" in text
    assert "多人反馈" not in text


def test_job_review_only_reports_trends_after_minimum_feedback_sample():
    candidates = [
        {
            "job_name": "Java",
            "match_score": 80 - index,
            "feedback_status": "误推" if index < 3 else "合适",
            "feedback_reasons": ["规则过宽"] if index < 3 else ["其他"],
        }
        for index in range(5)
    ]

    text = stats_presenter.build_job_review_text("Java", candidates)

    assert "误推占比较高" in text
    assert "规则过宽：3/5 条" in text
    assert "样本不足" not in text


def test_job_review_model_exposes_text_and_structured_recommendations_together():
    candidates = [
        {
            "match_score": 70,
            "feedback_status": "误推",
            "feedback_reasons": ["薪资不合适"],
        }
        for _index in range(5)
    ]

    review = stats_presenter.build_job_review_model("Java", candidates)

    salary = next(
        item for item in review["recommendations"] if item["title"] == "薪资不合适"
    )
    assert salary["config_target"] == "salary"
    assert salary["action_label"] == "定位薪资范围"
    assert salary["text"] in review["suggestions"]
