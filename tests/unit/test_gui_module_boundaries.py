import ast
from pathlib import Path
from unittest.mock import Mock, patch

import candidate_diagnostics_presenter
import candidate_cleanup
import candidate_presenter
import changelog_renderer
import contact_presenter
import gui_dialogs
import gui_candidate_actions
import gui_candidate_diagnostics
import gui_candidate_menus
import gui_candidate_review
import gui_candidate_state_dialogs
import gui_contact_queue
import gui_config_page
import gui_data_maintenance_dialogs
import gui_education_page
import gui_home_page
import gui_job_review
import gui_main
import gui_model_catalog_dialog
import gui_result_page
import gui_run_page
import gui_settings_page
import gui_stats_detail
import gui_stats_page
import model_catalog
import resume_parser
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


def test_resume_parser_is_ui_free_and_does_not_mutate_candidate_state():
    forbidden = {
        "bossmaster",
        "gui_main",
        "resume_store",
        "storage",
        "tkinter",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("resume_parser") & forbidden)
    assert callable(resume_parser.parse_resume_text)


def test_resume_import_controller_delegates_file_parsing_only():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _import_resume"):]
    block = block[:block.index("\n    def _revert_resume_eval")]

    assert "parse_resume_text(filepath)" in block
    assert "store_resume_copy(filepath" in block
    assert "mutate_candidates_with_resume_cleanup(" in block
    assert "evaluate_with_resume(" in block
    assert "pdfminer.high_level" not in block
    assert "docx.Document" not in block
    assert "striprtf.striprtf" not in block
    assert "re.sub(" not in block
    assert "open(filepath" not in block


def test_candidate_cleanup_does_not_import_gui_storage_or_network_modules():
    forbidden = {
        "bossmaster",
        "gui_main",
        "storage",
        "tkinter",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("candidate_cleanup") & forbidden)
    assert callable(candidate_cleanup.clear_candidates_in_place)


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
        "gui_candidate_menus",
        "gui_candidate_review",
        "gui_candidate_state_dialogs",
        "gui_candidate_workbench",
        "gui_contact_queue",
        "gui_config_page",
        "gui_data_maintenance_dialogs",
        "gui_education_page",
        "gui_home_page",
        "gui_job_review",
        "gui_result_page",
        "gui_run_page",
        "gui_settings_page",
        "gui_stats_detail",
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


def test_candidate_detail_compatibility_method_is_a_thin_presenter_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _format_candidate_detail"):]
    block = block[:block.index("\n    @staticmethod\n    def _greet_queue_key")]

    assert "candidate_presenter.format_candidate_detail(" in block
    assert "extract_summary_info(candidate.get('summary', ''))" in block
    assert "feedback_reasons=self._feedback_reasons(candidate)" in block
    assert "candidate.get('llm_dimension_scores')" in block
    assert "dimension_labels=dimension_labels" in block
    assert "lines.append" not in block
    assert "tk." not in block


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


def test_stats_detail_dialog_exposes_explicit_callbacks_and_widget_bundle():
    assert gui_stats_detail.StatsDetailCallbacks.__dataclass_fields__.keys() == {
        "row_values",
        "export_candidates",
        "add_to_queue",
        "batch_ai_eval_label",
        "evaluate_candidates",
        "confirm_manual_review",
        "open_review",
        "show_candidate_menu",
        "bind_tooltip",
        "remove_candidates",
        "refresh",
    }
    assert gui_stats_detail.StatsDetailWidgets.__dataclass_fields__.keys() == {
        "window",
        "tree",
        "candidates_ref",
        "greeted_label",
    }


def test_stats_detail_compatibility_methods_keep_filters_in_main_controller():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _show_stats_detail_dialog"):]
    block = block[:block.index("\n    def _get_job_rules_cached")]

    assert "gui_stats_detail.show_stats_detail_dialog(" in block
    assert "gui_stats_detail.StatsDetailCallbacks(" in block
    assert "tk.Toplevel" not in block
    assert "ttk.Treeview" not in block
    assert "load_candidates_all(CANDIDATES_PATH)" in block
    assert "derive_candidate_decision(candidate).screening_result" in block
    assert "self._get_result_date_filter()" in block


