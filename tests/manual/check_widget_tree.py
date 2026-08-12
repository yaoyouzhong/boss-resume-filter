"""Capture or compare the complete widget tree for every main GUI page."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tkinter as tk
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"] = "1"

import gui_main


BASELINE_PATH = Path(__file__).with_name("widget_baseline.json")
PAGE_CREATORS = {
    "home": ("home_page", "create_home_page"),
    "config": ("config_page", "create_config_page"),
    "run": ("run_page", "create_run_page"),
    "result": ("result_page", "create_result_page"),
    "education": ("education_page", "create_education_page"),
    "stats": ("stats_page", "create_stats_page"),
    "settings": ("api_config_page", "create_api_config_page"),
}
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


def _descendants(widget: tk.Misc) -> list[tk.Misc]:
    descendants: list[tk.Misc] = []
    for child in widget.winfo_children():
        descendants.append(child)
        descendants.extend(_descendants(child))
    return descendants


def _build_all_pages(app: gui_main.BossFilterGUI) -> dict[str, tk.Misc]:
    pages: dict[str, tk.Misc] = {}
    for name, (page_attr, creator_name) in PAGE_CREATORS.items():
        page = getattr(app, page_attr)
        if page is None:
            getattr(app, creator_name)()
            page = getattr(app, page_attr)
        if page is None:
            raise RuntimeError(f"{name} page was not created")
        pages[name] = page
    return pages


def _delete_one_leaf(page: tk.Misc) -> str:
    descendants = _descendants(page)
    for widget in reversed(descendants):
        if not widget.winfo_children():
            widget_path = str(widget)
            widget.destroy()
            return widget_path
    raise RuntimeError("page has no leaf widget to delete")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="write the current complete page counts instead of comparing",
    )
    parser.add_argument(
        "--print-current",
        action="store_true",
        help="print the current complete page counts without reading or writing a baseline",
    )
    parser.add_argument(
        "--delete-one",
        choices=PAGE_CREATORS,
        help="destroy one leaf in memory before comparison (negative-control check)",
    )
    args = parser.parse_args()

    root = tk.Tk()
    root.withdraw()
    try:
        app = gui_main.BossFilterGUI(root)
        pages = _build_all_pages(app)
        root.update_idletasks()

        if args.delete_one:
            deleted = _delete_one_leaf(pages[args.delete_one])
            root.update_idletasks()
            print(f"NEGATIVE CONTROL: deleted {deleted} from {args.delete_one}")

        counts = {name: len(_descendants(page)) for name, page in pages.items()}
        if args.print_current:
            for name, count in counts.items():
                print(f"{name}: {count}")
            return 0
        if args.write_baseline:
            BASELINE_PATH.write_text(
                json.dumps(counts, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for name, count in counts.items():
                print(f"{name}: {count}")
            print(f"WROTE {BASELINE_PATH}")
            return 0

        expected = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        passed = True
        for name, expected_count in expected.items():
            actual_count = counts.get(name)
            if actual_count == expected_count:
                print(f"{GREEN}PASS {name}: {actual_count}{RESET}")
            else:
                passed = False
                print(
                    f"{RED}FAIL {name}: expected {expected_count}, "
                    f"actual {actual_count}{RESET}"
                )
        return 0 if passed else 1
    finally:
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
