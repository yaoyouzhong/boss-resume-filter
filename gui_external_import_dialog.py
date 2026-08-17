"""Tk form dialog for importing external-channel candidate resumes.

Single-file mode collects file/name/job/channel/note and hands the form to
the host. Selecting two or more files switches the same dialog to batch
mode: the form locks to shared job/channel/note, then swaps in-place to a
progress view and finally a per-file summary view. All parsing, filtering,
persistence and AI evaluation stay in the host; batch progress and results
arrive through the injected callbacks bundle.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Any, Protocol

import ui_theme
from constants import SCORE_THRESHOLD_PASS
from ui_windowing import create_toplevel, place_window_centered


CHANNEL_PRESETS: tuple[str, ...] = (
    "智联招聘",
    "前程无忧",
    "猎聘",
    "内推",
    "猎头",
    "其他",
)

RESUME_FILETYPES: tuple[tuple[str, str], ...] = (
    ("简历文件", "*.pdf *.docx *.doc *.txt *.md *.rtf *.html"),
    ("PDF 文件", "*.pdf"),
    ("Word 文件", "*.docx *.doc"),
    ("文本文件", "*.txt *.md"),
    ("RTF 文件", "*.rtf"),
    ("HTML 文件", "*.html *.htm"),
    ("所有文件", "*.*"),
)

# 与服务层 BatchImportItem.status 对应的展示文案；未知状态原样显示。
_BATCH_STATUS_LABELS: dict[str, str] = {
    "imported": "已导入",
    "rejected": "未通过筛选",
    "skipped_duplicate": "重复跳过",
    "failed": "失败",
}


class ExternalImportDialogHost(Protocol):
    """Visual host contract used by the external-import dialog."""

    colors: Mapping[str, str]
    dpi_scale: float
    font_scale: float
    zoom_factor: float


@dataclass(frozen=True)
class ExternalImportFormData:
    """Validated user input handed back to the host."""

    file_path: str
    name: str
    job_name: str
    source_channel: str
    source_note: str
    file_paths: tuple[str, ...] = ()
    ai_enhance: bool = False
    ai_resume_eval: bool = False


@dataclass(frozen=True)
class ExternalImportBatchCallbacks:
    """View callbacks the host drives from its batch worker thread.

    The host wraps each callback so it always runs on the UI thread. Payloads
    are duck-typed service results (``item.name``/``item.status``/...); the
    dialog never imports the service module.
    """

    on_progress: Callable[[int, int, Any], None]
    on_import_done: Callable[[Any], None]
    on_eval_progress: Callable[[int, int, str], None]
    on_all_done: Callable[[Any, str], None]


# run_batch 的返回值为停止函数，供“取消导入”按钮调用。
RunBatchFn = Callable[
    [list[str], ExternalImportFormData, ExternalImportBatchCallbacks],
    Callable[[], None],
]


@dataclass(frozen=True)
class ExternalImportDialogWidgets:
    """Dialog references used by focused Tk tests."""

    window: tk.Toplevel
    file_var: tk.StringVar
    name_var: tk.StringVar
    job_var: tk.StringVar
    channel_var: tk.StringVar
    note_text: tk.Text
    feedback_var: tk.StringVar
    confirm_button: ttk.Button
    cancel_button: ttk.Button
    progress_var: tk.StringVar
    progress_bar: ttk.Progressbar
    summary_var: tk.StringVar
    summary_label: ttk.Label
    summary_detail_var: tk.StringVar
    summary_tree: ttk.Treeview
    eval_var: tk.StringVar
    ai_enhance_var: tk.BooleanVar
    ai_resume_eval_var: tk.BooleanVar
    # 单份后台导入完成后由宿主调用关窗（幂等，可在 UI 线程重复调用）
    close_dialog: Callable[[], None]


def show_external_import_dialog(
    host: ExternalImportDialogHost,
    parent: tk.Misc,
    *,
    font_family: str,
    job_names: Sequence[str],
    default_job: str = "",
    preview_file: Callable[[str], tuple[bool, str]] | None = None,
    name_guesser: Callable[[str], str] | None = None,
    run_batch: RunBatchFn | None = None,
    on_confirm: Callable[[ExternalImportFormData], bool | None],
    ai_enhance_available: bool = False,
    ai_enhance_initial: bool = False,
    on_ai_enhance_toggle: Callable[[bool], None] | None = None,
    ai_resume_eval_available: bool = False,
    ai_resume_eval_initial: bool = False,
    on_ai_resume_eval_toggle: Callable[[bool], None] | None = None,
    ai_model_label: str = "",
    switch_factory: Callable[[tk.Misc, tk.Variable, tk.Variable | None], tk.Widget] | None = None,
) -> ExternalImportDialogWidgets:
    """Show the external import form and return validated input to the host.

    单份导入的 ``on_confirm`` 契约：宿主同步完成前置确认（查重等），
    返回 True 表示后台导入已启动——对话框转入进行中视图，由宿主在
    完成后调用 ``widgets.close_dialog()`` 关窗；返回 False/None 表示
    前置确认未通过，对话框留在表单等待用户修正。
    """
    scale = host.dpi_scale * host.zoom_factor
    dialog_font_scale = host.font_scale * 0.88
    window = create_toplevel(parent)
    window.title("导入外部候选人")
    window.transient(parent)
    window.grab_set()
    window.resizable(False, False)
    window.configure(background=host.colors["bg_main"])
    window.withdraw()

    style = ttk.Style(window)
    style.configure("ExtImport.TLabel", background=host.colors["bg_main"])
    style.configure("ExtImport.TFrame", background=host.colors["bg_main"])
    field_font = (font_family, int(13 * dialog_font_scale))
    hint_font = (font_family, int(11 * dialog_font_scale))
    muted_color = host.colors.get("text_muted", ui_theme.TEXT_MUTED)

    ttk.Label(
        window,
        text="导入外部渠道候选人",
        font=(font_family, int(16 * dialog_font_scale), "bold"),
        style="ExtImport.TLabel",
    ).pack(pady=(int(16 * scale), int(2 * scale)))
    subtitle_var = tk.StringVar(
        value="规则筛选在本机完成；仅勾选的 AI 项目会发送简历文本"
    )
    ttk.Label(
        window,
        textvariable=subtitle_var,
        font=hint_font,
        foreground=muted_color,
        style="ExtImport.TLabel",
    ).pack(pady=(0, int(12 * scale)))

    form = ttk.Frame(window, style="ExtImport.TFrame")
    form.pack(fill="x", padx=int(28 * scale))
    form.columnconfigure(1, weight=1)

    file_var = tk.StringVar()
    name_var = tk.StringVar()
    job_var = tk.StringVar(value=default_job)
    channel_var = tk.StringVar()
    feedback_var = tk.StringVar()
    progress_var = tk.StringVar()
    summary_var = tk.StringVar()
    summary_detail_var = tk.StringVar()
    eval_var = tk.StringVar()

    # 姓名自动填充跟踪：自动提取的姓名在换文件时可被重新提取覆盖，
    # 用户手动改过的姓名则保留（trace 在 set 时也触发，用 updating 防自激）。
    name_auto = {"filled": False, "updating": False}

    def _on_name_modified(*_args: object) -> None:
        if not name_auto["updating"]:
            name_auto["filled"] = False

    name_var.trace_add("write", _on_name_modified)

    def set_auto_name(value: str) -> None:
        name_auto["updating"] = True
        name_var.set(value)
        name_auto["updating"] = False
        name_auto["filled"] = bool(value)

    row_pady = (int(5 * scale), int(5 * scale))

    def row_label(row: int, text: str, *, top: bool = False) -> None:
        ttk.Label(
            form, text=text, font=field_font, style="ExtImport.TLabel",
        ).grid(
            row=row,
            column=0,
            sticky="ne" if top else "e",
            pady=row_pady,
            padx=(0, int(10 * scale)),
        )

    # 标签右对齐贴近输入框；输入框与第 3 列的按钮/提示同网格对齐，
    # 所有输入框视觉长度一致。
    row_label(0, "简历文件")
    file_entry = ttk.Entry(form, textvariable=file_var, font=field_font, state="readonly")
    file_entry.grid(row=0, column=1, sticky="ew", pady=row_pady)

    row_label(1, "姓名")
    name_entry = ttk.Entry(form, textvariable=name_var, font=field_font)
    name_entry.grid(row=1, column=1, sticky="ew", pady=row_pady)
    name_hint_label = ttk.Label(
        form,
        text="自动提取，可修改",
        font=hint_font,
        foreground=muted_color,
        style="ExtImport.TLabel",
    )
    name_hint_label.grid(row=1, column=2, sticky="w", padx=(int(8 * scale), 0), pady=row_pady)

    row_label(2, "归属岗位")
    job_combo = ttk.Combobox(
        form,
        textvariable=job_var,
        values=list(job_names),
        state="readonly",
        font=field_font,
    )
    job_combo.grid(row=2, column=1, sticky="ew", pady=row_pady)

    row_label(3, "来源渠道")
    channel_combo = ttk.Combobox(
        form,
        textvariable=channel_var,
        values=list(CHANNEL_PRESETS),
        font=field_font,
    )
    channel_combo.grid(row=3, column=1, sticky="ew", pady=row_pady)

    row_label(4, "备注", top=True)
    note_text = tk.Text(
        form,
        font=field_font,
        height=3,
        wrap="word",
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=host.colors.get("border", "#c9ced6"),
        highlightcolor=host.colors["primary"],
        padx=int(5 * scale),
        pady=int(4 * scale),
    )
    note_text.grid(row=4, column=1, sticky="ew", pady=row_pady)

    # ---- AI 增强识别开关：默认关；未配置默认模型或 API Key 时禁用 ----
    ai_enhance_var = tk.BooleanVar(
        value=bool(ai_enhance_initial) and ai_enhance_available
    )
    ai_enhance_enabled_var = tk.BooleanVar(value=ai_enhance_available)

    def _on_ai_enhance_changed(*_args: object) -> None:
        if on_ai_enhance_toggle is not None:
            on_ai_enhance_toggle(bool(ai_enhance_var.get()))

    ai_enhance_var.trace_add("write", _on_ai_enhance_changed)

    row_label(5, "AI 增强识别")
    model_text = ai_model_label or "当前默认 AI 模型"
    if ai_enhance_available:
        ai_hint_text = f"{model_text}；发送前 8000 字，仅补全规则未识别字段"
    else:
        ai_hint_text = "未配置默认 AI 模型或 API Key，无法开启"
    # 开关与说明同行横排：说明只有一行，独立成行会留白且割裂关联
    ai_row = ttk.Frame(form, style="ExtImport.TFrame")
    ai_row.grid(
        row=5, column=1, columnspan=2, sticky="w",
        pady=(int(5 * scale), int(5 * scale)),
    )
    if switch_factory is not None:
        switch_widget = switch_factory(ai_row, ai_enhance_var, ai_enhance_enabled_var)
    else:
        switch_widget = ttk.Checkbutton(ai_row, variable=ai_enhance_var, takefocus=0)
        if not ai_enhance_available:
            switch_widget.state(["disabled"])
    switch_widget.pack(side="left")
    ttk.Label(
        ai_row,
        text=ai_hint_text,
        font=hint_font,
        foreground=muted_color,
        style="ExtImport.TLabel",
    ).pack(side="left", padx=(int(10 * scale), 0))

    # 完整简历评估是独立授权：不再把“已配置 API Key”等同于允许发送简历。
    ai_resume_eval_var = tk.BooleanVar(
        value=bool(ai_resume_eval_initial) and ai_resume_eval_available
    )
    ai_resume_eval_enabled_var = tk.BooleanVar(value=ai_resume_eval_available)

    def _on_ai_resume_eval_changed(*_args: object) -> None:
        if on_ai_resume_eval_toggle is not None:
            on_ai_resume_eval_toggle(bool(ai_resume_eval_var.get()))

    ai_resume_eval_var.trace_add("write", _on_ai_resume_eval_changed)
    row_label(6, "AI 简历评估")
    resume_eval_row = ttk.Frame(form, style="ExtImport.TFrame")
    resume_eval_row.grid(
        row=6, column=1, columnspan=2, sticky="w",
        pady=(int(5 * scale), int(5 * scale)),
    )
    if switch_factory is not None:
        resume_eval_switch = switch_factory(
            resume_eval_row, ai_resume_eval_var, ai_resume_eval_enabled_var
        )
    else:
        resume_eval_switch = ttk.Checkbutton(
            resume_eval_row, variable=ai_resume_eval_var, takefocus=0
        )
        if not ai_resume_eval_available:
            resume_eval_switch.state(["disabled"])
    resume_eval_switch.pack(side="left")
    resume_eval_hint = (
        f"{model_text}；发送前 6000 字，用于调整规则评分"
        if ai_resume_eval_available
        else "未配置默认 AI 模型或 API Key，无法开启"
    )
    ttk.Label(
        resume_eval_row,
        text=resume_eval_hint,
        font=hint_font,
        foreground=muted_color,
        style="ExtImport.TLabel",
    ).pack(side="left", padx=(int(10 * scale), 0))

    # ---- 批量进度视图（初始隐藏，确认后原地替换表单） ----
    progress_frame = ttk.Frame(window, style="ExtImport.TFrame")
    ttk.Label(
        progress_frame,
        textvariable=progress_var,
        font=field_font,
        style="ExtImport.TLabel",
    ).pack(anchor="w")
    progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", mode="determinate")
    progress_bar.pack(fill="x", pady=(int(8 * scale), 0))

    # ---- 汇总视图（初始隐藏） ----
    summary_frame = ttk.Frame(window, style="ExtImport.TFrame")
    summary_label = ttk.Label(
        summary_frame,
        textvariable=summary_var,
        font=field_font,
        style="ExtImport.TLabel",
        justify="left",
    )
    summary_label.pack(anchor="w", fill="x")
    # 次要口径（重复跳过/失败/淘汰去向）降级为小字灰显，与主结论拉开层次
    summary_detail_label = tk.Label(
        summary_frame,
        textvariable=summary_detail_var,
        font=hint_font,
        background=host.colors["bg_main"],
        foreground=muted_color,
        anchor="w",
        justify="left",
    )
    summary_detail_label.pack(anchor="w", fill="x", pady=(int(2 * scale), 0))
    # 汇总树用独立样式：内容与字段同字号（比默认大一号），标题粗体
    style.configure(
        "ExtImport.Treeview",
        font=field_font,
        rowheight=int(26 * scale),
    )
    style.configure(
        "ExtImport.Treeview.Heading",
        font=(font_family, int(13 * dialog_font_scale), "bold"),
    )
    # 滚动条与树同容器：纵向超过 5 行才显示，行数少时整表全展示；
    # 结果/原因列加宽到能完整显示常见内容，超出部分由底部横向滚动条查看
    tree_holder = ttk.Frame(summary_frame, style="ExtImport.TFrame")
    tree_holder.pack(fill="x", pady=(int(8 * scale), 0))
    summary_tree = ttk.Treeview(
        tree_holder,
        columns=("file", "name", "status", "reason"),
        show="headings",
        height=5,
        style="ExtImport.Treeview",
    )
    summary_scroll = ttk.Scrollbar(
        tree_holder, orient="vertical", command=summary_tree.yview
    )
    summary_hscroll = ttk.Scrollbar(
        tree_holder, orient="horizontal", command=summary_tree.xview
    )
    summary_tree.configure(
        yscrollcommand=summary_scroll.set, xscrollcommand=summary_hscroll.set
    )
    summary_tree.heading("file", text="文件")
    summary_tree.heading("name", text="姓名")
    summary_tree.heading("status", text="结果")
    summary_tree.heading("reason", text="原因")
    summary_tree.column("file", width=int(240 * scale), anchor="w")
    summary_tree.column("name", width=int(100 * scale), anchor="w")
    summary_tree.column("status", width=int(190 * scale), anchor="w")
    summary_tree.column("reason", width=int(460 * scale), anchor="w")
    # 横滚条先于树 pack：side=bottom 从容器底部切条，否则会被树占满剩余空间
    summary_hscroll.pack(side="bottom", fill="x")
    summary_tree.pack(side="left", fill="x", expand=True)

    # 结果/原因列内容超宽截断时，悬停显示完整文本（对话框自包含，
    # 不依赖宿主 Tooltip 槽位）
    summary_tip = {"win": None, "key": None}
    measure_font = tkfont.Font(family=font_family, size=int(13 * dialog_font_scale))

    def hide_summary_tip() -> None:
        win = summary_tip["win"]
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass
        summary_tip["win"] = None
        summary_tip["key"] = None

    def on_summary_motion(event: tk.Event) -> None:
        row_id = summary_tree.identify_row(event.y)
        column = summary_tree.identify_column(event.x)
        col_index = {"#3": 2, "#4": 3}.get(column)  # 结果列 / 原因列
        if not row_id or col_index is None:
            hide_summary_tip()
            return
        values = summary_tree.item(row_id, "values")
        text = str(values[col_index]) if col_index < len(values) else ""
        col_name = "status" if column == "#3" else "reason"
        col_width = int(summary_tree.column(col_name, "width"))
        if not text or measure_font.measure(text) <= col_width - 14:
            hide_summary_tip()
            return
        key = (row_id, column)
        if summary_tip["key"] == key:
            return
        hide_summary_tip()
        tip = create_toplevel(summary_tree)
        tip.wm_overrideredirect(True)
        tk.Label(
            tip,
            text=text,
            background=host.colors.get("tooltip_bg", ui_theme.TOOLTIP_BG),
            foreground=host.colors.get("tooltip_fg", ui_theme.TOOLTIP_FG),
            font=(font_family, int(11 * dialog_font_scale)),
            padx=10,
            pady=6,
            justify="left",
            wraplength=int(320 * scale),
        ).pack()
        tip.wm_geometry(f"+{event.x_root + 12}+{event.y_root + 10}")
        summary_tip["win"] = tip
        summary_tip["key"] = key

    summary_tree.bind("<Motion>", on_summary_motion)
    summary_tree.bind("<Leave>", lambda _event: hide_summary_tip())
    eval_label = tk.Label(
        summary_frame,
        textvariable=eval_var,
        font=hint_font,
        background=host.colors["bg_main"],
        foreground=muted_color,
        anchor="w",
        justify="left",
    )
    eval_label.pack(fill="x", pady=(int(6 * scale), 0))

    # 分隔线是表单/进度/汇总三种视图的共同下界：运行态与汇总态 pack 时
    # 必须 before=separator，否则会落到 footer（按钮行）之后。
    separator = ttk.Separator(window, orient="horizontal")
    separator.pack(fill="x", padx=int(28 * scale), pady=(int(12 * scale), 0))

    feedback_label = tk.Label(
        window,
        textvariable=feedback_var,
        font=hint_font,
        background=host.colors["bg_main"],
        foreground=muted_color,
        anchor="w",
    )
    footer = ttk.Frame(window, style="ExtImport.TFrame")
    footer.pack(fill="x", padx=int(28 * scale), pady=(int(6 * scale), int(14 * scale)))
    # 提示独立成行（位于按钮行上方），不再与按钮共享同一行，避免把按钮挤到边缘。
    feedback_label.pack(fill="x", padx=int(28 * scale), pady=(int(10 * scale), 0), before=footer)

    # ---- 阶段状态 ----
    selected_paths: list[str] = []
    phase = {"name": "form"}  # form → running → summary
    batch_form = {"active": False}  # 表单是否处于批量（多文件）选择状态
    stop_holder: list[Callable[[], None]] = []
    preview_state = {"seq": 0, "pending": False}  # 单人文件后台预解析
    preview_results: queue.SimpleQueue = queue.SimpleQueue()

    def poll_preview_results() -> None:
        """主线程轮询预解析结果队列（Tk 只能从主线程触碰）。"""
        if not view_alive():
            return
        while True:
            try:
                seq_done, ok, message = preview_results.get_nowait()
            except queue.Empty:
                break
            if seq_done != preview_state["seq"]:
                continue
            preview_state["pending"] = False
            confirm_button.configure(state="normal")
            set_feedback(message, is_error=not ok)
        if preview_state["pending"] and view_alive():
            try:
                window.after(100, poll_preview_results)
            except tk.TclError:
                pass

    def set_feedback(message: str, *, is_error: bool = False) -> None:
        # 底栏单行展示，超长消息截断，避免挤压按钮。
        display = message if len(message) <= 46 else f"{message[:45]}…"
        feedback_var.set(display)
        feedback_label.configure(
            foreground=host.colors["danger"] if is_error else muted_color
        )

    def refit_window(width: int | None = None) -> None:
        """按当前阶段内容重调窗口尺寸，保持位置不变。"""
        window.update_idletasks()
        width = width or max(560, int(560 * scale))
        window.geometry(
            f"{width}x{window.winfo_reqheight()}+{window.winfo_x()}+{window.winfo_y()}"
        )

    def set_form_inputs_enabled(enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        browse_button.configure(state=state)
        name_entry.configure(state=state)
        channel_combo.configure(state=state)
        job_combo.configure(state="readonly" if enabled else "disabled")
        note_text.configure(state=state)

    def enter_batch_form_state(count: int) -> None:
        preview_state["seq"] += 1  # 作废可能仍在进行的单文件预解析
        preview_state["pending"] = False
        confirm_button.configure(state="normal")
        batch_form["active"] = True
        file_var.set(f"已选 {count} 个文件")
        name_var.set("按文件名自动提取")
        name_entry.configure(state="disabled")
        name_hint_label.configure(text="逐份按文件名提取")
        confirm_button.configure(text="批量导入")
        set_feedback(f"将批量导入 {count} 个文件，统一归属所选岗位与渠道。")

    def leave_batch_form_state() -> None:
        if not batch_form["active"]:
            return
        batch_form["active"] = False
        name_entry.configure(state="normal")
        name_var.set("")
        name_hint_label.configure(text="自动提取，可修改")
        confirm_button.configure(text="导入")

    def run_preview_async(file_path: str) -> None:
        """后台线程预解析选中文件；旧版 .doc 经本机 Word 转换可能耗时数秒。"""
        preview_state["seq"] += 1
        seq = preview_state["seq"]
        preview_state["pending"] = True
        confirm_button.configure(state="disabled")
        set_feedback("正在解析简历文件，请稍候…")

        def _worker() -> None:
            try:
                ok, message = preview_file(file_path)
            except Exception as exc:  # preview_file 自身已兜底，这里再保险
                ok, message = False, f"无法解析：{exc}"
            preview_results.put((seq, ok, message))

        threading.Thread(target=_worker, daemon=True).start()
        try:
            window.after(100, poll_preview_results)
        except tk.TclError:
            pass

    def browse_file() -> None:
        paths = list(
            filedialog.askopenfilenames(
                title="选择候选人简历（可多选）",
                filetypes=list(RESUME_FILETYPES),
                parent=window,
            )
        )
        if not paths:
            return
        selected_paths.clear()
        selected_paths.extend(paths)
        if len(paths) > 1:
            enter_batch_form_state(len(paths))
            return
        leave_batch_form_state()
        file_path = paths[0]
        file_var.set(file_path)
        if name_guesser is not None and (
            not name_var.get().strip() or name_auto["filled"]
        ):
            try:
                guessed = name_guesser(file_path)
            except Exception:
                guessed = ""
            if guessed:
                set_auto_name(guessed)
        if preview_file is None:
            set_feedback("")
            return
        run_preview_async(file_path)

    browse_button = ttk.Button(form, text="浏览…", command=browse_file)
    browse_button.grid(row=0, column=2, sticky="w", padx=(int(8 * scale), 0), pady=row_pady)

    def close() -> None:
        if not view_alive():
            return
        try:
            window.grab_release()
        except tk.TclError:
            pass
        window.destroy()

    def view_alive() -> bool:
        try:
            return bool(window.winfo_exists())
        except tk.TclError:
            return False

    # ---- 批量视图回调（host 负责包装到 UI 线程后调用） ----
    def view_on_progress(done: int, total: int, item: Any) -> None:
        if not view_alive():
            return
        progress_var.set(f"正在导入 {done}/{total}：{getattr(item, 'name', '')}")
        progress_bar.configure(value=done)

    def view_on_import_done(summary: Any) -> None:
        if not view_alive():
            return
        items = list(getattr(summary, "items", ()) or ())
        counts = {
            key: sum(1 for item in items if getattr(item, "status", "") == key)
            for key in _BATCH_STATUS_LABELS
        }
        # 低于通过线的导入记录会进入淘汰记录视图，口径上不算"成功"
        low_score = sum(
            1
            for item in items
            if getattr(item, "status", "") == "imported"
            and getattr(item, "score", 0) < SCORE_THRESHOLD_PASS
        )
        # 未通过筛选的记录同样入库（淘汰记录视图），汇总主口径是"入库"总数，
        # 避免"成功 0 人"被误解为导入失败；零值的次要项不占文案。
        # 主结论占一行正文字号，次要口径逐条降级为小字灰显并加引导符，
        # 弹窗按像素折行，每条保持短句避免数字被腰斩。
        stored = counts["imported"] + counts["rejected"]
        summary_var.set(
            f"导入完成：入库 {stored} 人"
            f"（通过筛选 {counts['imported']} 人、未通过 {counts['rejected']} 人）"
        )
        detail_lines: list[str] = []
        if counts["skipped_duplicate"]:
            detail_lines.append(f"重复跳过 {counts['skipped_duplicate']} 人")
        if counts["failed"]:
            detail_lines.append(f"失败 {counts['failed']} 人")
        if counts["rejected"]:
            detail_lines.append("未通过筛选的记录已入淘汰记录")
        if low_score:
            detail_lines.append(
                f"通过者中 {low_score} 人低于 {SCORE_THRESHOLD_PASS} 分，已入淘汰记录"
            )
        if getattr(summary, "stopped", False):
            remaining = len(selected_paths) - len(items)
            detail_lines.append(f"已取消，剩余 {remaining} 个文件未处理")
        summary_detail_var.set("\n".join(f"· {line}" for line in detail_lines))
        for item in items:
            status = getattr(item, "status", "")
            label = _BATCH_STATUS_LABELS.get(status, status)
            score = getattr(item, "score", 0)
            if status == "imported":
                if score < SCORE_THRESHOLD_PASS:
                    # 低于通过线的导入落进淘汰记录视图，与正常"已导入"口径区分
                    label = f"低于通过线（{score} 分）"
                else:
                    label = f"已导入（{score} 分）"
            elif status == "rejected" and score > 0:
                # 淘汰行展示参考分：剔除硬条件后的技能/经验匹配度
                label = f"未通过筛选（参考 {score} 分）"
            display_name = getattr(item, "name", "") or "未命名"
            if getattr(item, "name_needs_review", False):
                display_name += "（待核对）"
            summary_tree.insert(
                "",
                "end",
                values=(
                    Path(getattr(item, "path", "")).name,
                    display_name,
                    label,
                    getattr(item, "reason", ""),
                ),
            )
        # 5 条以内全展示并隐藏滚动条；更多时固定 5 行高并给出滚动条
        row_count = len(items)
        summary_tree.configure(height=max(1, min(row_count, 5)))
        if row_count > 5:
            summary_scroll.pack(side="right", fill="y")
        else:
            summary_scroll.pack_forget()
        form.pack_forget()
        progress_frame.pack_forget()
        summary_frame.pack(fill="x", padx=int(28 * scale), before=separator)
        cancel_button.configure(text="关闭", state="normal")
        phase["name"] = "summary"
        # 汇总行可能很长：按窗口实际可用宽度折行，先定 wraplength 再重算窗口高度
        summary_width = max(700, int(700 * scale))
        wrap = int(summary_width - 2 * 28 * scale)
        summary_label.configure(wraplength=wrap)
        summary_detail_label.configure(wraplength=wrap)
        refit_window(width=summary_width)

    def view_on_eval_progress(done: int, total: int, name: str) -> None:
        if not view_alive():
            return
        eval_var.set(f"简历评估中 {done}/{total}：{name}")

    def view_on_all_done(_summary: Any, eval_line: str) -> None:
        if not view_alive():
            return
        if eval_line:
            eval_var.set(eval_line)
        cancel_button.configure(text="关闭", state="normal")

    batch_callbacks = ExternalImportBatchCallbacks(
        on_progress=view_on_progress,
        on_import_done=view_on_import_done,
        on_eval_progress=view_on_eval_progress,
        on_all_done=view_on_all_done,
    )

    def enter_running_state(total: int) -> None:
        phase["name"] = "running"
        set_form_inputs_enabled(False)
        # 批量可能耗时较长，释放模态抓取，主窗口保持可操作。
        try:
            window.grab_release()
        except tk.TclError:
            pass
        progress_var.set(f"正在导入 0/{total}：准备中")
        progress_bar.configure(maximum=max(total, 1), value=0)
        form.pack_forget()
        progress_frame.pack(fill="x", padx=int(28 * scale), before=separator)
        confirm_button.pack_forget()
        cancel_button.configure(text="取消导入")
        set_feedback("")
        refit_window()

    def enter_single_import_state(name: str) -> None:
        """单份导入中间态：原地替换表单为进行中提示，宿主完成后关窗。

        单份事务不支持中途取消（无 stop_event），取消按钮禁用；
        窗口 X 仍可关闭视图，后台导入完成后结果照常弹出。
        """
        phase["name"] = "importing"
        set_form_inputs_enabled(False)
        try:
            window.grab_release()
        except tk.TclError:
            pass
        progress_var.set(f"正在导入 {name} 的简历，解析与评分中…")
        progress_bar.configure(mode="indeterminate")
        form.pack_forget()
        progress_frame.pack(fill="x", padx=int(28 * scale), before=separator)
        progress_bar.start(12)
        confirm_button.pack_forget()
        cancel_button.configure(state="disabled")
        set_feedback("")
        refit_window()

    def request_stop() -> None:
        cancel_button.configure(state="disabled", text="正在取消…")
        if stop_holder:
            stop_holder[0]()

    def on_cancel_button() -> None:
        if phase["name"] == "running":
            request_stop()
        else:
            close()

    def confirm() -> None:
        if not selected_paths:
            set_feedback("请先选择简历文件。", is_error=True)
            return
        job_name = job_var.get().strip()
        if not job_name:
            set_feedback("请选择归属岗位。", is_error=True)
            return
        channel = channel_var.get().strip()
        if not channel:
            set_feedback("请选择或填写来源渠道。", is_error=True)
            return
        note = note_text.get("1.0", "end-1c").strip()
        form_data = ExternalImportFormData(
            file_path=selected_paths[0],
            name=name_var.get().strip(),
            job_name=job_name,
            source_channel=channel,
            source_note=note,
            file_paths=tuple(selected_paths),
            ai_enhance=bool(ai_enhance_var.get()) and ai_enhance_available,
            ai_resume_eval=(
                bool(ai_resume_eval_var.get()) and ai_resume_eval_available
            ),
        )
        if len(selected_paths) > 1:
            if run_batch is None:
                set_feedback("当前版本不支持批量导入。", is_error=True)
                return
            enter_running_state(len(selected_paths))
            stop_holder.append(run_batch(list(selected_paths), form_data, batch_callbacks))
            return
        # 单文件：宿主先同步完成前置确认（查重等），返回 True 表示后台导入
        # 已启动——对话框转入进行中视图，由宿主完成后经 close_dialog 关窗；
        # 返回 False（用户取消查重等）则留在表单。
        name = name_var.get().strip()
        if not name:
            set_feedback("请填写候选人姓名。", is_error=True)
            return
        started = on_confirm(form_data)
        if not started:
            return
        enter_single_import_state(name)

    # 右侧按钮：确认使用应用全局 Accent 实心主按钮（gui_style_setup 注册）。
    cancel_button = ttk.Button(footer, text="取消", command=on_cancel_button)
    cancel_button.pack(side="right")
    confirm_button = ttk.Button(
        footer,
        text="导入",
        command=confirm,
        style="Accent.TButton",
    )
    confirm_button.pack(side="right", padx=(0, int(8 * scale)))

    def on_window_close() -> None:
        if phase["name"] == "running":
            if stop_holder:
                stop_holder[0]()
        close()

    window.protocol("WM_DELETE_WINDOW", on_window_close)
    browse_button.focus_set()
    # 内容构建完成后按实际高度居中，避免窗口底部留出大片空白。
    window.update_idletasks()
    place_window_centered(
        window,
        max(560, int(560 * scale)),
        window.winfo_reqheight(),
        parent=parent,
    )
    window.deiconify()
    return ExternalImportDialogWidgets(
        window=window,
        file_var=file_var,
        name_var=name_var,
        job_var=job_var,
        channel_var=channel_var,
        note_text=note_text,
        feedback_var=feedback_var,
        confirm_button=confirm_button,
        cancel_button=cancel_button,
        progress_var=progress_var,
        progress_bar=progress_bar,
        summary_var=summary_var,
        summary_label=summary_label,
        summary_detail_var=summary_detail_var,
        summary_tree=summary_tree,
        eval_var=eval_var,
        ai_enhance_var=ai_enhance_var,
        ai_resume_eval_var=ai_resume_eval_var,
        close_dialog=close,
    )
