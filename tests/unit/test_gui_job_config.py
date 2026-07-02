import json
import queue
import re
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import Mock, patch

import gui_main
import icons
from gui_main import (
    BossFilterGUI,
    _optional_int_to_entry,
    _parse_optional_int_entry,
    _candidate_has_ai_eval,
    _resolve_rule_score,
)


def test_optional_max_age_none_displays_as_blank():
    assert _optional_int_to_entry(None) == ""


def test_optional_max_age_number_displays_as_number_text():
    assert _optional_int_to_entry(35) == "35"


def test_blank_max_age_saves_as_unlimited():
    assert _parse_optional_int_entry("", "最大年龄") is None
    assert _parse_optional_int_entry("   ", "最大年龄") is None


def test_invalid_max_age_is_rejected_with_field_name():
    try:
        _parse_optional_int_entry("None", "最大年龄")
    except ValueError as e:
        assert str(e) == "最大年龄必须为数字"
    else:
        raise AssertionError("invalid max age should raise ValueError")


def test_humanize_ai_parse_warning_replaces_internal_field_names():
    gui = BossFilterGUI.__new__(BossFilterGUI)

    text = gui._humanize_ai_parse_warning(
        "`keywords_add` 中的 Python weight 建议确认，required_conditions 里 OR 条件需要看一下"
    )

    assert "keywords" not in text
    assert "required_conditions" not in text
    assert "技能关键词" in text
    assert "权重" in text
    assert "必要条件" in text
    assert "满足任一项" in text


class _FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _FakeCombo(dict):
    def __init__(self):
        super().__init__()
        self.current_value = ""

    def set(self, value):
        self.current_value = value


def test_save_current_job_keeps_ai_preferred_keywords_as_preferred():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.job_name_var = _FakeVar("Python 工程师")
    gui.min_exp_var = _FakeVar("3")
    gui.max_age_var = _FakeVar("")
    gui.edu_var = _FakeVar("本科")
    gui.work_location_var = _FakeVar("")
    gui.salary_min_var = _FakeVar("")
    gui.salary_max_var = _FakeVar("")
    gui.skills_data = [
        {"name": "Python", "weight": 2, "source": "解析"},
        {"name": "证券行业", "weight": 2, "source": "AI优先"},
    ]
    gui.required_conditions_data = []
    gui.job_rules = {}
    gui.config_job_combo = _FakeCombo()
    gui._job_step_active = -1
    gui._hide_save_hint = Mock()
    gui._hide_job_step_bar = Mock()
    gui._show_btn_add_hint = Mock()
    gui._get_requirement_text = Mock(return_value="原始需求")
    gui.save_config = Mock()

    with patch("gui_main.messagebox.showinfo"), patch("gui_main.messagebox.showwarning"):
        gui.save_current_job()

    rule = gui.job_rules["Python 工程师"]
    assert rule["keywords"] == [{"name": "Python", "weight": 2}]
    assert rule["preferred_keywords"] == [{"name": "证券行业", "bonus": 2}]


def test_save_current_job_strips_required_condition_evidence_metadata():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.job_name_var = _FakeVar("Java 工程师")
    gui.min_exp_var = _FakeVar("5")
    gui.max_age_var = _FakeVar("35")
    gui.edu_var = _FakeVar("本科")
    gui.work_location_var = _FakeVar("南京")
    gui.salary_min_var = _FakeVar("")
    gui.salary_max_var = _FakeVar("")
    gui.skills_data = [{"name": "Java", "weight": 2, "source": "解析"}]
    gui.required_conditions_data = [
        {"type": "or", "items": ["债券", "基金"], "category": "金融投资行业经验", "_evidence": "原文"},
        "统招本科",
    ]
    gui.job_rules = {}
    gui.config_job_combo = _FakeCombo()
    gui._job_step_active = -1
    gui._hide_save_hint = Mock()
    gui._hide_job_step_bar = Mock()
    gui._show_btn_add_hint = Mock()
    gui._get_requirement_text = Mock(return_value="")
    gui.save_config = Mock()

    with patch("gui_main.messagebox.showinfo"), patch("gui_main.messagebox.showwarning"):
        gui.save_current_job()

    required = gui.job_rules["Java 工程师"]["required_conditions"]
    assert required[0] == {"type": "or", "items": ["债券", "基金"], "category": "金融投资行业经验"}
    assert "_evidence" not in required[0]


def test_parse_edit_snapshot_marks_user_changes_dirty_before_ai_result():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.edu_var = _FakeVar("本科")
    gui.min_exp_var = _FakeVar("3")
    gui.max_age_var = _FakeVar("35")
    gui.work_location_var = _FakeVar("南京")
    gui.salary_min_var = _FakeVar("15")
    gui.salary_max_var = _FakeVar("25")
    gui.skills_data = [{"name": "Python", "weight": 2, "source": "解析"}]
    gui.required_conditions_data = [{"type": "or", "items": ["债券", "基金"]}]

    gui._ai_parse_edit_snapshot = gui._snapshot_parse_edit_state()
    gui.min_exp_var.set("5")
    gui.skills_data.append({"name": "SQL", "weight": 1, "source": "手动"})

    assert gui._dirty_fields_since_parse_snapshot() == {"min_exp", "skills"}


def test_candidate_detail_groups_api_resume_sections():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {
        "name": "张三",
        "job_name": "数据分析师",
        "geek_id": "g-api-detail",
        "match_score": 80,
        "skill_match_ratio": "3/3",
        "greet_sent": False,
        "summary": "\n".join([
            "期望薪资：15-20K",
            "年龄：29岁",
            "学历：本科",
            "经验：6年",
            "教育经历：南京大学 计算机科学 本科 2014 2018",
            "工作经历：某证券公司 数据分析师 2020 至今",
            "工作职责：负责 ETL 调度、SQL 指标开发和 Python 数据分析",
            "技能标签：Python、SQL、ETL",
        ]),
    }

    detail = gui._format_candidate_detail(candidate)

    assert "【教育经历】" in detail
    assert "南京大学 计算机科学 本科 2014 2018" in detail
    assert "【工作经历】" in detail
    assert "某证券公司 数据分析师 2020 至今" in detail
    assert "【工作职责】" in detail
    assert "负责 ETL 调度、SQL 指标开发和 Python 数据分析" in detail
    assert "【技能标签】" in detail
    assert "Python、SQL、ETL" in detail
    assert "【候选人摘要】" in detail


class _FakeRoot:
    def __init__(self, state="normal", width=1500, height=950):
        self._state = state
        self._width = width
        self._height = height

    def state(self):
        return self._state

    def winfo_width(self):
        return self._width

    def winfo_height(self):
        return self._height

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080


class _FakeTree:
    def __init__(self, width):
        self._width = width
        self.displaycolumns = "#all"
        self.column_options = {}
        self.items = {}

    def winfo_width(self):
        return self._width

    def cget(self, key):
        assert key == "displaycolumns"
        return self.displaycolumns

    def configure(self, **kwargs):
        self.displaycolumns = kwargs["displaycolumns"]

    def column(self, column, **kwargs):
        self.column_options[column] = kwargs

    def exists(self, item):
        return item in self.items

    def item(self, item, **kwargs):
        if kwargs:
            self.items.setdefault(item, {})["values"] = kwargs["values"]
        return self.items.get(item, {})


