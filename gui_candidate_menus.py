"""Tk popup-menu builders for candidate actions."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class CandidateMenuHost(Protocol):
    """Visual host contract used by candidate popup menus."""

    colors: Mapping[str, str]
    font_scale: float
    icons: Any


@dataclass(frozen=True)
class WorkflowCandidateMenuState:
    """Eligibility and ordering state for workflow candidate menus."""

    primary_action: str | None
    needs_review: bool
    can_confirm_review: bool
    needs_send_verification: bool
    has_active_queue_item: bool
    can_queue: bool
    can_approve_queue: bool
    greet_sent: bool
    followup_status: str
    blacklisted: bool


@dataclass(frozen=True)
class WorkflowCandidateMenuCallbacks:
    """Controller-owned actions exposed to a workflow candidate menu."""

    view_detail: Callable[[], None]
    confirm_review: Callable[[], None]
    reject_review: Callable[[], None]
    add_queue: Callable[[], None]
    focus_queue: Callable[[], None]
    approve_queue: Callable[[], None]
    verify_sent: Callable[[], None]
    import_resume: Callable[[], None]
    update_followup: Callable[[], None]
    mark_replied: Callable[[], None]
    advance_to_interview: Callable[[], None]
    follow_up_tomorrow: Callable[[], None]
    mark_feedback: Callable[[], None]
    add_blacklist: Callable[[], None]
    remove_blacklist: Callable[[], None]


@dataclass(frozen=True)
class CandidateContextMenuState:
    """Eligibility state for a single result-table candidate menu."""

    has_ai_evaluation: bool
    has_resume_adjustment: bool
    needs_review: bool
    can_confirm_review: bool
    queue_action: str
    blacklisted: bool


@dataclass(frozen=True)
class CandidateContextMenuCallbacks:
    """Controller-owned actions exposed to a single-candidate menu."""

    view_detail: Callable[[], None]
    evaluate_ai: Callable[[], None]
    import_resume: Callable[[], None]
    revert_resume_evaluation: Callable[[], None]
    confirm_review: Callable[[], None]
    reject_review: Callable[[], None]
    add_queue: Callable[[], None]
    focus_queue: Callable[[], None]
    approve_queue: Callable[[], None]
    update_followup: Callable[[], None]
    mark_feedback: Callable[[], None]
    add_blacklist: Callable[[], None]
    remove_blacklist: Callable[[], None]
    remove_candidate: Callable[[], None]


@dataclass(frozen=True)
class CandidateBatchMenuState:
    """Eligibility state for a multi-selection result-table menu."""

    ai_label: str | None
    can_confirm_review: bool


@dataclass(frozen=True)
class CandidateBatchMenuCallbacks:
    """Controller-owned actions exposed to a multi-selection menu."""

    add_queue: Callable[[], None]
    evaluate_ai: Callable[[], None]
    confirm_review: Callable[[], None]
    remove_selected: Callable[[], None]
    export_selected: Callable[[], None]


def _create_menu(
    host: CandidateMenuHost,
    parent: tk.Misc,
    font_family: str,
    icon_specs: Mapping[str, tuple[str, str]],
) -> tuple[tk.Menu, dict[str, Any]]:
    """Create a popup menu and retain all generated icon references."""
    menu = tk.Menu(
        parent,
        tearoff=0,
        font=(font_family, int(11 * host.font_scale)),
    )
    icons = {
        key: host.icons.button(icon_name, host.colors[color_key])
        for key, (icon_name, color_key) in icon_specs.items()
    }
    menu._icon_refs = list(icons.values())
    return menu, icons


def _add_command(
    menu: tk.Menu,
    icons: Mapping[str, Any],
    *,
    label: str,
    icon: str,
    command: Callable[[], None],
) -> None:
    """Add one icon command using the project's stable menu layout."""
    menu.add_command(
        label=f" {label}",
        image=icons[icon],
        compound=tk.LEFT,
        command=command,
    )


