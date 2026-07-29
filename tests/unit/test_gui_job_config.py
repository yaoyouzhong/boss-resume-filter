import ast
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import types
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import gui_main
import icons
import bossmaster
from gui_main import (
    BossFilterGUI,
    PAGE_SPECS,
    PageIndex,
    _api_provider_display_name,
    _api_timeout_hint_text,
    _optional_int_to_entry,
    _parse_optional_int_entry,
    _candidate_has_ai_eval,
    _filter_candidates_by_result_view,
)
from job_config_diagnostics import summarize_job_config_diagnostics
from llm_eval import _resolve_rule_score


def test_optional_max_age_none_displays_as_blank():
    assert _optional_int_to_entry(None) == ""


def test_run_control_uses_same_provider_name_as_model_settings():
    config = {
        "api_provider": "kimi",
        "base_url": "https://api.kimi.com/coding/v1",
        "model": "k3",
    }

    assert _api_provider_display_name(config) == "月之暗面 (Kimi)"
    assert _api_timeout_hint_text(config) == "月之暗面 (Kimi) / k3 · 默认 60 秒"


def test_relay_timeout_hint_uses_flat_note_format():
    config = {
        "api_provider": "kimi",
        "base_url": "https://relay.example.com/v1",
        "model": "k3",
    }

    assert _api_timeout_hint_text(config) == "中转服务 / k3 · 默认 120 秒"


def test_gui_timeout_policy_recognizes_token_plan_as_official_service():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    }

    assert gui._is_relay_endpoint_for_timeout() is False


def test_result_view_separates_recommended_review_and_rejected_without_limit():
    candidates = [
        {
            "geek_id": f"active-{i}",
            "qualification_status": "qualified",
            "match_score": 70,
        }
        for i in range(125)
    ] + [
        {"geek_id": "pending", "qualification_status": "qualified", "match_score": 60},
        {
            "geek_id": "approved",
            "qualification_status": "qualified",
            "match_score": 60,
            "contact_approved_at": "20260728_100000",
        },
        {"geek_id": "manual", "qualification_status": "manual_review", "match_score": 72},
        {"geek_id": "ai-failed", "qualification_status": "qualified", "match_score": 72, "llm_error": "timeout"},
        {"geek_id": "send-pending", "qualification_status": "qualified", "match_score": 72, "greet_confirmation_pending": True},
        {"geek_id": "rejected", "qualification_status": "rejected", "match_score": 0},
    ]

    assert len(_filter_candidates_by_result_view(candidates, "推荐候选人")) == 126
    assert {
        c["geek_id"] for c in _filter_candidates_by_result_view(candidates, "待复核")
    } == {"pending", "manual", "ai-failed"}
    assert {
        c["geek_id"] for c in _filter_candidates_by_result_view(candidates, "人工确认")
    } == {"approved"}
    assert [c["geek_id"] for c in _filter_candidates_by_result_view(candidates, "淘汰记录")] == ["rejected"]
    assert len(_filter_candidates_by_result_view(candidates, "全部记录")) == 131


def test_run_job_config_warning_is_acknowledged_until_diagnostics_change():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._acknowledged_job_config_warnings = set()

    assert gui._should_prompt_run_job_config("warning-a", False) is True
    gui._remember_run_job_config_warning("warning-a", False, True)
    assert gui._should_prompt_run_job_config("warning-a", False) is False
    assert gui._should_prompt_run_job_config("warning-b", False) is True
    assert gui._should_prompt_run_job_config("warning-a", True) is True


def test_job_config_diagnostics_dialog_is_content_sized_and_keeps_actions_visible():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    dialog_block = source[source.index("def _show_job_config_diagnostics_dialog"):]
    dialog_block = dialog_block[:dialog_block.index("\n    def _should_prompt_run_job_config")]

    assert "win.grid_rowconfigure(0, weight=1)" in dialog_block
    assert 'body.grid(row=0, column=0, sticky="nsew")' in dialog_block
    assert 'btn_row.grid(row=1, column=0, sticky="ew")' in dialog_block
    assert "text_widget.configure(height=max(6, min(18, estimated_rows)))" in dialog_block
    assert "max(int(260 * scale), win.winfo_reqheight())" in dialog_block
    assert "_place_window_centered(win, dialog_width, dialog_height" in dialog_block
    assert "int(720 * scale), int(520 * scale)" not in dialog_block


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
    assert "任选其一" in text


def test_humanize_ai_parse_warning_preserves_oracle_and_langchain_names():
    gui = BossFilterGUI.__new__(BossFilterGUI)

    text = gui._humanize_ai_parse_warning(
        "职位描述第2条mysql、oracle其中一种表明数据库技能满足其一即可，属于OR关系；"
        "LangChain 不属于 AND 条件"
    )

    assert "oracle" in text.lower()
    assert "LangChain" in text
    assert "满足任一项acle" not in text
    assert "LangCh全部满足in" not in text
    assert "职位描述第2条：mysql、oracle任选其一，请确认是否符合预期" in text
    assert "全部满足" in text


def test_ai_parse_warning_uses_aligned_wrapping_numbered_item_rows():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _apply_ai_enhance_result"):]
    block = block[:block.index("\n    def _start_ai_progress_animation")]

    assert "warning_items = [" in block
    assert "numbered_items=warning_items" in block
    assert 'headline="AI 增强解析完成，请确认以下内容"' in block
    assert "min_width=820" in block
    assert "max_width=900" in block


def test_invalidating_requirement_parse_rejects_pending_callbacks():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._requirement_parse_generation = 7
    gui._active_requirement_parse_id = 7
    gui._ai_enhance_pending = True
    gui._ai_parse_edit_snapshot = {"min_exp": "3"}
    gui._stop_ai_progress_animation = Mock()
    gui._stop_requirement_parse_progress = Mock()
    gui._finish_parse_button = Mock()

    gui._invalidate_requirement_parse()

    assert gui._requirement_parse_generation == 8
    assert gui._active_requirement_parse_id is None
    assert gui._ai_enhance_pending is False
    assert gui._ai_parse_edit_snapshot is None
    assert gui._is_current_requirement_parse(7) is False
    gui._stop_ai_progress_animation.assert_called_once_with()
    gui._stop_requirement_parse_progress.assert_called_once_with()
    gui._finish_parse_button.assert_called_once_with()


def test_stale_requirement_and_ai_results_do_not_touch_current_form():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._active_requirement_parse_id = 2
    gui._ai_enhance_pending = True
    gui._stop_ai_progress_animation = Mock()
    gui._stop_requirement_parse_progress = Mock()

    gui._apply_requirement_parse_result({}, 1)
    gui._apply_ai_enhance_result({"ai_success": True}, 1)

    assert gui._active_requirement_parse_id == 2
    assert gui._ai_enhance_pending is True
    gui._stop_ai_progress_animation.assert_not_called()
    gui._stop_requirement_parse_progress.assert_not_called()


def test_save_current_job_is_blocked_while_requirement_parse_is_active():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = Mock()
    gui._active_requirement_parse_id = 3
    gui._hide_save_hint = Mock()

    with patch("gui_main.messagebox.showwarning") as warning:
        assert gui.save_current_job() is False

    gui._hide_save_hint.assert_not_called()
    warning.assert_called_once_with(
        "招聘需求正在解析",
        "请等待解析完成后再保存岗位配置。",
        parent=gui.root,
    )


def test_job_config_primary_actions_are_lowered_without_moving_quality_row():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("# 按钮行（居中布局"):]
    block = block[:block.index("# 存储技能数据的列表")]

    assert "pady=(int(6 * self.dpi_scale * self.zoom_factor), 0)" in block
    footer_block = source[
        source.index("# 底部按钮固定在页面底部"):source.index("# 在所有控件创建完毕后绑定滚轮事件")
    ]
    assert "int(10 * self.dpi_scale * self.zoom_factor),\n                0," in footer_block


class _FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _FakeWidget:
    def __init__(self):
        self.configs = []
        self.text = ""

    def config(self, **kwargs):
        self.configs.append(kwargs)

    configure = config

    def delete(self, *_args):
        self.text = ""

    def insert(self, _index, text):
        self.text = text


class _FakeStopEvent:
    def __init__(self):
        self.cleared = False

    def clear(self):
        self.cleared = True


class _FakeCombo(dict):
    def __init__(self):
        super().__init__()
        self.current_value = ""

    def set(self, value):
        self.current_value = value

    def get(self):
        return self.current_value


def test_run_job_default_prefers_recent_concrete_job():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.job_select_var = _FakeVar("")
    gui.job_combo = _FakeCombo()
    gui._last_run_job_selection = "Python 工程师"

    selected = gui._sync_run_job_combo_values(
        {"Java 工程师": {}, "Python 工程师": {}},
        prefer_current=False,
    )

    assert selected == "Python 工程师"
    assert gui.job_select_var.get() == "Python 工程师"
    assert gui.job_combo["values"] == ["全部岗位", "Java 工程师", "Python 工程师"]


def test_run_job_default_falls_back_to_first_saved_job():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.job_select_var = _FakeVar("")
    gui.job_combo = _FakeCombo()
    gui._last_run_job_selection = "已删除岗位"

    selected = gui._sync_run_job_combo_values(
        {"Java 工程师": {}, "Python 工程师": {}},
        prefer_current=False,
    )

    assert selected == "Java 工程师"
    assert gui.job_select_var.get() == "Java 工程师"


def test_run_job_selection_preserves_current_valid_choice_on_refresh():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.job_select_var = _FakeVar("全部岗位")
    gui.job_combo = _FakeCombo()
    gui._last_run_job_selection = "Java 工程师"

    selected = gui._sync_run_job_combo_values(
        {"Java 工程师": {}},
        prefer_current=True,
    )

    assert selected == "全部岗位"
    assert gui.job_select_var.get() == "全部岗位"


def test_remember_run_job_selection_persists_only_concrete_jobs():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._run_preferences = {}
    saved = []

    with patch("gui_main._save_run_preferences", saved.append):
        gui._remember_run_job_selection("Java 工程师")
        gui._remember_run_job_selection("全部岗位")

    assert gui._last_run_job_selection == "Java 工程师"
    assert saved == [{"last_run_job_name": "Java 工程师"}]


class _FakePackFrame:
    def __init__(self):
        self.manager = ""

    def winfo_manager(self):
        return self.manager

    def pack(self, **_kwargs):
        self.manager = "pack"

    def pack_forget(self):
        self.manager = ""


class _SearchSortResultTree:
    def __init__(self, rows):
        self.rows = rows
        self.attached = list(rows)
        self.tags = {item_id: tuple() for item_id in rows}
        self.selection = None

    def get_children(self):
        return tuple(self.attached)

    def exists(self, item_id):
        return item_id in self.rows

    def detach(self, item_id):
        if item_id in self.attached:
            self.attached.remove(item_id)

    def reattach(self, item_id, _parent, index):
        if item_id in self.attached:
            self.attached.remove(item_id)
        if index == "end":
            self.attached.append(item_id)
        else:
            self.attached.insert(index, item_id)

    def delete(self, item_id):
        self.detach(item_id)
        self.rows.pop(item_id, None)
        self.tags.pop(item_id, None)

    def item(self, item_id, option=None, **kwargs):
        if "tags" in kwargs:
            self.tags[item_id] = tuple(kwargs["tags"])
        if option == "tags":
            return self.tags[item_id]
        return {"tags": self.tags[item_id]}

    def tag_configure(self, *_args, **_kwargs):
        return None

    def see(self, _item_id):
        return None

    def selection_set(self, item_id):
        self.selection = item_id

    def set(self, item_id, column):
        return self.rows[item_id].get(column, "")

    def move(self, item_id, _parent, index):
        self.attached.remove(item_id)
        self.attached.insert(index, item_id)


class _FakeCalendarTop:
    def __init__(self, mapped=True):
        self.mapped = mapped

    def winfo_ismapped(self):
        return self.mapped

    def withdraw(self):
        self.mapped = False


def test_job_config_page_uses_business_sections_and_one_low_frequency_menu():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def create_config_page"):]
    block = block[:block.index("\n    def create_api_config_page")]

    assert '"招聘需求"' in block
    assert '"基础筛选条件"' in block
    assert '"技能评分条件"' in block
    assert '"必要条件"' in block
    assert "ttk.Menubutton" in block
    assert "CenteredActions.TMenubutton" in block
    assert "tk.Menu(select_frame, tearoff=0, font=self.font_label)" in block
    assert "requirement_header_status_var" in block
    assert "requirement_toggle_icon_label" in block
    assert "'chevron_down'" in block
    assert "'chevron_up'" in block
    assert 'text="收起"' not in block
    assert 'text="查看问题"' not in block
    assert 'text="查看详情"' in block
    assert "self.job_config_quality_link.bind" in block
    assert "_bind_job_config_quality_interaction" not in block
    assert "self.skill_weight_spinbox = ttk.Spinbox" in block
    assert "self.add_skill_weight_spinbox = ttk.Spinbox" in block
    assert "JobWeight.TSpinbox" not in block
    assert block.count("justify='left'") >= 2
    assert 'label=" 导入配置"' in block
    assert 'label=" 导出配置"' in block
    assert 'label=" 删除当前岗位"' in block
    assert "_start_breathing" not in block


def test_job_config_basic_filters_use_compact_grouped_rows_and_keep_alignment():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("# 岗位名称"):]
    block = block[:block.index("# 技能关键词区域")]

    assert "self.job_name_entry = ttk.Entry(row1, textvariable=self.job_name_var, width=22" in block
    assert "basic_filter_input_width = 6" in block
    assert "secondary_filter_gap = int(30 * self.dpi_scale * self.zoom_factor)" in block
    assert block.count("padx=(secondary_filter_gap, 0)") == 2
    assert block.count("width=basic_filter_input_width") == 3
    assert block.count("style='CompactFilter.TSpinbox'") == 2
    assert block.count("style='CompactFilter.TCombobox'") == 1
    assert "row_education_salary = ttk.Frame" in block
    education_pos = block.index('text="最低学历:"')
    salary_pos = block.index('text="薪资范围:"')
    assert education_pos < salary_pos
    assert block.count('text="年"') == 2
    assert "foreground=self.colors['bg_card']" in block
    assert 'filter_unit_width = font.Font(font=self.font_label).measure("年")' not in block
    assert "row_experience_age = ttk.Frame" in block
    assert 'text="最低经验:"' in block
    assert 'text="最大年龄:"' in block
    assert 'ttk.Label(row_experience_age, text="岁"' in block
    assert "row_salary =" not in block
    assert "row_age" not in block
    assert "salary_max_entry = ttk.Entry(" in block
    assert "row_education_salary," in block
    assert "width=8," in block
    assert "work_location_entry = ttk.Entry(row3, textvariable=self.work_location_var, width=22" in block


def test_compact_filter_spinbox_matches_combobox_pixel_width_contract():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    styles_block = source[source.index("def setup_styles"):]
    styles_block = styles_block[:styles_block.index("\n    def create_sidebar")]

    assert '_filter_char_width = font.Font(font=self.font_label).measure("0")' in styles_block
    assert "'CompactFilter.TCombobox'" in styles_block
    assert "padding=(max(0, _filter_char_width - 6), 0)" in styles_block
    assert "'CompactFilter.TSpinbox'" in styles_block
    assert "padding=(max(0, _filter_char_width - 4), 0)" in styles_block


def test_skill_score_table_uses_shared_font_and_wider_key_columns():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("# 使用 Treeview 显示技能列表"):]
    block = block[:block.index("skills_scroll = ttk.Scrollbar")]

    assert "tree_font = self.font_table" in block
    assert "style='Skills.Treeview'" in block
    assert "_style.configure('Skills.Treeview.Heading', font=(*self.font_table, 'bold'))" in block
    assert 'column("name", width=190, minwidth=150, stretch=False' in block
    assert 'column("weight", width=80, minwidth=70, stretch=False' in block
    assert 'column("source", width=90, minwidth=75, stretch=False' in block
    assert 'column("evidence", width=320, minwidth=220, stretch=True' in block


def test_populate_skills_deduplicates_aliases_and_prefers_preferred_items():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.refresh_skills_tree = Mock()
    config = {
        "keywords": [
            {"name": "SpringBoot", "weight": 3},
            {"name": "Dubbo", "weight": 2},
            {"name": "智能体", "weight": 1},
        ],
        "preferred_keywords": [
            {"name": "Spring Boot", "bonus": 2},
            {"name": "Dubbo", "bonus": 2},
            {"name": "AI Agent", "bonus": 2},
        ],
    }

    gui._populate_skills_from_config(config, {"skills": []})

    assert [item["name"] for item in gui.skills_data] == [
        "Spring Boot",
        "Dubbo",
        "AI Agent",
    ]
    assert all(item["source"] == "优先" for item in gui.skills_data)


def test_job_config_status_distinguishes_saved_and_unsaved_business_state():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.job_name_var = _FakeVar("Java 工程师")
    gui.edu_var = _FakeVar("本科")
    gui.min_exp_var = _FakeVar("3")
    gui.max_age_var = _FakeVar("35")
    gui.work_location_var = _FakeVar("南京")
    gui.salary_min_var = _FakeVar("20")
    gui.salary_max_var = _FakeVar("30")
    gui.skills_data = [
        {"name": "Java", "weight": 3, "source": "配置"},
        {"name": "Spring", "weight": 2, "source": "配置"},
        {"name": "MySQL", "weight": 1, "source": "配置"},
    ]
    gui.required_conditions_data = ["统招本科"]
    gui._get_requirement_text = Mock(return_value="招聘 Java 工程师")
    gui.config_job_combo = _FakeCombo()
    gui.config_job_combo.set("Java 工程师")
    gui.job_rules = {"Java 工程师": {}}
    gui.job_form_status_var = _FakeVar()
    gui.job_form_status_label = Mock()
    gui.job_config_quality_var = _FakeVar()
    gui.job_config_quality_label = Mock()
    gui.btn_restore_job = Mock()
    gui.btn_view_job_config_issues = Mock()
    gui.colors = {
        "warning": "orange",
        "danger": "red",
        "success": "green",
        "text_secondary": "gray",
    }
    gui._job_form_saved_snapshot = gui._job_form_fingerprint()

    gui._refresh_job_form_status()

    assert gui.job_form_status_var.get() == "已保存"
    assert gui.job_config_quality_var.get() == (
        "配置质量：100 分｜阻断 0 项｜提醒 0 项｜建议 1 项"
    )
    preview_name, preview_rule, issues, validation_error = gui._job_config_preview
    assert validation_error == ""
    assert "严重 0 项，提醒 0 项，建议 1 项" in summarize_job_config_diagnostics(
        preview_name, preview_rule, issues=issues
    )
    assert gui.btn_restore_job.configure.call_args.kwargs["state"] == "disabled"

    gui.min_exp_var.set("5")
    gui._refresh_job_form_status()

    assert gui.job_form_status_var.get() == "有未保存修改"
    assert gui.btn_restore_job.configure.call_args.kwargs["state"] == "normal"


def test_new_job_quality_waits_for_configuration_before_scoring():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.job_name_var = _FakeVar("")
    gui.edu_var = _FakeVar("不限")
    gui.min_exp_var = _FakeVar("0")
    gui.max_age_var = _FakeVar("")
    gui.work_location_var = _FakeVar("")
    gui.salary_min_var = _FakeVar("")
    gui.salary_max_var = _FakeVar("")
    gui.skills_data = []
    gui.required_conditions_data = []
    gui._get_requirement_text = Mock(return_value="")
    gui.config_job_combo = _FakeCombo()
    gui.job_rules = {"已有岗位": {}}
    gui._job_step_active = 0
    gui.job_form_status_var = _FakeVar()
    gui.job_form_status_label = Mock()
    gui.job_config_quality_var = _FakeVar()
    gui.job_config_quality_label = Mock()
    gui.btn_restore_job = Mock()
    gui.btn_view_job_config_issues = Mock()
    gui.colors = {
        "warning": "orange",
        "danger": "red",
        "success": "green",
        "text_secondary": "gray",
    }
    gui._job_form_saved_snapshot = gui._job_form_fingerprint()

    gui._refresh_job_form_status()

    assert gui.job_form_status_var.get() == "新岗位，尚未保存"
    assert gui.job_config_quality_var.get() == "配置质量：待配置"
    assert gui._job_config_quality_clickable is False
    assert gui.btn_view_job_config_issues.configure.call_args.kwargs["state"] == "disabled"

    gui._get_requirement_text.return_value = "招聘 Java 工程师"
    gui._job_step_active = 1
    gui._refresh_job_form_status()

    assert gui.job_config_quality_var.get() == "配置质量：待解析"
    assert gui._job_config_quality_clickable is False

    gui.job_name_var.set("Java 工程师")
    gui._job_step_active = 2
    gui._refresh_job_form_status()

    assert "阻断 1 项" in gui.job_config_quality_var.get()
    assert gui._job_config_quality_clickable is True


def test_initialize_new_job_draft_resets_selector_and_workflow():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.config_job_combo = _FakeCombo()
    gui.config_job_combo.set("高级 Java/Python 工程师")
    gui.job_name_var = _FakeVar("高级 Java/Python 工程师")
    gui.reset_job_form = Mock(side_effect=lambda: gui.job_name_var.set(""))
    gui.btn_restore_job = Mock()
    gui._set_requirement_section_expanded = Mock()
    gui.requirement_template_btn = Mock()
    gui._show_requirement_hint = Mock()
    gui._hide_btn_add_hint = Mock()
    gui._update_job_step = Mock()
    gui._refresh_job_form_status = Mock()
    gui.config_canvas = Mock()

    gui._initialize_new_job_draft()

    gui.reset_job_form.assert_called_once_with()
    assert gui.config_job_combo.get() == ""
    assert gui.job_name_var.get() == ""
    gui.btn_restore_job.configure.assert_called_once_with(text=" 清空内容")
    gui._set_requirement_section_expanded.assert_called_once_with(True)
    gui.requirement_template_btn.state.assert_called_once_with(['!disabled'])
    gui._update_job_step.assert_called_once_with(0)
    gui._refresh_job_form_status.assert_called_once_with()
    gui.config_canvas.yview_moveto.assert_called_once_with(0)


def test_clear_new_job_draft_uses_complete_initializer():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.config_job_combo = _FakeCombo()
    gui.config_job_combo.set("未保存岗位")
    gui.job_rules = {"已有岗位": {}}
    gui._initialize_new_job_draft = Mock()

    with patch("gui_main.messagebox.askyesno", return_value=True):
        gui._restore_or_clear_job_form()

    gui._initialize_new_job_draft.assert_called_once_with()


def test_unsaved_job_transition_can_save_discard_or_cancel():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = Mock()
    gui._job_form_has_unsaved_changes = Mock(return_value=True)
    gui.save_current_job = Mock(return_value=True)

    with patch("gui_main.messagebox.askyesnocancel", return_value=True):
        assert gui._confirm_job_form_transition() is True
    gui.save_current_job.assert_called_once_with()

    with patch("gui_main.messagebox.askyesnocancel", return_value=False):
        assert gui._confirm_job_form_transition() is True

    with patch("gui_main.messagebox.askyesnocancel", return_value=None):
        assert gui._confirm_job_form_transition() is False


def test_selecting_current_job_does_not_reload_and_discard_form_state():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.config_job_combo = _FakeCombo()
    gui.config_job_combo.set("Java 工程师")
    gui._job_form_loaded_name = "Java 工程师"
    gui._confirm_job_form_transition = Mock()
    gui.load_job_to_form = Mock()

    gui.on_job_selected(None)

    gui._confirm_job_form_transition.assert_not_called()
    gui.load_job_to_form.assert_not_called()


def test_import_config_protects_unsaved_form_before_opening_file_dialog():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._confirm_job_form_transition = Mock(return_value=False)
    gui.load_config_dialog = Mock()

    gui.import_config()

    gui.load_config_dialog.assert_not_called()

    gui._confirm_job_form_transition.return_value = True
    gui.import_config()

    gui.load_config_dialog.assert_called_once_with()


def test_collapsed_requirement_header_summarizes_saved_and_changed_content():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.requirement_section_expanded = False
    gui.requirement_header_status_var = _FakeVar()
    gui.requirement_toggle_icon_label = Mock()
    gui.requirement_expand_icon = "down"
    gui.requirement_collapse_icon = "up"
    gui._get_requirement_text = Mock(return_value="高级 Java 工程师招聘需求")
    gui.config_job_combo = _FakeCombo()
    gui.config_job_combo.set("Java 工程师")
    gui.job_rules = {"Java 工程师": {}}
    gui._job_form_has_unsaved_changes = Mock(return_value=False)

    gui._refresh_requirement_header_state()

    assert gui.requirement_header_status_var.get() == "已保存招聘需求"
    assert gui.requirement_toggle_icon_label.configure.call_args.kwargs["image"] == "down"

    gui._job_form_has_unsaved_changes.return_value = True
    gui._refresh_requirement_header_state()

    assert gui.requirement_header_status_var.get() == "招聘需求已修改"

    gui.requirement_section_expanded = True
    gui._refresh_requirement_header_state()

    assert gui.requirement_header_status_var.get() == ""
    assert gui.requirement_toggle_icon_label.configure.call_args.kwargs["image"] == "up"


def test_bounded_spinbox_mousewheel_adjusts_one_step_and_clamps_range():
    spinbox = Mock()
    variable = _FakeVar("2")

    BossFilterGUI._bind_bounded_spinbox_mousewheel(spinbox, variable, 1, 3)

    wheel_handler = spinbox.bind.call_args_list[0].args[1]
    assert wheel_handler(types.SimpleNamespace(delta=120)) == "break"
    assert variable.get() == "3"
    wheel_handler(types.SimpleNamespace(delta=120))
    assert variable.get() == "3"
    wheel_handler(types.SimpleNamespace(delta=-120))
    assert variable.get() == "2"

    variable.set("not-a-number")
    wheel_handler(types.SimpleNamespace(delta=-120))
    assert variable.get() == "1"


def test_quality_summary_row_opens_details_only_when_configuration_exists():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._show_current_job_config_diagnostics = Mock()
    gui._job_config_quality_clickable = False

    gui._open_job_config_quality_details()

    gui._show_current_job_config_diagnostics.assert_not_called()

    gui._job_config_quality_clickable = True
    gui._open_job_config_quality_details()

    gui._show_current_job_config_diagnostics.assert_called_once_with()


def test_disclosure_chevrons_are_registered_as_line_icons():
    for name in ("chevron_up", "chevron_down"):
        assert name in icons.ICON_REGISTRY
        for size in (48, 124):
            image = icons.ICON_REGISTRY[name](
                size, "#2563EB", (0, 0, 0, 0), 4
            )
            assert image.getbbox() is not None


def test_checkbox_icons_are_registered_as_box_and_checkmark_states():
    assert "checkbox_off" in icons.ICON_REGISTRY
    assert "checkbox_on" in icons.ICON_REGISTRY
    off = icons.ICON_REGISTRY["checkbox_off"](
        48, "#CBD5E1", (0, 0, 0, 0), 4
    )
    on = icons.ICON_REGISTRY["checkbox_on"](
        48, "#1E88E5", (0, 0, 0, 0), 4
    )

    assert off.getbbox() is not None
    assert on.getbbox() is not None
    assert off.tobytes() != on.tobytes()
    colors = on.getcolors(maxcolors=on.width * on.height)
    assert colors is not None
    assert any(pixel[:3] == (255, 255, 255) and pixel[3] for _, pixel in colors)


def test_reset_job_form_uses_unrestricted_new_job_values():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    for name, value in (
        ("job_name_var", "Java 工程师"),
        ("min_exp_var", "3"),
        ("max_age_var", "35"),
        ("edu_var", "本科"),
        ("work_location_var", "南京"),
        ("salary_min_var", "20"),
        ("salary_max_var", "30"),
    ):
        setattr(gui, name, _FakeVar(value))
    gui.skills_data = [{"name": "Java", "weight": 3}]
    gui.required_conditions_data = ["统招本科"]
    gui.refresh_skills_tree = Mock()
    gui.refresh_required_listbox = Mock()
    gui.requirement_text = Mock()
    gui._req_placeholder_text = "在此粘贴招聘需求内容..."
    gui.parse_result_label = Mock()
    gui._hide_requirement_hint = Mock()
    gui._hide_parse_hint = Mock()
    gui._hide_save_hint = Mock()
    gui._set_job_form_baseline = Mock()
    gui._invalidate_requirement_parse = Mock()

    gui.reset_job_form()

    gui._invalidate_requirement_parse.assert_called_once_with()
    assert gui.job_name_var.get() == ""
    assert gui.edu_var.get() == "不限"
    assert gui.min_exp_var.get() == "0"
    assert gui.max_age_var.get() == ""
    assert gui.work_location_var.get() == ""
    assert gui.salary_min_var.get() == ""
    assert gui.salary_max_var.get() == ""


def test_loading_legacy_job_without_optional_limits_keeps_fields_unrestricted():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.config_job_combo = _FakeCombo()
    gui.config_job_combo.set("旧岗位")
    for name in (
        "job_name_var",
        "min_exp_var",
        "max_age_var",
        "edu_var",
        "work_location_var",
        "salary_min_var",
        "salary_max_var",
    ):
        setattr(gui, name, _FakeVar())
    gui.requirement_text = Mock()
    gui._req_placeholder_text = "在此粘贴招聘需求内容..."
    gui._invalidate_requirement_parse = Mock()
    gui.refresh_skills_tree = Mock()
    gui.refresh_required_listbox = Mock()
    gui._set_job_form_baseline = Mock()

    gui.load_job_to_form({})

    assert gui.edu_var.get() == "不限"
    assert gui.max_age_var.get() == ""


def test_requirement_parse_fallbacks_match_unrestricted_new_job_defaults():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    local_block = source[source.index("def _apply_requirement_parse_result"):]
    local_block = local_block[:local_block.index("\n    def _apply_ai_enhance_result")]
    ai_block = source[source.index("def _apply_ai_enhance_result"):]
    ai_block = ai_block[:ai_block.index("\n    def _start_ai_progress_animation")]

    assert 'job_config.get("edu", "不限")' in local_block
    assert 'job_config.get("max_age")' in local_block
    assert 'job_config.get("edu", "本科")' not in local_block
    assert 'job_config.get("max_age", 35)' not in local_block
    assert 'job_config.get("edu", "不限")' in ai_block
    assert 'job_config.get("max_age")' in ai_block
    assert 'job_config.get("edu", "本科")' not in ai_block
    assert 'job_config.get("max_age", 35)' not in ai_block


def test_result_time_range_defaults_to_all_time():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_time_range_var = _FakeVar("全部时间")

    assert gui._get_result_date_filter() == (None, None)


