"""Read-only user-facing release audit checks."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


FORBIDDEN_RELEASE_ASSETS = {"README.md", "job_config.json", "ui_config.json", "api_config.json", "selectors.json"}
STYLE_KEYWORDS = (
    "listener", "API 兜底", "持久化字段", "结构化数据", "去重合并", "阶段 1.", "阶段 2.",
    "正则", "OR 条件", "AND 条件", "provider", "keyring", "DPI", "sha256", "SHA256",
    "locale-data", "openpyxl", "srcdoc", "iframe", "greet_context", "latest.json",
    "job_config.json", "api_config.json", "selectors.json", "ui_config.json",
)


@dataclass(frozen=True)
class ReleaseAuditIssue:
    severity: str
    title: str
    detail: str
    suggestion: str


def audit_user_facing_release(base_dir: str | Path) -> list[ReleaseAuditIssue]:
    """Return read-only release readiness issues from a normal user's view."""
    base = Path(base_dir)
    issues: list[ReleaseAuditIssue] = []
    version = _read_version(base)
    if not version:
        return [_issue("error", "无法读取当前版本", "gui_main.py 中没有找到 __version__。", "先修复版本来源。")]

    readme = _read_text(base / "README.md")
    changelog = _read_text(base / "CHANGELOG.md")
    latest = _read_latest_json(base / "latest.json")

    if f"当前发布版本：v{version}" not in readme:
        issues.append(_issue(
            "error", "README 当前版本未同步",
            f"README 顶部没有标明当前发布版本 v{version}。",
            "发布前同步 README 顶部版本标识。",
        ))
    if not re.search(rf"^###\s+v{re.escape(version)}(?:\s|$)", readme, re.MULTILINE):
        issues.append(_issue(
            "warning", "README 缺少当前版本摘要",
            f"README 中没有找到 v{version} 的用户可见摘要小节。",
            "在 README 版本历史保留当前版本摘要。",
        ))

    changelog_section = _extract_changelog_section(changelog, version)
    if not changelog_section:
        issues.append(_issue(
            "error", "CHANGELOG 缺少当前版本段落",
            f"CHANGELOG.md 中没有找到 v{version} 段落。",
            "先补齐当前版本变更说明。",
        ))
    else:
        issues.extend(_audit_release_text("CHANGELOG", changelog_section))

    if latest:
        latest_version = str(latest.get("version", "")).lstrip("v")
        if latest_version and latest_version != version:
            issues.append(_issue(
                "warning", "latest.json 版本滞后",
                f"latest.json 是 v{latest_version}，源码当前版本是 v{version}。",
                "发布流程会更新 latest.json；发布前确认这是预期状态。",
            ))
        release_notes = str(latest.get("release_notes") or "")
        if latest_version == version and changelog_section and _normalize(release_notes) != _normalize(_extract_release_body(changelog_section)):
            issues.append(_issue(
                "warning", "latest.json 更新说明未同步",
                "latest.json 的 release_notes 与 CHANGELOG 当前版本段落不一致。",
                "发布前运行同步或发布流程，让更新弹窗说明来自 CHANGELOG。",
            ))
        asset_names = _latest_asset_names(latest)
        forbidden = sorted(name for name in asset_names if name in FORBIDDEN_RELEASE_ASSETS)
        if forbidden:
            issues.append(_issue(
                "error", "latest.json 暴露内部文件为下载资产",
                "不应作为普通用户下载资产：" + "、".join(forbidden),
                "Release 资产只保留安装包和自动更新必需产物。",
            ))
    else:
        issues.append(_issue("warning", "latest.json 不存在或无法解析", "无法检查更新源说明和资产边界。", "发布前确认 latest.json 可正常解析。"))

    return issues


def summarize_release_user_audit(issues: list[ReleaseAuditIssue]) -> str:
    counts = {
        "error": sum(1 for issue in issues if issue.severity == "error"),
        "warning": sum(1 for issue in issues if issue.severity == "warning"),
        "info": sum(1 for issue in issues if issue.severity == "info"),
    }
    status = "可发布" if counts["error"] == 0 else "不建议发布"
    lines = [
        "发布前用户视角审计",
        f"结论：{status}",
        f"发现问题：{len(issues)} 项（阻断 {counts['error']}，提醒 {counts['warning']}，建议 {counts['info']}）",
    ]
    if not issues:
        lines.extend(["", "未发现明显用户视角发布问题。"])
        return "\n".join(lines)
    lines.append("")
    label = {"error": "阻断", "warning": "提醒", "info": "建议"}
    for idx, issue in enumerate(issues, 1):
        lines.extend([
            f"{idx}. [{label.get(issue.severity, '提醒')}] {issue.title}",
            f"   问题：{issue.detail}",
            f"   建议：{issue.suggestion}",
            "",
        ])
    return "\n".join(lines).rstrip()


def _audit_release_text(source: str, text: str) -> list[ReleaseAuditIssue]:
    issues: list[ReleaseAuditIssue] = []
    for keyword in STYLE_KEYWORDS:
        if keyword in text:
            issues.append(_issue(
                "warning", f"{source} 含内部实现表述",
                f"用户可见说明中出现“{keyword}”。",
                "改成普通用户能理解的功能、体验或问题描述。",
            ))
            break
    backticks = re.findall(r"`[^`\s]+`", text)
    if backticks:
        issues.append(_issue(
            "warning", f"{source} 含疑似字段名或文件名",
            "反引号内容：" + "、".join(backticks[:5]),
            "用户说明中尽量不用变量名、字段名和内部文件名。",
        ))
    return issues


def _read_version(base: Path) -> str:
    text = _read_text(base / "gui_main.py")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def _extract_changelog_section(text: str, version: str) -> str:
    match = re.search(rf"^##\s+v{re.escape(version)}.*?\n(.*?)(?=^##\s+v|\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(0) if match else ""


def _extract_release_body(section: str) -> str:
    return "\n".join(section.splitlines()[1:]).strip()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_latest_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _latest_asset_names(data: dict) -> set[str]:
    names: set[str] = set()
    assets = data.get("assets")
    if isinstance(assets, dict):
        for value in assets.values():
            if isinstance(value, dict):
                name = value.get("name")
                if name:
                    names.add(str(name))
    for key in ("downloads", "downloads_cn"):
        downloads = data.get(key)
        if isinstance(downloads, dict):
            for value in downloads.values():
                if isinstance(value, str):
                    names.add(value.rstrip("/").split("/")[-1])
    return names


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in str(text or "").strip().splitlines() if line.strip())


def _issue(severity: str, title: str, detail: str, suggestion: str) -> ReleaseAuditIssue:
    return ReleaseAuditIssue(severity, title, detail, suggestion)
