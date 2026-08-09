"""Generate presentation screenshots from the current GUI with synthetic data.

The script replaces saved job configuration and candidate records with
in-memory or tracked synthetic fixtures before any page is captured.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageGrab


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = Path(__file__).resolve().parent
DEMO_DATA_PATH = OUT_DIR / "demo-candidates.json"
MENU_DEMO_DATA_PATH = OUT_DIR / "demo-candidates-menu.json"
SCREENSHOT_ALIASES = {
    "03-candidate-screening-results.png": ("02-candidate-screening-results.png",),
    "04-ai-evaluation-detail.png": ("03-ai-evaluation-detail.png",),
    "05-recruitment-data-dashboard.png": ("04-recruitment-data-dashboard.png",),
}
os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"] = "1"
sys.path.insert(0, str(ROOT))

import gui_main


DEMO_JOB = "证券IT开发工程师（演示岗位）"
DEMO_JOB_RULE = {
    "min_exp": 4,
    "edu": "本科",
    "max_age": 38,
    "work_location": "南京",
    "salary_min": 18,
    "salary_max": 25,
    "keywords": [
        {"name": "Java", "weight": 2},
        {"name": "Spring Cloud", "weight": 2},
        {"name": "MySQL", "weight": 1},
        {"name": "Redis", "weight": 1},
        {"name": "金融系统", "weight": 2},
        {"name": "微服务", "weight": 2},
    ],
    "preferred_keywords": [
        {"name": "证券行业", "weight": 2},
        {"name": "高并发", "weight": 1},
    ],
    "required_conditions": ["统招本科", "4年以上Java开发经验"],
    "original_requirement": (
        "岗位：证券IT开发工程师（演示岗位）\n"
        "负责证券业务系统的设计、开发与优化；熟悉 Java、Spring Cloud、"
        "MySQL、Redis 和微服务架构；具备 4 年以上开发经验，统招本科；"
        "有证券行业、高并发系统经验者优先。"
    ),
}


def build_demo_job_rules() -> dict[str, dict]:
    """Return an isolated, fully synthetic job configuration."""
    return {DEMO_JOB: copy.deepcopy(DEMO_JOB_RULE)}


def install_demo_job_config_source() -> None:
    """Prevent screenshot runs from reading the user's saved job configuration."""

    def load_demo_job_config(*_args, **_kwargs) -> dict:
        return {"job_requirements": build_demo_job_rules()}

    gui_main.load_job_config_snapshot = load_demo_job_config


