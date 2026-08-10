"""Tk widget construction for the education-certificate page."""
from __future__ import annotations

import tkinter as tk
from collections.abc import Mapping
from dataclasses import dataclass
from tkinter import font, ttk
from typing import Any, Protocol


class ScrollSupport(Protocol):
    def create_scroll_container(
        self,
        parent: tk.Misc,
        bg_color: str,
        *,
        auto_hide_scrollbar: bool = False,
    ) -> tuple[tk.Canvas, ttk.Frame]: ...

    def bind_mousewheel(self, canvas: tk.Canvas, frame: tk.Misc) -> None: ...


class InputSupport(Protocol):
    def bind_entry_context_menu(self, entry: tk.Misc) -> None: ...


class FeedbackSupport(Protocol):
    def hide_tooltip(self, event: tk.Event | None = None) -> None: ...


class WidgetSupport(Protocol):
    def create_page_header(
        self,
        parent: tk.Misc,
        title: str,
        subtitle: str | None = None,
        top_padding: int = 0,
    ) -> tk.Misc: ...

    def create_card(
        self,
        parent: tk.Misc,
        title: str,
        **kwargs: Any,
    ) -> tk.Misc: ...


class LayoutSupport(Protocol):
    def update_education_queue_columns(self) -> None: ...


class EducationPageHost(Protocol):
    """Narrow host contract required to build the education page."""

    pages_frame: tk.Misc
    root: tk.Misc
    colors: Mapping[str, str]
    dpi_scale: float
    zoom_factor: float
    font_scale: float
    font_label: Any
    icons: Any
    _context_menus: list[tk.Menu]
    scroll_support: ScrollSupport
    input_support: InputSupport
    feedback_support: FeedbackSupport
    widget_support: WidgetSupport
    layout_support: LayoutSupport

    def _remove_current_education_image(self) -> None: ...

    def _select_education_images(self) -> None: ...

    def _on_education_queue_select(self, event: tk.Event | None = None) -> None: ...

    def _on_education_queue_motion(self, event: tk.Event) -> None: ...

    def _show_education_queue_context_menu(self, event: tk.Event) -> None: ...

    def _recognize_education_image(self) -> None: ...

    def _remove_selected_education_images(self) -> None: ...

    def _rotate_education_image_cw90(self) -> None: ...

    def _schedule_education_preview_render(self) -> None: ...

    def _fill_chsi_page(self) -> None: ...

    def _solve_captcha(self) -> None: ...


@dataclass(frozen=True)
class EducationPageWidgets:
    """Page-local state and widget references exposed to the GUI controller."""

    page: ttk.Frame
    canvas: tk.Canvas
    scrollable_frame: ttk.Frame
    items: dict[str, dict[str, Any]]
    current_id: str | None
    item_counter: int
    recognition_running: bool
    manual_rotation: dict[str, int]
    rotation_locked: set[str]
    file_var: tk.StringVar
    remove_button: ttk.Button
    queue_card: tk.Misc
    tree_font: font.Font
    queue_tree: ttk.Treeview
    queue_scrollbar: ttk.Scrollbar
    queue_menu: tk.Menu
    workspace: ttk.Frame
    rotate_button: tk.Label
    preview_label: tk.Label
    name_var: tk.StringVar
    number_var: tk.StringVar
    status_var: tk.StringVar
    warning_var: tk.StringVar
    recognize_button: ttk.Button
    fill_button: ttk.Button
    captcha_button: ttk.Button