def test_stats_detail_delegate_keeps_persistence_and_refresh_callbacks_in_main():
    gui = gui_main.BossFilterGUI.__new__(gui_main.BossFilterGUI)
    gui._stats_detail_row_values = Mock()
    gui._run_export = Mock()
    gui._add_candidates_to_greet_queue = Mock()
    gui._batch_ai_eval_menu_label = Mock()
    gui._ai_eval_selected_candidates = Mock()
    gui._batch_confirm_manual_review = Mock()
    gui._open_candidate_review_workbench = Mock()
    gui._build_candidate_context_menu = Mock()
    gui._bind_detail_tree_tooltip = Mock()
    gui._remove_stats_detail_candidates = Mock()
    refresh = Mock()
    dialog = object()

    with patch.object(
        gui_stats_detail,
        "show_stats_detail_dialog",
        return_value=dialog,
    ) as show_dialog:
        result = gui._show_stats_detail_dialog(
            "推荐",
            [{"geek_id": "candidate-1"}],
            refresh=refresh,
        )

    assert result is dialog
    callbacks = show_dialog.call_args.kwargs["callbacks"]
    assert callbacks.remove_candidates == gui._remove_stats_detail_candidates
    assert callbacks.refresh == refresh
    assert callbacks.export_candidates == gui._run_export
    assert callbacks.open_review == gui._open_candidate_review_workbench


def test_stats_detail_removal_updates_only_controller_confirmed_candidates():
    removed = {"geek_id": "candidate-1", "greet_sent": True}
    retained = {"geek_id": "candidate-2", "greet_sent": False}
    tree = Mock()
    tree._candidate_map = {"row-1": removed, "row-2": retained}
    candidates_ref = [[removed, retained]]
    greeted_label = Mock()

    gui_stats_detail._update_after_removal(
        tree,
        candidates_ref,
        [removed],
        greeted_label,
    )

    assert candidates_ref[0] == [retained]
    assert tree._candidate_map == {"row-2": retained}
    tree.delete.assert_called_once_with("row-1")
    greeted_label.configure.assert_called_once_with(text="，已打招呼 0 人")


def test_stats_detail_controller_rejects_identity_less_removals():
    gui = gui_main.BossFilterGUI.__new__(gui_main.BossFilterGUI)
    gui._remove_candidate_records = Mock()
    removable = {"geek_id": "candidate-1", "job_name": "Java"}
    identity_less = {"name": "缺少身份"}
    path = Mock()
    path.exists.return_value = True

    with patch.object(gui_main, "CANDIDATES_PATH", path):
        removed = gui._remove_stats_detail_candidates([removable, identity_less])

    assert removed == [removable]
    predicate = gui._remove_candidate_records.call_args.args[0]
    assert predicate(removable) is True
    assert predicate({"geek_id": "candidate-2", "job_name": "Java"}) is False


def test_job_review_builder_exposes_explicit_callbacks_and_widget_bundle():
    assert gui_job_review.JobReviewCallbacks.__dataclass_fields__.keys() == {
        "show_feedback_candidates",
        "open_job_config",
        "format_suggestion",
    }
    assert gui_job_review.JobReviewWidgets.__dataclass_fields__.keys() == {
        "window",
        "canvas",
        "content",
        "close",
    }


def test_job_review_compatibility_method_only_wires_business_callbacks():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _show_job_review_workbench"):]
    block = block[:block.index("\n    def _show_job_review_feedback_candidates")]

    assert "gui_job_review.JobReviewCallbacks(" in block
    assert "gui_job_review.build_job_review_workbench(" in block
    assert "self._show_job_review_feedback_candidates(job_name, candidates)" in block
    assert "self._open_job_config_from_review(job_name)" in block
    assert "tk.Toplevel" not in block
    assert "ttk.Frame" not in block


