import ast
from pathlib import Path
from unittest.mock import Mock, patch

import api_connectivity
import browser_connection
import browser_controller
import candidate_controller
import candidate_diagnostics_presenter
import candidate_cleanup
import candidate_scan_policy
import candidate_presenter
import changelog_renderer
import contact_controller
import contact_presenter
import data_maintenance_controller
import education_controller
import gui_dialogs
import gui_app_shell
import gui_candidate_actions
import gui_candidate_diagnostics
import gui_candidate_menus
import gui_candidate_review
import gui_candidate_state_dialogs
import gui_contact_queue
import gui_config_page
import gui_data_maintenance_dialogs
import gui_education_page
import gui_feedback_support
import gui_home_page
import gui_input_support
import gui_job_review
import gui_layout_support
import gui_main
import gui_model_catalog_dialog
import gui_result_page
import gui_run_page
import gui_scroll_support
import gui_settings_page
import gui_stats_detail
import gui_stats_page
import gui_style_setup
import gui_widget_support
import model_catalog
import resume_parser
import resume_import_service
import result_controller
import run_controller
import settings_controller
import stats_presenter
import ui_windowing
import updater


ROOT = Path(__file__).resolve().parents[2]


def _all_imports(module_name: str) -> set[str]:
    """Return imports from every lexical scope so lazy imports cannot bypass policy."""
    tree = ast.parse((ROOT / f"{module_name}.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


# Existing focused assertions use this historical helper name. Its behavior is
# intentionally all-scope; new policy tests use the explicit name above.
_top_level_imports = _all_imports


GUI_SUPPORT_MODULES = {
    "gui_app_shell",
    "gui_dialogs",
    "gui_feedback_support",
    "gui_input_support",
    "gui_layout_support",
    "gui_main",
    "gui_model_catalog_dialog",
    "gui_scroll_support",
    "gui_style_setup",
    "gui_widget_support",
}
GUI_BUILDER_MODULES = {
    path.stem for path in ROOT.glob("gui_*.py")
} - GUI_SUPPORT_MODULES


def _thread_target_function_names(module_name: str) -> set[str]:
    tree = ast.parse((ROOT / f"{module_name}.py").read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callable_name = ""
        if isinstance(node.func, ast.Name):
            callable_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callable_name = node.func.attr
        if callable_name != "Thread":
            continue
        target = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "target"),
            None,
        )
        if isinstance(target, ast.Name):
            targets.add(target.id)
        elif isinstance(target, ast.Attribute):
            targets.add(target.attr)
    return targets


def _direct_attribute_calls(function: ast.FunctionDef) -> set[str]:
    """Return calls in a function body, excluding nested callback definitions."""
    attributes: set[str] = set()

    class DirectCallVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            return

        def visit_AsyncFunctionDef(self, node):
            return

        def visit_Lambda(self, node):
            return

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute):
                attributes.add(node.func.attr)
            self.generic_visit(node)

    visitor = DirectCallVisitor()
    for statement in function.body:
        visitor.visit(statement)
    return attributes


def test_gui_modules_share_leaf_helpers_without_duplicate_implementations():
    assert gui_main._place_window_centered is ui_windowing.place_window_centered
    assert gui_main._get_windows_monitor_area is ui_windowing.get_windows_monitor_area
    assert gui_dialogs.place_window_centered is ui_windowing.place_window_centered
    assert updater.place_window_centered is ui_windowing.place_window_centered
    assert gui_dialogs.render_changelog_text is changelog_renderer.render_changelog_text
    assert updater.render_changelog_text is changelog_renderer.render_changelog_text


def test_toplevel_creation_is_explicit_and_does_not_patch_tkinter_globally():
    gui_source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    assert "tk.Toplevel =" not in gui_source
    for path in ROOT.glob("*.py"):
        if path.name == "ui_windowing.py":
            continue
        assert "tk.Toplevel(" not in path.read_text(encoding="utf-8")

    window = Mock()
    window.tk.call.return_value = "close-window"
    with patch.object(ui_windowing.tk, "Toplevel", return_value=window) as factory:
        created = ui_windowing.create_toplevel("parent")
    assert created is window
    factory.assert_called_once_with("parent")
    escape_callback = window.bind.call_args.args[1]
    escape_callback()
    window.tk.call.assert_any_call(
        "wm", "protocol", window._w, "WM_DELETE_WINDOW"
    )
    window.tk.call.assert_any_call("close-window")


