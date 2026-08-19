import tempfile
import types
from pathlib import Path
from unittest.mock import Mock, patch

import gui_main
import home_presenter
from gui_main import BossFilterGUI, PageIndex


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Label:
    def __init__(self):
        self.config = {}

    def configure(self, **kwargs):
        self.config.update(kwargs)


def _dashboard_host():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.colors = {
        "primary": "blue",
        "purple": "purple",
        "success": "green",
        "warning": "orange",
        "danger": "red",
        "danger_text": "darkred",
        "text_secondary": "gray",
        "text_muted": "gray",
        "home_primary": "blue",
        "home_success": "green",
        "home_warning": "orange",
        "home_danger": "red",
        "home_secondary": "gray",
    }
    gui.home_job_var = _Var("全部岗位")
    gui.home_stats_vars = {
        key: _Var()
        for key in ("total_home", "strong_home", "recommended_home", "greeted_home")
    }
    gui.home_task_vars = {
        key: _Var()
        for key in ("pending_contact", "pending_verification", "pending_review")
    }
    gui.home_task_action_vars = {
        key: _Var()
        for key in ("pending_contact", "pending_verification", "pending_review")
    }
    gui.home_task_labels = {
        "pending_contact": (_Label(), _Label(), "home_primary"),
        "pending_verification": (_Label(), _Label(), "home_primary"),
        "pending_review": (_Label(), _Label(), "home_primary"),
    }
    gui.home_task_widgets = {}
    gui.home_task_total_var = _Var()
    gui.home_task_headline_prefix_var = _Var()
    gui.home_task_headline_suffix_var = _Var()
    gui.home_health_vars = {key: _Var() for key in ("api", "browser", "storage")}
    gui.home_health_note_vars = {
        key: _Var() for key in ("api", "browser", "storage")
    }
    gui.home_health_labels = {key: _Label() for key in ("api", "browser", "storage")}
    gui.home_health_widgets = {}
    gui.home_scan_summary_var = _Var()
    gui.home_scan_status_var = _Var()
    gui.home_scan_status_label = _Label()
    gui._home_stats_fingerprint = None
    gui._run_preferences = {}
    gui.current_page_index = PageIndex.HOME
    return gui


def test_home_refresh_combines_candidate_and_contact_state_without_double_counting():
    gui = _dashboard_host()
    candidates = [
        {"geek_id": "ready", "job_name": "岗位 A", "match_score": 70},
        {"geek_id": "verify", "job_name": "岗位 A", "match_score": 70},
    ]
    queue = {
        "items": [
            {
                "status": "待核实",
                "candidate": {
                    "geek_id": "verify",
                    "job_name": "岗位 A",
                    "match_score": 70,
                },
            }
        ]
    }
    with tempfile.TemporaryDirectory() as temp_dir:
        candidates_path = Path(temp_dir) / "candidates.json"
        queue_path = Path(temp_dir) / "queue.json"
        candidates_path.write_text("{}", encoding="utf-8")
        queue_path.write_text("{}", encoding="utf-8")
        with (
            patch.object(gui_main, "CANDIDATES_PATH", candidates_path),
            patch.object(gui_main, "CONTACT_QUEUE_PATH", queue_path),
            patch.object(gui_main, "load_candidates_all", return_value=candidates),
            patch.object(gui_main, "load_contact_queue_snapshot", return_value=queue),
        ):
            gui.refresh_home_stats()

    assert gui.home_stats_vars["recommended_home"].value == "2"
    assert gui.home_task_vars["pending_contact"].value == "1"
    assert gui.home_task_vars["pending_verification"].value == "1"
    assert gui.home_task_total_var.value == "2"
    assert gui.home_task_headline_prefix_var.value == "今天有"
    assert gui.home_task_headline_suffix_var.value == "位候选人需要处理"
    assert gui.home_health_vars["storage"].value == "2 条"
    assert gui.home_task_action_vars["pending_contact"].value == "查看待联系  →"
    assert gui.home_task_action_vars["pending_review"].value == "当前无需处理"
    assert gui.home_task_labels["pending_review"][0].config["foreground"] == "gray"


