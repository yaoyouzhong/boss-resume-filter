"""OS credential storage for the standalone education tool."""
from __future__ import annotations

import logging

from education_tool_config import EDUCATION_TOOL_SERVICE_NAME
from security import (
    _credential_get_password,
    _credential_set_password,
    get_storage_key,
)


logger = logging.getLogger(__name__)


def get_education_api_key(provider: str, base_url: str | None = None) -> str | None:
    """Read one provider/endpoint key from the current user's secure store."""
    if not provider:
        return None
    try:
        if base_url:
            value = _credential_get_password(
                EDUCATION_TOOL_SERVICE_NAME,
                get_storage_key(provider, base_url),
            )
            if value:
                return value
        return _credential_get_password(
            EDUCATION_TOOL_SERVICE_NAME,
            get_storage_key(provider),
        )
    except Exception as exc:
        logger.warning("读取学历核验工具 API Key 失败：%s", exc)
        return None


def save_education_api_key(
    provider: str,
    api_key: str,
    base_url: str | None = None,
) -> bool:
    """Store one key in Windows Credential Manager or the platform keychain."""
    if not provider or not api_key:
        return False
    try:
        _credential_set_password(
            EDUCATION_TOOL_SERVICE_NAME,
            get_storage_key(provider, base_url),
            api_key,
        )
        return True
    except Exception as exc:
        logger.warning("保存学历核验工具 API Key 失败：%s", exc)
        return False
