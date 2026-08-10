from contact_controller import (
    ContactController,
    ContactNotice,
    ContactRunCounters,
    SendExceptionDecision,
)
from contact_queue import build_contact_queue_item


def _candidate(geek_id, *, job="Java", name="候选人", **extra):
    candidate = {
        "geek_id": geek_id,
        "job_name": job,
        "name": name,
        "match_score": 80,
    }
    candidate.update(extra)
    return candidate


def _run_queue(items, send_candidate, **overrides):
    logs = []
    pending_writes = []
    commits = []
    refreshes = []
    values = {
        "stop_requested": lambda: False,
        "is_paused": lambda: False,
        "reload_candidate": lambda item: (item["candidate"], ""),
        "revalidate": lambda _candidate: ("待发送", ""),
        "has_direct_context": lambda _candidate: True,
        "ensure_page_ready": lambda _candidate: (True, ""),
        "send_candidate": send_candidate,
        "classify_send_exception": lambda _exc: None,
        "persist_pending": lambda candidate, message: pending_writes.append(
            (candidate["geek_id"], message)
        ),
        "persist_success": lambda _candidate, _method: True,
        "commit_state": lambda: commits.append(True),
        "refresh_results": lambda: refreshes.append(True),
        "log": logs.append,
        "uncertain_limit": 2,
        "sleep": lambda _seconds: None,
        "delay_seconds": lambda: 2.5,
    }
    values.update(overrides)
    outcome = ContactController.run_queue(items, list(items), **values)
    return outcome, logs, pending_writes, commits, refreshes


def test_add_candidates_applies_gate_before_persisting_queue_intent():
    items = []
    allowed = _candidate("g1")
    blocked = _candidate("g2", blocked=True)

    first = ContactController.add_candidates(
        items,
        [allowed, blocked],
        source="manual",
        skip_reason=lambda candidate: "业务门禁阻断" if candidate.get("blocked") else "",
    )
    second = ContactController.add_candidates(
        items,
        [allowed],
        source="manual",
        skip_reason=lambda _candidate: "",
    )

    assert first.added_count == 1
    assert first.skipped_reasons == {"业务门禁阻断": 1}
    assert second.skipped_reasons == {"已在队列": 1}
    assert len(items) == 1
    assert items[0]["candidate"]["geek_id"] == "g1"


def test_load_revalidates_sendable_rows_but_preserves_pending_verification():
    sendable = build_contact_queue_item(_candidate("g1"))
    pending = build_contact_queue_item(_candidate("g2"))
    pending["status"] = "待核实"

    outcome = ContactController.load_and_revalidate(
        object(),
        object(),
        load_candidates=lambda _path: [],
        load_queue=lambda _candidates, _path: [sendable, pending],
        revalidate=lambda candidate: (
            ("已跳过", "状态已变化")
            if candidate["geek_id"] == "g1"
            else ("待发送", "")
        ),
        now=lambda: "20260810_120000",
    )

    assert outcome.changed is True
    assert outcome.restored_count == 2
    assert sendable["status"] == "已跳过"
    assert pending["status"] == "待核实"


def test_sync_candidate_propagates_blacklist_to_same_person_across_jobs():
    first = build_contact_queue_item(_candidate("g1", job="Java"))
    second = build_contact_queue_item(_candidate("g1", job="Python"))
    updated = ContactController.sync_candidate(
        [first, second],
        _candidate("g1", job="Java", blacklisted=True, blacklist_reason="不合适"),
        revalidate=lambda candidate: (
            ("已跳过", "已加入黑名单")
            if candidate.get("blacklisted")
            else ("待发送", "")
        ),
        now="20260810_120000",
    )

    assert updated == 2
    assert first["status"] == second["status"] == "已跳过"
    assert second["candidate"]["blacklisted"] is True
    assert second["candidate"]["job_name"] == "Python"


def test_resolve_pending_requires_durable_confirmation_before_state_change():
    sent = build_contact_queue_item(_candidate("g1", name="甲"))
    failed = build_contact_queue_item(_candidate("g2", name="乙"))
    sent["status"] = failed["status"] = "待核实"

    outcome = ContactController.resolve_pending(
        [sent, failed],
        sent=True,
        candidates_path=object(),
        resolver=lambda candidate, **_kwargs: candidate["geek_id"] == "g1",
    )

    assert outcome.resolved_count == 1
    assert outcome.failures == (("乙", "持久化未返回成功"),)
    assert sent["status"] == "已发送"
    assert failed["status"] == "待核实"


