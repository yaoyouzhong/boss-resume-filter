from pathlib import Path
from unittest.mock import Mock, patch

from ui_messagebox import CenteredMessageBox


def test_centered_messagebox_uses_configured_window_placer():
    box = CenteredMessageBox()
    placer = Mock()
    window = Mock()
    parent = Mock()
    box.set_window_placer(placer)

    box._place(window, 480, 260, parent)

    placer.assert_called_once_with(window, 480, 260, parent=parent)


def test_centered_messagebox_accepts_application_scaled_fonts():
    box = CenteredMessageBox()
    box.set_ui_fonts(
        headline=("App Font", 18, "bold"),
        message=("App Font", 15),
        button=("App Font", 15),
    )

    assert box._headline_font == ("App Font", 18, "bold")
    assert box._message_font == ("App Font", 15)
    assert box._button_font == ("App Font", 15)


def test_centered_messagebox_accepts_compact_structured_fonts():
    box = CenteredMessageBox()
    box.set_structured_ui_fonts(
        headline=("App Font", 14, "bold"),
        message=("App Font", 12),
        meta=("App Font", 11),
        button=("App Font", 12),
    )

    assert box._structured_headline_font == ("App Font", 14, "bold")
    assert box._structured_message_font == ("App Font", 12)
    assert box._structured_meta_font == ("App Font", 11)
    assert box._structured_button_font == ("App Font", 12)


def test_gui_uses_readable_dedicated_modal_fonts():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    setup = source[source.index("messagebox.set_ui_fonts("):]
    setup = setup[:setup.index("# 设置 Combobox")]

    assert "modal_font_size = max(9, self.font_log[1])" in source
    assert "headline=(FONT_FAMILY, max(10, self.font_log[1]), 'bold')" in setup
    legacy_setup = setup[:setup.index("structured_message_size")]
    assert legacy_setup.count("modal_font_size") == 2
    assert "message=self.font_label" not in setup
    assert "structured_message_size = max(9, modal_font_size - 2)" in setup
    assert "messagebox.set_structured_ui_fonts(" in setup
    assert "headline=(FONT_FAMILY, structured_message_size + 2, 'bold')" in setup
    assert "meta=(FONT_FAMILY, max(9, structured_message_size - 1))" in setup


def test_api_probe_failure_copy_does_not_claim_connectivity_succeeded():
    source = Path("gui_main.py").read_text(encoding="utf-8")

    assert "模型不能用于 AI 评估" in source
    assert "API 可访问，但模型不能用于 AI 评估" not in source


def test_centered_messagebox_can_reduce_dialog_fonts_by_one_level():
    box = CenteredMessageBox()

    assert box._font_with_delta(("App Font", 16, "bold"), -1) == (
        "App Font", 15, "bold"
    )


def test_centered_messagebox_supports_numbered_items_with_aligned_wrapping():
    box = CenteredMessageBox()
    parent = Mock()
    numbered_items = ["一段需要完整展示的较长提醒"]
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show", return_value="ok") as show:
        assert box.showinfo(
            "AI 解析提醒",
            "",
            parent=parent,
            numbered_items=numbered_items,
            min_width=820,
            max_width=900,
        ) == "ok"

    assert show.call_args.kwargs["numbered_items"] == numbered_items
    assert show.call_args.kwargs["min_width"] == 820
    assert show.call_args.kwargs["max_width"] == 900
    assert show.call_args.kwargs["show_icon"] is False


def test_numbered_item_rows_wrap_content_without_splitting_the_number_column():
    source = Path("ui_messagebox.py").read_text(encoding="utf-8")
    block = source[source.index("if numbered_items:"):]
    block = block[:block.index("elif self._message_needs_scroll(message):")]

    assert 'text=f"{index}."' in block
    assert "column=0" in block
    assert "column=1" in block
    assert "wraplength=item_wraplength" in block
    assert "wraplength=0" not in block
    assert "item_text, item_prompt = item" in block
    assert "text=str(item_text)" in block
    assert "text=str(item_prompt)" in block
    assert "fg=ui_theme.TEXT_SECONDARY" in block


