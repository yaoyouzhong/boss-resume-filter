"""
GUI 对话框模块 - 从 gui_main.py 提取的独立对话框
"""
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from changelog_parser import normalize_version, parse_changelog_versions, resolve_local_changelog_path


def render_changelog_text(
    text_widget,
    body,
    colors,
    font_family,
    font_family_bold,
    font_scale,
    layout_scale,
    *,
    section_font_size=13,
    item_font_size=12,
    include_version_title=False,
):
    """Render a small CHANGELOG markdown subset into a Tk Text widget."""
    fs = lambda size: int(size * font_scale)
    pad = lambda value: int(value * layout_scale)

    section_font = (font_family_bold, fs(section_font_size))
    item_font = (font_family, fs(item_font_size))
    item_bold_font = (font_family_bold, fs(item_font_size))
    item_left_margin = pad(18)
    item_wrap_margin = pad(36)

    text_widget.tag_configure("section_new", font=section_font, foreground=colors.get('success', '#48BB78'))
    text_widget.tag_configure("section_opt", font=section_font, foreground=colors['primary'])
    text_widget.tag_configure("section_ui", font=section_font, foreground=colors.get('purple', '#805AD5'))
    text_widget.tag_configure("section_fix", font=section_font, foreground=colors.get('danger', '#E53E3E'))
    text_widget.tag_configure("section_build", font=section_font, foreground=colors.get('warning', '#D69E2E'))
    text_widget.tag_configure("item", font=item_font, foreground=colors['text_secondary'],
                              lmargin1=item_left_margin, lmargin2=item_wrap_margin)
    text_widget.tag_configure("item_bold", font=item_bold_font, foreground=colors['text_primary'])

    section_map = {
        '新增功能': 'section_new',
        '体验优化': 'section_opt',
        '行为优化': 'section_opt',
        '性能优化': 'section_opt',
        'UI 改进': 'section_ui',
        'UI改进': 'section_ui',
        '问题修复': 'section_fix',
        'Bug 修复': 'section_fix',
        'Bug修复': 'section_fix',
        '构建改进': 'section_build',
    }

    for line in body.splitlines():
        stripped = line.lstrip('#').strip()
        header_level = len(line) - len(line.lstrip('#'))
        is_section = (header_level in (2, 3)) and stripped and not stripped.startswith('v')

        if line.startswith("## v") and not include_version_title:
            continue
        if is_section:
            stag = section_map.get(stripped, 'section_opt')
            text_widget.insert("end", "\n" + stripped + "\n\n", stag)
        elif line.startswith("- "):
            item_text = line[2:]
            if item_text.startswith("**"):
                end_pos = item_text.find("**", 2)
                if end_pos > 0:
                    title_part = item_text[2:end_pos]
                    rest = item_text[end_pos + 2:]
                    full_text = "• " + title_part + rest + "\n"
                    line_start = text_widget.index("end")
                    text_widget.insert("end", full_text, "item")
                    bold_start = f"{line_start} + 2 chars"
                    bold_end = f"{line_start} + {2 + len(title_part)} chars"
                    text_widget.tag_add("item_bold", bold_start, bold_end)
                else:
                    text_widget.insert("end", "• " + item_text + "\n", "item")
            else:
                text_widget.insert("end", "• " + item_text + "\n", "item")