class _FakeResultTree:
    def __init__(self):
        self.items = []
        self.tags = {}
        self.seen = []
        self.selection = []
        self.focused = None
        self.focus_set_called = False

    def get_children(self):
        return tuple(range(len(self.items)))

    def delete(self, item):
        self.items.pop(item)

    def tag_configure(self, tag, **kwargs):
        self.tags[tag] = kwargs

    def insert(self, parent, index, values=(), tags=()):
        self.items.append({"values": values, "tags": tags})
        return f"item-{len(self.items)}"

    def see(self, item):
        self.seen.append(item)

    def selection_set(self, item):
        self.selection = [item]

    def focus(self, item):
        self.focused = item

    def focus_set(self):
        self.focus_set_called = True


def test_result_tree_columns_expand_only_when_space_is_available():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = _FakeRoot()
    gui.result_tree = _FakeTree(1600)
    gui._update_result_tree_columns()
    assert len(gui.result_tree.displaycolumns) == 8

    gui.root = _FakeRoot(state="zoomed", width=1920, height=1040)
    gui.result_tree = _FakeTree(1400)
    gui._update_result_tree_columns()
    assert len(gui.result_tree.displaycolumns) == 11

    gui.result_tree = _FakeTree(1500)
    gui._update_result_tree_columns()
    assert len(gui.result_tree.displaycolumns) == 13
    assert gui.result_tree.displaycolumns[-2:] == ("school", "company")
    assert gui.result_tree.column_options["school"]["width"] > 150
    assert gui.result_tree.column_options["company"]["width"] > 170
    assert gui.result_tree.column_options["level"]["width"] < 110
    assert gui.result_tree.column_options["education"]["width"] == 140
    assert gui.result_tree.column_options["age"]["width"] == 110
    assert gui.result_tree.column_options["skills"]["width"] < 140
    assert gui.result_tree.column_options["name"]["stretch"] is False
    assert gui.result_tree.column_options["education"]["stretch"] is False
    assert gui.result_tree.column_options["skills"]["stretch"] is False
    assert gui.result_tree.column_options["school"]["stretch"] is False
    assert gui.result_tree.column_options["company"]["stretch"] is False
    assert sum(
        options["width"] for options in gui.result_tree.column_options.values()
    ) == 1498

    gui.root = _FakeRoot()
    gui.result_tree = _FakeTree(1500)
    gui._update_result_tree_columns()
    assert all(
        options["stretch"] is True
        for options in gui.result_tree.column_options.values()
    )
    assert gui.result_tree.column_options["skills"]["width"] == 85


def test_model_list_columns_keep_4k_widths_and_fit_narrow_screens():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = _FakeRoot(state="zoomed", width=3840, height=2000)
    gui.model_list_tree = _FakeTree(1800)

    gui._update_model_list_columns()

    assert gui.model_list_tree.column_options["name"]["width"] == 320
    assert gui.model_list_tree.column_options["provider"]["width"] == 280
    assert gui.model_list_tree.column_options["compat"]["width"] == 170
    assert gui.model_list_tree.column_options["edu_ref"]["width"] == 150

    gui.root = _FakeRoot(width=1920, height=1040)
    gui.model_list_tree = _FakeTree(920)
    gui._update_model_list_columns()

    widths_1080p = {
        column: options["width"]
        for column, options in gui.model_list_tree.column_options.items()
    }
    assert sum(widths_1080p.values()) <= 896
    assert widths_1080p["provider"] < 240
    assert widths_1080p["base_url"] >= 170

    gui.root = _FakeRoot(width=2560, height=1400)
    gui.model_list_tree = _FakeTree(980)
    gui._update_model_list_columns()

    widths_2k = {
        column: options["width"]
        for column, options in gui.model_list_tree.column_options.items()
    }
    assert sum(widths_2k.values()) <= 956
    assert widths_2k["provider"] < 240


def test_education_queue_columns_keep_status_visible_on_narrow_screens():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.education_queue_tree = _FakeTree(1300)

    gui._update_education_queue_columns()

    assert gui.education_queue_tree.column_options["file"]["width"] == 230
    assert gui.education_queue_tree.column_options["major"]["width"] == 210
    assert gui.education_queue_tree.column_options["status"]["width"] == 140

    gui.education_queue_tree = _FakeTree(950)
    gui._update_education_queue_columns()

    widths_1080p = {
        column: options["width"]
        for column, options in gui.education_queue_tree.column_options.items()
    }
    assert sum(widths_1080p.values()) <= 926
    assert widths_1080p["status"] >= 120
    assert widths_1080p["major"] < 210

    gui.education_queue_tree = _FakeTree(1030)
    gui._update_education_queue_columns()

    widths_2k = {
        column: options["width"]
        for column, options in gui.education_queue_tree.column_options.items()
    }
    assert sum(widths_2k.values()) <= 1006
    assert widths_2k["status"] >= 120


def test_model_list_status_update_changes_only_status_cell():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.model_list_tree = _FakeTree(1000)
    gui.model_list_tree.items["row-1"] = {
        "values": ("qwen3.5-plus", "通义千问 (Qwen)", "未检测", "", "https://example.test")
    }

    gui._set_model_list_item_status("row-1", "测试中...")

    assert gui.model_list_tree.items["row-1"]["values"] == (
        "qwen3.5-plus",
        "通义千问 (Qwen)",
        "测试中...",
        "",
        "https://example.test",
    )


def test_save_capability_to_model_matches_provider_base_url_and_model():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.saved_models = [
        {
            "api_provider": "qwen",
            "base_url": "https://one.example/v1",
            "model": "same-model",
        },
        {
            "api_provider": "qwen",
            "base_url": "https://two.example/v1",
            "model": "same-model",
        },
    ]
    gui.api_config = {"saved_models": gui.saved_models}
    gui.load_saved_models_to_tree = Mock()
    gui._mark_api_config_ui_current = Mock()
    with tempfile.TemporaryDirectory() as tmp_dir:
        config_path = Path(tmp_dir) / "api_config.json"
        with patch.object(gui_main, "API_CONFIG_PATH", config_path):
            gui._save_capability_to_model(
                "same-model",
                {"status": "compatible", "output_mode": "tool"},
                provider_key="qwen",
                base_url="https://two.example/v1",
                refresh=False,
            )

    assert "capability" not in gui.saved_models[0]
    assert gui.saved_models[1]["capability"] == {
        "status": "compatible",
        "output_mode": "tool",
    }
    gui.load_saved_models_to_tree.assert_not_called()
    gui._mark_api_config_ui_current.assert_called_once()


def test_latest_history_value_uses_latest_end_date_not_list_order():
    entries = [
        {"school": "较早学校", "end": "2018.06"},
        {"school": "最近学校", "end": "2022.06"},
    ]

    value = BossFilterGUI._latest_history_value(entries, "school", "", "教育经历：")

    assert value == "最近学校"


def test_latest_history_value_treats_present_as_latest_and_falls_back_to_summary():
    works = [
        {"company": "上一家公司", "end": "2024.01"},
        {"company": "当前公司", "end": "至今"},
    ]
    assert BossFilterGUI._latest_history_value(
        works, "company", "", "工作经历："
    ) == "当前公司"

    assert BossFilterGUI._latest_history_value(
        [], "company", "工作经历：摘要公司 高级工程师 2022 至今", "工作经历："
    ) == "摘要公司"