def test_centered_messagebox_hides_character_icons_for_all_dialog_types_by_default():
    box = CenteredMessageBox()
    parent = Mock()
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show", return_value=True) as show:
        box.showinfo("信息", "完成", parent=parent)
        box.showwarning("警告", "请检查", parent=parent)
        box.showerror("错误", "失败", parent=parent)
        box.askyesno("确认", "继续？", parent=parent)
        box.askokcancel("确认", "继续？", parent=parent)
        box.askretrycancel("失败", "重试？", parent=parent)
        box.askyesnocancel("保存", "保存修改？", parent=parent)

    assert show.call_count == 7
    assert all(call.kwargs["show_icon"] is False for call in show.call_args_list)


def test_centered_messagebox_question_supports_action_labels():
    box = CenteredMessageBox()
    parent = Mock()
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show", return_value=True) as show:
        result = box.askyesno(
            "确认开始发送",
            "确认联系 2 名候选人？",
            parent=parent,
            yes_label="开始发送",
            no_label="取消",
            headline="发送 2 名候选人？",
            show_icon=False,
            min_width=560,
            font_delta=-1,
        )

    assert result is True
    assert show.call_args.kwargs["buttons"] == (("开始发送", True), ("取消", False))
    assert show.call_args.kwargs["parent"] is parent
    assert show.call_args.kwargs["headline"] == "发送 2 名候选人？"
    assert show.call_args.kwargs["show_icon"] is False
    assert show.call_args.kwargs["min_width"] == 560
    assert show.call_args.kwargs["font_delta"] == -1


def test_centered_messagebox_can_add_space_above_action_footer():
    box = CenteredMessageBox()
    parent = Mock()
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show", return_value="ok") as show:
        result = box.showwarning(
            "发送结果",
            "成功：1 人",
            parent=parent,
            content_bottom_padding=28,
        )

    assert result == "ok"
    assert show.call_args.kwargs["content_bottom_padding"] == 28


def test_centered_messagebox_supports_compact_single_action_layout():
    box = CenteredMessageBox()
    parent = Mock()
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show", return_value="ok") as show:
        result = box.showinfo(
            "检查更新",
            "当前已是最新版本 v2.23.4",
            parent=parent,
            min_width=500,
            font_delta=-1,
            compact_action=True,
        )

    assert result == "ok"
    assert show.call_args.kwargs["min_width"] == 500
    assert show.call_args.kwargs["font_delta"] == -1
    assert show.call_args.kwargs["compact_action"] is True


def test_update_result_uses_compact_single_action_layout():
    source = Path("updater.py").read_text(encoding="utf-8")
    block = source[source.index('messagebox.showinfo(\n                    "检查更新"'):]
    block = block[:block.index("\n                )")]

    assert "min_width=500" in block
    assert "font_delta=-1" in block
    assert "compact_action=True" in block


def test_centered_messagebox_centers_buttons_vertically_in_footer():
    source = Path("ui_messagebox.py").read_text(encoding="utf-8")
    legacy_show = source[source.index("    def _show("):]
    footer_block = legacy_show[
        legacy_show.index('footer = tk.Frame(window, bg=ui_theme.BG_FOOTER)'):
    ]
    footer_block = footer_block[:footer_block.index('window.protocol("WM_DELETE_WINDOW"')]

    assert 'footer.grid(row=2, column=0, sticky="ew")' in footer_block
    assert "ipady=14" not in footer_block
    assert "footer_padding = (11, 11) if compact_action else (14, 14)" in footer_block
    assert "button.pack(pady=footer_padding)" in footer_block
    assert "pady=(14, 14)" in footer_block


