"""Pure presentation helpers for the contact-candidate workbench."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from candidate_workflow import candidate_greet_skip_reason


def has_direct_send_context(candidate: Mapping[str, Any]) -> bool:
    """Return whether the candidate has a direct chat-start context."""
    return bool((candidate.get("greet_context") or {}).get("chat_start"))


def greet_queue_readiness_label(candidate: Mapping[str, Any]) -> str:
    """Return the short readiness label shown in the queue."""
    return "已就绪" if has_direct_send_context(candidate) else "发送时检查"


def greet_queue_readiness_tooltip(candidate: Mapping[str, Any]) -> str:
    """Explain what will be checked before this candidate is contacted."""
    if has_direct_send_context(candidate):
        return "发送前仍会检查 Chrome、BOSS 登录状态和推荐牛人页面。"
    job_name = str(candidate.get("job_name") or "对应岗位")
    return (
        "发送时会检查当前推荐牛人页面。"
        f"如岗位不一致，将保留在清单并提示切换到“{job_name}”后重试。"
    )


def greet_queue_method_label(candidate: Mapping[str, Any]) -> str:
    """Compatibility label used by existing queue callers."""
    return greet_queue_readiness_label(candidate)


def format_greet_queue_skip_summary(skipped_reasons: Mapping[str, int]) -> str:
    """Format grouped skip reasons for a bounded result dialog."""
    if not skipped_reasons:
        return ""
    return "\n".join(
        f"- {reason}：{count} 人" for reason, count in skipped_reasons.items()
    )


def greet_queue_group_hint(status: str) -> str:
    """Return one short instruction for the selected contact-queue group."""
    hints = {
        "全部": "按状态筛选候选人；待核实和发送失败应优先处理。",
        "待核实": "请先在 BOSS 沟通列表逐一核实，再确认发送结果。",
        "发送失败": "选择候选人后重试；不再需要的任务可以移除。",
        "待发送": "未选择时联系全部待发送候选人；选中后只联系选中范围。",
        "发送中": "当前联系任务正在执行，可暂停后继续。",
        "已发送": "已完成的发送记录仅供查看。",
        "已跳过": "发送前复核未通过，不会自动联系。",
    }
    return hints.get(status, "选择候选人后，可在下方处理当前状态。")


def greet_queue_selection_text(selected: Sequence[Mapping[str, Any]]) -> str:
    """Summarize the selected scope with candidate names and status."""
    statuses = {item.get("status") or "待发送" for item in selected}
    status_text = next(iter(statuses)) if len(statuses) == 1 else "包含多种状态"
    names = [
        str((item.get("candidate") or {}).get("name") or "未命名").strip()
        for item in selected
    ]
    if len(selected) == 1:
        return f"已选：{names[0]} · {status_text}"
    visible_names = "、".join(names[:3])
    if len(names) > 3:
        visible_names += "等"
    return f"已选 {len(selected)} 人：{visible_names} · {status_text}"


def build_greet_queue_confirmation_content(
    pending: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    """Build the final pre-send confirmation headline and message."""
    direct_count = 0
    page_jobs: Counter[str] = Counter()
    for item in pending:
        candidate = item.get("candidate") or {}
        if has_direct_send_context(candidate):
            direct_count += 1
            continue
        job_name = str(candidate.get("job_name") or "未指定岗位").strip()
        page_jobs[job_name] += 1

    page_count = len(pending) - direct_count
    headline = f"联系 {len(pending)} 名候选人？"
    common = "Chrome：已连接，推荐牛人页面已就绪\n登录：BOSS 账号已登录"
    if not page_count:
        return headline, f"{common}\n岗位：无需切换岗位页面"

    jobs_text = "、".join(
        f"{job_name}（{count} 人）" for job_name, count in page_jobs.items()
    )
    if not direct_count:
        message = f"{common}\n岗位：需要切换到 {jobs_text}"
    else:
        message = (
            f"{common}\n岗位：{direct_count} 人无需切换；"
            f"{page_count} 人需要 {jobs_text}"
        )
    if len(page_jobs) > 1:
        message += "\n提醒：当前岗位不一致的候选人会保留，切换后可再次发送"
    return headline, message


def is_boss_recommend_url(url: object) -> bool:
    """Return whether a URL is one of the supported BOSS recommendation pages."""
    url_lower = str(url or "").lower()
    return (
        "zhipin.com/web/chat/recommend" in url_lower
        or "zhipin.com/web/frame/recommend" in url_lower
    )


def is_boss_login_page(url: object, page_text: object = "") -> bool:
    """Return whether the URL or visible text represents a BOSS login page."""
    url_lower = str(url or "").lower()
    text = str(page_text or "")
    if "login" in url_lower or "/web/user" in url_lower:
        return True
    login_marks = (
        "扫码登录",
        "密码登录",
        "短信登录",
        "登录 BOSS",
        "登录Boss",
        "微信扫码",
    )
    return any(mark in text for mark in login_marks)


def revalidate_greet_queue_candidate(
    candidate: dict[str, Any],
) -> tuple[str, str]:
    """Map the latest candidate truth to a safe queue action."""
    skip_reason = candidate_greet_skip_reason(candidate)
    if not skip_reason:
        return "待发送", ""
    if skip_reason == "已打招呼":
        return "已发送", "本地已标记为已沟通"
    if skip_reason == "发送结果待核实":
        return (
            "待核实",
            str(candidate.get("greet_confirmation_reason") or "发送结果待核实"),
        )
    return "已跳过", skip_reason


def build_greet_queue_run_feedback(
    result: Mapping[str, Any],
) -> tuple[str, str, str, str]:
    """Build the bounded result dialog content for one contact run."""
    success = int(result.get("success") or 0)
    failed = int(result.get("failed") or 0)
    pending = int(result.get("pending") or 0)
    page_waiting = int(result.get("page_waiting") or 0)
    page_waiting_jobs = result.get("page_waiting_jobs") or {}
    skipped = int(result.get("skipped") or 0)
    error = str(result.get("error") or "").strip()
    if error:
        headline = f"已发送 {success} 人，流程中断" if success else "本轮未发送"
        return "发送未完成", headline, error, "error"

    if success and not any(
        (failed, pending, page_waiting, skipped, result.get("stopped"))
    ):
        return "发送完成", "发送完成", f"成功：{success} 人\n状态：联系结果已保存", "info"

    headline = "发送部分完成" if success else "本轮未发送"
    lines = []
    if success:
        lines.append(f"成功：{success} 人")
    if failed:
        lines.append(f"失败：{failed} 人（可在“发送失败”中重试）")
    if pending:
        lines.append(f"待核实：{pending} 人（请到 BOSS 沟通列表确认）")
    if page_waiting:
        jobs_text = "、".join(
            f"{job_name}（{count} 人）"
            for job_name, count in page_waiting_jobs.items()
        )
        lines.append(f"待切换岗位：{page_waiting} 人")
        if jobs_text:
            lines.append(f"涉及岗位：{jobs_text}")
        lines.append("下一步：切换对应岗位后再次发送")
    if skipped:
        lines.append(f"已跳过：{skipped} 人（候选人状态已变化）")
    if result.get("stopped"):
        lines.append("状态：发送已停止，未处理候选人仍保留")
    if not lines:
        lines.append("结果：没有符合发送条件的候选人")
    return "发送结果", headline, "\n".join(lines), "warning"
