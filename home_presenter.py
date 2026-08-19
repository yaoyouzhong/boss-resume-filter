"""Pure home-dashboard aggregation and display semantics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from candidate_workflow import (
    CandidateActionItem,
    build_daily_candidate_actions,
    derive_candidate_decision,
)
from data_schema import canonical_candidate_identity
from job_identity import normalize_job_name


@dataclass(frozen=True)
class HomeCandidateSummary:
    """Candidate metrics and mutually exclusive next-action counts."""

    passed: int
    strong: int
    recommended: int
    greeted: int
    pending_contact: int
    pending_verification: int
    pending_review: int


@dataclass(frozen=True)
class StatusDisplay:
    """One concise status with an explicit semantic tone and explanation."""

    text: str
    tone: str
    note: str
    action: str = ""


@dataclass(frozen=True)
class ReadinessDisplay:
    """One actionable readiness conclusion across the three health signals."""

    title: str
    tone: str
    note: str


@dataclass(frozen=True)
class ScanDisplay:
    """Last scan timestamp, scope, and actual terminal status."""

    summary: str
    status: str
    tone: str


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[str, str]:
    return canonical_candidate_identity(candidate)


def _scoped_candidate_copies(
    candidates: Sequence[Mapping[str, Any]],
    selected_job: str,
) -> list[dict[str, Any]]:
    job_scope = normalize_job_name(selected_job)
    return [
        dict(candidate)
        for candidate in candidates
        if not candidate.get("blacklisted")
        and (
            not selected_job
            or selected_job == "全部岗位"
            or normalize_job_name(candidate.get("job_name")) == job_scope
        )
    ]


def build_home_candidate_actions(
    candidates: Sequence[Mapping[str, Any]],
    queue_items: Sequence[Mapping[str, Any]],
    selected_job: str,
) -> list[CandidateActionItem]:
    """Build scoped highest-priority actions with queue verification projected."""
    job_scope = normalize_job_name(selected_job)
    scoped = _scoped_candidate_copies(candidates, selected_job)
    verification_candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for item in queue_items:
        if not isinstance(item, Mapping):
            continue
        queued_candidate = item.get("candidate") or {}
        if not isinstance(queued_candidate, Mapping):
            continue
        status = str(item.get("status") or "").strip()
        if queued_candidate.get("greet_confirmation_pending") or status in {"待核实", "发送中"}:
            queued_job = normalize_job_name(queued_candidate.get("job_name"))
            if selected_job and selected_job != "全部岗位" and queued_job != job_scope:
                continue
            verification_candidates[_candidate_key(queued_candidate)] = dict(
                queued_candidate
            )

    for candidate in scoped:
        if _candidate_key(candidate) in verification_candidates:
            candidate["greet_confirmation_pending"] = True

    action_candidates = list(scoped)
    scoped_keys = {_candidate_key(candidate) for candidate in scoped}
    for key, queued_candidate in verification_candidates.items():
        if key in scoped_keys:
            continue
        queued_candidate["greet_confirmation_pending"] = True
        action_candidates.append(queued_candidate)

    return build_daily_candidate_actions(action_candidates)


def build_home_candidate_summary(
    candidates: Sequence[Mapping[str, Any]],
    queue_items: Sequence[Mapping[str, Any]],
    selected_job: str,
) -> HomeCandidateSummary:
    """Build scoped metrics and one highest-priority action per candidate.

    Queue-only verification state is projected onto an in-memory candidate copy
    before the existing workflow model runs. This prevents one candidate from
    appearing in both verification and contact/review counts.
    """
    scoped = _scoped_candidate_copies(candidates, selected_job)

    decisions = [derive_candidate_decision(candidate) for candidate in scoped]
    passed_candidates = [
        candidate
        for candidate, decision in zip(scoped, decisions)
        if decision.screening_result in {"强烈推荐", "推荐", "待定"}
    ]
    actions = build_home_candidate_actions(candidates, queue_items, selected_job)
    return HomeCandidateSummary(
        passed=len(passed_candidates),
        strong=sum(decision.screening_result == "强烈推荐" for decision in decisions),
        recommended=sum(decision.screening_result == "推荐" for decision in decisions),
        greeted=sum(bool(candidate.get("greet_sent")) for candidate in passed_candidates),
        pending_contact=sum(
            action.group in {"待打招呼", "待外部联系"} for action in actions
        ),
        pending_verification=sum(
            action.group == "发送结果待核实" for action in actions
        ),
        pending_review=sum(action.group == "待复核" for action in actions),
    )


def api_key_display(*, model_configured: bool, key_state: str) -> StatusDisplay:
    """Describe local API-key readiness without claiming network availability."""
    if not model_configured:
        return StatusDisplay(
            "未配置",
            "warning",
            "AI 评估不可用，基础筛选仍可运行",
            "去设置 →",
        )
    if key_state == "checking":
        return StatusDisplay("检测中", "neutral", "正在读取本机安全凭据")
    if key_state == "present":
        return StatusDisplay("已配置", "success", "本机安全凭据已保存")
    if key_state == "error":
        return StatusDisplay("读取失败", "danger", "打开系统设置重新检查", "去设置 →")
    return StatusDisplay(
        "未配置",
        "warning",
        "当前模型缺少 API Key，基础筛选仍可运行",
        "去设置 →",
    )


def chrome_display(state: str) -> StatusDisplay:
    """Describe only the locally observed Chrome debug connection state."""
    displays = {
        "checking": StatusDisplay("检测中", "neutral", "正在检查本机 Chrome"),
        "connected": StatusDisplay("已连接", "success", "当前浏览器连接可直接使用"),
        "available": StatusDisplay(
            "未连接",
            "warning",
            "筛选运行所需浏览器",
            "去连接 →",
        ),
        "offline": StatusDisplay(
            "未连接",
            "warning",
            "筛选运行所需浏览器",
            "去连接 →",
        ),
    }
    return displays.get(state, displays["offline"])


def storage_display(
    *,
    error: str,
    exists: bool,
    candidate_count: int,
    queue_error: str = "",
) -> StatusDisplay:
    """Describe candidate and contact storage failures without conflating them."""
    if str(error or "").strip():
        return StatusDisplay(
            "候选人异常",
            "danger",
            "候选人数据读取失败，请检查备份或原文件",
            "去检查 →",
        )
    if str(queue_error or "").strip():
        return StatusDisplay(
            "联系清单异常",
            "danger",
            "联系清单读取失败，请检查本地数据",
            "去检查 →",
        )
    if not exists or candidate_count <= 0:
        return StatusDisplay("暂无数据", "neutral", "完成筛选或导入候选人后显示")
    return StatusDisplay(
        f"{candidate_count} 条",
        "success",
        "本地候选人数据可读取",
    )


def build_readiness_display(
    statuses: Mapping[str, StatusDisplay],
) -> ReadinessDisplay:
    """Summarize health without hiding which capability is actually blocked."""
    if any(status.tone == "neutral" and status.text == "检测中" for status in statuses.values()):
        return ReadinessDisplay("正在检查运行条件", "neutral", "结果会在几秒内自动更新")

    storage = statuses.get("storage")
    if storage and storage.tone == "danger":
        if storage.text == "联系清单异常":
            return ReadinessDisplay(
                "联系清单暂不可用",
                "danger",
                "请先检查本地数据，再处理待联系任务",
            )
        return ReadinessDisplay(
            "候选人数据暂不可用",
            "danger",
            "请先检查数据存储，再开始筛选或导入",
        )

    browser = statuses.get("browser")
    if not browser or browser.text != "已连接":
        return ReadinessDisplay(
            "开始筛选前需要连接 Chrome",
            "warning",
            "进入运行控制页启动或连接浏览器即可。",
        )

    api = statuses.get("api")
    if api and api.text != "已配置":
        return ReadinessDisplay(
            "基础筛选已就绪",
            "warning",
            "AI 评估需要先配置 API Key",
        )

    return ReadinessDisplay(
        "运行条件已就绪",
        "success",
        "可以开始本轮候选人筛选",
    )


def classify_run_status(final_desc: str) -> str:
    """Map one scanner terminal description to a stable persisted status code."""
    text = str(final_desc or "")
    if text.startswith("[完成]"):
        return "completed"
    if text.startswith(("[达到轮次上限]", "[可能未扫完]")):
        return "partial"
    if text.startswith(("[扫描中断]", "[已停止]")):
        return "stopped"
    return "failed"


def format_scan_display(
    record: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> ScanDisplay:
    """Format a persisted run terminal record without inferring from candidates."""
    if not isinstance(record, Mapping):
        return ScanDisplay("暂无记录 · 完成一次筛选后显示", "", "neutral")
    raw_time = str(record.get("finished_at") or "").strip()
    try:
        finished_at = datetime.fromisoformat(raw_time)
    except ValueError:
        return ScanDisplay("暂无记录 · 完成一次筛选后显示", "", "neutral")

    current = now or datetime.now()
    if finished_at.date() == current.date():
        time_text = f"今天 {finished_at:%H:%M}"
    elif (current.date() - finished_at.date()).days == 1:
        time_text = f"昨天 {finished_at:%H:%M}"
    else:
        time_text = finished_at.strftime("%m-%d %H:%M")
    job_name = str(record.get("job_name") or "全部岗位").strip() or "全部岗位"
    status_code = str(record.get("status") or "failed")
    status_text, tone = {
        "completed": ("已完成", "success"),
        "partial": ("可能未扫完", "warning"),
        "stopped": ("已停止", "warning"),
        "failed": ("失败", "danger"),
    }.get(status_code, ("失败", "danger"))
    return ScanDisplay(f"{time_text} · {job_name}", status_text, tone)