def test_gui_thread_targets_do_not_schedule_tk_directly():
    for module_name in ("gui_main", "gui_dialogs", "gui_model_catalog_dialog"):
        tree = ast.parse(
            (ROOT / f"{module_name}.py").read_text(encoding="utf-8")
        )
        target_names = _thread_target_function_names(module_name)
        assert target_names
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name in target_names
        ):
            assert not (
                _direct_attribute_calls(function) & {"after", "after_idle"}
            ), f"{module_name}.{function.name} must dispatch through run_on_ui"


def test_manual_tk_scripts_disable_live_data_and_update_side_effects():
    for relative_path in (
        "tests/manual/bench_page_switch.py",
        "tests/manual/check_widget_tree.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        import_index = source.index("import gui_main")
        prefix = source[:import_index]
        assert "BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION" in prefix
        assert "BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE" in prefix
        assert "BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE" in prefix


def test_dialog_and_updater_imports_do_not_recreate_gui_main_cycle():
    assert "gui_main" not in _all_imports("gui_dialogs")
    assert "updater" not in _all_imports("gui_dialogs")
    assert "gui_main" not in _all_imports("updater")
    assert "gui_dialogs" not in _all_imports("updater")


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


def test_resume_import_controller_delegates_parsing_and_persistence_boundaries():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _import_resume"):]
    block = block[:block.index("\n    def _revert_resume_eval")]

    assert "_candidate_controller_for(self).import_resume(" in block
    assert "parser=parse_resume_text" in block
    assert "persister=persist_candidate_resume" in block
    assert "parse_resume_text(filepath)" not in block
    assert "persist_candidate_resume(" not in block
    assert "store_resume_copy(" not in block
    assert "mutate_candidates_with_resume_cleanup(" not in block
    assert "evaluator=evaluate_with_resume" in block
    assert "pdfminer.high_level" not in block
    assert "docx.Document" not in block
    assert "striprtf.striprtf" not in block
    assert "re.sub(" not in block
    assert "open(filepath" not in block


def test_resume_revert_delegates_mutation_to_candidate_controller():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _revert_resume_eval"):]
    block = block[:block.index("\n    # ===== 一键AI评估功能 =====")]

    assert "_candidate_controller_for(self).revert_resume_evaluation(" in block
    assert "mutate_candidates_with_resume_cleanup(" not in block
    assert "persisted.pop(" not in block


def test_resume_worker_routes_all_ui_updates_through_ui_queue():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _eval_worker():"):]
    block = block[:block.index("threading.Thread(target=_eval_worker")]

    assert "self.run_on_ui(" in block
    assert "_parent.after(" not in block


def test_resume_import_service_excludes_gui_parser_and_network_dependencies():
    forbidden = {
        "gui_main",
        "tkinter",
        "resume_parser",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("resume_import_service") & forbidden)
    assert callable(resume_import_service.persist_candidate_resume)


def test_api_connectivity_service_excludes_gui_secrets_and_persistence():
    forbidden = {
        "gui_main",
        "tkinter",
        "security",
        "storage",
        "paths",
    }
    assert not (_top_level_imports("api_connectivity") & forbidden)
    assert callable(api_connectivity.probe_api_connectivity)
    assert callable(api_connectivity.probe_model_capability)


def test_browser_connection_service_excludes_gui_storage_and_scan_dependencies():
    forbidden = {
        "bossmaster",
        "gui_main",
        "storage",
        "tkinter",
    }
    assert not (_top_level_imports("browser_connection") & forbidden)
    assert callable(browser_connection.classify_browser_url)
    assert callable(browser_connection.probe_page_url)
    assert callable(browser_connection.is_debug_port_open)
    assert callable(browser_connection.connect_browser_address)


def test_browser_controller_excludes_tk_gui_storage_and_business_workflows():
    forbidden = {
        "bossmaster",
        "candidate_workflow",
        "contact_queue",
        "gui_main",
        "storage",
        "tkinter",
    }
    assert not (_top_level_imports("browser_controller") & forbidden)
    assert callable(browser_controller.BrowserController)
    assert callable(browser_controller.BrowserRuntime)


def test_contact_controller_excludes_tk_gui_storage_browser_and_network_dependencies():
    forbidden = {
        "bossmaster",
        "browser_controller",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "tkinter",
        "urllib",
    }
    assert not (_top_level_imports("contact_controller") & forbidden)
    assert callable(contact_controller.ContactController)
    assert callable(contact_controller.ContactRunCounters)


def test_contact_worker_delegates_state_machine_and_queues_all_tk_updates():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _run_greet_queue_worker"):]
    block = block[:block.index("\n    @staticmethod\n    def _build_greet_queue_run_feedback")]

    assert "_CONTACT_CONTROLLER.run_queue(" in block
    assert "_CONTACT_CONTROLLER.finalize_interrupted(" in block
    assert "self.run_on_ui(" in block
    assert "self.root.after(" not in block


def test_run_controller_excludes_tk_gui_storage_browser_and_network_dependencies():
    forbidden = {
        "bossmaster",
        "browser_controller",
        "contact_queue",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "tkinter",
        "urllib",
    }
    assert not (_top_level_imports("run_controller") & forbidden)
    assert callable(run_controller.RunController)
    assert callable(run_controller.RunRequest)
    assert callable(run_controller.RunProgressEvent)
    assert callable(run_controller.RunTerminalEvent)


def test_run_worker_consumes_snapshot_and_routes_tk_work_to_ui_queue():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    worker = source[source.index("def run_worker"):]
    worker = worker[:worker.index("\n    def _apply_run_terminal_event")]

    assert "request = self._pending_run_request" in worker
    assert "_RUN_CONTROLLER.execute(" in worker
    assert "_RUN_CONTROLLER.terminal_event(" in worker
    assert "self.run_on_ui(" in worker
    assert ".get()" not in worker
    assert "self.root.after(" not in worker


def test_result_controller_excludes_tk_gui_and_storage_dependencies():
    forbidden = {
        "bossmaster",
        "gui_main",
        "storage",
        "tkinter",
    }
    assert not (_top_level_imports("result_controller") & forbidden)
    assert callable(result_controller.prepare_result_view)
    assert callable(result_controller.candidate_query_match)
    assert callable(result_controller.result_sort_value)


def test_candidate_controller_excludes_tk_gui_and_storage_dependencies():
    forbidden = {
        "bossmaster",
        "gui_main",
        "storage",
        "tkinter",
    }
    assert not (_top_level_imports("candidate_controller") & forbidden)
    assert callable(candidate_controller.CandidateController)
    assert callable(candidate_controller.CandidatePersistence)


def test_settings_data_and_education_controllers_exclude_gui_and_tk():
    common_forbidden = {"bossmaster", "gui_main", "storage", "tkinter"}
    assert not (_top_level_imports("settings_controller") & (
        common_forbidden | {"security", "requests"}
    ))
    assert not (_top_level_imports("data_maintenance_controller") & common_forbidden)
    assert not (_top_level_imports("education_controller") & common_forbidden)
    assert callable(settings_controller.SettingsController)
    assert callable(data_maintenance_controller.DataMaintenanceController)
    assert callable(education_controller.EducationController)


def test_checkpoint_two_gui_methods_delegate_business_orchestration():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    assert "_SETTINGS_CONTROLLER.prepare_saved_models(" in source
    assert "_SETTINGS_CONTROLLER.fetch_catalog(" in source
    assert "_DATA_MAINTENANCE_CONTROLLER.clear_candidates(" in source
    assert "_EDUCATION_CONTROLLER.recognize_documents(" in source
    assert "_EDUCATION_CONTROLLER.attempt_captcha(" in source

    fetch_block = source[source.index("def fetch_model_list"):]
    fetch_block = fetch_block[:fetch_block.index("\n    def _apply_model_catalog_outcome")]
    assert "self.run_on_ui(" in fetch_block
    assert "self.root.after(" not in fetch_block


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


def test_candidate_scan_policy_is_a_pure_business_module():
    forbidden = {
        "bossmaster",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "tkinter",
        "urllib",
    }
    assert not (_all_imports("candidate_scan_policy") & forbidden)
    assert callable(candidate_scan_policy.build_rejection_reason_labels)
    assert callable(candidate_scan_policy.normalize_rejection_reason)
    assert callable(candidate_scan_policy.build_ai_hard_conditions)


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
    assert "gui_candidate_workbench" in GUI_BUILDER_MODULES
    for module_name in sorted(GUI_BUILDER_MODULES):
        assert not (_all_imports(module_name) & forbidden)


def test_incremental_page_builders_use_explicit_host_protocols():
    expected = {
        "gui_config_page": ("build_config_page_steps", "ConfigPageHost"),
        "gui_run_page": ("build_run_page_steps", "RunPageHost"),
        "gui_settings_page": (
            "build_settings_content_steps",
            "SettingsPageHost",
        ),
    }
    for module_name, (function_name, protocol_name) in expected.items():
        tree = ast.parse(
            (ROOT / f"{module_name}.py").read_text(encoding="utf-8")
        )
        classes = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        assert protocol_name in classes
        assert any(
            isinstance(base, ast.Name) and base.id == "Protocol"
            for base in classes[protocol_name].bases
        )
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        )
        annotation = function.args.args[0].annotation
        assert isinstance(annotation, ast.Name)
        assert annotation.id == protocol_name


