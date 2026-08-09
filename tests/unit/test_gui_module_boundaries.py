import ast
from pathlib import Path
from unittest.mock import Mock, patch

import candidate_diagnostics_presenter
import candidate_presenter
import changelog_renderer
import contact_presenter
import gui_dialogs
import gui_candidate_actions
import gui_candidate_diagnostics
import gui_candidate_review
import gui_contact_queue
import gui_config_page
import gui_main
import gui_result_page
import gui_run_page
import gui_stats_page
import run_presenter
import stats_presenter
import ui_windowing
import updater


ROOT = Path(__file__).resolve().parents[2]


def _top_level_imports(module_name: str) -> set[str]:
    """Return only imports executed while the module itself is imported."""
    tree = ast.parse((ROOT / f"{module_name}.py").read_text(encoding="utf-8"))
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_gui_modules_share_leaf_helpers_without_duplicate_implementations():
    assert gui_main._place_window_centered is ui_windowing.place_window_centered
    assert gui_main._get_windows_monitor_area is ui_windowing.get_windows_monitor_area
    assert gui_dialogs.place_window_centered is ui_windowing.place_window_centered
    assert updater.place_window_centered is ui_windowing.place_window_centered
    assert gui_dialogs.render_changelog_text is changelog_renderer.render_changelog_text
    assert updater.render_changelog_text is changelog_renderer.render_changelog_text


def test_dialog_and_updater_imports_do_not_recreate_gui_main_cycle():
    assert "gui_main" not in _top_level_imports("gui_dialogs")
    assert "gui_main" not in _top_level_imports("updater")
    assert "gui_dialogs" not in _top_level_imports("updater")