def show_about_dialog(gui, version):
    """显示关于弹窗"""
    import webbrowser
    import updater
    from gui_main import FONT_FAMILY

    dialog = tk.Toplevel(gui.root)
    dialog.title("关于 BOSS 简历筛选器")
    dialog.transient(gui.root)
    dialog.resizable(False, False)
    dialog.configure(background=gui.colors['bg_main'])

    _s = gui.dpi_scale * gui.zoom_factor
    about_fs = gui.font_scale * 0.88
    dialog_width = max(580, int(580 * _s))
    dialog_height = max(420, int(420 * _s))
    gui._center_window(dialog, dialog_width, dialog_height)

    tk.Label(dialog, text="BOSS 简历筛选器",
             font=(FONT_FAMILY, int(20 * about_fs), 'bold'),
             bg=gui.colors['bg_main'],
             fg=gui.colors['text_primary']).pack(pady=(int(25 * _s), int(5 * _s)))

    tk.Label(dialog, text=f"v{version}",
             font=(FONT_FAMILY, int(13 * about_fs)),
             bg=gui.colors['bg_main'],
             fg=gui.colors['text_secondary']).pack(pady=(0, int(15 * _s)))

    tk.Label(dialog, text="智能招聘需求解析 · 候选人评估筛选 · 自动打招呼 · 学历核验",
             font=(FONT_FAMILY, int(12 * about_fs)),
             bg=gui.colors['bg_main'],
             fg=gui.colors['text_primary']).pack(pady=(0, int(5 * _s)))

    tk.Label(dialog, text="基于 DrissionPage 的 BOSS 直聘自动化工具",
             font=(FONT_FAMILY, int(12 * about_fs)),
             bg=gui.colors['bg_main'],
             fg=gui.colors['text_secondary']).pack(pady=(0, int(15 * _s)))

    tk.Frame(dialog, bg=gui.colors['border'], height=1).pack(
        fill="x", padx=int(40 * _s), pady=int(10 * _s))

    links_card = tk.Frame(dialog, bg=gui.colors['bg_card'],
                          highlightbackground=gui.colors['border'],
                          highlightthickness=1)
    links_card.pack(fill="x", padx=int(40 * _s), pady=(int(10 * _s), int(5 * _s)))
    link_pad_x = int(15 * _s)
    link_pad_y = int(8 * _s)

    github_url = "https://github.com/yaoyouzhong/boss-resume-filter"
    github_row = tk.Frame(links_card, bg=gui.colors['bg_card'])
    github_row.pack(fill="x", padx=link_pad_x, pady=(link_pad_y, int(2 * _s)))
    tk.Label(github_row, text="项目",
             font=(FONT_FAMILY, int(12 * about_fs)),
             bg=gui.colors['bg_card'],
             fg=gui.colors['text_secondary']).pack(side="left", padx=(0, int(10 * _s)))
    github_label = tk.Label(github_row, text=github_url,
                            font=(FONT_FAMILY, int(12 * about_fs)),
                            bg=gui.colors['bg_card'],
                            fg=gui.colors['primary'],
                            cursor="hand2")
    github_label.pack(side="left")
    github_label.bind("<Button-1>", lambda e: webbrowser.open(github_url))

    gitee_url = "https://gitee.com/yaoyouzhong/boss-resume-filter"
    gitee_row = tk.Frame(links_card, bg=gui.colors['bg_card'])
    gitee_row.pack(fill="x", padx=link_pad_x, pady=(int(2 * _s), int(2 * _s)))
    tk.Label(gitee_row, text="镜像",
             font=(FONT_FAMILY, int(12 * about_fs)),
             bg=gui.colors['bg_card'],
             fg=gui.colors['text_secondary']).pack(side="left", padx=(0, int(10 * _s)))
    gitee_label = tk.Label(gitee_row, text=gitee_url,
                           font=(FONT_FAMILY, int(12 * about_fs)),
                           bg=gui.colors['bg_card'],
                           fg=gui.colors['primary'],
                           cursor="hand2")
    gitee_label.pack(side="left")
    gitee_label.bind("<Button-1>", lambda e: webbrowser.open(gitee_url))

    author_row = tk.Frame(links_card, bg=gui.colors['bg_card'])
    author_row.pack(fill="x", padx=link_pad_x, pady=(int(2 * _s), link_pad_y))
    tk.Label(author_row, text="作者",
             font=(FONT_FAMILY, int(12 * about_fs)),
             bg=gui.colors['bg_card'],
             fg=gui.colors['text_secondary']).pack(side="left", padx=(0, int(10 * _s)))
    tk.Label(author_row, text="yaoyouzhong & Claude Code & Codex",
             font=(FONT_FAMILY, int(12 * about_fs)),
             bg=gui.colors['bg_card'],
             fg=gui.colors['text_primary']).pack(side="left")

    btn_frame = tk.Frame(dialog, bg=gui.colors['bg_main'])
    btn_frame.pack(pady=(int(20 * _s), int(10 * _s)))

    icon_refresh = gui.icons.button('refresh', gui.colors['primary'])
    icon_close = gui.icons.button('close', gui.colors['text_secondary'])
    _pad = int(10 * _s)
    btn_w = int(130 * _s)
    btn_h = int(32 * _s)

    def _icon_btn(parent, icon, text, command):
        frame = tk.Frame(parent, bg=gui.colors['bg_card'],
                       highlightbackground=gui.colors['border'],
                       highlightthickness=1, cursor='hand2',
                       width=btn_w, height=btn_h)
        frame.pack_propagate(False)
        content = tk.Frame(frame, bg=gui.colors['bg_card'])
        content.pack(expand=True)
        tk.Label(content, image=icon, bg=gui.colors['bg_card']).pack(
            side='left', padx=(0, 2), anchor='center')
        tk.Label(content, text=text, bg=gui.colors['bg_card'],
                font=(FONT_FAMILY, int(13 * about_fs)), fg=gui.colors['text_primary']).pack(
            side='left', padx=(2, 0), anchor='center')

        def _all_descendants(w):
            result = [w]
            for child in w.winfo_children():
                result.extend(_all_descendants(child))
            return result

        _children = _all_descendants(frame)

        def _on_enter(e, ch=_children, c=gui.colors['bg_hover']):
            for w in ch:
                w.config(bg=c)

        def _on_leave(e, ch=_children, c=gui.colors['bg_card']):
            for w in ch:
                w.config(bg=c)

        for widget in _children:
            widget.bind('<Enter>', _on_enter)
            widget.bind('<Leave>', _on_leave)
            widget.bind('<Button-1>', lambda e, cmd=command: cmd())
        return frame

    _icon_btn(btn_frame, icon_refresh, '检查更新',
              lambda: updater.check_and_update_gui(
                  gui.root, silent=False, gui=gui, source="manual")
              ).pack(side="left", padx=_pad)
    _icon_btn(btn_frame, icon_close, '关闭', dialog.destroy).pack(side="left", padx=_pad)

    tk.Label(dialog, text="MIT License · 开源免费",
             font=(FONT_FAMILY, int(10 * about_fs)),
             bg=gui.colors['bg_main'],
             fg=gui.colors['text_muted']).pack(pady=(int(10 * _s), int(10 * _s)))

    dialog.bind('<Escape>', lambda e: dialog.destroy())
    dialog.grab_set()


