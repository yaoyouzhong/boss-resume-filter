"""Tk dialog for candidate-state diagnostics."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping, Sequence
from tkinter import ttk
from typing import Any, Protocol

import ui_theme
import gui_candidate_workbench
from job_identity import normalize_job_name
from ui_windowing import place_window_centered


class StateIssueLike(Protocol):
    """Fields displayed by the candidate-state diagnostics dialog."""

    candidate_key: str
    name: str
    job_name: str
    severity: str
    title: str
    detail: str
    suggestion: str


class CandidateDiagnosticsHost(Protocol):
    """Narrow GUI contract required by the diagnostics workbench."""

    root: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    font_label: Any
    _tooltip_item: Any
    _tooltip: Any
    _tooltip_after_id: Any

    def _format_state_issue_key_info(
        self,
        issue: StateIssueLike,
        candidate: Mapping[str, Any] | None,
    ) -> str: ...

    def _clip_table_text(self, text: object, limit: int) -> str: ...

    def _open_candidate_review_workbench(self, candidate: Mapping[str, Any]) -> None: ...

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
        candidate: Mapping[str, Any] | None,
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


Candidate = Mapping[str, Any]
LoadDiagnostics = Callable[[], tuple[Sequence[Candidate], Sequence[StateIssueLike]]]
ExportReport = Callable[[tk.Misc], None]


def show_candidate_state_diagnostics_dialog(
    self: CandidateDiagnosticsHost,
    scope: str,
    candidates: Sequence[Candidate],
    issues: Sequence[StateIssueLike],
    *,
    load_diagnostics: LoadDiagnostics,
    export_report: ExportReport,
    ui_config: Mapping[str, Any],
) -> tk.Toplevel:
    """Build and show the diagnostics dialog without loading or writing business data."""
    win = tk.Toplevel(self.root)
    win.title("候选人状态体检")
    win.transient(self.root)
    win.withdraw()
    scale = self.dpi_scale * self.zoom_factor

    body = ttk.Frame(win, style="Page.TFrame", padding=int(16 * scale))
    body.pack(fill="both", expand=True)

    counts = {
        "error": sum(1 for item in issues if item.severity == "error"),
        "warning": sum(1 for item in issues if item.severity == "warning"),
        "info": sum(1 for item in issues if item.severity == "info"),
    }
    gui_candidate_workbench.create_header(
        self,
        body,
        "候选人状态体检",
        "定位状态冲突和待补信息，并给出可执行的处理建议",
        scope,
    )
    metric_vars = gui_candidate_workbench.create_metrics(self, body, (
        ("candidates", "检查人数", len(candidates), self.colors["primary"]),
        ("error", "严重", counts["error"], self.colors["danger"]),
        ("warning", "提醒", counts["warning"], self.colors["warning"]),
        ("info", "建议", counts["info"], self.colors["primary"]),
    ))

    content = ttk.Frame(body, style="Page.TFrame")
    content.pack(fill="both", expand=True)
    state_style = ttk.Style()
    state_style.configure(
        "StateCheck.Treeview",
        font=(ui_theme.FONT_FAMILY, int(11 * self.font_scale)),
        rowheight=int(ui_config["treeview_rowheight"] * scale),
    )
    state_style.configure(
        "StateCheck.Treeview.Heading",
        font=(ui_theme.FONT_FAMILY, int(11 * self.font_scale), "bold"),
    )

    severity_label = {"error": "严重", "warning": "提醒", "info": "建议"}
    severity_rank = {"error": 0, "warning": 1, "info": 2}

    def rebuild_issue_groups(refreshed_issues: Sequence[StateIssueLike]):
        grouped: dict[str, list[StateIssueLike]] = {}
        if refreshed_issues:
            for issue in sorted(
                refreshed_issues,
                key=lambda item: (
                    severity_rank.get(item.severity, 9),
                    item.title,
                    item.name,
                ),
            ):
                grouped.setdefault(issue.title, []).append(issue)
        else:
            grouped["未发现问题"] = []
        return grouped

    grouped_issues = rebuild_issue_groups(issues)

    def candidate_key_for(candidate: Candidate) -> str:
        geek_id = str(candidate.get("geek_id") or "").strip()
        job_name = " ".join(str(candidate.get("job_name") or "").strip().split())
        name = " ".join(str(candidate.get("name") or "").strip().split())
        if geek_id and job_name:
            return f"{geek_id}:{job_name}"
        return geek_id or name or "unknown"

    candidate_by_key = {
        candidate_key_for(candidate): candidate for candidate in candidates
    }

    def candidate_for_issue(issue: StateIssueLike) -> Candidate | None:
        candidate = candidate_by_key.get(issue.candidate_key)
        if candidate:
            return candidate
        for item in candidate_by_key.values():
            if (
                (not issue.name or item.get("name") == issue.name)
                and (
                    not issue.job_name
                    or normalize_job_name(item.get("job_name"))
                    == normalize_job_name(issue.job_name)
                )
            ):
                return item
        return None

    nav_frame = ttk.Frame(content, style="Card.TFrame", padding=int(10 * scale))
    nav_frame.pack(side="left", fill="y", padx=(0, int(10 * scale)))
    ttk.Label(
        nav_frame,
        text="按问题筛选",
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
        columns=("count", "level"),
        show="tree",
        height=10,
        style=navigation_style,
        selectmode="browse",
    )
    group_tree.column("#0", width=int(230 * scale), minwidth=int(180 * scale), anchor="w")
    group_tree.column("count", width=0, minwidth=0, stretch=False)
    group_tree.column("level", width=0, minwidth=0, stretch=False)
    gui_candidate_workbench.apply_navigation_tags(self, group_tree)
    group_tree.pack(fill="y", expand=True)

    issue_group_by_iid: dict[str, str] = {}
    issue_items_by_key: dict[str, list[StateIssueLike]] = {}
    issue_label_by_key: dict[str, str] = {}
    issue_iid_by_key: dict[str, str] = {}

    def rebuild_issue_navigation() -> None:
        group_tree.delete(*group_tree.get_children())
        issue_group_by_iid.clear()
        issue_items_by_key.clear()
        issue_label_by_key.clear()
        issue_iid_by_key.clear()
        has_issues = any(grouped_issues.values())
        if not has_issues:
            key = "severity::clear"
            iid = "severity_clear"
            issue_group_by_iid[iid] = key
            issue_items_by_key[key] = []
            issue_label_by_key[key] = "未发现问题"
            issue_iid_by_key[key] = iid
            group_tree.insert(
                "", "end", iid=iid, text="通过 · 未发现问题",
                values=(0, "通过"), tags=("workbench_root",),
            )
            return

        for level_index, level in enumerate(("error", "warning", "info")):
            level_items = [
                issue
                for values in grouped_issues.values()
                for issue in values
                if issue.severity == level
            ]
            if not level_items:
                continue
            level_key = f"severity::{level}"
            level_iid = f"severity_{level_index}"
            level_text = severity_label[level]
            issue_group_by_iid[level_iid] = level_key
            issue_items_by_key[level_key] = level_items
            issue_label_by_key[level_key] = level_text
            issue_iid_by_key[level_key] = level_iid
            group_tree.insert(
                "", "end", iid=level_iid,
                text=f"{level_text}  {len(level_items)}",
                values=(len(level_items), level_text),
                tags=("workbench_root",),
                open=True,
            )
            child_index = 0
            for title, values in grouped_issues.items():
                title_items = [issue for issue in values if issue.severity == level]
                if not title_items:
                    continue
                child_key = f"issue::{level}::{child_index}"
                child_iid = f"{level_iid}_issue_{child_index}"
                issue_group_by_iid[child_iid] = child_key
                issue_items_by_key[child_key] = title_items
                issue_label_by_key[child_key] = title
                issue_iid_by_key[child_key] = child_iid
                group_tree.insert(
                    level_iid, "end", iid=child_iid,
                    text=f"{title}  {len(title_items)}",
                    values=(len(title_items), level_text),
                    tags=("workbench_child",),
                )
                child_index += 1

    rebuild_issue_navigation()

    detail_frame = ttk.Frame(content, style="Card.TFrame")
    detail_frame.pack(side="left", fill="both", expand=True)
    selected_issue_group_var = tk.StringVar()
    selected_issue_summary_var = tk.StringVar()
    ttk.Label(
        detail_frame,
        textvariable=selected_issue_group_var,
        font=self.font_label,
        foreground=self.colors["text_primary"],
        background=self.colors["bg_card"],
    ).grid(
        row=0, column=0, columnspan=3, sticky="w",
        padx=int(10 * scale), pady=(int(10 * scale), 0),
    )
    ttk.Label(
        detail_frame,
        textvariable=selected_issue_summary_var,
        font=(ui_theme.FONT_FAMILY, int(10 * self.font_scale)),
        foreground=self.colors["text_secondary"],
        background=self.colors["bg_card"],
        justify="left",
        wraplength=int(760 * scale),
    ).grid(
        row=1, column=0, columnspan=3, sticky="w",
        padx=int(10 * scale), pady=(int(4 * scale), int(2 * scale)),
    )

    columns = ("name", "job", "issue", "key_info")
    tree = ttk.Treeview(
        detail_frame,
        columns=columns,
        show="headings",
        height=8,
        style="StateCheck.Treeview",
    )
    tree.heading("name", text="候选人")
    tree.heading("job", text="岗位")
    tree.heading("issue", text="问题类型")
    tree.heading("key_info", text="关键信息")
    tree.column("name", width=int(105 * scale), minwidth=85, anchor="center")
    tree.column("job", width=int(145 * scale), minwidth=110, anchor="w")
    tree.column("issue", width=int(205 * scale), minwidth=160, anchor="w")
    tree.column("key_info", width=int(325 * scale), minwidth=220, anchor="w")
    current_issues: list[StateIssueLike] = []
    current_issue_group_name = {"value": next(iter(issue_items_by_key), "")}
    scroll_y = ttk.Scrollbar(detail_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll_y.set)
    tree.grid(row=2, column=0, sticky="nsew", padx=int(10 * scale), pady=int(10 * scale))
    scroll_y.grid(row=2, column=1, sticky="ns", pady=int(10 * scale))
    detail_frame.grid_rowconfigure(2, weight=1)
    detail_frame.grid_columnconfigure(0, weight=1)

    issue_context = ttk.Frame(detail_frame, style="Card.TFrame", padding=int(10 * scale))
    issue_context.grid(row=3, column=0, columnspan=2, sticky="ew")
    issue_context.grid_columnconfigure(0, weight=1)
    issue_detail_var = tk.StringVar(value="选择一位候选人，查看并处理")
    ttk.Label(
        issue_context,
        textvariable=issue_detail_var,
        font=(ui_theme.FONT_FAMILY, int(10 * self.font_scale)),
        foreground=self.colors["text_secondary"],
        background=self.colors["bg_card"],
    ).grid(row=0, column=0, sticky="w")

    def open_selected_candidate() -> None:
        selection = tree.selection()
        if not selection or not current_issues:
            return
        try:
            candidate = candidate_for_issue(current_issues[int(selection[0])])
        except (ValueError, IndexError):
            return
        if candidate:
            self._open_candidate_review_workbench(candidate)

    inspect_btn = ttk.Button(
        issue_context,
        text="查看与处理",
        style="Workbench.Primary.TButton",
        command=open_selected_candidate,
        state="disabled",
    )
    inspect_btn.grid(row=0, column=1, sticky="e", padx=(int(12 * scale), 0))

    def update_issue_context(_event: tk.Event | None = None) -> None:
        selection = tree.selection()
        if not selection or not current_issues:
            issue_detail_var.set(
                "状态正常，无需处理" if not current_issues else "选择一位候选人，查看并处理"
            )
            inspect_btn.configure(state="disabled")
            return
        try:
            issue = current_issues[int(selection[0])]
        except (ValueError, IndexError):
            return
        candidate = candidate_for_issue(issue)
        name = issue.name or issue.candidate_key
        job_name = issue.job_name or "未知岗位"
        key_info = self._format_state_issue_key_info(issue, candidate)
        issue_detail_var.set(
            f"已选择：{name} · {job_name} · {self._clip_table_text(key_info, 46)}"
        )
        inspect_btn.configure(state="normal" if candidate else "disabled")

    def populate_issue_group(selection_key: str) -> None:
        nonlocal current_issues
        current_issues = issue_items_by_key.get(selection_key, [])
        current_issue_group_name["value"] = selection_key
        tree.delete(*tree.get_children())
        if not current_issues:
            selected_issue_group_var.set("未发现明显状态冲突。")
            selected_issue_summary_var.set("当前范围内没有需要处理的候选人状态问题。")
            update_issue_context()
            return
        label = issue_label_by_key.get(selection_key, selection_key)
        selected_issue_group_var.set(f"{label}：{len(current_issues)} 项")
        if selection_key.startswith("severity::"):
            issue_type_count = len({issue.title for issue in current_issues})
            selected_issue_summary_var.set(
                f"包含 {issue_type_count} 类问题；选择左侧具体问题可查看统一处理建议。"
            )
        else:
            selected_issue_summary_var.set(f"处理建议：{current_issues[0].suggestion}")
        for index, issue in enumerate(current_issues):
            candidate = candidate_for_issue(issue)
            tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    issue.name or issue.candidate_key,
                    issue.job_name or "未知岗位",
                    issue.title,
                    self._format_state_issue_key_info(issue, candidate),
                ),
                tags=(issue.severity,),
            )
        tree.selection_set("0")
        tree.focus("0")
        update_issue_context()

    def on_issue_group_selected(_event: tk.Event | None = None) -> None:
        selection = group_tree.selection()
        if selection:
            populate_issue_group(issue_group_by_iid.get(selection[0], ""))

    def on_issue_group_motion(event: tk.Event) -> None:
        item_id = group_tree.identify_row(event.y)
        column_id = group_tree.identify_column(event.x)
        selection_key = issue_group_by_iid.get(item_id, "")
        label = issue_label_by_key.get(selection_key, "")
        if not item_id or column_id != "#0" or not label:
            self._hide_tooltip()
            return
        tooltip_key = ("state_check_group", item_id, column_id)
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
            lambda: self._show_tooltip(label, x, y, tooltip_key, parent=win),
        )

    def refresh_current_state_diagnostics() -> None:
        nonlocal grouped_issues, current_issues
        try:
            refreshed_candidates, refreshed_issues = load_diagnostics()
        except Exception:
            refreshed_candidates = []
            refreshed_issues = []
        candidate_by_key.clear()
        candidate_by_key.update({
            candidate_key_for(candidate): candidate
            for candidate in refreshed_candidates
        })
        grouped_issues = rebuild_issue_groups(refreshed_issues)
        refreshed_counts = {
            "candidates": len(refreshed_candidates),
            "error": sum(1 for item in refreshed_issues if item.severity == "error"),
            "warning": sum(1 for item in refreshed_issues if item.severity == "warning"),
            "info": sum(1 for item in refreshed_issues if item.severity == "info"),
        }
        for key, value in refreshed_counts.items():
            metric_vars[key].set(str(value))
        rebuild_issue_navigation()
        preferred = (
            current_issue_group_name["value"]
            if current_issue_group_name["value"] in issue_items_by_key
            else next(iter(issue_items_by_key), "")
        )
        if preferred:
            preferred_iid = issue_iid_by_key[preferred]
            group_tree.selection_set(preferred_iid)
            group_tree.focus(preferred_iid)
            populate_issue_group(preferred)

    def show_detail(_event: tk.Event | None = None) -> None:
        selection = tree.selection()
        if not selection or not current_issues:
            return
        issue = current_issues[int(selection[0])]
        detail = "\n".join([
            f"级别：{severity_label.get(issue.severity, '提醒')}",
            f"候选人：{issue.name or issue.candidate_key}",
            f"岗位：{issue.job_name or '未知岗位'}",
            f"问题：{issue.title}",
            "",
            f"说明：{issue.detail}",
            f"建议：{issue.suggestion}",
        ])
        self._show_text_dialog("状态体检详情", detail, width=620, height=360)

    tree.bind("<Double-Button-1>", show_detail)
    tree.bind("<<TreeviewSelect>>", update_issue_context)

    def show_state_context_menu(event: tk.Event) -> None:
        item_id = tree.identify_row(event.y)
        if not item_id or not current_issues:
            return
        tree.selection_set(item_id)
        try:
            issue = current_issues[int(item_id)]
        except (ValueError, IndexError):
            return
        candidate = candidate_for_issue(issue)
        primary = "confirm" if issue.title == "需要人工确认" else None
        self._show_candidate_workflow_context_menu(
            win,
            candidate,
            event.x_root,
            event.y_root,
            refresh_fn=refresh_current_state_diagnostics,
            primary_action=primary,
        )

    def on_state_motion(event: tk.Event) -> None:
        item = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        if not item or column_id not in ("#3", "#4") or not current_issues:
            self._hide_tooltip()
            return
        try:
            issue = current_issues[int(item)]
        except (ValueError, IndexError):
            self._hide_tooltip()
            return
        if column_id == "#3":
            full = f"{issue.title}\n\n{issue.detail}"
        else:
            full = issue.detail
        tooltip_key = ("state_check", item, column_id)
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

    tree.bind("<Motion>", on_state_motion)
    tree.bind("<Leave>", self._hide_tooltip)
    tree.bind("<Button-3>", show_state_context_menu)
    group_tree.bind("<<TreeviewSelect>>", on_issue_group_selected)
    group_tree.bind("<Motion>", on_issue_group_motion)
    group_tree.bind("<Leave>", self._hide_tooltip)
    default_group = next(iter(issue_items_by_key), "")
    if default_group:
        default_iid = issue_iid_by_key[default_group]
        group_tree.selection_set(default_iid)
        group_tree.focus(default_iid)
        populate_issue_group(default_group)

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
