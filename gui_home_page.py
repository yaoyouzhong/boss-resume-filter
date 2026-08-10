"""Tk widget construction for the application home page."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Mapping
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Protocol


class HomePageHost(Protocol):
    """Narrow host contract required to build the home page."""

    pages_frame: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_title: Any
    font_label: Any
    font_stat: Any
    font_stat_label: Any
    icons: Any

    def refresh_home_stats(self) -> None: ...

    def show_stat_detail(self, stat_type: str) -> None: ...

    def _request_sidebar_page(self, page_index: int) -> None: ...

    def _create_card(
        self,
        parent: tk.Misc,
        title: str,
        **kwargs: Any,
    ) -> tk.Misc: ...


@dataclass(frozen=True)
class HomePageWidgets:
    """Widget references consumed by home-page refresh and navigation logic."""

    page: ttk.Frame
    job_var: tk.StringVar
    job_combo: ttk.Combobox
    stats_vars: dict[str, tk.StringVar]
    stats_labels: dict[str, tuple[ttk.Label, str]]


def build_home_page(
    host: HomePageHost,
    ui_config: Mapping[str, Any],
    *,
    run_page_index: int,
    result_page_index: int,
    config_page_index: int,
) -> HomePageWidgets:
    """Build the startup home page without loading jobs or candidate statistics."""
    scale = host.dpi_scale * host.zoom_factor
    page = ttk.Frame(host.pages_frame, style="Page.TFrame")

    card_padding = int(20 * scale)
    header_card = ttk.Frame(page, style="WelcomeCard.TFrame")
    header_card.pack(fill="x", pady=(0, int(25 * scale)))
    accent_bar = tk.Frame(
        header_card,
        width=int(4 * scale),
        bg=host.colors["primary"],
    )
    accent_bar.pack(side="left", fill="y")
    header_frame = ttk.Frame(header_card, style="WelcomeInner.TFrame")
    header_frame.pack(
        fill="x",
        padx=(card_padding, card_padding),
        pady=(card_padding, card_padding),
    )
    ttk.Label(
        header_frame,
        text="欢迎使用 BOSS 简历筛选器",
        font=host.font_title,
        foreground=host.colors["text_primary"],
        background=host.colors["bg_card"],
    ).pack(anchor="w")
    ttk.Label(
        header_frame,
        text=(
            "智能解析、智能匹配、AI 评估、候选人联系、学历核验、"
            "人工反馈、跟进状态、数据复盘"
        ),
        font=host.font_label,
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_card"],
    ).pack(anchor="w", pady=(int(10 * scale), 0))

    filter_frame = ttk.Frame(page, style="Page.TFrame")
    filter_frame.pack(fill="x", pady=(int(15 * scale), 0))
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
    job_combo.bind(
        "<<ComboboxSelected>>",
        lambda _event: host.refresh_home_stats(),
    )

    stats_container = ttk.Frame(page, style="Page.TFrame")
    stats_container.pack(fill="x", pady=int(30 * scale))
    cards_data = [
        ("passed_filter", "通过筛选", "total_home", host.colors["primary"]),
        (
            "strong_recommend",
            "强烈推荐",
            "strong_home",
            host.colors["purple"],
        ),
        ("thumbs_up", "推荐", "recommended_home", host.colors["success"]),
        ("chat", "已打招呼", "greeted_home", host.colors["warning"]),
    ]
    stats_vars: dict[str, tk.StringVar] = {}
    stats_labels: dict[str, tuple[ttk.Label, str]] = {}
    card_gap = int(15 * scale)
    for index, (icon_name, label_text, var_name, color) in enumerate(cards_data):
        card_frame = ttk.Frame(stats_container, style="Card.TFrame")
        card_padx = (0, card_gap) if index < len(cards_data) - 1 else 0
        card_frame.pack(
            side="left",
            fill="x",
            expand=True,
            padx=card_padx,
            pady=int(12 * scale),
        )
        icon_size = int(ui_config["stat_icon_size"] * scale)
        icon_canvas = tk.Canvas(
            card_frame,
            width=icon_size,
            height=icon_size,
            bg=host.colors["bg_card"],
            highlightthickness=0,
        )
        icon_canvas.pack(
            anchor="center",
            pady=(int(20 * scale), int(8 * scale)),
        )
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
        icon_canvas.create_image(
            icon_size // 2,
            icon_size // 2,
            image=stat_icon,
        )
        icon_canvas._icon_ref = stat_icon

        value_var = tk.StringVar(value="0")
        stats_vars[var_name] = value_var
        value_label = ttk.Label(
            card_frame,
            textvariable=value_var,
            font=host.font_stat,
            foreground=color,
            background=host.colors["bg_card"],
            cursor="hand2",
        )
        value_label.pack(anchor="center", pady=(0, int(8 * scale)))
        stats_labels[var_name] = (value_label, label_text)
        value_label.bind(
            "<Button-1>",
            lambda _event, stat_type=var_name: host.show_stat_detail(stat_type),
        )
        ttk.Label(
            card_frame,
            text=label_text,
            font=host.font_stat_label,
            foreground=host.colors["text_secondary"],
            background=host.colors["bg_card"],
        ).pack(anchor="center", pady=(0, int(20 * scale)))

    quick_frame = host._create_card(
        page,
        "快速操作",
        padding=int(ui_config["card_padding"] * scale),
        fill="both",
        expand=True,
        pady=int(30 * scale),
    )
    quick_buttons = ttk.Frame(quick_frame, style="TFrame")
    quick_buttons.pack(fill="x")

    play_icon = host.icons.button("play", "#FFFFFF")
    run_button = ttk.Button(
        quick_buttons,
        image=play_icon,
        text=" 开始筛选",
        compound=tk.LEFT,
        command=lambda: host._request_sidebar_page(run_page_index),
        style="Accent.TButton",
    )
    run_button._icon_ref = play_icon
    run_button.pack(side="left", padx=int(15 * scale))
    result_icon = host.icons.button("filter", host.colors["text_primary"])
    result_button = ttk.Button(
        quick_buttons,
        image=result_icon,
        text=" 查看结果",
        compound=tk.LEFT,
        command=lambda: host._request_sidebar_page(result_page_index),
        style="TButton",
    )
    result_button._icon_ref = result_icon
    result_button.pack(side="left", padx=int(15 * scale))
    config_icon = host.icons.button("briefcase", host.colors["text_primary"])
    config_button = ttk.Button(
        quick_buttons,
        image=config_icon,
        text=" 配置岗位",
        compound=tk.LEFT,
        command=lambda: host._request_sidebar_page(config_page_index),
        style="TButton",
    )
    config_button._icon_ref = config_icon
    config_button.pack(side="left", padx=int(15 * scale))

    return HomePageWidgets(
        page=page,
        job_var=job_var,
        job_combo=job_combo,
        stats_vars=stats_vars,
        stats_labels=stats_labels,
    )
