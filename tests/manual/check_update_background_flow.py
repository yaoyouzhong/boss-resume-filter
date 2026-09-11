"""Local HTTP + real Tk lifecycle test; never installs or accesses user data."""
import hashlib
import json
import queue
import sys
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import gui_home_page
import gui_settings_page
import gui_style_setup
import gui_widget_support
import icons
import updater
from gui_feedback_support import FeedbackSupport
from gui_main import BossFilterGUI, UI_CONFIG
from update_controller import UpdateController
from update_store import UpdateStore


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(window, text):
    return next(w for w in descendants(window) if w.winfo_class() in {"TButton", "TCheckbutton"} and w.cget("text") == text)


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    root = tk.Tk()
    root.tk.call("tk", "scaling", 96 / 72)
    root.geometry("1180x860+40+40")
    root.title("后台更新完整流程验证")
    ui_queue = queue.Queue()
    errors = []
    root.report_callback_exception = lambda kind, error, tb: errors.append(error)

    def drain():
        while not ui_queue.empty():
            ui_queue.get_nowait()()
        root.after(10, drain)
    drain()

    def wait_for(predicate, timeout=8):
        deadline = time.monotonic() + timeout
        def poll():
            if predicate() or time.monotonic() >= deadline:
                root.quit()
            else:
                root.after(20, poll)
        root.after(20, poll)
        root.mainloop()
        root.update_idletasks()
        assert predicate(), "Timed out waiting for update state"
        assert not errors, errors

    host = SimpleNamespace(
        root=root, pages_frame=root, dpi_scale=1.0, zoom_factor=1.0, font_boost=1.0,
        font_scale=1.0, icons=icons.init(1.0), run_on_ui=ui_queue.put,
        app_shell=Mock(), refresh_home_stats=Mock(), show_stat_detail=Mock(),
        on_home_task_click=Mock(), on_home_health_click=Mock(),
        import_external_candidate=Mock(), open_home_data_maintenance=Mock(),
        open_home_system_settings=Mock(), is_running=False,
    )
    gui_style_setup.setup_styles(host)
    host.feedback_support = FeedbackSupport(host, font_family=host.home_fonts["meta"][0])
    host.widget_support = gui_widget_support.WidgetSupport(host, ui_config=UI_CONFIG)
    for name in ("open_available_update", "update_tooltip_text", "_receive_update_result",
                 "_update_status_changed", "_update_business_busy", "update_auto_download_enabled",
                 "set_update_auto_download", "check_for_updates"):
        setattr(host, name, getattr(BossFilterGUI, name).__get__(host))
    widgets = gui_home_page.build_home_page(
        host, UI_CONFIG, run_page_index=2, result_page_index=3, config_page_index=1, education_page_index=4,
    )
    host._home_page_widgets = widgets
    widgets.page.pack(fill="both", expand=True)
    payload = b"MZ" + b"synthetic-package" * 4096
    release = dict(
        version="2.34", release_notes="### 体验优化\n- 测试用更新内容",
        assets={"windows": {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}},
    )
    gate = threading.Event()
    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests_seen.append(self.path)
            body = json.dumps(release).encode() if self.path == "/latest.json" else payload
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.path == "/package.exe":
                self.wfile.write(body[:8192])
                self.wfile.flush()
                gate.wait(20)
                self.wfile.write(body[8192:])
            else:
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    release["downloads"] = {"windows": base + "/package.exe"}
    actual_check = updater.check_gitee_latest
    try:
        with TemporaryDirectory() as folder, patch.object(updater, "get_base_dir", return_value=Path(folder)), patch.object(
            updater, "check_gitee_latest", side_effect=lambda **kwargs: actual_check(base + "/latest.json", **kwargs),
        ), patch.object(updater, "_fetch_changelog_section", return_value="### 体验优化\n- 测试用更新内容"), patch.object(
            updater, "check_github_release", side_effect=AssertionError("Must not access GitHub"),
        ), patch.object(updater, "update_windows", return_value=(False, "测试桩：不执行安装")) as install, patch.object(
            updater.messagebox, "showinfo",
        ) as notice:
            store = UpdateStore(Path(folder))
            controller = UpdateController(
                current_version="2.33", load=store.load, save=store.save,
                check=lambda done: updater.check_and_update_gui(
                    root, silent=True, gui=host, current_version="2.33", on_complete=done,
                    on_update_available=lambda _result: None,
                ), download=updater.download_update_package, dispatch=ui_queue.put,
                changed=host._update_status_changed, is_busy=host._update_business_busy,
            )
            host._update_controller = controller
            host._ensure_update_controller = lambda: host._update_controller
            controller.tick()
            wait_for(lambda: controller.available is not None)
            assert widgets.update_button.winfo_ismapped()
            assert requests_seen == ["/latest.json"]
            assert not any(isinstance(w, tk.Toplevel) for w in root.winfo_children())

            # Build the real settings card and turn on its persisted checkbox.
            settings = tk.Toplevel(root)
            gui_settings_page.build_update_settings_card(host, settings)
            assert host.update_auto_download_var.get() is False
            host.is_running = True
            button(settings, "空闲时自动下载安装包").invoke()
            assert store.load()["auto_download"] is True and controller.download_state == "idle"
            settings.destroy()
            host.is_running = False
            controller.tick()
            wait_for(lambda: controller.progress[0] >= 8192)
            widgets.update_button.invoke()
            first = host._update_dialog
            wait_for(lambda: button(first, "立即更新").instate(["disabled"]))
            first.tk.call(first.protocol("WM_DELETE_WINDOW"))
            assert controller.download_state == "downloading"
            gate.set()
            wait_for(lambda: controller.download_state == "ready")
            install.assert_not_called()
            assert requests_seen.count("/package.exe") == 1

            # Reopen while already downloaded; install remains a distinct action.
            widgets.update_button.invoke()
            dialog = host._update_dialog
            wait_for(lambda: any(w.winfo_class() == "TButton" and w.cget("text") == "立即安装" for w in descendants(dialog)))
            host.is_running = True
            button(dialog, "立即安装").invoke()
            notice.assert_called_once()
            install.assert_not_called()
            host.is_running = False
            button(dialog, "立即安装").invoke()
            wait_for(lambda: install.call_count == 1 and not controller.installing)
            button(dialog, "重试安装").invoke()
            wait_for(lambda: install.call_count == 2 and not controller.installing)
            dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))

            # A fresh controller reads the disk snapshot and revalidates the cache.
            controller.close()
            restored = UpdateController(
                current_version="2.33", load=store.load, save=store.save,
                check=Mock(), download=updater.download_update_package, dispatch=ui_queue.put,
                changed=Mock(), is_busy=lambda: False,
            )
            restored.restore_cache(updater.get_cached_update)
            wait_for(lambda: restored.download_state == "ready")
            restored.tick()
            restored.check.assert_not_called()
            assert requests_seen.count("/package.exe") == 1
            assert restored.available["latest"] == "2.34"
            # Deleting a previously verified file is handled as a recoverable failure.
            host._update_controller = restored
            Path(restored.downloaded_path).unlink()
            widgets.update_button.invoke()
            damaged_dialog = host._update_dialog
            button(damaged_dialog, "立即安装").invoke()
            wait_for(lambda: restored.download_state == "failed" and not restored.installing)
            assert restored.download_error and install.call_count == 2
            damaged_dialog.tk.call(damaged_dialog.protocol("WM_DELETE_WINDOW"))
            restored.close()
            assert not errors
            print("BACKGROUND_UPDATE_E2E_OK: HTTP check, default off, busy defer, shared transfer, close/reopen, verified cache, explicit install guard, restart reuse")
    finally:
        gate.set()
        server.shutdown()
        server.server_close()
        root.destroy()


if __name__ == "__main__":
    main()
