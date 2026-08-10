"""Responsive window, page-height, and table-column layout support."""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
import tkinter as tk
from tkinter import font as tkfont
from typing import Any, Protocol

from ui_layout import result_display_columns


class LayoutSupportHost(Protocol):
    """Minimal host state consumed by responsive layout calculations."""

    root: tk.Misc
    dpi_scale: float
    zoom_factor: float
    font_scale: float


class LayoutSupport:
    """Apply responsive dimensions to page-local widgets owned by the GUI host."""

    def __init__(
        self,
        host: LayoutSupportHost,
        *,
        ui_config: Mapping[str, Any],
        font_family: str,
    ) -> None:
        self.host = host
        self.ui_config = ui_config
        self.font_family = font_family

    def update_run_page_dynamic_heights(self) -> None:
        """Use tall-window surplus for the run log without changing normal layout."""
        log_text = getattr(self.host, "log_text", None)
        if log_text is None:
            return
        extra_rows = self.get_tall_window_extra_rows()
        try:
            log_text.configure(height=min(40, 20 + extra_rows))
        except tk.TclError:
            return

    def update_result_stats_compact(self) -> None:
        """Hide result-card icons when a short window needs more table height."""
        host = self.host
        cards = getattr(host, "_result_stat_icon_canvases", None)
        if not cards:
            return
        try:
            window_height = int(host.root.winfo_height())
        except (tk.TclError, ValueError):
            return
        if window_height <= 0:
            return
        compact = window_height < 820
        if compact == getattr(host, "_result_stats_compact", False):
            return
        host._result_stats_compact = compact
        icon_pady = (
            int(12 * host.dpi_scale * host.zoom_factor),
            int(4 * host.dpi_scale * host.zoom_factor),
        )
        for icon_canvas, value_label in cards:
            try:
                if compact:
                    icon_canvas.pack_forget()
                else:
                    icon_canvas.pack(anchor="center", pady=icon_pady, before=value_label)
            except tk.TclError:
                pass

    def is_window_maximized(self) -> bool:
        """Return whether the main window is maximized or effectively fullscreen."""
        root = self.host.root
        try:
            if root.state() == "zoomed":
                return True
            return (
                root.winfo_width() >= root.winfo_screenwidth() * 0.9
                and root.winfo_height() >= root.winfo_screenheight() * 0.85
            )
        except (tk.TclError, ValueError):
            return False

    def update_result_tree_columns(self) -> None:
        """Keep every result field available and size it for horizontal scrolling."""
        tree = getattr(self.host, "result_tree", None)
        if tree is None:
            return
        try:
            tree_width = int(tree.winfo_width())
        except (tk.TclError, ValueError):
            tree_width = 0
        display_columns = result_display_columns(
            tree_width,
            maximized=self.is_window_maximized(),
        )
        self.apply_result_tree_column_widths(display_columns)
        if tuple(tree.cget("displaycolumns")) != display_columns:
            tree.configure(displaycolumns=display_columns)

    def tree_header_floors(
        self,
        tree: Any,
        display_columns: Sequence[str],
        min_widths: Mapping[str, int],
    ) -> dict[str, int]:
        """Return measured header-width floors including sort and padding overhead."""
        host = self.host
        scale = host.dpi_scale * host.zoom_factor
        overhead = int(30 * scale)
        try:
            measure_font = tkfont.Font(
                font=(
                    self.font_family,
                    int(12 * getattr(host, "font_scale", 1.0)),
                    "bold",
                )
            )
            return {
                column: max(
                    min_widths[column],
                    measure_font.measure(
                        str(tree.heading(column).get("text", "") or "")
                    )
                    + overhead,
                )
                for column in display_columns
            }
        except (tk.TclError, RuntimeError, AttributeError):
            return {column: min_widths[column] for column in display_columns}

    @staticmethod
    def distribute_tree_surplus(
        widths: MutableMapping[str, int],
        flexible_columns: Sequence[str],
        floors: Mapping[str, int],
        base_widths: Mapping[str, int],
        growth_caps: Mapping[str, int],
        extra: int,
    ) -> None:
        """Distribute surplus by base-width weight, respecting growth caps first."""
        while extra > 0:
            eligible = [
                column
                for column in flexible_columns
                if widths[column] < max(growth_caps[column], floors[column])
            ]
            if not eligible:
                break
            total_weight = sum(base_widths[column] for column in eligible)
            allocated = 0
            for column in eligible:
                share = min(
                    extra * base_widths[column] // total_weight,
                    max(growth_caps[column], floors[column]) - widths[column],
                )
                widths[column] += share
                allocated += share
            if allocated <= 0:
                break
            extra -= allocated
        if extra > 0:
            total_weight = sum(base_widths[column] for column in flexible_columns)
            allocated = 0
            for column in flexible_columns[:-1]:
                share = extra * base_widths[column] // total_weight
                widths[column] += share
                allocated += share
            widths[flexible_columns[-1]] += extra - allocated

    def apply_result_tree_column_widths(
        self,
        display_columns: Sequence[str],
    ) -> None:
        """Keep readable result widths and prefer horizontal overflow to compression."""
        tree = getattr(self.host, "result_tree", None)
        if tree is None:
            return
        base_widths = {
            "name": 80,
            "gender": 55,
            "exp": 85,
            "salary": 85,
            "skills": 85,
            "score": 70,
            "ai_eval": 70,
            "level": 80,
            "status": 180,
            "age": 70,
            "education": 90,
            "job_status": 130,
            "school": 150,
            "company": 160,
        }
        min_widths = {
            "name": 60,
            "gender": 48,
            "exp": 70,
            "salary": 70,
            "skills": 70,
            "score": 60,
            "ai_eval": 60,
            "level": 70,
            "status": 150,
            "age": 60,
            "education": 80,
            "job_status": 90,
            "school": 120,
            "company": 125,
        }
        try:
            available_width = max(0, int(tree.winfo_width()) - 2)
        except (tk.TclError, ValueError):
            available_width = 0

        fixed_columns = {"gender", "age", "education"}
        flexible_columns = [
            column for column in display_columns if column not in fixed_columns
        ]
        floors = self.tree_header_floors(tree, display_columns, min_widths)
        widths = {
            column: max(base_widths[column], floors[column])
            for column in display_columns
        }
        growth_caps = {
            "name": 130,
            "gender": 65,
            "exp": 115,
            "salary": 120,
            "skills": 130,
            "score": 95,
            "ai_eval": 95,
            "level": 120,
            "status": 260,
            "age": 80,
            "education": 110,
            "job_status": 170,
            "school": 280,
            "company": 320,
        }
        content_width = sum(widths.values())
        if available_width > content_width and flexible_columns:
            self.distribute_tree_surplus(
                widths,
                flexible_columns,
                floors,
                base_widths,
                growth_caps,
                available_width - content_width,
            )
        for column in display_columns:
            tree.column(
                column,
                width=widths[column],
                minwidth=min_widths[column],
                stretch=False,
            )

    def update_stats_tree_columns(self) -> None:
        """Rebalance stats-detail columns so wide windows fill the table."""
        tree = getattr(self.host, "stats_tree", None)
        if tree is None:
            return
        base_widths = {
            "job": 200,
            "filter_dist": 175,
            "greeted": 100,
            "feedback": 80,
            "suitable_rate": 75,
            "false_positive_rate": 75,
            "replied": 100,
            "interviewed": 100,
            "avg_score": 65,
        }
        min_widths = {
            "job": 150,
            "filter_dist": 140,
            "greeted": 80,
            "feedback": 65,
            "suitable_rate": 60,
            "false_positive_rate": 60,
            "replied": 80,
            "interviewed": 80,
            "avg_score": 55,
        }
        growth_caps = {
            "job": 340,
            "filter_dist": 260,
            "greeted": 150,
            "feedback": 120,
            "suitable_rate": 110,
            "false_positive_rate": 110,
            "replied": 150,
            "interviewed": 150,
            "avg_score": 100,
        }
        columns = list(base_widths)
        try:
            available_width = max(0, int(tree.winfo_width()) - 2)
        except (tk.TclError, ValueError):
            available_width = 0

        floors = self.tree_header_floors(tree, columns, min_widths)
        widths = dict(base_widths)
        stretch = True
        floor_total = sum(floors.values())
        if available_width > max(sum(base_widths.values()), floor_total):
            widths.update(floors)
            self.distribute_tree_surplus(
                widths,
                columns,
                floors,
                base_widths,
                growth_caps,
                available_width - floor_total,
            )
            stretch = False
        for column in columns:
            tree.column(
                column,
                width=widths[column],
                minwidth=min_widths[column],
                stretch=stretch,
            )

    def is_tall_window(self) -> bool:
        """Return whether height exceeds 85 percent of screen height, at least 1000 px."""
        root = self.host.root
        try:
            window_height = int(root.winfo_height())
            screen_height = int(root.winfo_screenheight())
        except (tk.TclError, ValueError):
            return False
        return window_height >= max(1000, int(screen_height * 0.85))

    def get_tall_window_extra_rows(self) -> int:
        """Return extra visible rows for pages that can use fullscreen height."""
        if not self.is_tall_window():
            return 0
        try:
            window_height = int(self.host.root.winfo_height())
        except (tk.TclError, ValueError):
            return 0
        return max(2, (window_height - self.ui_config["window_base_height"]) // 70)

    def update_config_page_dynamic_heights(self) -> None:
        """Increase config text and list heights only for tall windows."""
        host = self.host
        extra_rows = self.get_tall_window_extra_rows()
        requirement_extra_rows = 0 if extra_rows == 0 else max(1, extra_rows // 2)
        requirement_rows = min(
            24,
            self.ui_config["text_height_large"] + requirement_extra_rows,
        )
        skills_rows = min(18, self.ui_config["treeview_height"] + extra_rows * 2)
        try:
            requirement_text = getattr(host, "requirement_text", None)
            if requirement_text is not None:
                requirement_text.configure(height=requirement_rows)
            skills_tree = getattr(host, "skills_tree", None)
            if skills_tree is not None:
                skills_tree.configure(height=skills_rows)
        except tk.TclError:
            return

    def get_model_list_max_rows(self) -> int:
        """Return saved-model list max rows for the current window height."""
        base_rows = 6
        if not self.is_tall_window():
            return base_rows
        try:
            window_height = int(self.host.root.winfo_height())
        except (tk.TclError, ValueError):
            return base_rows
        extra_rows = max(
            0,
            (window_height - self.ui_config["window_base_height"]) // 42,
        )
        return min(18, max(10, base_rows + extra_rows))

    def update_model_list_height(self) -> None:
        """Resize the saved-model Treeview without changing normal-window layout."""
        tree = getattr(self.host, "model_list_tree", None)
        if tree is None:
            return
        try:
            row_count = len(tree.get_children())
            max_rows = self.get_model_list_max_rows()
            tree["height"] = max(1, min(row_count, max_rows))
        except tk.TclError:
            return

    def update_model_list_columns(self) -> None:
        """Fit saved-model columns while preserving the wider 4K layout."""
        tree = getattr(self.host, "model_list_tree", None)
        if tree is None:
            return
        display = ("name", "provider", "compat", "base_url")
        if tuple(tree.cget("displaycolumns")) != display:
            tree.configure(displaycolumns=display)

        if self.is_window_maximized():
            widths = {
                "name": 400,
                "provider": 300,
                "compat": 220,
                "base_url": 380,
            }
        else:
            widths = {
                "name": 320,
                "provider": 260,
                "compat": 190,
                "base_url": 360,
            }
        min_widths = {
            "name": 180,
            "provider": 160,
            "compat": 120,
            "base_url": 170,
        }
        try:
            available_width = max(0, int(tree.winfo_width()) - 24)
        except (tk.TclError, ValueError):
            available_width = 0

        overflow = sum(widths.values()) - available_width
        if available_width > 0 and overflow > 0:
            for column in ("provider", "base_url", "compat", "name"):
                reducible = max(0, widths[column] - min_widths[column])
                reduction = min(reducible, overflow)
                widths[column] -= reduction
                overflow -= reduction
                if overflow <= 0:
                    break
            if overflow > 0:
                widths["base_url"] = max(
                    min_widths["base_url"],
                    widths["base_url"] - overflow,
                )
        for column in display:
            tree.column(
                column,
                width=widths[column],
                minwidth=min_widths[column],
                stretch=column == "base_url",
            )

    def update_education_queue_columns(self) -> None:
        """Keep the education-queue status column visible on 1080p screens."""
        tree = getattr(self.host, "education_queue_tree", None)
        if tree is None:
            return
        base_widths = {
            "file": 230,
            "name": 120,
            "number": 160,
            "school": 175,
            "major": 210,
            "status": 140,
        }
        min_widths = {
            "file": 150,
            "name": 80,
            "number": 130,
            "school": 130,
            "major": 150,
            "status": 120,
        }
        widths = dict(base_widths)
        try:
            available_width = max(0, int(tree.winfo_width()) - 24)
        except (tk.TclError, ValueError):
            available_width = 0

        overflow = sum(widths.values()) - available_width
        if available_width > 0 and overflow > 0:
            for column in ("major", "file", "school", "name", "number", "status"):
                reducible = max(0, widths[column] - min_widths[column])
                reduction = min(reducible, overflow)
                widths[column] -= reduction
                overflow -= reduction
                if overflow <= 0:
                    break
        for column in ("file", "name", "number", "school", "major", "status"):
            tree.column(
                column,
                width=widths[column],
                minwidth=min_widths[column],
                anchor="w" if column == "file" else "center",
                stretch=column in ("file", "number", "school", "major"),
            )
