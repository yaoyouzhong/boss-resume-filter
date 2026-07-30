"""Capture GUI screenshots for the Markdown user guide."""

from __future__ import annotations

import time
import os
import sys
from pathlib import Path

from PIL import Image, ImageGrab

import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "assets" / "user-guide"
sys.path.insert(0, str(ROOT))
os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"] = "1"

import gui_main


def capture_window(root: tk.Tk, filename: str) -> None:
    """Save the current Tk root window as a PNG."""
    root.update_idletasks()
    root.update()
    time.sleep(0.35)

    x = root.winfo_rootx()
    y = root.winfo_rooty()
    w = root.winfo_width()
    h = root.winfo_height()
    image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    image.save(OUT_DIR / filename)


def capture_toplevel(root: tk.Tk, title: str, filename: str) -> None:
    """Capture a visible top-level dialog by its window title."""
    root.update_idletasks()
    root.update()
    time.sleep(0.5)

    target = None
    for window in root.winfo_children():
        if isinstance(window, tk.Toplevel) and window.winfo_exists() and window.title() == title:
            target = window
            break
    if target is None:
        raise RuntimeError(f"Dialog not found: {title}")

    target.lift()
    root.update_idletasks()
    root.update()
    time.sleep(0.25)

    x = target.winfo_rootx()
    y = target.winfo_rooty()
    w = target.winfo_width()
    h = target.winfo_height()
    image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    image.save(OUT_DIR / filename)
    target.destroy()
    root.update_idletasks()
    root.update()


def find_child_widgets(parent: tk.Widget, widget_type: type) -> list[tk.Widget]:
    """Find child widgets of a given Tk type."""
    found = []
    for child in parent.winfo_children():
        if isinstance(child, widget_type):
            found.append(child)
        found.extend(find_child_widgets(child, widget_type))
    return found


def capture_toplevel_with_full_text(root: tk.Tk, title: str, filename: str) -> None:
    """Capture a dialog and stitch the tallest scrollable Text widget."""
    root.update_idletasks()
    root.update()
    time.sleep(0.5)

    target = None
    for window in root.winfo_children():
        if isinstance(window, tk.Toplevel) and window.winfo_exists() and window.title() == title:
            target = window
            break
    if target is None:
        raise RuntimeError(f"Dialog not found: {title}")

    text_widgets = find_child_widgets(target, tk.Text)
    if not text_widgets:
        capture_toplevel(root, title, filename)
        return
    text_widget = max(text_widgets, key=lambda widget: widget.winfo_width() * widget.winfo_height())

    target.lift()
    text_widget.yview_moveto(0.0)
    root.update_idletasks()
    root.update()
    time.sleep(0.25)

    dialog_x = target.winfo_rootx()
    dialog_y = target.winfo_rooty()
    dialog_w = target.winfo_width()
    dialog_h = target.winfo_height()
    text_x = text_widget.winfo_rootx()
    text_y = text_widget.winfo_rooty()
    text_w = text_widget.winfo_width()
    text_h = text_widget.winfo_height()

    first, last = text_widget.yview()
    visible_fraction = max(last - first, 0.001)
    content_h = max(text_h, int(text_h / visible_fraction))
    top_h = text_y - dialog_y
    bottom_h = max(dialog_h - top_h - text_h, 0)
    full_h = top_h + content_h + bottom_h

    first_screen = ImageGrab.grab(bbox=(dialog_x, dialog_y, dialog_x + dialog_w, dialog_y + dialog_h))
    stitched = Image.new("RGB", (dialog_w, full_h), "#F5F6F8")
    stitched.paste(first_screen.crop((0, 0, dialog_w, top_h)), (0, 0))
    if bottom_h:
        bottom_crop = first_screen.crop((0, top_h + text_h, dialog_w, dialog_h))
        stitched.paste(bottom_crop, (0, top_h + content_h))

    # Keep the left version list visible; the right detail text is stitched below.
    left_w = max(text_x - dialog_x, 0)
    if left_w:
        stitched.paste(first_screen.crop((0, 0, left_w, dialog_h)), (0, 0))

    max_offset = max(content_h - text_h, 0)
    step = max(text_h, 1)
    offsets = list(range(0, max_offset + 1, step))
    if offsets[-1] != max_offset:
        offsets.append(max_offset)

    for offset in offsets:
        text_widget.yview_moveto(offset / max(content_h, 1))
        root.update_idletasks()
        root.update()
        time.sleep(0.15)
        crop = ImageGrab.grab(bbox=(text_x, text_y, text_x + text_w, text_y + text_h))
        paste_h = min(text_h, content_h - offset)
        stitched.paste(crop.crop((0, 0, text_w, paste_h)), (text_x - dialog_x, top_h + offset))

    stitched.save(OUT_DIR / filename)
    target.destroy()
    root.update_idletasks()
    root.update()


