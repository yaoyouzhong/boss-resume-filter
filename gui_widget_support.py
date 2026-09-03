"""Reusable Tk widget primitives without page or business semantics."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from tkinter import ttk
from typing import Any, Protocol

import ui_theme


class WidgetSupportHost(Protocol):
    """Explicit visual state consumed by shared widget factories."""

    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_section: Any
    font_label: Any
    icons: Any


@dataclass(frozen=True)
class StatusIconSet:
    """Status icon references retained by the application host."""

    ok: Any
    fail: Any


class WidgetSupport:
    """Build shared visual primitives while leaving layout flow to callers."""

    def __init__(
        self,
        host: WidgetSupportHost,
        *,
        ui_config: Mapping[str, Any],
    ) -> None:
        self.host = host
        self.ui_config = ui_config

    @property
    def scale(self) -> float:
        """Return the current combined DPI and user zoom scale."""
        return self.host.dpi_scale * self.host.zoom_factor

    def create_page_header(
        self,
        parent: tk.Misc,
        title: str,
        subtitle: str | None = None,
        top_padding: int = 0,
        trailing_builder: Callable[[tk.Misc], None] | None = None,
    ) -> ttk.Frame:
        """Create the shared page title card and return its inner frame."""
        host = self.host
        scale = self.scale
        padding = int(16 * scale)
        card = ttk.Frame(parent, style="PageHeader.TFrame")
        card.pack(
            fill="x",
            pady=(int(top_padding * scale), int(25 * scale)),
        )
        tk.Frame(card, width=int(4 * scale), bg=host.colors["primary"]).pack(
            side="left",
            fill="y",
        )
        inner = ttk.Frame(card, style="PageHeaderInner.TFrame")
        inner.pack(fill="x", padx=(padding, padding), pady=(padding, padding))
        label_parent: tk.Misc = inner
        if trailing_builder is not None:
            trailing = ttk.Frame(inner, style="PageHeaderInner.TFrame")
            trailing.pack(side="right", anchor="n", padx=(padding, 0))
            trailing_builder(trailing)
            label_parent = ttk.Frame(inner, style="PageHeaderInner.TFrame")
            label_parent.pack(side="left", fill="x", expand=True)
        ttk.Label(
            label_parent,
            text=title,
            font=host.font_section,
            foreground=host.colors["text_primary"],
            background=host.colors["bg_card"],
        ).pack(anchor="w")
        if subtitle:
            ttk.Label(
                label_parent,
                text=subtitle,
                font=host.font_label,
                foreground=host.colors["text_secondary"],
                background=host.colors["bg_card"],
            ).pack(anchor="w", pady=(int(8 * scale), 0))
        return inner

    def create_navigation_button(
        self,
        parent: tk.Misc,
        *,
        text: str,
        icon_name: str,
        command: Callable[[], Any],
        surface_color: str | None = None,
    ) -> tk.Canvas:
        """Create a compact rounded icon-and-text button for page navigation."""
        from PIL import Image, ImageDraw, ImageTk

        host = self.host
        scale = self.scale
        button_font = host.font_label
        icon_size = max(17, int(round(18 * scale)))
        gap = max(7, int(round(8 * scale)))
        horizontal_padding = max(14, int(round(16 * scale)))
        text_width = tkfont.Font(font=button_font).measure(text)
        width = horizontal_padding * 2 + icon_size + gap + text_width
        height = max(38, int(round(40 * scale)))
        body_height = height - max(2, int(round(3 * scale)))
        radius = max(7, int(round(ui_theme.RADIUS_LG * scale)))
        outer_background = surface_color or host.colors["bg_main"]
        canvas = tk.Canvas(
            parent,
            width=width,
            height=height,
            bg=outer_background,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            takefocus=1,
        )
        icon = host.icons.get(
            icon_name,
            icon_size,
            host.colors["primary_dark"],
        )
        state = {"hover": False, "pressed": False, "focus": False}

        def draw_background() -> None:
            supersample = 4
            image = Image.new(
                "RGBA",
                (width * supersample, height * supersample),
                outer_background,
            )
            draw = ImageDraw.Draw(image)
            shadow_offset = max(1, int(round(2 * scale))) * supersample
            body_bottom = body_height * supersample - 1
            draw.rounded_rectangle(
                (
                    supersample,
                    shadow_offset,
                    width * supersample - supersample - 1,
                    body_bottom + shadow_offset,
                ),
                radius=radius * supersample,
                fill=host.colors["border"],
            )
            if state["pressed"]:
                fill = host.colors.get("home_primary_border", ui_theme.PRIMARY_PALE)
            elif state["hover"]:
                fill = host.colors["banner_info_bg"]
            else:
                fill = host.colors.get("home_primary_tint", host.colors["banner_info_bg"])
            outline = (
                host.colors["primary"]
                if state["focus"] or state["hover"]
                else host.colors.get("home_primary_border", ui_theme.PRIMARY_PALE)
            )
            outline_width = (2 if state["focus"] else 1) * supersample
            draw.rounded_rectangle(
                (
                    supersample,
                    0,
                    width * supersample - supersample - 1,
                    body_bottom,
                ),
                radius=radius * supersample,
                fill=fill,
                outline=outline,
                width=outline_width,
            )
            image = image.resize((width, height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            canvas._navigation_background = photo
            canvas.itemconfigure("background", image=photo)

        center_y = body_height // 2
        canvas.create_image(
            width // 2,
            height // 2,
            tags="background",
        )
        canvas.create_image(
            horizontal_padding + icon_size // 2,
            center_y,
            image=icon,
            tags="content",
        )
        canvas.create_text(
            horizontal_padding + icon_size + gap,
            center_y,
            text=text,
            font=button_font,
            fill=host.colors["primary_dark"],
            anchor="w",
            tags="content",
        )
        canvas._navigation_icon = icon
        canvas.tag_raise("content")

        def set_state(key: str, value: bool) -> None:
            state[key] = value
            draw_background()
            canvas.tag_raise("content")

        def release(event: tk.Event) -> str:
            was_pressed = state["pressed"]
            set_state("pressed", False)
            if (
                was_pressed
                and 0 <= int(event.x) < int(canvas.winfo_width())
                and 0 <= int(event.y) < int(canvas.winfo_height())
            ):
                command()
            return "break"

        def press(_event: tk.Event) -> str:
            canvas.focus_set()
            set_state("pressed", True)
            return "break"

        def keyboard_activate(_event: tk.Event) -> str:
            command()
            return "break"

        canvas.bind("<Enter>", lambda _event: set_state("hover", True))
        canvas.bind("<Leave>", lambda _event: set_state("hover", False))
        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<ButtonRelease-1>", release)
        canvas.bind("<space>", keyboard_activate)
        canvas.bind("<Return>", keyboard_activate)
        canvas.bind("<FocusIn>", lambda _event: set_state("focus", True))
        canvas.bind("<FocusOut>", lambda _event: set_state("focus", False))
        draw_background()
        canvas.tag_raise("content")
        return canvas

    def create_card(
        self,
        parent: tk.Misc,
        title: str,
        padding: int | None = None,
        title_trailing_builder: Callable[[tk.Misc, int], None] | None = None,
        **pack_options: Any,
    ) -> ttk.Frame:
        """Create a titled card and return its padded content frame."""
        host = self.host
        if padding is None:
            padding = int(self.ui_config["label_frame_padding"] * self.scale)
        title_font = pack_options.pop("title_font", host.font_label)
        content_style = pack_options.pop("content_style", "TFrame")
        card = tk.Frame(
            parent,
            bg=host.colors["bg_card"],
            highlightbackground=host.colors["border"],
            highlightthickness=1,
        )
        card.pack(**pack_options)
        title_background = host.colors.get("bg_footer", ui_theme.BG_FOOTER)
        title_bar = tk.Frame(card, bg=title_background)
        title_bar.pack(fill="x")
        tk.Frame(
            title_bar,
            width=int(2 * self.scale),
            bg=host.colors["primary"],
        ).pack(side="left", fill="y")
        title_label = tk.Label(
            title_bar,
            text=f" {title} ",
            font=title_font,
            fg=host.colors["text_primary"],
            bg=title_background,
        )
        if title_trailing_builder is not None:
            title_trailing_builder(title_bar, padding)
        title_label.pack(
            anchor="w",
            padx=padding,
            pady=(int(padding * 0.7), int(padding * 0.7)),
        )
        tk.Frame(card, bg=host.colors["border"], height=1).pack(fill="x")
        content = ttk.Frame(card, style=content_style)
        content.pack(fill="both", expand=True, padx=padding, pady=padding)
        return content

    def _render_switch_photo(
        self,
        *,
        width: int,
        height: int,
        scale: float,
        switched_on: bool,
    ) -> Any:
        """Render one supersampled switch frame as a Tk image."""
        from PIL import Image, ImageDraw, ImageTk

        host = self.host
        track = (
            host.colors["primary"]
            if switched_on
            else host.colors.get("border_strong", ui_theme.BORDER_STRONG)
        )
        render_scale = 4
        image = Image.new(
            "RGBA",
            (width * render_scale, height * render_scale),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (0, 0, width * render_scale - 1, height * render_scale - 1),
            radius=height * render_scale // 2,
            fill=track,
        )
        margin = max(2, int(round(2 * scale)))
        knob_diameter = height - margin * 2
        knob_x = width - knob_diameter - margin if switched_on else margin
        draw.ellipse(
            (
                knob_x * render_scale,
                margin * render_scale,
                (knob_x + knob_diameter) * render_scale,
                (margin + knob_diameter) * render_scale,
            ),
            fill="#FFFFFF",
        )
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(image)

    def create_switch(
        self,
        parent: tk.Misc,
        variable: tk.Variable,
        enabled_variable: tk.Variable | None = None,
    ) -> tk.Canvas:
        """Create a supersampled accessible on/off switch bound to variables."""
        host = self.host
        scale = self.scale
        width = max(28, int(round(30 * scale)))
        height = max(14, int(round(16 * scale)))
        canvas = tk.Canvas(
            parent,
            width=width,
            height=height,
            bg=host.colors["bg_card"],
            highlightthickness=1,
            highlightbackground=host.colors["bg_card"],
            bd=0,
            cursor="hand2",
            takefocus=1,
        )

        def is_enabled() -> bool:
            return enabled_variable is None or bool(enabled_variable.get())

        def draw_switch() -> None:
            try:
                if not canvas.winfo_exists():
                    return
            except tk.TclError:
                return
            canvas.delete("all")
            enabled = is_enabled()
            canvas.configure(cursor="hand2" if enabled else "arrow", takefocus=int(enabled))
            switched_on = bool(variable.get())
            photo = self._render_switch_photo(
                width=width,
                height=height,
                scale=scale,
                switched_on=switched_on,
            )
            canvas._switch_photo = photo
            canvas.create_image(width // 2, height // 2, image=photo)

        def toggle(_event: tk.Event | None = None) -> str:
            if not is_enabled():
                variable.set(False)
                return "break"
            variable.set(not variable.get())
            return "break"

        canvas.bind("<Button-1>", toggle)
        canvas.bind("<space>", toggle)
        canvas.bind(
            "<FocusIn>",
            lambda _event: canvas.configure(
                highlightbackground=host.colors.get(
                    "primary_light", ui_theme.PRIMARY_LIGHT
                )
            ),
        )
        canvas.bind(
            "<FocusOut>",
            lambda _event: canvas.configure(
                highlightbackground=host.colors["bg_card"]
            ),
        )
        draw_switch()
        variable.trace_add("write", lambda *_args: draw_switch())
        if enabled_variable is not None:
            enabled_variable.trace_add("write", lambda *_args: draw_switch())
        return canvas

    def build_empty_state(
        self,
        parent: tk.Misc,
        icon_name: str,
        title: str,
        hint: str,
        action_text: str | None = None,
        action_command: Callable[[], Any] | None = None,
    ) -> ttk.Frame:
        """Build a reusable place-managed empty state without showing it."""
        host = self.host
        frame = ttk.Frame(parent, style="TFrame")
        inner = ttk.Frame(frame, style="TFrame")
        inner.place(relx=0.5, rely=0.42, anchor="center")
        icon_image = host.icons.get(
            icon_name,
            int(56 * self.scale),
            host.colors.get("text_muted", ui_theme.TEXT_MUTED),
            host.colors["bg_card"],
        )
        icon_label = ttk.Label(
            inner,
            image=icon_image,
            background=host.colors["bg_card"],
        )
        icon_label._icon_ref = icon_image
        icon_label.pack(anchor="center")
        ttk.Label(
            inner,
            text=title,
            font=host.font_section,
            foreground=host.colors["text_primary"],
            background=host.colors["bg_card"],
        ).pack(anchor="center", pady=(int(12 * host.dpi_scale), 0))
        ttk.Label(
            inner,
            text=hint,
            font=host.font_label,
            foreground=host.colors["text_secondary"],
            background=host.colors["bg_card"],
            justify="center",
        ).pack(anchor="center", pady=(int(6 * host.dpi_scale), 0))
        if action_text and action_command:
            ttk.Button(
                inner,
                text=action_text,
                style="Accent.TButton",
                command=action_command,
            ).pack(anchor="center", pady=(int(16 * host.dpi_scale), 0))
        return frame

    def create_status_icons(self) -> StatusIconSet:
        """Create the shared success and failure progress icons."""
        from PIL import Image, ImageDraw, ImageTk

        host = self.host
        size = int(18 * self.scale)

        def make_icon(background: str, symbol: str) -> Any:
            image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse([0, 0, size - 1, size - 1], fill=background)
            line_width = max(2, size // 8)
            if symbol == "check":
                points = [
                    (size * 0.25, size * 0.50),
                    (size * 0.42, size * 0.68),
                    (size * 0.75, size * 0.32),
                ]
                draw.line([points[0], points[1]], fill="white", width=line_width)
                draw.line([points[1], points[2]], fill="white", width=line_width)
            else:
                padding = size * 0.3
                draw.line(
                    [(padding, padding), (size - padding, size - padding)],
                    fill="white",
                    width=line_width,
                )
                draw.line(
                    [(size - padding, padding), (padding, size - padding)],
                    fill="white",
                    width=line_width,
                )
            return ImageTk.PhotoImage(image)

        return StatusIconSet(
            ok=make_icon(host.colors["success"], "check"),
            fail=make_icon(host.colors["danger"], "cross"),
        )
