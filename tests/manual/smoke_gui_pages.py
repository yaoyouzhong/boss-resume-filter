"""Smoke all seven GUI pages and extracted workbenches without live I/O."""

from __future__ import annotations

import sys
import os
import json
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"] = "1"

import gui_main
import gui_candidate_actions
import gui_candidate_diagnostics
import gui_candidate_review
import gui_contact_queue
import gui_config_page
import gui_dialogs
import gui_job_review


PAGES = (
    ("home", "home_page", "create_home_page", "show_page_home"),
    ("config", "config_page", "create_config_page", "show_page_config"),
    ("run", "run_page", "create_run_page", "show_page_run"),
    ("result", "result_page", "create_result_page", "show_page_result"),
    ("education", "education_page", "create_education_page", "show_page_education"),
    ("stats", "stats_page", "create_stats_page", "show_page_stats"),
    ("settings", "api_config_page", "create_api_config_page", "show_page_api"),
)


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    try:
        app = gui_main.BossFilterGUI(root)
        app._start_browser_auto_check = lambda: None
        app._stop_browser_auto_check = lambda: None
        app._schedule_api_key_resolution = lambda: None
        for name, page_attr, creator_name, show_name in PAGES:
            if getattr(app, page_attr) is None:
                getattr(app, creator_name)()
            getattr(app, show_name)()
            root.update_idletasks()
            page = getattr(app, page_attr)
            if page is None or not page.winfo_manager():
                raise RuntimeError(f"{name} page did not open")
            print(f"PASS page {name}")

        app.refresh_results(force=True)
        root.update_idletasks()
        print("PASS refresh_results(force=True)")

        app.show_page_config()
        root.update_idletasks()
        form_before = app._job_form_fingerprint()
        rules_before = json.dumps(app.job_rules, ensure_ascii=False, sort_keys=True)
        located_label = gui_config_page.locate_job_config_review_target(
            app,
            "requirement",
        )
        root.update_idletasks()
        if located_label != "招聘需求":
            raise RuntimeError(f"unexpected review target: {located_label}")
        if app._job_form_fingerprint() != form_before:
            raise RuntimeError("job review locator changed the config form")
        if json.dumps(app.job_rules, ensure_ascii=False, sort_keys=True) != rules_before:
            raise RuntimeError("job review locator changed saved job rules")
        print("PASS job review config locator (read-only)")

        previous_toplevels = set(root.winfo_children())
        gui_dialogs.show_changelog_dialog(
            app,
            gui_main.__version__,
            get_cached_release_notes=lambda _version: None,
            fetch_current_release_notes=lambda _version, **_kwargs: None,
        )
        root.update_idletasks()
        changelog_windows = [
            child
            for child in root.winfo_children()
            if child not in previous_toplevels
            and isinstance(child, tk.Toplevel)
            and child.title() == "更新日志"
        ]
        if len(changelog_windows) != 1:
            raise RuntimeError(
                f"expected one changelog dialog, found {len(changelog_windows)}"
            )
        print("PASS changelog dialog")
        changelog_windows[0].destroy()

        noop = lambda *_args, **_kwargs: None
        review = gui_candidate_review.build_candidate_review_workbench(
            app,
            navigate=noop,
            show_view=lambda _view: "break",
            toggle_view=lambda: "break",
            close_window=lambda window: window.destroy(),
        )
        diagnostics = gui_candidate_diagnostics.show_candidate_state_diagnostics_dialog(
            app,
            "全部岗位",
            [],
            [],
            load_diagnostics=lambda: ([], []),
            export_report=noop,
            ui_config=gui_main.UI_CONFIG,
        )
        actions = gui_candidate_actions.show_daily_candidate_actions_dialog(
            app,
            "全部岗位",
            [],
            load_actions=lambda: [],
            export_report=noop,
            ui_config=gui_main.UI_CONFIG,
        )
        contact = gui_contact_queue.build_contact_queue_workbench(
            app,
            root,
            selected_group="全部",
            initial_counts={},
            callbacks=gui_contact_queue.ContactQueueCallbacks(
                start=noop,
                pause=noop,
                resume=noop,
                group_selected=noop,
                confirm_sent=noop,
                confirm_not_sent=noop,
                retry_failed=noop,
                remove_selected=noop,
                show_selected_detail=noop,
                update_action_states=noop,
                row_motion=noop,
                hide_tooltip=noop,
                context_menu=noop,
                select_all=noop,
                close=noop,
            ),
            ui_config=gui_main.UI_CONFIG,
        )
        locator_requests = []
        job_review = gui_job_review.build_job_review_workbench(
            app,
            job_name="演示岗位",
            time_range="全部",
            review={
                "candidate_count": 5,
                "qualified_count": 4,
                "greeted_count": 3,
                "replied_count": 2,
                "interviewed_count": 1,
                "avg_score": 72.0,
                "feedback_count": 5,
                "status_counts": Counter({"误推": 3, "合适": 2}),
                "reason_counts": Counter({"规则过宽": 3}),
                "false_positive_reasons": Counter({"规则过宽": 3}),
                "false_negative_reasons": Counter(),
                "ai_bias_counts": Counter(),
                "recommendations": [{
                    "title": "规则过宽",
                    "detail": "补充确属硬性的约束。",
                    "evidence": "5 条反馈中 3 条标记为“规则过宽”",
                    "config_target": "required_conditions",
                    "action_label": "定位必要条件",
                }],
                "suggestions": [],
            },
            callbacks=gui_job_review.JobReviewCallbacks(
                show_feedback_candidates=noop,
                open_job_config=locator_requests.append,
                format_suggestion=gui_main.stats_presenter.format_job_review_suggestion,
            ),
            font_family=gui_main.FONT_FAMILY,
        )
        root.update_idletasks()
        workbench_windows = (
            review.window,
            diagnostics,
            actions,
            contact.window,
            job_review.window,
        )
        expected_titles = {
            "候选人查看与复核",
            "候选人状态体检",
            "今日待办",
            "联系候选人",
            "岗位复盘 - 演示岗位",
        }
        actual_titles = {window.title() for window in workbench_windows}
        if actual_titles != expected_titles:
            raise RuntimeError(f"workbench titles mismatch: {actual_titles}")
        locator_buttons = []
        pending_widgets = [job_review.window]
        while pending_widgets:
            widget = pending_widgets.pop()
            pending_widgets.extend(widget.winfo_children())
            if isinstance(widget, ttk.Button) and widget.cget("text") == "定位必要条件":
                locator_buttons.append(widget)
        if len(locator_buttons) != 1:
            raise RuntimeError(
                f"expected one job review locator button, found {len(locator_buttons)}"
            )
        locator_buttons[0].invoke()
        root.update_idletasks()
        if [item.get("config_target") for item in locator_requests] != [
            "required_conditions"
        ]:
            raise RuntimeError(f"unexpected locator callback: {locator_requests}")
        print("PASS extracted workbenches")
        for window in workbench_windows:
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError:
                pass
        return 0
    finally:
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
