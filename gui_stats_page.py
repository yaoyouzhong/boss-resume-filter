"""Tk widget construction for the statistics page."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Mapping
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Protocol

import ui_theme


class LayoutShell(Protocol):
    def schedule_page_width_policy(self) -> None: ...


class WidgetSupport(Protocol):
    def create_page_header(
        self,
        parent: tk.Misc,
        title: str,
        subtitle: str | None = None,
        top_padding: int = 0,
    ) -> tk.Misc: ...


class StatsPageHost(Protocol):
    """Narrow host contract required to build the statistics page."""

    pages_frame: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    font_label: Any
    font_stat: Any
    font_stat_label: Any
    font_section: Any
    font_table: Any
    icons: Any
    app_shell: LayoutShell
    widget_support: WidgetSupport

    def refresh_stats(self) -> None: ...

    def _show_selected_job_review(self) -> None: ...

    def _show_stats_context_menu(self, event: tk.Event) -> None: ...


@dataclass(frozen=True)
class StatsPageWidgets:
    """Widget references kept by the page and exposed through compatibility aliases."""

    page: ttk.Frame
    job_var: tk.StringVar
    job_combo: ttk.Combobox
    time_var: tk.StringVar
    summary_vars: dict[str, tk.StringVar]
    tree: ttk.Treeview


def build_stats_page(
    host: StatsPageHost,
    ui_config: Mapping[str, Any],
) -> StatsPageWidgets:
    """Build the statistics page without reading candidate data."""
    page = ttk.Frame(host.pages_frame, style="Page.TFrame")
    host.widget_support.create_page_header(page, "数据统计")
    scale = host.dpi_scale * host.zoom_factor

    filter_frame = ttk.Frame(page, style="Page.TFrame")
    filter_frame.pack(fill="x", pady=(0, int(15 * scale)))

    ttk.Label(
        filter_frame,
        text="岗位过滤:",
        font=host.font_label,
        background=host.colors["bg_main"],
    ).pack(side="left")
    job_var = tk.StringVar(value="全部岗位")
    job_combo = ttk.Combobox(
        filter_frame,
        textvariable=job_var,
        values=["全部岗位"],
        width=28,
        state="readonly",
        font=host.font_label,
    )
    job_combo.pack(side="left", padx=int(15 * scale))
    job_combo.bind("<<ComboboxSelected>>", lambda _event: host.refresh_stats())

    ttk.Label(
        filter_frame,
        text="时间范围:",
        font=host.font_label,
        background=host.colors["bg_main"],
    ).pack(side="left", padx=int(30 * scale))
    time_var = tk.StringVar(value="全部")
    time_combo = ttk.Combobox(
        filter_frame,
        textvariable=time_var,
        values=["今天", "本周", "本月", "全部"],
        width=12,
        state="readonly",
        font=host.font_label,
    )
    time_combo.pack(side="left", padx=int(15 * scale))
    time_combo.bind("<<ComboboxSelected>>", lambda _event: host.refresh_stats())

    summary_container = ttk.Frame(page, style="Page.TFrame")
    summary_container.pack(fill="x", pady=int(10 * scale))
    summary_vars: dict[str, tk.StringVar] = {}
    summary_items = [
        ("passed_filter", "通过筛选", "total", host.colors["primary"]),
        ("strong_recommend", "强烈推荐", "strong", host.colors["purple"]),
        ("thumbs_up", "推荐", "recommended", host.colors["success"]),
        ("chat", "已打招呼", "greeted", host.colors["warning"]),
    ]
    card_gap = int(10 * scale)
    for index, (icon_name, label_text, var_name, color) in enumerate(summary_items):
        card = ttk.Frame(summary_container, style="Card.TFrame")
        card_padx = (0, card_gap) if index < len(summary_items) - 1 else 0
        card.pack(
            side="left",
            fill="x",
            expand=True,
            padx=card_padx,
            pady=int(10 * scale),
        )
        icon_size = int(ui_config["stat_icon_size"] * scale)
        icon_canvas = tk.Canvas(
            card,
            width=icon_size,
            height=icon_size,
            bg=host.colors["bg_card"],
            highlightthickness=0,
        )
        icon_canvas.pack(pady=(int(12 * scale), int(5 * scale)))
        margin = int(ui_config["icon_margin"] * scale)
        icon_canvas.create_oval(
            margin,
            margin,
            icon_size - margin,
            icon_size - margin,
            fill=color,
            outline="",
        )
        stat_icon = host.icons.stat(icon_name, "white")
        icon_canvas.create_image(icon_size // 2, icon_size // 2, image=stat_icon)
        icon_canvas._icon_ref = stat_icon

        value_var = tk.StringVar(value="0")
        summary_vars[var_name] = value_var
        ttk.Label(
            card,
            textvariable=value_var,
            font=host.font_stat,
            foreground=color,
            background=host.colors["bg_card"],
        ).pack(pady=(0, int(4 * scale)))
        ttk.Label(
            card,
            text=label_text,
            font=host.font_stat_label,
            foreground=host.colors["text_secondary"],
            background=host.colors["bg_card"],
        ).pack(pady=(0, int(12 * scale)))

    ttk.Label(
        page,
        text="岗位明细",
        font=host.font_section,
        foreground=host.colors["text_primary"],
        background=host.colors["bg_main"],
    ).pack(
        anchor="w",
        padx=int(5 * scale),
        pady=(int(20 * scale), int(10 * scale)),
    )

    table_container = ttk.Frame(page, style="Card.TFrame")
    table_container.pack(fill="both", expand=True, pady=int(10 * scale))
    columns = (
        "job",
        "filter_dist",
        "greeted",
        "feedback",
        "suitable_rate",
        "false_positive_rate",
        "replied",
        "interviewed",
        "avg_score",
    )
    tree = ttk.Treeview(
        table_container,
        columns=columns,
        show="headings",
        height=8,
    )
    headings = {
        "job": "岗位名称",
        "filter_dist": "筛选分布",
        "greeted": "已打招呼",
        "feedback": "已反馈",
        "suitable_rate": "合适率",
        "false_positive_rate": "误推率",
        "replied": "已回复",
        "interviewed": "已约面",
        "avg_score": "平均分",
    }
    for column, text in headings.items():
        tree.heading(column, text=text)
    column_widths = {
        "job": (200, 150, "w"),
        "filter_dist": (175, 140, "center"),
        "greeted": (100, 80, "center"),
        "feedback": (80, 65, "center"),
        "suitable_rate": (75, 60, "center"),
        "false_positive_rate": (75, 60, "center"),
        "replied": (100, 80, "center"),
        "interviewed": (100, 80, "center"),
        "avg_score": (65, 55, "center"),
    }
    for column, (width, minwidth, anchor) in column_widths.items():
        tree.column(column, width=width, minwidth=minwidth, anchor=anchor)

    style = ttk.Style()
    style.configure(
        "Stats.Treeview",
        font=host.font_table,
        rowheight=int(ui_config["treeview_rowheight"] * scale),
    )
    style.configure(
        "Stats.Treeview.Heading",
        font=(ui_theme.FONT_FAMILY, int(12 * host.font_scale), "bold"),
    )
    tree.configure(style="Stats.Treeview")

    tree_scroll_y = ttk.Scrollbar(table_container, orient="vertical", command=tree.yview)
    tree_scroll_x = ttk.Scrollbar(table_container, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
    tree.grid(
        row=0,
        column=0,
        sticky="nsew",
        padx=int(15 * scale),
        pady=int(15 * scale),
    )
    tree_scroll_y.grid(row=0, column=1, sticky="ns", pady=int(10 * scale))
    tree_scroll_x.grid(row=1, column=0, sticky="ew", padx=int(15 * scale))
    table_container.grid_rowconfigure(0, weight=1)
    table_container.grid_columnconfigure(0, weight=1)
    tree.bind("<Double-Button-1>", lambda _event: host._show_selected_job_review())
    tree.bind("<Button-3>", host._show_stats_context_menu)
    tree.bind(
        "<Configure>",
        lambda _event: host.app_shell.schedule_page_width_policy(),
        add="+",
    )
    return StatsPageWidgets(
        page=page,
        job_var=job_var,
        job_combo=job_combo,
        time_var=time_var,
        summary_vars=summary_vars,
        tree=tree,
    )
