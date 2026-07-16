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
    assert "缺少打招呼审计信息" in titles


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

    assert any(issue.title == "需要人工确认" for issue in issues)


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
        )
    ]
    text = summarize_candidate_state_diagnostics(candidates)

    assert "候选人：1 人" in text
    assert "未发现明显状态冲突" in text
