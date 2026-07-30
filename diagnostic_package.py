"""Create bounded, privacy-audited support packages from local runtime state."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contact_queue import validate_contact_queue_snapshot
from data_schema import (
    CANDIDATE_SCHEMA_VERSION,
    CONTACT_QUEUE_SCHEMA_VERSION,
    JOB_CONFIG_SCHEMA_VERSION,
    normalize_job_uuid,
    upgrade_job_config,
)
from job_config_store import validate_job_config
from paths import get_api_config_path
from storage import validate_candidates_snapshot


DIAGNOSTIC_FORMAT_VERSION = 1
MAX_LOG_FILES = 8
MAX_LOG_BYTES_PER_FILE = 512 * 1024
MAX_LOG_BYTES_TOTAL = 2 * 1024 * 1024
_FORBIDDEN_ARCHIVE_NAMES = {
    "api_config.json",
    "api_config.local.json",
    "candidates_all.json",
    "contact_queue.json",
    "job_config.json",
}
_SAFE_CONTEXT_KEYS = {
    "browser_connected",
    "browser_state",
    "current_page",
    "data_storage_error_present",
    "dpi_scale",
    "screen_height",
    "screen_width",
    "tk_patchlevel",
    "window_height",
    "window_width",
    "zoom_factor",
}
_CANDIDATE_SECRET_KEYS = {
    "email",
    "encrypt_expect_id",
    "encrypt_jid",
    "encrypt_job_id",
    "expect_id",
    "expectid",
    "geek_id",
    "geekid",
    "id_card",
    "idcard",
    "jid",
    "lid",
    "mobile",
    "name",
    "phone",
    "resume_file",
    "security_id",
    "securityid",
}
_CONFIG_SECRET_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)([\"']?\b(?:securityId|encryptJid|encryptExpectId|encryptJobId|"
    r"expectId|geekId|jobId|lid|jid|access_token|refresh_token|"
    r"authorization|api[_-]?key|token|password|cookie)"
    r"[\"']?\s*(?:[=:]|%3d)\s*[\"']?)[^&\s,;\"'}]+"
)
_BEARER_RE = re.compile(r"(?i)(\b(?:Bearer|Basic)\s+)[A-Za-z0-9._~+/=-]+")
_API_KEY_RE = re.compile(
    r"(?i)\b(?:sk|ak|key|token)[-_][A-Za-z0-9._~+/=-]{8,}\b"
)
_EMAIL_RE = re.compile(
    r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
)
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_CN_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\w)")
_IP_RE = re.compile(
    r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"
)
_WINDOWS_USER_PATH_RE = re.compile(
    r"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\r\n]+"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)\b[A-Z]:[\\/][^\r\n]*"
)
_UNIX_USER_PATH_RE = re.compile(
    r"(?<!\w)/(?:Users|home)/[^/\s]+"
)
_BUSINESS_LABEL_PATTERNS = (
    re.compile(
        r"(\[简历评估\]\s*(?:正在评估|✗)\s+)([^:：\s.]+)"
    ),
    re.compile(r"(\[撤销评估\]\s+)([^:：\r\n]+)(?=[：:])"),
    re.compile(r"(\[联系候选人\]\s*(?:正在向|核实)\s+)([^\s:：]+)"),
    re.compile(r"(\b正在向\s+)([^\s]+)(?=\s+打招呼)"),
    re.compile(r"(^\s*-\s+)([^(\r\n]+)(?=\s+\()", re.MULTILINE),
    re.compile(
        r"(\[\d+/\d+\]\s+)([^\s-]+)(?=\s+(?:-|打招呼|\())"
    ),
    re.compile(r"((?:已屏蔽|已移除)[：:]\s*)([^\s，,。]+)"),
    re.compile(
        r"((?:配置岗位|BOSS 当前岗位|处理岗位|岗位)[：:]\s*)"
        r"([^\r\n，,]+)"
    ),
    re.compile(r"(处理岗位\s+\d+/\d+[：:]\s*)([^\r\n，,]+)"),
    re.compile(r"(岗位[“\"])([^”\"\r\n]+)([”\"])"),
)


class DiagnosticPrivacyError(RuntimeError):
    """Raised when a package cannot be proven free of known raw secrets."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _read_json_without_recovery(
    path: Path,
    default: Any,
) -> tuple[Any, str]:
    if not path.is_file():
        return default, ""
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj), ""
    except (OSError, json.JSONDecodeError) as exc:
        return default, f"{path.name}: {type(exc).__name__}"