def test_presenters_are_leaf_modules_without_ui_storage_or_network_imports():
    forbidden = {
        "bossmaster",
        "contact_queue",
        "gui_main",
        "tkinter",
        "storage",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    for module_name in (
        "candidate_diagnostics_presenter",
        "candidate_presenter",
        "contact_presenter",
        "run_presenter",
        "stats_presenter",
    ):
        assert not (_top_level_imports(module_name) & forbidden)


def test_gui_builders_do_not_import_gui_main_storage_or_network_modules():
    forbidden = {
        "bossmaster",
        "contact_queue",
        "gui_main",
        "storage",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    for module_name in (
        "gui_candidate_actions",
        "gui_candidate_diagnostics",
        "gui_candidate_review",
        "gui_candidate_workbench",
        "gui_contact_queue",
        "gui_config_page",
        "gui_result_page",
        "gui_run_page",
        "gui_stats_page",
    ):
        assert not (_top_level_imports(module_name) & forbidden)


def test_gui_compatibility_methods_delegate_to_presenters():
    candidate = {"match_score": 70, "recommend_level": "推荐"}
    BossFilterGUI = gui_main.BossFilterGUI
    assert BossFilterGUI._candidate_gender_display(candidate) == (
        candidate_presenter.candidate_gender_display(candidate)
    )
    assert BossFilterGUI._greet_queue_readiness_label(candidate) == (
        contact_presenter.greet_queue_readiness_label(candidate)
    )
    assert BossFilterGUI._format_terminal_log_text("[完成] ok") == (
        run_presenter.format_terminal_log_text("[完成] ok")
    )
    assert BossFilterGUI._stats_time_cutoff("全部") is None
    assert BossFilterGUI._clip_table_text("a" * 10, 5) == (
        candidate_diagnostics_presenter.clip_table_text("a" * 10, 5)
    )
    assert BossFilterGUI._format_job_review_suggestion("规则过宽：检查关键词") == (
        stats_presenter.format_job_review_suggestion("规则过宽：检查关键词")
    )


def test_stats_page_builder_exposes_an_explicit_widget_bundle():
    assert gui_stats_page.StatsPageWidgets.__dataclass_fields__.keys() == {
        "page",
        "job_var",
        "job_combo",
        "time_var",
        "summary_vars",
        "tree",
    }


def test_result_page_builder_exposes_an_explicit_widget_bundle():
    assert gui_result_page.ResultPageWidgets.__dataclass_fields__.keys() == {
        "page",
        "job_var",
        "job_combo",
        "time_range_var",
        "time_range_combo",
        "custom_date_frame",
        "stats_vars",
        "stats_greeted",
        "stats_click",
        "stat_icon_canvases",
        "search_var",
        "search_entry",
        "search_clear_hint",
        "view_label",
        "view_var",
        "view_combo",
        "count_var",
        "show_blacklist_var",
        "tree",
        "tree_font",
        "empty_state",
        "review_button",
        "greet_queue_button",
        "greet_queue_badge",
        "more_menu_button",
        "more_menu",
    }


def test_result_page_compatibility_method_is_a_thin_builder_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def create_result_page"):]
    block = block[:block.index("\n    def create_education_page")]

    assert "gui_result_page.build_result_page(" in block
    assert "ttk.Treeview" not in block
    assert "tk.Menu" not in block
    assert "self._update_result_tree_columns()" in block
    assert "self._refresh_contact_queue_badge()" in block


def test_run_page_compatibility_method_is_a_thin_incremental_delegate():
    assert callable(gui_run_page.build_run_page_steps)
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _create_run_page_steps"):]
    block = block[:block.index("\n    def _schedule_run_page_api_key_check")]

    assert "yield from gui_run_page.build_run_page_steps(" in block
    assert "ttk.Frame" not in block
    assert "tk.Text" not in block


def test_config_page_compatibility_method_is_a_thin_incremental_delegate():
    assert callable(gui_config_page.build_config_page_steps)
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _create_config_page_steps"):]
    block = block[:block.index("\n    def create_api_config_page")]

    assert "yield from gui_config_page.build_config_page_steps(" in block
    assert "ttk.Frame" not in block
    assert "tk.Text" not in block


def test_config_page_keeps_loading_saving_and_diagnostics_in_main_controller():
    imports = _top_level_imports("gui_config_page")
    assert not ({"job_config_store", "job_config_diagnostics"} & imports)

    builder = (ROOT / "gui_config_page.py").read_text(encoding="utf-8")
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    for method_name in (
        "load_job_to_form",
        "save_current_job",
        "parse_requirement",
    ):
        assert f"def {method_name}" not in builder
        assert f"def {method_name}" in source


def test_run_page_keeps_api_key_resolution_in_main_controller():
    imports = _top_level_imports("gui_run_page")
    assert not ({"security", "threading"} & imports)
    assert "_get_api_key_cached" not in (
        ROOT / "gui_run_page.py"
    ).read_text(encoding="utf-8")

    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _schedule_run_page_api_key_check"):]
    block = block[:block.index("\n    def create_result_page")]
    assert "self._get_api_key_cached(" in block
    assert "threading.Thread(" in block


def test_run_page_api_key_check_does_not_start_after_leaving_run_page():
    gui = gui_main.BossFilterGUI.__new__(gui_main.BossFilterGUI)
    gui.root = Mock()
    gui.current_page_index = gui_main.PageIndex.HOME
    gui.run_page = object()
    gui.api_config = {"api_provider": "openai", "base_url": ""}
    gui._get_api_key_cached = Mock()
    gui._update_ai_eval_status = Mock()
    gui.run_on_ui = Mock()

    with patch.object(gui_main.threading, "Thread") as thread:
        gui._schedule_run_page_api_key_check(Mock())
        delay, callback = gui.root.after.call_args.args
        callback()

    assert delay == 150
    thread.assert_not_called()
    gui._get_api_key_cached.assert_not_called()


def test_candidate_diagnostics_compatibility_method_is_a_thin_dialog_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _show_candidate_state_diagnostics_dialog"):]
    block = block[:block.index("\n    @staticmethod\n    def _clip_table_text")]

    assert "gui_candidate_diagnostics.show_candidate_state_diagnostics_dialog(" in block
    assert "tk.Toplevel" not in block


def test_candidate_diagnostics_delegate_keeps_loading_and_export_in_gui_main():
    gui = gui_main.BossFilterGUI.__new__(gui_main.BossFilterGUI)
    candidates = [{"geek_id": "candidate-1", "job_name": "Java"}]
    issues = [object()]
    gui._load_candidates_for_state_diagnostics = Mock(
        return_value=(candidates, "Java")
    )
    dialog = object()

    with (
        patch.object(gui_main, "diagnose_candidate_states", return_value=issues),
        patch.object(gui_main, "_export_candidate_state_diagnostics_report") as export,
        patch.object(
            gui_candidate_diagnostics,
            "show_candidate_state_diagnostics_dialog",
            return_value=dialog,
        ) as show_dialog,
    ):
        result = gui._show_candidate_state_diagnostics_dialog(
            "Java",
            candidates,
            issues,
            "summary",
        )
        kwargs = show_dialog.call_args.kwargs
        loaded = kwargs["load_diagnostics"]()
        parent = object()
        kwargs["export_report"](parent)

    assert result is dialog
    assert loaded == (candidates, issues)
    export.assert_called_once_with(
        "summary",
        parent,
    )


def test_daily_actions_compatibility_method_is_a_thin_dialog_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _show_daily_candidate_actions_dialog"):]
    block = block[:block.index("\n    def _show_candidate_state_diagnostics_dialog")]

    assert "gui_candidate_actions.show_daily_candidate_actions_dialog(" in block
    assert "tk.Toplevel" not in block


def test_daily_actions_delegate_keeps_loading_and_export_in_gui_main():
    gui = gui_main.BossFilterGUI.__new__(gui_main.BossFilterGUI)
    candidates = [{"geek_id": "candidate-1", "job_name": "Java"}]
    items = [object()]
    refreshed_items = [object()]
    gui._load_candidates_for_daily_actions = Mock(
        return_value=(candidates, "Java")
    )
    dialog = object()

    with (
        patch.object(
            gui_main,
            "build_daily_candidate_actions",
            return_value=refreshed_items,
        ),
        patch.object(gui_main, "_export_daily_candidate_actions_report") as export,
        patch.object(
            gui_candidate_actions,
            "show_daily_candidate_actions_dialog",
            return_value=dialog,
        ) as show_dialog,
    ):
        result = gui._show_daily_candidate_actions_dialog("Java", items)
        kwargs = show_dialog.call_args.kwargs
        loaded = kwargs["load_actions"]()
        parent = object()
        kwargs["export_report"](parent)

    assert result is dialog
    assert loaded == refreshed_items
    export.assert_called_once_with(items, parent)


def test_candidate_review_builder_exposes_an_explicit_widget_bundle():
    assert gui_candidate_review.CandidateReviewWidgets.__dataclass_fields__.keys() == {
        "window",
        "title_var",
        "meta_var",
        "position_var",
        "previous_button",
        "next_button",
        "result_var",
        "reason_var",
        "communication_var",
        "state_labels",
        "primary_section",
        "primary_label",
        "primary_actions",
        "secondary_section",
        "secondary_actions",
        "view_buttons",
        "view_indicators",
        "view_frames",
        "summary_text",
        "detail_text",
    }


def test_candidate_review_compatibility_method_delegates_window_construction():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _open_candidate_review_workbench"):]
    block = block[:block.index("\n    def _on_greet_queue_motion")]

    assert "gui_candidate_review.build_candidate_review_workbench(" in block
    assert "tk.Toplevel" not in block


def test_candidate_review_view_helpers_preserve_selection_and_toggle_behavior():
    frames = {"summary": Mock(), "detail": Mock()}
    buttons = {"summary": Mock(), "detail": Mock()}
    indicators = {"summary": Mock(), "detail": Mock()}
    colors = {
        "banner_info_bg": "selected-bg",
        "bg_card": "normal-bg",
        "primary": "selected-fg",
        "text_secondary": "normal-fg",
        "bg_hover": "hover-bg",
        "text_primary": "hover-fg",
    }

    assert gui_candidate_review.show_candidate_review_view(
        "detail",
        frames=frames,
        buttons=buttons,
        indicators=indicators,
        colors=colors,
    ) == "break"
    frames["detail"].tkraise.assert_called_once_with()
    buttons["detail"].configure.assert_called_once_with(
        bg="selected-bg",
        fg="selected-fg",
        activebackground="selected-bg",
        activeforeground="selected-fg",
    )
    indicators["summary"].configure.assert_called_once_with(bg="normal-bg")

    show_view = Mock(return_value="break")
    assert gui_candidate_review.toggle_candidate_review_view(
        "summary",
        show_view,
    ) == "break"
    show_view.assert_called_once_with("detail")


def test_candidate_workbench_compatibility_methods_delegate_to_shared_primitives():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _create_candidate_workbench_header"):]
    block = block[:block.index("\n    def _show_daily_candidate_actions_dialog")]

    assert "gui_candidate_workbench.create_header(" in block
    assert "gui_candidate_workbench.create_metrics(" in block
    assert "gui_candidate_workbench.navigation_style(" in block
    assert "gui_candidate_workbench.apply_navigation_tags(" in block
    assert "ttk.Frame(" not in block


def test_contact_queue_builder_exposes_explicit_callbacks_and_widget_bundle():
    assert gui_contact_queue.ContactQueueCallbacks.__dataclass_fields__.keys() == {
        "start",
        "pause",
        "resume",
        "group_selected",
        "confirm_sent",
        "confirm_not_sent",
        "retry_failed",
        "remove_selected",
        "show_selected_detail",
        "update_action_states",
        "row_motion",
        "hide_tooltip",
        "context_menu",
        "select_all",
        "close",
    }
    assert gui_contact_queue.ContactQueueWidgets.__dataclass_fields__.keys() == {
        "window",
        "metric_vars",
        "summary_var",
        "action_scope_var",
        "start_button",
        "transport_frame",
        "pause_button",
        "resume_button",
        "status_filter_var",
        "group_tree",
        "detail_title_var",
        "detail_summary_var",
        "selection_var",
        "selected_action_buttons",
        "confirm_sent_button",
        "confirm_not_sent_button",
        "retry_button",
        "remove_button",
        "tree",
    }


def test_contact_queue_compatibility_method_delegates_only_window_construction():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _show_greet_queue_dialog"):]
    block = block[:block.index("\n    def _open_greet_queue_from_result")]

    assert "gui_contact_queue.build_contact_queue_workbench(" in block
    assert "gui_contact_queue.ContactQueueCallbacks(" in block
    assert "tk.Toplevel" not in block
    assert "_run_greet_queue_worker" not in block


def test_contact_queue_delegate_keeps_refresh_and_business_callbacks_in_gui_main():
    gui = gui_main.BossFilterGUI.__new__(gui_main.BossFilterGUI)
    gui.root = object()
    gui.greet_queue_window = None
    gui.greet_queue_items = [{"status": "待发送"}]
    gui.greet_queue_selected_group = "待发送"
    gui._ensure_greet_queue_loaded = Mock()
    gui._refresh_greet_queue_dialog = Mock()
    widgets = Mock()
    widgets.window = Mock()

    with patch.object(
        gui_contact_queue,
        "build_contact_queue_workbench",
        return_value=widgets,
    ) as build:
        gui._show_greet_queue_dialog()

    callbacks = build.call_args.kwargs["callbacks"]
    assert callbacks.start == gui._start_greet_queue
    assert callbacks.pause == gui._pause_greet_queue
    assert callbacks.retry_failed == gui._retry_failed_greet_queue_items
    assert callbacks.close == gui._close_greet_queue_window
    gui._refresh_greet_queue_dialog.assert_called_once_with()
    widgets.window.deiconify.assert_called_once_with()
