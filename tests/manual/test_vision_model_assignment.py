"""Real Tk save/selection smoke test with isolated configs and synthetic HTTP."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"

from gui_main import BossFilterGUI
from api_connectivity import probe_api_connectivity
import vision_capability as vision


def run_smoke():
    for standalone in (False, True):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            models = [
                {"api_provider": "custom", "base_url": "https://vision.example.test/v1", "model": name}
                for name in ("primary-old", "primary-new", "education-only")
            ]
            path = folder / "api.json"
            path.write_text(json.dumps({**models[0], "saved_models": models}), encoding="utf-8")
            response = SimpleNamespace(status_code=200, json=lambda: {
                "choices": [{"message": {"content": '{"code":"22222222"}'}, "finish_reason": "stop"}],
            })
            root = tk.Tk()
            root.withdraw()
            try:
                with patch.object(vision, "CACHE_DIR", folder / "evidence"), \
                        patch.object(vision.secrets, "choice", return_value="2"), \
                        patch("education_certificate.requests.post", return_value=response) as post, \
                        patch.object(BossFilterGUI, "_load_startup_updater"), \
                        patch.object(BossFilterGUI, "_status_flash"):
                    gui = BossFilterGUI(
                        root, standalone_education=standalone,
                        education_api_config_path=path,
                        education_api_key_getter=lambda *_: "synthetic-key",
                        education_api_key_saver=lambda *_: True,
                        run_preferences_path=folder / "preferences.json",
                        start_with_settings=True,
                    )
                    gui.show_page_api()
                    root.geometry("1300x950+12000+12000")
                    root.deiconify()
                    root.update()

                    def wait_verified(model, expected_requests):
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            root.update()
                            if (vision.has_verified_vision(model)
                                    and "已验证支持图片识别" in gui.api_status_label.cget("text")):
                                break
                            time.sleep(0.02)
                        assert vision.has_verified_vision(model)
                        assert "已验证支持图片识别" in gui.api_status_label.cget("text")
                        assert post.call_count == expected_requests, post.call_count

                    # Select a pre-existing untested primary through the real selector.
                    choice = next(label for label, ref in gui._model_choice_refs.items()
                                  if ref["model"] == "primary-new")
                    gui.default_model_choice_var.set(choice)
                    gui.default_model_combo.event_generate("<<ComboboxSelected>>")
                    wait_verified(models[1], 1)
                    assert gui.api_status_label.cget("text") == "primary-new 连接成功；已验证支持图片识别。"
                    assert gui._assigned_model_test_states["default"] == "success"
                    if not standalone:
                        assert gui._assigned_model_test_states["education"] == "success"
                    assert gui._get_education_api_config()["model"] == "primary-new"

                    if not standalone:
                        choice = next(label for label, ref in gui._model_choice_refs.items()
                                      if ref["model"] == "education-only")
                        gui.education_model_choice_var.set(choice)
                        gui.education_model_combo.event_generate("<<ComboboxSelected>>")
                        wait_verified(models[2], 2)
                        assert gui._assigned_model_test_states["education"] == "success"
                        assert gui._get_education_api_config()["model"] == "education-only"
                        gui.education_model_choice_var.set("跟随默认 AI 模型")
                        gui.education_model_combo.event_generate("<<ComboboxSelected>>")
                        wait_verified(models[1], 2)
                        assert gui._get_education_api_config()["model"] == "primary-new"

                    # Save a genuinely new model through the real form.
                    gui.api_provider_var.set(gui.PROVIDER_DISPLAY["custom"])
                    gui.api_base_url_var.set(models[0]["base_url"])
                    gui.api_model_var.set("newly-added")
                    gui.api_key_var.set("synthetic-key")
                    gui.save_api_config()
                    wait_verified({**models[0], "model": "newly-added"}, 2 if standalone else 3)
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    assert any(item["model"] == "newly-added" for item in saved["saved_models"])
                    assert "synthetic-key" not in path.read_text(encoding="utf-8")
                    choice = next(label for label, ref in gui._model_choice_refs.items()
                                  if ref["model"] == "newly-added")
                    gui.default_model_choice_var.set(choice)
                    gui.default_model_combo.event_generate("<<ComboboxSelected>>")
                    wait_verified({**models[0], "model": "newly-added"}, 2 if standalone else 3)
                    assert gui._assigned_model_test_states["default"] == "success"
                    # A null image answer still proves this endpoint connected.
                    # The primary button must test images when education follows it.
                    role = "default"
                    previous_requests = post.call_count
                    gui._test_assigned_model(role)
                    wait_verified({**models[0], "model": "newly-added"}, previous_requests + 1)
                    post.return_value = SimpleNamespace(status_code=200, json=lambda: {
                        "choices": [{"message": {"content": '{"code":null}'}, "finish_reason": "stop"}],
                    })
                    choice = next(label for label, ref in gui._model_choice_refs.items()
                                  if ref["model"] == "primary-old")
                    gui.default_model_choice_var.set(choice)
                    gui.default_model_combo.event_generate("<<ComboboxSelected>>")
                    expected = "primary-old 连接成功；图片测试未通过，多模态能力尚需验证。"
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        root.update()
                        if gui.api_status_label.cget("text") == expected:
                            break
                        time.sleep(0.02)
                    assert gui.api_status_label.cget("text") == expected
                    assert gui._assigned_model_test_states["default"] == "success"
                    if not standalone:
                        assert gui._assigned_model_test_states["education"] == "success"
                    assert not vision.has_verified_vision(models[0])
                    # The editable form and model library use the same image probe.
                    with patch("gui_main.probe_api_connectivity", side_effect=lambda config, key, **kwargs:
                               probe_api_connectivity(config, key, dns_lookup=lambda _: "127.0.0.1", **kwargs)):
                        gui.test_api_connection()
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            root.update()
                            if gui.api_status_label.cget("text") == expected:
                                break
                            time.sleep(0.02)
                        assert gui.api_status_label.cget("text") == expected
                    row = next(row for row in gui.model_list_tree.get_children()
                               if gui.model_list_tree.item(row, 'values')[0] == "primary-old")
                    gui.model_list_tree.selection_set(row)
                    gui.test_saved_model_connectivity()
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        root.update()
                        if gui.api_status_label.cget("text") == expected:
                            break
                        time.sleep(0.02)
                    assert gui.api_status_label.cget("text") == expected
                    assert "连接成功" in gui.model_list_tree.item(row, 'values')[2]
                    outcome = gui._probe_model_for_dialog("custom", models[0]["base_url"], "primary-old", "synthetic-key")
                    assert outcome.status == "success" and outcome.mode == expected
                    post.side_effect = TimeoutError("synthetic timeout")
                    gui._test_assigned_model(role)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        root.update()
                        if gui._assigned_model_test_states[role] == "error":
                            break
                        time.sleep(0.02)
                    assert gui._assigned_model_test_states[role] == "error"
                    assert "连接尚未确认" in gui.api_status_label.cget("text")
                    gui.show_page_education()
                    root.geometry("1300x950+12000+12000")
                    root.deiconify()
                    root.update()
                    gui.education_warning_var.set("证书编号为 17 位，请人工核对；关键查询字段仍存在明确疑点，已停止重复识别，请人工核对")

                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)

                    from tkinter import font as tkfont
                    for width in (1300, 1700, 2100):
                        root.geometry(f"{width}x950+12000+12000")
                        root.update()
                        label = next(widget for widget in descendants(gui.education_page)
                                     if widget.winfo_class() == "TLabel" and "证书编号为" in str(widget.cget("text")))
                        text = str(label.cget("text"))
                        assert "17位" in text and "17\n位" not in text
                        measured = tkfont.Font(root=root, font=label.cget("font"))
                        assert all(measured.measure(line) <= label.winfo_width() for line in text.splitlines())
                    print(f"TK_CERTIFICATE_WRAP {'standalone' if standalone else 'boss'} PASS widths=1300,1700,2100", flush=True)
                    print(f"TK_VISION {'standalone' if standalone else 'boss'} PASS requests={post.call_count}", flush=True)
            finally:
                for callback in root.tk.call("after", "info"):
                    root.after_cancel(callback)
                root.destroy()


if __name__ == "__main__":
    run_smoke()