def test_medium_multiline_message_uses_scrollable_content():
    message = (
        "当前学历核验模型可能不支持图片输入。\n\n"
        "图片识别需要多模态视觉模型，例如：\n"
        "国外：GPT-4o / GPT-4.1、Claude Sonnet 4、Gemini 2.5 Pro\n"
        "国内：qwen3.7-plus、mimo-v2.5、GLM-5V、Kimi K2.5、MiniMax-M2.7\n\n"
        "PDF 文件使用文本提取，不受此限制。\n\n"
        "可在系统设置的使用中的模型中选择学历核验模型。\n\n"
        "是否仍要尝试识别？"
    )

    assert CenteredMessageBox._message_needs_scroll(message) is True


def test_dialog_height_is_screen_aware_and_bounded():
    assert CenteredMessageBox._max_dialog_height(600) == 492
    assert CenteredMessageBox._max_dialog_height(1080) == 680


def test_dialog_button_width_counts_cjk_glyphs_as_double_width():
    assert CenteredMessageBox._button_text_units("确认并继续") == 10
    assert CenteredMessageBox._button_text_units("Continue") == 8
    assert CenteredMessageBox._button_text_units("AI 确认") == 7


def test_structured_dialog_splits_windows_and_posix_paths_for_display():
    assert CenteredMessageBox._split_display_path(
        r"C:\Users\user\Desktop\backup.zip"
    ) == ("backup.zip", r"C:\Users\user\Desktop")
    assert CenteredMessageBox._split_display_path(
        "/Users/user/Desktop/backup.zip"
    ) == ("backup.zip", "/Users/user/Desktop")


def test_structured_dialog_fallback_keeps_sections_and_full_path():
    message = CenteredMessageBox._structured_fallback_message(
        headline="备份已完成",
        message="已完成校验。",
        metrics=(("岗位", "2 个"), ("候选人", "56 人")),
        file_path=r"C:\Users\user\Desktop\backup.zip",
        notice="此 ZIP 未加密。",
        detail="校验详情",
    )

    assert message.split("\n\n") == [
        "备份已完成",
        "已完成校验。",
        "岗位 2 个，候选人 56 人",
        "保存位置：\nC:\\Users\\user\\Desktop\\backup.zip",
        "此 ZIP 未加密。",
        "详细信息：\n校验详情",
    ]


def test_show_result_uses_file_actions_and_structured_sections():
    box = CenteredMessageBox()
    parent = Mock()
    metrics = (("岗位", "2 个"), ("候选人", "56 人"))
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(
                box, "_show_structured", return_value="open_location"
            ) as show:
        result = box.show_result(
            "数据备份",
            headline="备份已完成",
            metrics=metrics,
            file_path="D:/safe/backup.zip",
            notice="此 ZIP 未加密。",
            parent=parent,
        )

    assert result == "open_location"
    assert show.call_args.kwargs["kind"] == "success"
    assert show.call_args.kwargs["metrics"] == metrics
    assert show.call_args.kwargs["file_path"] == "D:/safe/backup.zip"
    assert show.call_args.kwargs["buttons"] == (
        ("打开所在文件夹", "open_location"),
        ("关闭", "close"),
    )
    assert show.call_args.kwargs["parent"] is parent


def test_show_failure_separates_user_message_from_technical_detail():
    box = CenteredMessageBox()
    parent = Mock()
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show_structured", return_value="close") as show:
        result = box.show_failure(
            "数据备份",
            headline="备份未完成",
            message="没有生成可用备份。",
            detail="磁盘空间不足",
            parent=parent,
        )

    assert result == "close"
    assert show.call_args.kwargs["kind"] == "error"
    assert show.call_args.kwargs["message"] == "没有生成可用备份。"
    assert show.call_args.kwargs["detail"] == "磁盘空间不足"
    assert show.call_args.kwargs["buttons"] == (("关闭", "close"),)


