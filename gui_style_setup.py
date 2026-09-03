"""Global Tk and ttk style registration for the desktop GUI."""
from __future__ import annotations

import tkinter as tk
from tkinter import font, ttk

import ui_theme


def setup_styles(host):
    """设置自定义样式"""
    style = ttk.Style()

    # 统一使用 clam：唯一允许完整定制背景/边框/hover 的主题，
    # vista 下按钮等控件无法着色，导致主操作与普通按钮无视觉层级
    try:
        style.theme_use('clam')
    except tk.TclError:
        pass  # 使用默认主题

    # 配色方案 - 统一来自 ui_theme 设计令牌
    host.colors = ui_theme.build_palette()

    # 设置右侧功能页字体。左侧边栏在 create_sidebar() 中单独计算，避免被这里牵动。
    fs = host.dpi_scale * host.zoom_factor
    page_fs = fs * 0.92 * host.font_boost
    host.font_title = (ui_theme.FONT_FAMILY, int(28 * page_fs))
    host.font_section = (ui_theme.FONT_FAMILY, int(16 * page_fs))
    host.font_label = (ui_theme.FONT_FAMILY, int(13 * page_fs))  # 通用 UI 字体（表单标签、按钮、下拉框、副标题）
    host.font_stat = (ui_theme.FONT_FAMILY, int(36 * page_fs))
    host.font_stat_label = (ui_theme.FONT_FAMILY, int(15 * page_fs))
    host.font_log = (ui_theme.FONT_FAMILY, int(12 * page_fs))
    host.font_table = (ui_theme.FONT_FAMILY, int(12 * page_fs))  # 表格字体
    home_bold = ui_theme.FONT_FAMILY
    number_family = ui_theme.FONT_FAMILY
    host.home_fonts = {
        "title": (home_bold, max(18, int(26 * page_fs)), "bold"),
        "hero": (home_bold, max(17, int(24 * page_fs)), "bold"),
        "hero_number": (number_family, max(23, int(30 * page_fs)), "bold"),
        "eyebrow": (home_bold, max(9, int(11 * page_fs)), "bold"),
        "card_heading": (home_bold, max(11, int(15 * page_fs)), "bold"),
        "summary_heading": (home_bold, max(9, int(12 * page_fs)), "bold"),
        "task_title": (home_bold, max(12, int(14 * page_fs)), "bold"),
        "task_number": (number_family, max(21, int(27 * page_fs)), "bold"),
        "body": (ui_theme.FONT_FAMILY, max(10, int(12 * page_fs))),
        "meta": (ui_theme.FONT_FAMILY, max(10, int(11 * page_fs))),
        "action": (home_bold, max(10, int(12 * page_fs)), "bold"),
        "micro": (ui_theme.FONT_FAMILY, max(8, int(10 * page_fs))),
        "data_small": (number_family, max(8, int(10 * page_fs))),
    }

    # 设置 Combobox 下拉列表字体（与 font_label 保持一致）
    # 必须用元组格式 + priority 80，确保 Tk option database 正确解析并覆盖默认值
    host.root.option_add('*TCombobox*Listbox.font', host.font_label, 80)

    # 禁用所有 Combobox 的鼠标滚轮（防止误触改变选中值）
    host.root.bind_class('TCombobox', '<MouseWheel>', lambda e: 'break')
    host.root.bind_class('TCombobox', '<Button-4>', lambda e: 'break')
    host.root.bind_class('TCombobox', '<Button-5>', lambda e: 'break')

    # 配置样式
    c = host.colors
    style.configure('TFrame', background=c['bg_card'])
    style.configure('Page.TFrame', background=c['bg_main'])
    style.configure('Home.Page.TFrame', background=c['home_bg'])
    style.configure('EducationTool.TFrame', background=c['home_bg'])
    style.configure(
        'EducationTool.Workbench.TFrame',
        background=c['home_surface_quiet'],
    )
    style.configure(
        'EducationTool.Workbench.TLabel',
        background=c['home_surface_quiet'],
        foreground=c['text_primary'],
    )
    style.configure('TLabel', font=host.font_label, foreground=c['text_primary'],
                    background=c['bg_card'])

    # ---------------- 三级按钮体系（clam 下可完整着色） ----------------
    # 次级（默认）：白底灰边，hover 浅灰
    style.configure('TButton', font=host.font_label, padding=(15, 8),
                    background=c['bg_card'], foreground=c['text_primary'],
                    bordercolor=c.get('border_strong', ui_theme.BORDER_STRONG),
                    focuscolor=c['primary'], lightcolor=c['bg_card'], darkcolor=c['bg_card'])
    style.layout(
        'TButton',
        [
            ('Button.border', {
                'sticky': 'nswe',
                'border': '1',
                'children': [
                    ('Button.padding', {
                        'sticky': 'nswe',
                        'children': [('Button.label', {'sticky': 'nswe'})],
                    }),
                ],
            }),
        ],
    )
    style.map('TButton',
              background=[('pressed', c['bg_hover']), ('active', c['bg_hover']),
                          ('disabled', c['bg_input'])],
              foreground=[('disabled', c.get('text_muted', ui_theme.TEXT_MUTED))],
              bordercolor=[('focus', c['primary'])])
    # Menubutton 的原生下拉指示区默认会从文字区域扣除宽度，导致文字
    # 相对整个按钮视觉偏左。让箭头覆盖在右侧对称内边距中，文字继续
    # 使用完整按钮宽度居中，同时保留原生下拉提示和交互。
    style.layout(
        'CenteredActions.TMenubutton',
        [
            ('Menubutton.button', {
                'sticky': 'nswe',
                'children': [
                    ('Menubutton.padding', {
                        'sticky': 'nswe',
                        'children': [('Menubutton.label', {'sticky': ''})],
                    }),
                    ('Menubutton.dropdown', {'sticky': 'e'}),
                ],
            }),
        ],
    )
    style.configure(
        'CenteredActions.TMenubutton',
        font=host.font_label,
        padding=(24, 8),
        anchor='center',
        justify='center',
    )
    # 主级（Accent）：实心品牌蓝白字，hover 深蓝，pressed 更深
    style.configure('Accent.TButton', font=(ui_theme.FONT_FAMILY_SEMIBOLD, int(13 * page_fs)), padding=(20, 8),
                    background=c['primary'], foreground='#FFFFFF',
                    bordercolor=c['primary_dark'], focuscolor=c['primary_dark'],
                    lightcolor=c['primary'], darkcolor=c['primary'])
    style.map('Accent.TButton',
              background=[('pressed', c.get('primary_deep', ui_theme.PRIMARY_DEEP)),
                          ('active', c['primary_dark']),
                          ('disabled', c['bg_input'])],
              foreground=[('disabled', c.get('text_muted', ui_theme.TEXT_MUTED))],
              bordercolor=[('disabled', c['border'])])
    home_button_pad = (18, 9)
    style.configure(
        'Home.Primary.TButton',
        font=host.home_fonts["action"],
        padding=home_button_pad,
        background=c['home_primary'],
        foreground='#FFFFFF',
        bordercolor=c['home_primary'],
        focuscolor=c['home_primary_pressed'],
        lightcolor=c['home_primary'],
        darkcolor=c['home_primary'],
    )
    style.map(
        'Home.Primary.TButton',
        background=[
            ('pressed', c['home_primary_pressed']),
            ('active', c['home_primary_hover']),
            ('disabled', c['bg_input']),
        ],
        foreground=[('disabled', c['text_muted'])],
        bordercolor=[('focus', c['home_primary_pressed'])],
    )
    style.configure(
        'Home.Secondary.TButton',
        font=host.home_fonts["action"],
        padding=home_button_pad,
        background=c['home_surface'],
        foreground=c['home_ink'],
        bordercolor=c['home_border'],
        focuscolor=c['home_primary'],
        lightcolor=c['home_surface'],
        darkcolor=c['home_surface'],
    )
    style.map(
        'Home.Secondary.TButton',
        background=[
            ('pressed', c['home_primary_tint']),
            ('active', c['home_surface_quiet']),
        ],
        bordercolor=[('focus', c['home_primary'])],
    )
    # 工作台主动作：与普通按钮保持相同字号和内边距，仅用颜色区分主次。
    style.configure('Workbench.Primary.TButton', font=host.font_label, padding=(15, 8),
                    background=c['primary'], foreground='#FFFFFF',
                    bordercolor=c['primary_dark'], focuscolor=c['primary_dark'],
                    lightcolor=c['primary'], darkcolor=c['primary'])
    style.map('Workbench.Primary.TButton',
              background=[('pressed', c.get('primary_deep', ui_theme.PRIMARY_DEEP)),
                          ('active', c['primary_dark']),
                          ('disabled', c['bg_input'])],
              foreground=[('disabled', c.get('text_muted', ui_theme.TEXT_MUTED))],
              bordercolor=[('disabled', c['border'])])
    # 危险级（Danger）：实心红，用于删除/停止等需警示的动作
    style.configure('Danger.TButton', font=host.font_label, padding=(15, 8),
                    background=c['danger'], foreground='#FFFFFF',
                    bordercolor=c.get('danger_text', ui_theme.DANGER_TEXT),
                    lightcolor=c['danger'], darkcolor=c['danger'])
    style.map('Danger.TButton',
              background=[('pressed', c.get('danger_deep', ui_theme.DANGER_DEEP)), ('active', c.get('danger_text', ui_theme.DANGER_TEXT)),
                          ('disabled', c['bg_input'])],
              foreground=[('disabled', c.get('text_muted', ui_theme.TEXT_MUTED))],
              bordercolor=[('disabled', c['border'])])
    # 运行控制的开始/停止按钮使用同一字体，仅保留颜色语义差异。
    style.configure(
        'RunControl.Danger.TButton',
        font=(ui_theme.FONT_FAMILY_SEMIBOLD, int(13 * page_fs)),
    )

    style.configure('Card.TFrame', background=c['bg_card'], relief='solid', borderwidth=1)
    style.configure('WelcomeCard.TFrame', background=host.colors['bg_card'],
                    relief='flat', borderwidth=0)
    style.configure('WelcomeInner.TFrame', background=host.colors['bg_card'])
    style.configure('PageHeader.TFrame', background=host.colors['bg_card'],
                    relief='flat', borderwidth=0)
    style.configure('PageHeaderInner.TFrame', background=host.colors['bg_card'])
    style.configure('Sidebar.TFrame', background=host.colors['bg_sidebar'])
    sidebar_font_size = int(11 * host.font_scale)
    style.configure('Sidebar.TLabel', font=(ui_theme.FONT_FAMILY, sidebar_font_size),
                   foreground=host.colors['text_sidebar'], background=host.colors['bg_sidebar'])
    style.configure('SidebarSelected.TLabel', font=(ui_theme.FONT_FAMILY, sidebar_font_size, 'bold'),
                   foreground=host.colors['text_sidebar_active'], background=host.colors['bg_sidebar'])
    style.configure('Header.TLabel', font=host.font_title, foreground=host.colors['text_primary'])
    style.configure('Section.TLabel', font=host.font_section, foreground=host.colors['text_primary'])
    style.configure('Stat.TLabel', font=host.font_stat, foreground=host.colors['primary'])
    style.configure('StatLabel.TLabel', font=host.font_stat_label, foreground=host.colors['text_secondary'])
    style.configure('Primary.TLabel', font=host.font_label, foreground=host.colors['primary'])
    style.configure('Success.TLabel', font=host.font_label, foreground=host.colors['success'])
    style.configure('Warning.TLabel', font=host.font_label, foreground=host.colors['warning'])
    # 下拉菜单样式 - 设置行高确保文字垂直居中
    combo_font_size = int(15 * host.font_scale)
    style.configure('TCombobox', font=host.font_label)
    style.configure('TCombobox', rowheight=int(combo_font_size * 1.8))
    # macOS aqua 下 fieldbackground 只能通过 map 设置，configure 被原生渲染忽略
    style.map(
        'TCombobox',
        fieldbackground=[
            ('disabled', c['bg_input']),
            ('readonly', c['bg_card']),
            ('!disabled', c['bg_card']),
        ],
        foreground=[
            ('disabled', c['text_muted']),
            ('readonly', c['text_primary']),
            ('!disabled', c['text_primary']),
        ],
        selectbackground=[
            ('disabled', c['bg_input']),
            ('readonly', c['bg_card']),
            ('!focus', c['bg_card']),
            ('focus', c['primary']),
        ],
        selectforeground=[
            ('disabled', c['text_muted']),
            ('readonly', c['text_primary']),
            ('!focus', c['text_primary']),
            ('focus', '#FFFFFF'),
        ],
    )
    style.map('TSpinbox',
              fieldbackground=[('!disabled', host.colors['bg_card']),
                               ('disabled', host.colors['bg_input'])])
    # 基础筛选的下拉框和 Spinbox 都带箭头区；按当前字体字符宽补偿，
    # 使 width=6 的两类控件与 width=8 的薪资 Entry 保持相同像素宽度。
    _filter_char_width = font.Font(font=host.font_label).measure("0")
    style.configure(
        'CompactFilter.TCombobox',
        padding=(max(0, _filter_char_width - 6), 0),
    )
    style.configure(
        'CompactFilter.TSpinbox',
        padding=(max(0, _filter_char_width - 4), 0),
    )
    style.map('TEntry',
              fieldbackground=[('!disabled', host.colors['bg_card']),
                               ('disabled', host.colors['bg_input'])])
    # 模型名称 Entry 没有 Combobox 的下拉箭头区，补足固定边框差值，
    # 使同为 width=18 时两者的视觉外宽一致。
    style.configure('SettingsModel.TEntry', padding=(8, 0))

    # ---------------- 表格 / 表头 / 滚动条 / 输入控件（clam 扁平化） ----------------
    style.configure('Treeview',
                    background=c['bg_card'], fieldbackground=c['bg_card'],
                    foreground=c['text_primary'],
                    bordercolor=c['border'], lightcolor=c['border'], darkcolor=c['border'])
    style.map('Treeview',
              background=[('selected', c.get('banner_info_bg', ui_theme.BANNER_INFO_BG))],
              foreground=[('selected', c['primary_dark'])])
    style.configure('Treeview.Heading',
                    background=c.get('bg_footer', ui_theme.BG_FOOTER),
                    foreground=c['text_secondary'],
                    bordercolor=c['border'], padding=(4, 3), relief='flat')
    style.map('Treeview.Heading',
              background=[('active', c['bg_hover'])],
              foreground=[('active', c['text_primary'])])
    for _sb in ('Vertical.TScrollbar', 'Horizontal.TScrollbar'):
        style.configure(_sb,
                        background=c['border'], troughcolor=c['bg_main'],
                        bordercolor=c['bg_main'], arrowcolor=c['text_secondary'],
                        lightcolor=c['border'], darkcolor=c['border'])
        style.map(_sb, background=[('active', c.get('border_strong', ui_theme.BORDER_STRONG)),
                                   ('pressed', c['text_secondary'])])
    # 输入控件：白底灰边，聚焦时品牌蓝边
    for _input in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(_input,
                        bordercolor=c.get('border_strong', ui_theme.BORDER_STRONG),
                        lightcolor=c.get('border_strong', ui_theme.BORDER_STRONG),
                        darkcolor=c.get('border_strong', ui_theme.BORDER_STRONG),
                        focuscolor=c['primary'])
        style.map(_input,
                  bordercolor=[('focus', c['primary'])],
                  lightcolor=[('focus', c['primary'])],
                  darkcolor=[('focus', c['primary'])])
    checkbox_size = max(24, int(round(24 * fs)))
    checkbox_off = host.icons.get(
        'checkbox_off', checkbox_size, c.get('border_strong', ui_theme.BORDER_STRONG)
    )
    checkbox_on = host.icons.get('checkbox_on', checkbox_size, c['primary'])
    checkbox_disabled_off = host.icons.get(
        'checkbox_off', checkbox_size, c['text_muted']
    )
    checkbox_disabled_on = host.icons.get(
        'checkbox_on', checkbox_size, c['text_muted']
    )
    host._checkbox_style_images = (
        checkbox_off,
        checkbox_on,
        checkbox_disabled_off,
        checkbox_disabled_on,
    )
    checkbox_indicator = 'App.Checkbutton.indicator'
    if checkbox_indicator not in style.element_names():
        style.element_create(
            checkbox_indicator,
            'image',
            checkbox_off,
            ('disabled', 'selected', checkbox_disabled_on),
            ('disabled', checkbox_disabled_off),
            ('selected', checkbox_on),
            sticky='w',
        )
    style.layout(
        'TCheckbutton',
        [
            ('Checkbutton.padding', {
                'sticky': 'nswe',
                'children': [
                    (checkbox_indicator, {'side': 'left', 'sticky': ''}),
                    ('Checkbutton.label', {
                        'side': 'left',
                        'sticky': 'nswe',
                    }),
                ],
            }),
        ],
    )
    style.configure(
        'TCheckbutton',
        background=c['bg_card'],
        foreground=c['text_primary'],
        padding=(2, 2),
    )
    style.configure('TRadiobutton', background=c['bg_card'], foreground=c['text_primary'])
    style.configure('Horizontal.TProgressbar',
                    troughcolor=c['bg_main'], background=c['primary'],
                    bordercolor=c['bg_main'], lightcolor=c['primary'], darkcolor=c['primary'])
    style.configure('TSeparator', background=c['border'])

    style.configure('Custom.TLabelframe', font=host.font_label, background=host.colors['bg_card'])
    style.configure('Custom.TLabelframe.Label', font=host.font_label, background=host.colors['bg_card'])
