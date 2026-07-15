"""Generate privacy-safe screenshots for the user guide.

The script uses synthetic job and candidate data, so generated screenshots never
depend on local real candidate records.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import tkinter as tk
from PIL import ImageGrab


ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = Path(__file__).resolve().parent
DEMO_DATA_PATH = OUT_DIR / "_demo-candidates-user-guide.json"
DEMO_API_CONFIG_PATH = OUT_DIR / "_demo-api-config-user-guide.json"
sys.path.insert(0, str(ROOT))

import gui_main


DEMO_JOB = "证券IT开发工程师（演示岗位）"
DEMO_API_CONFIG = {
    "api_provider": "qwen",
    "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/v1",
    "model": "kimi-k2.6",
    "saved_models": [
        {
            "api_provider": "qwen",
            "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/v1",
            "model": "kimi-k2.6",
            "capability": {"status": "compatible", "output_mode": "tool"},
        },
        {
            "api_provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "capability": {"status": "compatible", "output_mode": "tool"},
        },
    ],
    "providers": {},
    "fetched_models": {},
    "llm_read_timeout": 60,
    "education_model_ref": {
        "api_provider": "qwen",
        "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/v1",
        "model": "kimi-k2.6",
    },
}
DEMO_JOB_RULE = {
    "min_exp": 4,
    "edu": "本科",
    "max_age": 38,
    "work_location": "南京/上海",
    "salary_min": 18,
    "salary_max": 25,
    "keywords": [
        {"name": "Java", "weight": 2},
        {"name": "Spring Cloud", "weight": 2},
        {"name": "MySQL", "weight": 1},
        {"name": "Redis", "weight": 1},
        {"name": "微服务", "weight": 2},
        {"name": "金融系统", "weight": 2},
    ],
    "preferred_keywords": [
        {"name": "证券行业", "bonus": 2},
        {"name": "高并发经验", "bonus": 2},
    ],
    "required_conditions": [
        "统招本科",
        {"type": "or", "items": ["债券", "基金", "期货", "期权"], "category": "金融投资行业经验"},
    ],
    "original_requirement": (
        "岗位：证券IT开发工程师（演示岗位）\n"
        "职位描述：负责证券业务系统设计、开发与优化。\n"
        "必要条件：统招本科，4年以上 Java 开发经验，熟悉 Spring Cloud、"
        "MySQL、Redis 和微服务架构；具备债券、基金、期货、期权任一金融投资行业经验。\n"
        "优先条件：证券行业经验、高并发系统经验优先。"
    ),
}


def _candidate(index: int, score: int, *, job: str = DEMO_JOB, greeted: bool = False, followup: str = "未沟通") -> dict:
    level = "强烈推荐" if score >= 75 else ("推荐" if score >= 65 else "待定")
    name = f"候选人{chr(64 + index)}"
    return {
        "geek_id": f"DEMO-{index:03d}",
        "name": name,
        "job_name": job,
        "batch_timestamp": f"202606{28 + index % 3:02d}_100000",
        "summary": (
            f"{name}  {28 + index}岁  {4 + index % 5}年经验  本科  南京  期望薪资20-25K\n"
            "教育经历：某高校 软件工程 本科 2014.09 2018.06\n"
            "工作经历：某金融科技公司 Java开发工程师 2020.03 至今\n"
            "工作职责：负责证券交易周边系统、客户服务平台和微服务架构改造。\n"
            "技能标签：Java、Spring Cloud、MySQL、Redis、微服务、金融系统"
        ),
        "structured": {
            "age": 28 + index,
            "exp_years": 4 + index % 5,
            "salary": "20-25K",
            "education": "本科",
            "city": "南京",
            "job_status": "在职-考虑机会",
        },
        "company": "某金融科技公司",
        "school": "某高校",
        "rule_score": score - 2,
        "match_score": score,
        "recommend_level": level,
        "skill_match_ratio": "6/6",
        "skill_matches": [
            {"name": "Java", "weight": 2},
            {"name": "Spring Cloud", "weight": 2},
            {"name": "MySQL", "weight": 1},
            {"name": "Redis", "weight": 1},
            {"name": "微服务", "weight": 2},
            {"name": "金融系统", "weight": 2},
        ],
        "keyword_evidence": [
            {"type": "skill", "name": "Spring Cloud", "weight": 2, "evidence": "参与微服务拆分和服务治理。"},
            {"type": "preferred", "name": "证券行业", "weight": 2, "evidence": "具备证券业务系统研发经历。"},
        ],
        "score_breakdown": {"base": 25, "skill": 38, "experience": 8, "education": 5, "preferred": 2},
        "score_explanation": ["学历和经验满足岗位要求。", "核心技术栈覆盖完整。", "金融系统经历与岗位相关。"],
        "llm_evaluated": True,
        "llm_adjustment": 2,
        "llm_model": "演示模型",
        "llm_reason": "候选人的金融系统和微服务研发经历与岗位要求匹配。",
        "resume_eval_adjustment": 3 if index == 1 else None,
        "resume_eval_reason": "完整简历补充证明候选人负责过核心模块设计。" if index == 1 else "",
        "qualification_status": "qualified",
        "qualification_evidence": ["学历、经验和核心技能均有明确材料支持。"],
        "manual_review_required": False,
        "greet_sent": greeted,
        "followup_status": followup,
        "feedback_status": "合适" if followup in {"已回复", "待约面", "已约面"} else "",
    }


def build_demo_candidates() -> list[dict]:
    return [
        _candidate(1, 86, greeted=True, followup="已回复"),
        _candidate(2, 79, greeted=True, followup="待约面"),
        _candidate(3, 76, greeted=True, followup="已约面"),
        _candidate(4, 72, greeted=True, followup="已打招呼"),
        _candidate(5, 69),
        _candidate(6, 66),
        _candidate(7, 63),
        _candidate(8, 59),
        _candidate(9, 82, job="数据分析工程师（演示岗位）", greeted=True, followup="已回复"),
        _candidate(10, 73, job="数据分析工程师（演示岗位）"),
    ]


def capture_widget(widget: tk.Widget, filename: str) -> None:
    try:
        widget.lift()
        widget.attributes("-topmost", True)
    except tk.TclError:
        pass
    widget.update_idletasks()
    widget.update()
    time.sleep(0.35)
    x = widget.winfo_rootx()
    y = widget.winfo_rooty()
    w = widget.winfo_width()
    h = widget.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(OUT_DIR / filename)
    try:
        widget.attributes("-topmost", False)
    except tk.TclError:
        pass


def capture_dialog(root: tk.Tk, title: str, filename: str) -> None:
    root.update_idletasks()
    root.update()
    for child in root.winfo_children():
        if isinstance(child, tk.Toplevel) and child.winfo_exists() and child.title() == title:
            child.lift()
            capture_widget(child, filename)
            child.destroy()
            return
    raise RuntimeError(f"Dialog not found: {title}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DEMO_DATA_PATH.write_text(json.dumps(build_demo_candidates(), ensure_ascii=False, indent=2), encoding="utf-8")
    DEMO_API_CONFIG_PATH.write_text(
        json.dumps(DEMO_API_CONFIG, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    gui_main.CANDIDATES_PATH = DEMO_DATA_PATH
    gui_main.CANDIDATES_XLSX_PATH = OUT_DIR / "_demo-candidates-user-guide.xlsx"
    gui_main.get_api_config_path = lambda for_write=False: DEMO_API_CONFIG_PATH
    gui_main.get_api_key = lambda provider, base_url=None: "demo-api-key"
    gui_main._enable_high_dpi_awareness()
    gui_main.BossFilterGUI._load_startup_updater = lambda self: None

    root = tk.Tk()
    root.withdraw()
    app = gui_main.BossFilterGUI(root)
    app.job_rules = {DEMO_JOB: DEMO_JOB_RULE}
    gui_main._show_main_window_centered(root, gui_main._get_windows_monitor_area())
    root.update()

    app.show_page_home()
    capture_widget(root, "01-home.png")

    app.show_page_config()
    app.config_job_combo["values"] = [DEMO_JOB]
    app.config_job_combo.set(DEMO_JOB)
    app.on_job_selected(None)
    app.config_canvas.yview_moveto(0.18)
    capture_widget(root, "02-job-config-full.png")
    app.config_canvas.yview_moveto(0.58)
    capture_widget(root, "02-job-config-skills.png")

    app.show_page_api()
    capture_widget(root, "03-api-config-full.png")

    app.show_page_run()
    app.job_combo["values"] = ["全部岗位", DEMO_JOB]
    app.job_combo.set(DEMO_JOB)
    app.run_canvas.yview_moveto(0.0)
    capture_widget(root, "04-run-full.png")

    app.show_page_result()
    app.refresh_results()
    capture_widget(root, "05-results.png")

    app.show_page_stats()
    app.refresh_stats()
    capture_widget(root, "06-stats.png")

    app.show_page_education()
    capture_widget(root, "09-education.png")

    app.show_changelog()
    capture_dialog(root, "更新日志", "07-changelog-dialog.png")

    root.destroy()


if __name__ == "__main__":
    main()
