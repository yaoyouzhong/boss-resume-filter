"""Tk construction for the candidate contact queue workbench."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Protocol

import gui_candidate_workbench
import ui_theme
from ui_windowing import create_toplevel, place_window_centered


class ContactQueueHost(Protocol):
    """Visual attributes required to build the contact queue workbench."""

    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    font_label: Any


@dataclass(frozen=True)
class ContactQueueCallbacks:
    """Business and interaction callbacks owned by the main GUI controller."""

    start: Callable[[], None]
    pause: Callable[[], None]
    resume: Callable[[], None]
    group_selected: Callable[[], None]
    confirm_sent: Callable[[], None]
    confirm_not_sent: Callable[[], None]
    retry_failed: Callable[[], None]
    remove_selected: Callable[[], None]
    show_selected_detail: Callable[[], None]
    update_action_states: Callable[[], None]
    row_motion: Callable[[tk.Event], None]
    hide_tooltip: Callable[[tk.Event | None], None]
    context_menu: Callable[[tk.Event], None]
    select_all: Callable[[tk.Event | None], Any]
    close: Callable[[], None]


@dataclass(frozen=True)
class ContactQueueWidgets:
    """Widget references exposed through the existing BossFilterGUI aliases."""

    window: tk.Toplevel
    metric_vars: dict[str, tk.StringVar]
    summary_var: tk.StringVar
    action_scope_var: tk.StringVar
    start_button: ttk.Button
    transport_frame: ttk.Frame
    pause_button: ttk.Button
    resume_button: ttk.Button
    status_filter_var: tk.StringVar
    group_tree: ttk.Treeview
    detail_title_var: tk.StringVar
    detail_summary_var: tk.StringVar
    selection_var: tk.StringVar
    selected_action_buttons: ttk.Frame
    confirm_sent_button: ttk.Button
    confirm_not_sent_button: ttk.Button
    retry_button: ttk.Button
    remove_button: ttk.Button
    tree: ttk.Treeview


def build_contact_queue_workbench(
    host: ContactQueueHost,
    parent: tk.Misc,
    *,
    selected_group: str,
    initial_counts: Mapping[str, int],
    callbacks: ContactQueueCallbacks,
    ui_config: Mapping[str, Any],
) -> ContactQueueWidgets:
    """Build the withdrawn contact window without loading, saving, or sending data."""
    scale = host.dpi_scale * host.zoom_factor
    win = create_toplevel(parent)
    win.title("联系候选人")
    win.transient(parent)
    win.withdraw()
    win.configure(bg=host.colors["bg_main"])

    body = ttk.Frame(win, style="Page.TFrame", padding=int(16 * scale))
    body.pack(fill="both", expand=True)

    gui_candidate_workbench.create_header(
        host,
        body,
        "联系候选人",
        "确认发送范围，跟踪待核实与失败任务",
        "当前联系清单",
    )
    metric_vars = gui_candidate_workbench.create_metrics(host, body, (
        ("pending", "待发送", initial_counts.get("待发送", 0), host.colors["primary"]),
        (
            "attention",
            "需处理",
            initial_counts.get("待核实", 0) + initial_counts.get("发送失败", 0),
            host.colors["warning"],
        ),
        ("sending", "发送中", initial_counts.get("发送中", 0), host.colors["purple"]),
        ("sent", "已发送", initial_counts.get("已发送", 0), host.colors["success"]),
    ))

    header = ttk.Frame(
        body,
        style="Card.TFrame",
        padding=(int(10 * scale), int(8 * scale)),
    )
    header.pack(fill="x", pady=(0, int(10 * scale)))
    summary_var = tk.StringVar(value="")
    ttk.Label(
        header,
        textvariable=summary_var,
        font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_card"],
    ).pack(side="left", anchor="w")

    queue_actions = ttk.Frame(header, style="Card.TFrame")
    queue_actions.pack(side="right")
    action_scope_var = tk.StringVar(value="")
    ttk.Label(
        queue_actions,
        textvariable=action_scope_var,
        font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
        foreground=host.colors["text_muted"],
        background=host.colors["bg_card"],
    ).pack(side="left", padx=(0, int(10 * scale)))
    start_button = ttk.Button(
        queue_actions,
        text="开始联系",
        command=callbacks.start,
        style="Workbench.Primary.TButton",
    )
    start_button.pack(side="right")
    transport_frame = ttk.Frame(queue_actions, style="Card.TFrame")
    transport_frame.pack(side="right", padx=(0, int(6 * scale)))
    pause_button = ttk.Button(
        transport_frame,
        text="暂停",
        command=callbacks.pause,
        style="GreetQueue.Small.TButton",
        width=8,
    )
    resume_button = ttk.Button(
        transport_frame,
        text="继续",
        command=callbacks.resume,
        style="GreetQueue.Small.TButton",
        width=8,
    )

    status_filter_var = tk.StringVar(value=selected_group)
    style = ttk.Style()
    style.configure(
        "GreetQueue.Treeview",
        font=(ui_theme.FONT_FAMILY, int(11 * host.font_scale)),
        rowheight=int(ui_config["treeview_rowheight"] * scale),
    )
    style.configure(
        "GreetQueue.Treeview.Heading",
        font=(ui_theme.FONT_FAMILY, int(11 * host.font_scale), "bold"),
    )
    style.configure(
        "GreetQueue.Small.TButton",
        font=(ui_theme.FONT_FAMILY, int(11 * host.font_scale)),
        padding=(int(12 * scale), int(5 * scale)),
    )
    content = ttk.Frame(body, style="Page.TFrame")
    content.pack(fill="both", expand=True)

    nav_frame = ttk.Frame(
        content,
        style="Card.TFrame",
        padding=(int(6 * scale), int(10 * scale)),
    )
    nav_frame.pack(side="left", fill="y", padx=(0, int(8 * scale)))
    ttk.Label(
        nav_frame,
        text="按状态筛选",
        font=host.font_label,
        foreground=host.colors["text_primary"],
        background=host.colors["bg_card"],
    ).pack(anchor="w", pady=(0, int(8 * scale)))
    navigation_style = gui_candidate_workbench.navigation_style(
        host,
        scale,
        ui_config,
    )
    group_tree = ttk.Treeview(
        nav_frame,
        columns=("count",),
        show="tree",
        height=8,
        style=navigation_style,
        selectmode="browse",
    )
    group_tree.column(
        "#0",
        width=int(190 * scale),
        minwidth=int(150 * scale),
        anchor="w",
    )
    group_tree.column("count", width=0, minwidth=0, stretch=False)
    gui_candidate_workbench.apply_navigation_tags(host, group_tree)
    group_tree.pack(fill="y", expand=True)
    group_tree.bind(
        "<<TreeviewSelect>>",
        lambda _event: callbacks.group_selected(),
    )

    tree_frame = ttk.Frame(content, style="Card.TFrame")
    tree_frame.pack(side="left", fill="both", expand=True)
    detail_title_var = tk.StringVar(value="")
    detail_header = ttk.Frame(tree_frame, style="Card.TFrame")
    detail_header.grid(
        row=0,
        column=0,
        columnspan=3,
        sticky="ew",
        padx=(int(10 * scale), int(8 * scale)),
        pady=(int(10 * scale), 0),
    )
    detail_header.grid_columnconfigure(0, weight=1)
    ttk.Label(
        detail_header,
        textvariable=detail_title_var,
        font=host.font_label,
        foreground=host.colors["text_primary"],
        background=host.colors["bg_card"],
    ).grid(row=0, column=0, sticky="w", padx=(0, int(10 * scale)))
    detail_summary_var = tk.StringVar(value="")
    ttk.Label(
        detail_header,
        textvariable=detail_summary_var,
        font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_card"],
        justify="left",
        wraplength=int(820 * scale),
    ).grid(row=1, column=0, sticky="w", pady=(int(4 * scale), 0))

    selected_actions = ttk.Frame(
        tree_frame,
        style="Card.TFrame",
        padding=int(10 * scale),
    )
    selected_actions.grid(row=2, column=0, columnspan=2, sticky="ew")
    selected_actions.grid_columnconfigure(0, weight=1)
    selection_var = tk.StringVar(value="选择候选人后，可在这里处理当前状态")
    ttk.Label(
        selected_actions,
        textvariable=selection_var,
        font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_card"],
    ).grid(row=0, column=0, sticky="w")
    selected_action_buttons = ttk.Frame(selected_actions, style="Card.TFrame")
    selected_action_buttons.grid(
        row=0,
        column=1,
        sticky="e",
        padx=(int(10 * scale), 0),
    )
    confirm_sent_button = ttk.Button(
        selected_action_buttons,
        text="确认已发送",
        command=callbacks.confirm_sent,
        style="GreetQueue.Small.TButton",
    )
    confirm_not_sent_button = ttk.Button(
        selected_action_buttons,
        text="确认未发送",
        command=callbacks.confirm_not_sent,
        style="GreetQueue.Small.TButton",
    )
    retry_button = ttk.Button(
        selected_action_buttons,
        text="重试失败",
        command=callbacks.retry_failed,
        style="GreetQueue.Small.TButton",
        width=8,
    )
    remove_button = ttk.Button(
        selected_action_buttons,
        text="移除选中",
        command=callbacks.remove_selected,
        style="GreetQueue.Small.TButton",
        width=8,
    )

    columns = ("name", "job", "score", "level", "readiness", "status", "message")
    tree = ttk.Treeview(
        tree_frame,
        columns=columns,
        show="headings",
        height=8,
        selectmode="extended",
        style="GreetQueue.Treeview",
    )
    headings = {
        "name": "候选人",
        "job": "岗位",
        "score": "分数",
        "level": "推荐",
        "readiness": "发送准备",
        "status": "状态",
        "message": "最近结果",
    }
    widths = {
        "name": 90,
        "job": 150,
        "score": 55,
        "level": 70,
        "readiness": 145,
        "status": 80,
        "message": 260,
    }
    anchors = {
        "name": "center",
        "job": "w",
        "score": "center",
        "level": "center",
        "readiness": "center",
        "status": "center",
        "message": "w",
    }
    for column in columns:
        tree.heading(column, text=headings[column])
        tree.column(
            column,
            width=int(widths[column] * scale),
            minwidth=50,
            anchor=anchors[column],
        )
    scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.grid(
        row=1,
        column=0,
        sticky="nsew",
        padx=int(10 * scale),
        pady=int(10 * scale),
    )
    scroll.grid(row=1, column=1, sticky="ns", pady=int(10 * scale))
    tree_frame.grid_rowconfigure(1, weight=1)
    tree_frame.grid_columnconfigure(0, weight=1)
    tree.bind(
        "<Double-Button-1>",
        lambda _event: callbacks.show_selected_detail(),
    )
    tree.bind(
        "<<TreeviewSelect>>",
        lambda _event: callbacks.update_action_states(),
    )
    tree.bind("<Motion>", callbacks.row_motion)
    tree.bind("<Leave>", callbacks.hide_tooltip)
    tree.bind("<Button-3>", callbacks.context_menu)
    tree.bind("<Control-a>", callbacks.select_all, add="+")
    tree.bind("<Control-A>", callbacks.select_all, add="+")

    win.protocol("WM_DELETE_WINDOW", callbacks.close)
    place_window_centered(win, int(1220 * scale), int(680 * scale), parent=parent)
    return ContactQueueWidgets(
        window=win,
        metric_vars=metric_vars,
        summary_var=summary_var,
        action_scope_var=action_scope_var,
        start_button=start_button,
        transport_frame=transport_frame,
        pause_button=pause_button,
        resume_button=resume_button,
        status_filter_var=status_filter_var,
        group_tree=group_tree,
        detail_title_var=detail_title_var,
        detail_summary_var=detail_summary_var,
        selection_var=selection_var,
        selected_action_buttons=selected_action_buttons,
        confirm_sent_button=confirm_sent_button,
        confirm_not_sent_button=confirm_not_sent_button,
        retry_button=retry_button,
        remove_button=remove_button,
        tree=tree,
    )
