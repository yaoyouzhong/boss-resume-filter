"""Run an invisible local Tk page/layout smoke without Chrome or network access."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"] = "1"

import tkinter as tk

import gui_main
from ui_layout import result_display_columns


PAGES = (
    ("home", "home_page", "show_page_home"),
    ("config", "config_page", "show_page_config"),
    ("run", "run_page", "show_page_run"),
    ("result", "result_page", "show_page_result"),
    ("education", "education_page", "show_page_education"),
    ("stats", "stats_page", "show_page_stats"),
    ("settings", "api_config_page", "show_page_api"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _render(root: tk.Tk) -> None:
    root.update_idletasks()
    root.update()


def _page_smoke(root: tk.Tk, app: gui_main.BossFilterGUI) -> list[dict]:
    results = []
    for page_id, page_attr, show_name in PAGES:
        getattr(app, show_name)()
        _render(root)
        page = getattr(app, page_attr)
        mapped = bool(page is not None and page.winfo_ismapped())
        width = int(page.winfo_width()) if page is not None else 0
        height = int(page.winfo_height()) if page is not None else 0
        passed = mapped and width > 100 and height > 100
        results.append({
            "id": f"page-{page_id}",
            "status": "passed" if passed else "failed",
            "width": width,
            "height": height,
        })
        if not passed:
            raise RuntimeError(
                f"{page_id} page failed layout smoke: "
                f"mapped={mapped}, size={width}x{height}"
            )
    return results


def _result_layout_smoke(
    root: tk.Tk,
    app: gui_main.BossFilterGUI,
) -> dict:
    app.show_page_result()
    _render(root)
    app._update_result_tree_columns()
    _render(root)
    tree_width = int(app.result_tree.winfo_width())
    actual = tuple(app.result_tree.cget("displaycolumns"))
    expected = result_display_columns(
        tree_width,
        maximized=app._is_window_maximized(),
    )
    if actual != expected:
        raise RuntimeError(
            f"result columns mismatch: width={tree_width}, "
            f"actual={len(actual)}, expected={len(expected)}"
        )
    return {
        "id": "result-column-policy-current-host",
        "status": "passed",
        "tree_width": tree_width,
        "maximized": app._is_window_maximized(),
        "column_count": len(actual),
    }


def run(output_path: Path) -> dict:
    gui_main._enable_high_dpi_awareness()
    root = tk.Tk()
    try:
        try:
            root.attributes("-alpha", 0.0)
        except tk.TclError:
            root.withdraw()
        screen_width = int(root.winfo_screenwidth())
        screen_height = int(root.winfo_screenheight())
        width = max(1100, min(1600, screen_width - 80))
        height = max(720, min(950, screen_height - 80))
        root.geometry(f"{width}x{height}+0+0")
        root.deiconify()
        _render(root)

        app = gui_main.BossFilterGUI(root)
        app._start_browser_auto_check = lambda: None
        app._stop_browser_auto_check = lambda: None
        app._schedule_api_key_resolution = lambda: None
        root.geometry(f"{width}x{height}+0+0")
        _render(root)

        checks = _page_smoke(root, app)
        checks.append(_result_layout_smoke(root, app))
        if not hasattr(app, "data_backup_status_var"):
            raise RuntimeError("settings page is missing data backup controls")
        if not hasattr(app, "diagnostic_package_status_var"):
            raise RuntimeError("settings page is missing diagnostic controls")
        checks.append({
            "id": "settings-data-safety-controls",
            "status": "passed",
        })

        report = {
            "schema_version": 1,
            "created_at": _utc_now(),
            "application_version": gui_main.__version__,
            "environment": {
                "os": platform.system(),
                "os_release": platform.release(),
                "screen_width": screen_width,
                "screen_height": screen_height,
                "tk_patchlevel": str(
                    root.tk.call("info", "patchlevel")
                ),
                "dpi_scale": round(float(app.dpi_scale), 3),
                "zoom_factor": round(float(app.zoom_factor), 3),
            },
            "network_or_browser_accessed": False,
            "checks": checks,
            "status": (
                "passed"
                if all(item["status"] == "passed" for item in checks)
                else "failed"
            ),
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return report
    finally:
        root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run local GUI acceptance smoke without Chrome/network",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tmp" / "gui-acceptance-report.json",
    )
    args = parser.parse_args()
    try:
        report = run(args.output)
    except Exception as exc:
        print(f"FAIL GUI acceptance: {type(exc).__name__}: {exc}")
        return 1
    print(
        "PASS GUI acceptance: "
        f"{len(report['checks'])} checks, "
        f"{report['environment']['screen_width']}x"
        f"{report['environment']['screen_height']}"
    )
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
