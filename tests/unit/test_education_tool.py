"""独立学历证书核验助手测试。"""
import base64
import hashlib
import hmac
import json
import secrets
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

from education_tool_config import EDUCATION_TOOL_API_CONFIG
from education_tool_security import (
    _keystream,
    _wrap_key,
    decrypt_embedded_secret,
    get_embedded_api_key,
)
from gui_main import BossFilterGUI


def test_fixed_api_config_uses_supported_vision_model():
    assert EDUCATION_TOOL_API_CONFIG == {
        "api_provider": "qwen",
        "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "model": "kimi-k2.6",
    }


def _encrypted_payload(plaintext: str) -> dict[str, str]:
    raw = plaintext.encode("utf-8")
    nonce = secrets.token_bytes(16)
    data_key = secrets.token_bytes(32)
    ciphertext = bytes(
        a ^ b for a, b in zip(raw, _keystream(data_key, nonce, len(raw)))
    )
    wrapped_key = bytes(
        a ^ b for a, b in zip(data_key, _keystream(_wrap_key(), nonce, len(data_key)))
    )
    return {
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "wrapped_key": base64.b64encode(wrapped_key).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "tag": base64.b64encode(
            hmac.new(data_key, nonce + ciphertext, hashlib.sha256).digest()
        ).decode("ascii"),
    }


def test_embedded_api_key_can_be_decrypted_without_plaintext_file():
    plaintext = "test-api-key"
    with tempfile.TemporaryDirectory() as temp_dir:
        secret_path = Path(temp_dir) / "education_tool_secret.json"
        secret_path.write_text(
            json.dumps(_encrypted_payload(plaintext)),
            encoding="utf-8",
        )
        assert plaintext not in secret_path.read_text(encoding="utf-8")
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("education_tool_security._resource_path", return_value=secret_path),
        ):
            assert get_embedded_api_key() == plaintext


def test_embedded_api_key_rejects_tampering():
    payload = _encrypted_payload("test-api-key")
    payload["ciphertext"] = base64.b64encode(b"broken").decode("ascii")
    try:
        decrypt_embedded_secret(payload)
    except RuntimeError as error:
        assert "完整性" in str(error)
    else:
        raise AssertionError("tampered secret should fail")


def test_boss_mode_and_standalone_mode_use_separate_key_sources():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._education_api_key_provider = lambda: "embedded-key"
    assert gui._get_education_api_key(EDUCATION_TOOL_API_CONFIG) == "embedded-key"

    gui._education_api_key_provider = None
    with patch("gui_main.get_api_key", return_value="keyring-key"):
        assert gui._get_education_api_key(EDUCATION_TOOL_API_CONFIG) == "keyring-key"


def test_standalone_browser_uses_auto_port_instead_of_fixed_profile():
    calls = []

    class FakeOptions:
        def __init__(self, read_file=True):
            calls.append(("init", read_file))

        def auto_port(self):
            calls.append(("auto_port",))

    class FakePage:
        def __init__(self, options):
            calls.append(("page", options))

        def run_js(self, _script):
            return 1

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.standalone_education = True
    fake_module = types.SimpleNamespace(
        ChromiumOptions=FakeOptions,
        ChromiumPage=FakePage,
    )
    with patch.dict("sys.modules", {"DrissionPage": fake_module}):
        page = gui._create_fresh_browser_page()

    assert isinstance(page, FakePage)
    assert calls[0] == ("init", False)
    assert calls[1] == ("auto_port",)


def test_standalone_build_keeps_drissionpage_openpyxl_dependency():
    source = Path("build_education_tool.py").read_text(encoding="utf-8")
    assert '"openpyxl"' not in source
