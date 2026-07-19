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


def test_gui_uses_readable_dedicated_modal_fonts():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    setup = source[source.index("messagebox.set_ui_fonts("):]
    setup = setup[:setup.index("# 设置 Combobox")]

    assert "modal_font_size = max(9, self.font_log[1])" in source
    assert "headline=(FONT_FAMILY, max(10, self.font_log[1]), 'bold')" in setup
    assert setup.count("modal_font_size") == 2
    assert "message=self.font_label" not in setup


def test_api_probe_failure_copy_does_not_claim_connectivity_succeeded():
    source = Path("gui_main.py").read_text(encoding="utf-8")

    assert "模型连接或兼容性验证未通过" in source
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


def test_centered_messagebox_centers_buttons_vertically_in_footer():
    source = Path("ui_messagebox.py").read_text(encoding="utf-8")
    footer_block = source[source.index('footer = tk.Frame(window, bg=ui_theme.BG_FOOTER)'):]
    footer_block = footer_block[:footer_block.index('window.protocol("WM_DELETE_WINDOW"')]

    assert 'footer.grid(row=2, column=0, sticky="ew")' in footer_block
    assert "ipady=14" not in footer_block
    assert footer_block.count("pady=(14, 14)") == 2


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
