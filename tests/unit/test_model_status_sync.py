"""Connection results must reach all views of the same model identity."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gui_main
from api_connectivity import ModelConnectivityResult


REF = {"api_provider": "codex", "base_url": "codex://local", "model": "gpt-6.1-sol"}


class ModelTree:
    def __init__(self):
        self.rows = {
            "current": (REF["model"], gui_main.PROVIDER_DISPLAY["codex"], "未检测", REF["base_url"]),
            "other": (REF["model"], gui_main.PROVIDER_DISPLAY["codex"], "未检测", "codex://other"),
        }

    def get_children(self):
        return tuple(self.rows)

    def exists(self, row):
        return row in self.rows

    def item(self, row, **kwargs):
        if kwargs:
            self.rows[row] = kwargs["values"]
        return {"values": self.rows[row]}


def app():
    gui = gui_main.BossFilterGUI.__new__(gui_main.BossFilterGUI)
    gui.api_config = {**REF, "saved_models": [dict(REF)]}
    gui.DISPLAY_TO_KEY = gui_main.DISPLAY_TO_KEY
    gui.colors = {"success": "green", "warning": "orange", "danger": "red", "text_secondary": "gray"}
    gui._assigned_model_test_states = {"default": "pending", "education": "pending"}
    gui._assigned_model_test_tokens = {"default": 0, "education": 0}
    gui._assigned_model_test_icons = {"pending": "yellow", "success": "green", "error": "red"}
    gui._assigned_model_test_buttons = {role: Mock() for role in ("default", "education")}
    gui._assigned_model_test_status_labels = {role: Mock() for role in ("default", "education")}
    gui.model_list_tree = ModelTree()
    gui._update_api_status = Mock()
    return gui


def result(connected=True, vision=True):
    return ModelConnectivityResult(
        "limited" if connected else "incompatible", 1.2,
        "连接成功；已验证支持图片识别" if vision else "图片能力尚未确认",
        {"output_mode": "vision_probe", "connected": connected, "vision_verified": vision},
    )


def test_connection_button_updates_both_lights_and_saved_row_on_ui_thread():
    gui = app()
    for name, value in (("key", ""), ("base_url", REF["base_url"]),
                        ("model", REF["model"]), ("provider", gui_main.PROVIDER_DISPLAY["codex"])):
        setattr(gui, f"api_{name}_var", SimpleNamespace(get=lambda value=value: value))
    queued = []
    gui.run_on_ui = queued.append
    with patch.object(gui_main.threading, "Thread") as thread, \
            patch.object(gui_main, "probe_api_connectivity", return_value=result()):
        gui.test_api_connection()
        assert set(gui._assigned_model_test_states.values()) == {"testing"}
        assert gui.model_list_tree.rows["current"][2] == "测试中..."
        thread.call_args.kwargs["target"]()
        assert set(gui._assigned_model_test_states.values()) == {"testing"}
        queued.pop()()
    assert set(gui._assigned_model_test_states.values()) == {"success"}
    for label in gui._assigned_model_test_status_labels.values():
        label.configure.assert_called_with(text="已通过", foreground="green")
    assert gui.model_list_tree.rows["current"][2] == "✓ 可用"
    assert "已验证支持图片识别" in gui._update_api_status.call_args.kwargs["text"]
    assert gui.model_list_tree.rows["other"][2] == "未检测"


def test_connection_failure_clears_previous_success_in_all_three_places():
    gui = app()
    gui._apply_api_connectivity_result(result(), REF["model"], REF)
    token = gui._sync_model_test_state(REF, "testing", "测试中...")
    gui._apply_api_connectivity_result(result(False, False), REF["model"], REF, token)
    assert set(gui._assigned_model_test_states.values()) == {"error"}
    assert gui.model_list_tree.rows["current"][2] == "✗ 连接失败"


def test_connected_without_image_proof_keeps_separate_vision_message():
    gui = app()
    gui._apply_api_connectivity_result(result(True, False), REF["model"], REF)
    assert set(gui._assigned_model_test_states.values()) == {"success"}
    assert gui.model_list_tree.rows["current"][2] == "✓ 可用"
    assert gui._update_api_status.call_args.kwargs["text"] == "图片能力尚未确认"
    assert gui._update_api_status.call_args.kwargs["foreground"] == "orange"


def test_same_name_at_another_endpoint_and_switched_assignment_are_independent():
    gui = app()
    other = {**REF, "base_url": "codex://other"}
    gui.api_config["education_model_ref"] = other
    gui._apply_api_connectivity_result(result(), REF["model"], REF)
    assert gui._assigned_model_test_states == {"default": "success", "education": "pending"}
    gui.api_config.update(other)
    gui._assigned_model_test_states["default"] = "pending"
    gui._apply_api_connectivity_result(result(), REF["model"], REF)
    assert set(gui._assigned_model_test_states.values()) == {"pending"}
    assert gui.model_list_tree.rows["other"][2] == "未检测"


def test_late_result_does_not_overwrite_a_newer_test():
    gui = app()
    old = gui._sync_model_test_state(REF, "testing", "测试中...")
    current = gui._sync_model_test_state(REF, "testing", "测试中...")
    gui._apply_api_connectivity_result(result(False, False), REF["model"], REF, current)
    calls = gui._update_api_status.call_count
    gui._apply_api_connectivity_result(result(), REF["model"], REF, old)
    assert set(gui._assigned_model_test_states.values()) == {"error"}
    assert gui.model_list_tree.rows["current"][2] == "✗ 连接失败"
    assert gui._update_api_status.call_count == calls


def test_saved_row_recreation_during_test_uses_identity_instead_of_old_item_id():
    gui = app()
    token = gui._sync_model_test_state(REF, "testing", "测试中...")
    gui.model_list_tree.rows["new-row"] = gui.model_list_tree.rows.pop("current")
    gui._apply_model_connectivity_result({
        "model_config": REF, "item_id": "current", "connectivity_token": token,
    }, {"status": "success", "capability": dict(result().capability, message="已验证支持图片识别")},
        {"done": 0, "success": 0, "fail": 0}, 1)
    assert gui.model_list_tree.rows["new-row"][2] == "✓ 可用"
    assert set(gui._assigned_model_test_states.values()) == {"success"}


def test_assignment_light_image_test_also_updates_saved_row():
    gui = app()
    gui._get_api_key_cached = Mock(return_value="")
    queued = []
    gui.run_on_ui = queued.append

    def probe(_config, _key, **kwargs):
        kwargs["on_connected"]()
        return True

    with patch.object(gui_main.threading, "Thread") as thread, \
            patch("vision_capability.probe_vision_capability", side_effect=probe):
        gui._test_assigned_model("default")
        assert set(gui._assigned_model_test_states.values()) == {"testing"}
        thread.call_args.kwargs["target"]()
        queued.pop()()
    assert set(gui._assigned_model_test_states.values()) == {"success"}
    assert gui.model_list_tree.rows["current"][2] == "✓ 可用"