def test_job_review_delegate_keeps_feedback_and_navigation_in_main_controller():
    gui = gui_main.BossFilterGUI.__new__(gui_main.BossFilterGUI)
    gui.stats_time_var = Mock()
    gui.stats_time_var.get.return_value = "近30天"
    gui._show_job_review_feedback_candidates = Mock()
    gui._open_job_config_from_review = Mock()
    gui._format_job_review_suggestion = Mock(return_value=("标题", "详情"))
    candidates = [{"geek_id": "candidate-1"}]
    review = {"candidate_count": 1}
    workbench = object()

    with patch.object(
        gui_job_review,
        "build_job_review_workbench",
        return_value=workbench,
    ) as build:
        result = gui._show_job_review_workbench("Java", candidates, review)

    assert result is workbench
    assert build.call_args.kwargs["time_range"] == "近30天"
    callbacks = build.call_args.kwargs["callbacks"]
    callbacks.show_feedback_candidates()
    callbacks.open_job_config()
    assert callbacks.format_suggestion("建议") == ("标题", "详情")
    gui._show_job_review_feedback_candidates.assert_called_once_with(
        "Java",
        candidates,
    )
    gui._open_job_config_from_review.assert_called_once_with("Java")


def test_result_page_compatibility_method_is_a_thin_builder_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def create_result_page"):]
    block = block[:block.index("\n    def create_education_page")]

    assert "gui_result_page.build_result_page(" in block
    assert "ttk.Treeview" not in block
    assert "tk.Menu" not in block
    assert "self._update_result_tree_columns()" in block
    assert "self._refresh_contact_queue_badge()" in block


def test_education_page_builder_exposes_an_explicit_widget_bundle():
    assert gui_education_page.EducationPageWidgets.__dataclass_fields__.keys() == {
        "page",
        "canvas",
        "scrollable_frame",
        "items",
        "current_id",
        "item_counter",
        "recognition_running",
        "manual_rotation",
        "rotation_locked",
        "file_var",
        "remove_button",
        "queue_card",
        "tree_font",
        "queue_tree",
        "queue_scrollbar",
        "queue_menu",
        "workspace",
        "rotate_button",
        "preview_label",
        "name_var",
        "number_var",
        "status_var",
        "warning_var",
        "recognize_button",
        "fill_button",
        "captcha_button",
    }


def test_education_page_compatibility_method_is_a_thin_builder_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def create_education_page"):]
    block = block[:block.index("\n    def _select_education_images")]

    assert "gui_education_page.build_education_page(" in block
    assert "self.education_page = widgets.page" in block
    assert "self.education_queue_tree = widgets.queue_tree" in block
    assert "ttk.Treeview" not in block
    assert "tk.Menu" not in block
    assert "_recognize_education_image(" not in block


def test_education_page_keeps_ai_browser_and_certificate_actions_in_controller():
    builder = (ROOT / "gui_education_page.py").read_text(encoding="utf-8")
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    assert not (
        {
            "ai_adapter",
            "bossmaster",
            "education_certificate",
            "gui_main",
            "requests",
            "storage",
        }
        & _top_level_imports("gui_education_page")
    )
    for method_name in (
        "_select_education_images",
        "_recognize_education_image",
        "_fill_chsi_page",
        "_solve_captcha",
    ):
        assert f"host.{method_name}" in builder
        assert f"def {method_name}" in source


def test_home_page_builder_exposes_an_explicit_widget_bundle():
    assert gui_home_page.HomePageWidgets.__dataclass_fields__.keys() == {
        "page",
        "job_var",
        "job_combo",
        "stats_vars",
        "stats_labels",
    }


def test_home_page_compatibility_method_is_a_thin_builder_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def create_home_page"):]
    block = block[:block.index("\n    def create_config_page")]

    assert "gui_home_page.build_home_page(" in block
    assert "self.home_page = widgets.page" in block
    assert "self.home_stats_vars = widgets.stats_vars" in block
    assert "ttk.Frame" not in block
    assert "tk.Canvas" not in block


def test_home_page_keeps_data_loading_and_navigation_lifecycle_in_controller():
    forbidden = {
        "bossmaster",
        "gui_main",
        "storage",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("gui_home_page") & forbidden)
    builder = (ROOT / "gui_home_page.py").read_text(encoding="utf-8")
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    assert "load_candidates_all" not in builder
    assert "def refresh_home_stats(self):" not in builder
    assert "def refresh_home_stats(self):" in source
    assert "host.refresh_home_stats()" in builder
    assert "host._request_sidebar_page(" in builder


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


def test_settings_page_compatibility_method_is_a_thin_incremental_delegate():
    assert callable(gui_settings_page.build_settings_content_steps)
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _create_api_config_content_steps"):]
    block = block[:block.index("\n    def load_api_config_to_ui")]

    assert "yield from gui_settings_page.build_settings_content_steps(" in block
    assert "ttk.Treeview" not in block
    assert "tk.Button" not in block


