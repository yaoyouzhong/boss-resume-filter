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


def test_gui_uses_smaller_dedicated_modal_fonts():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    setup = source[source.index("messagebox.set_ui_fonts("):]
    setup = setup[:setup.index("# 设置 Combobox")]

    assert "self.font_log[1] + 1" in setup
    assert setup.count("self.font_log[1]") == 3
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
    footer_block = source[source.index('footer = tk.Frame(window, bg="#F7F8FA")'):]
    footer_block = footer_block[:footer_block.index('window.protocol("WM_DELETE_WINDOW"')]

    assert 'footer.pack(fill="x", padx=0, pady=0)' in footer_block
    assert "ipady=14" not in footer_block
    assert footer_block.count("pady=(14, 14)") == 2


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
    assert "headline=(FONT_FAMILY, max(10, self.font_log[1] + 1), 'bold')" in gui_source
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