def show_changelog_dialog(gui):
    """显示更新日志对话框（版本列表 + 详情分栏）

    Args:
        gui: BossFilterGUI 实例
    """
    from paths import BASE_DIR
    changelog_path = resolve_local_changelog_path(BASE_DIR)
    if not changelog_path:
        messagebox.showinfo("更新日志", "CHANGELOG.md 文件不存在")
        return

    try:
        content = changelog_path.read_text(encoding="utf-8")
    except Exception as e:
        messagebox.showerror("错误", f"读取更新日志失败：{e}")
        return

    versions = parse_changelog_versions(content)
    if not versions:
        messagebox.showinfo("更新日志", "CHANGELOG.md 中没有版本记录")
        return

    dialog = tk.Toplevel(gui.root)
    dialog.title("更新日志")
    dialog.transient(gui.root)
    dialog.withdraw()

    fs = gui.dpi_scale * gui.zoom_factor
    changelog_fs = gui.font_scale * 0.88
    from gui_main import _clamp, _place_window_centered, FONT_FAMILY
    dialog_scale = _clamp(fs, 1.0, 1.50)
    dw = int(940 * dialog_scale)
    dh = int(620 * dialog_scale)
    _place_window_centered(dialog, dw, dh, parent=gui.root)

    # ---- 左侧版本列表（深色侧边栏风格）----
    sidebar_bg = '#2D3748'
    left_frame = tk.Frame(dialog, bg=sidebar_bg, width=int(190 * fs))
    left_frame.pack(side="left", fill="y")
    left_frame.pack_propagate(False)

    # 标题
    title_frame = tk.Frame(left_frame, bg=sidebar_bg)
    title_frame.pack(fill="x", padx=int(16 * fs), pady=(int(18 * fs), int(8 * fs)))
    tk.Label(title_frame, text="版本历史", bg=sidebar_bg, fg='#E2E8F0',
             font=(FONT_FAMILY, int(14 * changelog_fs), 'bold')).pack(anchor="center")
    tk.Label(title_frame, text=f"共 {len(versions)} 个版本", bg=sidebar_bg, fg='#A0AEC0',
             font=(FONT_FAMILY, int(11 * changelog_fs))).pack(anchor="center", pady=(int(2 * fs), 0))
    if len(versions) > 20:
        tk.Label(title_frame, text="可滚动查看", bg=sidebar_bg, fg='#718096',
                 font=(FONT_FAMILY, int(9 * changelog_fs))).pack(anchor="center")

    # 版本列表
    list_container = tk.Frame(left_frame, bg=sidebar_bg)
    list_container.pack(fill="both", expand=True, padx=int(16 * fs), pady=(int(4 * fs), int(6 * fs)))

    # wrapper 撑满容器高度（显示全部版本），内容水平居中
    list_wrapper = tk.Frame(list_container, bg=sidebar_bg)
    list_wrapper.pack(fill="both", expand=True)

    # Canvas + Label 方案（Listbox 不支持逐行字体）
    canvas_bg = sidebar_bg
    list_canvas = tk.Canvas(list_wrapper, bg=canvas_bg, highlightthickness=0, borderwidth=0)
    list_inner = tk.Frame(list_canvas, bg=canvas_bg)
    canvas_window_id = list_canvas.create_window(0, 0, window=list_inner, anchor='nw')
    list_inner.bind('<Configure>', lambda e: list_canvas.configure(scrollregion=list_canvas.bbox('all')))
    list_canvas.bind('<Configure>', lambda e: list_canvas.itemconfigure(canvas_window_id, width=e.width))
    list_scrollbar = tk.Scrollbar(list_wrapper, orient="vertical",
                                  command=list_canvas.yview,
                                  width=max(8, int(8 * fs)),
                                  bg='#718096',
                                  activebackground='#CBD5E0',
                                  troughcolor='#1F2937',
                                  borderwidth=0, highlightthickness=0, relief='flat')
    list_canvas.configure(yscrollcommand=list_scrollbar.set)
    list_scrollbar.pack(side="right", fill="y")
    list_canvas.pack(fill="both", expand=True)

    def _canvas_mousewheel(event):
        list_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"
    list_canvas.bind("<MouseWheel>", _canvas_mousewheel)
    list_inner.bind("<MouseWheel>", _canvas_mousewheel)
    if sys.platform != 'win32':
        def _cb_scroll_up(e): list_canvas.yview_scroll(-1, "units"); return "break"
        def _cb_scroll_down(e): list_canvas.yview_scroll(1, "units"); return "break"
        list_canvas.bind("<Button-4>", _cb_scroll_up)
        list_canvas.bind("<Button-5>", _cb_scroll_down)
        list_inner.bind("<Button-4>", _cb_scroll_up)
        list_inner.bind("<Button-5>", _cb_scroll_down)

    row_frames = []
    selected_row_idx = [0]
    current_version = str(getattr(gui, "version", "") or getattr(gui, "__version__", ""))
    if not current_version:
        try:
            import gui_main
            current_version = getattr(gui_main, "__version__", "")
        except Exception:
            current_version = ""
    current_idx = next(
        (i for i, (tag, _, _) in enumerate(versions)
         if normalize_version(tag) == normalize_version(current_version)),
        0,
    )
    remote_sections = {}

    for idx, (tag, title_line, _) in enumerate(versions):
        is_patch = tag.count('.') >= 2  # X.Y.Z 是补丁版本
        font_size = int(10 * changelog_fs) if is_patch else int(12 * changelog_fs)
        row_bg = '#243041' if idx % 2 == 0 else sidebar_bg
        row_fg = '#718096' if is_patch else ('#F8FAFC' if idx == 0 else '#E2E8F0')

        row_frame = tk.Frame(list_inner, bg=row_bg)
        row_frame.pack(fill='x', pady=0)
        lbl = tk.Label(row_frame, text=f"  {tag}", bg=row_bg, fg=row_fg,
                       font=(FONT_FAMILY, font_size), anchor='w')
        lbl.pack(fill='x', ipady=int(2 * fs))
        row_frames.append((row_frame, lbl, row_bg, row_fg, idx))

        def make_select_handler(i):
            def handler(event=None):
                select_version(i)
            return handler
        def _enter(e, rf=row_frame, lb=lbl):
            if selected_row_idx[0] != rf._idx:
                rf.config(bg=gui.colors['primary'])
                lb.config(bg=gui.colors['primary'], fg='#FFFFFF')
        def _leave(e, rf=row_frame, lb=lbl, rb=row_bg, rfg=row_fg):
            if selected_row_idx[0] != rf._idx:
                rf.config(bg=rb)
                lb.config(bg=rb, fg=rfg)
        row_frame._idx = idx
        for widget in (row_frame, lbl):
            widget.bind('<Button-1>', make_select_handler(idx))
            widget.bind('<Enter>', _enter)
            widget.bind('<Leave>', _leave)
            widget.bind('<MouseWheel>', _canvas_mousewheel)
            if sys.platform != 'win32':
                widget.bind('<Button-4>', _cb_scroll_up)
                widget.bind('<Button-5>', _cb_scroll_down)

    def select_version(idx):
        selected_row_idx[0] = idx
        for rf, lb, orig_bg, orig_fg, i in row_frames:
            if i == idx:
                rf.config(bg=gui.colors['primary'])
                lb.config(bg=gui.colors['primary'], fg='#FFFFFF')
            else:
                rf.config(bg=orig_bg)
                lb.config(bg=orig_bg, fg=orig_fg)
        show_version(idx)

    # 左侧边栏底部：关于链接
    about_label = tk.Label(left_frame, text="关于",
                           bg=sidebar_bg, fg='#A0AEC0',
                           font=(FONT_FAMILY, int(12 * changelog_fs)),
                           cursor="hand2")
    about_label.pack(padx=int(12 * fs), pady=(int(8 * fs), int(12 * fs)))
    about_label.bind("<Button-1>", lambda e: gui.show_about())

    # ---- 右侧详情 ----
    right_outer = tk.Frame(dialog, bg=gui.colors['bg_main'])
    right_outer.pack(side="left", fill="both", expand=True)

    # 顶部标题栏
    header_frame = tk.Frame(right_outer, bg=gui.colors['bg_card'], height=int(72 * fs))
    header_frame.pack(fill="x")
    header_frame.pack_propagate(False)

    version_title = tk.Label(header_frame, text="",
                             font=(FONT_FAMILY, int(16 * changelog_fs), 'bold'),
                             fg=gui.colors['text_primary'], bg=gui.colors['bg_card'])
    version_title.pack(anchor="w", padx=int(20 * fs), pady=(int(14 * fs), 0))

    version_subtitle = tk.Label(header_frame, text="",
                                font=(FONT_FAMILY, int(11 * changelog_fs)),
                                fg=gui.colors['text_muted'], bg=gui.colors['bg_card'])
    version_subtitle.pack(anchor="w", padx=int(20 * fs))

    # 分隔线
    tk.Frame(right_outer, bg=gui.colors['border'], height=1).pack(fill="x")

    # 内容区
    content_frame = tk.Frame(right_outer, bg=gui.colors['bg_main'])
    content_frame.pack(fill="both", expand=True)

    text_widget = tk.Text(content_frame, wrap="char", borderwidth=0,
                          font=(FONT_FAMILY, int(12 * changelog_fs)),
                          bg=gui.colors['bg_main'], fg=gui.colors['text_primary'],
                          padx=int(12 * fs), pady=int(12 * fs),
                          spacing1=0, spacing2=1, spacing3=2,
                          selectbackground=gui.colors['primary'],
                          relief='flat', highlightthickness=0)
    text_widget.pack(side="left", fill="both", expand=True)

    scrollbar = ttk.Scrollbar(content_frame, orient="vertical", command=text_widget.yview)
    detail_scrollbar_job = None

    def update_detail_scrollbar(first, last):
        scrollbar.set(first, last)
        try:
            needs_scroll = float(first) > 0.0 or float(last) < 1.0
        except ValueError:
            needs_scroll = True
        if needs_scroll:
            if not scrollbar.winfo_ismapped():
                scrollbar.pack(side="right", fill="y")
        else:
            if scrollbar.winfo_ismapped():
                scrollbar.pack_forget()

    def refresh_detail_scrollbar():
        nonlocal detail_scrollbar_job
        detail_scrollbar_job = None
        text_widget.update_idletasks()
        first, last = text_widget.yview()
        update_detail_scrollbar(first, last)

    def schedule_detail_scrollbar_refresh(delay_ms=50):
        nonlocal detail_scrollbar_job
        if detail_scrollbar_job:
            dialog.after_cancel(detail_scrollbar_job)
        detail_scrollbar_job = dialog.after(delay_ms, refresh_detail_scrollbar)

    text_widget.configure(yscrollcommand=update_detail_scrollbar)

    def show_version(index):
        tag, title_line, section = versions[index]
        display_section = remote_sections.get(index, section)
        # 更新顶部标题
        version_title.config(text=tag)
        # 提取副标题（## v2.7 — LLM 智能评估... → LLM 智能评估...）
        if "—" in title_line:
            sub = title_line.split("—", 1)[1].strip()
        elif "–" in title_line:
            sub = title_line.split("–", 1)[1].strip()
        else:
            sub = ""
        version_subtitle.config(text=sub)

        text_widget.configure(state="normal")
        text_widget.delete("1.0", "end")
        render_changelog_text(
            text_widget, display_section, gui.colors, FONT_FAMILY, FONT_FAMILY,
            changelog_fs, fs, section_font_size=13, item_font_size=12)
        text_widget.configure(state="disabled")
        text_widget.yview_moveto(0)
        schedule_detail_scrollbar_refresh()

    def apply_remote_current_notes(notes):
        if not notes:
            return
        try:
            if not dialog.winfo_exists():
                return
        except tk.TclError:
            return
        tag, title_line, _ = versions[current_idx]
        remote_sections[current_idx] = f"{title_line}\n\n{notes.strip()}"
        if selected_row_idx[0] == current_idx:
            show_version(current_idx)

    def load_remote_current_notes():
        try:
            import updater
            cached = updater.get_cached_release_notes(current_version)
            if cached:
                gui.root.after(0, lambda: apply_remote_current_notes(cached))
            notes = updater.fetch_current_release_notes(current_version, use_cache=False)
            if notes and notes != cached:
                gui.root.after(0, lambda: apply_remote_current_notes(notes))
        except Exception:
            return

    # 默认选中第一个版本（最新）
    select_version(0)

    if current_version:
        threading.Thread(target=load_remote_current_notes, daemon=True).start()

    # 内容创建完成后再按实际窗口尺寸复位一次，避免初始布局请求导致视觉中心偏移。
    dialog.update_idletasks()
    _place_window_centered(dialog, dw, dh, parent=gui.root)
    dialog.deiconify()
    schedule_detail_scrollbar_refresh(100)
