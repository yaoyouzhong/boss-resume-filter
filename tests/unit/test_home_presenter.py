from datetime import datetime

import home_presenter


def test_home_summary_uses_one_highest_priority_action_per_candidate():
    candidates = [
        {"geek_id": "ready", "job_name": "岗位 A", "match_score": 70},
        {"geek_id": "queued", "job_name": "岗位 A", "match_score": 70},
        {"geek_id": "review", "job_name": "岗位 A", "match_score": 60},
        {
            "geek_id": "greeted",
            "job_name": "岗位 A",
            "match_score": 80,
            "greet_sent": True,
        },
        {
            "geek_id": "rejected",
            "job_name": "岗位 A",
            "match_score": 90,
            "qualification_status": "rejected",
        },
        {"geek_id": "other-job", "job_name": "岗位 B", "match_score": 80},
    ]
    queue_items = [
        {
            "status": "待核实",
            "candidate": {
                "geek_id": "queued",
                "job_name": "岗位 A",
                "match_score": 70,
            },
        },
        {
            "status": "发送中",
            "candidate": {
                "geek_id": "queue-only",
                "job_name": "岗位 A",
                "match_score": 75,
            },
        },
    ]

    summary = home_presenter.build_home_candidate_summary(
        candidates,
        queue_items,
        "岗位 A",
    )

    assert summary == home_presenter.HomeCandidateSummary(
        passed=4,
        strong=1,
        recommended=2,
        greeted=1,
        pending_contact=1,
        pending_verification=2,
        pending_review=1,
    )
    actions = home_presenter.build_home_candidate_actions(
        candidates,
        queue_items,
        "岗位 A",
    )
    action_groups = {item.candidate["geek_id"]: item.group for item in actions}
    assert action_groups["ready"] == "待打招呼"
    assert action_groups["queued"] == "发送结果待核实"
    assert action_groups["queue-only"] == "发送结果待核实"
    assert action_groups["review"] == "待复核"


def test_home_summary_applies_job_scope_to_candidates_and_queue_items():
    candidates = [
        {"geek_id": "a", "job_name": "岗位 A", "match_score": 70},
        {"geek_id": "b", "job_name": "岗位 B", "match_score": 70},
    ]
    queue_items = [
        {
            "status": "待核实",
            "candidate": {
                "geek_id": "b",
                "job_name": "岗位 B",
                "match_score": 70,
            },
        }
    ]

    summary = home_presenter.build_home_candidate_summary(
        candidates,
        queue_items,
        "岗位 A",
    )

    assert summary.passed == 1
    assert summary.pending_contact == 1
    assert summary.pending_verification == 0


def test_health_copy_does_not_confuse_configuration_with_connectivity():
    configured = home_presenter.api_key_display(
        model_configured=True,
        key_state="present",
    )
    available_chrome = home_presenter.chrome_display("available")
    failed_storage = home_presenter.storage_display(
        error="invalid json",
        exists=True,
        candidate_count=0,
    )

    assert configured.text == "已配置"
    assert "可用" not in configured.text + configured.note
    assert available_chrome.text == "未连接"
    assert available_chrome.tone == "warning"
    assert failed_storage.text == "候选人异常"
    assert "0" not in failed_storage.text + failed_storage.note

    failed_queue = home_presenter.storage_display(
        error="",
        queue_error="invalid queue",
        exists=True,
        candidate_count=8,
    )
    assert failed_queue.text == "联系清单异常"
    assert "候选人数据读取失败" not in failed_queue.note


def test_readiness_summary_prioritizes_real_blockers_without_overstating_api_key():
    ready_storage = home_presenter.storage_display(
        error="",
        exists=True,
        candidate_count=56,
    )
    missing_api = home_presenter.api_key_display(
        model_configured=False,
        key_state="missing",
    )
    offline_browser = home_presenter.chrome_display("offline")

    browser_blocked = home_presenter.build_readiness_display(
        {
            "api": missing_api,
            "browser": offline_browser,
            "storage": ready_storage,
        }
    )
    assert browser_blocked.title == "开始筛选前需要连接 Chrome"
    assert browser_blocked.tone == "warning"

    basic_ready = home_presenter.build_readiness_display(
        {
            "api": missing_api,
            "browser": home_presenter.chrome_display("connected"),
            "storage": ready_storage,
        }
    )
    assert basic_ready.title == "基础筛选已就绪"
    assert "AI 评估" in basic_ready.note

    queue_blocked = home_presenter.build_readiness_display(
        {
            "api": home_presenter.api_key_display(
                model_configured=True,
                key_state="present",
            ),
            "browser": home_presenter.chrome_display("connected"),
            "storage": home_presenter.storage_display(
                error="",
                queue_error="invalid queue",
                exists=True,
                candidate_count=56,
            ),
        }
    )
    assert queue_blocked.title == "联系清单暂不可用"
    assert "候选人数据" not in queue_blocked.title


def test_scan_display_uses_persisted_terminal_record_only():
    display = home_presenter.format_scan_display(
        {
            "finished_at": "2026-08-19T09:05:00",
            "job_name": "Java 工程师",
            "status": "partial",
        },
        now=datetime(2026, 8, 19, 10, 0),
    )

    assert display == home_presenter.ScanDisplay(
        "今天 09:05 · Java 工程师",
        "可能未扫完",
        "warning",
    )
    assert home_presenter.format_scan_display(None).summary.startswith("暂无记录")
    assert home_presenter.classify_run_status("[完成] 扫描完成") == "completed"
    assert home_presenter.classify_run_status("[已停止] 用户停止") == "stopped"
    assert home_presenter.classify_run_status("[出错] 浏览器断开") == "failed"
