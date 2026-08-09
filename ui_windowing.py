"""Shared Tk window placement helpers with no application-module dependency."""
from __future__ import annotations

import sys
import tkinter as tk
from typing import Any


def clamp(value: float, min_value: float, max_value: float) -> float:
    """Clamp a numeric value to the inclusive bounds."""
    return max(min_value, min(max_value, value))


def get_windows_monitor_area(
    window: Any = None,
    parent: Any = None,
) -> tuple[int, int, int, int] | None:
    """Return the relevant Windows monitor work area as left/top/width/height."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [
            wintypes.HMONITOR,
            ctypes.POINTER(MONITORINFO),
        ]
        user32.GetMonitorInfoW.restype = wintypes.BOOL

        monitor = None
        if parent is not None:
            parent.update_idletasks()
            point = wintypes.POINT(
                parent.winfo_rootx() + parent.winfo_width() // 2,
                parent.winfo_rooty() + parent.winfo_height() // 2,
            )
            monitor = user32.MonitorFromPoint(point, 2)
        else:
            point = wintypes.POINT()
            if user32.GetCursorPos(ctypes.byref(point)):
                monitor = user32.MonitorFromPoint(point, 2)
            if not monitor and window is not None:
                monitor = user32.MonitorFromWindow(window.winfo_id(), 2)

        if not monitor:
            return None

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None

        work = info.rcWork
        return work.left, work.top, work.right - work.left, work.bottom - work.top
    except (ImportError, OSError, AttributeError, tk.TclError):
        return None


def bind_parent_center_correction(
    window: Any,
    parent: Any,
    width: int,
    height: int,
    screen_left: int,
    screen_top: int,
    screen_width: int,
    screen_height: int,
) -> None:
    """Correct parent-relative centering once Tk exposes actual mapped geometry."""
    try:
        if getattr(window, "_parent_center_correction_bound", False):
            return
        window._parent_center_correction_bound = True

        def correct_once(event: Any = None) -> None:
            _ = event
            try:
                window.unbind(
                    "<Map>",
                    getattr(window, "_parent_center_correction_bind_id", ""),
                )
            except tk.TclError:
                pass
            try:
                parent.update_idletasks()
                window.update_idletasks()
                parent_center_x = parent.winfo_rootx() + parent.winfo_width() // 2
                parent_center_y = parent.winfo_rooty() + parent.winfo_height() // 2
                window_center_x = window.winfo_rootx() + window.winfo_width() // 2
                window_center_y = window.winfo_rooty() + window.winfo_height() // 2
                dx = parent_center_x - window_center_x
                dy = parent_center_y - window_center_y
                if abs(dx) < 1 and abs(dy) < 1:
                    return
                try:
                    new_x = window.winfo_x() + dx
                    new_y = window.winfo_y() + dy
                except (tk.TclError, AttributeError):
                    new_x = window.winfo_rootx() + dx
                    new_y = window.winfo_rooty() + dy
                max_x = screen_left + max(0, screen_width - width)
                max_y = screen_top + max(0, screen_height - height)
                new_x = min(max(screen_left, new_x), max_x)
                new_y = min(max(screen_top, new_y), max_y)
                window.geometry(
                    f"{width}x{height}{int(new_x):+d}{int(new_y):+d}"
                )
            except (tk.TclError, AttributeError):
                return

        bind_id = window.bind("<Map>", correct_once, add="+")
        window._parent_center_correction_bind_id = bind_id
        window.after(50, correct_once)
    except (tk.TclError, AttributeError):
        return


def place_window_centered(
    window: Any,
    width: int | None = None,
    height: int | None = None,
    parent: Any = None,
    screen_width: int | None = None,
    screen_height: int | None = None,
    screen_left: int | None = None,
    screen_top: int | None = None,
    max_width_ratio: float = 0.9,
    max_height_ratio: float = 0.85,
) -> tuple[int, int, int, int]:
    """Center a Tk window and clamp the result to the visible work area."""
    if parent is not None:
        parent.update_idletasks()
    window.update_idletasks()

    current_width = int(window.winfo_width() or 0)
    current_height = int(window.winfo_height() or 0)
    req_width = int(window.winfo_reqwidth() or 0)
    req_height = int(window.winfo_reqheight() or 0)
    width = int(width or (current_width if current_width > 1 else req_width))
    height = int(height or (current_height if current_height > 1 else req_height))
    monitor_area = None
    if screen_width is None or screen_height is None:
        monitor_area = get_windows_monitor_area(window, parent)

    if monitor_area is not None:
        screen_left, screen_top, screen_width, screen_height = monitor_area
    else:
        screen_left = int(screen_left or 0)
        screen_top = int(screen_top or 0)
        screen_width = int(screen_width or window.winfo_screenwidth())
        screen_height = int(screen_height or window.winfo_screenheight())

    if width > screen_width:
        width = max(1, int(screen_width * max_width_ratio))
    if height > screen_height:
        height = max(1, int(screen_height * max_height_ratio))

    if parent is not None:
        try:
            parent_x = parent.winfo_x()
            parent_y = parent.winfo_y()
        except (tk.TclError, AttributeError):
            parent_x = parent.winfo_rootx()
            parent_y = parent.winfo_rooty()
        x = parent_x + (parent.winfo_width() - width) // 2
        y = parent_y + (parent.winfo_height() - height) // 2
    else:
        x = screen_left + (screen_width - width) // 2
        y = screen_top + (screen_height - height) // 2

    min_x = screen_left
    min_y = screen_top
    max_x = screen_left + max(0, screen_width - width)
    max_y = screen_top + max(0, screen_height - height)
    x = min(max(min_x, x), max_x)
    y = min(max(min_y, y), max_y)
    window.geometry(f"{width}x{height}{x:+d}{y:+d}")
    if parent is not None:
        bind_parent_center_correction(
            window,
            parent,
            width,
            height,
            screen_left,
            screen_top,
            screen_width,
            screen_height,
        )
    return width, height, x, y
