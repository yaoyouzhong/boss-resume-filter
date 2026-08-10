"""Tk form dialogs for candidate blacklist, follow-up, and feedback state."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Protocol

import ui_theme
from ui_windowing import place_window_centered


class CandidateStateDialogHost(Protocol):
    """Visual host contract shared by candidate-state form dialogs."""

    colors: Mapping[str, str]
    dpi_scale: float
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

    window = tk.Toplevel(parent)
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