def test_run_queue_stops_after_terminal_http_failure_and_leaves_later_rows_pending():
    first = build_contact_queue_item(_candidate("g1", name="甲"))
    second = build_contact_queue_item(_candidate("g2", name="乙"))

    outcome, logs, _writes, _commits, _refreshes = _run_queue(
        [first, second],
        lambda _candidate: (
            False,
            "上下文打招呼失败: HTTP 403 请求未成功",
            "queue_context",
        ),
    )

    assert outcome.counters.failed == 1
    assert outcome.notice is not None
    assert first["status"] == "发送失败"
    assert second["status"] == "待发送"
    assert any("4xx" in message for message in logs)
    assert any("已停止后续发送" in message for message in logs)


def test_run_queue_pauses_after_consecutive_uncertain_results():
    items = [
        build_contact_queue_item(_candidate("g1")),
        build_contact_queue_item(_candidate("g2")),
        build_contact_queue_item(_candidate("g3")),
    ]

    outcome, logs, writes, _commits, _refreshes = _run_queue(
        items,
        lambda _candidate: (None, "button unchanged", "queue_context"),
    )

    assert outcome.pause_requested is True
    assert outcome.counters.pending == 2
    assert [item["status"] for item in items] == ["待核实", "待核实", "待发送"]
    assert len(writes) == 2
    assert any("连续发送结果待核实" in message for message in logs)


def test_run_queue_keeps_page_dependent_candidate_pending_for_manual_switch():
    item = build_contact_queue_item(_candidate("g1", job="Python"))

    outcome, _logs, _writes, _commits, _refreshes = _run_queue(
        [item],
        lambda _candidate: (True, "sent", "queue_list"),
        has_direct_context=lambda _candidate: False,
        ensure_page_ready=lambda _candidate: (False, "请切换到 Python 岗位推荐页"),
    )

    assert outcome.counters.page_waiting == 1
    assert outcome.counters.page_waiting_jobs == {"Python": 1}
    assert item["status"] == "待发送"
    assert "Python" in item["message"]


def test_unexpected_send_exception_recovers_sending_row_as_pending_verification():
    item = build_contact_queue_item(_candidate("g1"))

    try:
        _run_queue(
            [item],
            lambda _candidate: (_ for _ in ()).throw(RuntimeError("connection lost")),
        )
    except RuntimeError as exc:
        assert str(exc) == "connection lost"
    else:
        raise AssertionError("unexpected send exception must propagate")

    assert item["status"] == "发送中"
    persisted = []
    recovered, failures = ContactController.finalize_interrupted(
        [item],
        persist_pending=lambda candidate, message: persisted.append(
            (candidate["geek_id"], message)
        ),
    )
    assert recovered == 1
    assert failures == ()
    assert item["status"] == "待核实"
    assert persisted and persisted[0][0] == "g1"


def test_host_classified_send_exception_becomes_terminal_state_without_tk():
    first = build_contact_queue_item(_candidate("g1"))
    second = build_contact_queue_item(_candidate("g2"))
    notice = ContactNotice("warning", "访问保护", "后续发送已停止", "冷却中")

    outcome, _logs, _writes, _commits, _refreshes = _run_queue(
        [first, second],
        lambda _candidate: (_ for _ in ()).throw(RuntimeError("risk")),
        classify_send_exception=lambda _exc: SendExceptionDecision(
            "发送失败",
            "冷却中",
            "[访问保护] 已停止",
            notice,
        ),
    )

    assert outcome.notice is notice
    assert outcome.counters.failed == 1
    assert first["status"] == "发送失败"
    assert second["status"] == "待发送"


def test_contact_run_counters_return_plain_feedback_data():
    counters = ContactRunCounters(success=2, failed=1, pending=3, skipped=4)
    counters.page_waiting = 1
    counters.page_waiting_jobs["Java"] = 1

    assert counters.feedback(stopped=True, error="stopped") == {
        "success": 2,
        "failed": 1,
        "pending": 3,
        "page_waiting": 1,
        "page_waiting_jobs": {"Java": 1},
        "skipped": 4,
        "stopped": True,
        "error": "stopped",
    }