def test_structured_confirmation_preserves_boolean_result_and_action_labels():
    box = CenteredMessageBox()
    parent = Mock()
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show_structured", return_value=True) as show:
        result = box.ask_confirmation(
            "恢复数据备份",
            headline="恢复这份数据备份？",
            message="当前数据将被替换。",
            metrics=(("候选人", "56 人"),),
            notice="执行前会保存恢复点。",
            yes_label="开始恢复",
            no_label="取消",
            parent=parent,
        )

    assert result is True
    assert show.call_args.kwargs["kind"] == "question"
    assert show.call_args.kwargs["buttons"] == (
        ("开始恢复", True),
        ("取消", False),
    )
    assert show.call_args.kwargs["close_value"] is False


def test_show_notice_uses_structured_warning_sections():
    box = CenteredMessageBox()
    parent = Mock()
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show_structured", return_value="close") as show:
        result = box.show_notice(
            "模型能力提醒",
            headline="当前模型可能不支持图片识别",
            message="请切换支持图片输入的模型。",
            detail="当前模型：demo-model",
            parent=parent,
        )

    assert result == "close"
    assert show.call_args.kwargs["kind"] == "warning"
    assert show.call_args.kwargs["detail"] == "当前模型：demo-model"
    assert show.call_args.kwargs["buttons"] == (("关闭", "close"),)


def test_dangerous_confirmation_defaults_to_close_and_uses_danger_tone():
    box = CenteredMessageBox()
    parent = Mock()
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show_structured", return_value=False) as show:
        result = box.ask_confirmation(
            "清空候选人",
            headline="清空全部候选人数据？",
            message="操作前会创建备份。",
            yes_label="清空数据",
            dangerous=True,
            parent=parent,
        )

    assert result is False
    assert show.call_args.kwargs["primary_tone"] == "danger"
    assert show.call_args.kwargs["default_to_close"] is True


def test_structured_choice_preserves_three_way_result():
    box = CenteredMessageBox()
    parent = Mock()
    choices = (
        ("保存并继续", "save"),
        ("不保存", "discard"),
        ("取消", None),
    )
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show_structured", return_value="discard") as show:
        result = box.ask_choice(
            "岗位配置尚未保存",
            headline="当前岗位有未保存的修改",
            message="请选择如何继续。",
            choices=choices,
            parent=parent,
        )

    assert result == "discard"
    assert show.call_args.kwargs["buttons"] == choices
    assert show.call_args.kwargs["close_value"] is None


def test_all_gui_messagebox_calls_use_centered_proxy():
    gui_source = Path("gui_main.py").read_text(encoding="utf-8")
    dialogs_source = Path("gui_dialogs.py").read_text(encoding="utf-8")
    updater_source = Path("updater.py").read_text(encoding="utf-8")

    assert "from ui_messagebox import messagebox" in gui_source
    assert "from ui_messagebox import messagebox" in dialogs_source
    assert "from ui_messagebox import messagebox" in updater_source
    assert "from tkinter import filedialog, font, messagebox, ttk" not in gui_source
    assert "from tkinter import ttk, messagebox" not in dialogs_source
    assert "from tkinter import messagebox" not in updater_source
    assert "headline=(FONT_FAMILY, max(10, self.font_log[1]), 'bold')" in gui_source
    assert "headline=(FONT_FAMILY_SEMIBOLD, self.font_label[1])" not in gui_source
    assert "headline=self.font_section" not in gui_source


def test_centered_messagebox_preserves_tkinter_style_results():
    box = CenteredMessageBox()
    parent = Mock()
    with patch.object(box, "_resolve_parent", return_value=parent), \
            patch.object(box, "_show", side_effect=["ok", False, None]):
        assert box.showinfo("提示", "完成", parent=parent) == "ok"
        assert box.askokcancel("确认", "继续？", parent=parent) is False
        assert box.askyesnocancel("确认", "继续？", parent=parent) is None


def test_update_failures_stay_in_update_dialog_and_restore_actions():
    source = Path("updater.py").read_text(encoding="utf-8")
    block = source[source.index("def show_update_dialog"):]
    block = block[:block.index("\ndef _read_cooldown")]

    assert "def show_update_failure(headline, message, detail=None):" in block
    assert 'text="重试更新",' in block
    assert "command=on_update," in block
    assert 'text="关闭",' in block
    assert "command=on_cancel," in block
    assert "button_frame.pack(pady=(pad(8), pad(20)))" in block
    assert "messagebox.showerror(" not in block
    assert block.count("show_update_failure(") == 13


