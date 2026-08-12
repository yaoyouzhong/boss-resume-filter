"""Smoke all seven GUI pages and extracted workbenches without live I/O."""

from __future__ import annotations

import sys
import os
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"] = "1"

import gui_main
import gui_candidate_actions
import gui_candidate_diagnostics
import gui_candidate_review
import gui_contact_queue
import gui_dialogs


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
        root.update_idletasks()
        workbench_windows = (
            review.window,
            diagnostics,
            actions,
            contact.window,
        )
        expected_titles = {
            "候选人查看与复核",
            "候选人状态体检",
            "今日待办",
            "联系候选人",
        }
        actual_titles = {window.title() for window in workbench_windows}
        if actual_titles != expected_titles:
            raise RuntimeError(f"workbench titles mismatch: {actual_titles}")
        print("PASS extracted workbenches")
        for window in workbench_windows:
            window.destroy()
        return 0
    finally:
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