def test_candidate_status_hides_internal_greet_context_capability():
    """状态栏只展示业务状态，不暴露打招呼上下文等内部实现。"""
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {
        "greet_sent": False,
        "followup_status": "未沟通",
        "greet_context": {"chat_start": {"jid": "job-1", "lid": "list-1"}},
    }

    assert gui._format_candidate_status(candidate) == "未沟通"


def test_candidate_status_surfaces_pending_greeting_confirmation():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {
        "greet_sent": False,
        "followup_status": "未沟通",
        "greet_confirmation_pending": True,
    }

    assert gui._format_candidate_status(candidate) == "未沟通｜发送待确认"


def test_candidate_status_shows_temporary_ai_eval_state_and_expires():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {"geek_id": 123, "followup_status": "未沟通"}
    gui._ai_evaluating_ids = {"123"}
    gui._ai_eval_results = {}

    assert gui._format_candidate_status(candidate) == "AI评估中..."

    gui._ai_evaluating_ids.clear()
    gui._ai_eval_results["123"] = {
        "status": "success",
        "message": "评估完成，调整分：-3",
        "timestamp": time.time(),
    }
    assert gui._format_candidate_status(candidate) == "✓ 评估完成，调整分：-3"

    gui._ai_eval_results["123"]["timestamp"] = time.time() - 4
    assert gui._format_candidate_status(candidate) == "未沟通"
    assert "123" not in gui._ai_eval_results


def test_refresh_results_force_rebuilds_for_transient_ai_status():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        candidates_path.write_text(
            json.dumps([{
                "geek_id": "g1",
                "name": "候选人",
                "job_name": "Java 工程师",
                "match_score": 70,
                "followup_status": "未沟通",
            }], ensure_ascii=False),
            encoding="utf-8",
        )

        gui = BossFilterGUI.__new__(BossFilterGUI)
        gui.result_tree = _FakeResultTree()
        gui.result_job_var = _FakeVar("全部岗位")
        gui.result_show_blacklist_var = _FakeVar(False)
        gui.result_stats_vars = {key: _FakeVar() for key in ("strong", "recommended", "pending", "greeted")}
        gui.result_stats_greeted = {key: _FakeVar() for key in ("strong", "recommended", "pending", "greeted")}
        gui.colors = {
            "bg_tree_tag_high": "#fff",
            "bg_tree_tag_mid": "#fff",
            "bg_tree_tag_low": "#fff",
        }
        gui._ai_evaluating_ids = {"g1"}
        gui._ai_eval_results = {}
        gui._result_last_job = "全部岗位"
        gui._result_last_dates = (None, None)
        gui._result_last_show_blacklist = False
        stat = candidates_path.stat()
        gui._result_tree_fingerprint = (stat.st_mtime, stat.st_size)
        gui._parse_salary_exp = Mock(return_value=("", ""))
        gui._extract_extra_fields = Mock(return_value=("", "", "", "", ""))
        gui._sort_bound = True
        gui.append_log = Mock()

        with patch("gui_main.CANDIDATES_PATH", candidates_path):
            gui.refresh_results()
            assert gui.result_tree.items == []

            gui.refresh_results(force=True)

        assert gui.result_tree.items[0]["values"][7] == "AI评估中..."


def test_refresh_results_keeps_ai_evaluated_candidate_below_pass_score():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        candidates_path.write_text(
            json.dumps([{
                "geek_id": "g1",
                "name": "候选人",
                "job_name": "Java 工程师",
                "match_score": 52,
                "followup_status": "未沟通",
                "llm_evaluated": True,
                "llm_adjustment": -3,
            }], ensure_ascii=False),
            encoding="utf-8",
        )

        gui = BossFilterGUI.__new__(BossFilterGUI)
        gui.result_tree = _FakeResultTree()
        gui.result_job_var = _FakeVar("全部岗位")
        gui.result_show_blacklist_var = _FakeVar(False)
        gui.result_stats_vars = {key: _FakeVar() for key in ("strong", "recommended", "pending", "greeted")}
        gui.result_stats_greeted = {key: _FakeVar() for key in ("strong", "recommended", "pending", "greeted")}
        gui.colors = {
            "bg_tree_tag_high": "#fff",
            "bg_tree_tag_mid": "#fff",
            "bg_tree_tag_low": "#fff",
        }
        gui._ai_evaluating_ids = set()
        gui._ai_eval_results = {}
        gui._result_tree_fingerprint = None
        gui._result_last_job = None
        gui._result_last_dates = None
        gui._result_last_show_blacklist = False
        gui._parse_salary_exp = Mock(return_value=("", ""))
        gui._extract_extra_fields = Mock(return_value=("", "", "", "", ""))
        gui._sort_bound = True
        gui.append_log = Mock()

        with patch("gui_main.CANDIDATES_PATH", candidates_path):
            gui.refresh_results()

        values = gui.result_tree.items[0]["values"]
        assert values[4] == 52
        assert values[5] == "-3"
        assert values[6] == "未通过"
        assert values[7] == "未沟通"


def test_scroll_to_ai_evaluated_candidate_selects_row_by_geek_id_string():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_tree = _FakeResultTree()
    gui.result_tree.items = [{}, {}]
    gui._item_to_candidate = {
        0: {"geek_id": 123},
        1: {"geek_id": 456},
    }

    selected = gui._scroll_to_ai_evaluated_candidates({"123"})

    assert selected is True
    assert gui.result_tree.seen == [0]
    assert gui.result_tree.selection == [0]
    assert gui.result_tree.focused == 0
    assert gui.result_tree.focus_set_called is True


def test_ai_eval_batch_summary_formats_success_failure_and_skipped():
    summary = {
        "selected_count": 4,
        "success": [
            {"name": "候选人A", "adjustment": 3},
            {"name": "候选人B", "adjustment": -2},
        ],
        "failed": [{"name": "候选人C", "reason": "API 请求超时"}],
        "skipped": [{"name": "候选人D", "reason": "已评估过"}],
    }

    title, message, has_failure = BossFilterGUI._format_ai_eval_batch_summary(summary)

    assert title == "AI 评估完成"
    assert has_failure is True
    assert "本次共选择 4 人" in message
    assert "成功 2 人" in message
    assert "失败 1 人" in message
    assert "跳过 1 人" in message
    assert "- 候选人C：API 请求超时" in message
    assert "- 候选人D：已评估过" in message


def test_ai_eval_batch_summary_caps_detail_size_for_large_batches():
    summary = {
        "selected_count": 80,
        "success": [{"name": f"成功{i}", "adjustment": 0} for i in range(60)],
        "failed": [
            {
                "name": f"失败候选人{i}姓名很长很长",
                "reason": "接口返回超时，且错误详情非常长，可能包含多段网络诊断信息和重试结果",
            }
            for i in range(12)
        ],
        "skipped": [
            {"name": f"跳过候选人{i}姓名很长很长", "reason": "已评估过，无需重复评估"}
            for i in range(8)
        ],
    }

    _, message, has_failure = BossFilterGUI._format_ai_eval_batch_summary(summary)

    lines = message.splitlines()
    assert has_failure is True
    assert "本次共选择 80 人" in message
    assert "成功 60 人" in message
    assert "失败 12 人" in message
    assert "跳过 8 人" in message
    assert "另有 6 人失败" in message
    assert "另有 5 人已跳过" in message
    assert sum(1 for line in lines if line.startswith("- 失败候选人")) == 6
    assert sum(1 for line in lines if line.startswith("- 跳过候选人")) == 3
    assert max(len(line) for line in lines) <= 54
    assert len(lines) <= 19


