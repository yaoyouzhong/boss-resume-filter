"""Standalone education-certificate tool tests."""
from __future__ import annotations

import json
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import Mock, patch

import build_education_tool
from education_tool_config import (
    EDUCATION_TOOL_API_CONFIG,
    EDUCATION_TOOL_SERVICE_NAME,
    get_education_tool_config_path,
)
from education_tool import _assert_page_fills_viewport
from education_tool_security import (
    get_education_api_key,
    save_education_api_key,
)
from gui_main import BossFilterGUI
from security import get_storage_key


def test_default_config_has_no_credential_and_keeps_supported_vision_model():
    assert EDUCATION_TOOL_API_CONFIG["api_key"] == ""
    assert EDUCATION_TOOL_API_CONFIG["api_provider"] == "qwen"
    assert EDUCATION_TOOL_API_CONFIG["base_url"] == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert EDUCATION_TOOL_API_CONFIG["model"] == "kimi-k2.6"


def test_frozen_config_uses_local_app_data():
    with (
        patch.object(sys, "frozen", True, create=True),
        patch.dict("os.environ", {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"}),
    ):
        path = get_education_tool_config_path()
    assert path == Path(
        r"C:\Users\tester\AppData\Local\EducationCertificateTool\config.json"
    )


def test_standalone_credential_uses_separate_service_and_endpoint_identity():
    base_url = "https://example.test/v1"
    with patch(
        "education_tool_security._credential_get_password",
        return_value="secret-key",
    ) as credential_get:
        assert get_education_api_key("custom", base_url) == "secret-key"
    credential_get.assert_called_once_with(
        EDUCATION_TOOL_SERVICE_NAME,
        get_storage_key("custom", base_url),
    )


def test_standalone_credential_save_never_writes_a_plaintext_config_file():
    with patch("education_tool_security._credential_set_password") as credential_set:
        assert save_education_api_key("openai", "secret-key", "https://api.openai.com/v1")
    credential_set.assert_called_once_with(
        EDUCATION_TOOL_SERVICE_NAME,
        get_storage_key("openai", "https://api.openai.com/v1"),
        "secret-key",
    )


def test_standalone_config_loader_drops_plaintext_keys_defensively():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.standalone_education = True
    gui._standalone_api_config_defaults = EDUCATION_TOOL_API_CONFIG
    gui._api_key_cache = {}
    gui._api_key_cache_lock = None
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "config.json"
        path.write_text(
            json.dumps(
                {
                    "api_provider": "custom",
                    "base_url": "https://example.test/v1",
                    "model": "vision-model",
                    "api_key": "must-not-survive",
                    "saved_models": [
                        {
                            "api_provider": "custom",
                            "base_url": "https://example.test/v1",
                            "model": "vision-model",
                            "api_key": "must-not-survive",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        gui._api_config_path = path
        gui.load_api_config(resolve_keys=False)

    assert gui.api_config["api_key"] == ""
    assert "api_key" not in gui.api_config["saved_models"][0]


def test_boss_and_standalone_modes_use_separate_key_getters():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._api_key_cache = {}
    gui._api_key_cache_lock = None
    gui._api_key_getter = lambda provider, base_url: f"standalone:{provider}:{base_url}"
    assert gui._get_education_api_key(EDUCATION_TOOL_API_CONFIG).startswith(
        "standalone:qwen:"
    )

    gui._api_key_cache = {}
    gui._api_key_getter = None
    with patch("gui_main.get_api_key", return_value="boss-key"):
        assert gui._get_education_api_key(EDUCATION_TOOL_API_CONFIG) == "boss-key"


def test_standalone_recognition_without_key_opens_model_settings():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.standalone_education = True
    gui.root = object()
    gui.education_items = {
        "education_1": {
            "path": "certificate.pdf",
            "is_pdf": True,
            "status": "待识别",
        }
    }
    gui.education_current_id = None
    gui.education_recognition_running = False
    gui.education_screenshot_running = False
    gui._save_current_education_fields = Mock()
    gui._get_education_api_config = Mock(return_value=EDUCATION_TOOL_API_CONFIG)
    gui._get_education_api_key = Mock(return_value="")
    gui.show_page_api = Mock()

    with patch("gui_main.messagebox.show_notice") as notice:
        gui._recognize_education_image()

    notice.assert_called_once()
    gui.show_page_api.assert_called_once_with()
    assert gui.education_recognition_running is False


def test_standalone_browser_uses_auto_port_instead_of_fixed_profile():
    calls = []

    class FakeOptions:
        def __init__(self, read_file=True):
            calls.append(("init", read_file))

        def auto_port(self):
            calls.append(("auto_port",))

        def set_argument(self, name, value=None):
            calls.append(("argument", name, value))

    class FakePage:
        def __init__(self, options):
            calls.append(("page", options))

        def run_js(self, _script):
            return 1

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.standalone_education = True
    gui.root = Mock()
    gui.root.winfo_screenwidth.return_value = 1920
    gui.root.winfo_screenheight.return_value = 1080
    fake_module = types.SimpleNamespace(
        ChromiumOptions=FakeOptions,
        ChromiumPage=FakePage,
    )
    with patch.dict("sys.modules", {"DrissionPage": fake_module}):
        page = gui._create_fresh_browser_page()

    assert isinstance(page, FakePage)
    assert calls[0] == ("init", False)
    assert calls[1] == ("auto_port",)
    assert calls[2] == ("argument", "--window-size", "1360,900")


def test_standalone_build_contains_no_embedded_secret_pipeline():
    source = Path("build_education_tool.py").read_text(encoding="utf-8")
    assert "EDUCATION_TOOL_API_KEY" not in source
    assert "education_tool_secret.json" not in source
    assert "--add-data" not in source
    assert '"win32ctypes.pywin32.win32cred"' in source
    assert 'PACK_ENV_DIR = BASE_DIR / "pack_venv"' in source
    assert "run_in_venv(__file__)" in source
    assert "_check_pack_environment()" in source
    assert '"EDUCATION_TOOL_TK_FALLBACK"' in source
    assert '";_tcl_data"' in source
    assert '";_tk_data"' in source
    assert '"win32ctypes.core.ctypes"' in source
    assert '"win32ctypes.core.cffi"' in source
    assert '"--debug-console"' in source
    assert '"--ci"' in source
    assert 'os.environ.get("GITHUB_ACTIONS") != "true"' in source
    assert 'os.environ.get("RUNNER_OS") != "Windows"' in source
    assert '[str(artifact_path), "--smoke-test"]' in source
    assert Path(
        "pyinstaller-hooks/pre_find_module_path/hook-tkinter.py"
    ).is_file()
    assert '"openpyxl"' not in source


def test_standalone_ci_build_mode_is_limited_to_windows_github_actions():
    with (
        patch.dict(
            build_education_tool.os.environ,
            {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows"},
            clear=True,
        ),
        patch.object(build_education_tool.os, "name", "nt"),
        patch.object(build_education_tool.sys, "executable", r"C:\hosted\python.exe"),
    ):
        assert build_education_tool._resolve_build_python(True) == Path(
            r"C:\hosted\python.exe"
        )

    with patch.dict(build_education_tool.os.environ, {}, clear=True):
        try:
            build_education_tool._resolve_build_python(True)
        except RuntimeError as error:
            assert "GitHub Actions" in str(error)
        else:
            raise AssertionError("local --ci build must be rejected")


def test_standalone_entry_injects_config_and_credential_backends():
    source = Path("education_tool.py").read_text(encoding="utf-8")
    assert "education_api_config_path=config_path" in source
    assert "education_api_key_getter=get_education_api_key" in source
    assert "education_api_key_saver=save_education_api_key" in source
    assert "start_with_settings=not config_path.is_file()" in source
    assert '_smoke_test = "--smoke-test" in sys.argv[1:]' in source
    assert "root.tk.dooneevent" in source
    assert 'root.after_cancel(ui_queue_after_id)' in source
    assert "packaged smoke test failed" in source


def test_standalone_smoke_test_rejects_a_collapsed_first_page():
    gui = types.SimpleNamespace(
        pages_frame=types.SimpleNamespace(
            winfo_width=lambda: 1600,
            winfo_height=lambda: 900,
        )
    )
    page = types.SimpleNamespace(
        winfo_width=lambda: 1,
        winfo_height=lambda: 1,
    )

    try:
        _assert_page_fills_viewport(gui, page)
    except RuntimeError as error:
        assert "page=1x1" in str(error)
    else:
        raise AssertionError("collapsed standalone page must fail the smoke test")
