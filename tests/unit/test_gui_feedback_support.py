"""Feedback lifetime and fast sequential tooltip behavior."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gui_feedback_support import FeedbackSupport


def test_replacing_timed_banner_cancels_previous_timer_and_keeps_error_visible():
    host = SimpleNamespace(colors={"text_primary": "black", "text_secondary": "gray"},
                           dpi_scale=1, font_label=("Arial", 10))
    support = FeedbackSupport(host, font_family="Arial")
    page = Mock()
    page.winfo_children.return_value = []
    first, second = Mock(), Mock()
    first.after.return_value = "timer-1"
    with patch("gui_feedback_support.tk.Frame", side_effect=[first, second]), \
         patch("gui_feedback_support.tk.Label"), patch("gui_feedback_support.ttk.Button"):
        support.show_inline_banner(page, "success", "Saved")
        support.show_inline_banner(page, "error", "Retry required")
    first.after_cancel.assert_called_once_with("timer-1")
    first.destroy.assert_called_once_with()
    second.after.assert_not_called()
    assert host._inline_banners[page] is second
    support.hide_inline_banner(page)
    second.destroy.assert_called_once_with()
    assert page not in host._inline_banners


def test_tooltip_delay_is_skipped_only_during_recent_inspection():
    host = SimpleNamespace()
    support = FeedbackSupport(host, font_family="Arial")
    with patch("gui_feedback_support.time.monotonic", return_value=10):
        assert support.tooltip_delay() == 300
        host._tooltip = Mock()
        assert support.tooltip_delay() == 0
        support.hide_tooltip()
    with patch("gui_feedback_support.time.monotonic", return_value=10.5):
        assert support.tooltip_delay() == 0
    with patch("gui_feedback_support.time.monotonic", return_value=11):
        assert support.tooltip_delay() == 300