def _candidate(
    index: int,
    score: int,
    *,
    job: str = DEMO_JOB,
    llm_adjustment: int = 0,
    resume_adjustment: int | None = None,
    greeted: bool = False,
    followup: str = "未沟通",
    feedback: str = "",
) -> dict:
    """Build one fully synthetic candidate record."""
    level = "强烈推荐" if score >= 75 else ("推荐" if score >= 65 else "待定")
    rule_score = score - llm_adjustment - (resume_adjustment or 0)
    name = f"候选人{chr(64 + index)}"
    company = f"某金融科技公司{index}"
    school = f"某重点大学{index}"
    summary = (
        f"{name}  {28 + index}岁  {4 + index % 5}年经验  本科  南京  期望薪资20-25K\n"
        f"教育经历：{school} 软件工程 本科 2014.09 2018.06\n"
        f"工作经历：{company} Java开发工程师 2020.03 至今\n"
        "工作职责：负责交易周边系统和客户服务平台建设，参与微服务拆分、"
        "接口性能优化和生产问题分析。\n"
        "技能标签：Java、Spring Cloud、MySQL、Redis、微服务、金融系统"
    )
    record = {
        "demo_data_origin": "fully_synthetic",
        "geek_id": f"DEMO-{index:03d}",
        "name": name,
        "job_name": job,
        "batch_timestamp": f"202606{20 + index % 3:02d}_100000",
        "summary": summary,
        "structured": {
            "age": 28 + index,
            "exp_years": 4 + index % 5,
            "salary": "20-25K",
            "education": "本科",
            "city": "南京",
            "job_status": "在职-考虑机会",
        },
        "_api_profile": {
            "educations": [
                {
                    "school": school,
                    "major": "软件工程",
                    "degree": "本科",
                    "start": "2014.09",
                    "end": "2018.06",
                }
            ],
            "works": [
                {
                    "company": company,
                    "position": "Java开发工程师",
                    "category": "金融科技",
                    "start": "2020.03",
                    "end": "至今",
                    "responsibility": "负责证券业务系统开发、微服务改造与性能优化。",
                    "skills": ["Java", "Spring Cloud", "MySQL", "Redis"],
                }
            ],
            "personal_summary": "具备金融系统研发经验，能够独立分析和处理复杂问题。",
        },
        "rule_score": rule_score,
        "match_score": score,
        "recommend_level": level,
        "skill_match_ratio": "6/6",
        "skill_matches": [
            {"name": "Java", "weight": 2},
            {"name": "Spring Cloud", "weight": 2},
            {"name": "MySQL", "weight": 1},
            {"name": "Redis", "weight": 1},
            {"name": "金融系统", "weight": 2},
            {"name": "微服务", "weight": 2},
        ],
        "keyword_evidence": [
            {
                "name": "Spring Cloud",
                "weight": 2,
                "type": "skill",
                "evidence": "参与核心系统微服务拆分和服务治理。",
            },
            {
                "name": "金融系统",
                "weight": 2,
                "type": "skill",
                "evidence": "持续参与交易周边和客户服务平台建设。",
            },
            {
                "name": "证券行业",
                "weight": 2,
                "type": "preferred",
                "evidence": "具备证券业务系统研发经历。",
            },
        ],
        "score_breakdown": {
            "base": 25,
            "skill": 38,
            "experience": 8,
            "education": 5,
            "preferred": 2,
            "ai_adjustment": llm_adjustment,
            "resume_adjustment": resume_adjustment or 0,
        },
        "score_explanation": [
            "学历和工作年限满足岗位要求。",
            "核心技术栈覆盖完整，具备金融系统开发经验。",
            "项目经历与岗位职责具有较高相关性。",
        ],
        "llm_evaluated": True,
        "llm_adjustment": llm_adjustment,
        "llm_model": "演示模型",
        "llm_reason": (
            "候选人的微服务研发和金融系统经历与岗位高度相关；"
            "能够提供性能优化和生产问题处理的具体经历。"
        ),
        "qualification_status": "qualified",
        "qualification_reasons": [],
        "qualification_evidence": ["学历、经验和核心技能均有明确材料支持。"],
        "manual_review_required": False,
        "greet_sent": greeted,
        "followup_status": followup,
        "feedback_status": feedback,
        "feedback_updated_at": "20260622_100000" if feedback else "",
        "resume_file": "候选人A_脱敏简历.pdf" if resume_adjustment is not None else "",
        "resume_imported_at": "20260622_093000" if resume_adjustment is not None else "",
        "resume_eval_adjustment": resume_adjustment,
        "resume_eval_reason": (
            "完整简历补充证明候选人曾负责核心模块设计，并主导接口性能优化；"
            "项目深度高于平台摘要所呈现的信息。"
            if resume_adjustment is not None
            else ""
        ),
        "resume_eval_model": "演示模型" if resume_adjustment is not None else "",
        "resume_eval_at": "20260622_094000" if resume_adjustment is not None else "",
        "risk_flags": [],
    }
    return record


def build_demo_candidates() -> list[dict]:
    """Create a varied dataset for result and statistics screenshots."""
    rows = [
        _candidate(1, 86, llm_adjustment=4, resume_adjustment=3, greeted=True, followup="已回复", feedback="合适"),
        _candidate(2, 79, llm_adjustment=3, greeted=True, followup="待约面", feedback="合适"),
        _candidate(3, 76, llm_adjustment=2, greeted=True, followup="已约面", feedback="合适"),
        _candidate(4, 72, llm_adjustment=2, greeted=True, followup="已回复"),
        _candidate(5, 69, llm_adjustment=1, greeted=False),
        _candidate(6, 66, llm_adjustment=1, greeted=True, followup="已打招呼", feedback="误推"),
        _candidate(7, 63, llm_adjustment=0, greeted=False),
        _candidate(8, 59, llm_adjustment=-1, greeted=False),
        _candidate(9, 82, job="数据分析工程师（演示岗位）", llm_adjustment=4, greeted=True, followup="已回复", feedback="合适"),
        _candidate(10, 73, job="数据分析工程师（演示岗位）", llm_adjustment=2, greeted=True, followup="已约面", feedback="合适"),
        _candidate(11, 67, job="数据分析工程师（演示岗位）", llm_adjustment=1, greeted=False),
        _candidate(12, 58, job="数据分析工程师（演示岗位）", llm_adjustment=-1, greeted=False),
    ]
    return rows


def write_demo_candidates(*paths: Path) -> None:
    """Write the deterministic synthetic dataset to one or more demo paths."""
    payload = json.dumps(build_demo_candidates(), ensure_ascii=False, indent=2) + "\n"
    for path in paths or (DEMO_DATA_PATH,):
        path.write_text(payload, encoding="utf-8")


def _badge_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = Path(r"C:\Windows\Fonts\msyh.ttc")
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    return ImageFont.load_default()


