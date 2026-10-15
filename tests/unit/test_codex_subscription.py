"""Subscription transport and business routes; never use live credentials/network."""

import base64
import io
import json
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

import codex_adapter as codex
import education_certificate
import job_ai_parser
import llm_eval
import model_catalog
import resume_ai_profile
from api_connectivity import probe_api_connectivity
from gui_main import BossFilterGUI


CONFIG = {"api_provider": "codex", "base_url": "codex://local", "model": "gpt-6.1-sol"}
MESSAGES = [{"role": "user", "content": "synthetic task"}]


def test_subscription_does_not_read_or_save_api_keys():
    app = BossFilterGUI.__new__(BossFilterGUI)
    app._api_key_getter = Mock(side_effect=AssertionError("must not read keys"))
    assert app._read_runtime_api_key("codex", "codex://local") == ""
    assert codex.has_model_credentials(CONFIG, "")
    assert not codex.has_model_credentials({"api_provider": "openai"}, "")
    app._api_key_getter.assert_not_called()
    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_KEY": "secret",
            "CODEX_API_KEY": "secret",
            "OPENAI_BASE_URL": "https://invalid.test",
        },
    ):
        child = codex.child_environment()
    assert not {"OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"} & child.keys()


def test_invalid_input_login_and_cancellation_never_launch_cli():
    adapter = codex.CodexAdapter()
    cfg = codex.CodexInvocation(CONFIG["model"], "connection_test")
    with patch.object(codex.subprocess, "Popen") as launch:
        for request, messages, code in (
            (replace(cfg, purpose="unknown"), MESSAGES, "CODEX_PURPOSE_UNSUPPORTED"),
            (replace(cfg, cancelled=lambda: True), MESSAGES, "AI_CANCELLED"),
            (
                cfg,
                [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "https://invalid.test/a.png"},
                            }
                        ],
                    }
                ],
                "CODEX_IMAGE_INVALID",
            ),
            (
                cfg,
                [{"role": "user", "content": "x" * codex.MAX_INPUT_BYTES}],
                "CODEX_INPUT_LIMIT",
            ),
        ):
            try:
                adapter.invoke(request, messages)
                assert False, code
            except codex.CodexError as exc:
                assert exc.code == code
        with patch.object(
            adapter,
            "status",
            return_value={"compatible": True, "chatgpt_logged_in": False},
        ):
            try:
                adapter.invoke(cfg, MESSAGES)
                assert False
            except codex.CodexError as exc:
                assert exc.code == "CODEX_CHATGPT_LOGIN_REQUIRED"
        launch.assert_not_called()


def _run_fake(event):
    adapter = codex.CodexAdapter()
    captured = {}

    def launch(args, **kwargs):
        captured["args"] = args
        captured["directory"] = kwargs["cwd"]
        Path(args[args.index("--output-last-message") + 1]).write_text(
            '{"text":"done"}', encoding="utf-8"
        )
        kwargs["stdout"].write(
            (json.dumps(event) + '\n{"type":"turn.completed"}\n').encode()
        )
        kwargs["stdout"].flush()
        return Mock(poll=Mock(return_value=0), returncode=0)

    with (
        patch.object(
            adapter,
            "status",
            return_value={"compatible": True, "chatgpt_logged_in": True},
        ),
        patch.object(adapter, "executable", return_value="codex.exe"),
        patch.object(codex.subprocess, "Popen", side_effect=launch),
        patch.object(codex, "stop_process") as stop,
    ):
        try:
            result = adapter.invoke(
                codex.CodexInvocation(CONFIG["model"], "connection_test"), MESSAGES
            )
        finally:
            stop.assert_called_once()
            assert not Path(captured["directory"]).exists()
    return result, captured


def test_isolated_structured_transport_and_tool_activity_rejection():
    result, captured = _run_fake(
        {"type": "item.completed", "item": {"type": "agent_message"}}
    )
    assert result == "done"
    args = captured["args"]
    for flag in codex._POLICY:
        assert flag in args
    assert args[-1] == "-"
    try:
        _run_fake({"type": "item.completed", "item": {"type": "command_execution"}})
        assert False
    except codex.CodexError as exc:
        assert exc.code == "CODEX_TOOL_FORBIDDEN"


def test_image_input_keeps_pixels_and_drops_metadata():
    from PIL.PngImagePlugin import PngInfo

    metadata = PngInfo()
    metadata.add_text("private", "synthetic metadata")
    stream = io.BytesIO()
    with Image.new("RGB", (40, 20), "blue") as image:
        image.save(stream, "PNG", pnginfo=metadata)
        expected = image.tobytes()
    url = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
    prompt, images = codex.prepare_messages(
        [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": url}}],
            }
        ]
    )
    assert "base64" not in prompt
    with Image.open(io.BytesIO(images[0])) as cleaned:
        assert cleaned.tobytes() == expected
        assert "private" not in cleaned.info


