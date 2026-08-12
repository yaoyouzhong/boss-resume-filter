"""Tk construction for the evidence-first job review workbench."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Protocol

from ui_windowing import create_toplevel, get_windows_monitor_area, place_window_centered


class ScrollSupport(Protocol):
    def bind_mousewheel(self, canvas: tk.Canvas, content: tk.Misc) -> None: ...


class WidgetSupport(Protocol):
    def create_card(
        self,
        parent: tk.Misc,
        title: str,
        **kwargs: Any,
    ) -> tk.Misc: ...


class JobReviewHost(Protocol):
    """Narrow UI contract used by the job review builder."""

    root: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    scroll_support: ScrollSupport
    widget_support: WidgetSupport


@dataclass(frozen=True)
class JobReviewCallbacks:
    """Business actions supplied by the main controller."""

    show_feedback_candidates: Callable[[], Any]
    open_job_config: Callable[[], Any]
    format_suggestion: Callable[[object], tuple[str, str]]


@dataclass(frozen=True)
class JobReviewWidgets:
    """Top-level references exposed for controller and GUI smoke tests."""

    window: tk.Toplevel
    canvas: tk.Canvas
    content: tk.Frame
    close: Callable[[], None]


def _add_header(
    host: JobReviewHost,
    parent: tk.Misc,
    *,
    job_name: str,
    time_range: str,
    review: Mapping[str, Any],
    font_family: str,
    scale: float,
) -> None:
    header = tk.Frame(parent, bg=host.colors["bg_main"])
    header.pack(fill="x", pady=(0, int(14 * scale)))
    tk.Label(
        header,
        text=f"{job_name} · 岗位复盘",
        font=(font_family, int(17 * host.font_scale), "bold"),
        fg=host.colors["text_primary"],
        bg=host.colors["bg_main"],
    ).pack(anchor="w")
    tk.Label(
        header,
        text=(
            f"数据范围：{time_range}    "
            f"复盘样本：{review['candidate_count']} 人"
        ),
        font=(font_family, int(10 * host.font_scale)),
        fg=host.colors["text_secondary"],
        bg=host.colors["bg_main"],
    ).pack(anchor="w", pady=(int(3 * scale), 0))


def _add_metrics(
    host: JobReviewHost,
    parent: tk.Misc,
    review: Mapping[str, Any],
    *,
    font_family: str,
    scale: float,
) -> None:
    metrics = tk.Frame(parent, bg=host.colors["bg_main"])
    metrics.pack(fill="x", pady=(0, int(14 * scale)))
    average_score = review["avg_score"]
    metric_items = (
        ("通过筛选", review["qualified_count"], host.colors["primary"]),
        ("已打招呼", review["greeted_count"], host.colors["warning_text"]),
        ("已回复", review["replied_count"], host.colors["success"]),
        ("已约面", review["interviewed_count"], host.colors["purple"]),
        (
            "平均分",
            f"{average_score:.1f}" if average_score is not None else "—",
            host.colors["text_primary"],
        ),
    )
    for column, (label, value, color) in enumerate(metric_items):
        metrics.grid_columnconfigure(
            column,
            weight=1,
            uniform="job_review_metric",
        )
        card = tk.Frame(
            metrics,
            bg=host.colors["bg_card"],
            highlightbackground=host.colors["border"],
            highlightthickness=1,
        )
        card.grid(
            row=0,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else int(5 * scale), 0),
        )
        tk.Label(
            card,
            text=label,
            font=(font_family, int(10 * host.font_scale)),
            fg=host.colors["text_secondary"],
            bg=host.colors["bg_card"],
        ).pack(anchor="w", padx=int(12 * scale), pady=(int(10 * scale), 0))
        tk.Label(
            card,
            text=str(value),
            font=(font_family, int(18 * host.font_scale), "bold"),
            fg=color,
            bg=host.colors["bg_card"],
        ).pack(anchor="w", padx=int(12 * scale), pady=(0, int(10 * scale)))


def _add_funnel(
    host: JobReviewHost,
    parent: tk.Misc,
    review: Mapping[str, Any],
    *,
    font_family: str,
    scale: float,
) -> None:
    funnel = host.widget_support.create_card(
        parent,
        "筛选转化",
        fill="x",
        pady=(0, int(14 * scale)),
    )
    funnel_base = review["qualified_count"]
    funnel_items = (
        ("通过筛选", review["qualified_count"], host.colors["primary"]),
        ("已打招呼", review["greeted_count"], host.colors["warning"]),
        ("已回复", review["replied_count"], host.colors["success"]),
        ("已约面", review["interviewed_count"], host.colors["purple"]),
    )
    for row_index, (label, count, color) in enumerate(funnel_items):
        row = tk.Frame(funnel, bg=host.colors["bg_card"])
        row.pack(fill="x", pady=(0 if row_index == 0 else int(7 * scale), 0))
        tk.Label(
            row,
            text=label,
            width=9,
            anchor="w",
            font=(font_family, int(10 * host.font_scale)),
            fg=host.colors["text_primary"],
            bg=host.colors["bg_card"],
        ).pack(side="left")
        tk.Label(
            row,
            text=str(count),
            width=5,
            anchor="e",
            font=(font_family, int(10 * host.font_scale), "bold"),
            fg=host.colors["text_primary"],
            bg=host.colors["bg_card"],
        ).pack(side="left", padx=(0, int(10 * scale)))
        ratio = min(1.0, count / funnel_base) if funnel_base else 0.0
        bar = tk.Canvas(
            row,
            height=max(8, int(10 * scale)),
            bg=host.colors["bg_input"],
            highlightthickness=0,
            bd=0,
        )
        bar.pack(side="left", fill="x", expand=True)

        def draw_bar(
            event: tk.Event,
            target: tk.Canvas = bar,
            value: float = ratio,
            fill: str = color,
        ) -> None:
            target.delete("all")
            width = max(1, event.width)
            height = max(1, event.height)
            if value > 0:
                target.create_rectangle(
                    0,
                    0,
                    max(2, int(width * value)),
                    height,
                    fill=fill,
                    outline="",
                )

        bar.bind("<Configure>", draw_bar)
        percent = int(round(ratio * 100)) if funnel_base else 0
        tk.Label(
            row,
            text=f"{percent}%",
            width=5,
            anchor="e",
            font=(font_family, int(10 * host.font_scale)),
            fg=host.colors["text_secondary"],
            bg=host.colors["bg_card"],
        ).pack(side="left", padx=(int(10 * scale), 0))


def _feedback_presentation(
    host: JobReviewHost,
    feedback_count: int,
) -> tuple[str, str, str, str]:
    if feedback_count == 0:
        return (
            "暂无反馈",
            "尚无结构化反馈，当前不生成趋势判断。",
            host.colors["banner_warning_bg"],
            host.colors["warning_text"],
        )
    if feedback_count < 5:
        return (
            "样本不足",
            f"当前只有 {feedback_count} 条反馈，样本不足 5 条，暂不根据趋势调整岗位规则。",
            host.colors["banner_warning_bg"],
            host.colors["warning_text"],
        )
    return (
        "可生成趋势",
        "反馈样本已达到趋势判断门槛，可结合原因分布调整岗位规则。",
        host.colors["banner_success_bg"],
        host.colors["success"],
    )


def _add_feedback(
    host: JobReviewHost,
    parent: tk.Misc,
    review: Mapping[str, Any],
    *,
    font_family: str,
    scale: float,
    show_feedback_candidates: Callable[[], Any],
) -> None:
    feedback = host.widget_support.create_card(
        parent,
        "反馈质量",
        fill="x",
        pady=(0, int(14 * scale)),
    )
    feedback_count = review["feedback_count"]
    state, message, background, foreground = _feedback_presentation(
        host,
        feedback_count,
    )
    banner = tk.Frame(feedback, bg=background)
    banner.pack(fill="x")
    text_box = tk.Frame(banner, bg=background)
    text_box.pack(
        side="left",
        fill="x",
        expand=True,
        padx=int(12 * scale),
        pady=int(10 * scale),
    )
    tk.Label(
        text_box,
        text=(
            f"反馈覆盖 {feedback_count}/{review['candidate_count']} 人"
            f"  ·  {state}"
        ),
        font=(font_family, int(11 * host.font_scale), "bold"),
        fg=foreground,
        bg=background,
    ).pack(anchor="w")
    tk.Label(
        text_box,
        text=message,
        font=(font_family, int(10 * host.font_scale)),
        fg=host.colors["text_primary"],
        bg=background,
        justify="left",
        wraplength=max(420, int(580 * min(scale, 1.2))),
    ).pack(anchor="w", pady=(int(2 * scale), 0))
    if feedback_count:
        ttk.Button(
            banner,
            text="查看反馈候选人",
            command=show_feedback_candidates,
        ).pack(side="right", padx=int(12 * scale), pady=int(10 * scale))


def _add_insights(
    host: JobReviewHost,
    parent: tk.Misc,
    review: Mapping[str, Any],
    *,
    font_family: str,
    scale: float,
) -> None:
    insight_sections = [
        ("反馈分布", review["status_counts"]),
        ("高频原因", review["reason_counts"]),
        ("误推原因", review["false_positive_reasons"]),
        ("误杀原因", review["false_negative_reasons"]),
        ("AI 偏差", review["ai_bias_counts"]),
    ]
    insight_sections = [item for item in insight_sections if item[1]]
    if not insight_sections:
        return
    insights = host.widget_support.create_card(
        parent,
        "问题洞察",
        fill="x",
        pady=(0, int(14 * scale)),
    )
    for column in range(2):
        insights.grid_columnconfigure(
            column,
            weight=1,
            uniform="job_review_insight",
        )
    for index, (title, counter) in enumerate(insight_sections):
        panel = tk.Frame(
            insights,
            bg=host.colors["bg_card"],
            highlightbackground=host.colors["border"],
            highlightthickness=1,
        )
        panel.grid(
            row=index // 2,
            column=index % 2,
            sticky="nsew",
            padx=(0, int(6 * scale))
            if index % 2 == 0
            else (int(6 * scale), 0),
            pady=(0, int(8 * scale)),
        )
        tk.Label(
            panel,
            text=title,
            font=(font_family, int(10 * host.font_scale), "bold"),
            fg=host.colors["text_primary"],
            bg=host.colors["bg_card"],
        ).pack(
            anchor="w",
            padx=int(10 * scale),
            pady=(int(8 * scale), int(4 * scale)),
        )
        for name, count in counter.most_common(4):
            item_row = tk.Frame(panel, bg=host.colors["bg_card"])
            item_row.pack(
                fill="x",
                padx=int(10 * scale),
                pady=(0, int(4 * scale)),
            )
            tk.Label(
                item_row,
                text=str(name),
                font=(font_family, int(10 * host.font_scale)),
                fg=host.colors["text_secondary"],
                bg=host.colors["bg_card"],
                anchor="w",
            ).pack(side="left", fill="x", expand=True)
            tk.Label(
                item_row,
                text=str(count),
                font=(font_family, int(10 * host.font_scale), "bold"),
                fg=host.colors["primary"],
                bg=host.colors["bg_card"],
            ).pack(side="right")


def _add_suggestions(
    host: JobReviewHost,
    parent: tk.Misc,
    review: Mapping[str, Any],
    *,
    font_family: str,
    scale: float,
    callbacks: JobReviewCallbacks,
) -> None:
    def build_suggestion_action(title_bar: tk.Misc, padding: int) -> None:
        if review["feedback_count"] < 5:
            return
        ttk.Button(
            title_bar,
            text="前往岗位配置",
            command=callbacks.open_job_config,
        ).pack(
            side="right",
            padx=(0, padding),
            pady=max(4, int(padding * 0.45)),
        )

    suggestions = host.widget_support.create_card(
        parent,
        "建议调整",
        fill="x",
        pady=(0, int(10 * scale)),
        title_trailing_builder=build_suggestion_action,
    )
    for index, suggestion in enumerate(review["suggestions"], start=1):
        title, detail = callbacks.format_suggestion(suggestion)
        row = tk.Frame(
            suggestions,
            bg=host.colors["bg_input"],
            highlightbackground=host.colors["border"],
            highlightthickness=1,
        )
        row.pack(fill="x", pady=(0, int(7 * scale)))
        tk.Label(
            row,
            text=str(index),
            width=2,
            font=(font_family, int(10 * host.font_scale), "bold"),
            fg="white",
            bg=host.colors["primary"],
        ).pack(
            side="left",
            anchor="n",
            padx=int(10 * scale),
            pady=int(10 * scale),
        )
        text_box = tk.Frame(row, bg=host.colors["bg_input"])
        text_box.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, int(12 * scale)),
            pady=int(9 * scale),
        )
        tk.Label(
            text_box,
            text=title,
            font=(font_family, int(10 * host.font_scale), "bold"),
            fg=host.colors["text_primary"],
            bg=host.colors["bg_input"],
            justify="left",
            anchor="w",
            wraplength=max(520, int(700 * min(scale, 1.2))),
        ).pack(fill="x", anchor="w")
        if detail:
            tk.Label(
                text_box,
                text=detail,
                font=(font_family, int(10 * host.font_scale)),
                fg=host.colors["text_secondary"],
                bg=host.colors["bg_input"],
                justify="left",
                anchor="w",
                wraplength=max(520, int(700 * min(scale, 1.2))),
            ).pack(fill="x", anchor="w", pady=(int(2 * scale), 0))


def _place_workbench(
    host: JobReviewHost,
    window: tk.Toplevel,
    scale: float,
) -> None:
    try:
        host.root.update_idletasks()
        root_height = host.root.winfo_height()
        monitor_area = get_windows_monitor_area(window, host.root)
        area_width = monitor_area[2] if monitor_area else window.winfo_screenwidth()
        area_height = monitor_area[3] if monitor_area else window.winfo_screenheight()
        width = min(int(820 * scale), int(area_width * 0.92))
        height = min(int(760 * scale), root_height, int(area_height * 0.82))
    except tk.TclError:
        width, height = int(820 * scale), int(760 * scale)
    place_window_centered(window, width, height, parent=host.root)


def build_job_review_workbench(
    host: JobReviewHost,
    *,
    job_name: str,
    time_range: str,
    review: Mapping[str, Any],
    callbacks: JobReviewCallbacks,
    font_family: str,
) -> JobReviewWidgets:
    """Build and show the modal job review workbench from a prepared model."""
    scale = host.dpi_scale * host.zoom_factor
    window = create_toplevel(host.root)
    window.title(f"岗位复盘 - {job_name}")
    window.transient(host.root)
    window.grab_set()
    window.withdraw()
    window.configure(bg=host.colors["bg_main"])
    window.grid_rowconfigure(0, weight=1)
    window.grid_columnconfigure(0, weight=1)

    def close() -> None:
        try:
            window.grab_release()
        except tk.TclError:
            pass
        window.destroy()

    def close_then(callback: Callable[[], Any]) -> None:
        close()
        callback()

    shell = ttk.Frame(
        window,
        style="Page.TFrame",
        padding=(int(20 * scale), int(18 * scale), int(20 * scale), 0),
    )
    shell.grid(row=0, column=0, sticky="nsew")
    shell.grid_rowconfigure(0, weight=1)
    shell.grid_columnconfigure(0, weight=1)

    canvas = tk.Canvas(
        shell,
        bg=host.colors["bg_main"],
        highlightthickness=0,
        bd=0,
    )
    scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
    content = tk.Frame(canvas, bg=host.colors["bg_main"])
    content_window = canvas.create_window((0, 0), window=content, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    content.bind(
        "<Configure>",
        lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
    )
    canvas.bind(
        "<Configure>",
        lambda event: canvas.itemconfigure(content_window, width=event.width),
    )
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    _add_header(
        host,
        content,
        job_name=job_name,
        time_range=time_range,
        review=review,
        font_family=font_family,
        scale=scale,
    )
    _add_metrics(host, content, review, font_family=font_family, scale=scale)
    _add_funnel(host, content, review, font_family=font_family, scale=scale)
    _add_feedback(
        host,
        content,
        review,
        font_family=font_family,
        scale=scale,
        show_feedback_candidates=lambda: close_then(
            callbacks.show_feedback_candidates
        ),
    )
    _add_insights(host, content, review, font_family=font_family, scale=scale)
    contextual_callbacks = JobReviewCallbacks(
        show_feedback_candidates=callbacks.show_feedback_candidates,
        open_job_config=lambda: close_then(callbacks.open_job_config),
        format_suggestion=callbacks.format_suggestion,
    )
    _add_suggestions(
        host,
        content,
        review,
        font_family=font_family,
        scale=scale,
        callbacks=contextual_callbacks,
    )

    footer = ttk.Frame(
        window,
        style="Page.TFrame",
        padding=(int(20 * scale), int(12 * scale), int(20 * scale), int(14 * scale)),
    )
    footer.grid(row=1, column=0, sticky="ew")
    ttk.Button(footer, text="关闭", command=close).pack(side="right")

    host.scroll_support.bind_mousewheel(canvas, content)
    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Escape>", lambda _event: close())
    _place_workbench(host, window, scale)
    window.deiconify()
    return JobReviewWidgets(window, canvas, content, close)
