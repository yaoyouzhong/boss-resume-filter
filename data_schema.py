"""Versioned identities and pure migrations for persisted user data."""
from __future__ import annotations

import copy
import uuid
from typing import Any

from job_identity import normalize_job_name


JOB_CONFIG_SCHEMA_VERSION = 2
CANDIDATE_SCHEMA_VERSION = 2
CONTACT_QUEUE_SCHEMA_VERSION = 2
_JOB_UUID_NAMESPACE = uuid.UUID("86dd34fa-b03d-5df8-aef1-c9c925aa6a83")


def normalize_job_uuid(value: Any) -> str:
    """Return a canonical UUID string or an empty string for a missing value."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError) as exc:
        raise ValueError("岗位稳定 ID 格式无效") from exc


def legacy_job_uuid(job_name: Any) -> str:
    """Create an idempotent UUID for one unversioned configured job."""
    normalized = normalize_job_name(job_name).casefold()
    if not normalized or normalized == "default":
        return ""
    return str(uuid.uuid5(_JOB_UUID_NAMESPACE, normalized))


def new_job_uuid() -> str:
    """Create a stable ID for a newly configured job."""
    return str(uuid.uuid4())


def job_identity_token(job_uuid: Any, job_name: Any) -> str:
    """Prefer a stable job UUID while retaining a legacy name fallback."""
    stable_id = normalize_job_uuid(job_uuid)
    if stable_id:
        return f"uuid:{stable_id}"
    return normalize_job_name(job_name)


def upgrade_job_config(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Upgrade one config in memory without mutating the caller's object."""
    if not isinstance(payload, dict):
        raise ValueError("岗位配置根节点必须是对象")
    version = payload.get("schema_version", 1)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError("岗位配置 Schema 版本无效")
    if version > JOB_CONFIG_SCHEMA_VERSION:
        raise ValueError("岗位配置来自更高版本，当前程序无法安全读取")

    upgraded = copy.deepcopy(payload)
    seen_names: dict[str, str] = {}
    seen_name_ids: dict[str, str] = {}
    seen_ids: dict[str, str] = {}
    for field in ("job_requirements", "jobs"):
        rules = upgraded.get(field)
        if rules is None:
            continue
        if not isinstance(rules, dict):
            raise ValueError(f"{field} 必须是岗位名称到规则的对象")
        for display_name, rule in rules.items():
            if not isinstance(display_name, str) or not isinstance(rule, dict):
                raise ValueError(f"{field} 中的岗位名称和规则格式无效")
            normalized_name = normalize_job_name(display_name).casefold()
            if not normalized_name or normalized_name == "default":
                continue
            previous_name = seen_names.get(normalized_name)
            if previous_name is not None and previous_name != display_name:
                raise ValueError(
                    f"岗位名称归一化后重复：{previous_name} / {display_name}"
                )
            seen_names[normalized_name] = display_name
            stable_id = normalize_job_uuid(rule.get("job_uuid"))
            if not stable_id:
                stable_id = legacy_job_uuid(display_name)
                rule["job_uuid"] = stable_id
            previous_id = seen_name_ids.get(normalized_name)
            if previous_id is not None and previous_id != stable_id:
                raise ValueError("同一岗位名称不能对应多个稳定 ID")
            seen_name_ids[normalized_name] = stable_id
            previous_owner = seen_ids.get(stable_id)
            if previous_owner is not None and previous_owner != normalized_name:
                raise ValueError("不同岗位不能共用同一个稳定 ID")
            seen_ids[stable_id] = normalized_name

    upgraded["schema_version"] = JOB_CONFIG_SCHEMA_VERSION
    return upgraded, upgraded != payload


def job_uuid_by_normalized_name(
    config: dict[str, Any],
) -> dict[str, str]:
    """Build an unambiguous legacy-name to stable-ID migration map."""
    upgraded, _ = upgrade_job_config(config)
    result: dict[str, str] = {}
    for field in ("job_requirements", "jobs"):
        rules = upgraded.get(field) or {}
        for display_name, rule in rules.items():
            normalized_name = normalize_job_name(display_name).casefold()
            stable_id = normalize_job_uuid(rule.get("job_uuid"))
            if normalized_name and normalized_name != "default" and stable_id:
                existing = result.get(normalized_name)
                if existing and existing != stable_id:
                    raise ValueError(
                        f"岗位名称无法唯一映射：{display_name}"
                    )
                result[normalized_name] = stable_id
    return result


def migrate_candidate_records(
    candidates: list[dict[str, Any]],
    job_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Attach stable job IDs where legacy names have one exact configured match."""
    if not isinstance(candidates, list) or any(
        not isinstance(item, dict) for item in candidates
    ):
        raise ValueError("候选人数据必须是对象列表")
    name_map = job_uuid_by_normalized_name(job_config)
    current_names: dict[str, str] = {}
    upgraded_config, _ = upgrade_job_config(job_config)
    for field in ("job_requirements", "jobs"):
        for display_name, rule in (upgraded_config.get(field) or {}).items():
            stable_id = normalize_job_uuid(rule.get("job_uuid"))
            if stable_id:
                current_names[stable_id] = display_name
    migrated: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for candidate in candidates:
        item = copy.deepcopy(candidate)
        version = item.get("schema_version", 1)
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("候选人 Schema 版本无效")
        if version > CANDIDATE_SCHEMA_VERSION:
            raise ValueError("候选人数据来自更高版本，当前程序无法安全读取")
        stable_id = normalize_job_uuid(item.get("job_uuid"))
        job_name = str(item.get("job_name") or "")
        if not stable_id and job_name:
            stable_id = name_map.get(normalize_job_name(job_name).casefold(), "")
            if stable_id:
                item["job_uuid"] = stable_id
            else:
                unresolved.append({
                    "geek_id": str(item.get("geek_id") or ""),
                    "job_name": job_name,
                })
        elif stable_id and stable_id not in current_names:
            unresolved.append({
                "geek_id": str(item.get("geek_id") or ""),
                "job_name": job_name,
            })
        if stable_id in current_names:
            item["job_name"] = current_names[stable_id]
        item["schema_version"] = CANDIDATE_SCHEMA_VERSION
        migrated.append(item)
    return migrated, unresolved


def validate_candidate_schema(candidate: dict[str, Any]) -> None:
    """Validate version fields without requiring legacy data to have a job ID."""
    version = candidate.get("schema_version", CANDIDATE_SCHEMA_VERSION)
    if version != CANDIDATE_SCHEMA_VERSION:
        raise ValueError("候选人 Schema 版本无效")
    normalize_job_uuid(candidate.get("job_uuid"))
