from candidate_workflow import (
    ACTION_TIMING_ORDER,
    apply_followup_state,
    build_daily_candidate_actions,
    candidate_can_manual_approve_contact,
    candidate_greet_skip_reason,
    candidate_has_manual_contact_approval,
    candidate_has_review_passed,
    candidate_review_category,
    classify_followup_timing,
    default_next_followup_at,
    derive_candidate_decision,
    filter_candidates_by_result_view,
    format_followup_due_at,
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
    assert decision.review_status == "pending"
    assert decision.primary_review_reason == "工作年限证据不足"
    assert decision.review_reasons == (
        "工作年限证据不足",
        "评分处于待定区间（60 分）",
    )


def test_candidate_decision_keeps_legacy_string_reason_intact():
    decision = derive_candidate_decision({
        "match_score": 80,
        "qualification_status": "manual_review",
        "qualification_reasons": "工作年限证据不足",
    })

    assert decision.review_reasons == ("工作年限证据不足",)


def test_result_views_keep_screening_and_review_scopes_independent():
    candidates = [
        {"geek_id": "recommended", "match_score": 70},
        {"geek_id": "send-pending", "match_score": 70, "greet_confirmation_pending": True},
        {"geek_id": "pending", "match_score": 60},
        {"geek_id": "approved", "match_score": 60, "contact_approved_at": "20260728_100000"},
        {
            "geek_id": "hard-passed",
            "match_score": 80,
            "review_passed_at": "20260728_110000",
            "review_passed_reasons": ["学历形式待确认"],
        },
        {"geek_id": "ai-failed", "match_score": 70, "llm_error": "timeout"},
        {"geek_id": "manual", "match_score": 80, "manual_review_required": True},
        {"geek_id": "rejected", "match_score": 90, "qualification_status": "rejected"},
    ]

    assert {c["geek_id"] for c in filter_candidates_by_result_view(candidates, "推荐候选人")} == {
        "recommended", "send-pending", "hard-passed", "ai-failed"
    }
    assert {c["geek_id"] for c in filter_candidates_by_result_view(candidates, "待复核")} == {
        "pending", "manual"
    }
    assert {
        c["geek_id"] for c in filter_candidates_by_result_view(
            candidates, "复核通过"
        )
    } == {"approved", "hard-passed"}
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
    assert candidate_greet_skip_reason({
        "geek_id": "scheduled",
        "match_score": 80,
        "followup_status": "待约面",
    }) == "跟进状态为待约面"


def test_pending_candidate_can_be_manually_approved_without_changing_score():
    pending = {"geek_id": "pending", "match_score": 60}

    assert candidate_can_manual_approve_contact(pending) is True
    pending["contact_approved_at"] = "20260728_100000"

    decision = derive_candidate_decision(pending)
    assert candidate_has_manual_contact_approval(pending) is True
    assert candidate_has_review_passed(pending) is True
    assert decision.screening_result == "待定"
    assert decision.result_view == "复核通过"
    assert decision.review_status == "passed"
    assert decision.review_reasons == ()
    assert candidate_greet_skip_reason(pending) == ""


def test_prior_hard_condition_review_does_not_approve_a_new_pending_score():
    candidate = {
        "geek_id": "rescored",
        "match_score": 60,
        "review_passed_at": "20260728_100000",
        "review_passed_reasons": ["学历形式待确认"],
    }

    decision = derive_candidate_decision(candidate)

    assert decision.review_status == "pending"
    assert decision.review_reasons == ("评分处于待定区间（60 分）",)
    assert candidate_can_manual_approve_contact(candidate) is True


def test_suitable_feedback_resolves_only_non_hard_pending_review():
    approved = {
        "geek_id": "approved",
        "match_score": 64,
        "feedback_status": "合适",
    }
    hard_review = dict(
        approved,
        geek_id="hard",
        qualification_status="manual_review",
        qualification_reasons=["学历形式待确认"],
    )

    assert derive_candidate_decision(approved).result_view == "复核通过"
    assert candidate_greet_skip_reason(approved) == ""
    assert derive_candidate_decision(hard_review).result_view == "待复核"
    assert candidate_greet_skip_reason(hard_review) == "学历形式待确认"


def test_rule_qualified_ai_failure_does_not_require_review_or_approval():
    candidate = {
        "geek_id": "ai-failed",
        "match_score": 70,
        "qualification_status": "qualified",
        "llm_error": "timeout",
    }

    decision = derive_candidate_decision(candidate)
    assert decision.screening_result == "推荐"
    assert decision.result_view == "推荐候选人"
    assert decision.review_status == "not_required"
    assert decision.review_status == "not_required"
    assert decision.review_reasons == ()
    assert candidate_can_manual_approve_contact(candidate) is False
    assert candidate_greet_skip_reason(candidate) == ""


def test_negative_feedback_blocks_contact_and_closes_daily_action():
    for feedback, reason in (("误推", "人工反馈为误推"), ("放弃", "人工已放弃")):
        candidate = {
            "geek_id": feedback,
            "match_score": 80,
            "feedback_status": feedback,
        }
        assert candidate_greet_skip_reason(candidate) == reason
        assert build_daily_candidate_actions([candidate]) == []


def test_manual_contact_approval_does_not_override_low_score_or_rejection():
    low_score = {
        "geek_id": "low",
        "match_score": 54,
        "contact_approved_at": "20260728_100000",
    }
    rejected = {
        "geek_id": "rejected",
        "match_score": 80,
        "qualification_status": "rejected",
        "contact_approved_at": "20260728_100000",
    }

    assert candidate_greet_skip_reason(low_score) == "评分低于通过线（54 分）"
    assert candidate_greet_skip_reason(rejected) == "已淘汰"
    assert candidate_can_manual_approve_contact(low_score) is False
    assert candidate_can_manual_approve_contact(rejected) is False


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
    assert {item.group for item in items} == {"待复核", "待打招呼"}
    assert {
        item.candidate["geek_id"]
        for item in items
        if item.group == "待复核"
    } == {f"score-{index}" for index in range(12)} | {"manual"}
    assert [
        item.candidate["geek_id"]
        for item in items
        if item.group == "待打招呼"
    ] == ["ai-failed"]


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
        "",
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


def test_daily_actions_classify_followup_by_due_date_without_hiding_future_work():
    candidates = [
        {
            "geek_id": "overdue",
            "match_score": 80,
            "greet_sent": True,
            "followup_status": "已打招呼",
            "next_followup_at": "20260717_090000",
        },
        {
            "geek_id": "today",
            "match_score": 80,
            "greet_sent": True,
            "followup_status": "待约面",
            "next_followup_at": "20260718_090000",
        },
        {
            "geek_id": "future",
            "match_score": 80,
            "greet_sent": True,
            "followup_status": "已约面",
            "next_followup_at": "20260721_090000",
        },
        {
            "geek_id": "legacy",
            "match_score": 80,
            "greet_sent": True,
            "followup_status": "已打招呼",
        },
    ]

    items = build_daily_candidate_actions(candidates, today="2026-07-18")
    by_id = {item.candidate["geek_id"]: item for item in items}

    assert by_id["overdue"].timing_group == "已逾期"
    assert by_id["today"].timing_group == "今天"
    assert by_id["today"].group == "待约面待推进"
    assert by_id["future"].timing_group == "以后"
    assert by_id["future"].group == "面试后待反馈"
    assert by_id["legacy"].timing_group == "待安排"
    assert ACTION_TIMING_ORDER == ("立即处理", "已逾期", "今天", "待安排", "以后")


def test_existing_followup_status_is_not_requeued_when_legacy_greet_flag_is_missing():
    items = build_daily_candidate_actions([
        {
            "geek_id": "legacy-followup",
            "match_score": 80,
            "followup_status": "待约面",
            "next_followup_at": "20260718_090000",
        },
        {
            "geek_id": "legacy-greeted",
            "match_score": 80,
            "followup_status": "已打招呼",
        },
    ], today="2026-07-18")

    by_id = {item.candidate["geek_id"]: item for item in items}
    assert by_id["legacy-followup"].group == "待约面待推进"
    assert by_id["legacy-followup"].timing_group == "今天"
    assert by_id["legacy-greeted"].group == "已打招呼待跟进"
    assert by_id["legacy-greeted"].timing_group == "待安排"


def test_completed_interview_only_returns_to_actions_when_followup_is_scheduled():
    unscheduled = {
        "geek_id": "interviewed",
        "match_score": 80,
        "greet_sent": True,
        "followup_status": "已约面",
    }

    assert build_daily_candidate_actions([unscheduled], today="2026-07-18") == []

    scheduled = dict(unscheduled, next_followup_at="20260720_090000")
    items = build_daily_candidate_actions([scheduled], today="2026-07-18")
    assert len(items) == 1
    assert items[0].group == "面试后待反馈"
    assert items[0].timing_group == "以后"


def test_immediate_candidate_actions_override_followup_schedule():
    items = build_daily_candidate_actions([
        {
            "geek_id": "reply",
            "match_score": 80,
            "greet_sent": True,
            "followup_status": "已回复",
            "next_followup_at": "20260801_090000",
        },
        {
            "geek_id": "pending",
            "match_score": 80,
            "greet_confirmation_pending": True,
        },
    ], today="2026-07-18")

    assert [item.timing_group for item in items] == ["立即处理", "立即处理"]


def test_followup_state_defaults_and_terminal_states_manage_due_date():
    candidate = {}

    updated_at = apply_followup_state(
        candidate,
        "已打招呼",
        "已联系",
        timestamp="20260718_100000",
    )

    assert updated_at == "20260718_100000"
    assert candidate["next_followup_at"] == "20260719_100000"
    assert classify_followup_timing(candidate, "2026-07-19") == (
        "今天", "20260719_100000"
    )

    apply_followup_state(
        candidate,
        "待约面",
        timestamp="20260719_110000",
        next_followup_at="2026-07-23",
    )
    assert candidate["next_followup_at"] == "20260723_000000"
    assert format_followup_due_at(candidate["next_followup_at"]) == "2026-07-23"

    apply_followup_state(candidate, "已归档", timestamp="20260720_120000")
    assert "next_followup_at" not in candidate


def test_default_next_followup_at_uses_status_specific_offsets():
    assert default_next_followup_at("已打招呼", "20260718_100000") == "20260719_100000"
    assert default_next_followup_at("已回复", "20260718_100000") == "20260718_100000"
    assert default_next_followup_at("待约面", "20260718_100000") == "20260719_100000"
    assert default_next_followup_at("已归档", "20260718_100000") == ""


def test_daily_action_summary_separates_timing_and_business_groups():
    items = build_daily_candidate_actions([
        {
            "geek_id": "due",
            "name": "到期候选人",
            "job_name": "Java",
            "match_score": 80,
            "greet_sent": True,
            "followup_status": "已打招呼",
            "next_followup_at": "20260718_090000",
        }
    ], today="2026-07-18")

    text = summarize_daily_candidate_actions(items)

    assert "# 今天" in text
    assert "## 已打招呼待跟进" in text
    assert "到期：2026-07-18" in text
