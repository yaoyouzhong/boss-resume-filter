"""Open the result page with fully synthetic data and keep its context menu visible."""

from __future__ import annotations

import importlib.util
import sys
import tkinter as tk
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HELPER_PATH = Path(__file__).with_name("generate_latest_screenshots.py")
DEMO_PATH = Path(__file__).with_name("demo-candidates-menu.json")

spec = importlib.util.spec_from_file_location("result_menu_helper", HELPER_PATH)
helper = importlib.util.module_from_spec(spec)
sys.modules["result_menu_helper"] = helper
assert spec.loader is not None
spec.loader.exec_module(helper)

helper.gui_main._enable_high_dpi_awareness()
monitor_area = helper.gui_main._get_windows_monitor_area()
helper.install_demo_job_config_source()

root = tk.Tk()
root.withdraw()
app = helper.gui_main.BossFilterGUI(root)
helper.gui_main._show_main_window_centered(root, monitor_area)
root.geometry("+0+0")
root.attributes("-topmost", True)
root.lift()
root.focus_force()
root.update()

if list(app.job_rules) != [helper.DEMO_JOB]:
    raise RuntimeError("Synthetic demo job configuration was not installed")
helper.write_demo_candidates(DEMO_PATH)
helper.gui_main.CANDIDATES_PATH = DEMO_PATH
helper.gui_main.CANDIDATES_XLSX_PATH = DEMO_PATH.with_suffix(".xlsx")

app.show_page_result()
root.update()
app.refresh_results()
root.update_idletasks()
root.update()

item = app.result_tree.get_children()[0]
app.result_tree.selection_set(item)
app.result_tree.focus(item)
app.result_tree.see(item)
candidate = app._find_candidate_by_tree_item(item)
if not candidate:
    raise RuntimeError("Selected candidate not resolved")

original_popup = tk.Menu.tk_popup


def popup_at_lower_right(menu: tk.Menu, _x: int, _y: int, entry: str = "") -> None:
    root.attributes("-topmost", False)
    root.lift()
    root.focus_force()
    root.update()
    menu.post(
        root.winfo_rootx() + root.winfo_width() - 110,
        root.winfo_rooty() + 690,
    )
    root.attributes("-topmost", True)
    menu.post(
        root.winfo_rootx() + root.winfo_width() - 110,
        root.winfo_rooty() + 690,
    )


tk.Menu.tk_popup = popup_at_lower_right
app._build_candidate_context_menu(
    parent=root,
    tree=app.result_tree,
    tree_item=item,
    candidate=candidate,
    show_detail_fn=lambda: None,
    remove_fn=lambda: None,
    export_fn=lambda: None,
    refresh_fn=lambda: None,
    x_root=0,
    y_root=0,
)

root.after(120_000, root.destroy)
try:
    root.mainloop()
finally:
    tk.Menu.tk_popup = original_popup