def test_show_ai_eval_batch_summary_suppresses_single_candidate_popup():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = object()
    gui._ai_eval_batch_summary = {
        "enabled": False,
        "selected_count": 1,
        "success": [{"name": "候选人A", "adjustment": 1}],
        "failed": [],
        "skipped": [],
    }

    with patch("gui_main.messagebox.showinfo") as showinfo, \
            patch("gui_main.messagebox.showwarning") as showwarning:
        gui._show_ai_eval_batch_summary()

    showinfo.assert_not_called()
    showwarning.assert_not_called()
    assert gui._ai_eval_batch_summary is None


def test_show_ai_eval_batch_summary_uses_info_when_all_batch_items_succeed():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = object()
    gui._ai_eval_batch_summary = {
        "enabled": True,
        "selected_count": 2,
        "success": [
            {"name": "候选人A", "adjustment": 1},
            {"name": "候选人B", "adjustment": 0},
        ],
        "failed": [],
        "skipped": [],
    }

    with patch("gui_main.messagebox.showinfo") as showinfo, \
            patch("gui_main.messagebox.showwarning") as showwarning:
        gui._show_ai_eval_batch_summary()

    showwarning.assert_not_called()
    showinfo.assert_called_once()
    args, kwargs = showinfo.call_args
    assert args[0] == "AI 评估完成"
    assert "成功 2 人" in args[1]
    assert kwargs["parent"] is gui.root
    assert gui._ai_eval_batch_summary is None


def test_show_ai_eval_batch_summary_uses_warning_when_batch_has_failures():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = object()
    gui._ai_eval_batch_summary = {
        "enabled": True,
        "selected_count": 2,
        "success": [{"name": "候选人A", "adjustment": 1}],
        "failed": [{"name": "候选人B", "reason": "评估失败"}],
        "skipped": [],
    }

    with patch("gui_main.messagebox.showinfo") as showinfo, \
            patch("gui_main.messagebox.showwarning") as showwarning:
        gui._show_ai_eval_batch_summary()

    showinfo.assert_not_called()
    showwarning.assert_called_once()
    args, kwargs = showwarning.call_args
    assert args[0] == "AI 评估完成"
    assert "失败 1 人" in args[1]
    assert "- 候选人B：评估失败" in args[1]
    assert kwargs["parent"] is gui.root
    assert gui._ai_eval_batch_summary is None


def test_greet_confirmation_hint_explains_prepared_path_without_technical_terms():
    candidate = {
        "greet_context": {"chat_start": {"jid": "job-1", "lid": "list-1"}},
    }

    hint = BossFilterGUI._get_greet_confirmation_hint(candidate)

    assert "无需停留在原推荐页面" in hint
    assert "上下文" not in hint
    assert "API" not in hint


def test_greet_confirmation_hint_explains_current_page_fallback():
    hint = BossFilterGUI._get_greet_confirmation_hint({})

    assert "当前推荐页面定位" in hint
    assert "该岗位的推荐牛人页面" in hint


def test_update_log_waits_until_lazy_run_page_creates_log_widget():
    """未进入运行控制页时保留日志，不能因 log_text 尚未创建而报错或丢消息。"""
    class FakeRoot:
        def __init__(self):
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()
    gui.log_queue = queue.Queue()
    gui.log_queue.put("打招呼成功")

    gui.update_log()

    assert gui.log_queue.qsize() == 1
    assert gui.root.scheduled == [(100, gui.update_log)]


def test_browser_auto_check_debounces_one_transient_navigation_miss():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._browser_non_target_checks = 0

    assert gui._should_defer_browser_navigation_warning(silent=True) is True
    assert gui._should_defer_browser_navigation_warning(silent=True) is False


def test_browser_auto_check_debounces_one_transient_connection_failure():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._browser_connection_failures = 0

    assert gui._should_defer_browser_connection_failure(silent=True) is True
    assert gui._should_defer_browser_connection_failure(silent=True) is False
    assert gui._should_defer_browser_connection_failure(silent=False) is False


def test_result_page_stats_show_greeted_after_pending():
    """结果页依次展示强烈推荐、推荐、待定、已打招呼。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    stats_block = source[source.index("stats_data = [", source.index("def create_result_page")):]
    stats_block = stats_block[:stats_block.index("\n\n        for icon_name")]

    assert '"通过筛选"' not in stats_block
    assert (
        stats_block.index('"强烈推荐"')
        < stats_block.index('"推荐"')
        < stats_block.index('"待定"')
        < stats_block.index('"已打招呼"')
    )
    assert '"strong_recommend"' in stats_block
    assert '"hourglass"' in stats_block
    assert '"pending"' in stats_block
    assert '("chat", "已打招呼", "greeted"' in stats_block


def test_result_page_greeted_detail_uses_passed_candidates_only():
    """已打招呼指标只统计通过筛选且已打招呼的候选人。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    detail_block = source[source.index("elif stat_type == 'greeted':"):]
    detail_block = detail_block[:detail_block.index("\n            else:")]

    assert "SCORE_THRESHOLD_PASS" in detail_block
    assert "c.get('greet_sent', False)" in detail_block


def test_passed_filter_uses_enlarged_original_people_icon():
    """通过筛选沿用原双人图案，并适当放大视觉占位。"""
    assert "passed_filter" in icons.ICON_REGISTRY
    original = icons.ICON_REGISTRY["people"](40, "white", (0, 0, 0, 0), 3)
    image = icons.ICON_REGISTRY["passed_filter"](40, "white", (0, 0, 0, 0), 3)
    assert image.size == (40, 40)
    assert image.getbbox() is not None
    original_width = original.getbbox()[2] - original.getbbox()[0]
    enlarged_width = image.getbbox()[2] - image.getbbox()[0]
    assert enlarged_width > original_width
    assert enlarged_width >= 36


def test_strong_recommendation_uses_registered_emphasized_thumb_icon():
    """强烈推荐使用点赞加光芒，与普通推荐保持同一视觉语言。"""
    assert "strong_recommend" in icons.ICON_REGISTRY
    image = icons.ICON_REGISTRY["strong_recommend"](40, "white", (0, 0, 0, 0), 3)
    assert image.size == (40, 40)
    assert image.getbbox() is not None
    assert image.getbbox()[2] - image.getbbox()[0] >= 32
    assert image.getbbox()[3] - image.getbbox()[1] >= 36


def test_home_page_strong_recommendation_uses_emphasized_thumb_icon():
    """首页与筛选结果页统一使用点赞加光芒表达强烈推荐。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    home_block = source[source.index("cards_data = [", source.index("def create_home_page")):]
    home_block = home_block[:home_block.index("\n\n        self.home_stats_vars")]

    assert '("strong_recommend", "强烈推荐"' in home_block
    assert '("star", "强烈推荐"' not in home_block


def test_home_page_renames_total_candidates_to_passed_filter():
    """首页第一张卡片展示通过筛选，并使用放大的原双人图案。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    home_block = source[source.index("cards_data = [", source.index("def create_home_page")):]
    home_block = home_block[:home_block.index("\n\n        self.home_stats_vars")]

    assert '("passed_filter", "通过筛选", "total_home"' in home_block
    assert '"累计候选人"' not in home_block