def _collect_nested_values(
    value: Any,
    *,
    accepted_keys: set[str],
) -> set[str]:
    result: set[str] = set()

    def visit(current: Any, key: str = "") -> None:
        normalized_key = key.replace("-", "_").casefold()
        if normalized_key in accepted_keys and current not in (None, ""):
            text = str(current).strip()
            if text:
                result.add(text)
            return
        if isinstance(current, dict):
            for child_key, child_value in current.items():
                visit(child_value, str(child_key))
        elif isinstance(current, list):
            for child in current:
                visit(child, key)

    visit(value)
    return result


def _build_redaction_map(
    root: Path,
    candidates: list[dict[str, Any]],
    config: dict[str, Any],
    api_config: dict[str, Any],
) -> dict[str, str]:
    replacements: dict[str, str] = {}
    for index, candidate in enumerate(candidates, 1):
        alias = f"<candidate-{index:04d}>"
        for value in _collect_nested_values(
            candidate,
            accepted_keys=_CANDIDATE_SECRET_KEYS,
        ):
            replacements[value] = alias

    upgraded_config, _ = upgrade_job_config(config)
    job_names: list[str] = []
    for field in ("job_requirements", "jobs"):
        job_names.extend(
            str(name)
            for name in (upgraded_config.get(field) or {})
            if str(name) != "default"
        )
    for candidate in candidates:
        name = str(candidate.get("job_name") or "").strip()
        if name:
            job_names.append(name)
    for index, job_name in enumerate(dict.fromkeys(job_names), 1):
        replacements[job_name] = f"<job-{index:03d}>"

    def collect_config_secrets(current: Any, key: str = "") -> None:
        normalized_key = key.replace("-", "_").casefold()
        if any(marker in normalized_key for marker in _CONFIG_SECRET_MARKERS):
            if current not in (None, "") and not isinstance(current, (dict, list)):
                replacements[str(current)] = "<redacted-secret>"
            return
        if isinstance(current, dict):
            for child_key, child_value in current.items():
                collect_config_secrets(child_value, str(child_key))
        elif isinstance(current, list):
            for child in current:
                collect_config_secrets(child, key)

    collect_config_secrets(api_config)
    replacements[str(root)] = "<app-dir>"
    try:
        replacements[str(Path.home())] = "<user-dir>"
    except RuntimeError:
        pass
    return {
        key: value
        for key, value in replacements.items()
        if key and len(key) >= 2
    }


