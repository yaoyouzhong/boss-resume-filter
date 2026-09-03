"""Responsive window, page-height, and table-column layout support."""
from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
import tkinter as tk
from tkinter import font as tkfont
from typing import Any, Protocol
import unicodedata

from ui_layout import result_display_columns


class LayoutSupportHost(Protocol):
    """Minimal host state consumed by responsive layout calculations."""

    root: tk.Misc
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    standalone_education: bool
    font_label: object


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
        self._layout_states: dict[str, object] = {}
        self._header_measure_font: tuple[tuple[str, int], tkfont.Font] | None = None
        self._model_measure_font: tuple[object, tkfont.Font] | None = None

    def _state_is_current(self, key: str, state: object) -> bool:
        """Return whether a layout target has already been applied."""
        return self._layout_states.get(key) == state

    def _remember_state(self, key: str, state: object) -> None:
        """Remember one successfully applied layout target."""
        self._layout_states[key] = state

    @staticmethod
    def _configure_tree_column_if_changed(
        tree: Any,
        column: str,
        **options: Any,
    ) -> None:
        """Avoid a Tk geometry cascade when one Treeview column is unchanged."""
        try:
            current = tree.column(column)
        except (tk.TclError, TypeError, AttributeError):
            current = None
        if isinstance(current, Mapping):
            matches = True
            for key, value in options.items():
                current_value = current.get(key)
                if key in {"width", "minwidth"}:
                    try:
                        matches = int(current_value) == int(value)
                    except (TypeError, ValueError):
                        matches = False
                elif key == "stretch":
                    matches = bool(current_value) == bool(value)
                else:
                    matches = current_value == value
                if not matches:
                    break
            if matches:
                return
        tree.column(column, **options)

    def update_home_page_layout(self) -> None:
        """Reflow the home workbench from its rendered page width and height."""
        bundle = getattr(self.host, "_home_page_widgets", None)
        if bundle is None:
            return
        layout = bundle.layout
        scale = self.host.dpi_scale * self.host.zoom_factor

        def px(value: float) -> int:
            return max(1, int(round(value * scale)))

        try:
            rendered_width = int(bundle.page.winfo_width())
            page_height = int(bundle.page.winfo_height())
            root_height = int(self.host.root.winfo_height())
        except (tk.TclError, ValueError, AttributeError):
            return
        if rendered_width <= 1 or page_height <= 1 or root_height <= 1:
            return

        page_pad_x = int(getattr(self.host, "_last_page_pack_padx", 0) or 0)
        raw_page_pady = getattr(self.host, "_last_page_pack_pady", 0) or 0
        if isinstance(raw_page_pady, tuple):
            page_pad_y = int(raw_page_pady[0]) + int(raw_page_pady[1])
        else:
            page_pad_y = int(raw_page_pady) * 2
        rendered_width = max(1, rendered_width - page_pad_x * 2)
        page_height = max(1, page_height - page_pad_y)

        width = rendered_width / max(scale, 0.01)
        height = root_height / max(scale, 0.01)
        mode = "wide" if width >= 1120 else "medium" if width >= 880 else "stacked"
        compact = height <= 820
        header_stacked = width < 960
        header_row_height = px(92 if compact else 101)
        candidate_row_height = px(76 if compact else 88)
        tools_row_height = px(94 if compact else 100)
        section_gap = px(12 if compact else 16)
        workspace_cap = px(520 if compact else 598)
        workspace_floor = px(390)
        workspace_height = min(
            workspace_cap,
            max(
                workspace_floor,
                page_height
                - header_row_height
                - candidate_row_height
                - tools_row_height
                - section_gap,
            ),
        )
        state = (mode, compact, header_stacked, workspace_height)
        if state == getattr(self.host, "_home_layout_state", None):
            return
        self.host._home_layout_state = state

        controls = layout.header_controls
        controls.grid_forget()
        if not header_stacked:
            controls.grid(
                row=0,
                column=1,
                sticky="se",
                padx=(px(24), 0),
                pady=(px(7), 0),
            )
        else:
            controls.grid(
                row=1,
                column=0,
                columnspan=2,
                sticky="ew",
                pady=(px(12), 0),
            )
        controls.grid_columnconfigure(
            0,
            minsize=px(270 if mode == "wide" else 250),
        )
        controls.grid_columnconfigure(1, minsize=px(98))
        controls.grid_columnconfigure(2, minsize=px(126))

        workspace = layout.workspace
        layout.action_panel.grid_forget()
        layout.readiness_panel.grid_forget()
        workspace.grid_columnconfigure(0, weight=1, minsize=0)
        workspace.grid_columnconfigure(1, weight=0, minsize=0)
        workspace.grid_rowconfigure(0, weight=1)
        workspace.grid_rowconfigure(1, weight=0)
        if mode == "stacked":
            workspace.grid_propagate(True)
            layout.action_panel.grid(row=0, column=0, sticky="nsew")
            layout.readiness_panel.grid(
                row=1,
                column=0,
                sticky="nsew",
                pady=(px(12), 0),
            )
        else:
            workspace.configure(height=workspace_height)
            workspace.grid_propagate(False)
            readiness_width = 352 if mode == "wide" else 330
            workspace.grid_columnconfigure(1, minsize=px(readiness_width))
            layout.action_panel.grid(
                row=0,
                column=0,
                sticky="nsew",
                padx=(0, px(16)),
            )
            layout.readiness_panel.grid(row=0, column=1, sticky="nsew")

        layout.maintenance_frame.grid_forget()
        if mode == "stacked":
            layout.maintenance_frame.grid(
                row=1,
                column=0,
                columnspan=4,
                sticky="w",
                pady=(px(10), 0),
            )
        else:
            layout.maintenance_frame.grid(
                row=0,
                column=3,
                sticky="e",
                padx=(px(10), 0),
            )

        task_min_height = px(86 if compact else 94)
        for row_index, weight in enumerate((23, 90, 62)):
            layout.task_grid.grid_rowconfigure(
                row_index,
                minsize=task_min_height,
                weight=weight,
            )
        action_width = 132 if mode == "wide" else 128 if mode == "medium" else 116
        for widgets in bundle.task_widgets.values():
            widgets.action_box.configure(
                width=px(action_width),
                height=px(38),
            )

        health_min_height = px(58 if compact else 66)
        for row_index in range(3):
            layout.health_list.grid_rowconfigure(
                row_index,
                minsize=health_min_height,
                weight=0,
            )

        heading_width = px(112 if mode == "wide" else 96)
        first_width = px(248 if mode == "wide" else 236)
        second_width = px(278 if mode == "wide" else 266)
        layout.tools_content.grid_columnconfigure(0, minsize=heading_width)
        layout.tools_content.grid_columnconfigure(1, minsize=first_width)
        layout.tools_content.grid_columnconfigure(2, minsize=second_width)
        for tile, tile_width in zip(
            layout.tool_tiles,
            (first_width, second_width),
            strict=True,
        ):
            tile.configure(
                width=tile_width,
                height=px(72),
            )

        layout.action_header.configure(
            padx=px(28),
            pady=px(12 if compact else 14),
        )
        layout.tools_content.configure(
            padx=px(20),
            pady=px(9 if compact else 14),
        )
        bundle.page.grid_rowconfigure(0, minsize=header_row_height, weight=0)
        bundle.page.grid_rowconfigure(
            1,
            minsize=workspace_height + section_gap,
            weight=1 if mode == "stacked" else 0,
        )
        bundle.page.grid_rowconfigure(2, minsize=candidate_row_height, weight=0)
        bundle.page.grid_rowconfigure(3, minsize=tools_row_height, weight=0)
        bundle.page.grid_rowconfigure(4, minsize=0, weight=0 if mode == "stacked" else 1)
        layout.candidate_strip.configure(height=px(64 if compact else 72))
        layout.tools_band.configure(height=px(94 if compact else 100))
        layout.header.grid_configure(pady=(0, section_gap))
        layout.workspace.grid_configure(pady=(0, section_gap))
        layout.candidate_strip.grid_configure(pady=(0, section_gap))

    def update_run_page_dynamic_heights(self) -> None:
        """Use tall-window surplus for the run log without changing normal layout."""
        log_text = getattr(self.host, "log_text", None)
        if log_text is None:
            return
        extra_rows = self.get_tall_window_extra_rows()
        target_height = min(40, 20 + extra_rows)
        state = (id(log_text), target_height)
        if self._state_is_current("run_height", state):
            return
        try:
            log_text.configure(height=target_height)
        except tk.TclError:
            return
        self._remember_state("run_height", state)

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
        maximized = self.is_window_maximized()
        try:
            heading_texts = tuple(
                str(tree.heading(column).get("text", "") or "")
                for column in result_display_columns(tree_width, maximized=maximized)
            )
        except (tk.TclError, TypeError, AttributeError):
            heading_texts = ()
        state = (id(tree), tree_width, maximized, heading_texts)
        if self._state_is_current("result_columns", state):
            return
        display_columns = result_display_columns(tree_width, maximized=maximized)
        self.apply_result_tree_column_widths(display_columns)
        if tuple(tree.cget("displaycolumns")) != display_columns:
            tree.configure(displaycolumns=display_columns)
        self._remember_state("result_columns", state)

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
            font_size = int(12 * getattr(host, "font_scale", 1.0))
            font_key = (self.font_family, font_size)
            if self._header_measure_font is None or self._header_measure_font[0] != font_key:
                self._header_measure_font = (
                    font_key,
                    tkfont.Font(font=(self.font_family, font_size, "bold")),
                )
            measure_font = self._header_measure_font[1]
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
            self._configure_tree_column_if_changed(
                tree,
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

        try:
            heading_texts = tuple(
                str(tree.heading(column).get("text", "") or "")
                for column in columns
            )
        except (tk.TclError, TypeError, AttributeError):
            heading_texts = ()
        state = (id(tree), available_width, heading_texts)
        if self._state_is_current("stats_columns", state):
            return

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
            self._configure_tree_column_if_changed(
                tree,
                column,
                width=widths[column],
                minwidth=min_widths[column],
                stretch=stretch,
            )
        self._remember_state("stats_columns", state)

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
        requirement_text = getattr(host, "requirement_text", None)
        skills_tree = getattr(host, "skills_tree", None)
        state = (
            id(requirement_text),
            id(skills_tree),
            requirement_rows,
            skills_rows,
        )
        if self._state_is_current("config_heights", state):
            return
        try:
            if requirement_text is not None:
                requirement_text.configure(height=requirement_rows)
            if skills_tree is not None:
                skills_tree.configure(height=skills_rows)
        except tk.TclError:
            return
        self._remember_state("config_heights", state)

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
            target_height = max(1, min(row_count, max_rows))
            state = (id(tree), target_height)
            if self._state_is_current("model_height", state):
                return
            tree["height"] = target_height
        except tk.TclError:
            return
        self._remember_state("model_height", state)

    def update_model_list_columns(self) -> None:
        """Fit saved-model columns while preserving the wider 4K layout."""
        tree = getattr(self.host, "model_list_tree", None)
        if tree is None:
            return
        display = ("name", "provider", "compat", "base_url")
        if tuple(tree.cget("displaycolumns")) != display:
            tree.configure(displaycolumns=display)

        standalone = bool(getattr(self.host, "standalone_education", False))
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
        if standalone:
            min_widths["name"] = 220
            widths["name"] = self._standalone_model_name_width(tree)
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
        state = (
            id(tree),
            tuple((column, widths[column]) for column in display),
        )
        if self._state_is_current("model_columns", state):
            return
        for column in display:
            self._configure_tree_column_if_changed(
                tree,
                column,
                width=widths[column],
                minwidth=min_widths[column],
                stretch=column == "base_url",
            )
        self._remember_state("model_columns", state)

    @staticmethod
    def _text_display_units(text: object) -> int:
        """Approximate ttk character units while counting wide CJK glyphs correctly."""
        return sum(
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
            for character in str(text or "")
        )

    def _standalone_model_name_width(self, tree: Any) -> int:
        """Size the standalone model-name column from its rendered content."""
        names: list[str] = []
        try:
            for item_id in tree.get_children():
                values = tree.item(item_id).get("values", ())
                if values:
                    names.append(str(values[0] or ""))
        except (tk.TclError, TypeError, AttributeError):
            names = []

        fallback_width = max(
            (self._text_display_units(name) * 9 for name in names),
            default=0,
        )
        measured_width = fallback_width
        try:
            font_spec = getattr(
                self.host,
                "font_label",
                (self.font_family, int(12 * self.host.font_scale)),
            )
            if self._model_measure_font is None or self._model_measure_font[0] != font_spec:
                self._model_measure_font = (font_spec, tkfont.Font(font=font_spec))
            measure_font = self._model_measure_font[1]
            measured_width = max(
                (measure_font.measure(name) for name in names),
                default=0,
            )
        except (tk.TclError, RuntimeError, TypeError, AttributeError):
            pass

        horizontal_padding = int(40 * self.host.dpi_scale * self.host.zoom_factor)
        return max(220, min(520, measured_width + horizontal_padding))

    def update_standalone_model_selector_width(self, labels: Sequence[str]) -> None:
        """Fit the standalone active-model selector to its longest visible label."""
        if not getattr(self.host, "standalone_education", False):
            return
        combo = getattr(self.host, "default_model_combo", None)
        if combo is None:
            return
        longest = max(
            (self._text_display_units(label) for label in labels if label),
            default=0,
        )
        target_width = max(46, min(72, longest + 4))
        state = (id(combo), target_width)
        if self._state_is_current("standalone_model_selector", state):
            return
        try:
            combo.configure(width=target_width)
        except (tk.TclError, AttributeError):
            return
        self._remember_state("standalone_model_selector", state)

    def update_education_queue_columns(self) -> None:
        """Keep education workflow and screenshot statuses visible on narrow screens."""
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
            "screenshot": 100,
        }
        min_widths = {
            "file": 150,
            "name": 80,
            "number": 130,
            "school": 130,
            "major": 150,
            "status": 120,
            "screenshot": 90,
        }
        widths = dict(base_widths)
        try:
            available_width = max(0, int(tree.winfo_width()) - 24)
        except (tk.TclError, ValueError):
            available_width = 0

        overflow = sum(widths.values()) - available_width
        if available_width > 0 and overflow > 0:
            for column in (
                "major", "file", "school", "name", "number", "status", "screenshot"
            ):
                reducible = max(0, widths[column] - min_widths[column])
                reduction = min(reducible, overflow)
                widths[column] -= reduction
                overflow -= reduction
                if overflow <= 0:
                    break
        columns = (
            "file", "name", "number", "school", "major", "status", "screenshot"
        )
        state = (
            id(tree),
            tuple((column, widths[column]) for column in columns),
        )
        if self._state_is_current("education_columns", state):
            return
        for column in columns:
            self._configure_tree_column_if_changed(
                tree,
                column,
                width=widths[column],
                minwidth=min_widths[column],
                anchor="w" if column == "file" else "center",
                stretch=column in ("file", "number", "school", "major"),
            )
        self._remember_state("education_columns", state)