def show_workflow_candidate_menu(
    host: CandidateMenuHost,
    parent: tk.Misc,
    x_root: int,
    y_root: int,
    font_family: str,
    state: WorkflowCandidateMenuState,
    callbacks: WorkflowCandidateMenuCallbacks,
) -> tk.Menu:
    """Build and show the candidate menu used by workflow workbenches."""
    menu, icons = _create_menu(
        host,
        parent,
        font_family,
        {
            "detail": ("candidate_review", "primary"),
            "queue": ("chat", "success"),
            "confirm": ("stamp_check", "success"),
            "followup": ("pencil", "primary"),
            "feedback": ("check", "primary"),
            "document": ("document", "primary"),
            "blacklist": ("close", "danger"),
            "unblacklist": ("check", "success"),
        },
    )

    def add_confirm() -> None:
        _add_command(
            menu,
            icons,
            label="确认通过",
            icon="confirm",
            command=callbacks.confirm_review,
        )

    def add_queue() -> None:
        _add_command(
            menu,
            icons,
            label="加入联系清单",
            icon="queue",
            command=callbacks.add_queue,
        )

    def add_approve_queue() -> None:
        _add_command(
            menu,
            icons,
            label="确认并加入联系清单",
            icon="queue",
            command=callbacks.approve_queue,
        )

    def add_resume() -> None:
        _add_command(
            menu,
            icons,
            label="导入简历 / 二次评估",
            icon="document",
            command=callbacks.import_resume,
        )

    def add_followup() -> None:
        _add_command(
            menu,
            icons,
            label="更新跟进",
            icon="followup",
            command=callbacks.update_followup,
        )

    if state.needs_send_verification:
        _add_command(
            menu,
            icons,
            label="核实发送结果",
            icon="confirm",
            command=callbacks.verify_sent,
        )
        menu.add_separator()
    elif state.primary_action == "confirm" and state.can_confirm_review:
        add_confirm()
        menu.add_separator()
    elif state.primary_action == "confirm" and state.can_approve_queue:
        add_approve_queue()
        menu.add_separator()
    elif state.primary_action == "queue" and state.can_queue:
        add_queue()
        menu.add_separator()
    elif state.primary_action == "resume":
        add_resume()
        menu.add_separator()
    elif state.primary_action == "followup":
        add_followup()
        menu.add_separator()

    _add_command(
        menu,
        icons,
        label="查看与复核",
        icon="detail",
        command=callbacks.view_detail,
    )
    if state.can_confirm_review and state.primary_action != "confirm":
        add_confirm()
    if state.needs_review:
        _add_command(
            menu,
            icons,
            label="确认不通过",
            icon="blacklist",
            command=callbacks.reject_review,
        )

    if state.has_active_queue_item:
        _add_command(
            menu,
            icons,
            label="查看联系清单",
            icon="queue",
            command=callbacks.focus_queue,
        )
    elif state.can_queue and state.primary_action != "queue":
        add_queue()
    elif state.can_approve_queue and not (
        state.primary_action == "confirm" and not state.can_confirm_review
    ):
        add_approve_queue()

    if state.primary_action != "followup":
        add_followup()
    if state.greet_sent or state.followup_status in {
        "已回复",
        "待约面",
        "已约面",
    }:
        menu.add_separator()
        if state.followup_status not in {
            "已回复",
            "待约面",
            "已约面",
            "不合适",
            "已归档",
        }:
            _add_command(
                menu,
                icons,
                label="标记已回复",
                icon="followup",
                command=callbacks.mark_replied,
            )
        if state.followup_status not in {
            "待约面",
            "已约面",
            "不合适",
            "已归档",
        }:
            _add_command(
                menu,
                icons,
                label="推进到待约面",
                icon="confirm",
                command=callbacks.advance_to_interview,
            )
        if state.followup_status in {"已打招呼", "待约面", "已约面"}:
            _add_command(
                menu,
                icons,
                label="明天再跟进",
                icon="followup",
                command=callbacks.follow_up_tomorrow,
            )

    _add_command(
        menu,
        icons,
        label="标记反馈",
        icon="feedback",
        command=callbacks.mark_feedback,
    )
    if state.primary_action != "resume":
        add_resume()
    if state.blacklisted:
        _add_command(
            menu,
            icons,
            label="移出黑名单",
            icon="unblacklist",
            command=callbacks.remove_blacklist,
        )
    else:
        _add_command(
            menu,
            icons,
            label="加入黑名单",
            icon="blacklist",
            command=callbacks.add_blacklist,
        )

    menu.tk_popup(x_root, y_root)
    return menu


