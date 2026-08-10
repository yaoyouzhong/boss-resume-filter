"""Tk confirmation dialogs for destructive local-data maintenance actions."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from tkinter import ttk
from typing import Protocol

import ui_theme
from ui_windowing import place_window_centered


class DataMaintenanceDialogHost(Protocol):
    """Visual host contract used by data-maintenance dialogs."""

    colors: Mapping[str, str]
    dpi_scale: float
    font_scale: float
    zoom_factor: float


@dataclass(frozen=True)
class ClearCandidatesDialogWidgets:
    """Clear-candidate dialog references used by focused Tk tests."""

    window: tk.Toplevel
    choice_var: tk.StringVar
    keep_greeted_var: tk.BooleanVar
    current_job_radio: ttk.Radiobutton
    all_jobs_radio: ttk.Radiobutton
    keep_greeted_checkbutton: ttk.Checkbutton
    confirm_button: ttk.Button
    cancel_button: ttk.Button


def show_clear_candidates_dialog(
    host: DataMaintenanceDialogHost,
    parent: tk.Misc,
    *,
    font_family: str,
    selected_job: str,
    is_all_jobs: bool,
    greeted_count: int,
    on_confirm: Callable[[str, bool], None],
) -> ClearCandidatesDialogWidgets:
    """Show clear scope/retention choices and return them to the controller."""
    scale = host.dpi_scale * host.zoom_factor
    dialog_font_scale = host.font_scale * 0.88
    window = tk.Toplevel(parent)
    window.title("清空候选人")
    window.transient(parent)
    window.grab_set()
    window.resizable(False, False)
    window.configure(background=host.colors["bg_main"])
    window.withdraw()
    place_window_centered(
        window,
        max(460, int(460 * scale)),
        max(300, int(300 * scale)),
        parent=parent,
    )

    radio_font = (font_family, int(14 * dialog_font_scale))
    style = ttk.Style(window)
    style.configure(
        "ClearDialog.TLabel",
        background=host.colors["bg_main"],
    )
    style.configure(
        "ClearDialog.TFrame",
        background=host.colors["bg_main"],
    )
    style.configure(
        "ClearDialog.TRadiobutton",
        font=radio_font,
        background=host.colors["bg_main"],
    )
    style.configure(
        "ClearDialog.TCheckbutton",
        font=radio_font,
        background=host.colors["bg_main"],
    )
    ttk.Label(
        window,
        text="清空候选人数据",
        font=(font_family, int(16 * dialog_font_scale)),
        foreground=host.colors["danger"],
        style="ClearDialog.TLabel",
    ).pack(pady=(int(20 * scale), int(10 * scale)))

    choice_var = tk.StringVar(value="all" if is_all_jobs else "current")
    radio_frame = ttk.Frame(window, style="ClearDialog.TFrame")
    radio_frame.pack(fill="x", padx=int(30 * scale))
    current_job_radio = ttk.Radiobutton(
        radio_frame,
        text=f"清空当前岗位数据（{selected_job}）",
        variable=choice_var,
        value="current",
        style="ClearDialog.TRadiobutton",
    )
    current_job_radio.pack(anchor="w", pady=int(5 * scale))
    if is_all_jobs:
        current_job_radio.configure(state="disabled")
    all_jobs_radio = ttk.Radiobutton(
        radio_frame,
        text="清空全部数据（所有岗位）",
        variable=choice_var,
        value="all",
        style="ClearDialog.TRadiobutton",
    )
    all_jobs_radio.pack(anchor="w", pady=int(5 * scale))

    ttk.Separator(window, orient="horizontal").pack(
        fill="x",
        padx=int(30 * scale),
        pady=(int(10 * scale), int(6 * scale)),
    )
    keep_greeted_var = tk.BooleanVar(value=True)
    checkbox_frame = ttk.Frame(window, style="ClearDialog.TFrame")
    checkbox_frame.pack(
        fill="x",
        padx=int(30 * scale),
        pady=(int(12 * scale), 0),
    )
    checkbox_text = (
        f"保留已打招呼的候选人（{greeted_count} 人）"
        if greeted_count > 0
        else "保留已打招呼的候选人（无）"
    )
    keep_greeted_checkbutton = ttk.Checkbutton(
        checkbox_frame,
        text=checkbox_text,
        variable=keep_greeted_var,
        style="ClearDialog.TCheckbutton",
    )
    keep_greeted_checkbutton.pack(anchor="w")
    if greeted_count == 0:
        keep_greeted_checkbutton.configure(state="disabled")
        keep_greeted_var.set(False)

    ttk.Label(
        window,
        text="候选人数据会自动备份；无人引用的受管简历副本将一并删除",
        font=(font_family, int(13 * dialog_font_scale)),
        foreground=host.colors.get("text_muted", ui_theme.TEXT_MUTED),
        style="ClearDialog.TLabel",
    ).pack(pady=(int(12 * scale), 0))
    button_frame = ttk.Frame(window, style="ClearDialog.TFrame")
    button_frame.pack(pady=int(15 * scale))

    def close() -> None:
        try:
            window.grab_release()
        except tk.TclError:
            pass
        window.destroy()

    def confirm() -> None:
        choice = choice_var.get()
        keep_greeted = keep_greeted_var.get()
        close()
        on_confirm(choice, keep_greeted)

    style.configure(
        "ClearDialog.Danger.TButton",
        font=(font_family, int(11 * host.font_scale)),
        padding=(int(14 * scale), int(5 * scale)),
        background=host.colors["danger"],
        foreground=host.colors["bg_card"],
    )
    style.map(
        "ClearDialog.Danger.TButton",
        background=[
            (
                "pressed",
                host.colors.get("danger_deep", ui_theme.DANGER_DEEP),
            ),
            (
                "active",
                host.colors.get("danger_text", ui_theme.DANGER_TEXT),
            ),
        ],
    )
    confirm_button = ttk.Button(
        button_frame,
        text="清空所选数据",
        command=confirm,
        style="ClearDialog.Danger.TButton",
    )
    confirm_button.pack(side="left", padx=int(8 * scale))
    cancel_button = ttk.Button(
        button_frame,
        text="取消",
        command=close,
    )
    cancel_button.pack(side="left", padx=int(8 * scale))

    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Return>", lambda _event: None)
    cancel_button.focus_set()
    window.deiconify()
    return ClearCandidatesDialogWidgets(
        window=window,
        choice_var=choice_var,
        keep_greeted_var=keep_greeted_var,
        current_job_radio=current_job_radio,
        all_jobs_radio=all_jobs_radio,
        keep_greeted_checkbutton=keep_greeted_checkbutton,
        confirm_button=confirm_button,
        cancel_button=cancel_button,
    )
