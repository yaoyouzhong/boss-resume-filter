"""Incremental Tk construction for the run-control page."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterator, Mapping
from tkinter import font, ttk
from typing import Any

import ui_theme
from constants import (
    API_CANDIDATE_LIMIT_DEFAULT,
    GREET_CONTEXT_CAPTURE_LIMIT,
    MAX_ROUNDS_DEFAULT,
    SCORE_THRESHOLD_RECOMMEND,
    SCORE_THRESHOLD_STRONG,
)


def build_run_page_steps(
    host: Any,
    ui_config: Mapping[str, Any],
    *,
    font_family: str,
    scroll_warning_threshold: int,
    api_page_warning_threshold: int,
    contact_warning_threshold: int,
    timeout_hint: Callable[[Mapping[str, Any]], str],
) -> Iterator[None]:
    """Build the run-control page incrementally without starting a scan."""
    host.run_page = ttk.Frame(host.pages_frame, style='Page.TFrame')

    # 可滚动容器（macOS Tk 9.0+ 用 Text，其他用 Canvas）
    scroll_frame = ttk.Frame(host.run_page, style='Page.TFrame')
    scroll_frame.pack(fill="both", expand=True)

    host.run_canvas, scrollable_frame = host._create_scroll_container(
        scroll_frame, host.colors['bg_card'])

    host.run_scrollable_frame = scrollable_frame  # 保存引用，供 mousewheel 绑定使用

    # 所有内容放入 scrollable_frame
    content = scrollable_frame

    # 页面标题
    host._create_page_header(content, "运行控制")

    yield

    # 控制卡片
    control_container = ttk.Frame(content, style='Card.TFrame')
    control_container.pack(fill="x", pady=int(15 * host.dpi_scale * host.zoom_factor))

    # 浏览器操作与运行参数共用同一操作起始列。
    _run_control_gap = int(15 * host.dpi_scale * host.zoom_factor)
    _run_control_lead_width = (
        font.Font(font=host.font_label).measure("0") * 12 + _run_control_gap
    )

    def _create_run_control_lead(parent, text=None, label_font=None):
        lead = ttk.Frame(
            parent, style='TFrame', width=_run_control_lead_width
        )
        lead.pack(side="left", fill="y")
        lead.pack_propagate(False)
        if text is None:
            return lead
        label = ttk.Label(
            lead,
            text=text,
            font=label_font or host.font_label,
            background=host.colors['bg_card'],
        )
        label.pack(side="left")
        return label

    # === 浏览器连接状态检测 ===
    browser_frame = host._create_card(control_container, "浏览器状态",
        fill="x", padx=int(25 * host.dpi_scale * host.zoom_factor), pady=int(20 * host.dpi_scale * host.zoom_factor))

    browser_status_row = ttk.Frame(browser_frame, style='TFrame')
    browser_status_row.pack(fill="x")

    # 状态指示灯（交通灯图标 + 文本，由 _apply_lamp_status 统一渲染）
    browser_status_lead = _create_run_control_lead(browser_status_row)
    host.browser_status_indicator = ttk.Label(
        browser_status_lead,
        font=(font_family, int(11 * host.font_scale)),
        foreground=host.colors['danger'],
        background=host.colors['bg_card'],
    )
    host._apply_lamp_status(host.browser_status_indicator, "● 未连接", host.colors['danger'])
    host.browser_status_indicator.pack(side="left")

    # 检测按钮
    icon_browser = host.icons.button('search', host.colors['text_primary'])
    btn_browser = ttk.Button(browser_status_row, image=icon_browser, text=" 检测/连接浏览器", compound=tk.LEFT, command=host.check_browser_connection)
    btn_browser._icon_ref = icon_browser
    btn_browser.pack(side="left")

    # 状态说明
    host.browser_status_help = ttk.Label(browser_status_row, text="请点击按钮连接 BOSS 直聘页面",
                                         font=(font_family, int(11 * host.font_scale)),
                                         foreground=host.colors['text_secondary'])
    host.browser_status_help.pack(side="left", padx=(host.inline_note_gap, 0))

    yield

    # 运行参数
    param_frame = ttk.Frame(control_container, style='TFrame')
    _card_content_padding = int(
        ui_config['label_frame_padding'] * host.dpi_scale * host.zoom_factor
    )
    _param_horizontal_padding = (
        int(25 * host.dpi_scale * host.zoom_factor)
        + _card_content_padding
        + 1
    )
    param_frame.pack(
        fill="x",
        padx=_param_horizontal_padding,
        pady=int(20 * host.dpi_scale * host.zoom_factor),
    )

    # 选择岗位（多岗位运行时指定处理哪个岗位）
    row_job = ttk.Frame(param_frame, style='TFrame')
    row_job.pack(fill="x", pady=int(15 * host.dpi_scale * host.zoom_factor))
    _create_run_control_lead(row_job, "选择岗位:")
    host.job_select_var = tk.StringVar(value="")
    host.job_combo = ttk.Combobox(row_job, textvariable=host.job_select_var,
                                   values=["全部岗位"], width=28, state="readonly",
                                   font=host.font_label)
    host.job_combo.pack(side="left")
    host.job_combo.bind("<<ComboboxSelected>>", host.on_run_job_selected)
    host._sync_run_job_combo_values(host.job_rules, prefer_current=False)
    ttk.Label(row_job, text="建议每次选择一个岗位，\"全部岗位\"将依次处理",
             font=(font_family, int(11 * host.font_scale)),
             foreground=host.colors.get('text_muted', ui_theme.TEXT_MUTED),
             background=host.colors['bg_card']).pack(side="left", padx=(host.inline_note_gap, 0))

    yield

    # 筛选完成后的联系策略。GUI 发送统一进入联系清单。
    row2 = ttk.Frame(param_frame, style='TFrame')
    row2.pack(fill="x", pady=int(15 * host.dpi_scale * host.zoom_factor))
    _create_run_control_lead(row2, "筛选完成:")
    host.contact_after_scan_var = tk.StringVar(value="仅保存筛选结果")
    contact_combo = ttk.Combobox(
        row2,
        textvariable=host.contact_after_scan_var,
        values=[
            "仅保存筛选结果",
            "将强烈推荐加入联系清单",
            "将推荐及以上加入联系清单",
        ],
        width=28,
        state="readonly",
        font=host.font_label,
    )
    contact_combo.pack(side="left")
    host._contact_after_scan_note_label = ttk.Label(row2, text="",
             font=(font_family, int(11 * host.font_scale)),
             foreground=host.colors.get('text_muted', ui_theme.TEXT_MUTED), background=host.colors['bg_card'])
    host._contact_after_scan_note_label.pack(side="left", padx=(host.inline_note_gap, 0))

    def _update_contact_after_scan_note(*_):
        policy = host.contact_after_scan_var.get()
        if policy == "仅保存筛选结果":
            text = "扫描完成后进入筛选结果页处理"
        elif policy == "将强烈推荐加入联系清单":
            text = f"评分≥{SCORE_THRESHOLD_STRONG}分且已完成复核"
        else:
            text = f"评分≥{SCORE_THRESHOLD_RECOMMEND}分且已完成复核"
        host._contact_after_scan_note_label.config(text=text)

    _update_contact_after_scan_note()
    contact_combo.bind("<<ComboboxSelected>>", _update_contact_after_scan_note)

    yield

    # AI 辅助评估开关
    row_ai = ttk.Frame(param_frame, style='TFrame')
    host.ai_eval_row = row_ai
    row_ai.pack(fill="x", pady=int(15 * host.dpi_scale * host.zoom_factor))
    _create_run_control_lead(row_ai, "AI 评估:")
    # API Key 状态：先显示"检测中"，后台查 keyring 后更新（避免主线程阻塞）
    host.ai_eval_var = tk.BooleanVar(value=False)
    host.ai_eval_available_var = tk.BooleanVar(value=False)
    # 拨动开关 + 可点击文字（替代 clam 下 oversized 的勾选框）
    ai_switch = host._create_switch(
        row_ai, host.ai_eval_var,
        enabled_variable=host.ai_eval_available_var,
    )
    host.ai_eval_switch = ai_switch
    ai_switch.pack(side="left")
    ai_label = ttk.Label(
        row_ai, text="启用 AI 辅助评估", font=host.font_label,
        background=host.colors['bg_card'], cursor='arrow',
    )
    host.ai_eval_label = ai_label
    ai_label.pack(side="left")

    def _toggle_ai_eval_from_label(_event=None):
        if host.ai_eval_available_var.get():
            host.ai_eval_var.set(not host.ai_eval_var.get())
        return 'break'

    ai_label.bind('<Button-1>', _toggle_ai_eval_from_label)
    # API Key 状态标签（先显示检测中，后台查询完毕后由 _update_ai_eval_status 更新）
    _status_font = (font_family, int(11 * host.font_scale))
    host.ai_status_label = tk.Label(row_ai, text="检测中…", font=_status_font,
                                    foreground=host.colors['text_secondary'],
                                    background=host.colors['bg_card'])
    host.ai_status_label.pack(
        side="left", padx=(int(5 * host.dpi_scale * host.zoom_factor), 0)
    )
    yield

    host._schedule_run_page_api_key_check(host.ai_status_label)
    # 备注：+- 分色显示
    _note_prefix = "对通过筛选的候选人进行 LLM 二次评估，"
    _note_suffix = "15 分调整"
    _note_font = (font_family, int(11 * host.font_scale))
    _sign_font = (font_family, int(14 * host.font_scale))  # +/- 显式加大
    tk.Label(row_ai, text=_note_prefix, font=_note_font,
             foreground=host.colors.get('text_muted', ui_theme.TEXT_MUTED), background=host.colors['bg_card']).pack(side="left", padx=(host.inline_note_gap, 0))
    tk.Label(row_ai, text="+", font=_sign_font,
             foreground=host.colors['success'], background=host.colors['bg_card']).pack(side="left")
    tk.Label(row_ai, text="-", font=_sign_font,
             foreground=host.colors['danger'], background=host.colors['bg_card']).pack(side="left")
    tk.Label(row_ai, text=_note_suffix, font=_note_font,
             foreground=host.colors.get('text_muted', ui_theme.TEXT_MUTED), background=host.colors['bg_card']).pack(side="left")

    yield

    # 高级运行设置：位于 AI 评估行下方，默认折叠。
    row_advanced_header = ttk.Frame(param_frame, style='TFrame')
    row_advanced_header.pack(
        fill="x", pady=(int(2 * host.dpi_scale * host.zoom_factor), 0)
    )
    row_advanced_header.configure(cursor="hand2")
    host.scan_advanced_header = row_advanced_header
    host.scan_advanced_visible_var = tk.BooleanVar(value=False)
    host.scan_advanced_toggle_label = ttk.Label(
        row_advanced_header,
        text="高级运行设置 ▸",
        font=(font_family, int(11 * host.font_scale)),
        foreground=host.colors['text_secondary'],
        background=host.colors['bg_card'],
        cursor="hand2",
        takefocus=1,
    )
    host.scan_advanced_toggle_label.pack(side="left")
    host.scan_advanced_summary_label = ttk.Label(
        row_advanced_header,
        text="",
        font=(font_family, max(8, int(10 * host.font_scale))),
        foreground=host.colors.get('text_muted', ui_theme.TEXT_MUTED),
        background=host.colors['bg_card'],
        cursor="hand2",
    )
    host.scan_advanced_summary_label.pack(
        side="left", padx=(host.inline_note_gap, 0)
    )
    host.scan_advanced_warning_label = tk.Label(
        row_advanced_header,
        text="⚠ 部分设置会增加扫描耗时或页面访问量，请谨慎调高",
        font=(font_family, max(8, int(10 * host.font_scale))),
        foreground=host.colors.get('warning_text', ui_theme.WARNING_TEXT),
        background=host.colors.get(
            'banner_warning_bg', ui_theme.BANNER_WARNING_BG
        ),
        padx=max(6, int(8 * host.dpi_scale * host.zoom_factor)),
        pady=max(2, int(3 * host.dpi_scale * host.zoom_factor)),
        cursor="hand2",
    )

    host.scan_advanced_details_frame = ttk.Frame(param_frame, style='TFrame')
    advanced_inner = ttk.Frame(host.scan_advanced_details_frame, style='TFrame')
    advanced_inner.pack(fill="x")

    default_api_pages = max(1, (API_CANDIDATE_LIMIT_DEFAULT + 19) // 20)
    host.rounds_var = tk.StringVar(value=str(MAX_ROUNDS_DEFAULT))
    host.api_direct_enabled_var = tk.BooleanVar(value=True)
    host.api_direct_pages_var = tk.StringVar(value=str(default_api_pages))
    host.greet_context_capture_enabled_var = tk.BooleanVar(value=True)
    host.greet_context_capture_limit_var = tk.StringVar(value=str(GREET_CONTEXT_CAPTURE_LIMIT))
    _is_relay = host._is_relay_endpoint_for_timeout()
    _default_read = 120 if _is_relay else 60
    _init_read = host.api_config.get("llm_read_timeout") or _default_read
    host.llm_read_timeout_var = tk.IntVar(value=_init_read)

    _sub_font = (font_family, int(11 * host.font_scale))
    _spin_font = (font_family, int(12 * host.font_scale))
    _spin_pad = int(5 * host.dpi_scale * host.zoom_factor)
    _advanced_row_pady = int(7 * host.dpi_scale * host.zoom_factor)
    advanced_inner.columnconfigure(0, minsize=_run_control_lead_width)

    def _create_advanced_setting_label(row_index, label_text):
        setting_label = ttk.Label(
            advanced_inner,
            text=label_text,
            font=_sub_font,
            foreground=host.colors['text_secondary'],
            background=host.colors['bg_card'],
        )
        setting_label.grid(
            row=row_index,
            column=0,
            sticky="w",
            pady=(_advanced_row_pady, 0),
        )
        return setting_label

    # 1. 扫描范围
    _create_advanced_setting_label(0, "滚动轮次:")
    row_rounds_controls = ttk.Frame(advanced_inner, style='TFrame')
    row_rounds_controls.grid(
        row=0,
        column=1,
        columnspan=5,
        sticky="w",
        pady=(_advanced_row_pady, 0),
    )
    host.rounds_spin = ttk.Spinbox(
        row_rounds_controls,
        from_=ui_config['spinbox_rounds_min'],
        to=ui_config['spinbox_rounds_max'],
        increment=10,
        textvariable=host.rounds_var,
        width=8,
        font=_spin_font,
    )
    host.rounds_spin.pack(side="left")
    host.rounds_spin.bind(
        '<Enter>',
        lambda e: host.rounds_spin.bind('<MouseWheel>', host._on_rounds_mousewheel),
    )
    host.rounds_spin.bind(
        '<Leave>', lambda e: host.rounds_spin.unbind('<MouseWheel>')
    )
    ttk.Label(
        row_rounds_controls,
        text="轮",
        font=_sub_font,
        foreground=host.colors['text_secondary'],
        background=host.colors['bg_card'],
    ).pack(side="left", padx=(_spin_pad, 0))
    host.rounds_hint_label = ttk.Label(
        row_rounds_controls,
        text="默认 50，推荐 20-100",
        font=_sub_font,
        foreground=host.colors.get('text_muted', ui_theme.TEXT_MUTED),
        background=host.colors['bg_card'],
    )
    host.rounds_hint_label.pack(
        side="left", padx=(host.inline_note_gap, 0)
    )

    # 2. AI 评估响应等待时间
    host.ai_timeout_setting_label = _create_advanced_setting_label(
        1, "AI 响应超时:"
    )
    row_ai_timeout_controls = ttk.Frame(advanced_inner, style='TFrame')
    row_ai_timeout_controls.grid(
        row=1,
        column=1,
        columnspan=5,
        sticky="w",
        pady=(_advanced_row_pady, 0),
    )
    host.llm_read_timeout_spin = ttk.Spinbox(
        row_ai_timeout_controls,
        from_=10,
        to=300,
        increment=10,
        width=8,
        textvariable=host.llm_read_timeout_var,
        font=_spin_font,
    )
    host.llm_read_timeout_spin.pack(side="left")
    ttk.Label(
        row_ai_timeout_controls,
        text="秒",
        font=_sub_font,
        background=host.colors['bg_card'],
        foreground=host.colors['text_secondary'],
    ).pack(side="left", padx=(_spin_pad, 0))
    _hint = timeout_hint(host.api_config)
    host._timeout_hint_label = ttk.Label(
        row_ai_timeout_controls,
        text=_hint,
        font=_sub_font,
        background=host.colors['bg_card'],
        foreground=host.colors.get('text_muted', ui_theme.TEXT_MUTED),
    )
    host._timeout_hint_label.pack(
        side="left", padx=(host.inline_note_gap, 0)
    )

    # 3. 扫描信息补全
    _create_advanced_setting_label(2, "扫描增强:")
    row_api_controls = ttk.Frame(advanced_inner, style='TFrame')
    row_api_controls.grid(
        row=2,
        column=1,
        sticky="w",
        pady=(_advanced_row_pady, 0),
    )
    api_switch = host._create_switch(
        row_api_controls, host.api_direct_enabled_var
    )
    api_switch.pack(side="left")
    api_label = ttk.Label(
        row_api_controls,
        text="自动补全候选人详情",
        font=_sub_font,
        background=host.colors['bg_card'],
        cursor='arrow',
    )
    api_label.pack(side="left", padx=(_spin_pad, 0))
    ttk.Label(
        row_api_controls,
        text="最多读取:",
        font=_sub_font,
        foreground=host.colors['text_secondary'],
        background=host.colors['bg_card'],
    ).pack(side="left", padx=(host.inline_note_gap, 0))
    host.api_direct_pages_spin = ttk.Spinbox(
        row_api_controls,
        from_=1,
        to=20,
        increment=1,
        width=4,
        textvariable=host.api_direct_pages_var,
        font=_spin_font,
    )
    host.api_direct_pages_spin.pack(side="left", padx=(_spin_pad, 0))
    ttk.Label(
        row_api_controls,
        text="页",
        font=_sub_font,
        foreground=host.colors['text_secondary'],
        background=host.colors['bg_card'],
    ).pack(side="left", padx=(_spin_pad, 0))
    host.api_direct_risk_label = ttk.Label(
        row_api_controls,
        text="",
        font=_sub_font,
        foreground=host.colors.get('warning_text', ui_theme.WARNING_TEXT),
        background=host.colors['bg_card'],
    )
    host.api_direct_risk_label.pack(
        side="left", padx=(host.inline_note_gap, 0)
    )

    # 4. 联系信息准备
    _create_advanced_setting_label(3, "后续联系:")
    row_contact_controls = ttk.Frame(advanced_inner, style='TFrame')
    row_contact_controls.grid(
        row=3,
        column=1,
        sticky="w",
        pady=(_advanced_row_pady, 0),
    )
    contact_prepare_switch = host._create_switch(
        row_contact_controls, host.greet_context_capture_enabled_var
    )
    contact_prepare_switch.pack(side="left")
    contact_prepare_label = ttk.Label(
        row_contact_controls,
        text="扫描后准备联系信息",
        font=_sub_font,
        background=host.colors['bg_card'],
        cursor='arrow',
    )
    contact_prepare_label.pack(side="left", padx=(_spin_pad, 0))
    ttk.Label(
        row_contact_controls,
        text="最多准备:",
        font=_sub_font,
        foreground=host.colors['text_secondary'],
        background=host.colors['bg_card'],
    ).pack(side="left", padx=(host.inline_note_gap, 0))
    host.greet_context_capture_limit_spin = ttk.Spinbox(
        row_contact_controls,
        from_=1,
        to=100,
        increment=1,
        width=4,
        textvariable=host.greet_context_capture_limit_var,
        font=_spin_font,
    )
    host.greet_context_capture_limit_spin.pack(
        side="left", padx=(_spin_pad, 0)
    )
    ttk.Label(
        row_contact_controls,
        text="人",
        font=_sub_font,
        foreground=host.colors['text_secondary'],
        background=host.colors['bg_card'],
    ).pack(side="left", padx=(_spin_pad, 0))
    host.greet_context_risk_label = ttk.Label(
        row_contact_controls,
        text="",
        font=_sub_font,
        foreground=host.colors.get('warning_text', ui_theme.WARNING_TEXT),
        background=host.colors['bg_card'],
    )
    host.greet_context_risk_label.pack(
        side="left", padx=(host.inline_note_gap, 0)
    )

    _muted_text = host.colors.get('text_muted', ui_theme.TEXT_MUTED)
    _warning_text = host.colors.get('warning_text', ui_theme.WARNING_TEXT)

    def _advanced_setting_value(variable, default, minimum, maximum):
        return host._coerce_int_setting(
            variable.get(), default, minimum, maximum
        )

    def _sync_advanced_scan_controls(*_):
        try:
            if not host.scan_advanced_details_frame.winfo_exists():
                return
        except tk.TclError:
            return

        api_enabled = bool(host.api_direct_enabled_var.get())
        ai_enabled = bool(host.ai_eval_var.get())
        contact_enabled = bool(host.greet_context_capture_enabled_var.get())
        rounds = _advanced_setting_value(
            host.rounds_var,
            MAX_ROUNDS_DEFAULT,
            ui_config['spinbox_rounds_min'],
            ui_config['spinbox_rounds_max'],
        )
        api_pages = _advanced_setting_value(
            host.api_direct_pages_var,
            api_page_warning_threshold,
            1,
            20,
        )
        contact_limit = _advanced_setting_value(
            host.greet_context_capture_limit_var,
            contact_warning_threshold,
            1,
            100,
        )
        read_timeout = _advanced_setting_value(
            host.llm_read_timeout_var,
            _default_read,
            10,
            300,
        )

        host.api_direct_pages_spin.configure(
            state="normal" if api_enabled else "disabled"
        )
        host.llm_read_timeout_spin.configure(
            state="normal" if ai_enabled else "disabled"
        )
        host.greet_context_capture_limit_spin.configure(
            state="normal" if contact_enabled else "disabled"
        )
        host.ai_timeout_setting_label.configure(
            foreground=(host.colors['text_secondary'] if ai_enabled else _muted_text)
        )
        host._timeout_hint_label.configure(
            text=(
                timeout_hint(host.api_config)
                if ai_enabled
                else "开启 AI 辅助评估后可设置"
            )
        )

        rounds_high = rounds > scroll_warning_threshold
        host.rounds_hint_label.configure(
            text=(
                "访问量和耗时会明显增加"
                if rounds_high
                else f"默认 {MAX_ROUNDS_DEFAULT}，推荐 20-{scroll_warning_threshold}"
            ),
            foreground=_warning_text if rounds_high else _muted_text,
        )
        host.api_direct_risk_label.configure(
            text=(
                "继续调高会增加触发风控的风险"
                if api_enabled and api_pages > api_page_warning_threshold
                else ""
            )
        )
        host.greet_context_risk_label.configure(
            text=(
                "继续调高会增加触发风控的风险"
                if contact_enabled
                and contact_limit > contact_warning_threshold
                else ""
            )
        )

        ai_summary = f"AI {read_timeout} 秒" if ai_enabled else "AI 关闭"
        api_summary = f"增强 {api_pages} 页" if api_enabled else "增强关闭"
        contact_summary = (
            f"联系 {contact_limit} 人" if contact_enabled else "联系关闭"
        )
        host.scan_advanced_summary_label.configure(
            text=(
                f"{rounds} 轮 · {ai_summary} · "
                f"{api_summary} · {contact_summary}"
            )
        )

    def _toggle_api_direct_from_label(_event=None):
        host.api_direct_enabled_var.set(not host.api_direct_enabled_var.get())
        return 'break'

    def _toggle_contact_prepare_from_label(_event=None):
        host.greet_context_capture_enabled_var.set(
            not host.greet_context_capture_enabled_var.get()
        )
        return 'break'

    def _restore_advanced_run_defaults(_event=None):
        host.rounds_var.set(str(MAX_ROUNDS_DEFAULT))
        host.llm_read_timeout_var.set(120 if host._is_relay_endpoint_for_timeout() else 60)
        host.api_direct_enabled_var.set(True)
        host.api_direct_pages_var.set(str(api_page_warning_threshold))
        host.greet_context_capture_enabled_var.set(True)
        host.greet_context_capture_limit_var.set(
            str(contact_warning_threshold)
        )
        return 'break'

    host.scan_advanced_reset_label = ttk.Label(
        row_advanced_header,
        text="恢复默认",
        font=(
            font_family,
            max(8, int(10 * host.font_scale)),
            "underline",
        ),
        foreground=host.colors['primary'],
        background=host.colors['bg_card'],
        cursor="hand2",
        takefocus=1,
    )
    host.scan_advanced_reset_label.bind(
        '<Button-1>', _restore_advanced_run_defaults
    )
    host.scan_advanced_reset_label.bind(
        '<Return>', _restore_advanced_run_defaults
    )
    host.scan_advanced_reset_label.bind(
        '<space>', _restore_advanced_run_defaults
    )

    def _toggle_advanced_scan_settings(_event=None):
        visible = not host.scan_advanced_visible_var.get()
        host.scan_advanced_visible_var.set(visible)
        host.scan_advanced_toggle_label.config(
            text="高级运行设置 ▾" if visible else "高级运行设置 ▸"
        )
        if visible:
            host.scan_advanced_summary_label.pack_forget()
            host.scan_advanced_warning_label.pack(
                side="left", padx=(host.inline_note_gap, 0)
            )
            host.scan_advanced_reset_label.pack(
                side="left", padx=(host.inline_note_gap, 0)
            )
            pack_kwargs = {
                "fill": "x",
                "pady": (0, int(8 * host.dpi_scale * host.zoom_factor)),
            }
            before_widget = getattr(host, 'run_progress_frame', None)
            if before_widget is not None:
                host.scan_advanced_details_frame.pack(
                    before=before_widget, **pack_kwargs
                )
            else:
                host.scan_advanced_details_frame.pack(**pack_kwargs)
        else:
            host.scan_advanced_details_frame.pack_forget()
            host.scan_advanced_warning_label.pack_forget()
            host.scan_advanced_reset_label.pack_forget()
            host.scan_advanced_summary_label.pack(
                side="left", padx=(host.inline_note_gap, 0)
            )
        return 'break'

    for variable in (
        host.rounds_var,
        host.llm_read_timeout_var,
        host.api_direct_pages_var,
        host.greet_context_capture_limit_var,
    ):
        variable.trace_add('write', _sync_advanced_scan_controls)
    host.api_direct_enabled_var.trace_add('write', _sync_advanced_scan_controls)
    host.ai_eval_var.trace_add('write', _sync_advanced_scan_controls)
    host.greet_context_capture_enabled_var.trace_add(
        'write', _sync_advanced_scan_controls
    )
    api_label.bind('<Button-1>', _toggle_api_direct_from_label)
    contact_prepare_label.bind('<Button-1>', _toggle_contact_prepare_from_label)
    for widget in (
        row_advanced_header,
        host.scan_advanced_toggle_label,
        host.scan_advanced_summary_label,
        host.scan_advanced_warning_label,
    ):
        widget.bind('<Button-1>', _toggle_advanced_scan_settings)
    host.scan_advanced_toggle_label.bind(
        '<Return>', _toggle_advanced_scan_settings
    )
    host.scan_advanced_toggle_label.bind(
        '<space>', _toggle_advanced_scan_settings
    )
    host.scan_advanced_toggle_label.bind(
        '<FocusIn>',
        lambda _event: host.scan_advanced_toggle_label.configure(
            foreground=host.colors['primary']
        ),
    )
    host.scan_advanced_toggle_label.bind(
        '<FocusOut>',
        lambda _event: host.scan_advanced_toggle_label.configure(
            foreground=host.colors['text_secondary']
        ),
    )
    _sync_advanced_scan_controls()

    yield

    # === 进度条 ===
    progress_frame = ttk.Frame(param_frame, style='TFrame')
    host.run_progress_frame = progress_frame
    progress_frame.pack(fill="x", pady=int(15 * host.dpi_scale * host.zoom_factor))

    # 第一行：标签 + 进度条
    progress_row = ttk.Frame(progress_frame, style='TFrame')
    progress_row.pack(fill="x")

    ttk.Label(progress_row, text="筛选进度:", font=host.font_label,
             background=host.colors['bg_card']).pack(side="left")

    # 自定义 Progressbar 样式：高度与文字对齐
    _progress_height = int(20 * host.dpi_scale * host.zoom_factor)
    _progress_style = ttk.Style()
    _progress_style.configure('Run.Horizontal.TProgressbar',
                              thickness=_progress_height,
                              troughcolor=host.colors['bg_input'],
                              background=host.colors['primary'])

    host.progress_var = tk.DoubleVar(value=0)
    host.progress_bar = ttk.Progressbar(progress_row, variable=host.progress_var,
                                        maximum=100, mode='determinate', length=400,
                                        style='Run.Horizontal.TProgressbar')
    host.progress_bar.pack(side="left", padx=int(15 * host.dpi_scale * host.zoom_factor), fill="x", expand=True)

    # 第二行：进度描述文字（全宽，不截断）
    host.progress_label = ttk.Label(progress_frame, text="",
                                   font=host.font_label,
                                   foreground=host.colors['primary'],
                                   anchor="w", justify="left",
                                   background=host.colors['bg_card'])
    host.progress_label.pack(fill="x", pady=(int(4 * host.dpi_scale * host.zoom_factor), 0))

    yield

    # 本轮结果摘要：终态时固定展示，便于复盘筛选漏斗。
    summary_outer = tk.Frame(
        param_frame,
        bg=host.colors['bg_input'],
        highlightbackground=host.colors['border'],
        highlightthickness=1,
    )
    summary_outer.pack(
        fill="x",
        pady=(int(10 * host.dpi_scale * host.zoom_factor), 0),
    )
    host.run_summary_frame = summary_outer
    summary_pad = int(12 * host.dpi_scale * host.zoom_factor)
    summary_header = tk.Frame(summary_outer, bg=host.colors['bg_input'])
    summary_header.pack(fill="x", padx=summary_pad, pady=(summary_pad, int(4 * host.dpi_scale * host.zoom_factor)))
    tk.Label(
        summary_header,
        text="本轮结果摘要",
        font=(font_family, int(11 * host.font_scale), "bold"),
        foreground=host.colors['text_primary'],
        background=host.colors['bg_input'],
    ).pack(side="left")
    host.run_summary_status_label = tk.Label(
        summary_header,
        text="等待运行",
        font=(font_family, int(11 * host.font_scale)),
        foreground=host.colors['text_secondary'],
        background=host.colors['bg_input'],
    )
    host.run_summary_status_label.pack(side="right")
    summary_body = tk.Frame(summary_outer, bg=host.colors['bg_input'])
    summary_body.pack(
        fill="x",
        padx=summary_pad,
        pady=(0, summary_pad),
    )
    host.run_summary_text_label = tk.Text(
        summary_body,
        height=3,
        wrap="word",
        font=(font_family, int(11 * host.font_scale)),
        fg=host.colors['text_secondary'],
        bg=host.colors['bg_input'],
        borderwidth=0,
        highlightthickness=0,
        relief="flat",
        cursor="arrow",
        takefocus=False,
    )
    host.run_summary_text_label.pack(
        side="left",
        fill="x",
        expand=True,
    )
    host.run_summary_scrollbar = ttk.Scrollbar(
        summary_body,
        orient="vertical",
        command=host.run_summary_text_label.yview,
    )
    host.run_summary_text_label.configure(
        yscrollcommand=host.run_summary_scrollbar.set,
    )
    host._update_run_summary_text(
        "运行完成后显示通过率、主要淘汰原因、AI 淘汰和打招呼结果。",
        host.colors['text_secondary'],
    )

    yield

    # 控制按钮区
    btn_container = ttk.Frame(control_container, style='TFrame')
    btn_container.pack(fill="x", padx=int(25 * host.dpi_scale * host.zoom_factor), pady=int(20 * host.dpi_scale * host.zoom_factor))

    # 开始/停止按钮
    icon_play_run = host.icons.button('play', '#FFFFFF')
    icon_play_run_disabled = host.icons.button('play', host.colors['text_muted'])
    host.start_btn = ttk.Button(
        btn_container,
        image=(icon_play_run, 'disabled', icon_play_run_disabled),
        text=" 开始运行",
        compound=tk.LEFT,
        command=host.start_run,
        style='Accent.TButton',
        state="disabled",
    )
    host.start_btn._icon_refs = (icon_play_run, icon_play_run_disabled)
    host.start_btn.pack(side="left", padx=int(15 * host.dpi_scale * host.zoom_factor))

    icon_stop = host.icons.button('stop', '#FFFFFF')
    icon_stop_disabled = host.icons.button('stop', host.colors['text_muted'])
    host.stop_btn = ttk.Button(
        btn_container,
        image=(icon_stop, 'disabled', icon_stop_disabled),
        text=" 停止",
        compound=tk.LEFT,
        command=host.stop_run,
        style='RunControl.Danger.TButton',
        state="disabled",
    )
    host.stop_btn._icon_refs = (icon_stop, icon_stop_disabled)
    host.stop_btn.pack(side="left", padx=int(15 * host.dpi_scale * host.zoom_factor))

    # 状态指示器（交通灯图标 + 文本，由 _apply_lamp_status 统一渲染）
    host.status_label = ttk.Label(btn_container,
                                  font=(font_family, int(13 * host.font_scale)), foreground=host.colors['success'])
    host._apply_lamp_status(host.status_label, "● 就绪", host.colors['success'])
    host.status_label.pack(side="left", padx=int(50 * host.dpi_scale * host.zoom_factor))

    yield

    # 日志区域 — 与浏览器状态卡片一致的卡片式设计
    log_card = host._create_card(content, "运行日志",
        fill="both", expand=True, padx=int(25 * host.dpi_scale * host.zoom_factor), pady=int(15 * host.dpi_scale * host.zoom_factor))

    log_container = ttk.Frame(log_card, style='TFrame')
    log_container.pack(fill="both", expand=True)

    # 日志文本框 - 等宽字体
    host.log_text = tk.Text(log_container, wrap="word", state="disabled",
                           font=host.font_log, bg=host.colors['bg_input'], borderwidth=0,
                           highlightthickness=0, height=20)
    host.log_text.pack(side="left", fill="both", expand=True)
    host.bind_text_context_menu(host.log_text, editable=False)

    log_scroll = ttk.Scrollbar(log_container, orient="vertical", command=host.log_text.yview)
    log_scroll.pack(side="right", fill="y")
    host.log_text.config(yscrollcommand=log_scroll.set)

    host.log_text.bind('<Enter>', lambda e: setattr(host, '_over_text_widget', True))
    host.log_text.bind('<Leave>', lambda e: setattr(host, '_over_text_widget', False))

    # 日志工具栏 — 放在卡片内容区底部
    log_toolbar = ttk.Frame(log_card, style='TFrame')
    log_toolbar.pack(fill="x", pady=(int(8 * host.dpi_scale * host.zoom_factor), 0))

    icon_trash_log = host.icons.button('trash', host.colors['text_primary'])
    btn_clear_log = ttk.Button(log_toolbar, image=icon_trash_log, text=" 清空日志", compound=tk.LEFT, command=host.clear_log)
    btn_clear_log._icon_ref = icon_trash_log
    btn_clear_log.pack()

    # 启动进度条更新循环
    host.update_progress()

    # 在所有控件创建完毕后绑定滚轮事件
    host._bind_mousewheel(host.run_canvas, host.run_scrollable_frame)
