"""Tk widget construction for the candidate result page."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Mapping
from dataclasses import dataclass
from tkinter import font, ttk
from typing import Any, Protocol

import ui_theme
from ui_layout import result_display_columns


class NavigationShell(Protocol):
    def request_sidebar_page(self, page_index: int) -> None: ...

    def schedule_page_width_policy(self) -> None: ...


class ResultPageHost(Protocol):
    """Narrow host contract required to build the result page."""

    pages_frame: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    font_label: Any
    font_stat: Any
    font_stat_label: Any
    font_table: Any
    inline_note_gap: int
    icons: Any
    _result_search_placeholder: str
    _result_search_placeholder_active: bool
    _result_search_focused: bool
    app_shell: NavigationShell

    def _create_page_header(
        self,
        parent: tk.Misc,
        title: str,
        subtitle: str | None = None,
        top_padding: int = 0,
    ) -> tk.Misc: ...

    def refresh_results(self, force: bool = False) -> None: ...

    def _close_result_date_dropdowns(self) -> None: ...

    def _on_result_time_range_changed(self, event: tk.Event) -> None: ...

    def show_result_stat_detail(self, stat_type: str) -> None: ...

    def _filter_result_tree(self) -> None: ...

    def _refresh_results_and_reset_sort(self) -> None: ...

    def _show_tooltip(
        self,
        text: str,
        x: int,
        y: int,
        source_key: tuple[str, ...],
    ) -> None: ...

    def _hide_tooltip(self, event: tk.Event | None = None) -> None: ...

    def _update_result_review_button_state(self, event: tk.Event | None = None) -> None: ...

    def _build_empty_state(
        self,
        parent: tk.Misc,
        icon_name: str,
        title: str,
        subtitle: str,
        *,
        action_text: str,
        action_command: Any,
    ) -> tk.Misc: ...

    def show_daily_candidate_actions(self) -> None: ...

    def _open_selected_candidate_review(self) -> None: ...

    def _open_greet_queue_from_result(self) -> None: ...

    def _show_result_contact_badge_tooltip(self, event: tk.Event) -> None: ...

    def show_candidate_state_diagnostics(self) -> None: ...

    def export_excel(self) -> None: ...

    def clear_candidates(self) -> None: ...


@dataclass(frozen=True)
class ResultPageWidgets:
    """Widget references exposed through the existing BossFilterGUI aliases."""

    page: ttk.Frame
    job_var: tk.StringVar
    job_combo: ttk.Combobox
    time_range_var: tk.StringVar
    time_range_combo: ttk.Combobox
    custom_date_frame: ttk.Frame
    stats_vars: dict[str, tk.StringVar]
    stats_greeted: dict[str, tk.StringVar]
    stats_click: dict[str, str]
    stat_icon_canvases: list[tuple[tk.Canvas, ttk.Label]]
    search_var: tk.StringVar
    search_entry: ttk.Entry
    search_clear_hint: ttk.Label
    view_label: ttk.Label
    view_var: tk.StringVar
    view_combo: ttk.Combobox
    count_var: tk.StringVar
    show_blacklist_var: tk.BooleanVar
    tree: ttk.Treeview
    tree_font: font.Font
    empty_state: tk.Misc
    review_button: ttk.Button
    greet_queue_button: ttk.Button
    greet_queue_badge: tk.Label
    more_menu_button: ttk.Menubutton
    more_menu: tk.Menu


def build_result_page(
    host: ResultPageHost,
    ui_config: Mapping[str, Any],
    *,
    font_family: str,
    run_page_index: int,
) -> ResultPageWidgets:
    """Build the result page without reading or mutating candidate data."""
    scale = host.dpi_scale * host.zoom_factor
    page = ttk.Frame(host.pages_frame, style="Page.TFrame")
    host._create_page_header(page, "筛选结果")

    filter_frame = ttk.Frame(page, style="Page.TFrame")
    filter_frame.pack(fill="x", pady=(0, int(10 * scale)))
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
    job_combo.bind("<<ComboboxSelected>>", lambda _event: host.refresh_results())

    ttk.Label(
        filter_frame,
        text="时间范围:",
        font=host.font_label,
        background=host.colors["bg_main"],
    ).pack(side="left", padx=int(20 * scale))
    time_range_var = tk.StringVar(value="全部时间")
    time_range_combo = ttk.Combobox(
        filter_frame,
        textvariable=time_range_var,
        values=("全部时间", "今天", "近7天", "近30天", "自定义"),
        width=10,
        state="readonly",
        font=host.font_label,
        postcommand=host._close_result_date_dropdowns,
    )
    time_range_combo.pack(side="left", padx=(0, int(8 * scale)))
    time_range_combo.bind("<<ComboboxSelected>>", host._on_result_time_range_changed)
    custom_date_frame = ttk.Frame(filter_frame, style="Page.TFrame")

    stats_container = ttk.Frame(page, style="Page.TFrame")
    stats_container.pack(fill="x", pady=int(15 * scale))
    stats_vars: dict[str, tk.StringVar] = {}
    stats_greeted: dict[str, tk.StringVar] = {}
    stats_click: dict[str, str] = {}
    stats_data = [
        ("strong_recommend", "强烈推荐", "strong", host.colors["purple"]),
        ("thumbs_up", "推荐", "recommended", host.colors["success"]),
        ("hourglass", "待定", "pending", host.colors["pending"]),
        ("chat", "已打招呼", "greeted", host.colors["warning"]),
    ]
    card_gap = int(12 * scale)
    stat_icon_canvases: list[tuple[tk.Canvas, ttk.Label]] = []
    for index, (icon_name, label_text, var_name, color) in enumerate(stats_data):
        card_frame = ttk.Frame(stats_container, style="Card.TFrame")
        card_padx = (0, card_gap) if index < len(stats_data) - 1 else 0
        card_frame.pack(side="left", fill="x", expand=True, padx=card_padx)

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
            pady=(int(12 * scale), int(4 * scale)),
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
        icon_canvas.create_image(icon_size // 2, icon_size // 2, image=stat_icon)
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
        value_label.pack(anchor="center", pady=(0, int(2 * scale)))
        stat_icon_canvases.append((icon_canvas, value_label))

        greeted_var = tk.StringVar(
            value="通过筛选中" if var_name == "greeted" else "0 已打招呼"
        )
        stats_greeted[var_name] = greeted_var
        ttk.Label(
            card_frame,
            textvariable=greeted_var,
            font=(font_family, int(10 * host.font_scale)),
            foreground=host.colors["success"],
            background=host.colors["bg_card"],
        ).pack(anchor="center", pady=(0, int(2 * scale)))

        label = ttk.Label(
            card_frame,
            text=label_text,
            font=host.font_stat_label,
            foreground=host.colors["text_secondary"],
            background=host.colors["bg_card"],
        )
        label.pack(anchor="center", pady=(0, int(10 * scale)))
        stats_click[var_name] = label_text
        value_label.bind(
            "<Button-1>",
            lambda _event, stat_type=var_name: host.show_result_stat_detail(stat_type),
        )
        label.bind(
            "<Button-1>",
            lambda _event, stat_type=var_name: host.show_result_stat_detail(stat_type),
        )

    search_frame = ttk.Frame(page, style="Page.TFrame")
    search_frame.pack(fill="x", pady=(int(12 * scale), int(6 * scale)))
    ttk.Label(
        search_frame,
        text="搜索:",
        font=host.font_label,
        background=host.colors["bg_main"],
    ).pack(side="left")
    search_var = tk.StringVar(value=host._result_search_placeholder)
    search_entry = ttk.Entry(
        search_frame,
        textvariable=search_var,
        width=26,
        font=host.font_label,
    )
    search_placeholder_color = host.colors.get(
        "text_placeholder",
        ui_theme.TEXT_PLACEHOLDER,
    )
    search_entry.configure(foreground=search_placeholder_color)
    search_entry.pack(side="left", padx=(max(8, int(8 * scale)), 0))

    def sync_search_clear_hint() -> None:
        query_active = (
            not host._result_search_placeholder_active
            and bool(search_var.get().strip())
        )
        should_show = host._result_search_focused or query_active
        if should_show and not search_clear_hint.winfo_manager():
            search_clear_hint.pack(
                side="left",
                padx=(host.inline_note_gap, 0),
                before=view_label,
            )
        elif not should_show and search_clear_hint.winfo_manager():
            search_clear_hint.pack_forget()

    def hide_search_placeholder(_event: tk.Event | None = None) -> None:
        host._result_search_focused = True
        if host._result_search_placeholder_active:
            host._result_search_placeholder_active = False
            search_var.set("")
            search_entry.configure(foreground=host.colors["text_primary"])
        sync_search_clear_hint()

    def show_search_placeholder(_event: tk.Event | None = None) -> None:
        host._result_search_focused = False
        if not search_var.get():
            host._result_search_placeholder_active = True
            search_var.set(host._result_search_placeholder)
            search_entry.configure(foreground=search_placeholder_color)
        sync_search_clear_hint()

    def clear_search(_event: tk.Event | None = None) -> str:
        host._result_search_placeholder_active = False
        search_var.set("")
        search_entry.configure(foreground=host.colors["text_primary"])
        sync_search_clear_hint()
        return "break"

    def on_search_changed(*_args: object) -> None:
        host._filter_result_tree()
        sync_search_clear_hint()

    search_var.trace_add("write", on_search_changed)
    search_entry.bind("<FocusIn>", hide_search_placeholder)
    search_entry.bind("<FocusOut>", show_search_placeholder)
    search_entry.bind("<Escape>", clear_search)
    search_clear_hint = ttk.Label(
        search_frame,
        text="Esc 清空",
        font=(font_family, int(10 * host.font_scale)),
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_main"],
    )

    view_label = ttk.Label(
        search_frame,
        text="结果范围:",
        font=host.font_label,
        background=host.colors["bg_main"],
    )
    view_label.pack(side="left", padx=(int(16 * scale), 0))
    view_var = tk.StringVar(value="全部记录")
    view_combo = ttk.Combobox(
        search_frame,
        textvariable=view_var,
        values=("推荐候选人", "复核通过", "待复核", "淘汰记录", "全部记录"),
        width=11,
        state="readonly",
        font=host.font_label,
    )
    view_combo.pack(side="left", padx=int(10 * scale))
    view_combo.bind("<<ComboboxSelected>>", lambda _event: host.refresh_results())
    count_var = tk.StringVar(value="0 / 共 0 人")
    ttk.Label(
        search_frame,
        textvariable=count_var,
        font=(font_family, int(10 * host.font_scale)),
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_main"],
    ).pack(side="left", padx=int(8 * scale))

    show_blacklist_var = tk.BooleanVar(value=False)
    checkbox_style = ttk.Style()
    checkbox_style.configure(
        "Blacklist.TCheckbutton",
        font=host.font_label,
        background=host.colors["bg_main"],
    )
    checkbox_style.map(
        "Blacklist.TCheckbutton",
        background=[
            ("active", host.colors["bg_main"]),
            ("pressed", host.colors["bg_main"]),
            ("selected", host.colors["bg_main"]),
            ("disabled", host.colors["bg_main"]),
        ],
    )
    blacklist_check = ttk.Checkbutton(
        search_frame,
        text="显示已屏蔽",
        variable=show_blacklist_var,
        command=lambda: host.refresh_results(),
        style="Blacklist.TCheckbutton",
    )
    refresh_image = host.icons.get(
        "refresh_clean",
        int(24 * scale),
        host.colors["text_primary"],
    )
    refresh_icon = ttk.Label(
        search_frame,
        image=refresh_image,
        cursor="hand2",
        background=host.colors["bg_main"],
    )
    refresh_icon._icon_ref = refresh_image
    refresh_icon.bind(
        "<Button-1>",
        lambda _event: host._refresh_results_and_reset_sort(),
    )
    refresh_icon.bind(
        "<Enter>",
        lambda event: host._show_tooltip(
            "刷新结果并恢复默认排序",
            event.x_root + int(12 * scale),
            event.y_root + int(12 * scale),
            ("result_refresh",),
        ),
    )
    refresh_icon.bind("<Leave>", host._hide_tooltip)
    refresh_icon.pack(side="right", padx=(int(6 * scale), int(12 * scale)))
    blacklist_check.pack(side="right", padx=(0, int(6 * scale)))

    table_container = ttk.Frame(page, style="Card.TFrame")
    table_container.pack(fill="both", expand=True, pady=int(8 * scale))
    columns = (
        "name",
        "gender",
        "exp",
        "salary",
        "skills",
        "score",
        "ai_eval",
        "level",
        "status",
        "age",
        "education",
        "job_status",
        "school",
        "company",
    )
    tree = ttk.Treeview(
        table_container,
        columns=columns,
        displaycolumns=result_display_columns(0, maximized=False),
        show="headings",
        height=4,
        selectmode="extended",
    )
    headings = {
        "name": "姓名",
        "gender": "性别",
        "exp": "工作年限",
        "salary": "薪资",
        "skills": "技能匹配",
        "score": "匹配分",
        "ai_eval": "AI评估",
        "level": "推荐指数",
        "status": "状态 / 复核",
        "age": "年龄",
        "education": "学历",
        "job_status": "求职状态",
        "school": "毕业学校",
        "company": "最近公司",
    }
    for column, text in headings.items():
        tree.heading(column, text=text)
    column_specs = {
        "name": (80, 60),
        "gender": (55, 48),
        "exp": (85, 70),
        "salary": (85, 70),
        "skills": (85, 70),
        "score": (70, 60),
        "ai_eval": (70, 60),
        "level": (80, 70),
        "status": (180, 150),
        "age": (70, 60),
        "education": (90, 80),
        "job_status": (130, 90),
        "school": (150, 120),
        "company": (160, 125),
    }
    for column, (width, minwidth) in column_specs.items():
        tree.column(column, width=width, minwidth=minwidth, anchor="center")

    style = ttk.Style()
    style.configure(
        "Result.Treeview",
        font=host.font_table,
        rowheight=int(ui_config["treeview_rowheight"] * scale),
    )
    style.configure(
        "Result.Treeview.Heading",
        font=(font_family, int(12 * host.font_scale), "bold"),
    )
    tree.configure(style="Result.Treeview")
    tree_font = font.Font(font=host.font_table)
    tree_scroll = ttk.Scrollbar(table_container, orient="vertical", command=tree.yview)
    tree_scroll_x = ttk.Scrollbar(
        table_container,
        orient="horizontal",
        command=tree.xview,
    )
    tree.configure(yscrollcommand=tree_scroll.set, xscrollcommand=tree_scroll_x.set)
    pad_x = int(20 * scale)
    pad_y = int(12 * scale)
    tree_scroll_x.pack(
        side="bottom",
        fill="x",
        padx=pad_x,
        pady=(0, int(6 * scale)),
    )
    tree_scroll.pack(side="right", fill="y", pady=pad_y)
    tree.pack(side="left", fill="both", expand=True, padx=(pad_x, 0), pady=pad_y)
    tree.bind(
        "<Configure>",
        lambda _event: host.app_shell.schedule_page_width_policy(),
        add="+",
    )
    tree.bind(
        "<<TreeviewSelect>>",
        host._update_result_review_button_state,
        add="+",
    )
    empty_state = host._build_empty_state(
        table_container,
        "filter",
        "暂无候选人",
        "调整岗位或时间范围，或到运行控制页开始新一轮筛选",
        action_text="开始筛选",
        action_command=lambda: host.app_shell.request_sidebar_page(run_page_index),
    )

    button_frame = ttk.Frame(page, style="Page.TFrame")
    button_frame.pack(
        fill="x",
        padx=int(20 * scale),
        pady=(int(20 * scale), 0),
    )
    button_inner = ttk.Frame(button_frame, style="Page.TFrame")
    button_inner.pack(anchor="center")
    today_actions_icon = host.icons.button("task_list", host.colors["primary"])
    today_actions_button = ttk.Button(
        button_inner,
        image=today_actions_icon,
        text=" 今日待办",
        compound=tk.LEFT,
        command=host.show_daily_candidate_actions,
    )
    today_actions_button._icon_ref = today_actions_icon
    today_actions_button.pack(side="left", padx=int(8 * scale))

    review_icon = host.icons.button("candidate_review", host.colors["primary"])
    review_button = ttk.Button(
        button_inner,
        image=review_icon,
        text=" 查看与复核",
        compound=tk.LEFT,
        command=host._open_selected_candidate_review,
        state="disabled",
    )
    review_button._icon_ref = review_icon
    review_button.pack(side="left", padx=int(8 * scale))

    greet_queue_icon = host.icons.button("chat", host.colors["success"])
    greet_queue_button_frame = ttk.Frame(button_inner, style="Page.TFrame")
    greet_queue_button_frame.pack(side="left", padx=int(8 * scale))
    greet_queue_button = ttk.Button(
        greet_queue_button_frame,
        image=greet_queue_icon,
        text=" 联系候选人",
        compound=tk.LEFT,
        command=host._open_greet_queue_from_result,
    )
    greet_queue_button._icon_ref = greet_queue_icon
    greet_queue_button.pack()
    greet_queue_badge = tk.Label(
        greet_queue_button_frame,
        text="",
        font=(font_family, max(8, int(9 * host.font_scale)), "bold"),
        background=host.colors["danger"],
        foreground="#FFFFFF",
        padx=max(3, int(4 * scale)),
        pady=0,
        cursor="hand2",
    )
    greet_queue_badge.bind(
        "<Button-1>",
        lambda _event: host._open_greet_queue_from_result(),
    )
    greet_queue_badge.bind("<Enter>", host._show_result_contact_badge_tooltip)
    greet_queue_badge.bind("<Leave>", host._hide_tooltip)

    state_check_icon = host.icons.button("health_shield", host.colors["primary"])
    export_icon = host.icons.button("export", host.colors["text_primary"])
    clear_icon = host.icons.button("trash", host.colors["danger"])
    more_menu = tk.Menu(button_inner, tearoff=0, font=host.font_label)
    more_menu._icon_refs = [state_check_icon, export_icon, clear_icon]
    more_menu.add_command(
        label=" 候选人状态体检",
        image=state_check_icon,
        compound=tk.LEFT,
        command=host.show_candidate_state_diagnostics,
    )
    more_menu.add_separator()
    more_menu.add_command(
        label=" 导出 Excel",
        image=export_icon,
        compound=tk.LEFT,
        command=host.export_excel,
    )
    more_menu.add_separator()
    more_menu.add_command(
        label=" 清空候选人",
        image=clear_icon,
        compound=tk.LEFT,
        command=host.clear_candidates,
    )
    more_menu_button = ttk.Menubutton(
        button_inner,
        text="更多操作",
        menu=more_menu,
        width=9,
        style="CenteredActions.TMenubutton",
    )
    more_menu_button.pack(side="left", padx=int(8 * scale))

    return ResultPageWidgets(
        page=page,
        job_var=job_var,
        job_combo=job_combo,
        time_range_var=time_range_var,
        time_range_combo=time_range_combo,
        custom_date_frame=custom_date_frame,
        stats_vars=stats_vars,
        stats_greeted=stats_greeted,
        stats_click=stats_click,
        stat_icon_canvases=stat_icon_canvases,
        search_var=search_var,
        search_entry=search_entry,
        search_clear_hint=search_clear_hint,
        view_label=view_label,
        view_var=view_var,
        view_combo=view_combo,
        count_var=count_var,
        show_blacklist_var=show_blacklist_var,
        tree=tree,
        tree_font=tree_font,
        empty_state=empty_state,
        review_button=review_button,
        greet_queue_button=greet_queue_button,
        greet_queue_badge=greet_queue_badge,
        more_menu_button=more_menu_button,
        more_menu=more_menu,
    )