def test_stats_page_strong_recommendation_uses_emphasized_thumb_icon():
    """数据统计页与其他页面统一使用点赞加光芒表达强烈推荐。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    stats_block = source[source.index("summary_items = [", source.index("def create_stats_page")):]
    stats_block = stats_block[:stats_block.index("\n\n        for icon_name")]

    assert '("strong_recommend", "强烈推荐"' in stats_block
    assert '("star", "强烈推荐"' not in stats_block


def test_stats_page_renames_total_candidates_to_passed_filter():
    """数据统计页第一张卡片展示通过筛选，并使用放大的原双人图案。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    stats_block = source[source.index("summary_items = [", source.index("def create_stats_page")):]
    stats_block = stats_block[:stats_block.index("\n\n        for icon_name")]

    assert '("passed_filter", "通过筛选", "total"' in stats_block
    assert '"总候选人"' not in stats_block


def test_stats_page_greeted_uses_chat_icon_consistently():
    """数据统计页与首页、筛选结果页统一使用聊天气泡表示已打招呼。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    stats_block = source[source.index("summary_items = [", source.index("def create_stats_page")):]
    stats_block = stats_block[:stats_block.index("\n\n        for icon_name")]

    assert '("chat", "已打招呼", "greeted"' in stats_block
    assert '("mail", "已打招呼", "greeted"' not in stats_block


def test_education_browser_reuses_live_page():
    gui = object.__new__(BossFilterGUI)
    live_tab = Mock()
    live_tab.run_js.return_value = 1
    gui.education_tabs = {"edu_1": live_tab}
    gui.browser_page = None

    assert gui._get_education_tab("edu_1") is live_tab


def test_education_browser_rebuilds_after_both_page_objects_disconnect():
    gui = object.__new__(BossFilterGUI)
    stale_tab = Mock()
    stale_tab.run_js.side_effect = RuntimeError("与页面的连接已断开")
    stale_base = Mock()
    stale_base.run_js.side_effect = RuntimeError("与页面的连接已断开")
    fresh_page = Mock()
    fresh_page.run_js.return_value = 1
    fresh_page.address = "127.0.0.1:9222"
    new_tab = Mock()
    new_tab.run_js.return_value = 1
    fresh_page.new_tab.return_value = new_tab

    gui.education_tabs = {"edu_1": stale_tab}
    gui.browser_page = stale_base
    gui.browser_connected = True
    gui._try_reconnect_browser = Mock(return_value=False)
    gui._create_fresh_browser_page = Mock(return_value=fresh_page)

    result = gui._get_education_tab("edu_1")

    assert result is new_tab
    assert gui.education_tabs["edu_1"] is new_tab
    assert gui.browser_page is fresh_page
    assert gui.browser_connected is True


def test_education_browser_recovers_if_chrome_closes_before_new_tab():
    gui = object.__new__(BossFilterGUI)
    stale_tab = Mock()
    stale_tab.run_js.side_effect = RuntimeError("与页面的连接已断开")
    base_page = Mock()
    base_page.run_js.return_value = 1
    base_page.new_tab.side_effect = RuntimeError("与页面的连接已断开")
    fresh_page = Mock()
    fresh_page.run_js.return_value = 1
    fresh_page.address = "127.0.0.1:9222"

    gui.education_tabs = {"edu_1": stale_tab}
    gui.browser_page = base_page
    gui.browser_connected = True
    gui._try_reconnect_browser = Mock(return_value=False)
    gui._create_fresh_browser_page = Mock(return_value=fresh_page)

    assert gui._get_education_tab("edu_1") is fresh_page
    assert gui.browser_page is fresh_page


def test_education_browser_uses_auto_port_for_fresh_page():
    gui = object.__new__(BossFilterGUI)
    live_page = Mock()
    created_options = []

    class FakeChromiumOptions:
        def __init__(self, read_file=True):
            self.read_file = read_file
            self.auto_port_called = False

        def auto_port(self):
            self.auto_port_called = True

    def fake_chromium_page(options=None):
        if options is None:
            raise AssertionError("学历核验不应等待默认 9222 端口")
        created_options.append(options)
        return live_page

    with patch.dict(sys.modules, {"DrissionPage": types.SimpleNamespace(
        ChromiumOptions=FakeChromiumOptions,
        ChromiumPage=fake_chromium_page,
    )}):
        assert gui._create_fresh_browser_page() is live_page
        assert len(created_options) == 1
        assert created_options[0].read_file is False
        assert created_options[0].auto_port_called is True


def test_education_queue_saves_manual_edits_to_current_item():
    gui = object.__new__(BossFilterGUI)
    gui.education_current_id = "education_1"
    gui.education_items = {
        "education_1": {
            "path": "certificate.jpg",
            "name": "",
            "certificate_number": "",
            "status": "已识别",
        }
    }
    gui.education_name_var = Mock()
    gui.education_name_var.get.return_value = " 张三 "
    gui.education_number_var = Mock()
    gui.education_number_var.get.return_value = "123456789012345678"
    gui.education_queue_tree = Mock()
    gui.education_queue_tree.exists.return_value = True

    gui._save_current_education_fields()

    item = gui.education_items["education_1"]
    assert item["name"] == "张三"
    assert item["certificate_number"] == "123456789012345678"
    gui.education_queue_tree.item.assert_called_once()


def test_education_queue_disables_parallel_recognition():
    gui = object.__new__(BossFilterGUI)
    gui.education_items = {"education_1": {"path": "certificate.jpg"}}
    gui.education_current_id = "education_1"
    gui.education_recognition_running = True
    gui.education_file_var = Mock()
    gui.education_remove_btn = Mock()
    gui.education_recognize_btn = Mock()
    gui.education_fill_btn = Mock()

    gui._refresh_education_queue_summary()

    gui.education_recognize_btn.configure.assert_called_with(state="disabled")
    gui.education_remove_btn.configure.assert_called_with(state="normal")
    gui.education_fill_btn.configure.assert_called_with(state="normal")


def test_education_import_uses_multi_file_dialog():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[
        source.index("def _select_education_images"):
        source.index("def _refresh_education_queue_summary")
    ]

    assert "askopenfilenames(" in block
    assert "askopenfilename(" not in block


def test_education_queue_supports_multi_select_batch_recognition_and_context_menu():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    create_block = source[
        source.index("def create_education_page"):
        source.index("def _select_education_images")
    ]
    recognize_block = source[
        source.index("def _recognize_education_image"):
        source.index("def _fill_chsi_page")
    ]

    assert 'selectmode="extended"' in create_block
    assert 'text=" 识别证书"' in create_block
    assert 'label="识别证书"' in create_block
    assert 'label="删除证书"' in create_block
    assert "ThreadPoolExecutor(max_workers=workers)" in recognize_block
    assert "workers = min(3, len(item_ids))" in recognize_block


def test_education_selected_ids_preserve_multi_selection():
    gui = object.__new__(BossFilterGUI)
    gui.education_items = {
        "education_1": {},
        "education_2": {},
        "education_3": {},
    }
    gui.education_current_id = "education_1"
    gui.education_queue_tree = Mock()
    gui.education_queue_tree.selection.return_value = ("education_1", "education_3")

    assert gui._selected_education_item_ids() == ["education_1", "education_3"]


def test_education_page_has_scroll_container_and_conditional_queue():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    create_block = source[
        source.index("def create_education_page"):
        source.index("def _select_education_images")
    ]
    summary_block = source[
        source.index("def _refresh_education_queue_summary"):
        source.index("def _save_current_education_fields")
    ]

    assert "self.education_canvas, self.education_scrollable_frame" in create_block
    assert "self.education_queue_card.pack_forget()" in create_block
    queue_card_block = create_block[
        create_block.index('content, "待核验队列"'):
        create_block.index("self.education_queue_card")
    ]
    assert "title_font=" not in queue_card_block
    assert '"Education.Treeview"' in create_block
    assert '"Education.Treeview.Heading"' in create_block
    assert "font=(FONT_FAMILY, int(10 * self.font_scale))" in create_block
    assert "font=(FONT_FAMILY, int(11 * self.font_scale), \"bold\")" in create_block
    assert '("school", "学校"' in create_block
    assert '("major", "专业"' in create_block
    assert '("file", "文件", 230)' in create_block
    assert '("number", "证书编号", 160)' in create_block
    assert '("major", "专业", 210)' in create_block
    assert "def _on_education_queue_motion" in source
    assert 'tooltip_columns = {"#1": 0, "#4": 3, "#5": 4}' in source
    assert "self._education_tree_font.measure(full_text)" in source
    assert "if total >= 1" in summary_block
    assert "elif total < 1" in summary_block


def test_mousewheel_routes_education_and_api_pages_to_correct_canvas():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    cocoa_block = source[
        source.index("page_canvas = {"):
        source.index("}.get(getattr(self, 'current_page_index', -1))")
    ]

    assert "4: getattr(self, 'education_canvas', None)" in cocoa_block
    assert "6: getattr(self, 'api_canvas', None)" in cocoa_block


def test_education_queue_context_menu_uses_smaller_font():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    create_block = source[
        source.index("def create_education_page"):
        source.index("def _select_education_images")
    ]

    assert "int(11 * self.font_scale)" in create_block
    assert "int(12 * self.font_scale)" not in create_block
def test_education_remove_clears_manual_rotation():
    from gui_main import BossFilterGUI as _GUI
    gui = object.__new__(_GUI)
    gui.education_items = {
        "education_1": {"path": "a.jpg"},
        "education_2": {"path": "b.jpg"},
    }
    gui.education_manual_rotation = {"education_1": 90, "education_2": 180}
    gui.education_rotation_locked = {"education_1", "education_2"}
    gui.education_current_id = "education_1"
    gui.education_queue_tree = Mock()
    gui.education_queue_tree.get_children.return_value = (
        "education_1",
        "education_2",
    )
    gui.education_queue_tree.exists.return_value = True
    gui._on_education_queue_select = Mock()
    gui._refresh_education_queue_summary = Mock()

    gui._remove_education_items(["education_1"])

    assert "education_1" not in gui.education_manual_rotation
    assert gui.education_manual_rotation["education_2"] == 180
    assert "education_1" not in gui.education_rotation_locked


def test_education_rotate_cw90_accumulates_and_wraps():
    from gui_main import BossFilterGUI as _GUI
    gui = object.__new__(_GUI)
    gui.education_current_id = "education_1"
    gui.education_items = {"education_1": {"path": "a.jpg", "auto_rotation": 0}}
    gui.education_manual_rotation = {}
    gui.education_rotation_locked = set()
    gui._render_education_preview = Mock()

    gui._rotate_education_image_cw90()
    assert gui.education_manual_rotation["education_1"] == 90
    assert "education_1" in gui.education_rotation_locked
    gui._rotate_education_image_cw90()
    assert gui.education_manual_rotation["education_1"] == 180
    gui._rotate_education_image_cw90()
    assert gui.education_manual_rotation["education_1"] == 270
    gui._rotate_education_image_cw90()
    assert gui.education_manual_rotation["education_1"] == 0
    assert gui._render_education_preview.call_count == 4


def test_education_rotate_cw90_noop_without_current_item():
    from gui_main import BossFilterGUI as _GUI
    gui = object.__new__(_GUI)
    gui.education_current_id = None
    gui.education_items = {}
    gui.education_manual_rotation = {}
    gui.education_rotation_locked = set()
    gui._render_education_preview = Mock()

    gui._rotate_education_image_cw90()

    assert gui.education_manual_rotation == {}
    gui._render_education_preview.assert_not_called()


def test_education_manual_rotation_starts_from_model_rotation_and_locks():
    from gui_main import BossFilterGUI as _GUI
    gui = object.__new__(_GUI)
    gui.education_current_id = "education_1"
    gui.education_items = {
        "education_1": {"path": "a.jpg", "auto_rotation": 90}
    }
    gui.education_manual_rotation = {}
    gui.education_rotation_locked = set()
    gui._render_education_preview = Mock()

    gui._rotate_education_image_cw90()

    assert gui.education_manual_rotation["education_1"] == 180
    assert "education_1" in gui.education_rotation_locked


def test_education_preview_toolbar_has_rotate_not_flip():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    create_block = source[
        source.index("def create_education_page"):
        source.index("def _select_education_images")
    ]

    assert "顺转 90°" in create_block
    assert "education_rotate_btn" in create_block
    assert "_rotate_education_image_cw90" in source
    assert "education_manual_rotation" in source
    # 用 tk.Label + 点击绑定代替 ttk.Button，严格不撑高标题栏
    assert "EducationRotate.TButton" not in create_block
    assert 'cursor="hand2"' in create_block
    assert "<Button-1>" in create_block

    # 无快捷键提示
    assert '"快捷键 R"' not in create_block
    assert "rotate_hint" not in create_block

    # 按钮在预览卡片标题栏内（title_trailing_builder 注入），不挤占图片空间也不遮挡图片
    assert "preview_toolbar" not in create_block
    assert "preview_column" not in create_block
    assert "title_trailing_builder" in create_block
    assert "_build_rotate_button" in create_block
    assert "title_bar, text=" in create_block
    assert 'side="right"' in create_block
    assert "self.education_rotate_btn.place(" not in create_block
    # 按钮文字无前导空格（缩窄）
    assert 'text="顺转 90°"' in create_block
    assert 'text=" 顺转 90°"' not in create_block

    # 无任何自动方向检测
    assert "_detect_image_orientation" not in source
    assert "education_orientation_cache" not in source

    assert "flip_horizontal" not in source
    assert "flip_vertical" not in source
    assert "education_flip_h_btn" not in create_block
    assert "_flip_education_image_horizontal" not in source
    assert "_flip_education_image_vertical" not in source
    assert "_reset_education_image_flip" not in source
    assert "_set_education_flip_buttons_enabled" not in source

def test_education_recognize_disclaimer_text_simplified():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    create_block = source[
        source.index("def create_education_page"):
        source.index("def _select_education_images")
    ]

    # 精简后的提示文本
    assert "识别时图片/PDF 会发送当前配置的 AI 模型，请确认已取得候选人授权。" in create_block
    # 旧文本已删除（"给"、"学信网验证码"等）
    assert "识别时图片会发送给当前配置" not in create_block
    assert "学信网验证码" not in create_block

def test_education_remove_current_button_handles_multi_select():
    """'移除当前'按钮在多选时应移除所有选中项，而非只移除当前项。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    remove_block = source[
        source.index("def _remove_current_education_image"):
        source.index("def _remove_selected_education_images")
    ]

    assert "_selected_education_item_ids" in remove_block
    assert "self.education_current_id" not in remove_block