def test_text_business_routes_use_subscription_and_keep_existing_parsers():
    with (
        patch.object(
            llm_eval, "call_codex", return_value='{"adjustment":5,"reason":"匹配"}'
        ) as call,
        patch.object(llm_eval.requests, "Session") as http,
    ):
        event = threading.Event()
        result = llm_eval._call_llm_api(MESSAGES, CONFIG, "", stop_event=event)
        assert result.success and result.adjustment == 5
        assert call.call_args.kwargs["stop_event"] is event
        http.assert_not_called()
    for module, function, purpose in (
        (job_ai_parser, job_ai_parser._call_chat_completion, "requirement_extraction"),
        (resume_ai_profile, resume_ai_profile._call_profile_chat, "resume_profile"),
    ):
        with (
            patch.object(module, "call_codex", return_value='{"ok":true}') as call,
            patch.object(module.requests, "post") as http,
        ):
            assert function(CONFIG, "", MESSAGES) == '{"ok":true}'
            assert call.call_args.kwargs["purpose"] == purpose
            http.assert_not_called()


def test_image_route_uses_remaining_budget_and_stop_event():
    event = threading.Event()
    token = education_certificate._recognition_budget.set(
        (codex.time.monotonic() + 20, event)
    )
    try:
        with (
            patch.object(
                education_certificate, "call_codex", return_value='{"code":"12345678"}'
            ) as call,
            patch.object(
                education_certificate, "_post_with_recognition_budget"
            ) as http,
        ):
            assert education_certificate._invoke_model(
                CONFIG, "", MESSAGES, learn_vision=False
            ) == {"code": "12345678"}
            assert call.call_args.kwargs["stop_event"] is event
            assert 0 < call.call_args.kwargs["timeout"] <= 20
            http.assert_not_called()
    finally:
        education_certificate._recognition_budget.reset(token)


def test_subscription_dns_bypass_and_cached_catalog_do_not_probe_http():
    lookup = Mock(side_effect=AssertionError("no local DNS"))
    result = probe_api_connectivity(
        CONFIG,
        "",
        dns_lookup=lookup,
        probe=lambda *_args, **_kw: {"status": "compatible"},
    )
    assert result.successful
    lookup.assert_not_called()
    with patch.object(
        codex.ADAPTER,
        "status",
        return_value={
            "compatible": True,
            "chatgpt_logged_in": True,
            "cached_model_suggestions": ["gpt-6.1-sol"],
        },
    ):
        catalog = model_catalog.fetch_model_catalog(
            "codex", "", "codex://local", request_get=lookup
        )
    assert catalog.payload == {"data": [{"id": "gpt-6.1-sol"}]}
    lookup.assert_not_called()


def test_nontransient_errors_do_not_retry_and_absolute_deadline_bounds_backoff():
    adapter = codex.CodexAdapter()
    cfg = codex.CodexInvocation(CONFIG["model"], "connection_test", deadline=0)
    with patch.object(
        adapter,
        "_invoke_once",
        side_effect=codex.CodexError("CODEX_QUOTA_EXCEEDED", "quota"),
    ) as invoke:
        try:
            adapter.invoke(cfg, MESSAGES)
            assert False
        except codex.CodexError as exc:
            assert exc.code == "CODEX_QUOTA_EXCEEDED"
        assert invoke.call_count == 1
    try:
        adapter._wait_before_retry(1, cfg)
        assert False
    except codex.CodexError as exc:
        assert exc.code == "CODEX_TIMEOUT"


def test_login_refresh_updates_models_without_changing_selected_model():
    from gui_codex_support import CodexSettingsPanel

    panel = CodexSettingsPanel.__new__(CodexSettingsPanel)
    panel.generation = 0
    panel.frame = Mock()
    panel.frame.winfo_exists.return_value = True
    panel.host = Mock()
    panel.host.colors = {"success": "green", "warning": "orange"}
    panel.host.api_model_var.get.return_value = "existing-model"
    panel.host.run_on_ui.side_effect = lambda callback: callback()
    panel.status = Mock()
    panel.model_combo = Mock()
    panel.account_menu = Mock()
    panel.status_reader = Mock(
        return_value={
            "installed": True,
            "compatible": True,
            "chatgpt_logged_in": True,
            "login_in_progress": False,
            "cached_model_suggestions": ["new-model"],
        }
    )
    with (
        patch(
            "gui_codex_support.threading.Thread",
            side_effect=lambda target, daemon: Mock(start=target),
        ),
        patch("gui_codex_support.ttk.Style"),
    ):
        panel.refresh()
        assert panel.generation == 1
        panel.model_combo.configure.assert_called_with(
            values=["existing-model", "new-model"]
        )
        panel.status.set.assert_called_with("已登录 ChatGPT")
        panel.status_reader.return_value["cached_model_suggestions"] = ["latest-model"]
        panel.refresh()
        assert panel.generation == 2
        panel.model_combo.configure.assert_called_with(
            values=["existing-model", "latest-model"]
        )
    panel.host.api_model_var.set.assert_not_called()