def test_home_health_checks_keyring_and_chrome_off_the_ui_path():
    gui = _dashboard_host()
    gui._home_health_refresh_token = 0
    gui.api_config = {
        "api_provider": "deepseek",
        "base_url": "https://example.invalid/v1",
        "model": "deepseek-chat",
    }
    gui.browser_connected = False
    gui.browser_address = "127.0.0.1:9333"
    gui._get_api_key_cached = Mock(return_value="secret")
    gui.run_on_ui = lambda callback: callback()
    controller = Mock()
    controller.address_candidates.return_value = ("127.0.0.1:9333",)

    def start_immediately():
        thread_factory.call_args.kwargs["target"]()

    with (
        patch.object(gui_main, "_browser_controller_for", return_value=controller),
        patch.object(gui_main, "is_debug_port_open", return_value=True),
        patch.object(gui_main.threading, "Thread") as thread_factory,
    ):
        thread_factory.return_value.start.side_effect = start_immediately
        gui.refresh_home_status()

    assert gui.home_health_vars["api"].value == "已配置"
    assert gui.home_health_vars["browser"].value == "未连接"
    assert "可用" not in gui.home_health_note_vars["api"].value
    assert gui._home_health_check_running is False


def test_home_health_skips_repainting_an_unchanged_status():
    gui = _dashboard_host()
    display = home_presenter.StatusDisplay(
        "已配置", "success", "本机安全凭据已保存"
    )
    gui._home_health_displays = {"api": display}
    gui.home_health_labels["api"] = Mock()

    gui._apply_home_health("api", display)

    gui.home_health_labels["api"].configure.assert_not_called()


def test_home_scan_display_skips_repainting_unchanged_content():
    gui = _dashboard_host()
    gui.home_scan_summary_var = Mock()
    gui.home_scan_status_var = Mock()
    gui.home_scan_status_label = Mock()

    gui._apply_home_scan_display()
    gui.home_scan_summary_var.reset_mock()
    gui.home_scan_status_var.reset_mock()
    gui.home_scan_status_label.reset_mock()
    gui._apply_home_scan_display()

    gui.home_scan_summary_var.set.assert_not_called()
    gui.home_scan_status_var.set.assert_not_called()
    gui.home_scan_status_label.configure.assert_not_called()


def test_run_terminal_persists_real_last_scan_scope_and_status():
    gui = _dashboard_host()
    terminal = types.SimpleNamespace(final_desc="[可能未扫完] 页面连接中断")
    request = types.SimpleNamespace(selected_job="Java 工程师")

    with patch.object(gui_main, "_save_run_preferences") as save:
        gui._remember_last_scan(terminal, request)

    record = gui._run_preferences["last_scan"]
    assert record["job_name"] == "Java 工程师"
    assert record["status"] == "partial"
    assert "T" in record["finished_at"]
    save.assert_called_once_with(gui._run_preferences)


def test_pending_review_opens_exact_home_workbench_scope():
    gui = _dashboard_host()
    gui._show_home_action_group = Mock()

    gui.on_home_task_click("pending_review")

    gui._show_home_action_group.assert_called_once_with(
        {"待复核"},
        "暂无待复核候选人。",
        filter_label="待复核",
        guidance="逐一确认筛选判断，再决定联系或淘汰",
    )


def test_pending_verification_preserves_home_job_scope_before_queue_resolution():
    gui = _dashboard_host()
    gui._show_home_action_group = Mock()

    gui.on_home_task_click("pending_verification")

    gui._show_home_action_group.assert_called_once_with(
        {"发送结果待核实"},
        "暂无发送结果待核实的候选人。",
        filter_label="待核实",
        guidance="核对发送结果，确认后再继续联系",
    )


def test_pending_contact_opens_exact_home_workbench_scope():
    gui = _dashboard_host()
    gui._show_home_action_group = Mock()

    gui.on_home_task_click("pending_contact")

    gui._show_home_action_group.assert_called_once_with(
        {"待打招呼", "待外部联系"},
        "暂无待联系候选人。",
        filter_label="待联系",
        guidance="按候选人来源完成联系前确认和后续处理",
    )


def test_home_task_slice_is_a_preset_filter_of_the_shared_daily_workbench():
    gui = _dashboard_host()
    pending = types.SimpleNamespace(group="待复核")
    other = types.SimpleNamespace(group="待打招呼")
    gui._load_home_action_items = Mock(return_value=([pending, other], "岗位 A"))
    gui._show_daily_candidate_actions_dialog = Mock()

    gui._show_home_action_group(
        {"待复核"},
        "暂无待复核候选人。",
        filter_label="待复核",
        guidance="逐一确认筛选判断，再决定联系或淘汰",
    )

    call = gui._show_daily_candidate_actions_dialog.call_args
    assert call.args[0] == "岗位 A / 快捷筛选：待复核"
    assert call.args[1] == [pending]
    assert call.kwargs["title"] == "今日待办"
    assert call.kwargs["subtitle"].startswith("首页快捷筛选：待复核")


