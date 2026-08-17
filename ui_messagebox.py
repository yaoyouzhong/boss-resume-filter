"""Parent-centered modal message boxes with tkinter-compatible results."""

from __future__ import annotations

import math
import tkinter as tk
import unicodedata
from collections.abc import Sequence
from tkinter import messagebox as _native_messagebox, ttk

import ui_theme
from ui_windowing import create_toplevel


class CenteredMessageBox:
    """Drop-in subset of tkinter.messagebox with reliable parent centering."""

    _ICON_STYLE = {
        "info": ("i", ui_theme.PRIMARY),
        "success": ("✓", ui_theme.SUCCESS),
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
        self._structured_headline_font = (ui_theme.FONT_FAMILY, 13, "bold")
        self._structured_message_font = (ui_theme.FONT_FAMILY, 11)
        self._structured_meta_font = (ui_theme.FONT_FAMILY, 10)
        self._structured_button_font = (ui_theme.FONT_FAMILY, 11)

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

    def set_structured_ui_fonts(self, *, headline, message, meta, button):
        """Configure the compact tier used by structured result dialogs."""
        self._structured_headline_font = headline
        self._structured_message_font = message
        self._structured_meta_font = meta
        self._structured_button_font = button

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
    def _font_with_weight(font_spec, weight):
        """Return a font tuple with the requested weight and unchanged family."""
        if not isinstance(font_spec, (tuple, list)) or len(font_spec) < 2:
            return font_spec
        adjusted = list(font_spec)
        if len(adjusted) == 2:
            adjusted.append(weight)
        else:
            adjusted[2] = weight
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

    @staticmethod
    def _structured_fallback_message(
        *,
        headline: str,
        message: str = "",
        metrics: Sequence[tuple[str, str]] = (),
        file_path: str | None = None,
        notice: str | None = None,
        detail: str | None = None,
    ) -> str:
        """Flatten structured content when no Tk parent is available."""
        parts = [str(headline).strip()]
        if message:
            parts.append(str(message).strip())
        if metrics:
            parts.append("，".join(f"{label} {value}" for label, value in metrics))
        if file_path:
            parts.append(f"保存位置：\n{file_path}")
        if notice:
            parts.append(str(notice).strip())
        if detail:
            parts.append(f"详细信息：\n{detail}")
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _structured_notice_colors(kind: str) -> tuple[str, str]:
        """Return readable background/foreground colors for a notice strip."""
        return {
            "warning": (ui_theme.BANNER_WARNING_BG, ui_theme.WARNING_TEXT),
            "error": (ui_theme.BANNER_ERROR_BG, ui_theme.DANGER_TEXT),
            "success": (ui_theme.BANNER_SUCCESS_BG, ui_theme.SUCCESS),
        }.get(kind, (ui_theme.BANNER_INFO_BG, ui_theme.PRIMARY_DARK))

    @staticmethod
    def _split_display_path(file_path: str) -> tuple[str, str]:
        """Split Windows or POSIX paths without changing the visible separator."""
        path_text = str(file_path)
        normalized = path_text.replace("\\", "/")
        if "/" not in normalized:
            return normalized, ""
        directory, file_name = normalized.rsplit("/", 1)
        if "\\" in path_text:
            directory = directory.replace("/", "\\")
        return file_name, directory

    def _show_structured(
        self,
        title: str,
        *,
        kind: str,
        headline: str,
        message: str = "",
        metrics: Sequence[tuple[str, str]] = (),
        file_path: str | None = None,
        notice: str | None = None,
        notice_kind: str = "info",
        detail: str | None = None,
        buttons: Sequence[tuple[str, object]],
        close_value: object,
        parent,
        min_width: int = 600,
        max_width: int = 660,
        primary_tone: str = "accent",
        default_to_close: bool = False,
        metrics_first: bool = False,
    ):
        """Show a compact, sectioned dialog without changing legacy messages.

        ``metrics_first=True`` 时数据条排在正文之前——正文承担次级说明
        （如 AI 增强明细）、主结论由数据条表达的场景使用。
        """
        min_width = max(520, int(min_width))
        max_width = max(min_width, int(max_width))
        content_wraplength = max(460, min(580, min_width - 48))
        message_font = self._structured_message_font
        meta_font = self._structured_meta_font
        metric_value_font = self._font_with_weight(message_font, "bold")

        window = create_toplevel(parent)
        window.title(str(title or "提示"))
        window.transient(parent)
        window.resizable(False, False)
        window.withdraw()
        window.configure(bg=ui_theme.BG_CARD)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(0, weight=1)

        body = tk.Frame(window, bg=ui_theme.BG_CARD)
        body.grid(row=0, column=0, sticky="nsew", padx=24, pady=(20, 18))

        status_row = tk.Frame(body, bg=ui_theme.BG_CARD)
        status_row.pack(fill="x")
        self._make_icon(status_row, kind, size=28).pack(
            side="left", anchor="n", padx=(0, 10)
        )
        tk.Label(
            status_row,
            text=str(headline),
            font=self._structured_headline_font,
            fg=ui_theme.TEXT_PRIMARY,
            bg=ui_theme.BG_CARD,
            justify="left",
            anchor="w",
            wraplength=content_wraplength - 38,
        ).pack(side="left", fill="x", expand=True, anchor="w")

        if message and not metrics_first:
            tk.Label(
                body,
                text=str(message),
                font=message_font,
                fg=ui_theme.TEXT_PRIMARY,
                bg=ui_theme.BG_CARD,
                justify="left",
                anchor="w",
                wraplength=content_wraplength,
            ).pack(fill="x", anchor="w", pady=(12, 0))

        if metrics:
            metrics_frame = tk.Frame(
                body,
                bg=ui_theme.BG_ZEBRA,
                highlightthickness=1,
                highlightbackground=ui_theme.BORDER,
            )
            metrics_frame.pack(fill="x", pady=(14, 0))
            for column, (label, value) in enumerate(metrics):
                metrics_frame.grid_columnconfigure(column, weight=1, uniform="metric")
                cell = tk.Frame(metrics_frame, bg=ui_theme.BG_ZEBRA)
                cell.grid(row=0, column=column, sticky="nsew", padx=10, pady=9)
                tk.Label(
                    cell,
                    text=str(label),
                    font=meta_font,
                    fg=ui_theme.TEXT_SECONDARY,
                    bg=ui_theme.BG_ZEBRA,
                ).pack(anchor="w")
                tk.Label(
                    cell,
                    text=str(value),
                    font=metric_value_font,
                    fg=ui_theme.TEXT_PRIMARY,
                    bg=ui_theme.BG_ZEBRA,
                ).pack(anchor="w", pady=(2, 0))

        if message and metrics_first:
            tk.Label(
                body,
                text=str(message),
                font=message_font,
                fg=ui_theme.TEXT_PRIMARY,
                bg=ui_theme.BG_CARD,
                justify="left",
                anchor="w",
                wraplength=content_wraplength,
            ).pack(fill="x", anchor="w", pady=(12, 0))

        copy_button = None
        if file_path:
            path_text = str(file_path)
            file_name, directory = self._split_display_path(path_text)
            file_frame = tk.Frame(
                body,
                bg=ui_theme.BG_INPUT,
                highlightthickness=1,
                highlightbackground=ui_theme.BORDER,
            )
            file_frame.pack(fill="x", pady=(14, 0))
            file_content = tk.Frame(file_frame, bg=ui_theme.BG_INPUT)
            file_content.pack(side="left", fill="both", expand=True, padx=12, pady=9)
            tk.Label(
                file_content,
                text=file_name,
                font=metric_value_font,
                fg=ui_theme.TEXT_PRIMARY,
                bg=ui_theme.BG_INPUT,
                justify="left",
                anchor="w",
                wraplength=content_wraplength - 120,
            ).pack(fill="x", anchor="w")
            tk.Label(
                file_content,
                text=directory,
                font=meta_font,
                fg=ui_theme.TEXT_SECONDARY,
                bg=ui_theme.BG_INPUT,
                justify="left",
                anchor="w",
                wraplength=content_wraplength - 120,
            ).pack(fill="x", anchor="w", pady=(3, 0))

            inline_style = ttk.Style(window)
            inline_style.configure(
                "StructuredMessageBox.Inline.TButton",
                font=meta_font,
                padding=(7, 3),
            )

            def copy_path() -> None:
                try:
                    window.clipboard_clear()
                    window.clipboard_append(path_text)
                    window.update()
                    copy_button.configure(text="已复制")
                    window.after(
                        1200,
                        lambda: (
                            copy_button.configure(text="复制路径")
                            if copy_button.winfo_exists()
                            else None
                        ),
                    )
                except tk.TclError:
                    if copy_button is not None and copy_button.winfo_exists():
                        copy_button.configure(text="复制失败")

            copy_button = ttk.Button(
                file_frame,
                text="复制路径",
                command=copy_path,
                style="StructuredMessageBox.Inline.TButton",
                width=8,
            )
            copy_button.pack(side="right", padx=(8, 12), pady=9)

        if notice:
            notice_bg, notice_fg = self._structured_notice_colors(notice_kind)
            notice_frame = tk.Frame(body, bg=notice_bg)
            notice_frame.pack(fill="x", pady=(14, 0))
            tk.Label(
                notice_frame,
                text=str(notice),
                font=meta_font,
                fg=notice_fg,
                bg=notice_bg,
                justify="left",
                anchor="w",
                wraplength=content_wraplength - 20,
            ).pack(fill="x", padx=10, pady=8)

        if detail:
            detail_frame = tk.Frame(
                body,
                bg=ui_theme.BG_INPUT,
                highlightthickness=1,
                highlightbackground=ui_theme.BORDER,
            )
            detail_frame.pack(fill="x", pady=(14, 0))
            tk.Label(
                detail_frame,
                text="详细信息",
                font=self._font_with_weight(meta_font, "bold"),
                fg=ui_theme.TEXT_SECONDARY,
                bg=ui_theme.BG_INPUT,
            ).pack(anchor="w", padx=10, pady=(8, 3))
            detail_text = str(detail).strip()
            if len(detail_text) > 220 or self._estimated_visual_lines(
                detail_text, chars_per_line=52
            ) > 4:
                text_row = tk.Frame(detail_frame, bg=ui_theme.BG_INPUT)
                text_row.pack(fill="x", padx=8, pady=(0, 8))
                text_widget = tk.Text(
                    text_row,
                    width=68,
                    height=4,
                    wrap="word",
                    font=meta_font,
                    bg=ui_theme.BG_INPUT,
                    fg=ui_theme.TEXT_PRIMARY,
                    relief="flat",
                    borderwidth=0,
                    highlightthickness=0,
                )
                scrollbar = ttk.Scrollbar(
                    text_row, orient="vertical", command=text_widget.yview
                )
                text_widget.configure(yscrollcommand=scrollbar.set)
                text_widget.insert("1.0", detail_text)
                text_widget.configure(state="disabled")
                text_widget.pack(side="left", fill="x", expand=True)
                scrollbar.pack(side="right", fill="y")
            else:
                tk.Label(
                    detail_frame,
                    text=detail_text,
                    font=meta_font,
                    fg=ui_theme.TEXT_PRIMARY,
                    bg=ui_theme.BG_INPUT,
                    justify="left",
                    anchor="w",
                    wraplength=content_wraplength - 20,
                ).pack(fill="x", padx=10, pady=(0, 8))

        tk.Frame(window, bg=ui_theme.BORDER, height=1).grid(
            row=1, column=0, sticky="ew"
        )
        footer = tk.Frame(window, bg=ui_theme.BG_FOOTER)
        footer.grid(row=2, column=0, sticky="ew")

        result = {"value": close_value}
        previous_grab = parent.grab_current()
        primary_button = None

        def finish(value) -> None:
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
            "StructuredMessageBox.TButton",
            font=self._structured_button_font,
            padding=(11, 5),
        )
        button_style.configure(
            "StructuredMessageBox.Accent.TButton",
            font=self._structured_button_font,
            padding=(11, 5),
            background=ui_theme.PRIMARY,
            foreground=ui_theme.BG_CARD,
            bordercolor=ui_theme.PRIMARY_DARK,
        )
        button_style.map(
            "StructuredMessageBox.Accent.TButton",
            background=[
                ("pressed", ui_theme.PRIMARY_DEEP),
                ("active", ui_theme.PRIMARY_DARK),
            ],
        )
        button_style.configure(
            "StructuredMessageBox.Danger.TButton",
            font=self._structured_button_font,
            padding=(11, 5),
            background=ui_theme.DANGER,
            foreground=ui_theme.BG_CARD,
            bordercolor=ui_theme.DANGER_TEXT,
        )
        button_style.map(
            "StructuredMessageBox.Danger.TButton",
            background=[
                ("pressed", ui_theme.DANGER_DEEP),
                ("active", ui_theme.DANGER_TEXT),
            ],
        )
        secondary_button = None
        for index, (label, value) in enumerate(buttons):
            primary_style = (
                "StructuredMessageBox.Danger.TButton"
                if primary_tone == "danger"
                else "StructuredMessageBox.Accent.TButton"
            )
            button = ttk.Button(
                footer,
                text=label,
                command=lambda selected=value: finish(selected),
                style=(
                    primary_style
                    if index == 0
                    else "StructuredMessageBox.TButton"
                ),
                width=max(7, self._button_text_units(label) + 2),
            )
            button.pack(
                side="right",
                padx=(8, 24) if index == 0 else (8, 0),
                pady=12,
            )
            if index == 0:
                primary_button = button
            elif secondary_button is None:
                secondary_button = button

        window.protocol("WM_DELETE_WINDOW", lambda: finish(close_value))
        window.bind("<Escape>", lambda _event: finish(close_value))
        if default_to_close:
            # 危险操作默认聚焦取消按钮；由按钮自身处理回车，避免重复触发关闭回调。
            window.bind("<Return>", lambda _event: "break")
        else:
            window.bind("<Return>", lambda _event: finish(buttons[0][1]))
        window.update_idletasks()
        width = max(min_width, min(max_width, window.winfo_reqwidth()))
        max_height = self._max_dialog_height(window.winfo_screenheight())
        height = max(220, min(max_height, window.winfo_reqheight()))
        self._place(window, width, height, parent)
        window.deiconify()
        window.lift()
        window.grab_set()
        focus_button = (
            secondary_button
            if default_to_close and secondary_button is not None
            else primary_button
        )
        if focus_button is not None:
            focus_button.focus_set()
        window.wait_window()
        return result["value"]

    def show_result(
        self,
        title: str,
        *,
        headline: str,
        message: str = "",
        metrics: Sequence[tuple[str, str]] = (),
        file_path: str | None = None,
        notice: str | None = None,
        notice_kind: str = "warning",
        detail: str | None = None,
        parent=None,
    ) -> str:
        """Show a structured success/result dialog and return the selected action."""
        resolved_parent = self._resolve_parent(parent)
        if resolved_parent is None:
            _native_messagebox.showinfo(
                title,
                self._structured_fallback_message(
                    headline=headline,
                    message=message,
                    metrics=metrics,
                    file_path=file_path,
                    notice=notice,
                    detail=detail,
                ),
                parent=parent,
            )
            return "close"
        buttons = (
            (("打开所在文件夹", "open_location"), ("关闭", "close"))
            if file_path
            else (("关闭", "close"),)
        )
        return str(self._show_structured(
            title,
            kind="success",
            headline=headline,
            message=message,
            metrics=metrics,
            file_path=file_path,
            notice=notice,
            notice_kind=notice_kind,
            detail=detail,
            buttons=buttons,
            close_value="close",
            parent=resolved_parent,
        ))

    def show_failure(
        self,
        title: str,
        *,
        headline: str,
        message: str,
        detail: str | None = None,
        notice: str | None = None,
        parent=None,
    ) -> str:
        """Show a user-facing failure summary with separate technical detail."""
        resolved_parent = self._resolve_parent(parent)
        if resolved_parent is None:
            _native_messagebox.showerror(
                title,
                self._structured_fallback_message(
                    headline=headline,
                    message=message,
                    notice=notice,
                    detail=detail,
                ),
                parent=parent,
            )
            return "close"
        return str(self._show_structured(
            title,
            kind="error",
            headline=headline,
            message=message,
            notice=notice,
            notice_kind="warning",
            detail=detail,
            buttons=(("关闭", "close"),),
            close_value="close",
            parent=resolved_parent,
        ))

    def show_notice(
        self,
        title: str,
        *,
        headline: str,
        message: str = "",
        metrics: Sequence[tuple[str, str]] = (),
        notice: str | None = None,
        detail: str | None = None,
        kind: str = "warning",
        metrics_first: bool = False,
        parent=None,
    ) -> str:
        """Show a structured warning or informational notice."""
        kind = "info" if kind == "info" else "warning"
        resolved_parent = self._resolve_parent(parent)
        if resolved_parent is None:
            fallback = (
                _native_messagebox.showinfo
                if kind == "info"
                else _native_messagebox.showwarning
            )
            fallback(
                title,
                self._structured_fallback_message(
                    headline=headline,
                    message=message,
                    metrics=metrics,
                    notice=notice,
                    detail=detail,
                ),
                parent=parent,
            )
            return "close"
        return str(self._show_structured(
            title,
            kind=kind,
            headline=headline,
            message=message,
            metrics=metrics,
            notice=notice,
            notice_kind="warning" if kind == "warning" else "info",
            detail=detail,
            buttons=(("关闭", "close"),),
            close_value="close",
            metrics_first=metrics_first,
            parent=resolved_parent,
        ))

    def ask_confirmation(
        self,
        title: str,
        *,
        headline: str,
        message: str,
        metrics: Sequence[tuple[str, str]] = (),
        notice: str | None = None,
        detail: str | None = None,
        yes_label: str = "继续",
        no_label: str = "取消",
        dangerous: bool = False,
        parent=None,
    ) -> bool:
        """Show a structured confirmation while preserving boolean results."""
        resolved_parent = self._resolve_parent(parent)
        if resolved_parent is None:
            return bool(_native_messagebox.askyesno(
                title,
                self._structured_fallback_message(
                    headline=headline,
                    message=message,
                    metrics=metrics,
                    notice=notice,
                    detail=detail,
                ),
                parent=parent,
            ))
        return bool(self._show_structured(
            title,
            kind="question",
            headline=headline,
            message=message,
            metrics=metrics,
            notice=notice,
            notice_kind="warning",
            detail=detail,
            buttons=((yes_label, True), (no_label, False)),
            close_value=False,
            parent=resolved_parent,
            primary_tone="danger" if dangerous else "accent",
            default_to_close=dangerous,
        ))

    def ask_choice(
        self,
        title: str,
        *,
        headline: str,
        message: str,
        choices: Sequence[tuple[str, object]],
        close_value: object = None,
        metrics: Sequence[tuple[str, str]] = (),
        notice: str | None = None,
        parent=None,
    ):
        """Show a structured multi-choice decision and return the selected value."""
        resolved_parent = self._resolve_parent(parent)
        if resolved_parent is None:
            return close_value
        return self._show_structured(
            title,
            kind="question",
            headline=headline,
            message=message,
            metrics=metrics,
            notice=notice,
            notice_kind="warning",
            buttons=choices,
            close_value=close_value,
            parent=resolved_parent,
        )

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

        window = create_toplevel(parent)
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
