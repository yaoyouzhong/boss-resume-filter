"""Tk widget construction and local visual state for the home workbench."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Mapping
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Callable, Protocol


class NavigationShell(Protocol):
    def request_sidebar_page(
        self,
        page_index: int,
        on_ready: Callable[[], None] | None = None,
    ) -> None: ...
    def schedule_page_width_policy(self) -> None: ...


class HomePageHost(Protocol):
    """Narrow host contract required to build the home page."""

    pages_frame: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    home_fonts: Mapping[str, Any]
    app_shell: NavigationShell

    def refresh_home_stats(self) -> None: ...
    def show_stat_detail(self, stat_type: str) -> None: ...
    def on_home_task_click(self, task_type: str) -> None: ...
    def on_home_health_click(self, status_type: str) -> None: ...
    def import_external_candidate(self) -> None: ...
    def open_home_data_maintenance(self) -> None: ...
    def open_home_system_settings(self) -> None: ...


@dataclass(frozen=True)
class HomeTaskWidgets:
    """Widgets needed to update one task row without rebuilding it."""

    key: str
    row: tk.Frame
    marker: tk.Canvas
    marker_id: int
    marker_dot_id: int
    title_label: tk.Label
    note_label: tk.Label
    value_label: tk.Label
    unit_label: tk.Label
    priority_tag: tk.Label
    action_box: tk.Frame
    action_label: tk.Label
    background_widgets: tuple[tk.Widget, ...]
    interactive_widgets: tuple[tk.Widget, ...]
    enabled_state: dict[str, bool]


@dataclass(frozen=True)
class HomeHealthWidgets:
    """Widgets needed to update one readiness row."""

    row: tk.Frame
    dot: tk.Canvas
    dot_id: int
    dot_warning_id: int
    dot_text_id: int
    status_label: tk.Label
    action_label: tk.Label
    background_widgets: tuple[tk.Widget, ...]


@dataclass(frozen=True)
class HomeLayoutWidgets:
    """Page-local references used only by responsive layout support."""

    header: tk.Frame
    header_controls: tk.Frame
    workspace: tk.Frame
    action_panel: tk.Frame
    readiness_panel: tk.Frame
    action_header: tk.Frame
    task_grid: tk.Frame
    readiness_heading: tk.Frame
    health_list: tk.Frame
    candidate_strip: tk.Frame
    tools_band: tk.Frame
    tools_content: tk.Frame
    maintenance_frame: tk.Frame
    tool_tiles: tuple[tk.Frame, ...]


@dataclass(frozen=True)
class HomePageWidgets:
    """Widget references consumed by home refresh and navigation logic."""

    page: ttk.Frame
    job_var: tk.StringVar
    job_combo: ttk.Combobox
    stats_vars: dict[str, tk.StringVar]
    stats_labels: dict[str, tuple[tk.Label, str]]
    task_vars: dict[str, tk.StringVar]
    task_action_vars: dict[str, tk.StringVar]
    task_labels: dict[str, tuple[tk.Label, tk.Label, str]]
    task_widgets: dict[str, HomeTaskWidgets]
    task_total_var: tk.StringVar
    task_headline_prefix_var: tk.StringVar
    task_headline_suffix_var: tk.StringVar
    health_vars: dict[str, tk.StringVar]
    health_note_vars: dict[str, tk.StringVar]
    health_labels: dict[str, tk.Label]
    health_widgets: dict[str, HomeHealthWidgets]
    readiness_title_var: tk.StringVar
    readiness_note_var: tk.StringVar
    readiness_banner: tk.Frame
    readiness_rail: tk.Frame
    readiness_icon_label: tk.Label
    readiness_title_label: tk.Label
    readiness_note_label: tk.Label
    scan_summary_var: tk.StringVar
    scan_status_var: tk.StringVar
    scan_status_label: tk.Label
    layout: HomeLayoutWidgets


def _bind_surface(
    frame: tk.Frame,
    widgets: tuple[tk.Widget, ...],
    command: Any,
    *,
    normal: str,
    hover: str,
    focus: str,
    border: str | None = None,
) -> None:
    """Give one explicit action surface consistent mouse and keyboard behavior."""

    def recolor(color: str) -> None:
        for widget in widgets:
            try:
                widget.configure(background=color)
            except tk.TclError:
                pass

    for widget in widgets:
        widget.configure(cursor="hand2")
        widget.bind("<Button-1>", lambda _event: command())
        widget.bind("<Enter>", lambda _event: recolor(hover))
        widget.bind("<Leave>", lambda _event: recolor(normal))
    normal_border = border or normal
    frame.configure(
        takefocus=True,
        highlightthickness=1,
        highlightbackground=normal_border,
    )
    frame.bind("<Return>", lambda _event: command())
    frame.bind("<space>", lambda _event: command())
    frame.bind("<FocusIn>", lambda _event: frame.configure(highlightbackground=focus))
    frame.bind(
        "<FocusOut>",
        lambda _event: frame.configure(highlightbackground=normal_border),
    )


def _build_task_row(
    parent: tk.Misc,
    host: HomePageHost,
    scale: float,
    *,
    key: str,
    title: str,
    note: str,
    value_var: tk.StringVar,
    action_var: tk.StringVar,
    first: bool,
    last: bool,
) -> HomeTaskWidgets:
    colors = host.colors
    fonts = host.home_fonts
    row = tk.Frame(
        parent,
        background=colors["home_surface"],
        padx=0,
        pady=0,
    )
    row.grid_columnconfigure(1, weight=1)

    marker_cell = tk.Frame(
        row,
        width=int(28 * scale),
        background=colors["home_surface"],
    )
    marker_cell.grid(row=0, column=0, rowspan=2, sticky="ns")
    marker_cell.grid_propagate(False)
    if not first:
        tk.Frame(
            marker_cell,
            width=max(2, int(2 * scale)),
            background=colors["home_border"],
        ).place(relx=0.5, y=0, anchor="n", relheight=0.42)
    if not last:
        tk.Frame(
            marker_cell,
            width=max(2, int(2 * scale)),
            background=colors["home_border"],
        ).place(relx=0.5, rely=0.58, anchor="n", relheight=0.42)
    marker = tk.Canvas(
        marker_cell,
        width=int(16 * scale),
        height=int(16 * scale),
        background=colors["home_surface"],
        highlightthickness=0,
    )
    marker.place(relx=0.5, rely=0.5, anchor="center")
    marker_id = marker.create_oval(
        int(2 * scale),
        int(2 * scale),
        int(14 * scale),
        int(14 * scale),
        fill=colors["home_surface"],
        outline=colors["home_border"],
        width=max(1, int(2 * scale)),
    )
    marker_dot_id = marker.create_oval(
        int(5 * scale),
        int(5 * scale),
        int(11 * scale),
        int(11 * scale),
        fill=colors["home_surface"],
        outline="",
    )

    text_box = tk.Frame(row, background=colors["home_surface"])
    text_box.grid(
        row=0,
        column=1,
        rowspan=2,
        sticky="nsew",
        padx=(int(14 * scale), int(11 * scale)),
        pady=int(12 * scale),
    )
    title_line = tk.Frame(text_box, background=colors["home_surface"])
    title_line.pack(fill="x")
    title_label = tk.Label(
        title_line,
        text=title,
        font=fonts["task_title"],
        foreground=colors["home_ink"],
        background=colors["home_surface"],
        anchor="w",
    )
    title_label.pack(side="left")
    priority_tag = tk.Label(
        title_line,
        text="优先处理",
        font=fonts["micro"],
        foreground=colors["home_warning"],
        background=colors["home_warning_tint"],
        highlightbackground="#F0D4AA",
        highlightthickness=1,
        padx=int(7 * scale),
        pady=int(2 * scale),
    )
    note_label = tk.Label(
        text_box,
        text=note,
        font=fonts["micro"],
        foreground=colors["home_secondary"],
        background=colors["home_surface"],
        anchor="w",
        justify="left",
    )
    note_label.pack(fill="x", pady=(int(7 * scale), 0))
    count_box = tk.Frame(row, background=colors["home_surface"])
    count_box.grid(
        row=0,
        column=2,
        rowspan=2,
        sticky="e",
        padx=(0, int(11 * scale)),
    )
    value_label = tk.Label(
        count_box,
        textvariable=value_var,
        font=fonts["task_number"],
        foreground=colors["home_secondary"],
        background=colors["home_surface"],
        anchor="e",
    )
    value_label.pack(side="left")
    unit_label = tk.Label(
        count_box,
        text="人",
        font=fonts["micro"],
        foreground=colors["home_secondary"],
        background=colors["home_surface"],
    )
    unit_label.pack(side="left", padx=(int(2 * scale), 0), pady=(int(10 * scale), 0))
    action_box = tk.Frame(
        row,
        background=colors["home_surface"],
        highlightbackground=colors["home_surface"],
        highlightthickness=1,
        width=int(128 * scale),
        height=int(38 * scale),
    )
    action_box.grid(row=0, column=3, rowspan=2, sticky="e")
    action_box.grid_propagate(False)
    action_label = tk.Label(
        action_box,
        textvariable=action_var,
        font=fonts["action"],
        foreground=colors["home_secondary"],
        background=colors["home_surface"],
    )
    action_label.place(relx=0.5, rely=0.5, anchor="center")
    backgrounds = (
        row,
        marker_cell,
        marker,
        text_box,
        title_line,
        title_label,
        note_label,
        count_box,
        value_label,
        unit_label,
    )
    interactive = backgrounds + (action_box, action_label)
    state = {
        "enabled": False,
        "priority": False,
        "background": colors["home_surface"],
    }

    def run() -> None:
        if state["enabled"]:
            host.on_home_task_click(key)

    def hover(color: str) -> None:
        if not state["enabled"] or state["priority"]:
            return
        for widget in backgrounds:
            try:
                widget.configure(background=color)
            except tk.TclError:
                pass

    for widget in interactive:
        widget.bind("<Button-1>", lambda _event: run())
        widget.bind("<Enter>", lambda _event: hover(colors["home_surface_quiet"]))
        widget.bind("<Leave>", lambda _event: hover(colors["home_surface"]))
    row.configure(
        takefocus=True,
        highlightthickness=1,
        highlightbackground=colors["home_surface"],
    )
    row.bind("<Return>", lambda _event: run())
    row.bind("<space>", lambda _event: run())
    row.bind(
        "<FocusIn>",
        lambda _event: row.configure(highlightbackground=colors["home_primary"]),
    )
    row.bind(
        "<FocusOut>",
        lambda _event: row.configure(highlightbackground=state["background"]),
    )
    return HomeTaskWidgets(
        key,
        row,
        marker,
        marker_id,
        marker_dot_id,
        title_label,
        note_label,
        value_label,
        unit_label,
        priority_tag,
        action_box,
        action_label,
        backgrounds,
        interactive,
        state,
    )


def update_task_widget(
    widgets: HomeTaskWidgets,
    colors: Mapping[str, str],
    *,
    count: int,
    priority: bool,
    error: bool = False,
    error_note: str = "任务数据暂时不可用，请稍后刷新",
) -> None:
    """Apply zero, secondary, priority, or unavailable state to one task row."""
    enabled = count > 0 and not error
    widgets.enabled_state.update(enabled=enabled, priority=priority)
    for widget in widgets.interactive_widgets:
        widget.configure(cursor="hand2" if enabled else "arrow")
    if error:
        background, accent = colors["home_danger_tint"], colors["home_danger"]
    elif priority:
        background, accent = colors["home_active_row"], colors["home_primary"]
    else:
        background = colors["home_surface"]
        accent = colors["home_primary"] if enabled else colors["home_muted"]
    widgets.enabled_state["background"] = background
    widgets.row.configure(highlightbackground=background)
    for widget in widgets.background_widgets:
        try:
            widget.configure(background=background)
        except tk.TclError:
            pass
    widgets.marker.itemconfigure(
        widgets.marker_id,
        fill=background,
        outline=accent if enabled or error else colors["home_border"],
    )
    widgets.marker.itemconfigure(
        widgets.marker_dot_id,
        fill=accent if enabled or error else background,
    )
    widgets.value_label.configure(
        foreground=colors["home_danger"] if error else colors["home_ink"]
    )
    widgets.unit_label.configure(
        foreground=colors["home_danger"] if error else colors["home_secondary"]
    )
    widgets.title_label.configure(foreground=accent if error else colors["home_ink"])
    if priority and enabled:
        widgets.priority_tag.pack(side="left", padx=(9, 0))
    else:
        widgets.priority_tag.pack_forget()
    if enabled:
        widgets.action_box.configure(
            background=colors["home_primary_tint"],
            highlightbackground=colors["home_primary_border"],
        )
        widgets.action_label.configure(
            background=colors["home_primary_tint"],
            foreground=colors["home_primary"],
        )
    else:
        widgets.action_box.configure(background=background, highlightbackground=background)
        widgets.action_label.configure(background=background, foreground=accent)

    if error:
        widgets.note_label.configure(text=error_note)
        widgets.unit_label.configure(text="")
    else:
        widgets.unit_label.configure(text="人")
        descriptions = {
            "pending_verification": (
                f"{count} 位候选人的发送结果尚未确认，先核实以避免重复联系"
                if count
                else "当前没有发送结果需要确认"
            ),
            "pending_review": (
                f"{count} 位候选人需要人工判断，确认后才能进入联系流程"
                if count
                else "当前没有候选人需要人工判断"
            ),
            "pending_contact": (
                f"{count} 位候选人已符合条件，需按渠道联系或加入联系清单"
                if count
                else "当前没有候选人等待联系"
            ),
        }
        widgets.note_label.configure(text=descriptions.get(widgets.key, ""))


def update_health_widget(
    widgets: HomeHealthWidgets,
    colors: Mapping[str, str],
    *,
    tone: str,
    action: str,
) -> None:
    """Apply semantic state to a health row; only real actions remain clickable."""
    accent = {
        "success": colors["home_success"],
        "warning": colors["home_warning"],
        "danger": colors["home_danger"],
    }.get(tone, colors["home_secondary"])
    background = colors["home_surface"]
    for widget in widgets.background_widgets:
        try:
            widget.configure(background=background)
        except tk.TclError:
            pass
    icon_fill = {
        "success": colors["home_success_tint"],
        "warning": colors["home_warning_tint"],
        "danger": colors["home_danger_tint"],
    }.get(tone, colors["home_surface_quiet"])
    widgets.dot.itemconfigure(widgets.dot_id, fill=icon_fill, outline=accent)
    is_warning_shape = tone in {"warning", "danger"}
    widgets.dot.itemconfigure(
        widgets.dot_id,
        state="hidden" if is_warning_shape else "normal",
    )
    widgets.dot.itemconfigure(
        widgets.dot_warning_id,
        fill=icon_fill,
        outline=accent,
        state="normal" if is_warning_shape else "hidden",
    )
    widgets.dot.itemconfigure(
        widgets.dot_text_id,
        text={"success": "✓", "warning": "!", "danger": "×"}.get(tone, "·"),
        fill=accent,
    )
    widgets.status_label.configure(foreground=accent)
    if action:
        widgets.action_label.configure(
            text=action,
            background=background,
            foreground=accent,
            cursor="hand2",
            takefocus=True,
            highlightthickness=1,
            highlightbackground=background,
        )
        widgets.action_label.pack(side="left", padx=(8, 0))
    else:
        widgets.action_label.configure(takefocus=False)
        widgets.action_label.pack_forget()


def update_readiness_banner(
    page_widgets: HomePageWidgets,
    colors: Mapping[str, str],
    tone: str,
) -> None:
    accent = {
        "success": colors["home_success"],
        "warning": colors["home_warning"],
        "danger": colors["home_danger"],
    }.get(tone, colors["home_secondary"])
    background = {
        "success": colors["home_success_tint"],
        "warning": colors["home_warning_tint"],
        "danger": colors["home_danger_tint"],
    }.get(tone, colors["home_surface_quiet"])
    page_widgets.readiness_banner.configure(background=background)
    page_widgets.readiness_rail.configure(background=accent)
    page_widgets.readiness_icon_label.configure(
        text={"success": "✓", "warning": "!", "danger": "×"}.get(tone, "·"),
        background=background,
        foreground=accent,
    )
    page_widgets.readiness_title_label.configure(background=background)
    page_widgets.readiness_note_label.configure(background=background)


def build_home_page(
    host: HomePageHost,
    ui_config: Mapping[str, Any],
    *,
    run_page_index: int,
    result_page_index: int,
    config_page_index: int,
    education_page_index: int,
) -> HomePageWidgets:
    """Build the approved action-first home workbench without reading business data."""
    scale = host.dpi_scale * host.zoom_factor

    def px(value: float) -> int:
        return max(1, int(round(value * scale)))

    colors, fonts = host.colors, host.home_fonts
    # The page stack applies viewport padding to each cached top-level page.
    # Keep this outer container as ttk so it supports that shared padding policy;
    # all inner home surfaces remain plain Tk frames for exact background colors.
    page = ttk.Frame(host.pages_frame, style="Home.Page.TFrame")
    page.grid_columnconfigure(0, weight=1)
    page.grid_rowconfigure(0, minsize=px(101))
    page.grid_rowconfigure(1, minsize=px(614))
    page.grid_rowconfigure(2, minsize=px(88))
    page.grid_rowconfigure(3, minsize=px(92))
    page.grid_rowconfigure(4, weight=1)

    header = tk.Frame(page, background=colors["home_bg"])
    header.grid(row=0, column=0, sticky="new", pady=(0, px(16)))
    header.grid_columnconfigure(0, weight=1)
    title_box = tk.Frame(header, background=colors["home_bg"])
    title_box.grid(row=0, column=0, sticky="sw")
    tk.Label(
        title_box,
        text="招聘工作台",
        font=fonts["title"],
        foreground=colors["home_ink"],
        background=colors["home_bg"],
    ).pack(anchor="w")
    scan_row = tk.Frame(title_box, background=colors["home_bg"])
    scan_row.pack(anchor="w", pady=(px(6), 0))
    tk.Label(
        scan_row,
        text="✓",
        font=fonts["micro"],
        foreground=colors["home_success"],
        background=colors["home_success_tint"],
        padx=px(3),
        pady=1,
    ).pack(side="left", padx=(0, px(8)))
    tk.Label(
        scan_row,
        text="最近扫描",
        font=fonts["meta"],
        foreground=colors["home_secondary"],
        background=colors["home_bg"],
    ).pack(side="left")
    scan_summary_var = tk.StringVar(value="暂无记录 · 完成一次筛选后显示")
    tk.Label(
        scan_row,
        textvariable=scan_summary_var,
        font=fonts["meta"],
        foreground=colors["home_ink"],
        background=colors["home_bg"],
    ).pack(side="left", padx=(px(10), 0))
    scan_status_var = tk.StringVar(value="")
    scan_status_label = tk.Label(
        scan_row,
        textvariable=scan_status_var,
        font=fonts["meta"],
        foreground=colors["home_secondary"],
        background=colors["home_bg"],
    )
    scan_status_label.pack(side="left", padx=(px(10), 0))

    controls = tk.Frame(header, background=colors["home_bg"])
    controls.grid(
        row=0,
        column=1,
        sticky="se",
        padx=(px(24), 0),
        pady=(px(7), 0),
    )
    # Keep common job names identifiable without letting the selector dominate
    # the header action group.
    for column, width in enumerate((270, 98, 126)):
        controls.grid_columnconfigure(column, minsize=px(width))
    controls.grid_rowconfigure(1, minsize=px(40))
    tk.Label(
        controls,
        text="岗位范围",
        font=fonts["micro"],
        foreground=colors["home_secondary"],
        background=colors["home_bg"],
    ).grid(row=0, column=0, sticky="w", pady=(0, px(4)))
    job_var = tk.StringVar(value="全部岗位")
    job_combo = ttk.Combobox(
        controls,
        textvariable=job_var,
        values=["全部岗位"],
        width=26,
        state="readonly",
        font=fonts["body"],
    )
    job_combo.grid(row=1, column=0, sticky="nsew", padx=(0, px(10)))
    ttk.Button(
        controls,
        text="岗位配置",
        style="Home.Secondary.TButton",
        command=lambda: host.app_shell.request_sidebar_page(config_page_index),
    ).grid(row=1, column=1, sticky="nsew", padx=(0, px(10)))
    ttk.Button(
        controls,
        text="开始筛选",
        style="Home.Primary.TButton",
        command=lambda: host.app_shell.request_sidebar_page(run_page_index),
    ).grid(row=1, column=2, sticky="nsew")
    job_combo.bind("<<ComboboxSelected>>", lambda _event: host.refresh_home_stats())

    workspace = tk.Frame(page, background=colors["home_bg"])
    workspace.grid(row=1, column=0, sticky="nsew", pady=(0, px(16)))
    workspace.grid_columnconfigure(0, weight=1)
    workspace.grid_columnconfigure(1, minsize=px(330))
    workspace.grid_rowconfigure(0, weight=1)

    action_panel = tk.Frame(
        workspace,
        background=colors["home_surface"],
        highlightbackground=colors["home_border"],
        highlightthickness=1,
    )
    action_panel.grid(row=0, column=0, sticky="nsew", padx=(0, px(16)))
    action_panel.grid_columnconfigure(0, weight=1)
    action_panel.grid_rowconfigure(2, weight=1)
    action_header = tk.Frame(
        action_panel,
        background=colors["home_surface"],
        padx=px(28),
        pady=px(14),
    )
    action_header.grid(row=0, column=0, sticky="ew")
    tk.Label(
        action_header,
        text="今日待办",
        font=fonts["eyebrow"],
        foreground=colors["home_secondary"],
        background=colors["home_surface"],
    ).pack(anchor="w")
    headline = tk.Frame(action_header, background=colors["home_surface"])
    headline.pack(anchor="w", pady=(px(6), px(7)))
    task_headline_prefix_var = tk.StringVar(value="今天没有待处理事项")
    task_total_var = tk.StringVar(value="")
    task_headline_suffix_var = tk.StringVar(value="")
    tk.Label(
        headline,
        textvariable=task_headline_prefix_var,
        font=fonts["hero"],
        foreground=colors["home_ink"],
        background=colors["home_surface"],
    ).pack(side="left")
    tk.Label(
        headline,
        textvariable=task_total_var,
        font=fonts["hero_number"],
        foreground=colors["home_primary"],
        background=colors["home_surface"],
    ).pack(side="left", padx=(px(5), px(5)))
    tk.Label(
        headline,
        textvariable=task_headline_suffix_var,
        font=fonts["hero"],
        foreground=colors["home_ink"],
        background=colors["home_surface"],
    ).pack(side="left")
    tk.Label(
        action_header,
        text="每位候选人只计入当前最优先的一项任务",
        font=fonts["body"],
        foreground=colors["home_secondary"],
        background=colors["home_surface"],
    ).pack(anchor="w")
    tk.Frame(
        action_panel,
        height=1,
        background=colors["home_border_soft"],
    ).grid(row=1, column=0, sticky="ew")

    task_grid = tk.Frame(
        action_panel,
        background=colors["home_surface"],
        padx=px(22),
    )
    task_grid.grid(row=2, column=0, sticky="nsew")
    task_grid.grid_columnconfigure(0, weight=1)
    for row_index, weight in enumerate((23, 90, 62)):
        task_grid.grid_rowconfigure(
            row_index,
            weight=weight,
            minsize=px(94),
        )
    task_vars: dict[str, tk.StringVar] = {}
    task_action_vars: dict[str, tk.StringVar] = {}
    task_labels: dict[str, tuple[tk.Label, tk.Label, str]] = {}
    task_widgets: dict[str, HomeTaskWidgets] = {}
    task_rows = (
        ("pending_verification", "待核实", "发送结果尚未确认，先核实以避免重复联系"),
        ("pending_review", "待复核", "需要人工判断，确认后才能进入联系流程"),
        ("pending_contact", "待联系", "已符合条件，需按渠道联系或加入联系清单"),
    )
    for row_index, (key, title, note) in enumerate(task_rows):
        value_var = tk.StringVar(value="0")
        action_var = tk.StringVar(value="当前无需处理")
        task_vars[key] = value_var
        task_action_vars[key] = action_var
        widgets = _build_task_row(
            task_grid,
            host,
            scale,
            key=key,
            title=title,
            note=note,
            value_var=value_var,
            action_var=action_var,
            first=row_index == 0,
            last=row_index == len(task_rows) - 1,
        )
        widgets.row.grid(row=row_index, column=0, sticky="nsew")
        if row_index:
            tk.Frame(
                widgets.row,
                height=1,
                background=colors["home_border_soft"],
            ).place(x=0, y=0, relwidth=1)
        task_widgets[key] = widgets
        task_labels[key] = (widgets.value_label, widgets.action_label, "home_primary")

    readiness_panel = tk.Frame(
        workspace,
        background=colors["home_surface"],
        highlightbackground=colors["home_border"],
        highlightthickness=1,
    )
    readiness_panel.grid(row=0, column=1, sticky="nsew")
    readiness_panel.grid_columnconfigure(0, weight=1)
    readiness_panel.grid_rowconfigure(4, weight=1)
    readiness_heading = tk.Frame(
        readiness_panel,
        background=colors["home_surface"],
        padx=px(22),
        height=px(62),
    )
    readiness_heading.grid(row=0, column=0, sticky="ew")
    readiness_heading.grid_propagate(False)
    tk.Label(
        readiness_heading,
        text="运行准备",
        font=fonts["card_heading"],
        foreground=colors["home_ink"],
        background=colors["home_surface"],
    ).place(x=0, rely=0.5, anchor="w")
    tk.Frame(
        readiness_panel,
        height=1,
        background=colors["home_border_soft"],
    ).grid(row=1, column=0, sticky="ew")
    readiness_title_var = tk.StringVar(value="正在检查运行条件")
    readiness_note_var = tk.StringVar(value="结果会在几秒内自动更新")
    readiness_banner = tk.Frame(
        readiness_panel,
        background=colors["home_surface_quiet"],
        padx=px(15),
        pady=px(8),
    )
    readiness_banner.grid(
        row=2,
        column=0,
        sticky="ew",
        padx=px(20),
        pady=(px(18), px(16)),
    )
    readiness_rail = tk.Frame(
        readiness_banner,
        width=px(3),
        background=colors["home_warning"],
    )
    readiness_rail.place(x=-px(15), y=-px(8), relheight=1)
    readiness_banner.grid_columnconfigure(1, weight=1)
    readiness_icon_label = tk.Label(
        readiness_banner,
        text="!",
        font=fonts["action"],
        foreground=colors["home_secondary"],
        background=colors["home_surface_quiet"],
        width=2,
    )
    readiness_icon_label.grid(
        row=0,
        column=0,
        rowspan=2,
        sticky="n",
        padx=(0, px(10)),
    )
    readiness_title_label = tk.Label(
        readiness_banner,
        textvariable=readiness_title_var,
        font=fonts["card_heading"],
        foreground=colors["home_ink"],
        background=colors["home_surface_quiet"],
        anchor="w",
        justify="left",
    )
    readiness_title_label.grid(row=0, column=1, sticky="ew")
    readiness_note_label = tk.Label(
        readiness_banner,
        textvariable=readiness_note_var,
        font=fonts["meta"],
        foreground=colors["home_secondary"],
        background=colors["home_surface_quiet"],
        anchor="w",
        justify="left",
    )
    readiness_note_label.grid(row=1, column=1, sticky="ew", pady=(px(4), 0))

    health_list = tk.Frame(readiness_panel, background=colors["home_surface"])
    health_list.grid(row=3, column=0, sticky="new", padx=px(20))
    health_list.grid_columnconfigure(0, weight=1)
    health_vars: dict[str, tk.StringVar] = {}
    health_note_vars: dict[str, tk.StringVar] = {}
    health_labels: dict[str, tk.Label] = {}
    health_widgets: dict[str, HomeHealthWidgets] = {}
    health_rows = (
        ("api", "API Key", "正在读取本机安全凭据"),
        ("browser", "Chrome", "正在检查本机 Chrome"),
        ("storage", "数据存储", "正在读取候选人数据"),
    )
    for row_index, (key, title, initial_note) in enumerate(health_rows):
        health_list.grid_rowconfigure(row_index, minsize=px(66))
        row = tk.Frame(
            health_list,
            background=colors["home_surface"],
            padx=px(4),
            pady=px(11),
        )
        row.grid(row=row_index, column=0, sticky="nsew")
        row.grid_anchor("nw")
        row.grid_columnconfigure(1, weight=1)
        if row_index:
            tk.Frame(
                row,
                height=1,
                background=colors["home_border_soft"],
            ).place(x=0, y=0, relwidth=1)
        dot = tk.Canvas(
            row,
            width=px(18),
            height=px(18),
            background=colors["home_surface"],
            highlightthickness=0,
        )
        dot.grid(
            row=0,
            column=0,
            rowspan=2,
            sticky="n",
            padx=(0, px(10)),
            pady=(px(2), 0),
        )
        dot_id = dot.create_oval(
            1,
            1,
            px(17),
            px(17),
            fill=colors["home_surface_quiet"],
            outline=colors["home_secondary"],
        )
        dot_warning_id = dot.create_rectangle(
            1,
            1,
            px(17),
            px(17),
            fill=colors["home_surface_quiet"],
            outline=colors["home_secondary"],
            state="hidden",
        )
        dot_text_id = dot.create_text(
            px(9),
            px(9),
            text="·",
            font=fonts["micro"],
            fill=colors["home_secondary"],
        )
        copy_box = tk.Frame(row, background=colors["home_surface"])
        copy_box.grid(row=0, column=1, rowspan=2, sticky="nw")
        title_label = tk.Label(
            copy_box,
            text=title,
            font=fonts["body"],
            foreground=colors["home_ink"],
            background=colors["home_surface"],
        )
        title_label.pack(anchor="w")
        status_var = tk.StringVar(value="检测中")
        health_vars[key] = status_var
        status_box = tk.Frame(row, background=colors["home_surface"])
        status_box.grid(row=0, column=2, sticky="e", padx=(px(8), 0))
        status_label = tk.Label(
            status_box,
            textvariable=status_var,
            font=fonts["action"],
            foreground=colors["home_secondary"],
            background=colors["home_surface"],
        )
        status_label.pack(side="left")
        health_labels[key] = status_label
        note_var = tk.StringVar(value=initial_note)
        health_note_vars[key] = note_var
        note_label = tk.Label(
            copy_box,
            textvariable=note_var,
            font=fonts["micro"],
            foreground=colors["home_secondary"],
            background=colors["home_surface"],
            anchor="w",
            justify="left",
        )
        note_label.pack(anchor="w", pady=(px(3), 0))
        action_label = tk.Label(
            status_box,
            text="",
            font=fonts["action"],
            foreground=colors["home_primary"],
            background=colors["home_surface"],
            cursor="hand2",
        )
        action_label.pack_forget()
        def run_health_action(_event=None, status_key=key) -> None:
            host.on_home_health_click(status_key)

        action_label.bind("<Button-1>", run_health_action)
        action_label.bind("<Return>", run_health_action)
        action_label.bind("<space>", run_health_action)
        action_label.bind(
            "<FocusIn>",
            lambda _event, label=action_label: label.configure(
                highlightbackground=colors["home_primary"]
            ),
        )
        action_label.bind(
            "<FocusOut>",
            lambda _event, label=action_label: label.configure(
                highlightbackground=colors["home_surface"]
            ),
        )
        backgrounds = (
            row,
            dot,
            copy_box,
            title_label,
            status_box,
            status_label,
            note_label,
            action_label,
        )
        health_widgets[key] = HomeHealthWidgets(
            row,
            dot,
            dot_id,
            dot_warning_id,
            dot_text_id,
            status_label,
            action_label,
            backgrounds,
        )

    candidate_strip = tk.Frame(
        page,
        background=colors["home_surface"],
        highlightbackground=colors["home_border"],
        highlightthickness=1,
        height=px(72),
    )
    candidate_strip.grid(row=2, column=0, sticky="nsew", pady=(0, px(16)))
    candidate_strip.pack_propagate(False)
    candidate_content = tk.Frame(
        candidate_strip,
        background=colors["home_surface"],
        padx=px(24),
        pady=px(14),
    )
    candidate_content.pack(fill="both", expand=True)
    candidate_content.grid_columnconfigure(0, minsize=px(118))
    candidate_content.grid_columnconfigure(1, weight=1)
    tk.Label(
        candidate_content,
        text="候选人摘要",
        font=fonts["summary_heading"],
        foreground=colors["home_ink"],
        background=colors["home_surface"],
    ).grid(row=0, column=0, sticky="w", padx=(0, px(24)))
    summary_line = tk.Frame(candidate_content, background=colors["home_surface"])
    summary_line.grid(row=0, column=1, sticky="ew")
    stats_vars: dict[str, tk.StringVar] = {}
    stats_labels: dict[str, tuple[tk.Label, str]] = {}
    stats_data = (
        ("total_home", "通过筛选", "通过筛选 "),
        ("strong_home", "强烈推荐", " 人，其中强烈推荐 "),
        ("recommended_home", "推荐", " 人、推荐 "),
        ("greeted_home", "已打招呼", " 人，已打招呼 "),
    )
    for key, label_text, prefix in stats_data:
        tk.Label(
            summary_line,
            text=prefix,
            font=fonts["body"],
            foreground=colors["home_secondary"],
            background=colors["home_surface"],
        ).pack(side="left")
        value_var = tk.StringVar(value="0")
        stats_vars[key] = value_var
        value_label = tk.Label(
            summary_line,
            textvariable=value_var,
            font=fonts["action"],
            foreground=(
                colors["home_primary"]
                if key == "total_home"
                else colors["home_ink"]
            ),
            background=colors["home_surface"],
            cursor="hand2",
        )
        value_label.pack(side="left")
        value_label.bind(
            "<Button-1>",
            lambda _event, stat_type=key: host.show_stat_detail(stat_type),
        )
        stats_labels[key] = (value_label, label_text)
    tk.Label(
        summary_line,
        text=" 人",
        font=fonts["body"],
        foreground=colors["home_secondary"],
        background=colors["home_surface"],
    ).pack(side="left")
    all_results = tk.Label(
        candidate_content,
        text="查看全部结果  ›",
        font=fonts["action"],
        foreground=colors["home_primary"],
        background=colors["home_surface"],
        cursor="hand2",
    )
    all_results.grid(row=0, column=2, sticky="e", padx=(px(18), 0))
    all_results.bind(
        "<Button-1>",
        lambda _event: host.app_shell.request_sidebar_page(result_page_index),
    )

    tools_band = tk.Frame(
        page,
        background=colors["home_surface"],
        highlightbackground=colors["home_border"],
        highlightthickness=1,
    )
    tools_band.grid(row=3, column=0, sticky="nsew")
    tools_band.pack_propagate(False)
    tools_content = tk.Frame(
        tools_band,
        background=colors["home_surface"],
        padx=px(20),
        pady=px(14),
    )
    tools_content.pack(fill="both", expand=True)
    tools_content.grid_columnconfigure(0, minsize=px(96))
    tools_content.grid_columnconfigure(1, minsize=px(236))
    tools_content.grid_columnconfigure(2, minsize=px(266))
    tools_content.grid_columnconfigure(3, weight=1)
    tk.Label(
        tools_content,
        text="常用工作",
        font=fonts["summary_heading"],
        foreground=colors["home_ink"],
        background=colors["home_surface"],
    ).grid(row=0, column=0, sticky="w", padx=(0, px(18)))
    tool_specs = (
        (
            "验",
            "学历核验",
            "证书识别与学信网核验",
            lambda: host.app_shell.request_sidebar_page(education_page_index),
        ),
        (
            "入",
            "导入外部候选人",
            "录入其他招聘渠道简历",
            host.import_external_candidate,
        ),
    )
    tool_tiles: list[tk.Frame] = []
    for column, (icon_text, title, note, command) in enumerate(tool_specs, start=1):
        normal = colors["home_primary_tint"] if column == 1 else colors["home_surface_quiet"]
        border = colors["home_primary_border"] if column == 1 else colors["home_border_soft"]
        tile = tk.Frame(
            tools_content,
            background=normal,
            padx=px(14),
            pady=px(6),
            width=px(236 if column == 1 else 266),
            height=px(72),
        )
        tile.grid(row=0, column=column, sticky="nsw", padx=(px(12), 0))
        tile.pack_propagate(False)
        tool_tiles.append(tile)
        icon_label = tk.Label(
            tile,
            text=icon_text,
            font=fonts["action"],
            foreground=colors["home_primary"],
            background=normal,
            highlightbackground=colors["home_primary_border"],
            highlightthickness=1,
            width=2,
            height=1,
        )
        icon_label.pack(side="left", padx=(0, px(11)))
        text_box = tk.Frame(tile, background=normal)
        text_box.pack(side="left", fill="x", expand=True)
        title_label = tk.Label(
            text_box,
            text=title,
            font=fonts["action"],
            foreground=colors["home_ink"],
            background=normal,
            anchor="w",
        )
        title_label.pack(fill="x")
        note_label = tk.Label(
            text_box,
            text=note,
            font=fonts["meta"],
            foreground=colors["home_secondary"],
            background=normal,
            anchor="w",
        )
        note_label.pack(fill="x", pady=(px(2), 0))
        arrow = tk.Label(
            tile,
            text="›",
            font=fonts["action"],
            foreground=colors["home_primary"],
            background=normal,
        )
        arrow.pack(side="right", padx=(px(10), 0))
        _bind_surface(
            tile,
            (tile, icon_label, text_box, title_label, note_label, arrow),
            command,
            normal=normal,
            hover=colors["home_active_row"],
            focus=colors["home_primary"],
            border=border,
        )

    maintenance_frame = tk.Frame(tools_content, background=colors["home_surface"])
    maintenance_frame.grid(row=0, column=3, sticky="e", padx=(px(10), 0))
    tk.Frame(
        maintenance_frame,
        width=1,
        background=colors["home_border"],
    ).pack(side="left", fill="y", padx=(0, px(16)))
    tk.Label(
        maintenance_frame,
        text="维护",
        font=fonts["micro"],
        foreground=colors["home_muted"],
        background=colors["home_surface"],
    ).pack(side="left", padx=(0, px(10)))
    maintenance_actions = (
        ("数据备份与恢复", host.open_home_data_maintenance),
        ("系统设置", host.open_home_system_settings),
    )
    for index, (text, command) in enumerate(maintenance_actions):
        link = tk.Label(
            maintenance_frame,
            text=text,
            font=fonts["meta"],
            foreground=colors["home_primary"],
            background=colors["home_surface"],
            cursor="hand2",
        )
        link.pack(side="left")
        link.bind("<Button-1>", lambda _event, callback=command: callback())
        if index == 0:
            tk.Label(
                maintenance_frame,
                text=" · ",
                font=fonts["meta"],
                foreground=colors["home_muted"],
                background=colors["home_surface"],
            ).pack(side="left")

    page.bind(
        "<Configure>",
        lambda _event: host.app_shell.schedule_page_width_policy(),
    )

    return HomePageWidgets(
        page=page,
        job_var=job_var,
        job_combo=job_combo,
        stats_vars=stats_vars,
        stats_labels=stats_labels,
        task_vars=task_vars,
        task_action_vars=task_action_vars,
        task_labels=task_labels,
        task_widgets=task_widgets,
        task_total_var=task_total_var,
        task_headline_prefix_var=task_headline_prefix_var,
        task_headline_suffix_var=task_headline_suffix_var,
        health_vars=health_vars,
        health_note_vars=health_note_vars,
        health_labels=health_labels,
        health_widgets=health_widgets,
        readiness_title_var=readiness_title_var,
        readiness_note_var=readiness_note_var,
        readiness_banner=readiness_banner,
        readiness_rail=readiness_rail,
        readiness_icon_label=readiness_icon_label,
        readiness_title_label=readiness_title_label,
        readiness_note_label=readiness_note_label,
        scan_summary_var=scan_summary_var,
        scan_status_var=scan_status_var,
        scan_status_label=scan_status_label,
        layout=HomeLayoutWidgets(
            header,
            controls,
            workspace,
            action_panel,
            readiness_panel,
            action_header,
            task_grid,
            readiness_heading,
            health_list,
            candidate_strip,
            tools_band,
            tools_content,
            maintenance_frame,
            tuple(tool_tiles),
        ),
    )
