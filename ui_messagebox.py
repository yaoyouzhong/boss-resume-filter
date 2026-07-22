"""Parent-centered modal message boxes with tkinter-compatible results."""

import math
import tkinter as tk
import unicodedata
from tkinter import messagebox as _native_messagebox, ttk

import ui_theme


class CenteredMessageBox:
    """Drop-in subset of tkinter.messagebox with reliable parent centering."""

    _ICON_STYLE = {
        "info": ("i", ui_theme.PRIMARY),
        "warning": ("!", ui_theme.WARNING),
        "error": ("×", ui_theme.DANGER),
        "question": ("?", ui_theme.PRIMARY),
    }

    @staticmethod
    def _make_icon(parent, kind, size=30):
        """绘制圆形语义图标（有色圆底 + 白色符号），替代裸字母文本。"""
        symbol, color = CenteredMessageBox._ICON_STYLE[kind]
        canvas = tk.Canvas(
            parent, width=size, height=size,
            bg=ui_theme.BG_CARD, highlightthickness=0, bd=0,
        )
        canvas.create_oval(1, 1, size - 1, size - 1, fill=color, outline="")
        canvas.create_text(
            size / 2, size / 2, text=symbol, fill=ui_theme.BG_CARD,
            font=(ui_theme.FONT_FAMILY, int(size * 0.46), "bold"),
        )
        return canvas

    def __init__(self):
        self._window_placer = None
        # 哪些弹窗类型强制显示语义图标（调用方 show_icon 仍可按需覆盖）
        self.icon_kinds = frozenset()
        self._headline_font = (ui_theme.FONT_FAMILY, 13, "bold")
        self._message_font = (ui_theme.FONT_FAMILY, 13)
        self._button_font = (ui_theme.FONT_FAMILY, 13)

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
    def _estimated_visual_lines(message, chars_per_line=34):
        """Estimate wrapped lines so medium multi-line copy becomes scrollable."""
        lines = str(message or "").splitlines() or [""]
        return sum(max(1, math.ceil(len(line) / chars_per_line)) for line in lines)

    @classmethod
    def _message_needs_scroll(cls, message):
        message = str(message or "")
        return len(message) > 360 or cls._estimated_visual_lines(message) > 8

    @staticmethod
    def _max_dialog_height(screen_height):
        """Keep the modal inside the screen while allowing useful text height."""
        return min(680, max(320, round(screen_height * 0.82)))

    @staticmethod
    def _button_text_units(label):
        """Estimate ttk character-width units, counting CJK glyphs as double width."""
        return sum(
            2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
            for char in str(label)
        )

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
        show_icon=False,
        numbered_items=None,
        min_width=460,
        max_width=700,
        font_delta=0,
        content_bottom_padding=8,
        compact_action=False,
    ):
        parent = self._resolve_parent(parent)
        if parent is None:
            return None

        message = str(message or "")
        if detail:
            message = f"{message}\n\n{detail}"
        max_width = max(int(min_width), int(max_width))
        content_wraplength = max(500, min(max_width - 60, int(min_width) - 60))
        item_wraplength = max(460, content_wraplength - 36)
        headline_font = self._font_with_delta(self._headline_font, font_delta)
        message_font = self._font_with_delta(self._message_font, font_delta)
        button_font = self._font_with_delta(self._button_font, font_delta)

        window = tk.Toplevel(parent)
        window.title(str(title or "提示"))
        window.transient(parent)
        window.resizable(False, False)
        window.withdraw()
        window.configure(bg=ui_theme.BG_CARD)

        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(0, weight=1)

        body = tk.Frame(window, bg=ui_theme.BG_CARD)
        body.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=26,
            pady=(24, max(0, int(content_bottom_padding))),
        )
        if show_icon or kind in self.icon_kinds:
            self._make_icon(body, kind).pack(side="left", anchor="n", padx=(0, 12))

        content = tk.Frame(body, bg=ui_theme.BG_CARD)
        content.pack(side="left", fill="both", expand=True)
        if headline:
            tk.Label(
                content,
                text=str(headline),
                font=headline_font,
                fg=ui_theme.TEXT_PRIMARY,
                bg=ui_theme.BG_CARD,
                justify="left",
                anchor="w",
                wraplength=content_wraplength,
            ).pack(fill="x", anchor="w", pady=(0, 16))
        if numbered_items:
            items_frame = tk.Frame(content, bg=ui_theme.BG_CARD)
            items_frame.pack(fill="both", expand=True)
            items_frame.grid_columnconfigure(1, weight=1)
            for index, item in enumerate(numbered_items, start=1):
                row_padding = (0, 12) if index < len(numbered_items) else (0, 0)
                if isinstance(item, (tuple, list)) and len(item) == 2:
                    item_text, item_prompt = item
                else:
                    item_text, item_prompt = item, ""
                tk.Label(
                    items_frame,
                    text=f"{index}.",
                    font=message_font,
                    fg=ui_theme.TEXT_PRIMARY,
                    bg=ui_theme.BG_CARD,
                    justify="right",
                    anchor="ne",
                ).grid(row=index - 1, column=0, sticky="ne", padx=(0, 8), pady=row_padding)
                item_content = tk.Frame(items_frame, bg=ui_theme.BG_CARD)
                item_content.grid(row=index - 1, column=1, sticky="new", pady=row_padding)
                tk.Label(
                    item_content,
                    text=str(item_text),
                    font=message_font,
                    fg=ui_theme.TEXT_PRIMARY,
                    bg=ui_theme.BG_CARD,
                    justify="left",
                    anchor="nw",
                    wraplength=item_wraplength,
                ).pack(fill="x", anchor="w")
                if item_prompt:
                    tk.Label(
                        item_content,
                        text=str(item_prompt),
                        font=message_font,
                        fg=ui_theme.TEXT_SECONDARY,
                        bg=ui_theme.BG_CARD,
                        justify="left",
                        anchor="nw",
                        wraplength=item_wraplength,
                    ).pack(fill="x", anchor="w", pady=(3, 0))
        elif self._message_needs_scroll(message):
            text_frame = tk.Frame(content, bg=ui_theme.BG_CARD)
            text_frame.pack(fill="both", expand=True)
            text_widget = tk.Text(
                text_frame,
                width=64,
                height=12,
                wrap="word",
                font=message_font,
                bg=ui_theme.BG_CARD,
                fg=ui_theme.TEXT_PRIMARY,
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
                fg=ui_theme.TEXT_PRIMARY,
                bg=ui_theme.BG_CARD,
                justify="left",
                anchor="w",
                wraplength=content_wraplength,
            ).pack(fill="both", expand=True, anchor="w")

        tk.Frame(window, bg=ui_theme.BORDER, height=1).grid(
            row=1, column=0, sticky="ew"
        )
        footer = tk.Frame(window, bg=ui_theme.BG_FOOTER)
        footer.grid(row=2, column=0, sticky="ew")

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

        button_padding = (12, 5) if compact_action else (15, 8)
        footer_padding = (11, 11) if compact_action else (14, 14)
        button_style = ttk.Style(window)
        button_style.configure(
            "CenteredMessageBox.TButton",
            font=button_font,
            padding=button_padding,
        )
        # 主按钮（第一个）使用实心品牌蓝，建立主次层级
        button_style.configure(
            "CenteredMessageBox.Accent.TButton",
            font=button_font,
            padding=button_padding,
            background=ui_theme.PRIMARY,
            foreground=ui_theme.BG_CARD,
            bordercolor=ui_theme.PRIMARY_DARK,
        )
        button_style.map(
            "CenteredMessageBox.Accent.TButton",
            background=[("pressed", ui_theme.PRIMARY_DEEP), ("active", ui_theme.PRIMARY_DARK)],
        )
        min_button_width = 6 if compact_action else 8
        equal_button_width = max(
            min_button_width,
            max(self._button_text_units(label) for label, _value in buttons) + 2,
        )
        single_button = len(buttons) == 1
        for label, value in reversed(buttons):
            is_primary = (label, value) == buttons[0]
            button = ttk.Button(
                footer,
                text=label,
                command=lambda selected=value: finish(selected),
                style="CenteredMessageBox.Accent.TButton" if is_primary else "CenteredMessageBox.TButton",
                width=equal_button_width,
            )
            if single_button:
                button.pack(pady=footer_padding)
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
        width = max(int(min_width), min(max_width, window.winfo_reqwidth()))
        max_height = self._max_dialog_height(window.winfo_screenheight())
        height = max(180, min(max_height, window.winfo_reqheight()))
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
        show_icon = options.pop("show_icon", False)
        numbered_items = options.pop("numbered_items", None)
        min_width = options.pop("min_width", 460)
        max_width = options.pop("max_width", 700)
        font_delta = options.pop("font_delta", 0)
        content_bottom_padding = options.pop("content_bottom_padding", 8)
        compact_action = options.pop("compact_action", False)
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
            numbered_items=numbered_items,
            min_width=min_width,
            max_width=max_width,
            font_delta=font_delta,
            content_bottom_padding=content_bottom_padding,
            compact_action=compact_action,
        )
        return "ok"

    def showwarning(self, title, message, **options):
        headline = options.pop("headline", None)
        show_icon = options.pop("show_icon", False)
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
        show_icon = options.pop("show_icon", False)
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
        show_icon = options.pop("show_icon", False)
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
        show_icon = options.pop("show_icon", False)
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
        show_icon = options.pop("show_icon", False)
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
        show_icon = options.pop("show_icon", False)
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