def test_education_queue_summary_text_varies_by_count():
    """total=1 不显示'点击队列切换'，total>1 显示，单位用'张证书'。"""
    from unittest.mock import Mock
    from gui_main import BossFilterGUI as _GUI

    gui = object.__new__(_GUI)
    gui.education_items = {"edu_1": {}}
    gui.education_file_var = Mock()
    gui.education_queue_card = None
    gui.education_workspace = None
    gui.education_remove_btn = Mock()
    gui.education_recognize_btn = Mock()
    gui.education_fill_btn = Mock()
    gui.education_current_id = "edu_1"
    gui.education_recognition_running = False

    gui._refresh_education_queue_summary()
    gui.education_file_var.set.assert_called_with("已导入 1 张证书")

    gui.education_items = {"edu_1": {}, "edu_2": {}}
    gui._refresh_education_queue_summary()
    gui.education_file_var.set.assert_called_with("已导入 2 张证书，点击队列切换")

    gui.education_items = {}
    gui._refresh_education_queue_summary()
    gui.education_file_var.set.assert_called_with("尚未导入毕业证书")


def test_education_import_button_text_is_certificate():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    create_block = source[
        source.index("def create_education_page"):
        source.index("def _select_education_images")
    ]

    assert 'text=" 导入证书"' in create_block
    assert 'text=" 导入图片"' not in create_block

