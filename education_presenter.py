"""Plain display formatting for certificate verification."""
from collections.abc import Mapping
from typing import Any
import re


def format_recognition_notice(text: str) -> str:
    """Translate model field identifiers without discarding review details."""
    labels = {
        "certificate_number": "证书编号", "certificate_type": "证书类型",
        "field_confidence": "字段置信度", "school": "学校", "major": "专业",
        "name": "姓名", "confidence": "置信度", "unknown": "待确认",
    }
    text = re.sub(
        r"\b(" + "|".join(labels) + r")\b",
        lambda match: labels[match.group().lower()], str(text), flags=re.IGNORECASE,
    )
    notices = list(dict.fromkeys(
        part.strip() for part in re.split(r"[；\n]+", text) if part.strip()
    ))
    notices.sort(key=lambda notice: 0 if any(label in notice for label in ("姓名", "证书编号", "证书类型", "关键")) else 1)
    return "\n".join(notices)


def screenshot_item_feedback(name: str, status: str, detail: str) -> tuple[str, str]:
    """Describe the selected certificate's actual save outcome."""
    if status == "已保存":
        action = "重新截图成功，已更新原文件" if "替换原文件" in detail else "截图保存成功"
        return "success", f"{name}：{action}。"
    return "warning", f"{name}：{detail or status}"


def screenshot_session_summary(items: Mapping[str, Mapping[str, Any]]) -> str:
    """Count only this attempt's outcomes, never previously existing files."""
    statuses = [item.get("screenshot_attempt_status", "") for item in items.values()]
    saved = statuses.count("已保存")
    failed = sum(status in {"页面已关闭", "文件异常", "截图失败"} for status in statuses)
    pending = len(statuses) - saved - failed
    return f"已保存 {saved}｜未保存 {pending}｜保存失败 {failed}"
