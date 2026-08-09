"""Pure presentation helpers for run progress, logs, and summaries."""
from __future__ import annotations

import math
import re


def estimate_run_summary_rows(text: object, chars_per_row: int = 60) -> int:
    """Estimate wrapped summary rows without depending on a mapped window."""
    lines = str(text or "").splitlines() or [""]
    return sum(max(1, math.ceil(len(line) / chars_per_row)) for line in lines)


def format_terminal_progress_text(final_desc: object) -> str:
    """Keep the progress line short; full terminal details belong in the summary."""
    desc = str(final_desc or "")
    if desc.startswith("[完成]"):
        return "筛选完成，详细结果见下方摘要"
    if desc.startswith(("[达到轮次上限]", "[可能未扫完]")):
        return "本轮处理完成；尚未确认扫描到底，详见下方摘要"
    if desc.startswith("[扫描中断]"):
        return "扫描中断，已保存当前结果；详见下方摘要"
    if desc.startswith("[已停止]"):
        return "运行已停止，已保存当前结果"
    if desc.startswith("[出错]"):
        return "运行出错，详情见运行日志"
    return ""


def format_terminal_log_text(final_desc: object) -> str:
    """Return one terminal log line without repeating the business summary."""
    desc = str(final_desc or "")
    if desc.startswith("[完成]"):
        return "本轮处理完成"
    if desc.startswith(("[达到轮次上限]", "[可能未扫完]")):
        return "本轮处理完成，扫描达到轮次上限"
    if desc.startswith("[扫描中断]"):
        return "扫描中断，已保存当前结果"
    if desc.startswith("[已停止]"):
        return "运行已停止，已保存当前结果"
    if desc.startswith("[出错]"):
        detail = desc.split("]", 1)[-1].strip().splitlines()[0]
        return f"运行出错：{detail}" if detail else "运行出错"
    return "运行结束"


def replace_run_summary_contact_queue_count(
    final_desc: object,
    added_count: object,
) -> str:
    """Use the GUI contact-queue wording and count in the run summary."""
    try:
        count = max(0, int(added_count))
    except (TypeError, ValueError):
        count = 0
    text = str(final_desc or "")
    replacement = f"本轮已加联系清单：{count} 人"
    updated = re.sub(
        r"本轮(?:打招呼|已联系)：\d+ 人",
        replacement,
        text,
        count=1,
    )
    if updated != text:
        return updated

    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("最终保留："):
            lines.insert(index + 1, replacement)
            return "\n".join(lines)
    return f"{text.rstrip()}\n{replacement}".strip()