def sanitize_diagnostic_text(
    value: Any,
    replacements: dict[str, str] | None = None,
) -> str:
    """Redact known identities, credentials, contact details, and local paths."""
    text = str(value or "")
    for raw, alias in sorted(
        (replacements or {}).items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        text = text.replace(raw, alias)
    text = _BEARER_RE.sub(r"\1***", text)
    text = _SENSITIVE_ASSIGNMENT_RE.sub(r"\1***", text)
    text = _API_KEY_RE.sub("<redacted-secret>", text)
    text = _EMAIL_RE.sub("<redacted-email>", text)
    text = _PHONE_RE.sub("<redacted-phone>", text)
    text = _CN_ID_RE.sub("<redacted-id>", text)
    text = _IP_RE.sub("<redacted-ip>", text)
    for pattern in _BUSINESS_LABEL_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(r"\1<redacted-business>\3", text)
        else:
            text = pattern.sub(r"\1<redacted-business>", text)
    text = _WINDOWS_USER_PATH_RE.sub("<user-dir>", text)
    text = _UNIX_USER_PATH_RE.sub("<user-dir>", text)
    return _WINDOWS_ABSOLUTE_PATH_RE.sub("<redacted-path>", text)


def _safe_error(error: str, replacements: dict[str, str]) -> str:
    return sanitize_diagnostic_text(error, replacements)[:300]


def _schema_summary(
    config: dict[str, Any],
    candidates: list[dict[str, Any]],
    queue_payload: dict[str, Any],
    *,
    errors: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "job_config": {
            "schema_version": config.get("schema_version", 1)
            if isinstance(config, dict)
            else None,
            "valid": False,
            "job_count": 0,
            "stable_job_id_count": 0,
        },
        "candidates": {
            "valid": False,
            "candidate_count": len(candidates)
            if isinstance(candidates, list)
            else 0,
            "schema_versions": {},
            "stable_job_id_count": 0,
        },
        "contact_queue": {
            "schema_version": queue_payload.get("version", 1)
            if isinstance(queue_payload, dict)
            else None,
            "valid": False,
            "queue_count": len(queue_payload.get("items", []))
            if isinstance(queue_payload, dict)
            and isinstance(queue_payload.get("items"), list)
            else 0,
            "stable_job_id_count": 0,
        },
        "errors": list(errors),
    }
    try:
        upgraded_config, _ = upgrade_job_config(config)
        validate_job_config(upgraded_config)
        jobs: list[dict[str, Any]] = []
        for field in ("job_requirements", "jobs"):
            jobs.extend(
                rule
                for name, rule in (upgraded_config.get(field) or {}).items()
                if name != "default" and isinstance(rule, dict)
            )
        result["job_config"].update({
            "schema_version": upgraded_config.get("schema_version", 1),
            "valid": True,
            "job_count": len(jobs),
            "stable_job_id_count": sum(
                1 for rule in jobs if normalize_job_uuid(rule.get("job_uuid"))
            ),
        })
    except (TypeError, ValueError) as exc:
        result["errors"].append(f"job_config.json: {type(exc).__name__}")

    try:
        validate_candidates_snapshot(candidates)
        versions = Counter(
            int(candidate.get("schema_version", 1))
            for candidate in candidates
        )
        result["candidates"].update({
            "valid": True,
            "candidate_count": len(candidates),
            "schema_versions": {
                str(version): count
                for version, count in sorted(versions.items())
            },
            "stable_job_id_count": sum(
                1
                for candidate in candidates
                if normalize_job_uuid(candidate.get("job_uuid"))
            ),
        })
    except (TypeError, ValueError) as exc:
        result["errors"].append(f"candidates_all.json: {type(exc).__name__}")

    try:
        validate_contact_queue_snapshot(queue_payload)
        items = queue_payload.get("items", [])
        result["contact_queue"].update({
            "valid": True,
            "queue_count": len(items),
            "stable_job_id_count": sum(
                1 for item in items if normalize_job_uuid(item.get("job_uuid"))
            ),
        })
    except (TypeError, ValueError) as exc:
        result["errors"].append(f"contact_queue.json: {type(exc).__name__}")
    return result


def _api_summary(api_config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(api_config, dict):
        return {"exists": False}
    saved_models = api_config.get("saved_models")
    return {
        "exists": bool(api_config),
        "provider": str(api_config.get("api_provider") or ""),
        "model": str(api_config.get("model") or ""),
        "saved_model_count": len(saved_models)
        if isinstance(saved_models, list)
        else 0,
        "read_timeout": api_config.get("llm_read_timeout"),
        "has_plaintext_api_key_field": any(
            bool(api_config.get(key))
            for key in ("api_key", "token", "password")
        ),
    }


def _safe_runtime_context(context: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in (context or {}).items():
        if key not in _SAFE_CONTEXT_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    return result


def _read_log_tail(path: Path, limit: int) -> tuple[bytes, bool]:
    size = path.stat().st_size
    with open(path, "rb") as file_obj:
        if size > limit:
            file_obj.seek(size - limit)
            content = file_obj.read()
            newline = content.find(b"\n")
            if newline >= 0:
                content = content[newline + 1:]
            return content, True
        return file_obj.read(), False


def _bounded_utf8_tail(text: str, limit: int) -> tuple[bytes, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return encoded, False
    tail = encoded[-limit:].decode("utf-8", errors="ignore")
    newline = tail.find("\n")
    if newline >= 0:
        tail = tail[newline + 1:]
    bounded = tail.encode("utf-8")
    while len(bounded) > limit:
        tail = tail[1:]
        bounded = tail.encode("utf-8")
    return bounded, True


def _collect_sanitized_logs(
    root: Path,
    replacements: dict[str, str],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    log_dir = root / "logs"
    candidates = []
    if log_dir.is_dir():
        for pattern in ("app-*.log", "run-*.log"):
            candidates.extend(
                path
                for path in log_dir.glob(pattern)
                if path.is_file() and path.parent.resolve() == log_dir.resolve()
            )
    selected = sorted(
        candidates,
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )[:MAX_LOG_FILES]
    remaining = MAX_LOG_BYTES_TOTAL
    payloads: dict[str, bytes] = {}
    truncated_files: list[str] = []
    for path in selected:
        if remaining <= 0:
            break
        per_file_limit = min(MAX_LOG_BYTES_PER_FILE, remaining)
        raw, truncated = _read_log_tail(path, per_file_limit)
        text = raw.decode("utf-8", errors="replace")
        sanitized = sanitize_diagnostic_text(text, replacements)
        encoded, redaction_expanded = _bounded_utf8_tail(
            sanitized,
            per_file_limit,
        )
        payloads[f"logs/{path.name}"] = encoded
        remaining -= len(encoded)
        if truncated or redaction_expanded:
            truncated_files.append(path.name)
    return payloads, {
        "included_count": len(payloads),
        "truncated_count": len(truncated_files),
        "truncated_files": truncated_files,
        "source_total_count": len(candidates),
    }


def _privacy_audit(
    payloads: dict[str, bytes],
    raw_tokens: set[str],
) -> None:
    forbidden_paths = [
        name
        for name in payloads
        if Path(name).name in _FORBIDDEN_ARCHIVE_NAMES
        or name.startswith(("resumes/", ".chrome_profile/", ".storage/"))
    ]
    if forbidden_paths:
        raise DiagnosticPrivacyError(
            "诊断包包含禁止文件：" + "、".join(forbidden_paths)
        )
    combined = b"\n".join(payloads.values()).decode(
        "utf-8",
        errors="replace",
    )
    leaks = [
        token
        for token in raw_tokens
        if len(token) >= 2 and token in combined
    ]
    if leaks:
        raise DiagnosticPrivacyError(
            f"诊断包脱敏复核失败，发现 {len(leaks)} 个原始值残留"
        )
    unsafe_patterns = (
        _EMAIL_RE,
        _PHONE_RE,
        _CN_ID_RE,
        _WINDOWS_USER_PATH_RE,
        _UNIX_USER_PATH_RE,
    )
    if any(pattern.search(combined) for pattern in unsafe_patterns):
        raise DiagnosticPrivacyError("诊断包脱敏复核失败，发现敏感格式残留")


def _validate_zip(path: Path, manifest: dict[str, Any]) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        names = set(archive.namelist())
        expected = set(manifest["files"]) | {"manifest.json"}
        if names != expected:
            raise ValueError("诊断包文件清单不一致")
        for name, metadata in manifest["files"].items():
            content = archive.read(name)
            if (
                len(content) != metadata["size"]
                or _sha256_bytes(content) != metadata["sha256"]
            ):
                raise ValueError(f"诊断包文件校验失败：{name}")


def create_diagnostic_package(
    base_dir: str | Path,
    destination: str | Path,
    *,
    app_version: str,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an allowlisted support ZIP and reject it on any privacy audit failure."""
    root = Path(base_dir).resolve()
    destination_path = Path(destination)
    if destination_path.suffix.lower() != ".zip":
        destination_path = destination_path.with_suffix(".zip")

    config, config_error = _read_json_without_recovery(
        root / "job_config.json",
        {},
    )
    candidates, candidates_error = _read_json_without_recovery(
        root / "candidates_all.json",
        [],
    )
    queue_payload, queue_error = _read_json_without_recovery(
        root / "contact_queue.json",
        {"version": CONTACT_QUEUE_SCHEMA_VERSION, "items": []},
    )
    api_config, api_error = _read_json_without_recovery(
        get_api_config_path(root),
        {},
    )
    if not isinstance(config, dict):
        config = {}
        config_error = config_error or "job_config.json: invalid root"
    if not isinstance(candidates, list):
        candidates = []
        candidates_error = candidates_error or "candidates_all.json: invalid root"
    candidate_records = [
        candidate for candidate in candidates if isinstance(candidate, dict)
    ]
    if not isinstance(queue_payload, dict):
        queue_payload = {
            "version": CONTACT_QUEUE_SCHEMA_VERSION,
            "items": [],
        }
        queue_error = queue_error or "contact_queue.json: invalid root"
    if not isinstance(api_config, dict):
        api_config = {}
        api_error = api_error or "api_config.json: invalid root"

    replacements = _build_redaction_map(
        root,
        candidate_records,
        config,
        api_config,
    )
    raw_tokens = set(replacements)
    errors = [
        error
        for error in (
            config_error,
            candidates_error,
            queue_error,
            api_error,
        )
        if error
    ]
    schema = _schema_summary(
        config,
        candidates,
        queue_payload,
        errors=errors,
    )
    schema["errors"] = [
        _safe_error(error, replacements)
        for error in schema["errors"]
    ]
    logs, log_summary = _collect_sanitized_logs(root, replacements)
    summary = {
        "format_version": DIAGNOSTIC_FORMAT_VERSION,
        "created_at": _utc_now(),
        "application": {
            "version": str(app_version),
            "frozen": bool(getattr(sys, "frozen", False)),
        },
        "environment": {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
        },
        "ui": _safe_runtime_context(runtime_context),
        "data": schema,
        "api": _api_summary(api_config),
        "runtime_files": {
            "transaction_pending": (root / ".data_transaction.json").is_file(),
            "data_manifest_exists": (root / ".data_manifest.json").is_file(),
            "access_guard_state_exists": (root / ".boss_access_guard.json").is_file(),
        },
        "logs": log_summary,
        "privacy": {
            "raw_candidate_files_included": False,
            "resume_files_included": False,
            "api_keys_included": False,
            "browser_profile_included": False,
            "logs_redacted": True,
        },
        "supported_schema_versions": {
            "job_config": JOB_CONFIG_SCHEMA_VERSION,
            "candidates": CANDIDATE_SCHEMA_VERSION,
            "contact_queue": CONTACT_QUEUE_SCHEMA_VERSION,
        },
    }
    summary_text = sanitize_diagnostic_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        replacements,
    )
    readme = (
        "BOSS 简历筛选器诊断包\n"
        "\n"
        "本包仅包含环境、版本、数据结构计数和经过脱敏的最近日志。\n"
        "不包含候选人原始数据、简历、岗位内容、API Key、Cookie 或浏览器资料。\n"
        "程序已执行自动脱敏和残留复核，但分享前仍建议人工查看包内文本。\n"
    )
    readme = sanitize_diagnostic_text(readme, replacements)
    payloads = {
        "README.txt": readme.encode("utf-8"),
        "diagnostic-summary.json": (summary_text + "\n").encode("utf-8"),
        **logs,
    }
    _privacy_audit(payloads, raw_tokens)
    manifest = {
        "format_version": DIAGNOSTIC_FORMAT_VERSION,
        "created_at": summary["created_at"],
        "files": {
            name: {
                "sha256": _sha256_bytes(content),
                "size": len(content),
            }
            for name, content in sorted(payloads.items())
        },
    }

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination_path.name}.",
        suffix=".tmp",
        dir=destination_path.parent,
        delete=False,
    ) as tmp_file:
        tmp_path = Path(tmp_file.name)
    try:
        with zipfile.ZipFile(
            tmp_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for name, content in payloads.items():
                archive.writestr(name, content)
            archive.writestr("manifest.json", _json_bytes(manifest))
        with open(tmp_path, "r+b") as file_obj:
            os.fsync(file_obj.fileno())
        _validate_zip(tmp_path, manifest)
        os.replace(tmp_path, destination_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return {
        "path": str(destination_path),
        "log_count": log_summary["included_count"],
        "candidate_count": schema["candidates"]["candidate_count"],
        "privacy_checked": True,
    }
