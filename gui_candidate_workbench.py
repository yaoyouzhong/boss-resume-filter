"""Shared Tk primitives for candidate workbench windows."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Mapping, Sequence
from tkinter import ttk
from typing import Any, Protocol

import ui_theme


class CandidateWorkbenchHost(Protocol):
    """Visual attributes required by shared candidate workbench primitives."""

    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float


Metric = tuple[str, str, int, str]


def create_header(
    host: CandidateWorkbenchHost,
    parent: tk.Misc,
    title: str,
    subtitle: str,
    scope: str,
) -> ttk.Frame:
    """Create the shared title and scope block used by candidate workbenches."""
    scale = host.dpi_scale * host.zoom_factor
    header = ttk.Frame(parent, style="Page.TFrame")
    header.pack(fill="x", pady=(0, int(10 * scale)))
    ttk.Label(
        header,
        text=title,
        font=(ui_theme.FONT_FAMILY, int(18 * host.font_scale), "bold"),
        foreground=host.colors["text_primary"],
        background=host.colors["bg_main"],
    ).pack(anchor="w")
    ttk.Label(
        header,
        text=subtitle,
        font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
        foreground=host.colors["text_secondary"],
        background=host.colors["bg_main"],
    ).pack(anchor="w", pady=(int(2 * scale), 0))
    ttk.Label(
        header,
        text=f"范围：{scope}",
        font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
        foreground=host.colors["text_muted"],
        background=host.colors["bg_main"],
    ).pack(anchor="w", pady=(int(4 * scale), 0))
    return header


def create_metrics(
    host: CandidateWorkbenchHost,
    parent: tk.Misc,
    metrics: Sequence[Metric],
) -> dict[str, tk.StringVar]:
    """Create a compact segmented metric strip and return its value variables."""
    scale = host.dpi_scale * host.zoom_factor
    strip = ttk.Frame(parent, style="Page.TFrame")
    strip.pack(fill="x", pady=(0, int(12 * scale)))
    value_vars: dict[str, tk.StringVar] = {}
    for index, (key, label, value, color) in enumerate(metrics):
        segment = tk.Frame(
            strip,
            bg=host.colors["bg_card"],
            highlightbackground=host.colors["border"],
            highlightthickness=1,
        )
        segment.pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0 if index == 0 else int(4 * scale), 0),
        )
        tk.Frame(
            segment,
            bg=color,
            width=max(3, int(3 * scale)),
        ).pack(side="left", fill="y")
        content = tk.Frame(segment, bg=host.colors["bg_card"])
        content.pack(
            side="left",
            fill="x",
            expand=True,
            padx=int(10 * scale),
            pady=int(7 * scale),
        )
        value_var = tk.StringVar(value=str(value))
        value_vars[key] = value_var
        tk.Label(
            content,
            textvariable=value_var,
            font=(ui_theme.FONT_FAMILY, int(15 * host.font_scale), "bold"),
            foreground=host.colors["text_primary"],
            background=host.colors["bg_card"],
        ).pack(side="left")
        tk.Label(
            content,
            text=label,
            font=(ui_theme.FONT_FAMILY, int(10 * host.font_scale)),
            foreground=host.colors["text_secondary"],
            background=host.colors["bg_card"],
        ).pack(
            side="left",
            padx=(int(6 * scale), 0),
            pady=(int(2 * scale), 0),
        )
    return value_vars


def navigation_style(
    host: CandidateWorkbenchHost,
    scale: float,
    ui_config: Mapping[str, Any],
) -> str:
    """Configure the shared hierarchy style used by candidate workbenches."""
    style_name = "CandidateWorkbench.Navigation.Treeview"
    style = ttk.Style()
    style.configure(
        style_name,
        font=(ui_theme.FONT_FAMILY, int(11 * host.font_scale)),
        rowheight=int(ui_config["treeview_rowheight"] * scale),
        background=host.colors["bg_card"],
        fieldbackground=host.colors["bg_card"],
        foreground=host.colors["text_primary"],
    )
    return style_name


def apply_navigation_tags(
    host: CandidateWorkbenchHost,
    tree: ttk.Treeview,
) -> None:
    """Apply identical root and child typography to a workbench hierarchy."""
    tree.tag_configure(
        "workbench_root",
        font=(ui_theme.FONT_FAMILY, int(11 * host.font_scale), "bold"),
        foreground=host.colors["primary"],
    )
    tree.tag_configure(
        "workbench_child",
        font=(ui_theme.FONT_FAMILY, int(11 * host.font_scale)),
        foreground=host.colors["text_primary"],
    )