def test_review_workbench_formats_first_seen_timestamp_for_users():
    assert BossFilterGUI._format_display_datetime("20260610_220126") == (
        "2026-06-10 22:01"
    )
    assert BossFilterGUI._format_display_datetime("20260710T120000") == (
        "2026-07-10 12:00"
    )
    assert BossFilterGUI._format_display_datetime("2026-07-15T08:30:45+08:00") == (
        "2026-07-15 08:30"
    )
    assert BossFilterGUI._format_display_datetime("") == "未知"
    assert BossFilterGUI._format_display_datetime("历史数据") == "历史数据"


def test_result_time_range_presets_use_exact_natural_days():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_time_range_var = _FakeVar("近7天")

    with patch("gui_main.datetime") as datetime_mock:
        datetime_mock.now.return_value.date.return_value = date(2026, 7, 15)
        gui.result_time_range_var.set("今天")
        assert gui._get_result_date_filter() == ("20260715", "20260715")

        gui.result_time_range_var.set("近7天")
        assert gui._get_result_date_filter() == ("20260709", "20260715")

        gui.result_time_range_var.set("近30天")
        assert gui._get_result_date_filter() == ("20260616", "20260715")


def test_result_time_range_custom_mode_reads_both_date_entries():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_time_range_var = _FakeVar("自定义")
    gui.result_date_start_entry = Mock()
    gui.result_date_end_entry = Mock()
    gui.result_date_start_entry.get_date.return_value = date(2026, 6, 1)
    gui.result_date_end_entry.get_date.return_value = date(2026, 6, 30)

    assert gui._get_result_date_filter() == ("20260601", "20260630")


def test_result_custom_date_entries_are_created_once_on_demand():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_date_start_entry = None
    gui.result_date_end_entry = None
    gui.result_custom_date_frame = object()
    gui.font_scale = 1.0
    gui.font_label = ("Arial", 12)
    gui.dpi_scale = 1.0
    gui.zoom_factor = 1.0
    gui.colors = {"bg_main": "white"}
    start_entry = Mock()
    end_entry = Mock()
    gui._create_result_date_entry = Mock(side_effect=(start_entry, end_entry))
    gui._wrap_date_dropdown_mutex = Mock()
    gui._validate_date_range = Mock()

    with (
        patch("gui_main.ttk.Label") as label_class,
        patch("gui_main.datetime") as datetime_mock,
    ):
        datetime_mock.now.return_value.date.return_value = date(2026, 7, 15)
        gui._ensure_result_custom_date_entries()
        gui._ensure_result_custom_date_entries()

    assert gui._create_result_date_entry.call_count == 2
    assert gui.result_date_start_entry is start_entry
    assert gui.result_date_end_entry is end_entry
    start_entry.set_date.assert_called_once_with(date(2026, 7, 9))
    end_entry.set_date.assert_called_once_with(date(2026, 7, 15))
    assert gui._wrap_date_dropdown_mutex.call_args_list == [
        call(start_entry, end_entry),
        call(end_entry, start_entry),
    ]
    label_class.return_value.pack.assert_called_once()


def test_daily_actions_use_result_job_date_and_blacklist_scope():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_job_var = _FakeVar("Java 工程师")
    gui.result_date_start_entry = Mock()
    gui.result_time_range_var = _FakeVar("近7天")
    gui._get_result_date_filter = Mock(return_value=("20260709", "20260715"))
    candidates = [
        {"geek_id": "included", "job_name": "Java工程师", "first_seen_at": "20260710T120000"},
        {"geek_id": "old", "job_name": "Java工程师", "first_seen_at": "20260701T120000"},
        {"geek_id": "blacklisted", "job_name": "Java工程师", "first_seen_at": "20260710T120000", "blacklisted": True},
        {"geek_id": "other-job", "job_name": "Python工程师", "first_seen_at": "20260710T120000"},
    ]

    with patch.object(gui, "_load_candidates_for_state_diagnostics", return_value=(candidates[:3], "Java 工程师")):
        rows, scope = gui._load_candidates_for_daily_actions()

    assert [row["geek_id"] for row in rows] == ["included"]
    assert scope == "Java 工程师 / 近7天"


def test_result_time_range_only_shows_dates_for_custom_mode():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_time_range_var = _FakeVar("自定义")
    gui.result_custom_date_frame = _FakePackFrame()
    gui.result_tree = object()
    gui.refresh_results = Mock()
    gui._close_result_date_dropdowns = Mock()
    gui._ensure_result_custom_date_entries = Mock()

    gui._on_result_time_range_changed()
    assert gui.result_custom_date_frame.winfo_manager() == "pack"
    gui._ensure_result_custom_date_entries.assert_called_once_with()

    gui.result_time_range_var.set("全部时间")
    gui._on_result_time_range_changed()
    assert gui.result_custom_date_frame.winfo_manager() == ""
    assert gui.refresh_results.call_count == 2
    assert gui._close_result_date_dropdowns.call_count == 2


def test_time_range_dropdown_closes_both_open_date_calendars():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    start_top = _FakeCalendarTop()
    end_top = _FakeCalendarTop()
    gui.result_date_start_entry = Mock(_top_cal=start_top)
    gui.result_date_end_entry = Mock(_top_cal=end_top)

    gui._close_result_date_dropdowns()

    assert start_top.mapped is False
    assert end_top.mapped is False


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
    gui._confirm_job_config_diagnostics = Mock(return_value=True)
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
    gui._confirm_job_config_diagnostics = Mock(return_value=True)
    gui.save_config = Mock()

    with patch("gui_main.messagebox.showinfo"), patch("gui_main.messagebox.showwarning"):
        gui.save_current_job()

    required = gui.job_rules["Java 工程师"]["required_conditions"]
    assert required[0] == {"type": "or", "items": ["债券", "基金"], "category": "金融投资行业经验"}
    assert "_evidence" not in required[0]


def test_job_config_diagnostics_preview_uses_current_form_without_saving():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.job_name_var = _FakeVar("  Java   工程师  ")
    gui.min_exp_var = _FakeVar("5")
    gui.max_age_var = _FakeVar("")
    gui.edu_var = _FakeVar("本科")
    gui.work_location_var = _FakeVar("南京")
    gui.salary_min_var = _FakeVar("20")
    gui.salary_max_var = _FakeVar("30")
    gui.skills_data = [
        {"name": "Java", "weight": 2, "source": "手动"},
        {"name": "Spring", "weight": 2, "source": "手动"},
        {"name": "证券行业", "weight": 1, "source": "优先"},
    ]
    gui.required_conditions_data = [
        {"type": "or", "items": ["基金", "债券"], "_evidence": "原文"}
    ]
    gui._get_requirement_text = Mock(return_value="原始需求")

    name, rule = gui._build_current_job_rule_preview()

    assert name == "Java 工程师"
    assert rule["max_age"] is None
    assert rule["keywords"] == [
        {"name": "Java", "weight": 2},
        {"name": "Spring", "weight": 2},
    ]
    assert rule["preferred_keywords"] == [{"name": "证券行业", "bonus": 1}]
    assert rule["required_conditions"] == [{"type": "or", "items": ["基金", "债券"]}]
    assert not hasattr(gui, "job_rules")


def test_save_current_job_stops_when_diagnostics_cancel_save():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.job_name_var = _FakeVar("Python 工程师")
    gui.min_exp_var = _FakeVar("3")
    gui.max_age_var = _FakeVar("")
    gui.edu_var = _FakeVar("本科")
    gui.work_location_var = _FakeVar("")
    gui.salary_min_var = _FakeVar("")
    gui.salary_max_var = _FakeVar("")
    gui.skills_data = [{"name": "Python", "weight": 2, "source": "解析"}]
    gui.required_conditions_data = []
    gui.job_rules = {}
    gui.config_job_combo = _FakeCombo()
    gui._job_step_active = -1
    gui._hide_save_hint = Mock()
    gui._get_requirement_text = Mock(return_value="原始需求")
    gui._confirm_job_config_diagnostics = Mock(return_value=False)
    gui.save_config = Mock()

    with patch("gui_main.messagebox.showinfo"), patch("gui_main.messagebox.showwarning"):
        gui.save_current_job()

    assert gui.job_rules == {}
    gui.save_config.assert_not_called()


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
    """列数只由表格实际可用宽度驱动：<1250px 8 列，≥1250px 11 列，≥1700px 13 列；
    富余宽度显式分配给各列（ttk stretch 只会收缩不会放大，否则会右侧留白且表头截断）。"""
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = _FakeRoot()

    # 窄窗口（小于 8 列基础宽度合计 735）：保持基础宽度并允许收缩
    gui.result_tree = _FakeTree(700)
    gui._update_result_tree_columns()
    assert len(gui.result_tree.displaycolumns) == 8
    assert gui.result_tree.column_options["skills"]["width"] == 85
    assert gui.result_tree.column_options["skills"]["stretch"] is True

    # 富余宽度按比例放大填满表格
    gui.result_tree = _FakeTree(1200)
    gui._update_result_tree_columns()
    assert len(gui.result_tree.displaycolumns) == 8
    assert gui.result_tree.column_options["skills"]["width"] > 85
    assert gui.result_tree.column_options["skills"]["stretch"] is False
    assert sum(
        options["width"] for options in gui.result_tree.column_options.values()
    ) == 1198

    gui.result_tree = _FakeTree(1400)
    gui._update_result_tree_columns()
    assert len(gui.result_tree.displaycolumns) == 11
    assert sum(
        options["width"] for options in gui.result_tree.column_options.values()
    ) == 1398

    gui.result_tree = _FakeTree(1700)
    gui._update_result_tree_columns()
    assert len(gui.result_tree.displaycolumns) == 13
    assert gui.result_tree.displaycolumns[-2:] == ("school", "company")
    assert gui.result_tree.column_options["school"]["width"] > 150
    assert gui.result_tree.column_options["company"]["width"] > 170
    assert gui.result_tree.column_options["level"]["width"] < 110
    assert gui.result_tree.column_options["education"]["width"] == 140
    assert gui.result_tree.column_options["age"]["width"] == 110
    assert gui.result_tree.column_options["job_status"]["width"] > 120
    assert gui.result_tree.column_options["skills"]["width"] < 140
    assert gui.result_tree.column_options["name"]["stretch"] is False
    assert gui.result_tree.column_options["education"]["stretch"] is False
    assert gui.result_tree.column_options["skills"]["stretch"] is False
    assert gui.result_tree.column_options["school"]["stretch"] is False
    assert gui.result_tree.column_options["company"]["stretch"] is False
    assert sum(
        options["width"] for options in gui.result_tree.column_options.values()
    ) == 1698

    gui.result_tree = _FakeTree(0)
    gui._apply_result_tree_column_widths((
        "name", "exp", "salary", "skills", "score", "ai_eval", "level", "status",
        "education", "age", "job_status", "school", "company",
    ))
    assert gui.result_tree.column_options["job_status"]["width"] == 130
    assert gui.result_tree.column_options["company"]["width"] == 160


def test_stats_tree_columns_expand_with_available_width():
    """统计明细表与结果表同一套列宽逻辑：窄窗口保持基础宽度可收缩，
    宽窗口显式分配富余填满表格（ttk stretch 只会收缩不会放大）。"""
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = _FakeRoot()

    gui.stats_tree = _FakeTree(900)
    gui._update_stats_tree_columns()
    assert gui.stats_tree.column_options["job"]["width"] == 200
    assert gui.stats_tree.column_options["job"]["stretch"] is True

    gui.stats_tree = _FakeTree(1400)
    gui._update_stats_tree_columns()
    assert gui.stats_tree.column_options["job"]["width"] > 200
    assert gui.stats_tree.column_options["job"]["width"] <= 340
    assert gui.stats_tree.column_options["avg_score"]["width"] <= 105
    assert gui.stats_tree.column_options["job"]["stretch"] is False
    assert sum(
        options["width"] for options in gui.stats_tree.column_options.values()
    ) == 1398


def test_model_list_columns_keep_4k_widths_and_fit_narrow_screens():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = _FakeRoot(state="zoomed", width=3840, height=2000)
    gui.model_list_tree = _FakeTree(1800)

    gui._update_model_list_columns()

    assert gui.model_list_tree.column_options["name"]["width"] == 400
    assert gui.model_list_tree.column_options["provider"]["width"] == 300
    assert gui.model_list_tree.column_options["compat"]["width"] == 220
    assert gui.model_list_tree.column_options["base_url"]["width"] == 380
    assert "edu_ref" not in gui.model_list_tree.column_options

    gui.root = _FakeRoot(width=1920, height=1040)
    gui.model_list_tree = _FakeTree(920)
    gui._update_model_list_columns()

    widths_1080p = {
        column: options["width"]
        for column, options in gui.model_list_tree.column_options.items()
    }
    assert sum(widths_1080p.values()) <= 896
    assert widths_1080p["provider"] < 240
    assert widths_1080p["compat"] >= 160
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


def test_saved_model_list_keeps_library_fields_and_removes_derived_purpose_column():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    list_block = source[source.index("# 模型列表 Treeview"):]
    list_block = list_block[:list_block.index("# 滚动条（垂直 + 水平）")]
    load_block = source[source.index("def load_saved_models_to_tree"):]
    load_block = load_block[:load_block.index("\n    def _get_model_list_max_rows")]

    assert 'model_columns = ("name", "provider", "compat", "base_url")' in list_block
    assert 'heading("edu_ref"' not in list_block
    assert 'displaycolumns=("name", "provider", "compat", "base_url")' in list_block
    assert "purpose_display" not in load_block
    assert "values=(name, provider_display, status_display, base_url)" in load_block


def test_saved_model_list_marks_active_models_with_role_colors():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.colors = {
        "bg_tree_tag_high": "#E8F5E9",
        "success": "#43A047",
        "primary": "#1E88E5",
    }
    gui.PROVIDER_DISPLAY = {"qwen": "通义千问", "kimi": "Kimi", "deepseek": "DeepSeek"}
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com",
        "model": "qwen-plus",
        "education_model_ref": {
            "api_provider": "kimi",
            "base_url": "https://api.moonshot.cn",
            "model": "kimi-k2",
        },
        "saved_models": [
            {
                "api_provider": "qwen",
                "base_url": "https://dashscope.aliyuncs.com/",
                "model": "qwen-plus",
            },
            {
                "api_provider": "kimi",
                "base_url": "https://api.moonshot.cn",
                "model": "kimi-k2",
            },
            {
                "api_provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v3",
            },
        ],
    }
    gui.model_list_tree = _FakeResultTree()
    gui._update_model_list_height = Mock()
    gui._update_model_list_columns = Mock()
    gui._refresh_model_assignment_controls = Mock()
    gui._bind_mousewheel = Mock()
    gui.api_canvas = Mock()
    gui.api_scrollable_frame = Mock()

    gui.load_saved_models_to_tree()

    assert gui.model_list_tree.items[0]["tags"] == ("default_model",)
    assert gui.model_list_tree.items[1]["tags"] == ("education_model",)
    assert gui.model_list_tree.items[2]["tags"] == ()
    assert gui.model_list_tree.tags["default_model"] == {
        "background": "#E8F5E9", "foreground": "#43A047"
    }
    assert gui.model_list_tree.tags["education_model"] == {
        "background": "#E3F2FD", "foreground": "#1E88E5"
    }


def test_saved_model_list_marks_shared_default_and_education_model():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com",
        "model": "qwen-plus",
        "saved_models": [{
            "api_provider": "qwen",
            "base_url": "https://dashscope.aliyuncs.com",
            "model": "qwen-plus",
        }],
    }

    assert gui._saved_model_usage_tag(gui.api_config["saved_models"][0]) == (
        "default_and_education_model"
    )


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
        with patch.object(gui_main, "get_api_config_path", return_value=config_path):
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


def test_result_scope_label_matches_filter_label_style():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    result_block = source[source.index("def create_result_page"):]
    result_block = result_block[:result_block.index("\n    def create_education_page")]

    assert 'text="结果范围:"' in result_block


def test_result_page_defaults_to_all_records_and_offers_today():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    result_block = source[source.index("def create_result_page"):]
    result_block = result_block[:result_block.index("\n    def create_education_page")]

    assert 'values=("全部时间", "今天", "近7天", "近30天", "自定义")' in result_block
    assert 'self.result_view_var = tk.StringVar(value="全部记录")' in result_block
    assert '"推荐候选人", "人工确认", "待复核", "淘汰记录", "全部记录"' in result_block


def test_result_blacklist_check_blends_with_page_background_in_every_state():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    result_block = source[source.index("def create_result_page"):]
    result_block = result_block[:result_block.index("\n    def create_education_page")]
    style_block = result_block[result_block.index('_cb_style.configure('):]
    style_block = style_block[:style_block.index('blacklist_check = ttk.Checkbutton(')]

    assert "background=self.colors['bg_main']" in style_block
    for state in ("active", "pressed", "selected", "disabled"):
        assert f'(\"{state}\", self.colors[\'bg_main\'])' in style_block


def test_result_page_keeps_workflow_actions_visible_and_groups_utilities():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    result_block = source[source.index("def create_result_page"):]
    result_block = result_block[:result_block.index("\n    def create_education_page")]

    assert 'text=" 今日待办"' in result_block
    assert "self.icons.button('task_list', self.colors['primary'])" in result_block
    assert 'text=" 查看与复核"' in result_block
    assert "self.icons.button('candidate_review', self.colors['primary'])" in result_block
    review_button_block = result_block[result_block.index("self.result_review_button = ttk.Button("):]
    review_button_block = review_button_block[:review_button_block.index("self.result_review_button._icon_ref")]
    assert "style='Accent.TButton'" not in review_button_block
    assert 'text=" 联系候选人"' in result_block
    assert 'text="更多操作"' in result_block
    assert 'label=" 查看今日待办"' not in result_block
    assert 'label=" 候选人状态体检"' in result_block
    assert "self.icons.button('health_shield', self.colors['primary'])" in result_block
    assert 'label=" 导出 Excel"' in result_block
    assert 'label=" 清空候选人"' in result_block
    assert 'text=" 状态体检"' not in result_block
    assert 'text=" 导出 Excel"' not in result_block
    assert 'text=" 清空候选人"' not in result_block
    assert "pady=(int(20 * self.dpi_scale * self.zoom_factor), 0)" in result_block


def test_system_settings_model_name_matches_provider_control_width():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    settings_block = source[source.index("# 模型接入配置"):]
    settings_block = settings_block[:settings_block.index("# 第三行：API Key")]
    styles_block = source[source.index("def setup_styles"):]
    styles_block = styles_block[:styles_block.index("\n    def create_sidebar")]

    assert "self.api_provider_combo = ttk.Combobox" in settings_block
    assert "width=18, font=self.font_label" in settings_block
    assert "model_entry = ttk.Entry(" in settings_block
    assert "width=18," in settings_block
    assert "style='SettingsModel.TEntry'" in settings_block
    assert "style.configure('SettingsModel.TEntry', padding=(8, 0))" in styles_block


def test_more_action_menubuttons_share_centered_overlay_arrow_style():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    setup_block = source[source.index("def setup_styles"):]
    setup_block = setup_block[:setup_block.index("\n    def _create_status_icons")]

    assert "style.layout(" in setup_block
    assert "'CenteredActions.TMenubutton'" in setup_block
    assert "('Menubutton.dropdown', {'sticky': 'e'})" in setup_block
    assert "padding=(24, 8)" in setup_block
    assert "anchor='center'" in setup_block
    assert "justify='center'" in setup_block
    assert source.count("style='CenteredActions.TMenubutton'") == 2
    assert "ConfigActions.TMenubutton" not in source
    assert "ResultActions.TMenubutton" not in source

    config_block = source[source.index("def create_config_page"):]
    config_block = config_block[:config_block.index("\n    def create_api_config_page")]
    result_block = source[source.index("def create_result_page"):]
    result_block = result_block[:result_block.index("\n    def create_education_page")]
    assert "tk.Menu(select_frame, tearoff=0, font=self.font_label)" in config_block
    assert "font=self.font_label," in result_block[result_block.index("more_menu = tk.Menu("):]
    assert "menu=more_menu,\n            width=9," in config_block
    assert "menu=more_menu,\n            width=9," in result_block
    assert "self.icons.button('trash', self.colors['danger'])" in config_block


def test_combobox_style_keeps_readonly_text_visible_and_clears_inactive_highlight():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    setup_block = source[source.index("def setup_styles"):]
    setup_block = setup_block[:setup_block.index("\n    def create_sidebar")]
    combo_map = setup_block[setup_block.index("style.map(\n            'TCombobox'"):]
    combo_map = combo_map[:combo_map.index("\n        style.map('TSpinbox'")]

    assert "('readonly', c['text_primary'])" in combo_map
    assert "('readonly', c['bg_card'])" in combo_map
    assert "('!focus', c['bg_card'])" in combo_map
    assert "('!focus', c['text_primary'])" in combo_map


def test_checkbutton_style_uses_large_checkmark_indicator_but_ai_keeps_switch():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    setup_block = source[source.index("def setup_styles"):]
    setup_block = setup_block[:setup_block.index("\n    def create_sidebar")]
    run_block = source[source.index("# AI 辅助评估开关"):]
    run_block = run_block[:run_block.index("\n        yield")]

    assert "checkbox_size = max(24, int(round(24 * fs)))" in setup_block
    assert "checkbox_indicator = 'App.Checkbutton.indicator'" in setup_block
    assert "('selected', checkbox_on)" in setup_block
    assert "style.layout(\n            'TCheckbutton'" in setup_block
    check_layout = setup_block[setup_block.index("style.layout(\n            'TCheckbutton'"):]
    check_layout = check_layout[:check_layout.index("\n        style.configure(")]
    assert "Checkbutton.focus" not in check_layout
    assert "('Checkbutton.label', {" in check_layout
    assert "enabled_variable=self.ai_eval_available_var" in run_block


def test_ai_switch_is_compact_and_supersampled():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    switch_block = source[source.index("def _create_switch("):]
    switch_block = switch_block[:switch_block.index("\n    def _styled_tooltip")]

    assert "int(round(30 * scale))" in switch_block
    assert "int(round(16 * scale))" in switch_block
    assert "render_scale = 4" in switch_block
    assert "Image.Resampling.LANCZOS" in switch_block
    assert "ImageTk.PhotoImage(image)" in switch_block
    assert "canvas.create_image" in switch_block
    assert "canvas.create_oval" not in switch_block
    assert "if not _is_enabled():" in switch_block
    assert "variable.set(False)" in switch_block
    assert "enabled_variable.trace_add" in switch_block


def test_ai_eval_status_disables_switch_when_model_is_not_configured():
    gui = object.__new__(BossFilterGUI)
    gui.api_config = {"api_provider": "deepseek", "model": "deepseek-chat", "api_key": ""}
    gui.ai_status_label = Mock()
    gui.ai_eval_var = Mock()
    gui.ai_eval_var.get.return_value = True
    gui.ai_eval_available_var = Mock()
    gui.ai_eval_label = Mock()
    gui.colors = {
        "success": "green",
        "warning": "orange",
        "text_primary": "black",
        "text_muted": "gray",
    }
    gui._ai_eval_auto_done = True

    gui._update_ai_eval_status()

    gui.ai_eval_available_var.set.assert_called_once_with(False)
    gui.ai_eval_var.set.assert_called_once_with(False)
    gui.ai_status_label.config.assert_called_once_with(
        text="⚠ 未配置", foreground="orange"
    )
    gui.ai_eval_label.configure.assert_called_once_with(
        cursor="arrow", foreground="gray"
    )


def test_ai_eval_status_enables_switch_only_with_complete_model_config():
    gui = object.__new__(BossFilterGUI)
    gui.api_config = {
        "api_provider": "deepseek",
        "model": "deepseek-chat",
        "api_key": "secret",
    }
    gui.ai_status_label = Mock()
    gui.ai_eval_var = Mock()
    gui.ai_eval_available_var = Mock()
    gui.ai_eval_label = Mock()
    gui.colors = {
        "success": "green",
        "warning": "orange",
        "text_primary": "black",
        "text_muted": "gray",
    }
    gui._ai_eval_auto_done = True

    gui._update_ai_eval_status()

    gui.ai_eval_available_var.set.assert_called_once_with(True)
    gui.ai_eval_var.set.assert_not_called()
    gui.ai_status_label.config.assert_called_once_with(
        text="✓ 已配置", foreground="green"
    )
    gui.ai_eval_label.configure.assert_called_once_with(
        cursor="hand2", foreground="black"
    )


def test_button_style_removes_inner_focus_ring_and_keeps_outer_focus_border():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    setup_block = source[source.index("def setup_styles"):]
    setup_block = setup_block[:setup_block.index("\n    def create_sidebar")]
    button_layout = setup_block[setup_block.index("style.layout(\n            'TButton'"):]
    button_layout = button_layout[:button_layout.index("\n        style.map('TButton'")]

    assert "Button.focus" not in button_layout
    assert "('Button.border', {" in button_layout
    assert "('Button.padding', {" in button_layout
    assert "('Button.label', {'sticky': 'nswe'})" in button_layout
    assert "bordercolor=[('focus', c['primary'])]" in setup_block


def test_more_menu_excel_export_uses_exact_visible_result_candidates():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    visible = [
        {"geek_id": "review-low", "job_name": "Java工程师", "match_score": 53},
        {"geek_id": "review-manual", "job_name": "Java工程师", "match_score": 72},
    ]
    gui.result_tree = Mock()
    gui.result_tree.get_children.return_value = ("row-1", "row-2")
    gui._find_candidate_by_tree_item = Mock(side_effect=visible)
    gui.refresh_results = Mock()
    gui.result_job_var = _FakeVar("Java工程师")
    gui.result_view_var = _FakeVar("待复核")
    gui.result_date_start_entry = Mock()
    gui._get_result_date_filter = Mock(return_value=("20260709", "20260715"))
    gui.append_log = Mock()

    class _FakeRoot:
        def after(self, _delay, callback):
            callback()

    class _SyncThread:
        """测试用同步线程：start 立即执行 target，让异步导出可断言。"""

        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    gui.root = _FakeRoot()

    with (
        patch("gui_main.filedialog.asksaveasfilename", return_value="review.xlsx") as save_dialog,
        patch("bossmaster.export_to_excel", return_value=True) as export_mock,
        patch("gui_main.threading.Thread", _SyncThread),
        patch.object(BossFilterGUI, "_status_flash") as flash,
    ):
        gui.export_excel()

    gui.refresh_results.assert_called_once_with(force=True)
    export_mock.assert_called_once_with(visible, "review.xlsx", preserve_input=True)
    assert save_dialog.call_args.kwargs["initialfile"] == (
        "Java工程师_待复核_20260709_20260715.xlsx"
    )
    assert "2 名候选人" in flash.call_args.args[0]


def test_background_export_reports_worker_failure_on_ui_thread():
    class _ImmediateRoot:
        def after(self, _delay, callback):
            callback()

    class _SyncThread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = _ImmediateRoot()
    gui.status_bar_left_var = _FakeVar()

    with (
        patch("bossmaster.export_to_excel", side_effect=RuntimeError("disk full")),
        patch("gui_main.threading.Thread", _SyncThread),
        patch("gui_main.messagebox.showerror") as showerror,
    ):
        gui._run_export([{"geek_id": "candidate-1"}], "failed.xlsx")

    assert gui.status_bar_left_var.get() == ""
    assert "disk full" in showerror.call_args.args[1]
    assert showerror.call_args.kwargs["parent"] is gui.root


def test_model_settings_use_explicit_role_selectors_not_hidden_actions():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    settings_block = source[source.index("def _create_api_config_content"):]
    settings_block = settings_block[:settings_block.index("\n    def load_api_config_to_ui")]

    assert '"使用中的模型"' in settings_block
    assert 'text="默认 AI 模型:"' in settings_block
    assert 'text="学历核验模型:"' in settings_block
    assert "label_width_assignment = 14" in settings_block
    assert "model_choice_width = 34" in settings_block
    assert "traffic_light_size = int(32 * self.dpi_scale * self.zoom_factor)" in settings_block
    assert "traffic_light_pending" in settings_block
    assert "traffic_light_success" in settings_block
    assert "traffic_light_error" in settings_block
    assert "pulse_check" not in settings_block
    assert 'width=model_choice_width' in settings_block
    assert "width=UI_CONFIG['entry_width_url']" in settings_block
    assert "self.api_key_entry = ttk.Entry(" in settings_block
    api_key_block = settings_block[settings_block.index("self.api_key_entry = ttk.Entry("):]
    api_key_block = api_key_block[:api_key_block.index("self.api_key_entry.pack")]
    assert "width=UI_CONFIG['entry_width_url']" in api_key_block
    assert 'sticky="w"' in settings_block
    assert "btn_test_default_model" in settings_block
    assert "btn_test_education_model" in settings_block
    assert "_assigned_model_test_status_labels" in settings_block
    assert 'text="未检测"' in settings_block
    assert "font=self.font_small" not in settings_block
    assert "tk.Label(" in settings_block
    assert '<Button-1>", lambda _e: self._test_assigned_model("default")' in settings_block
    assert '<Button-1>", lambda _e: self._test_assigned_model("education")' in settings_block
    assert '_show_assigned_model_test_tooltip("default", e)' in settings_block
    assert '_show_assigned_model_test_tooltip("education", e)' in settings_block
    assert 'text="测试"' not in settings_block
    assert '"模型接入"' in settings_block
    assert '"已保存模型（双击切换）"' not in settings_block
    assert 'label="切换模型"' not in settings_block
    assert 'label="设为学历核验模型"' not in settings_block
    assert 'label="取消学历核验模型"' not in settings_block


def test_system_settings_populates_model_controls_before_page_is_visible():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    show_block = source[source.index("def show_page_api"):]
    show_block = show_block[:show_block.index("\n    def hide_all_pages")]

    assert "self._load_api_config_to_ui_if_needed()" in show_block
    assert "_defer_ui_work(\"api_config_to_ui\"" not in show_block
    assert show_block.index("self._load_api_config_to_ui_if_needed()") < show_block.index(
        "self.api_config_page.pack"
    )
    assert show_block.index("self._schedule_api_key_resolution()") > show_block.index(
        "self.api_config_page.pack"
    )


def test_system_settings_ignores_timeout_hint_from_cancelled_run_page():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.PROVIDER_DISPLAY = gui_main.PROVIDER_DISPLAY
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "llm_read_timeout": 60,
    }
    gui.api_provider_var = _FakeVar()
    gui.api_key_var = _FakeVar()
    gui.api_base_url_var = _FakeVar()
    gui.api_model_var = _FakeVar()
    gui.llm_read_timeout_var = _FakeVar()
    gui._timeout_hint_label = Mock()
    gui._timeout_hint_label.winfo_exists.return_value = False
    gui._is_relay_endpoint_for_timeout = Mock(return_value=False)
    gui.update_current_model_display = Mock()
    gui.load_saved_models_to_tree = Mock()

    gui.load_api_config_to_ui(resolve_key=False)

    gui._timeout_hint_label.config.assert_not_called()
    gui.update_current_model_display.assert_called_once_with()
    gui.load_saved_models_to_tree.assert_called_once_with()


