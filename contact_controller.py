"""Contact queue preparation and send-state control without Tk dependencies."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
import time
from typing import Any

from candidate_workflow import candidate_greet_skip_reason
from contact_queue import ACTIVE_STATUSES, build_contact_queue_item, candidate_identity
from greeting_failure import diagnose_greeting_failure, format_greeting_failure_message


QueueItem = dict[str, Any]
Candidate = dict[str, Any]


@dataclass(frozen=True)
class QueueLoadOutcome:
    items: tuple[QueueItem, ...]
    restored_count: int
    changed: bool
    error: str = ""


@dataclass(frozen=True)
class QueueAddOutcome:
    added_count: int
    skipped_reasons: dict[str, int]


@dataclass(frozen=True)
class QueueResolutionOutcome:
    resolved_count: int
    failures: tuple[tuple[str, str], ...]


@dataclass
class ContactRunCounters:
    success: int = 0
    failed: int = 0
    pending: int = 0
    skipped: int = 0
    page_waiting: int = 0
    page_waiting_jobs: Counter[str] = field(default_factory=Counter)

    def feedback(self, *, stopped: bool, error: str = "") -> dict[str, Any]:
        return {
            "success": self.success,
            "failed": self.failed,
            "pending": self.pending,
            "page_waiting": self.page_waiting,
            "page_waiting_jobs": dict(self.page_waiting_jobs),
            "skipped": self.skipped,
            "stopped": stopped,
            "error": error,
        }


@dataclass(frozen=True)
class SendTransition:
    status: str
    message: str
    log_message: str
    consecutive_uncertain: int
    stop_after: bool = False
    refresh_results: bool = False
    notice: ContactNotice | None = None


@dataclass(frozen=True)
class ContactNotice:
    """UI-neutral notice emitted by the contact state machine."""

    level: str
    title: str
    headline: str
    message: str
    detail: str = ""


@dataclass(frozen=True)
class SendExceptionDecision:
    """Host classification for an exception raised by an injected sender."""

    status: str
    message: str
    log_message: str
    notice: ContactNotice | None = None


@dataclass(frozen=True)
class ContactWorkerOutcome:
    """Deterministic result of one queue run."""

    counters: ContactRunCounters
    notice: ContactNotice | None = None
    pause_requested: bool = False


class ContactController:
    """Own queue intent and deterministic contact-result transitions."""

    @staticmethod
    def build_item(candidate: Candidate, *, source: str = "manual") -> QueueItem:
        return build_contact_queue_item(candidate, source=source)

    @staticmethod
    def identity(candidate: Candidate) -> tuple[str, str]:
        return candidate_identity(candidate)

    @classmethod
    def item_for(
        cls,
        items: Sequence[QueueItem],
        candidate: Candidate,
        *,
        active_only: bool = False,
    ) -> QueueItem | None:
        key = cls.identity(candidate)
        return next(
            (
                item
                for item in items
                if item.get("key") == key
                and (
                    not active_only
                    or (item.get("status") or "待发送") in ACTIVE_STATUSES
                )
            ),
            None,
        )

    @staticmethod
    def load_and_revalidate(
        candidates_path: Path,
        queue_path: Path,
        *,
        load_candidates: Callable[[Path], list[Candidate]],
        load_queue: Callable[[list[Candidate], Path], list[QueueItem]],
        revalidate: Callable[[Candidate], tuple[str, str]],
        now: Callable[[], str] = lambda: datetime.now().strftime("%Y%m%d_%H%M%S"),
    ) -> QueueLoadOutcome:
        try:
            candidates = load_candidates(candidates_path)
            items = load_queue(candidates, queue_path)
        except Exception as exc:
            return QueueLoadOutcome((), 0, False, str(exc))
        changed = False
        for item in items:
            if item.get("status") == "待核实":
                continue
            status, message = revalidate(item.get("candidate") or {})
            if status == "待发送":
                continue
            item.update({"status": status, "message": message, "updated_at": now()})
            changed = True
        return QueueLoadOutcome(tuple(items), len(items), changed)

    @classmethod
    def sync_candidate(
        cls,
        items: Sequence[QueueItem],
        candidate: Candidate,
        *,
        revalidate: Callable[[Candidate], tuple[str, str]],
        now: str,
    ) -> int:
        key = cls.identity(candidate)
        geek_id = str(candidate.get("geek_id") or "")
        updated = 0
        for item in items:
            if (item.get("status") or "待发送") not in ACTIVE_STATUSES:
                continue
            same_key = item.get("key") == key
            same_blacklisted_person = (
                candidate.get("blacklisted")
                and geek_id
                and str((item.get("candidate") or {}).get("geek_id") or "")
                == geek_id
            )
            if not (same_key or same_blacklisted_person):
                continue
            item_candidate = candidate
            if not same_key:
                item_candidate = dict(item.get("candidate") or {})
                item_candidate.update({
                    "blacklisted": True,
                    "blacklist_reason": candidate.get("blacklist_reason", ""),
                    "blacklisted_at": candidate.get("blacklisted_at", now),
                    "followup_status": "不合适",
                    "followup_updated_at": now,
                })
                item_candidate.pop("next_followup_at", None)
            status, message = revalidate(item_candidate)
            item.update({
                "candidate": item_candidate,
                "status": status,
                "message": message,
                "updated_at": now,
            })
            updated += 1
        return updated

    @classmethod
    def add_candidates(
        cls,
        items: list[QueueItem],
        candidates: Iterable[Candidate],
        *,
        source: str,
        skip_reason: Callable[[Candidate], str] = candidate_greet_skip_reason,
        build_item: Callable[..., QueueItem] = build_contact_queue_item,
    ) -> QueueAddOutcome:
        existing_keys = {item.get("key") for item in items}
        skipped: Counter[str] = Counter()
        added = 0
        for candidate in candidates:
            key = cls.identity(candidate)
            reason = skip_reason(candidate)
            if not reason and key in existing_keys:
                reason = "已在队列"
            if reason:
                skipped[reason] += 1
                continue
            items.append(build_item(candidate, source=source))
            existing_keys.add(key)
            added += 1
        return QueueAddOutcome(added, dict(skipped))

    @staticmethod
    def set_item_state(
        item: QueueItem,
        status: str,
        message: str,
        *,
        now: str | None = None,
    ) -> None:
        item.update({
            "status": status,
            "message": message,
            "updated_at": now or datetime.now().strftime("%Y%m%d_%H%M%S"),
        })

    @staticmethod
    def remove_items(
        items: Sequence[QueueItem],
        selected: Sequence[QueueItem],
    ) -> list[QueueItem]:
        remove_ids = {item.get("queue_id") for item in selected}
        return [item for item in items if item.get("queue_id") not in remove_ids]

    @classmethod
    def retry_failed(cls, selected: Sequence[QueueItem]) -> int:
        changed = 0
        for item in selected:
            if item.get("status") != "发送失败":
                continue
            cls.set_item_state(item, "待发送", "等待重试")
            changed += 1
        return changed

    @classmethod
    def resolve_pending(
        cls,
        selected: Sequence[QueueItem],
        *,
        sent: bool,
        candidates_path: Path,
        resolver: Callable[..., bool],
    ) -> QueueResolutionOutcome:
        resolved = 0
        failures: list[tuple[str, str]] = []
        for item in selected:
            candidate = item.get("candidate") or {}
            name = str(candidate.get("name") or "")
            try:
                saved = resolver(candidate, sent=sent, path=candidates_path)
            except Exception as exc:
                saved = False
                failures.append((name, str(exc)))
            if not saved:
                item["message"] = "未能保存核实结果"
                if not any(failure[0] == name for failure in failures):
                    failures.append((name, "持久化未返回成功"))
                continue
            resolved += 1
            cls.set_item_state(
                item,
                "已发送" if sent else "待发送",
                (
                    "已由用户在 BOSS 沟通列表确认"
                    if sent
                    else "已确认未发送，可以重新发送"
                ),
            )
        return QueueResolutionOutcome(resolved, tuple(failures))

    @staticmethod
    def pending_items(
        items: Sequence[QueueItem],
        selected: Sequence[QueueItem],
    ) -> list[QueueItem]:
        source = selected if selected else items
        return [item for item in source if item.get("status") == "待发送"]

    @classmethod
    def mark_page_waiting(
        cls,
        item: QueueItem,
        candidate: Candidate,
        message: str,
        counters: ContactRunCounters,
    ) -> None:
        counters.page_waiting += 1
        counters.page_waiting_jobs[
            str(candidate.get("job_name") or "未指定岗位").strip()
        ] += 1
        cls.set_item_state(item, "待发送", message)

    @classmethod
    def apply_revalidation(
        cls,
        item: QueueItem,
        status: str,
        message: str,
        counters: ContactRunCounters,
    ) -> None:
        cls.set_item_state(item, status, message)
        if status == "待核实":
            counters.pending += 1
        elif status == "已跳过":
            counters.skipped += 1

    @classmethod
    def apply_send_result(
        cls,
        item: QueueItem,
        candidate: Candidate,
        *,
        success: bool | None,
        raw_message: str,
        method: str,
        consecutive_uncertain: int,
        uncertain_limit: int,
        persist_pending: Callable[[Candidate, str], Any],
        persist_success: Callable[[Candidate, str], bool],
        counters: ContactRunCounters,
    ) -> SendTransition:
        name = str(candidate.get("name") or "")
        if success is None:
            persist_pending(candidate, raw_message)
            counters.pending += 1
            consecutive_uncertain += 1
            message = format_greeting_failure_message(raw_message)
            cls.set_item_state(item, "待核实", message)
            return SendTransition(
                "待核实",
                message,
                f"[联系候选人] {name} 待核实：{message}",
                consecutive_uncertain,
                stop_after=consecutive_uncertain >= uncertain_limit,
            )
        if success:
            if persist_success(candidate, method):
                counters.success += 1
                cls.set_item_state(item, "已发送", raw_message)
                return SendTransition(
                    "已发送",
                    raw_message,
                    f"[联系候选人] {name} 发送成功",
                    0,
                    refresh_results=True,
                )
            counters.pending += 1
            message = "BOSS 已返回发送成功，但本地状态保存失败，请先核实"
            cls.set_item_state(item, "待核实", message)
            return SendTransition(
                "待核实",
                message,
                f"[联系候选人] {name} 待核实：{message}",
                0,
            )

        counters.failed += 1
        message = format_greeting_failure_message(raw_message)
        diagnosis = diagnose_greeting_failure(raw_message)
        cls.set_item_state(item, "发送失败", message)
        return SendTransition(
            "发送失败",
            message,
            f"[联系候选人] {name} 发送失败：{message}",
            0,
            stop_after=bool(diagnosis.terminal),
            notice=(
                ContactNotice(
                    "notice",
                    diagnosis.title,
                    "后续发送已停止",
                    diagnosis.action,
                    f"原始信息：{raw_message}",
                )
                if diagnosis.terminal
                else None
            ),
        )

    @classmethod
    def run_queue(
        cls,
        items: list[QueueItem],
        queue_snapshot: Sequence[QueueItem],
        *,
        stop_requested: Callable[[], bool],
        is_paused: Callable[[], bool],
        reload_candidate: Callable[[QueueItem], tuple[Candidate | None, str]],
        revalidate: Callable[[Candidate], tuple[str, str]],
        has_direct_context: Callable[[Candidate], bool],
        ensure_page_ready: Callable[[Candidate], tuple[bool, str]],
        send_candidate: Callable[[Candidate], tuple[bool | None, str, str]],
        classify_send_exception: Callable[[Exception], SendExceptionDecision | None],
        persist_pending: Callable[[Candidate, str], Any],
        persist_success: Callable[[Candidate, str], bool],
        commit_state: Callable[[], None],
        refresh_results: Callable[[], None],
        log: Callable[[str], None],
        uncertain_limit: int,
        set_active_item: Callable[[QueueItem], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        delay_seconds: Callable[[], float] = lambda: 0.0,
    ) -> ContactWorkerOutcome:
        """Run contact transitions using only explicit host dependencies."""
        counters = ContactRunCounters()
        consecutive_uncertain = 0

        def load_ready_candidate(item: QueueItem) -> Candidate | None:
            candidate, reload_error = reload_candidate(item)
            if candidate is None:
                counters.skipped += 1
                cls.set_item_state(item, "已跳过", reload_error)
                commit_state()
                original_name = str((item.get("candidate") or {}).get("name") or "")
                log(
                    f"[联系候选人] {original_name or '未知候选人'} 已跳过："
                    f"{reload_error}"
                )
                return None
            status, message = revalidate(candidate)
            if status == "待发送":
                return candidate
            cls.apply_revalidation(item, status, message, counters)
            commit_state()
            log(
                f"[联系候选人] {candidate.get('name', '')} {status}：{message}"
            )
            return None

        for item_index, item in enumerate(queue_snapshot):
            if set_active_item is not None:
                set_active_item(item)
            if stop_requested():
                log("[联系候选人] 用户停止操作")
                break
            while is_paused() and not stop_requested():
                sleep(0.2)
            if item not in items or item.get("status") != "待发送":
                continue

            candidate = load_ready_candidate(item)
            if candidate is None:
                continue
            name = str(candidate.get("name") or "")
            if not has_direct_context(candidate):
                page_ready, page_message = ensure_page_ready(candidate)
                if not page_ready:
                    cls.mark_page_waiting(item, candidate, page_message, counters)
                    commit_state()
                    log(f"[联系候选人] {name} 暂未发送：{page_message}")
                    continue

            candidate = load_ready_candidate(item)
            if candidate is None:
                continue
            name = str(candidate.get("name") or "")
            if not has_direct_context(candidate):
                page_ready, page_message = ensure_page_ready(candidate)
                if not page_ready:
                    cls.mark_page_waiting(item, candidate, page_message, counters)
                    commit_state()
                    log(f"[联系候选人] {name} 暂未发送：{page_message}")
                    continue

            cls.set_item_state(item, "发送中", "")
            commit_state()
            log(f"[联系候选人] 正在向 {name} 打招呼...")
            try:
                success, raw_message, method = send_candidate(candidate)
            except Exception as exc:
                decision = classify_send_exception(exc)
                if decision is None:
                    raise
                item["attempts"] = item.get("attempts", 0) + 1
                counters.failed += 1
                cls.set_item_state(item, decision.status, decision.message)
                commit_state()
                log(decision.log_message)
                return ContactWorkerOutcome(counters, notice=decision.notice)

            item["attempts"] = item.get("attempts", 0) + 1
            transition = cls.apply_send_result(
                item,
                candidate,
                success=success,
                raw_message=raw_message,
                method=method,
                consecutive_uncertain=consecutive_uncertain,
                uncertain_limit=uncertain_limit,
                persist_pending=persist_pending,
                persist_success=persist_success,
                counters=counters,
            )
            consecutive_uncertain = transition.consecutive_uncertain
            commit_state()
            log(transition.log_message)
            if re.search(r"\bHTTP\s+4\d\d\b", str(raw_message), re.IGNORECASE):
                log(
                    f"[BOSS接口] 联系候选人返回 4xx：{name}，"
                    f"{transition.message}"
                )
            if transition.refresh_results:
                refresh_results()
            if transition.stop_after:
                if transition.status == "待核实":
                    log("[联系候选人] 连续发送结果待核实，已暂停，请人工核实")
                    return ContactWorkerOutcome(
                        counters,
                        pause_requested=True,
                    )
                if transition.notice is not None:
                    log(f"[联系候选人] {transition.notice.title}，已停止后续发送")
                return ContactWorkerOutcome(counters, notice=transition.notice)
            if stop_requested():
                break
            has_later_pending = any(
                later_item in items and later_item.get("status") == "待发送"
                for later_item in queue_snapshot[item_index + 1 :]
            )
            if has_later_pending:
                sleep(delay_seconds())

        return ContactWorkerOutcome(counters)

    @classmethod
    def finalize_interrupted(
        cls,
        items: Sequence[QueueItem],
        *,
        persist_pending: Callable[[Candidate, str], Any],
    ) -> tuple[int, tuple[tuple[str, str], ...]]:
        pending = 0
        failures: list[tuple[str, str]] = []
        message = "发送流程意外中断，请先到 BOSS 沟通列表核实"
        for item in items:
            if item.get("status") != "发送中":
                continue
            candidate = item.get("candidate") or {}
            cls.set_item_state(item, "待核实", message)
            try:
                persist_pending(candidate, message)
            except Exception as exc:
                failures.append((str(candidate.get("name") or ""), str(exc)))
            pending += 1
        return pending, tuple(failures)
