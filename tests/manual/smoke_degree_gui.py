"""Real Tk widget smoke test in both hosts, using only synthetic queue data.

Run each host in its own Tk process:
  python tests/manual/smoke_degree_gui.py
  python tests/manual/smoke_degree_gui.py --standalone
No model calls, browser queries, credentials, or candidate writes are performed.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"] = "1"

from education_controller import EducationController  # noqa: E402
from education_tool import _wait_for_smoke_layout  # noqa: E402
from gui_main import BossFilterGUI  # noqa: E402


def main() -> None:
    """Build real forms, switch queue types, and check viewport layout."""
    with TemporaryDirectory(prefix="degree-gui-smoke-") as directory:
        folder = Path(directory)
        for standalone in ("--standalone" in sys.argv,):
            root = tk.Tk()
            root.withdraw()
            errors = []
            root.report_callback_exception = lambda *error: errors.append(error)
            try:
                with patch.object(BossFilterGUI, "refresh_home_stats"), \
                        patch.object(BossFilterGUI, "refresh_home_status"), \
                        patch.object(BossFilterGUI, "_report_startup_data_state"):
                    gui = BossFilterGUI(
                        root, standalone_education=standalone,
                        education_api_config={"model": "test-only"},
                        education_api_config_path=folder / "config.json",
                        education_api_key_getter=lambda *_: "",
                        run_preferences_path=folder / "preferences.json",
                    )
                    if gui.education_page is None:
                        gui.create_education_page()
                    gui.show_page_education()
                    if gui._ui_queue_after_id is not None:
                        root.after_cancel(gui._ui_queue_after_id)
                        gui._ui_queue_after_id = None
                    root.geometry("1280x900")
                    root.deiconify()
                    _wait_for_smoke_layout(gui, gui.education_page)
                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)

                    folder_button = next(
                        widget for widget in descendants(gui.education_page)
                        if "text" in widget.keys() and widget.cget("text") == " 打开文件夹"
                    )
                    assert folder_button.instate(["disabled"])
                    gui.education_screenshot_folder_var.set("保存到：测试目录")
                    assert not folder_button.instate(["disabled"])
                    gui.education_screenshot_folder_var.set("")
                    assert folder_button.instate(["disabled"])
                    for kind in ("education", "degree"):
                        gui.education_items[kind] = dict(
                            path=str(folder / f"{kind}.png"), name="测试甲",
                            certificate_number="1234567890123456",
                            certificate_type=kind, status="已识别",
                        )
                        gui.education_queue_tree.insert("", "end", iid=kind)
                        gui._update_education_queue_row(kind)
                    gui._refresh_education_queue_summary()
                    for kind, label in (("education", "学历证书"), ("degree", "学位证书")):
                        gui.education_current_id = kind
                        gui._set_education_form_fields("测试甲", "1234567890123456", kind)
                        assert gui.education_type_var.get() == label
                        assert label[:2] in gui.education_queue_tree.item(kind, "values")[0]
                    gui.education_type_var.set("学历证书")
                    assert gui.education_items["degree"]["certificate_type"] == "education"
                    gui.education_type_var.set("学位证书")
                    assert gui.education_items["degree"]["certificate_type"] == "degree"
                    assert EducationController.action_states(gui.education_items).verify
                    from tests.pdf_samples import write_pdf
                    pdf = folder / "preview.pdf"
                    write_pdf(pdf, [b"1 0 0 rg 0 0 200 100 re f", b"0 0 1 rg 0 0 200 100 re f"])
                    gui.education_items["pdf"] = dict(path=str(pdf), is_pdf=True)
                    gui.education_current_id = "pdf"
                    gui.education_image_path = pdf
                    gui._render_education_preview()
                    assert gui.education_preview_label._image_ref is not None
                    assert gui.education_items["pdf"]["preview_page_count"] == 2
                    first = gui._get_education_source_image(pdf, "pdf", 0)
                    assert first.getpixel((100, 100)) == (255, 0, 0)
                    gui.education_items["pdf"]["preview_page"] = 1
                    gui._render_education_preview()
                    second = gui._get_education_source_image(pdf, "pdf", 90)
                    assert second.height > second.width
                    assert second.getpixel((100, 100)) == (0, 0, 255)
                    gui._rotate_education_image_cw90()
                    root.update_idletasks()
                    assert gui.education_queue_tree.winfo_width() > 500
                    assert not errors, errors
                    print(f"PASS {'standalone' if standalone else 'BOSS'}: real Tk form, type edits, mixed queue, 1280x900 layout")
            finally:
                for callback in root.tk.call("after", "info"):
                    root.after_cancel(callback)
                # Destroy the Tcl widget tree once; shared scroll bindings may
                # otherwise be deleted twice by Python widget destructors.
                root.tk.call("destroy", root._w)


if __name__ == "__main__":
    main()
