"""Interactive job-review locator preview backed only by synthetic data."""

from __future__ import annotations

import argparse
import os
import sys
import tkinter as tk
from collections.abc import Mapping, Sequence
from pathlib import Path
from tkinter import ttk
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Keep the same process-wide safety contract as the maintained GUI smoke scripts.
# This preview does not instantiate gui_main, but the flags protect future imports.
os.environ.setdefault("BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION", "1")
os.environ.setdefault("BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE", "1")
os.environ.setdefault("BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE", "1")

import gui_config_page  # noqa: E402
import gui_job_review  # noqa: E402
import gui_style_setup  # noqa: E402
import icons  # noqa: E402
import stats_presenter  # noqa: E402
import ui_theme  # noqa: E402
from gui_scroll_support import ScrollSupport  # noqa: E402
from gui_widget_support import WidgetSupport  # noqa: E402


PREVIEW_JOB_NAME = "合成数据 · 高级 Java 工程师"
EXPECTED_TARGETS = {
    "requirement",
    "education",
    "minimum_experience",
    "salary",
    "work_location",
    "skills",
    "required_conditions",
}


def build_synthetic_candidates() -> list[dict[str, Any]]:
    """Return five fictional feedback records covering every locator target."""
    return [
        {
            "name": "合成候选人 A",
            "match_score": 82,
            "greet_sent": True,
            "followup_status": "已回复",
            "feedback_status": "误推",
            "feedback_reasons": ["技能不匹配", "年限判断偏差"],
        },
        {
            "name": "合成候选人 B",
            "match_score": 76,
            "greet_sent": True,
            "followup_status": "待约面",
            "feedback_status": "误推",
            "feedback_reasons": ["学历/学校不符", "薪资不合适"],
        },
        {
            "name": "合成候选人 C",
            "match_score": 68,
            "greet_sent": True,
            "followup_status": "已回复",
            "feedback_status": "误杀",
            "feedback_reasons": ["地点不合适", "AI 高估"],
        },
        {
            "name": "合成候选人 D",
            "match_score": 61,
            "greet_sent": False,
            "followup_status": "未沟通",
            "feedback_status": "误杀",
            "feedback_reasons": ["规则过宽"],
        },
        {
            "name": "合成候选人 E",
            "match_score": 88,
            "greet_sent": True,
            "followup_status": "已约面",
            "feedback_status": "合适",
            "feedback_reasons": [],
        },
    ]


def build_synthetic_review() -> dict[str, Any]:
    """Build the production review model from fictional in-memory records."""
    return stats_presenter.build_job_review_model(
        PREVIEW_JOB_NAME,
        build_synthetic_candidates(),
    )


def review_targets(review: Mapping[str, Any]) -> set[str]:
    """Return config locator keys exposed by one review model."""
    return {
        str(item.get("config_target") or "")
        for item in review.get("recommendations", [])
        if isinstance(item, Mapping) and item.get("config_target")
    }


