"""Codex settings presentation shared by BOSS and standalone education UI."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk


class CodexSettingsPanel:
    def __init__(
        self,
        host,
        parent,
        model_row,
        api_rows,
        fetch_button,
        *,
        status_reader,
        login_starter,
        model_combo,
        label_width=10,
    ):
        self.status_reader = status_reader
        self.login_starter = login_starter
        self.host = host
        self.api_rows = api_rows
        self.fetch_button = fetch_button
        self.model_combo = model_combo
        self.generation = 0
        self.frame = ttk.Frame(parent)
        self.model_row = model_row
        self.gap = int(10 * host.dpi_scale * host.zoom_factor)
        self.status = tk.StringVar(value="正在检查本机 Codex…")
        account_row = ttk.Frame(self.frame)
        account_row.pack(fill="x")
        ttk.Label(
            account_row, text="订阅登录:", width=label_width, font=host.font_label
        ).pack(side="left")
        style = ttk.Style(self.frame)
        style.configure(
            "Codex.Account.TMenubutton",
            font=host.font_label,
            background=host.colors["bg_card"],
            foreground=host.colors["text_secondary"],
            relief="flat",
            borderwidth=0,
            padding=(8, 5),
        )
        style.map(
            "Codex.Account.TMenubutton",
            background=[("active", host.colors["home_primary_tint"])],
        )
        self.account_button = ttk.Menubutton(
            account_row,
            textvariable=self.status,
            style="Codex.Account.TMenubutton",
            takefocus=True,
        )
        self.account_button.pack(
            side="left",
            padx=(max(0, int(5 * host.dpi_scale * host.zoom_factor) - 8), 0),
        )
        self.account_menu = tk.Menu(
            self.account_button,
            tearoff=False,
            font=host.font_label,
            background=host.colors["bg_card"],
            foreground=host.colors["text_primary"],
            activebackground=host.colors["home_primary_tint"],
            activeforeground=host.colors["primary"],
        )
        self.account_menu.add_command(label="刷新状态与模型", command=self.refresh)
        self.account_menu.add_command(label="登录 ChatGPT", command=self.login)
        self.account_button.configure(menu=self.account_menu)
        hint_row = ttk.Frame(self.frame)
        hint_row.pack(fill="x", pady=(self.gap, 0))
        ttk.Label(hint_row, text="", width=label_width, font=host.font_label).pack(
            side="left"
        )
        ttk.Label(
            hint_row,
            text="无需 API Key；订阅额度和模型可用性以测试连接为准。",
            font=host.font_label,
            foreground=host.colors["text_secondary"],
        ).pack(side="left", padx=(int(5 * host.dpi_scale * host.zoom_factor), 0))
        self.trace = host.api_provider_var.trace_add("write", self.selection_changed)
        self.selection_changed()

    def selection_changed(self, *_):
        selected = self.host.api_provider_var.get() == self.host.PROVIDER_DISPLAY.get(
            "codex"
        )
        self.generation += 1
        if selected:
            for row in self.api_rows:
                row.pack_forget()
            self.host.api_key_var.set("")
            self.frame.pack(fill="x", after=self.model_row, pady=(self.gap, 0))
            self.fetch_button.pack_forget()
            self.model_combo.configure(state="readonly")
            self.refresh()
        else:
            self.frame.pack_forget()
            for row in self.api_rows:
                row.pack(fill="x", pady=(10, 0))
            self.model_combo.configure(state="normal", values=())
            self.fetch_button.pack(side="left")
            self.fetch_button.configure(text=" 自动识别并获取模型")

    def refresh(self):
        self.generation += 1
        generation = self.generation
        self.status.set("正在检查本机 Codex…")

        def worker():
            state = self.status_reader()

            def apply():
                if generation != self.generation or not self.frame.winfo_exists():
                    return
                current_model = self.host.api_model_var.get().strip()
                models = list(dict.fromkeys([current_model] if current_model else []))
                models.extend(
                    name
                    for name in state.get("cached_model_suggestions", [])
                    if name not in models
                )
                self.model_combo.configure(values=models)
                if not state["installed"]:
                    text = "未安装 Codex CLI"
                elif not state["compatible"]:
                    text = "请更新 Codex CLI"
                elif state["login_in_progress"]:
                    text = "等待浏览器登录"
                elif state["chatgpt_logged_in"]:
                    text = "已登录 ChatGPT"
                else:
                    text = state.get("login_error") or "尚未登录 ChatGPT"
                self.status.set(text)
                ttk.Style(self.frame).configure(
                    "Codex.Account.TMenubutton",
                    foreground=self.host.colors[
                        "success"
                        if state["chatgpt_logged_in"] and state["compatible"]
                        else "warning"
                    ],
                )
                self.account_menu.entryconfigure(
                    1,
                    label="重新登录 ChatGPT"
                    if state["chatgpt_logged_in"]
                    else "登录 ChatGPT",
                )
                if state["login_in_progress"]:
                    self.host.root.after(2000, self.refresh)

            self.host.run_on_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def login(self):
        self.status.set("正在启动 ChatGPT 登录…")

        def worker():
            try:
                self.login_starter()
            except Exception as exc:
                error = str(exc)
                self.host.run_on_ui(lambda: self.status.set(error))
                return
            self.host.run_on_ui(self.refresh)

        threading.Thread(target=worker, daemon=True).start()
