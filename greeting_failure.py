"""User-facing classification for greeting send failures."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GreetingFailureDiagnosis:
    category: str
    title: str
    action: str
    terminal: bool = False


def diagnose_greeting_failure(message: str, *, page_required: bool = False) -> GreetingFailureDiagnosis:
    """Map a raw greeting failure message to a concrete user action."""
    msg = str(message or "").strip()
    lower = msg.lower()

    if _contains_any(msg, ("上限", "次数已用完", "升级套餐", "沟通次数")):
        return GreetingFailureDiagnosis(
            "limit",
            "今日沟通次数可能已用完",
            "停止本轮发送，明天再试；不要继续重试同一批候选人。",
            terminal=True,
        )
    if _contains_any(msg, ("安全验证", "验证码", "验证弹窗")):
        return GreetingFailureDiagnosis(
            "captcha",
            "BOSS 触发安全验证",
            "先在浏览器里手动完成验证，再重新发起打招呼。",
            terminal=True,
        )
    if _contains_any(msg, ("浏览器未连接", "无法连接", "连接已断开", "重连失败", "driver lost", "page disconnected")):
        return GreetingFailureDiagnosis(
            "browser",
            "浏览器连接不可用",
            "切换到“运行控制”页点击“检测/连接浏览器”，连上后再重试。",
        )
    if _contains_any(msg, ("不是", "推荐牛人", "页面不对", "当前页面")) or page_required:
        return GreetingFailureDiagnosis(
            "wrong_page",
            "当前不在对应岗位推荐页",
            "先在浏览器打开该岗位的“推荐牛人”页面，再发送没有上下文的候选人。",
        )
    status_match = re.search(
        r"(?:\bHTTP\s+|\bcode\s*=\s*|业务码\s+)(4\d\d)\b",
        msg,
        re.IGNORECASE,
    )
    if status_match:
        status = int(status_match.group(1))
        if status in {401, 403, 408, 412, 418, 423, 425, 428, 429} or (
            430 <= status <= 499 and status not in {431, 451}
        ):
            return GreetingFailureDiagnosis(
                "risk_blocked",
                "BOSS 接口疑似触发访问保护",
                "停止本轮自动发送，等待冷却后再试；不要切换路径反复重试。",
                terminal=True,
            )
        return GreetingFailureDiagnosis(
            "client_error",
            "BOSS 接口拒绝请求",
            "停止本轮自动发送，检查登录状态和页面是否正常后再试。",
            terminal=True,
        )
    if _contains_any(msg, ("缺少打招呼上下文", "缺少", "字段", "上下文")):
        return GreetingFailureDiagnosis(
            "context",
            "打招呼上下文不完整",
            "重新扫描对应岗位刷新上下文；急用时先打开推荐牛人页面走列表按钮发送。",
        )
    if _contains_any(msg, ("无法确认", "按钮未变化", "卡片未出现", "待确认", "未完成发送结果确认")):
        return GreetingFailureDiagnosis(
            "pending",
            "发送结果无法确认",
            "先到 BOSS 沟通列表核实，确认没有发送成功前不要重复发。",
        )
    if "http" in lower or "exception" in lower or "异常" in msg:
        return GreetingFailureDiagnosis(
            "network",
            "网络或接口异常",
            "稍后重试；如果连续出现，先重新连接浏览器并确认 BOSS 页面正常。",
        )
    return GreetingFailureDiagnosis(
        "unknown",
        "发送失败",
        "请根据原始失败信息处理；不确定是否已发送时，到 BOSS 沟通列表核实。",
    )


def format_greeting_failure_message(message: str, *, page_required: bool = False) -> str:
    """Return a compact message for queue/table cells."""
    diagnosis = diagnose_greeting_failure(message, page_required=page_required)
    raw = str(message or "").strip()
    if raw:
        return f"{diagnosis.title}；{diagnosis.action}（原始信息：{raw}）"
    return f"{diagnosis.title}；{diagnosis.action}"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    haystack = str(text or "").lower()
    return any(term.lower() in haystack for term in terms)
