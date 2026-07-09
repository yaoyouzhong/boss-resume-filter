from candidate_workflow import build_daily_candidate_actions, summarize_daily_candidate_actions


def test_daily_actions_prioritize_pending_manual_and_greet_targets():
    items = build_daily_candidate_actions([
        {
            "geek_id": "p1",
            "name": "待确认",
            "job_name": "Java",
            "match_score": 80,
            "manual_review_required": True,
            "qualification_status": "manual_review",
        },
        {
            "geek_id": "g1",
            "name": "高分未沟通",
            "job_name": "Java",
            "match_score": 88,
            "greet_context": {"chat_start": {"url": "/chat/start"}},
        },
    ])

    assert [item.group for item in items] == ["待人工确认", "高分未打招呼"]
    assert items[0].action.startswith("查看详情")


def test_daily_actions_include_followup_and_resume_tasks():
    items = build_daily_candidate_actions([
        {
            "geek_id": "r1",
            "name": "已回复",
            "job_name": "Java",
            "match_score": 70,
            "greet_sent": True,
            "followup_status": "已回复",
        },
        {
            "geek_id": "cv1",
            "name": "有简历",
            "job_name": "Java",
            "match_score": 72,
            "resume_file": "resume.pdf",
        },
    ])

    groups = {item.group for item in items}
    assert "已回复待约面" in groups
    assert "有简历未二次评估" in groups


def test_summarize_daily_actions_reports_empty_state():
    text = summarize_daily_candidate_actions([])

    assert "暂无需要优先处理" in text
