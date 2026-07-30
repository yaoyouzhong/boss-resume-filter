"""Validated persistence for the multi-job configuration snapshot."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from safe_json_store import load_json_snapshot, save_json_snapshot


def validate_job_config(payload: Any) -> None:
    """Reject malformed roots before they become the authoritative config."""
    if not isinstance(payload, dict):
        raise ValueError("岗位配置根节点必须是对象")
    for field in ("job_requirements", "jobs"):
        if field in payload and not isinstance(payload[field], dict):
            raise ValueError(f"{field} 必须是岗位名称到规则的对象")
        rules = payload.get(field, {})
        if any(not isinstance(name, str) or not isinstance(rule, dict)
               for name, rule in rules.items()):
            raise ValueError(f"{field} 中的岗位名称和规则格式无效")


def load_job_config_snapshot(
    path: str | Path,
    backup_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load the primary config or atomically restore its validated backup."""
    payload = load_json_snapshot(
        path,
        validate_job_config,
        backup_path=backup_path,
        default={},
    )
    return dict(payload or {})


def save_job_config_snapshot(
    payload: dict[str, Any],
    path: str | Path,
    backup_path: str | Path | None = None,
) -> None:
    """Persist one job-config transaction while retaining the last good snapshot."""
    save_json_snapshot(
        payload,
        path,
        validate_job_config,
        backup_path=backup_path,
        indent=4,
    )
