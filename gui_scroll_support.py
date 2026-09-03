"""Cross-platform Canvas scrolling and macOS Cocoa touchpad support."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from typing import Any, Protocol

from gui_app_shell import PageIndex


class ScrollSupportHost(Protocol):
    """Explicit GUI surface used by cross-platform scroll routing."""

    root: tk.Misc
    current_page_index: int
    _over_text_widget: bool
    config_canvas: tk.Canvas
    run_canvas: tk.Canvas
    education_canvas: tk.Canvas
    api_canvas: tk.Canvas


class ScrollSupport:
    """Own generic scroll containers, wheel binding, routing, and Cocoa hook state."""

    _cocoa_hook_installed = False
    _cocoa_refs: dict[str, Any] = {}

    def __init__(self, host: ScrollSupportHost) -> None:
        self.host = host

    @staticmethod
    def delta_to_units(delta: int | float) -> int:
        """Normalize Windows wheel and macOS touchpad deltas to Canvas units."""
        if sys.platform == "darwin":
            return -1 if delta > 0 else 1
        return int(-1 * (delta / 120))

    @staticmethod
    def bind_bounded_spinbox_mousewheel(
        spinbox: tk.Misc,
        variable: Any,
        minimum: int,
        maximum: int,
    ) -> None:
        """Adjust a numeric Spinbox by one step without scrolling its page."""

        def on_wheel(event: tk.Event) -> str:
            delta = getattr(event, "delta", 0)
            button = getattr(event, "num", None)
            if delta > 0 or button == 4:
                direction = 1
            elif delta < 0 or button == 5:
                direction = -1
            else:
                return "break"
            try:
                current = int(variable.get())
            except (TypeError, ValueError):
                current = minimum
            variable.set(str(max(minimum, min(maximum, current + direction))))
            return "break"

        spinbox.bind("<MouseWheel>", on_wheel)
        if sys.platform != "win32":
            spinbox.bind("<Button-4>", on_wheel)
            spinbox.bind("<Button-5>", on_wheel)

    @staticmethod
    def create_scroll_container(
        parent: tk.Misc,
        bg_color: str,
        *,
        auto_hide_scrollbar: bool = False,
        content_style: str = "TFrame",
    ) -> tuple[tk.Canvas, ttk.Frame]:
        """Create a Canvas-backed scrolling container and its content frame."""
        canvas = tk.Canvas(parent, bg=bg_color, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        container = ttk.Frame(canvas, style=content_style)
        canvas_window = canvas.create_window((0, 0), window=container, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        sync_after_id = None

        if auto_hide_scrollbar:

            def sync_layout() -> None:
                nonlocal sync_after_id
                sync_after_id = None
                try:
                    viewport_height = max(1, canvas.winfo_height())
                    requested_height = max(1, container.winfo_reqheight())
                    content_height = max(requested_height, viewport_height)
                    canvas.itemconfig(canvas_window, height=content_height)
                    canvas.configure(
                        scrollregion=(0, 0, canvas.winfo_width(), content_height)
                    )
                    has_overflow = requested_height > viewport_height + 8
                    if has_overflow and not scrollbar.winfo_manager():
                        scrollbar.pack(side="right", fill="y")
                    elif not has_overflow:
                        if scrollbar.winfo_manager():
                            scrollbar.pack_forget()
                        canvas.yview_moveto(0)
                except tk.TclError:
                    return

            def schedule_sync(_event: tk.Event | None = None) -> None:
                nonlocal sync_after_id
                if sync_after_id is not None:
                    try:
                        canvas.after_cancel(sync_after_id)
                    except tk.TclError:
                        return
                sync_after_id = canvas.after_idle(sync_layout)

            container.bind("<Configure>", schedule_sync)
            canvas._schedule_overflow_sync = schedule_sync
        else:
            container.bind(
                "<Configure>",
                lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
            )

        def on_canvas_configure(event: tk.Event) -> None:
            canvas.itemconfig(canvas_window, width=event.width)
            if auto_hide_scrollbar:
                schedule_sync()

        canvas.bind("<Configure>", on_canvas_configure)
        canvas.pack(side="left", fill="both", expand=True)
        if not auto_hide_scrollbar:
            scrollbar.pack(side="right", fill="y")
        return canvas, container

    def bind_mousewheel(self, canvas: tk.Canvas, parent_frame: tk.Misc) -> None:
        """Bind a Canvas wheel handler to its non-scrollable descendants once."""
        if getattr(canvas, "_mousewheel_bound", False):
            return

        def on_wheel(event: tk.Event) -> str | None:
            delta = getattr(event, "delta", 0)
            if delta:
                units = self.delta_to_units(delta)
            else:
                button = getattr(event, "num", None)
                if button == 4:
                    units = -1
                elif button == 5:
                    units = 1
                else:
                    return None
            if units:
                canvas.yview_scroll(units, "units")
            return "break"

        skip_types = (
            ttk.Spinbox,
            ttk.Combobox,
            ttk.Scrollbar,
            tk.Text,
            tk.Entry,
            tk.Listbox,
        )

        def bind_recursive(widget: tk.Misc) -> None:
            if isinstance(widget, skip_types) or hasattr(widget, "identify_region"):
                return
            widget.bind("<MouseWheel>", on_wheel)
            if sys.platform != "win32":
                widget.bind("<Button-4>", on_wheel)
                widget.bind("<Button-5>", on_wheel)
            for child in widget.winfo_children():
                bind_recursive(child)

        canvas.bind("<MouseWheel>", on_wheel)
        if sys.platform != "win32":
            canvas.bind("<Button-4>", on_wheel)
            canvas.bind("<Button-5>", on_wheel)
        bind_recursive(parent_frame)
        canvas._mousewheel_bound = True

    def setup_cocoa_scroll_hook(self) -> None:
        """Install the macOS Tk 9 Cocoa touchpad hook, with silent fallback."""
        if ScrollSupport._cocoa_hook_installed:
            return
        try:
            import ctypes
            import ctypes.util

            objc_path = ctypes.util.find_library("objc")
            if not objc_path:
                return
            objc = ctypes.cdll.LoadLibrary(objc_path)
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.class_getInstanceMethod.restype = ctypes.c_void_p
            objc.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            objc.method_getImplementation.restype = ctypes.c_void_p
            objc.method_getImplementation.argtypes = [ctypes.c_void_p]
            objc.method_setImplementation.restype = ctypes.c_void_p
            objc.method_setImplementation.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            objc.objc_msgSend.restype = ctypes.c_void_p
            objc.objc_msgSend.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]

            sel_scroll = objc.sel_registerName(b"scrollWheel:")
            sel_shared = objc.sel_registerName(b"sharedApplication")
            sel_keywin = objc.sel_registerName(b"keyWindow")
            sel_cv = objc.sel_registerName(b"contentView")
            sel_super = objc.sel_registerName(b"superview")
            sel_is_kind = objc.sel_registerName(b"isKindOfClass:")
            sel_delta_y = objc.sel_registerName(b"scrollingDeltaY")
            cls_nsapp = objc.objc_getClass(b"NSApplication")
            cls_nsview = objc.objc_getClass(b"NSView")
            cls_nssv = objc.objc_getClass(b"NSScrollView")
            if not all([cls_nsapp, cls_nsview, cls_nssv]):
                return

            app = objc.objc_msgSend(cls_nsapp, sel_shared, None)
            if not app:
                self.host.root.after(1000, self.setup_cocoa_scroll_hook)
                return
            key_window = objc.objc_msgSend(app, sel_keywin, None)
            if not key_window:
                self.host.root.after(1000, self.setup_cocoa_scroll_hook)
                return
            content_view = objc.objc_msgSend(key_window, sel_cv, None)
            if not content_view:
                self.host.root.after(1000, self.setup_cocoa_scroll_hook)
                return

            try:
                objc.objc_msgSend_fpret.restype = ctypes.c_double
                objc.objc_msgSend_fpret.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                ]
                msg_send_double = objc.objc_msgSend_fpret
            except AttributeError:
                msg_send_double = ctypes.CFUNCTYPE(
                    ctypes.c_double,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                )(objc.objc_msgSend)
            msg_send_is_kind = ctypes.CFUNCTYPE(
                ctypes.c_bool,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            )(objc.objc_msgSend)
            ScrollSupport._cocoa_refs["app"] = app
            ScrollSupport._cocoa_refs["content_view"] = content_view
            scroll_callback_type = ctypes.CFUNCTYPE(
                None,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            )

            def cocoa_scroll_impl(view: Any, _command: Any, event: Any) -> None:
                try:
                    if getattr(self.host, "_over_text_widget", False):
                        return
                    current_view = view
                    for _ in range(10):
                        superview = objc.objc_msgSend(current_view, sel_super, None)
                        if not superview:
                            break
                        if msg_send_is_kind(superview, sel_is_kind, cls_nssv):
                            return
                        current_view = superview
                    delta_y = msg_send_double(event, sel_delta_y)
                    if delta_y == 0:
                        return
                    self._current_page_canvas(include_settings=True).yview_scroll(
                        -1 if delta_y > 0 else 1,
                        "units",
                    )
                except Exception:
                    pass

            scroll_callback = scroll_callback_type(cocoa_scroll_impl)
            callback_pointer = ctypes.cast(scroll_callback, ctypes.c_void_p).value
            method = objc.class_getInstanceMethod(cls_nsview, sel_scroll)
            if not method:
                return
            original_implementation = objc.method_getImplementation(method)
            objc.method_setImplementation(method, callback_pointer)
            ScrollSupport._cocoa_refs["callback"] = scroll_callback
            ScrollSupport._cocoa_refs["orig_impl"] = original_implementation
            ScrollSupport._cocoa_hook_installed = True
        except Exception:
            pass

    def on_mousewheel(self, event: tk.Event) -> str | None:
        """Route a global wheel event to its owning or current page Canvas."""
        widget = event.widget
        if isinstance(
            widget,
            (
                tk.Text,
                tk.Entry,
                tk.Listbox,
                ttk.Scrollbar,
                ttk.Combobox,
                ttk.Spinbox,
            ),
        ) or hasattr(widget, "identify_region"):
            return None

        delta = getattr(event, "delta", 0)
        if delta:
            units = self.delta_to_units(delta)
        else:
            button = getattr(event, "num", None)
            if button == 4:
                units = -1
            elif button == 5:
                units = 1
            else:
                return None
        if not units:
            return None

        target_canvas = self._canvas_for_widget(widget)
        if target_canvas is None:
            target_canvas = self._current_page_canvas(include_settings=False)
        if target_canvas is None:
            return None
        target_canvas.yview_scroll(units, "units")
        return "break"

    def _known_canvases(self) -> tuple[tk.Canvas, ...]:
        """Return only page canvases that have already been created."""
        canvases = (
            getattr(self.host, "config_canvas", None),
            getattr(self.host, "api_canvas", None),
            getattr(self.host, "run_canvas", None),
            getattr(self.host, "education_canvas", None),
        )
        return tuple(canvas for canvas in canvases if canvas is not None)

    def _canvas_for_widget(self, widget: tk.Misc) -> tk.Canvas | None:
        """Walk a widget's parents to find its owning page Canvas."""
        canvases = self._known_canvases()
        if widget in canvases:
            return widget
        try:
            current: tk.Misc | None = widget
            while current is not None:
                parent = current.master
                if parent in canvases:
                    return parent
                current = parent
        except Exception:
            return None
        return None

    def _current_page_canvas(self, *, include_settings: bool) -> tk.Canvas | None:
        """Resolve the lazily-created Canvas associated with the current page."""
        page_canvases = {
            PageIndex.CONFIG: getattr(self.host, "config_canvas", None),
            PageIndex.RUN: getattr(self.host, "run_canvas", None),
            PageIndex.EDUCATION: getattr(self.host, "education_canvas", None),
        }
        if include_settings:
            page_canvases[PageIndex.SETTINGS] = getattr(
                self.host,
                "api_canvas",
                None,
            )
        return page_canvases.get(getattr(self.host, "current_page_index", -1))
