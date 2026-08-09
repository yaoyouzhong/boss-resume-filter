"""Tk dialog and local selection state for the daily candidate action queue."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping, Sequence
from tkinter import ttk
from typing import Any, Protocol

import ui_theme
import gui_candidate_workbench
from candidate_workflow import (
    ACTION_TIMING_ORDER,
    REVIEW_CATEGORY_ORDER,
    candidate_review_category,
    format_followup_due_at,
)
from ui_windowing import place_window_centered


class DailyActionLike(Protocol):
    """Fields displayed by the daily candidate action workbench."""

    timing_group: str
    group: str
    candidate: Mapping[str, Any]
    name: str
    job_name: str
    score: int
    action: str
    reason: str
    due_at: str


class CandidateActionsHost(Protocol):
    """Narrow GUI contract required by the daily actions workbench."""

    root: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    font_label: Any
    _tooltip_item: Any
    _tooltip: Any
    _tooltip_after_id: Any

    def _format_daily_action_key_info(self, item: DailyActionLike) -> str: ...

    def _format_daily_action_due(self, item: DailyActionLike) -> str: ...

    def _clip_table_text(self, text: object, limit: int) -> str: ...

    def _open_candidate_review_workbench(
        self,
        candidate: Mapping[str, Any],
    ) -> None: ...

    def _show_text_dialog(
        self,
        title: str,
        text: str,
        *,
        width: int,
        height: int,
    ) -> None: ...

    def _show_candidate_workflow_context_menu(
        self,
        parent: tk.Misc,
        candidate: Mapping[str, Any],
        x_root: int,
        y_root: int,
        *,
        refresh_fn: Callable[[], None],
        primary_action: str | None = None,
    ) -> None: ...

    def _show_tooltip(
        self,
        text: str,
        x: int,
        y: int,
        item_key: object,
        *,
        parent: tk.Misc,
    ) -> None: ...

    def _hide_tooltip(self, event: tk.Event | None = None) -> None: ...


LoadActions = Callable[[], Sequence[DailyActionLike]]
ExportReport = Callable[[tk.Misc], None]


def show_daily_candidate_actions_dialog(
    self: CandidateActionsHost,
    scope: str,
    items: Sequence[DailyActionLike],
    *,
    load_actions: LoadActions,
    export_report: ExportReport,
    ui_config: Mapping[str, Any],
) -> tk.Toplevel:
    """Build and show the daily actions dialog without reading or writing business data."""
    win = tk.Toplevel(self.root)
    win.title("今日待办")
    win.transient(self.root)
    win.withdraw()
    scale = self.dpi_scale * self.zoom_factor

    body = ttk.Frame(win, style="Page.TFrame", padding=int(16 * scale))
    body.pack(fill="both", expand=True)

    business_group_order = [
        "发送结果待核实",
        "已回复待推进",
        "待复核",
        "待完成简历评估",
        "待打招呼",
        "已打招呼待跟进",
        "待约面待推进",
        "面试后待反馈",
    ]
    all_items = list(items)

    def daily_counts(current_items: Sequence[DailyActionLike]) -> dict[str, int]:
        return {
            "due": sum(
                item.timing_group in ("立即处理", "已逾期", "今天")
                for item in current_items
            ),
            "overdue": sum(item.timing_group == "已逾期" for item in current_items),
            "unscheduled": sum(item.timing_group == "待安排" for item in current_items),
            "future": sum(item.timing_group == "以后" for item in current_items),
        }

    counts = daily_counts(all_items)
    gui_candidate_workbench.create_header(
        self,
        body,
        "今日待办",
        "按时间优先级整理候选人，逐项推进下一步",
        scope,
    )
    metric_vars = gui_candidate_workbench.create_metrics(self, body, (
        ("due", "需处理", counts["due"], self.colors["primary"]),
        ("overdue", "已逾期", counts["overdue"], self.colors["danger"]),
        ("unscheduled", "待安排", counts["unscheduled"], self.colors["warning"]),
        ("future", "以后", counts["future"], self.colors["text_muted"]),
    ))

    content = ttk.Frame(body, style="Page.TFrame")
    content.pack(fill="both", expand=True)

    style = ttk.Style()
    style.configure(
        "ActionQueue.Treeview",
        font=(ui_theme.FONT_FAMILY, int(11 * self.font_scale)),
        rowheight=int(ui_config["treeview_rowheight"] * scale),
    )
    style.configure(
        "ActionQueue.Treeview.Heading",
        font=(ui_theme.FONT_FAMILY, int(11 * self.font_scale), "bold"),
    )

    nav_frame = ttk.Frame(content, style="Card.TFrame", padding=int(10 * scale))
    nav_frame.pack(side="left", fill="y", padx=(0, int(10 * scale)))
    ttk.Label(
        nav_frame,
        text="按优先级筛选",
        font=self.font_label,
        foreground=self.colors["text_primary"],
        background=self.colors["bg_card"],
    ).pack(anchor="w", pady=(0, int(8 * scale)))
    navigation_style = gui_candidate_workbench.navigation_style(
        self,
        scale,
        ui_config,
    )
    group_tree = ttk.Treeview(
        nav_frame,
        columns=("count",),
        show="tree",
        height=9,
        style=navigation_style,
        selectmode="browse",
    )
    group_tree.column("#0", width=int(210 * scale), minwidth=int(170 * scale), anchor="w")
    group_tree.column("count", width=0, minwidth=0, stretch=False)
    gui_candidate_workbench.apply_navigation_tags(self, group_tree)
    group_tree.pack(fill="y", expand=True)
    action_group_by_iid: dict[str, str] = {}
    action_items_by_key: dict[str, list[DailyActionLike]] = {}
    action_label_by_key: dict[str, str] = {}
    action_iid_by_key: dict[str, str] = {}

    def rebuild_action_group_tree() -> None:
        group_tree.delete(*group_tree.get_children())
        action_group_by_iid.clear()
        action_items_by_key.clear()
        action_label_by_key.clear()
        action_iid_by_key.clear()
        for timing_index, timing_group in enumerate(ACTION_TIMING_ORDER):
            timing_items = [
                item for item in all_items if item.timing_group == timing_group
            ]
            if not timing_items:
                continue
            parent_iid = f"timing_{timing_index}"
            action_group_by_iid[parent_iid] = timing_group
            action_items_by_key[timing_group] = timing_items
            action_label_by_key[timing_group] = timing_group
            action_iid_by_key[timing_group] = parent_iid
            group_tree.insert(
                "", "end", iid=parent_iid,
                text=f"{timing_group}  {len(timing_items)}",
                values=(len(timing_items),),
                open=(timing_group != "以后"),
                tags=("workbench_root",),
            )
            for group_index, group in enumerate(business_group_order):
                group_items = [item for item in timing_items if item.group == group]
                if not group_items:
                    continue
                child_iid = f"{parent_iid}_group_{group_index}"
                selection_key = f"{timing_group}::{group}"
                action_group_by_iid[child_iid] = selection_key
                action_items_by_key[selection_key] = group_items
                action_label_by_key[selection_key] = group
                action_iid_by_key[selection_key] = child_iid
                group_tree.insert(
                    parent_iid, "end", iid=child_iid,
                    text=f"{group}  {len(group_items)}",
                    values=(len(group_items),),
                    open=(group == "待复核"),
                    tags=("workbench_child",),
                )
                if group != "待复核":
                    continue
                review_subgroups = {category: [] for category in REVIEW_CATEGORY_ORDER}
                for item in group_items:
                    review_subgroups[candidate_review_category(item.candidate)].append(item)
                for sub_index, category in enumerate(REVIEW_CATEGORY_ORDER):
                    category_items = review_subgroups[category]
                    if not category_items:
                        continue
                    review_iid = f"{child_iid}_review_{sub_index}"
                    review_key = f"{selection_key}::{category}"
                    action_group_by_iid[review_iid] = review_key
                    action_items_by_key[review_key] = category_items
                    action_label_by_key[review_key] = category
                    action_iid_by_key[review_key] = review_iid
                    group_tree.insert(
                        child_iid, "end", iid=review_iid,
                        text=f"{category}  {len(category_items)}",
                        values=(len(category_items),),
                        tags=("workbench_child",),
                    )

    rebuild_action_group_tree()
    default_group = next(iter(action_items_by_key), "")

    detail_frame = ttk.Frame(content, style="Card.TFrame")
    detail_frame.pack(side="left", fill="both", expand=True)
    selected_group_var = tk.StringVar()
    selected_group_summary_var = tk.StringVar()
    ttk.Label(
        detail_frame,
        textvariable=selected_group_var,
        font=self.font_label,
        foreground=self.colors["text_primary"],
        background=self.colors["bg_card"],
    ).grid(
        row=0, column=0, columnspan=3, sticky="w",
        padx=int(10 * scale), pady=(int(10 * scale), 0),
    )
    ttk.Label(
        detail_frame,
        textvariable=selected_group_summary_var,
        font=(ui_theme.FONT_FAMILY, int(10 * self.font_scale)),
        foreground=self.colors["text_secondary"],
        background=self.colors["bg_card"],
        justify="left",
        wraplength=int(760 * scale),
    ).grid(
        row=1, column=0, columnspan=3, sticky="w",
        padx=int(10 * scale), pady=(int(4 * scale), int(2 * scale)),
    )

    columns = ("name", "job", "score", "task", "key_info", "due")
    tree = ttk.Treeview(
        detail_frame,
        columns=columns,
        show="headings",
        height=8,
        style="ActionQueue.Treeview",
    )
    for column, text, width, anchor in (
        ("name", "候选人", 95, "center"),
        ("job", "岗位", 135, "w"),
        ("score", "分数", 55, "center"),
        ("task", "任务类型", 165, "center"),
        ("key_info", "关键信息", 245, "w"),
        ("due", "到期", 80, "center"),
    ):
        tree.heading(column, text=text)
        tree.column(
            column,
            width=int(width * scale),
            minwidth=int(max(60, width * 0.65) * scale),
            anchor=anchor,
        )

    current_items: list[DailyActionLike] = []
    current_group_name = {"value": default_group}
    show_selected_action = {"value": False}
    scroll_y = ttk.Scrollbar(detail_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll_y.set)
    tree.grid(row=2, column=0, sticky="nsew", padx=int(10 * scale), pady=int(10 * scale))
    scroll_y.grid(row=2, column=1, sticky="ns", pady=int(10 * scale))
    detail_frame.grid_rowconfigure(2, weight=1)
    detail_frame.grid_columnconfigure(0, weight=1)

    selection_frame = ttk.Frame(
        detail_frame,
        style="Card.TFrame",
        padding=int(10 * scale),
    )
    selection_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
    selection_frame.grid_columnconfigure(0, weight=1)
    selection_reason_var = tk.StringVar(
        value="选择一位候选人，查看处理依据和下一步"
    )
    selection_action_var = tk.StringVar(value="")
    ttk.Label(
        selection_frame,
        textvariable=selection_reason_var,
        font=(ui_theme.FONT_FAMILY, int(10 * self.font_scale)),
        foreground=self.colors["text_secondary"],
        background=self.colors["bg_card"],
    ).grid(row=0, column=0, sticky="w")
    selection_action_label = ttk.Label(
        selection_frame,
        textvariable=selection_action_var,
        font=(ui_theme.FONT_FAMILY, int(10 * self.font_scale)),
        foreground=self.colors["text_primary"],
        background=self.colors["bg_card"],
    )
    selection_action_label.grid(
        row=1,
        column=0,
        sticky="w",
        pady=(int(3 * scale), 0),
    )
    selection_action_label.grid_remove()

    def open_selected_workbench() -> None:
        selection = tree.selection()
        if not selection:
            return
        try:
            item = current_items[int(selection[0])]
        except (ValueError, IndexError):
            return
        self._open_candidate_review_workbench(item.candidate)

    process_btn = ttk.Button(
        selection_frame,
        text="查看与处理",
        style="Workbench.Primary.TButton",
        command=open_selected_workbench,
        state="disabled",
    )
    process_btn.grid(
        row=0,
        column=1,
        rowspan=2,
        sticky="e",
        padx=(int(12 * scale), 0),
    )

    def update_selection_context(_event: tk.Event | None = None) -> None:
        selection = tree.selection()
        if not selection:
            selection_reason_var.set("选择一位候选人，查看处理依据和下一步")
            selection_action_var.set("")
            selection_action_label.grid_remove()
            process_btn.configure(state="disabled")
            return
        try:
            item = current_items[int(selection[0])]
        except (ValueError, IndexError):
            return
        key_info = self._format_daily_action_key_info(item)
        selection_reason_var.set(
            f"已选择：{item.name or '未命名'} · {item.job_name or '未知岗位'} · "
            f"{self._clip_table_text(key_info, 42)}"
        )
        if show_selected_action["value"]:
            selection_action_var.set(
                f"下一步：{self._clip_table_text(item.action, 72)}"
            )
            selection_action_label.grid()
        else:
            selection_action_var.set("")
            selection_action_label.grid_remove()
        process_btn.configure(state="normal")

    def populate_group(selection_key: str) -> None:
        nonlocal current_items
        current_items = action_items_by_key.get(selection_key, [])
        current_group_name["value"] = selection_key
        label = action_label_by_key.get(selection_key, selection_key)
        selected_group_var.set(f"{label}：{len(current_items)} 人")
        unique_actions = {
            " ".join(str(item.action or "").split())
            for item in current_items
            if str(item.action or "").strip()
        }
        if "::" not in selection_key:
            task_type_count = len({item.group for item in current_items})
            selected_group_summary_var.set(
                f"包含 {task_type_count} 类任务；选择左侧具体任务可查看统一处理建议。"
            )
            show_selected_action["value"] = True
        elif len(unique_actions) == 1:
            selected_group_summary_var.set(
                f"处理建议：{next(iter(unique_actions))}"
            )
            show_selected_action["value"] = False
        else:
            selected_group_summary_var.set(
                f"本组包含 {len(unique_actions)} 种处理方式；"
                "选中候选人后查看对应下一步。"
            )
            show_selected_action["value"] = True
        tree.delete(*tree.get_children())
        for index, item in enumerate(current_items):
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    item.name or "未命名",
                    item.job_name or "未知岗位",
                    item.score,
                    item.group,
                    self._format_daily_action_key_info(item),
                    self._format_daily_action_due(item),
                ),
            )
        if current_items:
            tree.selection_set("0")
            tree.focus("0")
        update_selection_context()

    def on_group_selected(_event: tk.Event | None = None) -> None:
        selection = group_tree.selection()
        if selection:
            populate_group(action_group_by_iid.get(selection[0], ""))

    def refresh_current_daily_actions() -> None:
        nonlocal all_items
        try:
            refreshed_items = list(load_actions())
        except Exception:
            refreshed_items = []
        all_items = refreshed_items
        refreshed_counts = daily_counts(all_items)
        for key, value in refreshed_counts.items():
            metric_vars[key].set(str(value))
        rebuild_action_group_tree()
        preferred = (
            current_group_name["value"]
            if current_group_name["value"] in action_items_by_key
            else next(iter(action_items_by_key), "")
        )
        if preferred:
            preferred_iid = action_iid_by_key[preferred]
            group_tree.selection_set(preferred_iid)
            group_tree.focus(preferred_iid)
            populate_group(preferred)
        else:
            current_items.clear()
            selected_group_var.set("暂无需要优先处理的候选人")
            selected_group_summary_var.set("当前范围内没有需要优先处理的候选人。")
            tree.delete(*tree.get_children())
            update_selection_context()

    def show_detail(_event: tk.Event | None = None) -> None:
        selection = tree.selection()
        if not selection:
            return
        item = current_items[int(selection[0])]
        detail = "\n".join([
            f"分组：{item.group}",
            f"候选人：{item.name or '未命名'}",
            f"岗位：{item.job_name or '未知岗位'}",
            f"分数：{item.score}",
            f"时间分组：{item.timing_group}",
            f"到期日期：{format_followup_due_at(item.due_at) if item.due_at else '未安排'}",
            "",
            f"为什么处理：{item.reason}",
            f"下一步：{item.action}",
        ])
        self._show_text_dialog("今日待办详情", detail, width=620, height=360)

    tree.bind("<Double-Button-1>", show_detail)
    tree.bind("<<TreeviewSelect>>", update_selection_context)

    def show_action_context_menu(event: tk.Event) -> None:
        item_id = tree.identify_row(event.y)
        if not item_id:
            return
        tree.selection_set(item_id)
        try:
            item = current_items[int(item_id)]
        except (ValueError, IndexError):
            return
        primary = (
            "queue"
            if item.group == "待打招呼"
            else (
                "confirm"
                if item.group == "待复核"
                else (
                    "resume"
                    if item.group == "待完成简历评估"
                    else (
                        "followup"
                        if item.group in (
                            "已打招呼待跟进",
                            "已回复待推进",
                            "待约面待推进",
                            "面试后待反馈",
                        )
                        else None
                    )
                )
            )
        )
        self._show_candidate_workflow_context_menu(
            win,
            item.candidate,
            event.x_root,
            event.y_root,
            refresh_fn=refresh_current_daily_actions,
            primary_action=primary,
        )

    def on_action_motion(event: tk.Event) -> None:
        item_id = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        if not item_id or column_id != "#5":
            self._hide_tooltip()
            return
        try:
            item = current_items[int(item_id)]
        except (ValueError, IndexError):
            self._hide_tooltip()
            return
        full = item.reason
        tooltip_key = ("daily_actions", item_id, column_id)
        if (
            tooltip_key == getattr(self, "_tooltip_item", None)
            and getattr(self, "_tooltip", None)
            and self._tooltip.winfo_exists()
        ):
            return
        after_id = getattr(self, "_tooltip_after_id", None)
        if after_id:
            self.root.after_cancel(after_id)
        x = event.x_root + int(12 * scale)
        y = event.y_root + int(12 * scale)
        self._tooltip_item = tooltip_key
        self._tooltip_after_id = self.root.after(
            250,
            lambda: self._show_tooltip(full, x, y, tooltip_key, parent=win),
        )

    tree.bind("<Motion>", on_action_motion)
    tree.bind("<Leave>", self._hide_tooltip)
    tree.bind("<Button-3>", show_action_context_menu)
    group_tree.bind("<<TreeviewSelect>>", on_group_selected)
    if default_group:
        default_iid = action_iid_by_key[default_group]
        group_tree.selection_set(default_iid)
        group_tree.focus(default_iid)
        populate_group(default_group)

    btn_row = ttk.Frame(
        win,
        style="Page.TFrame",
        padding=(int(16 * scale), 0, int(16 * scale), int(14 * scale)),
    )
    btn_row.pack(fill="x")
    ttk.Button(
        btn_row,
        text="导出报告",
        command=lambda: export_report(win),
    ).pack(side="left")
    ttk.Button(btn_row, text="关闭", command=win.destroy).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    place_window_centered(win, int(1120 * scale), int(680 * scale), parent=self.root)
    win.deiconify()
    return win