def test_api_key_resolution_waits_for_settings_page_first_frame():
    class FakeRoot:
        def __init__(self):
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))
            return "after-id"

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()
    gui.current_page_index = 6
    gui._api_key_resolve_thread = None
    gui._api_key_resolve_after_id = None
    gui._resolve_api_keys_async = Mock()

    gui._schedule_api_key_resolution()

    assert gui.root.scheduled[0][0] == 250
    assert gui._resolve_api_keys_async.call_count == 0
    gui.root.scheduled[0][1]()
    gui._resolve_api_keys_async.assert_called_once_with()
    assert gui._api_key_resolve_after_id is None


def test_api_key_cache_reuses_successful_normalized_identity():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._api_key_cache = {}

    with patch("gui_main.get_api_key", return_value="secret") as get_key:
        assert gui._get_api_key_cached("qwen", "https://example.test/v1/") == "secret"
        assert gui._get_api_key_cached("qwen", "https://example.test/v1") == "secret"

    get_key.assert_called_once_with("qwen", "https://example.test/v1/")


def test_api_key_background_resolution_reads_only_current_model():
    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target
            self.daemon = daemon

        def is_alive(self):
            return False

        def start(self):
            self.target()

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://current.example/v1",
        "model": "current-model",
        "saved_models": [
            {
                "api_provider": "deepseek",
                "base_url": "https://saved.example/v1",
                "model": "saved-model",
            }
        ],
    }
    gui._api_key_cache = {}
    gui._api_key_resolve_thread = None
    gui.api_key_var = _FakeVar()
    gui._update_ai_eval_status = Mock()
    gui.run_on_ui = lambda callback: callback()

    with (
        patch("gui_main.threading.Thread", ImmediateThread),
        patch("gui_main.get_api_key", return_value="current-secret") as get_key,
    ):
        gui._resolve_api_keys_async()

    get_key.assert_called_once_with("qwen", "https://current.example/v1")
    assert gui.api_config["api_key"] == "current-secret"
    assert gui.api_key_var.get() == "current-secret"


def test_api_key_background_resolution_marks_missing_current_model_only():
    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def is_alive(self):
            return False

        def start(self):
            self.target()

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://current.example/v1",
        "model": "current-model",
        "saved_models": [{"api_provider": "deepseek", "base_url": "https://saved.example/v1"}],
    }
    gui._api_key_cache = {}
    gui._api_key_resolve_thread = None
    gui.api_key_var = _FakeVar()
    gui.api_status_frame = object()
    gui.colors = {"warning": "orange"}
    gui._update_api_status = Mock()
    gui._update_ai_eval_status = Mock()
    gui.run_on_ui = lambda callback: callback()

    with (
        patch("gui_main.threading.Thread", ImmediateThread),
        patch("gui_main.get_api_key", return_value=None) as get_key,
    ):
        gui._resolve_api_keys_async()

    get_key.assert_called_once_with("qwen", "https://current.example/v1")
    assert gui.api_config["needs_reconfigure"] is True
    gui._update_api_status.assert_called_once_with(
        text="当前模型的 API Key 未配置，请重新输入并保存模型",
        foreground="orange",
    )


def test_deferred_page_work_is_skipped_after_navigation():
    class FakeRoot:
        def __init__(self):
            self.idle_callbacks = []

        def after_idle(self, callback):
            self.idle_callbacks.append(callback)

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()
    gui.current_page_index = 0
    gui._pending_idle_tasks = set()
    callback = Mock()

    gui._defer_ui_work("result_refresh", callback, page_index=3)
    gui.root.idle_callbacks.pop()()

    callback.assert_not_called()
    assert gui._pending_idle_tasks == set()

    gui.current_page_index = 3
    gui._defer_ui_work("result_refresh", callback, page_index=3)
    gui.root.idle_callbacks.pop()()
    callback.assert_called_once_with()


def test_sidebar_first_open_paints_loading_frame_before_building_page():
    class FakeRoot:
        def __init__(self):
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()
    gui.config_page = None
    gui._page_loading_var = _FakeVar()
    gui._page_loading_frame = _FakePackFrame()
    gui._pending_page_builds = set()
    gui.hide_all_pages = Mock()
    gui._schedule_page_width_policy = Mock()
    gui.update_nav_highlight = Mock()
    creator = Mock(side_effect=lambda: setattr(gui, "config_page", object()))
    show_page = Mock()

    gui._request_page_first_open(
        1, "config_page", "岗位配置", creator, show_page
    )

    assert gui.current_page_index == 1
    assert gui._page_loading_var.get() == "正在打开岗位配置…"
    assert gui._page_loading_frame.winfo_manager() == "pack"
    creator.assert_not_called()
    assert gui.root.scheduled[0][0] == 30

    gui.root.scheduled[0][1]()
    creator.assert_called_once_with()
    show_page.assert_called_once_with()
    assert gui._pending_page_builds == set()


def test_sidebar_first_open_advances_staged_page_one_chunk_per_callback():
    class FakeRoot:
        def __init__(self):
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()
    gui.config_page = None
    gui._page_loading_var = _FakeVar()
    gui._page_loading_frame = _FakePackFrame()
    gui._pending_page_builds = set()
    gui.hide_all_pages = Mock()
    gui._schedule_page_width_policy = Mock()
    gui.update_nav_highlight = Mock()
    events = []
    show_page = Mock()
    on_ready = Mock()

    def staged_creator():
        gui.config_page = object()
        events.append("first")
        yield
        events.append("second")
        yield
        events.append("complete")

    gui._request_page_first_open(
        1, "config_page", "岗位配置", staged_creator, show_page, on_ready=on_ready
    )

    delay, callback = gui.root.scheduled.pop(0)
    assert delay == 30
    callback()
    assert events == ["first"]
    show_page.assert_not_called()
    on_ready.assert_not_called()
    assert gui._pending_page_builds == {"config_page"}

    delay, callback = gui.root.scheduled.pop(0)
    assert delay == 1
    callback()
    assert events == ["first", "second"]
    show_page.assert_not_called()
    on_ready.assert_not_called()

    delay, callback = gui.root.scheduled.pop(0)
    assert delay == 1
    callback()
    assert events == ["first", "second", "complete"]
    show_page.assert_called_once_with()
    on_ready.assert_called_once_with()
    assert gui._pending_page_builds == set()
    assert gui.root.scheduled == []


def test_sidebar_first_open_cancels_staged_page_after_navigation():
    class FakeRoot:
        def __init__(self):
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))

    partial_page = Mock()
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()
    gui.run_page = None
    gui._page_loading_var = _FakeVar()
    gui._page_loading_frame = _FakePackFrame()
    gui._pending_page_builds = set()
    gui.hide_all_pages = Mock()
    gui._schedule_page_width_policy = Mock()
    gui.update_nav_highlight = Mock()
    events = []
    show_page = Mock()

    def staged_creator():
        gui.run_page = partial_page
        events.append("first")
        yield
        events.append("must-not-run")

    gui._request_page_first_open(
        2, "run_page", "运行控制", staged_creator, show_page
    )
    gui.root.scheduled.pop(0)[1]()
    gui.current_page_index = 0
    gui.root.scheduled.pop(0)[1]()

    assert events == ["first"]
    partial_page.destroy.assert_called_once_with()
    assert gui.run_page is None
    assert gui._pending_page_builds == set()
    show_page.assert_not_called()


def test_sidebar_first_open_skips_stale_build_after_navigation():
    class FakeRoot:
        def __init__(self):
            self.callback = None

        def after(self, _delay, callback):
            self.callback = callback

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()
    gui.result_page = None
    gui._page_loading_var = _FakeVar()
    gui._page_loading_frame = _FakePackFrame()
    gui._pending_page_builds = set()
    gui.hide_all_pages = Mock()
    gui._schedule_page_width_policy = Mock()
    gui.update_nav_highlight = Mock()
    creator = Mock()
    show_page = Mock()

    gui._request_page_first_open(
        3, "result_page", "筛选结果", creator, show_page
    )
    gui.current_page_index = 0
    gui.root.callback()

    creator.assert_not_called()
    show_page.assert_not_called()
    assert gui._pending_page_builds == set()


def test_sidebar_first_open_cleans_partial_page_after_build_failure():
    class FakeRoot:
        def __init__(self):
            self.callback = None

        def after(self, _delay, callback):
            self.callback = callback

    partial_page = Mock()
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()
    gui.run_page = None
    gui._page_loading_var = _FakeVar()
    gui._page_loading_frame = _FakePackFrame()
    gui._pending_page_builds = set()
    gui.hide_all_pages = Mock()
    gui._schedule_page_width_policy = Mock()
    gui.update_nav_highlight = Mock()

    def fail_build():
        gui.run_page = partial_page
        raise RuntimeError("broken widget")

    with patch("gui_main.messagebox.showerror") as show_error:
        gui._request_page_first_open(
            2, "run_page", "运行控制", fail_build, Mock()
        )
        gui.root.callback()

    partial_page.destroy.assert_called_once_with()
    assert gui.run_page is None
    assert gui._page_loading_var.get() == "运行控制打开失败"
    assert "broken widget" in show_error.call_args.args[1]


def test_sidebar_cached_page_is_shown_without_loading_frame():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.stats_page = object()
    show_page = Mock()
    on_ready = Mock()

    gui._request_page_first_open(
        5, "stats_page", "数据统计", Mock(), show_page, on_ready=on_ready
    )

    show_page.assert_called_once_with()
    on_ready.assert_called_once_with()


def test_global_shortcuts_do_not_bind_unsafe_candidate_snapshot_save():
    class FakeRoot:
        def __init__(self):
            self.bindings = {}

        def bind(self, event, callback):
            self.bindings[event] = callback

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()

    gui._setup_global_shortcuts()

    assert '<Control-s>' not in gui.root.bindings
    assert {'<F5>', '<Control-f>', '<Delete>'}.issubset(gui.root.bindings)


def test_page_specs_cover_each_sidebar_identity_once():
    assert tuple(PAGE_SPECS) == tuple(PageIndex)
    assert PAGE_SPECS[PageIndex.RESULTS].full_width is False
    assert PAGE_SPECS[PageIndex.STATS].full_width is False
    assert PAGE_SPECS[PageIndex.SETTINGS].page_attr == "api_config_page"


def test_f5_refreshes_only_the_visible_result_page():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.current_page_index = PageIndex.RESULTS
    gui.refresh_results = Mock()
    gui.refresh_home_stats = Mock()
    gui.refresh_stats = Mock()
    gui._status_flash = Mock()

    gui._shortcut_refresh()

    gui.refresh_results.assert_called_once_with(force=True)
    gui.refresh_home_stats.assert_not_called()
    gui.refresh_stats.assert_not_called()
    gui._status_flash.assert_called_once_with("已刷新")


def test_f5_refreshes_home_without_rebuilding_hidden_results():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.current_page_index = PageIndex.HOME
    gui.refresh_results = Mock()
    gui.refresh_home_stats = Mock()
    gui.refresh_stats = Mock()
    gui._status_flash = Mock()

    gui._shortcut_refresh()

    gui.refresh_home_stats.assert_called_once_with()
    gui.refresh_results.assert_not_called()
    gui.refresh_stats.assert_not_called()


def test_f5_does_not_refresh_unrelated_heavy_pages():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.current_page_index = PageIndex.CONFIG
    gui.refresh_results = Mock()
    gui.refresh_home_stats = Mock()
    gui.refresh_stats = Mock()
    gui._status_flash = Mock()

    gui._shortcut_refresh()

    gui.refresh_results.assert_not_called()
    gui.refresh_home_stats.assert_not_called()
    gui.refresh_stats.assert_not_called()
    gui._status_flash.assert_called_once_with("当前页面无需刷新")


def test_ctrl_f_waits_for_result_page_before_focusing_search():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_search_entry = Mock()

    def request_page(page_index, on_ready=None):
        assert page_index == 3
        gui.result_search_entry.focus_set.assert_not_called()
        on_ready()

    gui._request_sidebar_page = Mock(side_effect=request_page)

    gui._shortcut_focus_search()

    gui.result_search_entry.focus_set.assert_called_once_with()
    gui.result_search_entry.select_range.assert_called_once_with(0, 'end')


def test_global_click_moves_focus_out_of_single_line_input_controls():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = Mock()
    gui.result_search_entry = Mock()
    other_widget = Mock()
    other_widget.winfo_class.return_value = "TLabel"

    gui.root.tk.call.side_effect = lambda *args: (
        str(gui.result_search_entry) if args == ('focus',) else 'TEntry'
    )
    gui._on_global_left_click(types.SimpleNamespace(widget=other_widget))
    gui.root.focus_set.assert_called_once_with()

    gui.root.focus_set.reset_mock()
    gui._on_global_left_click(types.SimpleNamespace(widget=gui.result_search_entry))
    gui.root.focus_set.assert_not_called()

    gui.root.tk.call.side_effect = lambda *args: (
        ".!combobox.popdown.f.l" if args == ('focus',) else 'Listbox'
    )
    gui._on_global_left_click(types.SimpleNamespace(widget=Mock()))
    gui.root.focus_set.assert_not_called()

    for focus_path, widget_class in (
        (".!combobox", "TCombobox"),
        (".!spinbox", "TSpinbox"),
        (".!spinbox2", "Spinbox"),
        (".!entry", "TEntry"),
        (".!entry2", "Entry"),
    ):
        gui.root.tk.call.side_effect = lambda *args, path=focus_path, cls=widget_class: (
            path if args == ('focus',) else cls
        )
        gui._on_global_left_click(types.SimpleNamespace(widget=other_widget))

    assert gui.root.focus_set.call_count == 5

    input_widget = Mock()
    input_widget.winfo_class.return_value = "TEntry"
    gui.root.focus_set.reset_mock()
    gui.root.tk.call.side_effect = lambda *args: (
        ".!combobox" if args == ('focus',) else 'TCombobox'
    )
    gui._on_global_left_click(types.SimpleNamespace(widget=input_widget))
    gui.root.focus_set.assert_not_called()


@patch("gui_main.load_pending_contact_queue_count", return_value=3)
def test_nav_badge_uses_queue_module_before_runtime_queue_is_loaded(mock_load_count):
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._greet_queue_loaded = False
    gui.greet_queue_items = []
    gui.set_nav_badge = Mock()

    gui._refresh_nav_badges()

    mock_load_count.assert_called_once_with(gui_main.CONTACT_QUEUE_PATH)
    gui.set_nav_badge.assert_called_once_with(PageIndex.RESULTS, 3)


@patch("gui_main.load_pending_contact_queue_count")
def test_nav_badge_uses_memory_after_runtime_queue_is_loaded(mock_load_count):
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._greet_queue_loaded = True
    gui.greet_queue_items = [
        {"status": "待核实"},
        {"status": "发送失败"},
        {"status": "发送中"},
    ]
    gui.set_nav_badge = Mock()

    gui._refresh_nav_badges()

    mock_load_count.assert_not_called()
    gui.set_nav_badge.assert_called_once_with(PageIndex.RESULTS, 2)


def test_home_and_result_empty_state_use_staged_navigation_entrypoint():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    home_block = source[source.index("def create_home_page"):]
    home_block = home_block[:home_block.index("\n    def create_config_page")]
    result_block = source[source.index("def create_result_page"):]
    result_block = result_block[:result_block.index("\n    def create_education_page")]

    assert "command=lambda: self._request_sidebar_page(PageIndex.RUN)" in home_block
    assert "command=lambda: self._request_sidebar_page(PageIndex.RESULTS)" in home_block
    assert "command=lambda: self._request_sidebar_page(PageIndex.CONFIG)" in home_block
    assert "action_command=lambda: self._request_sidebar_page(PageIndex.RUN)" in result_block
    assert "command=self.show_page_run" not in home_block
    assert "command=self.show_page_config" not in home_block


def test_navigation_highlight_updates_only_previous_and_current_items():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.nav_components = [object() for _ in range(7)]
    gui.current_page_index = 3
    gui._highlighted_page_index = 1
    gui._apply_nav_state = Mock()

    gui.update_nav_highlight()
    gui.update_nav_highlight()

    assert gui._apply_nav_state.call_args_list == [
        call(gui.nav_components[1], "default"),
        call(gui.nav_components[3], "selected"),
    ]


def test_result_search_replaces_query_against_full_original_rows():
    rows = {
        "row-a": {"name": "张三", "score": "80"},
        "row-b": {"name": "李四", "score": "70"},
    }
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_tree = _SearchSortResultTree(rows)
    gui.result_search_var = _FakeVar("张")
    gui.result_count_var = _FakeVar()
    gui.result_tree_data = [{}, {}]
    gui._item_to_candidate = {
        "row-a": {"name": "张三", "match_score": 80},
        "row-b": {"name": "李四", "match_score": 70},
    }
    gui.colors = {"primary_dark": "#005E8A"}
    gui.font_table = ("Arial", 12)

    gui._filter_result_tree()
    assert gui.result_tree.get_children() == ("row-a",)

    gui.result_search_var.set("李")
    gui._filter_result_tree()
    assert gui.result_tree.get_children() == ("row-b",)
    assert gui.result_count_var.get() == "搜索命中 1 / 2 人"

    gui.result_search_var.set("")
    gui._filter_result_tree()
    assert gui.result_tree.get_children() == ("row-a", "row-b")
    assert gui.result_count_var.get() == "2 / 共 2 人"

    gui._result_search_placeholder_active = True
    gui.result_search_var.set("姓名/匹配分/推荐指数/状态")
    gui._filter_result_tree()
    assert gui.result_tree.get_children() == ("row-a", "row-b")
    assert gui.result_count_var.get() == "2 / 共 2 人"


def test_result_tree_sorting_handles_text_numeric_ranges_and_missing_values():
    rows = {
        "row-z": {"name": "Zulu", "salary": "15-25K", "score": "80"},
        "row-a": {"name": "Alpha", "salary": "10-12K", "score": "60"},
        "row-empty": {"name": "", "salary": "面议", "score": "—"},
    }
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_tree = _SearchSortResultTree(rows)
    gui._sort_col = None
    gui._sort_reverse = False
    gui._update_sort_indicators = Mock()

    gui._sort_treeview("name")
    assert gui.result_tree.get_children() == ("row-a", "row-z", "row-empty")

    gui._sort_treeview("name")
    assert gui.result_tree.get_children() == ("row-z", "row-a", "row-empty")

    gui._sort_treeview("score")
    assert gui.result_tree.get_children() == ("row-z", "row-a", "row-empty")

    gui._sort_treeview("salary")
    assert gui.result_tree.get_children() == ("row-a", "row-z", "row-empty")


def test_manual_result_refresh_restores_default_sort_and_keeps_active_search():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._sort_col = "name"
    gui._sort_reverse = True
    gui._tree_original_order = ["row-b", "row-a"]
    gui._column_headers = {"name": "姓名"}
    gui.result_search_var = _FakeVar("张")
    gui._result_search_placeholder_active = False
    gui.refresh_results = Mock()
    gui._update_sort_indicators = Mock()
    gui._filter_result_tree = Mock()

    gui._refresh_results_and_reset_sort()

    assert gui._sort_col is None
    assert gui._sort_reverse is False
    assert gui._tree_original_order is None
    gui.refresh_results.assert_called_once_with(force=True)
    gui._update_sort_indicators.assert_called_once_with()
    gui._filter_result_tree.assert_called_once_with()


def test_status_reset_does_not_overwrite_newer_operation_status():
    class FakeRoot:
        def __init__(self):
            self.callbacks = {}
            self.cancelled = []
            self.next_id = 0

        def after(self, _delay, callback):
            self.next_id += 1
            after_id = f"after-{self.next_id}"
            self.callbacks[after_id] = callback
            return after_id

        def after_cancel(self, after_id):
            self.cancelled.append(after_id)
            self.callbacks.pop(after_id, None)

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()
    gui.status_bar_left_var = _FakeVar("已刷新")

    gui._schedule_status_bar_reset("已刷新", 100)
    first_after_id = gui._status_flash_after_id
    first_callback = gui.root.callbacks[first_after_id]
    gui.status_bar_left_var.set("正在导出 2 名候选人…")
    first_callback()
    assert gui.status_bar_left_var.get() == "正在导出 2 名候选人…"

    gui.status_bar_left_var.set("已刷新")
    gui._schedule_status_bar_reset("已刷新", 100)
    gui.root.callbacks[gui._status_flash_after_id]()
    assert gui.status_bar_left_var.get() == ""

    gui._schedule_status_bar_reset("第二条提示", 100)
    second_after_id = gui._status_flash_after_id
    gui.status_bar_left_var.set("第二条提示")
    gui._schedule_status_bar_reset("第三条提示", 100)
    assert second_after_id in gui.root.cancelled


def test_stats_page_uses_centered_width_policy():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.pages_frame = Mock()
    gui.main_frame = Mock()
    gui.main_frame.winfo_width.return_value = 2400
    gui.dpi_scale = 1.0
    gui.zoom_factor = 1.0
    gui.current_page_index = 5
    gui._last_page_pack_padx = None

    gui._apply_page_width_policy()

    assert gui.pages_frame.pack_configure.call_args.kwargs["padx"] == max(
        int(gui_main.UI_CONFIG["page_padding_x"]),
        (2400 - int(gui_main.UI_CONFIG["content_max_width"])) // 2,
    )


