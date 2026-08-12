"""Tk form dialogs for candidate blacklist, follow-up, and feedback state."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from tkinter import ttk
from typing import Any, Protocol

import ui_theme
from ui_windowing import create_toplevel, place_window_centered


class CandidateStateDialogHost(Protocol):
    """Visual host contract shared by candidate-state form dialogs."""

    colors: Mapping[str, str]
    dpi_scale: float
    font_scale: float
    zoom_factor: float
    font_label: Any
    font_log: Any
    font_section: Any
    icons: Any


@dataclass(frozen=True)
class BlacklistReasonDialogWidgets:
    """Blacklist dialog references used by focused Tk acceptance tests."""

    window: tk.Toplevel
    reason_text: tk.Text
    save_button: ttk.Button
    cancel_button: ttk.Button


@dataclass(frozen=True)
class FollowupSaveResult:
    """Controller result consumed by the follow-up form after validation."""

    saved: bool
    request_feedback: bool = False


@dataclass(frozen=True)
class FollowupDialogWidgets:
    """Follow-up form references used by focused behavior and Tk tests."""

    window: tk.Toplevel
    status_var: tk.StringVar
    status_combo: ttk.Combobox
    next_followup_var: tk.StringVar
    next_followup_entry: ttk.Entry
    quick_date_buttons: dict[str, ttk.Button]
    note_text: tk.Text
    error_label: ttk.Label
    save_button: ttk.Button
    cancel_button: ttk.Button


@dataclass(frozen=True)
class FeedbackSaveResult:
    """Controller result consumed by the feedback form after validation."""

    saved: bool


@dataclass(frozen=True)
class FeedbackDialogWidgets:
    """Feedback form references used by focused behavior and Tk tests."""

    window: tk.Toplevel
    status_var: tk.StringVar
    status_combo: ttk.Combobox
    reason_vars: dict[str, tk.BooleanVar]
    reason_checkbuttons: dict[str, ttk.Checkbutton]
    note_text: tk.Text
    error_label: ttk.Label
    save_button: ttk.Button
    cancel_button: ttk.Button


def show_blacklist_reason_dialog(
    host: CandidateStateDialogHost,
    candidate: Mapping[str, Any],
    parent: tk.Misc,
    on_confirm: Callable[[str], None],
) -> BlacklistReasonDialogWidgets:
    """Show the blacklist-reason form and pass only the entered reason to the controller."""
    name = candidate.get("name") or "该候选人"
    job_name = candidate.get("job_name") or "未标记岗位"
    existing_reason = candidate.get("blacklist_reason") or ""
    reason_placeholder = "简历造假/性格原因/信用差/其它恶劣行为"
    scale = host.dpi_scale * host.zoom_factor
    width = max(500, int(500 * scale))
    height = max(320, int(320 * scale))
    padding = int(20 * scale)

    window = create_toplevel(parent)
    window.title("加入黑名单")
    window.withdraw()
    window.transient(parent)
    window.grab_set()
    window.configure(bg=host.colors["bg_main"])
    window.resizable(False, False)
    place_window_centered(window, width, height, parent=parent)

    container = ttk.Frame(window, style="Page.TFrame", padding=padding)
    container.pack(fill="both", expand=True)
    ttk.Label(
        container,
        text="加入黑名单",
        font=host.font_section,
        foreground=host.colors["text_primary"],
        background=host.colors["bg_main"],
    ).pack(anchor="w")
    ttk.Label(
        container,
        text=f"{name}｜{job_name}",
        font=host.font_label,
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_main"],
        wraplength=width - padding * 2,
    ).pack(anchor="w", pady=(int(6 * scale), int(16 * scale)))
    ttk.Label(
        container,
        text="屏蔽原因",
        font=host.font_label,
        foreground=host.colors["text_primary"],
        background=host.colors["bg_main"],
    ).pack(anchor="w", pady=(0, int(6 * scale)))

    reason_text = tk.Text(
        container,
        height=4,
        wrap="word",
        font=host.font_label,
        bg=host.colors["bg_card"],
        fg=host.colors["text_primary"],
        insertbackground=host.colors["text_primary"],
        relief="solid",
        bd=1,
        padx=int(10 * scale),
        pady=int(8 * scale),
    )
    reason_text.pack(fill="x")
    placeholder_active = {"value": False}

    def show_placeholder() -> None:
        placeholder_active["value"] = True
        reason_text.config(
            fg=host.colors.get("text_muted", ui_theme.TEXT_MUTED),
        )
        reason_text.delete("1.0", "end")
        reason_text.insert("1.0", reason_placeholder)

    def hide_placeholder() -> None:
        if placeholder_active["value"]:
            placeholder_active["value"] = False
            reason_text.config(fg=host.colors["text_primary"])
            reason_text.delete("1.0", "end")

    if existing_reason:
        reason_text.insert("1.0", existing_reason)
    else:
        show_placeholder()

    ttk.Label(
        container,
        text="后续扫描、统计和导出会跳过此候选人。",
        font=host.font_log,
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_main"],
    ).pack(anchor="w", pady=(int(8 * scale), 0))
    button_frame = tk.Frame(container, bg=host.colors["bg_main"])
    button_frame.pack(anchor="center", pady=(int(16 * scale), 0))

    def close() -> None:
        try:
            window.grab_release()
        except tk.TclError:
            pass
        window.destroy()

    def save() -> None:
        reason = (
            ""
            if placeholder_active["value"]
            else reason_text.get("1.0", "end").strip()
        )
        close()
        on_confirm(reason)

    check_icon = host.icons.button("check", host.colors["primary"])
    close_icon = host.icons.button("close", host.colors["text_secondary"])
    button_pad = int(8 * scale)
    dialog_button_style = ttk.Style(window)
    dialog_button_style.configure(
        "BlacklistDialog.TButton",
        font=host.font_label,
        padding=(int(12 * scale), int(5 * scale)),
    )
    save_button = ttk.Button(
        button_frame,
        image=check_icon,
        text=" 确认加入",
        compound=tk.LEFT,
        command=save,
        style="BlacklistDialog.TButton",
    )
    save_button._icon_ref = check_icon
    save_button.pack(side="left", padx=button_pad)
    cancel_button = ttk.Button(
        button_frame,
        image=close_icon,
        text=" 取消",
        compound=tk.LEFT,
        command=close,
        style="BlacklistDialog.TButton",
    )
    cancel_button._icon_ref = close_icon
    cancel_button.pack(side="left", padx=button_pad)

    window.protocol("WM_DELETE_WINDOW", close)
    reason_text.bind("<FocusIn>", lambda _event: hide_placeholder())
    reason_text.bind(
        "<FocusOut>",
        lambda _event: (
            show_placeholder()
            if not reason_text.get("1.0", "end").strip()
            else None
        ),
    )
    window.bind("<Escape>", lambda _event: close())
    window.bind("<Control-Return>", lambda _event: save())
    window.deiconify()
    window.lift(parent)
    if existing_reason:
        reason_text.focus_set()
        reason_text.tag_add("sel", "1.0", "end-1c")
    else:
        window.focus_set()

    return BlacklistReasonDialogWidgets(
        window=window,
        reason_text=reason_text,
        save_button=save_button,
        cancel_button=cancel_button,
    )


def show_followup_dialog(
    host: CandidateStateDialogHost,
    candidate: Mapping[str, Any],
    parent: tk.Misc,
    *,
    font_family: str,
    status_options: Sequence[str],
    default_next_followup: Callable[[str], str],
    format_followup_due: Callable[[Any], str],
    normalize_followup: Callable[[str], str],
    on_save: Callable[[str, str, str, tk.Toplevel], FollowupSaveResult],
    on_request_feedback: Callable[[], None],
) -> FollowupDialogWidgets:
    """Show the follow-up form while delegating candidate mutation to the controller."""
    scale = host.dpi_scale * host.zoom_factor
    window = create_toplevel(parent)
    window.title("更新跟进")
    window.transient(parent)
    window.grab_set()
    window.withdraw()
    window.configure(bg=host.colors["bg_main"])

    padding = int(18 * scale)
    frame = ttk.Frame(window, style="Page.TFrame", padding=padding)
    frame.pack(fill="both", expand=True)
    ttk.Label(
        frame,
        text=(
            f"{candidate.get('name', '未知')}｜"
            f"{candidate.get('job_name', '未知')}"
        ),
        font=(font_family, int(13 * host.font_scale)),
        foreground=host.colors["primary"],
        background=host.colors["bg_main"],
    ).pack(anchor="w", pady=(0, int(12 * scale)))
    ttk.Label(
        frame,
        text="跟进状态",
        font=(font_family, int(12 * host.font_scale)),
        style="Page.TLabel",
    ).pack(anchor="w")

    default_status = candidate.get("followup_status") or (
        "已打招呼" if candidate.get("greet_sent") else status_options[0]
    )
    status_var = tk.StringVar(value=default_status)
    status_combo = ttk.Combobox(
        frame,
        textvariable=status_var,
        values=status_options,
        state="readonly",
        font=(font_family, int(12 * host.font_scale)),
        width=18,
    )
    status_combo.pack(
        anchor="w",
        fill="x",
        pady=(int(5 * scale), int(12 * scale)),
    )

    ttk.Label(
        frame,
        text="下次跟进日期",
        font=(font_family, int(12 * host.font_scale)),
        style="Page.TLabel",
    ).pack(anchor="w")
    existing_due = format_followup_due(candidate.get("next_followup_at"))
    if existing_due == "未安排":
        existing_due = format_followup_due(default_next_followup(default_status))
        if existing_due == "未安排":
            existing_due = ""
    next_followup_var = tk.StringVar(value=existing_due)
    next_followup_entry = ttk.Entry(
        frame,
        textvariable=next_followup_var,
        font=(font_family, int(12 * host.font_scale)),
    )
    next_followup_entry.pack(
        anchor="w",
        fill="x",
        pady=(int(5 * scale), int(6 * scale)),
    )

    quick_date_frame = ttk.Frame(frame, style="Page.TFrame")
    quick_date_frame.pack(
        anchor="w",
        fill="x",
        pady=(0, int(12 * scale)),
    )
    for column in range(5):
        quick_date_frame.grid_columnconfigure(
            column,
            weight=1,
            uniform="followup_quick_date",
        )

    form_error_label = ttk.Label(
        frame,
        text=" ",
        font=(font_family, int(10 * host.font_scale)),
        foreground=host.colors.get("danger_text", ui_theme.DANGER_TEXT),
        background=host.colors["bg_main"],
        justify="left",
        wraplength=int(440 * scale),
    )

    def clear_form_error(_event: tk.Event | None = None) -> None:
        form_error_label.configure(text=" ")

    def show_form_error(message: str, focus_widget: tk.Misc) -> None:
        form_error_label.configure(text=message)
        try:
            focus_widget.focus_set()
        except tk.TclError:
            pass

    def set_quick_date(days: int | None) -> None:
        clear_form_error()
        if days is None:
            next_followup_var.set("")
            return
        next_followup_var.set(
            (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        )

    quick_date_buttons: dict[str, ttk.Button] = {}
    for column, (label, days) in enumerate(
        (
            ("今天", 0),
            ("明天", 1),
            ("3 天后", 3),
            ("7 天后", 7),
            ("不设置", None),
        )
    ):
        button = ttk.Button(
            quick_date_frame,
            text=label,
            command=lambda value=days: set_quick_date(value),
        )
        button.grid(
            row=0,
            column=column,
            sticky="ew",
            padx=(0, int(5 * scale)) if column < 4 else 0,
        )
        quick_date_buttons[label] = button

    def reset_due_for_status(_event: tk.Event | None = None) -> None:
        clear_form_error()
        formatted = format_followup_due(
            default_next_followup(status_var.get().strip())
        )
        next_followup_var.set("" if formatted == "未安排" else formatted)

    status_combo.bind("<<ComboboxSelected>>", reset_due_for_status)
    ttk.Label(
        frame,
        text="备注",
        font=(font_family, int(12 * host.font_scale)),
        style="Page.TLabel",
    ).pack(anchor="w")
    note_text = tk.Text(
        frame,
        height=5,
        wrap="word",
        font=(font_family, int(12 * host.font_scale)),
        bg=host.colors["bg_card"],
        fg=host.colors["text_primary"],
        relief="solid",
        bd=1,
    )
    note_text.pack(
        fill="both",
        expand=True,
        pady=(int(5 * scale), int(14 * scale)),
    )
    if candidate.get("followup_note"):
        note_text.insert("1.0", candidate.get("followup_note", ""))

    button_frame = ttk.Frame(frame, style="Page.TFrame")
    button_frame.pack(anchor="center")
    form_error_label.pack(
        anchor="w",
        fill="x",
        before=button_frame,
        pady=(0, int(8 * scale)),
    )
    next_followup_entry.bind("<KeyRelease>", clear_form_error)

    def close() -> None:
        try:
            window.grab_release()
        except tk.TclError:
            pass
        window.destroy()

    def save_followup() -> None:
        clear_form_error()
        status = status_var.get().strip()
        note = note_text.get("1.0", "end").strip()
        if status not in status_options:
            show_form_error("请选择有效的跟进状态。", status_combo)
            return
        due_input = next_followup_var.get().strip()
        next_due = normalize_followup(due_input)
        if due_input and not next_due:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_input):
                error_text = "下次跟进日期无效，请检查年月日是否正确"
            else:
                error_text = "下次跟进日期格式不正确，请使用 YYYY-MM-DD"
            show_form_error(error_text, next_followup_entry)
            return
        if status in {"待约面", "已约面"} and not next_due:
            show_form_error(
                f"{status}状态必须安排下次跟进日期。",
                next_followup_entry,
            )
            return

        result = on_save(status, note, next_due, window)
        if not result.saved:
            return
        close()
        if result.request_feedback:
            parent.after(80, on_request_feedback)

    save_button = ttk.Button(
        button_frame,
        text="保存",
        command=save_followup,
    )
    save_button.pack(side="left", padx=(0, int(8 * scale)))
    cancel_button = ttk.Button(button_frame, text="取消", command=close)
    cancel_button.pack(side="left")

    window.protocol("WM_DELETE_WINDOW", close)
    window.update_idletasks()
    followup_height = max(
        int(500 * scale),
        window.winfo_reqheight() + int(12 * scale),
    )
    place_window_centered(
        window,
        int(500 * scale),
        followup_height,
        parent=parent,
    )
    window.deiconify()

    return FollowupDialogWidgets(
        window=window,
        status_var=status_var,
        status_combo=status_combo,
        next_followup_var=next_followup_var,
        next_followup_entry=next_followup_entry,
        quick_date_buttons=quick_date_buttons,
        note_text=note_text,
        error_label=form_error_label,
        save_button=save_button,
        cancel_button=cancel_button,
    )


def show_feedback_dialog(
    host: CandidateStateDialogHost,
    candidate: Mapping[str, Any],
    parent: tk.Misc,
    *,
    font_family: str,
    status_options: Sequence[str],
    reason_options: Sequence[str],
    existing_reasons: Sequence[str],
    default_status: str | None,
    on_save: Callable[
        [str, list[str], str, tk.Toplevel],
        FeedbackSaveResult,
    ],
) -> FeedbackDialogWidgets:
    """Show the feedback form while delegating candidate mutation to the controller."""
    scale = host.dpi_scale * host.zoom_factor
    field_width = 30
    window = create_toplevel(parent)
    window.title("标记反馈")
    window.transient(parent)
    window.grab_set()
    window.withdraw()
    window.configure(bg=host.colors["bg_main"])

    frame = ttk.Frame(
        window,
        style="Page.TFrame",
        padding=int(16 * scale),
    )
    frame.pack(fill="both", expand=True)
    content = ttk.Frame(frame, style="Page.TFrame")
    content.pack(anchor="w", fill="x", expand=False)
    ttk.Label(
        content,
        text=(
            f"{candidate.get('name', '未知')}｜"
            f"{candidate.get('job_name', '未知')}"
        ),
        font=(font_family, int(13 * host.font_scale)),
        foreground=host.colors["primary"],
        background=host.colors["bg_main"],
    ).pack(anchor="w", pady=(0, int(14 * scale)))
    ttk.Label(
        content,
        text="反馈状态",
        font=(font_family, int(12 * host.font_scale)),
        style="Page.TLabel",
    ).pack(anchor="w")

    status_var = tk.StringVar(
        value=(
            candidate.get("feedback_status")
            or default_status
            or status_options[0]
        )
    )
    status_combo = ttk.Combobox(
        content,
        textvariable=status_var,
        values=status_options,
        state="readonly",
        font=(font_family, int(12 * host.font_scale)),
        width=field_width,
    )
    status_combo.pack(
        anchor="w",
        fill="x",
        pady=(int(5 * scale), int(10 * scale)),
    )
    ttk.Label(
        content,
        text="结构化原因（可多选）",
        font=(font_family, int(12 * host.font_scale)),
        style="Page.TLabel",
    ).pack(anchor="w")

    reasons_frame = ttk.Frame(content, style="Page.TFrame")
    reasons_frame.pack(
        anchor="w",
        pady=(int(6 * scale), int(10 * scale)),
    )
    reason_columns = 3
    for column in range(reason_columns):
        reasons_frame.grid_columnconfigure(column, weight=0)
    selected_reasons = set(existing_reasons)
    reason_vars: dict[str, tk.BooleanVar] = {}
    reason_checkbuttons: dict[str, ttk.Checkbutton] = {}
    reason_style = ttk.Style(window)
    reason_style.configure(
        "FeedbackReason.TCheckbutton",
        font=(font_family, int(11 * host.font_scale)),
    )

    form_error_label = ttk.Label(
        frame,
        text=" ",
        font=(font_family, int(10 * host.font_scale)),
        foreground=host.colors.get("danger_text", ui_theme.DANGER_TEXT),
        background=host.colors["bg_main"],
        justify="left",
        wraplength=int(390 * scale),
    )

    def clear_form_error(_event: tk.Event | None = None) -> None:
        form_error_label.configure(text=" ")

    def show_form_error(message: str, focus_widget: tk.Misc) -> None:
        form_error_label.configure(text=message)
        try:
            focus_widget.focus_set()
        except tk.TclError:
            pass

    for index, reason in enumerate(reason_options):
        variable = tk.BooleanVar(value=reason in selected_reasons)
        checkbutton = ttk.Checkbutton(
            reasons_frame,
            text=reason,
            variable=variable,
            style="FeedbackReason.TCheckbutton",
            command=clear_form_error,
        )
        checkbutton.grid(
            row=index // reason_columns,
            column=index % reason_columns,
            sticky="w",
            padx=(0, int(10 * scale)),
            pady=int(2 * scale),
        )
        reason_vars[reason] = variable
        reason_checkbuttons[reason] = checkbutton

    status_combo.bind("<<ComboboxSelected>>", clear_form_error, add="+")
    ttk.Label(
        content,
        text="备注",
        font=(font_family, int(12 * host.font_scale)),
        style="Page.TLabel",
    ).pack(anchor="w")
    note_text = tk.Text(
        content,
        height=3,
        width=field_width,
        wrap="word",
        font=(font_family, int(12 * host.font_scale)),
        bg=host.colors["bg_card"],
        fg=host.colors["text_primary"],
        relief="solid",
        bd=1,
    )
    note_text.pack(
        anchor="w",
        fill="x",
        expand=False,
        pady=(int(5 * scale), int(18 * scale)),
    )
    if candidate.get("feedback_note"):
        note_text.insert("1.0", candidate.get("feedback_note", ""))

    button_frame = ttk.Frame(frame, style="Page.TFrame")
    button_frame.pack(anchor="center")
    form_error_label.pack(
        anchor="w",
        fill="x",
        before=button_frame,
        pady=(0, int(8 * scale)),
    )

    def close() -> None:
        try:
            window.grab_release()
        except tk.TclError:
            pass
        window.destroy()

    def save_feedback() -> None:
        clear_form_error()
        status = status_var.get().strip()
        reasons = [
            reason
            for reason, variable in reason_vars.items()
            if variable.get()
        ]
        note = note_text.get("1.0", "end").strip()
        if status not in status_options:
            show_form_error("请选择有效的反馈状态。", status_combo)
            return
        if status in {"误推", "误杀"} and not reasons:
            first_reason = next(
                iter(reason_checkbuttons.values()),
                status_combo,
            )
            show_form_error(
                "标记误推或误杀时，请至少选择一个原因。",
                first_reason,
            )
            return
        result = on_save(status, reasons, note, window)
        if result.saved:
            close()

    save_button = ttk.Button(
        button_frame,
        text="保存",
        command=save_feedback,
    )
    save_button.pack(side="left", padx=(0, int(8 * scale)))
    cancel_button = ttk.Button(button_frame, text="取消", command=close)
    cancel_button.pack(side="left")

    window.protocol("WM_DELETE_WINDOW", close)
    window.update_idletasks()
    dialog_height = max(
        int(485 * scale),
        window.winfo_reqheight() + int(12 * scale),
    )
    place_window_centered(
        window,
        int(440 * scale),
        dialog_height,
        parent=parent,
    )
    window.deiconify()

    return FeedbackDialogWidgets(
        window=window,
        status_var=status_var,
        status_combo=status_combo,
        reason_vars=reason_vars,
        reason_checkbuttons=reason_checkbuttons,
        note_text=note_text,
        error_label=form_error_label,
        save_button=save_button,
        cancel_button=cancel_button,
    )