def show_candidate_context_menu(
    host: CandidateMenuHost,
    parent: tk.Misc,
    x_root: int,
    y_root: int,
    font_family: str,
    state: CandidateContextMenuState,
    callbacks: CandidateContextMenuCallbacks,
) -> tk.Menu:
    """Build and show a result-table menu for one candidate."""
    menu, icons = _create_menu(
        host,
        parent,
        font_family,
        {
            "detail": ("candidate_review", "primary"),
            "document": ("document", "primary"),
            "queue": ("chat", "success"),
            "followup": ("pencil", "primary"),
            "feedback": ("check", "primary"),
            "blacklist": ("close", "danger"),
            "unblacklist": ("check", "success"),
            "trash": ("trash", "text_primary"),
            "undo": ("refresh", "text_primary"),
            "ai": ("ai_spark", "primary"),
            "confirm": ("stamp_check", "success"),
        },
    )
    _add_command(
        menu,
        icons,
        label="查看与复核",
        icon="detail",
        command=callbacks.view_detail,
    )
    if not state.has_ai_evaluation:
        _add_command(
            menu,
            icons,
            label="AI评估",
            icon="ai",
            command=callbacks.evaluate_ai,
        )
    _add_command(
        menu,
        icons,
        label="导入简历 / 二次评估",
        icon="document",
        command=callbacks.import_resume,
    )
    if state.has_resume_adjustment:
        _add_command(
            menu,
            icons,
            label="撤销简历评估",
            icon="undo",
            command=callbacks.revert_resume_evaluation,
        )
    if state.can_confirm_review:
        _add_command(
            menu,
            icons,
            label="确认通过",
            icon="confirm",
            command=callbacks.confirm_review,
        )
    if state.needs_review:
        _add_command(
            menu,
            icons,
            label="确认不通过",
            icon="blacklist",
            command=callbacks.reject_review,
        )

    queue_actions = {
        "focus": ("查看联系清单", callbacks.focus_queue),
        "add": ("加入联系清单", callbacks.add_queue),
        "approve": ("确认并加入联系清单", callbacks.approve_queue),
    }
    if state.queue_action in queue_actions:
        label, command = queue_actions[state.queue_action]
        _add_command(
            menu,
            icons,
            label=label,
            icon="queue",
            command=command,
        )
    _add_command(
        menu,
        icons,
        label="更新跟进",
        icon="followup",
        command=callbacks.update_followup,
    )
    _add_command(
        menu,
        icons,
        label="标记反馈",
        icon="feedback",
        command=callbacks.mark_feedback,
    )
    if state.blacklisted:
        _add_command(
            menu,
            icons,
            label="移出黑名单",
            icon="unblacklist",
            command=callbacks.remove_blacklist,
        )
    else:
        _add_command(
            menu,
            icons,
            label="加入黑名单",
            icon="blacklist",
            command=callbacks.add_blacklist,
        )
    _add_command(
        menu,
        icons,
        label="移除此人",
        icon="trash",
        command=callbacks.remove_candidate,
    )

    menu.tk_popup(x_root, y_root)
    return menu


def show_candidate_batch_menu(
    host: CandidateMenuHost,
    parent: tk.Misc,
    x_root: int,
    y_root: int,
    font_family: str,
    state: CandidateBatchMenuState,
    callbacks: CandidateBatchMenuCallbacks,
) -> tk.Menu:
    """Build and show a result-table menu for multiple candidates."""
    menu, icons = _create_menu(
        host,
        parent,
        font_family,
        {
            "export": ("export", "text_primary"),
            "trash": ("trash", "text_primary"),
            "queue": ("chat", "success"),
            "ai": ("ai_spark", "primary"),
            "confirm": ("stamp_check", "success"),
        },
    )
    _add_command(
        menu,
        icons,
        label="加入联系清单",
        icon="queue",
        command=callbacks.add_queue,
    )
    if state.ai_label:
        _add_command(
            menu,
            icons,
            label=state.ai_label.lstrip(),
            icon="ai",
            command=callbacks.evaluate_ai,
        )
    if state.can_confirm_review:
        _add_command(
            menu,
            icons,
            label="批量确认通过",
            icon="confirm",
            command=callbacks.confirm_review,
        )
    _add_command(
        menu,
        icons,
        label="移除选中",
        icon="trash",
        command=callbacks.remove_selected,
    )
    menu.add_separator()
    _add_command(
        menu,
        icons,
        label="导出选中",
        icon="export",
        command=callbacks.export_selected,
    )

    menu.tk_popup(x_root, y_root)
    return menu