def select_job_config(app: gui_main.BossFilterGUI, job_name: str) -> str:
    """Select a job in the config page, falling back to the first AI job."""
    jobs = list(getattr(app, "job_rules", {}).keys())
    selected = job_name if job_name in jobs else next((job for job in jobs if "AI" in job), jobs[0])
    app.config_job_combo.set(selected)
    app.on_job_selected(None)
    app.config_canvas.yview_moveto(0.0)
    return selected


def capture_scrollable_page(
    root: tk.Tk,
    page: tk.Widget,
    canvas: tk.Canvas,
    filename: str,
    bg: str = "#F5F6F8",
) -> None:
    """Capture a page whose main content is inside a vertical canvas."""
    root.update_idletasks()
    root.update()
    time.sleep(0.35)

    page_x = page.winfo_rootx()
    page_y = page.winfo_rooty()
    page_w = page.winfo_width()
    canvas_x = canvas.winfo_rootx()
    canvas_y = canvas.winfo_rooty()
    canvas_w = canvas.winfo_width()
    canvas_h = canvas.winfo_height()

    scrollregion = canvas.bbox("all")
    if not scrollregion:
        capture_window(root, filename)
        return

    content_h = scrollregion[3] - scrollregion[1]
    header_h = canvas_y - page_y
    stitched = Image.new("RGB", (page_w, header_h + content_h), bg)

    canvas.yview_moveto(0.0)
    root.update_idletasks()
    root.update()
    full_window = ImageGrab.grab(
        bbox=(page_x, page_y, page_x + page_w, page_y + page.winfo_height())
    )
    if header_h > 0:
        stitched.paste(full_window.crop((0, 0, page_w, header_h)), (0, 0))

    max_offset = max(content_h - canvas_h, 0)
    offsets = list(range(0, max_offset + 1, max(canvas_h, 1)))
    if offsets[-1] != max_offset:
        offsets.append(max_offset)

    for offset in offsets:
        canvas.yview_moveto(offset / max(content_h, 1))
        root.update_idletasks()
        root.update()
        time.sleep(0.15)
        crop = ImageGrab.grab(
            bbox=(canvas_x, canvas_y, canvas_x + canvas_w, canvas_y + canvas_h)
        )
        paste_h = min(canvas_h, content_h - offset)
        stitched.paste(crop.crop((0, 0, canvas_w, paste_h)), (canvas_x - page_x, header_h + offset))

    stitched.save(OUT_DIR / filename)
    canvas.yview_moveto(0.0)


def capture_job_config_full(root: tk.Tk, app: gui_main.BossFilterGUI, filename: str) -> None:
    """Capture the full scrollable job config page as one stitched image."""
    app.show_page_config()
    selected = select_job_config(app, "中高级AI开发工程师")
    root.update_idletasks()
    root.update()
    time.sleep(0.35)

    canvas = app.config_canvas
    page_x = app.config_page.winfo_rootx()
    page_y = app.config_page.winfo_rooty()
    page_w = app.config_page.winfo_width()
    canvas_x = canvas.winfo_rootx()
    canvas_y = canvas.winfo_rooty()
    canvas_w = canvas.winfo_width()
    canvas_h = canvas.winfo_height()
    btn_y = app.btn_frame.winfo_rooty()
    btn_h = app.btn_frame.winfo_height()

    scrollregion = canvas.bbox("all")
    if not scrollregion:
        capture_window(root, filename)
        return

    content_h = scrollregion[3] - scrollregion[1]
    header_h = canvas_y - page_y
    bottom_h = max(btn_h, 1)
    full_h = header_h + content_h + bottom_h
    stitched = Image.new("RGB", (page_w, full_h), "#F5F6F8")

    # Header and bottom controls are outside the scrollable canvas.
    app.config_canvas.yview_moveto(0.0)
    root.update_idletasks()
    root.update()
    full_window = ImageGrab.grab(
        bbox=(page_x, page_y, page_x + page_w, page_y + app.config_page.winfo_height())
    )
    stitched.paste(full_window.crop((0, 0, page_w, header_h)), (0, 0))

    max_offset = max(content_h - canvas_h, 0)
    offsets = list(range(0, max_offset + 1, max(canvas_h, 1)))
    if offsets[-1] != max_offset:
        offsets.append(max_offset)

    for offset in offsets:
        canvas.yview_moveto(offset / max(content_h, 1))
        root.update_idletasks()
        root.update()
        time.sleep(0.15)
        crop = ImageGrab.grab(
            bbox=(canvas_x, canvas_y, canvas_x + canvas_w, canvas_y + canvas_h)
        )
        paste_h = min(canvas_h, content_h - offset)
        stitched.paste(crop.crop((0, 0, canvas_w, paste_h)), (canvas_x - page_x, header_h + offset))

    canvas.yview_moveto(1.0)
    root.update_idletasks()
    root.update()
    time.sleep(0.15)
    bottom_window = ImageGrab.grab(
        bbox=(page_x, btn_y, page_x + page_w, btn_y + bottom_h)
    )
    stitched.paste(bottom_window, (0, header_h + content_h))

    stitched.save(OUT_DIR / filename)
    canvas.yview_moveto(0.0)
    print(f"Selected job config screenshot: {selected}")