def test_new_controllers_and_presenters_are_automatically_boundary_checked():
    controllers = {path.stem for path in ROOT.glob("*_controller.py")}
    presenters = {path.stem for path in ROOT.glob("*_presenter.py")}
    assert controllers
    assert presenters

    for module_name in sorted(controllers):
        assert not (_all_imports(module_name) & {"gui_main", "tkinter"})

    presenter_forbidden = {
        "bossmaster",
        "contact_queue",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "tkinter",
        "urllib",
    }
    for module_name in sorted(presenters):
        assert not (_all_imports(module_name) & presenter_forbidden)


def test_gui_style_setup_owns_only_global_tk_style_registration():
    forbidden = {
        "bossmaster",
        "browser_controller",
        "contact_queue",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("gui_style_setup") & forbidden)
    assert callable(gui_style_setup.setup_styles)

    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    assert "gui_style_setup.setup_styles(self)" in source
    assert "def setup_styles(self)" not in source


def test_gui_app_shell_excludes_gui_business_storage_browser_and_network_dependencies():
    forbidden = {
        "bossmaster",
        "browser_controller",
        "candidate_workflow",
        "contact_queue",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("gui_app_shell") & forbidden)
    assert callable(gui_app_shell.AppShell)
    assert tuple(gui_app_shell.PAGE_SPECS) == tuple(gui_app_shell.PageIndex)

    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gui_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BossFilterGUI"
    )
    method_names = {
        node.name
        for node in gui_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "self.app_shell = gui_app_shell.AppShell(" in source
    assert {
        "create_sidebar",
        "_request_sidebar_page",
        "_request_page_first_open",
        "_schedule_page_width_policy",
        "_apply_page_width_policy",
    }.isdisjoint(method_names)


def test_gui_scroll_support_excludes_business_storage_browser_and_gui_main_dependencies():
    forbidden = {
        "bossmaster",
        "browser_controller",
        "candidate_workflow",
        "contact_queue",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("gui_scroll_support") & forbidden)
    assert callable(gui_scroll_support.ScrollSupport)

    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gui_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BossFilterGUI"
    )
    method_names = {
        node.name
        for node in gui_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "self.scroll_support = gui_scroll_support.ScrollSupport(self)" in source
    assert {
        "_delta_to_units",
        "_bind_bounded_spinbox_mousewheel",
        "_create_scroll_container",
        "_bind_mousewheel",
        "_setup_cocoa_scroll_hook",
        "_on_mousewheel",
    }.isdisjoint(method_names)
    for page_module in (
        "gui_config_page.py",
        "gui_run_page.py",
        "gui_education_page.py",
        "gui_job_review.py",
    ):
        page_source = (ROOT / page_module).read_text(encoding="utf-8")
        assert ".scroll_support." in page_source


def test_gui_input_support_excludes_business_storage_browser_and_gui_main_dependencies():
    forbidden = {
        "bossmaster",
        "browser_controller",
        "candidate_workflow",
        "contact_queue",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("gui_input_support") & forbidden)
    assert callable(gui_input_support.InputSupport)

    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gui_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BossFilterGUI"
    )
    method_names = {
        node.name
        for node in gui_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "self.input_support = gui_input_support.InputSupport(" in source
    assert {
        "bind_entry_context_menu",
        "bind_text_context_menu",
        "_show_text_dialog",
    }.isdisjoint(method_names)
    for page_module in (
        "gui_candidate_actions.py",
        "gui_candidate_diagnostics.py",
        "gui_candidate_review.py",
        "gui_config_page.py",
        "gui_education_page.py",
        "gui_run_page.py",
        "gui_settings_page.py",
    ):
        page_source = (ROOT / page_module).read_text(encoding="utf-8")
        assert ".input_support." in page_source


def test_gui_feedback_support_excludes_business_storage_browser_and_gui_main_dependencies():
    forbidden = {
        "bossmaster",
        "browser_controller",
        "candidate_workflow",
        "contact_queue",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("gui_feedback_support") & forbidden)
    assert callable(gui_feedback_support.FeedbackSupport)
    host = type("FeedbackHost", (), {})()
    gui_feedback_support.FeedbackSupport(host, font_family="Arial")
    assert host._tooltip is None
    assert host._model_tooltip is None
    assert host._skills_tooltip is None
    assert host._req_tooltip is None
    assert host._inline_banners == {}

    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gui_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BossFilterGUI"
    )
    method_names = {
        node.name
        for node in gui_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "self.feedback_support = gui_feedback_support.FeedbackSupport(" in source
    assert {
        "_styled_tooltip",
        "_show_tooltip",
        "_hide_tooltip",
        "_show_model_tooltip",
        "_hide_model_tooltip",
        "_create_simple_tooltip",
        "_hide_skills_tooltip",
        "_hide_req_tooltip",
        "_show_inline_banner",
        "_hide_inline_banner",
    }.isdisjoint(method_names)
    for page_module in (
        "gui_candidate_actions.py",
        "gui_candidate_diagnostics.py",
        "gui_config_page.py",
        "gui_education_page.py",
        "gui_result_page.py",
        "gui_settings_page.py",
    ):
        page_source = (ROOT / page_module).read_text(encoding="utf-8")
        assert ".feedback_support." in page_source


def test_gui_widget_support_excludes_business_storage_browser_and_gui_main_dependencies():
    forbidden = {
        "bossmaster",
        "browser_controller",
        "candidate_workflow",
        "contact_queue",
        "filtering",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("gui_widget_support") & forbidden)
    assert callable(gui_widget_support.WidgetSupport)
    assert callable(gui_widget_support.StatusIconSet)

    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gui_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BossFilterGUI"
    )
    method_names = {
        node.name
        for node in gui_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "self.widget_support = gui_widget_support.WidgetSupport(" in source
    assert "status_icons = self.widget_support.create_status_icons()" in source
    assert (
        "self._icon_status_ok, self._icon_status_fail = status_icons.ok, status_icons.fail"
        in source
    )
    assert {
        "_create_page_header",
        "_create_card",
        "_create_switch",
        "_build_empty_state",
        "_create_status_icons",
    }.isdisjoint(method_names)
    assert "_toggle_result_empty_state" in method_names
    for page_module in (
        "gui_config_page.py",
        "gui_education_page.py",
        "gui_home_page.py",
        "gui_job_review.py",
        "gui_result_page.py",
        "gui_run_page.py",
        "gui_settings_page.py",
        "gui_stats_page.py",
    ):
        page_source = (ROOT / page_module).read_text(encoding="utf-8")
        assert ".widget_support." in page_source


def test_gui_layout_support_owns_responsive_layout_without_host_forwarders():
    forbidden = {
        "bossmaster",
        "browser_controller",
        "candidate_workflow",
        "contact_queue",
        "filtering",
        "gui_main",
        "requests",
        "socket",
        "storage",
        "subprocess",
        "urllib",
    }
    assert not (_top_level_imports("gui_layout_support") & forbidden)
    assert callable(gui_layout_support.LayoutSupport)

    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    gui_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BossFilterGUI"
    )
    method_names = {
        node.name
        for node in gui_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "self.layout_support = gui_layout_support.LayoutSupport(" in source
    assert {
        "_update_run_page_dynamic_heights",
        "_update_result_stats_compact",
        "_is_window_maximized",
        "_update_result_tree_columns",
        "_tree_header_floors",
        "_distribute_tree_surplus",
        "_apply_result_tree_column_widths",
        "_update_stats_tree_columns",
        "_is_tall_window",
        "_get_tall_window_extra_rows",
        "_update_config_page_dynamic_heights",
        "_get_model_list_max_rows",
        "_update_model_list_height",
        "_update_model_list_columns",
        "_update_education_queue_columns",
    }.isdisjoint(method_names)

    app_shell_source = (ROOT / "gui_app_shell.py").read_text(encoding="utf-8")
    settings_source = (ROOT / "gui_settings_page.py").read_text(encoding="utf-8")
    education_source = (ROOT / "gui_education_page.py").read_text(encoding="utf-8")
    assert "layout = host.layout_support" in app_shell_source
    assert "self.layout_support.update_model_list_columns()" in settings_source
    assert "host.layout_support.update_education_queue_columns()" in education_source


def test_gui_main_does_not_restore_unreferenced_compatibility_facades():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    gui_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BossFilterGUI"
    )
    gui_methods = {
        node.name
        for node in gui_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert {
        "delete_api_key",
        "_filter_candidates_by_result_view",
    }.isdisjoint(module_functions)
    assert {
        "_format_maintenance_time",
        "_build_job_review_text",
        "_start_breathing",
        "_format_terminal_log_text",
        "_extract_extra_fields",
        "_latest_history_value",
        "_get_greet_confirmation_hint",
        "_set_greet_queue_item_state",
    }.isdisjoint(gui_methods)


def test_gui_compatibility_methods_delegate_to_presenters():
    candidate = {"match_score": 70, "recommend_level": "推荐"}
    BossFilterGUI = gui_main.BossFilterGUI
    assert BossFilterGUI._candidate_gender_display(candidate) == (
        candidate_presenter.candidate_gender_display(candidate)
    )
    assert BossFilterGUI._greet_queue_readiness_label(candidate) == (
        contact_presenter.greet_queue_readiness_label(candidate)
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
    assert "self.layout_support.update_result_tree_columns()" in block
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


def test_home_page_keeps_data_loading_in_host_and_delegates_navigation_to_shell():
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
    assert "host.app_shell.request_sidebar_page(" in builder


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


def test_api_connectivity_controllers_delegate_network_probes():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    direct = source[source.index("def test_api_connection"):]
    direct = direct[:direct.index("\n    def save_config")]
    saved = source[source.index("def test_saved_model_connectivity"):]
    saved = saved[:saved.index("\n    def _set_model_list_item_status")]

    assert "probe_api_connectivity(config, api_key)" in direct
    assert "self.run_on_ui(" in direct
    assert "requests.Session" not in direct
    assert "certifi" not in direct
    assert "time.sleep" not in direct
    assert "probe_model_capability(config, api_key)" in saved
    assert "from llm_eval import probe_model_compatibility" not in saved


def test_browser_controllers_delegate_bounded_connection_probes():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    controller_source = (ROOT / "browser_controller.py").read_text(encoding="utf-8")
    reconnect = source[source.index("def _try_reconnect_browser"):]
    reconnect = reconnect[:reconnect.index("\n    def _launch_boss_browser")]
    check = source[source.index("def check_browser_connection"):]
    check = check[:check.index("\n    def _start_browser_auto_check")]

    assert "_browser_controller_for(self).reconnect(" in reconnect
    assert "self._runtime.port_open(address, timeout=0.5)" in controller_source
    assert "self._runtime.connector(" in controller_source
    assert "prefer_boss_tab=prefer_boss_tab" in controller_source
    assert "validate_page=validate_page" in controller_source
    assert "from DrissionPage" not in reconnect

    assert "probe_page_url(" in check
    assert "classify_browser_url(" in check
    assert "is_debug_port_open(addr, timeout=1)" in check
    assert "connect_browser_address(addr, timeout=3)" in check
    assert "except ImportError:\n                    raise" in check
    assert "self._launch_boss_browser()" in check
    assert "self._runtime.popen(" in controller_source
    assert "_reactivate_and_navigate(" in check
    assert "self.set_browser_ui(" in check


def test_result_page_controller_owns_data_scope_metrics_and_row_decisions():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    refresh = source[source.index("def refresh_results"):]
    refresh = refresh[:refresh.index("\n    def _refresh_results_and_reset_sort")]

    assert "ResultQuery(" in refresh
    assert "ResultController(load_candidates_all)" in refresh
    assert "controller.load(CANDIDATES_PATH, query)" in refresh
    assert "state.metrics" in refresh
    assert "state.rows" in refresh
    assert "derive_candidate_decision(" not in refresh
    assert "normalize_job_name(" not in refresh
    assert "_parse_salary_exp(" not in refresh


def test_candidate_persistence_compatibility_methods_delegate_to_controller():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    assert "_candidate_controller_for(self).blacklist(" in source
    assert "_candidate_controller_for(self).unblacklist(" in source
    assert "_candidate_controller_for(self).update_followup(" in source
    assert "_candidate_controller_for(self).complete_review(" in source
    assert "_candidate_controller_for(self).reject_review(" in source
    assert "_candidate_controller_for(self).approve_contact(" in source
    assert "_candidate_controller_for(self).update_feedback(" in source
    assert "_candidate_controller_for(self).save_ai_evaluations(" in source


def test_model_catalog_dialog_does_not_import_controller_storage_or_http_clients():
    forbidden = {
        "gui_main",
        "llm_eval",
        "requests",
        "security",
        "storage",
    }
    assert not (_all_imports("gui_model_catalog_dialog") & forbidden)
    assert callable(gui_model_catalog_dialog.show_model_catalog_dialog)

    source = (ROOT / "gui_model_catalog_dialog.py").read_text(encoding="utf-8")
    assert "probe_model(" in source
    assert "run_on_ui(" in source
    assert "_get_api_key_cached" not in source
    assert ".root.after(0" not in source


def test_model_catalog_service_is_ui_free_and_main_uses_both_extracted_parts():
    forbidden = {"gui_main", "tkinter", "ui_messagebox", "ui_theme"}
    assert not (_top_level_imports("model_catalog") & forbidden)
    assert callable(model_catalog.fetch_model_catalog)
    assert callable(model_catalog.analyze_model_catalog)

    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def fetch_model_list"):]
    block = block[:block.index("\n    def _show_api_key_while_pressed")]
    assert "_SETTINGS_CONTROLLER.fetch_catalog(" in block
    assert "fetcher=fetch_model_catalog" in block
    assert "analyzer=analyze_model_catalog" in block
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


def test_candidate_blacklist_persistence_delegates_to_candidate_controller():
    builder = (ROOT / "gui_candidate_state_dialogs.py").read_text(encoding="utf-8")
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    assert "def _update_candidate_blacklist" not in builder
    assert "def _update_candidate_blacklist" in source
    block = source[
        source.index("def _update_candidate_blacklist"):
        source.index("\n    def _import_resume")
    ]
    assert "_candidate_controller_for(self).blacklist(" in block
    assert "update_candidate_records(" not in block


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
    block = block[:block.index("\n    def show_about")]

    assert "gui_data_maintenance_dialogs.show_clear_candidates_dialog(" in block
    assert "load_candidates_all(CANDIDATES_PATH)" in block
    assert "tk.Toplevel" not in block
    assert "ttk.Radiobutton" not in block
    assert "mutate_candidates_with_resume_cleanup" not in block


def test_clear_candidates_persistence_delegates_to_data_controller():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    builder = (ROOT / "gui_data_maintenance_dialogs.py").read_text(
        encoding="utf-8"
    )
    controller = source[source.index("def _clear_candidates_from_dialog"):]
    controller = controller[:controller.index("\n    def clear_candidates")]

    assert "_DATA_MAINTENANCE_CONTROLLER.clear_candidates(" in controller
    assert "mutate_with_resume_cleanup=mutate_candidates_with_resume_cleanup" in controller
    assert "clear_in_place=clear_candidates_in_place" in controller
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


def test_candidate_workbench_builders_use_shared_primitives_without_gui_wrappers():
    source = (ROOT / "gui_main.py").read_text(encoding="utf-8")
    builders = "\n".join(
        (ROOT / module_name).read_text(encoding="utf-8")
        for module_name in (
            "gui_candidate_actions.py",
            "gui_candidate_diagnostics.py",
            "gui_contact_queue.py",
        )
    )

    assert "def _create_candidate_workbench_header" not in source
    assert "def _create_candidate_workbench_metrics" not in source
    assert "def _candidate_workbench_navigation_style" not in source
    assert "def _apply_candidate_workbench_navigation_tags" not in source
    assert "gui_candidate_workbench.create_header(" in builders
    assert "gui_candidate_workbench.create_metrics(" in builders
    assert "gui_candidate_workbench.navigation_style(" in builders
    assert "gui_candidate_workbench.apply_navigation_tags(" in builders


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
    gui.feedback_support = Mock()
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
