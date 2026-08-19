"""Application shell for sidebar navigation and lazy page opening."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import IntEnum
import logging
import tkinter as tk
from tkinter import ttk
from typing import Any, Protocol

import ui_theme
from ui_messagebox import messagebox


logger = logging.getLogger(__name__)


class PageIndex(IntEnum):
    """Stable sidebar page identities shared by navigation and page logic."""

    HOME = 0
    CONFIG = 1
    RUN = 2
    RESULTS = 3
    EDUCATION = 4
    STATS = 5
    SETTINGS = 6


@dataclass(frozen=True)
class PageSpec:
    """Declarative identity and lazy builder names for one main page."""

    icon_name: str
    title: str
    page_attr: str
    creator_name: str
    show_name: str
    full_width: bool = False


PAGE_SPECS = {
    PageIndex.HOME: PageSpec(
        "home",
        "首页",
        "home_page",
        "create_home_page",
        "show_page_home",
    ),
    PageIndex.CONFIG: PageSpec(
        "briefcase", "岗位配置", "config_page", "_create_config_page_steps", "show_page_config"
    ),
    PageIndex.RUN: PageSpec(
        "play", "运行控制", "run_page", "_create_run_page_steps", "show_page_run"
    ),
    PageIndex.RESULTS: PageSpec(
        "filter", "筛选结果", "result_page", "create_result_page", "show_page_result"
    ),
    PageIndex.EDUCATION: PageSpec(
        "document", "学历核验", "education_page", "create_education_page", "show_page_education"
    ),
    PageIndex.STATS: PageSpec(
        "chart", "数据统计", "stats_page", "create_stats_page", "show_page_stats"
    ),
    PageIndex.SETTINGS: PageSpec(
        "gear", "系统设置", "api_config_page", "_create_api_config_page_steps", "show_page_api"
    ),
}
PRIMARY_NAV_PAGES = tuple(page for page in PageIndex if page is not PageIndex.SETTINGS)
TRAFFIC_LIGHT_BASE_SIZE = 32


class LayoutSupport(Protocol):
    """Responsive layout operations used by the application shell."""

    def update_model_list_height(self) -> None: ...

    def update_model_list_columns(self) -> None: ...

    def update_config_page_dynamic_heights(self) -> None: ...

    def update_run_page_dynamic_heights(self) -> None: ...

    def update_result_tree_columns(self) -> None: ...

    def update_result_stats_compact(self) -> None: ...

    def update_education_queue_columns(self) -> None: ...

    def update_stats_tree_columns(self) -> None: ...


class FeedbackSupport(Protocol):
    """Transient visual feedback cleared when the active page changes."""

    def hide_all_tooltips(self) -> None: ...


class AppShellHost(Protocol):
    """Explicit host surface consumed by the application shell."""

    root: tk.Misc
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    colors: dict[str, str]
    icons: Any
    current_page_index: int
    nav_labels: list[Any]
    nav_components: list[dict[str, Any]]
    _pending_page_builds: set[str]
    _pending_page_ready_callbacks: dict[str, list[Callable[[], None]]]
    _data_storage_error: str
    _page_loading_var: tk.StringVar
    _page_loading_frame: tk.Widget
    _highlighted_page_index: int | None
    _page_width_policy_after_id: str | None
    _last_page_pack_padx: int | None
    _last_page_pack_pady: int | None
    _active_page_widget: tk.Widget | None
    main_frame: tk.Widget
    pages_frame: tk.Widget
    home_page: tk.Widget | None
    config_page: tk.Widget | None
    api_config_page: tk.Widget | None
    run_page: tk.Widget | None
    result_page: tk.Widget | None
    stats_page: tk.Widget | None
    education_page: tk.Widget | None
    layout_support: LayoutSupport
    feedback_support: FeedbackSupport

    def _ensure_data_storage_available(self, action: str) -> bool: ...

    def _stop_browser_auto_check(self) -> None: ...

    def create_home_page(self) -> object | None: ...

    def _create_config_page_steps(self) -> Iterator[object]: ...

    def _create_run_page_steps(self) -> Iterator[object]: ...

    def create_result_page(self) -> object | None: ...

    def create_education_page(self) -> object | None: ...

    def create_stats_page(self) -> object | None: ...

    def _create_api_config_page_steps(self) -> Iterator[object]: ...

    def show_page_home(self) -> None: ...

    def show_page_config(self) -> None: ...

    def show_page_run(self) -> None: ...

    def show_page_result(self) -> None: ...

    def show_page_education(self) -> None: ...

    def show_page_stats(self) -> None: ...

    def show_page_api(self) -> None: ...

    def show_changelog(self) -> None: ...


class AppShell:
    """Own sidebar UI, page identities, lazy opening, and navigation state."""

    def __init__(
        self,
        host: AppShellHost,
        *,
        ui_config: dict[str, Any],
        font_family: str,
        font_family_semibold: str,
        version: str,
    ) -> None:
        self.host = host
        self.ui_config = ui_config
        self.font_family = font_family
        self.font_family_semibold = font_family_semibold
        self.version = version
        self._page_padding_states: dict[int, tuple[int, int, int]] = {}

    def create_sidebar(self) -> None:
        """Build the left navigation sidebar and retain its visual state."""
        host = self.host
        scale = host.dpi_scale * host.zoom_factor
        sidebar = ttk.Frame(
            host.root,
            style="Sidebar.TFrame",
            width=int(self.ui_config["sidebar_width"] * scale),
        )
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        self._create_logo(sidebar, scale)
        ttk.Separator(sidebar, orient="horizontal").pack(
            fill="x", padx=0, pady=int(10 * scale)
        )

        pill_bg = self._configure_nav_styles()
        host.nav_labels = []
        host.nav_components = []
        for page_index in PRIMARY_NAV_PAGES:
            self._create_nav_item(sidebar, page_index, scale, pill_bg)

        ttk.Separator(sidebar, orient="horizontal").pack(
            fill="x", padx=0, pady=int(10 * scale)
        )
        self._create_nav_item(sidebar, PageIndex.SETTINGS, scale, pill_bg)
        self._create_version_link(sidebar, scale)

    def _create_logo(self, sidebar: ttk.Frame, scale: float) -> None:
        """Build the sidebar logo without owning navigation state."""
        host = self.host
        logo_frame = ttk.Frame(sidebar, style="Sidebar.TFrame")
        logo_frame.pack(
            fill="x",
            padx=int(20 * scale),
            pady=(int(30 * scale), int(20 * scale)),
        )

        title_row = ttk.Frame(logo_frame, style="Sidebar.TFrame")
        title_row.pack(anchor="center")
        gap = int(4 * scale)
        logo_icon = host.icons.logo(
            "search_color",
            host.colors["text_sidebar_active"],
            host.colors["bg_sidebar"],
        )
        logo_icon_label = ttk.Label(
            title_row,
            image=logo_icon,
            background=host.colors["bg_sidebar"],
        )
        logo_icon_label._icon_ref = logo_icon
        logo_icon_label.pack(side="left")
        logo_text = ttk.Label(
            title_row,
            text="BOSS",
            font=(self.font_family_semibold, int(26 * host.font_scale)),
            foreground=host.colors["text_sidebar_active"],
            background=host.colors["bg_sidebar"],
        )
        logo_text.pack(side="left", padx=(gap, 0))

        subtitle_label = ttk.Label(
            logo_frame,
            text="简历筛选器",
            font=(self.font_family, int(16 * host.font_scale)),
            foreground=host.colors["text_sidebar_subtitle"],
            background=host.colors["bg_sidebar"],
        )
        subtitle_label.pack(anchor="center", pady=(int(6 * scale), 0))

    def _configure_nav_styles(self) -> str:
        """Register sidebar navigation styles and return the active pill color."""
        host = self.host
        sidebar_nav_font_size = int(15 * host.font_scale)
        style = ttk.Style()
        pill_bg = host.colors.get("bg_sidebar_pill", ui_theme.BG_SIDEBAR_PILL)
        style.configure(
            "SidebarNav.TLabel",
            font=(self.font_family, sidebar_nav_font_size),
            foreground=host.colors["text_sidebar"],
            background=host.colors["bg_sidebar"],
        )
        style.configure(
            "SidebarNavSelected.TLabel",
            font=(self.font_family_semibold, sidebar_nav_font_size),
            foreground=host.colors["text_sidebar_active"],
            background=host.colors["bg_sidebar"],
        )
        style.configure("SidebarPill.TFrame", background=pill_bg)
        style.configure(
            "SidebarNavPill.TLabel",
            font=(self.font_family, sidebar_nav_font_size),
            foreground=host.colors["text_sidebar_active"],
            background=pill_bg,
        )
        style.configure(
            "SidebarNavSelectedPill.TLabel",
            font=(self.font_family_semibold, sidebar_nav_font_size),
            foreground=host.colors["text_sidebar_active"],
            background=pill_bg,
        )
        return pill_bg

    def _create_nav_item(
        self,
        sidebar: ttk.Frame,
        page_index: PageIndex,
        scale: float,
        pill_bg: str,
    ) -> None:
        """Build one sidebar navigation item and bind its visual state."""
        host = self.host
        page_spec = PAGE_SPECS[page_index]
        idx = int(page_index)
        emoji_padx = int(14 * scale)
        text_padx = int(10 * scale)
        nav_outer_padx = int(12 * scale)
        badge_font = (self.font_family, int(10 * host.font_scale), "bold")

        def command() -> None:
            self.request_sidebar_page(page_index)

        icon_default = host.icons.nav(
            page_spec.icon_name,
            host.colors["text_sidebar"],
            host.colors["bg_sidebar"],
        )
        icon_active = host.icons.nav(
            page_spec.icon_name,
            host.colors["text_sidebar_active"],
            pill_bg,
        )
        nav_frame = ttk.Frame(sidebar, style="Sidebar.TFrame")
        nav_frame.pack(fill="x", padx=nav_outer_padx, pady=1)
        accent_bar = tk.Frame(
            nav_frame, width=3, background=host.colors["bg_sidebar"]
        )
        accent_bar.pack(side="left", fill="y")
        icon_label = ttk.Label(
            nav_frame,
            image=icon_default,
            style="SidebarNav.TLabel",
            cursor="hand2",
        )
        icon_label._icon_default = icon_default
        icon_label._icon_active = icon_active
        icon_label.pack(side="left", padx=(emoji_padx, 0))
        text_label = ttk.Label(
            nav_frame,
            text=page_spec.title,
            style="SidebarNav.TLabel",
            cursor="hand2",
            padding=(text_padx, int(14 * scale)),
        )
        text_label.pack(side="left", fill="x", expand=True)
        badge_label = tk.Label(
            nav_frame,
            text="",
            font=badge_font,
            cursor="hand2",
            background=host.colors["danger"],
            foreground="#FFFFFF",
            padx=int(5 * host.dpi_scale),
            pady=0,
        )
        for widget in (nav_frame, accent_bar, icon_label, text_label, badge_label):
            widget.bind("<Button-1>", lambda _event: command())
            widget.bind("<Enter>", lambda _event: self.on_nav_enter(idx))
            widget.bind("<Leave>", lambda _event: self.on_nav_leave(idx))
        host.nav_components.append(
            {
                "frame": nav_frame,
                "accent": accent_bar,
                "icon": icon_label,
                "icon_default": icon_default,
                "icon_active": icon_active,
                "text": text_label,
                "badge": badge_label,
                "command": command,
                "index": idx,
            }
        )
        host.nav_labels.append(text_label)

    def _create_version_link(self, sidebar: ttk.Frame, scale: float) -> None:
        """Build the bottom version link."""
        host = self.host
        bottom_frame = ttk.Frame(sidebar, style="Sidebar.TFrame")
        bottom_frame.pack(
            side="bottom",
            fill="x",
            padx=int(20 * scale),
            pady=int(20 * scale),
        )
        version_label = ttk.Label(
            bottom_frame,
            text=f"v{self.version}",
            font=(self.font_family, int(12 * host.font_scale)),
            foreground=host.colors["text_sidebar_version"],
            background=host.colors["bg_sidebar"],
            cursor="hand2",
        )
        version_label.pack(anchor="w")
        version_label.bind("<Button-1>", lambda _event: host.show_changelog())

    def request_sidebar_page(
        self,
        page_index: PageIndex | int,
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        """Navigate to a page, painting feedback before its first build."""
        host = self.host
        try:
            page = PageIndex(page_index)
        except (TypeError, ValueError):
            return
        if (
            str(getattr(host, "_data_storage_error", "") or "").strip()
            and page not in {PageIndex.HOME, PageIndex.SETTINGS}
        ):
            host._ensure_data_storage_available(f"打开“{PAGE_SPECS[page].title}”")
            return
        page_spec = PAGE_SPECS[page]
        self.request_page_first_open(
            page,
            page_spec.page_attr,
            page_spec.title,
            getattr(host, page_spec.creator_name),
            getattr(host, page_spec.show_name),
            on_ready=on_ready,
        )

    def request_page_first_open(
        self,
        page_index: int,
        page_attr: str,
        title: str,
        creator: Callable[[], object | None],
        show_page: Callable[[], None],
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        """Show a lightweight first frame, then build and cache a missing page."""
        host = self.host
        if not hasattr(host, "_pending_page_builds"):
            host._pending_page_builds = set()
        if not hasattr(host, "_pending_page_ready_callbacks"):
            host._pending_page_ready_callbacks = {}
        if on_ready is not None:
            host._pending_page_ready_callbacks.setdefault(page_attr, []).append(on_ready)

        def run_ready_callbacks() -> None:
            callbacks = host._pending_page_ready_callbacks.pop(page_attr, [])
            for callback in callbacks:
                try:
                    callback()
                except Exception:
                    logger.exception("%s页面就绪回调失败", title)

        def paint_loading_frame() -> None:
            self.deactivate_page_activity()
            host._active_page_widget = None
            host._page_loading_var.set(f"正在打开{title}…")
            if not host._page_loading_frame.winfo_manager():
                host._page_loading_frame.pack(fill="both", expand=True)
            host._page_loading_frame.lift()
            host.current_page_index = page_index
            self.schedule_page_width_policy()
            self.update_nav_highlight()

        if page_attr in host._pending_page_builds:
            paint_loading_frame()
            return
        if getattr(host, page_attr, None) is not None:
            if getattr(host, "current_page_index", None) == page_index and on_ready is None:
                return
            show_page()
            run_ready_callbacks()
            return

        paint_loading_frame()
        host._pending_page_builds.add(page_attr)

        def discard_partial_page() -> None:
            partial_page = getattr(host, page_attr, None)
            if partial_page is not None:
                try:
                    partial_page.destroy()
                except tk.TclError:
                    pass
                setattr(host, page_attr, None)

        def advance(iterator: Iterator[object] | None = None) -> None:
            if (
                iterator is None
                and getattr(host, "current_page_index", None) != page_index
            ):
                host._pending_page_builds.discard(page_attr)
                host._pending_page_ready_callbacks.pop(page_attr, None)
                return
            host._pending_page_builds.discard(page_attr)
            try:
                if iterator is None:
                    build_result = creator()
                    if isinstance(build_result, Iterator):
                        iterator = build_result
                    else:
                        show_page()
                        run_ready_callbacks()
                        return
                next(iterator)
            except StopIteration:
                if getattr(host, "current_page_index", None) == page_index:
                    show_page()
                    run_ready_callbacks()
                else:
                    # Once staged construction has started, finish and cache the
                    # page instead of destroying a half-built widget tree. This
                    # avoids stale host references and duplicate context menus.
                    host._pending_page_ready_callbacks.pop(page_attr, None)
                return
            except Exception as exc:
                logger.exception("首次创建%s页面失败", title)
                host._pending_page_ready_callbacks.pop(page_attr, None)
                discard_partial_page()
                if getattr(host, "current_page_index", None) == page_index:
                    host._page_loading_var.set(f"{title}打开失败")
                    messagebox.showerror(
                        "页面打开失败",
                        f"{title}页面打开失败：{exc}",
                        parent=host.root,
                    )
                return

            host._pending_page_builds.add(page_attr)
            host.root.after(1, lambda: advance(iterator))

        host.root.after(30, advance)

    def schedule_page_width_policy(self) -> None:
        """Coalesce layout work without starving it during configure storms."""
        host = self.host
        if host._page_width_policy_after_id is not None:
            return

        def apply_policy() -> None:
            host._page_width_policy_after_id = None
            self.apply_page_width_policy()

        host._page_width_policy_after_id = host.root.after(60, apply_policy)

    def apply_page_width_policy(self) -> None:
        """Center bounded pages and refresh the current page's responsive layout."""
        host = self.host
        if not hasattr(host, "pages_frame") or not hasattr(host, "main_frame"):
            return

        scale = host.dpi_scale * host.zoom_factor
        current_page = getattr(host, "current_page_index", PageIndex.HOME)
        if current_page == PageIndex.HOME:
            try:
                root_width = int(host.root.winfo_width())
                root_height = int(host.root.winfo_height())
            except (tk.TclError, ValueError, AttributeError):
                root_width = root_height = 0
            logical_root_width = root_width / max(scale, 0.01) if root_width else 0
            logical_root_height = root_height / max(scale, 0.01) if root_height else 0
            base_pad_x = int(
                self.ui_config[
                    "home_page_padding_x_medium"
                    if logical_root_width and logical_root_width <= 1380
                    else "home_page_padding_x"
                ]
                * scale
            )
            base_pad_y = int(
                self.ui_config[
                    "home_page_padding_y_compact"
                    if logical_root_height and logical_root_height <= 820
                    else "home_page_padding_y"
                ]
                * scale
            )
        else:
            base_pad_x = int(self.ui_config["page_padding_x"] * scale)
            base_pad_y = int(self.ui_config["page_padding_y"] * scale)
        full_width_pages = {
            page for page, page_spec in PAGE_SPECS.items() if page_spec.full_width
        }
        if current_page in full_width_pages:
            target_pad_x = base_pad_x
        else:
            try:
                available_width = max(0, host.main_frame.winfo_width())
            except tk.TclError:
                available_width = 0
            width_key = (
                "home_content_max_width"
                if current_page == PageIndex.HOME
                else "content_max_width"
            )
            max_content_width = int(
                self.ui_config[width_key]
                * scale
            )
            extra_pad = max(0, (available_width - max_content_width) // 2)
            target_pad_x = max(base_pad_x, extra_pad)

        target_pad_y: int | tuple[int, int]
        if current_page == PageIndex.HOME:
            # Preserve a small visual stop below the tools band. The responsive
            # workspace yields this space first on compact-height windows.
            target_pad_y = (base_pad_y, int(12 * scale))
        elif current_page == PageIndex.CONFIG:
            target_pad_y = max(0, base_pad_y - int(15 * scale))
        else:
            target_pad_y = base_pad_y
        host._last_page_pack_padx = target_pad_x
        host._last_page_pack_pady = target_pad_y
        self._resize_active_page_to_viewport(target_pad_x, target_pad_y)
        self._refresh_current_page_layout(current_page)

    def _resize_active_page_to_viewport(
        self,
        padx: int,
        pady: int | tuple[int, int],
    ) -> None:
        """Resize only the active page; inactive cached pages keep frozen geometry."""
        host = self.host
        page = getattr(host, "_active_page_widget", None)
        if page is None:
            return
        try:
            if isinstance(pady, tuple):
                pad_top, pad_bottom = (int(pady[0]), int(pady[1]))
            else:
                pad_top = pad_bottom = int(pady)
            padding_state = (int(padx), pad_top, pad_bottom)
            page_key = id(page)
            if self._page_padding_states.get(page_key) != padding_state:
                page.configure(
                    padding=(int(padx), pad_top, int(padx), pad_bottom)
                )
                self._page_padding_states[page_key] = padding_state

            try:
                target_width = max(1, int(host.pages_frame.winfo_width()))
                target_height = max(1, int(host.pages_frame.winfo_height()))
            except (tk.TclError, TypeError, ValueError):
                target_width = target_height = 1
            manager = page.winfo_manager()
            if manager != "place":
                if manager == "pack":
                    page.pack_forget()
                elif manager == "grid":
                    page.grid_forget()
                page.place(x=0, y=0, width=target_width, height=target_height)
                return
            place_info = page.place_info()
            try:
                matches_viewport = (
                    int(place_info.get("x", 0) or 0) == 0
                    and int(place_info.get("y", 0) or 0) == 0
                    and int(place_info.get("width", 0) or 0) == target_width
                    and int(place_info.get("height", 0) or 0) == target_height
                    and float(place_info.get("relwidth", 0) or 0) == 0.0
                    and float(place_info.get("relheight", 0) or 0) == 0.0
                )
            except (TypeError, ValueError, AttributeError):
                matches_viewport = False
            if matches_viewport:
                return
            page.place_configure(
                x=0,
                y=0,
                width=target_width,
                height=target_height,
                relx=0,
                rely=0,
                relwidth=0,
                relheight=0,
            )
        except (tk.TclError, TypeError, ValueError):
            return

    def _refresh_current_page_layout(self, current_page: int) -> None:
        """Delegate page-local responsive updates through the explicit host surface."""
        host = self.host
        layout = host.layout_support
        if current_page == PageIndex.HOME:
            layout.update_home_page_layout()
        elif current_page == PageIndex.SETTINGS:
            layout.update_model_list_height()
            layout.update_model_list_columns()
        elif current_page == PageIndex.CONFIG:
            layout.update_config_page_dynamic_heights()
        elif current_page == PageIndex.RUN:
            layout.update_run_page_dynamic_heights()
        elif current_page == PageIndex.RESULTS:
            layout.update_result_tree_columns()
            layout.update_result_stats_compact()
        elif current_page == PageIndex.EDUCATION:
            layout.update_education_queue_columns()
        elif current_page == PageIndex.STATS:
            layout.update_stats_tree_columns()

    def deactivate_page_activity(self) -> None:
        """Stop page-local activity without unmapping cached page widgets."""
        host = self.host
        host._stop_browser_auto_check()
        host.feedback_support.hide_all_tooltips()

    def activate_page(self, page: tk.Widget, page_index: PageIndex | int) -> None:
        """Raise one cached page while keeping the rest of the page stack mapped."""
        host = self.host
        self.deactivate_page_activity()
        host.current_page_index = PageIndex(page_index)
        host._active_page_widget = page

        # Apply the destination page policy before it becomes visible. This avoids
        # briefly painting the previous page's padding and responsive state.
        self.apply_page_width_policy()

        loading_frame = getattr(host, "_page_loading_frame", None)
        if loading_frame is not None and loading_frame is not page:
            try:
                if loading_frame.winfo_manager() == "pack":
                    loading_frame.pack_forget()
            except tk.TclError:
                pass

        try:
            page.lift()
        except tk.TclError:
            return

        self.schedule_page_width_policy()
        self.update_nav_highlight()

    def hide_all_pages(self) -> None:
        """Explicitly unmap every page while preserving the compatibility facade."""
        host = self.host
        self.deactivate_page_activity()
        for page in (
            getattr(host, "_page_loading_frame", None),
            host.home_page,
            host.config_page,
            host.api_config_page,
            host.run_page,
            host.result_page,
            host.stats_page,
            host.education_page,
        ):
            if page is not None:
                try:
                    manager = page.winfo_manager()
                    if manager == "place":
                        page.place_forget()
                    elif manager == "grid":
                        page.grid_forget()
                    elif manager == "pack":
                        page.pack_forget()
                except tk.TclError:
                    pass
        host._active_page_widget = None

    def update_nav_highlight(self) -> None:
        """Update only the previous and current navigation items."""
        host = self.host
        current_index = host.current_page_index
        previous_index = getattr(host, "_highlighted_page_index", None)
        if previous_index == current_index:
            return
        if previous_index is not None and 0 <= previous_index < len(host.nav_components):
            self.apply_nav_state(host.nav_components[previous_index], "default")
        if 0 <= current_index < len(host.nav_components):
            self.apply_nav_state(host.nav_components[current_index], "selected")
        host._highlighted_page_index = current_index

    def apply_nav_state(self, component: dict[str, Any], state: str) -> None:
        """Apply default, hover, or selected sidebar visuals."""
        host = self.host
        pill_bg = host.colors.get("bg_sidebar_pill", ui_theme.BG_SIDEBAR_PILL)
        active = state in ("hover", "selected")
        selected = state == "selected"
        component["frame"].configure(
            style="SidebarPill.TFrame" if active else "Sidebar.TFrame"
        )
        label_style = (
            "SidebarNavSelectedPill.TLabel" if selected else "SidebarNavPill.TLabel"
        ) if active else "SidebarNav.TLabel"
        component["icon"].configure(
            image=component["icon_active"] if active else component["icon_default"],
            style=label_style,
        )
        component["text"].configure(style=label_style)
        if "accent" in component:
            component["accent"].configure(
                background=(
                    host.colors["primary_light"]
                    if selected
                    else (pill_bg if active else host.colors["bg_sidebar"])
                )
            )

    def on_nav_enter(self, index: int) -> None:
        """Highlight a non-selected navigation item on pointer entry."""
        host = self.host
        if index != host.current_page_index:
            self.apply_nav_state(host.nav_components[index], "hover")

    def on_nav_leave(self, index: int) -> None:
        """Restore a non-selected navigation item on pointer exit."""
        host = self.host
        if index != host.current_page_index:
            self.apply_nav_state(host.nav_components[index], "default")

    def set_nav_badge(self, page_index: int, count: int) -> None:
        """Show or hide the numeric badge for one sidebar page."""
        host = self.host
        if not (0 <= page_index < len(host.nav_components)):
            return
        badge = host.nav_components[page_index].get("badge")
        if badge is None:
            return
        if count and count > 0:
            badge.configure(text=str(count if count < 100 else "99+"))
            if not badge.winfo_ismapped():
                badge.pack(side="right", padx=(0, int(12 * host.dpi_scale * host.zoom_factor)))
        else:
            badge.pack_forget()
