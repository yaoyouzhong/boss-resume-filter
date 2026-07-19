"""
API Key 安全存储模块

使用操作系统级加密存储：
- Windows: DPAPI (Data Protection API)
- macOS: Keychain
- Linux: Secret Service / KWallet

API Key 按服务商 + Base URL 组合存储，同一服务商不同接入方式（API / Token Plan）独立管理。
"""
from __future__ import annotations

import hashlib
import logging
import sys
from typing import Any

SERVICE_NAME = "boss-resume-filter"
logger = logging.getLogger(__name__)


def _get_keyring_module() -> Any:
    """延迟加载 keyring，避免 Windows 首次枚举后端阻塞 GUI。"""
    import keyring

    return keyring


def _get_windows_credential_modules() -> tuple[Any, Any]:
    """加载 keyring 在 Windows 正式环境中使用的凭据管理器接口。"""
    from win32ctypes.pywin32 import pywintypes, win32cred

    return win32cred, pywintypes


def _decode_windows_credential(credential: dict[str, Any]) -> str:
    """按 python-keyring 的编码规则解码 Windows 凭据。"""
    blob = credential.get("CredentialBlob", b"")
    if isinstance(blob, str):
        return blob
    if isinstance(blob, bytes):
        try:
            return blob.decode("utf-16")
        except UnicodeDecodeError:
            return blob.decode("utf-8")
    return str(blob)


def _read_windows_credential(target: str) -> dict[str, Any] | None:
    """读取一个 Windows 通用凭据；不存在时返回 None。"""
    win32cred, pywintypes = _get_windows_credential_modules()
    try:
        return win32cred.CredRead(
            Type=win32cred.CRED_TYPE_GENERIC,
            TargetName=target,
        )
    except pywintypes.error as exc:
        if getattr(exc, "winerror", None) == 1168:
            return None
        raise


def _write_windows_credential(target: str, username: str, password: str) -> None:
    """以 python-keyring 兼容格式写入 Windows 通用凭据。"""
    win32cred, _ = _get_windows_credential_modules()
    win32cred.CredWrite(
        {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": target,
            "UserName": username,
            "CredentialBlob": str(password),
            "Comment": "Stored using python-keyring",
            "Persist": win32cred.CRED_PERSIST_ENTERPRISE,
        },
        0,
    )


def _delete_windows_credential(target: str) -> None:
    """删除一个 Windows 通用凭据，兼容并发删除导致的不存在。"""
    win32cred, pywintypes = _get_windows_credential_modules()
    try:
        win32cred.CredDelete(
            Type=win32cred.CRED_TYPE_GENERIC,
            TargetName=target,
        )
    except pywintypes.error as exc:
        if getattr(exc, "winerror", None) == 1168:
            return
        raise


def _windows_get_password(service: str, username: str) -> str | None:
    """从 Windows 凭据管理器读取 keyring 兼容记录。"""
    credential = _read_windows_credential(service)
    if not credential or credential.get("UserName") != username:
        credential = _read_windows_credential(f"{username}@{service}")
    return _decode_windows_credential(credential) if credential else None


def _windows_set_password(service: str, username: str, password: str) -> None:
    """写入 Windows 凭据管理器并保留 keyring 的同服务多账户行为。"""
    existing = _read_windows_credential(service)
    if existing:
        existing_username = str(existing.get("UserName", ""))
        _write_windows_credential(
            f"{existing_username}@{service}",
            existing_username,
            _decode_windows_credential(existing),
        )
    _write_windows_credential(service, username, password)


def _windows_delete_password(service: str, username: str) -> None:
    """删除 Windows 凭据管理器中的主记录和复合名称记录。"""
    deleted = False
    for target in (service, f"{username}@{service}"):
        existing = _read_windows_credential(target)
        if existing and existing.get("UserName") == username:
            _delete_windows_credential(target)
            deleted = True
    if not deleted:
        raise LookupError(f"Credential not found: {service}/{username}")


def _credential_get_password(service: str, username: str) -> str | None:
    if sys.platform == "win32":
        return _windows_get_password(service, username)
    return _get_keyring_module().get_password(service, username)


def _credential_set_password(service: str, username: str, password: str) -> None:
    if sys.platform == "win32":
        _windows_set_password(service, username, password)
        return
    _get_keyring_module().set_password(service, username, password)


def _credential_delete_password(service: str, username: str) -> None:
    if sys.platform == "win32":
        _windows_delete_password(service, username)
        return
    _get_keyring_module().delete_password(service, username)


def get_storage_key(provider: str, base_url: str | None = None) -> str:
    """
    生成用于 keyring 存储的键名（按服务商 + Base URL 组合）

    Args:
        provider: 服务商名称（如 "qwen", "deepseek"）
        base_url: API Base URL（可选，用于区分同一服务商的不同接入方式）

    Returns:
        存储键名
    """
    if base_url:
        # 用 base_url 的短 hash 区分不同接入方式；strip 尾部斜杠防止同一 URL 两种写法产生不同 key
        normalized = base_url.rstrip('/')
        url_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]
        return f"api_key:{provider}:{url_hash}"
    return f"api_key:{provider}"


def save_api_key(provider: str, api_key: str, base_url: str | None = None) -> bool:
    """
    加密保存 API Key 到系统钥匙串

    Args:
        provider: 服务商名称（如 "qwen", "deepseek"）
        api_key: 要存储的 API Key
        base_url: API Base URL（可选，用于区分同一服务商的不同接入方式）

    Returns:
        是否成功
    """
    try:
        key = get_storage_key(provider, base_url)
        _credential_set_password(SERVICE_NAME, key, api_key)
        return True
    except Exception as e:
        logger.warning("保存 API Key 失败：%s", e)
        return False


def get_api_key(provider: str, base_url: str | None = None) -> str | None:
    """
    从系统钥匙串解密读取 API Key

    Args:
        provider: 服务商名称
        base_url: API Base URL（可选，用于区分同一服务商的不同接入方式）

    Returns:
        API Key，如果不存在或读取失败则返回 None
    """
    try:
        # 优先用新格式（带 base_url）查找
        if base_url:
            key = get_storage_key(provider, base_url)
            result = _credential_get_password(SERVICE_NAME, key)
            if result:
                return result
        # 回退到旧格式（仅 provider）向后兼容
        key = get_storage_key(provider)
        return _credential_get_password(SERVICE_NAME, key)
    except Exception as e:
        logger.warning("读取 API Key 失败：%s", e)
        return None


def delete_api_key(provider: str, base_url: str | None = None) -> bool:
    """
    从系统钥匙串删除 API Key（同时清理新旧两种格式，防止残留）

    Args:
        provider: 服务商名称
        base_url: API Base URL（可选，用于区分同一服务商的不同接入方式）

    Returns:
        是否成功
    """
    try:
        key = get_storage_key(provider, base_url)
        _credential_delete_password(SERVICE_NAME, key)
    except Exception as e:
        logger.warning("删除 API Key 失败：%s", e)
        return False
    # 同时清理另一种格式，防止孤儿 key 残留
    try:
        alt_key = get_storage_key(provider, None) if base_url else None
        if alt_key and alt_key != key:
            try:
                _credential_delete_password(SERVICE_NAME, alt_key)
            except Exception:
                pass  # 旧格式不存在，忽略
    except Exception:
        pass
    return True


def list_all_providers() -> list[str]:
    """
    列出所有已配置 API Key 的服务商

    Returns:
        服务商列表
    """
    # 各系统后端都没有统一、可靠的枚举接口。
    return []