def test_stats_tree_reflows_after_its_rendered_width_changes():
    """统计表监听自身最终宽度，确保最大化和恢复窗口都能重新分配列宽。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    stats_block = source[source.index("def create_stats_page"):]
    stats_block = stats_block[:stats_block.index("\n    def _load_stats_candidates")]

    assert 'self.stats_tree.bind(\n            "<Configure>",' in stats_block
    assert "lambda _event: self._schedule_page_width_policy()" in stats_block


def test_job_config_page_releases_bottom_padding_but_preserves_header_position():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.pages_frame = Mock()
    gui.main_frame = Mock()
    gui.main_frame.winfo_width.return_value = 1400
    gui.dpi_scale = 1.0
    gui.zoom_factor = 1.0
    gui.current_page_index = PageIndex.CONFIG
    gui._last_page_pack_padx = None
    gui._last_page_pack_pady = None
    gui._update_config_page_dynamic_heights = Mock()

    gui._apply_page_width_policy()

    assert gui.pages_frame.pack_configure.call_args.kwargs["pady"] == (
        gui_main.UI_CONFIG["page_padding_y"] - 15
    )
    source = Path("gui_main.py").read_text(encoding="utf-8")
    config_block = source[source.index("def create_config_page"):]
    config_block = config_block[:config_block.index("\n    def create_api_config_page")]
    assert 'self._create_page_header(self.config_page, "岗位配置", top_padding=15)' in config_block


def test_api_key_is_visible_only_while_eye_button_is_pressed():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.api_key_entry = _FakeWidget()
    gui.api_key_toggle_btn = _FakeWidget()
    gui.api_key_toggle_btn._icon_eye = "eye"
    gui.api_key_toggle_btn._icon_eye_off = "eye-off"
    gui.api_key_show_var = _FakeVar(False)

    gui._show_api_key_while_pressed()

    assert gui.api_key_entry.configs[-1] == {"show": ""}
    assert gui.api_key_toggle_btn.configs[-1] == {"image": "eye-off"}
    assert gui.api_key_show_var.get() is True

    gui._hide_api_key_after_release()

    assert gui.api_key_entry.configs[-1] == {"show": "*"}
    assert gui.api_key_toggle_btn.configs[-1] == {"image": "eye"}
    assert gui.api_key_show_var.get() is False


def test_api_key_eye_uses_press_and_release_bindings_not_click_toggle():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    settings_block = source[source.index("def _create_api_config_content"):]
    settings_block = settings_block[:settings_block.index("\n    def load_api_config_to_ui")]

    assert '"<ButtonPress-1>", self._show_api_key_while_pressed' in settings_block
    assert '"<ButtonRelease-1>", self._hide_api_key_after_release' in settings_block
    assert '"<Leave>", self._hide_api_key_after_release' in settings_block
    assert '"<FocusOut>", self._hide_api_key_after_release' in settings_block
    assert "command=self.toggle_api_key_visibility" not in settings_block


def test_api_key_eye_blends_with_settings_card_background():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    settings_block = source[source.index("def _create_api_config_content"):]
    settings_block = settings_block[:settings_block.index("\n    def load_api_config_to_ui")]
    button_block = settings_block[settings_block.index("self.api_key_toggle_btn = tk.Button("):]
    button_block = button_block[:button_block.index("self.api_key_toggle_btn._icon_eye")]

    assert 'relief="flat"' in button_block
    assert 'overrelief="flat"' in button_block
    assert "bg=self.colors['bg_card']" in button_block
    assert "activebackground=self.colors['bg_card']" in button_block


def test_assigned_model_test_result_updates_matching_traffic_light_only():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    default_button = _FakeWidget()
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://example.test/v1",
        "model": "qwen-plus",
        "education_model_ref": {
            "api_provider": "kimi",
            "base_url": "https://example.test/kimi/v1",
            "model": "k3",
        },
    }
    gui._assigned_model_test_buttons = {"default": default_button}
    gui._assigned_model_test_icons = {
        "pending": "yellow-light",
        "success": "green-light",
        "error": "red-light",
    }
    gui._assigned_model_test_states = {"default": "testing"}
    gui._assigned_model_test_tokens = {"default": 4}

    gui._apply_assigned_model_test_result({
        "assigned_role": "default",
        "assigned_test_token": 4,
        "assigned_model_ref": {
            "api_provider": "qwen",
            "base_url": "https://example.test/v1/",
            "model": "qwen-plus",
        },
    }, {"status": "success"})

    assert gui._assigned_model_test_states["default"] == "success"
    assert default_button.configs[-1] == {"image": "green-light"}


def test_assigned_model_test_result_syncs_both_roles_when_explicit_model_is_same():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    model_ref = {
        "api_provider": "kimi",
        "base_url": "https://api.kimi.com/coding/v1",
        "model": "k3",
    }
    gui.api_config = {
        **model_ref,
        "education_model_ref": dict(model_ref),
    }
    gui.colors = {
        "text_secondary": "gray", "warning": "yellow",
        "success": "green", "danger": "red",
    }
    gui._assigned_model_test_buttons = {
        "default": _FakeWidget(), "education": _FakeWidget(),
    }
    gui._assigned_model_test_status_labels = {
        "default": _FakeWidget(), "education": _FakeWidget(),
    }
    gui._assigned_model_test_icons = {
        "pending": "yellow-light", "success": "green-light", "error": "red-light",
    }
    gui._assigned_model_test_states = {"default": "testing", "education": "testing"}
    gui._assigned_model_test_tokens = {"default": 2, "education": 2}

    gui._apply_assigned_model_test_result({
        "assigned_role": "default",
        "assigned_test_token": 2,
        "assigned_model_ref": model_ref,
    }, {"status": "success"})

    assert gui._assigned_model_test_states == {
        "default": "success", "education": "success",
    }
    assert gui._assigned_model_test_status_labels["default"].configs[-1] == {
        "text": "已通过", "foreground": "green",
    }
    assert gui._assigned_model_test_status_labels["education"].configs[-1] == {
        "text": "已通过", "foreground": "green",
    }


def test_education_test_result_syncs_failure_back_to_default_when_following():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    model_ref = {
        "api_provider": "qwen",
        "base_url": "https://example.test/v1",
        "model": "qwen-plus",
    }
    gui.api_config = dict(model_ref)
    gui.colors = {
        "text_secondary": "gray", "warning": "yellow",
        "success": "green", "danger": "red",
    }
    gui._assigned_model_test_buttons = {
        "default": _FakeWidget(), "education": _FakeWidget(),
    }
    gui._assigned_model_test_status_labels = {
        "default": _FakeWidget(), "education": _FakeWidget(),
    }
    gui._assigned_model_test_icons = {
        "pending": "yellow-light", "success": "green-light", "error": "red-light",
    }
    gui._assigned_model_test_states = {"default": "testing", "education": "testing"}
    gui._assigned_model_test_tokens = {"default": 7, "education": 7}

    gui._apply_assigned_model_test_result({
        "assigned_role": "education",
        "assigned_test_token": 7,
        "assigned_model_ref": model_ref,
    }, {"status": "error"})

    assert gui._assigned_model_test_states == {
        "default": "error", "education": "error",
    }


def test_assigned_model_test_feedback_identifies_role_and_keeps_rows_independent():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.PROVIDER_DISPLAY = gui_main.PROVIDER_DISPLAY
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://example.test/v1",
        "model": "qwen-plus",
        "education_model_ref": {
            "api_provider": "kimi",
            "base_url": "https://example.test/kimi",
            "model": "kimi-k2.6",
        },
    }
    gui.colors = {
        "text_secondary": "gray",
        "warning": "yellow",
        "success": "green",
        "danger": "red",
    }
    gui._assigned_model_test_buttons = {
        "default": _FakeWidget(), "education": _FakeWidget(),
    }
    gui._assigned_model_test_status_labels = {
        "default": _FakeWidget(), "education": _FakeWidget(),
    }
    gui._assigned_model_test_icons = {
        "pending": "yellow-light", "success": "green-light", "error": "red-light",
    }
    gui._assigned_model_test_states = {"default": "pending", "education": "pending"}

    gui._set_assigned_model_test_state("default", "testing")
    gui._set_assigned_model_test_state("education", "success")

    assert gui._assigned_model_test_status_labels["default"].configs[-1] == {
        "text": "测试中", "foreground": "yellow",
    }
    assert gui._assigned_model_test_status_labels["education"].configs[-1] == {
        "text": "已通过", "foreground": "green",
    }
    default_target = gui._assigned_model_test_target_label("default")
    education_target = gui._assigned_model_test_target_label("education")
    assert default_target.startswith("默认 AI 模型（")
    assert default_target.endswith("/ qwen-plus）")
    assert education_target.startswith("学历核验模型（")
    assert education_target.endswith("/ kimi-k2.6）")


def test_assigned_model_connectivity_status_uses_role_and_model_name():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    test_block = source[source.index("def test_saved_model_connectivity"):]
    test_block = test_block[:test_block.index("\n    def _set_model_list_item_status")]
    result_block = source[source.index("def _apply_model_connectivity_result"):]
    result_block = result_block[:result_block.index("\n    def _apply_assigned_model_test_result")]

    assert "assigned_target_label = (" in test_block
    assert 'f"正在测试{assigned_target_label}..."' in test_block
    assert 'f"✓ {assigned_target_label}测试通过"' in result_block


def test_assigned_model_test_result_is_ignored_after_model_switch():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    default_button = _FakeWidget()
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://new.example.test/v1",
        "model": "new-model",
    }
    gui._assigned_model_test_buttons = {"default": default_button}
    gui._assigned_model_test_icons = {
        "pending": "yellow-light",
        "success": "green-light",
        "error": "red-light",
    }
    gui._assigned_model_test_states = {"default": "pending"}
    gui._assigned_model_test_tokens = {"default": 5}

    gui._apply_assigned_model_test_result({
        "assigned_role": "default",
        "assigned_test_token": 4,
        "assigned_model_ref": {
            "api_provider": "qwen",
            "base_url": "https://old.example.test/v1",
            "model": "old-model",
        },
    }, {"status": "success"})

    assert gui._assigned_model_test_states["default"] == "pending"
    assert default_button.configs == []


def test_assigned_model_test_state_restores_when_returning_to_tested_model():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    default_button = _FakeWidget()
    tested_model = {
        "api_provider": "qwen",
        "base_url": "https://tested.example.test/v1",
        "model": "tested-model",
    }
    gui.api_config = dict(tested_model)
    gui._assigned_model_test_buttons = {"default": default_button}
    gui._assigned_model_test_icons = {
        "pending": "yellow-light",
        "success": "green-light",
        "error": "red-light",
    }
    gui._assigned_model_test_states = {"default": "pending", "education": "pending"}
    gui._assigned_model_test_tokens = {"default": 3, "education": 0}
    gui._assigned_model_test_refs = {
        "default": {
            "api_provider": "qwen",
            "base_url": "https://other.example.test/v1",
            "model": "other-model",
        },
        "education": dict(tested_model),
    }
    gui._assigned_model_test_results = {
        gui._model_ref_key(tested_model): "success",
    }

    gui._reset_assigned_model_test_states()

    assert gui._assigned_model_test_states["default"] == "success"
    assert default_button.configs[-1] == {"image": "green-light"}


def test_traffic_light_icons_are_registered_without_pulse_check():
    assert "traffic_light_pending" in icons.ICON_REGISTRY
    assert "traffic_light_success" in icons.ICON_REGISTRY
    assert "traffic_light_error" in icons.ICON_REGISTRY
    assert "pulse_check" not in icons.ICON_REGISTRY


def test_result_action_icons_are_registered_visible_and_distinct():
    assert "task_list" in icons.ICON_REGISTRY
    assert "candidate_review" in icons.ICON_REGISTRY
    assert "health_shield" in icons.ICON_REGISTRY
    assert "ai_spark" in icons.ICON_REGISTRY
    task_list = icons.ICON_REGISTRY["task_list"](48, "#2563EB", (0, 0, 0, 0), 4)
    candidate_review = icons.ICON_REGISTRY["candidate_review"](
        48, "#2563EB", (0, 0, 0, 0), 4
    )
    health_shield = icons.ICON_REGISTRY["health_shield"](
        48, "#2563EB", (0, 0, 0, 0), 4
    )
    ai_spark = icons.ICON_REGISTRY["ai_spark"](
        48, "#2563EB", (0, 0, 0, 0), 4
    )

    assert task_list.getbbox() is not None
    assert candidate_review.getbbox() is not None
    assert health_shield.getbbox() is not None
    assert ai_spark.getbbox() is not None
    assert len({
        task_list.tobytes(), candidate_review.tobytes(),
        health_shield.tobytes(), ai_spark.tobytes(),
    }) == 4


def test_icon_cache_uses_four_times_supersampling():
    source = Path("icons.py").read_text(encoding="utf-8")

    assert "supersample = 4" in source
    assert "super_px = size_px * supersample" in source
    assert "super_sw = max(1, sw * supersample)" in source


def test_model_ref_matches_full_connection_identity():
    same_model_one = {
        "api_provider": "qwen",
        "base_url": "https://one.example/v1/",
        "model": "same-model",
    }
    same_model_two = {
        "api_provider": "qwen",
        "base_url": "https://two.example/v1",
        "model": "same-model",
    }

    assert BossFilterGUI._model_ref_matches(same_model_one, {
        "api_provider": "qwen",
        "base_url": "https://one.example/v1",
        "model": "same-model",
    })
    assert not BossFilterGUI._model_ref_matches(same_model_one, same_model_two)


def test_save_api_config_preserves_existing_default_model():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.PROVIDER_DISPLAY = gui_main.PROVIDER_DISPLAY
    gui.DISPLAY_TO_KEY = gui_main.DISPLAY_TO_KEY
    gui.api_provider_var = _FakeVar("qwen")
    gui.api_model_var = _FakeVar("new-model")
    gui.api_key_var = _FakeVar("secret")
    gui.api_base_url_var = _FakeVar("https://two.example/v1")
    gui.llm_read_timeout_var = _FakeVar(60)
    gui._pending_models_to_add = []
    gui.saved_models = [{
        "api_provider": "qwen",
        "base_url": "https://one.example/v1",
        "model": "current-model",
    }]
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://one.example/v1/",
        "model": "current-model",
        "saved_models": gui.saved_models,
    }
    gui.colors = {"success": "green", "danger": "red"}
    gui.api_status_label = _FakeWidget()
    gui._status_clickable_labels = []
    gui.load_saved_models_to_tree = Mock()
    gui._mark_api_config_ui_current = Mock()

    with tempfile.TemporaryDirectory() as tmp_dir:
        config_path = Path(tmp_dir) / "api_config.json"
        with patch.object(gui_main, "get_api_config_path", return_value=config_path), \
             patch.object(gui_main, "save_api_key", return_value=True), \
             patch.object(gui_main.messagebox, "showinfo") as showinfo, \
             patch.object(gui_main.messagebox, "showwarning") as showwarning, \
             patch.object(gui_main.messagebox, "showerror") as showerror:
            gui.save_api_config()

    showwarning.assert_not_called()
    showerror.assert_not_called()
    assert gui.api_config["model"] == "current-model"
    assert gui.api_config["base_url"] == "https://one.example/v1/"
    assert any(m["model"] == "new-model" for m in gui.api_config["saved_models"])
    assert "默认 AI 模型保持不变" in showinfo.call_args.args[1]


def test_save_api_config_stops_when_system_credential_write_fails():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.PROVIDER_DISPLAY = gui_main.PROVIDER_DISPLAY
    gui.DISPLAY_TO_KEY = gui_main.DISPLAY_TO_KEY
    gui.api_provider_var = _FakeVar("qwen")
    gui.api_model_var = _FakeVar("model-x")
    gui.api_key_var = _FakeVar("secret")
    gui.api_base_url_var = _FakeVar("https://example.test/v1")
    gui._pending_models_to_add = []
    gui.colors = {"success": "green", "danger": "red"}
    gui._remember_api_key = Mock()
    gui._update_api_status = Mock()

    with (
        patch.object(gui_main, "save_api_key", return_value=False),
        patch.object(gui_main.messagebox, "showinfo") as showinfo,
        patch.object(gui_main.messagebox, "showerror") as showerror,
    ):
        gui.save_api_config()

    gui._remember_api_key.assert_not_called()
    showinfo.assert_not_called()
    assert "未能写入系统凭据存储" in showerror.call_args.args[1]
    assert "未能写入系统凭据存储" in gui._update_api_status.call_args.kwargs["text"]


def test_save_api_config_sets_default_when_current_model_is_not_saved():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    save_block = source[source.index("def save_api_config"):]
    save_block = save_block[:save_block.index("\n    def fetch_model_list")]

    assert "has_saved_current = any(" in save_block
    assert "should_set_default = not has_saved_current" in save_block
    assert 'default_summary = "本次保存的模型已设为默认 AI 模型" if should_set_default else "默认 AI 模型保持不变"' in save_block


def test_model_discovery_keeps_custom_base_url_explicit_and_scopes_catalog_cache():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    fetch_block = source[source.index("def fetch_model_list"):]
    fetch_block = fetch_block[:fetch_block.index("\n    def _show_api_key_while_pressed")]

    assert 'self.api_base_url_var = tk.StringVar()' in source
    assert 'text=" 自动识别并获取模型"' in source
    assert 'if not base_url and not has_endpoint_discovery(provider):' in fetch_block
    assert '自定义/中转地址只验证用户明确输入的 URL' in fetch_block
    assert 'resolution = discover_api_endpoint(' in fetch_block
    assert 'catalog_key = model_catalog_cache_key(provider, base_url)' in fetch_block


def test_education_captcha_low_confidence_is_not_auto_submitted():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    solve_block = source[source.index("def _attempt_captcha_solve"):]
    solve_block = solve_block[:solve_block.index("\n    def _solve_captcha")]

    assert "CAPTCHA_AUTO_SUBMIT_MIN_CONFIDENCE" in solve_block
    assert "confidence < CAPTCHA_AUTO_SUBMIT_MIN_CONFIDENCE" in solve_block
    assert 'return False, "待人工验证"' in solve_block


def test_education_captcha_retries_three_times_before_manual_fallback():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._education_browser_lock = threading.Lock()
    gui._attempt_captcha_solve = Mock(side_effect=[
        (False, "待人工验证"),
        (False, "识别失败"),
        (False, "待人工验证"),
    ])
    progress = []

    with patch("education_certificate.navigate_to_chsi") as navigate, \
            patch("education_certificate.fill_chsi_query_page") as fill, \
            patch("gui_main.time.sleep"):
        result = gui._fill_and_solve_captcha(
            object(),
            "张三",
            "123456789012345678",
            on_progress=lambda status, detail: progress.append((status, detail)),
            max_attempts=3,
        )

    assert result == (False, "待人工验证")
    assert gui._attempt_captcha_solve.call_count == 3
    assert navigate.call_count == 3
    assert fill.call_count == 3
    assert any("2/3" in status for status, _detail in progress)
    assert any("3/3" in status for status, _detail in progress)


def test_use_selected_model_matches_provider_and_base_url_not_model_name_only():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    use_block = source[source.index("def use_selected_model"):]
    use_block = use_block[:use_block.index("\n    def test_saved_model_connectivity")]

    assert 'selected_base_url = item[\'values\'][3]' in use_block
    assert '"api_provider": provider_key' in use_block
    assert '"base_url": selected_base_url' in use_block
    assert 'if saved.get("model") == model_name:' not in use_block


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

    assert gui._format_candidate_status(candidate) == "发送待核实"


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

    candidate["llm_error"] = "请求超时"
    assert gui._format_candidate_status(candidate) == "未沟通｜待复核"
    assert candidate["_full_status"] == (
        "复核原因：AI 评估失败，需人工判断或重试"
    )


def test_result_status_tooltip_shows_hidden_review_reason():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {
        "geek_id": "review-1",
        "followup_status": "未沟通",
        "llm_error": "请求超时",
    }
    assert gui._format_candidate_status(candidate) == "未沟通｜待复核"

    gui.result_tree = Mock()
    gui.result_tree.identify_row.return_value = "row-1"
    gui.result_tree.identify_column.return_value = "#8"
    gui.result_tree.cget.return_value = (
        "name", "exp", "salary", "skill", "score", "ai", "level", "status"
    )
    gui._item_to_candidate = {"row-1": candidate}
    gui._tooltip = None
    gui._tooltip_item = None
    gui._tooltip_after_id = None
    gui.root = Mock()
    gui.root.winfo_pointerx.return_value = 100
    gui.root.winfo_pointery.return_value = 200
    gui._hide_tooltip = Mock()
    gui._show_tooltip = Mock()

    gui._on_tree_motion(types.SimpleNamespace(x=10, y=10))

    gui.root.after.assert_called_once()
    callback = gui.root.after.call_args.args[1]
    callback()
    gui._show_tooltip.assert_called_once_with(
        "复核原因：AI 评估失败，需人工判断或重试",
        115,
        210,
        ("row-1", "status"),
    )


def test_result_job_status_tooltip_only_shows_when_text_is_clipped():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {
        "_extra_fields": ("本科", "30岁", "正在考虑机会，合适的话可以到岗", "", ""),
    }
    gui.result_tree = Mock()
    gui.result_tree.identify_row.return_value = "row-1"
    gui.result_tree.identify_column.return_value = "#11"
    gui.result_tree.cget.return_value = (
        "name", "exp", "salary", "skills", "score", "ai_eval", "level", "status",
        "education", "age", "job_status",
    )
    gui.result_tree.bbox.return_value = (0, 0, 100, 24)
    gui._item_to_candidate = {"row-1": candidate}
    gui._result_tree_font = Mock()
    gui._tooltip = None
    gui._tooltip_item = None
    gui._tooltip_after_id = None
    gui.root = Mock()
    gui.root.winfo_pointerx.return_value = 100
    gui.root.winfo_pointery.return_value = 200
    gui._hide_tooltip = Mock()
    gui._show_tooltip = Mock()

    gui._result_tree_font.measure.return_value = 120
    gui._on_tree_motion(types.SimpleNamespace(x=10, y=10))
    callback = gui.root.after.call_args.args[1]
    callback()
    gui._show_tooltip.assert_called_once_with(
        "正在考虑机会，合适的话可以到岗", 115, 210, ("row-1", "job_status")
    )

    gui.root.after.reset_mock()
    gui._show_tooltip.reset_mock()
    gui._tooltip = None
    gui._tooltip_item = None
    gui._result_tree_font.measure.return_value = 80
    gui._on_tree_motion(types.SimpleNamespace(x=10, y=10))
    gui.root.after.assert_not_called()
    gui._show_tooltip.assert_not_called()


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
        gui.result_view_var = _FakeVar("推荐候选人")
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


def test_refresh_results_keeps_ai_evaluated_and_failed_candidates_below_pass_score():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        candidates_path.write_text(
            json.dumps([
                {
                    "geek_id": "g1",
                    "name": "已评估候选人",
                    "job_name": "Java 工程师",
                    "match_score": 52,
                    "followup_status": "未沟通",
                    "llm_evaluated": True,
                    "llm_adjustment": -3,
                },
                {
                    "geek_id": "g2",
                    "name": "评估失败候选人",
                    "job_name": "Java 工程师",
                    "match_score": 51,
                    "followup_status": "未沟通",
                    "llm_evaluated": False,
                    "llm_error": "请求超时",
                },
                {
                    "geek_id": "g3",
                    "name": "已淘汰候选人",
                    "job_name": "Java 工程师",
                    "match_score": 90,
                    "followup_status": "未沟通",
                    "llm_evaluated": True,
                    "qualification_status": "rejected",
                },
            ], ensure_ascii=False),
            encoding="utf-8",
        )

        gui = BossFilterGUI.__new__(BossFilterGUI)
        gui.result_tree = _FakeResultTree()
        gui.result_job_var = _FakeVar("全部岗位")
        gui.result_view_var = _FakeVar("待复核")
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

        assert len(gui.result_tree.items) == 2
        values = gui.result_tree.items[0]["values"]
        assert values[4] == 52
        assert values[5] == "-3"
        assert values[6] == "未通过"
        assert values[7] == "未沟通｜待复核"
        first_candidate = gui._item_to_candidate["item-1"]
        assert first_candidate["_full_status"] == (
            "复核原因：评分低于通过线（52 分）"
        )
        failed_values = gui.result_tree.items[1]["values"]
        assert failed_values[4] == 51
        assert failed_values[5] == "—"
        assert failed_values[6] == "未通过"
        assert failed_values[7] == "未沟通｜待复核"


def test_refresh_results_keeps_full_dataset_and_uses_stable_metric_scope():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        candidates_path.write_text(
            json.dumps([
                {
                    "geek_id": "strong",
                    "name": "强推候选人",
                    "job_name": "Java 工程师",
                    "match_score": 80,
                    "greet_sent": True,
                },
                {
                    "geek_id": "pending",
                    "name": "待定候选人",
                    "job_name": "Java 工程师",
                    "match_score": 60,
                },
                {
                    "geek_id": "rejected",
                    "name": "淘汰候选人",
                    "job_name": "Java 工程师",
                    "match_score": 90,
                    "qualification_status": "rejected",
                },
            ], ensure_ascii=False),
            encoding="utf-8",
        )

        gui = BossFilterGUI.__new__(BossFilterGUI)
        gui.result_tree = _FakeResultTree()
        gui.result_job_var = _FakeVar("全部岗位")
        gui.result_view_var = _FakeVar("待复核")
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

        assert len(gui.all_candidates) == 3
        assert [candidate["geek_id"] for candidate in gui.result_tree_data] == ["pending"]
        assert gui.result_stats_vars["strong"].get() == "1"
        assert gui.result_stats_vars["pending"].get() == "1"
        assert gui.result_stats_vars["greeted"].get() == "1"


def test_result_tree_item_map_wins_for_duplicate_name_and_score():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    first = {"geek_id": "first", "name": "同名", "match_score": 70}
    second = {"geek_id": "second", "name": "同名", "match_score": 70}
    gui.result_tree = _FakeResultTree()
    gui.result_tree.items = [{"values": ("同名", "", "", "", 70)}]
    gui.result_tree_data = [first, second]
    gui._item_to_candidate = {0: second}

    assert gui._find_candidate_by_tree_item(0) is second


def test_candidate_detail_opens_review_workbench_with_mapped_candidate():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {"geek_id": "mapped", "name": "同名", "match_score": 70}
    gui._find_candidate_by_tree_item = Mock(return_value=candidate)
    gui._open_candidate_review_workbench = Mock()

    gui._show_candidate_detail("row-2")

    gui._open_candidate_review_workbench.assert_called_once_with(candidate)


def test_result_review_button_follows_candidate_selection():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_review_button = Mock()
    gui.result_tree = Mock()

    gui.result_tree.selection.return_value = ("row-1",)
    gui._update_result_review_button_state()
    gui.result_review_button.configure.assert_called_with(state="normal")

    gui.result_tree.selection.return_value = ()
    gui.result_tree.get_children.return_value = ("row-1",)
    gui._update_result_review_button_state()
    gui.result_review_button.configure.assert_called_with(state="normal")

    gui.result_tree.get_children.return_value = ()
    gui._update_result_review_button_state()
    gui.result_review_button.configure.assert_called_with(state="disabled")


def test_visible_review_action_opens_first_selected_candidate():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_tree = Mock()
    gui.result_tree.selection.return_value = ("row-2", "row-3")
    gui._show_candidate_detail = Mock()

    gui._open_selected_candidate_review()

    gui._show_candidate_detail.assert_called_once_with("row-2")


def test_visible_review_action_starts_from_first_row_without_selection():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_tree = Mock()
    gui.result_tree.selection.return_value = ()
    gui.result_tree.get_children.return_value = ("row-1", "row-2")
    gui._show_candidate_detail = Mock()

    gui._open_selected_candidate_review()

    gui.result_tree.selection_set.assert_called_once_with("row-1")
    gui.result_tree.focus.assert_called_once_with("row-1")
    gui.result_tree.see.assert_called_once_with("row-1")
    gui._show_candidate_detail.assert_called_once_with("row-1")


def test_candidate_decision_summary_leads_with_action_and_review_evidence():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    summary = gui._format_candidate_decision_summary({
        "name": "候选人",
        "match_score": 60,
        "manual_review_required": True,
        "qualification_status": "manual_review",
        "qualification_reasons": ["工作年限证据不足"],
        "keyword_evidence": [{"name": "Python", "evidence": "项目使用 Python"}],
        "score_breakdown": {
            "base": 25,
            "skill": 25,
            "experience": 5,
            "education": 5,
            "preferred": 0,
        },
    })

    assert summary.startswith("下一步\n")
    assert "工作年限证据不足" in summary
    assert "评分处于待定区间（60 分）" in summary
    assert "关键匹配" in summary
    assert "Python：项目使用 Python" in summary


def test_candidate_review_action_keeps_current_after_refresh_even_outside_result_scope():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    current = {"geek_id": "current", "job_name": "Java"}
    refreshed_current = {
        "geek_id": "current",
        "job_name": "Java",
        "contact_approved_at": "20260729_100000",
    }
    next_candidate = {"geek_id": "next", "job_name": "Java"}
    gui._candidate_review_index = 0
    gui._candidate_review_candidates = [current, next_candidate]
    gui.result_tree_data = [next_candidate]
    gui.refresh_results = Mock()
    gui._render_candidate_review_workbench = Mock()

    with patch(
        "gui_main.load_candidates_all",
        return_value=[refreshed_current, next_candidate],
    ):
        gui._candidate_review_action_saved(("current", "Java"))

    gui.refresh_results.assert_called_once_with(force=True)
    assert gui._candidate_review_candidates == [refreshed_current, next_candidate]
    assert gui._candidate_review_index == 0
    gui._render_candidate_review_workbench.assert_called_once()


def test_clear_manual_review_is_scoped_to_candidate_job():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        candidates_path.write_text(json.dumps([
            {
                "geek_id": "same-geek",
                "job_name": "Java 工程师",
                "match_score": 75,
                "manual_review_required": True,
                "qualification_status": "manual_review",
            },
            {
                "geek_id": "same-geek",
                "job_name": "Python 工程师",
                "match_score": 75,
                "manual_review_required": True,
                "qualification_status": "manual_review",
            },
        ], ensure_ascii=False), encoding="utf-8")
        gui = BossFilterGUI.__new__(BossFilterGUI)

        with patch("gui_main.CANDIDATES_PATH", candidates_path):
            updated = gui._clear_manual_review("same-geek", "Java 工程师")

        saved = json.loads(candidates_path.read_text(encoding="utf-8"))
        assert updated == 1
        assert saved[0]["manual_review_required"] is False
        assert saved[0]["qualification_status"] == "qualified"
        assert saved[1]["manual_review_required"] is True
        assert saved[1]["qualification_status"] == "manual_review"


def test_confirm_manual_review_once_approves_pending_contact_without_changing_score():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._clear_manual_review = Mock(return_value=1)
    gui._regenerate_excel = Mock()
    gui.refresh_home_stats = Mock()
    gui.refresh_stats = Mock()
    gui.refresh_results = Mock()
    gui._status_flash = Mock()
    on_saved = Mock()
    candidate = {
        "geek_id": "pending",
        "name": "待定候选人",
        "job_name": "Java 工程师",
        "match_score": 60,
        "manual_review_required": True,
        "qualification_status": "manual_review",
        "qualification_reasons": ["学历形式待确认"],
    }

    with patch("gui_main.messagebox.askyesno", return_value=True):
        gui._confirm_manual_review(
            None,
            candidate=candidate,
            parent=Mock(),
            on_saved=on_saved,
        )

    assert candidate["match_score"] == 60
    assert candidate["manual_review_required"] is False
    assert candidate["qualification_status"] == "qualified"
    assert candidate["contact_approval_reason"] == "人工确认复核通过并可联系"
    assert candidate["contact_approved_at"]
    _args, kwargs = gui._clear_manual_review.call_args
    assert kwargs["contact_approval_reason"] == "人工确认复核通过并可联系"
    assert kwargs["timestamp"] == candidate["contact_approved_at"]
    assert gui._format_candidate_status(candidate) == "未沟通｜人工确认"
    on_saved.assert_called_once_with()


def test_clear_manual_review_persists_contact_approval_for_only_matching_job():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        candidates_path.write_text(json.dumps([
            {
                "geek_id": "same-geek",
                "job_name": "Java 工程师",
                "match_score": 60,
                "manual_review_required": True,
                "qualification_status": "manual_review",
            },
            {
                "geek_id": "same-geek",
                "job_name": "Python 工程师",
                "match_score": 60,
                "manual_review_required": True,
                "qualification_status": "manual_review",
            },
        ], ensure_ascii=False), encoding="utf-8")
        gui = BossFilterGUI.__new__(BossFilterGUI)

        with patch("gui_main.CANDIDATES_PATH", candidates_path):
            updated = gui._clear_manual_review(
                "same-geek",
                "Java 工程师",
                contact_approval_reason="人工确认复核通过并可联系",
                timestamp="20260729_100000",
            )

        saved = json.loads(candidates_path.read_text(encoding="utf-8"))
        assert updated == 1
        assert saved[0]["match_score"] == 60
        assert saved[0]["manual_review_required"] is False
        assert saved[0]["qualification_status"] == "qualified"
        assert saved[0]["contact_approved_at"] == "20260729_100000"
        assert saved[0]["contact_approval_reason"] == "人工确认复核通过并可联系"
        assert saved[1]["manual_review_required"] is True
        assert saved[1]["qualification_status"] == "manual_review"
        assert "contact_approved_at" not in saved[1]


def test_clear_manual_review_rejects_missing_candidate_id():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        original = [{
            "geek_id": "",
            "job_name": "Java 工程师",
            "manual_review_required": True,
            "qualification_status": "manual_review",
        }]
        candidates_path.write_text(
            json.dumps(original, ensure_ascii=False), encoding="utf-8"
        )
        gui = BossFilterGUI.__new__(BossFilterGUI)

        with patch("gui_main.CANDIDATES_PATH", candidates_path):
            updated = gui._clear_manual_review("", "Java 工程师")

        assert updated == 0
        assert json.loads(candidates_path.read_text(encoding="utf-8")) == original


def test_update_candidate_followup_persists_due_date_for_only_matching_job():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        candidates_path.write_text(json.dumps([
            {
                "geek_id": "same-geek",
                "job_name": "Java 工程师",
                "match_score": 80,
                "followup_status": "已打招呼",
            },
            {
                "geek_id": "same-geek",
                "job_name": "Python 工程师",
                "match_score": 80,
                "followup_status": "已打招呼",
            },
        ], ensure_ascii=False), encoding="utf-8")
        gui = BossFilterGUI.__new__(BossFilterGUI)

        with patch("gui_main.CANDIDATES_PATH", candidates_path):
            updated = gui._update_candidate_followup(
                "same-geek",
                "Java 工程师",
                "待约面",
                "周三确认",
                "20260723_090000",
                "20260718_100000",
            )

        saved = json.loads(candidates_path.read_text(encoding="utf-8"))
        assert updated is True
        assert saved[0]["followup_status"] == "待约面"
        assert saved[0]["greet_sent"] is True
        assert saved[0]["greet_method"] == "manual_status"
        assert saved[0]["next_followup_at"] == "20260723_090000"
        assert saved[0]["followup_updated_at"] == "20260718_100000"
        assert saved[1]["followup_status"] == "已打招呼"
        assert "next_followup_at" not in saved[1]


def test_manual_contact_approval_is_persisted_for_only_matching_job():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        candidates_path.write_text(json.dumps([
            {
                "geek_id": "same-geek",
                "job_name": "Java 工程师",
                "match_score": 64,
            },
            {
                "geek_id": "same-geek",
                "job_name": "Python 工程师",
                "match_score": 64,
            },
        ], ensure_ascii=False), encoding="utf-8")
        gui = BossFilterGUI.__new__(BossFilterGUI)

        with patch("gui_main.CANDIDATES_PATH", candidates_path):
            updated = gui._update_candidate_contact_approval(
                "same-geek",
                "Java 工程师",
                "人工确认待定候选人可联系",
                "20260728_100000",
            )

        saved = json.loads(candidates_path.read_text(encoding="utf-8"))
        assert updated is True
        assert saved[0]["contact_approved_at"] == "20260728_100000"
        assert saved[0]["contact_approval_reason"] == "人工确认待定候选人可联系"
        assert "contact_approved_at" not in saved[1]


def test_manual_contact_confirmation_persists_before_queue_without_changing_score():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._update_candidate_contact_approval = Mock(return_value=True)
    gui._add_candidates_to_greet_queue = Mock(return_value=1)
    gui.refresh_results = Mock()
    parent = Mock()
    candidate = {
        "geek_id": "g1",
        "name": "候选人",
        "job_name": "Java 工程师",
        "match_score": 64,
    }

    with patch("gui_main.messagebox.askyesno", return_value=True):
        added = gui._approve_candidate_contact_and_queue(
            candidate,
            parent=parent,
        )

    assert added == 1
    assert candidate["match_score"] == 64
    assert candidate["contact_approval_reason"] == "人工确认待定候选人可联系"
    gui._update_candidate_contact_approval.assert_called_once()
    gui._add_candidates_to_greet_queue.assert_called_once_with(
        [candidate],
        parent=parent,
    )
    gui.refresh_results.assert_called_once_with(force=True)


def test_negative_feedback_clears_prior_contact_approval():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        candidates_path.write_text(json.dumps([{
            "geek_id": "g1",
            "job_name": "Java 工程师",
            "match_score": 64,
            "contact_approved_at": "20260728_100000",
            "contact_approval_reason": "人工确认待定候选人可联系",
        }], ensure_ascii=False), encoding="utf-8")
        gui = BossFilterGUI.__new__(BossFilterGUI)

        with patch("gui_main.CANDIDATES_PATH", candidates_path):
            updated = gui._update_candidate_feedback(
                "g1", "Java 工程师", "放弃", [], ""
            )

        saved = json.loads(candidates_path.read_text(encoding="utf-8"))
        assert updated is True
        assert saved[0]["feedback_status"] == "放弃"
        assert "contact_approved_at" not in saved[0]
        assert "contact_approval_reason" not in saved[0]


def test_blacklist_ends_active_followup_and_clears_reminder():
    with tempfile.TemporaryDirectory() as tmp_dir:
        candidates_path = Path(tmp_dir) / "candidates.json"
        candidates_path.write_text(json.dumps([{
            "geek_id": "g1",
            "job_name": "Java 工程师",
            "match_score": 80,
            "greet_sent": True,
            "followup_status": "待约面",
            "next_followup_at": "20260730_090000",
        }], ensure_ascii=False), encoding="utf-8")
        gui = BossFilterGUI.__new__(BossFilterGUI)

        with patch("gui_main.CANDIDATES_PATH", candidates_path):
            updated = gui._update_candidate_blacklist(
                "g1", "不再考虑", "20260728_100000"
            )

        saved = json.loads(candidates_path.read_text(encoding="utf-8"))
        assert updated == 1
        assert saved[0]["blacklisted"] is True
        assert saved[0]["followup_status"] == "不合适"
        assert saved[0]["followup_updated_at"] == "20260728_100000"
        assert "next_followup_at" not in saved[0]


def test_suitable_pending_status_is_resolved_without_changing_recommendation():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {
        "geek_id": "pending",
        "match_score": 64,
        "feedback_status": "合适",
    }

    status = gui._format_candidate_status(candidate)

    assert status == "未沟通｜人工确认合适"
    assert "待复核" not in status


def test_candidate_review_workbench_exposes_flat_switch_and_direct_actions():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _open_candidate_review_workbench"):]
    block = block[:block.index("\n    def _show_candidate_detail")]

    assert 'win.title("候选人查看与复核")' in block
    assert "ttk.Notebook" not in block
    assert "CandidateReview.TNotebook" not in block
    assert "self.candidate_review_view_buttons" in block
    assert "self.candidate_review_view_indicators" in block
    assert "self._show_candidate_review_view('summary')" in block
    assert "'<Control-Tab>'" in block
    assert 'text="上一位"' in block
    assert 'text="下一位"' in block
    assert "header.grid_columnconfigure(0, weight=1)" in block
    assert "title_area.grid(row=0, column=0, sticky='ew')" in block
    assert "nav.grid(row=0, column=1, sticky='e')" in block
    assert 'text="上一位", width=8' in block
    assert 'text="下一位", width=8' in block
    assert "width=9," in block
    assert '("summary", "决策摘要")' in block
    assert '("detail", "完整资料")' in block
    assert "preferred_width = max(700, int(root_width * 0.62))" in block
    assert "int(1040 * max(1.0, scale))" in block
    assert "height = min(root_height, int(area_height * 0.9))" in block
    assert "_place_window_centered(win, width, height, parent=self.root)" in block
    assert 'win.geometry(f"{width}x{height}+{x}+{y}")' not in block
    assert '"确认通过"' in block
    assert '"加入联系清单"' in block
    assert '"更新跟进"' in block
    assert '"标记反馈"' in block
    assert '"导入简历"' in block
    assert 'text="建议下一步"' in block
    assert 'text="其他操作"' in block
    assert "int(9 * self.font_scale)" not in block
    assert block.count("int(10 * self.font_scale)") >= 4
    assert "orient='horizontal'" in block
    assert "style='Accent.TButton'" not in block


def test_candidate_review_flat_switch_uses_theme_colors_and_raises_selected_panel():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _show_candidate_review_view"):]
    block = block[:block.index("\n    def _toggle_candidate_review_view")]

    assert "frames[view_name].tkraise()" in block
    assert "self.colors['banner_info_bg'] if selected" in block
    assert "self.colors['primary'] if selected" in block
    assert "self.colors['text_secondary']" in block
    assert "self.colors['bg_hover']" in block


def test_candidate_review_shortcut_switch_does_not_force_focus_repaint():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _toggle_candidate_review_view"):]
    block = block[:block.index("\n    @staticmethod")]

    assert "self._show_candidate_review_view(target)" in block
    assert ".focus_set()" not in block


def test_candidate_review_actions_keep_stable_two_row_layout_and_button_style():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    workbench = source[source.index("def _open_candidate_review_workbench"):]
    workbench = workbench[:workbench.index("\n    def _render_candidate_review_workbench")]
    render_actions = source[source.index("def _render_candidate_review_actions"):]
    render_actions = render_actions[:render_actions.index("\n    def _navigate_candidate_review")]

    assert "primary_section.grid(row=0, column=0, sticky='w')" in workbench
    assert "secondary_section.grid(row=2, column=0, sticky='w')" in workbench
    assert "orient='horizontal'" in workbench
    assert "actions.bind('<Configure>'" not in workbench
    assert "def _layout_candidate_review_actions" not in source
    assert "style='Accent.TButton'" not in render_actions


def test_candidate_context_menu_uses_review_workbench_wording():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _build_candidate_context_menu"):]
    block = block[:block.index("\n    def _find_candidate_by_tree_item")]

    assert 'label=" 查看与复核"' in block
    assert "self.icons.button('candidate_review', self.colors['primary'])" in block
    assert "self.icons.button('ai_spark', self.colors['primary'])" in block
    assert 'label=" 查看详情"' not in block
    assert 'label=" 加入联系清单"' in block
    assert 'label=" 确认并加入联系清单"' in block
    assert 'label=" 打招呼"' not in block
    assert "candidate_greet_skip_reason(candidate)" in block
    assert "not _candidate_has_ai_eval(candidate)" in block
    assert "candidate.get('qualification_status') == 'manual_review'" in block
    assert 'label=" 导出选中"' not in block


def test_candidate_detail_explains_ai_failure_and_retained_rule_score():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    detail = gui._format_candidate_detail({
        "name": "候选人",
        "job_name": "Java 工程师",
        "geek_id": "g-failed",
        "match_score": 60,
        "summary": "本科，3年 Java",
        "llm_evaluated": False,
        "llm_error": "请求超时\n请稍后重试",
    })

    assert "【AI 一次评估】" in detail
    assert "状态：评估失败，当前分数仍为规则评分" in detail
    assert "失败原因：请求超时 请稍后重试" in detail
    assert "【AI 一次评估】未启用" not in detail


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
    """未进入运行控制页时保留扫描日志，不能因控件尚未创建而丢失。"""
    class FakeRoot:
        def __init__(self):
            self.scheduled = []

        def after(self, delay, callback):
            self.scheduled.append((delay, callback))

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = FakeRoot()
    gui.log_queue = queue.Queue()
    gui.log_queue.put("候选人扫描中")

    gui.update_log()

    assert gui.log_queue.qsize() == 1
    assert gui.root.scheduled == [(100, gui.update_log)]


def test_non_run_page_diagnostics_do_not_feed_run_log():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.log_queue = queue.Queue()

    with patch("gui_main.logger.info") as info:
        gui.append_log("[联系候选人] 已恢复 3 个未完成任务")

    assert gui.log_queue.empty()
    info.assert_called_once_with(
        "[GUI] %s", "[联系候选人] 已恢复 3 个未完成任务"
    )


def test_scan_messages_feed_run_log_explicitly():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.log_queue = queue.Queue()

    with patch.object(gui, "_append_run_log_file") as append_file:
        gui.append_run_log("开始扫描候选人")

    assert gui.log_queue.get_nowait() == "开始扫描候选人"
    append_file.assert_called_once_with("开始扫描候选人")


def test_operation_log_writes_both_files_without_feeding_scan_ui():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.log_queue = queue.Queue()

    with patch.object(gui, "_append_runtime_log_file") as append_runtime, \
            patch.object(gui, "_append_run_log_file") as append_run:
        gui.append_operation_log("[联系候选人] Candidate A 发送成功")

    assert gui.log_queue.empty()
    append_runtime.assert_called_once_with(
        "app",
        "[联系候选人] Candidate A 发送成功",
        add_timestamp=True,
    )
    append_run.assert_called_once_with("[联系候选人] Candidate A 发送成功")


def test_operation_log_can_surface_critical_event_to_scan_ui():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.log_queue = queue.Queue()
    message = "[访问保护] 联系候选人返回 HTTP 429，已停止后续发送"

    with patch.object(gui, "_append_runtime_log_file") as append_runtime, \
            patch.object(gui, "_append_run_log_file") as append_run:
        gui.append_operation_log(message, show_in_run_ui=True)

    assert gui.log_queue.get_nowait() == message
    append_runtime.assert_called_once_with("app", message, add_timestamp=True)
    append_run.assert_called_once_with(message)


def test_boss_access_protection_message_reaches_ui_and_file_logs():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.log_queue = queue.Queue()
    message = (
        "[访问保护] BOSS 返回 HTTP 429（API 直调第 1 页）：请求过于频繁。"
        "已停止后续 BOSS 访问"
    )

    with patch.object(gui, "_append_run_log_file") as append_file:
        gui.append_run_log(message)

    assert gui.log_queue.get_nowait() == message
    append_file.assert_called_once_with(message)


def test_run_log_appends_to_daily_file():
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"

        with patch("gui_main.RUN_LOG_DIR", log_dir), \
             patch("gui_main.datetime") as fake_datetime:
            fake_datetime.now.return_value.strftime.side_effect = lambda fmt: {
                "%Y%m%d": "20260723",
            }[fmt]
            gui = BossFilterGUI.__new__(BossFilterGUI)
            gui._append_run_log_file("开始扫描候选人")

        assert (log_dir / "run-20260723.log").read_text(encoding="utf-8") == "开始扫描候选人\n"


def test_runtime_logs_are_separated_and_sensitive_values_are_redacted():
    with tempfile.TemporaryDirectory() as tmp_dir, \
            patch("gui_main.RUN_LOG_DIR", Path(tmp_dir)):
        gui = BossFilterGUI.__new__(BossFilterGUI)
        gui.log_queue = queue.Queue()

        gui.append_run_log(
            'BOSS {"securityId":"top-secret","access_token":"token-value",'
            '"expectId":"expect-value"}'
        )
        gui.append_log("请求头 Authorization: Bearer bearer-value")

        run_log = next(Path(tmp_dir).glob("run-*.log"))
        app_log = next(Path(tmp_dir).glob("app-*.log"))
        run_text = run_log.read_text(encoding="utf-8")
        app_text = app_log.read_text(encoding="utf-8")

    assert "top-secret" not in run_text
    assert "token-value" not in run_text
    assert "expect-value" not in run_text
    assert "bearer-value" not in app_text
    assert '"securityId":"***"' in run_text
    assert '"expectId":"***"' in run_text
    assert "Authorization: ***" in app_text
    assert gui.log_queue.get_nowait() == (
        'BOSS {"securityId":"***","access_token":"***","expectId":"***"}'
    )


def test_runtime_log_cleanup_only_removes_expired_owned_logs():
    with tempfile.TemporaryDirectory() as tmp_dir, \
            patch("gui_main.RUN_LOG_DIR", Path(tmp_dir)):
        root = Path(tmp_dir)
        expired_run = root / "run-20260101.log"
        expired_app = root / "app-20260101.log"
        unrelated = root / "notes-20260101.log"
        current_run = root / "run-current.log"
        for path in (expired_run, expired_app, unrelated, current_run):
            path.write_text(path.name, encoding="utf-8")
        old_time = time.time() - (gui_main.RUNTIME_LOG_RETENTION_DAYS + 2) * 86400
        for path in (expired_run, expired_app, unrelated):
            os.utime(path, (old_time, old_time))

        gui = BossFilterGUI.__new__(BossFilterGUI)
        gui._cleanup_runtime_logs_once()

        assert not expired_run.exists()
        assert not expired_app.exists()
        assert unrelated.exists()
        assert current_run.exists()


def test_run_settings_snapshot_writes_only_to_file_log():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.log_queue = queue.Queue()
    file_logs = []
    gui._append_run_log_file = file_logs.append

    gui._append_run_settings_snapshot([
        ("滚动轮次", 30),
        ("扫描增强", "自动补全候选人详情"),
    ])

    assert gui.log_queue.empty()
    assert file_logs == [
        "本轮参数设置：",
        "  滚动轮次：30",
        "  扫描增强：自动补全候选人详情",
    ]


def test_scan_ui_log_sink_is_limited_to_run_control():
    tree = ast.parse(Path("gui_main.py").read_text(encoding="utf-8"))
    gui_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BossFilterGUI"
    )
    actual_methods = {
        method.name
        for method in gui_class.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append_run_log"
            for node in ast.walk(method)
        )
    }

    assert actual_methods == {
        "_auto_check_selectors",
        "check_browser_connection",
        "start_run",
        "stop_run",
        "run_worker",
    }


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
    stats_block = stats_block[:stats_block.index("\n\n        card_gap")]

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

    assert "derive_candidate_decision(c).screening_result" in detail_block
    assert "{'强烈推荐', '推荐', '待定'}" in detail_block
    assert "c.get('greet_sent', False)" in detail_block


def test_result_page_has_greet_queue_entry():
    """筛选结果页提供显性的候选人联系入口。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    result_block = source[source.index("def create_result_page"):]
    result_block = result_block[:result_block.index("\n    def create_education_page")]

    assert 'text=" 联系候选人"' in result_block
    assert "command=self._show_greet_queue_dialog" in result_block
    assert result_block.index('text=" 联系候选人"') < result_block.index('label=" 导出 Excel"')


