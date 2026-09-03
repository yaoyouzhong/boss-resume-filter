"""Runtime configuration paths and defaults for the standalone education tool."""
from __future__ import annotations

import os
import sys
from pathlib import Path


EDUCATION_TOOL_APP_DIR = "EducationCertificateTool"
EDUCATION_TOOL_SERVICE_NAME = "education-certificate-tool"

EDUCATION_TOOL_API_CONFIG = {
    "api_provider": "qwen",
    "api_key": "",
    "base_url": (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    ),
    "model": "kimi-k2.6",
    "saved_models": [
        {
            "api_provider": "qwen",
            "base_url": (
                "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
            ),
            "model": "kimi-k2.6",
        }
    ],
    "providers": {},
    "fetched_models": {},
    "llm_read_timeout": 120,
}


def get_education_tool_data_dir() -> Path:
    """Return a writable standalone data directory without touching BOSS data."""
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return root / EDUCATION_TOOL_APP_DIR
    return Path(__file__).resolve().parent


def get_education_tool_config_path(*, for_write: bool = False) -> Path:
    """Return the standalone model metadata path; API keys never use this file."""
    data_dir = get_education_tool_data_dir()
    if getattr(sys, "frozen", False):
        path = data_dir / "config.json"
    else:
        path = data_dir / "education_tool_config.local.json"
    if for_write:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_education_tool_preferences_path() -> Path:
    """Return the standalone UI-preferences path."""
    data_dir = get_education_tool_data_dir()
    path = (
        data_dir / "preferences.json"
        if getattr(sys, "frozen", False)
        else data_dir / ".education_tool_preferences.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