def test_windows_download_completion_offers_details_and_immediate_install():
    updater_source = Path("updater.py").read_text(encoding="utf-8")
    dialog_block = updater_source[updater_source.index("def show_update_dialog"):]
    dialog_block = dialog_block[:dialog_block.index("\ndef _read_cooldown")]
    gui_source = Path("gui_main.py").read_text(encoding="utf-8")

    completion_at = dialog_block.index(
        'progress_label.configure(text="下载完成，新版本已准备就绪")'
    )
    details_at = dialog_block.index('text="升级内容"', completion_at)
    install_at = dialog_block.index('text="立即安装"', details_at)
    launch_at = dialog_block.index("success, error = update_windows(")
    exit_at = dialog_block.index("exit_for_update(root)", launch_at)

    assert completion_at < details_at < install_at
    assert launch_at < exit_at
    assert "command=show_update_details" in dialog_block
    assert "command=install_downloaded_update" in dialog_block
    assert "def show_update_details():" in dialog_block
    assert "render_changelog_text(" in dialog_block
    assert '.pack(side="bottom", pady=(pad(8), pad(18)))' in dialog_block
    assert "content_frame.pack_forget()" in dialog_block
    assert "_windows_update_cache_dir(result[\"latest\"])" in dialog_block
    assert 'partial_exe = cache_dir / "BOSS_ResumeFilter_new.part.exe"' in dialog_block
    assert "os.replace(partial_exe, cached_exe)" in dialog_block
    assert "cached_update_path = result.get(\"cached_update_path\")" in dialog_block
    assert "shutil.rmtree" not in dialog_block
    assert "稍后重新打开应用也无需再次下载" in dialog_block
    assert "int(230 * height_scale)" in dialog_block
    assert "int(600 * layout_scale)" in dialog_block
    assert "int(260 * height_scale)" in dialog_block
    assert "messagebox.ask_confirmation(" not in dialog_block
    assert 'dialog.protocol("WM_DELETE_WINDOW", on_cancel)' in dialog_block
    assert 'dialog.bind(\'<Escape>\', lambda e: on_cancel())' in dialog_block
    assert 'old_version=result["current"]' in dialog_block
    assert 'sys.argv[1] == "--apply-windows-update"' in gui_source
    assert "updater.run_windows_update_helper(sys.argv[2])" in gui_source


def test_update_helper_reuses_the_main_window_search_icon():
    updater_source = Path("updater.py").read_text(encoding="utf-8")
    helper_block = updater_source[
        updater_source.index("def run_windows_update_helper"):
        updater_source.index("\ndef update_windows")
    ]
    gui_source = Path("gui_main.py").read_text(encoding="utf-8")
    main_icon_block = gui_source[
        gui_source.index("    def _set_window_icon(self):"):
        gui_source.index("\n    def show_stat_detail", gui_source.index(
            "    def _set_window_icon(self):"
        ))
    ]

    assert "from gui_main import _set_search_window_icon" in helper_block
    assert "_set_search_window_icon(root)" in helper_block
    assert "iconbitmap(default=sys.executable)" not in helper_block
    assert "self._icon_photo = _set_search_window_icon(self.root)" in main_icon_block


def test_update_check_failures_separate_user_guidance_from_detail():
    source = Path("updater.py").read_text(encoding="utf-8")
    previous_failure = source[
        source.index("def notify_previous_update_failure"):
        source.index("\ndef _clean_pyinstaller_environment")
    ]
    check_failure = source[
        source.index("def check_and_update_gui"):
        source.index("\ndef get_cached_release_notes")
    ]

    assert "messagebox.show_failure(" in previous_failure
    assert "detail=detail or None" in previous_failure
    assert "messagebox.show_failure(" in check_failure
    assert "detail=result['error']" in check_failure