def test_education_import_dialog_supports_pdf():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    select_block = source[
        source.index("def _select_education_images"):
        source.index("def _refresh_education_queue_summary")
    ]

    assert '"图片和 PDF", "*.jpg *.jpeg *.png *.bmp *.webp *.pdf"' in select_block
    assert '("PDF 文件", "*.pdf")' in select_block
    # 用 validate_document_path（接受图片+PDF），不再用 validate_image_path
    assert "validate_document_path" in select_block
    assert "is_pdf_path" in select_block
    # item 字典存 is_pdf 标记
    assert '"is_pdf": is_pdf_path(path)' in select_block


def test_education_worker_branches_pdf_and_image():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    worker_block = source[
        source.index("def _recognize_education_image"):
        source.index("def _fill_chsi_page")
    ]

    # 同时导入两个识别函数
    assert "recognize_certificate_pdf" in worker_block
    assert "recognize_certificate_image" in worker_block
    # 按 is_pdf 分支
    assert 'item.get("is_pdf")' in worker_block
    assert "recognize_certificate_pdf(path" in worker_block


def test_education_render_shows_text_placeholder_for_pdf():
    from unittest.mock import Mock
    from gui_main import BossFilterGUI as _GUI
    gui = object.__new__(_GUI)
    gui.education_current_id = "edu_1"
    gui.education_items = {"edu_1": {"path": "cert.pdf", "is_pdf": True}}
    gui.education_manual_rotation = {}
    label = Mock()
    gui.education_preview_label = label
    gui.education_image_path = "cert.pdf"

    gui._render_education_preview()

    # PDF 不走 Image.open，直接显示文字占位
    label.configure.assert_called_once()
    kwargs = label.configure.call_args.kwargs
    assert kwargs.get("image") == ""
    assert "PDF" in kwargs.get("text", "")
    assert label._image_ref is None


# === 评分拆解与简历评估的替代关系（regression: resume_adj=0 时不得回退显示 AI 调整值）===

def _breakdown_parts_sum(line: str) -> int:
    """评分拆解行中各项（除'总分'外）的数值合计。

    兼容 gui_main 的 ' + '/'优先项' 与 bossmaster 的 ' / '/'优先' 两种格式，
    且同时识别 CJK 标签（基础/简历）与拉丁标签（AI）。
    """
    pairs = re.findall(r'([A-Za-z一-鿿]{2,})([-+]?\d+)', line)
    s = 0
    for label, num in pairs:
        if '总分' in label:
            continue
        s += int(num)
    return s


def _detail_breakdown_line(candidate: dict) -> str:
    gui = BossFilterGUI.__new__(BossFilterGUI)
    detail = gui._format_candidate_detail(candidate)
    for line in detail.splitlines():
        if line.startswith("  评分拆解："):
            return line
    raise AssertionError(f"未找到评分拆解行：{detail}")


def _base_candidate_with_breakdown(breakdown: dict, score: int) -> dict:
    return {
        "name": "张三",
        "job_name": "Java 工程师",
        "geek_id": "g-brk",
        "match_score": score,
        "summary": "本科\n5 年 Java",
        "score_breakdown": breakdown,
    }


def test_detail_breakdown_resume_adj_zero_hides_ai():
    """resume_adj=0 时拆解不显示一次评估 AI 值，各项合计 = 总分。"""
    breakdown = {
        "base": 25, "skill": 30, "experience": 5, "education": 5, "preferred": 0,
        "ai_adjustment": 8, "resume_adjustment": 0, "total": 65,
    }
    line = _detail_breakdown_line(_base_candidate_with_breakdown(breakdown, 65))
    assert "AI" not in line, f"resume_adj=0 时不应回退显示 AI 调整值：{line}"
    assert "简历" not in line, f"resume_adj=0 时不应显示简历0：{line}"
    assert _breakdown_parts_sum(line) == 65, f"拆解各项合计 != 总分 65：{line}"


def test_detail_breakdown_resume_adj_nonzero_shows_resume_only():
    """resume_adj≠0 时只显示简历调整值，不显示一次评估 AI 值，合计 = 总分。"""
    breakdown = {
        "base": 25, "skill": 30, "experience": 5, "education": 5, "preferred": 0,
        "ai_adjustment": 8, "resume_adjustment": 5, "total": 70,
    }
    line = _detail_breakdown_line(_base_candidate_with_breakdown(breakdown, 70))
    assert "简历+5" in line
    assert "AI" not in line, f"有简历评估时不应显示一次评估 AI 值：{line}"
    assert _breakdown_parts_sum(line) == 70, f"拆解各项合计 != 总分 70：{line}"


def test_detail_breakdown_no_resume_shows_ai():
    """无简历评估（resume_adjustment 缺失）时显示一次评估 AI 值，合计 = 总分。"""
    breakdown = {
        "base": 25, "skill": 30, "experience": 5, "education": 5, "preferred": 0,
        "ai_adjustment": 8, "total": 73,
    }
    line = _detail_breakdown_line(_base_candidate_with_breakdown(breakdown, 73))
    assert "AI+8" in line
    assert "简历" not in line
    assert _breakdown_parts_sum(line) == 73, f"拆解各项合计 != 总分 73：{line}"


# === _save_ai_eval_results 落盘白名单（regression: llm_dimension_scores 等字段必须随评估结果写盘）===