def test_result_page_hides_technical_json_button():
    """筛选结果页不暴露面向技术排障的 JSON 文件入口。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    result_block = source[source.index("def create_result_page"):]
    result_block = result_block[:result_block.index("\n    def create_education_page")]

    assert 'text=" 打开 JSON"' not in result_block
    assert "command=self.open_json" not in result_block


def test_batch_greet_context_menu_adds_to_queue_instead_of_direct_send():
    """多选右键只加入队列，不再直接启动批量发送黑盒流程。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")

    assert 'label=" 加入联系清单"' in source
    assert 'menu.add_command(label=" 批量打招呼"' not in source
    assert "_collect_selected_candidates_for_queue" in source
    assert "_add_candidates_to_greet_queue" in source


def test_greet_queue_add_filters_before_enqueue():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    add_block = source[source.index("def _add_candidates_to_greet_queue"):]
    add_block = add_block[:add_block.index("\n    @staticmethod\n    def _format_greet_queue_skip_summary")]

    assert "self._greet_queue_skip_reason(candidate)" in add_block
    assert 'skip_reason = "已在队列"' in add_block
    assert "self._build_greet_queue_item(candidate" in add_block
    assert "没有可加入联系清单的候选人" in add_block
    assert "self._show_text_dialog(" in add_block
    assert "messagebox.showinfo" not in add_block


def test_greet_queue_item_builds_only_sendable_pending_items():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.greet_queue_items = []

    direct = gui._build_greet_queue_item({
        "geek_id": "g1",
        "job_name": "Java",
        "greet_context": {"chat_start": {"jid": "j1", "securityId": "s1"}},
    })

    assert direct["status"] == "待发送"
    assert gui._greet_queue_readiness_label(direct["candidate"]) == "已就绪"
    assert direct["message"] == ""

    page_checked = {"job_name": "中高级 AI 工程师"}
    assert gui._greet_queue_readiness_label(page_checked) == "发送时检查"
    tooltip = gui._greet_queue_readiness_tooltip(page_checked)
    assert "发送时会检查" in tooltip
    assert "中高级 AI 工程师" in tooltip


def test_text_dialog_keeps_scrollbar_buttons_and_horizontal_inset_visible():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _show_text_dialog"):]
    block = block[:block.index("\n    def refresh_stats")]
    add_block = source[source.index("def _add_candidates_to_greet_queue"):]
    add_block = add_block[:add_block.index("\n    @staticmethod\n    def _format_greet_queue_skip_summary")]

    assert 'body.grid(row=0, column=0, sticky="nsew")' in block
    assert 'scroll.grid(row=0, column=1, sticky="ns")' in block
    assert 'btn_row.grid(row=1, column=0, sticky="ew")' in block
    assert 'text=button_text' in block
    assert add_block.count('button_text="确定"') == 2
    assert add_block.count('button_align="center"') == 2
    assert 'horizontal_padding if button_align == "center" else 0' not in block
    assert (
        'padding=(\n'
        '                horizontal_padding,\n'
        '                0,\n'
        '                horizontal_padding,'
    ) in block
    assert 'if button_align == "center":' in block
    assert "button.pack()" in block


def test_contact_queue_readiness_and_result_columns_have_explanatory_tooltips():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    dialog_block = source[source.index("def _show_greet_queue_dialog"):]
    dialog_block = dialog_block[:dialog_block.index("\n    def _on_greet_queue_group_selected")]
    tooltip_block = source[source.index("def _on_greet_queue_motion"):]
    tooltip_block = tooltip_block[:tooltip_block.index("\n    def _close_greet_queue_window")]

    assert 'tree.bind("<Motion>", self._on_greet_queue_motion)' in dialog_block
    assert 'tree.bind("<Leave>", self._hide_tooltip)' in dialog_block
    assert 'tree.bind("<Button-3>", self._show_greet_queue_context_menu)' in dialog_block
    assert 'column_id not in ("#5", "#7")' in tooltip_block
    assert "self._greet_queue_readiness_tooltip" in tooltip_block
    assert 'queue_item.get(\'message\') or "暂无最近结果"' in tooltip_block


def test_contact_queue_context_menu_preserves_multi_selection_and_sends_it():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    first = {"queue_id": "q1", "status": "待发送"}
    second = {"queue_id": "q2", "status": "待发送"}
    gui.greet_queue_items = [first, second]
    gui.greet_queue_tree = Mock()
    gui.greet_queue_tree.identify_row.return_value = "q1"
    gui.greet_queue_tree.selection.return_value = ("q1", "q2")
    gui._update_greet_queue_action_states = Mock()
    gui._start_greet_queue = Mock()
    gui.greet_queue_window = None
    gui.root = Mock()
    gui.font_scale = 1.25
    gui.icons = Mock()
    gui.icons.button.side_effect = ["review-icon", "send-icon"]
    gui.colors = {"success": "#16A34A", "primary": "#2563EB"}
    event = Mock(y=10, x_root=100, y_root=120)

    with patch("gui_main.tk.Menu") as menu_class:
        menu = menu_class.return_value
        gui._show_greet_queue_context_menu(event)

    gui.greet_queue_tree.selection_set.assert_not_called()
    menu_class.assert_called_once_with(
        gui.root,
        tearoff=0,
        font=(gui_main.FONT_FAMILY, int(11 * gui.font_scale)),
    )
    assert menu.add_command.call_args_list[0].kwargs == {
        "label": " 联系选中候选人（2 人）",
        "image": "send-icon",
        "compound": gui_main.tk.LEFT,
        "command": gui._start_greet_queue,
        "state": "normal",
    }
    menu.tk_popup.assert_called_once_with(100, 120)


def test_contact_queue_single_menu_matches_result_menu_alignment_and_order():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    item = {"queue_id": "q1", "status": "待发送"}
    gui.greet_queue_items = [item]
    gui.greet_queue_tree = Mock()
    gui.greet_queue_tree.identify_row.return_value = "q1"
    gui.greet_queue_tree.selection.return_value = ("q1",)
    gui._update_greet_queue_action_states = Mock()
    gui._start_greet_queue = Mock()
    gui._show_selected_greet_queue_detail = Mock()
    gui.greet_queue_window = Mock()
    gui.root = Mock()
    gui.font_scale = 1.25
    gui.icons = Mock()
    gui.icons.button.side_effect = ["review-icon", "send-icon"]
    gui.colors = {"success": "#16A34A", "primary": "#2563EB"}
    event = Mock(y=10, x_root=100, y_root=120)

    with patch("gui_main.tk.Menu") as menu_class:
        menu = menu_class.return_value
        gui._show_greet_queue_context_menu(event)

    assert menu.add_command.call_args_list[0].kwargs == {
        "label": " 查看与复核",
        "image": "review-icon",
        "compound": gui_main.tk.LEFT,
        "command": gui._show_selected_greet_queue_detail,
    }
    assert menu.add_command.call_args_list[1].kwargs["label"] == " 联系此候选人"
    assert menu.add_command.call_args_list[1].kwargs["image"] == "send-icon"
    menu.add_separator.assert_not_called()


def test_contact_queue_ctrl_a_selects_all_visible_candidates():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.greet_queue_tree = Mock()
    gui.greet_queue_tree.winfo_exists.return_value = True
    gui.greet_queue_tree.get_children.return_value = ("q1", "q2", "q3")
    gui._update_greet_queue_action_states = Mock()

    result = gui._select_all_greet_queue_rows()

    assert result == "break"
    gui.greet_queue_tree.selection_set.assert_called_once_with(("q1", "q2", "q3"))
    gui.greet_queue_tree.focus.assert_called_once_with("q1")
    gui.greet_queue_tree.see.assert_called_once_with("q1")
    gui._update_greet_queue_action_states.assert_called_once_with()


def test_contact_queue_binds_ctrl_a_for_visible_rows():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    dialog_block = source[source.index("def _show_greet_queue_dialog"):]
    dialog_block = dialog_block[:dialog_block.index("\n    def _on_greet_queue_group_selected")]

    assert 'tree.bind("<Control-a>", self._select_all_greet_queue_rows' in dialog_block
    assert 'tree.bind("<Control-A>", self._select_all_greet_queue_rows' in dialog_block


def test_greet_queue_skip_reason_filters_non_sendable_candidates():
    assert BossFilterGUI._greet_queue_skip_reason({}) == "缺少候选人标识"
    assert BossFilterGUI._greet_queue_skip_reason({"geek_id": "g1", "blacklisted": True}) == "已加入黑名单"
    assert BossFilterGUI._greet_queue_skip_reason({"geek_id": "g1", "greet_sent": True}) == "已打招呼"
    assert BossFilterGUI._greet_queue_skip_reason({
        "geek_id": "g1",
        "greet_confirmation_pending": True,
    }) == "发送结果待核实"
    assert BossFilterGUI._greet_queue_skip_reason({
        "geek_id": "g1",
        "manual_review_required": True,
    }) == "硬性条件需要人工确认"
    assert BossFilterGUI._greet_queue_skip_reason({"geek_id": "g1"}) == "评分未达到推荐标准"
    assert BossFilterGUI._greet_queue_skip_reason({
        "geek_id": "g1", "match_score": 70,
    }) == ""


def test_contact_queue_revalidates_latest_candidate_state_before_sending():
    assert BossFilterGUI._revalidate_greet_queue_candidate({
        "geek_id": "ready", "match_score": 70,
    }) == ("待发送", "")
    assert BossFilterGUI._revalidate_greet_queue_candidate({
        "geek_id": "blocked", "match_score": 80, "blacklisted": True,
    }) == ("已跳过", "已加入黑名单")
    assert BossFilterGUI._revalidate_greet_queue_candidate({
        "geek_id": "review", "match_score": 80, "manual_review_required": True,
    }) == ("已跳过", "硬性条件需要人工确认")
    assert BossFilterGUI._revalidate_greet_queue_candidate({
        "geek_id": "sent", "match_score": 80, "greet_sent": True,
    }) == ("已发送", "本地已标记为已沟通")
    assert BossFilterGUI._revalidate_greet_queue_candidate({
        "geek_id": "pending",
        "match_score": 80,
        "greet_confirmation_pending": True,
        "greet_confirmation_reason": "button unchanged",
    }) == ("待核实", "button unchanged")


def test_restored_contact_list_reflects_latest_blocked_state_immediately():
    candidate = {
        "geek_id": "blocked",
        "job_name": "Java Engineer",
        "match_score": 80,
        "blacklisted": True,
    }
    item = {
        "queue_id": "q1",
        "key": ("blocked", "JavaEngineer"),
        "candidate": candidate,
        "status": "发送失败",
        "message": "old failure",
    }
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._greet_queue_loaded = False
    gui.greet_queue_items = []
    gui.log_queue = queue.Queue()
    gui._persist_greet_queue = Mock()

    with patch("gui_main.load_candidates_all", return_value=[candidate]), patch(
        "gui_main.load_contact_queue", return_value=[item]
    ), patch("gui_main.logger.info") as info:
        gui._ensure_greet_queue_loaded()

    assert item["status"] == "已跳过"
    assert item["message"] == "已加入黑名单"
    assert gui.log_queue.empty()
    assert any("已恢复 1 个未完成任务" in call.args[1] for call in info.call_args_list)
    gui._persist_greet_queue.assert_called_once()


def test_scan_contact_policy_selects_threshold_then_reuses_queue_validation():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = object()
    gui.append_log = Mock()
    gui._add_candidates_to_greet_queue = Mock(return_value=1)
    candidates = [
        {"geek_id": "strong", "match_score": 80},
        {"geek_id": "normal", "match_score": 70},
        {"geek_id": "pending", "match_score": 60},
    ]

    assert gui._add_scan_candidates_to_contact_queue(
        candidates, "将强烈推荐加入联系清单"
    ) == 1
    selected = gui._add_candidates_to_greet_queue.call_args.args[0]
    assert [candidate["geek_id"] for candidate in selected] == ["strong"]

    gui._add_candidates_to_greet_queue.reset_mock()
    gui._add_scan_candidates_to_contact_queue(
        candidates, "将推荐及以上加入联系清单"
    )
    selected = gui._add_candidates_to_greet_queue.call_args.args[0]
    assert [candidate["geek_id"] for candidate in selected] == ["strong", "normal"]


def test_page_dependent_contact_reports_wrong_job_page_for_confirmation():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.browser_page = object()
    gui._get_greet_queue_page_state = Mock(return_value=(
        True,
        "https://www.zhipin.com/web/frame/recommend/?jobid=1",
        "",
        "",
    ))
    with patch("bossmaster.get_iframe", return_value=None), patch(
        "bossmaster._read_recommend_page_identity",
        return_value={"job_title": "Python Engineer"},
    ), patch("bossmaster._job_titles_match", return_value=False):
        ready, message, actual_job = gui._greet_queue_candidate_page_ready(
            {"job_name": "Java Engineer"}
        )

    assert ready is False
    assert actual_job == "Python Engineer"
    assert "Python Engineer" in message
    assert "Java Engineer" in message


def test_page_dependent_contact_can_continue_after_confirming_job_name_mismatch():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._greet_queue_candidate_page_ready = Mock(
        return_value=(False, "岗位名称不同", "精选")
    )
    gui._confirm_job_name_mismatch = Mock(return_value=True)
    decisions = {}
    candidate = {"job_name": "中高级AI工程师"}

    assert gui._ensure_greet_queue_candidate_page_ready(
        candidate, object(), decisions
    ) == (True, "")
    assert gui._ensure_greet_queue_candidate_page_ready(
        candidate, object(), decisions
    ) == (True, "")

    gui._confirm_job_name_mismatch.assert_called_once()
    assert decisions == {("中高级ai工程师", "精选"): True}


def test_page_dependent_contact_stays_pending_when_job_mismatch_is_not_confirmed():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._greet_queue_candidate_page_ready = Mock(
        return_value=(False, "岗位名称不同", "精选")
    )
    gui._confirm_job_name_mismatch = Mock(return_value=False)

    assert gui._ensure_greet_queue_candidate_page_ready(
        {"job_name": "中高级AI工程师"}, object(), {}
    ) == (False, "岗位名称不同")


def test_job_name_mismatch_confirmation_explains_names_may_differ():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.stop_event = Mock()
    gui.stop_event.is_set.return_value = False
    gui.run_on_ui = lambda callback: callback()

    with patch("gui_main.messagebox.askyesno", return_value=True) as confirm:
        assert gui._confirm_job_name_mismatch(
            "中高级AI工程师",
            "精选",
            context="run",
            parent=object(),
        ) is True

    _title, message = confirm.call_args.args[:2]
    assert "岗位名称不同，但可能指向同一个岗位" in message
    assert "本地岗位配置：中高级AI工程师" in message
    assert "BOSS 当前岗位：精选" in message
    assert confirm.call_args.kwargs["yes_label"] == "确认并继续"


def test_legacy_gui_contact_methods_only_add_to_contact_list():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _greet_single_candidate"):]
    block = block[:block.index("\n    def _update_greet_status")]

    assert block.count("self._add_candidates_to_greet_queue(") == 2
    assert "send_greeting_with_context" not in block
    assert "send_greeting_on_list_page" not in block


def test_greet_queue_skip_summary_is_user_readable():
    summary = BossFilterGUI._format_greet_queue_skip_summary({
        "已打招呼": 2,
        "需人工确认": 3,
    })

    assert "- 已打招呼：2 人" in summary
    assert "- 需人工确认：3 人" in summary


def test_greet_queue_dialog_has_status_groups_and_double_click_detail():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    dialog_block = source[source.index("def _show_greet_queue_dialog"):]
    dialog_block = dialog_block[:dialog_block.index("\n    def _refresh_greet_queue_dialog")]
    refresh_block = source[source.index("def _refresh_greet_queue_dialog"):]
    refresh_block = refresh_block[:refresh_block.index("\n    def _set_greet_queue_item_state")]

    assert "按状态筛选" in dialog_block
    assert "self.greet_queue_group_tree" in dialog_block
    assert "self.greet_queue_detail_title_var" in dialog_block
    assert "GreetQueue.Small.TButton" in dialog_block
    assert "GreetQueue.Primary.TButton" not in dialog_block
    assert 'win.title("联系候选人")' in dialog_block
    assert 'text="开始联系"' in dialog_block
    assert 'style="Workbench.Primary.TButton"' in dialog_block
    assert 'width=14' not in dialog_block
    assert 'text="暂停",' in dialog_block and 'width=8' in dialog_block
    assert 'text="确认已发送"' in dialog_block
    assert 'text="确认未发送"' in dialog_block
    assert 'text="移除选中",' in dialog_block
    assert 'queue_actions.pack(side="right")' in dialog_block
    assert "padding=(int(6 * scale), int(10 * scale))" in dialog_block
    assert "detail_header = ttk.Frame(tree_frame, style='Card.TFrame')" in dialog_block
    assert "detail_title_box" not in dialog_block
    assert "self.greet_queue_detail_summary_var = tk.StringVar" in dialog_block
    assert "foreground=self.colors['text_primary']" in dialog_block
    assert "background=self.colors['bg_card']" in dialog_block
    assert 'padx=(int(10 * scale), int(8 * scale))' in dialog_block
    assert "selected_actions = ttk.Frame(tree_frame" in dialog_block
    assert "selected_actions.grid(row=2, column=0, columnspan=2, sticky=\"ew\")" in dialog_block
    assert '"job": "w"' in dialog_block
    assert '"message": "w"' in dialog_block
    assert 'orient="horizontal"' not in dialog_block
    assert 'xscrollcommand' not in dialog_block
    assert 'group_tree.column("#0", width=int(190 * scale), minwidth=int(150 * scale), anchor="w")' in dialog_block
    assert 'group_tree.column("count", width=0, minwidth=0, stretch=False)' in dialog_block
    assert 'group_order = ("全部", "待核实", "待发送", "发送失败", "发送中", "已发送", "已跳过")' in refresh_block
    assert 'iid="全部"' in refresh_block
    assert 'open=True' in refresh_block
    assert 'if counts.get(group, 0) > 0' in refresh_block
    assert 'for group in visible_groups:' in refresh_block
    assert 'group_tree.insert(\n                    "全部",' in refresh_block
    assert 'item.get(\'message\') or "—"' in refresh_block
    assert 'self._greet_queue_group_hint(selected_status)' in refresh_block
    assert "联系清单为空" in refresh_block
    assert "需处理" in refresh_block
    assert 'tree.bind("<Double-Button-1>", lambda _event: self._show_selected_greet_queue_detail())' in dialog_block


def test_contact_queue_result_tooltip_wraps_and_stays_screen_bounded():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    tooltip_block = source[source.index("def _styled_tooltip"):]
    tooltip_block = tooltip_block[:tooltip_block.index("\n    def _hide_tooltip")]
    queue_motion_block = source[source.index("def _on_greet_queue_motion"):]
    queue_motion_block = queue_motion_block[:queue_motion_block.index("\n    def _show_greet_queue_context_menu")]

    assert "_get_windows_monitor_area(tip, tooltip_parent)" in tooltip_block
    assert "safe_x =" in tooltip_block
    assert "safe_y =" in tooltip_block
    assert "tooltip_wraplength = max(" in queue_motion_block
    assert "wraplength=tooltip_wraplength" in queue_motion_block


def test_contact_queue_selection_text_includes_names_without_unbounded_growth():
    one = [{"status": "待发送", "candidate": {"name": "张三"}}]
    many = [
        {"status": "待发送", "candidate": {"name": name}}
        for name in ("张三", "李四", "王五", "赵六")
    ]

    assert BossFilterGUI._greet_queue_selection_text(one) == "已选：张三 · 待发送"
    assert BossFilterGUI._greet_queue_selection_text(many) == (
        "已选 4 人：张三、李四、王五等 · 待发送"
    )


def test_contact_queue_group_hints_explain_the_selected_status_action():
    assert "只联系选中范围" in BossFilterGUI._greet_queue_group_hint("待发送")
    assert "逐一核实" in BossFilterGUI._greet_queue_group_hint("待核实")


