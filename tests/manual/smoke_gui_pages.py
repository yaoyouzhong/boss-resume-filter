"""Smoke all seven GUI pages, force-refresh results, and open the changelog."""

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
        app.show_changelog()
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
        return 0
    finally:
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
