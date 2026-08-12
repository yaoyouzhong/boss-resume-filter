"""Tk construction and local view state for the candidate review workbench."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Protocol

import ui_theme
from ui_windowing import create_toplevel, get_windows_monitor_area, place_window_centered


class InputSupport(Protocol):
    def bind_text_context_menu(
        self,
        text_widget: tk.Text,
        *,
        editable: bool,
    ) -> None: ...


class CandidateReviewHost(Protocol):
    """Narrow GUI contract required to construct the review workbench."""

    root: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    font_label: Any
    _candidate_review_view_name: str
    input_support: InputSupport


@dataclass(frozen=True)
class CandidateReviewWidgets:
    """Widget references exposed through the existing BossFilterGUI aliases."""

    window: tk.Toplevel
    title_var: tk.StringVar
    meta_var: tk.StringVar
    position_var: tk.StringVar
    previous_button: ttk.Button
    next_button: ttk.Button
    result_var: tk.StringVar
    reason_var: tk.StringVar
    communication_var: tk.StringVar
    state_labels: tuple[tk.Label, ...]
    primary_section: ttk.Frame
    primary_label: ttk.Label
    primary_actions: ttk.Frame
    secondary_section: ttk.Frame
    secondary_actions: ttk.Frame
    view_buttons: dict[str, tk.Button]
    view_indicators: dict[str, tk.Frame]
    view_frames: dict[str, tk.Frame]
    summary_text: tk.Text
    detail_text: tk.Text


def create_review_text_area(
    host: CandidateReviewHost,
    parent: tk.Misc,
) -> tk.Text:
    """Create one read-only workbench text area with its vertical scrollbar."""
    container = ttk.Frame(parent, style="Page.TFrame")
    container.pack(
        fill="both",
        expand=True,
        padx=int(4 * host.dpi_scale),
        pady=int(8 * host.dpi_scale),
    )
    text_widget = tk.Text(
        container,
        wrap="word",
        font=(ui_theme.FONT_FAMILY, int(11 * host.font_scale)),
        bg=host.colors["bg_card"],
        fg=host.colors["text_primary"],
        relief="flat",
        padx=int(16 * host.dpi_scale),
        pady=int(12 * host.dpi_scale),
        spacing1=int(2 * host.dpi_scale),
        spacing3=int(4 * host.dpi_scale),
    )
    scrollbar = ttk.Scrollbar(container, orient="vertical", command=text_widget.yview)
    text_widget.configure(yscrollcommand=scrollbar.set)
    text_widget.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    host.input_support.bind_text_context_menu(text_widget, editable=False)
    return text_widget


def show_candidate_review_view(
    view_name: str,
    *,
    frames: Mapping[str, tk.Misc],
    buttons: Mapping[str, tk.Button],
    indicators: Mapping[str, tk.Misc],
    colors: Mapping[str, str],
) -> str:
    """Raise one content panel and refresh its flat selected state."""
    if view_name not in frames:
        return "break"
    frames[view_name].tkraise()
    for name, button in buttons.items():
        selected = name == view_name
        button.configure(
            bg=(colors["banner_info_bg"] if selected else colors["bg_card"]),
            fg=(colors["primary"] if selected else colors["text_secondary"]),
            activebackground=(
                colors["banner_info_bg"] if selected else colors["bg_hover"]
            ),
            activeforeground=(
                colors["primary"] if selected else colors["text_primary"]
            ),
        )
        indicators[name].configure(
            bg=colors["primary"] if selected else colors["bg_card"]
        )
    return "break"


def toggle_candidate_review_view(
    current_view: str,
    show_view: Callable[[str], str],
) -> str:
    """Toggle summary/detail without forcing a focus repaint."""
    target = "detail" if current_view == "summary" else "summary"
    show_view(target)
    return "break"


def replace_readonly_text(text_widget: tk.Text, text: str) -> None:
    """Replace a disabled text widget while keeping the viewport at the top."""
    text_widget.configure(state="normal")
    text_widget.delete("1.0", "end")
    text_widget.insert("1.0", text)
    text_widget.configure(state="disabled")
    text_widget.yview_moveto(0)


def build_candidate_review_workbench(
    host: CandidateReviewHost,
    *,
    navigate: Callable[[int], None],
    show_view: Callable[[str], str],
    toggle_view: Callable[[], str],
    close_window: Callable[[tk.Toplevel], None],
) -> CandidateReviewWidgets:
    """Build the withdrawn review window without reading or mutating candidate data."""
    scale = host.dpi_scale * host.zoom_factor
    win = create_toplevel(host.root)
    win.title("候选人查看与复核")
    win.transient(host.root)
    win.withdraw()
    win.configure(bg=host.colors["bg_main"])

    body = ttk.Frame(win, style="Page.TFrame", padding=int(18 * scale))
    body.pack(fill="both", expand=True)

    header = ttk.Frame(body, style="Page.TFrame")
    header.pack(fill="x", pady=(0, int(10 * scale)))
    header.grid_columnconfigure(0, weight=1)
    title_area = ttk.Frame(header, style="Page.TFrame")
    title_area.grid(row=0, column=0, sticky="ew")
    title_var = tk.StringVar()
    meta_var = tk.StringVar()
    ttk.Label(
        title_area,
        textvariable=title_var,
        font=(ui_theme.FONT_FAMILY, int(16 * host.font_scale), "bold"),
        foreground=host.colors["text_primary"],
        background=host.colors["bg_main"],
    ).pack(anchor="w")
    ttk.Label(
        title_area,
        textvariable=meta_var,
        font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_main"],
    ).pack(anchor="w", pady=(int(2 * scale), 0))

    nav = ttk.Frame(header, style="Page.TFrame")
    nav.grid(row=0, column=1, sticky="e")
    position_var = tk.StringVar()
    previous_button = ttk.Button(
        nav,
        text="上一位",
        width=8,
        command=lambda: navigate(-1),
    )
    previous_button.pack(side="left")
    ttk.Label(
        nav,
        textvariable=position_var,
        font=host.font_label,
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_main"],
        width=9,
        anchor="center",
    ).pack(side="left", padx=int(6 * scale))
    next_button = ttk.Button(
        nav,
        text="下一位",
        width=8,
        command=lambda: navigate(1),
    )
    next_button.pack(side="left")

    state_band = tk.Frame(
        body,
        bg=host.colors["bg_card"],
        highlightbackground=host.colors["border"],
        highlightthickness=1,
    )
    state_band.pack(fill="x", pady=(0, int(12 * scale)))
    result_var = tk.StringVar()
    reason_var = tk.StringVar()
    communication_var = tk.StringVar()
    state_items = (
        ("筛选结论", result_var),
        ("复核状态", reason_var),
        ("沟通状态", communication_var),
    )
    state_labels: list[tk.Label] = []
    for column, (label, variable) in enumerate(state_items):
        state_band.grid_columnconfigure(column, weight=1)
        cell = tk.Frame(state_band, bg=host.colors["bg_card"])
        cell.grid(
            row=0,
            column=column,
            sticky="ew",
            padx=int(16 * scale),
            pady=int(10 * scale),
        )
        tk.Label(
            cell,
            text=label,
            font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
            fg=host.colors["text_secondary"],
            bg=host.colors["bg_card"],
        ).pack(anchor="w")
        value_label = tk.Label(
            cell,
            textvariable=variable,
            font=(ui_theme.FONT_FAMILY, int(12 * host.font_scale), "bold"),
            fg=host.colors["text_primary"],
            bg=host.colors["bg_card"],
            anchor="w",
            justify="left",
            wraplength=max(180, int(260 * min(scale, 1.2))),
        )
        value_label.pack(anchor="w", fill="x")
        state_labels.append(value_label)

    actions = ttk.Frame(body, style="Page.TFrame")
    actions.pack(side="bottom", fill="x", pady=(int(12 * scale), 0))
    actions.grid_columnconfigure(0, weight=1)
    primary_section = ttk.Frame(actions, style="Page.TFrame")
    primary_label = ttk.Label(
        primary_section,
        text="建议下一步",
        font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_main"],
    )
    primary_label.pack(anchor="w", pady=(0, int(4 * scale)))
    primary_actions = ttk.Frame(primary_section, style="Page.TFrame")
    primary_actions.pack(anchor="w")
    primary_section.grid(row=0, column=0, sticky="w")
    ttk.Separator(actions, orient="horizontal").grid(
        row=1,
        column=0,
        sticky="ew",
        pady=int(8 * scale),
    )
    secondary_section = ttk.Frame(actions, style="Page.TFrame")
    ttk.Label(
        secondary_section,
        text="其他操作",
        font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_main"],
    ).pack(anchor="w", pady=(0, int(4 * scale)))
    secondary_actions = ttk.Frame(secondary_section, style="Page.TFrame")
    secondary_actions.pack(anchor="w")
    secondary_section.grid(row=2, column=0, sticky="w")

    switch_bar = tk.Frame(
        body,
        bg=host.colors["bg_card"],
        highlightbackground=host.colors["border"],
        highlightthickness=1,
    )
    switch_bar.pack(fill="x")
    switch_inner = tk.Frame(switch_bar, bg=host.colors["bg_card"])
    switch_inner.pack(anchor="w")
    view_buttons: dict[str, tk.Button] = {}
    view_indicators: dict[str, tk.Frame] = {}
    switch_items = (("summary", "决策摘要"), ("detail", "完整资料"))
    for view_name, label in switch_items:
        cell = tk.Frame(switch_inner, bg=host.colors["bg_card"])
        cell.pack(side="left")
        button = tk.Button(
            cell,
            text=label,
            command=lambda name=view_name: show_view(name),
            font=(ui_theme.FONT_FAMILY, max(10, int(12 * host.font_scale)), "bold"),
            relief="flat",
            bd=0,
            padx=int(18 * scale),
            pady=int(7 * scale),
            cursor="hand2",
            takefocus=1,
            highlightthickness=1,
            highlightbackground=host.colors["bg_card"],
            highlightcolor=host.colors["primary"],
        )
        button.pack(fill="x")
        indicator = tk.Frame(
            cell,
            height=max(2, int(3 * scale)),
            bg=host.colors["bg_card"],
        )
        indicator.pack(fill="x")
        view_buttons[view_name] = button
        view_indicators[view_name] = indicator
        button.bind(
            "<Enter>",
            lambda _event, name=view_name, widget=button: (
                widget.configure(bg=host.colors["bg_hover"])
                if getattr(host, "_candidate_review_view_name", "summary") != name
                else None
            ),
        )
        button.bind(
            "<Leave>",
            lambda _event: show_view(
                getattr(host, "_candidate_review_view_name", "summary")
            ),
        )
        button.bind(
            "<Return>",
            lambda _event, name=view_name: show_view(name),
        )

    review_content = tk.Frame(
        body,
        bg=host.colors["bg_card"],
        highlightbackground=host.colors["border"],
        highlightthickness=1,
    )
    review_content.pack(fill="both", expand=True)
    review_content.grid_rowconfigure(0, weight=1)
    review_content.grid_columnconfigure(0, weight=1)
    summary_panel = tk.Frame(review_content, bg=host.colors["bg_card"])
    detail_panel = tk.Frame(review_content, bg=host.colors["bg_card"])
    summary_panel.grid(row=0, column=0, sticky="nsew")
    detail_panel.grid(row=0, column=0, sticky="nsew")
    view_frames = {
        "summary": summary_panel,
        "detail": detail_panel,
    }
    summary_text = create_review_text_area(host, summary_panel)
    detail_text = create_review_text_area(host, detail_panel)

    win.protocol("WM_DELETE_WINDOW", lambda: close_window(win))
    win.bind("<Left>", lambda _event: navigate(-1))
    win.bind("<Right>", lambda _event: navigate(1))
    win.bind("<Control-Tab>", lambda _event: toggle_view())
    win.bind("<Control-Shift-Tab>", lambda _event: toggle_view())

    try:
        host.root.update_idletasks()
        root_width = host.root.winfo_width()
        root_height = host.root.winfo_height()
        monitor_area = get_windows_monitor_area(win, host.root)
        area_width = monitor_area[2] if monitor_area is not None else win.winfo_screenwidth()
        area_height = monitor_area[3] if monitor_area is not None else win.winfo_screenheight()
        preferred_width = max(700, int(root_width * 0.62))
        width = min(
            preferred_width,
            int(1040 * max(1.0, scale)),
            int(area_width * 0.9),
        )
        height = min(root_height, int(area_height * 0.9))
    except tk.TclError:
        width, height = 980, 900
    place_window_centered(win, width, height, parent=host.root)

    return CandidateReviewWidgets(
        window=win,
        title_var=title_var,
        meta_var=meta_var,
        position_var=position_var,
        previous_button=previous_button,
        next_button=next_button,
        result_var=result_var,
        reason_var=reason_var,
        communication_var=communication_var,
        state_labels=tuple(state_labels),
        primary_section=primary_section,
        primary_label=primary_label,
        primary_actions=primary_actions,
        secondary_section=secondary_section,
        secondary_actions=secondary_actions,
        view_buttons=view_buttons,
        view_indicators=view_indicators,
        view_frames=view_frames,
        summary_text=summary_text,
        detail_text=detail_text,
    )