def test_contact_queue_persists_intent_and_revalidates_before_each_send():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    load_block = source[source.index("def _ensure_greet_queue_loaded"):]
    load_block = load_block[:load_block.index("\n    @staticmethod\n    def _has_direct_send_context")]
    worker_block = source[source.index("def _run_greet_queue_worker"):]
    worker_block = worker_block[:worker_block.index("\n    @staticmethod\n    def _candidate_identity_key")]
    resolve_block = source[source.index("def _resolve_selected_greet_queue_pending"):]
    resolve_block = resolve_block[:resolve_block.index("\n    def _pause_greet_queue")]

    assert "load_contact_queue(candidates, CONTACT_QUEUE_PATH)" in load_block
    assert "save_contact_queue(self.greet_queue_items, CONTACT_QUEUE_PATH)" in load_block
    assert worker_block.index("self._reload_greet_queue_candidate(item)") < worker_block.index(
        "self._revalidate_greet_queue_candidate(candidate)"
    )
    assert worker_block.index("self._revalidate_greet_queue_candidate(candidate)") < worker_block.index(
        'self._set_greet_queue_item_state(item, "发送中", "")'
    )
    assert worker_block.count("self._reload_greet_queue_candidate(item)") == 2
    assert worker_block.count("self._ensure_greet_queue_candidate_page_ready(") >= 2
    assert "job_mismatch_decisions = {}" in worker_block
    assert "resolve_candidate_greeting_confirmation(" in resolve_block
    assert 'item[\'status\'] = "已发送"' in resolve_block
    assert 'item[\'status\'] = "待发送"' in resolve_block


def test_pending_verification_cannot_be_discarded_without_resolution():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    item = {"queue_id": "q1", "status": "待核实"}
    gui.greet_queue_items = [item]
    gui.greet_queue_window = None
    gui.root = object()
    gui._selected_greet_queue_items = Mock(return_value=[item])
    gui._persist_greet_queue = Mock()
    gui._refresh_greet_queue_dialog = Mock()

    with patch("gui_main.messagebox.showwarning") as warning:
        gui._remove_selected_greet_queue_items()

    assert gui.greet_queue_items == [item]
    warning.assert_called_once()
    assert "不能直接移除" in warning.call_args.args[1]
    gui._persist_greet_queue.assert_not_called()


def _contact_worker_gui(candidate):
    gui = BossFilterGUI.__new__(BossFilterGUI)
    item = gui._build_greet_queue_item(candidate)
    gui.greet_queue_items = [item]
    gui.greet_queue_window = None
    gui.root = Mock()
    gui.browser_page = object()
    gui.stop_event = threading.Event()
    gui.greet_queue_running = True
    gui.greet_queue_paused = False
    gui._browser_connection_lock = threading.Lock()
    gui._ensure_greet_queue_browser = Mock(return_value=True)
    gui._make_greet_queue_captcha_callback = Mock(return_value=None)
    gui._reload_greet_queue_candidate = Mock(return_value=(candidate, ""))
    gui._persist_greet_queue = Mock()
    gui.append_log = Mock()
    gui.append_run_log = Mock()
    gui.append_operation_log = Mock()
    gui.refresh_results = Mock()
    gui.refresh_home_stats = Mock()
    gui._refresh_greet_queue_dialog = Mock()
    gui._show_greet_queue_run_result = Mock()

    def set_state(queue_item, status, message=""):
        queue_item["status"] = status
        queue_item["message"] = message

    gui._set_greet_queue_item_state = Mock(side_effect=set_state)
    return gui, item


def test_contact_success_with_local_save_failure_stays_pending_verification():
    candidate = {
        "geek_id": "g1",
        "job_name": "Java Engineer",
        "name": "Candidate A",
        "match_score": 80,
        "greet_context": {"chat_start": {"jid": "j1"}},
    }
    gui, item = _contact_worker_gui(candidate)
    gui._update_greet_status = Mock(return_value=False)

    with patch(
        "bossmaster.send_greeting_with_context", return_value=(True, "sent")
    ), patch("gui_main.time.sleep", return_value=None):
        gui._run_greet_queue_worker()

    assert item["status"] == "待核实"
    assert "本地状态保存失败" in item["message"]
    assert any(
        "成功 0 人" in call.args[0]
        for call in gui.append_operation_log.call_args_list
    )


def test_contact_worker_exception_recovers_sending_item_as_pending_verification():
    candidate = {
        "geek_id": "g1",
        "job_name": "Java Engineer",
        "name": "Candidate A",
        "match_score": 80,
        "greet_context": {"chat_start": {"jid": "j1"}},
    }
    gui, item = _contact_worker_gui(candidate)
    gui._update_greet_status = Mock(return_value=True)

    with patch(
        "bossmaster.send_greeting_with_context",
        side_effect=RuntimeError("connection lost after click"),
    ):
        gui._run_greet_queue_worker()

    assert item["status"] == "待核实"
    assert "意外中断" in item["message"]
    gui._persist_greet_queue.assert_called()
    feedback_callbacks = [
        call.args[1] for call in gui.root.after.call_args_list
        if len(call.args) > 1
    ]
    for callback in feedback_callbacks:
        callback()
    gui._show_greet_queue_run_result.assert_called_once()
    assert "connection lost after click" in gui._show_greet_queue_run_result.call_args.args[0]["error"]


def test_contact_worker_does_not_delay_result_after_final_candidate():
    candidate = {
        "geek_id": "g1",
        "job_name": "Java Engineer",
        "name": "Candidate A",
        "match_score": 80,
        "greet_context": {"chat_start": {"jid": "j1"}},
    }
    gui, _item = _contact_worker_gui(candidate)

    with patch(
        "bossmaster.send_greeting_with_context", return_value=(False, "send failed")
    ), patch("gui_main.time.sleep") as sleep:
        gui._run_greet_queue_worker()

    sleep.assert_not_called()


def test_contact_worker_keeps_delay_between_candidates_only():
    first = {
        "geek_id": "g1",
        "job_name": "Java Engineer",
        "name": "Candidate A",
        "match_score": 80,
        "greet_context": {"chat_start": {"jid": "j1"}},
    }
    second = {
        "geek_id": "g2",
        "job_name": "Java Engineer",
        "name": "Candidate B",
        "match_score": 80,
        "greet_context": {"chat_start": {"jid": "j2"}},
    }
    gui, _item = _contact_worker_gui(first)
    gui.greet_queue_items.append(gui._build_greet_queue_item(second))
    gui._reload_greet_queue_candidate = Mock(
        side_effect=lambda item: (item["candidate"], "")
    )

    with patch(
        "bossmaster.send_greeting_with_context", return_value=(False, "send failed")
    ), patch("gui_main.random.uniform", return_value=2.5), patch(
        "gui_main.time.sleep"
    ) as sleep:
        gui._run_greet_queue_worker()

    sleep.assert_called_once_with(2.5)


def test_contact_worker_stops_after_terminal_http_403():
    first = {
        "geek_id": "g1",
        "job_name": "Java Engineer",
        "name": "Candidate A",
        "match_score": 80,
        "greet_context": {"chat_start": {"jid": "j1"}},
    }
    second = {
        "geek_id": "g2",
        "job_name": "Java Engineer",
        "name": "Candidate B",
        "match_score": 80,
        "greet_context": {"chat_start": {"jid": "j2"}},
    }
    gui, first_item = _contact_worker_gui(first)
    second_item = gui._build_greet_queue_item(second)
    gui.greet_queue_items.append(second_item)
    gui._reload_greet_queue_candidate = Mock(
        side_effect=lambda item: (item["candidate"], "")
    )

    with patch(
        "bossmaster.send_greeting_with_context",
        return_value=(False, "上下文打招呼失败: HTTP 403 请求未成功"),
    ) as send:
        gui._run_greet_queue_worker()

    assert send.call_count == 1
    assert first_item["status"] == "发送失败"
    assert second_item["status"] == "待发送"
    assert any(
        "4xx" in call.args[0]
        and call.kwargs.get("show_in_run_ui") is True
        for call in gui.append_operation_log.call_args_list
    )
    assert any(
        "已停止后续发送" in call.args[0]
        for call in gui.append_operation_log.call_args_list
    )


def test_contact_worker_stops_after_shared_access_block():
    first = {
        "geek_id": "g1",
        "job_name": "Java Engineer",
        "name": "Candidate A",
        "match_score": 80,
        "greet_context": {"chat_start": {"jid": "j1"}},
    }
    second = {
        "geek_id": "g2",
        "job_name": "Java Engineer",
        "name": "Candidate B",
        "match_score": 80,
        "greet_context": {"chat_start": {"jid": "j2"}},
    }
    gui, first_item = _contact_worker_gui(first)
    second_item = gui._build_greet_queue_item(second)
    gui.greet_queue_items.append(second_item)
    gui._reload_greet_queue_candidate = Mock(
        side_effect=lambda item: (item["candidate"], "")
    )
    risk = bossmaster.activate_boss_access_block(
        429,
        "操作频繁",
        "发起沟通",
        cooldown_seconds=30,
    )

    with patch(
        "bossmaster.send_greeting_with_context",
        side_effect=risk,
    ) as send:
        gui._run_greet_queue_worker()

    assert send.call_count == 1
    assert first_item["status"] == "发送失败"
    assert second_item["status"] == "待发送"
    assert any(
        "[访问保护]" in call.args[0]
        and call.kwargs.get("show_in_run_ui") is True
        for call in gui.append_operation_log.call_args_list
    )


def test_contact_worker_stops_when_job_page_identity_triggers_access_block():
    first = {
        "geek_id": "g1",
        "job_name": "Java Engineer",
        "name": "Candidate A",
        "match_score": 80,
    }
    second = {
        "geek_id": "g2",
        "job_name": "Java Engineer",
        "name": "Candidate B",
        "match_score": 80,
    }
    gui, first_item = _contact_worker_gui(first)
    second_item = gui._build_greet_queue_item(second)
    gui.greet_queue_items.append(second_item)
    gui._reload_greet_queue_candidate = Mock(
        side_effect=lambda item: (item["candidate"], "")
    )
    risk = bossmaster.activate_boss_access_block(
        429,
        "岗位身份请求过于频繁",
        "岗位身份读取",
        cooldown_seconds=30,
    )
    gui._ensure_greet_queue_candidate_page_ready = Mock(side_effect=risk)

    with patch("bossmaster.send_greeting_on_list_page") as send:
        gui._run_greet_queue_worker()

    send.assert_not_called()
    assert first_item["status"] == "待发送"
    assert "访问保护" in first_item["message"]
    assert second_item["status"] == "待发送"
    assert any(
        "准备阶段" in call.args[0]
        and call.kwargs.get("show_in_run_ui") is True
        for call in gui.append_operation_log.call_args_list
    )


def test_contact_page_identity_does_not_swallow_access_block():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.browser_page = object()
    gui._get_greet_queue_page_state = Mock(return_value=(
        True,
        "https://www.zhipin.com/web/chat/recommend",
        "",
        "",
    ))
    risk = bossmaster.activate_boss_access_block(
        429,
        "岗位身份请求过于频繁",
        "岗位身份读取",
        cooldown_seconds=30,
    )

    try:
        with patch("bossmaster.get_iframe", return_value=None), \
                patch("bossmaster._read_recommend_page_identity", side_effect=risk):
            gui._greet_queue_candidate_page_ready({"job_name": "Java Engineer"})
        assert False, "job-page risk must reach the queue-level circuit breaker"
    except bossmaster.ApiRiskBlocked as exc:
        assert exc.status == 429


def test_browser_reconnect_method_reuses_existing_live_page():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.browser_page = Mock()
    gui.browser_page.run_js.return_value = 1
    gui.browser_connected = False

    assert gui._try_reconnect_browser() is True
    assert gui.browser_connected is True


def test_contact_browser_reconnect_launches_recommend_page_when_chrome_is_absent():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.append_operation_log = Mock()
    gui._try_reconnect_browser = Mock(return_value=False)
    gui._launch_boss_browser = Mock(return_value=True)

    assert gui._reconnect_browser_or_warn(None, "浏览器未连接", "", "") is True
    gui._launch_boss_browser.assert_called_once_with()
    assert any(
        "自动启动推荐牛人页面" in call.args[0]
        for call in gui.append_operation_log.call_args_list
    )


def test_launch_boss_browser_uses_managed_profile_and_recommend_url():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.stop_event = threading.Event()
    gui.browser_page = Mock(url='https://www.zhipin.com/web/chat/recommend')
    gui._try_reconnect_browser = Mock(return_value=True)
    port_socket = MagicMock()
    port_socket.__enter__.return_value.getsockname.return_value = ('127.0.0.1', 45678)
    connection = MagicMock()

    with patch("gui_main.sys.platform", "win32"), \
            patch("gui_main.os.path.exists", return_value=True), \
            patch("gui_main.socket.socket", return_value=port_socket), \
            patch("gui_main.socket.create_connection", return_value=connection), \
            patch("gui_main.subprocess.Popen") as popen, \
            patch("pathlib.Path.mkdir"), \
            patch("pathlib.Path.write_text"), \
            patch("gui_main.time.sleep", return_value=None):
        assert gui._launch_boss_browser() is True

    command = popen.call_args.args[0]
    assert '--remote-debugging-port=45678' in command
    assert any(arg.startswith('--user-data-dir=') for arg in command)
    assert 'https://www.zhipin.com/web/chat/recommend' in command
    assert gui.browser_address == '127.0.0.1:45678'
    gui._try_reconnect_browser.assert_called_once_with()


def test_greet_queue_run_feedback_covers_success_and_visible_errors():
    success = BossFilterGUI._build_greet_queue_run_feedback({"success": 1})
    assert success == (
        "发送完成",
        "发送完成",
        "成功：1 人\n状态：联系结果已保存",
        "info",
    )

    error = BossFilterGUI._build_greet_queue_run_feedback({
        "error": "无法连接到 Chrome 浏览器。",
    })
    assert error == (
        "发送未完成",
        "本轮未发送",
        "无法连接到 Chrome 浏览器。",
        "error",
    )

    partial = BossFilterGUI._build_greet_queue_run_feedback({
        "success": 1,
        "failed": 1,
        "page_waiting": 2,
        "page_waiting_jobs": {"Java": 1, "Python": 1},
    })
    assert partial[0] == "发送结果"
    assert partial[1] == "发送部分完成"
    assert "成功：1 人" in partial[2]
    assert "失败：1 人" in partial[2]
    assert "待切换岗位：2 人" in partial[2]
    assert "Java（1 人）" in partial[2]
    assert "Python（1 人）" in partial[2]
    assert "下一步：切换对应岗位后再次发送" in partial[2]


def test_greet_queue_result_dialog_uses_shared_fonts_and_adaptive_width():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _show_greet_queue_run_result"):]
    block = block[:block.index("\n    @staticmethod\n    def _candidate_identity_key")]

    assert "font_delta=" not in block
    assert 'min_width=620 if message.count("\\n") >= 2 else 540' in block
    assert "content_bottom_padding=28" in block


def test_candidate_state_diagnostics_uses_group_summary_and_compact_candidate_rows():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    dialog_block = source[source.index("def _show_candidate_state_diagnostics_dialog"):]
    dialog_block = dialog_block[:dialog_block.index("\n    def _clip_table_text")]

    assert 'columns=("count", "level")' in dialog_block
    assert 'show="tree"' in dialog_block
    assert 'for level_index, level in enumerate(("error", "warning", "info")):' in dialog_block
    assert 'group_tree.insert(\n                    "", "end", iid=level_iid' in dialog_block
    assert 'group_tree.insert(\n                        level_iid, "end", iid=child_iid' in dialog_block
    assert 'columns = ("name", "job", "issue", "key_info")' in dialog_block
    assert 'tree.heading("issue", text="问题类型")' in dialog_block
    assert 'tree.heading("key_info", text="关键信息")' in dialog_block
    assert 'orient="horizontal"' not in dialog_block
    assert 'xscrollcommand' not in dialog_block
    assert 'selected_issue_summary_var.set(f"处理建议：{current_issues[0].suggestion}")' in dialog_block
    assert 'self._format_state_issue_key_info(issue, candidate)' in dialog_block
    assert 'self._clip_table_text(issue.suggestion, 42)' not in dialog_block
    assert 'issue_action_var' not in dialog_block
    assert 'tree.heading("severity", text="级别")' not in dialog_block
    assert 'tree.column("severity"' not in dialog_block
    assert 'severity_label.get(issue.severity, "提醒"),' not in dialog_block
    assert 'column_id not in ("#3", "#4")' in dialog_block
    assert 'if column_id == "#3":' in dialog_block
    assert 'full = f"{issue.title}\\n\\n{issue.detail}"' in dialog_block
    assert 'full = issue.detail' in dialog_block
    assert 'def on_issue_group_motion(event):' in dialog_block
    assert 'column_id != "#0"' in dialog_block
    assert '("state_check_group", item_id, column_id)' in dialog_block
    assert 'lambda: self._show_tooltip(label, x, y, tooltip_key, parent=win)' in dialog_block
    assert 'group_tree.bind("<Motion>", on_issue_group_motion)' in dialog_block
    assert 'group_tree.bind("<Leave>", self._hide_tooltip)' in dialog_block
    assert 'ttk.Button(btn_row, text="查看详情", command=show_detail)' not in dialog_block


def test_candidate_workbench_primary_buttons_keep_standard_button_typography():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    style_block = source[source.index("style.configure('Workbench.Primary.TButton'"):]
    style_block = style_block[:style_block.index("# 危险级")]
    daily_block = source[source.index("def _show_daily_candidate_actions_dialog"):]
    daily_block = daily_block[:daily_block.index("\n    def _show_candidate_state_diagnostics_dialog")]
    state_block = source[source.index("def _show_candidate_state_diagnostics_dialog"):]
    state_block = state_block[:state_block.index("\n    def _clip_table_text")]
    queue_block = source[source.index("def _show_greet_queue_dialog"):]
    queue_block = queue_block[:queue_block.index("\n    def _set_greet_queue_item_state")]

    assert "font=self.font_label, padding=(15, 8)" in style_block
    assert "style='Workbench.Primary.TButton'" in daily_block
    assert "style='Workbench.Primary.TButton'" in state_block
    assert 'style="Workbench.Primary.TButton"' in queue_block


def test_candidate_workbench_hierarchies_share_navigation_style_and_neutral_details():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    daily_block = source[source.index("def _show_daily_candidate_actions_dialog"):]
    daily_block = daily_block[:daily_block.index("\n    def _show_candidate_state_diagnostics_dialog")]
    state_block = source[source.index("def _show_candidate_state_diagnostics_dialog"):]
    state_block = state_block[:state_block.index("\n    def _clip_table_text")]
    queue_block = source[source.index("def _show_greet_queue_dialog"):]
    queue_block = queue_block[:queue_block.index("\n    def _set_greet_queue_item_state")]

    for block in (daily_block, state_block, queue_block):
        assert "self._candidate_workbench_navigation_style(scale)" in block
        assert "self._apply_candidate_workbench_navigation_tags(group_tree)" in block
        assert 'tags=("workbench_root",)' in block
        assert 'tags=("workbench_child",)' in block
    assert 'tree.tag_configure("warning", background=' not in state_block
    assert 'tree.tag_configure("error", background=' not in state_block
    assert 'tree.tag_configure("info", background=' not in state_block


def test_candidate_workflow_dialog_subtitles_use_neutral_text_color():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    daily_block = source[source.index("def _show_daily_candidate_actions_dialog"):]
    daily_block = daily_block[:daily_block.index("\n    def _show_candidate_state_diagnostics_dialog")]
    state_block = source[source.index("def _show_candidate_state_diagnostics_dialog"):]
    state_block = state_block[:state_block.index("\n    def _clip_table_text")]
    queue_block = source[source.index("def _show_greet_queue_dialog"):]
    queue_block = queue_block[:queue_block.index("\n    def _on_greet_queue_group_selected")]

    assert "foreground=self.colors['text_secondary']" in daily_block
    assert "foreground=self.colors['primary']" not in daily_block
    assert "headline_color" not in state_block
    assert "foreground=self.colors['text_secondary']" in state_block
    assert "foreground=headline_color" not in state_block
    assert "foreground=self.colors['text_secondary']" in queue_block
    assert "textvariable=self.greet_queue_detail_title_var" in queue_block
    assert "background=self.colors['bg_card']" in queue_block


def test_information_and_workbench_windows_do_not_lock_main_window():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    dialog_source = Path("gui_dialogs.py").read_text(encoding="utf-8")
    blocks = [
        source[source.index("def show_stat_detail"):source.index("\n    def show_result_stat_detail")],
        source[source.index("def show_result_stat_detail"):source.index("\n    def _get_job_rules_cached")],
        source[source.index("def _show_daily_candidate_actions_dialog"):source.index("\n    def _show_candidate_state_diagnostics_dialog")],
        source[source.index("def _show_candidate_state_diagnostics_dialog"):source.index("\n    def _clip_table_text")],
        source[source.index("def _show_greet_queue_dialog"):source.index("\n    def _on_greet_queue_group_selected")],
        source[source.index("def _open_candidate_review_workbench"):source.index("\n    def _render_candidate_review_workbench")],
        dialog_source[dialog_source.index("def show_about_dialog"):dialog_source.index("\ndef show_changelog_dialog")],
        dialog_source[dialog_source.index("def show_changelog_dialog"):],
    ]

    assert all(".grab_set()" not in block for block in blocks)

    modal_block = source[
        source.index("def _show_job_config_diagnostics_dialog"):
        source.index("\n    def _should_prompt_run_job_config")
    ]
    assert ".grab_set()" in modal_block


def test_daily_actions_dialog_uses_time_groups_then_business_and_review_subgroups():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    daily_block = source[source.index("def _show_daily_candidate_actions_dialog"):]
    daily_block = daily_block[:daily_block.index("\n    def _show_candidate_state_diagnostics_dialog")]

    assert 'win.title("今日待办")' in daily_block
    assert 'win.title("今日候选人待办")' not in daily_block
    assert "for timing_index, timing_group in enumerate(ACTION_TIMING_ORDER):" in daily_block
    assert 'text=f"{timing_group}  {len(timing_items)}"' in daily_block
    assert 'if group != "待复核":' in daily_block
    assert "candidate_review_category(item.candidate)" in daily_block
    assert 'child_iid, "end", iid=review_iid, text=f"{category}  {len(category_items)}"' in daily_block
    assert 'open=(group == "待复核")' in daily_block
    assert '"按时间优先级整理候选人，逐项推进下一步"' in daily_block
    assert '("due", "需处理", counts["due"], self.colors[\'primary\'])' in daily_block
    assert 'columns = ("name", "job", "score", "task", "key_info", "due")' in daily_block
    assert '("task", "任务类型", 165, "center")' in daily_block
    assert '("key_info", "关键信息", 245, "w")' in daily_block
    assert '("due", "到期", 80, "center")' in daily_block
    assert 'selected_group_summary_var.set(f"处理建议：{next(iter(unique_actions))}")' in daily_block
    assert 'self._format_daily_action_key_info(item)' in daily_block
    assert 'self._format_daily_action_due(item)' in daily_block
    assert 'orient="horizontal"' not in daily_block
    assert 'xscrollcommand' not in daily_block
    assert 'ttk.Button(btn_row, text="查看详情", command=show_detail)' not in daily_block


def test_daily_action_key_info_removes_repeated_due_text_and_explains_contact_gap():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    scheduled = types.SimpleNamespace(
        group="待约面待推进",
        reason="候选人等待约面安排；下次处理时间：2026-07-23",
        action="确认面试时间，或调整下次跟进日期。",
        due_at="20260723_000000",
        timing_group="以后",
    )
    missing_contact_context = types.SimpleNamespace(
        group="待打招呼",
        reason="候选人已通过筛选，尚未联系",
        action="打开对应岗位的推荐牛人页面并重新扫描，再加入联系清单。",
        due_at="",
        timing_group="待安排",
    )

    assert gui._format_daily_action_key_info(scheduled) == "候选人等待约面安排"
    assert gui._format_daily_action_key_info(missing_contact_context) == "缺少联系条件，需重新扫描岗位"


def test_daily_action_due_uses_explicit_immediate_and_unscheduled_labels():
    immediate = types.SimpleNamespace(due_at="", timing_group="立即处理")
    unscheduled = types.SimpleNamespace(due_at="", timing_group="待安排")
    scheduled = types.SimpleNamespace(due_at="20260723_000000", timing_group="以后")

    assert BossFilterGUI._format_daily_action_due(immediate) == "立即"
    assert BossFilterGUI._format_daily_action_due(unscheduled) == "未安排"
    assert BossFilterGUI._format_daily_action_due(scheduled) == "2026-07-23"


def test_daily_resume_action_promotes_resume_context_menu_entry():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    action_block = source[source.index("def _show_daily_candidate_actions_dialog"):]
    action_block = action_block[:action_block.index("\n    def _show_candidate_state_diagnostics_dialog")]
    menu_block = source[source.index("def _show_candidate_workflow_context_menu"):]
    menu_block = menu_block[:menu_block.index("\n    def _bind_treeview_sorting")]
    result_menu_block = source[source.index("def _build_candidate_context_menu"):]
    result_menu_block = result_menu_block[:result_menu_block.index("\n    def _find_candidate_by_tree_item")]

    assert '"resume" if item.group == "待完成简历评估"' in action_block
    assert 'label=" 导入简历 / 二次评估"' in menu_block
    assert 'label=" 导入简历 / 二次评估"' in result_menu_block
    assert 'elif primary_action == "resume":' in menu_block


def test_workflow_context_menu_opens_review_and_uses_shared_decision_rules():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    menu_block = source[source.index("def _show_candidate_workflow_context_menu"):]
    menu_block = menu_block[:menu_block.index("\n    def _bind_treeview_sorting")]

    assert 'label=" 查看与复核"' in menu_block
    assert "self.icons.button('candidate_review', self.colors['primary'])" in menu_block
    assert 'label=" 查看详情"' not in menu_block
    assert "self._open_candidate_review_workbench(candidate)" in menu_block
    assert "candidate_greet_skip_reason(candidate)" in menu_block
    assert "candidate_can_manual_approve_contact(candidate)" in menu_block
    assert 'label=" 确认并加入联系清单"' in menu_block
    assert "candidate.get('qualification_status') == 'manual_review'" in menu_block
    assert 'label=" 核实发送结果"' in menu_block
    assert "self._focus_candidate_in_greet_queue(candidate)" in menu_block


def test_review_workbench_promotes_pending_send_verification():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    action_block = source[source.index("def _render_candidate_review_actions"):]
    action_block = action_block[:action_block.index("\n    def _navigate_candidate_review")]

    assert "if candidate.get('greet_confirmation_pending'):" in action_block
    assert '"核实发送结果"' in action_block
    assert "self._focus_candidate_in_greet_queue(candidate)" in action_block


def test_review_queue_add_refreshes_current_candidate_only_after_success():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.candidate_review_window = Mock()
    gui._add_candidates_to_greet_queue = Mock(return_value=1)
    on_saved = Mock()
    candidate = {"geek_id": "queued", "job_name": "Java"}

    added = gui._add_candidate_to_greet_queue_from_review(candidate, on_saved)

    assert added == 1
    gui._add_candidates_to_greet_queue.assert_called_once_with(
        [candidate],
        parent=gui.candidate_review_window,
    )
    on_saved.assert_called_once_with()

    gui._add_candidates_to_greet_queue.reset_mock()
    gui._add_candidates_to_greet_queue.return_value = 0
    on_saved.reset_mock()

    added = gui._add_candidate_to_greet_queue_from_review(candidate, on_saved)

    assert added == 0
    on_saved.assert_not_called()


def test_review_workbench_queued_candidate_offers_view_instead_of_duplicate_add():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.candidate_review_primary_actions = Mock()
    gui.candidate_review_secondary_actions = Mock()
    gui.candidate_review_window = Mock()
    gui.colors = {
        "primary": "#2563EB",
        "success": "#16A34A",
        "warning": "#D97706",
    }
    gui._clear_candidate_review_actions = Mock()
    gui._greet_queue_item_for_candidate = Mock(
        return_value={"status": "待发送"}
    )
    gui._add_candidate_review_action = Mock()
    candidate = {
        "geek_id": "queued",
        "job_name": "Java",
        "qualification_status": "qualified",
        "match_score": 70,
    }

    gui._render_candidate_review_actions(candidate, Mock())

    action_labels = [
        item.args[1] for item in gui._add_candidate_review_action.call_args_list
    ]
    assert "查看联系清单" in action_labels
    assert "加入联系清单" not in action_labels


def test_focus_candidate_in_greet_queue_uses_actual_status_group():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.root = Mock()
    gui.greet_queue_tree = None
    item = {
        "queue_id": "queue-1",
        "status": "待发送",
        "candidate": {"geek_id": "queued", "job_name": "Java"},
    }
    gui._greet_queue_item_for_candidate = Mock(return_value=item)
    gui._show_greet_queue_dialog = Mock()
    gui._refresh_greet_queue_dialog = Mock()
    candidate = {"geek_id": "queued", "job_name": "Java"}

    gui._focus_candidate_in_greet_queue(candidate)

    assert gui.greet_queue_selected_group == "待发送"
    gui._show_greet_queue_dialog.assert_called_once_with(parent=gui.root)
    gui._refresh_greet_queue_dialog.assert_called_once_with()


def test_daily_followup_action_promotes_followup_context_menu_entry():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    action_block = source[source.index("def _show_daily_candidate_actions_dialog"):]
    action_block = action_block[:action_block.index("\n    def _show_candidate_state_diagnostics_dialog")]
    menu_block = source[source.index("def _show_candidate_workflow_context_menu"):]
    menu_block = menu_block[:menu_block.index("\n    def _bind_treeview_sorting")]

    assert '"待约面待推进", "面试后待反馈",' in action_block
    assert 'label=" 更新跟进"' in menu_block
    assert 'elif primary_action == "followup":' in menu_block
    assert 'label=" 标记已回复"' in menu_block
    assert 'label=" 推进到待约面"' in menu_block
    assert 'label=" 明天再跟进"' in menu_block


def test_followup_dialog_supports_due_date_quick_choices_and_persistence():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _mark_candidate_followup"):]
    block = block[:block.index("\n    def _update_candidate_feedback")]

    assert 'text="下次跟进日期"' in block
    for label in ("今天", "明天", "3 天后", "7 天后", "不设置"):
        assert f'("{label}",' in block
    assert "quick_date_frame.pack(" in block
    assert "fill='x'" in block
    assert "grid_columnconfigure(column, weight=1, uniform='followup_quick_date')" in block
    assert "sticky='ew'" in block
    assert 'status_combo.bind("<<ComboboxSelected>>", reset_due_for_status)' in block
    assert "next_due = normalize_followup_at(due_input)" in block
    assert 're.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", due_input)' in block
    assert "下次跟进日期无效，请检查年月日是否正确" in block
    assert "下次跟进日期格式不正确，请使用 YYYY-MM-DD" in block
    assert 'messagebox.showerror("日期错误", error_text, parent=win)' in block
    assert 'status == "待约面" and not next_due' in block
    assert "apply_followup_state(" in block
    assert 'needs_feedback = status == "不合适"' in block
    assert 'default_status="误推"' in block


