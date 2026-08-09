"""Model-catalog selection dialog and its local interaction state."""
from __future__ import annotations

import threading
import tkinter as tk
from collections.abc import Callable, Collection, Sequence
from tkinter import ttk
from typing import Any

import ui_theme
from ui_messagebox import messagebox
from ui_windowing import place_window_centered


def show_model_catalog_dialog(
    host: Any,
    *,
    provider: str,
    models: Sequence[str],
    filtered_count: int,
    new_models: Collection[str],
    removed_models: Collection[str],
    font_family: str,
    show_model_detail: Callable[[str], None],
) -> None:
    """Show model search, selection, and explicit connectivity-test controls."""
    self = host
    _show_model_detail = show_model_detail
    # 防止重复打开（可能在 after 调度期间再次触发）
    if self._model_dialog is not None:
        try:
            self._model_dialog.lift()
            return
        except tk.TclError:
            self._model_dialog = None

    def _close_dialog():
        """统一关闭对话框，清理引用"""
        self._model_dialog = None
        try:
            dialog.destroy()
        except tk.TclError:
            pass

    dialog = tk.Toplevel(self.root)
    self._model_dialog = dialog
    dialog.title("选择模型")
    dialog.transient(self.root)
    dialog.withdraw()  # 先隐藏，布局完成后再定位显示
    dialog.configure(background=self.colors['bg_card'])
    # 对话框内标签统一白底，避免 macOS aqua 灰底上出现白色方块
    _dlg_style = ttk.Style(dialog)
    _dlg_style.configure('Dialog.TLabel', background=self.colors['bg_card'])

    # 对话框大小
    dialog_scale = max(
        1.0,
        min(self.dpi_scale * self.zoom_factor, 1.35),
    )
    dialog_width = int(760 * dialog_scale)
    dialog_height = int(680 * dialog_scale)
    dialog.resizable(True, True)
    dialog.minsize(
        int(560 * dialog_scale),
        int(440 * dialog_scale),
    )

    # 关闭按钮（红叉）也走统一清理
    dialog.protocol("WM_DELETE_WINDOW", _close_dialog)

    # 标题
    title_text = f"{provider} - 可用模型 ({len(models)} 个)"
    info_label = ttk.Label(dialog, text=title_text,
                           font=self.font_section,
                           style='Dialog.TLabel')
    info_label.pack(pady=(15, 0))

    # 过滤说明
    filter_note = "已自动过滤 embedding、rerank、tts 等非聊天模型" if filtered_count > 0 else ""
    if filter_note:
        note_label = ttk.Label(dialog, text=filter_note,
                               font=(font_family, int(11 * self.font_scale)),
                               foreground=self.colors['warning'],
                               style='Dialog.TLabel')
        note_label.pack(pady=(4, 0))

    # 新增模型提醒（放在过滤说明和列表之间）
    if new_models:
        new_frame = ttk.Frame(dialog, style='Dialog.TFrame')
        new_frame.pack(pady=(4, 0))
        ttk.Label(new_frame, text="✦ 发现 ",
            font=(font_family, int(11 * self.font_scale)),
            foreground=self.colors['success'],
            style='Dialog.TLabel').pack(side="left")
        new_num_label = ttk.Label(new_frame,
            text=f"{len(new_models)}",
            font=(font_family, int(11 * self.font_scale), 'bold'),
            foreground=self.colors['success'],
            cursor="hand2",
            style='Dialog.TLabel')
        new_num_label.pack(side="left")
        new_num_label.bind("<Button-1>", lambda e: _show_model_detail('new'))
        ttk.Label(new_frame, text=" 个新增模型（绿色标记）",
            font=(font_family, int(11 * self.font_scale)),
            foreground=self.colors['success'],
            style='Dialog.TLabel').pack(side="left")
    # 下线模型提醒
    if removed_models:
        removed_frame = ttk.Frame(dialog, style='Dialog.TFrame')
        removed_frame.pack(pady=(4, 0))
        ttk.Label(removed_frame, text="⚠ ",
            font=(font_family, int(11 * self.font_scale)),
            foreground=self.colors['danger'],
            style='Dialog.TLabel').pack(side="left")
        removed_num_label = ttk.Label(removed_frame,
            text=f"{len(removed_models)}",
            font=(font_family, int(11 * self.font_scale), 'bold'),
            foreground=self.colors['danger'],
            cursor="hand2",
            style='Dialog.TLabel')
        removed_num_label.pack(side="left")
        removed_num_label.bind("<Button-1>", lambda e: _show_model_detail('removed'))
        ttk.Label(removed_frame, text=" 个模型已下线（已从服务商移除）",
            font=(font_family, int(11 * self.font_scale)),
            foreground=self.colors['danger'],
            style='Dialog.TLabel').pack(side="left")

    # 列表前的间距（有提醒文字时加间距，没有时由列表自带间距）
    if filter_note or new_models:
        ttk.Frame(dialog, height=8).pack()

    # 搜索框
    search_frame = ttk.Frame(dialog)
    search_frame.pack(fill="x", padx=20, pady=(6, 0))

    search_var = tk.StringVar()
    search_entry = ttk.Entry(search_frame, textvariable=search_var,
                             font=self.font_label)
    search_entry.pack(fill="x")

    # 占位文字
    _search_placeholder = "输入关键词搜索模型..."
    search_entry.config(foreground=self.colors.get('text_muted', ui_theme.TEXT_MUTED))
    search_var.set(_search_placeholder)
    _search_active = [False]  # 用列表避免闭包问题

    def _on_search_focus_in(event=None):
        if not _search_active[0]:
            _search_active[0] = True
            search_var.set("")
            search_entry.config(foreground=self.colors['text_primary'])

    def _on_search_focus_out(event=None):
        if not search_var.get():
            _search_active[0] = False
            search_var.set(_search_placeholder)
            search_entry.config(foreground=self.colors.get('text_muted', ui_theme.TEXT_MUTED))

    search_entry.bind("<FocusIn>", _on_search_focus_in)
    search_entry.bind("<FocusOut>", _on_search_focus_out)

    # 模型列表框
    listbox_frame = ttk.Frame(dialog)
    listbox_frame.pack(fill="both", expand=True, padx=20, pady=10)

    listbox = tk.Listbox(listbox_frame, font=self.font_label, height=10, selectmode=tk.EXTENDED)
    scrollbar = ttk.Scrollbar(listbox_frame, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=scrollbar.set)

    scrollbar.pack(side="right", fill="y")
    listbox.pack(side="left", fill="both", expand=True)

    test_status_var = tk.StringVar(value="")
    test_status_label = ttk.Label(
        dialog,
        textvariable=test_status_var,
        font=(font_family, int(10 * self.font_scale)),
        foreground=self.colors['text_secondary'],
        style='Dialog.TLabel',
        anchor="w",
    )
    test_status_label.pack(
        fill="x",
        padx=20,
        pady=(0, 2),
    )

    def _refresh_listbox(query=""):
        """根据搜索词刷新列表，保持新增模型绿色高亮"""
        listbox.delete(0, "end")
        q = query.lower()
        for model in models:
            if not q or q in model.lower():
                listbox.insert("end", model)
        # 新增模型绿色高亮
        if new_models:
            for i in range(listbox.size()):
                if listbox.get(i) in new_models:
                    listbox.itemconfig(i, foreground=self.colors['success'])
        # 自动选中第一项
        if listbox.size() > 0:
            listbox.selection_set(0)
            listbox.see(0)

    def _on_search_changed(*args):
        if _search_active[0]:
            _refresh_listbox(search_var.get().strip())

    search_var.trace_add("write", _on_search_changed)

    # 初始填充
    _refresh_listbox()

    # 右键菜单 - 测试连通性
    _ctx_menu_font = (font_family, int(12 * self.font_scale))
    _ctx_menu = tk.Menu(listbox, tearoff=0, font=_ctx_menu_font)
    _ctx_menu.add_command(label="测试连通性", command=lambda: _test_model_in_dialog())

    def _show_ctx_menu(event):
        idx = listbox.nearest(event.y)
        if idx >= 0:
            # 如果点击的项未选中，清除其他选择只选这一项
            # 如果已选中，保持当前多选状态
            if idx not in listbox.curselection():
                listbox.selection_clear(0, "end")
                listbox.selection_set(idx)
            _ctx_menu.tk_popup(event.x_root, event.y_root)

    def _test_model_in_dialog():
        """在选择模型对话框中测试选中模型的连通性（支持多选并行测试）"""
        selection = listbox.curselection()
        if not selection:
            return

        test_models = [listbox.get(idx) for idx in selection]

        # 获取 API Key 和 Base URL
        provider_key = self.DISPLAY_TO_KEY.get(provider, provider)
        test_base_url = self.api_base_url_var.get().strip()
        test_api_key = self._get_api_key_cached(
            provider_key, test_base_url
        )

        if not test_api_key:
            messagebox.showwarning("警告",
                f"请先配置 {self.PROVIDER_DISPLAY.get(provider_key, provider)} 的 API Key",
                parent=dialog)
            return
        if not test_base_url:
            messagebox.showwarning("警告", "请先配置 Base URL", parent=dialog)
            return

        # 在列表项中显示测试状态
        for idx in selection:
            current_text = listbox.get(idx)
            # 清除旧的状态标记（如果有）
            if " [" in current_text:
                current_text = current_text.split(" [")[0]
            listbox.delete(idx)
            listbox.insert(idx, f"{current_text} [测试中...]")
        test_status_var.set(
            f"正在测试 {len(test_models)} 个模型，请稍候…"
        )
        test_status_label.configure(
            foreground=self.colors['warning']
        )

        # 测试结果收集
        results = {}
        results_lock = threading.Lock()

        def _test_single_model(model_name):
            """测试单个模型能否稳定生成程序所需评估格式。"""
            try:
                from llm_eval import probe_model_compatibility
                capability = probe_model_compatibility({
                    "api_provider": provider_key,
                    "base_url": test_base_url,
                    "model": model_name,
                }, test_api_key, force=True)
                if capability.get("status") in ("compatible", "limited"):
                    mode = "工具" if capability.get("output_mode") == "tool" else "兼容"
                    result = {
                        "status": "success",
                        "time": capability.get("response_time", 0),
                        "mode": mode,
                    }
                else:
                    result = {"status": "error", "msg": capability.get("message", "不兼容")}
            except Exception as e:
                result = {"status": "error", "msg": f"异常: {str(e)[:50]}"}

            with results_lock:
                results[model_name] = result

            # 更新列表项状态
            for idx in selection:
                if listbox.get(idx).startswith(model_name):
                    # 清除旧状态
                    current_text = listbox.get(idx)
                    if " [" in current_text:
                        current_text = current_text.split(" [")[0]
                    # 设置新状态
                    if result["status"] == "success":
                        new_text = f"{current_text} [✓ {result.get('mode', '兼容')} {result['time']:.1f}s]"
                        self.root.after(0, lambda i=idx, t=new_text: (
                            listbox.delete(i),
                            listbox.insert(i, t),
                            listbox.itemconfig(i, foreground=self.colors['success'])
                        ))
                    else:
                        new_text = f"{current_text} [✗ {result['msg']}]"
                        self.root.after(0, lambda i=idx, t=new_text: (
                            listbox.delete(i),
                            listbox.insert(i, t),
                            listbox.itemconfig(i, foreground=self.colors.get('text_muted', ui_theme.TEXT_MUTED))
                        ))
                    break

        # 启动所有测试线程
        threads = []
        for model_name in test_models:
            t = threading.Thread(target=_test_single_model, args=(model_name,), daemon=True)
            threads.append(t)
            t.start()

        # 等待所有测试完成，并在当前对话框内更新汇总。
        def _show_summary():
            for t in threads:
                t.join()

            success_count = sum(1 for r in results.values() if r["status"] == "success")
            fail_count = len(results) - success_count

            if len(test_models) == 1:
                model_name = test_models[0]
                result = results[model_name]
                if result["status"] == "success":
                    summary = (
                        f"测试完成：{model_name} 可用，"
                        f"响应时间 {result['time']:.1f} 秒"
                    )
                else:
                    summary = (
                        f"测试完成：{model_name} 不可用，"
                        "请查看列表中的失败原因"
                    )
            else:
                summary = f"测试完成：{success_count} 个可用，{fail_count} 个不可用"

            def _apply_summary():
                try:
                    if not dialog.winfo_exists():
                        return
                except tk.TclError:
                    return
                test_status_var.set(summary)
                test_status_label.configure(
                    foreground=(
                        self.colors['success']
                        if fail_count == 0
                        else self.colors['warning']
                    )
                )

            self.root.after(0, _apply_summary)

        threading.Thread(target=_show_summary, daemon=True).start()

    listbox.bind("<Button-3>", _show_ctx_menu)

    def _select_all(event=None):
        listbox.selection_set(0, "end")
        return "break"

    listbox.bind("<Control-a>", _select_all)
    listbox.bind("<Control-A>", _select_all)

    # 按钮行
    btn_frame = ttk.Frame(dialog)
    btn_frame.pack(fill="x", padx=25, pady=(10, 15))

    def _get_model_name(idx):
        """获取模型名称，去掉连通性测试的状态后缀"""
        text = listbox.get(idx)
        if " [" in text:
            text = text.split(" [")[0]
        return text

    def on_select(event=None):
        selection = listbox.curselection()
        if not selection:
            return
        selected_models = [_get_model_name(i) for i in selection]
        if len(selected_models) == 1:
            # 单选：回填输入框准备保存（保存不切换顶层活动模型）
            self.api_model_var.set(selected_models[0])
            self._pending_models_to_add = []
        else:
            # 多选：不改输入框（不切换当前活动模型），暂存待批量加入列表
            self._pending_models_to_add = selected_models
        if len(selected_models) == 1:
            status_text = f"✓ 已选择 {selected_models[0]}"
        else:
            # 多选：列出模型名，超过 5 个截断
            preview = "、".join(selected_models[:5])
            if len(selected_models) > 5:
                preview += f" 等 {len(selected_models)} 个"
            status_text = f"✓ 已选择 {len(selected_models)} 个模型：{preview}"
        self._update_api_status(
            text=status_text,
            foreground=self.colors['success']
        )
        _close_dialog()

    def on_double_click(event):
        selection = listbox.curselection()
        if selection:
            selected_model = _get_model_name(selection[0])
            self.api_model_var.set(selected_model)
            _close_dialog()
            self._update_api_status(text="⏳ 正在测试连接...", foreground=self.colors['warning'])
            self.root.after(300, self.test_api_connection)

    # 按钮布局（居中）
    btn_inner = ttk.Frame(btn_frame)
    btn_inner.pack()
    ttk.Button(btn_inner, text="确定", command=on_select, width=12).pack(side="left", padx=8)
    ttk.Button(btn_inner, text="取消", command=_close_dialog, width=12).pack(side="left", padx=8)

    # 绑定回车键和双击
    dialog.bind("<Return>", lambda e: on_select())
    listbox.bind("<Double-Button-1>", on_double_click)

    place_window_centered(dialog, dialog_width, dialog_height, parent=self.root)
    dialog.deiconify()
    dialog.grab_set()