def test_settings_page_keeps_secrets_network_and_data_actions_in_main_controller():
    imports = _top_level_imports("gui_settings_page")
    assert not (
        {
            "ai_adapter",
            "data_recovery",
            "diagnostic_package",
            "gui_main",
            "security",
            "threading",
        }
        & imports
    )

    builder = (ROOT / "gui_settings_page.py").read_text(encoding="utf-8")
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    for method_name in (
        "save_api_config",
        "fetch_model_list",
        "test_api_connection",
        "_export_data_backup",
        "_restore_data_backup",
        "_export_diagnostic_package",
    ):
        assert f"def {method_name}" not in builder
        assert f"def {method_name}" in source


def test_model_catalog_dialog_does_not_import_controller_storage_or_http_clients():
    forbidden = {
        "gui_main",
        "requests",
        "security",
        "storage",
    }
    assert not (_top_level_imports("gui_model_catalog_dialog") & forbidden)
    assert callable(gui_model_catalog_dialog.show_model_catalog_dialog)


def test_model_catalog_service_is_ui_free_and_main_uses_both_extracted_parts():
    forbidden = {"gui_main", "tkinter", "ui_messagebox", "ui_theme"}
    assert not (_top_level_imports("model_catalog") & forbidden)
    assert callable(model_catalog.fetch_model_catalog)
    assert callable(model_catalog.analyze_model_catalog)

    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def fetch_model_list"):]
    block = block[:block.index("\n    def _show_api_key_while_pressed")]
    assert "catalog_response = fetch_model_catalog(" in block
    assert "analysis = analyze_model_catalog(" in block
    assert "gui_model_catalog_dialog.show_model_catalog_dialog(" in block
    assert "requests.get(" not in block
    assert "tk.Listbox(" not in block


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


def test_candidate_blacklist_dialog_exposes_widgets_and_controller_callback():
    assert (
        gui_candidate_state_dialogs.BlacklistReasonDialogWidgets
        .__dataclass_fields__.keys()
    ) == {
        "window",
        "reason_text",
        "save_button",
        "cancel_button",
    }
    source = (ROOT / "gui_candidate_state_dialogs.py").read_text(encoding="utf-8")
    assert "on_confirm(reason)" in source
    assert "update_candidate_records" not in source
    assert "CANDIDATES_PATH" not in source


def test_candidate_blacklist_dialog_compatibility_method_is_a_thin_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _open_blacklist_reason_dialog"):]
    block = block[:block.index("\n    def _update_candidate_blacklist")]

    assert "gui_candidate_state_dialogs.show_blacklist_reason_dialog(" in block
    assert "parent or self.root" in block
    assert "tk.Toplevel" not in block
    assert "ttk.Button" not in block


def test_candidate_blacklist_persistence_remains_in_main_controller():
    builder = (ROOT / "gui_candidate_state_dialogs.py").read_text(encoding="utf-8")
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    assert "def _update_candidate_blacklist" not in builder
    assert "def _update_candidate_blacklist" in source
    assert "update_candidate_records(" in source[
        source.index("def _update_candidate_blacklist"):
        source.index("\n    def _import_resume")
    ]


def test_candidate_followup_dialog_exposes_form_and_save_result_contracts():
    assert gui_candidate_state_dialogs.FollowupSaveResult.__dataclass_fields__.keys() == {
        "saved",
        "request_feedback",
    }
    assert gui_candidate_state_dialogs.FollowupDialogWidgets.__dataclass_fields__.keys() == {
        "window",
        "status_var",
        "status_combo",
        "next_followup_var",
        "next_followup_entry",
        "quick_date_buttons",
        "note_text",
        "error_label",
        "save_button",
        "cancel_button",
    }


def test_candidate_followup_dialog_compatibility_method_is_a_thin_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _mark_candidate_followup"):]
    block = block[:block.index("\n    def _update_candidate_feedback")]

    assert "gui_candidate_state_dialogs.show_followup_dialog(" in block
    assert "default_next_followup=default_next_followup_at" in block
    assert "normalize_followup=normalize_followup_at" in block
    assert "ttk.Combobox" not in block
    assert "tk.Text" not in block
    assert "tk.Toplevel" not in block


