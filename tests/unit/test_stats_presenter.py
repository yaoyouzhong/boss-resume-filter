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
