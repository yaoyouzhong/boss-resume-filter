"""Parent-centered modal message boxes with tkinter-compatible results."""

import tkinter as tk
from tkinter import messagebox as _native_messagebox, ttk


class CenteredMessageBox:
    """Drop-in subset of tkinter.messagebox with reliable parent centering."""

    _ICON_STYLE = {
        "info": ("i", "#2563EB"),
        "warning": ("!", "#D97706"),
        "error": ("x", "#DC2626"),
        "question": ("?", "#2563EB"),
    }

    def __init__(self):
        self._window_placer = None
        self._headline_font = ("Microsoft YaHei UI", 13, "bold")
        self._message_font = ("Microsoft YaHei UI", 13)
        self._button_font = ("Microsoft YaHei UI", 13)

    def __getattr__(self, name):
        return getattr(_native_messagebox, name)

    def set_window_placer(self, placer):
        """Use the application's monitor-aware window placement helper."""
        self._window_placer = placer

    def set_ui_fonts(self, *, headline, message, button):
        """Keep modal typography aligned with the application's scaled fonts."""
        self._headline_font = headline
        self._message_font = message
        self._button_font = button

    @staticmethod
    def _font_with_delta(font_spec, delta):
        if not delta or not isinstance(font_spec, (tuple, list)) or len(font_spec) < 2:
            return font_spec
        adjusted = list(font_spec)
        try:
            adjusted[1] = max(8, int(adjusted[1]) + int(delta))
        except (TypeError, ValueError):
            return font_spec
        return tuple(adjusted)

    @staticmethod
    def _resolve_parent(parent):
        parent = parent or getattr(tk, "_default_root", None)
        if parent is None:
            return None
        try:
            if not parent.winfo_exists():
                return None
            return parent.winfo_toplevel()
        except tk.TclError:
            return None

    @staticmethod
    def _fallback_place(window, width, height, parent):
        parent.update_idletasks()
        window.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        x = min(max(0, x), max(0, screen_width - width))
        y = min(max(0, y), max(0, screen_height - height))
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _place(self, window, width, height, parent):
        if self._window_placer is not None:
            self._window_placer(window, width, height, parent=parent)
            return
        self._fallback_place(window, width, height, parent)

    def _show(
        self,
        title,
        message,
        *,
        kind,
        buttons,
        close_value,
        parent=None,
        detail=None,
        headline=None,
        show_icon=True,
        min_width=460,
        font_delta=0,
        content_bottom_padding=8,
    ):
        parent = self._resolve_parent(parent)
        if parent is None:
            return None

        message = str(message or "")
        if detail:
            message = f"{message}\n\n{detail}"
        content_wraplength = max(500, min(640, int(min_width) - 60))
        headline_font = self._font_with_delta(self._headline_font, font_delta)
        message_font = self._font_with_delta(self._message_font, font_delta)
        button_font = self._font_with_delta(self._button_font, font_delta)

        window = tk.Toplevel(parent)
        window.title(str(title or "提示"))
        window.transient(parent)
        window.resizable(False, False)
        window.withdraw()
        window.configure(bg="#FFFFFF")

        body = tk.Frame(window, bg="#FFFFFF")
        body.pack(
            fill="both",
            expand=True,
            padx=26,
            pady=(24, max(0, int(content_bottom_padding))),
        )
        if show_icon:
            symbol, color = self._ICON_STYLE[kind]
            tk.Label(
                body,
                text=symbol,
                font=headline_font,
                fg=color,
                bg="#FFFFFF",
                width=2,
                anchor="n",
            ).pack(side="left", anchor="n", padx=(0, 12))

        content = tk.Frame(body, bg="#FFFFFF")
        content.pack(side="left", fill="both", expand=True)
        if headline:
            tk.Label(
                content,
                text=str(headline),
                font=headline_font,
                fg="#111827",
                bg="#FFFFFF",
                justify="left",
                anchor="w",
                wraplength=content_wraplength,
            ).pack(fill="x", anchor="w", pady=(0, 16))
        is_long = len(message) > 600 or message.count("\n") > 11
        if is_long:
            text_frame = tk.Frame(content, bg="#FFFFFF")
            text_frame.pack(fill="both", expand=True)
            text_widget = tk.Text(
                text_frame,
                width=64,
                height=14,
                wrap="word",
                font=message_font,
                bg="#FFFFFF",
                fg="#1F2937",
                relief="flat",
                borderwidth=0,
                highlightthickness=0,
            )
            scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=text_widget.yview)
            text_widget.configure(yscrollcommand=scrollbar.set)
            text_widget.insert("1.0", message)
            text_widget.configure(state="disabled")
            text_widget.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
        else:
            tk.Label(
                content,
                text=message,
                font=message_font,
                fg="#1F2937",
                bg="#FFFFFF",
                justify="left",
                anchor="w",
                wraplength=content_wraplength,
            ).pack(fill="both", expand=True, anchor="w")

        tk.Frame(window, bg="#E5E7EB", height=1).pack(fill="x")
        footer = tk.Frame(window, bg="#F7F8FA")
        footer.pack(fill="x", padx=0, pady=0)

        result = {"value": close_value}
        previous_grab = parent.grab_current()
        button_widgets = []

        def finish(value):
            result["value"] = value
            try:
                window.grab_release()
            except tk.TclError:
                pass
            window.destroy()
            if previous_grab is not None:
                try:
                    if previous_grab.winfo_exists():
                        previous_grab.grab_set()
                except tk.TclError:
                    pass

        button_style = ttk.Style(window)
        button_style.configure(
            "CenteredMessageBox.TButton",
            font=button_font,
            padding=(15, 8),
        )
        equal_button_width = max(8, max(len(str(label)) for label, _value in buttons) + 2)
        single_button = len(buttons) == 1
        for label, value in reversed(buttons):
            button = ttk.Button(
                footer,
                text=label,
                command=lambda selected=value: finish(selected),
                style="CenteredMessageBox.TButton",
                width=equal_button_width,
            )
            if single_button:
                button.pack(pady=(14, 14))
            else:
                button.pack(
                    side="right",
                    padx=(8, 0) if button_widgets else (8, 26),
                    pady=(14, 14),
                )
            button_widgets.insert(0, button)

        window.protocol("WM_DELETE_WINDOW", lambda: finish(close_value))
        window.bind("<Escape>", lambda _event: finish(close_value))
        window.bind("<Return>", lambda _event: finish(buttons[0][1]))
        window.update_idletasks()
        width = max(int(min_width), min(700, window.winfo_reqwidth()))
        height = max(180, min(560, window.winfo_reqheight()))
        self._place(window, width, height, parent)
        window.deiconify()
        window.lift()
        window.grab_set()
        if button_widgets:
            button_widgets[0].focus_set()
        window.wait_window()
        return result["value"]

    def showinfo(self, title, message, **options):
        headline = options.pop("headline", None)
        show_icon = options.pop("show_icon", True)
        min_width = options.pop("min_width", 460)
        font_delta = options.pop("font_delta", 0)
        content_bottom_padding = options.pop("content_bottom_padding", 8)
        parent = self._resolve_parent(options.get("parent"))
        if parent is None:
            return _native_messagebox.showinfo(title, message, **options)
        self._show(
            title,
            message,
            kind="info",
            buttons=((options.pop("ok_label", "确定"), "ok"),),
            close_value="ok",
            parent=parent,
            detail=options.pop("detail", None),
            headline=headline,
            show_icon=show_icon,
            min_width=min_width,
            font_delta=font_delta,
            content_bottom_padding=content_bottom_padding,
        )
        return "ok"

    def showwarning(self, title, message, **options):
        headline = options.pop("headline", None)
        show_icon = options.pop("show_icon", True)
        min_width = options.pop("min_width", 460)
        font_delta = options.pop("font_delta", 0)
        content_bottom_padding = options.pop("content_bottom_padding", 8)
        parent = self._resolve_parent(options.get("parent"))
        if parent is None:
            return _native_messagebox.showwarning(title, message, **options)
        self._show(
            title,
            message,
            kind="warning",
            buttons=((options.pop("ok_label", "确定"), "ok"),),
            close_value="ok",
            parent=parent,
            detail=options.pop("detail", None),
            headline=headline,
            show_icon=show_icon,
            min_width=min_width,
            font_delta=font_delta,
            content_bottom_padding=content_bottom_padding,
        )
        return "ok"

    def showerror(self, title, message, **options):
        headline = options.pop("headline", None)
        show_icon = options.pop("show_icon", True)
        min_width = options.pop("min_width", 460)
        font_delta = options.pop("font_delta", 0)
        content_bottom_padding = options.pop("content_bottom_padding", 8)
        parent = self._resolve_parent(options.get("parent"))
        if parent is None:
            return _native_messagebox.showerror(title, message, **options)
        self._show(
            title,
            message,
            kind="error",
            buttons=((options.pop("ok_label", "确定"), "ok"),),
            close_value="ok",
            parent=parent,
            detail=options.pop("detail", None),
            headline=headline,
            show_icon=show_icon,
            min_width=min_width,
            font_delta=font_delta,
            content_bottom_padding=content_bottom_padding,
        )
        return "ok"

    def askyesno(self, title, message, **options):
        headline = options.pop("headline", None)
        show_icon = options.pop("show_icon", True)
        min_width = options.pop("min_width", 460)
        font_delta = options.pop("font_delta", 0)
        parent = self._resolve_parent(options.get("parent"))
        if parent is None:
            return _native_messagebox.askyesno(title, message, **options)
        return bool(self._show(
            title,
            message,
            kind="question",
            buttons=(
                (options.pop("yes_label", "是"), True),
                (options.pop("no_label", "否"), False),
            ),
            close_value=False,
            parent=parent,
            detail=options.pop("detail", None),
            headline=headline,
            show_icon=show_icon,
            min_width=min_width,
            font_delta=font_delta,
        ))

    def askokcancel(self, title, message, **options):
        headline = options.pop("headline", None)
        show_icon = options.pop("show_icon", True)
        min_width = options.pop("min_width", 460)
        parent = self._resolve_parent(options.get("parent"))
        if parent is None:
            return _native_messagebox.askokcancel(title, message, **options)
        return bool(self._show(
            title,
            message,
            kind="question",
            buttons=(
                (options.pop("ok_label", "确定"), True),
                (options.pop("cancel_label", "取消"), False),
            ),
            close_value=False,
            parent=parent,
            detail=options.pop("detail", None),
            headline=headline,
            show_icon=show_icon,
            min_width=min_width,
        ))

    def askretrycancel(self, title, message, **options):
        headline = options.pop("headline", None)
        show_icon = options.pop("show_icon", True)
        min_width = options.pop("min_width", 460)
        parent = self._resolve_parent(options.get("parent"))
        if parent is None:
            return _native_messagebox.askretrycancel(title, message, **options)
        return bool(self._show(
            title,
            message,
            kind="question",
            buttons=(
                (options.pop("retry_label", "重试"), True),
                (options.pop("cancel_label", "取消"), False),
            ),
            close_value=False,
            parent=parent,
            detail=options.pop("detail", None),
            headline=headline,
            show_icon=show_icon,
            min_width=min_width,
        ))

    def askquestion(self, title, message, **options):
        return "yes" if self.askyesno(title, message, **options) else "no"

    def askyesnocancel(self, title, message, **options):
        headline = options.pop("headline", None)
        show_icon = options.pop("show_icon", True)
        min_width = options.pop("min_width", 460)
        parent = self._resolve_parent(options.get("parent"))
        if parent is None:
            return _native_messagebox.askyesnocancel(title, message, **options)
        return self._show(
            title,
            message,
            kind="question",
            buttons=(
                (options.pop("yes_label", "是"), True),
                (options.pop("no_label", "否"), False),
                (options.pop("cancel_label", "取消"), None),
            ),
            close_value=None,
            parent=parent,
            detail=options.pop("detail", None),
            headline=headline,
            show_icon=show_icon,
            min_width=min_width,
        )


messagebox = CenteredMessageBox()
