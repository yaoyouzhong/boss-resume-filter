from candidate_workflow import (
    build_daily_candidate_actions,
    candidate_greet_skip_reason,
    candidate_review_category,
    derive_candidate_decision,
    filter_candidates_by_result_view,
    summarize_daily_candidate_actions,
)


def test_candidate_decision_separates_screening_review_and_communication():
    decision = derive_candidate_decision({
        "match_score": 72,
        "qualification_status": "qualified",
        "greet_confirmation_pending": True,
    })

    assert decision.screening_result == "推荐"
    assert decision.result_view == "推荐候选人"
    assert decision.review_reasons == ()
    assert decision.communication_status == "发送待核实"
    assert "核实发送结果" in decision.next_action


def test_candidate_decision_exposes_deterministic_review_reason():
    decision = derive_candidate_decision({
        "match_score": 60,
        "qualification_status": "manual_review",
        "qualification_reasons": ["工作年限证据不足", "工作年限证据不足"],
        "llm_error": "timeout",
    })

    assert decision.result_view == "待复核"
    assert decision.primary_review_reason == "工作年限证据不足"
    assert decision.review_reasons == (
        "工作年限证据不足",
        "AI 评估失败，需人工判断或重试",
        "评分处于待定区间（60 分）",
    )


def test_candidate_decision_keeps_legacy_string_reason_intact():
    decision = derive_candidate_decision({
        "match_score": 80,
        "qualification_status": "manual_review",
        "qualification_reasons": "工作年限证据不足",
    })

    assert decision.review_reasons == ("工作年限证据不足",)


def test_result_views_form_disjoint_decision_partitions():
    candidates = [
        {"geek_id": "recommended", "match_score": 70},
        {"geek_id": "send-pending", "match_score": 70, "greet_confirmation_pending": True},
        {"geek_id": "pending", "match_score": 60},
        {"geek_id": "manual", "match_score": 80, "manual_review_required": True},
        {"geek_id": "rejected", "match_score": 90, "qualification_status": "rejected"},
    ]

    assert {c["geek_id"] for c in filter_candidates_by_result_view(candidates, "推荐候选人")} == {
        "recommended", "send-pending"
    }
    assert {c["geek_id"] for c in filter_candidates_by_result_view(candidates, "待复核")} == {
        "pending", "manual"
    }
    assert [c["geek_id"] for c in filter_candidates_by_result_view(candidates, "淘汰记录")] == [
        "rejected"
    ]
    assert derive_candidate_decision(candidates[-1]).screening_result == "淘汰"
    assert filter_candidates_by_result_view(candidates, "") == candidates


def test_greeting_queue_requires_completed_review():
    assert candidate_greet_skip_reason({"geek_id": "pending", "match_score": 60}) == (
        "评分处于待定区间（60 分）"
    )
    assert candidate_greet_skip_reason({
        "geek_id": "manual",
        "match_score": 80,
        "manual_review_required": True,
    }) == "硬性条件需要人工确认"
    assert candidate_greet_skip_reason({"geek_id": "ready", "match_score": 70}) == ""


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

    assert [item.group for item in items] == ["待复核", "待打招呼"]
    assert "核对硬性条件证据" in items[0].action


def test_daily_actions_use_business_groups_without_exposing_context_terms():
    items = build_daily_candidate_actions([
        {
            "geek_id": "without-context",
            "name": "无上下文",
            "job_name": "Java",
            "match_score": 80,
        },
        {
            "geek_id": "pending-score",
            "name": "待定分数",
            "job_name": "Java",
            "match_score": 60,
        },
    ])

    assert [item.group for item in items] == ["待复核", "待打招呼"]
    greet_item = items[1]
    assert greet_item.candidate["geek_id"] == "without-context"
    assert "上下文" not in greet_item.group + greet_item.reason + greet_item.action
    assert "推荐牛人页面" in greet_item.action


def test_daily_actions_reuse_all_pending_review_decisions_without_default_limit():
    candidates = [
        {"geek_id": f"score-{index}", "match_score": 60}
        for index in range(12)
    ] + [
        {"geek_id": "manual", "match_score": 80, "manual_review_required": True},
        {"geek_id": "ai-failed", "match_score": 70, "llm_error": "timeout"},
    ]

    items = build_daily_candidate_actions(candidates)

    assert len(items) == 14
    assert {item.group for item in items} == {"待复核"}
    assert {item.candidate["geek_id"] for item in items} == {
        candidate["geek_id"] for candidate in candidates
    }


def test_review_categories_normalize_primary_reasons_without_duplicate_people():
    candidates = [
        {
            "geek_id": "education-short",
            "match_score": 80,
            "qualification_status": "manual_review",
            "qualification_reasons": ["学历形式待确认"],
        },
        {
            "geek_id": "education-detail",
            "match_score": 80,
            "qualification_status": "manual_review",
            "qualification_reasons": ["学历形式待确认：未发现明确统招本科证据"],
        },
        {
            "geek_id": "job-status",
            "match_score": 80,
            "qualification_status": "manual_review",
            "qualification_reasons": ["在职状态：在职-考虑机会"],
        },
        {"geek_id": "score", "match_score": 60},
        {"geek_id": "ai-failed", "match_score": 70, "llm_error": "timeout"},
    ]

    assert [candidate_review_category(candidate) for candidate in candidates] == [
        "学历形式待确认",
        "学历形式待确认",
        "求职状态待确认",
        "评分待确认",
        "AI评估失败",
    ]
    assert candidate_review_category({"geek_id": "ready", "match_score": 70}) == ""


def test_daily_actions_assign_each_candidate_to_one_highest_priority_group():
    items = build_daily_candidate_actions([
        {
            "geek_id": "reply-and-resume",
            "match_score": 80,
            "greet_sent": True,
            "followup_status": "已回复",
            "resume_file": "resume.pdf",
        },
        {
            "geek_id": "send-pending",
            "match_score": 80,
            "greet_confirmation_pending": True,
            "followup_status": "已回复",
        },
    ])

    assert [(item.candidate["geek_id"], item.group) for item in items] == [
        ("send-pending", "发送结果待核实"),
        ("reply-and-resume", "已回复待推进"),
    ]


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
    assert "已回复待推进" in groups
    assert "待完成简历评估" in groups


def test_summarize_daily_actions_reports_empty_state():
    text = summarize_daily_candidate_actions([])

    assert text.startswith("今日待办\n")
    assert "暂无需要优先处理" in text