def add_privacy_badge(image: Image.Image) -> Image.Image:
    """Add a visible presentation badge to every final screenshot."""
    output = image.convert("RGB")
    draw = ImageDraw.Draw(output)
    text = "真实系统界面 · 完全合成数据"
    font = _badge_font(max(18, output.width // 70))
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x, pad_y = 16, 9
    x2 = output.width - 18
    x1 = x2 - text_w - pad_x * 2
    y1 = 18
    y2 = y1 + text_h + pad_y * 2
    draw.rounded_rectangle((x1, y1, x2, y2), radius=10, fill="#163A5F")
    draw.text((x1 + pad_x, y1 + pad_y - 2), text, font=font, fill="white")
    return output


def capture_widget(widget: tk.Widget, filename: str, *, privacy_badge: bool = True) -> None:
    """Capture one Tk window or page, optionally adding the privacy badge."""
    widget.update_idletasks()
    widget.update()
    time.sleep(0.45)
    x = widget.winfo_rootx()
    y = widget.winfo_rooty()
    width = widget.winfo_width()
    height = widget.winfo_height()
    image = ImageGrab.grab(bbox=(x, y, x + width, y + height))
    final_image = add_privacy_badge(image) if privacy_badge else image
    final_image.save(OUT_DIR / filename)
    for alias in SCREENSHOT_ALIASES.get(filename, ()):
        final_image.save(OUT_DIR / alias)


def find_toplevel(root: tk.Tk, title: str) -> tk.Toplevel:
    """Find a visible child dialog by title."""
    root.update_idletasks()
    root.update()
    for child in root.winfo_children():
        if isinstance(child, tk.Toplevel) and child.winfo_exists() and child.title() == title:
            return child
    raise RuntimeError(f"Dialog not found: {title}")


def find_text_widget(parent: tk.Widget) -> tk.Text:
    """Find the largest Text widget in a dialog."""
    found: list[tk.Text] = []

    def walk(widget: tk.Widget) -> None:
        for child in widget.winfo_children():
            if isinstance(child, tk.Text):
                found.append(child)
            walk(child)

    walk(parent)
    if not found:
        raise RuntimeError("No Text widget found")
    return max(found, key=lambda item: item.winfo_width() * item.winfo_height())


def select_demo_job(app: gui_main.BossFilterGUI) -> str:
    """Select the in-memory synthetic job used by every demo screenshot."""
    jobs = list(app.job_rules)
    if jobs != [DEMO_JOB]:
        raise RuntimeError("Synthetic demo job configuration was not installed")
    selected = DEMO_JOB
    app.config_job_combo["values"] = jobs
    app.config_job_combo.set(selected)
    app.on_job_selected(None)
    # Show the real structured fields and keywords.
    app.root.update_idletasks()
    app.root.update()
    app.config_canvas.configure(scrollregion=app.config_canvas.bbox("all"))
    app.config_canvas.yview_moveto(0.0)
    app.root.update_idletasks()
    app.root.update()
    return selected


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gui_main._enable_high_dpi_awareness()
    monitor_area = gui_main._get_windows_monitor_area()
    install_demo_job_config_source()

    root = tk.Tk()
    root.withdraw()
    app = gui_main.BossFilterGUI(root)
    gui_main._show_main_window_centered(root, monitor_area)
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    root.attributes("-topmost", False)

    app.show_page_config()
    selected_job = select_demo_job(app)
    capture_widget(root, "01-job-requirement-parsing.png")
    print(f"Captured synthetic job configuration: {selected_job}")

    app.config_canvas.yview_moveto(0.48)
    capture_widget(root, "06-job-config-skills-top.png")
    app.config_canvas.yview_moveto(1.0)
    capture_widget(root, "07-job-config-skills-bottom.png")

    app.show_page_run()
    app.job_combo["values"] = ["全部岗位", *list(app.job_rules)]
    app.job_combo.set(selected_job)
    app.run_canvas.yview_moveto(0.0)
    root.update_idletasks()
    root.update()
    capture_widget(root, "02-run-control.png")
    app.hide_all_pages()

    # Candidate screenshots always use deterministic, fully synthetic records.
    write_demo_candidates(DEMO_DATA_PATH, MENU_DEMO_DATA_PATH)
    gui_main.CANDIDATES_PATH = DEMO_DATA_PATH
    gui_main.CANDIDATES_XLSX_PATH = OUT_DIR / "sanitized-candidates.xlsx"

    app.show_page_result()
    root.update()
    app.refresh_results()
    root.update()
    capture_widget(root, "03-candidate-screening-results.png")

    first_item = app.result_tree.get_children()[0]
    app.result_tree.selection_set(first_item)
    app.result_tree.focus(first_item)
    app._show_candidate_detail(first_item)
    root.update()
    detail = find_toplevel(root, "候选人查看与复核")
    detail.lift()
    detail_text = find_text_widget(detail)
    ai_section = detail_text.search("【AI 一次评估】", "1.0", stopindex="end")
    if ai_section:
        detail_text.yview(ai_section)
        detail.update_idletasks()
        detail.update()
    capture_widget(detail, "04-ai-evaluation-detail.png")
    detail.grab_release()
    detail.destroy()

    app.show_page_stats()
    root.update()
    app.refresh_stats()
    root.update()
    capture_widget(root, "05-recruitment-data-dashboard.png")

    root.destroy()
    print(f"Generated screenshots in: {OUT_DIR}")


if __name__ == "__main__":
    main()
