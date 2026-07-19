from gui_main import (
    _calculate_effective_scale,
    _calculate_system_dpi_aware_font_scale,
    _calculate_system_dpi_aware_scale,
    _enable_high_dpi_awareness,
    _is_system_dpi_aware_scale,
    _place_main_window,
    _place_window_centered,
    _resolve_display_scale,
    _show_main_window_centered,
)
from updater import _place_dialog_centered


class FakeWindow:
    def __init__(
        self,
        width=100,
        height=100,
        req_width=None,
        req_height=None,
        screen_width=1920,
        screen_height=1080,
        root_x=0,
        root_y=0,
        x=None,
        y=None,
    ):
        self._width = width
        self._height = height
        self._req_width = req_width if req_width is not None else width
        self._req_height = req_height if req_height is not None else height
        self._screen_width = screen_width
        self._screen_height = screen_height
        self._root_x = root_x
        self._root_y = root_y
        self._x = root_x if x is None else x
        self._y = root_y if y is None else y
        self.geometry_value = None
        self.alpha_values = []
        self.after_calls = []
        self.deiconified = False

    def update_idletasks(self):
        pass

    def winfo_width(self):
        return self._width

    def winfo_height(self):
        return self._height

    def winfo_reqwidth(self):
        return self._req_width

    def winfo_reqheight(self):
        return self._req_height

    def winfo_screenwidth(self):
        return self._screen_width

    def winfo_screenheight(self):
        return self._screen_height

    def winfo_rootx(self):
        return self._root_x

    def winfo_rooty(self):
        return self._root_y

    def winfo_x(self):
        return self._x

    def winfo_y(self):
        return self._y

    def geometry(self, value):
        self.geometry_value = value

    def attributes(self, name, value):
        if name == "-alpha":
            self.alpha_values.append(value)

    def after(self, delay_ms, callback):
        self.after_calls.append(delay_ms)
        callback()

    def deiconify(self):
        self.deiconified = True


def test_place_window_centered_clamps_oversized_window_to_screen_ratio():
    window = FakeWindow(screen_width=1000, screen_height=800)

    width, height, x, y = _place_window_centered(window, 1600, 1000, screen_width=1000, screen_height=800)

    assert (width, height) == (900, 680)
    assert (x, y) == (50, 60)
    assert window.geometry_value == "900x680+50+60"


def test_place_window_centered_keeps_parent_center_inside_visible_screen():
    parent = FakeWindow(width=500, height=300, screen_width=1000, screen_height=800, root_x=-200, root_y=-100)
    dialog = FakeWindow(screen_width=1000, screen_height=800)

    width, height, x, y = _place_window_centered(
        dialog,
        400,
        300,
        parent=parent,
        screen_width=1000,
        screen_height=800,
    )

    assert (width, height) == (400, 300)
    assert (x, y) == (0, 0)
    assert dialog.geometry_value == "400x300+0+0"


def test_place_window_centered_uses_parent_outer_window_origin():
    parent = FakeWindow(width=1200, height=800, root_x=100, root_y=50, y=0)
    dialog = FakeWindow()

    width, height, x, y = _place_window_centered(
        dialog,
        940,
        620,
        parent=parent,
        screen_width=1920,
        screen_height=1080,
    )

    assert (width, height) == (940, 620)
    assert (x, y) == (230, 90)
    assert dialog.geometry_value == "940x620+230+90"


def test_place_window_centered_uses_requested_size_when_hidden_window_reports_one_pixel():
    parent = FakeWindow(width=1200, height=800, root_x=100, root_y=50)
    dialog = FakeWindow(width=1, height=1, req_width=940, req_height=620)

    width, height, x, y = _place_window_centered(
        dialog,
        parent=parent,
        screen_width=1920,
        screen_height=1080,
    )

    assert (width, height) == (940, 620)
    assert (x, y) == (230, 140)
    assert dialog.geometry_value == "940x620+230+140"


def test_place_window_centered_uses_non_zero_screen_origin():
    window = FakeWindow()

    width, height, x, y = _place_window_centered(
        window,
        1200,
        800,
        screen_left=1920,
        screen_top=0,
        screen_width=2560,
        screen_height=1440,
    )

    assert (width, height) == (1200, 800)
    assert (x, y) == (2600, 320)
    assert window.geometry_value == "1200x800+2600+320"