def test_greet_queue_start_requires_confirmation():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    start_block = source[source.index("def _start_greet_queue"):]
    start_block = start_block[:start_block.index("\n    def _confirm_start_greet_queue")]
    confirm_block = source[source.index("def _confirm_start_greet_queue"):]
    confirm_block = confirm_block[:confirm_block.index("\n    def _make_greet_queue_captcha_callback")]

    assert "self._confirm_start_greet_queue(pending)" in start_block
    assert "self._build_greet_queue_confirmation_content(pending)" in confirm_block
    assert 'yes_label="开始联系"' in confirm_block
    assert 'no_label="取消"' in confirm_block
    assert 'headline=headline' in confirm_block
    assert 'show_icon=False' in confirm_block
    assert 'min_width=620' in confirm_block
    assert "font_delta=" not in confirm_block
    assert "当前已就绪" not in confirm_block
    assert "待核实不会自动重发" not in confirm_block
    assert "失败待重试" not in confirm_block
    assert "每位候选人发送前都会重新检查" not in confirm_block
    assert "messagebox.askyesno" in confirm_block


def test_greet_queue_confirmation_explains_page_requirements_by_send_path():
    direct = {
        "candidate": {
            "job_name": "Java",
            "greet_context": {"chat_start": {"jid": "j1"}},
        }
    }
    java_page = {"candidate": {"job_name": "Java"}}
    python_page = {"candidate": {"job_name": "Python"}}

    headline, direct_message = BossFilterGUI._build_greet_queue_confirmation_content([direct])
    assert headline == "联系 1 名候选人？"
    assert "Chrome：已连接，推荐牛人页面已就绪" in direct_message
    assert "登录：BOSS 账号已登录" in direct_message
    assert "岗位：无需切换岗位页面" in direct_message

    _, page_message = BossFilterGUI._build_greet_queue_confirmation_content([java_page])
    assert "岗位：需要切换到 Java（1 人）" in page_message
    assert "Java（1 人）" in page_message

    _, mixed_message = BossFilterGUI._build_greet_queue_confirmation_content(
        [direct, java_page, python_page]
    )
    assert "岗位：1 人无需切换；2 人需要" in mixed_message
    assert "Java（1 人）" in mixed_message
    assert "Python（1 人）" in mixed_message
    assert "当前岗位不一致的候选人会保留" in mixed_message


def test_greet_queue_click_prepares_browser_before_confirmation():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.greet_queue_running = False
    gui.greet_queue_preparing = False
    gui.greet_queue_paused = True
    gui.is_running = False
    gui.greet_queue_items = [{"status": "待发送", "candidate": {"geek_id": "g1"}}]
    gui.greet_queue_tree = None
    gui.stop_event = Mock()
    gui._ensure_greet_queue_loaded = Mock()
    gui._confirm_start_greet_queue = Mock(return_value=True)
    gui._update_greet_queue_action_states = Mock()
    gui.append_operation_log = Mock()

    with patch("gui_main.threading.Thread") as thread:
        gui._start_greet_queue()

    gui._confirm_start_greet_queue.assert_not_called()
    gui.stop_event.clear.assert_called_once_with()
    assert gui.greet_queue_preparing is True
    assert gui.greet_queue_running is False
    thread.assert_called_once_with(
        target=gui._prepare_greet_queue_start,
        args=(gui.greet_queue_items,),
        daemon=True,
    )
    thread.return_value.start.assert_called_once_with()


def test_greet_queue_preparation_status_is_not_rendered_inside_send_button():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.greet_queue_items = [
        {"status": "待发送"},
        {"status": "待发送"},
    ]
    gui.greet_queue_preparing = True
    gui.greet_queue_prepare_text = "正在打开推荐牛人页面..."
    gui.greet_queue_running = False
    gui.greet_queue_paused = False
    gui.greet_queue_tree = None
    gui.greet_queue_summary_var = Mock()
    gui.greet_queue_start_btn = Mock()
    gui.greet_queue_start_btn.winfo_exists.return_value = True

    gui._update_greet_queue_action_states()

    gui.greet_queue_summary_var.set.assert_called_once_with(
        "发送准备：正在打开推荐牛人页面..."
    )
    gui.greet_queue_start_btn.configure.assert_called_once_with(
        text="开始联系",
        state="disabled",
    )


def test_ready_browser_prompts_confirmation_then_starts_send_worker():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    pending = [{"status": "待发送", "candidate": {"geek_id": "g1"}}]
    gui.greet_queue_preparing = True
    gui.greet_queue_prepare_text = "正在连接 Chrome..."
    gui._update_greet_queue_action_states = Mock()
    gui._confirm_start_greet_queue = Mock(return_value=True)
    gui._begin_greet_queue_send = Mock()

    gui._finish_greet_queue_preparation(pending)

    assert gui.greet_queue_preparing is False
    assert gui.greet_queue_prepare_text == ""
    gui._confirm_start_greet_queue.assert_called_once_with(pending)
    gui._begin_greet_queue_send.assert_called_once_with(pending)


def test_contact_queue_sends_only_selected_pending_candidate():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    selected = {"queue_id": "q1", "status": "待发送"}
    other = {"queue_id": "q2", "status": "待发送"}
    gui.greet_queue_items = [selected, other]
    gui.greet_queue_running = False
    gui.greet_queue_preparing = False
    gui.greet_queue_paused = False
    gui.is_running = False
    gui.stop_event = Mock()
    gui._ensure_greet_queue_loaded = Mock()
    gui._selected_greet_queue_items = Mock(return_value=[selected])
    gui._update_greet_queue_action_states = Mock()
    gui.append_operation_log = Mock()

    with patch("gui_main.threading.Thread") as thread:
        gui._start_greet_queue()

    thread.assert_called_once_with(
        target=gui._prepare_greet_queue_start,
        args=([selected],),
        daemon=True,
    )


def test_contact_browser_readiness_only_checks_selected_pending_candidates():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    selected = {
        "status": "待发送",
        "candidate": {"greet_context": {"chat_start": {"jid": "j1"}}},
    }
    other = {"status": "待发送", "candidate": {}}
    gui.greet_queue_items = [selected, other]
    gui.browser_page = Mock()
    gui.append_operation_log = Mock()
    gui._get_greet_queue_page_state = Mock(
        return_value=(True, "https://www.zhipin.com/web/geek/chat", "", "")
    )
    gui._is_boss_recommend_url = Mock(return_value=False)

    assert gui._ensure_greet_queue_browser(None, [selected]) is True

    gui._is_boss_recommend_url.assert_not_called()


def test_contact_queue_does_not_fall_back_to_all_when_selection_is_not_pending():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    selected = {"queue_id": "q1", "status": "已发送"}
    other = {"queue_id": "q2", "status": "待发送"}
    gui.greet_queue_items = [selected, other]
    gui.greet_queue_running = False
    gui.greet_queue_preparing = False
    gui.is_running = False
    gui.greet_queue_window = None
    gui.root = Mock()
    gui._ensure_greet_queue_loaded = Mock()
    gui._selected_greet_queue_items = Mock(return_value=[selected])

    with patch("gui_main.threading.Thread") as thread, patch(
        "gui_main.messagebox.showinfo"
    ) as showinfo:
        gui._start_greet_queue()

    thread.assert_not_called()
    assert "选中的候选人当前不可联系" in showinfo.call_args.args[1]


def test_contact_queue_send_button_reflects_selected_scope():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    selected = {"queue_id": "q1", "status": "待发送"}
    other = {"queue_id": "q2", "status": "待发送"}
    gui.greet_queue_items = [selected, other]
    gui.greet_queue_running = False
    gui.greet_queue_preparing = False
    gui.greet_queue_paused = False
    gui.greet_queue_summary_var = Mock()
    gui.greet_queue_start_btn = Mock()
    gui.greet_queue_start_btn.winfo_exists.return_value = True
    gui._selected_greet_queue_items = Mock(return_value=[selected])

    gui._update_greet_queue_action_states()

    gui.greet_queue_start_btn.configure.assert_called_once_with(
        text="开始联系",
        state="normal",
    )


def test_contact_queue_send_worker_receives_confirmed_selection():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    pending = [{"queue_id": "q1", "status": "待发送"}]
    gui.greet_queue_running = False
    gui.greet_queue_paused = True
    gui._update_greet_queue_action_states = Mock()

    with patch("gui_main.threading.Thread") as thread:
        gui._begin_greet_queue_send(pending)

    thread.assert_called_once_with(
        target=gui._run_greet_queue_worker,
        args=(pending,),
        daemon=True,
    )


def test_ready_browser_restores_queue_summary_before_confirmation():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    pending = [{"status": "待发送", "candidate": {"geek_id": "g1"}}]
    gui.greet_queue_items = pending
    gui.greet_queue_preparing = True
    gui.greet_queue_prepare_text = "正在连接 Chrome..."
    gui.greet_queue_running = False
    gui.greet_queue_paused = False
    gui.greet_queue_tree = None
    gui.greet_queue_summary_var = Mock()
    gui._begin_greet_queue_send = Mock()

    def confirm(_pending):
        gui.greet_queue_summary_var.set.assert_called_with(
            "发送前会再次核验候选人和岗位状态"
        )
        return False

    gui._confirm_start_greet_queue = Mock(side_effect=confirm)

    gui._finish_greet_queue_preparation(pending)

    gui._confirm_start_greet_queue.assert_called_once_with(pending)
    gui._begin_greet_queue_send.assert_not_called()


def test_browser_preflight_waits_for_login_before_showing_confirmation():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    pending = [{"status": "待发送", "candidate": {"geek_id": "g1"}}]
    gui.greet_queue_window = None
    gui.root = Mock()
    gui.browser_page = Mock()
    gui.stop_event = threading.Event()
    gui._browser_connection_lock = threading.Lock()
    gui._is_browser_page_alive = Mock(return_value=True)
    gui._set_greet_queue_prepare_status = Mock()
    gui._finish_greet_queue_preparation = Mock()
    gui.append_operation_log = Mock()
    gui._get_greet_queue_page_state = Mock(side_effect=[
        (False, "https://www.zhipin.com/web/user/", "", "当前停留在 BOSS 登录页，请先完成登录"),
        (True, "https://www.zhipin.com/web/chat/recommend", "", ""),
    ])

    with patch("gui_main.time.sleep", return_value=None):
        gui._prepare_greet_queue_start(pending)

    callback = gui.root.after.call_args.args[1]
    callback()
    assert gui._get_greet_queue_page_state.call_count == 2
    assert any(
        "请在 Chrome 中完成 BOSS 登录" in call.args[0]
        for call in gui.append_operation_log.call_args_list
    )
    gui._finish_greet_queue_preparation.assert_called_once_with(pending, "")


def test_browser_preflight_navigates_existing_chrome_to_recommend_page():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    pending = [{"status": "待发送", "candidate": {"geek_id": "g1"}}]
    gui.greet_queue_window = None
    gui.root = Mock()
    gui.browser_page = Mock()
    gui.stop_event = threading.Event()
    gui._browser_connection_lock = threading.Lock()
    gui._is_browser_page_alive = Mock(return_value=True)
    gui._set_greet_queue_prepare_status = Mock()
    gui._finish_greet_queue_preparation = Mock()
    gui.append_operation_log = Mock()
    gui._get_greet_queue_page_state = Mock(side_effect=[
        (False, "https://example.com", "", "当前页面不是 BOSS 直聘页面"),
        (True, "https://www.zhipin.com/web/chat/recommend", "", ""),
    ])

    with patch("gui_main.time.sleep", return_value=None):
        gui._prepare_greet_queue_start(pending)

    gui.browser_page.get.assert_called_once_with(
        "https://www.zhipin.com/web/chat/recommend"
    )
    gui.root.after.call_args.args[1]()
    gui._finish_greet_queue_preparation.assert_called_once_with(pending, "")


def test_gui_run_builds_contact_list_without_direct_sending():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    run_page_block = source[source.index("def create_run_page"):]
    run_page_block = run_page_block[:run_page_block.index("\n    def create_result_page")]
    worker_block = source[source.index("def run_worker"):]
    worker_block = worker_block[:worker_block.index("\n        # 启动后台线程")]

    assert 'value="仅保存筛选结果"' in run_page_block
    assert '"将强烈推荐加入联系清单"' in run_page_block
    assert '"将推荐及以上加入联系清单"' in run_page_block
    assert "greet=False" in worker_block
    assert "scanned_candidates = run_smart_scan(" in worker_block
    assert "self._add_scan_candidates_to_contact_queue(" in worker_block
    assert "def greet_confirm_callback(message):" not in worker_block
    assert "job_config_callback=job_config_callback" in worker_block
    assert "greet_confirm_callback=" not in worker_block


def test_run_page_describes_actual_ai_score_adjustment_range():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    run_page_block = source[source.index("def create_run_page"):]
    run_page_block = run_page_block[:run_page_block.index("\n    def create_result_page")]

    assert '_note_suffix = "15 分调整"' in run_page_block
    assert '_note_suffix = "10 分调整"' not in run_page_block


def test_run_page_exposes_user_friendly_advanced_scan_settings():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    run_page_block = source[source.index("def create_run_page"):]
    run_page_block = run_page_block[:run_page_block.index("\n    def create_result_page")]
    worker_block = source[source.index("def run_worker"):]
    worker_block = worker_block[:worker_block.index("\n    def on_closing")]

    assert "高级扫描设置" in run_page_block
    assert "这些设置会增加访问频率，建议谨慎调高" in run_page_block
    assert 'advanced_inner.pack(fill="x")' in run_page_block
    assert 'ttk.Label(row_api_enhance, text="", width=12' in run_page_block
    assert "扫描增强:" in run_page_block
    assert "自动补全候选人详情" in run_page_block
    assert "最多读取:" in run_page_block
    assert "可提升经验、薪资、城市等信息准确性" in run_page_block
    assert 'ttk.Label(row_contact_prepare, text="", width=12' in run_page_block
    assert "后续联系:" in run_page_block
    assert "扫描后准备联系信息" in run_page_block
    assert "最多准备:" in run_page_block
    assert "可提升打招呼成功率" in run_page_block
    assert "读取越多越慢" not in run_page_block
    assert "准备人数越多耗时越长" not in run_page_block
    assert "api_direct_pages * 20" in worker_block
    assert "listener_first=not api_direct_enabled" in worker_block
    assert "greet_context_capture=greet_context_capture_enabled" in worker_block
    assert "greet_context_limit=greet_context_capture_limit" in worker_block
    assert '"提取链路"' in worker_block
    assert 'self.append_run_log(f"扫描增强：' not in worker_block
    assert 'self.append_run_log(f"后续联系：' not in worker_block


def test_run_page_job_selector_defaults_to_recent_or_saved_job():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    run_page_block = source[source.index("def create_run_page"):]
    run_page_block = run_page_block[:run_page_block.index("\n    def create_result_page")]
    show_page_run_block = source[source.index("def show_page_run"):]
    show_page_run_block = show_page_run_block[:show_page_run_block.index("\n    def show_page_result")]
    save_job_block = source[source.index("def save_current_job"):]
    save_job_block = save_job_block[:save_job_block.index("\n    def _restore_or_clear_job_form")]

    assert 'self.job_select_var = tk.StringVar(value="")' in run_page_block
    assert "_sync_run_job_combo_values(self.job_rules, prefer_current=False)" in run_page_block
    assert "self._sync_run_job_combo_values(job_rules)" in show_page_run_block
    assert "self._remember_run_job_selection(normalized_job_name)" in save_job_block


def test_result_tree_ctrl_a_selects_all_visible_candidates():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.result_tree = Mock()
    gui.result_tree.get_children.return_value = ("row-1", "row-2")

    result = gui._select_all_result_rows()

    assert result == "break"
    gui.result_tree.selection_set.assert_called_once_with(("row-1", "row-2"))
    gui.result_tree.focus.assert_called_once_with("row-1")
    gui.result_tree.see.assert_called_once_with("row-1")


def test_result_tree_binds_ctrl_a_for_batch_selection():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    bind_block = source[source.index("def _bind_treeview_context_menu"):]
    bind_block = bind_block[:bind_block.index("\n    def _on_tree_motion")]

    assert "self._select_all_result_rows" in bind_block
    assert "'<Control-a>'" in bind_block
    assert "'<Control-A>'" in bind_block


def test_greet_queue_page_state_detects_boss_login_page():
    assert BossFilterGUI._is_boss_login_page("https://www.zhipin.com/web/user/")
    assert BossFilterGUI._is_boss_login_page("https://www.zhipin.com/", "扫码登录\n微信扫码")
    assert not BossFilterGUI._is_boss_login_page("https://www.zhipin.com/web/chat/recommend")


def test_boss_recommend_url_accepts_chat_and_frame_routes():
    assert BossFilterGUI._is_boss_recommend_url("https://www.zhipin.com/web/chat/recommend")
    assert BossFilterGUI._is_boss_recommend_url("https://www.zhipin.com/web/frame/recommend/?jobid=job-123&status=0")
    assert not BossFilterGUI._is_boss_recommend_url("https://www.zhipin.com/web/user/")
    assert not BossFilterGUI._is_boss_recommend_url("https://example.com/web/chat/recommend")


def test_start_run_accepts_frame_recommend_page():
    class FakePage:
        url = "https://www.zhipin.com/web/frame/recommend/?jobid=job-123&status=0"

        def run_js(self, script):
            if script == 'return 1':
                return 1
            if 'slice(0, 800)' in script:
                return "推荐牛人"
            return {
                "readyState": "complete",
                "href": self.url,
                "hasCards": True,
                "text": "推荐牛人",
            }

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.is_running = False
    gui.browser_connected = True
    gui.browser_page = FakePage()
    gui.start_btn = _FakeWidget()
    gui.stop_btn = _FakeWidget()
    gui.status_label = _FakeWidget()
    gui.progress_label = _FakeWidget()
    gui.progress_var = _FakeVar()
    gui.stop_event = _FakeStopEvent()
    gui.colors = {"warning": "#F9A825"}
    logs = []
    gui.append_run_log = logs.append
    started = []

    class FakeThread:
        def __init__(self, target):
            self.target = target
            self.daemon = False

        def start(self):
            started.append(self.target)

    with patch("gui_main.threading.Thread", FakeThread), patch("gui_main.messagebox.showwarning") as showwarning:
        gui.start_run()

    showwarning.assert_not_called()
    assert gui.is_running is True
    assert gui.stop_event.cleared is True
    assert started == [gui.run_worker]


def test_run_page_readiness_rejects_bare_frame_shell():
    class FakePage:
        url = "https://www.zhipin.com/web/frame/recommend/"

        def run_js(self, script):
            if script == 'return 1':
                return 1
            if 'slice(0, 800)' in script:
                return "推荐牛人"
            return {
                "readyState": "complete",
                "href": self.url,
                "hasCards": False,
                "text": "推荐牛人",
            }

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.browser_page = FakePage()

    ready, reason = gui._get_run_page_readiness()

    assert ready is False
    assert "选择岗位" in reason


def test_run_page_readiness_reports_missing_published_job():
    class FakePage:
        url = "https://www.zhipin.com/web/chat/recommend"

        def run_js(self, script):
            if script == 'return 1':
                return 1
            if 'slice(0, 800)' in script:
                return "推荐牛人"
            return {
                "readyState": "complete",
                "href": "https://www.zhipin.com/web/frame/recommend/?jobid&status=0",
                "hasCards": False,
                "text": "推荐\n您需要先发布职位，才能查看推荐牛人\n发布职位",
            }

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.browser_page = FakePage()

    ready, reason = gui._get_run_page_readiness()

    assert ready is False
    assert reason == "当前账号没有可用的已发布职位，暂时无法扫描候选人"


def test_run_page_readiness_rejects_login_page():
    class FakePage:
        url = "https://www.zhipin.com/web/user/"

        def run_js(self, script):
            if script == 'return 1':
                return 1
            return "扫码登录\n微信扫码"

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.browser_page = FakePage()

    ready, reason = gui._get_run_page_readiness()

    assert ready is False
    assert "完成登录" in reason


def test_selector_auto_check_is_limited_to_recommend_page():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    check_block = source[source.index("def _auto_check_selectors"):]
    check_block = check_block[:check_block.index("\n    def check_browser_connection")]

    assert "self._is_boss_recommend_url(current_url)" in check_block
    assert "选择器自动检查已跳过" in check_block


def test_selector_auto_check_does_not_warn_for_skipped_card_check():
    class FakePage:
        url = "https://www.zhipin.com/web/chat/recommend"

        def run_js(self, _script):
            return 1

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._selectors_auto_checked = False
    gui.browser_connected = True
    gui.browser_page = FakePage()
    logs = []
    ui_callbacks = []
    gui.append_run_log = logs.append
    gui.run_on_ui = ui_callbacks.append
    results = [{
        "group": "candidate_card",
        "name": "all_cards",
        "status": "skip",
        "detail": "当前账号没有已发布职位，跳过卡片选择器验证",
    }]

    with patch("bossmaster.check_selectors_health", return_value=results):
        gui._auto_check_selectors()

    assert gui._selectors_auto_checked is True
    assert any("选择器自动检查已跳过" in log for log in logs)
    assert ui_callbacks == []


def test_selector_auto_check_defers_refresh_errors_and_keeps_page_for_retry():
    class ContextLostError(Exception):
        pass

    class FakePage:
        url = "https://www.zhipin.com/web/chat/recommend"

        def run_js(self, _script):
            return 1

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._selectors_auto_checked = False
    gui._selector_check_retry_pending = False
    gui.browser_connected = True
    gui.browser_page = FakePage()
    logs = []
    gui.append_run_log = logs.append

    with patch(
        "bossmaster.check_selectors_health",
        side_effect=ContextLostError("页面被刷新，请等待页面加载完成"),
    ):
        gui._auto_check_selectors()
        gui._auto_check_selectors()

    assert gui._selectors_auto_checked is False
    assert gui._selector_check_retry_pending is True
    assert gui.browser_connected is True
    assert gui.browser_page is not None
    assert logs == ["选择器自动检查暂缓：页面正在加载，稳定后将自动重试"]


def test_selector_auto_check_treats_closed_browser_as_disconnect_not_selector_failure():
    class PageDisconnectedError(Exception):
        pass

    class FakePage:
        url = "https://www.zhipin.com/web/chat/recommend"

        def run_js(self, _script):
            return 1

    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._selectors_auto_checked = False
    gui._selector_check_retry_pending = False
    gui.browser_connected = True
    gui.browser_page = FakePage()
    logs = []
    gui.append_run_log = logs.append

    with patch(
        "bossmaster.check_selectors_health",
        side_effect=PageDisconnectedError("与页面的连接已断开。"),
    ):
        gui._auto_check_selectors()

    assert gui._selectors_auto_checked is False
    assert gui.browser_connected is False
    assert gui.browser_page is None
    assert logs == ["浏览器页面连接短暂中断，等待自动重连..."]


def test_silent_browser_poll_does_not_log_missing_debug_port():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    check_block = source[source.index("def check_browser_connection"):]
    check_block = check_block[:check_block.index("\n    def _start_browser_auto_check")]

    assert 'if not silent and prev_state != "● 未连接":' in check_block


def test_run_worker_preserves_scan_completion_state():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    run_block = source[source.index("def run_worker"):]
    run_block = run_block[:run_block.index("\n    def on_closing")]
    create_block = source[source.index("def create_run_page"):]
    create_block = create_block[:create_block.index("\n    def create_result_page")]
    start_block = source[source.index("def start_run"):]
    start_block = start_block[:start_block.index("\n    def stop_run")]

    assert 'final_desc.startswith(("[达到轮次上限]", "[可能未扫完]"))' in run_block
    assert 'final_desc.startswith("[扫描中断]")' in run_block
    assert "str(description).startswith('[')" in run_block
    assert "job_match_callback=job_match_callback" in run_block
    assert "job_config_callback=job_config_callback" in run_block
    assert 'context="run"' in run_block
    assert 'self.progress_var.set(100)' in run_block
    assert 'self._replace_run_summary_contact_queue_count(final_desc, 0)' in run_block
    assert 'self._set_run_summary(summary_desc)' in run_block
    assert 'self._reset_run_summary()' in start_block
    assert 'self.run_summary_text_label' in create_block
    assert '本轮结果摘要' in create_block
    assert '✔ 运行完成' not in run_block


def test_run_control_buttons_keep_icons_visible_while_disabled():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    create_block = source[source.index("def create_run_page"):]
    create_block = create_block[:create_block.index("\n    def create_result_page")]

    assert "icon_play_run_disabled = self.icons.button('play', self.colors['text_muted'])" in create_block
    assert "image=(icon_play_run, 'disabled', icon_play_run_disabled)" in create_block
    assert "self.start_btn._icon_refs = (icon_play_run, icon_play_run_disabled)" in create_block
    assert "icon_stop_disabled = self.icons.button('stop', self.colors['text_muted'])" in create_block
    assert "image=(icon_stop, 'disabled', icon_stop_disabled)" in create_block
    assert "self.stop_btn._icon_refs = (icon_stop, icon_stop_disabled)" in create_block


def test_run_control_uses_compact_rounds_input_and_matching_button_fonts():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    styles_block = source[source.index("def setup_styles"):]
    styles_block = styles_block[:styles_block.index("\n    def create_sidebar")]
    create_block = source[source.index("def create_run_page"):]
    create_block = create_block[:create_block.index("\n    def create_result_page")]

    assert "increment=10, textvariable=self.rounds_var,\n                                       width=8" in create_block
    assert "'RunControl.Danger.TButton'" in styles_block
    assert "font=(FONT_FAMILY_SEMIBOLD, int(13 * page_fs))" in styles_block
    assert "style='Accent.TButton'" in create_block
    assert "style='RunControl.Danger.TButton'" in create_block


def test_inline_form_notes_use_flat_copy_without_outer_parentheses():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    run_block = source[source.index("def create_run_page"):]
    run_block = run_block[:run_block.index("\n    def create_result_page")]
    result_block = source[source.index("def create_result_page"):]
    result_block = result_block[:result_block.index("\n    def create_education_page")]

    assert 'text="推荐 20-100 轮次"' in run_block
    assert '_note_prefix = "对通过筛选的候选人进行 LLM 二次评估，"' in run_block
    assert '_note_suffix = "15 分调整"' in run_block
    assert '(推荐 50-200 轮次)' not in run_block
    assert 'StringVar(value=str(MAX_ROUNDS_DEFAULT))' in run_block
    assert 'self._result_search_placeholder = "姓名/匹配分/推荐指数/状态"' in result_block
    assert "textvariable=self.result_search_var, width=26" in result_block
    assert "'text_placeholder', ui_theme.TEXT_PLACEHOLDER" in result_block
    assert "max(8, int(8 * self.dpi_scale * self.zoom_factor))" in result_block
    assert "bind('<FocusIn>', _hide_result_search_placeholder)" in result_block
    assert "bind('<FocusOut>', _show_result_search_placeholder)" in result_block
    assert 'text="Esc 清空"' in result_block
    assert "def _sync_result_search_clear_hint():" in result_block
    assert "hint.pack_forget()" in result_block
    assert "pack_options['before'] = result_view_label" in result_block
    assert 'text="姓名/匹配分/推荐指数/状态，Esc 清空"' not in result_block
    assert 'bind_all("<Button-1>", self._on_global_left_click, add="+")' in source
    assert 'lambda _event: self._refresh_results_and_reset_sort()' in result_block
    assert '"刷新结果并恢复默认排序"' in result_block


def test_run_log_and_shared_dialogs_use_the_larger_font():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    styles_block = source[source.index("def setup_styles"):]
    styles_block = styles_block[:styles_block.index("\n    def create_sidebar")]
    create_block = source[source.index("def create_run_page"):]
    create_block = create_block[:create_block.index("\n    def create_result_page")]

    assert "self.font_log = (FONT_FAMILY, int(12 * page_fs))" in styles_block
    assert "font_run_log" not in styles_block
    assert "font=self.font_log" in create_block


def test_ai_parse_reminder_uses_shared_modal_font_without_compact_delta():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _apply_ai_enhance_result"):]
    block = block[:block.index("\n    def _start_ai_progress_animation")]

    assert '"AI 解析提醒"' in block
    assert "self._format_ai_parse_warning_item(" in block
    assert "font_delta=" not in block


def test_ai_parse_warning_item_separates_conclusion_from_confirmation_prompt():
    assert BossFilterGUI._format_ai_parse_warning_item(
        "数据库要求写的是 MySQL、Oracle 其中一种，请确认是否满足任一即可"
    ) == (
        "数据库要求写的是 MySQL、Oracle 其中一种",
        "请确认：是否满足任一即可",
    )
    assert BossFilterGUI._format_ai_parse_warning_item("需要人工核对") == (
        "需要人工核对",
        "",
    )


def test_candidate_state_issue_key_info_keeps_only_candidate_specific_fact():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    incomplete_greeting = types.SimpleNamespace(
        title="打招呼记录不完整",
        detail="候选人已标记为已打招呼，但没有记录发送时间或发送方式。",
    )
    low_score = types.SimpleNamespace(
        title="低分候选人缺少保留理由",
        detail="匹配分低于通过线。",
    )
    manual_review = types.SimpleNamespace(
        title="需要人工确认",
        detail="候选人结论没有显示为待人工确认。",
    )

    assert gui._format_state_issue_key_info(
        incomplete_greeting,
        {"greet_sent_at": "", "greet_method": ""},
    ) == "缺少：发送时间、发送方式"
    assert gui._format_state_issue_key_info(low_score, {"match_score": 0}) == "匹配分：0"
    assert gui._format_state_issue_key_info(
        manual_review,
        {"auto_greet_blocked_reason": "工作经验证据不足"},
    ) == "待确认：工作经验证据不足"
    assert gui._format_state_issue_key_info(manual_review, {}) == (
        "待确认：尚未形成明确资格结论"
    )


def test_delete_job_confirmation_describes_only_the_saved_job_deletion():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def delete_job(self):"):]
    block = block[:block.index("\n    def _build_current_job_rule_preview")]

    assert "删除后需要重新配置该岗位" in block
    assert "当前未保存修改" not in block


def test_run_summary_splits_status_prefix_for_fixed_summary_card():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.colors = {
        "success": "#0A0",
        "warning": "#FA0",
        "danger": "#D00",
        "text_primary": "#111",
        "text_secondary": "#666",
    }
    gui.run_summary_status_label = _FakeWidget()
    gui.run_summary_text_label = _FakeWidget()

    gui._set_run_summary(
        "[达到轮次上限] 筛选结果\n"
        "规则筛选：8 / 30 人通过\n\n"
        "扫描范围\n岗位A：达到 30 轮上限"
    )

    assert gui.run_summary_status_label.configs[-1] == {
        "text": "未确认扫描到底",
        "foreground": "#FA0",
    }
    assert gui.run_summary_text_label.text == (
        "筛选结果\n规则筛选：8 / 30 人通过\n\n"
        "扫描范围\n岗位A：达到 30 轮上限"
    )
    assert gui.run_summary_text_label.configs[-1] == {
        "state": "disabled",
        "height": 5,
        "foreground": "#111",
    }


