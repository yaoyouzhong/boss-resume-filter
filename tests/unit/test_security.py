# -*- coding: utf-8 -*-
"""Unit tests for security.py — API Key 安全存储模块"""
import sys
from unittest.mock import patch
import security


def test_get_storage_key_format_without_base_url():
    """无 base_url 时格式正确（向后兼容）"""
    assert security.get_storage_key("qwen") == "api_key:qwen"
    assert security.get_storage_key("deepseek") == "api_key:deepseek"
    assert security.get_storage_key("openai") == "api_key:openai"


def test_get_storage_key_format_with_base_url():
    """有 base_url 时格式包含 hash"""
    key1 = security.get_storage_key("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    key2 = security.get_storage_key("qwen", "https://token-plan.example.com/v1")
    # 同一服务商不同 base_url 应生成不同 key
    assert key1 != key2
    assert key1.startswith("api_key:qwen:")
    assert key2.startswith("api_key:qwen:")
    # 同一 provider + base_url 应生成相同 key
    key1_again = security.get_storage_key("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert key1 == key1_again


def test_service_name_is_set():
    """SERVICE_NAME 常量非空"""
    assert security.SERVICE_NAME == "boss-resume-filter"


@patch("security._credential_set_password")
def test_save_api_key_success(mock_set_password):
    """正常保存返回 True（无 base_url）"""
    result = security.save_api_key("qwen", "sk-test-123")
    assert result is True
    mock_set_password.assert_called_once_with(
        "boss-resume-filter", "api_key:qwen", "sk-test-123"
    )


@patch("security._credential_set_password")
def test_save_api_key_with_base_url(mock_set_password):
    """带 base_url 保存"""
    result = security.save_api_key("qwen", "sk-test-123", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert result is True
    # key 应包含 hash
    call_args = mock_set_password.call_args[0]
    assert call_args[1].startswith("api_key:qwen:")
    assert call_args[1] != "api_key:qwen"


@patch("security._credential_set_password")
@patch("security.logger.warning")
def test_save_api_key_failure(mock_warning, mock_set_password):
    """keyring 异常时返回 False"""
    mock_set_password.side_effect = Exception("keyring error")
    result = security.save_api_key("qwen", "sk-test")
    assert result is False
    mock_warning.assert_called_once()
    assert mock_warning.call_args[0][0] == "保存 API Key 失败：%s"
    assert str(mock_warning.call_args[0][1]) == "keyring error"


@patch("security._credential_get_password")
def test_get_api_key_found(mock_get_password):
    """找到 Key 时返回值（无 base_url）"""
    mock_get_password.return_value = "sk-found"
    result = security.get_api_key("qwen")
    assert result == "sk-found"
    # 无 base_url 时只查旧格式
    mock_get_password.assert_called_once_with(
        "boss-resume-filter", "api_key:qwen"
    )


@patch("security._credential_get_password")
def test_get_api_key_with_base_url_found(mock_get_password):
    """带 base_url 找到 Key"""
    # 第一次调用（新格式）返回 key，第二次不会调用
    def side_effect(service, key):
        if key.startswith("api_key:qwen:"):
            return "sk-found-new"
        return None
    mock_get_password.side_effect = side_effect
    result = security.get_api_key("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert result == "sk-found-new"


@patch("security._credential_get_password")
def test_get_api_key_with_base_url_fallback(mock_get_password):
    """带 base_url 但新格式未找到时回退到旧格式"""
    def side_effect(service, key):
        if key.startswith("api_key:qwen:"):
            return None  # 新格式没找到
        if key == "api_key:qwen":
            return "sk-found-old"  # 旧格式找到了
        return None
    mock_get_password.side_effect = side_effect
    result = security.get_api_key("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert result == "sk-found-old"


@patch("security._credential_get_password")
def test_get_api_key_not_found(mock_get_password):
    """未找到 Key 时返回 None"""
    mock_get_password.return_value = None
    result = security.get_api_key("unknown_provider")
    assert result is None


@patch("security._credential_get_password")
@patch("security.logger.warning")
def test_get_api_key_exception(mock_warning, mock_get_password):
    """keyring 异常时返回 None"""
    mock_get_password.side_effect = Exception("keyring error")
    result = security.get_api_key("qwen")
    assert result is None
    mock_warning.assert_called_once()
    assert mock_warning.call_args[0][0] == "读取 API Key 失败：%s"
    assert str(mock_warning.call_args[0][1]) == "keyring error"


@patch("security._credential_delete_password")
def test_delete_api_key_success(mock_delete_password):
    """正常删除返回 True"""
    result = security.delete_api_key("qwen")
    assert result is True
    mock_delete_password.assert_called_once_with(
        "boss-resume-filter", "api_key:qwen"
    )


@patch("security._credential_delete_password")
def test_delete_api_key_with_base_url(mock_delete_password):
    """带 base_url 删除，同时清理新格式和旧格式 key"""
    result = security.delete_api_key("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert result is True
    # 第一次调用：删除新格式（带 hash）
    first_call = mock_delete_password.call_args_list[0]
    assert first_call[0][1].startswith("api_key:qwen:")
    # 第二次调用：清理旧格式（仅 provider）
    assert mock_delete_password.call_count == 2
    second_call = mock_delete_password.call_args_list[1]
    assert second_call[0][1] == "api_key:qwen"


@patch("security._credential_delete_password")
@patch("security.logger.warning")
def test_delete_api_key_failure(mock_warning, mock_delete_password):
    """keyring 异常时返回 False"""
    mock_delete_password.side_effect = Exception("delete error")
    result = security.delete_api_key("qwen")
    assert result is False
    mock_warning.assert_called_once()
    assert mock_warning.call_args[0][0] == "删除 API Key 失败：%s"
    assert str(mock_warning.call_args[0][1]) == "delete error"


def test_list_all_providers_returns_list():
    """list_all_providers 返回列表（Windows 上为空列表）"""
    result = security.list_all_providers()
    assert isinstance(result, list)


def test_decode_windows_credential_uses_keyring_compatible_encoding():
    """Windows 凭据应按 keyring 的 UTF-16 规则解码。"""
    credential = {"CredentialBlob": "sk-凭据".encode("utf-16")}
    assert security._decode_windows_credential(credential) == "sk-凭据"


def test_windows_credential_modules_use_declared_pywin32_ctypes_backend():
    """Windows 正式环境必须能加载项目声明的凭据后端。"""
    if sys.platform != "win32":
        return

    win32cred, pywintypes = security._get_windows_credential_modules()

    assert win32cred.__name__.startswith("win32ctypes.pywin32")
    assert pywintypes.__name__.startswith("win32ctypes.pywin32")
    assert hasattr(win32cred, "CredRead")
    assert hasattr(pywintypes, "error")


@patch("security._read_windows_credential")
def test_windows_get_password_uses_compound_target_on_username_mismatch(mock_read):
    """主记录属于其他账户时，应读取 keyring 的复合名称记录。"""
    mock_read.side_effect = [
        {"UserName": "api_key:other", "CredentialBlob": "old".encode("utf-16")},
        {"UserName": "api_key:qwen", "CredentialBlob": "wanted".encode("utf-16")},
    ]

    result = security._windows_get_password("boss-resume-filter", "api_key:qwen")

    assert result == "wanted"
    assert mock_read.call_args_list[1].args[0] == "api_key:qwen@boss-resume-filter"


@patch("security._write_windows_credential")
@patch("security._read_windows_credential")
def test_windows_set_password_preserves_existing_service_record(mock_read, mock_write):
    """写入新账户前，应按 keyring 规则迁移同服务下的旧账户。"""
    mock_read.return_value = {
        "UserName": "api_key:old",
        "CredentialBlob": "old-secret".encode("utf-16"),
    }

    security._windows_set_password("boss-resume-filter", "api_key:qwen", "new-secret")

    assert mock_write.call_args_list[0].args == (
        "api_key:old@boss-resume-filter",
        "api_key:old",
        "old-secret",
    )
    assert mock_write.call_args_list[1].args == (
        "boss-resume-filter",
        "api_key:qwen",
        "new-secret",
    )


@patch("security._delete_windows_credential")
@patch("security._read_windows_credential")
def test_windows_delete_password_removes_primary_and_compound_records(mock_read, mock_delete):
    """删除时应清理 keyring 兼容的两种目标名称。"""
    credential = {"UserName": "api_key:qwen", "CredentialBlob": b""}
    mock_read.side_effect = [credential, credential]

    security._windows_delete_password("boss-resume-filter", "api_key:qwen")

    assert [call.args[0] for call in mock_delete.call_args_list] == [
        "boss-resume-filter",
        "api_key:qwen@boss-resume-filter",
    ]


@patch("security._windows_get_password", return_value="sk-native")
@patch("security.sys.platform", "win32")
def test_credential_get_password_uses_native_windows_backend(mock_windows_get):
    """Windows 路径不应触发 keyring 后端枚举。"""
    result = security._credential_get_password("boss-resume-filter", "api_key:qwen")

    assert result == "sk-native"
    mock_windows_get.assert_called_once_with("boss-resume-filter", "api_key:qwen")


@patch("security._get_keyring_module")
@patch("security.sys.platform", "darwin")
def test_credential_get_password_keeps_keyring_fallback(mock_get_keyring):
    """非 Windows 平台继续使用系统 keyring。"""
    mock_get_keyring.return_value.get_password.return_value = "sk-keychain"

    result = security._credential_get_password("boss-resume-filter", "api_key:qwen")

    assert result == "sk-keychain"
    mock_get_keyring.return_value.get_password.assert_called_once_with(
        "boss-resume-filter", "api_key:qwen"
    )