def test_place_window_centered_formats_negative_secondary_monitor_origin():
    window = FakeWindow()

    width, height, x, y = _place_window_centered(
        window,
        1000,
        700,
        screen_left=-1920,
        screen_top=-120,
        screen_width=1920,
        screen_height=1080,
    )

    assert (width, height, x, y) == (1000, 700, -1460, 70)
    assert window.geometry_value == "1000x700-1460+70"


def test_place_main_window_uses_fixed_startup_monitor_area():
    window = FakeWindow(width=1200, height=800, screen_width=4480, screen_height=1440)

    width, height, x, y = _place_main_window(window, (1920, 0, 2560, 1440))

    assert (width, height) == (1200, 800)
    assert (x, y) == (2600, 320)
    assert window.geometry_value == "1200x800+2600+320"


def test_place_main_window_ignores_physical_monitor_area_when_tk_uses_virtual_pixels():
    window = FakeWindow(width=1200, height=800, screen_width=2194, screen_height=1234)

    width, height, x, y = _place_main_window(window, (0, 0, 3840, 2160))

    assert (width, height) == (1200, 800)
    assert (x, y) == (497, 217)
    assert window.geometry_value == "1200x800+497+217"


def test_show_main_window_centered_reveals_after_delayed_centering(monkeypatch=None):
    window = FakeWindow(width=1200, height=800, screen_width=4480, screen_height=1440)

    _show_main_window_centered(window, (1920, 0, 2560, 1440))

    assert window.deiconified
    assert window.alpha_values in ([], [0.0, 1.0])
    assert window.after_calls == [50, 250]
    assert window.geometry_value == "1200x800+2600+320"


def test_update_dialog_centering_clamps_to_visible_screen():
    parent = FakeWindow(width=300, height=200, screen_width=800, screen_height=600, root_x=700, root_y=500)
    dialog = FakeWindow(screen_width=800, screen_height=600)

    _place_dialog_centered(dialog, parent, 400, 300)

    assert dialog.geometry_value == "400x300+400+300"


def test_effective_scale_grows_on_large_standard_dpi_screen():
    scale = _calculate_effective_scale(1.0, 3840, 2160, platform="win32")

    assert 1.63 < scale < 1.65


def test_effective_scale_uses_compact_target_on_2560x1440_screen():
    scale = _calculate_effective_scale(1.0, 2560, 1440, platform="win32")

    assert 1.08 < scale < 1.11


def test_effective_scale_reduces_high_dpi_150_percent():
    # 150% > 130%，触发 high_dpi_reduction: 1.5 × 0.50 = 0.75，再受 0.85 下限保护
    scale = _calculate_effective_scale(1.5, 2560, 1440, platform="win32")
    assert 0.84 < scale < 0.86


def test_effective_scale_reduces_4k_175():
    # 175% > 130%，触发 high_dpi_reduction: 1.75 × 0.50 = 0.875
    scale = _calculate_effective_scale(1.75, 3840, 2160, platform="win32")
    assert 0.87 < scale < 0.88


def test_effective_scale_no_reduction_below_130_percent():
    # 125% ≤ 130%，不触发缩减
    scale = _calculate_effective_scale(1.25, 1920, 1200, platform="win32")
    assert 1.24 < scale < 1.26


def test_effective_scale_keeps_existing_default_on_normal_screen():
    scale = _calculate_effective_scale(1.0, 1920, 1080, platform="win32")
    assert scale == 1.0


def test_resolve_display_scale_uses_physical_virtual_ratio_in_dpi_unaware_mode():
    scale = _resolve_display_scale(1.0, 3840, 2194)

    assert 1.74 < scale < 1.76


def test_resolve_display_scale_prefers_tk_dpi_in_system_dpi_aware_mode():
    scale = _resolve_display_scale(1.75, 3840, 3840)

    assert scale == 1.75


def test_system_dpi_aware_scale_is_larger_than_dpi_unaware_reduction_on_4k_175():
    scale = _calculate_system_dpi_aware_scale(1.75, 3840, 2160)

    assert 1.58 < scale < 1.59


def test_system_dpi_aware_font_scale_is_restrained_below_native_4k_175():
    scale = _calculate_system_dpi_aware_font_scale(1.75)

    assert 1.08 < scale < 1.09


def test_system_dpi_aware_detection_requires_physical_pixels_and_native_dpi():
    assert _is_system_dpi_aware_scale(1.75, 3840, 3840)
    assert not _is_system_dpi_aware_scale(1.0, 3840, 2194)


def test_enable_high_dpi_awareness_is_noop_off_windows():
    _enable_high_dpi_awareness()
