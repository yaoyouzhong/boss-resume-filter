"""Reusable tooltip windows and non-modal inline page feedback."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Mapping
from typing import Any, Protocol

import ui_theme
from ui_windowing import get_windows_monitor_area


class FeedbackSupportHost(Protocol):
    """Explicit visual and compatibility state used by feedback helpers."""

    root: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_label: Any
    _tooltip: tk.Toplevel | None
    _tooltip_item: Any
    _tooltip_after_id: str | None
    _model_tooltip: tk.Toplevel | None
    _model_tooltip_item: Any
    _model_tooltip_after_id: str | None
    _skills_tooltip: tk.Toplevel | None
    _skills_tooltip_item: Any
    _req_tooltip: tk.Toplevel | None
    _req_tooltip_idx: Any
    _inline_banners: dict[tk.Misc, tk.Misc]


class FeedbackSupport:
    """Own tooltip windows and inline-banner rendering without business rules."""

    def __init__(self, host: FeedbackSupportHost, *, font_family: str) -> None:
        self.host = host
        self.font_family = font_family
        for attribute in (
            "_tooltip",
            "_tooltip_item",
            "_tooltip_after_id",
            "_model_tooltip",
            "_model_tooltip_item",
            "_model_tooltip_after_id",
            "_skills_tooltip",
            "_skills_tooltip_item",
            "_req_tooltip",
            "_req_tooltip_idx",
        ):
            if not hasattr(host, attribute):
                setattr(host, attribute, None)
        if not hasattr(host, "_inline_banners"):
            host._inline_banners = {}

    def show_inline_banner(
        self,
        page: tk.Misc,
        kind: str,
        text: str,
        duration_ms: int = 6000,
    ) -> None:
        """Show a dismissible non-modal banner at the top of a page."""
        host = self.host
        try:
            if page is None or not page.winfo_exists():
                return
        except tk.TclError:
            return
        self.hide_inline_banner(page)
        bg_key, bg_fallback = {
            "info": ("banner_info_bg", ui_theme.BANNER_INFO_BG),
            "warning": ("banner_warning_bg", ui_theme.BANNER_WARNING_BG),
            "error": ("banner_error_bg", ui_theme.BANNER_ERROR_BG),
            "success": ("banner_success_bg", ui_theme.BANNER_SUCCESS_BG),
        }.get(kind, ("banner_info_bg", ui_theme.BANNER_INFO_BG))
        background = host.colors.get(bg_key, bg_fallback)
        children = page.winfo_children()
        banner = tk.Frame(page, bg=background)
        tk.Label(
            banner,
            text=text,
            bg=background,
            fg=host.colors["text_primary"],
            font=host.font_label,
            anchor="w",
            justify="left",
        ).pack(
            side="left",
            fill="x",
            expand=True,
            padx=(int(12 * host.dpi_scale), int(8 * host.dpi_scale)),
            pady=int(8 * host.dpi_scale),
        )
        close = tk.Label(
            banner,
            text="✕",
            bg=background,
            cursor="hand2",
            fg=host.colors["text_secondary"],
            font=host.font_label,
        )
        close.pack(side="right", padx=(0, int(12 * host.dpi_scale)))
        close.bind("<Button-1>", lambda _event: self.hide_inline_banner(page))
        if children:
            banner.pack(side="top", fill="x", before=children[0])
        else:
            banner.pack(side="top", fill="x")
        host._inline_banners[page] = banner
        if duration_ms:
            banner.after(duration_ms, lambda: self.hide_inline_banner(page))

    def hide_inline_banner(self, page: tk.Misc) -> None:
        """Destroy the active banner for one page, if present."""
        banner = getattr(self.host, "_inline_banners", {}).pop(page, None)
        if banner is not None:
            try:
                banner.destroy()
            except tk.TclError:
                pass

    def show_tooltip(
        self,
        text: str,
        x: int,
        y: int,
        tooltip_key: Any = None,
        *,
        parent: tk.Misc | None = None,
        wraplength: int | None = None,
    ) -> None:
        """Replace the general tooltip slot with a positioned window."""
        host = self.host
        self.hide_tooltip()
        host._tooltip = self._styled_tooltip(
            text,
            x,
            y,
            wraplength=wraplength,
            parent=parent,
        )
        host._tooltip_item = tooltip_key

    def hide_tooltip(self, _event: tk.Event | None = None) -> None:
        """Cancel and destroy the general tooltip slot."""
        host = self.host
        after_id = getattr(host, "_tooltip_after_id", None)
        if after_id:
            host.root.after_cancel(after_id)
            host._tooltip_after_id = None
        tooltip = getattr(host, "_tooltip", None)
        if tooltip:
            tooltip.destroy()
            host._tooltip = None
        host._tooltip_item = None

    def show_model_tooltip(
        self,
        text: str,
        x: int,
        y: int,
        tooltip_key: Any = None,
    ) -> None:
        """Replace the model-list tooltip slot."""
        host = self.host
        self.hide_model_tooltip()
        host._model_tooltip = self._styled_tooltip(text, x, y, wraplength=400)
        host._model_tooltip_item = tooltip_key

    def hide_model_tooltip(self, _event: tk.Event | None = None) -> None:
        """Cancel and destroy the model-list tooltip slot."""
        host = self.host
        if host._model_tooltip_after_id:
            host.root.after_cancel(host._model_tooltip_after_id)
            host._model_tooltip_after_id = None
        if host._model_tooltip:
            host._model_tooltip.destroy()
            host._model_tooltip = None
        host._model_tooltip_item = None

    def create_simple_tooltip(self, text: str, x: int, y: int) -> tk.Toplevel:
        """Create an unmanaged tooltip window for a page-specific slot."""
        return self._styled_tooltip(text, x, y, wraplength=500)

    def hide_skills_tooltip(self, _event: tk.Event | None = None) -> None:
        """Destroy the job-skills tooltip slot."""
        host = self.host
        if host._skills_tooltip:
            host._skills_tooltip.destroy()
            host._skills_tooltip = None
        host._skills_tooltip_item = None

    def hide_requirement_tooltip(self, _event: tk.Event | None = None) -> None:
        """Destroy the job-requirement tooltip slot."""
        host = self.host
        if host._req_tooltip:
            host._req_tooltip.destroy()
            host._req_tooltip = None
        host._req_tooltip_idx = None

    def _styled_tooltip(
        self,
        text: str,
        x: int,
        y: int,
        *,
        wraplength: int | None = None,
        parent: tk.Misc | None = None,
    ) -> tk.Toplevel:
        """Create and clamp one borderless tooltip to its monitor work area."""
        host = self.host
        tooltip_parent = parent or host.root
        tooltip = tk.Toplevel(tooltip_parent)
        tooltip.wm_overrideredirect(True)
        label_options: dict[str, Any] = {}
        if wraplength:
            label_options["wraplength"] = wraplength
            label_options["justify"] = "left"
        tk.Label(
            tooltip,
            text=text,
            background=host.colors.get("tooltip_bg", ui_theme.TOOLTIP_BG),
            foreground=host.colors.get("tooltip_fg", ui_theme.TOOLTIP_FG),
            relief="flat",
            borderwidth=0,
            font=(
                self.font_family,
                int(10 * host.dpi_scale * host.zoom_factor),
            ),
            padx=10,
            pady=6,
            **label_options,
        ).pack()
        tooltip.update_idletasks()
        monitor_area = get_windows_monitor_area(tooltip, tooltip_parent)
        if monitor_area is None:
            monitor_area = (
                0,
                0,
                int(tooltip.winfo_screenwidth()),
                int(tooltip.winfo_screenheight()),
            )
        left, top, area_width, area_height = monitor_area
        margin = 8
        max_x = left + area_width - int(tooltip.winfo_reqwidth()) - margin
        max_y = top + area_height - int(tooltip.winfo_reqheight()) - margin
        safe_x = max(left + margin, min(int(x), max_x))
        safe_y = max(top + margin, min(int(y), max_y))
        x_geometry = f"+{safe_x}" if safe_x >= 0 else str(safe_x)
        y_geometry = f"+{safe_y}" if safe_y >= 0 else str(safe_y)
        tooltip.wm_geometry(f"{x_geometry}{y_geometry}")
        return tooltip
