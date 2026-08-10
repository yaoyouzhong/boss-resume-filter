"""Incremental Tk construction for the job-configuration page."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator, Mapping
from tkinter import ttk
from typing import Any

import ui_theme
from filtering import GENDER_VALUES


def build_config_page_steps(
    host: Any,
    ui_config: Mapping[str, Any],
    *,
    font_family: str,
) -> Iterator[None]:
    """Build the job-configuration page without loading or saving configuration."""
    self = host
    self.config_page = ttk.Frame(self.pages_frame, style='Page.TFrame')
    self._job_form_tracking_ready = False
    self._job_form_loading = False
    self._job_form_saved_snapshot = None
    self._job_form_loaded_name = ""
    self._job_form_status_after_id = None
    self._job_config_preview = None
    self._requirement_parse_generation = 0
    self._active_requirement_parse_id = None
    self._ai_enhance_pending = False

    # 页面标题
    self._create_page_header(self.config_page, "岗位配置", top_padding=15)

    # 配置容器 - 支持垂直滚动（macOS Tk 9.0+ 用 Text，其他用 Canvas）
    scroll_frame = ttk.Frame(self.config_page, style='Card.TFrame')
    scroll_frame.pack(fill="both", expand=True)

    self.config_canvas, self.config_scrollable_frame = (
        self.scroll_support.create_scroll_container(
            scroll_frame,
            self.colors['bg_card'],
        )
    )

    # 使用 scrollable_frame 作为实际容器
    config_container = self.config_scrollable_frame

    yield

    # 岗位选择区域
    select_frame = ttk.Frame(config_container, style='TFrame')
    self._config_select_frame = select_frame
    select_frame.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=(int(25 * self.dpi_scale * self.zoom_factor), int(10 * self.dpi_scale * self.zoom_factor)))

    ttk.Label(select_frame, text="选择岗位:", font=self.font_label,
             background=self.colors['bg_card']).pack(side="left")
    # 高频动作直接显示，低频动作收入菜单。
    more_menu = tk.Menu(select_frame, tearoff=0, font=self.font_label)
    icon_import_cfg = self.icons.button('import', self.colors['text_primary'])
    icon_export_cfg = self.icons.button('export', self.colors['text_primary'])
    icon_trash_small = self.icons.button('trash', self.colors['danger'])
    more_menu._icon_refs = [icon_import_cfg, icon_export_cfg, icon_trash_small]
    more_menu.add_command(
        label=" 导入配置", image=icon_import_cfg, compound=tk.LEFT,
        command=self.import_config,
    )
    more_menu.add_command(
        label=" 导出配置", image=icon_export_cfg, compound=tk.LEFT,
        command=self.export_config,
    )
    more_menu.add_separator()
    more_menu.add_command(
        label=" 删除当前岗位", image=icon_trash_small, compound=tk.LEFT,
        command=self.delete_job,
    )
    btn_more = ttk.Menubutton(
        select_frame,
        text="更多操作",
        menu=more_menu,
        width=9,
        style='CenteredActions.TMenubutton',
    )
    self.config_more_menu_button = btn_more
    btn_more.pack(side="right", padx=(int(8 * self.dpi_scale * self.zoom_factor), 0))
    self._context_menus.append(more_menu)

    icon_plus_small = self.icons.button('plus', self.colors['success'])
    btn_add = ttk.Button(select_frame, image=icon_plus_small, text="新建", compound=tk.LEFT, command=self.add_job)
    btn_add._icon_ref = icon_plus_small
    btn_add.pack(side="right", padx=int(8 * self.dpi_scale * self.zoom_factor))

    self.btn_add_hint = None
    # 下拉框
    self.config_job_combo = ttk.Combobox(select_frame, values=list(self.job_rules.keys()), width=28, font=self.font_label)
    self.config_job_combo.pack(
        side="left", padx=(int(15 * self.dpi_scale * self.zoom_factor), 0)
    )
    self.config_job_combo.bind("<<ComboboxSelected>>", self.on_job_selected)
    self.job_form_status_var = tk.StringVar(value="未选择岗位")
    self.job_form_status_label = ttk.Label(
        select_frame,
        textvariable=self.job_form_status_var,
        font=self.font_log,
        foreground=self.colors['text_secondary'],
        background=self.colors['bg_card'],
    )
    self.job_form_status_label.pack(
        side="left",
        padx=(self.inline_note_gap, int(8 * self.dpi_scale * self.zoom_factor)),
    )

    # ===== 新建岗位步骤引导条 =====
    _fs = self.dpi_scale * self.zoom_factor
    self._job_step_bar = ttk.Frame(config_container, style='TFrame')
    # 默认隐藏，add_job 时显示

    self._job_step_labels: list[ttk.Label] = []
    _step_texts = ["① 填入需求", "② 解析需求", "③ 检查结果", "④ 保存配置"]
    _step_font = (font_family, int(12 * self.font_scale))

    # 标题行
    _step_title = ttk.Label(self._job_step_bar, text="新建岗位流程",
                            font=self.font_section,
                            foreground=self.colors['primary'],
                            background=self.colors['bg_card'])
    _step_title.pack(anchor="w", padx=int(20 * _fs), pady=(int(12 * _fs), int(4 * _fs)))

    # 步骤行
    _steps_row = ttk.Frame(self._job_step_bar, style='TFrame')
    _steps_row.pack(fill="x", padx=int(20 * _fs), pady=(0, int(12 * _fs)))

    for i, text in enumerate(_step_texts):
        if i > 0:
            arrow = ttk.Label(_steps_row, text="→", font=_step_font,
                              foreground=self.colors.get('text_muted', ui_theme.TEXT_MUTED),
                              background=self.colors['bg_card'])
            arrow.pack(side="left", padx=int(6 * _fs))
        lbl = ttk.Label(_steps_row, text=text, font=_step_font,
                        background=self.colors['bg_card'])
        lbl.pack(side="left", padx=int(2 * _fs))
        self._job_step_labels.append(lbl)

    self._job_step_active = -1  # -1 = 隐藏

    yield

    # ===== 需求文档解析区域 =====
    def _build_requirement_toggle(title_bar, padding):
        title_bg = self.colors.get('bg_footer', ui_theme.BG_FOOTER)
        self.requirement_title_bar = title_bar
        self.requirement_header_status_var = tk.StringVar(value="")
        self.requirement_expand_icon = self.icons.button(
            'chevron_down', self.colors['text_secondary']
        )
        self.requirement_collapse_icon = self.icons.button(
            'chevron_up', self.colors['text_secondary']
        )
        self.requirement_toggle_icon_label = tk.Label(
            title_bar,
            image=self.requirement_collapse_icon,
            bg=title_bg,
            cursor="hand2",
        )
        self.requirement_toggle_icon_label.pack(
            side="right", padx=(int(8 * _fs), padding)
        )
        self.requirement_header_status_label = tk.Label(
            title_bar,
            textvariable=self.requirement_header_status_var,
            font=self.font_log,
            fg=self.colors['text_secondary'],
            bg=title_bg,
            cursor="hand2",
        )
        self.requirement_header_status_label.pack(side="right")

    parse_frame = self._create_card(
        config_container,
        "招聘需求",
        title_trailing_builder=_build_requirement_toggle,
        fill="x",
        padx=int(25 * self.dpi_scale * self.zoom_factor),
        pady=int(20 * self.dpi_scale * self.zoom_factor),
    )
    self.requirement_parse_frame = parse_frame
    self.requirement_section_expanded = True
    self._bind_requirement_header_interaction()

    yield

    # 需求输入框
    self._req_header_frame = ttk.Frame(parse_frame, style='TFrame')
    req_header = self._req_header_frame
    req_header.pack(fill="x", pady=(0, int(10 * self.dpi_scale * self.zoom_factor)))
    ttk.Label(req_header, text="粘贴招聘需求内容:", font=self.font_label,
             background=self.colors['bg_card']).pack(side="left")
    icon_clipboard = self.icons.button('clipboard', self.colors['text_primary'])
    self.requirement_template_btn = ttk.Button(req_header, image=icon_clipboard, text=" 招聘需求示例", compound=tk.LEFT, command=self._insert_requirement_template)
    self.requirement_template_btn._icon_ref = icon_clipboard
    self.requirement_template_btn.pack(side="right")
    self.requirement_template_btn.state(['disabled'])
    self.requirement_hint_label = None

    # 需求输入框 - 白底 + focus蓝边框 + 占位提示
    text_container = ttk.Frame(parse_frame, style='TFrame')
    text_container.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))

    self.requirement_text = tk.Text(text_container, height=ui_config['text_height_large'],
                                    font=(font_family, int(10 * self.font_scale)),
                                    bg=self.colors['bg_card'], fg=self.colors['text_primary'],
                                    borderwidth=0, highlightthickness=2,
                                    highlightbackground=self.colors['border'],
                                    highlightcolor=self.colors['primary'])
    self.requirement_text.pack(side="left", fill="both", expand=True)

    req_scroll = ttk.Scrollbar(text_container, orient="vertical", command=self.requirement_text.yview)
    req_scroll.pack(side="right", fill="y")
    self.requirement_text.config(yscrollcommand=req_scroll.set)

    # 占位提示文字
    self._req_placeholder_text = "在此粘贴招聘需求内容..."
    _placeholder_color = self.colors.get('text_muted', ui_theme.TEXT_MUTED)
    self.requirement_text.tag_configure("placeholder", foreground=_placeholder_color)
    self.requirement_text.insert("1.0", self._req_placeholder_text, "placeholder")
    self._req_placeholder_active = True

    def _req_focus_in(event):
        if self._req_placeholder_active:
            self.requirement_text.delete("1.0", tk.END)
            self.requirement_text.tag_remove("placeholder", "1.0", tk.END)
            self._req_placeholder_active = False

    def _req_focus_out(event):
        content = self.requirement_text.get("1.0", tk.END).strip()
        if not content:
            self.requirement_text.delete("1.0", tk.END)
            self.requirement_text.insert("1.0", self._req_placeholder_text, "placeholder")
            self._req_placeholder_active = True

    self.requirement_text.bind('<FocusIn>', _req_focus_in)
    self.requirement_text.bind('<FocusOut>', _req_focus_out)
    # 粘贴后保持解析入口就近可见。
    def _on_paste(event):
        self._hide_requirement_hint()
    self.requirement_text.bind('<<Paste>>', _on_paste, add='+')

    # Text 控件 Enter/Leave 绑定，防止页面滚动干扰 Text 自身滚动
    self.requirement_text.bind('<Enter>', lambda e: setattr(self, '_over_text_widget', True))
    self.requirement_text.bind('<Leave>', lambda e: setattr(self, '_over_text_widget', False))

    self.bind_text_context_menu(self.requirement_text)

    # 解析按钮
    self._parse_btn_frame = ttk.Frame(parse_frame, style='TFrame')
    parse_btn_frame = self._parse_btn_frame
    parse_btn_frame.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))
    icon_search_parse = self.icons.button('search', self.colors['text_primary'])
    self.btn_parse_requirement = ttk.Button(parse_btn_frame, image=icon_search_parse, text=" 解析招聘需求", compound=tk.LEFT, command=self.parse_requirement)
    self.btn_parse_requirement._icon_ref = icon_search_parse
    self.btn_parse_requirement.pack(side="left")
    self.parse_hint_label = None

    # 解析结果展示
    self.parse_result_label = ttk.Label(parse_frame, text="", font=self.font_label,
                                       foreground=self.colors['success'], background=self.colors['bg_card'],
                                       justify="left")
    self.parse_result_label.pack(fill="x", anchor="w", pady=int(10 * self.dpi_scale * self.zoom_factor))

    yield

    # ===== 解析结果详细展示区域 =====
    self.result_detail_frame = ttk.Frame(config_container, style='Card.TFrame')
    # 先隐藏，等 show_page_config 或 on_job_selected 时再显示

    # 基本信息区
    basic_frame = self._create_card(self.result_detail_frame, "基础筛选条件",
        fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

    # 岗位名称
    row1 = ttk.Frame(basic_frame, style='TFrame')
    row1.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))
    ttk.Label(row1, text="岗位名称:", font=self.font_label, width=ui_config['entry_width_job'],
             background=self.colors['bg_card']).pack(side="left")
    self.job_name_var = tk.StringVar()
    self.job_name_entry = ttk.Entry(row1, textvariable=self.job_name_var, width=22, font=self.font_label)
    self.job_name_entry.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
    self.bind_entry_context_menu(self.job_name_entry)

    basic_filter_input_width = 6
    secondary_filter_gap = int(30 * self.dpi_scale * self.zoom_factor)

    # 左列为枚举条件，右列为数字门槛；薪资和地点各自保留完整一行。
    self.salary_min_var = tk.StringVar()
    self.salary_max_var = tk.StringVar()
    self.salary_min_var.trace_add('write', self._validate_salary_input)
    self.salary_max_var.trace_add('write', self._validate_salary_input)
    row_education_experience = ttk.Frame(basic_frame, style='TFrame')
    row_education_experience.pack(
        fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor)
    )
    ttk.Label(
        row_education_experience,
        text="最低学历:",
        font=self.font_label,
        width=ui_config['entry_width_job'],
        background=self.colors['bg_card'],
    ).pack(side="left")
    self.edu_var = tk.StringVar(value="不限")
    edu_combo = ttk.Combobox(
        row_education_experience,
        textvariable=self.edu_var,
        values=["不限", "高中", "中专", "大专", "本科", "硕士", "博士"],
        width=basic_filter_input_width,
        font=self.font_label,
        style='CompactFilter.TCombobox',
    )
    edu_combo.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
    # 禁用滚轮切换，防止误操作
    edu_combo.bind('<Enter>', lambda e: edu_combo.bind('<MouseWheel>', lambda ev: 'break'))
    edu_combo.bind('<Leave>', lambda e: edu_combo.unbind('<MouseWheel>'))
    # 使用与下一行完全相同的“年”标签作透明占位，避免主题内边距造成偏差。
    ttk.Label(
        row_education_experience,
        text="年",
        font=self.font_label,
        foreground=self.colors['bg_card'],
        background=self.colors['bg_card'],
    ).pack(side="left")
    ttk.Label(
        row_education_experience,
        text="最低经验:",
        font=self.font_label,
        width=ui_config['entry_width_label'],
        background=self.colors['bg_card'],
    ).pack(side="left", padx=(secondary_filter_gap, 0))
    self.min_exp_var = tk.StringVar(value="0")
    min_exp_spin = ttk.Spinbox(
        row_education_experience,
        from_=ui_config['spinbox_exp_min'],
        to=ui_config['spinbox_exp_max'],
        textvariable=self.min_exp_var,
        width=basic_filter_input_width,
        font=self.font_label,
        style='CompactFilter.TSpinbox',
    )
    min_exp_spin.pack(
        side="left", padx=int(15 * self.dpi_scale * self.zoom_factor)
    )
    min_exp_spin.bind(
        '<Enter>',
        lambda e: min_exp_spin.bind('<MouseWheel>', lambda ev: 'break'),
    )
    min_exp_spin.bind('<Leave>', lambda e: min_exp_spin.unbind('<MouseWheel>'))
    ttk.Label(
        row_education_experience,
        text="年",
        font=self.font_label,
        background=self.colors['bg_card'],
    ).pack(side="left")

    row_gender_age = ttk.Frame(basic_frame, style='TFrame')
    row_gender_age.pack(
        fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor)
    )
    ttk.Label(
        row_gender_age,
        text="性别要求:",
        font=self.font_label,
        width=ui_config['entry_width_job'],
        background=self.colors['bg_card'],
    ).pack(side="left")
    self.gender_var = tk.StringVar(value="不限")
    gender_combo = ttk.Combobox(
        row_gender_age,
        textvariable=self.gender_var,
        values=GENDER_VALUES,
        width=basic_filter_input_width,
        font=self.font_label,
        style='CompactFilter.TCombobox',
        state="readonly",
    )
    gender_combo.pack(
        side="left", padx=int(15 * self.dpi_scale * self.zoom_factor)
    )
    gender_combo.bind(
        '<Enter>',
        lambda e: gender_combo.bind('<MouseWheel>', lambda ev: 'break'),
    )
    gender_combo.bind('<Leave>', lambda e: gender_combo.unbind('<MouseWheel>'))
    # 与上一行的“年”保持同宽，让右侧数字门槛垂直对齐。
    ttk.Label(
        row_gender_age,
        text="年",
        font=self.font_label,
        foreground=self.colors['bg_card'],
        background=self.colors['bg_card'],
    ).pack(side="left")
    ttk.Label(
        row_gender_age,
        text="最大年龄:",
        font=self.font_label,
        width=ui_config['entry_width_label'],
        background=self.colors['bg_card'],
    ).pack(side="left", padx=(secondary_filter_gap, 0))
    self.max_age_var = tk.StringVar(value="")
    max_age_spin = ttk.Spinbox(
        row_gender_age,
        from_=0,
        to=99,
        textvariable=self.max_age_var,
        width=basic_filter_input_width,
        font=self.font_label,
        style='CompactFilter.TSpinbox',
    )
    max_age_spin.pack(
        side="left", padx=int(15 * self.dpi_scale * self.zoom_factor)
    )
    max_age_spin.bind(
        '<Enter>',
        lambda e: max_age_spin.bind('<MouseWheel>', lambda ev: 'break'),
    )
    max_age_spin.bind('<Leave>', lambda e: max_age_spin.unbind('<MouseWheel>'))
    ttk.Label(
        row_gender_age,
        text="岁",
        font=self.font_label,
        background=self.colors['bg_card'],
    ).pack(side="left")

    row_salary = ttk.Frame(basic_frame, style='TFrame')
    row_salary.pack(
        fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor)
    )
    salary_unit_gap = int(6 * self.dpi_scale * self.zoom_factor)
    salary_range_gap = int(10 * self.dpi_scale * self.zoom_factor)
    ttk.Label(
        row_salary,
        text="薪资范围:",
        font=self.font_label,
        width=ui_config['entry_width_job'],
        background=self.colors['bg_card'],
    ).pack(side="left")
    salary_min_entry = ttk.Entry(
        row_salary,
        textvariable=self.salary_min_var,
        width=8,
        font=self.font_label,
    )
    salary_min_entry.pack(
        side="left",
        padx=(int(15 * self.dpi_scale * self.zoom_factor), 0),
    )
    self.bind_entry_context_menu(salary_min_entry)
    self.salary_min_entry = salary_min_entry
    ttk.Label(
        row_salary,
        text="K",
        font=self.font_label,
        background=self.colors['bg_card'],
    ).pack(side="left", padx=(salary_unit_gap, 0))
    ttk.Label(
        row_salary,
        text="~",
        font=self.font_label,
        background=self.colors['bg_card'],
    ).pack(side="left", padx=(salary_range_gap, salary_range_gap))
    salary_max_entry = ttk.Entry(
        row_salary,
        textvariable=self.salary_max_var,
        width=8,
        font=self.font_label,
    )
    salary_max_entry.pack(side="left")
    self.bind_entry_context_menu(salary_max_entry)
    self.salary_max_entry = salary_max_entry
    ttk.Label(
        row_salary,
        text="K",
        font=self.font_label,
        background=self.colors['bg_card'],
    ).pack(side="left", padx=(salary_unit_gap, 0))
    ttk.Label(
        row_salary,
        text="留空表示不限制薪资",
        font=(font_family, int(10 * self.font_scale)),
        foreground=self.colors['text_secondary'],
        background=self.colors['bg_card'],
    ).pack(side="left", padx=(self.inline_note_gap, 0))

    # 工作地点
    row_location = ttk.Frame(basic_frame, style='TFrame')
    row_location.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))
    ttk.Label(row_location, text="工作地点:", font=self.font_label, width=ui_config['entry_width_job'],
             background=self.colors['bg_card']).pack(side="left")
    self.work_location_var = tk.StringVar()
    work_location_entry = ttk.Entry(row_location, textvariable=self.work_location_var, width=22, font=self.font_label)
    work_location_entry.pack(
        side="left", padx=(int(15 * self.dpi_scale * self.zoom_factor), 0)
    )
    self.bind_entry_context_menu(work_location_entry)
    ttk.Label(row_location, text="留空表示不限   多地点用 / 分隔，如：南京/上海",
              font=(font_family, int(10 * self.font_scale)),
              foreground=self.colors['text_secondary'], background=self.colors['bg_card']).pack(side="left", padx=(self.inline_note_gap, 0))

    yield

    # 技能关键词区域（带权重显示）- 左右分栏布局
    skills_frame = self._create_card(self.result_detail_frame, "技能评分条件",
        fill="both", side="top", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

    # 左右分栏容器
    skills_container = ttk.Frame(skills_frame, style='TFrame')
    skills_container.pack(fill="both", expand=True)

    # 左侧：技能列表（可伸缩）
    skills_left = ttk.Frame(skills_container, style='TFrame')
    skills_left.pack(side="left", fill="both", expand=True)

    # 右侧：操作面板（固定宽度，上下布局）
    skills_right = ttk.Frame(skills_container, style='Card.TFrame', width=int(280 * self.dpi_scale * self.zoom_factor))
    skills_right.pack(side="right", fill="y")
    # 不固定高度，让内容自动撑开

    # === 左侧：技能列表 ===
    list_container = ttk.Frame(skills_left, style='Card.TFrame')
    list_container.pack(fill="both", expand=True)

    # 使用 Treeview 显示技能列表
    columns = ("name", "weight", "source", "evidence")
    tree_font = self.font_table

    self.skills_tree = ttk.Treeview(
        list_container,
        columns=columns,
        show="headings",
        height=ui_config['treeview_height'],
        style='Skills.Treeview',
    )
    self.skills_tree.heading("name", text="技能名称")
    self.skills_tree.heading("weight", text="权重")
    self.skills_tree.heading("source", text="来源")
    self.skills_tree.heading("evidence", text="原文出处")
    # 设置列 - 全部居中
    self.skills_tree.column("name", width=190, minwidth=150, stretch=False, anchor='center')
    self.skills_tree.column("weight", width=80, minwidth=70, stretch=False, anchor='center')
    self.skills_tree.column("source", width=90, minwidth=75, stretch=False, anchor='center')
    self.skills_tree.column("evidence", width=320, minwidth=220, stretch=True, anchor='w')
    # 设置颜色标记（带字体）- 覆盖所有情况
    self.skills_tree.tag_configure('high_weight', font=tree_font, background=self.colors['bg_tree_tag_high'])
    self.skills_tree.tag_configure('mid_weight', font=tree_font, background=self.colors['bg_tree_tag_mid'])
    self.skills_tree.tag_configure('low_weight', font=tree_font, background=self.colors['bg_tree_tag_low'])

    # 设置 Treeview 默认字体和行高
    _style = ttk.Style()
    _style.configure(
        'Skills.Treeview',
        font=tree_font,
        rowheight=int(ui_config['treeview_rowheight'] * self.dpi_scale * self.zoom_factor),
    )
    _style.configure('Skills.Treeview.Heading', font=(*self.font_table, 'bold'))

    skills_scroll = ttk.Scrollbar(list_container, orient="vertical", command=self.skills_tree.yview)
    self.skills_tree.configure(yscrollcommand=skills_scroll.set)
    self.skills_tree.pack(side="left", fill="both", expand=True)
    skills_scroll.pack(side="right", fill="y")

    # 技能表"原文出处"列 tooltip
    self._skills_tooltip = None
    self._skills_tooltip_item = None

    def _on_skills_motion(event):
        """鼠标悬停在 evidence 列时显示完整原文"""
        item = self.skills_tree.identify_row(event.y)
        column = self.skills_tree.identify_column(event.x)
        if not item or column != "#4":  # evidence 是第4列
            self._hide_skills_tooltip()
            return
        values = self.skills_tree.item(item, 'values')
        if not values or len(values) < 4:
            self._hide_skills_tooltip()
            return
        # 从 skills_data 获取完整 evidence（Treeview 中可能被截断）
        idx = self.skills_tree.index(item)
        if idx < len(self.skills_data):
            full_text = self.skills_data[idx].get("evidence", "")
        else:
            full_text = str(values[3])
        if not full_text:
            self._hide_skills_tooltip()
            return
        tooltip_key = (item, column)
        if tooltip_key == self._skills_tooltip_item and self._skills_tooltip and self._skills_tooltip.winfo_exists():
            return
        self._hide_skills_tooltip()
        self._skills_tooltip_item = tooltip_key
        x = self.root.winfo_pointerx() + 15
        y = self.root.winfo_pointery() + 10
        self._skills_tooltip = self._create_simple_tooltip(full_text, x, y)

    def _on_skills_leave(event):
        self._hide_skills_tooltip()

    self.skills_tree.bind("<Motion>", _on_skills_motion)
    self.skills_tree.bind("<Leave>", _on_skills_leave)

    yield

    # 选中技能编辑区
    edit_card = self._create_card(skills_right, "编辑选中技能",
        padding=int(12 * self.dpi_scale * self.zoom_factor),
        fill="x", padx=int(10 * self.dpi_scale * self.zoom_factor), pady=(int(10 * self.dpi_scale * self.zoom_factor), int(15 * self.dpi_scale * self.zoom_factor)))

    # 选中技能名称
    ttk.Label(edit_card, text="当前选中:", font=self.font_label,
             background=self.colors['bg_card']).pack(anchor="w", pady=(0, int(5 * self.dpi_scale * self.zoom_factor)))
    self.selected_skill_var = tk.StringVar(value="未选择")
    self.selected_skill_label = ttk.Label(edit_card, textvariable=self.selected_skill_var,
                                          font=self.font_label,
                                          foreground=self.colors['primary'], background=self.colors['bg_card'],
                                          wraplength=int(240 * self.dpi_scale * self.zoom_factor), justify='left')
    self.selected_skill_label.pack(fill="x", pady=(0, int(10 * self.dpi_scale * self.zoom_factor)))

    # 权重输入框（标签和输入框同一行）
    weight_row = ttk.Frame(edit_card, style='TFrame')
    weight_row.pack(fill="x", pady=(0, int(10 * self.dpi_scale * self.zoom_factor)))
    ttk.Label(weight_row, text="权重 (1-3):", font=self.font_label,
             background=self.colors['bg_card'], width=ui_config['entry_width_label']).pack(side="left")
    self.new_skill_weight_var = tk.StringVar(value="1")
    self.skill_weight_spinbox = ttk.Spinbox(
        weight_row,
        from_=1,
        to=3,
        increment=1,
        textvariable=self.new_skill_weight_var,
        font=self.font_label,
        width=5,
        justify='left',
    )
    self.skill_weight_spinbox.pack(side="left")
    self.bind_entry_context_menu(self.skill_weight_spinbox)
    self.scroll_support.bind_bounded_spinbox_mousewheel(
        self.skill_weight_spinbox, self.new_skill_weight_var, 1, 3
    )

    # 操作按钮
    icon_pencil_skill = self.icons.button('pencil', self.colors['text_primary'])
    btn_update = ttk.Button(edit_card, image=icon_pencil_skill, text=" 更新权重", compound=tk.LEFT, command=self.update_skill_weight)
    btn_update._icon_ref = icon_pencil_skill
    btn_update.pack(fill="x", pady=(0, int(5 * self.dpi_scale * self.zoom_factor)))
    icon_trash_skill = self.icons.button('trash', self.colors['text_primary'])
    btn_del_skill = ttk.Button(edit_card, image=icon_trash_skill, text=" 删除技能", compound=tk.LEFT, command=self.delete_skill)
    btn_del_skill._icon_ref = icon_trash_skill
    btn_del_skill.pack(fill="x")

    # 添加新技能区
    add_card = self._create_card(skills_right, "添加新技能",
        padding=int(12 * self.dpi_scale * self.zoom_factor),
        fill="x", padx=int(10 * self.dpi_scale * self.zoom_factor), pady=int(10 * self.dpi_scale * self.zoom_factor))

    ttk.Label(add_card, text="技能名称:", font=self.font_label,
             background=self.colors['bg_card']).pack(anchor="w", pady=(0, int(5 * self.dpi_scale * self.zoom_factor)))
    self.new_skill_var = tk.StringVar()
    skill_entry = ttk.Entry(add_card, textvariable=self.new_skill_var, font=self.font_label)
    skill_entry.pack(fill="x", pady=(0, int(8 * self.dpi_scale * self.zoom_factor)))
    self.bind_entry_context_menu(skill_entry)

    # 权重输入框（标签和输入框同一行）
    weight_row = ttk.Frame(add_card, style='TFrame')
    weight_row.pack(fill="x", pady=(0, int(8 * self.dpi_scale * self.zoom_factor)))
    ttk.Label(weight_row, text="权重 (1-3):", font=self.font_label,
             background=self.colors['bg_card'], width=ui_config['entry_width_label']).pack(side="left")
    self.new_skill_add_weight_var = tk.StringVar(value="1")
    self.add_skill_weight_spinbox = ttk.Spinbox(
        weight_row,
        from_=1,
        to=3,
        increment=1,
        textvariable=self.new_skill_add_weight_var,
        font=self.font_label,
        width=5,
        justify='left',
    )
    self.add_skill_weight_spinbox.pack(side="left")
    self.bind_entry_context_menu(self.add_skill_weight_spinbox)
    self.scroll_support.bind_bounded_spinbox_mousewheel(
        self.add_skill_weight_spinbox, self.new_skill_add_weight_var, 1, 3
    )

    icon_plus_add = self.icons.button('plus', self.colors['text_primary'])
    btn_add_skill = ttk.Button(add_card, image=icon_plus_add, text=" 添加技能", compound=tk.LEFT, command=self.add_skill)
    btn_add_skill._icon_ref = icon_plus_add
    btn_add_skill.pack(fill="x", pady=(int(8 * self.dpi_scale * self.zoom_factor), 0))

    # 绑定选中事件
    self.skills_tree.bind("<<TreeviewSelect>>", self.on_skill_selected)

    yield

    # 必要条件区域
    required_frame = self._create_card(self.result_detail_frame, "必要条件",
        fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

    # 使用说明
    required_help = ttk.Label(required_frame,
        text="不满足以下任一条件的候选人将直接淘汰。\n"
             "简单匹配：输入关键词，简历中包含即可通过\n"
             "OR（满足任一）：多个关键词用逗号分隔，满足任意一个即通过\n"
             "AND（全部满足）：多个关键词用逗号分隔，必须全部满足才通过\n"
             "示例：统招本科  |  微服务,分布式（OR）  |  Spring Boot,MySQL（AND）",
        font=self.font_log, foreground=self.colors['text_secondary'],
        background=self.colors['bg_card'], justify='left')
    required_help.pack(anchor='w', pady=(0, int(6 * self.dpi_scale * self.zoom_factor)))

    # 必要条件列表显示
    self.required_listbox = tk.Listbox(required_frame, height=ui_config['listbox_height'],
                                      font=self.font_label,
                                      borderwidth=1, highlightthickness=0)
    self.required_listbox.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))

    # 必要条件 tooltip（显示原文出处）
    self._req_tooltip = None
    self._req_tooltip_idx = None

    def _on_req_motion(event):
        """鼠标悬停在必要条件上时显示原文出处"""
        idx = self.required_listbox.nearest(event.y)
        if idx < 0:
            self._hide_req_tooltip()
            return
        if idx == self._req_tooltip_idx and self._req_tooltip and self._req_tooltip.winfo_exists():
            return
        self._hide_req_tooltip()
        evidence = ""
        if idx < len(self.required_conditions_data):
            cond = self.required_conditions_data[idx]
            if isinstance(cond, dict):
                evidence = cond.get("_evidence", "")
            else:
                evidence = self._required_evidence_map.get(str(cond), "") if hasattr(self, '_required_evidence_map') else ""
        if not evidence:
            return
        self._req_tooltip_idx = idx
        x = self.root.winfo_pointerx() + 15
        y = self.root.winfo_pointery() + 10
        self._req_tooltip = self._create_simple_tooltip(evidence, x, y)

    def _on_req_leave(event):
        self._hide_req_tooltip()

    self.required_listbox.bind("<Motion>", _on_req_motion)
    self.required_listbox.bind("<Leave>", _on_req_leave)

    # 必要条件编辑 - 条件类型选择 + 关键词（逗号分隔）
    required_edit_frame = ttk.Frame(required_frame, style='TFrame')
    required_edit_frame.pack(fill="x")
    ttk.Label(required_edit_frame, text="类型:", font=self.font_label,
             background=self.colors['bg_card']).pack(side="left")
    self.required_cond_type_var = tk.StringVar(value="简单匹配")
    cond_type_combo = ttk.Combobox(required_edit_frame, textvariable=self.required_cond_type_var,
                                    values=["简单匹配", "OR（满足任一）", "AND（全部满足）"],
                                    width=12, state="readonly", font=self.font_label)
    cond_type_combo.pack(side="left", padx=int(3 * self.dpi_scale * self.zoom_factor))
    ttk.Label(required_edit_frame, text="关键词:", font=self.font_label,
             background=self.colors['bg_card']).pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), 0))
    self.new_required_var = tk.StringVar()
    required_edit = ttk.Entry(required_edit_frame, textvariable=self.new_required_var, font=self.font_label)
    required_edit.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor), fill="x", expand=True)
    self.bind_entry_context_menu(required_edit)
    ttk.Button(required_edit_frame, text="添加", command=self.add_required_condition).pack(side="left", padx=(int(8 * self.dpi_scale * self.zoom_factor), int(3 * self.dpi_scale * self.zoom_factor)))
    ttk.Button(required_edit_frame, text="删除选中", command=self.delete_required_condition).pack(side="left", padx=(int(3 * self.dpi_scale * self.zoom_factor), 0))

    yield

    # 按钮行（居中布局，固定在页面底部，不随 Canvas 滚动）
    self.btn_frame = ttk.Frame(self.config_page, style='Page.TFrame')
    quality_bg = self.colors.get('bg_footer', ui_theme.BG_FOOTER)
    quality_frame = tk.Frame(
        self.btn_frame,
        bg=quality_bg,
        highlightbackground=self.colors['border'],
        highlightthickness=1,
        cursor="arrow",
    )
    self.job_config_quality_frame = quality_frame
    quality_frame.pack(
        fill="x",
        padx=int(18 * self.dpi_scale * self.zoom_factor),
        pady=(int(6 * self.dpi_scale * self.zoom_factor), int(4 * self.dpi_scale * self.zoom_factor)),
    )
    self.job_config_quality_var = tk.StringVar(value="配置质量：待检查")
    self.job_config_quality_label = tk.Label(
        quality_frame,
        textvariable=self.job_config_quality_var,
        font=self.font_log,
        fg=self.colors['text_secondary'],
        bg=quality_bg,
        cursor="arrow",
    )
    self.job_config_quality_label.pack(
        side="left",
        padx=(int(12 * self.dpi_scale * self.zoom_factor), 0),
        pady=int(8 * self.dpi_scale * self.zoom_factor),
    )
    self.job_config_quality_link = tk.Label(
        quality_frame,
        text="查看详情",
        font=self.font_log,
        fg=self.colors['primary'],
        bg=quality_bg,
        cursor="hand2",
    )
    self.job_config_quality_link.pack(
        side="right",
        padx=int(12 * self.dpi_scale * self.zoom_factor),
    )
    self.btn_view_job_config_issues = self.job_config_quality_link
    self._job_config_quality_clickable = False
    self.job_config_quality_link.bind(
        '<Button-1>', self._open_job_config_quality_details
    )

    self._btn_inner = ttk.Frame(self.btn_frame, style='Page.TFrame')
    btn_inner = self._btn_inner
    btn_inner.pack(
        anchor="center",
        pady=(int(6 * self.dpi_scale * self.zoom_factor), 0),
    )

    self.save_hint_label = None

    icon_save_cfg = self.icons.button('save', self.colors['text_primary'])
    self.btn_save = ttk.Button(btn_inner, image=icon_save_cfg, text=" 保存配置", compound=tk.LEFT, command=self.save_current_job)
    self.btn_save._icon_ref = icon_save_cfg
    self.btn_save.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))
    icon_refresh_cfg = self.icons.button('refresh', self.colors['text_primary'])
    self.btn_restore_job = ttk.Button(
        btn_inner,
        image=icon_refresh_cfg,
        text=" 恢复已保存",
        compound=tk.LEFT,
        command=self._restore_or_clear_job_form,
    )
    self.btn_restore_job._icon_ref = icon_refresh_cfg
    self.btn_restore_job.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))

    # 存储技能数据的列表（带权重）；source="优先" 时保存到 preferred_keywords
    self.skills_data = []  # [{"name": "Java", "weight": 2, "source": "解析"}, ...]
    self.required_conditions_data = []  # ["统招本科", ...]

    # 设置下拉框的值
    self.config_job_combo['values'] = list(self.job_rules.keys())
    self._bind_job_form_change_tracking()

    # 如果有已存在的岗位，自动加载第一个并显示详细结果区域
    if self.job_rules:
        first_job = list(self.job_rules.keys())[0]
        self.config_job_combo.set(first_job)
        rule = self.job_rules[first_job]
        self.load_job_to_form(rule)
        self._set_requirement_section_expanded(False)
        # 注意：这里不 pack result_detail_frame，因为 config_page 还没有被显示
        # 将在 show_page_config 中 pack
    else:
        self._set_requirement_section_expanded(True)
        self._set_job_form_baseline("")

    # 底部按钮固定在页面底部，不随 Canvas 滚动
    self.btn_frame.pack(
        fill="x",
        side="bottom",
        pady=(
            int(10 * self.dpi_scale * self.zoom_factor),
            0,
        ),
    )

    # 在所有控件创建完毕后绑定滚轮事件
    self.scroll_support.bind_mousewheel(
        self.config_canvas,
        self.config_scrollable_frame,
    )
