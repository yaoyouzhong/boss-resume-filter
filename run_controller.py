"""Run-session orchestration and terminal semantics without Tk dependencies."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import sys
import traceback
from typing import Any, Callable, Sequence


Candidate = dict[str, Any]


@dataclass(frozen=True)
class RunTuning:
    """Plain scan tuning values supplied by the GUI host."""

    api_candidate_limit_default: int
    greet_context_limit_default: int
    dom_delay_center: float
    dom_delay_spread: float
    dom_batch_min: int
    dom_batch_max: int
    dom_pause_center: float
    dom_pause_spread: float


@dataclass(frozen=True)
class RunRequest:
    """Immutable snapshot of all Tk-owned values needed by one scan."""

    version: str
    rounds: int
    contact_policy: str
    selected_job: str
    api_direct_enabled: bool
    api_direct_pages: int
    greet_context_capture_enabled: bool
    greet_context_capture_limit: int
    ai_requested: bool
    ai_eval_enabled: bool
    ai_api_config: dict[str, Any] | None
    ai_api_key: str | None
    llm_read_timeout: object
    tuning: RunTuning
    setup_logs: tuple[str, ...]

    @property
    def job_arg(self) -> str | None:
        return None if self.selected_job == "全部岗位" else self.selected_job

    @property
    def greet_level(self) -> str:
        return (
            "strong"
            if self.contact_policy == "将强烈推荐加入联系清单"
            else "normal"
        )

    def settings_snapshot(self) -> tuple[tuple[str, object], ...]:
        tuning = self.tuning
        delay_min = tuning.dom_delay_center - tuning.dom_delay_spread / 2
        delay_max = tuning.dom_delay_center + tuning.dom_delay_spread / 2
        pause_min = tuning.dom_pause_center - tuning.dom_pause_spread / 2
        pause_max = tuning.dom_pause_center + tuning.dom_pause_spread / 2
        model = (
            str((self.ai_api_config or {}).get("model") or "")
            if self.ai_requested
            else "未启用"
        )
        return (
            ("滚动轮次", self.rounds),
            ("DOM滚动间隔", f"{delay_min:g}-{delay_max:g} 秒"),
            (
                "DOM长暂停",
                f"每 {tuning.dom_batch_min}-{tuning.dom_batch_max} 轮暂停 "
                f"{pause_min:g}-{pause_max:g} 秒",
            ),
            ("筛选完成后", self.contact_policy),
            ("选择岗位", self.selected_job),
            (
                "扫描增强",
                "自动补全候选人详情" if self.api_direct_enabled else "关闭",
            ),
            (
                "最多读取页数",
                self.api_direct_pages if self.api_direct_enabled else "未启用",
            ),
            (
                "后续联系",
                (
                    "扫描后准备联系信息"
                    if self.greet_context_capture_enabled
                    else "关闭"
                ),
            ),
            (
                "最多准备人数",
                (
                    self.greet_context_capture_limit
                    if self.greet_context_capture_enabled
                    else "未启用"
                ),
            ),
            ("AI 辅助评估", "启用" if self.ai_requested else "关闭"),
            ("AI 模型", model),
            (
                "AI 响应超时",
                (
                    f"{self.llm_read_timeout} 秒"
                    if self.ai_requested
                    else "未启用"
                ),
            ),
            ("打招呼等级", self.greet_level),
            (
                "提取链路",
                "先读取页面已有信息；再滚动确认可见候选人；"
                "必要时按设置补全候选人详情",
            ),
        )

    def scan_args(self) -> argparse.Namespace:
        """Build the legacy scanner namespace without importing bossmaster."""
        max_candidates = self.api_direct_pages * 20 if self.api_direct_enabled else 0
        return argparse.Namespace(
            clear=False,
            job=self.job_arg,
            greet=False,
            re_greet=False,
            greet_level=self.greet_level,
            greet_names=None,
            list_candidates=False,
            rounds=self.rounds,
            max_candidates=max_candidates,
            dom_only=False,
            listener_first=not self.api_direct_enabled,
            verbose=False,
            ai_eval=self.ai_eval_enabled,
            api_config=self.ai_api_config,
            api_key=self.ai_api_key,
            greet_context_capture=self.greet_context_capture_enabled,
            greet_context_limit=self.greet_context_capture_limit,
        )


@dataclass(frozen=True)
class RunProgressEvent:
    """One progress update emitted by the scanner."""

    current: int | float
    total: int | float
    desc: str


@dataclass(frozen=True)
class RunOutcome:
    """Raw scan result before GUI terminal rendering and contact-list handling."""

    final_desc: str
    scanned_candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class RunTerminalEvent:
    """UI-neutral terminal state consumed by the GUI host."""

    final_desc: str
    status_text: str
    status_tone: str
    progress_text: str
    terminal_log: str
    should_build_contact_list: bool


class TimestampedLogRedirector:
    """Line-buffer stdout into the run log without owning files or widgets."""

    def __init__(
        self,
        callback: Callable[[str], None],
        *,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.callback = callback
        self.buffer = ""
        self._now = now

    def _emit(self, text: str) -> None:
        self.callback(f"[{self._now().strftime('%H:%M:%S')}] {text}")

    def write(self, text: str) -> None:
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self._emit(line)

    def flush(self) -> None:
        if self.buffer.strip():
            self._emit(self.buffer)
        self.buffer = ""


class RunController:
    """Own scan request normalization, execution, progress, and terminal semantics."""

    @staticmethod
    def _coerce_int(value: object, default: int, minimum: int, maximum: int) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = default
        return max(minimum, min(maximum, result))

    @classmethod
    def prepare_request(
        cls,
        *,
        version: str,
        rounds: object,
        contact_policy: object,
        selected_job: object,
        api_direct_enabled: object,
        api_direct_pages: object,
        greet_context_capture_enabled: object,
        greet_context_capture_limit: object,
        ai_eval_enabled: object,
        ai_api_config: dict[str, Any] | None,
        ai_api_key: str | None,
        llm_read_timeout: object,
        tuning: RunTuning,
        setup_logs: Sequence[str] = (),
    ) -> RunRequest:
        default_pages = max(1, (tuning.api_candidate_limit_default + 19) // 20)
        return RunRequest(
            version=str(version),
            rounds=cls._coerce_int(rounds, 50, 1, 500),
            contact_policy=str(contact_policy or "仅保存筛选结果"),
            selected_job=str(selected_job or "全部岗位"),
            api_direct_enabled=bool(api_direct_enabled),
            api_direct_pages=cls._coerce_int(
                api_direct_pages,
                default_pages,
                1,
                20,
            ),
            greet_context_capture_enabled=bool(greet_context_capture_enabled),
            greet_context_capture_limit=cls._coerce_int(
                greet_context_capture_limit,
                tuning.greet_context_limit_default,
                1,
                100,
            ),
            ai_requested=bool(ai_eval_enabled),
            ai_eval_enabled=bool(ai_eval_enabled and ai_api_key),
            ai_api_config=ai_api_config if ai_eval_enabled else None,
            ai_api_key=ai_api_key if ai_eval_enabled and ai_api_key else None,
            llm_read_timeout=llm_read_timeout,
            tuning=tuning,
            setup_logs=tuple(setup_logs),
        )

    @staticmethod
    def progress_payload(event: RunProgressEvent | dict[str, Any]) -> dict[str, Any]:
        if isinstance(event, RunProgressEvent):
            return {
                "current": event.current,
                "total": event.total,
                "desc": event.desc,
            }
        return dict(event)

    def execute(
        self,
        request: RunRequest,
        *,
        scan: Callable[..., Sequence[Candidate] | None],
        log: Callable[[str], None],
        settings_sink: Callable[[Sequence[tuple[str, object]]], None],
        progress_sink: Callable[[RunProgressEvent], None],
        stop_event: object,
        existing_page: object,
        confirm_callback: Callable[..., bool],
        captcha_callback: Callable[..., bool],
        notice_callback: Callable[..., None],
        blocking_notice_callback: Callable[..., None],
        job_match_callback: Callable[..., bool],
        job_config_callback: Callable[..., bool],
    ) -> RunOutcome:
        """Execute one scan through injected business and UI dependencies."""
        final_desc = ""
        scanned_candidates: Sequence[Candidate] = ()
        old_stdout = sys.stdout
        redirector = TimestampedLogRedirector(log)
        try:
            sys.stdout = redirector
            log(f">>> BOSS 直聘候选人智能提取工具 v{request.version} [图形界面模式]")
            for message in request.setup_logs:
                log(message)
            settings_sink(request.settings_snapshot())
            if request.ai_eval_enabled:
                model_name = str((request.ai_api_config or {}).get("model") or "unknown")
                log(f"AI 辅助评估已启用（模型：{model_name}）")
            elif (
                request.ai_requested
                and not request.ai_api_key
                and not any(
                    message.startswith("加载 API 配置失败：")
                    for message in request.setup_logs
                )
            ):
                log("AI 评估需要 API Key，但未配置，将跳过")

            if request.job_arg:
                log(f"[初次扫描模式] 指定岗位：{request.job_arg}")
            else:
                log("[初次扫描模式] 处理全部岗位")
            log("开始扫描候选人...")

            def on_progress(percentage: int | float, description: object) -> None:
                nonlocal final_desc
                desc = str(description)
                if percentage >= 100 and desc.startswith("["):
                    final_desc = desc
                progress_sink(RunProgressEvent(percentage, 100, desc))

            scanned_candidates = scan(
                request.scan_args(),
                progress_callback=on_progress,
                confirm_callback=confirm_callback,
                stop_event=stop_event,
                existing_page=existing_page,
                captcha_callback=captcha_callback,
                notice_callback=notice_callback,
                blocking_notice_callback=blocking_notice_callback,
                job_match_callback=job_match_callback,
                job_config_callback=job_config_callback,
            ) or ()
        except KeyboardInterrupt:
            final_desc = final_desc or "[已停止] 用户取消岗位切换"
            log("用户取消岗位切换，已停止")
        except Exception as exc:
            final_desc = f"[出错] {str(exc)[:30]}"
            log(f"运行出错：{exc}")
            log(traceback.format_exc())
        finally:
            redirector.flush()
            sys.stdout = old_stdout

        final_desc = final_desc or "[出错] 未取得最终运行状态"
        return RunOutcome(final_desc, tuple(scanned_candidates))

    @staticmethod
    def terminal_event(outcome: RunOutcome, contact_policy: str) -> RunTerminalEvent:
        from run_presenter import (
            format_terminal_log_text,
            format_terminal_progress_text,
        )

        final_desc = outcome.final_desc
        if final_desc.startswith("[完成]"):
            status_text, status_tone = "● 已完成", "success"
        elif final_desc.startswith(("[达到轮次上限]", "[可能未扫完]")):
            status_text, status_tone = "● 本轮处理完成", "success"
        elif final_desc.startswith("[扫描中断]"):
            status_text, status_tone = "● 扫描中断", "warning"
        elif final_desc.startswith("[已停止]"):
            status_text, status_tone = "● 已停止", "danger"
        else:
            status_text, status_tone = "● 运行出错", "danger"
        should_build = bool(
            contact_policy != "仅保存筛选结果"
            and outcome.scanned_candidates
            and final_desc.startswith(
                ("[完成]", "[达到轮次上限]", "[可能未扫完]")
            )
        )
        return RunTerminalEvent(
            final_desc,
            status_text,
            status_tone,
            format_terminal_progress_text(final_desc),
            format_terminal_log_text(final_desc),
            should_build,
        )
