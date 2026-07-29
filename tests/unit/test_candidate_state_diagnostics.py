from candidate_state_diagnostics import (
    diagnose_candidate_states,
    summarize_candidate_state_diagnostics,
)


def _candidate(**overrides):
    data = {
        "geek_id": "g1",
        "name": "张三",
        "job_name": "Java 工程师",
        "match_score": 75,
        "qualification_status": "qualified",
        "followup_status": "未沟通",
    }
    data.update(overrides)
    return data


def test_diagnose_greeted_candidate_requires_followup_and_audit_fields():
    issues = diagnose_candidate_states([
        _candidate(greet_sent=True, followup_status="未沟通"),
    ])

    titles = {issue.title for issue in issues}
    assert "已打招呼但跟进状态未更新" in titles
    assert "打招呼记录不完整" in titles


def test_diagnose_pending_and_success_greeting_conflict():
    issues = diagnose_candidate_states([
        _candidate(
            greet_sent=True,
            followup_status="已打招呼",
            greet_sent_at="20260709_100000",
            greet_method="manual_context",
            greet_confirmation_pending=True,
        ),
    ])

    assert any(issue.severity == "error" and issue.title == "已发送与待核实并存" for issue in issues)


def test_diagnose_manual_review_flag_must_match_qualification_status():
    issues = diagnose_candidate_states([
        _candidate(manual_review_required=True, qualification_status="qualified"),
    ])

    assert any(issue.title == "人工复核字段待归一" for issue in issues)


def test_diagnose_rejected_candidate_must_not_stay_active():
    issues = diagnose_candidate_states([
        _candidate(
            qualification_status="rejected",
            greet_sent=True,
            followup_status="已回复",
            greet_context={"chat_start": {"url": "/chat/start"}},
        ),
    ])

    titles = {issue.title for issue in issues}
    assert "淘汰候选人仍处于沟通状态" in titles
    assert "淘汰候选人仍保留打招呼上下文" in titles


def test_diagnose_low_score_rejected_history_has_valid_retention_reason():
    issues = diagnose_candidate_states([
        _candidate(
            match_score=0,
            qualification_status="rejected",
            qualification_reasons=["最新扫描未通过筛选"],
            rejection_source="previously_recommended",
        ),
    ])

    assert all(issue.title != "低分候选人缺少保留理由" for issue in issues)


def test_diagnose_incomplete_resume_eval_and_greet_context():
    issues = diagnose_candidate_states([
        _candidate(
            resume_eval_adjustment=8,
            resume_eval_reason="",
            resume_eval_at="",
            greet_context={"detail": {"jid": "j1"}},
        ),
    ])

    titles = {issue.title for issue in issues}
    assert "简历评估信息不完整" in titles
    assert "打招呼上下文不完整" in titles


def test_diagnose_duplicate_candidate_records_by_geek_and_job():
    issues = diagnose_candidate_states([
        _candidate(geek_id="g1", match_score=75, followup_status="未沟通"),
        _candidate(geek_id="g1", match_score=82, followup_status="已回复"),
    ])

    assert any(issue.title == "重复候选人记录" and issue.severity == "error" for issue in issues)


def test_diagnose_cross_job_blacklist_state_mismatch():
    issues = diagnose_candidate_states([
        _candidate(geek_id="g1", job_name="Java 工程师", blacklisted=True),
        _candidate(geek_id="g1", job_name="Python 工程师", blacklisted=False),
    ])

    assert any(issue.title == "跨岗位黑名单状态不一致" for issue in issues)


def test_summarize_candidate_state_diagnostics_reports_clean_data():
    candidates = [
        _candidate(
            geek_id="g1",
            greet_sent=True,
            followup_status="已打招呼",
            greet_sent_at="20260709_100000",
            greet_method="manual_context",
            next_followup_at="20260710_100000",
        )
    ]
    text = summarize_candidate_state_diagnostics(candidates)

    assert "候选人：1 人" in text
    assert "未发现明显状态冲突" in text


def test_diagnose_followup_schedule_conflicts_and_missing_dates():
    issues = diagnose_candidate_states([
        _candidate(
            geek_id="terminal",
            followup_status="已归档",
            next_followup_at="20260720_090000",
        ),
        _candidate(
            geek_id="interview",
            followup_status="待约面",
        ),
        _candidate(
            geek_id="interviewed",
            followup_status="已约面",
        ),
        _candidate(
            geek_id="invalid",
            followup_status="已打招呼",
            next_followup_at="下周一",
        ),
    ])

    titles = {issue.title for issue in issues}
    assert "结束状态仍有跟进提醒" in titles
    assert "待约面未安排时间" in titles
    assert "已约面未安排时间" in titles
    assert "下次跟进日期无效" in titles


def test_diagnose_unknown_enums_missing_score_and_conflicting_review_results():
    issues = diagnose_candidate_states([
        _candidate(
            geek_id="invalid",
            match_score="unknown",
            qualification_status="mystery",
            followup_status="unknown",
            feedback_status="unknown",
            review_passed_at="20260729_100000",
            review_rejected_at="20260729_110000",
        ),
    ])

    error_titles = {
        issue.title for issue in issues if issue.severity == "error"
    }
    assert {
        "匹配分缺失或无效",
        "未知资格审查状态",
        "未知跟进状态",
        "未知人工反馈",
        "复核通过与不通过并存",
    } <= error_titles


def test_diagnose_legacy_active_followup_is_non_blocking_schedule_info():
    issues = diagnose_candidate_states([
        _candidate(followup_status="已打招呼")
    ])

    issue = next(item for item in issues if item.title == "跟进时间待安排")
    assert issue.severity == "info"
    assert any(
        item.title == "沟通进度与已打招呼记录不一致"
        and item.severity == "error"
        for item in issues
    )


def test_diagnose_manual_contact_approval_conflicts():
    issues = diagnose_candidate_states([
        _candidate(
            geek_id="negative",
            feedback_status="放弃",
            contact_approved_at="20260728_100000",
        ),
        _candidate(
            geek_id="hard",
            match_score=64,
            feedback_status="合适",
            qualification_status="manual_review",
            manual_review_required=True,
            contact_approved_at="20260728_100000",
        ),
    ])

    titles = {issue.title for issue in issues}
    assert "人工负面反馈与联系批准并存" in titles
    assert "人工合适结论仍有资格阻断" in titles
    assert "人工联系批准当前不可用" in titles