def test_candidate_followup_persistence_and_state_sync_remain_in_controller():
    builder = (ROOT / "gui_candidate_state_dialogs.py").read_text(encoding="utf-8")
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    controller = source[source.index("def _save_candidate_followup_from_dialog"):]
    controller = controller[:controller.index("\n    def _mark_candidate_followup")]

    assert "_update_candidate_followup(" not in builder
    assert "mark_candidate_greeted(" not in builder
    assert "apply_followup_state(" not in builder
    assert "self._update_candidate_followup(" in controller
    assert "mark_candidate_greeted(" in controller
    assert "apply_followup_state(" in controller
    assert "self._sync_greet_queue_candidate_state(candidate)" in controller


def test_candidate_feedback_dialog_exposes_form_and_save_result_contracts():
    assert gui_candidate_state_dialogs.FeedbackSaveResult.__dataclass_fields__.keys() == {
        "saved",
    }
    assert gui_candidate_state_dialogs.FeedbackDialogWidgets.__dataclass_fields__.keys() == {
        "window",
        "status_var",
        "status_combo",
        "reason_vars",
        "reason_checkbuttons",
        "note_text",
        "error_label",
        "save_button",
        "cancel_button",
    }


def test_candidate_feedback_compatibility_method_is_a_thin_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _mark_candidate_feedback"):]
    block = block[:block.index("\n    def _format_candidate_detail")]

    assert "gui_candidate_state_dialogs.show_feedback_dialog(" in block
    assert "existing_reasons=self._feedback_reasons(candidate)" in block
    assert "ttk.Combobox" not in block
    assert "tk.Text" not in block
    assert "tk.Toplevel" not in block


def test_candidate_feedback_persistence_and_state_sync_remain_in_controller():
    builder = (ROOT / "gui_candidate_state_dialogs.py").read_text(encoding="utf-8")
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    controller = source[source.index("def _save_candidate_feedback_from_dialog"):]
    controller = controller[:controller.index("\n    def _mark_candidate_feedback")]

    assert "_update_candidate_feedback(" not in builder
    assert "CANDIDATES_PATH" not in builder
    assert "self._update_candidate_feedback(" in controller
    assert "candidate.pop(\"contact_approved_at\", None)" in controller
    assert "self._sync_greet_queue_candidate_state(candidate)" in controller


def test_candidate_menu_builders_only_consume_explicit_state_and_callbacks():
    source = (ROOT / "gui_candidate_menus.py").read_text(encoding="utf-8")

    assert "derive_candidate_decision" not in source
    assert "candidate_greet_skip_reason" not in source
    assert "candidate_can_manual_approve_contact" not in source
    assert "candidate.get(" not in source
    assert "CANDIDATES_PATH" not in source


def test_candidate_menu_compatibility_methods_delegate_tk_construction():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    workflow = source[source.index("def _show_candidate_workflow_context_menu"):]
    workflow = workflow[:workflow.index("\n    def _bind_treeview_sorting")]
    batch = source[source.index("def _show_context_menu"):]
    batch = batch[:batch.index("\n    def _build_candidate_context_menu")]
    single = source[source.index("def _build_candidate_context_menu"):]
    single = single[:single.index("\n    def _find_candidate_by_tree_item")]

    assert "gui_candidate_menus.show_workflow_candidate_menu(" in workflow
    assert "gui_candidate_menus.show_candidate_batch_menu(" in batch
    assert "gui_candidate_menus.show_candidate_context_menu(" in single
    assert "tk.Menu" not in workflow + batch + single
    assert "menu.add_command" not in workflow + batch + single
    assert "derive_candidate_decision(candidate)" in workflow
    assert "derive_candidate_decision(candidate)" in single
    assert "candidate_greet_skip_reason(candidate)" in workflow
    assert "candidate_greet_skip_reason(candidate)" in single