def capture_run_full(root: tk.Tk, app: gui_main.BossFilterGUI, filename: str) -> None:
    """Capture the full run-control page."""
    app.show_page_run()
    app.run_canvas.yview_moveto(0.0)
    capture_scrollable_page(root, app.run_page, app.run_canvas, filename)


def capture_api_config_full(root: tk.Tk, app: gui_main.BossFilterGUI, filename: str) -> None:
    """Capture the full system settings page."""
    app.show_page_api()
    app.api_canvas.yview_moveto(0.0)
    capture_scrollable_page(root, app.api_config_page, app.api_canvas, filename)


def capture_changelog_dialog(root: tk.Tk, app: gui_main.BossFilterGUI, filename: str) -> None:
    """Capture the version history dialog."""
    app.show_page_home()
    app.show_changelog()
    capture_toplevel(root, "更新日志", filename)


def capture_update_dialog(root: tk.Tk, app: gui_main.BossFilterGUI, filename: str) -> None:
    """Capture a sample auto-update prompt without downloading anything."""
    sample_result = {
        "current": "2.10.3",
        "latest": "2.11",
        "download_url": "https://example.com/BOSS_ResumeFilter.exe",
        "download_url_fallback": "https://example.com/BOSS_ResumeFilter.exe",
        "asset_info": {},
        "changelog_body": """### 新增功能
- **候选人黑名单**：按 geek_id 跨岗位屏蔽，扫描、统计和 Excel 导出自动跳过。
- **简历二次评估**：支持导入 PDF/Word 简历，再次调用 LLM 评估并叠加调整分。
- **显示已屏蔽候选人**：结果页新增开关，可临时查看和恢复黑名单候选人。

### 体验优化
- **黑名单弹窗布局优化**：屏蔽原因使用提示文本引导填写，按钮风格统一。
- **安装包更轻**：PDF 解析切换到 pdfminer.six，体积更小，中文解析更准确。
""",
    }
    gui_main.updater.show_update_dialog(root, sample_result, gui=app)
    capture_toplevel(root, "发现新版本", filename)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gui_main._enable_high_dpi_awareness()
    monitor_area = gui_main._get_windows_monitor_area()

    root = tk.Tk()
    root.withdraw()
    app = gui_main.BossFilterGUI(root)
    app._start_browser_auto_check = lambda: None
    app._stop_browser_auto_check = lambda: None
    app._schedule_api_key_resolution = lambda: None
    gui_main._show_main_window_centered(root, monitor_area)
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    root.attributes("-topmost", False)

    pages = [
        ("01-home.png", app.show_page_home),
        ("05-results.png", app.show_page_result),
        ("06-stats.png", app.show_page_stats),
    ]

    for filename, show_page in pages:
        show_page()
        capture_window(root, filename)

    capture_job_config_full(root, app, "02-job-config-full.png")
    capture_api_config_full(root, app, "03-api-config-full.png")
    capture_run_full(root, app, "04-run-full.png")
    capture_changelog_dialog(root, app, "07-changelog-dialog.png")
    capture_update_dialog(root, app, "08-update-dialog.png")

    root.destroy()


if __name__ == "__main__":
    main()
