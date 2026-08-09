"""Render the supported CHANGELOG subset into an existing Tk text widget."""
from __future__ import annotations

from typing import Any

import ui_theme


def render_changelog_text(
    text_widget: Any,
    body: str,
    colors: dict[str, str],
    font_family: str,
    font_family_bold: str,
    font_scale: float,
    layout_scale: float,
    *,
    section_font_size: int = 13,
    item_font_size: int = 12,
    include_version_title: bool = False,
) -> None:
    """Render a small CHANGELOG Markdown subset into a Tk Text widget."""
    def scaled_font(size: int) -> int:
        return int(size * font_scale)

    def scaled_padding(value: int) -> int:
        return int(value * layout_scale)

    section_font = (font_family_bold, scaled_font(section_font_size))
    item_font = (font_family, scaled_font(item_font_size))
    item_bold_font = (font_family_bold, scaled_font(item_font_size))
    item_left_margin = scaled_padding(18)
    item_wrap_margin = scaled_padding(36)

    text_widget.tag_configure(
        "section_new",
        font=section_font,
        foreground=colors.get("success", ui_theme.SUCCESS),
    )
    text_widget.tag_configure(
        "section_opt", font=section_font, foreground=colors["primary"]
    )
    text_widget.tag_configure(
        "section_ui",
        font=section_font,
        foreground=colors.get("purple", ui_theme.PURPLE),
    )
    text_widget.tag_configure(
        "section_fix",
        font=section_font,
        foreground=colors.get("danger", ui_theme.DANGER),
    )
    text_widget.tag_configure(
        "section_build",
        font=section_font,
        foreground=colors.get("warning", ui_theme.WARNING),
    )
    text_widget.tag_configure(
        "item",
        font=item_font,
        foreground=colors["text_secondary"],
        lmargin1=item_left_margin,
        lmargin2=item_wrap_margin,
    )
    text_widget.tag_configure(
        "item_bold", font=item_bold_font, foreground=colors["text_primary"]
    )

    section_map = {
        "新增功能": "section_new",
        "体验优化": "section_opt",
        "行为优化": "section_opt",
        "性能优化": "section_opt",
        "UI 改进": "section_ui",
        "UI改进": "section_ui",
        "问题修复": "section_fix",
        "Bug 修复": "section_fix",
        "Bug修复": "section_fix",
        "构建改进": "section_build",
    }

    for line in body.splitlines():
        stripped = line.lstrip("#").strip()
        header_level = len(line) - len(line.lstrip("#"))
        is_section = (
            header_level in (2, 3)
            and bool(stripped)
            and not stripped.startswith("v")
        )

        if line.startswith("## v") and not include_version_title:
            continue
        if is_section:
            section_tag = section_map.get(stripped, "section_opt")
            text_widget.insert("end", "\n" + stripped + "\n\n", section_tag)
        elif line.startswith("- "):
            item_text = line[2:]
            if item_text.startswith("**"):
                end_pos = item_text.find("**", 2)
                if end_pos > 0:
                    title_part = item_text[2:end_pos]
                    rest = item_text[end_pos + 2 :]
                    full_text = "• " + title_part + rest + "\n"
                    line_start = text_widget.index("end")
                    text_widget.insert("end", full_text, "item")
                    bold_start = f"{line_start} + 2 chars"
                    bold_end = f"{line_start} + {2 + len(title_part)} chars"
                    text_widget.tag_add("item_bold", bold_start, bold_end)
                else:
                    text_widget.insert("end", "• " + item_text + "\n", "item")
            else:
                text_widget.insert("end", "• " + item_text + "\n", "item")