def test_workflow_candidate_menu_preserves_primary_and_quick_action_order():
    host = Mock()
    host.font_scale = 1.0
    host.colors = {
        "primary": "primary",
        "success": "success",
        "danger": "danger",
        "text_primary": "text",
    }
    host.icons.button.side_effect = lambda name, color: (name, color)
    menu = Mock()
    callback_values = {
        name: Mock()
        for name in (
            gui_candidate_menus.WorkflowCandidateMenuCallbacks
            .__dataclass_fields__
        )
    }
    callbacks = gui_candidate_menus.WorkflowCandidateMenuCallbacks(
        **callback_values
    )
    state = gui_candidate_menus.WorkflowCandidateMenuState(
        primary_action="followup",
        needs_review=False,
        can_confirm_review=False,
        needs_send_verification=False,
        has_active_queue_item=False,
        can_queue=True,
        can_approve_queue=False,
        greet_sent=True,
        followup_status="已打招呼",
        blacklisted=False,
    )

    with patch.object(gui_candidate_menus.tk, "Menu", return_value=menu):
        result = gui_candidate_menus.show_workflow_candidate_menu(
            host,
            Mock(),
            100,
            200,
            font_family="Microsoft YaHei UI",
            state=state,
            callbacks=callbacks,
        )

    labels = [item.kwargs["label"] for item in menu.add_command.call_args_list]
    assert result is menu
    assert labels[0] == " 更新跟进"
    assert labels.count(" 更新跟进") == 1
    assert " 查看与复核" in labels
    assert " 加入联系清单" in labels
    assert " 标记已回复" in labels
    assert " 推进到待约面" in labels
    assert " 明天再跟进" in labels
    assert labels[-1] == " 加入黑名单"
    menu.tk_popup.assert_called_once_with(100, 200)


def test_candidate_batch_menu_keeps_one_icon_alignment_prefix():
    host = Mock()
    host.font_scale = 1.0
    host.colors = {
        "primary": "primary",
        "success": "success",
        "text_primary": "text",
    }
    host.icons.button.side_effect = lambda name, color: (name, color)
    menu = Mock()
    callbacks = gui_candidate_menus.CandidateBatchMenuCallbacks(
        **{
            name: Mock()
            for name in (
                gui_candidate_menus.CandidateBatchMenuCallbacks
                .__dataclass_fields__
            )
        }
    )

    with patch.object(gui_candidate_menus.tk, "Menu", return_value=menu):
        gui_candidate_menus.show_candidate_batch_menu(
            host,
            Mock(),
            10,
            20,
            font_family="Microsoft YaHei UI",
            state=gui_candidate_menus.CandidateBatchMenuState(
                ai_label=" 批量AI评估（2人）",
                can_confirm_review=False,
            ),
            callbacks=callbacks,
        )

    labels = [item.kwargs["label"] for item in menu.add_command.call_args_list]
    assert " 批量AI评估（2人）" in labels
    assert "  批量AI评估（2人）" not in labels


def test_clear_candidates_dialog_exposes_choices_and_controller_callback():
    assert (
        gui_data_maintenance_dialogs.ClearCandidatesDialogWidgets
        .__dataclass_fields__.keys()
    ) == {
        "window",
        "choice_var",
        "keep_greeted_var",
        "current_job_radio",
        "all_jobs_radio",
        "keep_greeted_checkbutton",
        "confirm_button",
        "cancel_button",
    }
    source = (ROOT / "gui_data_maintenance_dialogs.py").read_text(
        encoding="utf-8"
    )
    assert "on_confirm(choice, keep_greeted)" in source
    assert "mutate_candidates_with_resume_cleanup" not in source
    assert "CANDIDATES_PATH" not in source


def test_clear_candidates_compatibility_method_is_a_thin_delegate():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def clear_candidates"):]
    block = block[:block.index("\n    def show_help")]

    assert "gui_data_maintenance_dialogs.show_clear_candidates_dialog(" in block
    assert "load_candidates_all(CANDIDATES_PATH)" in block
    assert "tk.Toplevel" not in block
    assert "ttk.Radiobutton" not in block
    assert "mutate_candidates_with_resume_cleanup" not in block


def test_clear_candidates_persistence_remains_in_main_controller():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    builder = (ROOT / "gui_data_maintenance_dialogs.py").read_text(
        encoding="utf-8"
    )
    controller = source[source.index("def _clear_candidates_from_dialog"):]
    controller = controller[:controller.index("\n    def clear_candidates")]

    assert "mutate_candidates_with_resume_cleanup(" in controller
    assert "clear_candidates_in_place(" in controller
    assert "self._regenerate_excel()" in controller
    assert "self.refresh_results()" in controller
    assert "mutate_candidates_with_resume_cleanup(" not in builder


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
