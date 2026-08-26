"""Generate privacy-safe screenshots for the user guide.

The script uses synthetic job and candidate data, so generated screenshots never
depend on local real candidate records.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import tkinter as tk
from PIL import Image, ImageGrab


# Keep screenshot generation isolated from the user's runtime data and updater.
os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"] = "1"


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
    "gender": "不限",
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
        "必要条件：统招本科，性别不限，4年以上 Java 开发经验，熟悉 Spring Cloud、"
        "MySQL、Redis 和微服务架构；具备债券、基金、期货、期权任一金融投资行业经验。\n"
        "优先条件：证券行业经验、高并发系统经验优先。"
    ),
}


def _candidate(index: int, score: int, *, job: str = DEMO_JOB, greeted: bool = False, followup: str = "未沟通") -> dict:
    level = "强烈推荐" if score >= 75 else ("推荐" if score >= 65 else "待定")
    name = f"候选人{chr(64 + index)}"
    today = datetime.now().date()
    return {
        "geek_id": f"DEMO-{index:03d}",
        "name": name,
        "job_name": job,
        "batch_timestamp": f"{today:%Y%m%d}_100000",
        "first_seen_at": f"{today:%Y%m%d}_100000",
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
            "gender": "男" if index % 2 else "女",
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
        "next_followup_at": (
            f"{today:%Y-%m-%d}"
            if followup == "已回复"
            else (f"{today + timedelta(days=1):%Y-%m-%d}" if followup == "待约面" else "")
        ),
        "feedback_status": "合适" if followup in {"已回复", "待约面", "已约面"} else "",
        "greet_context": (
            {
                "chat_start": {
                    "jid": "demo-jid",
                    "lid": "demo-lid",
                    "securityId": "demo-security",
                    "expectId": "demo-expect",
                }
            }
            if index == 5
            else {}
        ),
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
    # The GUI uses short after() callbacks for the initial reveal, page-width
    # adjustment, and deferred data refresh. Keep pumping Tk while the page
    # settles; a plain sleep blocks those callbacks and captures the blue
    # startup curtain instead of the requested page.
    deadline = time.monotonic() + 0.8
    while time.monotonic() < deadline:
        widget.update_idletasks()
        widget.update()
        time.sleep(0.05)
    if sys.platform == "win32":
        # Screen capture can return only the desktop when this script is run
        # from a background terminal.  PrintWindow renders the actual Tk
        # window and is independent of desktop focus or overlap.
        try:
            import ctypes
            import win32con
            import win32gui
            import win32ui
        except ImportError:
            x = widget.winfo_rootx()
            y = widget.winfo_rooty()
            w = widget.winfo_width()
            h = widget.winfo_height()
            ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(OUT_DIR / filename)
        else:
            hwnd = widget.winfo_id()
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top
            window_dc = win32gui.GetWindowDC(hwnd)
            source_dc = win32ui.CreateDCFromHandle(window_dc)
            memory_dc = source_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(source_dc, width, height)
            memory_dc.SelectObject(bitmap)
            try:
                ctypes.windll.user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), 2)
                info = bitmap.GetInfo()
                bits = bitmap.GetBitmapBits(True)
                image = Image.frombuffer(
                    "RGB",
                    (info["bmWidth"], info["bmHeight"]),
                    bits,
                    "raw",
                    "BGRX",
                    0,
                    1,
                )
                image.save(OUT_DIR / filename)
            finally:
                win32gui.DeleteObject(bitmap.GetHandle())
                memory_dc.DeleteDC()
                source_dc.DeleteDC()
                win32gui.ReleaseDC(hwnd, window_dc)
    else:
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


def crop_maximized_result_columns(root: tk.Tk, tree: tk.Widget, filename: str) -> None:
    """Crop the 4K capture to the result table's readable right-side columns."""
    image_path = OUT_DIR / filename
    image = Image.open(image_path)
    root_x = root.winfo_rootx()
    root_y = root.winfo_rooty()
    tree_x = tree.winfo_rootx() - root_x
    tree_y = tree.winfo_rooty() - root_y
    tree_right = tree_x + tree.winfo_width()
    items = tree.get_children()
    if items:
        _, row_y, _, row_height = tree.bbox(items[-1])
        tree_bottom = tree_y + row_y + row_height + 16
    else:
        tree_bottom = tree_y + min(tree.winfo_height(), 520)
    crop_box = (
        max(tree_x, tree_right - 1600),
        max(0, tree_y),
        min(image.width, tree_right),
        min(image.height, tree_bottom),
    )
    image.crop(crop_box).save(image_path)


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
    app.config_canvas.yview_moveto(0.42)
    capture_widget(root, "02-job-config-skills.png")

    app.show_page_api()
    capture_widget(root, "03-api-config-full.png")
    app.api_canvas.yview_moveto(1.0)
    capture_widget(root, "03-data-maintenance.png")
    app.api_canvas.yview_moveto(0.0)

    app.show_page_run()
    app.job_combo["values"] = ["全部岗位", DEMO_JOB]
    app.job_combo.set(DEMO_JOB)
    app.scan_advanced_toggle_label.event_generate("<Button-1>")
    app.run_canvas.yview_moveto(0.0)
    capture_widget(root, "04-run-full.png")

    app.show_page_result()
    app.refresh_results()
    capture_widget(root, "05-results.png")
    normal_geometry = root.geometry()
    root.state("zoomed")
    root.update()
    app.layout_support.update_result_tree_columns()
    capture_widget(root, "05-results-maximized.png")
    crop_maximized_result_columns(root, app.result_tree, "05-results-maximized.png")
    root.state("normal")
    root.geometry(normal_geometry)
    root.update()

    import gui_external_import_dialog

    import_widgets = gui_external_import_dialog.show_external_import_dialog(
        app,
        root,
        font_family=gui_main.FONT_FAMILY,
        job_names=[DEMO_JOB],
        default_job=DEMO_JOB,
        on_confirm=lambda _form: False,
        ai_enhance_available=True,
        ai_resume_eval_available=True,
        ai_model_label="qwen-plus",
        switch_factory=app.widget_support.create_switch,
    )
    import_widgets.file_var.set("王晨-高级Java工程师.pdf")
    import_widgets.name_var.set("王晨")
    import_widgets.channel_var.set("猎头")
    import_widgets.note_text.insert("1.0", "重点关注证券项目与大模型应用经验")
    import_widgets.ai_enhance_var.set(True)
    import_widgets.ai_resume_eval_var.set(True)
    import_widgets.window.update_idletasks()
    import_widgets.window.geometry(
        f"{max(1120, import_widgets.window.winfo_width())}x"
        f"{import_widgets.window.winfo_height()}"
    )
    capture_dialog(root, "导入外部候选人", "13-external-import.png")

    import gui_external_edit_dialog

    gui_external_edit_dialog.show_external_edit_dialog(
        app,
        root,
        font_family=gui_main.FONT_FAMILY,
        candidate_name="王晨",
        initial={
            "name": "王晨",
            "gender": "男",
            "age": "32",
            "education": "本科",
            "exp_years": "8",
            "salary": "30-35K",
            "city": "南京",
            "job_status": "在职",
            "school": "南京理工大学",
            "company": "某证券科技公司",
        },
        current_job=DEMO_JOB,
        job_names=[DEMO_JOB],
        on_confirm=lambda _form: False,
    )
    capture_dialog(root, "编辑候选人信息", "14-external-edit.png")

    app.show_daily_candidate_actions()
    capture_dialog(root, "今日待办", "10-today-tasks.png")

    review_candidate = next(
        candidate
        for candidate in app.result_tree_data
        if 55 <= int(candidate.get("match_score", 0)) < 65
    )
    review_candidate["qualification_status"] = "manual_review"
    review_candidate["manual_review_required"] = True
    review_candidate["qualification_reasons"] = ["学历形式待确认"]
    review_candidate["qualification_evidence"] = ["学历层级为本科，学历形式需人工核实。"]
    app._open_candidate_review_workbench(review_candidate, candidates=app.result_tree_data)
    capture_dialog(root, "候选人查看与复核", "11-review-workbench.png")
    app.candidate_review_window = None

    queue_candidates = build_demo_candidates()[4:6]
    queue_candidates[1]["greet_confirmation_pending"] = True
    queue_candidates[1]["greet_confirmation_reason"] = "上次发送结果需要人工核实"
    app._greet_queue_loaded = True
    app.greet_queue_items = [
        app._build_greet_queue_item(queue_candidates[0], source="user_guide"),
        app._build_greet_queue_item(queue_candidates[1], source="user_guide"),
    ]
    app.greet_queue_items[1]["status"] = "待核实"
    app.greet_queue_items[1]["message"] = "上次发送结果需要人工核实"
    app._show_greet_queue_dialog()
    capture_dialog(root, "联系候选人", "12-contact-workbench.png")
    app.greet_queue_window = None

    app.show_page_stats()
    app.refresh_stats()
    capture_widget(root, "06-stats.png")

    app.show_page_education()
    capture_widget(root, "09-education.png")

    app.show_changelog()
    capture_dialog(root, "更新日志", "07-changelog-dialog.png")

    import updater

    updater.show_update_dialog(
        root,
        {
            "current": gui_main.__version__,
            "latest": "2.31",
            "update_type": "version",
            "changelog_body": (
                "### 新增功能\n\n"
                "- 新增候选人处理能力。\n\n"
                "### 体验优化\n\n"
                "- 优化候选人处理流程和界面提示。"
            ),
            "release_info": {"body": ""},
            "download_url": "https://example.invalid/BOSS_ResumeFilter.exe",
        },
        gui=app,
    )
    capture_dialog(root, "发现新版本", "08-update-dialog.png")

    root.destroy()


if __name__ == "__main__":
    main()
