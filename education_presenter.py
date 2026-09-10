"""Plain display formatting for certificate verification."""
from collections.abc import Mapping
from typing import Any


def screenshot_session_summary(items: Mapping[str, Mapping[str, Any]]) -> str:
    """Count only this attempt's outcomes, never previously existing files."""
    statuses = [item.get("screenshot_attempt_status", "") for item in items.values()]
    saved = statuses.count("已保存")
    failed = sum(status in {"页面已关闭", "文件异常", "截图失败"} for status in statuses)
    pending = len(statuses) - saved - failed
    return f"已保存 {saved}｜未保存 {pending}｜保存失败 {failed}"
