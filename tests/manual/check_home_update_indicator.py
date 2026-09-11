"""Real Tk smoke with synthetic release data; no network or business data writes."""
import sys
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gui_home_page
import gui_style_setup
import icons
from gui_feedback_support import FeedbackSupport
from gui_main import BossFilterGUI, UI_CONFIG


def main() -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    root = tk.Tk()
    root.tk.call("tk", "scaling", 96 / 72)
    root.title("首页升级提醒验证")
    host = SimpleNamespace(
        root=root, pages_frame=root, dpi_scale=1.0, zoom_factor=1.0,
        font_boost=1.0, font_scale=1.0, app_shell=Mock(), refresh_home_stats=Mock(),
        show_stat_detail=Mock(), on_home_task_click=Mock(), on_home_health_click=Mock(),
        import_external_candidate=Mock(), open_home_data_maintenance=Mock(),
        open_home_system_settings=Mock(),
    )
    host.open_available_update = lambda: BossFilterGUI.open_available_update(host)
    host.update_tooltip_text = lambda: BossFilterGUI.update_tooltip_text(host)
    host.icons = icons.init(1.0)
    receive = lambda result: BossFilterGUI._receive_update_result(host, result)
    gui_style_setup.setup_styles(host)
    host.feedback_support = FeedbackSupport(host, font_family=host.home_fonts["meta"][0])
    try:
        widgets = gui_home_page.build_home_page(
            host, UI_CONFIG, run_page_index=2, result_page_index=3,
            config_page_index=1, education_page_index=4,
        )
        host._home_page_widgets = widgets
        widgets.page.pack(fill="both", expand=True, padx=24, pady=20)
        root.geometry("1180x860+40+40")
        root.update()
        assert not widgets.update_button.winfo_ismapped()
        result = {
            "current": "2.33", "latest": "2.34", "has_update": True,
            "error": None, "body": "验证用升级内容", "changelog_body": "验证用升级内容",
            "download_url": "", "html_url": "", "asset_info": {},
        }
        receive(result)
        root.update()
        assert widgets.update_button.winfo_ismapped()
        assert not any(isinstance(w, tk.Toplevel) for w in root.winfo_children())
        for width in (1024, 1180, 1440):
            root.geometry(f"{width}x860+40+40")
            root.update()
            button = widgets.update_button
            controls = widgets.layout.header_controls
            assert button.winfo_rootx() + button.winfo_width() <= controls.winfo_rootx()
            assert button.winfo_rootx() + button.winfo_width() < root.winfo_rootx() + width
        for _ in range(2):
            widgets.update_button.invoke()
            root.update()
            dialogs = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
            assert len(dialogs) == 1 and dialogs[0].title() == "发现新版本"
            dialog = dialogs[0]
            dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))
            root.update()
            assert widgets.update_button.winfo_ismapped()
        receive({"error": "临时断网"})
        assert host._available_update == result
        if "--screenshot" in sys.argv:
            from PIL import ImageGrab
            output = Path(sys.argv[sys.argv.index("--screenshot") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            root.geometry("1180x860+40+40")
            root.attributes("-topmost", True)
            root.deiconify()
            root.lift()
            root.focus_force()
            root.update()
            root.after(400, root.quit)
            root.mainloop()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + 170)).save(output)
            widgets.update_button.event_generate(
                "<Enter>", rootx=widgets.update_button.winfo_rootx() + 15,
                rooty=widgets.update_button.winfo_rooty() + 15,
            )
            root.update()
            assert host._tooltip is not None
            assert host._tooltip.winfo_rooty() + host._tooltip.winfo_height() <= widgets.update_button.winfo_rooty() - 8
            assert abs(
                host._tooltip.winfo_rootx() + host._tooltip.winfo_width() / 2
                - widgets.update_button.winfo_rootx() - widgets.update_button.winfo_width() / 2
            ) <= 1
            host._tooltip.attributes("-topmost", True)
            host._tooltip.lift()
            root.after(300, root.quit)
            root.mainloop()
            ImageGrab.grab(bbox=(x, max(0, y - 40), x + root.winfo_width(), y + 130)).save(
                output.with_stem(output.stem + "-hover"),
            )
            widgets.update_button.event_generate("<Leave>")
            root.update()
            assert host._tooltip is None
            print(f"SCREENSHOT: {output}")
        receive({"error": None, "has_update": False})
        root.update()
        assert not widgets.update_button.winfo_ismapped()
        print("HOME_UPDATE_SMOKE_OK: hidden/notify/click/close/reopen/error/clear, widths 1024/1180/1440")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