def test_home_system_settings_entry_resets_the_ready_page_to_the_top():
    gui = _dashboard_host()
    gui.app_shell = Mock()
    gui.api_canvas = Mock()
    gui.api_canvas.winfo_exists.return_value = True

    gui.open_home_system_settings()

    call = gui.app_shell.request_sidebar_page.call_args
    assert call.args == (PageIndex.SETTINGS,)
    call.kwargs["on_ready"]()
    gui.api_canvas.yview_moveto.assert_called_once_with(0.0)


def test_home_api_health_action_uses_the_same_scroll_reset_entry():
    gui = _dashboard_host()
    gui.open_home_system_settings = Mock()

    gui.on_home_health_click("api")

    gui.open_home_system_settings.assert_called_once_with()


def test_home_refresh_retries_transient_candidate_error_without_file_change():
    gui = _dashboard_host()
    candidate = {"geek_id": "retry", "job_name": "岗位 A", "match_score": 70}
    with tempfile.TemporaryDirectory() as temp_dir:
        candidates_path = Path(temp_dir) / "candidates.json"
        queue_path = Path(temp_dir) / "queue.json"
        candidates_path.write_text("{}", encoding="utf-8")
        queue_path.write_text("{}", encoding="utf-8")
        candidate_loader = Mock(side_effect=[ValueError("temporary"), [candidate]])
        with (
            patch.object(gui_main, "CANDIDATES_PATH", candidates_path),
            patch.object(gui_main, "CONTACT_QUEUE_PATH", queue_path),
            patch.object(gui_main, "load_candidates_all", candidate_loader),
            patch.object(
                gui_main,
                "load_contact_queue_snapshot",
                return_value={"items": []},
            ),
        ):
            gui.refresh_home_stats()
            assert gui._home_stats_fingerprint is None
            gui.refresh_home_stats()

    assert candidate_loader.call_count == 2
    assert gui.home_stats_vars["recommended_home"].value == "1"
    assert gui.home_task_vars["pending_contact"].value == "1"
    assert gui._home_stats_fingerprint is not None


def test_home_refresh_reports_queue_failure_without_hiding_candidate_summary():
    gui = _dashboard_host()
    candidate = {"geek_id": "ready", "job_name": "岗位 A", "match_score": 70}
    with tempfile.TemporaryDirectory() as temp_dir:
        candidates_path = Path(temp_dir) / "candidates.json"
        queue_path = Path(temp_dir) / "queue.json"
        candidates_path.write_text("{}", encoding="utf-8")
        queue_path.write_text("{}", encoding="utf-8")
        with (
            patch.object(gui_main, "CANDIDATES_PATH", candidates_path),
            patch.object(gui_main, "CONTACT_QUEUE_PATH", queue_path),
            patch.object(gui_main, "load_candidates_all", return_value=[candidate]),
            patch.object(
                gui_main,
                "load_contact_queue_snapshot",
                side_effect=ValueError("invalid queue"),
            ),
        ):
            gui.refresh_home_stats()

    assert gui.home_stats_vars["recommended_home"].value == "1"
    assert gui.home_task_headline_prefix_var.value == "联系清单暂不可用"
    assert gui.home_health_vars["storage"].value == "联系清单异常"
    assert "候选人数据读取失败" not in gui.home_health_note_vars["storage"].value
    assert gui._home_stats_fingerprint is None


def test_readiness_banner_updates_rail_and_icon_with_semantic_tone():
    widgets = types.SimpleNamespace(
        readiness_banner=Mock(),
        readiness_rail=Mock(),
        readiness_icon_label=Mock(),
        readiness_title_label=Mock(),
        readiness_note_label=Mock(),
    )
    colors = {
        "home_success": "green",
        "home_warning": "orange",
        "home_danger": "red",
        "home_secondary": "gray",
        "home_success_tint": "lightgreen",
        "home_warning_tint": "lightorange",
        "home_danger_tint": "lightred",
        "home_surface_quiet": "white",
    }

    gui_main.gui_home_page.update_readiness_banner(widgets, colors, "success")

    widgets.readiness_rail.configure.assert_called_once_with(background="green")
    assert widgets.readiness_icon_label.configure.call_args.kwargs["text"] == "✓"
    assert (
        widgets.readiness_icon_label.configure.call_args.kwargs["background"]
        == "lightgreen"
    )
