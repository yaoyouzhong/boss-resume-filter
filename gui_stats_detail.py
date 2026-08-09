"""Tk dialog shared by home and result-page candidate statistics."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, ttk
from typing import Any

from ui_messagebox import messagebox


Candidate = dict[str, Any]
CandidateRef = list[list[Candidate]]


@dataclass(frozen=True)
class StatsDetailCallbacks:
    """Business callbacks kept in the main controller."""

    row_values: Callable[[Candidate], tuple[Any, ...]]
    export_candidates: Callable[[list[Candidate], str], None]
    add_to_queue: Callable[[list[Candidate], tk.Misc], Any]
    batch_ai_eval_label: Callable[[list[Candidate]], str | None]
    evaluate_candidates: Callable[[list[Candidate]], Any]
    confirm_manual_review: Callable[[list[Candidate], tk.Misc], Any]
    open_review: Callable[[Candidate, list[Candidate]], Any]
    show_candidate_menu: Callable[..., Any]
    bind_tooltip: Callable[[ttk.Treeview, CandidateRef], Any]
    remove_candidates: Callable[[list[Candidate]], list[Candidate]]
    refresh: Callable[[], Any]


@dataclass(frozen=True)
class StatsDetailWidgets:
    """Widget references useful to the controller and GUI smoke tests."""

    window: tk.Toplevel
    tree: ttk.Treeview
    candidates_ref: CandidateRef
    greeted_label: ttk.Label


def _selected_candidates(
    tree: ttk.Treeview,
    selection: Sequence[str],
) -> list[Candidate]:
    candidate_map = getattr(tree, "_candidate_map", {}) or {}
    return [candidate_map[item] for item in selection if item in candidate_map]


def _export_selected(
    candidates: list[Candidate],
    callbacks: StatsDetailCallbacks,
) -> None:
    if not candidates:
        messagebox.showwarning("警告", "请先选择要导出的候选人")
        return
    if len(candidates) == 1:
        initial_name = f"{candidates[0].get('name', '候选人')}.xlsx"
    else:
        initial_name = (
            f"{candidates[0].get('name', '候选人')}等{len(candidates)}人_"
            f"{datetime.now():%Y%m%d}.xlsx"
        )
    file_path = filedialog.asksaveasfilename(
        title="保存选中的候选人",
        defaultextension=".xlsx",
        filetypes=[("Excel 文件", "*.xlsx")],
        initialfile=initial_name,
    )
    if file_path:
        callbacks.export_candidates(candidates, file_path)


def _update_after_removal(
    tree: ttk.Treeview,
    candidates_ref: CandidateRef,
    removed: Sequence[Candidate],
    greeted_label: ttk.Label,
) -> None:
    removed_ids = {id(candidate) for candidate in removed}
    candidates_ref[0] = [
        candidate
        for candidate in candidates_ref[0]
        if id(candidate) not in removed_ids
    ]
    candidate_map = getattr(tree, "_candidate_map", {}) or {}
    for item, candidate in list(candidate_map.items()):
        if id(candidate) in removed_ids:
            candidate_map.pop(item, None)
            tree.delete(item)
    greeted_count = sum(
        bool(candidate.get("greet_sent")) for candidate in candidates_ref[0]
    )
    greeted_label.configure(text=f"，已打招呼 {greeted_count} 人")


def _confirm_remove(
    parent: tk.Misc,
    candidates: Sequence[Candidate],
) -> bool:
    if len(candidates) == 1:
        headline = f"移除 {candidates[0].get('name') or '该候选人'}？"
        message = "该记录将从当前结果和本地候选人数据中移除。"
    else:
        headline = f"移除选中的 {len(candidates)} 名候选人？"
        message = "这些记录将从当前结果和本地候选人数据中移除。"
    return messagebox.ask_confirmation(
        "移除候选人",
        headline=headline,
        message=message,
        notice=(
            "无人继续引用的受管简历副本也会删除，共享副本保留；"
            "重新扫描时仍可能再次发现这些候选人。"
        ),
        yes_label="移除候选人",
        no_label="取消",
        dangerous=True,
        parent=parent,
    )


def _remove_selected(
    window: tk.Toplevel,
    tree: ttk.Treeview,
    candidates_ref: CandidateRef,
    candidates: list[Candidate],
    greeted_label: ttk.Label,
    callbacks: StatsDetailCallbacks,
    *,
    lift_after_remove: bool = True,
) -> None:
    if not candidates or not _confirm_remove(window, candidates):
        return
    removed = callbacks.remove_candidates(candidates)
    if not removed:
        return
    _update_after_removal(tree, candidates_ref, removed, greeted_label)
    callbacks.refresh()
    if lift_after_remove:
        window.lift()


def _show_batch_menu(
    window: tk.Toplevel,
    tree: ttk.Treeview,
    candidates_ref: CandidateRef,
    selection: Sequence[str],
    greeted_label: ttk.Label,
    callbacks: StatsDetailCallbacks,
    event: tk.Event,
    *,
    font_family: str,
    font_scale: float,
    colors: Mapping[str, str],
    icons: Any,
    lift_after_batch_remove: bool,
) -> None:
    selected = _selected_candidates(tree, selection)
    menu = tk.Menu(
        window,
        tearoff=0,
        font=(font_family, int(11 * font_scale)),
    )
    icon_export = icons.button("export", colors["text_primary"])
    icon_trash = icons.button("trash", colors["text_primary"])
    icon_greet = icons.button("chat", colors["success"])
    menu._icon_refs = [icon_export, icon_trash, icon_greet]
    menu.add_command(
        label=" 加入联系清单",
        image=icon_greet,
        compound=tk.LEFT,
        command=lambda: callbacks.add_to_queue(selected, window),
    )
    ai_label = callbacks.batch_ai_eval_label(selected)
    if ai_label:
        icon_ai_eval = icons.button("ai_spark", colors["primary"])
        menu._icon_refs.append(icon_ai_eval)
        menu.add_command(
            label=ai_label,
            image=icon_ai_eval,
            compound=tk.LEFT,
            command=lambda: callbacks.evaluate_candidates(selected),
        )
    if any(candidate.get("manual_review_required") for candidate in selected):
        icon_confirm = icons.button("stamp_check", colors["success"])
        menu._icon_refs.append(icon_confirm)
        menu.add_command(
            label=" 批量确认通过",
            image=icon_confirm,
            compound=tk.LEFT,
            command=lambda: callbacks.confirm_manual_review(selected, window),
        )
    menu.add_command(
        label=" 移除选中",
        image=icon_trash,
        compound=tk.LEFT,
        command=lambda: _remove_selected(
            window,
            tree,
            candidates_ref,
            selected,
            greeted_label,
            callbacks,
            lift_after_remove=lift_after_batch_remove,
        ),
    )
    menu.add_separator()
    menu.add_command(
        label=" 导出选中",
        image=icon_export,
        compound=tk.LEFT,
        command=lambda: _export_selected(selected, callbacks),
    )
    menu.tk_popup(event.x_root, event.y_root)


def _bind_tree_actions(
    window: tk.Toplevel,
    tree: ttk.Treeview,
    candidates_ref: CandidateRef,
    greeted_label: ttk.Label,
    callbacks: StatsDetailCallbacks,
    *,
    font_family: str,
    font_scale: float,
    colors: Mapping[str, str],
    icons: Any,
    lift_after_batch_remove: bool,
) -> None:
    def on_right_click(event: tk.Event) -> None:
        clicked_item = tree.identify_row(event.y)
        if not clicked_item:
            return
        if clicked_item not in tree.selection():
            tree.selection_set(clicked_item)
        selection = tree.selection()
        if len(selection) > 1:
            _show_batch_menu(
                window,
                tree,
                candidates_ref,
                selection,
                greeted_label,
                callbacks,
                event,
                font_family=font_family,
                font_scale=font_scale,
                colors=colors,
                icons=icons,
                lift_after_batch_remove=lift_after_batch_remove,
            )
            return
        candidate = _selected_candidates(tree, (clicked_item,))
        if not candidate:
            return
        selected_candidate = candidate[0]
        callbacks.show_candidate_menu(
            parent=window,
            tree=tree,
            tree_item=clicked_item,
            candidate=selected_candidate,
            show_detail_fn=lambda: callbacks.open_review(
                selected_candidate,
                candidates_ref[0],
            ),
            remove_fn=lambda: _remove_selected(
                window,
                tree,
                candidates_ref,
                [selected_candidate],
                greeted_label,
                callbacks,
            ),
            x_root=event.x_root,
            y_root=event.y_root,
        )

    def on_double_click(event: tk.Event) -> None:
        clicked_item = tree.identify_row(event.y)
        candidate = _selected_candidates(tree, (clicked_item,)) if clicked_item else []
        if candidate:
            callbacks.open_review(candidate[0], candidates_ref[0])

    tree.bind("<Button-3>", on_right_click)
    tree.bind("<Double-Button-1>", on_double_click)
    callbacks.bind_tooltip(tree, candidates_ref)


def show_stats_detail_dialog(
    host: Any,
    *,
    title: str,
    candidates: Sequence[Candidate],
    ui_config: Mapping[str, Any],
    font_family: str,
    callbacks: StatsDetailCallbacks,
    lift_after_batch_remove: bool = False,
) -> StatsDetailWidgets:
    """Build a non-modal statistics detail dialog for filtered candidates."""
    scale = host.dpi_scale * host.zoom_factor
    window = tk.Toplevel(host.root)
    window.transient(host.root)
    window.title(title)
    window.configure(bg=host.colors["bg_main"])

    window_width = min(1280, host.root.winfo_width() - 100)
    window_height = min(900, host.root.winfo_height() - 80)
    host._center_window(window, window_width, window_height)

    ttk.Label(
        window,
        text=title,
        font=(font_family, int(13 * host.font_scale)),
        foreground=host.colors["primary"],
        background=host.colors["bg_main"],
    ).pack(fill="x", padx=int(20 * scale), pady=(int(15 * scale), 0))

    count_frame = ttk.Frame(window, style="Page.TFrame")
    count_frame.pack(
        anchor="w",
        padx=int(20 * scale),
        pady=(int(5 * scale), 0),
    )
    count_font = (font_family, int(11 * host.font_scale))
    ttk.Label(
        count_frame,
        text=f"共 {len(candidates)} 人",
        font=count_font,
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_main"],
    ).pack(side="left")
    greeted_count = sum(bool(candidate.get("greet_sent")) for candidate in candidates)
    greeted_label = ttk.Label(
        count_frame,
        text=f"，已打招呼 {greeted_count} 人",
        font=count_font,
        foreground=host.colors["success"],
        background=host.colors["bg_main"],
    )
    greeted_label.pack(side="left")

    table_frame = ttk.Frame(window, style="Card.TFrame")
    table_frame.pack(
        fill="both",
        expand=True,
        padx=int(20 * scale),
        pady=int(15 * scale),
    )
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
    )
    tree = ttk.Treeview(
        table_frame,
        columns=columns,
        show="headings",
        height=18,
    )
    tree._candidate_map = {}
    headings = (
        ("name", "姓名", 80, 60),
        ("gender", "性别", 60, 50),
        ("exp", "工作年限", 110, 100),
        ("salary", "薪资", 100, 80),
        ("skills", "技能匹配", 140, 100),
        ("score", "匹配分", 90, 80),
        ("ai_eval", "AI评估", 90, 80),
        ("level", "推荐指数", 120, 100),
        ("status", "状态", 220, 180),
    )
    for name, label, width, min_width in headings:
        tree.heading(name, text=label)
        tree.column(name, width=width, minwidth=min_width, anchor="center")

    style = ttk.Style()
    style.configure(
        "Detail.Treeview",
        font=(font_family, int(11 * host.font_scale)),
        rowheight=int(ui_config["treeview_rowheight"] * scale),
    )
    style.configure(
        "Detail.Treeview.Heading",
        font=(font_family, int(11 * host.font_scale), "bold"),
    )
    tree.configure(style="Detail.Treeview")
    scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    candidates_ref: CandidateRef = [list(candidates)]
    for candidate in sorted(
        candidates_ref[0],
        key=lambda item: item.get("match_score", 0),
        reverse=True,
    ):
        item_id = tree.insert("", "end", values=callbacks.row_values(candidate))
        tree._candidate_map[item_id] = candidate

    _bind_tree_actions(
        window,
        tree,
        candidates_ref,
        greeted_label,
        callbacks,
        font_family=font_family,
        font_scale=host.font_scale,
        colors=host.colors,
        icons=host.icons,
        lift_after_batch_remove=lift_after_batch_remove,
    )
    host._center_window(window, window_width, window_height)
    return StatsDetailWidgets(window, tree, candidates_ref, greeted_label)
