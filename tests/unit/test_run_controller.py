from datetime import datetime

from run_controller import (
    RunController,
    RunOutcome,
    RunProgressEvent,
    RunTuning,
    TimestampedLogRedirector,
)


def _tuning():
    return RunTuning(
        api_candidate_limit_default=160,
        greet_context_limit_default=15,
        dom_delay_center=2.75,
        dom_delay_spread=2.5,
        dom_batch_min=5,
        dom_batch_max=10,
        dom_pause_center=11.5,
        dom_pause_spread=7,
    )


def _request(**overrides):
    values = {
        "version": "2.27",
        "rounds": "50",
        "contact_policy": "仅保存筛选结果",
        "selected_job": "全部岗位",
        "api_direct_enabled": True,
        "api_direct_pages": "8",
        "greet_context_capture_enabled": True,
        "greet_context_capture_limit": "15",
        "ai_eval_enabled": False,
        "ai_api_config": {"model": "test-model"},
        "ai_api_key": None,
        "llm_read_timeout": "120",
        "tuning": _tuning(),
    }
    values.update(overrides)
    return RunController.prepare_request(**values)


def _execute(request, scan):
    logs = []
    settings = []
    progress = []

    def callback(*_args, **_kwargs):
        return True

    outcome = RunController().execute(
        request,
        scan=scan,
        log=logs.append,
        settings_sink=lambda rows: settings.extend(rows),
        progress_sink=progress.append,
        stop_event=object(),
        existing_page=object(),
        confirm_callback=callback,
        captcha_callback=callback,
        notice_callback=callback,
        blocking_notice_callback=callback,
        job_match_callback=callback,
        job_config_callback=callback,
    )
    return outcome, logs, settings, progress


def test_prepare_request_normalizes_plain_values_and_never_enables_direct_greeting():
    request = _request(
        rounds="bad",
        selected_job="Java",
        contact_policy="将强烈推荐加入联系清单",
        api_direct_pages="99",
        greet_context_capture_limit="0",
        ai_eval_enabled=True,
        ai_api_key="secret",
    )
    args = request.scan_args()

    assert request.rounds == 50
    assert request.api_direct_pages == 20
    assert request.greet_context_capture_limit == 1
    assert request.job_arg == "Java"
    assert args.greet is False
    assert args.re_greet is False
    assert args.greet_level == "strong"
    assert args.max_candidates == 400
    assert args.api_key == "secret"


def test_missing_api_key_disables_ai_but_keeps_requested_state_for_visible_log():
    request = _request(ai_eval_enabled=True, ai_api_key=None)

    assert request.ai_requested is True
    assert request.ai_eval_enabled is False
    assert request.scan_args().ai_eval is False
    assert request.ai_api_key is None


def test_settings_snapshot_describes_effective_scan_and_contact_limits():
    request = _request(
        api_direct_enabled=False,
        greet_context_capture_enabled=False,
    )
    settings = dict(request.settings_snapshot())

    assert settings["滚动轮次"] == 50
    assert settings["扫描增强"] == "关闭"
    assert settings["最多读取页数"] == "未启用"
    assert settings["后续联系"] == "关闭"
    assert settings["DOM滚动间隔"] == "1.5-4 秒"


def test_execute_emits_progress_events_and_preserves_normal_terminal_summary():
    candidate = {"geek_id": "g1"}

    def scan(_args, *, progress_callback, **_kwargs):
        print("scanner detail")
        progress_callback(25, "正在扫描")
        progress_callback(100, "[完成] 最终保留：1 人")
        return [candidate]

    outcome, logs, settings, progress = _execute(_request(), scan)

    assert outcome == RunOutcome("[完成] 最终保留：1 人", (candidate,))
    assert [event.current for event in progress] == [25, 100]
    assert all(isinstance(event, RunProgressEvent) for event in progress)
    assert any("scanner detail" in message for message in logs)
    assert ("滚动轮次", 50) in settings


def test_execute_converts_keyboard_interrupt_to_stopped_terminal():
    def scan(*_args, **_kwargs):
        raise KeyboardInterrupt

    outcome, logs, _settings, _progress = _execute(_request(), scan)

    assert outcome.final_desc == "[已停止] 用户取消岗位切换"
    assert any("用户取消岗位切换" in message for message in logs)


def test_execute_converts_unexpected_exception_to_error_with_traceback():
    def scan(*_args, **_kwargs):
        raise RuntimeError("connection lost")

    outcome, logs, _settings, _progress = _execute(_request(), scan)

    assert outcome.final_desc == "[出错] connection lost"
    assert any("运行出错：connection lost" in message for message in logs)
    assert any("Traceback" in message for message in logs)


def test_terminal_event_preserves_four_distinct_business_outcomes():
    candidate = ({"geek_id": "g1"},)
    controller = RunController()
    complete = controller.terminal_event(
        RunOutcome("[完成] ok", candidate),
        "将推荐及以上加入联系清单",
    )
    limited = controller.terminal_event(
        RunOutcome("[达到轮次上限] range uncertain", candidate),
        "将推荐及以上加入联系清单",
    )
    interrupted = controller.terminal_event(
        RunOutcome("[扫描中断] saved", candidate),
        "将推荐及以上加入联系清单",
    )
    error = controller.terminal_event(
        RunOutcome("[出错] failed", candidate),
        "将推荐及以上加入联系清单",
    )

    assert complete.status_tone == "success"
    assert complete.should_build_contact_list is True
    assert limited.status_text == "● 本轮处理完成"
    assert limited.should_build_contact_list is True
    assert interrupted.status_tone == "warning"
    assert interrupted.should_build_contact_list is False
    assert error.status_tone == "danger"
    assert error.should_build_contact_list is False


def test_progress_payload_accepts_new_events_and_legacy_dicts():
    event = RunProgressEvent(3, 10, "scanning")

    assert RunController.progress_payload(event) == {
        "current": 3,
        "total": 10,
        "desc": "scanning",
    }
    assert RunController.progress_payload({"current": 1}) == {"current": 1}


def test_timestamped_log_redirector_flushes_partial_line_once():
    logs = []
    redirector = TimestampedLogRedirector(
        logs.append,
        now=lambda: datetime(2026, 8, 10, 12, 34, 56),
    )

    redirector.write("first\npartial")
    redirector.flush()

    assert logs == [
        "[12:34:56] first",
        "[12:34:56] partial",
    ]
