"""学历证书核验助手独立入口。"""
from __future__ import annotations

import sys
import tkinter as tk

from education_tool_config import (
    EDUCATION_TOOL_API_CONFIG,
    get_education_tool_config_path,
    get_education_tool_preferences_path,
)
from education_tool_security import get_education_api_key, save_education_api_key
from gui_main import (
    BossFilterGUI,
    _enable_high_dpi_awareness,
    _get_windows_monitor_area,
    _show_main_window_centered,
)


def _assert_page_fills_viewport(gui: BossFilterGUI, page: tk.Widget) -> None:
    """Fail the packaged smoke test when the first page collapses at startup."""
    viewport_width = int(gui.pages_frame.winfo_width())
    viewport_height = int(gui.pages_frame.winfo_height())
    page_width = int(page.winfo_width())
    page_height = int(page.winfo_height())
    minimum_page_width = max(400, int(viewport_width * 0.8))
    minimum_page_height = max(300, int(viewport_height * 0.8))
    if (
        viewport_width < 400
        or viewport_height < 300
        or page_width < minimum_page_width
        or page_height < minimum_page_height
    ):
        raise RuntimeError(
            "独立工具首个业务页面布局异常："
            f"viewport={viewport_width}x{viewport_height}, "
            f"page={page_width}x{page_height}"
        )


def main(*, smoke_test: bool = False) -> None:
    _enable_high_dpi_awareness()
    startup_monitor_area = _get_windows_monitor_area()
    root = tk.Tk()
    root.withdraw()
    config_path = get_education_tool_config_path()
    gui = BossFilterGUI(
        root,
        standalone_education=True,
        education_api_config=EDUCATION_TOOL_API_CONFIG,
        education_api_config_path=config_path,
        education_api_key_getter=get_education_api_key,
        education_api_key_saver=save_education_api_key,
        run_preferences_path=get_education_tool_preferences_path(),
        start_with_settings=not config_path.is_file(),
    )
    _show_main_window_centered(root, startup_monitor_area)
    if smoke_test:
        # Read a guaranteed-unused target to exercise the packaged Windows
        # credential backend without creating or changing any credential.
        get_education_api_key(
            "education-tool-smoke-test",
            "https://smoke-test.invalid/v1",
        )
        root.update_idletasks()
        root.update()
        if not root.winfo_viewable():
            raise RuntimeError("独立工具主窗口未进入可见状态")
        expected_page = (
            gui.api_config_page if not config_path.is_file() else gui.education_page
        )
        if expected_page is None:
            raise RuntimeError("独立工具首个业务页面未创建")
        _assert_page_fills_viewport(gui, expected_page)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    _smoke_test = "--smoke-test" in sys.argv[1:]
    if _smoke_test:
        try:
            main(smoke_test=True)
        except Exception as error:
            print(f"packaged smoke test failed: {error}", file=sys.stderr)
            raise SystemExit(1) from error
    else:
        main()
