"""Image probes must prove pixel input and persist endpoint-scoped evidence."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import vision_capability as vision
from education_certificate import _invoke_model, likely_supports_vision


CONFIG = {"api_provider": "custom", "base_url": "https://example.test/v1", "model": "deepseek-flash"}


def response(payload):
    return SimpleNamespace(status_code=200, json=lambda: {
        "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
    })


def test_probe_checks_pixels_and_persists_without_secrets_or_retesting():
    with tempfile.TemporaryDirectory() as directory, patch.object(vision, "CACHE_DIR", Path(directory)), \
            patch.object(vision.secrets, "choice", side_effect=list("23456789")), \
            patch("education_certificate.requests.post", return_value=response({"code": "23456789"})) as post:
        assert not likely_supports_vision(CONFIG)
        verified = Mock()
        assert vision.probe_vision_capability(CONFIG, "secret-test-key", on_verified=verified)
        verified.assert_called_once_with()
        body = post.call_args.kwargs["json"]
        assert "23456789" not in json.dumps(body)
        assert "image_url" in json.dumps(body)
        records = list(Path(directory).glob("*.json"))
        assert len(records) == 1
        saved = records[0].read_text()
        assert "secret-test-key" not in saved and "23456789" not in saved
        # Simulate a new process by discarding all session evidence.
        vision._confirmed.clear()
        assert likely_supports_vision(CONFIG)
        verified.reset_mock()
        assert vision.probe_vision_capability(CONFIG, "another-key", on_verified=verified)
        verified.assert_not_called()
        assert post.call_count == 1
        for changed in ({"api_provider": "deepseek"}, {"base_url": "https://other.test/v1"},
                        {"model": "other-model"}, {"deployment": "another"}):
            assert not vision.has_verified_vision({**CONFIG, **changed})


def test_wrong_empty_and_failed_probes_never_confirm_vision():
    for result in (response({"code": "wrong"}), response({}), RuntimeError("network unavailable"),
                   response({"name": "张三", "certificate_number": "123456789012345678", "confidence": 99})):
        with tempfile.TemporaryDirectory() as directory, patch.object(vision, "CACHE_DIR", Path(directory)), \
                patch("education_certificate.requests.post", side_effect=(
                    result if isinstance(result, Exception) else lambda *a, **k: result
                )):
            assert not vision.probe_vision_capability(CONFIG, "test-key")
            assert not vision.has_verified_vision(CONFIG)
            assert not list(Path(directory).iterdir())


def test_real_image_success_corrects_hint_but_text_pdf_and_empty_results_do_not():
    payload = {"name": "张三", "certificate_number": "123456789012345678", "confidence": 95}
    with tempfile.TemporaryDirectory() as directory, patch.object(vision, "CACHE_DIR", Path(directory)), \
            patch("education_certificate.requests.post", return_value=response(payload)):
        _invoke_model(CONFIG, "test-key", [{"role": "user", "content": "PDF text"}])
        assert not likely_supports_vision(CONFIG)
        image_messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}]}]
        for invalid in ({}, {**payload, "confidence": 0}, {"rotation": 0, "rotation_confidence": 99}):
            vision.observe_image_recognition(CONFIG, image_messages, invalid)
            assert not likely_supports_vision(CONFIG)
        _invoke_model(CONFIG, "test-key", image_messages)
        assert likely_supports_vision(CONFIG)


def test_anthropic_image_protocol_and_captcha_success_are_supported():
    config = {**CONFIG, "api_provider": "anthropic"}
    with tempfile.TemporaryDirectory() as directory, patch.object(vision, "CACHE_DIR", Path(directory)), \
            patch.object(vision.secrets, "choice", side_effect=list("23456789")), \
            patch("education_certificate.requests.post", return_value=SimpleNamespace(
                status_code=200, json=lambda: {"content": [{"type": "text", "text": '{"code":"23456789"}'}], "stop_reason": "end_turn"},
            )) as post:
        assert vision.probe_vision_capability(config, "test-key")
        assert post.call_args.kwargs["json"]["messages"][0]["content"][0]["type"] == "image"
        vision.observe_image_recognition(CONFIG, [{"content": [{"type": "image"}]}],
                                         {"type": "letter", "answer": "Ab3D", "confidence": 90})
        assert vision.has_verified_vision(CONFIG)


def test_cache_corruption_and_write_failure_do_not_break_recognition():
    with tempfile.TemporaryDirectory() as directory, patch.object(vision, "CACHE_DIR", Path(directory)):
        key = vision._identity(CONFIG)
        (Path(directory) / f"{key[1]}.json").write_text("[]")
        assert not vision.has_verified_vision(CONFIG)
        with patch.object(vision.tempfile, "NamedTemporaryFile", side_effect=OSError("read only")):
            vision.record_vision_success(CONFIG)
        assert vision.has_verified_vision(CONFIG)


def test_boss_and_standalone_probe_workers_use_ui_dispatch_and_report_unknown():
    from gui_main import BossFilterGUI

    for standalone in (False, True):
        gui = BossFilterGUI.__new__(BossFilterGUI)
        gui.standalone_education = standalone
        gui.colors = {"warning": "orange", "success": "green"}
        gui._update_api_status = Mock()
        queued = []
        gui.run_on_ui = queued.append
        with patch("gui_main.threading.Thread") as thread, \
                patch.object(vision, "probe_vision_capability", side_effect=[True, False]) as probe:
            gui._test_added_models_vision("custom", CONFIG["base_url"], ("one", "two"), "test-key")
            thread.return_value.start.assert_called_once()
            thread.call_args.kwargs["target"]()
            assert len(queued) == 1
            assert probe.call_count == 2
            assert gui._update_api_status.call_count == 1
            queued.pop()()
            assert "one 已验证支持图片识别" in gui._update_api_status.call_args.kwargs["text"]
            assert "two 连接尚未确认；图片测试未完成，多模态能力尚需验证。" in gui._update_api_status.call_args.kwargs["text"]


def test_empty_image_answer_proves_connection_but_not_vision():
    with tempfile.TemporaryDirectory() as directory, patch.object(vision, "CACHE_DIR", Path(directory)), \
            patch("education_certificate.requests.post", return_value=response({"code": None})):
        connected, verified = Mock(), Mock()
        assert not vision.probe_vision_capability(CONFIG, "test-key", on_connected=connected, on_verified=verified)
        connected.assert_called_once_with()
        verified.assert_not_called()
        assert not vision.has_verified_vision(CONFIG)


def test_transport_failure_and_cached_vision_do_not_claim_live_connection():
    with tempfile.TemporaryDirectory() as directory, patch.object(vision, "CACHE_DIR", Path(directory)):
        connected = Mock()
        with patch("education_certificate.requests.post", side_effect=TimeoutError()):
            assert not vision.probe_vision_capability(CONFIG, "test-key", on_connected=connected)
        connected.assert_not_called()
        vision.record_vision_success(CONFIG)
        with patch("education_certificate.requests.post") as post:
            assert vision.probe_vision_capability(CONFIG, "test-key", on_connected=connected)
        connected.assert_not_called()
        post.assert_not_called()


def test_explicit_image_test_bypasses_cached_success():
    with tempfile.TemporaryDirectory() as directory, patch.object(vision, "CACHE_DIR", Path(directory)):
        vision.record_vision_success(CONFIG)
        connected = Mock()
        with patch("education_certificate.requests.post", return_value=response({"code": None})) as post:
            assert not vision.probe_vision_capability(CONFIG, "test-key", force=True, on_connected=connected)
        post.assert_called_once()
        connected.assert_called_once_with()
        assert vision.has_verified_vision(CONFIG)


def test_manual_probe_reports_connection_and_vision_separately():
    with tempfile.TemporaryDirectory() as directory, patch.object(vision, "CACHE_DIR", Path(directory)), \
            patch("education_certificate.requests.post", return_value=response({"code": None})) as post:
        result = vision.probe_image_connectivity(CONFIG, "test-key")
        assert result["connected"] and not result["vision_verified"]
        assert result["output_mode"] == "vision_probe"
        assert "连接成功；图片测试未通过，多模态能力尚需验证。" in result["message"]
        assert post.call_count == 1
        post.side_effect = TimeoutError()
        result = vision.probe_image_connectivity(CONFIG, "test-key")
        assert not result["connected"] and not result["vision_verified"]
        assert "连接尚未确认" in result["message"]


def test_all_role_buttons_test_images_regardless_of_education_assignment():
    from gui_main import BossFilterGUI

    other = {**CONFIG, "model": "education-other"}
    for standalone, education, role, expect_image in (
        (False, CONFIG, "default", True),  # following, or explicitly the same identity
        (False, other, "default", True),
        (False, other, "education", True),
        (True, CONFIG, "default", True),
        (False, {**CONFIG, "base_url": "https://other.test/v1"}, "default", True),
    ):
        gui = BossFilterGUI.__new__(BossFilterGUI)
        gui.standalone_education = standalone
        refs = {"default": CONFIG, "education": education}
        gui._get_assigned_model_ref = refs.get
        gui._assigned_model_test_tokens = {"default": 0, "education": 0}
        gui._assigned_model_test_refs = {}
        gui._set_assigned_model_test_state = Mock()
        gui._test_selected_model_vision = Mock()
        gui._update_api_status = Mock()
        gui._assigned_model_test_target_label = Mock(return_value="test")
        gui.colors = {"warning": "orange"}
        gui.DISPLAY_TO_KEY = {}
        gui.model_list_tree = Mock()
        gui.model_list_tree.get_children.return_value = ["row"]
        gui.model_list_tree.item.return_value = (CONFIG["model"], CONFIG["api_provider"], "", CONFIG["base_url"])
        gui.test_saved_model_connectivity = Mock()
        gui._test_assigned_model(role)
        if expect_image:
            gui._test_selected_model_vision.assert_called_once_with(refs[role], force=True)
            gui.test_saved_model_connectivity.assert_not_called()
        else:
            gui._test_selected_model_vision.assert_not_called()
            gui.test_saved_model_connectivity.assert_called_once()


def test_selection_reuses_evidence_and_manual_test_invalidates_old_connection_result():
    from gui_main import BossFilterGUI

    for standalone in (False, True):
        gui = BossFilterGUI.__new__(BossFilterGUI)
        gui.standalone_education = standalone
        gui._assigned_model_test_tokens = {"default": 4}
        gui._assigned_model_test_refs = {"default": CONFIG}
        gui._get_assigned_model_ref = Mock(return_value=CONFIG)
        gui._set_assigned_model_test_state = Mock()
        gui._test_added_models_vision = Mock()
        gui._test_selected_model_vision(CONFIG)
        assert gui._assigned_model_test_tokens["default"] == 4
        gui._set_assigned_model_test_state.assert_not_called()
        assert gui._test_added_models_vision.call_args.kwargs["force"] is False
        gui._test_selected_model_vision(CONFIG, force=True)
        assert gui._assigned_model_test_tokens["default"] == 5
        gui._set_assigned_model_test_state.assert_called_once_with("default", "testing")
        assert gui._test_added_models_vision.call_args.kwargs["force"] is True