def test_save_ai_eval_results_persists_dimension_scores_and_hard_fields():
    """AI 评估写入的 llm_dimension_scores / manual_review_required / auto_greet_blocked_reason
    必须随 _save_ai_eval_results 持久化到 candidates.json，否则关闭程序后丢失。"""
    import json
    import tempfile
    from pathlib import Path

    gui = BossFilterGUI.__new__(BossFilterGUI)
    with tempfile.TemporaryDirectory() as tmpdir:
        cand_path = Path(tmpdir) / "candidates.json"
        disk_cand = {"geek_id": "g-dim", "name": "张三", "match_score": 70, "job_name": "Java"}
        cand_path.write_text(json.dumps([disk_cand], ensure_ascii=False), encoding="utf-8")
        gui.all_candidates = [dict(disk_cand)]

        # 模拟 evaluate_batch 原地写入的评估字段
        evaluated = dict(disk_cand)
        evaluated.update({
            "llm_evaluated": True,
            "llm_adjustment": 5,
            "match_score": 75,
            "recommend_level": "推荐",
            "rule_score": 70,
            "llm_dimension_scores": {
                "skill_depth": 8, "experience_quality": 7,
                "industry_fit": 6, "growth_potential": 9,
            },
            "qualification_status": "rejected",
            "manual_review_required": False,
            "auto_greet_blocked_reason": "硬条件不符合",
        })

        with patch.object(gui_main, "CANDIDATES_PATH", cand_path):
            gui._save_ai_eval_results([evaluated])

        saved = json.loads(cand_path.read_text(encoding="utf-8"))
        assert saved[0]["llm_dimension_scores"] == {
            "skill_depth": 8, "experience_quality": 7,
            "industry_fit": 6, "growth_potential": 9,
        }, "llm_dimension_scores 未落盘（_save_ai_eval_results 白名单遗漏）"
        assert saved[0]["manual_review_required"] is False
        assert saved[0]["auto_greet_blocked_reason"] == "硬条件不符合"
        assert saved[0]["qualification_status"] == "rejected"


# === _candidate_has_ai_eval 守卫（regression: 已导入简历的候选人不得再跑一次评估，否则叠加两次调整）===

def test_candidate_has_ai_eval_helper():
    assert _candidate_has_ai_eval({'llm_evaluated': True}) is True
    assert _candidate_has_ai_eval({'resume_eval_adjustment': 5}) is True
    assert _candidate_has_ai_eval({'resume_eval_adjustment': 0}) is True  # 0 也是已评估
    assert _candidate_has_ai_eval({}) is False
    assert _candidate_has_ai_eval({'llm_evaluated': False}) is False
    assert _candidate_has_ai_eval({'llm_evaluated': None}) is False


def test_ai_eval_batch_guard_uses_has_ai_eval_helper():
    """批量评估守卫必须调用 _candidate_has_ai_eval，否则已导入简历的候选人会被重复评估、
    叠加两次调整并污染 rule_score（guard 在 _ai_eval_selected_candidates 内，难以端到端测试，用源码断言固化）。"""
    source = Path(gui_main.__file__).read_text(encoding="utf-8")
    assert "_candidate_has_ai_eval(c)" in source, "守卫未调用 _candidate_has_ai_eval(c)"


# === _resolve_rule_score 还原真实规则分（regression: 未跑一次评估却导入简历时撤回还原）===

def test_resolve_rule_score_uses_rule_score_when_present():
    assert _resolve_rule_score({'rule_score': 65}) == 65


def test_resolve_rule_score_from_breakdown_when_missing():
    """rule_score 缺失时从评分拆解各项求和还原（基础+技能+经验+学历+优先）。"""
    bd = {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0}
    assert _resolve_rule_score({'score_breakdown': bd}) == 65


def test_resolve_rule_score_zero_when_nothing_available():
    assert _resolve_rule_score({}) == 0


def test_resolve_rule_score_handles_none_rule_score():
    """rule_score 显式为 None 时回退到拆解求和（兼容脏数据）。"""
    bd = {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0}
    assert _resolve_rule_score({'rule_score': None, 'score_breakdown': bd}) == 65


# === 详情弹窗【AI 一次评估】调整后分数（regression: 简历评估存在时不得显示被替代后的最终分）===

def test_detail_ai_eval_block_adjusted_score_uses_rule_plus_llm_with_resume():
    """简历二次评估存在时，【AI 一次评估】的'调整后分数'应为 rule_score+llm_adjustment，
    而非被简历替代后的 match_score（否则区块内'原始规则分 + AI调整值 ≠ 调整后分数'）。"""
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {
        'name': '张三', 'job_name': 'Java', 'geek_id': 'g-r1',
        'match_score': 70,  # 65(rule) + 5(resume)，被简历替代后的最终分
        'rule_score': 65,
        'llm_evaluated': True, 'llm_adjustment': 8, 'llm_model': 'm', 'llm_reason': '匹配',
        'resume_eval_adjustment': 5,
        'summary': '本科\n5 年 Java',
        'score_breakdown': {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0,
                            'ai_adjustment': 8, 'resume_adjustment': 5, 'total': 70},
    }
    detail = gui._format_candidate_detail(candidate)
    for line in detail.splitlines():
        if line.startswith("  调整后分数："):
            assert line == "  调整后分数：73", f"应为 rule(65)+llm(8)=73，实：{line}"
            break
    else:
        raise AssertionError(f"未找到调整后分数行：{detail}")


# === 维度评分替代显示（regression: 简历评估的维度评分应替代一次评估的显示）===

def test_detail_dim_scores_prefer_resume_over_round1():
    """有简历评估维度评分时，详情弹窗显示简历评估的（替代一次评估的）。"""
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {
        'name': '张三', 'job_name': 'Java', 'geek_id': 'g-dim',
        'match_score': 70, 'rule_score': 65,
        'llm_evaluated': True, 'llm_adjustment': 5, 'llm_model': 'm', 'llm_reason': '匹配',
        'resume_eval_adjustment': 5,
        'llm_dimension_scores': {'skill_depth': 4, 'experience_quality': 4, 'industry_fit': 4, 'growth_potential': 4},
        'resume_eval_dimension_scores': {'skill_depth': 9, 'experience_quality': 8, 'industry_fit': 7, 'growth_potential': 9},
        'summary': '本科\n5 年 Java',
        'score_breakdown': {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0,
                            'ai_adjustment': 5, 'resume_adjustment': 5, 'total': 70},
    }
    detail = gui._format_candidate_detail(candidate)
    dim_lines = [l for l in detail.splitlines() if '技能深度' in l]
    assert dim_lines, f"未找到维度评估行：{detail}"
    assert "9/10" in dim_lines[0], f"应显示简历评估的技能深度 9，实：{dim_lines[0]}"
    assert "4/10" not in dim_lines[0], f"不应显示一次评估的 4：{dim_lines[0]}"


def test_detail_dim_scores_fallback_to_round1_without_resume():
    """无简历评估维度评分时，详情弹窗回退显示一次评估的。"""
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {
        'name': '张三', 'job_name': 'Java', 'geek_id': 'g-dim2',
        'match_score': 73, 'rule_score': 65,
        'llm_evaluated': True, 'llm_adjustment': 8, 'llm_model': 'm', 'llm_reason': '匹配',
        'llm_dimension_scores': {'skill_depth': 6, 'experience_quality': 5, 'industry_fit': 5, 'growth_potential': 5},
        'summary': '本科\n5 年 Java',
        'score_breakdown': {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0,
                            'ai_adjustment': 8, 'total': 73},
    }
    detail = gui._format_candidate_detail(candidate)
    dim_lines = [l for l in detail.splitlines() if '技能深度' in l]
    assert dim_lines, f"未找到维度评估行：{detail}"
    assert "6/10" in dim_lines[0]