class PreviewHost:
    """Minimal visual host for the production workbench and locator modules."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.dpi_scale = 1.0
        self.zoom_factor = 1.0
        self.font_boost = 1.0
        self.font_scale = 1.0
        self.current_page_index = 1
        self._over_text_widget = False
        self.icons = icons.init(1.0)
        gui_style_setup.setup_styles(self)
        self.widget_support = WidgetSupport(
            self,
            ui_config={"label_frame_padding": 14},
        )
        self.scroll_support = ScrollSupport(self)
        self._job_config_review_targets: dict[
            str,
            gui_config_page.ConfigReviewTarget,
        ] = {}
        self._job_config_review_highlight = None
        self._job_config_review_highlight_after_id = None
        self._active_workbench: gui_job_review.JobReviewWidgets | None = None
        self.status_var = tk.StringVar(
            value="打开岗位复盘后，点击任一“定位…”按钮检查滚动和高亮。"
        )
        self.review = build_synthetic_review()
        self._build_page()

    def _build_page(self) -> None:
        self.root.title("岗位复盘定位预览（合成数据）")
        self.root.geometry("1000x720")
        self.root.minsize(820, 620)
        self.root.configure(bg=self.colors["bg_main"])

        header = tk.Frame(self.root, bg=self.colors["bg_main"])
        header.pack(fill="x", padx=24, pady=(20, 12))
        tk.Label(
            header,
            text="岗位复盘定位预览",
            font=self.font_section,
            fg=self.colors["text_primary"],
            bg=self.colors["bg_main"],
        ).pack(anchor="w")
        tk.Label(
            header,
            text=(
                "5 条合成反馈已达到展示门槛；不读取业务 JSON，"
                "不实例化主程序，也没有保存入口。"
            ),
            font=self.font_label,
            fg=self.colors["text_secondary"],
            bg=self.colors["bg_main"],
        ).pack(anchor="w", pady=(5, 0))

        actions = tk.Frame(self.root, bg=self.colors["bg_main"])
        actions.pack(fill="x", padx=24, pady=(0, 10))
        ttk.Button(
            actions,
            text="打开岗位复盘",
            style="Accent.TButton",
            command=self.open_review,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="回到页面顶部",
            command=lambda: self.config_canvas.yview_moveto(0),
        ).pack(side="left", padx=(10, 0))
        tk.Label(
            actions,
            textvariable=self.status_var,
            font=self.font_log,
            fg=self.colors["primary"],
            bg=self.colors["bg_main"],
            anchor="w",
            justify="left",
            wraplength=590,
        ).pack(side="left", fill="x", expand=True, padx=(18, 0))

        shell = ttk.Frame(self.root, style="Page.TFrame")
        shell.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        self.config_canvas, self.config_scrollable_frame = (
            self.scroll_support.create_scroll_container(
                shell,
                self.colors["bg_main"],
            )
        )
        # ScrollSupportHost requires these page canvas attributes. The preview has
        # one page, so all routes deliberately point to the same in-memory canvas.
        self.run_canvas = self.config_canvas
        self.education_canvas = self.config_canvas
        self.api_canvas = self.config_canvas
        self._build_config_cards()
        self.scroll_support.bind_mousewheel(
            self.config_canvas,
            self.config_scrollable_frame,
        )

    def _field_row(
        self,
        parent: tk.Misc,
        label: str,
        value: str,
    ) -> ttk.Frame:
        row = ttk.Frame(parent, style="TFrame")
        row.pack(fill="x", pady=8)
        ttk.Label(row, text=f"{label}：", width=13, font=self.font_label).pack(
            side="left"
        )
        ttk.Label(
            row,
            text=value,
            font=self.font_label,
            foreground=self.colors["text_secondary"],
        ).pack(side="left", fill="x", expand=True)
        return row

    def _build_config_cards(self) -> None:
        content = self.config_scrollable_frame
        card_pad = {"fill": "x", "padx": 18, "pady": 10}

        requirement = self.widget_support.create_card(
            content,
            "招聘需求",
            **card_pad,
        )
        self._field_row(
            requirement,
            "需求原文",
            "负责企业级 Java 平台建设，要求能独立完成服务设计与交付。",
        )

        basic = self.widget_support.create_card(
            content,
            "基础筛选条件",
            **card_pad,
        )
        education = self._field_row(basic, "最低学历", "本科")
        experience = self._field_row(basic, "最低经验", "5 年")
        salary = self._field_row(basic, "薪资范围", "25K - 40K")
        location = self._field_row(basic, "工作地点", "南京 / 上海")

        skills = self.widget_support.create_card(
            content,
            "技能评分条件",
            **card_pad,
        )
        self._field_row(skills, "核心技能", "Java、Spring Boot、MySQL")
        self._field_row(skills, "优先技能", "微服务、分布式、云平台")

        required = self.widget_support.create_card(
            content,
            "必要条件",
            **card_pad,
        )
        self._field_row(required, "简单匹配", "统招本科")
        self._field_row(required, "OR", "微服务，分布式")

        self._job_config_review_targets = {
            "requirement": gui_config_page.ConfigReviewTarget(
                "招聘需求",
                requirement,
                requirement.master,
            ),
            "education": gui_config_page.ConfigReviewTarget(
                "最低学历",
                education,
                basic.master,
            ),
            "minimum_experience": gui_config_page.ConfigReviewTarget(
                "最低经验",
                experience,
                basic.master,
            ),
            "salary": gui_config_page.ConfigReviewTarget(
                "薪资范围",
                salary,
                basic.master,
            ),
            "work_location": gui_config_page.ConfigReviewTarget(
                "工作地点",
                location,
                basic.master,
            ),
            "skills": gui_config_page.ConfigReviewTarget(
                "技能评分条件",
                skills,
                skills.master,
            ),
            "required_conditions": gui_config_page.ConfigReviewTarget(
                "必要条件",
                required,
                required.master,
            ),
        }

    def _set_requirement_section_expanded(self, _expanded: bool) -> None:
        """Match the production locator host contract; preview is always expanded."""

    def _show_feedback_note(self) -> None:
        self.status_var.set(
            "候选人明细未接入：本预览只包含 5 条内存合成记录。"
        )

    def locate_recommendation(self, recommendation: Mapping[str, Any]) -> None:
        """Run the production locator and report the synthetic evidence used."""
        target_key = str(recommendation.get("config_target") or "")
        label = gui_config_page.locate_job_config_review_target(self, target_key)
        evidence = str(recommendation.get("evidence") or "").strip()
        if label is None:
            self.status_var.set(f"无法定位目标：{target_key or '未提供'}")
            return
        self.status_var.set(
            f"已定位并高亮“{label}”。复盘证据：{evidence}。未修改任何配置。"
        )
        self.root.lift()
        self.root.focus_force()

    def open_review(self) -> gui_job_review.JobReviewWidgets:
        """Open the production workbench over the synthetic config preview."""
        active = self._active_workbench
        if active is not None:
            try:
                if active.window.winfo_exists():
                    active.window.lift()
                    active.window.focus_force()
                    return active
            except tk.TclError:
                pass
        self._active_workbench = gui_job_review.build_job_review_workbench(
            self,
            job_name=PREVIEW_JOB_NAME,
            time_range="合成样本",
            review=self.review,
            callbacks=gui_job_review.JobReviewCallbacks(
                show_feedback_candidates=self._show_feedback_note,
                open_job_config=self.locate_recommendation,
                format_suggestion=stats_presenter.format_job_review_suggestion,
            ),
            font_family=ui_theme.FONT_FAMILY,
        )
        return self._active_workbench


def run_smoke(root: tk.Tk, preview: PreviewHost) -> None:
    """Build the workbench and exercise all seven production locator keys."""
    root.update()
    workbench = preview.open_review()
    root.update_idletasks()
    if not workbench.window.winfo_exists():
        raise RuntimeError("job review workbench did not open")
    workbench.close()
    for recommendation in preview.review["recommendations"]:
        target_key = str(recommendation.get("config_target") or "")
        if target_key not in EXPECTED_TARGETS:
            continue
        preview.locate_recommendation(recommendation)
        root.update_idletasks()
    if review_targets(preview.review) != EXPECTED_TARGETS:
        raise RuntimeError("synthetic review does not cover every locator target")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse preview-only command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="build and validate the preview, then exit without interaction",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the synthetic locator preview or its deterministic smoke mode."""
    args = parse_args(argv)
    review = build_synthetic_review()
    if review["feedback_count"] != stats_presenter.JOB_REVIEW_FEEDBACK_MINIMUM:
        raise RuntimeError("synthetic feedback count no longer matches the review threshold")
    if review_targets(review) != EXPECTED_TARGETS:
        raise RuntimeError("synthetic review no longer covers every locator target")

    root = tk.Tk()
    try:
        preview = PreviewHost(root)
        if args.smoke:
            run_smoke(root, preview)
            print("PASS synthetic job-review locator preview (7 targets, no business I/O)")
            return 0
        root.after(150, preview.open_review)
        root.mainloop()
        return 0
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
