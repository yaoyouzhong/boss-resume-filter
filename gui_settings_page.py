"""Incremental Tk construction for the system-settings page."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator, Mapping
from tkinter import ttk
from typing import Any, Protocol


class SettingsPageHost(Protocol):
    """Shared visual services required by the incremental settings builder."""

    root: tk.Misc
    api_scrollable_frame: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    font_label: Any
    icons: Any
    widget_support: Any
    feedback_support: Any
    input_support: Any
    layout_support: Any
    api_config: Mapping[str, Any]
    saved_models: list[Mapping[str, Any]]

    def __getattr__(self, name: str) -> Any: ...

    def __setattr__(self, name: str, value: Any) -> None: ...


def build_settings_content_steps(
    host: SettingsPageHost,
    ui_config: Mapping[str, Any],
    *,
    font_family: str,
    font_family_semibold: str,
    traffic_light_base_size: int,
    provider_display: Mapping[str, str],
    display_to_key: Mapping[str, str],
) -> Iterator[None]:
    """Build settings controls without reading keys or performing external actions."""
    self = host
    api_container = self.api_scrollable_frame

    # 系统设置页面标题
    self.widget_support.create_page_header(api_container, "系统设置")

    # 新电脑提示：检测到已保存配置但 API Key 丢失
    self.reconfig_card = None
    if hasattr(self, 'api_config') and self.api_config.get("needs_reconfigure"):
        _pad = int(ui_config['label_frame_padding'] * self.dpi_scale * self.zoom_factor)
        self.reconfig_card = tk.Frame(api_container, bg=self.colors['bg_card'],
                                      highlightbackground=self.colors['border'], highlightthickness=1)
        self.reconfig_card.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))
        tk.Label(self.reconfig_card, text="提示",
                 font=(font_family_semibold, int(13 * self.font_scale)),
                 fg=self.colors['text_primary'], bg=self.colors['bg_card']).pack(anchor="w", padx=_pad, pady=(_pad, 0))
        _inner = ttk.Frame(self.reconfig_card, style='TFrame')
        _inner.pack(fill="both", expand=True, padx=_pad, pady=_pad)
        ttk.Label(_inner, text="检测到已保存的模型配置，但 API Key 未配置（可能是新电脑）",
                 font=self.font_label, foreground=self.colors['warning'],
                 background=self.colors['bg_card']).pack(anchor="w")
        ttk.Label(_inner, text="请在下方重新输入 API Key 并点击「保存模型」",
                 font=self.font_label, foreground=self.colors['text_secondary'],
                 background=self.colors['bg_card']).pack(anchor="w", pady=(5, 0))

    yield

    # 模型用途分配
    assignment_card = self.widget_support.create_card(api_container, "使用中的模型",
        fill="both", expand=True, padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(20 * self.dpi_scale * self.zoom_factor))
    assignment_frame = ttk.Frame(assignment_card, style='TFrame')
    assignment_frame.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor),
                          pady=int(15 * self.dpi_scale * self.zoom_factor))
    self.default_model_choice_var = tk.StringVar()
    self.education_model_choice_var = tk.StringVar()
    self._model_choice_refs = {}
    self._updating_model_assignment_controls = False
    self._assigned_model_test_buttons = {}
    self._assigned_model_test_status_labels = {}
    traffic_light_size = int(
        traffic_light_base_size * self.dpi_scale * self.zoom_factor
    )
    self._assigned_model_test_icons = {
        "pending": self.icons.get('traffic_light_pending', traffic_light_size, self.colors['text_primary']),
        "success": self.icons.get('traffic_light_success', traffic_light_size, self.colors['text_primary']),
        "error": self.icons.get('traffic_light_error', traffic_light_size, self.colors['text_primary']),
    }
    self._assigned_model_test_states = {"default": "pending", "education": "pending"}
    self._assigned_model_test_tokens = {"default": 0, "education": 0}
    self._assigned_model_test_refs = {}
    self._assigned_model_test_results = {}

    label_width_assignment = 14
    model_choice_width = 34
    icon_test_default_model = self._assigned_model_test_icons["pending"]
    icon_test_education_model = self._assigned_model_test_icons["pending"]

    default_row = ttk.Frame(assignment_frame, style='TFrame')
    default_row.pack(fill="x")
    ttk.Label(default_row, text="默认 AI 模型:", font=self.font_label,
              width=label_width_assignment).grid(row=0, column=0, sticky="w")
    self.default_model_combo = ttk.Combobox(
        default_row, textvariable=self.default_model_choice_var,
        state="readonly", width=model_choice_width, font=self.font_label,
    )
    self.default_model_combo.grid(
        row=0, column=1, sticky="w",
        padx=(int(5 * self.dpi_scale * self.zoom_factor), int(8 * self.dpi_scale * self.zoom_factor)),
    )
    self.default_model_combo.bind("<<ComboboxSelected>>", self._on_default_model_selected)
    btn_test_default_model = tk.Label(
        default_row, image=icon_test_default_model,
        bg=self.colors['bg_card'], cursor="hand2", takefocus=1,
    )
    btn_test_default_model._icon_ref = icon_test_default_model
    self._assigned_model_test_buttons["default"] = btn_test_default_model
    btn_test_default_model.grid(row=0, column=2, sticky="e")
    default_test_status = ttk.Label(
        default_row, text="未检测", font=self.font_label,
        foreground=self.colors['text_secondary'], background=self.colors['bg_card'], width=6,
    )
    self._assigned_model_test_status_labels["default"] = default_test_status
    default_test_status.grid(
        row=0, column=3, sticky="w",
        padx=(int(8 * self.dpi_scale * self.zoom_factor), 0),
    )
    btn_test_default_model.bind("<Button-1>", lambda _e: self._test_assigned_model("default"))
    btn_test_default_model.bind("<Return>", lambda _e: self._test_assigned_model("default"))
    btn_test_default_model.bind("<space>", lambda _e: self._test_assigned_model("default"))
    btn_test_default_model.bind(
        "<Enter>",
        lambda e: self._show_assigned_model_test_tooltip("default", e),
    )
    btn_test_default_model.bind("<Leave>", self.feedback_support.hide_tooltip)

    yield

    education_row = ttk.Frame(assignment_frame, style='TFrame')
    education_row.pack(fill="x", pady=(int(10 * self.dpi_scale * self.zoom_factor), 0))
    ttk.Label(education_row, text="学历核验模型:", font=self.font_label,
              width=label_width_assignment).grid(row=0, column=0, sticky="w")
    self.education_model_combo = ttk.Combobox(
        education_row, textvariable=self.education_model_choice_var,
        state="readonly", width=model_choice_width, font=self.font_label,
    )
    self.education_model_combo.grid(
        row=0, column=1, sticky="w",
        padx=(int(5 * self.dpi_scale * self.zoom_factor), int(8 * self.dpi_scale * self.zoom_factor)),
    )
    self.education_model_combo.bind("<<ComboboxSelected>>", self._on_education_model_selected)
    btn_test_education_model = tk.Label(
        education_row, image=icon_test_education_model,
        bg=self.colors['bg_card'], cursor="hand2", takefocus=1,
    )
    btn_test_education_model._icon_ref = icon_test_education_model
    self._assigned_model_test_buttons["education"] = btn_test_education_model
    btn_test_education_model.grid(row=0, column=2, sticky="e")
    education_test_status = ttk.Label(
        education_row, text="未检测", font=self.font_label,
        foreground=self.colors['text_secondary'], background=self.colors['bg_card'], width=6,
    )
    self._assigned_model_test_status_labels["education"] = education_test_status
    education_test_status.grid(
        row=0, column=3, sticky="w",
        padx=(int(8 * self.dpi_scale * self.zoom_factor), 0),
    )
    btn_test_education_model.bind("<Button-1>", lambda _e: self._test_assigned_model("education"))
    btn_test_education_model.bind("<Return>", lambda _e: self._test_assigned_model("education"))
    btn_test_education_model.bind("<space>", lambda _e: self._test_assigned_model("education"))
    btn_test_education_model.bind(
        "<Enter>",
        lambda e: self._show_assigned_model_test_tooltip("education", e),
    )
    btn_test_education_model.bind("<Leave>", self.feedback_support.hide_tooltip)

    yield

    # 模型接入配置
    config_card = self.widget_support.create_card(api_container, "模型接入",
        fill="both", expand=True, padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

    # API 配置输入区（服务商、Key、URL、模型名称）
    input_frame = ttk.Frame(config_card, style='TFrame')
    input_frame.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

    # 第一行：服务商
    row1 = ttk.Frame(input_frame, style='TFrame')
    row1.pack(fill="x")

    # 引用模块级常量（兼容旧代码 self.PROVIDER_DISPLAY / self.DISPLAY_TO_KEY）
    self.PROVIDER_DISPLAY = provider_display
    self.DISPLAY_TO_KEY = display_to_key

    ttk.Label(row1, text="服务商:", font=self.font_label, width=ui_config['label_width_provider']).pack(side="left")
    self.api_provider_var = tk.StringVar(value=self.PROVIDER_DISPLAY["qwen"])
    self.api_provider_combo = ttk.Combobox(row1, textvariable=self.api_provider_var,
                                           values=list(self.PROVIDER_DISPLAY.values()),
                                           width=18, font=self.font_label)
    self.api_provider_combo.pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), int(20 * self.dpi_scale * self.zoom_factor)))
    self.api_provider_combo.bind("<<ComboboxSelected>>", self.on_api_provider_changed)

    # 第二行：模型名称
    row2 = ttk.Frame(input_frame, style='TFrame')
    row2.pack(fill="x", pady=(int(10 * self.dpi_scale * self.zoom_factor), 0))

    ttk.Label(row2, text="模型名称:", font=self.font_label, width=ui_config['label_width_model']).pack(side="left")
    self.api_model_var = tk.StringVar()
    model_entry = ttk.Entry(
        row2,
        textvariable=self.api_model_var,
        width=18,
        font=self.font_label,
        style='SettingsModel.TEntry',
    )
    model_entry.pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), int(10 * self.dpi_scale * self.zoom_factor)))
    self.input_support.bind_entry_context_menu(model_entry)

    # 获取模型列表按钮
    icon_download_models = self.icons.button('download', self.colors['text_primary'])
    btn_fetch = ttk.Button(
        row2,
        image=icon_download_models,
        text=" 自动识别并获取模型",
        compound=tk.LEFT,
        command=self.fetch_model_list,
    )
    btn_fetch._icon_ref = icon_download_models
    btn_fetch.pack(side="left")

    yield

    # 第三行：API Key
    row3 = ttk.Frame(input_frame, style='TFrame')
    row3.pack(fill="x", pady=(int(10 * self.dpi_scale * self.zoom_factor), 0))

    ttk.Label(row3, text="API Key:", font=self.font_label, width=ui_config['label_width_api_key']).pack(side="left")
    self.api_key_var = tk.StringVar()
    self.api_key_entry = ttk.Entry(
        row3, textvariable=self.api_key_var,
        width=ui_config['entry_width_url'], font=self.font_label, show="*",
    )
    self.api_key_entry.pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), 0))
    self.input_support.bind_entry_context_menu(self.api_key_entry)

    # 按住显示 API Key；松开或离开按钮立即恢复掩码。
    self.api_key_show_var = tk.BooleanVar(value=False)
    eye_icon = self.icons.button('eye', self.colors['text_primary'])
    eye_off_icon = self.icons.button('eye_off', self.colors['text_primary'])
    self.api_key_toggle_btn = tk.Button(row3, image=eye_icon,
        relief="flat", overrelief="flat", bd=0, highlightthickness=0,
        bg=self.colors['bg_card'], activebackground=self.colors['bg_card'],
        cursor="hand2")
    self.api_key_toggle_btn._icon_eye = eye_icon
    self.api_key_toggle_btn._icon_eye_off = eye_off_icon
    self.api_key_toggle_btn.pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), 0))
    self.api_key_toggle_btn.bind("<ButtonPress-1>", self._show_api_key_while_pressed)
    self.api_key_toggle_btn.bind("<ButtonRelease-1>", self._hide_api_key_after_release)
    self.api_key_toggle_btn.bind("<Leave>", self._hide_api_key_after_release)
    self.api_key_toggle_btn.bind("<FocusOut>", self._hide_api_key_after_release)

    yield

    # 第四行：Base URL
    row4 = ttk.Frame(input_frame, style='TFrame')
    row4.pack(fill="x", pady=(int(10 * self.dpi_scale * self.zoom_factor), 0))

    ttk.Label(row4, text="Base URL:", font=self.font_label, width=ui_config['label_width_url']).pack(side="left")
    self.api_base_url_var = tk.StringVar()
    url_entry = ttk.Entry(
        row4, textvariable=self.api_base_url_var,
        width=ui_config['entry_width_url'], font=self.font_label,
    )
    url_entry.pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), 0))
    self.input_support.bind_entry_context_menu(url_entry)

    # 操作按钮行
    button_row = ttk.Frame(config_card, style='TFrame')
    button_row.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

    icon_save_api = self.icons.button('save', self.colors['text_primary'])
    btn_save_api = ttk.Button(button_row, image=icon_save_api, text=" 保存模型", compound=tk.LEFT, command=self.save_api_config)
    btn_save_api._icon_ref = icon_save_api
    btn_save_api.pack(side="left", padx=(int(10 * self.dpi_scale * self.zoom_factor), int(5 * self.dpi_scale * self.zoom_factor)))
    icon_search_test = self.icons.button('search', self.colors['text_primary'])
    btn_test = ttk.Button(button_row, image=icon_search_test, text=" 测试连接", compound=tk.LEFT, command=self.test_api_connection)
    btn_test._icon_ref = icon_search_test
    btn_test.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))

    # API 配置状态提示（改为 Frame 容器，支持多段可点击文本）
    self.api_status_frame = ttk.Frame(config_card)
    self.api_status_frame.pack(anchor="w", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=(0, int(10 * self.dpi_scale * self.zoom_factor)))
    self.api_status_label = ttk.Label(self.api_status_frame, text="",
                                     font=(font_family, int(11 * self.font_scale)),
                                     foreground=self.colors['success'])
    self.api_status_label.pack(side="left")
    # 用于存放可点击的标签引用
    self._status_clickable_labels = []

    yield

    # 已保存模型列表
    model_list_card = self.widget_support.create_card(api_container, "已保存模型",
        fill="both", expand=True, padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

    # 模型列表 Treeview
    model_columns = ("name", "provider", "compat", "base_url")
    self.model_list_tree = ttk.Treeview(model_list_card, columns=model_columns, show="headings", selectmode='extended')
    self.model_list_tree.heading("name", text="模型名称")
    self.model_list_tree.heading("provider", text="服务商")
    self.model_list_tree.heading("compat", text="状态")
    self.model_list_tree.heading("base_url", text="Base URL")
    self.model_list_tree.column("name", width=260, minwidth=200, anchor='center', stretch=False)
    self.model_list_tree.column("provider", width=240, minwidth=200, anchor='center', stretch=False)
    self.model_list_tree.column("compat", width=180, minwidth=120, anchor='center', stretch=False)
    self.model_list_tree.column("base_url", width=300, minwidth=170, anchor='w', stretch=True)
    # 默认显示全部列
    self.model_list_tree.configure(displaycolumns=("name", "provider", "compat", "base_url"))

    # 已保存模型列表字体比表格字体小一号
    fs = self.dpi_scale * self.zoom_factor
    model_list_font = (font_family, int(12 * self.font_scale))
    model_tree_style = ttk.Style()
    model_tree_style.configure("ModelList.Treeview", font=model_list_font,
                              rowheight=int(ui_config['treeview_rowheight'] * fs))
    model_tree_style.configure("ModelList.Treeview.Heading",
                              font=(font_family, int(12 * self.font_scale), 'bold'))
    self.model_list_tree.configure(style="ModelList.Treeview")

    # 滚动条（垂直 + 水平）
    model_v_scrollbar = ttk.Scrollbar(model_list_card, orient="vertical", command=self.model_list_tree.yview)
    model_h_scrollbar = ttk.Scrollbar(model_list_card, orient="horizontal", command=self.model_list_tree.xview)
    self.model_list_tree.configure(yscrollcommand=model_v_scrollbar.set, xscrollcommand=model_h_scrollbar.set)

    self.model_list_tree.pack(side="top", fill="both", expand=True)
    model_v_scrollbar.pack(side="right", fill="y")
    model_h_scrollbar.pack(side="bottom", fill="x")

    # 右键菜单 - 模型列表
    model_menu_font = (font_family, int(12 * self.font_scale))
    self.model_context_menu = tk.Menu(self.model_list_tree, tearoff=0, font=model_menu_font)
    self.model_context_menu.add_command(label="测试连通性", command=self.test_saved_model_connectivity)
    self.model_context_menu.add_separator()
    self.model_context_menu.add_command(label="删除模型", command=self.delete_selected_model)

    def show_model_context_menu(event):
        item = self.model_list_tree.identify_row(event.y)
        if item:
            # 右键点击的行已在多选集合内时，保持现有选区
            if item not in self.model_list_tree.selection():
                self.model_list_tree.selection_set(item)
            self.model_context_menu.tk_popup(event.x_root, event.y_root)

    self.model_list_tree.bind("<Button-3>", show_model_context_menu)

    # 列 tooltip（文字截断时弹出）
    self._model_tooltip_after_id = None
    self._model_tooltip = None
    self._model_tooltip_item = None
    # 列标识 → values 下标
    self._model_col_idx = {"#1": 0, "#2": 1, "#3": 2, "#4": 3, "#5": 4}

    def _on_model_motion(event):
        """鼠标移动时检查是否需要显示 tooltip"""
        item = self.model_list_tree.identify_row(event.y)
        column = self.model_list_tree.identify_column(event.x)
        if not item or column not in self._model_col_idx:
            self.feedback_support.hide_model_tooltip()
            return
        idx = self._model_col_idx[column]
        values = self.model_list_tree.item(item, 'values')
        if not values or len(values) <= idx:
            self.feedback_support.hide_model_tooltip()
            return
        text = str(values[idx])
        if not text:
            self.feedback_support.hide_model_tooltip()
            return
        # 用 bbox 获取单元格实际像素宽度
        try:
            bbox = self.model_list_tree.bbox(item, column)
            if bbox:
                cell_width = bbox[2]  # (x, y, width, height)
            else:
                cell_width = self.model_list_tree.column(column, "width")
        except Exception:
            cell_width = self.model_list_tree.column(column, "width")
        # 用字体度量文字像素宽度
        try:
            style = ttk.Style()
            font_name = style.lookup("ModelList.Treeview", "font") or (font_family, 12)
            from tkinter.font import Font
            text_width = Font(font=font_name).measure(text)
        except Exception:
            text_width = len(text) * 8
        # 内边距 16px
        if text_width <= cell_width - 16:
            self.feedback_support.hide_model_tooltip()
            return
        tooltip_key = (item, column)
        if tooltip_key == self._model_tooltip_item and self._model_tooltip and self._model_tooltip.winfo_exists():
            return
        self._model_tooltip_item = tooltip_key
        if self._model_tooltip_after_id:
            self.root.after_cancel(self._model_tooltip_after_id)
        x = self.root.winfo_pointerx() + 15
        y = self.root.winfo_pointery() + 10
        self._model_tooltip_after_id = self.root.after(
            300,
            lambda t=text, k=tooltip_key, px=x, py=y: (
                self.feedback_support.show_model_tooltip(t, px, py, k)
            ),
        )

    def _on_model_leave(event):
        """鼠标离开时隐藏 tooltip"""
        self.feedback_support.hide_model_tooltip()

    self.model_list_tree.bind("<Motion>", _on_model_motion)
    self.model_list_tree.bind("<Leave>", _on_model_leave)
    self.model_list_tree.bind(
        "<Configure>",
        lambda _event: self.layout_support.update_model_list_columns(),
        add="+",
    )

    # 初始化模型列表
    self.saved_models = []

    yield

    data_card = self.widget_support.create_card(
        api_container,
        "数据备份与恢复",
        fill="x",
        padx=int(25 * self.dpi_scale * self.zoom_factor),
        pady=int(15 * self.dpi_scale * self.zoom_factor),
    )
    self.data_maintenance_card = data_card
    ttk.Label(
        data_card,
        text=(
            "备份包含候选人、岗位配置、联系清单和已导入的简历副本。"
            "导出的 ZIP 未加密，请保存在受控位置。"
        ),
        font=self.font_label,
        foreground=self.colors["text_secondary"],
        background=self.colors["bg_card"],
        wraplength=int(900 * self.dpi_scale * self.zoom_factor),
        justify="left",
    ).pack(anchor="w")

    yield

    data_button_row = ttk.Frame(data_card, style="TFrame")
    data_button_row.pack(
        fill="x",
        pady=(int(12 * self.dpi_scale * self.zoom_factor), 0),
    )
    export_icon = self.icons.button("export", self.colors["text_primary"])
    export_button = ttk.Button(
        data_button_row,
        image=export_icon,
        text=" 导出数据备份",
        compound=tk.LEFT,
        command=self._export_data_backup,
    )
    export_button._icon_ref = export_icon
    export_button.pack(side="left")

    import_icon = self.icons.button("import", self.colors["text_primary"])
    restore_button = ttk.Button(
        data_button_row,
        image=import_icon,
        text=" 从备份恢复",
        compound=tk.LEFT,
        command=self._restore_data_backup,
    )
    restore_button._icon_ref = import_icon
    restore_button.pack(
        side="left",
        padx=(int(10 * self.dpi_scale * self.zoom_factor), 0),
    )

    audit_icon = self.icons.button(
        "health_shield",
        self.colors["text_primary"],
    )
    audit_button = ttk.Button(
        data_button_row,
        image=audit_icon,
        text=" 简历存储体检",
        compound=tk.LEFT,
        command=self._show_resume_storage_audit,
    )
    audit_button._icon_ref = audit_icon
    audit_button.pack(
        side="left",
        padx=(int(10 * self.dpi_scale * self.zoom_factor), 0),
    )

    yield

    self.data_backup_status_var = tk.StringVar(
        value=self._data_backup_note_text()
    )
    ttk.Label(
        data_card,
        textvariable=self.data_backup_status_var,
        font=self.font_label,
        foreground=self.colors["text_secondary"],
        background=self.colors["bg_card"],
        wraplength=int(1050 * self.dpi_scale * self.zoom_factor),
        justify="left",
    ).pack(anchor="w", pady=(int(10 * self.dpi_scale * self.zoom_factor), 0))

    yield

    diagnostic_card = self.widget_support.create_card(
        api_container,
        "故障诊断",
        fill="x",
        padx=int(25 * self.dpi_scale * self.zoom_factor),
        pady=int(15 * self.dpi_scale * self.zoom_factor),
    )
    ttk.Label(
        diagnostic_card,
        text=(
            "导出环境、版本、数据结构计数和最近日志。"
            "不包含候选人原始数据、简历、岗位内容、API Key、Cookie 或浏览器资料；"
            "日志会自动脱敏并复核残留。"
        ),
        font=self.font_label,
        foreground=self.colors["text_secondary"],
        background=self.colors["bg_card"],
        wraplength=int(900 * self.dpi_scale * self.zoom_factor),
        justify="left",
    ).pack(anchor="w")
    diagnostic_icon = self.icons.button(
        "export",
        self.colors["text_primary"],
    )
    diagnostic_button = ttk.Button(
        diagnostic_card,
        image=diagnostic_icon,
        text=" 导出脱敏诊断包",
        compound=tk.LEFT,
        command=self._export_diagnostic_package,
    )
    diagnostic_button._icon_ref = diagnostic_icon
    diagnostic_button.pack(
        anchor="w",
        pady=(int(12 * self.dpi_scale * self.zoom_factor), 0),
    )
    self.diagnostic_package_status_var = tk.StringVar(
        value=self._diagnostic_export_note_text()
    )
    ttk.Label(
        diagnostic_card,
        textvariable=self.diagnostic_package_status_var,
        font=self.font_label,
        foreground=self.colors["text_secondary"],
        background=self.colors["bg_card"],
    ).pack(anchor="w", pady=(int(10 * self.dpi_scale * self.zoom_factor), 0))