def build_education_page(
    host: EducationPageHost,
    ui_config: Mapping[str, Any],
    *,
    font_family: str,
) -> EducationPageWidgets:
    """Build the education page without reading certificates or accessing AI/browser services."""
    scale = host.dpi_scale * host.zoom_factor
    page = ttk.Frame(host.pages_frame, style="Page.TFrame")
    host.widget_support.create_page_header(
        page,
        "学历核验",
        "导入毕业证书图片/PDF，识别姓名和证书编号；验证码与手机扫码由 HR 人工完成。",
    )

    scroll_frame = ttk.Frame(page, style="Page.TFrame")
    scroll_frame.pack(fill="both", expand=True)
    canvas, scrollable_frame = host.scroll_support.create_scroll_container(
        scroll_frame,
        host.colors["bg_main"],
        auto_hide_scrollbar=True,
    )
    content = scrollable_frame

    toolbar = host.widget_support.create_card(
        content,
        "毕业证书",
        fill="x",
        pady=(0, int(16 * scale)),
    )
    file_var = tk.StringVar(value="尚未导入毕业证书")
    ttk.Label(
        toolbar,
        textvariable=file_var,
        font=host.font_label,
        foreground=host.colors["text_secondary"],
    ).pack(side="left", fill="x", expand=True)
    remove_icon = host.icons.button("trash", host.colors["danger"])
    remove_button = ttk.Button(
        toolbar,
        text=" 移除当前",
        image=remove_icon,
        compound=tk.LEFT,
        command=host._remove_current_education_image,
        state="disabled",
    )
    remove_button._icon_ref = remove_icon
    remove_button.pack(side="right", padx=(10, 0))
    select_icon = host.icons.button("folder", host.colors["text_primary"])
    select_button = ttk.Button(
        toolbar,
        text=" 导入证书",
        image=select_icon,
        compound=tk.LEFT,
        command=host._select_education_images,
    )
    select_button._icon_ref = select_icon
    select_button.pack(side="right")

    queue_content = host.widget_support.create_card(
        content,
        "待核验队列",
        fill="x",
        pady=(0, int(16 * scale)),
    )
    queue_card = queue_content.master
    queue_columns = ("file", "name", "number", "school", "major", "status")
    education_style = ttk.Style()
    education_style.configure(
        "Education.Treeview",
        font=(font_family, int(10 * host.font_scale)),
        rowheight=int(ui_config["treeview_rowheight"] * scale),
    )
    education_style.configure(
        "Education.Treeview.Heading",
        font=(font_family, int(11 * host.font_scale), "bold"),
    )
    education_style.configure(
        "Education.Vertical.TScrollbar",
        width=max(14, int(16 * scale)),
        arrowsize=max(14, int(16 * scale)),
        background=host.colors.get("border_strong", host.colors["border"]),
        troughcolor=host.colors.get("bg_footer", host.colors["bg_main"]),
        bordercolor=host.colors["border"],
        arrowcolor=host.colors["text_secondary"],
        lightcolor=host.colors.get("border_strong", host.colors["border"]),
        darkcolor=host.colors.get("border_strong", host.colors["border"]),
    )
    education_style.map(
        "Education.Vertical.TScrollbar",
        background=[
            ("active", host.colors["text_secondary"]),
            ("pressed", host.colors["text_secondary"]),
        ],
    )
    tree_font = font.Font(
        family=font_family,
        size=int(10 * host.font_scale),
    )
    queue_tree = ttk.Treeview(
        queue_content,
        columns=queue_columns,
        show="headings",
        height=5,
        selectmode="extended",
        style="Education.Treeview",
    )
    for column, title, width in (
        ("file", "文件", 230),
        ("name", "姓名", 120),
        ("number", "证书编号", 160),
        ("school", "学校", 175),
        ("major", "专业", 210),
        ("status", "状态", 140),
    ):
        queue_tree.heading(column, text=title)
        queue_tree.column(
            column,
            width=width,
            minwidth=80,
            anchor="w" if column == "file" else "center",
            stretch=column in ("file", "number", "school", "major"),
        )
    queue_scrollbar = ttk.Scrollbar(
        queue_content,
        orient="vertical",
        command=queue_tree.yview,
        style="Education.Vertical.TScrollbar",
    )
    queue_tree.configure(yscrollcommand=queue_scrollbar.set)
    queue_content.columnconfigure(0, weight=1)
    queue_content.rowconfigure(0, weight=1)
    queue_tree.grid(row=0, column=0, sticky="nsew")
    queue_scrollbar.grid(row=0, column=1, sticky="ns")
    queue_scrollbar.grid_remove()
    queue_tree.bind("<<TreeviewSelect>>", host._on_education_queue_select)
    queue_tree.bind("<Motion>", host._on_education_queue_motion, add="+")
    queue_tree.bind("<Leave>", host.feedback_support.hide_tooltip, add="+")
    queue_tree.bind("<Button-3>", host._show_education_queue_context_menu)
    queue_tree.bind(
        "<Configure>",
        lambda _event: host.layout_support.update_education_queue_columns(),
        add="+",
    )
    queue_menu = tk.Menu(
        host.root,
        tearoff=0,
        font=(font_family, int(11 * host.font_scale)),
    )
    queue_menu.add_command(
        label="识别证书",
        command=host._recognize_education_image,
    )
    queue_menu.add_command(
        label="删除证书",
        command=host._remove_selected_education_images,
    )
    host._context_menus.append(queue_menu)

    workspace = ttk.Frame(
        content,
        style="Page.TFrame",
        height=max(420, int(440 * scale)),
    )
    workspace.pack(fill="both", expand=True)
    workspace.pack_propagate(False)

    rotate_button: tk.Label | None = None

    def _build_rotate_button(title_bar: tk.Misc, padding: Any) -> None:
        nonlocal rotate_button
        title_bg = title_bar.cget("bg")
        rotate_button = tk.Label(
            title_bar,
            text="顺转 90°",
            font=host.font_label,
            fg=host.colors["primary"],
            bg=title_bg,
            cursor="hand2",
        )
        rotate_button.pack(side="right", padx=padding)
        rotate_button.bind(
            "<Button-1>",
            lambda _event: host._rotate_education_image_cw90(),
        )

    preview = host.widget_support.create_card(
        workspace,
        "证书预览",
        fill="both",
        expand=True,
        side="left",
        title_trailing_builder=_build_rotate_button,
    )
    if rotate_button is None:
        raise RuntimeError("证书预览旋转按钮未创建")

    preview_label = tk.Label(
        preview,
        text="请选择 JPG、JPEG、PNG、BMP、WEBP 图片或 PDF 文件",
        bg=host.colors["bg_card"],
        fg=host.colors["text_secondary"],
        font=host.font_label,
        justify="center",
    )
    preview_label.bind(
        "<Configure>",
        lambda _event: host._schedule_education_preview_render(),
    )
    preview_label.pack(fill="both", expand=True)

    form = host.widget_support.create_card(
        workspace,
        "识别结果",
        fill="both",
        expand=True,
        side="left",
        padx=(int(16 * scale), 0),
    )
    name_var = tk.StringVar()
    number_var = tk.StringVar()
    status_var = tk.StringVar(value="等待选择证书")
    warning_var = tk.StringVar(value="")

    ttk.Label(form, text="姓名", font=host.font_label).pack(anchor="w")
    name_entry = ttk.Entry(form, textvariable=name_var, font=host.font_label)
    name_entry.pack(fill="x", pady=(6, 16))
    host.input_support.bind_entry_context_menu(name_entry)
    ttk.Label(form, text="证书编号", font=host.font_label).pack(anchor="w")
    number_entry = ttk.Entry(form, textvariable=number_var, font=host.font_label)
    number_entry.pack(fill="x", pady=(6, 16))
    host.input_support.bind_entry_context_menu(number_entry)

    ttk.Label(
        form,
        textvariable=status_var,
        font=host.font_label,
        foreground=host.colors["primary"],
    ).pack(anchor="w", pady=(0, 8))
    ttk.Label(
        form,
        textvariable=warning_var,
        font=(font_family, int(10 * host.font_scale)),
        foreground=host.colors["warning"],
        wraplength=600,
        justify="left",
    ).pack(anchor="w", fill="x")

    actions = ttk.Frame(form, style="TFrame")
    actions.pack(fill="x", pady=(22, 0))
    recognize_icon = host.icons.button("search", host.colors["text_primary"])
    recognize_button = ttk.Button(
        actions,
        text=" 识别证书",
        image=recognize_icon,
        compound=tk.LEFT,
        command=host._recognize_education_image,
        state="disabled",
    )
    recognize_button._icon_ref = recognize_icon
    recognize_button.pack(side="left")
    fill_icon = host.icons.button("play", host.colors["text_primary"])
    fill_button = ttk.Button(
        actions,
        text=" 打开学信网验证",
        image=fill_icon,
        compound=tk.LEFT,
        command=host._fill_chsi_page,
        state="disabled",
    )
    fill_button._icon_ref = fill_icon
    fill_button.pack(side="left", padx=(10, 0))
    captcha_icon = host.icons.button("refresh", host.colors["text_primary"])
    captcha_button = ttk.Button(
        actions,
        text=" 重新识别验证码",
        image=captcha_icon,
        compound=tk.LEFT,
        command=host._solve_captcha,
        state="disabled",
    )
    captcha_button._icon_ref = captcha_icon
    captcha_button.pack(side="left", padx=(10, 0))

    ttk.Label(
        form,
        text="识别时图片/PDF 会发送当前配置的 AI 模型，请确认已取得候选人授权。",
        font=(font_family, int(10 * host.font_scale)),
        foreground=host.colors["text_secondary"],
        justify="left",
    ).pack(anchor="w", fill="x", pady=(20, 0))
    queue_card.pack_forget()
    host.scroll_support.bind_mousewheel(canvas, scrollable_frame)

    return EducationPageWidgets(
        page=page,
        canvas=canvas,
        scrollable_frame=scrollable_frame,
        items={},
        current_id=None,
        item_counter=0,
        recognition_running=False,
        manual_rotation={},
        rotation_locked=set(),
        file_var=file_var,
        remove_button=remove_button,
        queue_card=queue_card,
        tree_font=tree_font,
        queue_tree=queue_tree,
        queue_scrollbar=queue_scrollbar,
        queue_menu=queue_menu,
        workspace=workspace,
        rotate_button=rotate_button,
        preview_label=preview_label,
        name_var=name_var,
        number_var=number_var,
        status_var=status_var,
        warning_var=warning_var,
        recognize_button=recognize_button,
        fill_button=fill_button,
        captcha_button=captcha_button,
    )
