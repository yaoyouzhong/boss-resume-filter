"""Reusable input context menus and read-only text dialogs."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable, Mapping
from tkinter import ttk
from typing import Any, Protocol

from ui_windowing import place_window_centered


class InputSupportHost(Protocol):
    """Explicit visual surface required by input helpers."""

    root: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    font_log: Any
    _context_menus: list[tk.Menu]


class InputSupport:
    """Own edit context menus and generic read-only text dialogs."""

    def __init__(self, host: InputSupportHost, *, font_family: str) -> None:
        self.host = host
        self.font_family = font_family

    def bind_entry_context_menu(self, entry_widget: tk.Misc) -> None:
        """Bind cut, copy, paste, and select-all actions to an entry widget."""
        host = self.host
        menu = tk.Menu(
            entry_widget,
            tearoff=0,
            font=(self.font_family, int(12 * host.font_scale)),
        )
        host._context_menus.append(menu)

        def generate(sequence: str) -> None:
            try:
                entry_widget.event_generate(sequence)
            except tk.TclError:
                pass

        def select_all() -> None:
            try:
                entry_widget.select_range(0, "end")
                entry_widget.icursor("end")
            except tk.TclError:
                pass

        menu.add_command(label="剪切(T)", command=lambda: generate("<<Cut>>"))
        menu.add_command(label="复制(C)", command=lambda: generate("<<Copy>>"))
        menu.add_command(label="粘贴(P)", command=lambda: generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="全选(A)", command=select_all)
        entry_widget.bind(
            "<Button-3>",
            lambda event: menu.tk_popup(event.x_root, event.y_root),
        )

    def bind_text_context_menu(
        self,
        text_widget: tk.Text,
        *,
        editable: bool = True,
    ) -> None:
        """Bind edit actions to a text widget, respecting read-only mode."""
        host = self.host
        menu = tk.Menu(
            text_widget,
            tearoff=0,
            font=(self.font_family, int(12 * host.font_scale)),
        )
        host._context_menus.append(menu)

        def generate(sequence: str) -> None:
            try:
                text_widget.event_generate(sequence)
            except tk.TclError:
                pass

        def select_all() -> None:
            try:
                text_widget.tag_add("sel", "1.0", "end")
            except tk.TclError:
                pass

        if editable:
            menu.add_command(label="剪切(T)", command=lambda: generate("<<Cut>>"))
        menu.add_command(label="复制(C)", command=lambda: generate("<<Copy>>"))
        if editable:
            menu.add_command(label="粘贴(P)", command=lambda: generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="全选(A)", command=select_all)
        text_widget.bind(
            "<Button-3>",
            lambda event: menu.tk_popup(event.x_root, event.y_root),
        )

    def show_text_dialog(
        self,
        title: str,
        text: str,
        width: int = 700,
        height: int = 520,
        button_text: str = "关闭",
        button_align: str = "right",
        extra_actions: Iterable[tuple[str, Callable[[], None]]] | None = None,
    ) -> None:
        """Show modal read-only text with optional explicit business callbacks."""
        host = self.host
        win = tk.Toplevel(host.root)
        win.title(title)
        win.transient(host.root)
        win.grab_set()
        win.withdraw()
        scale = host.dpi_scale * host.zoom_factor
        win.grid_rowconfigure(0, weight=1)
        win.grid_columnconfigure(0, weight=1)

        body = ttk.Frame(win, style="Page.TFrame", padding=int(16 * scale))
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        text_widget = tk.Text(
            body,
            wrap="word",
            font=host.font_log,
            bg=host.colors["bg_card"],
            fg=host.colors["text_primary"],
            relief="solid",
            bd=1,
        )
        scrollbar = ttk.Scrollbar(
            body,
            orient="vertical",
            command=text_widget.yview,
        )
        text_widget.configure(yscrollcommand=scrollbar.set)
        text_widget.insert("1.0", text)
        text_widget.configure(state="disabled")
        text_widget.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        horizontal_padding = int(16 * scale)
        button_row = ttk.Frame(
            win,
            style="Page.TFrame",
            padding=(horizontal_padding, 0, horizontal_padding, int(12 * scale)),
        )
        button_row.grid(row=1, column=0, sticky="ew")

        def close() -> None:
            win.grab_release()
            win.destroy()

        def run_extra_action(command: Callable[[], None]) -> None:
            close()
            command()

        for action_text, action_command in extra_actions or ():
            ttk.Button(
                button_row,
                text=action_text,
                command=lambda command=action_command: run_extra_action(command),
            ).pack(side="left", padx=(0, int(8 * scale)))

        button = ttk.Button(button_row, text=button_text, command=close)
        if button_align == "center":
            button.pack()
        else:
            button.pack(side="right")
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda _event: close())
        place_window_centered(
            win,
            int(width * scale),
            int(height * scale),
            parent=host.root,
        )
        win.deiconify()