def test_run_summary_uses_contact_queue_wording_and_actual_added_count():
    assert BossFilterGUI._replace_run_summary_contact_queue_count(
        "[完成] 筛选结果\n"
        "最终保留：8 人\n"
        "本轮打招呼：0 人",
        3,
    ) == (
        "[完成] 筛选结果\n"
        "最终保留：8 人\n"
        "本轮已加联系清单：3 人"
    )
    assert BossFilterGUI._replace_run_summary_contact_queue_count(
        "[完成] 筛选结果\n"
        "最终保留：8 人\n"
        "本轮已联系：0 人",
        2,
    ) == (
        "[完成] 筛选结果\n"
        "最终保留：8 人\n"
        "本轮已加联系清单：2 人"
    )


def test_terminal_progress_line_is_short_and_defers_details_to_summary():
    assert BossFilterGUI._format_terminal_progress_text(
        "[完成] " + "很长的摘要" * 100
    ) == "筛选完成，详细结果见下方摘要"
    assert BossFilterGUI._format_terminal_progress_text(
        "[达到轮次上限] " + "很长的摘要" * 100
    ) == "本轮处理完成；尚未确认扫描到底，详见下方摘要"
    assert BossFilterGUI._format_terminal_progress_text(
        "正在扫描候选人"
    ) == ""


def test_run_summary_caps_at_ten_rows_and_only_then_shows_scrollbar():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.run_summary_text_label = _FakeWidget()
    gui.run_summary_scrollbar = _FakePackFrame()

    gui._update_run_summary_text("\n".join(f"第{i}行" for i in range(12)), "#111")

    assert gui.run_summary_text_label.configs[-1]["height"] == 10
    assert gui.run_summary_scrollbar.winfo_manager() == "pack"

    gui._update_run_summary_text("简短摘要", "#666")

    assert gui.run_summary_text_label.configs[-1]["height"] == 3
    assert gui.run_summary_scrollbar.winfo_manager() == ""


def test_terminal_log_keeps_one_status_line_without_repeating_summary():
    final_desc = (
        "[达到轮次上限] 筛选结果\n规则筛选：3 / 118 人通过\n"
        "扫描范围\n达到 30 轮上限"
    )

    assert BossFilterGUI._format_terminal_log_text(final_desc) == (
        "本轮处理完成，扫描达到轮次上限"
    )

    source = Path("gui_main.py").read_text(encoding="utf-8")
    run_block = source[source.index("def run_worker"):]
    run_block = run_block[:run_block.index("\n    def on_closing")]
    assert "terminal_log = self._format_terminal_log_text(final_desc)" in run_block
    assert "LogRedirector(self.append_run_log)" in run_block
    assert "self.append_log(" not in run_block


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
    stats_block = stats_block[:stats_block.index("\n\n        card_gap")]

    assert '("strong_recommend", "强烈推荐"' in stats_block
    assert '("star", "强烈推荐"' not in stats_block


def test_stats_page_renames_total_candidates_to_passed_filter():
    """数据统计页第一张卡片展示通过筛选，并使用放大的原双人图案。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    stats_block = source[source.index("summary_items = [", source.index("def create_stats_page")):]
    stats_block = stats_block[:stats_block.index("\n\n        card_gap")]

    assert '("passed_filter", "通过筛选", "total"' in stats_block
    assert '"总候选人"' not in stats_block


def test_stats_page_greeted_uses_chat_icon_consistently():
    """数据统计页与首页、筛选结果页统一使用聊天气泡表示已打招呼。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    stats_block = source[source.index("summary_items = [", source.index("def create_stats_page")):]
    stats_block = stats_block[:stats_block.index("\n\n        card_gap")]

    assert '("chat", "已打招呼", "greeted"' in stats_block
    assert '("mail", "已打招呼", "greeted"' not in stats_block


def test_stats_page_review_entry_is_row_level_not_toolbar_button():
    """岗位复盘保留在岗位行双击/右键，避免统计页顶部按钮噪音。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    create_block = source[source.index("def create_stats_page"):]
    create_block = create_block[:create_block.index("\n    def _load_stats_candidates")]
    context_block = source[source.index("def _show_stats_context_menu"):]
    context_block = context_block[:context_block.index("\n    def _selected_stats_job_name")]

    assert "btn_job_review" not in create_block
    assert 'self.stats_tree.bind("<Double-Button-1>", lambda e: self._show_selected_job_review())' in create_block
    assert 'self.stats_tree.bind("<Button-3>", self._show_stats_context_menu)' in create_block
    assert "self.icons.button('chart', self.colors['primary'])" in context_block


def test_feedback_dialog_status_control_stays_inside_form_content():
    """反馈状态下拉框必须和标签在同一表单容器内，避免被 pack 到弹窗底部。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    feedback_block = source[source.index("def _mark_candidate_feedback"):]
    feedback_block = feedback_block[:feedback_block.index("\n    def _format_candidate_detail")]

    assert "status_combo = ttk.Combobox(\n            content," in feedback_block
    assert "status_combo.pack(anchor='w', fill='x'" in feedback_block
    assert "note_text.pack(anchor='w', fill='x'" in feedback_block
    assert 'text="结构化原因（可多选）",\n            font=(FONT_FAMILY, int(12 * self.font_scale))' in feedback_block
    assert 'status in {"误推", "误杀"} and not reasons' in feedback_block


def test_feedback_dialog_height_expands_to_keep_buttons_visible():
    """反馈弹窗必须服从实际内容请求高度，避免 1080p 缩放下裁掉底部按钮。"""
    source = Path("gui_main.py").read_text(encoding="utf-8")
    feedback_block = source[source.index("def _mark_candidate_feedback"):]
    feedback_block = feedback_block[:feedback_block.index("\n    def _format_candidate_detail")]

    assert "win.update_idletasks()" in feedback_block
    assert "win.winfo_reqheight() + int(12 * scale)" in feedback_block
    assert "dialog_height = max(" in feedback_block
    assert "_place_window_centered(win, int(440 * scale), dialog_height" in feedback_block
    assert "_place_window_centered(win, int(440 * scale), int(485 * scale)" not in feedback_block


def test_job_review_text_aggregates_structured_feedback_reasons():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidates = [
        {
            "job_name": "Java",
            "match_score": 80,
            "greet_sent": True,
            "followup_status": "已回复",
            "feedback_status": "误推",
            "feedback_reasons": ["技能不匹配", "规则过宽"],
        },
        {
            "job_name": "Java",
            "match_score": 40,
            "qualification_status": "rejected",
            "feedback_status": "误杀",
            "feedback_reasons": ["规则过窄", "AI 低估"],
        },
        {
            "job_name": "Java",
            "match_score": 60,
            "feedback_status": "合适",
            "feedback_reasons": ["行业经验不符"],
        },
    ]

    text = gui._build_job_review_text("Java", candidates)

    assert "Java 岗位复盘" in text
    assert "- 已反馈：3 人" in text
    assert "- 技能不匹配: 1" in text
    assert "- 规则过宽: 1" in text
    assert "- 规则过窄: 1" in text
    assert "- 误杀: 1" in text
    assert "- 反馈覆盖：3/3 人" in text
    assert "误推占比较高" not in text
    assert "规则过宽" in text
    assert "样本不足 5 条" in text
    assert "多人反馈" not in text


def test_job_review_dialog_opens_structured_workbench_with_shared_model():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidates = [{
        "name": "候选人甲",
        "match_score": 40,
        "feedback_status": "误杀",
        "feedback_reasons": ["规则过窄"],
    }]
    gui._selected_stats_job_name = Mock(return_value="Java")
    gui._load_stats_candidates = Mock(return_value=candidates)
    gui._build_job_review_model = Mock(return_value={"job_name": "Java"})
    gui._show_job_review_workbench = Mock()

    gui._show_selected_job_review()

    gui._build_job_review_model.assert_called_once_with("Java", candidates)
    gui._show_job_review_workbench.assert_called_once_with(
        "Java", candidates, {"job_name": "Java"}
    )


def test_job_review_workbench_uses_cards_funnel_and_contextual_actions():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    block = source[source.index("def _show_job_review_workbench"):]
    block = block[:block.index("\n    def _show_job_review_feedback_candidates")]

    assert '"筛选转化"' in block
    assert '"反馈质量"' in block
    assert '"问题洞察"' in block
    assert '"建议调整"' in block
    assert "if insight_sections:" in block
    assert 'text="查看反馈候选人"' in block
    assert 'text="前往岗位配置"' in block
    assert "if review['feedback_count'] < 5:" in block
    assert "title_trailing_builder=build_suggestion_action" in block
    assert "enumerate(review['suggestions'], start=1)" in block
    assert "anchor='w'" in block
    assert "int(9 * self.font_scale)" not in block
    assert "self._show_text_dialog(" not in block
    assert "root_height = self.root.winfo_height()" in block
    assert "height = min(int(760 * scale), root_height, int(area_height * 0.82))" in block
    assert "width, height = int(820 * scale), int(760 * scale)" in block


def test_job_review_suggestion_format_separates_heading_and_detail():
    assert BossFilterGUI._format_job_review_suggestion(
        "- 先积累反馈样本；没有结构化反馈时不建议调整岗位规则。"
    ) == (
        "先积累反馈样本",
        "没有结构化反馈时不建议调整岗位规则。",
    )
    assert BossFilterGUI._format_job_review_suggestion(
        "- 规则过宽：2/5 条；补充硬性约束。"
    ) == (
        "规则过宽",
        "2/5 条；补充硬性约束。",
    )


def test_job_review_model_keeps_low_score_false_negative_as_evidence():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    review = gui._build_job_review_model("Java", [{
        "match_score": 40,
        "feedback_status": "误杀",
        "feedback_reasons": ["规则过窄", "AI 低估"],
    }])

    assert review["qualified_count"] == 0
    assert review["feedback_count"] == 1
    assert review["false_negative_reasons"] == {"规则过窄": 1, "AI 低估": 1}
    assert review["ai_bias_counts"] == {"AI 低估": 1}


def test_job_review_feedback_drilldown_keeps_low_score_feedback_evidence():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._show_text_dialog = Mock()

    gui._show_job_review_feedback_candidates("Java", [{
        "name": "候选人甲",
        "match_score": 40,
        "feedback_status": "误杀",
        "feedback_reasons": ["规则过窄", "AI 低估"],
        "feedback_note": "完整简历符合要求",
    }])

    text = gui._show_text_dialog.call_args.args[1]
    assert "候选人甲｜40 分｜误杀" in text
    assert "规则过窄、AI 低估" in text
    assert "完整简历符合要求" in text


def test_job_review_can_navigate_to_matching_saved_job_config():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui.job_rules = {"Java 工程师": {}}
    gui.config_job_combo = _FakeCombo()
    gui.config_job_combo.set("其他岗位")
    gui.on_job_selected = Mock()

    def request_page(page_index, on_ready=None):
        assert page_index == 1
        on_ready()

    gui._request_sidebar_page = Mock(side_effect=request_page)

    gui._open_job_config_from_review("  Java   工程师 ")

    gui._request_sidebar_page.assert_called_once()
    assert gui.config_job_combo.get() == "Java 工程师"
    gui.on_job_selected.assert_called_once_with(None)


def test_job_review_only_reports_trends_after_minimum_feedback_sample():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidates = [
        {
            "job_name": "Java",
            "match_score": 80 - index,
            "feedback_status": "误推" if index < 3 else "合适",
            "feedback_reasons": ["规则过宽"] if index < 3 else ["其他"],
        }
        for index in range(5)
    ]

    text = gui._build_job_review_text("Java", candidates)

    assert "误推占比较高" in text
    assert "规则过宽：3/5 条" in text
    assert "样本不足" not in text


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
    assert 'self._create_page_header(\n            self.education_page,' in create_block
    assert 'scroll_frame = ttk.Frame(self.education_page' in create_block
    assert '_create_scroll_container(\n            scroll_frame,' in create_block
    assert "auto_hide_scrollbar=True" in create_block
    assert create_block.index("self._create_page_header(") < create_block.index(
        "self._create_scroll_container("
    )
    scroll_content = create_block[create_block.index("content = self.education_scrollable_frame"):]
    assert "self._create_page_header(" not in scroll_content
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
    assert "height=max(420, int(440 * self.dpi_scale * self.zoom_factor))" in create_block
    assert "workspace.pack_propagate(False)" in create_block


def test_mousewheel_routes_education_and_api_pages_to_correct_canvas():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    cocoa_block = source[
        source.index("page_canvas = {"):
        source.index("}.get(getattr(self, 'current_page_index', -1))")
    ]

    assert "PageIndex.EDUCATION: getattr(self, 'education_canvas', None)" in cocoa_block
    assert "PageIndex.SETTINGS: getattr(self, 'api_canvas', None)" in cocoa_block


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
    gui.education_queue_card = Mock()
    gui.education_queue_card.winfo_manager.return_value = ""
    gui.education_workspace = Mock()
    gui.education_queue_tree = Mock()
    gui.education_queue_scrollbar = Mock()
    gui.education_queue_scrollbar.winfo_manager.return_value = ""
    gui.education_remove_btn = Mock()
    gui.education_recognize_btn = Mock()
    gui.education_fill_btn = Mock()
    gui.education_current_id = "edu_1"
    gui.education_recognition_running = False
    gui.dpi_scale = 1.0
    gui.zoom_factor = 1.0

    gui._refresh_education_queue_summary()
    gui.education_file_var.set.assert_called_with("已导入 1 张证书")
    gui.education_queue_card.pack.assert_called_once_with(
        fill="x",
        before=gui.education_workspace,
        pady=(0, int(16 * gui.dpi_scale * gui.zoom_factor)),
    )
    gui.education_queue_tree.configure.assert_called_with(height=1)

    gui.education_queue_card.winfo_manager.return_value = "pack"
    gui.education_items = {"edu_1": {}, "edu_2": {}}
    gui._refresh_education_queue_summary()
    gui.education_file_var.set.assert_called_with("已导入 2 张证书，点击队列切换")
    gui.education_queue_tree.configure.assert_called_with(height=2)
    gui.education_queue_scrollbar.pack.assert_not_called()

    gui.education_queue_card.winfo_manager.return_value = "pack"
    gui.education_items = {}
    gui._refresh_education_queue_summary()
    gui.education_file_var.set.assert_called_with("尚未导入毕业证书")
    gui.education_queue_card.pack_forget.assert_called_once_with()


def test_education_queue_caps_visible_rows_and_scrolls_after_five_items():
    gui = object.__new__(BossFilterGUI)
    gui.education_items = {f"edu_{index}": {} for index in range(6)}
    gui.education_file_var = Mock()
    gui.education_queue_card = Mock()
    gui.education_queue_card.winfo_manager.return_value = "pack"
    gui.education_workspace = Mock()
    gui.education_queue_tree = Mock()
    gui.education_queue_scrollbar = Mock()
    gui.education_queue_scrollbar.winfo_manager.return_value = ""
    gui.education_canvas = Mock()
    gui.education_canvas._schedule_overflow_sync = Mock()
    gui.education_remove_btn = Mock()
    gui.education_recognize_btn = Mock()
    gui.education_fill_btn = Mock()
    gui.education_current_id = "edu_0"
    gui.education_recognition_running = False
    gui.dpi_scale = 1.0
    gui.zoom_factor = 1.0

    gui._refresh_education_queue_summary()

    gui.education_queue_tree.configure.assert_called_once_with(height=5)
    gui.education_queue_scrollbar.grid.assert_called_once_with()
    gui.education_canvas._schedule_overflow_sync.assert_called_once_with()
    gui.education_canvas.after.assert_called_once_with(
        16, gui.education_canvas._schedule_overflow_sync
    )


def test_education_queue_scrollbar_returns_after_delete_and_reimport():
    gui = object.__new__(BossFilterGUI)
    gui.education_file_var = Mock()
    gui.education_queue_card = Mock()
    gui.education_queue_card.winfo_manager.return_value = "pack"
    gui.education_workspace = Mock()
    gui.education_queue_tree = Mock()
    gui.education_queue_scrollbar = Mock()
    gui.education_canvas = Mock()
    gui.education_canvas._schedule_overflow_sync = Mock()
    gui.education_remove_btn = Mock()
    gui.education_recognize_btn = Mock()
    gui.education_fill_btn = Mock()
    gui.education_current_id = None
    gui.education_recognition_running = False
    gui.dpi_scale = 1.0
    gui.zoom_factor = 1.0

    gui.education_items = {f"edu_{index}": {} for index in range(6)}
    gui.education_queue_scrollbar.winfo_manager.return_value = ""
    gui._refresh_education_queue_summary()

    gui.education_items = {}
    gui.education_queue_scrollbar.winfo_manager.return_value = "grid"
    gui._refresh_education_queue_summary()

    gui.education_items = {f"new_{index}": {} for index in range(6)}
    gui.education_queue_scrollbar.winfo_manager.return_value = ""
    gui._refresh_education_queue_summary()

    assert gui.education_queue_scrollbar.grid.call_count == 2
    gui.education_queue_scrollbar.grid_remove.assert_called_once_with()


def test_education_scrollbar_is_visible_only_when_content_overflows():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    helper_block = source[
        source.index("def _create_scroll_container"):
        source.index("def _bind_mousewheel")
    ]

    assert "max(requested_height, viewport_height)" in helper_block
    assert "has_overflow = requested_height > viewport_height + 8" in helper_block
    assert 'scrollbar.pack(side="right", fill="y")' in helper_block
    assert "scrollbar.pack_forget()" in helper_block
    assert "canvas.yview_moveto(0)" in helper_block
    assert "canvas._schedule_overflow_sync = _schedule_sync" in helper_block


def test_education_queue_scrollbar_has_visible_local_style():
    source = Path("gui_main.py").read_text(encoding="utf-8")
    create_block = source[
        source.index("def create_education_page"):
        source.index("def _select_education_images")
    ]

    assert '"Education.Vertical.TScrollbar"' in create_block
    assert "width=max(14" in create_block
    assert "arrowsize=max(14" in create_block
    assert "('active', self.colors['text_secondary'])" in create_block
    assert "('pressed', self.colors['text_secondary'])" in create_block
    assert "('active', self.colors['primary'])" not in create_block
    assert 'style="Education.Vertical.TScrollbar"' in create_block


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


def test_resume_eval_error_callback_keeps_background_exception_until_ui_runs():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    candidate = {"geek_id": "g-error", "name": "张三", "job_name": "Java"}
    gui.all_candidates = [candidate]
    gui.api_config = {
        "api_provider": "qwen",
        "base_url": "https://example.test/v1",
    }
    gui.append_log = Mock()
    gui.refresh_results = Mock()
    gui._format_candidate_status = Mock(return_value="已导入简历")
    parent = Mock()
    tree = Mock()
    callbacks = []
    parent.after.side_effect = lambda _delay, callback: callbacks.append(callback)

    def run_thread(*_args, **kwargs):
        return types.SimpleNamespace(start=kwargs["target"])

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        resume_path = tmp_path / "resume.txt"
        resume_path.write_text("Java 开发经验 " * 10, encoding="utf-8")
        with patch("gui_main.filedialog.askopenfilename", return_value=str(resume_path)), \
                patch("gui_main.messagebox.askyesno", return_value=True), \
                patch("gui_main.messagebox.showerror") as showerror, \
                patch("gui_main.save_candidates_all"), \
                patch("gui_main.get_api_key", return_value="secret"), \
                patch("paths.get_base_dir", return_value=tmp_path), \
                patch("llm_eval.evaluate_with_resume", side_effect=RuntimeError("模型故障")), \
                patch("gui_main.threading.Thread", side_effect=run_thread):
            gui._import_resume(
                None,
                candidate=candidate,
                parent=parent,
                tree=tree,
                tree_item="row-1",
            )

            assert len(callbacks) == 1
            callbacks[0]()

    assert gui.append_log.call_args_list[-1].args == (
        "[简历评估] ✗ 张三 异常：模型故障",
    )
    showerror.assert_called_once_with(
        "评估异常",
        "二次评估出错：\n模型故障",
        parent=parent,
    )
    tree.set.assert_any_call("row-1", "status", "已导入简历")


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


def test_save_ai_eval_results_only_updates_the_evaluated_job_record():
    """同一候选人出现在多个岗位时，AI 结果只能写入本次评估的岗位。"""
    import json
    import tempfile
    from pathlib import Path

    gui = BossFilterGUI.__new__(BossFilterGUI)
    with tempfile.TemporaryDirectory() as tmpdir:
        cand_path = Path(tmpdir) / "candidates.json"
        disk_candidates = [
            {"geek_id": "same-geek", "name": "张三", "job_name": "Java", "match_score": 70},
            {"geek_id": "same-geek", "name": "张三", "job_name": "Python", "match_score": 68},
        ]
        cand_path.write_text(
            json.dumps(disk_candidates, ensure_ascii=False), encoding="utf-8"
        )
        gui.all_candidates = [dict(candidate) for candidate in disk_candidates]

        evaluated = dict(disk_candidates[0])
        evaluated.update({
            "llm_evaluated": True,
            "llm_adjustment": 10,
            "match_score": 80,
        })

        with patch.object(gui_main, "CANDIDATES_PATH", cand_path):
            gui._save_ai_eval_results([evaluated])

        saved = json.loads(cand_path.read_text(encoding="utf-8"))
        assert saved[0]["job_name"] == "Java"
        assert saved[0]["match_score"] == 80
        assert saved[0]["llm_evaluated"] is True
        assert saved[1] == disk_candidates[1]
        assert gui.all_candidates[0]["match_score"] == 80
        assert gui.all_candidates[1] == disk_candidates[1]


# === _candidate_has_ai_eval 守卫（regression: 已导入简历的候选人不得再跑一次评估，否则叠加两次调整）===

def test_candidate_has_ai_eval_helper():
    assert _candidate_has_ai_eval({'llm_evaluated': True}) is True
    assert _candidate_has_ai_eval({'resume_eval_adjustment': 5}) is True
    assert _candidate_has_ai_eval({'resume_eval_adjustment': 0}) is True  # 0 也是已评估
    assert _candidate_has_ai_eval({}) is False
    assert _candidate_has_ai_eval({'llm_evaluated': False}) is False
    assert _candidate_has_ai_eval({'llm_evaluated': None}) is False


def test_batch_ai_eval_menu_hides_without_eligible_candidates_and_counts_the_rest():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._ai_eval_in_progress = False
    gui._ai_evaluating_ids = set()

    assert gui._batch_ai_eval_menu_label([
        {'geek_id': 'a', 'llm_evaluated': True},
        {'geek_id': 'b', 'resume_eval_adjustment': 0},
    ]) == ""

    assert gui._batch_ai_eval_menu_label([
        {'geek_id': 'a', 'llm_evaluated': True},
        {'geek_id': 'fresh-1'},
        {'geek_id': 'fresh-2'},
    ]) == " 批量AI评估（2人）"

    gui._ai_eval_in_progress = True
    assert gui._batch_ai_eval_menu_label([
        {'geek_id': 'c'},
    ]) == ""


def test_batch_ai_eval_partition_reports_evaluated_and_running_candidates():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._ai_evaluating_ids = {'running'}
    fresh = {'geek_id': 'fresh', 'name': '未评估'}

    eligible, skipped = gui._partition_candidates_for_ai_eval([
        fresh,
        {'geek_id': 'done', 'name': '已完成', 'llm_evaluated': True},
        {'geek_id': 'running', 'name': '进行中'},
    ])

    assert eligible == [fresh]
    assert [(item['name'], item['reason']) for item in skipped] == [
        ('已完成', '已评估过'),
        ('进行中', '正在评估'),
    ]


def test_batch_ai_eval_groups_candidates_by_job_and_counts_all_skips():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._ai_eval_in_progress = False
    gui._ai_evaluating_ids = set()
    gui._ai_eval_results = {}
    gui.api_config = {
        'api_provider': 'qwen',
        'base_url': 'https://example.test/v1',
    }
    gui._get_job_rules_cached = Mock(return_value={
        'Java': {'original_requirement': 'Java 岗位要求', 'min_exp': 3},
        'Python': {'original_requirement': 'Python 岗位要求', 'min_exp': 2},
    })
    gui.refresh_results = Mock()
    gui.root = Mock()
    gui.root.after.return_value = 'timer-1'
    java = {'geek_id': 'java-1', 'name': '甲', 'job_name': 'Java'}
    python = {'geek_id': 'python-1', 'name': '乙', 'job_name': 'Python'}
    evaluated = {
        'geek_id': 'done-1', 'name': '丙', 'job_name': 'Java',
        'llm_evaluated': True,
    }

    with patch('security.get_api_key', return_value='secret'), \
            patch('gui_main.messagebox.showinfo') as showinfo, \
            patch('gui_main.messagebox.askyesno', return_value=True), \
            patch('gui_main.threading.Thread') as thread:
        gui._ai_eval_selected_candidates([java, python, evaluated])

    evaluation_groups = thread.call_args.kwargs['args'][0]
    assert [([candidate['geek_id'] for candidate in group], requirement) for group, requirement, _rule in evaluation_groups] == [
        (['java-1'], 'Java 岗位要求'),
        (['python-1'], 'Python 岗位要求'),
    ]
    assert gui._ai_eval_batch_summary['selected_count'] == 3
    assert gui._ai_eval_batch_summary['eval_count'] == 2
    assert gui._ai_eval_batch_summary['skipped'] == [
        {'name': '丙', 'reason': '已评估过'},
    ]
    assert gui._ai_evaluating_ids == {'java-1', 'python-1'}
    showinfo.assert_called_once()
    thread.return_value.start.assert_called_once()


def test_batch_ai_eval_does_not_invent_requirement_for_missing_job_config():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._get_job_rules_cached = Mock(return_value={})

    requirement, rule = gui._get_job_requirement_for_candidates([
        {'job_name': ' 已删除岗位 '},
    ])

    assert requirement == ""
    assert rule == {}


def test_batch_ai_eval_worker_uses_each_jobs_requirement_and_merges_results():
    gui = BossFilterGUI.__new__(BossFilterGUI)
    gui._ai_eval_total = 2
    gui._ai_eval_done = 0
    gui._ai_eval_in_progress = True
    gui._ai_evaluating_ids = {'java-1', 'python-1'}
    gui._ai_eval_results = {}
    gui._ai_eval_batch_summary = {
        'enabled': True, 'selected_count': 2, 'skipped': [],
        'success': [], 'failed': [],
    }
    gui._save_ai_eval_results = Mock()
    gui._on_ai_eval_complete = Mock()
    gui.root = Mock()
    java = {'geek_id': 'java-1', 'name': '甲'}
    python = {'geek_id': 'python-1', 'name': '乙'}
    groups = [
        ([java], 'Java 岗位要求', {'min_exp': 3}),
        ([python], 'Python 岗位要求', {'min_exp': 2}),
    ]

    def evaluate(candidates, requirement, *_args, **_kwargs):
        candidates[0]['llm_evaluated'] = True
        candidates[0]['llm_adjustment'] = 2 if requirement.startswith('Java') else -1

    with patch('llm_eval.evaluate_batch', side_effect=evaluate) as evaluate_batch:
        gui._do_ai_eval_batch(groups, {'api_provider': 'qwen'}, 'secret')

    assert [call.args[1] for call in evaluate_batch.call_args_list] == [
        'Java 岗位要求', 'Python 岗位要求',
    ]
    assert gui._ai_eval_batch_summary['success'] == [
        {'name': '甲', 'adjustment': 2},
        {'name': '乙', 'adjustment': -1},
    ]
    assert gui._ai_eval_batch_summary['failed'] == []
    assert gui._ai_eval_done == 2
    assert gui._ai_evaluating_ids == set()
    assert gui._save_ai_eval_results.call_count == 2
    gui.root.after.assert_called_once_with(0, gui._on_ai_eval_complete)


def test_ai_eval_batch_guard_uses_has_ai_eval_helper():
    """统一分流守卫必须识别一次评估和简历二次评估，避免重复叠加调整分。"""
    source = Path(gui_main.__file__).read_text(encoding="utf-8")
    skip_block = source[source.index("def _candidate_ai_eval_skip_reason"):]
    skip_block = skip_block[:skip_block.index("\n    def _partition_candidates_for_ai_eval")]
    selection_block = source[source.index("def _ai_eval_selected_candidates"):]
    selection_block = selection_block[:selection_block.index("\n    def _get_job_requirement_for_candidates")]

    assert "_candidate_has_ai_eval(candidate)" in skip_block
    assert "self._partition_candidates_for_ai_eval(candidates)" in selection_block


# === _resolve_rule_score 还原真实规则分（regression: 未跑一次评估却导入简历时撤回还原）===

def test_resolve_rule_score_uses_rule_score_when_present():
    assert _resolve_rule_score({'rule_score': 65}) == 65


def test_resolve_rule_score_fresh_candidate_uses_match_score():
    """无拆解的候选人：退到 match_score（从未被 AI 评估时 match_score 即规则分）。"""
    assert _resolve_rule_score({'match_score': 100}) == 100


def test_resolve_rule_score_clamp_not_inflated_by_part_sum():
    """规则分被 clamp 到 100 时（五分项和=105），返回 min(100,105)=100，不得返回 105。"""
    bd = {'base': 25, 'skill': 50, 'experience': 15, 'education': 10, 'preferred': 5, 'total': 100}
    assert _resolve_rule_score({'match_score': 100, 'score_breakdown': bd}) == 100


def test_resolve_rule_score_legacy_resume_via_part_sum():
    """简历评估过但 rule_score 未固化（旧数据）：从五分项求和还原（match_score 已被污染为 rule+resume_adj，不可用）。"""
    bd = {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0,
          'resume_adjustment': 5, 'total': 70}
    # match_score=70 是 rule+resume_adj；正确 rule = min(100, 25+30+5+5+0) = 65
    assert _resolve_rule_score({'match_score': 70, 'score_breakdown': bd}) == 65


def test_resolve_rule_score_legacy_resume_clamped_total():
    """resume 评估后 total 被 clamp 到 100 时，不得用 total-resume_adj（会算低）；用五分项求和。"""
    # rule=100（已 clamp，五分项和=105），resume +8 → total = min(100, 108) = 100
    bd = {'base': 25, 'skill': 50, 'experience': 15, 'education': 10, 'preferred': 5,
          'resume_adjustment': 8, 'total': 100}
    # 正确 rule = min(100, 105) = 100；total-resume_adj 会错算成 92
    assert _resolve_rule_score({'match_score': 100, 'score_breakdown': bd}) == 100


def test_resolve_rule_score_from_breakdown_when_missing():
    """异常兜底：rule_score/match_score 全缺失时从拆解五分项求和 + clamp。"""
    bd = {'base': 25, 'skill': 30, 'experience': 5, 'education': 5, 'preferred': 0}
    assert _resolve_rule_score({'score_breakdown': bd}) == 65


def test_resolve_rule_score_zero_when_nothing_available():
    assert _resolve_rule_score({}) == 0


def test_resolve_rule_score_handles_none_rule_score():
    """rule_score 显式为 None 时走分层回退。"""
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
