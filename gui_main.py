"""
BOSS 简历筛选器 - 图形界面版本
优化：浏览器状态检测 + 进度条 + 数据安全性 + UI 细节增强
"""

__version__ = "2.27"

import json
import logging
import math
import os
import re
import shutil
import sys
import threading
import time
import tkinter as tk
import queue
import random
import socket
import subprocess
import zipfile
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, font, ttk

import icons
from api_connectivity import probe_api_connectivity, probe_model_capability
from browser_connection import (
    classify_browser_url,
    connect_browser_address,
    is_debug_port_open,
    probe_page_url,
)
from browser_controller import BrowserController, BrowserRuntime
from candidate_controller import CandidateController, CandidatePersistence
from candidate_cleanup import clear_candidates_in_place
from data_maintenance_controller import DataMaintenanceController
from education_controller import EducationController
from settings_controller import SettingsController
import candidate_presenter
import candidate_diagnostics_presenter
import contact_presenter
import gui_candidate_actions
import gui_candidate_diagnostics
import gui_candidate_menus
import gui_candidate_review
import gui_candidate_state_dialogs
import gui_contact_queue
import gui_config_page
import gui_data_maintenance_dialogs
import gui_education_page
import gui_feedback_support
import gui_home_page
import gui_input_support
import gui_job_review
import gui_app_shell
import gui_layout_support
import gui_result_page
import gui_run_page
import gui_scroll_support
import gui_settings_page
import gui_model_catalog_dialog
import gui_stats_page
import gui_stats_detail
import gui_style_setup
import gui_widget_support
from model_catalog import analyze_model_catalog, fetch_model_catalog
from result_controller import (
    ResultController,
    ResultQuery,
    candidate_query_match,
    result_cache_key,
    result_sort_value,
)
from gui_app_shell import PAGE_SPECS, TRAFFIC_LIGHT_BASE_SIZE, PageIndex
import run_presenter
import stats_presenter
import ui_theme
from ui_windowing import (
    clamp as _clamp,
    get_windows_monitor_area as _get_windows_monitor_area,
    place_window_centered as _place_window_centered,
)
from subprocess_utils import hidden_subprocess

subprocess = hidden_subprocess(subprocess)

logger = logging.getLogger(__name__)


class _EscCloseToplevel(tk.Toplevel):
    """统一支持 Esc 关闭的 Toplevel（等同点击窗口 X 按钮）。

    通过 WM_DELETE_WINDOW 协议关闭，走各弹窗自己的关闭清理逻辑；
    弹窗内已显式绑定 <Escape> 的会覆盖本补丁，行为不受影响。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bind('<Escape>', self._on_escape_close, add='+')

    def _on_escape_close(self, _event=None):
        try:
            cmd = self.tk.call('wm', 'protocol', self._w, 'WM_DELETE_WINDOW')
            if cmd:
                self.tk.call(cmd)
            elif self.winfo_exists():
                self.destroy()
        except Exception:
            pass


tk.Toplevel = _EscCloseToplevel
from collections import Counter
from candidate_workflow import (
    CONTACTED_FOLLOWUP_STATUSES,
    apply_followup_state,
    build_daily_candidate_actions,
    candidate_can_manual_approve_contact,
    candidate_greet_skip_reason,
    default_next_followup_at,
    derive_candidate_decision,
    filter_candidates_by_result_view,
    format_followup_due_at,
    normalize_followup_at,
    summarize_daily_candidate_actions,
)
from candidate_state_diagnostics import (
    diagnose_candidate_states,
    summarize_candidate_state_diagnostics,
)
from ai_adapter import (
    classify_api_endpoint,
    has_endpoint_discovery,
    normalize_api_base_url,
)
from filtering import GENDER_VALUES
from contact_queue import (
    count_pending_contact_queue,
    load_contact_queue,
    load_pending_contact_queue_count,
    save_contact_queue,
)
from job_config_diagnostics import (
    diagnose_job_config,
    score_job_config_quality,
    summarize_job_config_diagnostics,
)
from job_config_store import load_job_config_snapshot, save_job_config_snapshot
from data_schema import legacy_job_uuid, new_job_uuid, normalize_job_uuid
from data_recovery import (
    create_backup_package,
    ensure_runtime_data_schema,
    inspect_backup,
    recover_pending_transaction,
    restore_backup,
)
from diagnostic_package import (
    DiagnosticPrivacyError,
    create_diagnostic_package,
)
from job_identity import job_names_equal, normalize_job_name
from constants import (
    API_CANDIDATE_LIMIT_DEFAULT,
    DOM_SCROLL_BATCH_MAX,
    DOM_SCROLL_BATCH_MIN,
    DOM_SCROLL_BATCH_PAUSE_CENTER,
    DOM_SCROLL_BATCH_PAUSE_SPREAD,
    DOM_SCROLL_DELAY_CENTER,
    DOM_SCROLL_DELAY_SPREAD,
    EMPTY_RECOMMEND_MARKS,
    GREET_CONTEXT_CAPTURE_LIMIT,
    MAX_ROUNDS_DEFAULT,
    SCORE_THRESHOLD_PASS,
    SCORE_THRESHOLD_RECOMMEND,
    SCORE_THRESHOLD_STRONG,
    GREET_UNCERTAIN_LIMIT,
)
from storage import (
    load_candidates_all,
    mark_candidate_greeted,
    mark_candidate_not_greeted,
    mutate_candidates_all,
    mutate_candidates_with_resume_cleanup,
    persist_candidate_greeted,
    persist_candidate_greeting_pending,
    read_candidates_snapshot,
    remove_candidates_all_with_resume_cleanup,
    repair_candidate_resume_storage,
    resolve_candidate_greeting_confirmation,
    update_candidate_records,
)
from resume_store import (
    RESUME_STATE_FIELDS,
    audit_managed_resumes,
)
from contact_controller import (
    ContactController,
    ContactNotice,
    ContactRunCounters,
    SendExceptionDecision,
)
from run_controller import RunController, RunTuning
from resume_import_service import (
    ResumeCandidateNotFoundError,
    ResumeCopyError,
    ResumePersistenceError,
    persist_candidate_resume,
)
from resume_parser import (
    ResumeContentTooShortError,
    ResumeParserDependencyError,
    ResumeTextReadError,
    UnsupportedResumeFormatError,
    parse_resume_text,
)
import gui_dialogs
from ui_messagebox import messagebox

# ========== 路径常量 - 解决相对路径问题 ==========
# PyInstaller --onefile 模式下 __file__ 指向临时解压目录，需特殊处理
from paths import (
    BASE_DIR,
    CONTACT_QUEUE_PATH,
    ensure_config_files,
    get_api_config_path,
)

CONFIG_PATH = BASE_DIR / "job_config.json"
CANDIDATES_PATH = BASE_DIR / "candidates_all.json"
CANDIDATES_XLSX_PATH = BASE_DIR / "candidates_all.xlsx"
CONFIG_BACKUP_PATH = BASE_DIR / "job_config.json.bak"
RUN_LOG_DIR = BASE_DIR / "logs"
RUNTIME_LOG_RETENTION_DAYS = 30
RUN_PREFERENCES_PATH = BASE_DIR / ".run_preferences.json"
MAINTENANCE_TIME_PREFERENCE_KEYS = {
    "backup": "last_data_backup_at",
    "restore": "last_data_restore_at",
    "diagnostic_export": "last_diagnostic_export_at",
}
API_CONFIG_PATH = get_api_config_path()
CHROME_DEBUG_PORT_FILE = BASE_DIR / ".chrome_debug_port"

_SETTINGS_CONTROLLER = SettingsController()
_DATA_MAINTENANCE_CONTROLLER = DataMaintenanceController()
_EDUCATION_CONTROLLER = EducationController()
_CONTACT_CONTROLLER = ContactController()
_RUN_CONTROLLER = RunController()


def _candidate_controller_for(host) -> CandidateController:
    """Return the host's persistence controller without importing GUI state back."""
    controller = getattr(host, "_candidate_controller", None)
    if controller is None:
        controller = CandidateController(
            CANDIDATES_PATH,
            BASE_DIR,
            CandidatePersistence(
                update_records=update_candidate_records,
                mutate_all=mutate_candidates_all,
                mutate_with_resume_cleanup=mutate_candidates_with_resume_cleanup,
                remove_with_resume_cleanup=remove_candidates_all_with_resume_cleanup,
                mark_greeted=mark_candidate_greeted,
                mark_not_greeted=mark_candidate_not_greeted,
            ),
        )
        host._candidate_controller = controller
    return controller


def _browser_controller_for(host) -> BrowserController:
    """Return the host's Chrome lifecycle controller with explicit runtime deps."""
    controller = getattr(host, "_browser_controller", None)
    if controller is None:
        controller = BrowserController(
            BASE_DIR,
            CHROME_DEBUG_PORT_FILE,
            BrowserRuntime(
                platform=sys.platform,
                environ=os.environ,
                exists=os.path.exists,
                expandvars=os.path.expandvars,
                expanduser=os.path.expanduser,
                which=shutil.which,
                socket_factory=socket.socket,
                popen=subprocess.Popen,
                run_process=subprocess.run,
                kill_process=os.kill,
                sleep=time.sleep,
                port_open=is_debug_port_open,
                connector=connect_browser_address,
            ),
        )
        host._browser_controller = controller
    return controller

FEEDBACK_STATUS_OPTIONS = ["合适", "误推", "误杀", "放弃"]
FEEDBACK_REASON_OPTIONS = [
    "技能不匹配",
    "行业经验不符",
    "年限判断偏差",
    "学历/学校不符",
    "薪资不合适",
    "地点不合适",
    "求职状态不合适",
    "AI 高估",
    "AI 低估",
    "规则过宽",
    "规则过窄",
    "简历信息不足",
    "其他",
]
FOLLOWUP_STATUS_OPTIONS = ["未沟通", "已打招呼", "已回复", "待约面", "已约面", "不合适", "已归档"]


def _export_candidate_state_diagnostics_report(summary_text, parent):
    """Write a user-selected candidate-state diagnostics report."""
    default_name = (
        f"candidate_state_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    )
    path = filedialog.asksaveasfilename(
        title="导出状态体检报告",
        defaultextension=".txt",
        initialfile=default_name,
        filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        parent=parent,
    )
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as report_file:
            report_file.write(summary_text)
        messagebox.showinfo("状态体检", "报告已导出。", parent=parent)
    except Exception as exc:
        messagebox.showerror("状态体检", f"导出失败：{exc}", parent=parent)


def _export_daily_candidate_actions_report(items, parent):
    """Write a user-selected daily candidate action report."""
    path = filedialog.asksaveasfilename(
        title="导出今日待办",
        defaultextension=".txt",
        initialfile=(
            f"candidate_daily_actions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        ),
        filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        parent=parent,
    )
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as report_file:
            report_file.write(summarize_daily_candidate_actions(items))
        messagebox.showinfo("今日待办", "报告已导出。", parent=parent)
    except Exception as exc:
        messagebox.showerror("今日待办", f"导出失败：{exc}", parent=parent)


# 服务商显示名称映射（内部键 -> 显示名称）
PROVIDER_DISPLAY = {
    "qwen": "通义千问 (Qwen)",
    "deepseek": "DeepSeek",
    "kimi": "月之暗面 (Kimi)",
    "zhipu": "智谱 (Zhipu)",
    "minimax": "MiniMax",
    "xiaomi": "小米 (Xiaomi)",
    "stepfun": "阶跃星辰 (StepFun)",
    "openai": "OpenAI",
    "anthropic": "Anthropic (Claude)",
    "custom": "自定义 (Custom)"
}
DISPLAY_TO_KEY = {v: k for k, v in PROVIDER_DISPLAY.items()}


def _api_provider_display_name(api_config: dict) -> str:
    """Return the provider name used consistently across model settings and run control."""
    provider = str(api_config.get("api_provider") or "")
    return PROVIDER_DISPLAY.get(provider, provider)


def _api_timeout_hint_text(api_config: dict) -> str:
    """Return a flat timeout note without nesting the provider's English-name brackets."""
    is_relay = bool(classify_api_endpoint(api_config)["is_relay"])
    model_name = str(api_config.get("model") or "")
    if is_relay:
        label = f"中转服务 / {model_name}" if model_name else "中转服务"
        default_timeout = 120
    else:
        provider_name = _api_provider_display_name(api_config)
        label = f"{provider_name} / {model_name}" if model_name else provider_name
        default_timeout = 60
    return f"{label} · 默认 {default_timeout} 秒"


# 首次运行时确保配置文件存在
ensure_config_files(BASE_DIR)


def get_api_key(provider: str, base_url: str | None = None) -> str | None:
    """按需加载系统钥匙串，避免 GUI 冷启动时初始化 keyring。"""
    from security import get_api_key as _get_api_key
    return _get_api_key(provider, base_url)


def save_api_key(provider: str, api_key: str, base_url: str | None = None) -> bool:
    """按需加载系统钥匙串并保存 API Key。"""
    from security import save_api_key as _save_api_key
    return _save_api_key(provider, api_key, base_url)


def delete_api_key(provider: str, base_url: str | None = None) -> bool:
    """按需加载系统钥匙串并删除 API Key。"""
    from security import delete_api_key as _delete_api_key
    return _delete_api_key(provider, base_url)


class TextDateEntry(ttk.Entry):
    """Fallback date entry used when tkcalendar is unavailable."""

    def __init__(self, master=None, **kwargs):
        kwargs.pop('date_pattern', None)
        kwargs.pop('showweeknumbers', None)
        kwargs.pop('locale', None)
        self._date_var = tk.StringVar()
        super().__init__(master, textvariable=self._date_var, **kwargs)
        self.set_date(datetime.now().date())

    def set_date(self, date_value):
        if isinstance(date_value, datetime):
            date_value = date_value.date()
        self._date_var.set(date_value.strftime("%Y-%m-%d"))

    def get_date(self):
        return datetime.strptime(self._date_var.get().strip(), "%Y-%m-%d").date()

def _optional_int_to_entry(value):
    """Format optional integer config values for editable entry/spinbox fields."""
    if value is None:
        return ""
    if value == "":
        return ""
    return str(value)


def _parse_optional_int_entry(value, field_name):
    """Parse an optional integer entry, returning None for blank input."""
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"{field_name}必须为数字") from exc


def _candidate_has_ai_eval(c: dict) -> bool:
    """候选人是否已有任意一轮 AI 评估（一次评估或简历二次评估）。

    用于批量 AI 评估前过滤——简历二次评估已替代一次评估的调整值，
    对已导入简历的候选人再跑一次评估会污染 rule_score（叠加两次调整）。
    """
    return bool(c.get('llm_evaluated')) or c.get('resume_eval_adjustment') is not None


def _filter_candidates_by_result_view(candidates, view):
    """Keep the historical GUI helper while using the shared decision model."""
    return filter_candidates_by_result_view(list(candidates), view)


# _resolve_rule_score 已挪到 llm_eval（evaluate_batch 与撤回流程共用，统一规则分还原逻辑）


def get_font_family():
    """获取字体 - 支持跨平台降级（实现已收口到 ui_theme）"""
    return ui_theme.FONT_FAMILY


def get_font_family_semibold():
    """获取 Semibold 字体变体 - 支持跨平台降级（实现已收口到 ui_theme）"""
    return ui_theme.FONT_FAMILY_SEMIBOLD


FONT_FAMILY = get_font_family()
FONT_FAMILY_SEMIBOLD = get_font_family_semibold()


# UI 配置常量（支持从 ui_config.json 覆盖）
_DEFAULT_UI_CONFIG = {
    'zoom_factor': 1.0,              # 额外放大系数（默认，Windows/Linux）；普通 1080P 保持原生比例
    'mac_zoom_factor': 0.9,          # macOS Retina 下 Tk 已有 DPI 缩放，避免界面过大
    'high_dpi_reduction': 0.50,      # 高 DPI（>130%）等比例缩减系数，避免 UI 整体过大
    'window_base_width': 1500,       # 窗口基础宽度
    'window_base_height': 950,       # 窗口基础高度
    'window_min_width': 1300,        # 最小窗口宽度
    'window_min_height': 750,        # 最小窗口高度
    'sidebar_width': 230,            # 侧边栏宽度
    'content_max_width': 1480,       # 普通功能页最大内容宽度，避免全屏后横向失衡
    'page_padding_x': 35,            # 页面左右边距
    'page_padding_y': 25,            # 页面上下边距
    'card_padding': 20,              # 卡片内边距
    'stat_icon_size': 64,            # 统计图标大小
    'font_scale_base': 20,           # 字体缩放基准
    'logo_padding_x': 25,            # Logo 区域左右边距
    'logo_padding_y': 35,            # Logo 区域上下边距
    'nav_padding': 15,               # 导航项内边距
    'label_frame_padding': 15,       # LabelFrame 默认内边距
    'font_size_title': 32,           # 标题字体大小
    'font_size_logo': 28,            # Logo 字体大小
    'treeview_rowheight': 28,        # Treeview 行高
    'text_height_large': 16,          # 大文本框高度（行）
    'text_height_small': 4,          # 小文本框高度（行）
    'listbox_height': 4,             # 列表框高度
    'treeview_height': 8,            # 树形控件高度
    'spinbox_exp_min': 0,            # 经验 Spinbox 最小值
    'spinbox_exp_max': 30,           # 经验 Spinbox 最大值
    'spinbox_rounds_min': 0,         # 轮次 Spinbox 最小值
    'spinbox_rounds_max': 9999,      # 轮次 Spinbox 最大值（虚拟上限）
    'icon_margin': 4,                # 图标圆形边距
    'combobox_width_job': 40,        # 岗位 Combobox 宽度
    'combobox_width_provider': 15,   # 服务商 Combobox 宽度
    'combobox_width_edu': 15,        # 学历 Combobox 宽度
    'entry_width_label': 10,         # 标签 Entry 宽度
    'entry_width_job': 12,           # 岗位名称 Entry 宽度
    'entry_width_model': 30,         # 模型名称 Entry 宽度
    'entry_width_api_key': 65,       # API Key Entry 宽度（与 Base URL 保持一致）
    'entry_width_url': 65,           # Base URL Entry 宽度
    'entry_width_required': 40,      # 必要条件 Entry 宽度
    'treeview_column_width_base_url': 400,  # Treeview 列宽
    'label_width_provider': 10,      # 服务商标签宽度
    'label_width_model': 10,         # 模型名称标签宽度
    'label_width_api_key': 10,       # API Key 标签宽度
    'label_width_url': 10,           # Base URL 标签宽度
    'font_size_status': 11,          # 状态提示字体大小
    'font_size_model_label': 14,     # 模型标签字体大小
}


def _load_ui_config() -> dict:
    """加载 UI 配置，支持从 ui_config.json 覆盖默认值。"""
    config_path = BASE_DIR / "ui_config.json"
    try:
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                return {**_DEFAULT_UI_CONFIG, **loaded}
    except (json.JSONDecodeError, OSError) as e:
        logging.warning("加载 ui_config.json 失败：%s，使用默认 UI 配置", e)
    return _DEFAULT_UI_CONFIG.copy()


UI_CONFIG = _load_ui_config()
RUN_SCROLL_WARNING_THRESHOLD = 100
RUN_API_PAGE_WARNING_THRESHOLD = max(
    1, (API_CANDIDATE_LIMIT_DEFAULT + 19) // 20
)
RUN_CONTACT_WARNING_THRESHOLD = GREET_CONTEXT_CAPTURE_LIMIT


def _load_run_preferences() -> dict:
    """加载本机运行偏好，例如最近一次运行岗位。"""
    try:
        if RUN_PREFERENCES_PATH.exists():
            with open(RUN_PREFERENCES_PATH, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                return loaded
    except (json.JSONDecodeError, OSError) as e:
        logging.warning("加载运行偏好失败：%s", e)
    return {}


def _save_run_preferences(preferences: dict) -> None:
    """保存本机运行偏好；失败不影响主流程。"""
    try:
        with open(RUN_PREFERENCES_PATH, 'w', encoding='utf-8') as f:
            json.dump(preferences, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logging.warning("保存运行偏好失败：%s", e)


def _open_containing_folder(file_path: str) -> None:
    """Open the parent folder of an exported file using the host file manager."""
    folder = Path(file_path).expanduser().parent
    if sys.platform == "win32":
        os.startfile(str(folder))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)], show_window=True)
    else:
        subprocess.Popen(["xdg-open", str(folder)], show_window=True)


def _format_storage_bytes(byte_count: int) -> str:
    """Format a non-negative byte count for storage audit summaries."""
    value = float(max(0, byte_count))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


def _calculate_effective_scale(dpi_scale, screen_width, screen_height, platform=sys.platform):
    """根据 DPI 和屏幕尺寸计算最终 UI 缩放比例。

    所有 UI 元素（窗口大小、字体、间距、图标）统一使用此缩放比例。
    """
    base_zoom = UI_CONFIG['mac_zoom_factor'] if platform == 'darwin' else UI_CONFIG['zoom_factor']
    effective_scale = dpi_scale * base_zoom

    # 高 DPI 显示器（>130%）：等比例缩减，避免 UI 整体过大
    # 4K@175% 下，Windows 自动缩放 1.75 倍，UI 元素视觉偏大。
    # 缩减到约 50%，使 UI 整体缩小到合理的视觉比例。
    if dpi_scale > 1.3:
        effective_scale *= UI_CONFIG.get('high_dpi_reduction', 0.7)

    # 低 DPI 大屏幕（如 4K@100%）：适当放大窗口利用空间
    if dpi_scale <= 1.1 and (screen_width >= 2400 or screen_height >= 1350):
        target_w = (screen_width * 0.64) / UI_CONFIG['window_base_width']
        target_h = (screen_height * 0.74) / UI_CONFIG['window_base_height']
        effective_scale = max(effective_scale, min(target_w, target_h))

    min_scale = 0.85
    result = _clamp(effective_scale, min_scale, 2.5)
    return result


def _calculate_system_dpi_aware_scale(dpi_scale, screen_width, screen_height):
    """Calculate UI scale when Windows is already rendering Tk at native DPI."""
    target_w = (screen_width * 0.62) / UI_CONFIG['window_base_width']
    target_h = (screen_height * 0.82) / UI_CONFIG['window_base_height']
    screen_target = min(target_w, target_h)
    dpi_target = dpi_scale * 0.88
    return _clamp(max(screen_target, dpi_target), 1.0, 1.70)


def _calculate_system_dpi_aware_font_scale(dpi_scale):
    """Return a restrained Tk font DPI scale for System DPI Aware mode."""
    return _clamp(dpi_scale * 0.62, 1.0, 1.20)


def _resolve_display_scale(tk_dpi_scale, physical_width, screen_width):
    """Return the display scale used for UI sizing.

    In System DPI Aware mode Tk already reports the real DPI scale, and
    physical_width / screen_width becomes ~1.0. Prefer Tk's DPI value there so
    a 4K high-DPI display is not treated as a 100% low-DPI large screen.
    """
    scales = []
    try:
        tk_dpi_scale = float(tk_dpi_scale)
        if tk_dpi_scale > 0:
            scales.append(tk_dpi_scale)
    except (TypeError, ValueError):
        pass

    try:
        physical_width = int(physical_width)
        screen_width = int(screen_width)
        if physical_width > 0 and screen_width > 0:
            scales.append(physical_width / screen_width)
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    return max(scales) if scales else 1.0


def _is_system_dpi_aware_scale(tk_dpi_scale, physical_width, screen_width):
    """Return True when Tk already sees physical pixels and native DPI."""
    try:
        tk_dpi_scale = float(tk_dpi_scale)
        physical_width = int(physical_width)
        screen_width = int(screen_width)
        if tk_dpi_scale <= 1.3 or physical_width <= 0 or screen_width <= 0:
            return False
        width_ratio = physical_width / screen_width
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    return 0.90 <= width_ratio <= 1.15


def _enable_high_dpi_awareness():
    """启用 System DPI Aware，避免 Windows 对 Tk 窗口做位图缩放。

    不启用 Per-Monitor DPI V2：Tk 8.6 在 V2 下坐标和布局容易错乱。
    System DPI Aware 能让文字保持清晰，同时风险明显小于 V2。
    """
    if sys.platform != 'win32':
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()
            return
        except (OSError, AttributeError):
            pass

        shcore = ctypes.windll.shcore
        # PROCESS_SYSTEM_DPI_AWARE = 1
        shcore.SetProcessDpiAwareness(1)
    except (ImportError, OSError, AttributeError):
        return


def _get_primary_physical_width() -> int:
    """获取主显示器的物理像素宽度（DPI Unaware 模式下绕过虚拟化）。

    EnumDisplaySettingsW(None, -1) 不受 DPI 虚拟化影响，返回真实物理像素。
    返回 0 表示获取失败。
    """
    if sys.platform != 'win32':
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        class DEVMODEW(ctypes.Structure):
            _fields_ = [
                ('dmDeviceName', wintypes.WCHAR * 32),
                ('dmSpecVersion', wintypes.WORD),
                ('dmDriverVersion', wintypes.WORD),
                ('dmSize', wintypes.WORD),
                ('dmDriverExtra', wintypes.WORD),
                ('dmFields', wintypes.DWORD),
                ('dmOrientation', wintypes.WORD),
                ('dmPaperSize', wintypes.WORD),
                ('dmPaperLength', wintypes.WORD),
                ('dmPaperWidth', wintypes.WORD),
                ('dmScale', wintypes.WORD),
                ('dmCopies', wintypes.WORD),
                ('dmDefaultSource', wintypes.WORD),
                ('dmPrintQuality', wintypes.WORD),
                ('dmColor', wintypes.WORD),
                ('dmDuplex', wintypes.WORD),
                ('dmYResolution', wintypes.WORD),
                ('dmTTOption', wintypes.WORD),
                ('dmCollate', wintypes.WORD),
                ('dmFormName', wintypes.WCHAR * 32),
                ('dmLogPixels', wintypes.WORD),
                ('dmBitsPerPel', wintypes.DWORD),
                ('dmPelsWidth', wintypes.DWORD),
                ('dmPelsHeight', wintypes.DWORD),
                ('dmDisplayFlags', wintypes.DWORD),
                ('dmDisplayFrequency', wintypes.DWORD),
            ]

        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(DEVMODEW)
        dm.dmDriverExtra = 0
        if ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
            return dm.dmPelsWidth
    except Exception:
        pass
    return 0


messagebox.set_window_placer(_place_window_centered)


def _place_main_window(root, monitor_area=None):
    """按启动目标显示器居中主窗口。"""
    if monitor_area is None:
        return _place_window_centered(root)

    screen_left, screen_top, screen_width, screen_height = monitor_area
    try:
        tk_screen_width = int(root.winfo_screenwidth())
        tk_screen_height = int(root.winfo_screenheight())
    except tk.TclError:
        tk_screen_width = 0
        tk_screen_height = 0

    # DPI Unaware 下 Win32 API 可能返回物理像素，而 Tk geometry 使用虚拟像素。
    # 两套坐标混用会导致 4K 高缩放环境下主窗口偏离中心。
    if (
        tk_screen_width > 0 and tk_screen_height > 0
        and (screen_width > tk_screen_width * 1.25 or screen_height > tk_screen_height * 1.25)
    ):
        # 用 Tk 虚拟屏幕尺寸居中，不再调用 Win32 API（避免工作区高度因任务栏产生偏差）
        return _place_window_centered(
            root,
            screen_width=tk_screen_width,
            screen_height=tk_screen_height,
        )

    return _place_window_centered(
        root,
        screen_left=screen_left,
        screen_top=screen_top,
        screen_width=screen_width,
        screen_height=screen_height,
    )


def _show_main_window_centered(root, monitor_area=None):
    """显示主窗口前后复位居中，避免启动首帧偏移闪烁。"""
    transparent_until_centered = sys.platform == 'win32'
    if transparent_until_centered:
        try:
            root.attributes("-alpha", 0.0)
        except tk.TclError:
            transparent_until_centered = False

    _place_main_window(root, monitor_area)
    root.deiconify()

    def reveal_after_centering():
        _place_main_window(root, monitor_area)
        if transparent_until_centered:
            try:
                root.attributes("-alpha", 1.0)
            except tk.TclError:
                pass

    root.after(50, reveal_after_centering)
    root.after(250, lambda: _place_main_window(root, monitor_area))


# macOS Tk 9.0+ 触控板滚动修复标记：
# Tk 9.0 的 Cocoa 后端不向 Canvas 派发触控板滚动事件（scrollWheel: 在 NSView 层被消费），
# 需要通过 ObjC Runtime swizzle 拦截并转发。Windows (Tk 8.6) 不受影响。
_NEED_COCOA_SCROLL_HOOK = sys.platform == 'darwin' and tk.TkVersion >= 9.0


class BossFilterGUI:
    """BOSS 简历筛选器图形界面 - 优化版"""

    def __init__(
        self,
        root,
        *,
        standalone_education: bool = False,
        education_api_config: dict | None = None,
        education_api_key_provider=None,
    ):
        self.root = root
        self.standalone_education = standalone_education
        self._education_api_key_provider = education_api_key_provider
        if standalone_education:
            self.root.title("学历证书核验助手")
        else:
            self.root.title(f"BOSS 简历筛选器 v{__version__} - 智能候选人筛选工具")

        # 获取屏幕尺寸（System DPI Aware 模式下为物理像素）
        self.root.update_idletasks()
        _screen_width = self.root.winfo_screenwidth()
        _screen_height = self.root.winfo_screenheight()

        try:
            _tk_dpi_scale = self.root.winfo_fpixels('1i') / 96.0
        except Exception:
            _tk_dpi_scale = 1.0

        # 检测主显示器的真实缩放倍数。
        # DPI Unaware：physical_width / screen_width 可还原 Windows 缩放倍数。
        # System DPI Aware：Tk 已报告真实 DPI，physical_width / screen_width 接近 1.0。
        _physical_width = _get_primary_physical_width()
        _display_scale = _resolve_display_scale(_tk_dpi_scale, _physical_width, _screen_width)

        # 用真实 display_scale 计算 effective_scale（所有 UI 元素统一使用此缩放比例）。
        # System DPI Aware 下不沿用 DPI Unaware 的 0.50 强缩减，否则界面会明显变小。
        _system_dpi_aware = _is_system_dpi_aware_scale(_tk_dpi_scale, _physical_width, _screen_width)
        if _system_dpi_aware:
            effective_scale = _calculate_system_dpi_aware_scale(_display_scale, _screen_width, _screen_height)
            font_dpi_scale = _calculate_system_dpi_aware_font_scale(_tk_dpi_scale)
            try:
                self.root.tk.call('tk', 'scaling', font_dpi_scale * 96.0 / 72.0)
            except tk.TclError:
                pass
        else:
            effective_scale = _calculate_effective_scale(_display_scale, _screen_width, _screen_height)

        # self.dpi_scale 保持 Tk 报告值（≈1.0），zoom_factor 承载全部缩放
        # 最终缩放 = dpi_scale × zoom_factor = effective_scale
        self.dpi_scale = _tk_dpi_scale
        self.zoom_factor = effective_scale / self.dpi_scale if self.dpi_scale else 1.0

        # macOS Tk 8.6 (Apple Silicon + Anaconda/Homebrew) 报告 DPI=72，
        # 未反映 Retina 2x 缩放，字体物理像素减半，需补偿。
        # Tk 8.6 (Intel/venv) 报告 DPI≈96，Tk 8.5 (Intel/系统) 报告 DPI=144，
        # 这两种字体渲染正常，不需要补偿。阈值 80 仅命中 DPI=72。
        if sys.platform == 'darwin':
            _tk_dpi_raw = self.root.winfo_fpixels('1i')
            self.font_boost = 1.65 if _tk_dpi_raw < 80 else 1.0
        else:
            self.font_boost = 1.0
        # font_scale 仅用于字体大小，布局/间距/图标/窗口/rowheight 仍用 dpi_scale × zoom_factor
        self.font_scale = self.dpi_scale * self.zoom_factor * self.font_boost
        # 同行备注与其直接关联控件之间的统一视觉间距。
        self.inline_note_gap = max(8, int(10 * self.dpi_scale * self.zoom_factor))

        # 初始化图标缓存（DPI 感知的高清图标）
        self.icons = icons.init(effective_scale)

        # 设置窗口图标（替换 tkinter 默认羽毛图标）
        self._set_window_icon()

        # Combobox 下拉列表字体在 setup_styles() 中统一设置

        # 窗口初始化完成后居中显示（使用 DPI 感知前捕获的屏幕尺寸）
        self.root.update_idletasks()
        window_width = int(UI_CONFIG['window_base_width'] * effective_scale)
        window_height = int(UI_CONFIG['window_base_height'] * effective_scale)

        screen_width = _screen_width
        screen_height = _screen_height
        if window_width > screen_width:
            window_width = int(screen_width * 0.9)
        if window_height > screen_height:
            window_height = int(screen_height * 0.85)
        placed_width, placed_height, _, _ = _place_window_centered(
            self.root,
            window_width,
            window_height,
        )
        min_width = min(int(UI_CONFIG['window_min_width'] * effective_scale), placed_width)
        min_height = min(int(UI_CONFIG['window_min_height'] * effective_scale), placed_height)
        self.root.minsize(min_width, min_height)

        # 运行状态
        self.is_running = False
        self._pending_run_request = None
        self.log_queue = queue.Queue()
        self.progress_queue = queue.Queue()  # 进度条队列
        self.confirm_queue = queue.Queue()  # 岗位切换确认队列
        self.ui_queue = queue.Queue()  # UI 更新队列（线程安全）
        self.stop_event = threading.Event()  # 停止信号
        self.greet_queue_items = []
        self.greet_queue_window = None
        self.greet_queue_tree = None
        self.greet_queue_group_tree = None
        self.greet_queue_summary_var = None
        self.greet_queue_detail_title_var = None
        self.greet_queue_detail_summary_var = None
        self.greet_queue_status_filter_var = None
        self.run_summary_frame = None
        self.run_summary_status_label = None
        self.run_summary_text_label = None
        self.run_summary_scrollbar = None
        self.greet_queue_selected_group = "全部"
        self.greet_queue_running = False
        self.greet_queue_paused = False
        self.greet_queue_preparing = False
        self.greet_queue_prepare_text = ""
        self.greet_queue_thread = None
        self._greet_queue_loaded = False
        self._greet_queue_status_vars = {}
        self.result_greet_queue_button = None
        self.result_greet_queue_badge = None
        self._result_contact_pending_count = 0

        # 浏览器状态
        self.browser_connected = False
        self.browser_page = None
        self.education_browser_page = None
        self.education_tabs: dict[str, object] = {}  # per-candidate 独立 tab（item_id → page）
        self._api_listener = None  # 推荐接口监听器（连接时启动，扫描时复用）
        self._browser_auto_check_id = None  # after() 回调 ID
        self._browser_status_text = ""
        self._browser_status_help_text = ""
        self._selectors_auto_checked = False  # 连接后选择器是否已自动检查
        self._selector_check_retry_pending = False  # 页面刷新时等待下一轮自动复查
        self._pending_manual_check = False  # 待处理的手动检测请求
        self._pending_chrome_restart = False  # 待处理的 Chrome 重启请求
        self._browser_non_target_checks = 0  # 连续未命中推荐页次数，过滤页面刷新时的 URL 抖动
        self._browser_connection_failures = 0  # 连续页面连接失败次数，避免把短断误报为 Chrome 未启动
        # DrissionPage 4.1.1.2 的 Chromium 单例初始化不是完整原子的：
        # 并发构造 ChromiumPage 时，后一个线程可能拿到尚无 _dl_mgr 的半初始化对象。
        self._browser_connection_lock = threading.Lock()
        self._education_browser_lock = threading.RLock()  # 序列化学信网 tab 的 DrissionPage 操作

        # 右键菜单引用列表（统一销毁）
        self._context_menus = []
        self.nav_labels = []
        self.nav_components = []

        # 加载配置
        self.job_rules = {}
        self.api_config = {}
        self._data_storage_error = ""
        self._data_recovery_report = {}
        self._data_migration_report = {}
        self._data_maintenance_running = False
        if standalone_education:
            self.api_config = dict(education_api_config or {})
        else:
            if os.environ.get("BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION") != "1":
                try:
                    self._data_recovery_report = recover_pending_transaction(BASE_DIR)
                    self._data_migration_report = ensure_runtime_data_schema(BASE_DIR)
                except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
                    self._data_storage_error = str(exc)
            self.load_config()
            # 首屏启动只读 api_config.json，不同步查询 keyring。
            # keyring 初始化在 Windows 上可能耗时明显，等用户进入模型配置或真正运行时再按需读取。
            self.load_api_config(resolve_keys=False)

        # 缓存：job_config 读取（mtime 未变则跳过磁盘 IO）
        self._job_rules_cache = None
        self._job_rules_mtime = 0
        self._run_preferences = _load_run_preferences()
        self._last_run_job_selection = str(
            self._run_preferences.get("last_run_job_name") or ""
        ).strip()
        # 缓存：Treeview 刷新（数据未变则跳过重建）
        self._result_tree_fingerprint = None
        self._result_last_job = None
        self._result_last_dates = None
        self._result_last_show_blacklist = False
        self._result_last_view = None
        self._acknowledged_job_config_warnings = set()
        self._stats_tree_fingerprint = None
        self._stats_last_job = None
        self._stats_last_time = None
        self._home_stats_fingerprint = None
        self._home_stats_last_job = None
        self._skills_tree_fingerprint = None
        self._required_list_fingerprint = None

        # AI评估状态跟踪（使用 geek_id 集合，refresh_results 后仍有效）
        self._ai_evaluating_ids = set()
        self._ai_eval_results = {}  # {geek_id: {'status': 'success'/'failed', 'message': '...', 'timestamp': ...}}
        self._ai_eval_batch_summary = None
        self._api_ui_config_mtime = None
        self._api_key_resolve_thread = None
        self._api_key_resolve_after_id = None
        self._api_key_cache = {}
        self._api_key_cache_lock = threading.Lock()
        self._pending_idle_tasks = set()
        self._pending_page_builds = set()
        self._page_width_policy_after_id = None
        self._highlighted_page_index = None

        # 设置样式
        gui_style_setup.setup_styles(self)

        modal_font_size = max(9, self.font_log[1])
        messagebox.set_ui_fonts(
            headline=(FONT_FAMILY, max(10, self.font_log[1]), 'bold'),
            message=(FONT_FAMILY, modal_font_size),
            button=(FONT_FAMILY, modal_font_size),
        )
        structured_message_size = max(9, modal_font_size - 2)
        messagebox.set_structured_ui_fonts(
            headline=(FONT_FAMILY, structured_message_size + 2, 'bold'),
            message=(FONT_FAMILY, structured_message_size),
            meta=(FONT_FAMILY, max(9, structured_message_size - 1)),
            button=(FONT_FAMILY, structured_message_size),
        )
        # 警告/错误弹窗自动带语义图标，提升可扫读性
        messagebox.icon_kinds = frozenset({"warning", "error"})

        # 设置 Combobox 下拉列表字体由 gui_style_setup 统一注册

        self.app_shell = gui_app_shell.AppShell(
            self,
            ui_config=UI_CONFIG,
            font_family=FONT_FAMILY,
            font_family_semibold=FONT_FAMILY_SEMIBOLD,
            version=__version__,
        )
        self.scroll_support = gui_scroll_support.ScrollSupport(self)
        self.input_support = gui_input_support.InputSupport(
            self,
            font_family=FONT_FAMILY,
        )
        self.feedback_support = gui_feedback_support.FeedbackSupport(self, font_family=FONT_FAMILY)
        self.widget_support = gui_widget_support.WidgetSupport(self, ui_config=UI_CONFIG)
        self.layout_support = gui_layout_support.LayoutSupport(
            self,
            ui_config=UI_CONFIG,
            font_family=FONT_FAMILY,
        )

        # 创建进度状态图标（依赖 self.colors，必须在 setup_styles 之后）
        status_icons = self.widget_support.create_status_icons()
        self._icon_status_ok, self._icon_status_fail = status_icons.ok, status_icons.fail

        # 创建界面
        if standalone_education:
            self.create_education_main_content()
        else:
            self.app_shell.create_sidebar()
            self.create_main_content()

        # 启动日志更新
        if not standalone_education:
            self.update_log()
            self._report_startup_data_state()

        # 启动 UI 更新队列处理（线程安全）
        self._process_ui_queue()

        # 结果页数据等用户进入结果页时再加载，避免启动时导入自动化链路。

        # 注册窗口关闭处理
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._setup_macos_reopen_handler()

        # 标记鼠标是否在 Text 控件上（用于 Cocoa scroll hook 跳过页面滚动）
        self._over_text_widget = False

        # 统一绑定滚轮事件 - 根据当前页面分发到对应的 Canvas
        self.root.bind_all("<MouseWheel>", self.scroll_support.on_mousewheel)
        # 普通 Frame/Label 不会主动获取焦点；全局点击用于收起结果页搜索框占位状态。
        self.root.bind_all("<Button-1>", self._on_global_left_click, add="+")
        # macOS/Linux 触控板可能生成 Button-4/5 事件
        if sys.platform != 'win32':
            self.root.bind_all("<Button-4>", self.scroll_support.on_mousewheel)
            self.root.bind_all("<Button-5>", self.scroll_support.on_mousewheel)

        # macOS Tk 9.0+: Cocoa 层拦截触控板滚动事件并转发给 Tk
        if _NEED_COCOA_SCROLL_HOOK:
            self.root.after(500, self.scroll_support.setup_cocoa_scroll_hook)

        # 全局快捷键（F5 / Ctrl+F / Delete / Ctrl+1~7）
        if not standalone_education:
            self._setup_global_shortcuts()

        # 更新模块含 requests 等重型依赖，延迟并在后台导入，避免阻塞 GUI 冷启动。
        if (
            not standalone_education
            and os.environ.get(
                "BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"
            ) != "1"
        ):
            self.root.after(12000, self._load_startup_updater)

    def _load_startup_updater(self):
        """后台加载更新模块，再回到 Tk 主线程启动更新检查。"""
        def _worker():
            try:
                import updater
            except Exception as exc:
                logger.warning("加载自动更新模块失败：%s", exc)
                return

            def _start():
                updater.auto_check_on_startup(self.root, delay_ms=0, gui=self)
                if getattr(sys, 'frozen', False):
                    updater.mark_update_success_and_cleanup()
                    updater.notify_previous_update_failure(self.root)

            self.run_on_ui(_start)

        threading.Thread(target=_worker, daemon=True).start()

    def _report_startup_data_state(self) -> None:
        """Log startup recovery/migration and surface write-blocking failures."""
        error = str(getattr(self, "_data_storage_error", "") or "").strip()
        if error:
            self.append_log(f"[数据安全] 初始化失败，已阻止数据写入：{error}")
            self.root.after(
                0,
                lambda detail=error: messagebox.show_failure(
                    "数据安全检查",
                    headline="数据安全检查未通过",
                    message=(
                        "候选人、岗位配置和联系清单未通过启动检查，"
                        "本次已禁止继续写入。"
                    ),
                    detail=detail,
                    notice=(
                        "请先从系统设置恢复有效备份，"
                        "或检查数据文件后重新启动。"
                    ),
                    parent=self.root,
                ),
            )
            return

        recovery = getattr(self, "_data_recovery_report", {}) or {}
        if recovery.get("recovered"):
            action = {
                "complete": "完成上次中断的数据事务",
                "rollback": "回退到上次有效恢复点",
                "cleanup": "清理已完成的数据事务",
            }.get(recovery.get("action"), "恢复上次数据事务")
            self.append_log(f"[数据安全] {action}")

        migration = getattr(self, "_data_migration_report", {}) or {}
        if migration.get("changed"):
            self.append_log("[数据安全] 已完成岗位、候选人与联系清单的一致性升级")
        unresolved = int(migration.get("unresolved_candidate_count") or 0)
        unresolved_queue = int(migration.get("unresolved_queue_count") or 0)
        if unresolved or unresolved_queue:
            self.append_log(
                "[数据安全] 有 "
                f"{unresolved} 条候选人记录、{unresolved_queue} 条联系清单记录"
                "无法自动关联到现有岗位，已原样保留"
            )

    def _ensure_data_storage_available(
        self,
        action: str,
        *,
        show_dialog: bool = True,
    ) -> bool:
        """Fail closed when startup recovery or schema validation failed."""
        error = str(getattr(self, "_data_storage_error", "") or "").strip()
        if not error:
            return True
        if show_dialog:
            messagebox.show_failure(
                "数据写入已阻止",
                headline=f"暂时无法{action}",
                message="数据安全检查尚未通过，本次操作没有执行。",
                detail=error,
                notice="可在系统设置中从有效备份恢复数据。",
                parent=getattr(self, "root", None),
            )
        return False

    def _setup_global_shortcuts(self):
        """注册全局快捷键：F5 刷新、Ctrl+F 搜索、Delete 移除选中、Ctrl+1~7 切换页面。"""
        self.root.bind('<F5>', lambda _e: self._shortcut_refresh())
        self.root.bind('<Control-f>', lambda _e: self._shortcut_focus_search())
        self.root.bind('<Delete>', lambda _e: self._shortcut_delete_selected())
        for key_number, page_index in enumerate(PageIndex, start=1):
            self.root.bind(
                f'<Control-Key-{key_number}>',
                lambda _event, index=page_index: self.app_shell.request_sidebar_page(index),
            )

    def _on_global_left_click(self, event) -> None:
        """点击输入控件外的界面区域时，清除单行输入框、下拉框和 Spinbox 焦点。"""
        search_entry = getattr(self, 'result_search_entry', None)
        target_widget = getattr(event, 'widget', None)
        if target_widget is None or target_widget is search_entry:
            return
        try:
            # focus_get() 会尝试解析 ttk.Combobox 的原生 popdown 路径，
            # Windows 上该路径不在 Tkinter 控件树中，会触发 KeyError。
            focused_path = str(self.root.tk.call('focus'))
            target_path = str(target_widget)
            if (
                not focused_path
                or focused_path == target_path
                or '.popdown' in focused_path
                or '.popdown' in target_path
            ):
                return

            input_classes = {'TCombobox', 'TSpinbox', 'Spinbox', 'TEntry', 'Entry', 'Text'}
            if str(target_widget.winfo_class()) in input_classes:
                return

            search_path = str(search_entry) if search_entry is not None else ''
            focused_class = str(self.root.tk.call('winfo', 'class', focused_path))
            if focused_path == search_path or focused_class in {
                'TCombobox', 'TSpinbox', 'Spinbox', 'TEntry', 'Entry'
            }:
                self.root.focus_set()
        except (tk.TclError, KeyError):
            return

    def _shortcut_refresh(self):
        """F5：只刷新当前页面拥有的本地数据视图。"""
        try:
            current_page = PageIndex(getattr(self, 'current_page_index', PageIndex.HOME))
            refresh_action = {
                PageIndex.HOME: self.refresh_home_stats,
                PageIndex.RESULTS: lambda: self.refresh_results(force=True),
                PageIndex.STATS: self.refresh_stats,
            }.get(current_page)
            if refresh_action is None:
                self._status_flash("当前页面无需刷新")
                return
            refresh_action()
            self._status_flash("已刷新")
        except Exception as exc:
            logger.warning("F5 刷新失败：%s", exc)

    def _shortcut_focus_search(self):
        """Ctrl+F：跳到筛选结果页并聚焦搜索框。"""
        try:
            def _focus_search() -> None:
                if hasattr(self, 'result_search_entry'):
                    self.result_search_entry.focus_set()
                    self.result_search_entry.select_range(0, 'end')

            self.app_shell.request_sidebar_page(PageIndex.RESULTS, on_ready=_focus_search)
        except Exception as exc:
            logger.warning("Ctrl+F 聚焦搜索失败：%s", exc)

    def _shortcut_delete_selected(self):
        """Delete：焦点在结果表且有选中行时移除选中候选人。"""
        try:
            focus = self.root.focus_get()
            if focus is None or not str(focus).startswith(str(self.result_tree)):
                return
            if not self.result_tree.selection():
                return
            self._remove_selected_candidates()
        except Exception as exc:
            logger.warning("Delete 移除失败：%s", exc)

    def _schedule_status_bar_reset(self, expected_text: str, duration_ms: int) -> None:
        """Reset a transient status only if no newer status has replaced it."""
        previous_after_id = getattr(self, '_status_flash_after_id', None)
        if previous_after_id is not None:
            try:
                self.root.after_cancel(previous_after_id)
            except tk.TclError:
                pass

        def _reset_status():
            self._status_flash_after_id = None
            if self.status_bar_left_var.get() == expected_text:
                self.status_bar_left_var.set("")

        self._status_flash_after_id = self.root.after(duration_ms, _reset_status)

    def _status_flash(self, text, duration_ms=2200):
        """右下角轻量提示 + 状态栏消息，自动消失（非模态）。"""
        try:
            if hasattr(self, 'status_bar_left_var'):
                self.status_bar_left_var.set(text)
                self._schedule_status_bar_reset(text, duration_ms)
            if getattr(self, '_status_flash_win', None) and self._status_flash_win.winfo_exists():
                self._status_flash_win.destroy()
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes('-topmost', True)
            label = tk.Label(
                win, text=text, font=self.font_label,
                background=self.colors.get('tooltip_bg', ui_theme.TOOLTIP_BG), foreground=self.colors.get('tooltip_fg', ui_theme.TOOLTIP_FG),
                padx=14, pady=8,
            )
            label.pack()
            self.root.update_idletasks()
            x = self.root.winfo_x() + self.root.winfo_width() - win.winfo_reqwidth() - 24
            y = self.root.winfo_y() + self.root.winfo_height() - win.winfo_reqheight() - 24
            monitor_area = _get_windows_monitor_area(win, self.root)
            if monitor_area is not None:
                left, top, monitor_width, monitor_height = monitor_area
                x = min(max(left, x), left + monitor_width - win.winfo_reqwidth())
                y = min(max(top, y), top + monitor_height - win.winfo_reqheight())
            win.geometry(f"{x:+d}{y:+d}")
            self._status_flash_win = win
            win.after(duration_ms, lambda: win.winfo_exists() and win.destroy())
        except Exception:
            pass

    def _remove_selected_candidates(self):
        """移除结果表当前选中的候选人（多选批量，单选走单人确认）。"""
        selection = self.result_tree.selection()
        if not selection:
            return
        if len(selection) == 1:
            self._remove_candidate(selection[0])
            return
        if not messagebox.ask_confirmation(
            "移除候选人",
            headline=f"移除选中的 {len(selection)} 名候选人？",
            message="这些记录将从当前结果和本地候选人数据中移除。",
            notice=(
                "无人继续引用的受管简历副本也会删除，共享副本保留；"
                "重新扫描时仍可能再次发现这些候选人。"
            ),
            yes_label="移除候选人",
            no_label="取消",
            dangerous=True,
            parent=self.root,
        ):
            return
        remove_keys = set()
        for sel_item in selection:
            candidate = self._find_candidate_by_tree_item(sel_item)
            if candidate and candidate.get('geek_id'):
                remove_keys.add(self._candidate_identity_key(candidate))
        self.result_tree_data = [
            candidate for candidate in self.result_tree_data
            if self._candidate_identity_key(candidate) not in remove_keys
        ]
        if remove_keys and CANDIDATES_PATH.exists():
            self._remove_candidate_records(
                lambda candidate: self._candidate_identity_key(candidate) in remove_keys,
            )
        for sel_item in selection:
            if self.result_tree.exists(sel_item):
                self.result_tree.delete(sel_item)
        self.refresh_home_stats()
        self._status_flash(f"已移除 {len(remove_keys)} 名候选人")

    def _setup_macos_reopen_handler(self):
        """点击 macOS Dock 图标时恢复主窗口。"""
        if sys.platform != 'darwin':
            return

        try:
            self.root.createcommand('tk::mac::ReopenApplication', self._restore_main_window)
        except tk.TclError:
            # 非 Aqua Tk 或旧版 Tk 可能不支持该 macOS 专用命令。
            pass

    def _restore_main_window(self):
        """恢复、置前并聚焦主窗口。"""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def create_main_content(self):
        """创建主内容区域"""
        # 主容器
        self.main_frame = ttk.Frame(self.root, style='Page.TFrame')
        self.main_frame.pack(side="left", fill="both", expand=True)
        self._last_page_pack_padx = None
        self._last_page_pack_pady = None

        # 创建页面容器
        self.pages_frame = ttk.Frame(self.main_frame, style='Page.TFrame')
        self.pages_frame.pack(
            fill="both",
            expand=True,
            padx=int(UI_CONFIG['page_padding_x'] * self.dpi_scale * self.zoom_factor),
            pady=int(UI_CONFIG['page_padding_y'] * self.dpi_scale * self.zoom_factor),
        )
        self.main_frame.bind(
            "<Configure>",
            lambda _event: self.app_shell.schedule_page_width_policy(),
            add="+",
        )

        self.home_page = None
        self.config_page = None
        self.api_config_page = None
        self.run_page = None
        self.result_page = None
        self.stats_page = None
        self.education_page = None

        self._page_loading_var = tk.StringVar(value="正在打开…")
        self._page_loading_frame = ttk.Frame(self.pages_frame, style='Page.TFrame')
        loading_inner = ttk.Frame(self._page_loading_frame, style='Page.TFrame')
        loading_inner.place(relx=0.5, rely=0.42, anchor='center')
        ttk.Label(
            loading_inner,
            textvariable=self._page_loading_var,
            font=self.font_section,
            foreground=self.colors['text_primary'],
            background=self.colors['bg_main'],
        ).pack(anchor='center')
        ttk.Label(
            loading_inner,
            text="首次打开正在准备页面，后续切换将直接显示",
            font=self.font_label,
            foreground=self.colors['text_secondary'],
            background=self.colors['bg_main'],
        ).pack(anchor='center', pady=(int(8 * self.dpi_scale), 0))

        # 首屏只创建首页，其他页面首次点击时再构建并缓存。
        self.create_home_page()

        # 全局状态栏：瞬时操作反馈（导出完成、已屏蔽等提示的落点）
        footer_bg = self.colors.get('bg_footer', ui_theme.BG_FOOTER)
        status_bar = tk.Frame(
            self.main_frame, background=footer_bg,
            highlightthickness=1, highlightbackground=self.colors['border'],
        )
        status_bar.pack(side="bottom", fill="x")
        status_font = (FONT_FAMILY, int(10 * self.font_scale))
        self.status_bar_left_var = tk.StringVar(value="")
        tk.Label(
            status_bar, textvariable=self.status_bar_left_var, font=status_font,
            foreground=self.colors['text_secondary'], background=footer_bg,
            anchor='w', padx=12, pady=3,
        ).pack(side="left", fill="x", expand=True)
        self._refresh_contact_queue_badge()

        # 默认显示首页（current_page_index 在 show_page_home 中已设置为 0）
        self.show_page_home()

    def create_education_main_content(self):
        """创建独立学历核验工具的单页内容。"""
        self.main_frame = ttk.Frame(self.root, style='Page.TFrame')
        self.main_frame.pack(fill="both", expand=True)
        self._last_page_pack_padx = None
        self.pages_frame = ttk.Frame(self.main_frame, style='Page.TFrame')
        self.pages_frame.pack(
            fill="both",
            expand=True,
            padx=int(UI_CONFIG['page_padding_x'] * self.dpi_scale * self.zoom_factor),
            pady=int(UI_CONFIG['page_padding_y'] * self.dpi_scale * self.zoom_factor),
        )
        self.home_page = None
        self.config_page = None
        self.api_config_page = None
        self.run_page = None
        self.result_page = None
        self.stats_page = None
        self.education_page = None
        self.create_education_page()
        self.show_page_education()

    def _defer_ui_work(
        self,
        key: str,
        callback: Callable[[], None],
        page_index: int | None = None,
    ) -> None:
        """Run coalesced UI work after redraw and skip it after navigation."""
        if key in self._pending_idle_tasks:
            return
        self._pending_idle_tasks.add(key)

        def _run():
            self._pending_idle_tasks.discard(key)
            if (
                page_index is not None
                and getattr(self, 'current_page_index', None) != page_index
            ):
                return
            try:
                callback()
            except tk.TclError:
                return

        self.root.after_idle(_run)

    def _create_result_date_entry(self, parent, **kwargs):
        """创建结果页日期控件；只在结果页构建时加载 tkcalendar。"""
        try:
            from tkcalendar import DateEntry
        except ImportError:
            DateEntry = TextDateEntry

        try:
            return DateEntry(parent, locale='zh_CN', **kwargs)
        except Exception:
            return DateEntry(parent, **kwargs)

    def create_home_page(self):
        """创建首页。"""
        widgets = gui_home_page.build_home_page(
            self,
            UI_CONFIG,
            run_page_index=PageIndex.RUN,
            result_page_index=PageIndex.RESULTS,
            config_page_index=PageIndex.CONFIG,
        )
        self._home_page_widgets = widgets
        self.home_page = widgets.page
        self.home_job_var = widgets.job_var
        self.home_job_combo = widgets.job_combo
        self.home_stats_vars = widgets.stats_vars
        self.home_stats_labels = widgets.stats_labels

    def create_config_page(self) -> None:
        """同步创建岗位配置页，供需要立即访问控件的内部流程使用。"""
        for _step in self._create_config_page_steps():
            pass

    def _create_config_page_steps(self) -> Iterator[None]:
        """创建岗位配置页面。"""
        yield from gui_config_page.build_config_page_steps(
            self,
            UI_CONFIG,
            font_family=FONT_FAMILY,
        )

    def create_api_config_page(self) -> None:
        """同步创建系统设置页，供需要立即访问控件的内部流程使用。"""
        for _step in self._create_api_config_page_steps():
            pass

    def _create_api_config_page_steps(self) -> Iterator[None]:
        """创建 API 配置页面"""
        # 创建带滚动条的页面
        self.api_config_page = ttk.Frame(self.pages_frame, style='Page.TFrame')

        # 创建可滚动容器（macOS Tk 9.0+ 用 Text，其他用 Canvas）
        self.api_canvas, self.api_scrollable_frame = (
            self.scroll_support.create_scroll_container(
                self.api_config_page,
                self.colors['bg_card'],
            )
        )

        yield
        yield from self._create_api_config_content_steps()


    def _on_rounds_mousewheel(self, event):
        """滚动轮次 Spinbox 的鼠标滚轮处理"""
        step = 10 if event.delta > 0 else -10
        try:
            current = int(self.rounds_var.get())
        except ValueError:
            current = 100
        new_val = current + step
        new_val = max(UI_CONFIG['spinbox_rounds_min'],
                      min(UI_CONFIG['spinbox_rounds_max'], new_val))
        self.rounds_var.set(str(new_val))

    @staticmethod
    def _coerce_int_setting(value, default: int, minimum: int, maximum: int) -> int:
        """Return a bounded integer for run-page numeric settings."""
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    def _advanced_run_risk_metrics(self) -> tuple[tuple[str, str], ...]:
        """Return enabled run settings that exceed their recommended values."""
        rounds_var = getattr(self, "rounds_var", None)
        rounds = self._coerce_int_setting(
            rounds_var.get() if rounds_var is not None else MAX_ROUNDS_DEFAULT,
            MAX_ROUNDS_DEFAULT,
            UI_CONFIG['spinbox_rounds_min'],
            UI_CONFIG['spinbox_rounds_max'],
        )
        metrics = []
        if rounds > RUN_SCROLL_WARNING_THRESHOLD:
            metrics.append((
                "滚动轮次",
                f"{rounds} 轮（建议不超过 {RUN_SCROLL_WARNING_THRESHOLD}）",
            ))

        api_enabled_var = getattr(self, "api_direct_enabled_var", None)
        if api_enabled_var is not None and bool(api_enabled_var.get()):
            api_pages_var = getattr(self, "api_direct_pages_var", None)
            api_pages = self._coerce_int_setting(
                api_pages_var.get() if api_pages_var is not None else RUN_API_PAGE_WARNING_THRESHOLD,
                RUN_API_PAGE_WARNING_THRESHOLD,
                1,
                20,
            )
            if api_pages > RUN_API_PAGE_WARNING_THRESHOLD:
                metrics.append((
                    "扫描增强",
                    f"最多读取 {api_pages} 页（建议不超过 {RUN_API_PAGE_WARNING_THRESHOLD}）",
                ))

        contact_enabled_var = getattr(
            self, "greet_context_capture_enabled_var", None
        )
        if contact_enabled_var is not None and bool(contact_enabled_var.get()):
            contact_limit_var = getattr(
                self, "greet_context_capture_limit_var", None
            )
            contact_limit = self._coerce_int_setting(
                (
                    contact_limit_var.get()
                    if contact_limit_var is not None
                    else RUN_CONTACT_WARNING_THRESHOLD
                ),
                RUN_CONTACT_WARNING_THRESHOLD,
                1,
                100,
            )
            if contact_limit > RUN_CONTACT_WARNING_THRESHOLD:
                metrics.append((
                    "后续联系",
                    f"最多准备 {contact_limit} 人（建议不超过 {RUN_CONTACT_WARNING_THRESHOLD}）",
                ))

        return tuple(metrics)

    def _confirm_advanced_run_settings(self) -> bool:
        """Confirm settings that materially increase BOSS page access."""
        metrics = self._advanced_run_risk_metrics()
        if not metrics:
            return True
        return messagebox.ask_confirmation(
            "确认高访问量设置",
            headline="部分运行参数高于建议值",
            message="继续运行会增加扫描耗时或页面访问量。",
            metrics=metrics,
            notice="建议返回调整；确认后仍可按当前设置运行。",
            parent=getattr(self, "root", None),
            yes_label="仍按当前设置运行",
            no_label="返回调整",
        )

    def _remember_run_job_selection(self, job_name: str) -> None:
        """Remember the latest concrete run-page job selection."""
        normalized = str(job_name or "").strip()
        if not normalized or normalized == "全部岗位":
            return
        self._last_run_job_selection = normalized
        preferences = dict(getattr(self, "_run_preferences", {}) or {})
        preferences["last_run_job_name"] = normalized
        self._run_preferences = preferences
        _save_run_preferences(preferences)

    def _resolve_default_run_job_selection(self, job_rules: dict) -> str:
        """Prefer the latest concrete run job, then the config-page job, then first saved job."""
        if not job_rules:
            return "全部岗位"

        remembered = str(getattr(self, "_last_run_job_selection", "") or "").strip()
        if remembered in job_rules:
            return remembered

        if hasattr(self, "config_job_combo"):
            selected_config_job = str(self.config_job_combo.get() or "").strip()
            if selected_config_job in job_rules:
                return selected_config_job

        return next(iter(job_rules.keys()), "全部岗位")

    def _sync_run_job_combo_values(self, job_rules: dict | None = None, *, prefer_current: bool = True) -> str:
        """Refresh run-page job options and choose a concrete default when possible."""
        if job_rules is None:
            job_rules = self._get_job_rules_cached()
        jobs = ["全部岗位"] + list(job_rules.keys())
        self.job_combo['values'] = jobs

        current = str(self.job_select_var.get() or "").strip()
        if prefer_current and current in jobs and current:
            return current

        selected = self._resolve_default_run_job_selection(job_rules)
        self.job_select_var.set(selected)
        return selected


    def _create_api_config_content_steps(self) -> Iterator[None]:
        """创建系统设置页面内容。"""
        yield from gui_settings_page.build_settings_content_steps(
            self,
            UI_CONFIG,
            font_family=FONT_FAMILY,
            font_family_semibold=FONT_FAMILY_SEMIBOLD,
            traffic_light_base_size=TRAFFIC_LIGHT_BASE_SIZE,
            provider_display=PROVIDER_DISPLAY,
            display_to_key=DISPLAY_TO_KEY,
        )

    def load_api_config_to_ui(self, resolve_key=True):
        """加载 API 配置到 UI 控件"""
        if not hasattr(self, 'api_config') or not self.api_config:
            return

        # 确保变量已初始化
        if not hasattr(self, 'api_provider_var'):
            return

        # 将内部键转换为显示名称（兼容旧配置）
        provider_key = self.api_config.get("api_provider", "qwen")
        provider_display = self.PROVIDER_DISPLAY.get(provider_key, provider_key)
        self.api_provider_var.set(provider_display)
        # API Key 从 keyring 读取（api_config.json 不含明文）。首次打开设置页时不阻塞 UI，
        # 后台线程会在读取完成后回填。
        if resolve_key:
            _base_url = self.api_config.get("base_url", "")
            saved_key = self._get_api_key_cached(provider_key, _base_url)
            self.api_key_var.set(saved_key if saved_key else "")
        else:
            self.api_key_var.set(self.api_config.get("api_key", ""))
        self.api_base_url_var.set(self.api_config.get("base_url", ""))
        self.api_model_var.set(self.api_config.get("model", ""))

        # 超时设置（字段缺失时按中转/非中转取默认值）
        if hasattr(self, 'llm_read_timeout_var'):
            _is_relay = self._is_relay_endpoint_for_timeout()
            _default_read = 120 if _is_relay else 60
            self.llm_read_timeout_var.set(self.api_config.get("llm_read_timeout") or _default_read)
            # 刷新提示文案
            timeout_hint_label = getattr(self, '_timeout_hint_label', None)
            if timeout_hint_label is not None and timeout_hint_label.winfo_exists():
                ai_eval_var = getattr(self, 'ai_eval_var', None)
                _hint = (
                    _api_timeout_hint_text(self.api_config)
                    if ai_eval_var is not None and ai_eval_var.get()
                    else "开启 AI 辅助评估后可设置"
                )
                timeout_hint_label.config(text=_hint)

        # 更新当前使用模型显示
        self.update_current_model_display()

        # 加载已保存的模型列表
        self.load_saved_models_to_tree()

    @staticmethod
    def _format_backup_summary(result: dict) -> str:
        """Return a privacy-safe summary for backup and restore dialogs."""
        return _DATA_MAINTENANCE_CONTROLLER.format_backup_summary(result)

    @staticmethod
    def _backup_summary_metrics(result: dict) -> tuple[tuple[str, str], ...]:
        """Return compact, privacy-safe metrics for backup result dialogs."""
        return _DATA_MAINTENANCE_CONTROLLER.backup_summary_metrics(result)

    @staticmethod
    def _format_maintenance_time(value) -> str:
        """Format one persisted local activity timestamp for compact UI notes."""
        return _DATA_MAINTENANCE_CONTROLLER.format_time(value)


    def _remember_maintenance_success(
        self,
        activity: str,
        *,
        when: datetime | None = None,
    ) -> str:
        """Persist the latest successful local data-maintenance timestamp."""
        preferences, value = _DATA_MAINTENANCE_CONTROLLER.remember_success(
            getattr(self, "_run_preferences", {}) or {},
            activity,
            when=when,
        )
        self._run_preferences = preferences
        _save_run_preferences(preferences)
        return value

    def _data_backup_note_text(
        self,
        *,
        backup_at=None,
        restore_at=None,
        backup_summary: str = "",
        restore_summary: str = "",
    ) -> str:
        """Build the two-line backup/restore activity note."""
        return _DATA_MAINTENANCE_CONTROLLER.backup_note(
            getattr(self, "_run_preferences", {}) or {},
            backup_at=backup_at,
            restore_at=restore_at,
            backup_summary=backup_summary,
            restore_summary=restore_summary,
        )

    def _diagnostic_export_note_text(
        self,
        *,
        exported_at=None,
        summary: str = "",
    ) -> str:
        """Build the latest successful diagnostic-export note."""
        return _DATA_MAINTENANCE_CONTROLLER.diagnostic_note(
            getattr(self, "_run_preferences", {}) or {},
            exported_at=exported_at,
            summary=summary,
        )

    def _open_export_location(self, file_path: str) -> None:
        """Open an exported file's folder and report file-manager failures."""
        try:
            _open_containing_folder(file_path)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            messagebox.show_failure(
                "打开文件位置",
                headline="无法打开所在文件夹",
                message="文件已经生成，可以稍后从保存位置手动打开。",
                detail=str(exc),
                parent=getattr(self, "root", None),
            )

    def _data_operation_busy(self) -> bool:
        """Prevent backup/restore from racing with active candidate writes."""
        return _DATA_MAINTENANCE_CONTROLLER.operation_busy(
            scan_running=bool(getattr(self, "is_running", False)),
            contact_running=bool(getattr(self, "greet_queue_running", False)),
            contact_preparing=bool(getattr(self, "greet_queue_preparing", False)),
            maintenance_running=bool(
                getattr(self, "_data_maintenance_running", False)
            ),
        )

    def _set_data_backup_status(self, text: str) -> None:
        status_var = getattr(self, "data_backup_status_var", None)
        if status_var is not None:
            status_var.set(text)

    def _show_resume_storage_audit(self) -> None:
        """Audit resume storage and optionally repair it after confirmation."""
        if self._data_operation_busy():
            messagebox.showwarning(
                "暂时不能体检",
                "扫描、联系或数据维护正在进行，请结束后再执行体检。",
                parent=self.root,
            )
            return
        try:
            report = _DATA_MAINTENANCE_CONTROLLER.audit_resumes(
                CANDIDATES_PATH,
                BASE_DIR,
                reader=read_candidates_snapshot,
                auditor=audit_managed_resumes,
            )
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            messagebox.show_failure(
                "简历存储体检",
                headline="体检未完成",
                message="无法可靠核对候选人引用与受管简历目录。",
                detail=str(exc),
                notice="本次体检没有修改或恢复任何数据。",
                parent=self.root,
            )
            return

        abnormal_references = (
            report.missing_reference_count
            + report.unmanaged_reference_count
            + report.stale_metadata_count
        )
        metrics = (
            ("候选人引用", str(report.reference_count)),
            ("有效引用", str(report.valid_reference_count)),
            ("异常状态", str(abnormal_references)),
            (
                "孤立文件",
                f"{report.orphan_file_count} / "
                f"{_format_storage_bytes(report.orphan_bytes)}",
            ),
        )
        if not report.issue_count:
            messagebox.show_result(
                "简历存储体检",
                headline="简历引用与受管目录一致",
                message=(
                    f"受管目录共 {report.managed_file_count} 个文件，"
                    f"占用 {_format_storage_bytes(report.managed_bytes)}。"
                ),
                metrics=metrics,
                notice="本次仅执行只读体检，没有修改任何数据或文件。",
                notice_kind="info",
                parent=self.root,
            )
            return

        confirmed = messagebox.ask_confirmation(
            "简历存储体检",
            headline="发现需要处理的简历存储问题",
            message=(
                f"缺失引用 {report.missing_reference_count} 条，"
                f"非受管引用 {report.unmanaged_reference_count} 条，"
                f"无文件的残留评估状态 {report.stale_metadata_count} 条。"
            ),
            metrics=metrics,
            notice=(
                "修复会清除失效引用及对应简历评估状态，并删除无人引用的"
                "受管副本；目录外文件和仍被其他记录引用的副本不会删除。"
            ),
            yes_label="修复并清理",
            no_label="暂不处理",
            dangerous=True,
            parent=self.root,
        )
        if not confirmed:
            return

        self._data_maintenance_running = True
        try:
            repair_result = _DATA_MAINTENANCE_CONTROLLER.repair_resumes(
                CANDIDATES_PATH,
                BASE_DIR,
                repairer=repair_candidate_resume_storage,
                reader=read_candidates_snapshot,
                auditor=audit_managed_resumes,
            )
            repair = repair_result.repair
            cleanup = repair_result.cleanup
            remaining = repair_result.remaining
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            messagebox.show_failure(
                "简历存储体检",
                headline="修复未完成",
                message="候选人数据和简历目录未能完成一致性处理。",
                detail=str(exc),
                notice="请重新运行体检确认当前状态。",
                parent=self.root,
            )
            return
        finally:
            self._data_maintenance_running = False

        self._result_tree_fingerprint = None
        self._stats_tree_fingerprint = None
        self._home_stats_fingerprint = None
        if hasattr(self, "result_tree"):
            self.refresh_results(force=True)
        if hasattr(self, "stats_tree"):
            self.refresh_stats()
        if hasattr(self, "home_stats_labels"):
            self.refresh_home_stats()

        incomplete = bool(cleanup.failure_count or remaining.issue_count)
        messagebox.show_result(
            "简历存储体检",
            headline=(
                "简历存储仍有未处理项目"
                if incomplete
                else "简历存储已完成修复"
            ),
            message=(
                "失效引用及残留评估状态已按当前候选人数据重新核对。"
            ),
            metrics=(
                ("修复候选人", str(repair.repaired_candidate_count)),
                ("删除孤立文件", str(cleanup.deleted_file_count)),
                ("释放空间", _format_storage_bytes(cleanup.reclaimed_bytes)),
                ("剩余问题", str(remaining.issue_count)),
            ),
            notice=(
                f"有 {cleanup.failure_count} 项受管简历清理失败，"
                "可关闭占用文件的程序后重试。"
                if cleanup.failure_count
                else "目录外文件和共享受管副本均未删除。"
            ),
            notice_kind="warning" if incomplete else "success",
            parent=self.root,
        )

    def _export_data_backup(self) -> None:
        """Export one verified plaintext ZIP from the current runtime data."""
        if self._data_operation_busy():
            messagebox.showwarning(
                "暂时不能备份",
                "扫描、联系或数据维护正在进行，请结束后再导出备份。",
                parent=self.root,
            )
            return
        if not self._ensure_data_storage_available("导出数据备份"):
            return
        destination = filedialog.asksaveasfilename(
            title="导出数据备份",
            defaultextension=".zip",
            initialfile=f"BOSS数据备份-{datetime.now():%Y%m%d-%H%M%S}.zip",
            filetypes=[("ZIP 备份", "*.zip")],
            parent=self.root,
        )
        if not destination:
            return
        self._data_maintenance_running = True
        self._set_data_backup_status("正在生成并校验备份…")
        try:
            result = _DATA_MAINTENANCE_CONTROLLER.create_backup(
                BASE_DIR,
                destination,
                creator=create_backup_package,
            )
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            self._set_data_backup_status("备份失败")
            messagebox.show_failure(
                "数据备份",
                headline="备份未完成",
                message="没有生成可用备份。",
                detail=str(exc),
                parent=self.root,
            )
            return
        finally:
            self._data_maintenance_running = False
        summary = self._format_backup_summary(result)
        backup_at = self._remember_maintenance_success("backup")
        self._set_data_backup_status(
            self._data_backup_note_text(
                backup_at=backup_at,
                backup_summary=summary,
            )
        )
        self.append_operation_log(f"[数据安全] 已导出数据备份：{summary}")
        action = messagebox.show_result(
            "数据备份",
            headline="备份已完成",
            metrics=self._backup_summary_metrics(result),
            file_path=str(result["path"]),
            notice="此 ZIP 未加密，请妥善保管。",
            parent=self.root,
        )
        if action == "open_location":
            self._open_export_location(str(result["path"]))

    def _restore_data_backup(self) -> None:
        """Validate and transactionally restore a user-selected backup ZIP."""
        if self._data_operation_busy():
            messagebox.showwarning(
                "暂时不能恢复",
                "扫描、联系或数据维护正在进行，请结束后再恢复数据。",
                parent=self.root,
            )
            return
        source = filedialog.askopenfilename(
            title="选择数据备份",
            filetypes=[("ZIP 备份", "*.zip")],
            parent=self.root,
        )
        if not source:
            return
        try:
            preview = _DATA_MAINTENANCE_CONTROLLER.inspect_backup(
                source,
                inspector=inspect_backup,
            )
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            messagebox.show_failure(
                "恢复数据备份",
                headline="这个备份无法使用",
                message="完整性或数据格式检查未通过，当前数据没有修改。",
                detail=str(exc),
                parent=self.root,
            )
            return

        summary = self._format_backup_summary(preview)
        if not messagebox.ask_confirmation(
            "恢复数据备份",
            headline="恢复这份数据备份？",
            message=(
                "将替换当前候选人、岗位配置和联系清单，"
                "恢复备份中的简历副本，并删除恢复后无人引用的旧受管副本。"
            ),
            metrics=self._backup_summary_metrics(preview),
            notice="执行前会自动保存当前数据恢复点。",
            yes_label="开始恢复",
            no_label="取消",
            parent=self.root,
        ):
            return

        self._data_maintenance_running = True
        self._set_data_backup_status("正在校验并恢复数据…")
        try:
            result = _DATA_MAINTENANCE_CONTROLLER.restore_backup(
                BASE_DIR,
                source,
                restorer=restore_backup,
            )
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            self._set_data_backup_status("恢复失败，当前数据已保留或自动回退")
            messagebox.show_failure(
                "恢复数据备份",
                headline="恢复未完成",
                message=(
                    "当前数据已保留；程序会在下次启动时继续完成"
                    "或自动回退。"
                ),
                detail=str(exc),
                parent=self.root,
            )
            return
        finally:
            self._data_maintenance_running = False

        self._data_storage_error = ""
        self._data_recovery_report = {}
        self._data_migration_report = result
        self._job_rules_cache = None
        self._job_rules_mtime = 0
        self.load_config()
        if hasattr(self, "config_job_combo"):
            self.config_job_combo["values"] = list(self.job_rules.keys())
            selected = next(iter(self.job_rules), "")
            self.config_job_combo.set(selected)
            if selected:
                self.load_job_to_form(self.job_rules[selected])
        if hasattr(self, "job_combo"):
            self._sync_run_job_combo_values(self.job_rules, prefer_current=False)
        self.greet_queue_items = []
        self._greet_queue_loaded = False
        self._refresh_contact_queue_badge()
        self._result_tree_fingerprint = None
        self._stats_tree_fingerprint = None
        self._home_stats_fingerprint = None
        if hasattr(self, "result_tree"):
            self.refresh_results(force=True)
        if hasattr(self, "stats_tree"):
            self.refresh_stats()
        if hasattr(self, "home_stats_labels"):
            self.refresh_home_stats()

        summary = self._format_backup_summary(result)
        unresolved = (
            int(result.get("unresolved_candidate_count") or 0)
            + int(result.get("unresolved_queue_count") or 0)
        )
        suffix = f"，另有 {unresolved} 条记录未自动关联岗位" if unresolved else ""
        resume_cleanup_count = int(result.get("resume_cleanup_count") or 0)
        resume_cleanup_bytes = int(result.get("resume_cleanup_bytes") or 0)
        resume_cleanup_failures = int(
            result.get("resume_cleanup_failed_count") or 0
        )
        if resume_cleanup_count:
            suffix += (
                f"，清理 {resume_cleanup_count} 个旧简历副本"
                f"（{_format_storage_bytes(resume_cleanup_bytes)}）"
            )
        restore_at = self._remember_maintenance_success("restore")
        self._set_data_backup_status(
            self._data_backup_note_text(
                restore_at=restore_at,
                restore_summary=f"{summary}{suffix}",
            )
        )
        self.append_operation_log(f"[数据安全] 已从备份恢复：{summary}{suffix}")
        restore_notices = []
        if unresolved:
            restore_notices.append(
                f"另有 {unresolved} 条记录未自动关联岗位，已原样保留。"
            )
        if resume_cleanup_failures:
            restore_notices.append(
                f"有 {resume_cleanup_failures} 项旧简历清理失败，"
                "可稍后运行简历存储体检。"
            )
        messagebox.show_result(
            "恢复数据备份",
            headline="数据已恢复",
            message="界面已重新加载恢复后的数据。",
            metrics=self._backup_summary_metrics(result),
            notice=" ".join(restore_notices) or None,
            parent=self.root,
        )

    def _diagnostic_runtime_context(self) -> dict:
        """Return a strict allowlist of non-identifying GUI state."""
        try:
            current_page = PageIndex(
                getattr(self, "current_page_index", PageIndex.HOME)
            )
            current_page_name = PAGE_SPECS[current_page].title
        except (TypeError, ValueError, KeyError):
            current_page_name = "未知"
        context = {
            "browser_connected": bool(
                getattr(self, "browser_connected", False)
            ),
            "browser_state": (
                "connected"
                if getattr(self, "browser_connected", False)
                else "disconnected"
            ),
            "current_page": current_page_name,
            "data_storage_error_present": bool(
                str(getattr(self, "_data_storage_error", "") or "").strip()
            ),
            "dpi_scale": round(float(getattr(self, "dpi_scale", 1.0)), 3),
            "zoom_factor": round(float(getattr(self, "zoom_factor", 1.0)), 3),
        }
        try:
            context.update({
                "screen_width": int(self.root.winfo_screenwidth()),
                "screen_height": int(self.root.winfo_screenheight()),
                "window_width": int(self.root.winfo_width()),
                "window_height": int(self.root.winfo_height()),
                "tk_patchlevel": str(
                    self.root.tk.call("info", "patchlevel")
                ),
            })
        except (AttributeError, TypeError, ValueError, tk.TclError):
            pass
        return context

    def _export_diagnostic_package(self) -> None:
        """Export a bounded, redacted support package for troubleshooting."""
        if getattr(self, "_data_maintenance_running", False):
            messagebox.showwarning(
                "暂时不能导出",
                "数据备份或恢复正在进行，请结束后再导出诊断包。",
                parent=self.root,
            )
            return
        destination = filedialog.asksaveasfilename(
            title="导出脱敏诊断包",
            defaultextension=".zip",
            initialfile=f"BOSS诊断包-{datetime.now():%Y%m%d-%H%M%S}.zip",
            filetypes=[("ZIP 诊断包", "*.zip")],
            parent=self.root,
        )
        if not destination:
            return
        self._data_maintenance_running = True
        status_var = getattr(self, "diagnostic_package_status_var", None)
        if status_var is not None:
            status_var.set("正在收集、脱敏并复核…")
        try:
            result = _DATA_MAINTENANCE_CONTROLLER.create_diagnostic(
                BASE_DIR,
                destination,
                app_version=__version__,
                runtime_context=self._diagnostic_runtime_context(),
                creator=create_diagnostic_package,
            )
        except (
            DiagnosticPrivacyError,
            OSError,
            ValueError,
            RuntimeError,
            zipfile.BadZipFile,
        ) as exc:
            if status_var is not None:
                status_var.set("诊断包导出失败")
            messagebox.show_failure(
                "导出诊断包",
                headline="诊断包未导出",
                message="没有生成可分享的诊断包。",
                detail=str(exc),
                parent=self.root,
            )
            return
        finally:
            self._data_maintenance_running = False

        exported_at = self._remember_maintenance_success("diagnostic_export")
        if status_var is not None:
            status_var.set(self._diagnostic_export_note_text(
                exported_at=exported_at,
                summary=f"已脱敏并复核，包含 {result['log_count']} 个日志文件",
            ))
        self.append_operation_log(
            "[故障诊断] 已导出脱敏诊断包，"
            f"包含 {result['log_count']} 个日志文件"
        )
        action = messagebox.show_result(
            "导出诊断包",
            headline="诊断包已导出",
            message="已完成自动脱敏和残留复核。",
            file_path=str(result["path"]),
            notice="分享前，请检查 ZIP 内的文本内容。",
            parent=self.root,
        )
        if action == "open_location":
            self._open_export_location(str(result["path"]))

    def _api_config_file_mtime(self):
        """Return a stable file fingerprint for api_config.json."""
        try:
            path = get_api_config_path()
            return path.stat().st_mtime_ns if path.exists() else 0
        except OSError:
            return 0

    def _load_api_config_to_ui_if_needed(self):
        """Load API config into widgets only when the config file changed."""
        if not hasattr(self, 'api_provider_var'):
            return

        mtime = self._api_config_file_mtime()
        if self._api_ui_config_mtime == mtime:
            return

        if mtime:
            self.load_api_config(resolve_keys=False)
        self.load_api_config_to_ui(resolve_key=False)
        self._api_ui_config_mtime = mtime

    def _schedule_api_key_resolution(self, delay_ms: int = 250) -> None:
        """Resolve keyring after the settings page has painted its first frame."""
        resolve_thread = getattr(self, '_api_key_resolve_thread', None)
        if resolve_thread and resolve_thread.is_alive():
            return
        if getattr(self, '_api_key_resolve_after_id', None) is not None:
            return

        def _start():
            self._api_key_resolve_after_id = None
            if getattr(self, 'current_page_index', None) != PageIndex.SETTINGS:
                return
            self._resolve_api_keys_async()

        self._api_key_resolve_after_id = self.root.after(delay_ms, _start)

    @staticmethod
    def _api_key_cache_identity(provider: str, base_url: str = "") -> tuple[str, str]:
        """Return the normalized keyring identity used by the per-session cache."""
        return str(provider or "").strip(), str(base_url or "").strip().rstrip("/")

    def _remember_api_key(self, provider: str, base_url: str, api_key: str) -> None:
        """Cache a successfully resolved key without persisting any new plaintext copy."""
        if not api_key:
            return
        cache = getattr(self, '_api_key_cache', None)
        if cache is None:
            cache = self._api_key_cache = {}
        cache[self._api_key_cache_identity(provider, base_url)] = api_key

    def _get_api_key_cached(self, provider: str, base_url: str = "") -> str:
        """Read one keyring entry on demand and reuse successful reads this session."""
        if not provider:
            return ""
        cache = getattr(self, '_api_key_cache', None)
        if cache is None:
            cache = self._api_key_cache = {}
        identity = self._api_key_cache_identity(provider, base_url)
        lock = getattr(self, '_api_key_cache_lock', None)
        if lock is None:
            lock = self._api_key_cache_lock = threading.Lock()
        with lock:
            cached = cache.get(identity)
            if cached:
                return cached
            api_key = str(get_api_key(provider, base_url) or "")
            self._remember_api_key(provider, base_url, api_key)
            return api_key

    def _resolve_api_keys_async(self):
        """后台只读取当前模型的 keyring 项，其他模型在实际使用时按需读取。"""
        if self._api_key_resolve_thread and self._api_key_resolve_thread.is_alive():
            return
        if not getattr(self, 'api_config', None):
            return

        provider = self.api_config.get("api_provider", "")
        base_url = self.api_config.get("base_url", "")

        def _worker():
            current_key = ""
            try:
                if provider:
                    current_key = self._get_api_key_cached(provider, base_url)
            except Exception:
                current_key = ""

            def _apply():
                if not getattr(self, 'api_config', None):
                    return
                if (self.api_config.get("api_provider", ""), self.api_config.get("base_url", "")) != (provider, base_url):
                    return
                self.api_config["api_key"] = current_key
                if provider and not current_key:
                    self.api_config["needs_reconfigure"] = True
                    if hasattr(self, 'api_status_frame'):
                        self._update_api_status(
                            text="当前模型的 API Key 未配置，请重新输入并保存模型",
                            foreground=self.colors['warning'],
                        )
                else:
                    self.api_config.pop("needs_reconfigure", None)
                if hasattr(self, 'api_key_var'):
                    self.api_key_var.set(current_key)
                self._update_ai_eval_status()

            self.run_on_ui(_apply)

        self._api_key_resolve_thread = threading.Thread(target=_worker, daemon=True)
        self._api_key_resolve_thread.start()

    def _mark_api_config_ui_current(self):
        """Mark API config widgets as current after this instance writes the file."""
        self._api_ui_config_mtime = self._api_config_file_mtime()

    def update_current_model_display(self):
        """刷新两个模型用途选择器。"""
        self._refresh_model_assignment_controls()

    @staticmethod
    def _model_ref_matches(model_config, model_ref):
        """按完整连接身份比较模型，避免同名模型误匹配。"""
        if not model_config or not model_ref:
            return False
        return _SETTINGS_CONTROLLER.model_ref_matches(model_config, model_ref)

    def _model_choice_label(self, model_config, include_url=False):
        provider_key = model_config.get("api_provider", "")
        provider_display = self.PROVIDER_DISPLAY.get(provider_key, provider_key)
        label = f"{provider_display} / {model_config.get('model', '')}"
        if include_url and model_config.get("base_url"):
            label += f" ({model_config['base_url']})"
        return label

    def _get_assigned_model_ref(self, role):
        """返回指定用途当前实际使用模型的完整连接身份。"""
        config = self.api_config or {}
        if role == "education":
            return dict(config.get("education_model_ref") or {
                "api_provider": config.get("api_provider", ""),
                "base_url": config.get("base_url", ""),
                "model": config.get("model", ""),
            })
        return {
            "api_provider": config.get("api_provider", ""),
            "base_url": config.get("base_url", ""),
            "model": config.get("model", ""),
        }

    def _saved_model_usage_tag(self, model_config):
        """返回已保存模型的用途颜色标签，不在列表中重复展示用途文字。"""
        is_default = self._model_ref_matches(
            model_config, self._get_assigned_model_ref("default")
        )
        is_education = self._model_ref_matches(
            model_config, self._get_assigned_model_ref("education")
        )
        if is_default and is_education:
            return "default_and_education_model"
        if is_default:
            return "default_model"
        if is_education:
            return "education_model"
        return ""

    @staticmethod
    def _model_ref_key(model_ref):
        """将模型完整连接身份规整为可复用的状态缓存键。"""
        if not model_ref:
            return None
        return _SETTINGS_CONTROLLER.model_ref_key(model_ref)

    def _assigned_model_test_target_label(self, role, model_ref=None):
        """返回用途模型测试提示中使用的可辨识名称。"""
        role_label = "默认 AI 模型" if role == "default" else "学历核验模型"
        model_ref = model_ref or self._get_assigned_model_ref(role)
        provider_key = model_ref.get("api_provider", "")
        provider_display = getattr(self, "PROVIDER_DISPLAY", PROVIDER_DISPLAY).get(
            provider_key, provider_key
        )
        model_name = model_ref.get("model", "") or "未配置"
        return f"{role_label}（{provider_display} / {model_name}）"

    def _assigned_model_test_roles(self, role, model_ref=None):
        """返回一次测试应同步的用途；实际连接身份相同时双向同步。"""
        if role not in ("default", "education"):
            return (role,)
        model_ref = model_ref or self._get_assigned_model_ref(role)
        if all(
            self._model_ref_matches(model_ref, self._get_assigned_model_ref(target_role))
            for target_role in ("default", "education")
        ):
            return ("default", "education")
        return (role,)

    def _set_assigned_model_test_state(self, role, state):
        """更新用途模型的红绿灯和行内状态。"""
        states = getattr(self, "_assigned_model_test_states", None)
        icons = getattr(self, "_assigned_model_test_icons", None)
        buttons = getattr(self, "_assigned_model_test_buttons", None)
        if not states or not icons or not buttons:
            return
        states[role] = state
        icon = icons["pending" if state in ("pending", "testing") else state]
        button = buttons.get(role)
        if button is None:
            return
        button.configure(image=icon)
        button._icon_ref = icon
        status_label = getattr(self, "_assigned_model_test_status_labels", {}).get(role)
        if status_label is not None:
            status_text, foreground = {
                "pending": ("未检测", self.colors['text_secondary']),
                "testing": ("测试中", self.colors['warning']),
                "success": ("已通过", self.colors['success']),
                "error": ("失败", self.colors['danger']),
            }.get(state, ("未检测", self.colors['text_secondary']))
            status_label.configure(text=status_text, foreground=foreground)

    def _reset_assigned_model_test_states(self):
        """模型用途变更后撤销旧测试结果，避免把结果带给新模型。"""
        if not hasattr(self, "_assigned_model_test_tokens"):
            return
        for role in ("default", "education"):
            current_ref = self._get_assigned_model_ref(role)
            previous_ref = self._assigned_model_test_refs.get(role)
            if self._model_ref_matches(previous_ref, current_ref):
                continue
            self._assigned_model_test_refs[role] = current_ref
            self._assigned_model_test_tokens[role] += 1
            previous_state = getattr(self, "_assigned_model_test_results", {}).get(
                self._model_ref_key(current_ref), "pending"
            )
            self._set_assigned_model_test_state(role, previous_state)

    def _show_assigned_model_test_tooltip(self, role, event):
        """显示当前模型连通性信号灯的含义。"""
        target_label = self._assigned_model_test_target_label(role)
        state = getattr(self, "_assigned_model_test_states", {}).get(role, "pending")
        text = {
            "pending": f"未检测，点击测试{target_label}",
            "testing": f"正在测试{target_label}",
            "success": f"{target_label}测试通过，点击重新测试",
            "error": f"{target_label}测试失败，点击重试",
        }.get(state, f"测试{target_label}")
        self.feedback_support.show_tooltip(text, event.x_root + 12, event.y_root + 10, ("model-test", role))

    def _refresh_model_assignment_controls(self):
        """让模型用途选择器与 saved_models 和当前配置保持一致。"""
        if not hasattr(self, 'default_model_combo'):
            return
        saved_models = list((self.api_config or {}).get("saved_models", []))
        identity_counts = {}
        for model_config in saved_models:
            identity = (model_config.get("api_provider", ""), model_config.get("model", ""))
            identity_counts[identity] = identity_counts.get(identity, 0) + 1

        choices = []
        refs = {}
        for model_config in saved_models:
            identity = (model_config.get("api_provider", ""), model_config.get("model", ""))
            label = self._model_choice_label(model_config, identity_counts[identity] > 1)
            if label in refs:
                continue
            choices.append(label)
            refs[label] = model_config

        current_ref = {
            "api_provider": (self.api_config or {}).get("api_provider", ""),
            "base_url": (self.api_config or {}).get("base_url", ""),
            "model": (self.api_config or {}).get("model", ""),
        }
        default_label = next(
            (label for label, ref in refs.items() if self._model_ref_matches(ref, current_ref)),
            "",
        )
        if current_ref["model"] and not default_label:
            default_label = f"{self._model_choice_label(current_ref)}（未保存）"
            choices.insert(0, default_label)
            refs[default_label] = current_ref

        follow_label = "跟随默认 AI 模型"
        edu_choices = [follow_label, *choices]
        edu_ref = (self.api_config or {}).get("education_model_ref") or {}
        if edu_ref:
            education_label = next(
                (label for label, ref in refs.items() if self._model_ref_matches(ref, edu_ref)),
                "",
            )
            if not education_label:
                education_label = f"{self._model_choice_label(edu_ref)}（未保存）"
                edu_choices.append(education_label)
                refs[education_label] = edu_ref
        else:
            education_label = follow_label

        self._updating_model_assignment_controls = True
        try:
            self._model_choice_refs = refs
            self.default_model_combo.configure(values=choices)
            self.education_model_combo.configure(values=edu_choices)
            self.default_model_choice_var.set(default_label or "未配置")
            self.education_model_choice_var.set(education_label)
        finally:
            self._updating_model_assignment_controls = False
        self._reset_assigned_model_test_states()

    def load_saved_models_to_tree(self):
        """加载已保存的模型列表到 Treeview"""
        if not hasattr(self, 'model_list_tree'):
            return

        # 清空现有列表
        for item in self.model_list_tree.get_children():
            self.model_list_tree.delete(item)

        # 确保 api_config 已加载
        if not hasattr(self, 'api_config') or not self.api_config:
            return

        # 加载已保存的模型
        saved_models = self.api_config.get("saved_models", [])
        # 同步到 self.saved_models（关键修复！）
        self.saved_models = saved_models

        # 用颜色保留正在使用模型的辨识度，不重新引入“用途”列。
        self.model_list_tree.tag_configure(
            "default_model",
            background=self.colors['bg_tree_tag_high'],
            foreground=self.colors['success'],
        )
        self.model_list_tree.tag_configure(
            "education_model",
            background=self.colors.get('banner_info_bg', ui_theme.BANNER_INFO_BG),
            foreground=self.colors['primary'],
        )
        self.model_list_tree.tag_configure(
            "default_and_education_model",
            background=self.colors['bg_tree_tag_high'],
            foreground=self.colors['primary'],
        )

        for model_config in saved_models:
            name = model_config.get("model", "")
            provider_key = model_config.get("api_provider", "")
            # 将内部键转换为显示名称
            provider_display = self.PROVIDER_DISPLAY.get(provider_key, provider_key)
            base_url = model_config.get("base_url", "")
            # 可用性状态显示：优先从 capability_cache 读取，fallback 到 saved_models 中的 capability
            cap = model_config.get("capability", {})
            if not cap.get("status"):
                # 尝试从 capability_cache 读取
                try:
                    from ai_adapter import load_capability
                    cache_config = {"api_provider": provider_key, "base_url": base_url, "model": name}
                    cached = load_capability(cache_config)
                    if cached:
                        cap = cached
                except Exception:
                    pass
            cap_status = cap.get("status", "")
            if cap_status in ("compatible", "limited"):
                status_display = "✓ 可用"
            else:
                status_display = "未检测"
            usage_tag = self._saved_model_usage_tag(model_config)
            self.model_list_tree.insert(
                "", "end", values=(name, provider_display, status_display, base_url),
                tags=(usage_tag,) if usage_tag else (),
            )

        # 动态调整高度：普通窗口保持原来的最多6行，全屏/高窗口显示更多行。
        self.layout_support.update_model_list_height()
        # 根据窗口状态显示/隐藏 Base URL 列
        self.layout_support.update_model_list_columns()
        self._refresh_model_assignment_controls()

        # 在所有控件创建完毕后绑定滚轮事件
        self.scroll_support.bind_mousewheel(
            self.api_canvas,
            self.api_scrollable_frame,
        )

    def create_run_page(self) -> None:
        """同步创建运行控制页，供需要立即访问控件的内部流程使用。"""
        for _step in self._create_run_page_steps():
            pass

    def _create_run_page_steps(self) -> Iterator[None]:
        """分步创建运行控制页，保持首次打开时的逐帧调度。"""
        yield from gui_run_page.build_run_page_steps(
            self,
            UI_CONFIG,
            font_family=FONT_FAMILY,
            scroll_warning_threshold=RUN_SCROLL_WARNING_THRESHOLD,
            api_page_warning_threshold=RUN_API_PAGE_WARNING_THRESHOLD,
            contact_warning_threshold=RUN_CONTACT_WARNING_THRESHOLD,
            timeout_hint=_api_timeout_hint_text,
        )

    def _schedule_run_page_api_key_check(
        self,
        target_ai_status_label: tk.Label,
    ) -> None:
        """在运行页绘制完成后异步解析 API Key 状态。"""
        def _check_run_page_key_bg() -> None:
            provider = self.api_config.get("api_provider", "")
            if not provider:
                self.run_on_ui(self._update_ai_eval_status)
                return
            try:
                api_key = self._get_api_key_cached(
                    provider,
                    self.api_config.get("base_url", ""),
                )
            except Exception:
                api_key = None

            def _apply() -> None:
                if getattr(self, "ai_status_label", None) is not target_ai_status_label:
                    return
                if api_key and not self.api_config.get("api_key"):
                    self.api_config["api_key"] = api_key
                self._update_ai_eval_status()

            self.run_on_ui(_apply)

        def _start_run_page_key_check() -> None:
            if (
                getattr(self, "current_page_index", None) != PageIndex.RUN
                or getattr(self, "run_page", None) is None
            ):
                return
            threading.Thread(
                target=_check_run_page_key_bg,
                daemon=True,
            ).start()

        self.root.after(150, _start_run_page_key_check)

    def create_result_page(self):
        """创建筛选结果页面。"""
        self._result_search_placeholder = "姓名/性别/匹配分/推荐指数/状态"
        self._result_search_placeholder_active = True
        self._result_search_focused = False
        self.result_search_clear_hint = None
        self.result_date_start_entry = None
        self.result_date_end_entry = None

        widgets = gui_result_page.build_result_page(
            self,
            UI_CONFIG,
            font_family=FONT_FAMILY,
            run_page_index=PageIndex.RUN,
        )
        self._result_page_widgets = widgets
        self.result_page = widgets.page
        self.result_job_var = widgets.job_var
        self.result_job_combo = widgets.job_combo
        self.result_time_range_var = widgets.time_range_var
        self.result_time_range_combo = widgets.time_range_combo
        self.result_custom_date_frame = widgets.custom_date_frame
        self.result_stats_vars = widgets.stats_vars
        self.result_stats_greeted = widgets.stats_greeted
        self.result_stats_click = widgets.stats_click
        self._result_stat_icon_canvases = widgets.stat_icon_canvases
        self.result_search_var = widgets.search_var
        self.result_search_entry = widgets.search_entry
        self.result_search_clear_hint = widgets.search_clear_hint
        self.result_view_label = widgets.view_label
        self.result_view_var = widgets.view_var
        self.result_view_combo = widgets.view_combo
        self.result_count_var = widgets.count_var
        self.result_show_blacklist_var = widgets.show_blacklist_var
        self.result_tree = widgets.tree
        self._result_tree_font = widgets.tree_font
        self.result_empty_state = widgets.empty_state
        self.result_review_button = widgets.review_button
        self.result_greet_queue_button = widgets.greet_queue_button
        self.result_greet_queue_badge = widgets.greet_queue_badge
        self.result_more_menu_button = widgets.more_menu_button
        self.result_more_menu = widgets.more_menu

        self.layout_support.update_result_tree_columns()
        self._refresh_contact_queue_badge()


    def create_education_page(self):
        """创建学历核验页面。"""
        widgets = gui_education_page.build_education_page(
            self,
            UI_CONFIG,
            font_family=FONT_FAMILY,
        )
        self._education_page_widgets = widgets
        self.education_page = widgets.page
        self.education_canvas = widgets.canvas
        self.education_scrollable_frame = widgets.scrollable_frame
        self.education_items = widgets.items
        self.education_current_id = widgets.current_id
        self.education_item_counter = widgets.item_counter
        self.education_recognition_running = widgets.recognition_running
        self.education_manual_rotation = widgets.manual_rotation
        self.education_rotation_locked = widgets.rotation_locked
        self.education_file_var = widgets.file_var
        self.education_remove_btn = widgets.remove_button
        self.education_queue_card = widgets.queue_card
        self._education_tree_font = widgets.tree_font
        self.education_queue_tree = widgets.queue_tree
        self.education_queue_scrollbar = widgets.queue_scrollbar
        self.education_queue_menu = widgets.queue_menu
        self.education_workspace = widgets.workspace
        self.education_rotate_btn = widgets.rotate_button
        self.education_preview_label = widgets.preview_label
        self.education_name_var = widgets.name_var
        self.education_number_var = widgets.number_var
        self.education_status_var = widgets.status_var
        self.education_warning_var = widgets.warning_var
        self.education_recognize_btn = widgets.recognize_button
        self.education_fill_btn = widgets.fill_button
        self.education_captcha_btn = widgets.captcha_button

    def _select_education_images(self):
        """批量导入毕业证书图片并加入待核验队列。"""
        self._save_current_education_fields()
        paths = filedialog.askopenfilenames(
            title="导入毕业证书",
            filetypes=[
                ("图片和 PDF", "*.jpg *.jpeg *.png *.bmp *.webp *.pdf"),
                ("图片文件", "*.jpg *.jpeg *.png *.bmp *.webp"),
                ("PDF 文件", "*.pdf"),
                ("所有文件", "*.*"),
            ],
        )
        if not paths:
            return
        from education_certificate import is_pdf_path, validate_document_path
        batch = _EDUCATION_CONTROLLER.prepare_import(
            paths,
            self.education_items,
            self.education_item_counter,
            validator=validate_document_path,
            is_pdf=is_pdf_path,
        )
        self.education_item_counter = batch.next_counter
        self.education_items.update(batch.items)
        added_ids = list(batch.items)
        invalid_files = list(batch.invalid_files)
        for item_id, item in batch.items.items():
            path = Path(item["path"])
            self.education_queue_tree.insert(
                "", "end", iid=item_id,
                values=(path.name, "", "", "", "", "待识别"),
            )

        self._refresh_education_queue_summary()
        if added_ids:
            self.education_queue_tree.selection_set(added_ids[0])
            self.education_queue_tree.focus(added_ids[0])
            self.education_queue_tree.see(added_ids[0])
            self._on_education_queue_select()
            # 导入了图片文件时检查模型是否支持视觉
            has_image = any(
                not self.education_items.get(item_id, {}).get("is_pdf")
                for item_id in added_ids
            )
            if has_image:
                from education_certificate import likely_supports_vision
                if not likely_supports_vision(dict(self.api_config or {})):
                    model_name = str((self.api_config or {}).get("model") or "未配置")
                    messagebox.show_notice(
                        "图片识别模型提醒",
                        headline="当前模型可能无法识别图片",
                        message="请先在「API 配置」中切换支持图片输入的模型。",
                        metrics=(("当前模型", model_name),),
                        notice="PDF 使用文本提取，不受图片模型限制。",
                        detail=(
                            "可选视觉模型示例：\n"
                            "国外：GPT-4o / GPT-4.1、Claude Sonnet 4、Gemini 2.5 Pro\n"
                            "国内：qwen3.7-plus、mimo-v2.5、GLM-5V、Kimi K2.5、MiniMax-M2.7"
                        ),
                        parent=self.root,
                    )
        if invalid_files:
            omitted_count = max(0, len(invalid_files) - 10)
            messagebox.show_notice(
                "部分文件未导入",
                headline=f"{len(invalid_files)} 个文件未导入",
                message="学历核验仅支持图片或 PDF 文件。",
                metrics=(
                    ("已加入队列", f"{len(added_ids)} 个"),
                    ("未导入", f"{len(invalid_files)} 个"),
                ),
                detail="\n".join(invalid_files[:10]),
                notice=(
                    f"详细信息仅显示前 10 个，另有 {omitted_count} 个文件未列出。"
                    if omitted_count
                    else "请重新选择 JPG、PNG、BMP、WEBP 或 PDF 文件。"
                ),
                parent=self.root,
            )

    def _refresh_education_queue_summary(self):
        """更新队列数量和按钮状态。"""
        total = len(self.education_items)
        if total == 1:
            self.education_file_var.set("已导入 1 张证书")
        elif total > 1:
            self.education_file_var.set(f"已导入 {total} 张证书，点击队列切换")
        else:
            self.education_file_var.set("尚未导入毕业证书")
        queue_card = getattr(self, "education_queue_card", None)
        workspace = getattr(self, "education_workspace", None)
        if queue_card is not None:
            if total >= 1 and not queue_card.winfo_manager():
                queue_card.pack(
                    fill="x",
                    before=workspace,
                    pady=(0, int(16 * self.dpi_scale * self.zoom_factor)),
                )
            elif total < 1 and queue_card.winfo_manager():
                queue_card.pack_forget()
        queue_tree = getattr(self, "education_queue_tree", None)
        if total >= 1 and queue_tree is not None:
            queue_tree.configure(height=min(5, total))
        queue_scrollbar = getattr(self, "education_queue_scrollbar", None)
        if queue_scrollbar is not None:
            if total > 5 and not queue_scrollbar.winfo_manager():
                queue_scrollbar.grid()
            elif total <= 5 and queue_scrollbar.winfo_manager():
                queue_scrollbar.grid_remove()
        education_canvas = getattr(self, "education_canvas", None)
        schedule_scroll_sync = getattr(
            education_canvas, "_schedule_overflow_sync", None
        )
        if schedule_scroll_sync is not None:
            schedule_scroll_sync()
            # Treeview 的行数变化会经过一轮 Tk 几何计算才更新父容器请求高度；
            # 下一帧再同步一次，覆盖“先导入 1 张、随后继续追加”的场景。
            education_canvas.after(16, schedule_scroll_sync)
        has_current = self.education_current_id in self.education_items
        state = "normal" if has_current else "disabled"
        self.education_remove_btn.configure(state=state)
        recognize_state = (
            "normal" if has_current and not self.education_recognition_running else "disabled"
        )
        self.education_recognize_btn.configure(state=recognize_state)
        self.education_fill_btn.configure(state=state)

    def _save_current_education_fields(self):
        """将当前编辑框内容保存回队列项。"""
        item_id = self.education_current_id
        item = self.education_items.get(item_id)
        if not item:
            return
        item["name"] = self.education_name_var.get().strip()
        item["certificate_number"] = self.education_number_var.get().strip()
        self._update_education_queue_row(item_id)

    def _update_education_queue_row(self, item_id):
        """刷新一条队列记录。"""
        item = self.education_items.get(item_id)
        if not item or not self.education_queue_tree.exists(item_id):
            return
        self.education_queue_tree.item(
            item_id,
            values=(
                Path(item["path"]).name,
                item.get("name", ""),
                item.get("certificate_number", ""),
                item.get("school", ""),
                item.get("major", ""),
                item.get("status", "待识别"),
            ),
        )

    def _on_education_queue_select(self, _event=None):
        """切换当前待核验图片，并保存上一项的人工修改。"""
        selection = self.education_queue_tree.selection()
        if not selection:
            return
        focused = self.education_queue_tree.focus()
        next_id = focused if focused in selection else selection[0]
        if next_id == self.education_current_id:
            return
        self._save_current_education_fields()
        item = self.education_items.get(next_id)
        if not item:
            return
        self.education_current_id = next_id
        self.education_image_path = item["path"]
        self.education_name_var.set(item.get("name", ""))
        self.education_number_var.set(item.get("certificate_number", ""))
        self.education_status_var.set(item.get("detail") or item.get("status", "待识别"))
        self.education_warning_var.set(item.get("warnings", ""))
        self._refresh_education_queue_summary()
        self._render_education_preview()

    def _on_education_queue_motion(self, event):
        """文件、学校、专业被截断时显示完整内容。"""
        tree = self.education_queue_tree
        item_id = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        tooltip_columns = {"#1": 0, "#4": 3, "#5": 4}
        value_index = tooltip_columns.get(column_id)
        if not item_id or value_index is None:
            self.feedback_support.hide_tooltip()
            return
        values = tree.item(item_id, "values")
        if len(values) <= value_index:
            self.feedback_support.hide_tooltip()
            return
        full_text = str(values[value_index] or "")
        cell_bbox = tree.bbox(item_id, column_id)
        if (
            not full_text
            or not cell_bbox
            or self._education_tree_font.measure(full_text) <= max(0, cell_bbox[2] - 12)
        ):
            self.feedback_support.hide_tooltip()
            return
        tooltip_key = ("education", item_id, column_id)
        if (
            tooltip_key == getattr(self, "_tooltip_item", None)
            and getattr(self, "_tooltip", None)
            and self._tooltip.winfo_exists()
        ):
            return
        self._tooltip_item = tooltip_key
        after_id = getattr(self, "_tooltip_after_id", None)
        if after_id:
            self.root.after_cancel(after_id)
        x = self.root.winfo_pointerx() + 15
        y = self.root.winfo_pointery() + 10
        self._tooltip_after_id = self.root.after(
            300, lambda: self.feedback_support.show_tooltip(full_text, x, y, tooltip_key)
        )

    def _show_education_queue_context_menu(self, event):
        """右键队列行；保留已有多选，未选中行则切换为单选。"""
        item_id = self.education_queue_tree.identify_row(event.y)
        if not item_id:
            return
        if item_id not in self.education_queue_tree.selection():
            self._save_current_education_fields()
            self.education_queue_tree.selection_set(item_id)
        self.education_queue_tree.focus(item_id)
        self._on_education_queue_select()

        # 重建右键菜单
        self.education_queue_menu.delete(0, "end")
        self.education_queue_menu.add_command(
            label="识别证书", command=self._recognize_education_image
        )
        self.education_queue_menu.add_command(
            label="学信网验证", command=self._fill_chsi_page
        )
        self.education_queue_menu.add_separator()
        self.education_queue_menu.add_command(
            label="删除证书", command=self._remove_selected_education_images
        )

        self.education_queue_menu.tk_popup(event.x_root, event.y_root)

    def _selected_education_item_ids(self):
        """返回当前选中的有效队列项；无多选时回退当前项。"""
        selected = [
            item_id for item_id in self.education_queue_tree.selection()
            if item_id in self.education_items
        ]
        if selected:
            return selected
        if self.education_current_id in self.education_items:
            return [self.education_current_id]
        return []

    def _remove_current_education_image(self):
        """从队列移除当前或选中的图片（支持多选），不删除原文件。"""
        self._remove_education_items(self._selected_education_item_ids())

    def _remove_selected_education_images(self):
        """移除右键菜单选中的一个或多个队列项。"""
        self._remove_education_items(self._selected_education_item_ids())

    def _remove_education_items(self, item_ids):
        """从队列移除指定项目，不删除原始文件。"""
        valid_ids = [item_id for item_id in item_ids if item_id in self.education_items]
        if not valid_ids:
            return
        children = list(self.education_queue_tree.get_children())
        indexes = [children.index(item_id) for item_id in valid_ids if item_id in children]
        next_index = min(indexes) if indexes else 0
        for item_id in valid_ids:
            if self.education_queue_tree.exists(item_id):
                self.education_queue_tree.delete(item_id)
            self.education_items.pop(item_id, None)
            self.education_manual_rotation.pop(item_id, None)
            getattr(self, "education_rotation_locked", set()).discard(item_id)
        if self.education_current_id in valid_ids:
            self.education_current_id = None

        remaining = list(self.education_queue_tree.get_children())
        if remaining:
            next_id = remaining[min(next_index, len(remaining) - 1)]
            self.education_queue_tree.selection_set(next_id)
            self.education_queue_tree.focus(next_id)
            self._on_education_queue_select()
        else:
            self.education_image_path = None
            self.education_name_var.set("")
            self.education_number_var.set("")
            self.education_status_var.set("等待导入图片/PDF")
            self.education_warning_var.set("")
            self.education_preview_label.configure(
                image="", text="请选择 JPG、JPEG、PNG、BMP、WEBP 图片或 PDF 文件"
            )
            self.education_preview_label._image_ref = None
            self._refresh_education_queue_summary()

    def _schedule_education_preview_render(self):
        """预览区尺寸变化时防抖重绘，避免拖动窗口边框期间连续读盘解码。"""
        pending = getattr(self, '_education_preview_render_timer', None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except Exception:
                pass
        self._education_preview_render_timer = self.root.after(
            120, self._render_education_preview
        )

    def _get_education_source_image(self, path, item_id, display_angle):
        """读取并校正方向的源图，按 (路径, 角度) 缓存，避免每次重绘重复解码大图。"""
        from PIL import Image, ImageOps
        cache = getattr(self, '_education_source_cache', None)
        if cache is None:
            cache = self._education_source_cache = {}
        key = (str(path), display_angle)
        if key not in cache:
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
            if display_angle:
                image = image.rotate(
                    -display_angle, expand=True, resample=Image.Resampling.BICUBIC
                )
            cache[key] = image
            # 只保留最近几张，防止长会话内存膨胀
            while len(cache) > 4:
                cache.pop(next(iter(cache)))
        return cache[key]

    def _render_education_preview(self):
        """按当前预览区域尺寸显示证书图片，依次应用 EXIF 与自动/人工方向。"""
        path = getattr(self, 'education_image_path', None)
        label = getattr(self, 'education_preview_label', None)
        if not path or label is None:
            return
        item_id = self.education_current_id
        item = self.education_items.get(item_id) if item_id else None
        if item and item.get("is_pdf"):
            label.configure(
                image="",
                text="PDF 文档，无法预览图片。点击「识别证书」从文本提取字段。",
            )
            label._image_ref = None
            return
        try:
            from PIL import Image, ImageTk
            rotation_locked = getattr(self, "education_rotation_locked", set())
            if item_id in rotation_locked:
                display_angle = self.education_manual_rotation.get(item_id, 0)
            else:
                display_angle = int((item or {}).get("auto_rotation", 0) or 0)
            image = self._get_education_source_image(path, item_id, display_angle)
            width = max(320, label.winfo_width() - 20)
            height = max(320, label.winfo_height() - 20)
            image = image.copy()
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            label.configure(image=photo, text="")
            label._image_ref = photo
        except Exception as error:
            label.configure(image="", text=f"图片预览失败：{error}")


    def _rotate_education_image_cw90(self):
        """将当前显示方向顺转90°并锁定为人工方向。"""
        item_id = self.education_current_id
        if not item_id or item_id not in self.education_items:
            return
        item = self.education_items[item_id]
        if not hasattr(self, "education_rotation_locked"):
            self.education_rotation_locked = set()
        current_angle = (
            self.education_manual_rotation.get(item_id, 0)
            if item_id in self.education_rotation_locked
            else int(item.get("auto_rotation", 0) or 0)
        )
        self.education_manual_rotation[item_id] = (current_angle + 90) % 360
        self.education_rotation_locked.add(item_id)
        self._render_education_preview()

    def _get_education_api_config(self) -> dict:
        """获取学历核验使用的 API 配置。优先 education_model_ref，回退默认 AI 模型。"""
        return _EDUCATION_CONTROLLER.resolve_api_config(self.api_config or {})

    def _recognize_education_image(self):
        """最多三路并发识别当前选中的毕业证书。"""
        self._save_current_education_fields()
        item_ids = self._selected_education_item_ids()
        if not item_ids:
            messagebox.showinfo("请选择图片", "请先选择毕业证书。", parent=self.root)
            return
        if self.education_recognition_running:
            return
        # 学历核验专用配置（优先 education_model_ref，回退默认 AI 模型）
        edu_config = self._get_education_api_config()
        # 检查是否有图片文件需要视觉模型
        has_image = any(
            not self.education_items.get(item_id, {}).get("is_pdf")
            for item_id in item_ids
        )
        if has_image:
            from education_certificate import likely_supports_vision
            if not likely_supports_vision(edu_config):
                model_name = str(edu_config.get("model") or "未配置")
                if not messagebox.ask_confirmation(
                    "继续尝试图片识别？",
                    headline="当前学历核验模型可能不支持图片输入",
                    message="继续后仍会尝试识别，但可能直接失败或无法返回有效字段。",
                    metrics=(("当前模型", model_name),),
                    notice="建议先到系统设置的「使用中的模型」切换学历核验模型。",
                    detail=(
                        "可选视觉模型示例：\n"
                        "国外：GPT-4o / GPT-4.1、Claude Sonnet 4、Gemini 2.5 Pro\n"
                        "国内：qwen3.7-plus、mimo-v2.5、GLM-5V、Kimi K2.5、MiniMax-M2.7\n\n"
                        "PDF 使用文本提取，不受图片模型限制。"
                    ),
                    yes_label="仍然尝试",
                    no_label="返回切换模型",
                    parent=self.root,
                ):
                    return
        self.education_recognition_running = True
        from education_certificate import resolve_vision_api_config
        vision_config = resolve_vision_api_config(edu_config)
        vision_model = str(vision_config.get("model") or "当前模型")
        for item_id in item_ids:
            item = self.education_items[item_id]
            item["status"] = "识别中"
            item["detail"] = f"正在使用 {vision_model} 识别证书..."
            item["warnings"] = ""
            self._update_education_queue_row(item_id)
        current_item = self.education_items.get(self.education_current_id)
        if current_item and self.education_current_id in item_ids:
            self.education_status_var.set(current_item["detail"])
            self.education_warning_var.set("")
        self._refresh_education_queue_summary()

        def worker():
            from education_certificate import (
                recognize_certificate_image,
                recognize_certificate_pdf,
            )

            results = _EDUCATION_CONTROLLER.recognize_documents(
                self.education_items,
                item_ids,
                vision_config,
                self._get_education_api_key(vision_config) or "",
                recognize_image=recognize_certificate_image,
                recognize_pdf=recognize_certificate_pdf,
            )

            def show_results():
                updated_ids = _EDUCATION_CONTROLLER.apply_recognition_results(
                    self.education_items,
                    results,
                )
                for item_id in updated_ids:
                    self._update_education_queue_row(item_id)
                current = self.education_items.get(self.education_current_id)
                if current and self.education_current_id in results:
                    self.education_name_var.set(current.get("name", ""))
                    self.education_number_var.set(
                        current.get("certificate_number", "")
                    )
                    self.education_status_var.set(current.get("detail", ""))
                    self.education_warning_var.set(
                        current.get("warnings", "")
                    )
                    self._render_education_preview()
                self.education_recognition_running = False
                self._refresh_education_queue_summary()

            self.run_on_ui(show_results)
        threading.Thread(target=worker, daemon=True).start()

    def _fill_chsi_page(self):
        """打开学信网验证页；多选时每个候选人分配独立 tab 并行执行。"""
        self._save_current_education_fields()
        item_ids = self._selected_education_item_ids()
        if not item_ids:
            messagebox.showinfo("请选择图片", "请先从队列选择图片。", parent=self.root)
            return

        from education_certificate import validate_chsi_fields
        preparation = _EDUCATION_CONTROLLER.prepare_chsi(
            self.education_items,
            item_ids,
            validator=validate_chsi_fields,
        )
        prepared = list(preparation.prepared)
        for item_id in preparation.invalid_ids:
            self._update_education_queue_row(item_id)

        if not prepared:
            messagebox.showwarning(
                "无有效候选人",
                "所选候选人的姓名或证书编号不完整，请先完成识别。",
                parent=self.root,
            )
            return

        self.education_fill_btn.configure(state="disabled")
        for item_id, _, _ in prepared:
            item = self.education_items.get(item_id)
            if item:
                item["status"] = "打开中"
                item["detail"] = "正在连接浏览器并打开学信网..."
                item["warnings"] = ""
                self._update_education_queue_row(item_id)
        first_item = self.education_items.get(prepared[0][0])
        if first_item:
            self.education_status_var.set(first_item["detail"])
            self.education_warning_var.set("")

        # 确保 base 浏览器连接就绪（串行，只执行一次）
        try:
            self._get_education_tab(None)
        except Exception as error:
            self._log_education_error("连接浏览器", error)
            for item_id, _, _ in prepared:
                item = self.education_items.get(item_id)
                if item:
                    item["status"] = "打开失败"
                    item["detail"] = "浏览器连接失败"
                    item["warnings"] = str(error)
                    self._update_education_queue_row(item_id)
            self.education_fill_btn.configure(state="normal")
            return

        # 在主线程串行创建所有 tab（DrissionPage.new_tab 不支持并发）
        tabs: dict[str, object] = {}
        for item_id, _, _ in prepared:
            try:
                tabs[item_id] = self._get_education_tab(item_id)
            except Exception as error:
                self._log_education_error("创建标签页", error, item_id)
                item = self.education_items.get(item_id)
                if item:
                    item["status"] = "打开失败"
                    item["detail"] = "创建标签页失败"
                    item["warnings"] = str(error)
                    self._update_education_queue_row(item_id)

        # 每个候选人一个独立 worker，并行执行（tab 已预分配，不再并发创建）
        import time
        for idx, (item_id, name, certificate_number) in enumerate(prepared):
            tab = tabs.get(item_id)
            if tab is None:
                continue
            # 错开启动时间，避免同时请求触发风控
            if idx > 0:
                time.sleep(1.5)
            def worker(
                iid=item_id, n=name, cn=certificate_number, page=tab,
            ):
                # 进度回调：实时更新队列状态列和详情
                def on_progress(status_text: str, detail: str):
                    def _update(iid=iid, s=status_text, d=detail):
                        item = self.education_items.get(iid)
                        if item:
                            item["status"] = s
                            item["detail"] = d
                            self._update_education_queue_row(iid)
                    self.run_on_ui(_update)

                max_attempts = 3
                try:
                    on_progress("正在加载学信网页面...", "")
                    success, status = self._fill_and_solve_captcha(
                        page,
                        n,
                        cn,
                        on_progress=on_progress,
                        max_attempts=max_attempts,
                    )
                except Exception as error:
                    error_text = str(error)
                    self._log_education_error("打开学信网", error, iid)
                    def show_error(iid=iid, err=error_text):
                        queue_item = self.education_items.get(iid)
                        if not queue_item:
                            return
                        queue_item["status"] = "打开失败"
                        error_line = err.splitlines()[0] if err else "未知错误"
                        queue_item["detail"] = f"打开学信网失败：{error_line}"
                        queue_item["warnings"] = err
                        self._update_education_queue_row(iid)
                        if self.education_current_id == iid:
                            self.education_status_var.set(queue_item["detail"])
                            self.education_warning_var.set(err)
                        self._set_captcha_btn_state("normal")
                        self._restore_education_fill_button_if_done()
                    self.run_on_ui(show_error)
                    return
                # 显示最终结果
                def show_success(iid=iid, ok=success, st=status):
                    queue_item = self.education_items.get(iid)
                    if not queue_item:
                        return
                    if ok:
                        queue_item["status"] = "已提交查询"
                        queue_item["detail"] = "验证码已识别并自动提交查询，请等待页面显示二维码。"
                        queue_item["warnings"] = "验证码通过后按页面提示使用手机扫码。"
                    elif st == "识别失败":
                        queue_item["status"] = "识别失败"
                        queue_item["detail"] = f"验证码识别错误（已尝试 {max_attempts} 次），请点击「重新识别验证码」重试。"
                        queue_item["warnings"] = ""
                    else:
                        queue_item["status"] = "待人工验证"
                        queue_item["detail"] = "已填写姓名和证书编号，验证码请人工输入"
                        queue_item["warnings"] = "验证码通过后按页面提示使用手机扫码。"
                    self._update_education_queue_row(iid)
                    if self.education_current_id == iid:
                        self.education_status_var.set(queue_item["detail"])
                        self.education_warning_var.set(queue_item["warnings"])
                    self._set_captcha_btn_state("normal")
                    self._restore_education_fill_button_if_done()
                self.run_on_ui(show_success)
            threading.Thread(target=worker, daemon=True).start()

    def _restore_education_fill_button_if_done(self):
        """全部学信网任务结束后恢复批量核验按钮。"""
        active_statuses = {"打开中", "识别验证码中..."}
        if any(
            item.get("status") in active_statuses
            for item in self.education_items.values()
        ):
            return
        self.education_fill_btn.configure(state="normal")

    def _log_education_error(
        self,
        stage: str,
        error: Exception,
        item_id: str | None = None,
    ) -> None:
        """记录独立核验的浏览器错误，便于定位无控制台 EXE 的失败原因。"""
        if not self.standalone_education:
            return
        try:
            log_dir = (
                Path(os.environ.get("LOCALAPPDATA") or Path.home())
                / "EducationCertificateTool"
            )
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            item_text = f" item={item_id}" if item_id else ""
            with (log_dir / "education_tool.log").open("a", encoding="utf-8") as stream:
                stream.write(
                    f"[{timestamp}] {stage}{item_text}: "
                    f"{type(error).__name__}: {error}\n"
                )
        except OSError:
            pass

    def _set_captcha_btn_state(self, state: str):
        """安全设置"重新识别验证码"按钮状态。"""
        btn = getattr(self, "education_captcha_btn", None)
        if btn is not None:
            btn.configure(state=state)

    def _fill_and_solve_captcha(
        self, page, name: str, certificate_number: str,
        on_progress: "Callable[[str, str], None] | None" = None,
        max_attempts: int = 3,
    ) -> tuple[bool, str]:
        """Fill the CHSI form and delegate bounded captcha retries."""
        from education_certificate import fill_chsi_query_page, navigate_to_chsi

        result = _EDUCATION_CONTROLLER.fill_and_solve_captcha(
            page,
            name,
            certificate_number,
            navigate=navigate_to_chsi,
            fill_query=fill_chsi_query_page,
            attempt=self._attempt_captcha_solve,
            browser_lock=self._education_browser_lock,
            on_progress=on_progress,
            max_attempts=max_attempts,
            sleep=time.sleep,
        )
        return result.successful, result.status
    def _attempt_captcha_solve(
        self, page, *, on_progress: "Callable[[str, str], None] | None" = None,
    ) -> tuple[bool, str]:
        """Capture, recognize, and submit one captcha through the controller."""
        from education_certificate import (
            CAPTCHA_AUTO_SUBMIT_MIN_CONFIDENCE,
            capture_captcha_image,
            check_query_result,
            click_chsi_query_button,
            fill_captcha_answer,
            recognize_captcha,
            resolve_vision_api_config,
        )

        config = self._get_education_api_config()
        result = _EDUCATION_CONTROLLER.attempt_captcha(
            page,
            config=config,
            api_key=self._get_education_api_key(config),
            browser_lock=self._education_browser_lock,
            min_confidence=CAPTCHA_AUTO_SUBMIT_MIN_CONFIDENCE,
            capture_image=capture_captcha_image,
            recognize=recognize_captcha,
            fill_answer=fill_captcha_answer,
            click_query=click_chsi_query_button,
            check_result=check_query_result,
            resolve_vision_config=resolve_vision_api_config,
            on_progress=on_progress,
            sleep=time.sleep,
        )
        return result.successful, result.status
    def _solve_captcha(self):
        """手动点击"重新识别验证码"按钮的入口，支持多选批量重试失败项。"""
        self._set_captcha_btn_state("disabled")
        item_ids = self._selected_education_item_ids()

        # 筛选失败的项
        failed_statuses = {"待人工验证", "识别失败", "打开失败"}
        failed_items = [
            item_id for item_id in item_ids
            if self.education_items.get(item_id, {}).get("status") in failed_statuses
        ]

        if not failed_items:
            messagebox.showinfo("提示", "所选候选人都已成功提交，无需重试。", parent=self.root)
            self._set_captcha_btn_state("normal")
            return

        # 更新所有失败项的状态
        for item_id in failed_items:
            item = self.education_items.get(item_id)
            if item:
                item["status"] = "识别验证码中..."
                item["detail"] = "正在刷新验证码并重新识别..."
                item["warnings"] = ""
                self._update_education_queue_row(item_id)

        first_item = self.education_items.get(failed_items[0])
        if first_item:
            self.education_status_var.set(first_item["detail"])
            self.education_warning_var.set("")

        # 对每个失败项并发重试
        for item_id in failed_items:
            def worker(iid=item_id):
                try:
                    page = self._get_education_tab(iid)
                    item = self.education_items.get(iid) or {}
                    def _retry_progress(status_text: str, detail: str, iid=iid):
                        def _update(iid=iid, s=status_text, d=detail):
                            item = self.education_items.get(iid)
                            if item:
                                item["status"] = s
                                item["detail"] = d
                                self._update_education_queue_row(iid)
                        self.run_on_ui(_update)
                    success, status = self._fill_and_solve_captcha(
                        page,
                        str(item.get("name") or ""),
                        str(item.get("certificate_number") or ""),
                        on_progress=_retry_progress,
                        max_attempts=3,
                    )
                    def show_result(iid=iid, ok=success, st=status):
                        item = self.education_items.get(iid)
                        if item:
                            if ok:
                                item["status"] = "已提交查询"
                                item["detail"] = "验证码已识别并自动提交查询，请等待页面显示二维码。"
                                item["warnings"] = "验证码通过后按页面提示使用手机扫码。"
                            elif st == "识别失败":
                                item["status"] = "识别失败"
                                item["detail"] = "验证码连续 3 次识别错误，请再次点击「重新识别验证码」重试。"
                                item["warnings"] = ""
                            else:
                                item["status"] = "待人工验证"
                                item["detail"] = "验证码连续 3 次自动识别失败，请人工输入。"
                                item["warnings"] = ""
                            self._update_education_queue_row(iid)
                            if self.education_current_id == iid:
                                self.education_status_var.set(item["detail"])
                                self.education_warning_var.set(item["warnings"])
                        # 所有任务完成后才恢复按钮状态
                        self._check_all_captcha_tasks_done()
                    self.run_on_ui(show_result)
                except Exception as error:
                    error_text = str(error)
                    self._log_education_error("重新识别验证码", error, iid)
                    def show_error(iid=iid, err=error_text):
                        item = self.education_items.get(iid)
                        if item:
                            item["status"] = "识别失败"
                            item["detail"] = "验证码识别出错"
                            item["warnings"] = err
                            self._update_education_queue_row(iid)
                            if self.education_current_id == iid:
                                self.education_status_var.set(item["detail"])
                                self.education_warning_var.set(err)
                        self._check_all_captcha_tasks_done()
                    self.run_on_ui(show_error)
            threading.Thread(target=worker, daemon=True).start()

    def _check_all_captcha_tasks_done(self):
        """检查是否所有验证码识别任务都已完成，完成则恢复按钮状态。"""
        for item in self.education_items.values():
            if item.get("status") == "识别验证码中...":
                return  # 还有任务在进行中
        self._set_captcha_btn_state("normal")

    @staticmethod
    def _is_browser_page_alive(page) -> bool:
        """Confirm that a browser page can still execute commands."""
        return BrowserController.is_page_alive(page)

    def _try_reconnect_browser(self) -> bool:
        """Reconnect through the browser lifecycle controller."""
        if self._is_browser_page_alive(self.browser_page):
            self.browser_connected = True
            return True
        state = _browser_controller_for(self).reconnect(
            getattr(self, "browser_address", ""),
        )
        self.browser_page = state.page
        self.browser_connected = state.connected
        if state.address:
            self.browser_address = state.address
        return state.connected

    def _launch_boss_browser(self) -> bool:
        """Start the managed Chrome profile on the recommendation page."""
        target_url = "https://www.zhipin.com/web/chat/recommend"
        state = _browser_controller_for(self).launch_managed_chrome(
            target_url,
            recommend_matcher=self._is_boss_recommend_url,
            should_stop=lambda: bool(
                getattr(self, "stop_event", None)
                and self.stop_event.is_set()
            ),
        )
        self.browser_page = state.page
        self.browser_connected = state.connected
        if state.address:
            self.browser_address = state.address
        self._greet_queue_browser_error = state.error
        return state.connected
    def _get_education_api_key(self, config: dict) -> str:
        """按运行模式取得学历核验专用 API Key。"""
        if self._education_api_key_provider is not None:
            return str(self._education_api_key_provider() or "")
        provider = str(config.get("api_provider") or "")
        if not provider:
            return ""
        return self._get_api_key_cached(provider, config.get("base_url", ""))

    def _create_fresh_browser_page(self):
        """启动或连接新的 ChromiumPage，并验证连接。"""
        from DrissionPage import ChromiumOptions, ChromiumPage

        # 学历核验不依赖 BOSS 登录态。没有可复用浏览器时直接使用临时
        # Chrome，避免默认 9222 调试端口不可用时等待超时再报打开失败。
        options = ChromiumOptions(read_file=False)
        options.auto_port()
        page = ChromiumPage(options)
        if not self._is_browser_page_alive(page):
            raise RuntimeError("Chrome 已启动，但页面连接失败")
        return page

    def _get_education_tab(self, item_id: str | None):
        """获取候选人专属 tab；item_id 为 None 时仅确保 base 浏览器连接就绪。"""
        # item_id 为 None：仅确保 base 浏览器连接可用
        if item_id is None:
            base_page = self.browser_page
            if self._is_browser_page_alive(base_page):
                return None
            self.browser_page = None
            self.browser_connected = False
            if self._try_reconnect_browser():
                candidate = self.browser_page
                if self._is_browser_page_alive(candidate):
                    return None
                self.browser_page = None
                self.browser_connected = False
            page = self._create_fresh_browser_page()
            self.browser_page = page
            self.browser_connected = True
            try:
                self.browser_address = page.address
            except Exception:
                pass
            return None

        # 检查已有的 per-item tab
        tab = self.education_tabs.get(item_id)
        if self._is_browser_page_alive(tab):
            return tab
        self.education_tabs.pop(item_id, None)

        # 确保 base 浏览器连接可用（内部自带锁）
        base_page = self.browser_page
        if not self._is_browser_page_alive(base_page):
            self.browser_page = None
            self.browser_connected = False
            base_page = None
            if self._try_reconnect_browser():
                candidate = self.browser_page
                if self._is_browser_page_alive(candidate):
                    base_page = candidate
                else:
                    self.browser_page = None
                    self.browser_connected = False
            if base_page is None:
                page = self._create_fresh_browser_page()
                self.browser_page = page
                self.browser_connected = True
                try:
                    self.browser_address = page.address
                except Exception:
                    pass
                base_page = page

        # 在 base 上创建新 tab
        try:
            tab = base_page.new_tab()
            if not self._is_browser_page_alive(tab):
                raise RuntimeError("新标签页连接失败")
        except Exception:
            self.browser_page = None
            self.browser_connected = False
            page = self._create_fresh_browser_page()
            self.browser_page = page
            self.browser_connected = True
            try:
                self.browser_address = page.address
            except Exception:
                pass
            tab = page
        self.education_tabs[item_id] = tab
        return tab

    def create_stats_page(self):
        """创建数据统计页面 - 按岗位维度展示筛选和打招呼统计"""
        widgets = gui_stats_page.build_stats_page(self, UI_CONFIG)
        self._stats_page_widgets = widgets
        self.stats_page = widgets.page
        self.stats_job_var = widgets.job_var
        self.stats_job_combo = widgets.job_combo
        self.stats_time_var = widgets.time_var
        self.stats_summary_vars = widgets.summary_vars
        self.stats_tree = widgets.tree

    def _load_stats_candidates(self, job_name=None):
        """Load candidates with the current stats filters applied.

        When *job_name* is provided it overrides the stats dropdown filter,
        avoiding a redundant second filter pass in callers like job review.
        """
        if not CANDIDATES_PATH.exists():
            return []
        candidates = load_candidates_all(CANDIDATES_PATH)
        candidates = [c for c in candidates if not c.get('blacklisted')]

        if job_name is None:
            job_name = self.stats_job_var.get() if hasattr(self, 'stats_job_var') else "全部岗位"
        if job_name != "全部岗位":
            candidates = [
                c for c in candidates
                if normalize_job_name(c.get('job_name')) == normalize_job_name(job_name)
            ]

        time_range = self.stats_time_var.get() if hasattr(self, 'stats_time_var') else "全部"
        if time_range != "全部":
            cutoff = self._stats_time_cutoff(time_range)
            if cutoff:
                cutoff_str = cutoff.strftime("%Y%m%d_%H%M%S")
                candidates = [
                    c for c in candidates
                    if (c.get('first_seen_at') or c.get('batch_timestamp', '')) >= cutoff_str
                ]
        return candidates

    @staticmethod
    def _stats_time_cutoff(time_range):
        return stats_presenter.stats_time_cutoff(time_range)

    def _show_stats_context_menu(self, event):
        item = self.stats_tree.identify_row(event.y)
        if not item:
            return
        self.stats_tree.selection_set(item)
        menu = tk.Menu(self.root, tearoff=0, font=(FONT_FAMILY, int(11 * self.font_scale)))
        icon_review = self.icons.button('chart', self.colors['primary'])
        menu._icon_refs = [icon_review]
        menu.add_command(
            label=" 岗位复盘",
            image=icon_review,
            compound=tk.LEFT,
            command=self._show_selected_job_review,
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _selected_stats_job_name(self):
        selection = self.stats_tree.selection() if hasattr(self, 'stats_tree') else ()
        if selection:
            values = self.stats_tree.item(selection[0], 'values')
            if values:
                return str(values[0])
        selected_job = self.stats_job_var.get() if hasattr(self, 'stats_job_var') else "全部岗位"
        return selected_job if selected_job != "全部岗位" else ""

    def _show_selected_job_review(self):
        job_name = self._selected_stats_job_name()
        if not job_name:
            messagebox.showinfo("岗位复盘", "请先在岗位明细中选择一个岗位，或在岗位过滤中选择具体岗位。")
            return
        candidates = self._load_stats_candidates(job_name=job_name)
        if not candidates:
            messagebox.showinfo("岗位复盘", f"{job_name} 在当前时间范围内没有可复盘候选人。")
            return
        review = self._build_job_review_model(job_name, candidates)
        self._show_job_review_workbench(job_name, candidates, review)

    def _show_job_review_workbench(self, job_name, candidates, review):
        """Show one job review as a structured, evidence-first workbench."""
        time_range = (
            self.stats_time_var.get()
            if hasattr(self, 'stats_time_var')
            else "全部"
        )
        callbacks = gui_job_review.JobReviewCallbacks(
            show_feedback_candidates=lambda: (
                self._show_job_review_feedback_candidates(job_name, candidates)
            ),
            open_job_config=lambda: self._open_job_config_from_review(job_name),
            format_suggestion=self._format_job_review_suggestion,
        )
        return gui_job_review.build_job_review_workbench(
            self,
            job_name=job_name,
            time_range=time_range,
            review=review,
            callbacks=callbacks,
            font_family=FONT_FAMILY,
        )

    def _show_job_review_feedback_candidates(self, job_name, candidates):
        """Show the feedback samples behind a job review without changing them."""
        feedback_candidates = [
            candidate for candidate in candidates
            if candidate.get('feedback_status') in FEEDBACK_STATUS_OPTIONS
        ]
        lines = [f"{job_name} 反馈候选人（{len(feedback_candidates)} 人）", ""]
        for index, candidate in enumerate(feedback_candidates, start=1):
            name = str(candidate.get('name') or '姓名缺失').strip()
            score = candidate.get('match_score')
            score_text = f"{score} 分" if score is not None else "评分缺失"
            status = candidate.get('feedback_status') or "未反馈"
            reasons = "、".join(self._feedback_reasons(candidate)) or "未填写"
            note = str(candidate.get('feedback_note') or '').strip()
            lines.extend([
                f"{index}. {name}｜{score_text}｜{status}",
                f"   原因：{reasons}",
            ])
            if note:
                lines.append(f"   备注：{note}")
            lines.append("")
        self.input_support.show_text_dialog(
            f"反馈候选人 - {job_name}",
            "\n".join(lines).rstrip(),
            width=720,
            height=520,
        )

    def _open_job_config_from_review(self, job_name):
        """Navigate from a job review to the matching saved job configuration."""
        def _select_reviewed_job() -> None:
            normalized_target = " ".join(str(job_name or '').strip().split()).casefold()
            matched_job = next(
                (
                    saved_name for saved_name in self.job_rules
                    if " ".join(str(saved_name).strip().split()).casefold() == normalized_target
                ),
                "",
            )
            if not matched_job:
                messagebox.showwarning(
                    "岗位配置不存在",
                    f"未找到“{job_name}”的已保存岗位配置。",
                    parent=self.root,
                )
                return
            if self.config_job_combo.get() == matched_job:
                return
            self.config_job_combo.set(matched_job)
            self.on_job_selected(None)

        self.app_shell.request_sidebar_page(PageIndex.CONFIG, on_ready=_select_reviewed_job)

    @staticmethod
    def _feedback_reasons(candidate):
        return stats_presenter.feedback_reasons(candidate)

    @staticmethod
    def _format_job_review_suggestion(suggestion):
        """Split one suggestion into a short heading and supporting detail."""
        return stats_presenter.format_job_review_suggestion(suggestion)

    def _build_job_review_model(self, job_name, candidates):
        """Build the shared job-review model without changing candidate data."""
        return stats_presenter.build_job_review_model(job_name, candidates)

    def _build_job_review_text(self, job_name, candidates):
        """Build the compatibility text report from the shared review model."""
        return stats_presenter.build_job_review_text(job_name, candidates)

    _REASON_SUGGESTIONS = stats_presenter.REASON_SUGGESTIONS


    def refresh_stats(self):
        """刷新数据统计页面 - 按岗位维度聚合"""
        # 数据未变 + 过滤条件未变 → 跳过 Treeview 重建，避免页面切换卡顿
        current_job = self.stats_job_var.get() if hasattr(self, 'stats_job_var') else ""
        current_time = self.stats_time_var.get() if hasattr(self, 'stats_time_var') else ""
        if CANDIDATES_PATH.exists():
            stat = CANDIDATES_PATH.stat()
            fingerprint = (stat.st_mtime, stat.st_size)
            if (fingerprint == self._stats_tree_fingerprint
                    and current_job == self._stats_last_job
                    and current_time == self._stats_last_time):
                return
            self._stats_tree_fingerprint = fingerprint
            self._stats_last_job = current_job
            self._stats_last_time = current_time
        elif self._stats_tree_fingerprint is not None:
            self._stats_tree_fingerprint = None

        try:
            candidates = self._load_stats_candidates()
            dashboard = stats_presenter.build_stats_dashboard(candidates)
            for key, value in dashboard["summary"].items():
                self.stats_summary_vars[key].set(str(value))

            # 清空表格
            for item in self.stats_tree.get_children():
                self.stats_tree.delete(item)
            # 插入表格行（斑马纹便于宽表横向扫读）
            self.stats_tree.tag_configure(
                'zebra_odd', background=self.colors.get('bg_zebra', ui_theme.BG_ZEBRA)
            )
            for row_index, values in enumerate(dashboard["rows"]):
                self.stats_tree.insert(
                    "",
                    "end",
                    values=values,
                    tags=('zebra_odd',) if row_index % 2 else (),
                )

        except Exception as e:
            self.append_log(f"刷新统计失败：{e}")

    def show_page_home(self):
        """显示首页"""
        if self.home_page is None:
            self.create_home_page()
        self.hide_all_pages()
        self.home_page.pack(fill="both", expand=True)
        self.current_page_index = PageIndex.HOME
        self.app_shell.schedule_page_width_policy()
        self.update_nav_highlight()
        # 刷新岗位过滤列表
        try:
            job_rules = self._get_job_rules_cached()
            jobs = ["全部岗位"] + list(job_rules.keys())
            self.home_job_combo['values'] = jobs
        except Exception:
            pass
        self._defer_ui_work(
            "home_stats", self.refresh_home_stats, page_index=PageIndex.HOME
        )

    def show_page_config(self):
        """显示配置页面"""
        if self.config_page is None:
            self.create_config_page()
        self.hide_all_pages()
        self.config_page.pack(fill="both", expand=True)
        self.current_page_index = PageIndex.CONFIG
        self.app_shell.schedule_page_width_policy()
        # 刷新技能树和必要条件列表
        if self.job_rules:
            self._defer_ui_work(
                "config_lists",
                self._refresh_config_lists_if_needed,
                page_index=PageIndex.CONFIG,
            )
        # 始终显示详细结果区域（基本信息、技能关键词、必要条件、话术模板）
        self.result_detail_frame.pack(fill="both", expand=True, padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))
        self.update_nav_highlight()
        # 重新绑定滚轮事件（覆盖动态创建的控件）
        self.scroll_support.bind_mousewheel(
            self.config_canvas,
            self.config_scrollable_frame,
        )

    def show_page_run(self):
        """显示运行页面"""
        if self.run_page is None:
            self.create_run_page()
        self.hide_all_pages()
        self.run_page.pack(fill="both", expand=True)
        self.current_page_index = PageIndex.RUN
        self.app_shell.schedule_page_width_policy()
        self.update_nav_highlight()
        # 恢复浏览器自动检测（仅检测连接，不启动浏览器）
        self._start_browser_auto_check()
        # 刷新岗位选择列表
        try:
            job_rules = self._get_job_rules_cached()
            self._sync_run_job_combo_values(job_rules)
        except Exception:
            pass
        # 重新绑定滚轮事件（覆盖动态创建的控件）
        self.scroll_support.bind_mousewheel(
            self.run_canvas,
            self.run_scrollable_frame,
        )

    def show_page_result(self):
        """显示结果页面"""
        if self.result_page is None:
            self.create_result_page()
        self.hide_all_pages()
        self.result_page.pack(fill="both", expand=True)
        self.current_page_index = PageIndex.RESULTS
        self.app_shell.schedule_page_width_policy()
        self.update_nav_highlight()
        # 刷新岗位过滤列表
        try:
            job_rules = self._get_job_rules_cached()
            jobs = ["全部岗位"] + list(job_rules.keys())
            self.result_job_combo['values'] = jobs
        except Exception:
            pass
        self._defer_ui_work(
            "results_refresh", self.refresh_results, page_index=PageIndex.RESULTS
        )

    def show_page_stats(self):
        """显示数据统计页面"""
        if self.stats_page is None:
            self.create_stats_page()
        self.hide_all_pages()
        self.stats_page.pack(fill="both", expand=True)
        self.current_page_index = PageIndex.STATS
        self.app_shell.schedule_page_width_policy()
        self.update_nav_highlight()
        # 刷新岗位过滤列表
        try:
            job_rules = self._get_job_rules_cached()
            jobs = ["全部岗位"] + list(job_rules.keys())
            self.stats_job_combo['values'] = jobs
        except Exception:
            pass
        self._defer_ui_work(
            "stats_refresh", self.refresh_stats, page_index=PageIndex.STATS
        )

    def show_page_education(self):
        """显示学历核验页面。"""
        if self.education_page is None:
            self.create_education_page()
        self.hide_all_pages()
        self.education_page.pack(fill="both", expand=True)
        self.current_page_index = PageIndex.EDUCATION
        self.app_shell.schedule_page_width_policy()
        self.update_nav_highlight()
        self.scroll_support.bind_mousewheel(
            self.education_canvas,
            self.education_scrollable_frame,
        )

    def show_page_api(self):
        """显示 API 配置页面（系统设置）"""
        if self.api_config_page is None:
            self.create_api_config_page()
        # 配置文件已在启动时读取；在页面首次绘制前回填普通字段，避免短暂显示空下拉框。
        # 普通字段先回填，API Key 等页面首帧绘制后再到后台读取。
        self._load_api_config_to_ui_if_needed()
        self.hide_all_pages()
        self.api_config_page.pack(fill="both", expand=True)
        self.current_page_index = PageIndex.SETTINGS
        self.app_shell.schedule_page_width_policy()
        self.update_nav_highlight()
        # 重新绑定滚轮事件（覆盖动态创建的控件）
        self.scroll_support.bind_mousewheel(
            self.api_canvas,
            self.api_scrollable_frame,
        )
        self._schedule_api_key_resolution()

    def hide_all_pages(self):
        """Compatibility facade for scripts that hide all pages."""
        self.app_shell.hide_all_pages()

    def update_nav_highlight(self):
        """Compatibility facade for scripts that refresh sidebar selection."""
        self.app_shell.update_nav_highlight()

    def _set_result_contact_badge(self, count):
        """在“联系候选人”按钮右上角显示发送结果待核实数。"""
        pending = max(0, int(count or 0))
        self._result_contact_pending_count = pending
        badge = getattr(self, 'result_greet_queue_badge', None)
        if not badge or not badge.winfo_exists():
            return
        if pending:
            badge.configure(text=str(pending if pending < 100 else '99+'))
            badge.place(
                relx=1.0,
                rely=0.0,
                x=-1,
                y=-1,
                anchor="ne",
            )
            badge.lift()
        else:
            badge.place_forget()

    def _refresh_contact_queue_badge(self):
        """刷新结果页“联系候选人”按钮上的待核实角标。"""
        try:
            if self._greet_queue_loaded:
                pending = count_pending_contact_queue(self.greet_queue_items)
            else:
                pending = load_pending_contact_queue_count(CONTACT_QUEUE_PATH)
            self.app_shell.set_nav_badge(PageIndex.RESULTS, 0)
            self._set_result_contact_badge(pending)
        except Exception as exc:
            logger.warning("刷新联系清单角标失败：%s", exc)

    def _show_result_contact_badge_tooltip(self, event):
        """解释“联系候选人”按钮角标的业务含义。"""
        pending = getattr(self, '_result_contact_pending_count', 0)
        if pending <= 0:
            return
        self.feedback_support.show_tooltip(
            f"{pending} 人发送结果待核实",
            event.x_root + int(12 * self.dpi_scale * self.zoom_factor),
            event.y_root + int(12 * self.dpi_scale * self.zoom_factor),
            ("result_contact_pending",),
        )

    def refresh_home_stats(self):
        """刷新首页统计"""
        selected_job = self.home_job_var.get() if hasattr(self, 'home_job_var') else ""
        if CANDIDATES_PATH.exists():
            stat = CANDIDATES_PATH.stat()
            fingerprint = (stat.st_mtime, stat.st_size)
            if (fingerprint == self._home_stats_fingerprint
                    and selected_job == self._home_stats_last_job):
                return
            self._home_stats_fingerprint = fingerprint
            self._home_stats_last_job = selected_job
        else:
            if self._home_stats_fingerprint is None and self._home_stats_last_job == selected_job:
                return
            self._home_stats_fingerprint = None
            self._home_stats_last_job = selected_job
            for var in self.home_stats_vars.values():
                var.set("0")
            return

        try:
            if CANDIDATES_PATH.exists():
                candidates = load_candidates_all(CANDIDATES_PATH)
                candidates = [c for c in candidates if not c.get('blacklisted')]

                # 岗位过滤
                if selected_job != "全部岗位":
                    candidates = [
                        c for c in candidates
                        if normalize_job_name(c.get('job_name')) == normalize_job_name(selected_job)
                    ]

                # 淘汰结论优先于历史分数，首页与结果页使用同一决策口径。
                candidates = [
                    c for c in candidates
                    if derive_candidate_decision(c).screening_result
                    in {'强烈推荐', '推荐', '待定'}
                ]

                total = len(candidates)
                greeted = sum(1 for c in candidates if c.get('greet_sent', False))
                strong = sum(
                    1 for c in candidates
                    if derive_candidate_decision(c).screening_result == '强烈推荐'
                )
                recommended = sum(
                    1 for c in candidates
                    if derive_candidate_decision(c).screening_result == '推荐'
                )

                self.home_stats_vars['total_home'].set(str(total))
                self.home_stats_vars['recommended_home'].set(str(recommended))
                self.home_stats_vars['greeted_home'].set(str(greeted))
                self.home_stats_vars['strong_home'].set(str(strong))
        except Exception as e:
            print(f"刷新首页统计失败：{e}")

        # 如果当前在数据统计页，同步刷新统计
        if self.current_page_index == PageIndex.STATS:
            self.refresh_stats()

    def _center_window(self, window, width, height):
        """将子窗口相对于主窗口居中"""
        _place_window_centered(window, width, height, parent=self.root)

    def _set_window_icon(self):
        """设置窗口图标，替换 tkinter 默认羽毛图标"""
        try:
            # 用 iconphoto 设置高分图标，Windows 10/11 原生缩放比 ICO 清晰
            icons.set_search_window_icon(self.root)
        except Exception:
            pass  # 图标设置失败不影响程序运行

    def _stats_detail_row_values(self, candidate):
        """Format one candidate row for the shared statistics detail dialog."""
        score = candidate.get('match_score', 0)
        level = derive_candidate_decision(candidate).screening_result
        status = self._format_candidate_status(candidate)
        salary, exp = self._parse_salary_exp(
            candidate.get('summary', ''),
            candidate.get('structured'),
        )
        ai_adjustment = candidate.get('llm_adjustment')
        resume_adjustment = candidate.get('resume_eval_adjustment')
        if resume_adjustment is not None:
            ai_text = (
                f"+{resume_adjustment}"
                if resume_adjustment > 0
                else str(resume_adjustment)
            )
        elif ai_adjustment is not None and candidate.get('llm_evaluated'):
            ai_text = (
                f"+{ai_adjustment}" if ai_adjustment > 0 else str(ai_adjustment)
            )
        else:
            ai_text = "—"
        return (
            candidate.get('name', ''),
            self._candidate_gender_display(candidate),
            exp,
            salary,
            candidate.get('skill_match_ratio', ''),
            score,
            ai_text,
            level,
            status,
        )

    def _remove_stats_detail_candidates(self, candidates):
        """Persist removals requested from a statistics detail dialog."""
        removable = [
            candidate
            for candidate in candidates
            if self._candidate_identity_key(candidate)[0]
        ]
        remove_keys = {
            self._candidate_identity_key(candidate) for candidate in removable
        }
        if remove_keys and CANDIDATES_PATH.exists():
            self._remove_candidate_records(
                lambda item: self._candidate_identity_key(item) in remove_keys,
            )
        return removable

    def _show_stats_detail_dialog(
        self,
        title,
        candidates,
        *,
        refresh,
        lift_after_batch_remove=False,
    ):
        """Delegate shared statistics detail-window construction."""
        callbacks = gui_stats_detail.StatsDetailCallbacks(
            row_values=self._stats_detail_row_values,
            export_candidates=self._run_export,
            add_to_queue=lambda selected, parent: self._add_candidates_to_greet_queue(
                selected,
                parent=parent,
            ),
            batch_ai_eval_label=self._batch_ai_eval_menu_label,
            evaluate_candidates=self._ai_eval_selected_candidates,
            confirm_manual_review=lambda selected, parent: (
                self._batch_confirm_manual_review(selected, parent=parent)
            ),
            open_review=self._open_candidate_review_workbench,
            show_candidate_menu=self._build_candidate_context_menu,
            bind_tooltip=self._bind_detail_tree_tooltip,
            remove_candidates=self._remove_stats_detail_candidates,
            refresh=refresh,
        )
        return gui_stats_detail.show_stats_detail_dialog(
            self,
            title=title,
            candidates=candidates,
            ui_config=UI_CONFIG,
            font_family=FONT_FAMILY,
            callbacks=callbacks,
            lift_after_batch_remove=lift_after_batch_remove,
        )

    def _refresh_home_stats_detail(self):
        """Refresh both summaries affected by a home detail-window removal."""
        self.refresh_home_stats()
        self.refresh_results()

    def show_stat_detail(self, stat_type):
        """显示首页统计详情"""
        try:
            if not CANDIDATES_PATH.exists():
                self.feedback_support.show_inline_banner(
                    self.home_page,
                    'info',
                    "暂无候选人数据，请先到运行控制页开始筛选。",
                )
                return

            candidates = [
                candidate
                for candidate in load_candidates_all(CANDIDATES_PATH)
                if not candidate.get('blacklisted')
            ]
            if hasattr(self, 'home_job_var'):
                selected_job = self.home_job_var.get()
                if selected_job != "全部岗位":
                    candidates = [
                        candidate
                        for candidate in candidates
                        if normalize_job_name(candidate.get('job_name'))
                        == normalize_job_name(selected_job)
                    ]

            if stat_type == 'total_home':
                title = "通过筛选"
                filtered = [
                    candidate
                    for candidate in candidates
                    if derive_candidate_decision(candidate).screening_result
                    in {'强烈推荐', '推荐', '待定'}
                ]
            elif stat_type == 'strong_home':
                title = "强烈推荐"
                filtered = [
                    candidate
                    for candidate in candidates
                    if derive_candidate_decision(candidate).screening_result
                    == '强烈推荐'
                ]
            elif stat_type == 'recommended_home':
                title = "推荐"
                filtered = [
                    candidate
                    for candidate in candidates
                    if derive_candidate_decision(candidate).screening_result == '推荐'
                ]
            elif stat_type == 'greeted_home':
                title = "已打招呼"
                filtered = [
                    candidate
                    for candidate in candidates
                    if derive_candidate_decision(candidate).screening_result
                    in {'强烈推荐', '推荐', '待定'}
                    and candidate.get('greet_sent', False)
                ]
            else:
                return

            if not filtered:
                self.feedback_support.show_inline_banner(
                    self.home_page,
                    'info',
                    f"{title}：暂无数据。",
                )
                return
            self._show_stats_detail_dialog(
                title,
                filtered,
                refresh=self._refresh_home_stats_detail,
            )
        except Exception as exc:
            messagebox.showerror("错误", f"显示详情失败：{exc}")

    def show_result_stat_detail(self, stat_type):
        """显示筛选结果统计详情"""
        try:
            if not CANDIDATES_PATH.exists():
                self.feedback_support.show_inline_banner(
                    self.result_page,
                    'info',
                    "暂无候选人数据，请先到运行控制页开始筛选。",
                )
                return

            candidates = [
                candidate
                for candidate in load_candidates_all(CANDIDATES_PATH)
                if not candidate.get('blacklisted')
            ]
            if hasattr(self, 'result_job_var'):
                selected_job = self.result_job_var.get()
                if selected_job != "全部岗位":
                    candidates = [
                        candidate
                        for candidate in candidates
                        if normalize_job_name(candidate.get('job_name'))
                        == normalize_job_name(selected_job)
                    ]

            date_start, date_end = (
                self._get_result_date_filter()
                if hasattr(self, 'result_date_start_entry')
                else (None, None)
            )
            if date_start or date_end:
                def in_date_range(candidate):
                    timestamp = (
                        candidate.get('first_seen_at')
                        or candidate.get('batch_timestamp', '')
                    )
                    if not timestamp or len(timestamp) < 8:
                        return False
                    candidate_date = timestamp[:8]
                    if date_start and candidate_date < date_start:
                        return False
                    if date_end and candidate_date > date_end:
                        return False
                    return True

                candidates = [
                    candidate
                    for candidate in candidates
                    if in_date_range(candidate)
                ]

            if stat_type == 'strong':
                title = "强烈推荐"
                filtered = [
                    candidate
                    for candidate in candidates
                    if derive_candidate_decision(candidate).screening_result
                    == '强烈推荐'
                ]
            elif stat_type == 'recommended':
                title = "推荐"
                filtered = [
                    candidate
                    for candidate in candidates
                    if derive_candidate_decision(candidate).screening_result == '推荐'
                ]
            elif stat_type == 'pending':
                title = "待定"
                filtered = [
                    candidate
                    for candidate in candidates
                    if derive_candidate_decision(candidate).screening_result == '待定'
                ]
            elif stat_type == 'greeted':
                title = "已打招呼"
                filtered = [
                    candidate
                    for candidate in candidates
                    if derive_candidate_decision(candidate).screening_result
                    in {'强烈推荐', '推荐', '待定'}
                    and candidate.get('greet_sent', False)
                ]
            else:
                return

            if not filtered:
                self.feedback_support.show_inline_banner(
                    self.result_page,
                    'info',
                    f"{title}：暂无数据。",
                )
                return
            self._show_stats_detail_dialog(
                title,
                filtered,
                refresh=self.refresh_results,
                lift_after_batch_remove=True,
            )
        except Exception as exc:
            messagebox.showerror("错误", f"显示详情失败：{exc}")

    def _get_job_rules_cached(self):
        """缓存读取 job_config.json，文件 mtime 未变则跳过磁盘 IO。

        页面切换时调用，避免每次切页面都读一遍配置文件。
        """
        mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0
        if mtime != self._job_rules_mtime:
            self._job_rules_cache = self._read_job_rules_from_file()
            self._job_rules_mtime = mtime
        return self._job_rules_cache or {}

    def _read_job_rules_from_file(self):
        """轻量读取岗位规则，避免 GUI 首屏 import 自动化主程序。"""
        if not CONFIG_PATH.exists() and not CONFIG_BACKUP_PATH.exists():
            return {}
        try:
            config = load_job_config_snapshot(CONFIG_PATH, CONFIG_BACKUP_PATH)
        except (OSError, ValueError, RuntimeError):
            return {}

        if "job_requirements" in config and isinstance(config["job_requirements"], dict):
            return config["job_requirements"]
        if "jobs" in config and isinstance(config["jobs"], dict):
            return config["jobs"]
        return {}

    def load_config(self):
        """加载岗位配置"""
        try:
            config = load_job_config_snapshot(CONFIG_PATH, CONFIG_BACKUP_PATH)
        except (OSError, ValueError, RuntimeError) as e:
            print(f"加载岗位配置失败：{e}")
            config = {}
        if "job_requirements" in config:
            self.job_rules = config["job_requirements"]
        elif "jobs" in config:
            self.job_rules = config["jobs"]
        else:
            self.job_rules = {}

    def load_api_config(self, resolve_keys=True):
        """加载 API 配置 - 从系统钥匙串读取加密的 API Key（按服务商管理）"""
        api_config_path = get_api_config_path()
        if api_config_path.exists():
            try:
                with open(api_config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 确保所有必要字段都存在（兼容旧版本配置文件）
                    self.api_config = {
                        "api_provider": config.get("api_provider", "deepseek"),
                        "api_key": "",  # API Key 从 keyring 读取
                        "base_url": config.get("base_url", "https://api.deepseek.com"),
                        "model": config.get("model", "deepseek-chat"),
                        "saved_models": config.get("saved_models", []),
                        "providers": config.get("providers", {}),
                        "fetched_models": config.get("fetched_models", {}),
                        "llm_read_timeout": config.get("llm_read_timeout"),
                        "education_model_ref": config.get("education_model_ref"),
                    }

                    # 从 keyring 读取所有 saved_models 的 API Key（按服务商）
                    # 同时清理文件中可能已泄露的明文 Key（防御性清理）
                    for model_config in self.api_config["saved_models"]:
                        model_config.pop("api_key", None)
                        model_config.pop("api_key_ref", None)

                    if not resolve_keys:
                        return

                    # 从 keyring 读取当前服务商的 API Key
                    current_provider = self.api_config.get("api_provider", "")
                    if current_provider:
                        encrypted_key = self._get_api_key_cached(
                            current_provider, self.api_config.get("base_url", "")
                        )
                        if encrypted_key:
                            self.api_config["api_key"] = encrypted_key
            except Exception as e:
                print(f"加载 API 配置失败：{e}")
                self.api_config = self._default_api_config()
        else:
            self.api_config = self._default_api_config()

    def _default_api_config(self):
        """返回默认 API 配置"""
        return _SETTINGS_CONTROLLER.default_api_config()

    def _sanitize_config_for_save(self, config):
        """移除所有 api_key 字段（顶层 + saved_models 内嵌），返回可安全写入磁盘的副本"""
        return _SETTINGS_CONTROLLER.sanitize_for_save(config)


    def _on_default_model_selected(self, event=None):
        """将用途选择器中的模型设为默认 AI 模型。"""
        if getattr(self, '_updating_model_assignment_controls', False):
            return
        model_config = self._model_choice_refs.get(self.default_model_choice_var.get())
        if model_config and not self._activate_saved_model(model_config, announce=False):
            self._refresh_model_assignment_controls()

    def _on_education_model_selected(self, event=None):
        """显式设置学历核验模型，或选择跟随默认模型。"""
        if getattr(self, '_updating_model_assignment_controls', False):
            return
        choice = self.education_model_choice_var.get()
        if choice == "跟随默认 AI 模型":
            self._unset_education_model()
            return
        model_config = self._model_choice_refs.get(choice)
        if model_config:
            self._set_education_model_ref(model_config)

    def _set_education_model_ref(self, model_config):
        """保存学历核验模型的完整连接身份。"""
        self.api_config["education_model_ref"] = {
            "api_provider": model_config.get("api_provider", ""),
            "base_url": model_config.get("base_url", ""),
            "model": model_config.get("model", ""),
        }
        self._save_api_config_to_file()
        self._mark_api_config_ui_current()
        self.load_saved_models_to_tree()
        provider_display = self.PROVIDER_DISPLAY.get(
            model_config.get("api_provider", ""), model_config.get("api_provider", "")
        )
        self._update_api_status(
            text=f"✓ 学历核验模型已设为 {provider_display} / {model_config.get('model', '')}",
            foreground=self.colors['success'],
        )

    def _test_assigned_model(self, role):
        """复用模型库连通性测试，测试指定用途当前实际使用的模型。"""
        model_ref = self._get_assigned_model_ref(role)
        synced_roles = self._assigned_model_test_roles(role, model_ref)
        for target_role in synced_roles:
            self._assigned_model_test_tokens[target_role] += 1
            self._assigned_model_test_refs[target_role] = dict(model_ref)
            self._set_assigned_model_test_state(target_role, "testing")
        test_token = self._assigned_model_test_tokens[role]
        self._update_api_status(
            text=f"正在测试{self._assigned_model_test_target_label(role, model_ref)}...",
            foreground=self.colors['warning'],
        )
        for item_id in self.model_list_tree.get_children():
            values = self.model_list_tree.item(item_id, 'values')
            if len(values) < 4:
                continue
            item_ref = {
                "model": values[0],
                "api_provider": self.DISPLAY_TO_KEY.get(values[1], values[1]),
                "base_url": values[3],
            }
            if self._model_ref_matches(item_ref, model_ref):
                self.model_list_tree.selection_set(item_id)
                self.test_saved_model_connectivity(
                    assigned_role=role,
                    assigned_model_ref=model_ref,
                    assigned_test_token=test_token,
                )
                return
        for target_role in synced_roles:
            self._set_assigned_model_test_state(target_role, "error")
        messagebox.showwarning("模型未保存", "当前模型不在已保存模型列表中，请先保存模型配置。")


    def _unset_education_model(self):
        """取消学历核验专用模型，回退默认 AI 模型"""
        if not hasattr(self, 'api_config') or not self.api_config:
            return
        if not self.api_config.get("education_model_ref"):
            self._refresh_model_assignment_controls()
            return
        self.api_config.pop("education_model_ref", None)
        self._save_api_config_to_file()
        self._mark_api_config_ui_current()
        self.load_saved_models_to_tree()
        self._update_api_status(
            text="✓ 学历核验模型已改为跟随默认 AI 模型",
            foreground=self.colors['success'],
        )

    def _save_api_config_to_file(self):
        """将当前 api_config 持久化到 api_config.json"""
        if not hasattr(self, 'api_config') or not self.api_config:
            return
        try:
            with open(get_api_config_path(for_write=True), 'w', encoding='utf-8') as f:
                json.dump(self._sanitize_config_for_save(self.api_config), f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存配置失败：{e}")

    def delete_selected_model(self):
        """删除选中的模型（支持多选）"""
        selection = self.model_list_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择要删除的模型")
            return

        # 收集所有选中模型的信息
        deleted = []
        for item_id in selection:
            item = self.model_list_tree.item(item_id)
            values = item.get('values', ())
            if len(values) < 4:
                continue
            model_name = values[0]
            provider_display = values[1]
            provider_key = self.DISPLAY_TO_KEY.get(provider_display, provider_display)
            deleted.append({
                "model": model_name,
                "api_provider": provider_key,
                "base_url": values[3],
                "provider_display": provider_display,
            })

        # 去重（同一模型可能被重复选中）
        unique_deleted = []
        seen = set()
        for model_ref in deleted:
            key = (
                model_ref.get("api_provider", ""),
                str(model_ref.get("base_url", "")).strip().rstrip("/"),
                model_ref.get("model", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            unique_deleted.append(model_ref)
        deleted = unique_deleted
        if not deleted:
            messagebox.showwarning("警告", "未找到选中模型的完整配置信息")
            return

        current_ref = {
            "api_provider": (self.api_config or {}).get("api_provider", ""),
            "base_url": (self.api_config or {}).get("base_url", ""),
            "model": (self.api_config or {}).get("model", ""),
        }
        edu_ref = (self.api_config or {}).get("education_model_ref") or {}
        for model_ref in deleted:
            if self._model_ref_matches(model_ref, current_ref):
                messagebox.showwarning("无法删除", "该模型正在作为默认 AI 模型使用，请先在“使用中的模型”中更换默认模型。")
                return
            if edu_ref and self._model_ref_matches(model_ref, edu_ref):
                messagebox.showwarning("无法删除", "该模型正在作为学历核验模型使用，请先在“使用中的模型”中更换，或改为跟随默认 AI 模型。")
                return

        count = len(deleted)
        if not messagebox.ask_confirmation(
            "删除已保存模型",
            headline=f"删除选中的 {count} 个模型？",
            message="这些模型将从已保存模型列表中移除。",
            metrics=(("模型", f"{count} 个"),),
            detail="\n".join(
                f"• {model_ref['model']}（{model_ref['provider_display']}）"
                for model_ref in deleted
            ),
            notice="删除后如需再次使用，必须重新添加模型配置。",
            yes_label="删除模型",
            no_label="取消",
            dangerous=True,
            parent=self.root,
        ):
            return

        # 从 saved_models 移除所有被选中的模型
        if hasattr(self, 'saved_models'):
            self.saved_models = [
                m for m in self.saved_models
                if not any(self._model_ref_matches(m, model_ref) for model_ref in deleted)
            ]

        if hasattr(self, 'api_config') and self.api_config:
            self.api_config["saved_models"] = self.saved_models
            try:
                save_config = self._sanitize_config_for_save(self.api_config)
                with open(get_api_config_path(for_write=True), 'w', encoding='utf-8') as f:
                    json.dump(save_config, f, ensure_ascii=False, indent=4)
                self._mark_api_config_ui_current()
            except Exception as e:
                print(f"保存配置失败：{e}")

        # 刷新显示
        self.load_saved_models_to_tree()
        if count == 1:
            status_text = f"✓ 已删除模型 {deleted[0]['model']}"
        else:
            status_text = f"✓ 已删除 {count} 个模型"
        self._update_api_status(text=status_text, foreground=self.colors['success'])

    def _update_api_status(self, text, foreground=None):
        """更新 API 状态标签，同时清理之前的可点击标签；⏳ 进行状态期间显示忙碌光标。"""
        # 清理之前的可点击标签
        for lbl in self._status_clickable_labels:
            lbl.destroy()
        self._status_clickable_labels.clear()
        # 更新主标签
        config = {"text": text}
        if foreground is not None:
            config["foreground"] = foreground
        self.api_status_label.config(**config)
        # ⏳ 是测试连接 / 获取模型列表等耗时操作的统一进行标记，终态文案到达时恢复光标
        root = getattr(self, 'root', None)
        if root is not None:
            try:
                root.config(cursor='watch' if str(text).startswith("⏳") else '')
            except tk.TclError:
                pass

    def _is_relay_endpoint_for_timeout(self) -> bool:
        """判断当前 API 配置是否为中转服务（用于读取超时默认值）"""
        return bool(classify_api_endpoint(self.api_config)["is_relay"])

    def _update_ai_eval_status(self):
        """更新 AI 评估状态标签和 checkbox 默认值（根据当前 API Key 是否已配置）"""
        if not hasattr(self, 'ai_status_label'):
            return  # UI 尚未创建完成
        has_key = bool(self.api_config.get("api_key"))
        has_model_config = bool(
            has_key
            and str(self.api_config.get("api_provider") or "").strip()
            and str(self.api_config.get("model") or "").strip()
        )
        available_var = getattr(self, 'ai_eval_available_var', None)
        if available_var is not None:
            available_var.set(has_model_config)
        # 首次检测到已配置 Key 时自动启用 AI 评估，后续不覆盖用户手动取消
        if not getattr(self, '_ai_eval_auto_done', False):
            self._ai_eval_auto_done = True
            if has_model_config:
                self.ai_eval_var.set(True)
        if has_model_config:
            self.ai_status_label.config(text="✓ 已配置", foreground=self.colors['success'])
        else:
            self.ai_status_label.config(text="⚠ 未配置", foreground=self.colors['warning'])
            # 模型配置不完整时强制关闭，且开关/文字点击均不允许重新启用。
            self.ai_eval_var.set(False)
        ai_label = getattr(self, 'ai_eval_label', None)
        if ai_label is not None:
            ai_label.configure(
                cursor='hand2' if has_model_config else 'arrow',
                foreground=(
                    self.colors['text_primary'] if has_model_config
                    else self.colors.get('text_muted', ui_theme.TEXT_MUTED)
                ),
            )


    def _activate_saved_model(self, model_config, announce=True):
        """将已保存模型设为默认 AI 模型。"""
        if not model_config:
            return False

        provider_key = model_config.get("api_provider", "")
        base_url = model_config.get("base_url", "")
        model_name = model_config.get("model", "")
        provider_display = self.PROVIDER_DISPLAY.get(provider_key, provider_key)
        saved_api_key = self._get_api_key_cached(provider_key, base_url)

        if not saved_api_key:
            self._update_api_status(
                text=f"⚠ {model_name} 缺少 API Key，请重新保存",
                foreground=self.colors['warning'],
            )
            messagebox.show_notice(
                "模型配置不完整",
                headline=f"{model_name} 缺少 API Key",
                message="请重新输入 API Key 并保存该模型。",
                detail="可能原因：系统凭据被清理，或配置文件来自其他电脑。",
                parent=getattr(self, "api_config_page", None) or getattr(self, "root", None),
            )
            return False

        self.api_provider_var.set(provider_display)
        self.api_key_var.set(saved_api_key)
        self.api_base_url_var.set(base_url)
        self.api_model_var.set(model_name)

        if hasattr(self, 'api_config') and self.api_config:
            self.api_config["model"] = model_name
            self.api_config["api_provider"] = provider_key
            self.api_config["api_key"] = saved_api_key
            self.api_config["base_url"] = base_url

        self.update_current_model_display()
        self.load_saved_models_to_tree()

        self._save_api_config_to_file()
        self._mark_api_config_ui_current()

        self._update_api_status(text=f"✓ 默认 AI 模型已设为 {provider_display} / {model_name}", foreground=self.colors['success'])
        self._update_ai_eval_status()
        if announce:
            self._status_flash(f"默认 AI 模型已切换为 {model_name}")
        return True

    def test_saved_model_connectivity(self, assigned_role=None, assigned_model_ref=None,
                                      assigned_test_token=None):
        """测试已保存模型连通性，结果直接回写到列表状态列。"""
        selection = self.model_list_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择要测试的模型（Ctrl+点击多选）")
            return

        # 收集所有选中模型的配置
        models_to_test = []
        for item_id in selection:
            item = self.model_list_tree.item(item_id)
            values = item.get('values', ())
            if len(values) < 4:
                continue
            model_name = values[0]
            provider_display = values[1]
            provider_key = self.DISPLAY_TO_KEY.get(provider_display, provider_display)
            selected_base_url = values[3]

            model_config = None
            for saved in getattr(self, 'saved_models', []):
                if self._model_ref_matches(saved, {
                    "model": model_name,
                    "api_provider": provider_key,
                    "base_url": selected_base_url,
                }):
                    model_config = saved
                    break
            if not model_config:
                continue

            base_url = model_config.get("base_url", "").strip()
            models_to_test.append({
                "item_id": item_id,
                "model_name": model_name,
                "provider_display": provider_display,
                "provider_key": provider_key,
                "model_config": model_config,
                "base_url": base_url,
                "assigned_role": assigned_role,
                "assigned_model_ref": assigned_model_ref,
                "assigned_test_token": assigned_test_token,
            })

        if not models_to_test:
            if assigned_role:
                self._set_assigned_model_test_state(assigned_role, "error")
            messagebox.showerror("错误", "未找到选中模型的配置信息")
            return

        total = len(models_to_test)
        assigned_target_label = (
            self._assigned_model_test_target_label(assigned_role, assigned_model_ref)
            if assigned_role else ""
        )
        status_text = (
            f"正在测试{assigned_target_label}..."
            if assigned_target_label else f"正在测试 {total} 个模型..."
        )
        self._update_api_status(text=status_text, foreground=self.colors['warning'])
        for entry in models_to_test:
            self._set_model_list_item_status(entry["item_id"], "测试中...")

        progress = {"done": 0, "success": 0, "fail": 0}

        def _test_one(entry):
            api_key = self._get_api_key_cached(
                entry["provider_key"], entry["base_url"]
            )
            if not api_key:
                result = {"status": "error", "msg": "API Key 未配置"}
            elif not entry["base_url"]:
                result = {"status": "error", "msg": "Base URL 未配置"}
            else:
                config = dict(entry["model_config"])
                config["api_provider"] = entry["provider_key"]
                connectivity = probe_model_capability(config, api_key)
                if connectivity.successful:
                    result = {
                        "status": "success",
                        "time": connectivity.elapsed_seconds,
                        "capability": dict(connectivity.capability),
                    }
                else:
                    result = {
                        "status": "error",
                        "msg": connectivity.message or "模型不兼容",
                    }

            self.run_on_ui(
                lambda entry=entry, result=result: self._apply_model_connectivity_result(
                    entry, result, progress, total
                )
            )

        for entry in models_to_test:
            t = threading.Thread(target=_test_one, args=(entry,), daemon=True)
            t.start()

    def _set_model_list_item_status(self, item_id, status_text):
        """Update only the saved-model status cell."""
        if not hasattr(self, 'model_list_tree'):
            return
        try:
            if not self.model_list_tree.exists(item_id):
                return
            values = list(self.model_list_tree.item(item_id).get('values', ()))
            if len(values) < 3:
                return
            values[2] = status_text
            self.model_list_tree.item(item_id, values=tuple(values))
        except tk.TclError:
            return

    def _apply_model_connectivity_result(self, entry, result, progress, total):
        """Apply one connectivity result on the Tk UI thread."""
        progress["done"] += 1
        self._apply_assigned_model_test_result(entry, result)
        if result["status"] == "success":
            progress["success"] += 1
            capability = result.get("capability", {})
            status = capability.get("status", "")
            label = "可用" if status == "compatible" else "兼容"
            elapsed = result.get("time", 0)
            self._set_model_list_item_status(
                entry["item_id"], f"✓ {label} {elapsed:.1f}s"
            )
            self._save_capability_to_model(
                entry["model_name"],
                capability,
                provider_key=entry["provider_key"],
                base_url=entry["base_url"],
                refresh=False,
            )
        else:
            progress["fail"] += 1
            msg = result.get("msg", "未知错误")
            self._set_model_list_item_status(entry["item_id"], f"✗ {msg}")

        if progress["done"] < total:
            self._update_api_status(
                text=f"模型测试中：{progress['done']}/{total} 完成，"
                     f"{progress['success']} 通过 / {progress['fail']} 失败",
                foreground=self.colors['warning'],
            )
            return

        assigned_target_label = (
            self._assigned_model_test_target_label(
                entry.get("assigned_role"), entry.get("assigned_model_ref")
            ) if entry.get("assigned_role") else ""
        )
        if assigned_target_label and progress["fail"] == 0:
            self._update_api_status(
                text=f"✓ {assigned_target_label}测试通过",
                foreground=self.colors['success'],
            )
        elif assigned_target_label:
            self._update_api_status(
                text=f"✗ {assigned_target_label}测试失败",
                foreground=self.colors['danger'],
            )
        elif progress["fail"] == 0:
            self._update_api_status(
                text=f"✓ {progress['success']} 个模型测试通过",
                foreground=self.colors['success'],
            )
        elif progress["success"] == 0:
            self._update_api_status(
                text=f"✗ {progress['fail']} 个模型测试失败",
                foreground=self.colors['danger'],
            )
        else:
            self._update_api_status(
                text=f"{progress['success']} 通过 / {progress['fail']} 失败",
                foreground=self.colors['warning'],
            )

    def _apply_assigned_model_test_result(self, entry, result):
        """仅将仍对应当前模型的异步结果同步到用途信号灯。"""
        role = entry.get("assigned_role")
        if not role:
            return
        token = entry.get("assigned_test_token")
        if token != getattr(self, "_assigned_model_test_tokens", {}).get(role):
            return
        expected_ref = entry.get("assigned_model_ref")
        if not self._model_ref_matches(expected_ref, self._get_assigned_model_ref(role)):
            return
        state = "success" if result.get("status") == "success" else "error"
        if not hasattr(self, "_assigned_model_test_results"):
            self._assigned_model_test_results = {}
        self._assigned_model_test_results[self._model_ref_key(expected_ref)] = state
        for target_role in self._assigned_model_test_roles(role, expected_ref):
            if self._model_ref_matches(
                expected_ref, self._get_assigned_model_ref(target_role)
            ):
                self._set_assigned_model_test_state(target_role, state)

    def _save_capability_to_model(
        self, model_name, capability, provider_key=None, base_url=None, refresh=True
    ):
        """将探测到的 capability 回写到 saved_models 并持久化到磁盘"""
        if not hasattr(self, 'saved_models'):
            return
        # 只保留显示需要的字段，避免存储过多探测细节
        cap_slim = {
            "status": capability.get("status", ""),
            "output_mode": capability.get("output_mode", ""),
        }
        updated = False
        for m in self.saved_models:
            if m.get("model") != model_name:
                continue
            if provider_key is not None and m.get("api_provider") != provider_key:
                continue
            if base_url is not None and (m.get("base_url", "").strip() != base_url.strip()):
                continue
            m["capability"] = cap_slim
            updated = True
        if not updated:
            return
        # 同步到 api_config 并原子写盘
        self.api_config["saved_models"] = self.saved_models
        try:
            write_path = get_api_config_path(for_write=True)
            tmp_path = write_path.with_suffix('.json.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._sanitize_config_for_save(self.api_config), f, ensure_ascii=False, indent=4)
            os.replace(tmp_path, write_path)
        except Exception:
            # 写盘失败不影响内存状态，清理临时文件
            try:
                os.remove(write_path.with_suffix('.json.tmp'))
            except OSError:
                pass
        self._mark_api_config_ui_current()
        if refresh:
            self.load_saved_models_to_tree()

    def save_api_config(self):
        """保存 API 配置 - API Key 按服务商加密存储到系统钥匙串"""
        try:
            provider_display = self.api_provider_var.get().strip()
            # 将显示名称转换为内部键（兼容旧配置）
            provider = self.DISPLAY_TO_KEY.get(provider_display, provider_display)
            model_name = self.api_model_var.get().strip()
            api_key = self.api_key_var.get().strip()
            base_url = self.api_base_url_var.get().strip()
            # 对话框多选暂存：非空表示批量加入列表（输入框不参与），空表示单选/手输
            pending = list(getattr(self, '_pending_models_to_add', []) or [])

            if not model_name and not pending:
                self._update_api_status(
                    text="⚠ 请输入模型名称",
                    foreground=self.colors['warning'],
                )
                return
            if not api_key:
                self._update_api_status(
                    text="⚠ 请输入 API Key",
                    foreground=self.colors['warning'],
                )
                return
            if not base_url:
                self._update_api_status(
                    text="⚠ 请输入 Base URL",
                    foreground=self.colors['warning'],
                )
                return

            normalized_base_url = normalize_api_base_url({
                "api_provider": provider,
                "base_url": base_url,
            })
            if normalized_base_url != base_url.rstrip("/"):
                base_url = normalized_base_url
                self.api_base_url_var.set(base_url)
            # 按服务商 + Base URL 组合存储 API Key（区分同一服务商的不同接入方式）
            if not save_api_key(provider, api_key, base_url):
                raise RuntimeError("API Key 未能写入系统凭据存储，请检查系统凭据服务后重试")
            self._remember_api_key(provider, base_url, api_key)

            outcome = _SETTINGS_CONTROLLER.prepare_saved_models(
                self.api_config or {},
                getattr(self, "saved_models", []) or [],
                provider=provider,
                base_url=base_url,
                model_name=model_name,
                pending_models=pending,
                api_key=api_key,
                llm_read_timeout=(
                    self.llm_read_timeout_var.get()
                    if hasattr(self, "llm_read_timeout_var")
                    else 60
                ),
            )
            self.api_config = outcome.api_config
            self._pending_models_to_add = []

            with open(get_api_config_path(for_write=True), 'w', encoding='utf-8') as f:
                json.dump(self._sanitize_config_for_save(self.api_config), f, ensure_ascii=False, indent=4)
            self._mark_api_config_ui_current()

            # 更新内存中的模型列表
            self.saved_models = self.api_config["saved_models"]

            # 刷新列表显示
            self.load_saved_models_to_tree()

            # 更新当前模型显示
            self.update_current_model_display()

            summary = outcome.summary
            default_summary = (
                "本次保存的模型已设为默认 AI 模型"
                if outcome.default_changed
                else "默认 AI 模型保持不变"
            )
            self._update_api_status(
                text=f"✓ {summary}；{default_summary}",
                foreground=self.colors['success'],
            )
            # 更新 AI 评估状态标签（可能从未配置变为已配置）
            self._update_ai_eval_status()

            # 保存成功后清除"API Key 未配置"警示卡片
            if getattr(self, 'reconfig_card', None) and self.reconfig_card.winfo_exists():
                self.reconfig_card.destroy()
                self.reconfig_card = None
            self._status_flash("模型配置已保存，API Key 已加密存储")
        except Exception as e:
            self._update_api_status(text=f"✗ 保存失败：{e}", foreground=self.colors['danger'])
            messagebox.show_failure(
                "保存模型配置",
                headline="模型配置未保存",
                message="请检查输入内容和系统凭据服务后重试。",
                detail=str(e),
                parent=getattr(self, "api_config_page", None) or getattr(self, "root", None),
            )

    def on_api_provider_changed(self, event):
        """API 服务商改变时更新默认配置"""
        display_name = self.api_provider_var.get()
        provider = self.DISPLAY_TO_KEY.get(display_name, display_name)
        selection = _SETTINGS_CONTROLLER.resolve_provider_selection(
            provider,
            getattr(self, "api_config", {}) or {},
            getattr(self, "saved_models", []) or [],
        )
        self.api_base_url_var.set(selection.base_url)
        self.api_model_var.set(selection.model)
        saved_key = self._get_api_key_cached(provider, selection.base_url)
        self.api_key_var.set(saved_key if saved_key else "")

    _model_dialog = None  # 防止重复打开模型列表对话框

    def fetch_model_list(self):
        """获取服务商的模型列表 - 使用当前输入的 API Key 和 Base URL"""
        import requests

        # 防止重复打开对话框
        if self._model_dialog is not None:
            try:
                self._model_dialog.lift()
                self._model_dialog.focus_force()
            except tk.TclError:
                self._model_dialog = None
            else:
                return

        api_key = self.api_key_var.get().strip()
        base_url = self.api_base_url_var.get().strip()
        provider_display = self.api_provider_var.get().strip()
        provider = self.DISPLAY_TO_KEY.get(provider_display, provider_display)

        if not api_key:
            messagebox.showwarning("警告", "请先输入 API Key")
            return

        if not base_url and not has_endpoint_discovery(provider):
            messagebox.showwarning("警告", "请先输入 Base URL")
            return

        normalized_base_url = normalize_api_base_url({
            "api_provider": provider,
            "base_url": base_url,
        })
        if normalized_base_url != base_url.rstrip("/"):
            base_url = normalized_base_url
            self.api_base_url_var.set(base_url)

        # 显示加载中状态（不使用 update()，避免重入）
        status_text = (
            "⏳ 正在识别接入渠道并获取模型..."
            if has_endpoint_discovery(provider)
            else "⏳ 正在获取模型列表..."
        )
        self._update_api_status(text=status_text, foreground=self.colors['warning'])

        def fetch_thread():
            outcome = _SETTINGS_CONTROLLER.fetch_catalog(
                provider=provider,
                api_key=api_key,
                base_url=base_url,
                fetched_models=dict(
                    (self.api_config or {}).get("fetched_models", {})
                ),
                configured_base_url=str(
                    (self.api_config or {}).get("base_url", "")
                ),
                fetcher=fetch_model_catalog,
                analyzer=analyze_model_catalog,
                timeout_errors=(requests.exceptions.Timeout,),
                connection_errors=(requests.exceptions.ConnectionError,),
            )
            self.run_on_ui(
                lambda outcome=outcome: self._apply_model_catalog_outcome(
                    outcome
                )
            )
        threading.Thread(target=fetch_thread, daemon=True).start()

    def _apply_model_catalog_outcome(self, outcome):
        """Render one plain model-catalog result on the Tk thread."""
        parent = getattr(self, "api_config_page", None) or self.root
        if outcome.status == "success":
            analysis = outcome.analysis
            models = list(analysis.models)
            self.api_base_url_var.set(outcome.base_url)
            if outcome.endpoint_confirmed:
                self._verified_api_endpoint = (
                    outcome.provider,
                    self.api_key_var.get().strip(),
                    outcome.base_url,
                )
            self.api_config.setdefault("fetched_models", {})[
                analysis.catalog_key
            ] = models
            self._save_api_config_to_file()
            self._mark_api_config_ui_current()
            new_count = len(analysis.new_models)
            removed_count = len(analysis.removed_models)
            channel = (
                f"已识别 {outcome.service_name}，"
                if outcome.service_name
                else ""
            )
            verification = (
                "；API Key 待测试连接"
                if outcome.resolution_status == "catalog"
                else ""
            )
            status = f"✓ {channel}找到 {len(models)} 个模型{verification}"
            if new_count or removed_count:
                status += f"（{new_count} 个新增，{removed_count} 个下线）"
            self._update_api_status(
                text=status,
                foreground=self.colors["success"],
            )

            def show_detail(detail_type):
                values = (
                    analysis.new_models
                    if detail_type == "new"
                    else analysis.removed_models
                )
                if not values:
                    return
                title = (
                    f"{outcome.provider} 新增模型"
                    if detail_type == "new"
                    else f"{outcome.provider} 下线模型"
                )
                suffix = (
                    ""
                    if detail_type == "new"
                    else "\n\n如正在使用这些模型，请尽快切换。"
                )
                self.input_support.show_text_dialog(
                    title,
                    "\n".join(f"• {model}" for model in sorted(values)) + suffix,
                    width=640,
                    height=440,
                )

            gui_model_catalog_dialog.show_model_catalog_dialog(
                self,
                provider=outcome.provider,
                models=models,
                filtered_count=analysis.filtered_count,
                new_models=analysis.new_models,
                removed_models=analysis.removed_models,
                font_family=FONT_FAMILY,
                show_model_detail=show_detail,
            )
            return

        if outcome.status == "empty":
            self._update_api_status(
                text="⚠ 未找到模型列表",
                foreground=self.colors["warning"],
            )
            messagebox.show_notice(
                "未找到模型",
                headline="API 没有返回可用模型列表",
                message="可以手动输入模型名称后继续保存。",
                detail=json.dumps(outcome.payload, ensure_ascii=False)[:500],
                parent=parent,
            )
            return
        if outcome.status == "auth_error":
            self._update_api_status(
                text="✗ 认证失败",
                foreground=self.colors["danger"],
            )
            messagebox.show_failure(
                "认证失败",
                headline="API Key 无效或已过期",
                message="请检查 API Key 后重新获取模型列表。",
                parent=parent,
            )
            return
        if outcome.status == "unsupported":
            self._update_api_status(
                text="✗ 接口不存在",
                foreground=self.colors["danger"],
            )
            messagebox.show_notice(
                "接口不支持",
                headline="服务商不支持自动获取模型列表",
                message="请手动输入模型名称，或参考服务商文档。",
                metrics=(("HTTP 状态码", "404"),),
                parent=parent,
            )
            return
        if outcome.status == "timeout":
            self._update_api_status(
                text="⏱ 请求超时",
                foreground=self.colors["warning"],
            )
            messagebox.show_notice(
                "请求超时",
                headline="获取模型列表超时",
                message="请检查网络、代理或服务商是否支持模型列表接口。",
                parent=parent,
            )
            return
        if outcome.status == "connection_error":
            self._update_api_status(
                text="✗ 连接失败",
                foreground=self.colors["danger"],
            )
            messagebox.show_failure(
                "连接失败",
                headline="无法连接到 API 服务器",
                message="请检查 Base URL、网络或代理设置。",
                detail=outcome.error,
                parent=parent,
            )
            return

        temporary = outcome.status == "temporary"
        self._update_api_status(
            text=(
                f"! 请求失败 ({outcome.http_status or '未确认'})"
                if temporary
                else "✗ 请求失败"
            ),
            foreground=(
                self.colors["warning"]
                if temporary
                else self.colors["danger"]
            ),
        )
        detail = (outcome.error or outcome.response_text)[:300]
        if temporary:
            messagebox.show_notice(
                "自动识别暂未完成",
                headline="暂时无法自动识别服务商模型",
                message="可以稍后重试，或先手动输入模型名称。",
                metrics=(("HTTP 状态码", str(outcome.http_status or "未确认")),),
                detail=detail,
                parent=parent,
            )
        else:
            messagebox.show_failure(
                "请求失败",
                headline="模型列表获取失败",
                message="请检查服务地址和网络后重试。",
                detail=detail,
                parent=parent,
            )

    def _show_api_key_while_pressed(self, event=None):
        """按住眼睛图标时临时显示 API Key。"""
        self.api_key_entry.configure(show="")
        self.api_key_toggle_btn.configure(image=self.api_key_toggle_btn._icon_eye_off)
        self.api_key_show_var.set(True)

    def _hide_api_key_after_release(self, event=None):
        """松开、移出或失焦时立即恢复 API Key 掩码。"""
        self.api_key_entry.configure(show="*")
        self.api_key_toggle_btn.configure(image=self.api_key_toggle_btn._icon_eye)
        self.api_key_show_var.set(False)

    def _apply_api_connectivity_result(self, result, model):
        """Render one deterministic API connectivity result on the Tk thread."""
        parent = (
            getattr(self, "api_config_page", None)
            or getattr(self, "root", None)
        )
        if result.status == "dns_error":
            hostname = result.hostname or "当前服务地址"
            self._update_api_status(
                text="✗ DNS 解析失败",
                foreground=self.colors["danger"],
            )
            messagebox.show_failure(
                "DNS 解析失败",
                headline=f"无法解析域名 {hostname}",
                message="请检查 Base URL、DNS 设置或 hosts 配置。",
                detail=f"DNS 检查耗时 {result.elapsed_seconds:.1f} 秒",
                parent=parent,
            )
            return

        if result.successful:
            compatibility = (
                "完整兼容"
                if result.status == "compatible"
                else "兼容模式"
            )
            self._update_api_status(
                text=(
                    f"✓ {compatibility} "
                    f"({result.elapsed_seconds:.1f}s)"
                ),
                foreground=self.colors["success"],
            )
            self._status_flash(f"{model} 连接正常，可用于 AI 评估")
            return

        if result.status == "incompatible":
            self._update_api_status(
                text="✗ 验证未通过",
                foreground=self.colors["danger"],
            )
            messagebox.show_failure(
                "连接测试失败",
                headline="模型不能用于 AI 评估",
                message="连接或兼容性验证未通过。",
                detail=result.message,
                parent=parent,
            )
            return

        self._update_api_status(
            text="✗ 能力验证失败",
            foreground=self.colors["danger"],
        )
        messagebox.show_failure(
            "连接测试失败",
            headline="模型能力验证异常",
            message="连接测试没有得到可用结论。",
            detail=result.message,
            parent=parent,
        )

    def test_api_connection(self):
        """Validate DNS and the model's application-level response capability."""
        api_key = self.api_key_var.get().strip()
        base_url = self.api_base_url_var.get().strip()
        model = self.api_model_var.get().strip()
        provider_display = self.api_provider_var.get().strip()
        provider_key = self.DISPLAY_TO_KEY.get(
            provider_display,
            provider_display,
        )

        if not api_key:
            self._update_api_status(
                text="⚠ 请先输入 API Key",
                foreground=self.colors["warning"],
            )
            return
        if not base_url:
            self._update_api_status(
                text="⚠ 请先输入 Base URL",
                foreground=self.colors["warning"],
            )
            return
        if not model:
            self._update_api_status(
                text="⚠ 请先输入模型名称",
                foreground=self.colors["warning"],
            )
            return

        normalized_base_url = normalize_api_base_url(
            {
                "api_provider": provider_key,
                "base_url": base_url,
            }
        )
        if normalized_base_url != base_url.rstrip("/"):
            base_url = normalized_base_url
            self.api_base_url_var.set(base_url)
        self._update_api_status(
            text="⏳ 正在验证...",
            foreground=self.colors["warning"],
        )
        config = {
            "api_provider": provider_key,
            "base_url": base_url,
            "model": model,
        }

        def test_thread():
            result = probe_api_connectivity(config, api_key)
            self.run_on_ui(
                lambda result=result: self._apply_api_connectivity_result(
                    result,
                    model,
                )
            )

        threading.Thread(target=test_thread, daemon=True).start()

    def save_config(self):
        """保存配置文件 - 带备份保护，保留 requirement_template 等顶层字段"""
        if not self._ensure_data_storage_available("保存岗位配置"):
            return False
        # 加载主文件或已验证备份，保留 requirement_template 等顶层字段。
        try:
            existing = load_job_config_snapshot(CONFIG_PATH, CONFIG_BACKUP_PATH)
        except (OSError, ValueError, RuntimeError) as e:
            print(f"读取原岗位配置失败，将保留有效备份并写入当前配置：{e}")
            existing = {}

        # 更新 job_requirements，保留其他顶层字段；剔除解析溯源等 GUI 临时字段
        existing = {
            key: value
            for key, value in existing.items()
            if not str(key).startswith("_")
        }
        existing["job_requirements"] = self._strip_transient_fields(self.job_rules)
        save_job_config_snapshot(existing, CONFIG_PATH, CONFIG_BACKUP_PATH)
        return True

    def _confirm_job_form_transition(self):
        """Protect unsaved job edits before switching or starting another draft."""
        if not self._job_form_has_unsaved_changes():
            return True
        choice = messagebox.ask_choice(
            "岗位配置尚未保存",
            headline="当前岗位有未保存的修改",
            message="请选择如何处理这些修改。",
            choices=(
                ("保存并继续", "save"),
                ("不保存", "discard"),
                ("取消", None),
            ),
            notice="不保存将放弃当前表单中的修改。",
            parent=self.root,
        )
        if choice is None:
            return False
        if choice == "save":
            return bool(self.save_current_job())
        return True

    def on_job_selected(self, event):
        """岗位选择改变"""
        job_name = self.config_job_combo.get()
        previous_job = getattr(self, '_job_form_loaded_name', "")
        if job_name == previous_job:
            return
        if job_name != previous_job and not self._confirm_job_form_transition():
            self.config_job_combo.set(previous_job)
            return
        self.config_job_combo.set(job_name)
        if job_name in self.job_rules:
            rule = self.job_rules[job_name]
            self.load_job_to_form(rule)
            self._set_requirement_section_expanded(False)
            self.btn_restore_job.configure(text=" 恢复已保存")
            self.requirement_template_btn.state(['disabled'])
            self._hide_requirement_hint()
            self._hide_parse_hint()
            self._hide_save_hint()
            self._show_btn_add_hint()  # 切换到已有岗位时重新显示"点此新增岗位→"提示
            self._hide_job_step_bar()
            # 显示详细结果区域
            self.result_detail_frame.pack(fill="both", expand=True, padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))
        else:
            self._invalidate_requirement_parse()
            # 岗位未选中时也隐藏提示
            self._hide_requirement_hint()
            self._hide_parse_hint()
            self._hide_save_hint()

    def load_job_to_form(self, rule):
        """将岗位配置加载到表单（包含话术模板）"""
        self._invalidate_requirement_parse()
        self._job_form_loading = True
        # 岗位名称使用 combo 中选中的名称（而不是 rule 中的 job_title）
        job_name = self.config_job_combo.get()
        self.job_name_var.set(job_name)
        self.min_exp_var.set(str(rule.get("min_exp", 0)))
        self.max_age_var.set(_optional_int_to_entry(rule.get("max_age")))
        self.edu_var.set(rule.get("edu", "不限"))
        gender = rule.get("gender", "不限")
        self.gender_var.set(gender if gender in GENDER_VALUES else "不限")
        self.work_location_var.set(rule.get("work_location") or "")
        salary_min = rule.get("salary_min")
        salary_max = rule.get("salary_max")
        self.salary_min_var.set(str(salary_min) if salary_min is not None else "")
        self.salary_max_var.set(str(salary_max) if salary_max is not None else "")

        # 加载技能列表（带权重）
        self.skills_data = []
        keywords = rule.get("keywords", [])
        for kw in keywords:
            if isinstance(kw, dict):
                self.skills_data.append({
                    "name": kw.get("name", ""),
                    "weight": kw.get("weight", 1),
                    "source": "配置"
                })
            else:
                self.skills_data.append({
                    "name": kw,
                    "weight": 1,
                    "source": "配置"
                })
        preferred_keywords = rule.get("preferred_keywords", [])
        for kw in preferred_keywords:
            if isinstance(kw, dict):
                self.skills_data.append({
                    "name": kw.get("name", ""),
                    "weight": kw.get("bonus", kw.get("weight", 1)),
                    "source": "优先"
                })
            else:
                self.skills_data.append({
                    "name": kw,
                    "weight": 1,
                    "source": "优先"
                })
        self.refresh_skills_tree()

        # 加载必要条件
        self.required_conditions_data = []
        required = rule.get("required_conditions", [])
        if isinstance(required, list):
            for cond in required:
                self.required_conditions_data.append(cond)
        self.refresh_required_listbox()

        # 加载原始招聘需求到需求文档解析框
        self.requirement_text.delete("1.0", tk.END)
        self.requirement_text.tag_remove("placeholder", "1.0", tk.END)
        original_req = rule.get("original_requirement", "")
        if original_req:
            self.requirement_text.insert("1.0", original_req)
            self._req_placeholder_active = False
        else:
            self.requirement_text.insert("1.0", self._req_placeholder_text, "placeholder")
            self._req_placeholder_active = True
        self.requirement_text.edit_modified(False)
        self._job_form_loading = False
        self._set_job_form_baseline(job_name)

    def _populate_skills_from_config(self, job_config, source_map, source_override=None):
        """从 job_config 构建 skills_data（含原文出处）。

        source_override: 若提供，应为 {normalized_key: source_str} 映射，
        用于 AI 增强阶段保留 regex 阶段的来源标记。未命中时 fallback 为 "AI新增"/"AI优先"。
        """
        evidence = {
            self._skill_identity_key(item.get("name", "")): item.get("evidence", "")
            for item in source_map.get("skills", [])
        }
        normalized_sources = {
            self._skill_identity_key(key): value
            for key, value in (source_override or {}).items()
        }
        preferred = job_config.get("preferred_keywords", [])
        preferred_keys = {
            self._skill_identity_key(
                item.get("name", "") if isinstance(item, dict) else item
            )
            for item in preferred
        }
        self.skills_data = []
        seen = set()

        def append_skill(kw, is_preferred=False):
            name = kw.get("name", "") if isinstance(kw, dict) else kw
            key = self._skill_identity_key(name)
            if not key or key in seen or (not is_preferred and key in preferred_keys):
                return
            if source_override is not None:
                source = normalized_sources.get(
                    key, "AI优先" if is_preferred else "AI新增"
                )
            else:
                source = "优先" if is_preferred else "解析"
            self.skills_data.append({
                "name": name,
                "weight": (
                    kw.get("bonus", kw.get("weight", 1))
                    if is_preferred and isinstance(kw, dict)
                    else kw.get("weight", 1)
                    if isinstance(kw, dict)
                    else 1
                ),
                "source": source,
                "evidence": evidence.get(key, ""),
            })
            seen.add(key)

        for kw in job_config.get("keywords", []):
            append_skill(kw)
        for kw in preferred:
            append_skill(kw, is_preferred=True)
        self.refresh_skills_tree()

    @staticmethod
    def _skill_identity_key(name):
        """Normalize aliases and formatting before comparing skill rows."""
        from doc_parser import skill_identity_key

        return skill_identity_key(name)

    def _populate_required_from_config(self, job_config, source_map):
        """从 job_config 构建 required_conditions_data（含原文出处）。"""
        evidence_map = {}
        for item in source_map.get("required_conditions", []):
            cond = item.get("condition")
            ev = item.get("evidence", "")
            if isinstance(cond, dict):
                key = f"{cond.get('type','or')}:{','.join(cond.get('items',[]))}"
            else:
                key = str(cond)
            evidence_map[key] = ev

        self.required_conditions_data = []
        for cond in job_config.get("required_conditions", []):
            key = f"{cond.get('type','or')}:{','.join(cond.get('items',[]))}" if isinstance(cond, dict) else str(cond)
            entry = dict(cond) if isinstance(cond, dict) else cond
            if isinstance(entry, dict):
                entry["_evidence"] = evidence_map.get(key, "")
            self.required_conditions_data.append(entry)
        self._required_evidence_map = evidence_map
        self.refresh_required_listbox()

    def refresh_skills_tree(self):
        """刷新技能树显示（带颜色标记 + 原文出处）"""
        for item in self.skills_tree.get_children():
            self.skills_tree.delete(item)
        for skill in self.skills_data:
            # 根据权重设置颜色标记
            weight = skill.get("weight", 1)
            if weight >= 3:
                tag = 'high_weight'  # 绿色
            elif weight >= 2:
                tag = 'mid_weight'   # 橙色
            else:
                tag = 'low_weight'   # 灰色
            evidence = skill.get("evidence", "")
            # 截断过长的原文，tooltip 显示完整内容
            evidence_display = evidence[:60] + "…" if len(evidence) > 60 else evidence
            self.skills_tree.insert("", "end", values=(skill["name"], weight, skill["source"], evidence_display), tags=(tag,))
        self._skills_tree_fingerprint = self._skills_data_fingerprint()
        self._schedule_job_form_status_refresh()

    def refresh_required_listbox(self):
        """刷新必要条件列表显示"""
        self.required_listbox.delete(0, tk.END)
        for cond in self.required_conditions_data:
            if isinstance(cond, dict):
                cond_type = cond.get("type", "or").upper()
                items = ", ".join(cond.get("items", []))
                self.required_listbox.insert(tk.END, f"{cond_type}: {items}")
            else:
                self.required_listbox.insert(tk.END, str(cond))
        self._required_list_fingerprint = self._required_conditions_fingerprint()
        self._schedule_job_form_status_refresh()

    def _skills_data_fingerprint(self):
        """Return a stable fingerprint for the visible skills list."""
        return tuple(
            (
                skill.get("name", ""),
                skill.get("weight", 1),
                skill.get("source", ""),
            )
            for skill in getattr(self, 'skills_data', [])
        )

    def _required_conditions_fingerprint(self):
        """Return a stable fingerprint for the visible hard-condition list."""
        return tuple(
            json.dumps(cond, ensure_ascii=False, sort_keys=True)
            if isinstance(cond, dict) else str(cond)
            for cond in getattr(self, 'required_conditions_data', [])
        )

    @staticmethod
    def _is_preferred_skill_source(source):
        """Return True when a skill row belongs to preferred/additive scoring."""
        return str(source or "") in {"优先", "AI优先"}

    @staticmethod
    def _strip_transient_fields(value):
        """Remove GUI-only metadata before persisting business config."""
        if isinstance(value, dict):
            return {
                key: BossFilterGUI._strip_transient_fields(item)
                for key, item in value.items()
                if not str(key).startswith("_")
            }
        if isinstance(value, list):
            return [BossFilterGUI._strip_transient_fields(item) for item in value]
        return value

    def _snapshot_parse_edit_state(self):
        """Capture the editable parse result state before AI enhancement returns."""
        return {
            "edu": self.edu_var.get(),
            "gender": self.gender_var.get(),
            "min_exp": self.min_exp_var.get(),
            "max_age": self.max_age_var.get(),
            "work_location": self.work_location_var.get(),
            "salary": (self.salary_min_var.get(), self.salary_max_var.get()),
            "skills": self._skills_data_fingerprint(),
            "required_conditions": self._required_conditions_fingerprint(),
        }

    def _job_form_fingerprint(self):
        """Return the persisted business state represented by the current form."""
        requirement = ""
        if hasattr(self, 'requirement_text'):
            requirement = self._get_requirement_text()
        skills = tuple(
            (
                str(skill.get("name", "")).strip(),
                skill.get("weight", 1),
                self._is_preferred_skill_source(skill.get("source")),
            )
            for skill in getattr(self, 'skills_data', [])
        )
        required = tuple(
            json.dumps(
                self._strip_transient_fields(condition),
                ensure_ascii=False,
                sort_keys=True,
            )
            if isinstance(condition, dict)
            else str(condition)
            for condition in getattr(self, 'required_conditions_data', [])
        )
        return (
            self.job_name_var.get(),
            self.edu_var.get(),
            self.gender_var.get(),
            self.min_exp_var.get(),
            self.max_age_var.get(),
            self.work_location_var.get(),
            self.salary_min_var.get(),
            self.salary_max_var.get(),
            skills,
            required,
            requirement,
        )

    def _bind_job_form_change_tracking(self):
        """Refresh save and quality state after editable business fields change."""
        variables = (
            self.job_name_var,
            self.edu_var,
            self.gender_var,
            self.min_exp_var,
            self.max_age_var,
            self.work_location_var,
            self.salary_min_var,
            self.salary_max_var,
        )
        for variable in variables:
            variable.trace_add(
                'write',
                lambda *_args: self._schedule_job_form_status_refresh(),
            )
        self.requirement_text.bind(
            '<<Modified>>', self._on_requirement_text_modified, add='+'
        )
        self.requirement_text.edit_modified(False)
        self._job_form_tracking_ready = True

    def _on_requirement_text_modified(self, _event=None):
        if not self.requirement_text.edit_modified():
            return
        self.requirement_text.edit_modified(False)
        self._schedule_job_form_status_refresh()

    def _schedule_job_form_status_refresh(self, delay=300):
        if (
            not getattr(self, '_job_form_tracking_ready', False)
            or getattr(self, '_job_form_loading', False)
        ):
            return
        pending = getattr(self, '_job_form_status_after_id', None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except (tk.TclError, ValueError):
                pass
        self._job_form_status_after_id = self.root.after(
            delay, self._refresh_job_form_status
        )

    def _set_job_form_baseline(self, loaded_job_name):
        self._job_form_loaded_name = str(loaded_job_name or "")
        self._job_form_saved_snapshot = self._job_form_fingerprint()
        if getattr(self, '_job_form_tracking_ready', False):
            self._refresh_job_form_status()

    def _job_form_has_unsaved_changes(self):
        baseline = getattr(self, '_job_form_saved_snapshot', None)
        return baseline is not None and self._job_form_fingerprint() != baseline

    def _refresh_job_form_status(self):
        """Render unsaved state and deterministic configuration quality."""
        self._job_form_status_after_id = None
        selected_job = self.config_job_combo.get()
        job_name = self.job_name_var.get().strip()
        dirty = self._job_form_has_unsaved_changes()
        is_new_draft = (
            selected_job not in self.job_rules
            and getattr(self, '_job_step_active', -1) >= 0
        )
        if selected_job in self.job_rules:
            status_text = "有未保存修改" if dirty else "已保存"
        elif job_name or is_new_draft:
            status_text = "新岗位，尚未保存"
        else:
            status_text = "未选择岗位"
        status_color = (
            self.colors['warning']
            if dirty or is_new_draft or (job_name and selected_job not in self.job_rules)
            else self.colors['text_secondary']
        )
        self.job_form_status_var.set(status_text)
        self.job_form_status_label.configure(foreground=status_color)
        self.btn_restore_job.configure(
            text=" 恢复已保存"
            if selected_job in self.job_rules
            else " 清空内容",
            state="normal" if dirty else "disabled",
        )
        self._refresh_requirement_header_state()

        is_new_job_waiting_for_parse = (
            selected_job not in self.job_rules
            and 0 <= getattr(self, '_job_step_active', -1) < 2
            and not self.skills_data
            and not self.required_conditions_data
        )
        if is_new_job_waiting_for_parse:
            requirement = self._get_requirement_text().strip()
            self._job_config_quality_clickable = False
            self._job_config_preview = None
            self.job_config_quality_var.set(
                "配置质量：待解析" if requirement else "配置质量：待配置"
            )
            self.job_config_quality_label.configure(
                foreground=self.colors['text_secondary']
            )
            self.btn_view_job_config_issues.configure(state="disabled")
            return

        if not job_name:
            self._job_config_quality_clickable = False
            self._job_config_preview = None
            self.job_config_quality_var.set("配置质量：待配置")
            self.job_config_quality_label.configure(
                foreground=self.colors['text_secondary']
            )
            self.btn_view_job_config_issues.configure(state="disabled")
            return

        self.btn_view_job_config_issues.configure(state="normal")
        self._job_config_quality_clickable = True

        try:
            preview_name, preview_rule = self._build_current_job_rule_preview()
        except ValueError as exc:
            self._job_config_preview = (None, None, [], str(exc))
            self.job_config_quality_var.set("配置质量：待完善｜阻断 1 项")
            self.job_config_quality_label.configure(
                foreground=self.colors['danger']
            )
            return

        issues = diagnose_job_config(preview_name, preview_rule)
        quality = score_job_config_quality(issues)
        error_count = sum(issue.severity == "error" for issue in issues)
        warning_count = sum(issue.severity == "warning" for issue in issues)
        info_count = sum(issue.severity == "info" for issue in issues)
        self._job_config_preview = (preview_name, preview_rule, issues, "")
        self.job_config_quality_var.set(
            f"配置质量：{quality.score} 分｜阻断 {error_count} 项｜"
            f"提醒 {warning_count} 项｜建议 {info_count} 项"
        )
        quality_color = (
            self.colors['danger']
            if error_count
            else self.colors['warning']
            if warning_count
            else self.colors['success']
        )
        self.job_config_quality_label.configure(foreground=quality_color)

    def _show_current_job_config_diagnostics(self):
        self._refresh_job_form_status()
        preview_name, preview_rule, issues, validation_error = (
            self._job_config_preview
        )
        if validation_error:
            messagebox.showwarning(
                "配置尚未完成", validation_error, parent=self.root
            )
            return
        text = summarize_job_config_diagnostics(
            preview_name, preview_rule, issues=issues
        )
        self._show_job_config_diagnostics_dialog(
            text,
            any(issue.severity == "error" for issue in issues),
            context="preview",
        )

    def _open_job_config_quality_details(self, _event=None):
        if not getattr(self, '_job_config_quality_clickable', False):
            return
        self._show_current_job_config_diagnostics()

    def _dirty_fields_since_parse_snapshot(self):
        """Detect user edits made while asynchronous AI enhancement was running."""
        baseline = getattr(self, "_ai_parse_edit_snapshot", None)
        if not baseline:
            return set()
        current = self._snapshot_parse_edit_state()
        return {field for field, old_value in baseline.items() if current.get(field) != old_value}

    def _refresh_config_lists_if_needed(self):
        """Refresh config page lists only when the backing data changed."""
        if not hasattr(self, 'skills_tree') or not hasattr(self, 'required_listbox'):
            return

        skills_fp = self._skills_data_fingerprint()
        if skills_fp != self._skills_tree_fingerprint:
            self.refresh_skills_tree()

        required_fp = self._required_conditions_fingerprint()
        if required_fp != self._required_list_fingerprint:
            self.refresh_required_listbox()

    def add_skill(self):
        """添加技能"""
        skill_name = self.new_skill_var.get().strip()

        if not skill_name:
            messagebox.showwarning("警告", "请输入技能名称")
            return
        try:
            weight = int(self.new_skill_add_weight_var.get())
            if weight < 1 or weight > 3:
                raise ValueError()
        except ValueError:
            messagebox.showwarning("警告", "权重必须是 1-3 之间的数字")
            return

        # 检查是否已存在（忽略空格、格式差异和常见别名）
        new_key = self._skill_identity_key(skill_name)
        for s in self.skills_data:
            if self._skill_identity_key(s["name"]) == new_key:
                messagebox.showwarning("警告", "该技能已存在")
                return

        self.skills_data.append({"name": skill_name, "weight": weight, "source": "手动"})
        self.refresh_skills_tree()
        self.new_skill_var.set("")
        self._status_flash(f"已添加技能：{skill_name}（权重 {weight}）")

    def delete_skill(self):
        """删除选中技能"""
        selection = self.skills_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先在列表中选择要删除的技能")
            return
        if messagebox.ask_confirmation(
            "删除技能",
            headline=f"删除选中的 {len(selection)} 个技能？",
            message="这些技能将从当前岗位配置中移除。",
            notice="保存岗位配置后，删除结果才会写入配置文件。",
            yes_label="删除技能",
            no_label="取消",
            dangerous=True,
            parent=self.root,
        ):
            for item in selection:
                values = self.skills_tree.item(item, "values")
                skill_name = values[0]
                self.skills_data = [s for s in self.skills_data if s["name"] != skill_name]
            self.refresh_skills_tree()
            self.selected_skill_var.set("")

    def on_skill_selected(self, event):
        """技能被选中时，自动填充权重值到输入框"""
        selection = self.skills_tree.selection()
        if selection:
            values = self.skills_tree.item(selection[0], "values")
            skill_name = values[0]
            weight = values[1]
            self.selected_skill_var.set(skill_name)
            self.new_skill_weight_var.set(str(weight))
        else:
            self.selected_skill_var.set("未选择")

    def update_skill_weight(self):
        """更新选中技能的权重"""
        selection = self.skills_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先在列表中选择要更新的技能")
            return

        try:
            weight = int(self.new_skill_weight_var.get())
            if weight < 1 or weight > 3:
                raise ValueError()
        except ValueError:
            messagebox.showwarning("警告", "权重必须是 1-3 之间的数字")
            return

        for item in selection:
            values = self.skills_tree.item(item, "values")
            skill_name = values[0]
            for s in self.skills_data:
                if s["name"] == skill_name:
                    s["weight"] = weight
                    break
        self.refresh_skills_tree()
        self._status_flash(f"已更新技能权重为 {weight}")

    def add_required_condition(self):
        """添加必要条件"""
        cond_type = self.required_cond_type_var.get()
        raw = self.new_required_var.get().strip()
        if not raw:
            messagebox.showwarning("警告", "请输入关键词")
            return

        if cond_type == "简单匹配":
            # 简单字符串匹配
            self.required_conditions_data.append(raw)
        elif cond_type == "OR（满足任一）":
            items = [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]
            if not items:
                messagebox.showwarning("警告", "请输入至少一个关键词")
                return
            self.required_conditions_data.append({"type": "or", "items": items})
        elif cond_type == "AND（全部满足）":
            items = [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]
            if not items:
                messagebox.showwarning("警告", "请输入至少一个关键词")
                return
            self.required_conditions_data.append({"type": "and", "items": items})

        self.refresh_required_listbox()
        self.new_required_var.set("")

    def delete_required_condition(self):
        """删除选中条件"""
        selection = self.required_listbox.curselection()
        if not selection:
            messagebox.showwarning("警告", "请选择要删除的条件")
            return
        for index in reversed(selection):
            self.required_conditions_data.pop(index)
        self.refresh_required_listbox()

    def _validate_salary_input(self, *args):
        """实时验证薪资输入框内容（仅允许数字或空）"""
        for var, entry in [(self.salary_min_var, self.salary_min_entry),
                           (self.salary_max_var, self.salary_max_entry)]:
            text = var.get()
            if text == "":
                # 空值合法，恢复默认样式
                entry.configure(foreground=self.colors['text_primary'])
            elif not text.isdigit():
                entry.configure(foreground=self.colors.get('danger_text', ui_theme.DANGER_TEXT))
            else:
                entry.configure(foreground=self.colors['text_primary'])

    # ── 筛选结果页日期过滤（日历控件） ─────────────────────────────────

    def _ensure_result_custom_date_entries(self) -> None:
        """Create the two calendar widgets only when custom dates are requested."""
        if (
            getattr(self, 'result_date_start_entry', None) is not None
            and getattr(self, 'result_date_end_entry', None) is not None
        ):
            return

        calendar_font = (FONT_FAMILY, int(11 * self.font_scale))
        calendar_options = {
            'width': 12,
            'font': calendar_font,
            'date_pattern': 'yyyy-mm-dd',
            'showweeknumbers': False,
        }
        start_entry = self._create_result_date_entry(
            self.result_custom_date_frame, **calendar_options
        )
        end_entry = self._create_result_date_entry(
            self.result_custom_date_frame, **calendar_options
        )
        self.result_date_start_entry = start_entry
        self.result_date_end_entry = end_entry

        entry_pad = int(4 * self.dpi_scale * self.zoom_factor)
        start_entry.pack(side="left", padx=entry_pad)
        start_entry.bind(
            "<<DateEntrySelected>>", lambda _event: self._validate_date_range('start')
        )
        start_entry.bind("<Return>", lambda _event: self._validate_date_range('start'))
        start_entry.bind("<FocusOut>", lambda _event: self._validate_date_range('start'))

        ttk.Label(
            self.result_custom_date_frame,
            text="~",
            font=self.font_label,
            background=self.colors['bg_main'],
        ).pack(side="left", padx=int(2 * self.dpi_scale * self.zoom_factor))

        end_entry.pack(side="left", padx=entry_pad)
        end_entry.bind(
            "<<DateEntrySelected>>", lambda _event: self._validate_date_range('end')
        )
        end_entry.bind("<Return>", lambda _event: self._validate_date_range('end'))
        end_entry.bind("<FocusOut>", lambda _event: self._validate_date_range('end'))

        today = datetime.now().date()
        start_entry.set_date(today - timedelta(days=6))
        end_entry.set_date(today)
        self._wrap_date_dropdown_mutex(start_entry, end_entry)
        self._wrap_date_dropdown_mutex(end_entry, start_entry)

    @staticmethod
    def _close_date_dropdown(entry):
        """收起一个 DateEntry 的独立日历弹层。"""
        top = getattr(entry, '_top_cal', None)
        try:
            if top and top.winfo_ismapped():
                top.withdraw()
        except tk.TclError:
            pass

    def _close_result_date_dropdowns(self):
        """收起结果页两个日期控件可能仍展开的日历。"""
        for entry_name in ('result_date_start_entry', 'result_date_end_entry'):
            entry = getattr(self, entry_name, None)
            if entry is not None:
                self._close_date_dropdown(entry)

    @staticmethod
    def _wrap_date_dropdown_mutex(this_entry, other_entry):
        """包装 DateEntry.drop_down，展开自己前先收起对方的下拉日历"""
        original_drop_down = getattr(this_entry, 'drop_down', None)
        if not callable(original_drop_down):
            return

        def _wrapped_drop_down():
            BossFilterGUI._close_date_dropdown(other_entry)
            original_drop_down()

        this_entry.drop_down = _wrapped_drop_down

    def _on_result_time_range_changed(self, _event=None):
        """切换预设时间范围，并按需显示自定义日期控件。"""
        self._close_result_date_dropdowns()
        if self.result_time_range_var.get() == "自定义":
            self._ensure_result_custom_date_entries()
            if not self.result_custom_date_frame.winfo_manager():
                self.result_custom_date_frame.pack(side="left")
        else:
            self.result_custom_date_frame.pack_forget()
        if hasattr(self, 'result_tree'):
            self.refresh_results()

    def _validate_date_range(self, which: str):
        """验证日期范围：终止日期 >= 起始日期，终止日期 <= 今天"""
        try:
            today = datetime.now().date()
            start_date = self.result_date_start_entry.get_date()
            end_date = self.result_date_end_entry.get_date()

            # 终止日期不能超过今天
            if end_date > today:
                self.result_date_end_entry.set_date(today)
                end_date = today

            # 起始日期不能超过今天
            if start_date > today:
                self.result_date_start_entry.set_date(today)
                start_date = today

            # 起始日期不能晚于终止日期
            if start_date > end_date:
                if which == 'start':
                    # 用户改了起始日期，让终止日期跟随
                    self.result_date_end_entry.set_date(start_date)
                else:
                    # 用户改了终止日期，让起始日期跟随
                    self.result_date_start_entry.set_date(end_date)
        except Exception:
            pass

        if hasattr(self, 'result_tree'):
            self.refresh_results()

    def _get_result_date_filter(self):
        """读取筛选结果页日期过滤值，返回 (start_str, end_str)，均为 YYYYMMDD 格式或 None"""
        mode = (
            self.result_time_range_var.get()
            if hasattr(self, 'result_time_range_var') else "自定义"
        )
        if mode == "全部时间":
            return None, None

        today = datetime.now().date()
        preset_days = {"今天": 0, "近7天": 6, "近30天": 29}
        if mode in preset_days:
            return (
                (today - timedelta(days=preset_days[mode])).strftime("%Y%m%d"),
                today.strftime("%Y%m%d"),
            )

        start_str = end_str = None
        try:
            start_str = self.result_date_start_entry.get_date().strftime("%Y%m%d")
        except Exception:
            pass
        try:
            end_str = self.result_date_end_entry.get_date().strftime("%Y%m%d")
        except Exception:
            pass
        return start_str, end_str

    def _start_breathing(self, label, color_key='success', bg_key='bg_card'):
        """启动呼吸渐变动画（与 btn_add_hint 风格一致）"""
        def hex_to_rgb(h):
            h = h.lstrip('#')
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

        def rgb_to_hex(r, g, b):
            return f'#{int(r):02x}{int(g):02x}{int(b):02x}'

        color_rgb = hex_to_rgb(self.colors[color_key])
        bg_rgb = hex_to_rgb(self.colors[bg_key])

        def _fade(label=label, color=color_rgb, bg=bg_rgb, step=[0]):
            if not label.winfo_exists():
                return
            try:
                phase = step[0] / 60.0 * 2 * math.pi
                alpha = 0.15 + 0.85 * (0.5 + 0.5 * math.sin(phase))
                r = color[0] * alpha + bg[0] * (1 - alpha)
                g = color[1] * alpha + bg[1] * (1 - alpha)
                b = color[2] * alpha + bg[2] * (1 - alpha)
                label.config(foreground=rgb_to_hex(r, g, b))
                step[0] = (step[0] + 1) % 60
                self.root.after(50, _fade)
            except tk.TclError:
                pass

        _fade()

    def _bind_requirement_header_interaction(self):
        """Make the full recruitment-requirement header act as one disclosure row."""
        title_bar = getattr(self, 'requirement_title_bar', None)
        if title_bar is None:
            return
        widgets = [title_bar, *title_bar.winfo_children()]
        for widget in widgets:
            try:
                widget.configure(cursor="hand2")
            except tk.TclError:
                pass
            widget.bind('<Button-1>', lambda _event: self._toggle_requirement_section())
            widget.bind('<Enter>', lambda _event: self._set_requirement_header_hover(True))
            widget.bind('<Leave>', lambda _event: self._set_requirement_header_hover(False))

    def _set_requirement_header_hover(self, active):
        title_bar = getattr(self, 'requirement_title_bar', None)
        if title_bar is None:
            return
        background = self.colors.get('banner_info_bg', ui_theme.BANNER_INFO_BG) if active else self.colors.get('bg_footer', ui_theme.BG_FOOTER)
        title_bar.configure(bg=background)
        for widget in title_bar.winfo_children():
            if isinstance(widget, tk.Label):
                widget.configure(bg=background)

    def _refresh_requirement_header_state(self):
        status_var = getattr(self, 'requirement_header_status_var', None)
        icon_label = getattr(self, 'requirement_toggle_icon_label', None)
        if status_var is None or icon_label is None:
            return
        expanded = getattr(self, 'requirement_section_expanded', True)
        if expanded:
            summary = ""
            icon = self.requirement_collapse_icon
        else:
            requirement = self._get_requirement_text()
            selected_job = self.config_job_combo.get()
            if not requirement:
                summary = "尚未填写招聘需求"
            elif (
                selected_job in self.job_rules
                and self._job_form_has_unsaved_changes()
            ):
                summary = "招聘需求已修改"
            elif selected_job in self.job_rules:
                summary = "已保存招聘需求"
            else:
                summary = "已填写招聘需求"
            icon = self.requirement_expand_icon
        status_var.set(summary)
        icon_label.configure(image=icon)

    def _set_requirement_section_expanded(self, expanded):
        """Show or hide the requirement source content without losing its state."""
        frame = getattr(self, 'requirement_parse_frame', None)
        if frame is None:
            return
        self.requirement_section_expanded = bool(expanded)
        if expanded:
            if not frame.winfo_manager():
                padding = int(
                    UI_CONFIG['label_frame_padding']
                    * self.dpi_scale
                    * self.zoom_factor
                )
                frame.pack(
                    fill="both", expand=True, padx=padding, pady=padding
                )
        else:
            frame.pack_forget()
        self._refresh_requirement_header_state()

    def _toggle_requirement_section(self):
        self._set_requirement_section_expanded(
            not getattr(self, 'requirement_section_expanded', True)
        )

    def _show_requirement_hint(self):
        """Legacy call retained; the visible template button is self-explanatory."""
        return

    def _hide_requirement_hint(self):
        label = getattr(self, 'requirement_hint_label', None)
        if label is not None and label.winfo_exists():
            label.destroy()

    def _show_btn_add_hint(self):
        """Legacy call retained; the New button no longer needs an animated hint."""
        return

    def _hide_btn_add_hint(self):
        label = getattr(self, 'btn_add_hint', None)
        if label is not None and label.winfo_exists():
            label.destroy()

    def _show_parse_hint(self):
        """Legacy call retained; the parse action remains next to its input."""
        return

    def _hide_parse_hint(self):
        label = getattr(self, 'parse_hint_label', None)
        if label is not None and label.winfo_exists():
            label.destroy()

    def _show_save_hint(self):
        """Legacy call retained; the fixed save action is always visible."""
        return

    def _hide_save_hint(self):
        label = getattr(self, 'save_hint_label', None)
        if label is not None and label.winfo_exists():
            label.destroy()

    def _insert_requirement_template(self):
        """插入招聘需求模板到输入框（模板文本从 job_config.json 读取）"""
        try:
            config = load_job_config_snapshot(CONFIG_PATH, CONFIG_BACKUP_PATH)
            template = config.get("requirement_template", "")
        except (OSError, ValueError, RuntimeError):
            template = ""
        if not template:
            messagebox.showwarning("警告", "配置文件中未找到 requirement_template 模板")
            return
        self.requirement_text.delete("1.0", tk.END)
        self.requirement_text.tag_remove("placeholder", "1.0", tk.END)
        self._req_placeholder_active = False
        self.requirement_text.insert("1.0", template)
        self._hide_requirement_hint()
        self._show_parse_hint()
        # 步骤推进：填入需求 → 解析文档
        if self._job_step_active >= 0:
            self._update_job_step(1)

    def _get_requirement_text(self):
        """获取需求输入框内容，占位提示视为空。"""
        if getattr(self, '_req_placeholder_active', False):
            return ""
        return self.requirement_text.get("1.0", tk.END).strip()

    def _begin_requirement_parse(self):
        """Start a parse generation whose callbacks may update the current form."""
        self._invalidate_requirement_parse()
        parse_id = self._requirement_parse_generation
        self._active_requirement_parse_id = parse_id
        return parse_id

    def _is_current_requirement_parse(self, parse_id):
        return (
            parse_id is not None
            and getattr(self, '_active_requirement_parse_id', None) == parse_id
        )

    def _complete_requirement_parse(self, parse_id):
        if not self._is_current_requirement_parse(parse_id):
            return False
        self._active_requirement_parse_id = None
        self._ai_enhance_pending = False
        self._ai_parse_edit_snapshot = None
        return True

    def _invalidate_requirement_parse(self):
        """Invalidate pending callbacks and restore controls without killing the worker."""
        self._requirement_parse_generation = (
            getattr(self, '_requirement_parse_generation', 0) + 1
        )
        self._active_requirement_parse_id = None
        self._ai_enhance_pending = False
        self._ai_parse_edit_snapshot = None
        self._stop_ai_progress_animation()
        self._stop_requirement_parse_progress()
        self._finish_parse_button()

    def parse_requirement(self):
        """解析需求文档（两阶段：regex 即时 → AI 异步增强）"""
        self._hide_parse_hint()
        requirement_text = self._get_requirement_text()
        if not requirement_text:
            messagebox.showwarning("警告", "请输入招聘需求文档内容")
            return

        ai_provider = self.api_config.get("api_provider", "") if getattr(self, "api_config", None) else ""
        ai_base_url = self.api_config.get("base_url", "") if getattr(self, "api_config", None) else ""
        ai_model = self.api_config.get("model", "") if getattr(self, "api_config", None) else ""
        ai_key = (
            self._get_api_key_cached(ai_provider, ai_base_url)
            if ai_provider and ai_base_url and ai_model else None
        )
        parse_id = self._begin_requirement_parse()
        if hasattr(self, "btn_parse_requirement"):
            self._parse_requirement_button_text = self.btn_parse_requirement.cget("text")
            self.btn_parse_requirement.config(state="disabled", text=" 解析中...")
        if hasattr(self, "btn_save"):
            self.btn_save.state(['disabled'])
        self._set_parse_result_text("正在解析：使用本地规则提取岗位要求…", self.colors['warning'])
        self._start_requirement_parse_progress(bool(ai_key), parse_id)
        # 跟踪用户手动修改的字段，AI 增强时不覆盖
        self._dirty_fields = set()
        self._ai_enhance_pending = bool(ai_key)

        def _worker():
            try:
                # 阶段 1：regex 解析（快速）
                regex_result = self._build_regex_parse_result(requirement_text)
                self.root.after(
                    0,
                    lambda result=regex_result, task_id=parse_id:
                        self._apply_requirement_parse_result(result, task_id),
                )

                # 阶段 2：AI 增强（慢速，仅在有 key 时执行）
                if ai_key:
                    ai_result = self._build_ai_enhance_result(
                        requirement_text, regex_result["config"],
                        ai_provider, ai_base_url, ai_model, ai_key
                    )
                    self.root.after(
                        0,
                        lambda result=ai_result, task_id=parse_id:
                            self._apply_ai_enhance_result(result, task_id),
                    )
            except Exception as exc:
                self.root.after(
                    0,
                    lambda error=exc, task_id=parse_id:
                        self._handle_requirement_parse_error(error, task_id),
                )

        threading.Thread(target=_worker, daemon=True).start()

    def _build_regex_parse_result(self, requirement_text):
        """阶段 1：正则解析（快速，毫秒级）。"""
        from doc_parser import generate_config_from_text, parse_job_requirements

        parsed_detail = parse_job_requirements(requirement_text)
        if os.environ.get("BOSS_DEBUG_PARSE") == "1":
            debug_log_path = BASE_DIR / "parse_debug.log"
            with open(debug_log_path, 'w', encoding='utf-8') as f:
                f.write("=== 学历解析调试日志 ===\n")
                f.write(f"需求文档长度: {len(requirement_text)}\n")
                f.write(f"需求文档是否含'博士': {'博士' in requirement_text}\n")
                f.write(f"需求文档是否含'硕士': {'硕士' in requirement_text}\n")
                f.write(f"需求文档是否含'本科': {'本科' in requirement_text}\n")
                f.write(f"parse_job_requirements 结果: edu={parsed_detail['edu']}\n")
                f.write(f"\n=== 原始需求文档 ===\n{requirement_text}\n")

        config = generate_config_from_text(requirement_text, merge_existing=False)
        return {
            "config": config,
            "ai_parse_status": "本地规则",
            "ai_parse_warnings": [],
            "source_map": config.get("_source_map", {}),
        }

    def _build_ai_enhance_result(self, requirement_text, regex_config, ai_provider, ai_base_url, ai_model, ai_key):
        """阶段 2：AI 增强（慢速，6-12 秒）。"""
        from job_ai_parser import enhance_config_with_ai
        try:
            ai_result = enhance_config_with_ai(
                requirement_text,
                regex_config,
                {"api_provider": ai_provider, "base_url": ai_base_url, "model": ai_model},
                ai_key,
            )
            if ai_result.success:
                return {
                    "config": ai_result.config,
                    "ai_parse_status": "本地规则 + AI 增强解析",
                    "ai_parse_warnings": ai_result.warnings or [],
                    "source_map": ai_result.config.get("_source_map", regex_config.get("_source_map", {})),
                    "ai_success": True,
                }
            else:
                return {
                    "ai_parse_status": f"本地规则（AI 暂时不可用：{self._friendly_ai_parse_reason(ai_result.reason)}）",
                    "ai_success": False,
                }
        except Exception as ai_exc:
            return {
                "ai_parse_status": f"本地规则（AI 暂时不可用：{self._friendly_ai_parse_reason(str(ai_exc))}）",
                "ai_success": False,
            }

    def _friendly_ai_parse_reason(self, reason):
        """把底层 AI 错误转成普通用户能理解的回退原因。"""
        text = str(reason or "")
        if "连接超时" in text:
            return "网络连接太慢（DNS/代理/服务器不可达）"
        if "读取超时" in text:
            return "模型响应太慢"
        if any(token in text for token in ("超时", "Timeout", "timed out")):
            return "响应太慢"
        if any(token in text for token in ("鉴权", "401", "403", "API Key", "权限")):
            return "密钥或模型权限需要检查"
        if any(token in text for token in ("限流", "429", "额度", "quota", "rate")):
            return "额度不足或请求太频繁"
        if any(token in text for token in ("无法连接", "连接失败", "Connection", "DNS")):
            return "网络连接不稳定"
        if any(token in text for token in ("SSL", "证书")):
            return "网络证书校验失败"
        if any(token in text for token in ("404", "接口不存在", "Base URL")):
            return "服务地址可能填错了"
        if "HTTP 400" in text:
            return "模型服务拒绝了请求参数"
        if any(token in text for token in ("500", "502", "503", "504", "服务端错误")):
            return "模型服务临时不可用"
        if any(token in text for token in ("JSON", "返回为空")):
            return "模型返回内容无法识别"
        return "连接不稳定"

    def _humanize_ai_parse_warning(self, warning):
        """把 AI 提醒里的内部字段名转换为用户能看懂的说法。"""
        text = re.sub(r'[`"\'“”‘’]', '', str(warning or "")).strip()
        replacements = [
            ("preferred_keywords_add", "优先项"),
            ("preferred_keywords", "优先项"),
            ("keywords_update", "技能关键词"),
            ("keywords_add", "技能关键词"),
            ("keywords", "技能关键词"),
            ("required_conditions_remove", "必要条件"),
            ("required_conditions_add", "必要条件"),
            ("required_conditions", "必要条件"),
            ("basic_info", "基本信息"),
            ("job_title", "岗位名称"),
            ("gender", "性别要求"),
            ("work_location", "工作地点"),
            ("salary_min", "最低薪资"),
            ("salary_max", "最高薪资"),
            ("min_exp", "最低经验"),
            ("max_age", "最大年龄"),
            ("weight", "权重"),
            ("bonus", "加分"),
            ("JSON", "解析结果"),
            ("json", "解析结果"),
            ("null", "空"),
        ]
        for old, new in replacements:
            text = re.sub(re.escape(old), new, text, flags=re.IGNORECASE)
        text = re.sub(
            r'(?<![A-Za-z0-9])OR(?![A-Za-z0-9])',
            '任选其一',
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r'(?<![A-Za-z0-9])AND(?![A-Za-z0-9])',
            '全部满足',
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r'(职位描述第\s*\d+\s*条)(?![：:])', r'\1：', text)
        text = re.sub(
            r'其中一种表明[^，。]*?满足其一即可[，,]\s*属于任选其一关系',
            '任选其一，请确认是否符合预期',
            text,
        )
        text = text.replace('属于任选其一关系', '按任选其一处理')
        text = re.sub(r'\s+', ' ', text).strip()
        return text or "有一处解析结果需要人工确认"

    @staticmethod
    def _format_ai_parse_warning_item(warning):
        """Split one AI warning into its parsed conclusion and confirmation prompt."""
        text = str(warning or "").strip()
        for marker in ("，请确认", "。请确认", " 请确认"):
            if marker not in text:
                continue
            conclusion, prompt = text.split(marker, 1)
            conclusion = conclusion.rstrip("，。；; ")
            prompt = prompt.lstrip("：:，, ").rstrip("。 ")
            return conclusion, f"请确认：{prompt}"
        return text, ""

    def _apply_requirement_parse_result(self, result, parse_id):
        """在主线程中把解析结果填回界面。"""
        if not self._is_current_requirement_parse(parse_id):
            return
        try:
            self._stop_requirement_parse_progress()
            config = result["config"]
            ai_parse_status = result["ai_parse_status"]
            ai_parse_warnings = result["ai_parse_warnings"]
            source_map = result.get("source_map", {})
            job_title = list(config["job_requirements"].keys())[0]
            job_config = config["job_requirements"][job_title]

            job_title = self._clean_display_job_title(job_title)
            self.job_name_var.set(job_title)
            self.config_job_combo.set(job_title)

            self.min_exp_var.set(str(job_config.get("min_exp", 0)))
            self.max_age_var.set(_optional_int_to_entry(job_config.get("max_age")))
            self.edu_var.set(job_config.get("edu", "不限"))
            gender = job_config.get("gender", "不限")
            self.gender_var.set(gender if gender in GENDER_VALUES else "不限")
            self.work_location_var.set(job_config.get("work_location") or "")

            salary_min = job_config.get("salary_min")
            salary_max = job_config.get("salary_max")
            self.salary_min_var.set(str(salary_min) if salary_min is not None else "")
            self.salary_max_var.set(str(salary_max) if salary_max is not None else "")

            # 构建技能→原文出处的映射
            self._populate_skills_from_config(job_config, source_map)
            self._populate_required_from_config(job_config, source_map)

            skills_count = len([s for s in self.skills_data if s.get("source") != "优先"])
            preferred_count = len([s for s in self.skills_data if s.get("source") == "优先"])
            required_count = len(self.required_conditions_data)
            parsed_min_exp = job_config.get("min_exp", 0)
            parsed_edu = job_config.get("edu", "不限")
            parsed_gender = job_config.get("gender", "不限")
            parsed_location = job_config.get("work_location", "")
            gender_part = (
                f"，性别={parsed_gender}" if parsed_gender != "不限" else ""
            )
            loc_part = f"，地点={parsed_location}" if parsed_location else ""
            if salary_min is not None and salary_max is not None:
                salary_part = f"，薪资={salary_min}-{salary_max}K"
            elif salary_min is not None:
                salary_part = f"，薪资≥{salary_min}K"
            elif salary_max is not None:
                salary_part = f"，薪资≤{salary_max}K"
            else:
                salary_part = ""
            preferred_part = f"，优先项={preferred_count}个" if preferred_count else ""
            summary_base = (
                f"岗位={job_title}\n"
                f"经验={parsed_min_exp}年，学历={parsed_edu}{gender_part}"
                f"{loc_part}{salary_part}，"
                f"技能={skills_count}个{preferred_part}，必要条件={required_count}条，方式={ai_parse_status}"
            )

            if skills_count == 0:
                self._set_parse_result_text(
                    f"⚠ 未提取到技术关键字：{summary_base}\n"
                    "请完善招聘需求，或在下方手工添加技能关键词。",
                    self.colors['warning'],
                )
            elif skills_count <= 5:
                self._set_parse_result_text(
                    f"⚠ 仅提取到 {skills_count} 个技术关键字：{summary_base}\n"
                    "建议补充更多技术栈要求，或在下方手工添加关键词。",
                    self.colors['warning'],
                )
            else:
                if getattr(self, '_ai_enhance_pending', False):
                    self._set_parse_result_text(f"✓ 本地规则解析成功：{summary_base}", self.colors['success'])
                    self._start_ai_progress_animation(parse_id)
                else:
                    self._set_parse_result_text(f"✓ 解析成功：{summary_base}", self.colors['success'])
                if ai_parse_warnings:
                    friendly_warnings = [
                        self._humanize_ai_parse_warning(w)
                        for w in ai_parse_warnings[:5]
                    ]
                    self.feedback_support.show_inline_banner(
                        self.config_page,
                        "warning",
                        "AI 已补全解析结果，请确认："
                        + "；".join(friendly_warnings),
                    )

            self.result_detail_frame.pack(
                fill="both", expand=True,
                padx=int(25 * self.dpi_scale * self.zoom_factor),
                pady=int(15 * self.dpi_scale * self.zoom_factor)
            )
            if self._job_step_active >= 0:
                self._update_job_step(2)
                self._bind_job_step_advance()
            else:
                self._show_save_hint()
            if getattr(self, '_ai_enhance_pending', False):
                self._ai_parse_edit_snapshot = self._snapshot_parse_edit_state()
        finally:
            # 如果有 AI key，不恢复按钮（等 AI 增强完成后再恢复）
            _ai_pending = getattr(self, '_ai_enhance_pending', False)
            if self._is_current_requirement_parse(parse_id) and not _ai_pending:
                self._complete_requirement_parse(parse_id)
                self._finish_parse_button()

    def _apply_ai_enhance_result(self, result, parse_id):
        """阶段 2：AI 增强完成后，增量更新界面。"""
        if not self._is_current_requirement_parse(parse_id):
            return
        self._ai_enhance_pending = False
        self._stop_ai_progress_animation()
        self._stop_requirement_parse_progress()

        try:
            if not result.get("ai_success"):
                # AI 失败，只更新状态文字，保留 regex 结果
                status = result.get("ai_parse_status", "本地规则")
                self._set_parse_result_text(f"✓ 解析成功：{status}", self.colors['success'])
                return

            # AI 成功，用 AI 结果更新界面（不覆盖用户已修改的字段）
            config = result["config"]
            ai_parse_status = result["ai_parse_status"]
            ai_parse_warnings = result.get("ai_parse_warnings", [])
            source_map = result.get("source_map", {})
            job_title = list(config["job_requirements"].keys())[0]
            job_config = config["job_requirements"][job_title]
            dirty = set(getattr(self, '_dirty_fields', set()))
            dirty.update(self._dirty_fields_since_parse_snapshot())

            # 只更新非 dirty 字段
            if 'edu' not in dirty:
                self.edu_var.set(job_config.get("edu", "不限"))
            if 'gender' not in dirty:
                gender = job_config.get("gender", "不限")
                self.gender_var.set(
                    gender if gender in GENDER_VALUES else "不限"
                )
            if 'min_exp' not in dirty:
                self.min_exp_var.set(str(job_config.get("min_exp", 0)))
            if 'max_age' not in dirty:
                self.max_age_var.set(_optional_int_to_entry(job_config.get("max_age")))
            if 'work_location' not in dirty:
                self.work_location_var.set(job_config.get("work_location") or "")
            if 'salary' not in dirty:
                salary_min = job_config.get("salary_min")
                salary_max = job_config.get("salary_max")
                self.salary_min_var.set(str(salary_min) if salary_min is not None else "")
                self.salary_max_var.set(str(salary_max) if salary_max is not None else "")

            # 技能和必要条件：AI 增强可能新增/修改，合并而非覆盖
            if 'skills' not in dirty:
                existing = {re.sub(r'\s+', '', s.get("name", "")).lower(): s.get("source", "解析")
                           for s in self.skills_data}
                self._populate_skills_from_config(job_config, source_map, source_override=existing)

            if 'required_conditions' not in dirty:
                self._populate_required_from_config(job_config, source_map)

            # 更新状态
            skills_count = len([s for s in self.skills_data if s.get("source") not in ("优先", "AI优先")])
            preferred_count = len([s for s in self.skills_data if s.get("source") in ("优先", "AI优先")])
            required_count = len(self.required_conditions_data)
            preferred_part = f"，优先项={preferred_count}个" if preferred_count else ""
            summary = (
                f"技能={skills_count}个{preferred_part}，"
                f"必要条件={required_count}条，方式={ai_parse_status}"
            )
            self._set_parse_result_text(f"✓ AI 增强解析完成：{summary}", self.colors['success'])

            if ai_parse_warnings:
                warning_items = [
                    self._format_ai_parse_warning_item(
                        self._humanize_ai_parse_warning(warning)
                    )
                    for warning in ai_parse_warnings[:5]
                ]
                messagebox.showinfo(
                    "AI 解析提醒",
                    "",
                    parent=self.root,
                    headline="AI 增强解析完成，请确认以下内容",
                    numbered_items=warning_items,
                    min_width=820,
                    max_width=900,
                    content_bottom_padding=18,
                )

        except Exception:
            self._set_parse_result_text("✓ AI 增强解析完成（部分字段更新失败）", self.colors['warning'])
        finally:
            self._complete_requirement_parse(parse_id)
            self._finish_parse_button()

    def _start_ai_progress_animation(self, parse_id):
        """启动 AI 增强进度动画（状态栏文字循环闪烁）"""
        self._ai_anim_base = "\n⏳ AI 增强解析中"
        self._ai_anim_dots = 0
        self._ai_anim_running = True
        self._ai_anim_parse_id = parse_id
        # 更新按钮文字为 AI 进度
        if hasattr(self, "btn_parse_requirement"):
            self.btn_parse_requirement.config(state="disabled", text=" AI 增强中…")
        self._tick_ai_animation(parse_id)

    def _tick_ai_animation(self, parse_id):
        """动画帧：循环显示 . / .. / ... / …"""
        if (
            not getattr(self, '_ai_anim_running', False)
            or getattr(self, '_ai_anim_parse_id', None) != parse_id
            or not self._is_current_requirement_parse(parse_id)
        ):
            return
        self._ai_anim_dots = (self._ai_anim_dots + 1) % 4
        dots = "." * self._ai_anim_dots if self._ai_anim_dots > 0 else "…"
        # 在 parse_result_label 上追加动画后缀
        current_text = self.parse_result_label.cget("text")
        # 移除旧的动画后缀（支持换行分隔）
        base = current_text.split("\n⏳ AI 增强")[0] if "\n⏳ AI 增强" in current_text else current_text
        self.parse_result_label.config(text=f"{base}{self._ai_anim_base}{dots}")
        self._ai_anim_after_id = self.root.after(
            500, lambda task_id=parse_id: self._tick_ai_animation(task_id)
        )

    def _stop_ai_progress_animation(self):
        """停止 AI 增强进度动画"""
        self._ai_anim_running = False
        self._ai_anim_parse_id = None
        after_id = getattr(self, '_ai_anim_after_id', None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except (tk.TclError, ValueError):
                pass
        self._ai_anim_after_id = None

    def _finish_parse_button(self):
        """恢复解析按钮状态"""
        if hasattr(self, "btn_parse_requirement"):
            self.btn_parse_requirement.config(
                state="normal",
                text=getattr(self, "_parse_requirement_button_text", " 解析招聘需求"),
            )
        if hasattr(self, "btn_save"):
            self.btn_save.state(['!disabled'])

    def _handle_requirement_parse_error(self, exc, parse_id):
        if not self._is_current_requirement_parse(parse_id):
            return
        self._ai_enhance_pending = False
        self._stop_ai_progress_animation()
        self._stop_requirement_parse_progress()
        self._complete_requirement_parse(parse_id)
        self._finish_parse_button()
        friendly_reason = self._friendly_ai_parse_reason(str(exc))
        self._set_parse_result_text(
            f"解析失败：{friendly_reason}\n可以稍后再试，或先手工填写岗位配置。",
            self.colors['danger'],
        )

    def _start_requirement_parse_progress(self, use_ai, parse_id):
        self._stop_requirement_parse_progress()
        messages = [
            (7000, "还在处理：正在整理技能、优先项和必要条件。"),
        ]
        if use_ai:
            messages.extend([
                (16000, "AI 还没返回：网络或模型可能有点慢，请耐心等待。"),
                (30000, "继续等待 AI：如果服务超时，会保留本地解析结果，不会丢失内容。"),
            ])
        self._requirement_parse_after_ids = []
        for delay, message in messages:
            after_id = self.root.after(
                delay,
                lambda m=message, task_id=parse_id: (
                    self._set_parse_result_text(m, self.colors['warning'])
                    if self._is_current_requirement_parse(task_id)
                    else None
                ),
            )
            self._requirement_parse_after_ids.append(after_id)

    def _stop_requirement_parse_progress(self):
        for after_id in getattr(self, "_requirement_parse_after_ids", []):
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        self._requirement_parse_after_ids = []

    def _set_parse_result_text(self, text, foreground=None):
        try:
            wraplength = max(360, self.parse_result_label.winfo_width() - int(20 * self.dpi_scale * self.zoom_factor))
            self.parse_result_label.config(wraplength=wraplength)
        except Exception:
            pass
        self.parse_result_label.config(text=text, foreground=foreground or self.colors['success'])

    def _clean_display_job_title(self, title):
        title = re.sub(r'\s+', ' ', str(title or '')).strip()
        title = re.sub(r'^(?:岗位|职位|招聘)\s*\d+\s*[：:、.\-]\s*', '', title)
        title = re.sub(r'^\d+\s*[：:、.\-]\s*', '', title)
        return title.strip()

    def _update_job_step(self, active_step: int):
        """更新新建岗位步骤引导条，active_step: 0-3 表示当前步骤"""
        if not hasattr(self, '_job_step_bar') or not self._job_step_labels:
            return
        self._job_step_active = active_step
        # 显示步骤条（用 after 确保插入到岗位选择行之后，而非追加到末尾）
        _fs = self.dpi_scale * self.zoom_factor
        try:
            self._job_step_bar.pack_info()
        except tk.TclError:
            # 尚未 pack，用 after 插入到正确位置
            self._job_step_bar.pack(fill="x", after=self._config_select_frame,
                padx=int(25 * _fs), pady=(int(5 * _fs), 0))
        for i, lbl in enumerate(self._job_step_labels):
            if i < active_step:
                # 已完成：绿色 ✓
                original = ["① 填入需求", "② 解析需求", "③ 检查结果", "④ 保存配置"][i]
                done_text = f"✓ {original[2:]}"  # 去掉数字圆圈，加 ✓
                lbl.config(text=done_text, foreground=self.colors['success'])
            elif i == active_step:
                # 当前步骤：蓝色加粗效果
                original = ["① 填入需求", "② 解析需求", "③ 检查结果", "④ 保存配置"][i]
                lbl.config(text=original, foreground=self.colors['primary'])
            else:
                # 未到：灰色
                original = ["① 填入需求", "② 解析需求", "③ 检查结果", "④ 保存配置"][i]
                lbl.config(text=original, foreground=self.colors.get('text_muted', ui_theme.TEXT_MUTED))

    def _hide_job_step_bar(self):
        """隐藏新建岗位步骤引导条"""
        if hasattr(self, '_job_step_bar'):
            self._job_step_bar.pack_forget()
        self._job_step_active = -1

    def _bind_job_step_advance(self):
        """包装 canvas 的 yscrollcommand，滚动到底部时推进到保存配置步骤"""
        self._job_step_edit_done = False

        if hasattr(self, '_job_step_yscroll_wrapped'):
            return  # 已包装，只需重置标志

        self._job_step_yscroll_wrapped = True

        # 找到与 canvas 同级的 Scrollbar，取其 .set 方法作为原始回调
        _scrollbar_set = None
        for sibling in self.config_canvas.master.winfo_children():
            if isinstance(sibling, ttk.Scrollbar):
                _scrollbar_set = sibling.set
                break

        def _wrapped_yscroll(top, bottom):
            if _scrollbar_set:
                _scrollbar_set(top, bottom)
            if self._job_step_edit_done:
                return
            if self._job_step_active == 2 and float(bottom) >= 0.95:
                self._job_step_edit_done = True
                self._update_job_step(3)
                self._show_save_hint()

        self.config_canvas.configure(yscrollcommand=_wrapped_yscroll)

    def add_job(self):
        """新建岗位"""
        if not self._confirm_job_form_transition():
            return
        self._initialize_new_job_draft()

    def _initialize_new_job_draft(self):
        """Reset every job-config surface to the first step of a new draft."""
        self.reset_job_form()
        self.config_job_combo.set("")
        self.btn_restore_job.configure(text=" 清空内容")
        self._set_requirement_section_expanded(True)
        self.requirement_template_btn.state(['!disabled'])
        self._show_requirement_hint()
        self._hide_btn_add_hint()
        self._update_job_step(0)
        self._refresh_job_form_status()
        self.config_canvas.yview_moveto(0)

    def delete_job(self):
        """删除岗位"""
        job_name = self.config_job_combo.get()
        if job_name in self.job_rules:
            if messagebox.ask_confirmation(
                "删除岗位",
                headline=f"删除岗位“{job_name}”？",
                message="该岗位的筛选配置将从本地配置中移除。",
                notice="删除后需要重新配置该岗位。",
                yes_label="删除岗位",
                no_label="取消",
                dangerous=True,
                parent=getattr(self, "root", None),
            ):
                del self.job_rules[job_name]
                self.save_config()
                self.config_job_combo['values'] = list(self.job_rules.keys())
                self.config_job_combo.set('')
                self.reset_job_form()
                self._hide_job_step_bar()

    def _build_current_job_rule_preview(self):
        """Build an unsaved job config from the current form for diagnostics."""
        job_name = self.job_name_var.get().strip()
        normalized_job_name = re.sub(r'\s+', ' ', job_name).strip()

        keywords = [
            {"name": s["name"], "weight": s["weight"]}
            for s in self.skills_data
            if not self._is_preferred_skill_source(s.get("source"))
        ]
        preferred_keywords = [
            {"name": s["name"], "bonus": s["weight"]}
            for s in self.skills_data
            if self._is_preferred_skill_source(s.get("source"))
        ]
        required_conditions = [
            self._strip_transient_fields(cond)
            for cond in self.required_conditions_data
        ]

        salary_min = None
        salary_max = None
        salary_min_str = self.salary_min_var.get().strip()
        salary_max_str = self.salary_max_var.get().strip()
        if salary_min_str:
            try:
                salary_min = int(salary_min_str)
            except ValueError:
                raise ValueError("薪资范围最低值必须为数字（如：12）")
        if salary_max_str:
            try:
                salary_max = int(salary_max_str)
            except ValueError:
                raise ValueError("薪资范围最高值必须为数字（如：15）")

        try:
            min_exp = int(self.min_exp_var.get())
            max_age = _parse_optional_int_entry(self.max_age_var.get(), "最大年龄")
        except ValueError as e:
            raise ValueError(str(e))

        return normalized_job_name, {
            "min_exp": min_exp,
            "edu": self.edu_var.get(),
            "gender": self.gender_var.get(),
            "max_age": max_age,
            "work_location": self.work_location_var.get().strip() or None,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "keywords": keywords,
            "preferred_keywords": preferred_keywords,
            "required_conditions": required_conditions,
            "original_requirement": self._get_requirement_text() or None,
        }

    def _confirm_job_config_diagnostics(self, job_name, rule):
        """Run save-time diagnostics and return True when saving may continue."""
        issues = diagnose_job_config(job_name, rule)
        if not issues:
            return True

        has_error = any(issue.severity == "error" for issue in issues)
        text = summarize_job_config_diagnostics(job_name, rule, issues=issues)
        return self._show_job_config_diagnostics_dialog(text, has_error)

    def _show_job_config_diagnostics_dialog(self, text, has_error=False, context="save"):
        """Show diagnostics in a scrollable dialog and return whether to continue."""
        result = {"continue": False}
        win = tk.Toplevel(self.root)
        win.title("岗位配置体检")
        win.transient(self.root)
        win.grab_set()
        win.withdraw()

        scale = self.dpi_scale * self.zoom_factor
        dialog_width = int(720 * scale)
        win.grid_rowconfigure(0, weight=1)
        win.grid_columnconfigure(0, weight=1)

        body = ttk.Frame(win, padding=int(16 * scale))
        body.grid(row=0, column=0, sticky="nsew")

        if context == "preview":
            summary_text = (
                "当前配置存在阻断项。"
                if has_error else
                "当前配置的检查结果如下。"
            )
            continue_text = ""
            close_text = "关闭"
        elif context == "run":
            summary_text = (
                "发现严重问题，必须先修改岗位配置。"
                if has_error else
                "发现一些提醒项，可返回修改，也可确认后继续本次运行。"
            )
            continue_text = "仍然运行"
            close_text = "返回修改"
        else:
            summary_text = "发现严重问题，请返回修改后再保存。" if has_error else "发现一些提醒项，可返回修改，也可确认后继续保存。"
            continue_text = "仍然保存"
            close_text = "返回修改"
        ttk.Label(
            body,
            text=summary_text,
            font=self.font_label,
            foreground=self.colors['danger'] if has_error else self.colors['warning'],
        ).pack(anchor="w", pady=(0, int(8 * scale)))

        text_widget = tk.Text(
            body,
            wrap="word",
            font=self.font_log,
            bg=self.colors['bg_card'],
            fg=self.colors['text_primary'],
            borderwidth=1,
            relief="solid",
        )
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)
        text_widget.insert("1.0", text)
        text_font = font.Font(font=self.font_log)
        available_text_width = max(1, dialog_width - int(64 * scale))
        estimated_rows = sum(
            max(1, math.ceil(text_font.measure(line or " ") / available_text_width))
            for line in (str(text).splitlines() or [""])
        )
        text_widget.configure(height=max(6, min(18, estimated_rows)))
        text_widget.configure(state="disabled")
        text_widget.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        btn_row = ttk.Frame(win, padding=(int(16 * scale), 0, int(16 * scale), int(16 * scale)))
        btn_row.grid(row=1, column=0, sticky="ew")

        def _continue():
            result["continue"] = True
            win.destroy()

        ttk.Button(btn_row, text=close_text, command=win.destroy).pack(side="right")
        if context != "preview" and not has_error:
            ttk.Button(btn_row, text=continue_text, command=_continue).pack(
                side="right", padx=(0, int(8 * scale))
            )

        win.update_idletasks()
        dialog_height = min(
            int(520 * scale),
            max(int(260 * scale), win.winfo_reqheight()),
        )
        _place_window_centered(win, dialog_width, dialog_height, parent=self.root)
        win.deiconify()

        try:
            win.wait_window()
        except Exception:
            return False
        return result["continue"]

    def _should_prompt_run_job_config(self, text, has_error):
        """Return whether run diagnostics still require a dialog this session."""
        return bool(has_error or text not in self._acknowledged_job_config_warnings)

    def _remember_run_job_config_warning(self, text, has_error, confirmed):
        """Remember an accepted warning until its deterministic diagnostic text changes."""
        if confirmed and not has_error:
            self._acknowledged_job_config_warnings.add(text)

    def save_current_job(self):
        """保存当前岗位配置"""
        if not self._ensure_data_storage_available("保存岗位配置"):
            return False
        if getattr(self, '_active_requirement_parse_id', None) is not None:
            messagebox.showwarning(
                "招聘需求正在解析",
                "请等待解析完成后再保存岗位配置。",
                parent=self.root,
            )
            return False
        self._hide_save_hint()
        job_name = self.job_name_var.get().strip()
        if not job_name:
            messagebox.showwarning("警告", "岗位名称不能为空")
            return False

        # 先验证表单输入（薪资、经验、年龄等），再弹交互提示
        try:
            normalized_job_name, rule = self._build_current_job_rule_preview()
        except ValueError as e:
            messagebox.showwarning("警告", str(e))
            return False

        # 检查是否已存在相同（规范化后）的岗位
        loaded_key = str(getattr(self, "_job_form_loaded_name", "") or "")
        if loaded_key not in self.job_rules:
            loaded_key = ""
        matching_key = None
        for key in self.job_rules.keys():
            if job_names_equal(key, normalized_job_name):
                matching_key = key
                break

        if loaded_key and matching_key and matching_key != loaded_key:
            messagebox.showwarning(
                "岗位名称冲突",
                f"“{normalized_job_name}”已经属于另一个岗位。\n"
                "为避免历史候选人错误合并，请换一个名称。",
                parent=self.root,
            )
            return False

        if (
            not loaded_key
            and matching_key
            and matching_key != normalized_job_name
        ):
            if not messagebox.ask_confirmation(
                "岗位已存在",
                headline=f"覆盖岗位“{matching_key}”？",
                message="当前表单内容将替换这个岗位已保存的配置。",
                notice="原配置不会单独保留。",
                yes_label="覆盖更新",
                no_label="取消",
                dangerous=True,
                parent=self.root,
            ):
                return False

        if not self._confirm_job_config_diagnostics(normalized_job_name, rule):
            return False

        identity_source_key = loaded_key or matching_key
        identity_source = (
            self.job_rules.get(identity_source_key, {})
            if identity_source_key
            else {}
        )
        stable_id = normalize_job_uuid(identity_source.get("job_uuid"))
        if not stable_id and identity_source_key:
            stable_id = legacy_job_uuid(identity_source_key)
        rule["job_uuid"] = stable_id or new_job_uuid()

        key_to_delete = loaded_key or matching_key
        if key_to_delete and key_to_delete != normalized_job_name:
            del self.job_rules[key_to_delete]

        self.job_rules[normalized_job_name] = rule

        if not self.save_config():
            return False
        self.config_job_combo['values'] = list(self.job_rules.keys())
        self.config_job_combo.set(normalized_job_name)
        self._remember_run_job_selection(normalized_job_name)
        if hasattr(self, 'job_select_var') and hasattr(self, 'job_combo'):
            self._sync_run_job_combo_values(self.job_rules, prefer_current=False)
        restore_button = getattr(self, 'btn_restore_job', None)
        if restore_button is not None:
            restore_button.configure(text=" 恢复已保存")
        # 步骤完成：先显示全绿，800ms 后隐藏引导条
        if self._job_step_active >= 0:
            _step_texts = ["① 填入需求", "② 解析需求", "③ 检查结果", "④ 保存配置"]
            for i, lbl in enumerate(self._job_step_labels):
                lbl.config(text=f"✓ {_step_texts[i][2:]}", foreground=self.colors['success'])
            self.root.after(800, self._hide_job_step_bar)
        else:
            self._hide_job_step_bar()
        self._show_btn_add_hint()
        self._set_job_form_baseline(normalized_job_name)
        self._status_flash(f"岗位配置已保存：{normalized_job_name}")
        return True

    def _restore_or_clear_job_form(self):
        """Restore an existing job or clear the current new-job draft."""
        selected_job = self.config_job_combo.get()
        if selected_job in self.job_rules:
            if not messagebox.ask_confirmation(
                "恢复已保存",
                headline=f"恢复“{selected_job}”已保存的配置？",
                message="当前尚未保存的表单修改将被放弃。",
                notice="此操作不会修改已经保存的岗位配置。",
                yes_label="放弃修改并恢复",
                no_label="继续编辑",
                dangerous=True,
                parent=self.root,
            ):
                return
            self.load_job_to_form(self.job_rules[selected_job])
            self._set_requirement_section_expanded(False)
            return

        if not messagebox.ask_confirmation(
            "清空岗位草稿",
            headline="清空当前新岗位的全部内容？",
            message="岗位名称、筛选条件、技能和必要条件都会被清空。",
            notice="尚未保存的内容无法恢复。",
            yes_label="清空草稿",
            no_label="继续编辑",
            dangerous=True,
            parent=getattr(self, "root", None),
        ):
            return
        self._initialize_new_job_draft()

    def reset_job_form(self):
        """Reset the form to a new draft without implicit screening limits."""
        self._invalidate_requirement_parse()
        self._job_form_loading = True
        self.job_name_var.set("")
        self.min_exp_var.set("0")
        self.max_age_var.set("")
        self.edu_var.set("不限")
        self.gender_var.set("不限")
        self.work_location_var.set("")
        self.salary_min_var.set("")
        self.salary_max_var.set("")
        self.skills_data = []
        self.refresh_skills_tree()
        self.required_conditions_data = []
        self.refresh_required_listbox()
        self._required_evidence_map = {}
        self._dirty_fields = set()
        self._ai_parse_edit_snapshot = None
        self.requirement_text.delete("1.0", tk.END)
        self.requirement_text.tag_remove("placeholder", "1.0", tk.END)
        self.requirement_text.insert("1.0", self._req_placeholder_text, "placeholder")
        self._req_placeholder_active = True
        self.requirement_text.edit_modified(False)
        self.parse_result_label.config(text="")
        self._hide_requirement_hint()
        self._hide_parse_hint()
        self._hide_save_hint()
        self._job_form_loading = False
        self._set_job_form_baseline("")

    def load_config_dialog(self):
        """打开配置对话框"""
        filename = filedialog.askopenfilename(title="选择配置文件", filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not filename:
            return False
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                config = json.load(f)
            # 支持新旧两种格式
            if "job_requirements" in config:
                self.job_rules = config["job_requirements"]
            elif "jobs" in config:
                self.job_rules = config["jobs"]
            else:
                self.job_rules = {}
            self.save_config()
            self.config_job_combo['values'] = list(self.job_rules.keys())
            if self.job_rules:
                first_job = next(iter(self.job_rules))
                self.config_job_combo.set(first_job)
                self.load_job_to_form(self.job_rules[first_job])
                self._set_requirement_section_expanded(False)
                self.requirement_template_btn.state(['disabled'])
            else:
                self.config_job_combo.set("")
                self.reset_job_form()
                self._set_requirement_section_expanded(True)
            self._status_flash(f"已加载岗位配置：{len(self.job_rules)} 个岗位")
            return True
        except Exception as e:
            messagebox.show_failure(
                "导入岗位配置",
                headline="岗位配置未加载",
                message="原有岗位配置保持不变。",
                detail=str(e),
                parent=self.root,
            )
            return False

    def save_config_dialog(self):
        """保存配置对话框"""
        filename = filedialog.asksaveasfilename(title="保存配置文件", defaultextension=".json", filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if filename:
            try:
                config = {"jobs": self.job_rules}
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=4)
                self._status_flash("岗位配置文件已导出")
            except Exception as e:
                messagebox.show_failure(
                    "导出岗位配置",
                    headline="岗位配置文件未保存",
                    message="请检查保存位置后重试。",
                    detail=str(e),
                    parent=self.root,
                )

    def import_config(self):
        """导入配置"""
        if not self._confirm_job_form_transition():
            return
        self._invalidate_requirement_parse()
        self.load_config_dialog()

    def export_config(self):
        """导出配置"""
        self.save_config_dialog()

    def clear_log(self):
        """清空日志"""
        self.log_text.config(state="normal")
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state="disabled")

    def append_log(self, message):
        """Record non-scan diagnostics in a separate application log."""
        safe_message = self._sanitize_runtime_log_message(message)
        logger.info("[GUI] %s", safe_message)
        self._append_runtime_log_file("app", safe_message, add_timestamp=True)

    def append_run_log(self, message):
        """追加候选人扫描及其运行准备过程日志。"""
        safe_message = self._sanitize_runtime_log_message(message)
        log_queue = getattr(self, "log_queue", None)
        if log_queue is not None:
            log_queue.put(safe_message)
        self._append_run_log_file(safe_message)

    def append_operation_log(self, message):
        """Record a foreground business event without polluting the scan UI."""
        safe_message = self._sanitize_runtime_log_message(message)
        logger.info("[GUI] %s", safe_message)
        self._append_runtime_log_file("app", safe_message, add_timestamp=True)

    @staticmethod
    def _sanitize_runtime_log_message(message):
        try:
            from bossmaster import redact_boss_sensitive_text
            return redact_boss_sensitive_text(message)
        except Exception:
            return str(message or "")

    def _append_run_log_file(self, message):
        """把运行日志追加落盘；失败不影响扫描主流程。"""
        self._append_runtime_log_file("run", message)

    def _append_runtime_log_file(self, prefix, message, *, add_timestamp=False):
        """Append one sanitized line and surface the first write failure."""
        try:
            RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._cleanup_runtime_logs_once()
            log_path = RUN_LOG_DIR / f"{prefix}-{datetime.now().strftime('%Y%m%d')}.log"
            safe_message = self._sanitize_runtime_log_message(message)
            if add_timestamp:
                safe_message = f"[{datetime.now().strftime('%H:%M:%S')}] {safe_message}"
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(f"{safe_message}\n")
        except Exception as e:
            logger.warning("[GUI] 运行日志落盘失败: %s", e)
            if not getattr(self, "_runtime_log_write_warning_emitted", False):
                self._runtime_log_write_warning_emitted = True
                try:
                    self.log_queue.put(f"⚠ 运行日志写入失败：{str(e)[:80]}")
                except Exception:
                    pass

    def _cleanup_runtime_logs_once(self):
        """Delete only this application's run/app logs older than the retention window."""
        if getattr(self, "_runtime_log_cleanup_done", False):
            return
        self._runtime_log_cleanup_done = True
        cutoff = time.time() - RUNTIME_LOG_RETENTION_DAYS * 24 * 60 * 60
        log_root = RUN_LOG_DIR.resolve()
        for pattern in ("run-*.log", "app-*.log"):
            for log_path in RUN_LOG_DIR.glob(pattern):
                try:
                    resolved = log_path.resolve()
                    if resolved.parent != log_root:
                        continue
                    if resolved.stat().st_mtime < cutoff:
                        resolved.unlink()
                except OSError:
                    continue

    def _append_run_settings_snapshot(self, settings):
        """记录本轮运行参数到落盘日志，避免占用界面过程日志。"""
        self._append_run_log_file("本轮参数设置：")
        for label, value in settings:
            self._append_run_log_file(f"  {label}：{value}")

    def run_on_ui(self, callback):
        """在 Tk 主线程执行 UI 更新（线程安全）。

        后台线程不能直接调用 root.after()，改用队列 + 主线程轮询。
        """
        self.ui_queue.put(callback)

    def _process_ui_queue(self):
        """处理 UI 更新队列（由主线程定时器调用）"""
        try:
            while True:
                callback = self.ui_queue.get_nowait()
                try:
                    callback()
                except Exception as e:
                    print(f"[UI 队列] 回调执行失败: {e}")
        except queue.Empty:
            pass
        self.root.after(50, self._process_ui_queue)

    def _get_lamp_icon(self, color):
        """按状态颜色取交通灯图标（带缓存），统一替代文本状态圆点。"""
        cache = getattr(self, '_lamp_icon_cache', None)
        if cache is None:
            cache = self._lamp_icon_cache = {}
        if color == self.colors.get('success'):
            kind = 'success'
        elif color == self.colors.get('danger'):
            kind = 'error'
        else:
            kind = 'pending'
        if kind not in cache:
            size = int(
                TRAFFIC_LIGHT_BASE_SIZE
                * getattr(self, 'dpi_scale', 1.0)
                * getattr(self, 'zoom_factor', 1.0)
            )
            cache[kind] = self.icons.get(
                f'traffic_light_{kind}', size, self.colors.get('text_primary', ui_theme.TEXT_PRIMARY))
        return cache[kind]

    def _apply_lamp_status(self, label, status_text, color):
        """把 "● 文本" 形式的状态渲染为交通灯图标 + 文本，颜色语义保持不变；图标不可用时保留原文本。"""
        display_text = status_text
        image = ""
        if status_text.startswith("● "):
            try:
                image = self._get_lamp_icon(color)
            except Exception:
                image = ""
            if image:
                display_text = " " + status_text[2:]
        label.config(
            text=display_text, foreground=color,
            image=image, compound='left' if image else 'none',
        )
        label._icon_ref = image

    def set_browser_ui(self, indicator_text=None, indicator_color=None, help_text=None, start_state=None):
        """线程安全更新浏览器状态控件，并缓存状态文本供后台线程判断。"""
        if indicator_text is not None:
            self._browser_status_text = indicator_text
        if help_text is not None:
            self._browser_status_help_text = help_text

        def apply_update():
            if indicator_text is not None:
                self._apply_lamp_status(self.browser_status_indicator, indicator_text, indicator_color)
            if help_text is not None:
                self.browser_status_help.config(text=help_text)
            # 运行中不覆盖按钮状态，防止轮询覆盖 start_run 的 disabled
            if start_state is not None and not self.is_running:
                self.start_btn.config(state=start_state)

        self.run_on_ui(apply_update)

    def update_log(self):
        """更新日志显示"""
        log_text = getattr(self, 'log_text', None)
        if log_text is None:
            try:
                self.root.after(100, self.update_log)
            except tk.TclError:
                pass
            return
        try:
            while True:
                message = self.log_queue.get_nowait()
                log_text.config(state="normal")
                log_text.insert(tk.END, message + "\n")
                log_text.see(tk.END)
                log_text.config(state="disabled")
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            self.root.after(100, self.update_log)
        except tk.TclError:
            pass

    def _auto_check_selectors(self):
        """连接成功后自动检查选择器健康状态（仅在 check() 工作线程中调用）

        每次新连接到推荐牛人页面时执行一次，有异常弹窗提醒。
        """
        if self._selectors_auto_checked:
            return
        if not self.browser_connected or not self.browser_page:
            return
        page = self.browser_page
        try:
            page.run_js('return 1')
            if page is not self.browser_page:
                return
            current_url = str(getattr(page, 'url', '') or '')
            if not self._is_boss_recommend_url(current_url):
                self.append_log("选择器自动检查已跳过：当前页面不是 BOSS 推荐牛人页面")
                return
            from bossmaster import check_selectors_health
            results = check_selectors_health(page)
            self._selectors_auto_checked = True
            self._selector_check_retry_pending = False

            ok_count = sum(1 for r in results if r['status'] == 'ok')
            skip_count = sum(1 for r in results if r['status'] == 'skip')
            warn_count = sum(1 for r in results if r['status'] == 'warn')
            fail_count = sum(1 for r in results if r['status'] == 'fail')

            for r in results:
                if r['status'] == 'skip':
                    self.append_log(f"选择器自动检查已跳过 [{r['group']}]：{r['detail']}")

            # 只在有异常时输出日志
            if warn_count + fail_count > 0:
                self.append_log(
                    f"选择器自动检查：{ok_count} 正常 / {skip_count} 跳过 / "
                    f"{warn_count} 警告 / {fail_count} 失败"
                )

                for r in results:
                    if r['status'] in ('warn', 'fail'):
                        icon = {'warn': '⚠', 'fail': '✗'}.get(r['status'], '?')
                        self.append_log(f"  {icon} [{r['group']}] {r['name']}: {r['detail']}")

                self.append_log("⚠ 选择器异常可能导致扫描功能不正常，可编辑 selectors.json 修复")
                # 主线程弹窗提醒（线程安全）
                self.run_on_ui(lambda: messagebox.show_notice(
                    "选择器异常",
                    headline="部分页面选择器未通过自动检查",
                    message="这些异常可能导致扫描功能无法正常读取页面。",
                    metrics=(
                        ("失败", str(fail_count)),
                        ("警告", str(warn_count)),
                        ("正常", str(ok_count)),
                    ),
                    notice="可编辑 selectors.json 修复；具体项目已写入应用日志。",
                    parent=self.root,
                ))
        except Exception as e:
            self._selectors_auto_checked = False
            error_text = str(e).splitlines()[0] if str(e) else type(e).__name__
            from bossmaster import is_transient_page_refresh_error
            if is_transient_page_refresh_error(e):
                if not getattr(self, '_selector_check_retry_pending', False):
                    self.append_log("选择器自动检查暂缓：页面正在加载，稳定后将自动重试")
                self._selector_check_retry_pending = True
                return
            from bossmaster import is_page_connection_error
            if is_page_connection_error(e):
                self.browser_connected = False
                if page is self.browser_page:
                    self.browser_page = None
                self.append_log("浏览器页面连接短暂中断，等待自动重连...")
                return
            self.append_log(f"选择器自动检查失败：{error_text}")

    def _reactivate_and_navigate(self, page, target_url):
        """激活已有的 Chrome 进程并导航到目标页面。

        当 Chrome 关闭窗口但未退出时（macOS 常见），调试端口仍然可用，
        通过 AppleScript 激活 Chrome 窗口后直接导航，避免杀进程重启。

        Returns:
            导航成功返回新的/更新后的 page 对象，失败返回 None。
        """
        # macOS: 用 AppleScript 激活 Chrome 窗口
        if sys.platform == 'darwin':
            try:
                subprocess.run([
                    'osascript', '-e',
                    'tell application "Google Chrome" to activate'
                ], capture_output=True, timeout=3)
                time.sleep(1)
            except Exception:
                pass

        # 尝试 page.get() 直接导航（比 new_tab 更可靠）
        try:
            page.get(target_url)
            time.sleep(2)
            return page
        except Exception:
            return None

    def _should_defer_browser_navigation_warning(self, silent: bool) -> bool:
        """自动轮询首次读到非推荐页时暂缓告警，过滤页面刷新产生的瞬时 URL。"""
        return _browser_controller_for(self).should_defer_navigation(silent)

    def _should_defer_browser_connection_failure(self, silent: bool) -> bool:
        """自动轮询首次连接失败时暂缓报错，给页面连接一次自恢复机会。"""
        return _browser_controller_for(self).should_defer_connection_failure(silent)

    @staticmethod
    def _boss_access_cooldown_state():
        """Read the shared BOSS cooldown without making GUI startup fragile."""
        try:
            from bossmaster import get_boss_access_block_state
            return get_boss_access_block_state()
        except Exception as exc:
            return {
                "blocked": True,
                "remaining_seconds": 15 * 60,
                "status": "状态读取失败",
                "reason": f"无法读取访问保护状态：{type(exc).__name__}",
                "source": "本地状态恢复",
            }

    def _show_boss_access_cooldown(self, state, *, silent):
        remaining = max(1, int(state.get("remaining_seconds", 0) + 0.999))
        reason = str(state.get("reason") or "已触发 BOSS 访问保护")
        source = str(state.get("source") or "")
        help_text = f"访问冷却中，剩余约 {remaining} 秒"
        if source:
            help_text += f"；来源：{source}"
        self.set_browser_ui(
            "● 冷却中",
            self.colors['warning'],
            help_text,
            "disabled",
        )
        signature = (
            state.get("status"),
            reason,
            source,
            max(1, (remaining + 59) // 60),
        )
        should_log = not silent or signature != getattr(
            self, "_boss_cooldown_log_signature", None
        )
        self._boss_cooldown_log_signature = signature
        if should_log:
            return f"[访问保护] {reason}。剩余冷却约 {remaining} 秒。"
        return ""

    def check_browser_connection(self, silent=False):
        """检测浏览器连接状态

        Args:
            silent: True 时只更新 UI 不写日志（用于自动轮询）
        """
        guard_state = self._boss_access_cooldown_state()
        if guard_state.get("blocked"):
            cooldown_log = self._show_boss_access_cooldown(
                guard_state,
                silent=silent,
            )
            if cooldown_log:
                self.append_log(cooldown_log)
            return
        self._boss_cooldown_log_signature = None
        if getattr(self, '_browser_check_running', False):
            # 手动点击优先：标记待处理，当前 silent 检查结束后自动重试
            if not silent:
                self._pending_manual_check = True
                self.append_log("⏳ 正在执行其他检测，稍后自动重试...")
            return
        self._browser_check_running = True

        def check():
            connection_lock_acquired = False
            try:
                connection_lock_acquired = self._browser_connection_lock.acquire(blocking=False)
                if not connection_lock_acquired:
                    return
                if not silent:
                    self.append_log("正在检测浏览器连接...")

                # 已有可用连接，直接复用，不做端口检查
                if self.browser_page is not None:
                    try:
                        prev_help = self._browser_status_help_text
                        url_probe = probe_page_url(
                            self.browser_page,
                            timeout=1,
                        )
                        if url_probe.error is not None:
                            raise url_probe.error
                        current_url = url_probe.url
                        url_state = classify_browser_url(
                            current_url,
                            recommend_matcher=self._is_boss_recommend_url,
                        )
                        _browser_controller_for(self).reset_connection_failures()
                        if url_state == "recommend":
                            _browser_controller_for(self).reset_navigation_failures()
                            self.browser_connected = True
                            self.set_browser_ui("● 已连接", self.colors['success'], "已连接到 BOSS 直聘推荐牛人页面", "normal")
                            if prev_help != "已连接到 BOSS 直聘推荐牛人页面":
                                self.append_log("✓ 已连接到 BOSS 直聘推荐牛人页面")
                        else:
                            if self._should_defer_browser_navigation_warning(silent):
                                return
                            self.browser_connected = False
                            self.set_browser_ui("● 需导航", self.colors['warning'], "浏览器已连接，请导航到 BOSS 直聘推荐牛人页面", "disabled")
                            if prev_help != "浏览器已连接，请导航到 BOSS 直聘推荐牛人页面":
                                self.append_log("⚠ 浏览器已连接，请导航到 BOSS 直聘推荐牛人页面")
                        return
                    except Exception:
                        # 页面对象已失效，清理后走完整检测流程
                        self.browser_page = None
                        self.browser_connected = False
                        self._selectors_auto_checked = False  # 页面失效，下次连接重新检查选择器

                # 没有可用连接，按显式地址、持久化端口、默认端口顺序检查。
                addr = _browser_controller_for(self).address_candidates(
                    getattr(self, "browser_address", ""),
                )[0]
                _host, port = addr.rsplit(':', 1)
                port_open = is_debug_port_open(addr, timeout=1)

                if not port_open:
                    self.browser_connected = False
                    self.set_browser_ui(
                        "● 未连接",
                        self.colors["danger"],
                        start_state="disabled",
                    )
                    if silent:
                        self.set_browser_ui(
                            help_text="未检测到 Chrome，请确保浏览器已启动"
                        )
                        return

                    self.set_browser_ui(help_text="正在启动 Chrome 浏览器...")
                    self.append_log("正在启动 Chrome 浏览器...")
                    if self._launch_boss_browser():
                        self.set_browser_ui(
                            "● 已连接",
                            self.colors["success"],
                            "已连接到 BOSS 直聘推荐牛人页面",
                            "normal",
                        )
                        self.append_log("✓ 已连接到 BOSS 直聘推荐牛人页面")
                    else:
                        error = (
                            getattr(self, "_greet_queue_browser_error", "")
                            or "Chrome 启动或页面连接失败"
                        )
                        self.set_browser_ui(
                            "● 未连接",
                            self.colors["danger"],
                            error,
                            "disabled",
                        )
                        self.append_log(f"✗ {error}")
                    return
                try:
                    connection = connect_browser_address(addr, timeout=3)
                    if connection.error is not None:
                        raise connection.error
                    page = connection.page
                    current_url = connection.url
                    url_state = classify_browser_url(
                        current_url,
                        recommend_matcher=self._is_boss_recommend_url,
                    )
                    _browser_controller_for(self).reset_connection_failures()

                    # Chrome 进程还在但窗口已关闭时，page.url 可能是 about:blank
                    # 直接在现有进程里导航到 BOSS 直聘，不杀进程不重启
                    target_url = 'https://www.zhipin.com/web/chat/recommend'
                    if url_state == "blank":
                        if not silent:
                            self.append_log("⚠ Chrome 进程存在但无有效页面，正在激活并导航...")
                            nav_page = self._reactivate_and_navigate(page, target_url)
                            if nav_page is not None:
                                self.browser_connected = True
                                self.browser_page = nav_page
                                self.browser_address = page.address
                                try:
                                    nav_url = nav_page.url or ''
                                except Exception:
                                    nav_url = ''
                                if self._is_boss_recommend_url(nav_url):
                                    self.set_browser_ui("● 已连接", self.colors['success'], "已连接到 BOSS 直聘推荐牛人页面", "normal")
                                    self.append_log("✓ 已连接到 BOSS 直聘推荐牛人页面")
                                else:
                                    self.set_browser_ui("● 需导航", self.colors['warning'], "已激活 Chrome，正在加载页面...", "disabled")
                                    self.append_log("⚠ 已激活 Chrome，请等待页面加载完成")
                            else:
                                self.browser_connected = False
                                self.browser_page = None
                                self.set_browser_ui("● 需导航", self.colors['warning'], "请手动打开 Chrome 窗口", "disabled")
                                self.append_log("⚠ 无法激活 Chrome 页面，请手动打开 Chrome 窗口后点击重试")
                        else:
                            # 自动轮询：不尝试导航，避免 page.get() 挂起
                            self.browser_connected = False
                            self.browser_page = None
                            prev_state = self._browser_status_text
                            self.set_browser_ui("● 需导航", self.colors['warning'], "Chrome 进程存在但无有效页面", "disabled")
                            if prev_state != "● 需导航":
                                self.append_log("⚠ Chrome 进程存在但无有效页面，请点击按钮激活")
                        # 处理完毕，不再往下走 URL 检查
                        return

                    if url_state == "recommend":
                        _browser_controller_for(self).reset_navigation_failures()
                        prev_connected = self.browser_connected
                        self.browser_connected = True
                        self.browser_page = page
                        self.browser_address = page.address
                        self.set_browser_ui("● 已连接", self.colors['success'], "已连接到 BOSS 直聘推荐牛人页面", "normal")
                        if not silent or not prev_connected:
                            self.append_log("✓ 已连接到 BOSS 直聘推荐牛人页面")
                    else:
                        if self._should_defer_browser_navigation_warning(silent):
                            return
                        prev_state = self._browser_status_text
                        self.browser_connected = False
                        self.browser_page = page
                        self.browser_address = page.address
                        self.set_browser_ui("● 需导航", self.colors['warning'], "浏览器已连接，请导航到 BOSS 直聘推荐牛人页面", "disabled")
                        if not silent or prev_state != "● 需导航":
                            self.append_log("⚠ 浏览器已连接，请导航到 BOSS 直聘推荐牛人页面")

                except ImportError:
                    raise
                except Exception as e:
                    if self._should_defer_browser_connection_failure(silent):
                        self.browser_connected = False
                        self.browser_page = None
                        self._selectors_auto_checked = False
                        self.set_browser_ui(
                            "● 重连中",
                            self.colors['warning'],
                            "页面连接短暂中断，正在自动重连...",
                            "disabled",
                        )
                        return

                    prev_state = self._browser_status_text
                    self.browser_connected = False
                    self.browser_page = None  # 清理失效的 page 对象
                    self._selectors_auto_checked = False
                    self.set_browser_ui("● 未连接", self.colors['danger'], "浏览器页面连接失败", "disabled")
                    if not silent or prev_state != "● 未连接":
                        error_text = str(e).splitlines()[0] if str(e) else type(e).__name__
                        self.append_log(f"✗ 浏览器页面连接失败：{error_text}")

                    # 手动点击时，尝试杀掉彻底挂掉的调试 Chrome 进程并重启
                    if not silent:
                        self.append_log("⚠ 正在尝试清理残留的调试 Chrome 进程...")
                        cleanup = _browser_controller_for(
                            self
                        ).terminate_debug_processes(int(port))
                        killed = cleanup.killed
                        if cleanup.error:
                            self.append_log(
                                f"清理残留进程失败：{cleanup.error}"
                            )
                        if killed:
                            time.sleep(1)
                            self.append_log("✓ 已清理残留的调试 Chrome 进程，2秒后自动重新启动...")
                            self._pending_chrome_restart = True
                        else:
                            self.append_log("⚠ Chrome 调试端口被占用但无法清理，请手动关闭所有 Chrome 窗口后重试")
                            self.set_browser_ui("● 未连接", self.colors['danger'],
                                              "请关闭所有 Chrome 窗口后点击重试", "disabled")

            except ImportError:
                self.set_browser_ui("● 错误", self.colors['danger'], "未安装 DrissionPage，请运行：pip install DrissionPage")
                self.append_log("✗ DrissionPage 未安装")
            finally:
                # 连接成功后自动检查选择器（仅首次）
                if self.browser_connected and self.browser_page and not self._selectors_auto_checked:
                    try:
                        self._auto_check_selectors()
                    except Exception:
                        pass  # 选择器检查失败不影响主流程
                if connection_lock_acquired:
                    self._browser_connection_lock.release()
                self._browser_check_running = False
                # 注意：不在此处调用 root.after()（后台线程不安全）
                # _pending_manual_check 标志保留为 True，由主线程的 auto-poll 拾取

        thread = threading.Thread(target=check)
        thread.daemon = True
        thread.start()

    def _start_browser_auto_check(self):
        """每 2 秒自动检测浏览器状态"""
        if self._browser_auto_check_id is not None:
            return  # 已在运行

        def poll():
            # 如果有被阻塞的手动检测请求，在主线程中安全触发
            if getattr(self, '_pending_manual_check', False):
                self._pending_manual_check = False
                self.check_browser_connection(silent=False)
            # 如果有待处理的 Chrome 重启请求
            elif getattr(self, '_pending_chrome_restart', False):
                self._pending_chrome_restart = False
                self.check_browser_connection(silent=False)
            else:
                self.check_browser_connection(silent=True)
            self._browser_auto_check_id = self.root.after(2000, poll)

        self._browser_auto_check_id = self.root.after(500, poll)  # 首次 0.5s 后检测，之后每 2s

    def _stop_browser_auto_check(self):
        """停止自动检测"""
        if self._browser_auto_check_id is not None:
            self.root.after_cancel(self._browser_auto_check_id)
            self._browser_auto_check_id = None

    def _reset_run_summary(self):
        """Clear the fixed run summary before a new run starts."""
        if not getattr(self, 'run_summary_text_label', None):
            return
        self.run_summary_status_label.config(
            text="运行中",
            foreground=self.colors['warning'],
        )
        self._update_run_summary_text(
            "正在扫描和筛选候选人，运行结束后显示本轮结果摘要。",
            self.colors['text_secondary'],
        )

    @staticmethod
    def _estimate_run_summary_rows(text, chars_per_row=60):
        """Estimate wrapped summary rows without depending on a mapped window."""
        return run_presenter.estimate_run_summary_rows(text, chars_per_row)

    def _update_run_summary_text(self, text, foreground):
        """Update the read-only summary and cap its viewport at ten rows."""
        widget = getattr(self, 'run_summary_text_label', None)
        if not widget:
            return
        body = str(text or "")
        row_count = self._estimate_run_summary_rows(body)
        visible_rows = max(3, min(10, row_count))
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", body)
        widget.configure(
            state="disabled",
            height=visible_rows,
            foreground=foreground,
        )
        scrollbar = getattr(self, 'run_summary_scrollbar', None)
        if scrollbar:
            if row_count > 10:
                if not scrollbar.winfo_manager():
                    scrollbar.pack(side="right", fill="y")
            else:
                scrollbar.pack_forget()

    @staticmethod
    def _format_terminal_progress_text(final_desc):
        """Keep the progress line short; full terminal details belong in the summary."""
        return run_presenter.format_terminal_progress_text(final_desc)

    @staticmethod
    def _format_terminal_log_text(final_desc):
        """Return one terminal log line without repeating the business summary."""
        return run_presenter.format_terminal_log_text(final_desc)

    def _set_run_summary(self, final_desc):
        """Show the final run summary in the fixed run-page summary area."""
        if not getattr(self, 'run_summary_text_label', None):
            return
        desc = str(final_desc or "").strip()
        status_text = "运行结果"
        status_color = self.colors['text_secondary']
        body = desc
        if desc.startswith("[完成]"):
            status_text = "已完成"
            status_color = self.colors['success']
            body = desc[len("[完成]"):].lstrip()
        elif desc.startswith("[达到轮次上限]"):
            status_text = "未确认扫描到底"
            status_color = self.colors['warning']
            body = desc[len("[达到轮次上限]"):].lstrip()
        elif desc.startswith("[可能未扫完]"):
            status_text = "未确认扫描到底"
            status_color = self.colors['warning']
            body = desc[len("[可能未扫完]"):].lstrip()
        elif desc.startswith("[扫描中断]"):
            status_text = "扫描中断"
            status_color = self.colors['warning']
            body = desc[len("[扫描中断]"):].lstrip()
        elif desc.startswith("[已停止]"):
            status_text = "已停止"
            status_color = self.colors['danger']
            body = desc[len("[已停止]"):].lstrip()
        elif desc.startswith("[出错]"):
            status_text = "运行出错"
            status_color = self.colors['danger']
            body = desc[len("[出错]"):].lstrip()

        self.run_summary_status_label.config(text=status_text, foreground=status_color)
        self._update_run_summary_text(
            body or "未取得本轮摘要。",
            self.colors['text_primary'],
        )

    @staticmethod
    def _replace_run_summary_contact_queue_count(final_desc, added_count):
        """Use the GUI contact-queue wording and count in the run summary."""
        return run_presenter.replace_run_summary_contact_queue_count(
            final_desc,
            added_count,
        )

    def update_progress(self):
        """更新进度条显示"""
        try:
            # 处理进度队列
            while True:
                progress_data = _RUN_CONTROLLER.progress_payload(
                    self.progress_queue.get_nowait()
                )
                current = progress_data.get('current', 0)
                total = progress_data.get('total', 100)
                percentage = min(100, int((current / total) * 100)) if total > 0 else 0
                self.progress_var.set(percentage)
                desc = progress_data.get('desc', '')
                raw_desc = str(desc)

                terminal_text = self._format_terminal_progress_text(raw_desc)
                if terminal_text:
                    icon = None
                    if raw_desc.startswith('[完成]'):
                        icon = self._icon_status_ok
                    elif raw_desc.startswith(('[已停止]', '[出错]')):
                        icon = self._icon_status_fail
                    self.progress_label.config(
                        image=icon or '',
                        compound='left' if icon else 'text',
                        text=f"{percentage}%  {terminal_text}",
                    )
                else:
                    self.progress_label.config(
                        image='', compound='text',
                        text=f"{percentage}%  {raw_desc}"
                    )
                if percentage >= 100 and raw_desc.startswith('['):
                    summary_desc = self._replace_run_summary_contact_queue_count(raw_desc, 0)
                    self._set_run_summary(summary_desc)
        except queue.Empty:
            pass

        # 处理岗位切换确认队列
        try:
            confirm_data = self.confirm_queue.get_nowait()
            event = confirm_data['event']
            current_idx = confirm_data['current_idx']
            total = confirm_data['total']
            next_job_name = confirm_data['next_job_name']

            result = messagebox.ask_confirmation(
                "岗位切换确认",
                headline="切换到下一个岗位后继续扫描",
                message="请在 BOSS 直聘的推荐牛人页面手动切换岗位。",
                metrics=(
                    ("扫描进度", f"{current_idx}/{total}"),
                    ("下一个岗位", next_job_name),
                ),
                notice="页面切换完成后再继续；取消将停止本轮扫描。",
                yes_label="已切换，继续扫描",
                no_label="停止扫描",
                parent=self.root,
            )
            event.result = result
            event.set()
        except queue.Empty:
            pass

        self.root.after(200, self.update_progress)


    @staticmethod
    def _parse_salary_exp(summary, structured=None):
        """Keep the historical GUI entry point as a presenter delegate."""
        return candidate_presenter.parse_salary_experience(summary, structured)


    def _capture_run_request(self):
        """Snapshot every Tk-owned run value before starting the worker thread."""
        def value(name, default):
            variable = getattr(self, name, None)
            if variable is None:
                return default
            try:
                return variable.get()
            except (AttributeError, tk.TclError):
                return default

        contact_policy = value("contact_after_scan_var", "仅保存筛选结果")
        selected_job = value("job_select_var", "全部岗位")
        if selected_job != "全部岗位":
            self._remember_run_job_selection(selected_job)

        ai_requested = bool(value("ai_eval_var", False))
        api_config = getattr(self, "api_config", {})
        read_timeout = value(
            "llm_read_timeout_var",
            api_config.get("llm_read_timeout", 120),
        )
        if ai_requested and api_config.get("llm_read_timeout") != read_timeout:
            api_config["llm_read_timeout"] = read_timeout
            try:
                with open(
                    get_api_config_path(for_write=True),
                    "w",
                    encoding="utf-8",
                ) as config_file:
                    json.dump(
                        self._sanitize_config_for_save(api_config),
                        config_file,
                        ensure_ascii=False,
                        indent=4,
                    )
            except Exception:
                pass

        api_key = None
        setup_logs = []
        if ai_requested:
            try:
                api_key = self._get_api_key_cached(
                    api_config.get("api_provider", ""),
                    api_config.get("base_url", ""),
                )
            except Exception as exc:
                setup_logs.append(f"加载 API 配置失败：{exc}，跳过 AI 评估")

        tuning = RunTuning(
            API_CANDIDATE_LIMIT_DEFAULT,
            GREET_CONTEXT_CAPTURE_LIMIT,
            DOM_SCROLL_DELAY_CENTER,
            DOM_SCROLL_DELAY_SPREAD,
            DOM_SCROLL_BATCH_MIN,
            DOM_SCROLL_BATCH_MAX,
            DOM_SCROLL_BATCH_PAUSE_CENTER,
            DOM_SCROLL_BATCH_PAUSE_SPREAD,
        )
        return _RUN_CONTROLLER.prepare_request(
            version=__version__,
            rounds=value("rounds_var", MAX_ROUNDS_DEFAULT),
            contact_policy=contact_policy,
            selected_job=selected_job,
            api_direct_enabled=value("api_direct_enabled_var", True),
            api_direct_pages=value(
                "api_direct_pages_var",
                max(1, (API_CANDIDATE_LIMIT_DEFAULT + 19) // 20),
            ),
            greet_context_capture_enabled=value(
                "greet_context_capture_enabled_var",
                True,
            ),
            greet_context_capture_limit=value(
                "greet_context_capture_limit_var",
                GREET_CONTEXT_CAPTURE_LIMIT,
            ),
            ai_eval_enabled=ai_requested,
            ai_api_config=dict(api_config),
            ai_api_key=api_key,
            llm_read_timeout=read_timeout,
            tuning=tuning,
            setup_logs=setup_logs,
        )

    def start_run(self):
        """开始运行"""
        if self.is_running:
            return
        if getattr(self, 'greet_queue_running', False) or getattr(
            self, 'greet_queue_preparing', False
        ):
            messagebox.showinfo("联系候选人", "候选人联系任务正在执行，请先暂停或等待发送完成。")
            return
        if not self._ensure_data_storage_available("开始筛选"):
            return

        guard_state = self._boss_access_cooldown_state()
        if guard_state.get("blocked"):
            remaining = max(
                1,
                int(guard_state.get("remaining_seconds", 0) + 0.999),
            )
            reason = guard_state.get("reason") or "已触发 BOSS 访问保护"
            messagebox.show_notice(
                "BOSS 访问保护",
                headline="当前仍在访问冷却期",
                message="为避免继续触发访问限制，本轮扫描暂不能开始。",
                metrics=(("剩余时间", f"约 {remaining} 秒"),),
                detail=reason,
                parent=getattr(self, "root", None),
            )
            return

        # 立即禁用按钮，防止重复点击
        self.start_btn.config(state="disabled")

        if not self.browser_connected:
            self.start_btn.config(state="normal")
            messagebox.showwarning("未连接", "请先连接到 BOSS 直聘推荐页面后再运行")
            return

        if self.browser_page is None:
            self.start_btn.config(state="normal")
            messagebox.showwarning("未连接", "请先检测/连接浏览器")
            return

        page_ready, reason = self._get_run_page_readiness()
        if not page_ready:
            self.start_btn.config(state="normal")
            messagebox.showwarning("运行前检查", reason)
            return

        if not self._confirm_advanced_run_settings():
            self.start_btn.config(state="normal")
            return

        self._pending_run_request = self._capture_run_request()
        self.is_running = True
        self.stop_event.clear()
        self._apply_lamp_status(self.status_label, "● 运行中...", self.colors['warning'])
        self.stop_btn.config(state="normal")

        # 重置进度显示
        self.progress_var.set(0)
        self.progress_label.config(text="0%", image='', compound='text')
        self._reset_run_summary()

        self.append_run_log(f"[{datetime.now().strftime('%H:%M:%S')}] ▶ 开始运行...")

        self.worker_thread = threading.Thread(target=self.run_worker)
        self.worker_thread.daemon = True
        self.worker_thread.start()

    def stop_run(self):
        """停止运行"""
        self.is_running = False
        self.stop_event.set()
        self._apply_lamp_status(self.status_label, "● 已停止", self.colors['danger'])
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.append_run_log(f"[{datetime.now().strftime('%H:%M:%S')}] ⏹ 已停止")

    def _confirm_job_name_mismatch(self, expected_job_name, actual_job_name, *, context, parent):
        """Ask on the UI thread whether two differently named jobs are the same job."""
        event = threading.Event()
        result = [False]

        def show_dialog():
            if context == "contact":
                prompt = (
                    "请确认当前 BOSS 页面就是该候选人对应的岗位推荐页。\n"
                    "确认无误后，将继续联系候选人。"
                )
            else:
                prompt = (
                    "请确认当前 BOSS 页面就是要使用上述本地配置筛选的岗位。\n"
                    "确认无误后，本轮将继续运行。"
                )
            result[0] = messagebox.ask_confirmation(
                "确认岗位对应关系",
                headline="岗位名称不同，需要人工确认",
                message=prompt,
                metrics=(
                    ("本地岗位配置", expected_job_name),
                    ("BOSS 当前岗位", actual_job_name),
                ),
                notice="只有确认两个名称对应同一岗位后才会继续。",
                parent=parent,
                yes_label="确认对应，继续",
                no_label="暂不继续",
            )
            event.set()

        self.run_on_ui(show_dialog)
        while not event.is_set():
            if self.stop_event.is_set():
                break
            event.wait(timeout=0.5)
        return result[0]

    def run_worker(self):
        """Run one prepared scan session without reading Tk state from this thread."""
        from bossmaster import run_smart_scan

        request = self._pending_run_request
        if request is None:
            self.is_running = False
            self.append_run_log("运行出错：未取得启动参数快照")
            self.run_on_ui(
                lambda: self._apply_lamp_status(
                    self.status_label,
                    "● 运行出错",
                    self.colors["danger"],
                )
            )
            return

        def confirm_callback(current_idx, total, next_job_name):
            event = threading.Event()
            event.result = False
            self.confirm_queue.put({
                "event": event,
                "current_idx": current_idx,
                "total": total,
                "next_job_name": next_job_name,
            })
            while not event.is_set():
                if self.stop_event.is_set():
                    event.result = False
                    break
                event.wait(timeout=0.5)
            return event.result

        def captcha_callback(detail):
            result = [False]
            done = threading.Event()

            def show_dialog():
                result[0] = messagebox.ask_confirmation(
                    "检测到安全验证弹窗",
                    headline="需要在浏览器中完成人工验证",
                    message="程序已暂停后续访问，请先处理 BOSS 安全验证。",
                    detail=str(detail),
                    notice="继续等待不会自动绕过验证；停止将结束当前操作。",
                    yes_label="继续等待验证",
                    no_label="停止当前操作",
                    parent=self.root,
                )
                done.set()

            self.run_on_ui(show_dialog)
            while not done.is_set():
                if self.stop_event.is_set():
                    result[0] = False
                    done.set()
                    break
                done.wait(timeout=0.5)
            return result[0]

        def notice_callback(title, message):
            self.run_on_ui(
                lambda: messagebox.showinfo(title, message, parent=self.root)
            )

        def blocking_notice_callback(title, message):
            done = threading.Event()

            def show_dialog():
                messagebox.showinfo(title, message, parent=self.root)
                done.set()

            self.run_on_ui(show_dialog)
            while not done.is_set():
                if self.stop_event.is_set():
                    done.set()
                    break
                done.wait(timeout=0.5)

        def job_match_callback(expected_job_name, actual_job_name):
            return self._confirm_job_name_mismatch(
                expected_job_name,
                actual_job_name,
                context="run",
                parent=self.root,
            )

        def job_config_callback(text, has_error):
            if not self._should_prompt_run_job_config(text, has_error):
                self.append_run_log(
                    "岗位配置未变化，沿用本次启动中已确认的体检提醒"
                )
                return True
            event = threading.Event()
            result = [False]

            def show_dialog():
                result[0] = self._show_job_config_diagnostics_dialog(
                    text,
                    has_error=has_error,
                    context="run",
                )
                event.set()

            self.run_on_ui(show_dialog)
            while not event.is_set():
                if self.stop_event.is_set():
                    break
                event.wait(timeout=0.5)
            self._remember_run_job_config_warning(text, has_error, result[0])
            return result[0]

        outcome = _RUN_CONTROLLER.execute(
            request,
            scan=run_smart_scan,
            log=self.append_run_log,
            settings_sink=self._append_run_settings_snapshot,
            progress_sink=self.progress_queue.put,
            stop_event=self.stop_event,
            existing_page=self.browser_page,
            confirm_callback=confirm_callback,
            captcha_callback=captcha_callback,
            notice_callback=notice_callback,
            blocking_notice_callback=blocking_notice_callback,
            job_match_callback=job_match_callback,
            job_config_callback=job_config_callback,
        )
        terminal = _RUN_CONTROLLER.terminal_event(
            outcome,
            request.contact_policy,
        )
        self.is_running = False
        self._pending_run_request = None
        self.append_run_log(
            f"[{datetime.now().strftime('%H:%M:%S')}] {terminal.terminal_log}"
        )
        self.run_on_ui(
            lambda: self._apply_run_terminal_event(terminal, outcome, request)
        )

    def _apply_run_terminal_event(self, terminal, outcome, request):
        """Render one plain terminal event on the Tk main thread."""
        self._apply_lamp_status(
            self.status_label,
            terminal.status_text,
            self.colors[terminal.status_tone],
        )
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.progress_var.set(100)
        self.progress_label.config(
            text=f"100%  {terminal.progress_text}",
            image="",
            compound="text",
        )
        summary_desc = self._replace_run_summary_contact_queue_count(
            terminal.final_desc,
            0,
        )
        self._set_run_summary(summary_desc)
        self.root.after(100, self.refresh_results)
        if terminal.should_build_contact_list:
            added_count = self._add_scan_candidates_to_contact_queue(
                outcome.scanned_candidates,
                request.contact_policy,
            )
            self._set_run_summary(
                self._replace_run_summary_contact_queue_count(
                    terminal.final_desc,
                    added_count,
                )
            )
    def on_closing(self):
        """窗口关闭处理 - 安全等待工作线程结束"""
        active_scan = self.is_running
        active_contact = self.greet_queue_running or getattr(
            self, 'greet_queue_preparing', False
        )
        if active_scan or active_contact:
            operations = []
            if active_scan:
                operations.append("候选人扫描")
            if active_contact:
                operations.append("候选人联系")
            if not messagebox.ask_confirmation(
                "退出",
                headline=f"{'、'.join(operations)}仍在运行",
                message="退出将停止当前任务，并保留现有联系清单。",
                notice="发送中的候选人下次启动后需要人工核实。",
                yes_label="停止任务并退出",
                no_label="继续运行",
                dangerous=True,
                parent=self.root,
            ):
                return

        if (
            getattr(self, 'config_page', None) is not None
            and not self._confirm_job_form_transition()
        ):
            return

        self.is_running = False
        self.stop_event.set()
        self._persist_greet_queue()
        for thread in (
            getattr(self, 'worker_thread', None),
            getattr(self, 'greet_queue_thread', None),
        ):
            if thread and thread.is_alive():
                thread.join(timeout=5)
        self._persist_greet_queue()
        self.root.destroy()

    def on_run_job_selected(self, event=None):
        """运行页选择岗位后，提醒切换到 BOSS 对应发布职位"""
        selected = self.job_select_var.get()
        if selected and selected != "全部岗位":
            self._remember_run_job_selection(selected)
            messagebox.showinfo(
                "提示",
                f"请在 BOSS 直聘「推荐牛人」页面，切换到「{selected}」职位后再开始运行。",
                parent=self.root,
            )

    def refresh_results(self, force=False):
        """刷新结果 - 增强版：支持表头排序、颜色标记和岗位+日期过滤"""
        if not hasattr(self, 'result_tree') or self.result_tree is None:
            return

        current_job = self.result_job_var.get() if hasattr(self, 'result_job_var') else ""
        current_dates = self._get_result_date_filter() if hasattr(self, 'result_date_start_entry') else (None, None)
        show_blacklist = self.result_show_blacklist_var.get() if hasattr(self, 'result_show_blacklist_var') else False
        result_view = self.result_view_var.get() if hasattr(self, 'result_view_var') else "全部记录"
        query = ResultQuery(
            selected_job=current_job,
            date_start=current_dates[0],
            date_end=current_dates[1],
            show_blacklist=show_blacklist,
            result_view=result_view,
            evaluating_ids=frozenset(getattr(self, '_ai_evaluating_ids', set())),
            evaluation_results=getattr(self, '_ai_eval_results', {}) or {},
        )
        cache_key = result_cache_key(CANDIDATES_PATH, query)
        previous_key = getattr(self, '_result_controller_cache_key', None)
        if previous_key is None:
            previous_key = (
                getattr(self, '_result_tree_fingerprint', None),
                getattr(self, '_result_last_job', None),
                *(getattr(self, '_result_last_dates', (None, None)) or (None, None)),
                getattr(self, '_result_last_show_blacklist', False),
                getattr(self, '_result_last_view', result_view),
            )
        if not force and cache_key == previous_key:
            return
        self._result_controller_cache_key = cache_key
        self._result_tree_fingerprint = cache_key[0]
        self._result_last_job = current_job
        self._result_last_dates = current_dates
        self._result_last_show_blacklist = show_blacklist
        self._result_last_view = result_view

        try:
            controller = getattr(self, '_result_controller', None)
            if controller is None:
                controller = ResultController(load_candidates_all)
                self._result_controller = controller
            state = controller.load(CANDIDATES_PATH, query)
            self.all_candidates = list(state.all_candidates)
            self.result_tree_data = list(state.view_candidates)
            for geek_id in state.expired_evaluation_ids:
                getattr(self, '_ai_eval_results', {}).pop(geek_id, None)

            metrics = state.metrics
            if hasattr(self, 'result_stats_vars'):
                self.result_stats_vars['strong'].set(str(metrics.strong))
                self.result_stats_vars['recommended'].set(str(metrics.recommended))
                self.result_stats_vars['pending'].set(str(metrics.pending))
                self.result_stats_vars['greeted'].set(str(metrics.greeted))
            if hasattr(self, 'result_stats_greeted'):
                self.result_stats_greeted['strong'].set(f"{metrics.strong_greeted} 已打招呼")
                self.result_stats_greeted['recommended'].set(f"{metrics.recommended_greeted} 已打招呼")
                self.result_stats_greeted['pending'].set(f"{metrics.pending_greeted} 已打招呼")
                self.result_stats_greeted['greeted'].set("通过筛选中")

            cached_items = getattr(self, '_tree_original_order', None)
            tree_items = list(cached_items) if cached_items is not None else list(self.result_tree.get_children())
            for item in tree_items:
                if self.result_tree.exists(item):
                    self.result_tree.delete(item)
            self._tree_original_order = None
            self._item_to_candidate = {}

            self.result_tree.tag_configure('strong_recommend', background=self.colors['bg_tree_tag_high'])
            self.result_tree.tag_configure('recommend', background=self.colors['bg_tree_tag_mid'])
            self.result_tree.tag_configure('pending', background=self.colors['bg_tree_tag_low'])
            self.result_tree.tag_configure('blacklisted', background=self.colors['bg_tree_tag_low'], foreground=self.colors.get('danger_text', ui_theme.DANGER_TEXT))
            self.result_tree.tag_configure('rejected', background=self.colors['bg_tree_tag_low'], foreground=self.colors.get('text_muted', ui_theme.TEXT_MUTED))
            for row in state.rows:
                item_id = self.result_tree.insert(
                    "", "end", values=row.values, tags=(row.tag,)
                )
                self._item_to_candidate[item_id] = row.candidate

            if hasattr(self, 'result_count_var'):
                self.result_count_var.set(
                    f"{state.visible_count} / 共 {state.total_count} 人"
                )
            self._toggle_result_empty_state(state.visible_count == 0)
            self._tree_original_order = None
            self._update_result_review_button_state()
        except Exception as e:
            self.append_log(f"加载结果失败：{e}")

        # 绑定表头排序（只绑定一次）
        if not hasattr(self, '_sort_bound'):
            self._bind_treeview_sorting()
            self._bind_treeview_context_menu()
            self._sort_bound = True

    def _refresh_results_and_reset_sort(self) -> None:
        """手动刷新结果，并恢复默认的候选人排序。"""
        self._sort_col = None
        self._sort_reverse = False
        self._tree_original_order = None
        self.refresh_results(force=True)
        if hasattr(self, '_column_headers'):
            self._update_sort_indicators()

        search_query = ""
        if (
            hasattr(self, 'result_search_var')
            and not getattr(self, '_result_search_placeholder_active', False)
        ):
            search_query = self.result_search_var.get().strip()
        if search_query:
            self._filter_result_tree()

    def _load_candidates_for_state_diagnostics(self):
        """Load candidates for result-page state diagnostics using the job filter only."""
        if not CANDIDATES_PATH.exists():
            return [], "全部岗位"
        candidates = load_candidates_all(CANDIDATES_PATH)
        selected_job = self.result_job_var.get() if hasattr(self, 'result_job_var') else "全部岗位"
        if selected_job != "全部岗位":
            normalized_job = normalize_job_name(selected_job)
            candidates = [
                c for c in candidates
                if normalize_job_name(c.get('job_name')) == normalized_job
            ]
        return candidates, selected_job

    def show_candidate_state_diagnostics(self):
        """Show read-only consistency diagnostics for persisted candidate states."""
        try:
            candidates, scope = self._load_candidates_for_state_diagnostics()
        except Exception as exc:
            messagebox.showerror("状态体检", f"读取候选人数据失败：{exc}", parent=self.root)
            return
        if not candidates:
            self.feedback_support.show_inline_banner(self.result_page, "info", f"{scope} 没有候选人数据可检查。")
            return

        issues = diagnose_candidate_states(candidates)
        summary_text = summarize_candidate_state_diagnostics(candidates, issues=issues)
        self._show_candidate_state_diagnostics_dialog(scope, candidates, issues, summary_text)

    def show_daily_candidate_actions(self):
        """Show a read-only daily action queue for candidate follow-up work."""
        try:
            candidates, scope = self._load_candidates_for_daily_actions()
        except Exception as exc:
            messagebox.showerror("今日待办", f"读取候选人数据失败：{exc}", parent=self.root)
            return
        if not candidates:
            self.feedback_support.show_inline_banner(self.result_page, "info", f"{scope} 没有候选人数据可处理。")
            return
        items = build_daily_candidate_actions(candidates)
        if not items:
            self.feedback_support.show_inline_banner(self.result_page, "info", f"{scope} 暂无需要优先处理的候选人。")
            return
        self._show_daily_candidate_actions_dialog(scope, items)

    def _load_candidates_for_daily_actions(self):
        """Load actionable candidates using the result page's job and date filters."""
        candidates, scope = self._load_candidates_for_state_diagnostics()
        candidates = [candidate for candidate in candidates if not candidate.get('blacklisted')]
        if not hasattr(self, 'result_date_start_entry'):
            return candidates, scope

        date_start, date_end = self._get_result_date_filter()
        if date_start or date_end:
            def _in_date_range(candidate):
                timestamp = candidate.get('first_seen_at') or candidate.get('batch_timestamp', '')
                if not timestamp or len(timestamp) < 8:
                    return False
                candidate_date = timestamp[:8]
                if date_start and candidate_date < date_start:
                    return False
                if date_end and candidate_date > date_end:
                    return False
                return True

            candidates = [candidate for candidate in candidates if _in_date_range(candidate)]
            date_scope = self.result_time_range_var.get() if hasattr(self, 'result_time_range_var') else "当前日期范围"
            scope = f"{scope} / {date_scope}"
        return candidates, scope





    def _show_daily_candidate_actions_dialog(self, scope, items):
        """Show the daily candidate action queue through its dedicated Tk module."""
        def load_actions():
            refreshed_candidates, _scope = self._load_candidates_for_daily_actions()
            return build_daily_candidate_actions(refreshed_candidates)

        def export_report(parent):
            _export_daily_candidate_actions_report(items, parent)

        return gui_candidate_actions.show_daily_candidate_actions_dialog(
            self,
            scope,
            items,
            load_actions=load_actions,
            export_report=export_report,
            ui_config=UI_CONFIG,
        )

    def _show_candidate_state_diagnostics_dialog(self, scope, candidates, issues, summary_text):
        """Show the candidate-state workbench through the dedicated Tk dialog module."""
        def load_diagnostics():
            refreshed_candidates, _scope = self._load_candidates_for_state_diagnostics()
            refreshed_issues = diagnose_candidate_states(refreshed_candidates)
            return refreshed_candidates, refreshed_issues

        def export_report(parent):
            _export_candidate_state_diagnostics_report(summary_text, parent)

        return gui_candidate_diagnostics.show_candidate_state_diagnostics_dialog(
            self,
            scope,
            candidates,
            issues,
            load_diagnostics=load_diagnostics,
            export_report=export_report,
            ui_config=UI_CONFIG,
        )

    @staticmethod
    def _clip_table_text(text, limit):
        return candidate_diagnostics_presenter.clip_table_text(text, limit)

    def _format_daily_action_key_info(self, item):
        """Return the candidate-specific fact for one daily-action row."""
        reason = " ".join(str(item.reason or "").split())
        reason = re.sub(r"[；;]\s*下次处理时间[:：].*$", "", reason).strip()
        action = " ".join(str(item.action or "").split())

        if item.group == "发送结果待核实":
            return self._clip_table_text(reason or "发送结果尚未确认", 28)
        if item.group == "已回复待推进":
            return "尚未记录后续处理结果"
        if item.group == "待完成简历评估":
            return "已导入简历，尚未重新评分"
        if item.group == "待打招呼":
            if "重新扫描" in action:
                return "缺少联系条件，需重新扫描岗位"
            return self._clip_table_text(reason or "尚未联系", 28)
        if item.group == "已打招呼待跟进" and not item.due_at:
            return "尚未安排下次跟进"
        return self._clip_table_text(reason, 28)

    @staticmethod
    def _format_daily_action_due(item):
        """Format an explicit due state instead of an ambiguous dash."""
        if item.due_at:
            return format_followup_due_at(item.due_at)
        return {
            "立即处理": "立即",
            "已逾期": "已逾期",
            "今天": "今天",
            "待安排": "未安排",
            "以后": "以后",
        }.get(item.timing_group, "未安排")

    def _format_state_issue_key_info(self, issue, candidate):
        """Return only the candidate-specific fact that distinguishes one issue row."""
        return candidate_diagnostics_presenter.format_state_issue_key_info(
            issue,
            candidate,
        )

    def _show_candidate_workflow_context_menu(
        self,
        parent,
        candidate,
        x_root,
        y_root,
        *,
        refresh_fn,
        primary_action=None,
    ):
        """Show candidate actions inside diagnostics/action dialogs."""
        if not candidate:
            return

        def refresh_later():
            if refresh_fn:
                parent.after(150, refresh_fn)

        decision = derive_candidate_decision(candidate)
        needs_review = decision.review_status == "pending"
        can_confirm_review = needs_review and (
            candidate.get("manual_review_required")
            or candidate.get("qualification_status") == "manual_review"
        )
        active_queue_item = self._greet_queue_item_for_candidate(
            candidate,
            active_only=True,
        )
        followup_status = str(
            candidate.get("followup_status")
            or ("已打招呼" if candidate.get("greet_sent") else "未沟通")
        )
        state = gui_candidate_menus.WorkflowCandidateMenuState(
            primary_action=primary_action,
            needs_review=needs_review,
            can_confirm_review=can_confirm_review,
            needs_send_verification=bool(
                candidate.get("greet_confirmation_pending")
            ),
            has_active_queue_item=active_queue_item is not None,
            can_queue=(
                active_queue_item is None
                and not candidate_greet_skip_reason(candidate)
            ),
            can_approve_queue=candidate_can_manual_approve_contact(candidate),
            greet_sent=bool(candidate.get("greet_sent")),
            followup_status=followup_status,
            blacklisted=bool(candidate.get("blacklisted")),
        )
        callbacks = gui_candidate_menus.WorkflowCandidateMenuCallbacks(
            view_detail=lambda: self._open_candidate_review_workbench(candidate),
            confirm_review=lambda: self._confirm_manual_review(
                None,
                candidate=candidate,
                parent=parent,
                on_saved=refresh_later,
            ),
            reject_review=lambda: self._confirm_review_rejection(
                None,
                candidate=candidate,
                parent=parent,
                on_saved=refresh_later,
            ),
            add_queue=lambda: (
                self._add_candidates_to_greet_queue(
                    [candidate],
                    parent=parent,
                ),
                refresh_later(),
            ),
            focus_queue=lambda: self._focus_candidate_in_greet_queue(candidate),
            approve_queue=lambda: self._approve_candidate_contact_and_queue(
                candidate,
                parent=parent,
                on_saved=refresh_later,
            ),
            verify_sent=lambda: self._focus_candidate_in_greet_queue(candidate),
            import_resume=lambda: (
                self._import_resume(
                    None,
                    candidate=candidate,
                    parent=parent,
                ),
                refresh_later(),
            ),
            update_followup=lambda: self._mark_candidate_followup(
                None,
                candidate=candidate,
                parent=parent,
                on_saved=refresh_later,
            ),
            mark_replied=lambda: self._quick_update_candidate_followup(
                candidate,
                "已回复",
                parent,
                refresh_later,
            ),
            advance_to_interview=lambda: self._quick_update_candidate_followup(
                candidate,
                "待约面",
                parent,
                refresh_later,
            ),
            follow_up_tomorrow=lambda: self._quick_update_candidate_followup(
                candidate,
                followup_status,
                parent,
                refresh_later,
                days=1,
            ),
            mark_feedback=lambda: self._mark_candidate_feedback(
                None,
                candidate=candidate,
                parent=parent,
                on_saved=refresh_later,
            ),
            add_blacklist=lambda: self._blacklist_candidate(
                None,
                candidate=candidate,
                parent=parent,
                on_saved=refresh_later,
            ),
            remove_blacklist=lambda: self._unblacklist_candidate(
                None,
                candidate=candidate,
                parent=parent,
                on_saved=refresh_later,
            ),
        )
        gui_candidate_menus.show_workflow_candidate_menu(
            self,
            parent,
            x_root,
            y_root,
            font_family=FONT_FAMILY,
            state=state,
            callbacks=callbacks,
        )

    def _bind_treeview_sorting(self):
        """绑定 Treeview 表头排序功能"""
        # 设置中文表头显示
        column_headers = {
            "name": "姓名",
            "gender": "性别",
            "exp": "工作年限",
            "salary": "薪资",
            "score": "匹配分",
            "level": "推荐指数",
            "ai_eval": "AI评估",
            "status": "状态",
            "skills": "技能匹配",
            "education": "学历",
            "age": "年龄",
            "job_status": "求职状态",
            "school": "毕业学校",
            "company": "最近公司",
        }
        self._column_headers = column_headers
        self._sort_col = None
        self._sort_reverse = False
        columns = self.result_tree['columns']
        for col in columns:
            # 为每个表头添加点击事件，使用中文显示
            header_text = column_headers.get(col, col)
            self.result_tree.heading(col, text=header_text, command=lambda c=col: self._sort_treeview(c))

    def _update_sort_indicators(self):
        """根据当前排序状态刷新表头 ▲▼ 指示"""
        for col in self.result_tree['columns']:
            base = self._column_headers.get(col, col)
            if col == self._sort_col:
                base += ' ▼' if self._sort_reverse else ' ▲'
            self.result_tree.heading(col, text=base)

    def _sort_treeview(self, col):
        """按指定列排序 Treeview（重复点击同一列切换升降序）"""
        try:
            # 同一列再次点击 → 反转方向；新列 → 升序（匹配分列默认降序）
            if self._sort_col == col:
                self._sort_reverse = not self._sort_reverse
            else:
                self._sort_col = col
                self._sort_reverse = (col == 'score')

            parsed_items = []
            for item in self.result_tree.get_children():
                value = self.result_tree.set(item, col)
                valid, sort_value = self._result_tree_sort_value(col, value)
                parsed_items.append((valid, sort_value, item))

            # 空值和无法解析的数值始终放在末尾，避免降序时跑到最前面。
            valid_items = [entry for entry in parsed_items if entry[0]]
            invalid_items = [entry for entry in parsed_items if not entry[0]]
            valid_items.sort(key=lambda entry: entry[1], reverse=self._sort_reverse)
            items = valid_items + invalid_items

            # 移动项
            for index, (_valid, _value, item) in enumerate(items):
                self.result_tree.move(item, '', index)

            self._update_sort_indicators()

        except Exception:
            logger.exception("结果表按%s列排序失败", col)

    @staticmethod
    def _result_tree_sort_value(col: str, value) -> tuple[bool, float | str]:
        """Keep the historical GUI entry point as a controller delegate."""
        return result_sort_value(col, value)

    def _filter_result_tree(self):
        """根据搜索框内容实时过滤 Treeview 行（不匹配的行隐藏）。

        搜索范围：姓名、性别、匹配分、推荐指数、状态。
        数字语义：``60`` 表示匹配分 ≥ 60；支持 ``>=60``、``>60``、``=60`` 显式比较。
        匹配项高亮并按优先级排序（完全匹配姓名 > 部分匹配 > 分数 > 等级 > 状态），
        清空搜索时恢复全部行和原始排序。
        """
        query = (
            ""
            if getattr(self, '_result_search_placeholder_active', False)
            else self.result_search_var.get().strip().lower()
        )
        visible_items = list(self.result_tree.get_children())

        # 保存原始顺序（首次过滤时）
        original_order = getattr(self, '_tree_original_order', None)
        if query and original_order is None:
            original_order = visible_items
            self._tree_original_order = list(original_order)
        all_items = list(
            original_order if original_order is not None else visible_items
        )

        # 构建 item_id → candidate 映射（插入时建立，不受排序影响）
        item_map = getattr(self, '_item_to_candidate', {}) or {}

        if not query:
            # 清空搜索：恢复全部行、原始排序，清除高亮
            for item_id in all_items:
                tags = list(self.result_tree.item(item_id, 'tags') or ())
                if 'search_match' in tags:
                    tags.remove('search_match')
                    self.result_tree.item(item_id, tags=tuple(tags))
            for i, item_id in enumerate(all_items):
                if self.result_tree.exists(item_id):
                    self.result_tree.reattach(item_id, '', i)
            self._tree_original_order = None
            # 恢复默认计数文案
            if hasattr(self, 'result_count_var'):
                total = len(getattr(self, 'result_tree_data', []) or [])
                self.result_count_var.set(f"{len(all_items)} / 共 {total} 人")
            return

        _priority = {
            'exact_name': 0, 'partial_name': 1, 'gender': 2,
            'score': 3, 'level': 4, 'status': 5,
        }
        matched_with_type: list[tuple[str, str]] = []
        for item_id in all_items:
            mt = candidate_query_match(item_map.get(item_id, {}), query)
            if mt:
                matched_with_type.append((item_id, mt))

        # 匹配项按优先级排序：完全匹配姓名 > 部分匹配姓名 > 分数 > 等级 > 状态
        matched_with_type.sort(key=lambda x: _priority.get(x[1], 99))

        # 清除旧高亮 tag
        for item_id in all_items:
            tags = list(self.result_tree.item(item_id, 'tags') or ())
            if 'search_match' in tags:
                tags.remove('search_match')
                self.result_tree.item(item_id, tags=tuple(tags))

        # 匹配项：深青加粗高亮 tag（bold 继承 self.font_table 基础字号，避免行高不一致）
        self.result_tree.tag_configure('search_match', foreground=self.colors['primary_dark'], font=(*self.font_table, 'bold'))
        for item_id, _ in matched_with_type:
            tags = list(self.result_tree.item(item_id, 'tags') or ())
            if 'search_match' not in tags:
                tags.append('search_match')
            self.result_tree.item(item_id, tags=tuple(tags))

        # detach 全部 → 仅 reattach 匹配项（真筛选：不匹配的行隐藏）
        for item_id in visible_items:
            self.result_tree.detach(item_id)
        for item_id, _ in matched_with_type:
            self.result_tree.reattach(item_id, '', 'end')

        # 更新计数提示并滚动到第一个匹配项
        total = len(all_items)
        shown = len(matched_with_type)
        if hasattr(self, 'result_count_var'):
            self.result_count_var.set(f"搜索命中 {shown} / {total} 人")
        if matched_with_type:
            self.result_tree.see(matched_with_type[0][0])
            self.result_tree.selection_set(matched_with_type[0][0])

    def _bind_treeview_context_menu(self):
        """绑定 Treeview 右键菜单和双击"""
        self.result_tree.bind('<Button-3>', self._show_context_menu)
        self.result_tree.bind('<Double-Button-1>', self._on_tree_double_click)
        self.result_tree.bind('<Control-a>', self._select_all_result_rows, add='+')
        self.result_tree.bind('<Control-A>', self._select_all_result_rows, add='+')
        # 状态、求职状态及经历列 tooltip（截断时显示完整内容）
        self._tooltip = None
        self._tooltip_after_id = None
        self._tooltip_item = None
        self.result_tree.bind('<Motion>', self._on_tree_motion)
        self.result_tree.bind('<Leave>', self.feedback_support.hide_tooltip)

    def _select_all_result_rows(self, _event=None):
        """Select every candidate currently visible in the result table."""
        rows = self.result_tree.get_children("")
        if rows:
            self.result_tree.selection_set(rows)
            self.result_tree.focus(rows[0])
            self.result_tree.see(rows[0])
        return "break"

    def _on_tree_motion(self, event):
        """Treeview 鼠标移动：被截断的状态和经历信息显示完整 tooltip。"""
        item = self.result_tree.identify_row(event.y)
        column_id = self.result_tree.identify_column(event.x)
        if not item or not column_id:
            self.feedback_support.hide_tooltip()
            return

        try:
            display_columns = tuple(self.result_tree.cget("displaycolumns"))
            column_index = int(column_id[1:]) - 1
            column_name = display_columns[column_index]
        except (IndexError, TypeError, ValueError):
            self.feedback_support.hide_tooltip()
            return

        cand = self._item_to_candidate.get(item)
        full = ''
        if cand and column_name == 'status':
            full = cand.get('_full_status', '')
            display = cand.get('_display_status', '')
            show_tooltip = bool(full and full != display)
            if full and not show_tooltip:
                try:
                    cell_bbox = self.result_tree.bbox(item, column_id)
                    show_tooltip = bool(
                        cell_bbox
                        and self._result_tree_font.measure(display or full)
                        > max(0, cell_bbox[2] - 12)
                    )
                except (tk.TclError, TypeError, ValueError):
                    show_tooltip = False
        elif cand and column_name in ('job_status', 'school', 'company'):
            extra = cand.get('_extra_fields') or ('', '', '', '', '')
            extra_index = {'job_status': 2, 'school': 3, 'company': 4}[column_name]
            full = str(extra[extra_index] or '')
            try:
                cell_bbox = self.result_tree.bbox(item, column_id)
                show_tooltip = bool(
                    full
                    and cell_bbox
                    and self._result_tree_font.measure(full) > max(0, cell_bbox[2] - 12)
                )
            except (tk.TclError, TypeError, ValueError):
                show_tooltip = False
        else:
            show_tooltip = False

        if not full or not show_tooltip:
            self.feedback_support.hide_tooltip()
            return

        tooltip_key = (item, column_name)
        if tooltip_key == self._tooltip_item and self._tooltip and self._tooltip.winfo_exists():
            return
        self._tooltip_item = tooltip_key
        if self._tooltip_after_id:
            self.root.after_cancel(self._tooltip_after_id)
        x = self.root.winfo_pointerx() + 15
        y = self.root.winfo_pointery() + 10
        self._tooltip_after_id = self.root.after(
            300, lambda: self.feedback_support.show_tooltip(full, x, y, tooltip_key)
        )

    def _toggle_result_empty_state(self, show):
        """按可见候选人数切换结果页空态引导层。"""
        frame = getattr(self, 'result_empty_state', None)
        if frame is None:
            return
        try:
            if show:
                frame.place(relx=0, rely=0, relwidth=1, relheight=1)
                frame.lift()
            else:
                frame.place_forget()
        except tk.TclError:
            pass

    @staticmethod
    def _find_candidate_in_detail_tree(tree, item, filtered_ref):
        """Resolve a detail-tree row without relying on non-unique display values."""
        candidate = (getattr(tree, '_candidate_map', {}) or {}).get(item)
        if candidate is not None:
            return candidate
        values = tree.item(item, 'values')
        if not values:
            return None
        try:
            columns = tuple(tree["columns"])
            score_index = columns.index("score")
        except (AttributeError, KeyError, TypeError, ValueError):
            score_index = 5
        if len(values) <= score_index:
            return None
        matches = [
            candidate for candidate in filtered_ref[0]
            if candidate.get('name') == values[0]
            and str(candidate.get('match_score', '')) == str(values[score_index])
        ]
        return matches[0] if len(matches) == 1 else None

    def _bind_detail_tree_tooltip(self, tree, filtered_ref):
        """为明细窗口 Treeview 绑定状态列 tooltip（截断时显示完整状态）。"""
        _state = {'key': None, 'after_id': None}

        def _cancel_pending():
            """仅取消待执行的延迟，不清除已显示的 tooltip。"""
            if _state['after_id']:
                tree.after_cancel(_state['after_id'])
                _state['after_id'] = None

        def _hide_all():
            """取消延迟 + 隐藏已显示的 tooltip + 重置状态。"""
            _cancel_pending()
            self.feedback_support.hide_tooltip()
            _state['key'] = None

        def on_motion(event):
            item = tree.identify_row(event.y)
            column_id = tree.identify_column(event.x)
            if not item or not column_id:
                _hide_all()
                return
            try:
                display_columns = tuple(tree["columns"])
                column_index = int(column_id[1:]) - 1
                column_name = display_columns[column_index]
            except (IndexError, TypeError, ValueError):
                _hide_all()
                return
            if column_name != 'status':
                _hide_all()
                return
            candidate = self._find_candidate_in_detail_tree(tree, item, filtered_ref)
            full = candidate.get('_full_status', '') if candidate else ''
            display = candidate.get('_display_status', '') if candidate else ''
            if not full or full == display:
                _hide_all()
                return
            # 同一行同一列：已显示就保持，有待显示就保持
            tooltip_key = (item, column_name)
            if tooltip_key == _state['key']:
                tip = getattr(self, '_tooltip', None)
                if (tip and tip.winfo_exists()) or _state['after_id']:
                    return
            # 新目标：隐藏旧的，调度新的
            _hide_all()
            _state['key'] = tooltip_key
            x = tree.winfo_pointerx() + 15
            y = tree.winfo_pointery() + 10
            _parent = tree.winfo_toplevel()
            _state['after_id'] = tree.after(
                300, lambda: self.feedback_support.show_tooltip(full, x, y, tooltip_key, parent=_parent)
            )

        tree.bind('<Motion>', on_motion)
        tree.bind('<Leave>', lambda e: _hide_all())

    def _update_result_review_button_state(self, _event=None):
        """结果表有候选人时启用查看与复核入口。"""
        button = getattr(self, 'result_review_button', None)
        tree = getattr(self, 'result_tree', None)
        if button is None or tree is None:
            return
        try:
            has_candidates = bool(tree.selection() or tree.get_children())
        except (AttributeError, tk.TclError):
            has_candidates = bool(tree.selection())
        button.configure(state='normal' if has_candidates else 'disabled')

    def _open_selected_candidate_review(self):
        """打开选中候选人；未选择时从当前结果第一位开始。"""
        selection = self.result_tree.selection()
        if selection:
            item = selection[0]
        else:
            rows = self.result_tree.get_children()
            if not rows:
                return
            item = rows[0]
            self.result_tree.selection_set(item)
            self.result_tree.focus(item)
            self.result_tree.see(item)
        self._show_candidate_detail(item)

    def _on_tree_double_click(self, event):
        """双击候选人打开查看与复核工作台。"""
        item = self.result_tree.identify_row(event.y)
        if item:
            self._show_candidate_detail(item)

    def _show_context_menu(self, event):
        """显示右键菜单"""
        item = self.result_tree.identify_row(event.y)
        if not item:
            return
        if item not in self.result_tree.selection():
            self.result_tree.selection_set(item)

        selection = self.result_tree.selection()
        if len(selection) > 1:
            selected_candidates = []
            for selected_item in selection:
                candidate = self._find_candidate_by_tree_item(selected_item)
                if candidate:
                    selected_candidates.append(candidate)
            state = gui_candidate_menus.CandidateBatchMenuState(
                ai_label=self._batch_ai_eval_menu_label(selected_candidates),
                can_confirm_review=any(
                    candidate.get("manual_review_required")
                    for candidate in selected_candidates
                ),
            )
            callbacks = gui_candidate_menus.CandidateBatchMenuCallbacks(
                add_queue=lambda: self._add_candidates_to_greet_queue(
                    self._collect_selected_candidates_for_queue(
                        selection,
                        [self.result_tree_data],
                        self.result_tree,
                    ),
                    parent=self.root,
                ),
                evaluate_ai=lambda: self._ai_eval_selected_candidates(
                    selected_candidates
                ),
                confirm_review=lambda: self._batch_confirm_manual_review(
                    selected_candidates,
                    parent=self.root,
                ),
                remove_selected=self._remove_selected_candidates,
                export_selected=self._export_selected,
            )
            gui_candidate_menus.show_candidate_batch_menu(
                self,
                self.root,
                event.x_root,
                event.y_root,
                font_family=FONT_FAMILY,
                state=state,
                callbacks=callbacks,
            )
            return

        candidate = self._find_candidate_by_tree_item(item)
        if not candidate:
            return
        self._build_candidate_context_menu(
            parent=self.root,
            tree=self.result_tree,
            tree_item=item,
            candidate=candidate,
            show_detail_fn=lambda: self._show_candidate_detail(item),
            remove_fn=lambda: self._remove_candidate(item),
            x_root=event.x_root,
            y_root=event.y_root,
        )

    def _build_candidate_context_menu(
        self,
        parent,
        tree,
        tree_item,
        candidate,
        show_detail_fn,
        remove_fn,
        x_root,
        y_root,
    ):
        """构建候选人右键菜单（筛选结果页和详细列表窗口共用）。"""
        decision = derive_candidate_decision(candidate)
        can_confirm_review = (
            decision.review_status == "pending"
            and (
                candidate.get("manual_review_required")
                or candidate.get("qualification_status") == "manual_review"
            )
        )
        active_queue_item = self._greet_queue_item_for_candidate(
            candidate,
            active_only=True,
        )
        if active_queue_item is not None:
            queue_action = "focus"
        elif not candidate_greet_skip_reason(candidate):
            queue_action = "add"
        elif candidate_can_manual_approve_contact(candidate):
            queue_action = "approve"
        else:
            queue_action = "none"

        state = gui_candidate_menus.CandidateContextMenuState(
            has_ai_evaluation=_candidate_has_ai_eval(candidate),
            has_resume_adjustment=(
                candidate.get("resume_eval_adjustment") is not None
            ),
            needs_review=decision.review_status == "pending",
            can_confirm_review=can_confirm_review,
            queue_action=queue_action,
            blacklisted=bool(candidate.get("blacklisted")),
        )
        callbacks = gui_candidate_menus.CandidateContextMenuCallbacks(
            view_detail=show_detail_fn,
            evaluate_ai=lambda: self._ai_eval_selected_candidates([candidate]),
            import_resume=lambda: self._import_resume(
                None,
                candidate=candidate,
                parent=parent,
                tree=tree,
                tree_item=tree_item,
            ),
            revert_resume_evaluation=lambda: self._revert_resume_eval(
                None,
                candidate=candidate,
                parent=parent,
            ),
            confirm_review=lambda: self._confirm_manual_review(
                None,
                candidate=candidate,
                parent=parent,
            ),
            reject_review=lambda: self._confirm_review_rejection(
                None,
                candidate=candidate,
                parent=parent,
            ),
            add_queue=lambda: self._add_candidates_to_greet_queue(
                [candidate],
                parent=parent,
            ),
            focus_queue=lambda: self._focus_candidate_in_greet_queue(candidate),
            approve_queue=lambda: self._approve_candidate_contact_and_queue(
                candidate,
                parent=parent,
            ),
            update_followup=lambda: self._mark_candidate_followup(
                None,
                candidate=candidate,
                parent=parent,
            ),
            mark_feedback=lambda: self._mark_candidate_feedback(
                None,
                candidate=candidate,
                parent=parent,
            ),
            add_blacklist=lambda: self._blacklist_candidate(
                None,
                candidate=candidate,
                parent=parent,
            ),
            remove_blacklist=lambda: self._unblacklist_candidate(
                None,
                candidate=candidate,
                parent=parent,
            ),
            remove_candidate=remove_fn,
        )
        gui_candidate_menus.show_candidate_context_menu(
            self,
            parent,
            x_root,
            y_root,
            font_family=FONT_FAMILY,
            state=state,
            callbacks=callbacks,
        )

    def _find_candidate_by_tree_item(self, item):
        """按结果表选中行定位候选人记录。"""
        candidate = (getattr(self, '_item_to_candidate', {}) or {}).get(item)
        if candidate is not None:
            return candidate
        values = self.result_tree.item(item, 'values')
        if not values:
            return None
        name = values[0]
        try:
            score_index = tuple(self.result_tree['columns']).index('score')
        except (AttributeError, KeyError, TypeError, ValueError):
            score_index = 5
        if len(values) <= score_index:
            return None
        score = values[score_index]
        for c in getattr(self, 'result_tree_data', []):
            if c.get('name') == name and str(c.get('match_score', '')) == str(score):
                return c
        return None

    def _resolve_candidate(self, item=None, candidate=None):
        """统一候选人定位：优先用已解析的 dict，否则按 tree item 查找。"""
        if candidate is not None:
            return candidate
        if item is not None:
            return self._find_candidate_by_tree_item(item)
        return None

    def _extract_extra_fields(self, candidate):
        """Keep the historical GUI entry point as a presenter delegate."""
        return candidate_presenter.extract_candidate_extra_fields(candidate)

    @staticmethod
    def _candidate_gender_display(candidate):
        """Return normalized candidate gender from current or legacy records."""
        return candidate_presenter.candidate_gender_display(candidate)


    @staticmethod
    def _latest_history_value(entries, field, summary, summary_prefix):
        """按结束时间取最近一段经历的字段，缺失时从摘要对应行降级提取。"""
        return candidate_presenter.latest_history_value(
            entries,
            field,
            summary,
            summary_prefix,
        )

    def _format_candidate_status(self, candidate):
        """Keep the historical GUI entry point as a presenter delegate."""
        status = candidate_presenter.format_candidate_status(
            candidate,
            evaluating_ids=frozenset(getattr(self, '_ai_evaluating_ids', set())),
            evaluation_results=getattr(self, '_ai_eval_results', {}) or {},
        )
        candidate['_display_status'] = status.display
        candidate['_full_status'] = status.detail
        if status.expired_evaluation_id:
            getattr(self, '_ai_eval_results', {}).pop(
                status.expired_evaluation_id,
                None,
            )
        return status.display

    @staticmethod
    def _get_greet_confirmation_hint(candidate):
        """根据内部上下文状态生成面向普通用户的操作提示。"""
        if (candidate.get('greet_context') or {}).get('chat_start'):
            return (
                "已准备好该候选人的沟通信息，可直接发起打招呼，"
                "无需停留在原推荐页面。"
            )
        return (
            "程序将尝试在当前推荐页面定位该候选人并打招呼。"
            "请确认浏览器已打开该岗位的推荐牛人页面。"
        )

    def _open_blacklist_reason_dialog(self, candidate, parent, on_confirm):
        """打开加入黑名单原因弹窗。"""
        gui_candidate_state_dialogs.show_blacklist_reason_dialog(
            self,
            candidate,
            parent or self.root,
            on_confirm,
        )

    def _update_candidate_blacklist(self, geek_id, reason, timestamp=None):
        """Keep the historical GUI entry point as a controller delegate."""
        return _candidate_controller_for(self).blacklist(
            geek_id,
            reason,
            timestamp=timestamp,
        )

    def _import_resume(self, item, candidate=None, parent=None, tree=None, tree_item=None):
        """导入候选人简历文件并触发二次 AI 评估。"""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人", parent=parent or self.root)
            return

        # 1. 选择文件
        filepath = filedialog.askopenfilename(
            title=f"导入简历 — {candidate.get('name', '')}",
            filetypes=[
                ("简历文件", "*.pdf *.docx *.txt *.md *.rtf *.html"),
                ("PDF 文件", "*.pdf"),
                ("Word 文件", "*.docx"),
                ("文本文件", "*.txt *.md"),
                ("RTF 文件", "*.rtf"),
                ("HTML 文件", "*.html *.htm"),
                ("所有文件", "*.*"),
            ],
        )
        if not filepath:
            return

        # 2. 由候选人控制器解析并原子替换受管简历引用。
        try:
            import_outcome = _candidate_controller_for(self).import_resume(
                candidate,
                filepath,
                parser=parse_resume_text,
                persister=persist_candidate_resume,
            )
            resume_text = import_outcome.resume_text
        except ResumeParserDependencyError as exc:
            messagebox.show_notice(
                f"无法解析 {exc.format_name} 简历",
                headline=f"当前环境缺少 {exc.format_name} 解析组件",
                message=f"安装 {exc.package_name} 后可继续导入该文件。",
                detail=f"安装命令：pip install {exc.package_name}",
                parent=self.root,
            )
            return
        except ResumeTextReadError as exc:
            is_html = exc.format_name == "HTML"
            messagebox.show_failure(
                "读取简历",
                headline="未能读取 HTML 简历" if is_html else "未能读取简历文本",
                message="无法使用常见编码读取这个文件。",
                detail=Path(filepath).name,
                notice=(
                    "请确认文件内容完整后重试。"
                    if is_html
                    else "请确认文件是有效的纯文本文件后重试。"
                ),
                parent=parent or self.root,
            )
            return
        except UnsupportedResumeFormatError as exc:
            messagebox.show_notice(
                "无法导入简历",
                headline="不支持这种文件格式",
                message="请选择 PDF、DOCX、TXT、MD、RTF 或 HTML 文件。",
                metrics=(("当前格式", exc.extension or "无扩展名"),),
                detail=Path(filepath).name,
                parent=parent or self.root,
            )
            return
        except ResumeContentTooShortError as exc:
            messagebox.show_notice(
                "简历内容过少",
                headline="提取到的文本不足以评估",
                message="这个文件可能不是有效简历，或主要内容无法被当前解析器读取。",
                metrics=(("提取文本", f"{exc.text_length} 字"),),
                notice="可将文件转换为可复制文本的 PDF 或 DOCX 后重试。",
                parent=parent or self.root,
            )
            return
        except ResumeCopyError as exc:
            messagebox.show_failure(
                "保存简历",
                headline="简历文件未保存",
                message="无法将所选文件复制到受管简历目录。",
                detail=str(exc),
                notice="请检查磁盘空间和目录写入权限后重试。",
                parent=parent or self.root,
            )
            return
        except ResumePersistenceError as exc:
            messagebox.show_failure(
                "保存简历",
                headline="简历保存状态需要核对",
                message="候选人数据保存过程未能正常结束。",
                detail=str(exc),
                notice=(
                    "无法确认最终写入状态，新副本已保留；"
                    "请刷新后运行简历存储体检。"
                    if exc.copy_retained
                    else "候选人引用未写入，新复制的简历已回收。"
                ),
                parent=parent or self.root,
            )
            return
        except ResumeCandidateNotFoundError:
            messagebox.show_failure(
                "保存简历",
                headline="简历未关联到候选人",
                message="本地候选人记录已发生变化，本次导入没有保存。",
                notice="请刷新候选人列表后重新导入。",
                parent=parent or self.root,
            )
            return
        except Exception as e:
            messagebox.show_failure(
                "解析简历",
                headline="简历文件解析失败",
                message="没有从所选文件中提取到可用内容。",
                detail=str(e),
                notice="请检查文件是否损坏，或转换为 PDF、DOCX 后重试。",
                parent=parent or self.root,
            )
            return
        if import_outcome.cleanup.failure_count:
            self.append_log(
                "[简历导入] 新简历已保存，但旧受管副本清理失败，"
                "可运行简历存储体检重试"
            )

        # 4. 预览确认（只显示前 300 字）
        preview = resume_text[:300]
        if len(resume_text) > 300:
            preview += f"\n\n... (共 {len(resume_text)} 字)"

        confirm = messagebox.ask_confirmation(
            "简历预览",
            headline="已提取简历文本",
            message=preview,
            metrics=(("文本长度", f"{len(resume_text)} 字"),),
            notice="继续后将调用当前 AI 模型进行二次评估。",
            yes_label="开始二次评估",
            no_label="暂不评估",
            parent=parent or self.root,
        )

        if not confirm:
            self.refresh_results()
            return

        job_requirement, job_rule = self._get_job_requirement_for_candidates([candidate])
        if not job_requirement or not job_rule:
            messagebox.show_notice(
                "简历二次评估",
                headline="暂时不能开始二次评估",
                message="没有找到该候选人对应的已保存岗位规则。",
                notice="简历已保留。请先恢复或重新保存对应岗位配置。",
                parent=parent or self.root,
            )
            self.refresh_results()
            return
        hard_conditions = self._format_ai_hard_conditions(job_rule)

        # 5. 后台线程调用 LLM
        name = candidate.get('name', '')
        _parent = parent or self.root
        _tree = tree or self.result_tree
        _tree_item = tree_item if tree_item is not None else item

        # 表格状态即时反馈
        if _tree_item is not None:
            try:
                _tree.set(_tree_item, 'status', '简历评估中...')
                _tree.update_idletasks()
            except Exception:
                pass

        def _eval_worker():
            try:
                from llm_eval import evaluate_with_resume

                api_config = self.api_config
                provider_key = api_config.get('api_provider', '')
                base_url = api_config.get('base_url', '')
                api_key = self._get_api_key_cached(provider_key, base_url)

                if not api_key:
                    def _no_key():
                        if _tree_item is not None:
                            try:
                                _tree.set(_tree_item, 'status',
                                    self._format_candidate_status(candidate))
                            except Exception:
                                pass
                        messagebox.showwarning("API Key 缺失",
                            "未找到 API Key，请先在「模型配置」页配置。",
                            parent=_parent)
                    self.run_on_ui(_no_key)
                    return

                self.run_on_ui(
                    lambda: self.append_log(f"[简历评估] 正在评估 {name}...")
                )
                controller = _candidate_controller_for(self)
                result = controller.evaluate_resume(
                    candidate,
                    resume_text,
                    job_requirement,
                    api_config,
                    api_key,
                    hard_conditions,
                    evaluator=evaluate_with_resume,
                )
                if result.success:
                    controller.persist_resume_evaluation(candidate)

                def _on_done():
                    self.refresh_results()
                    self.refresh_home_stats()
                    if result.success:
                        sign = "+" if result.adjustment > 0 else ""
                        self.append_log(
                            f"[简历评估] ✓ {name}: {sign}{result.adjustment} "
                            f"→ 总分 {candidate.get('match_score', '?')}")
                        reason_text = candidate.get('resume_eval_reason', '')
                        messagebox.show_result(
                            "简历二次评估",
                            headline=f"{name} 的简历评估已完成",
                            metrics=(
                                ("调整分", f"{sign}{result.adjustment}"),
                                ("最终分", str(candidate.get('match_score', '?'))),
                            ),
                            detail=reason_text,
                            parent=_parent,
                        )
                    else:
                        self.append_log(f"[简历评估] ✗ {name}: {result.reason}")
                        messagebox.show_failure(
                            "简历二次评估",
                            headline=f"{name} 的简历评估未完成",
                            message="简历已保留，候选人分数没有更新。",
                            detail=result.reason,
                            notice="请检查模型配置或网络连接后重试。",
                            parent=_parent,
                        )

                self.run_on_ui(_on_done)

            except Exception as exc:
                def _on_error(error=exc):
                    self.append_log(f"[简历评估] ✗ {name} 异常：{error}")
                    if _tree_item is not None:
                        try:
                            _tree.set(_tree_item, 'status',
                                self._format_candidate_status(candidate))
                        except Exception:
                            pass
                    messagebox.show_failure(
                        "简历二次评估",
                        headline=f"{name} 的简历评估未完成",
                        message="评估过程中出现异常，候选人分数没有更新。",
                        detail=str(error),
                        notice="请检查模型配置或网络连接后重试。",
                        parent=_parent,
                    )
                self.run_on_ui(_on_error)

        threading.Thread(target=_eval_worker, daemon=True).start()

    def _revert_resume_eval(self, item, candidate=None, parent=None):
        """撤销简历评估：清空简历数据和二次评估结果，回退分数。"""
        from llm_eval import _recalc_recommend_level, _resolve_rule_score

        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            return

        _parent = parent or self.root
        name = candidate.get('name', '')
        confirm = messagebox.ask_confirmation(
            "撤销简历评估",
            headline=f"撤销 {name} 的简历评估？",
            message="候选人分数将回退到一次评估状态。",
            notice="关联的简历文件和二次评估结果将被清除。",
            yes_label="撤销评估",
            no_label="保留结果",
            dangerous=True,
            parent=_parent,
        )
        if not confirm:
            return

        outcome = _candidate_controller_for(self).revert_resume_evaluation(
            candidate,
            resolve_rule_score=_resolve_rule_score,
            recalc_recommend_level=_recalc_recommend_level,
            resume_state_fields=RESUME_STATE_FIELDS,
        )
        if not outcome.updated:
            messagebox.showerror(
                "撤销失败",
                "候选人记录已变化，请刷新后重试。",
                parent=_parent,
            )
            return

        if outcome.cleanup.unmanaged_reference_count:
            self.append_log("[撤销评估] 已清除记录，但未删除受管目录外的简历文件")
        if outcome.cleanup.failure_count:
            self.append_log(
                "[撤销评估] 受管简历副本删除失败，可运行简历存储体检重试"
            )

        self.refresh_results()
        self.refresh_home_stats()
        self.append_log(f"[撤销评估] {name}: 分数回退到 {outcome.score}")

    # ===== 一键AI评估功能 =====

    def _candidate_ai_eval_skip_reason(self, candidate):
        """返回候选人不能进入新一轮 AI 评估的确定性原因。"""
        geek_id = str(candidate.get('geek_id', ''))
        if geek_id and geek_id in getattr(self, '_ai_evaluating_ids', set()):
            return "正在评估"
        if _candidate_has_ai_eval(candidate):
            return "已评估过"
        return ""

    def _partition_candidates_for_ai_eval(self, candidates):
        """拆分本次可评估候选人与需要明确报告的跳过项。"""
        eligible = []
        skipped = []
        for candidate in candidates:
            reason = self._candidate_ai_eval_skip_reason(candidate)
            if reason:
                skipped.append({
                    'candidate': candidate,
                    'name': candidate.get('name') or '?',
                    'reason': reason,
                })
            else:
                eligible.append(candidate)
        return eligible, skipped

    def _batch_ai_eval_menu_label(self, candidates):
        """有可评估候选人时返回菜单文案，否则隐藏该操作。"""
        if getattr(self, '_ai_eval_in_progress', False):
            return ""
        eligible, _skipped = self._partition_candidates_for_ai_eval(candidates)
        return f" 批量AI评估（{len(eligible)}人）" if eligible else ""

    def _ai_eval_selected_candidates(self, candidates):
        """对选中的候选人发起AI评估"""
        if not candidates:
            return

        batch_requested = len(candidates) > 1
        if getattr(self, '_ai_eval_in_progress', False):
            messagebox.showinfo("提示", "已有 AI 评估任务正在运行，请完成后再试")
            return

        candidates_to_eval, skipped = self._partition_candidates_for_ai_eval(candidates)
        if not candidates_to_eval:
            reason_counts = Counter(item['reason'] for item in skipped)
            if set(reason_counts) == {"已评估过"}:
                text = f"选中的 {len(skipped)} 名候选人已全部评估过"
            elif set(reason_counts) == {"正在评估"}:
                text = "选中的候选人正在评估中"
            else:
                detail = "，".join(f"{reason} {count} 人" for reason, count in reason_counts.items())
                text = f"所选候选人当前均无需评估：{detail}"
            messagebox.showinfo("提示", text)
            return

        # 检查API配置
        api_config = self.api_config
        if not api_config or not api_config.get('api_provider'):
            messagebox.showwarning("警告", "请先配置AI模型")
            return

        api_key = self._get_api_key_cached(
            api_config.get('api_provider', ''), api_config.get('base_url', '')
        )
        if not api_key:
            messagebox.showwarning("警告", "请先配置API Key")
            return

        # 按岗位拆分，确保每名候选人使用自己的岗位要求评估。
        candidates_by_job = {}
        for candidate in candidates_to_eval:
            job_name = str(candidate.get('job_name') or '').strip()
            if not job_name:
                skipped.append({
                    'candidate': candidate,
                    'name': candidate.get('name') or '?',
                    'reason': '缺少岗位信息',
                })
                continue
            candidates_by_job.setdefault(job_name, []).append(candidate)

        evaluation_groups = []
        for job_candidates in candidates_by_job.values():
            job_requirement, rule = self._get_job_requirement_for_candidates(job_candidates)
            if not job_requirement:
                skipped.extend({
                    'candidate': candidate,
                    'name': candidate.get('name') or '?',
                    'reason': '无法获取岗位要求',
                } for candidate in job_candidates)
                continue
            evaluation_groups.append((job_candidates, job_requirement, rule))

        candidates_to_eval = [
            candidate
            for job_candidates, _job_requirement, _rule in evaluation_groups
            for candidate in job_candidates
        ]
        if not candidates_to_eval:
            detail = "，".join(
                f"{reason} {count} 人"
                for reason, count in Counter(item['reason'] for item in skipped).items()
            )
            messagebox.show_notice(
                "AI 评估",
                headline="没有可执行的 AI 评估",
                message="所选候选人当前均不满足评估条件。",
                detail=detail,
                kind="info",
                parent=self.root,
            )
            return

        skipped_detail = ""
        if skipped:
            skipped_detail = "，".join(
                f"{reason} {count} 人"
                for reason, count in Counter(item['reason'] for item in skipped).items()
            )

        # 批量操作只确认一次，同时说明可执行和将跳过的人数。
        count = len(candidates_to_eval)
        if batch_requested:
            metrics = [("将评估", f"{count} 人")]
            if skipped:
                metrics.append(("将跳过", f"{len(skipped)} 人"))
            if not messagebox.ask_confirmation(
                "批量 AI 评估",
                headline=f"开始评估 {count} 名候选人？",
                message="评估将在后台运行，完成结果会显示在候选人状态列。",
                metrics=tuple(metrics),
                notice=(
                    f"跳过原因：{skipped_detail}"
                    if skipped_detail
                    else None
                ),
                yes_label="开始评估",
                no_label="取消",
                parent=self.root,
            ):
                return

        # 设置评估中标记（使用全局集合，refresh_results 后仍有效）
        for c in candidates_to_eval:
            self._ai_evaluating_ids.add(str(c.get('geek_id', '')))

        # 立即刷新一次，显示"AI评估中..."
        self.refresh_results(force=True)

        # 启动后台线程
        self._ai_eval_in_progress = True
        self._ai_eval_total = len(candidates_to_eval)
        self._ai_eval_done = 0
        self._ai_eval_batch_summary = {
            'enabled': batch_requested,
            'selected_count': len(candidates),
            'eval_count': len(candidates_to_eval),
            'skipped': [
                {'name': item['name'], 'reason': item['reason']}
                for item in skipped
            ],
            'success': [],
            'failed': [],
        }

        # 启动定时刷新
        self._ai_eval_refresh_timer = self.root.after(1000, self._refresh_ai_eval_status)

        threading.Thread(
            target=self._do_ai_eval_batch,
            args=(evaluation_groups, api_config, api_key),
            daemon=True
        ).start()

    def _get_job_requirement_for_candidates(self, candidates):
        """根据候选人获取岗位需求文本和规则。返回 (job_requirement, rule)。"""
        if not candidates:
            return "", {}

        # 获取第一个候选人的岗位名称
        job_name = str(candidates[0].get('job_name') or '').strip()
        if not job_name:
            return "", {}

        # 从job_config.json获取岗位需求
        job_rules = self._get_job_rules_cached()
        normalized_job = normalize_job_name(job_name)
        matched_names = [
            configured_name
            for configured_name in job_rules
            if job_names_equal(configured_name, normalized_job)
        ]
        if len(matched_names) != 1:
            return "", {}
        rule = job_rules.get(matched_names[0])
        if not isinstance(rule, dict) or not rule:
            return "", {}

        job_requirement = rule.get('original_requirement', '')
        if not job_requirement:
            min_exp = rule.get('min_exp', 0)
            edu = rule.get('edu', '不限')
            job_requirement = f"岗位：{job_name}，{min_exp}年经验，{edu}学历"

        return job_requirement, rule

    @staticmethod
    def _format_ai_hard_conditions(rule: dict) -> str:
        """Format the saved job rule once for both first and resume AI reviews."""
        return candidate_presenter.format_ai_hard_conditions(rule)

    def _do_ai_eval_batch(self, evaluation_groups, api_config, api_key):
        """按岗位分组后台执行 AI 评估，并合并本轮结果。"""
        import sys
        import io
        from llm_eval import evaluate_batch

        all_candidates = [
            candidate
            for candidates, _job_requirement, _rule in evaluation_groups
            for candidate in candidates
        ]
        success_items = []
        failed_items = []
        completed_candidates = set()
        processed_count = 0
        old_stdout = sys.stdout
        try:
            for candidates, job_requirement, rule in evaluation_groups:
                group_size = len(candidates)

                def progress_callback(percentage, description, *, _base=processed_count, _size=group_size):
                    self._ai_eval_done = _base + int(percentage / 100 * _size)

                hard_conditions = self._format_ai_hard_conditions(rule)

                group_error = ""
                try:
                    sys.stdout = io.StringIO()
                    evaluate_batch(
                        candidates, job_requirement, api_config, api_key,
                        hard_conditions=hard_conditions,
                        rule=rule,
                        progress_callback=progress_callback,
                    )
                except Exception as exc:
                    group_error = str(exc)
                finally:
                    sys.stdout = old_stdout

                for candidate in candidates:
                    geek_id = str(candidate.get('geek_id', ''))
                    name = candidate.get('name') or '未命名候选人'
                    if candidate.get('llm_evaluated'):
                        adjustment = candidate.get('llm_adjustment', 0)
                        self._ai_eval_results[geek_id] = {
                            'status': 'success',
                            'message': f"评估完成，调整分：{'+' if adjustment >= 0 else ''}{adjustment}",
                            'timestamp': time.time()
                        }
                        success_items.append({'name': name, 'adjustment': adjustment})
                    else:
                        error = group_error or candidate.get('llm_error', '评估失败')
                        self._ai_eval_results[geek_id] = {
                            'status': 'failed',
                            'message': error,
                            'timestamp': time.time()
                        }
                        failed_items.append({'name': name, 'reason': error})
                    completed_candidates.add(id(candidate))
                self._save_ai_eval_results(candidates)
                processed_count += group_size
                self._ai_eval_done = processed_count

        except Exception as e:
            for candidate in all_candidates:
                name = candidate.get('name') or '未命名候选人'
                if id(candidate) in completed_candidates:
                    continue
                self._ai_eval_results[str(candidate.get('geek_id', ''))] = {
                    'status': 'failed',
                    'message': str(e),
                    'timestamp': time.time()
                }
                failed_items.append({'name': name, 'reason': str(e)})
        finally:
            sys.stdout = old_stdout
            self._set_ai_eval_batch_outcome(success_items, failed_items)
            self._ai_eval_in_progress = False
            # 从评估中集合移除
            for candidate in all_candidates:
                self._ai_evaluating_ids.discard(str(candidate.get('geek_id', '')))
            self.root.after(0, self._on_ai_eval_complete)

    def _refresh_ai_eval_status(self):
        """定时刷新AI评估状态；每组评估完成都会落盘，指纹变化时 refresh_results 自动全量刷新，未变时是廉价空操作。"""
        if self._ai_eval_in_progress:
            self.refresh_results()
            self._ai_eval_refresh_timer = self.root.after(1000, self._refresh_ai_eval_status)

    def _on_ai_eval_complete(self):
        """AI评估完成"""
        self._ai_eval_in_progress = False
        if hasattr(self, '_ai_eval_refresh_timer'):
            self.root.after_cancel(self._ai_eval_refresh_timer)

        # 记录需要滚动到的候选人 geek_id
        scroll_to_geek_ids = set(self._ai_eval_results.keys())

        self.refresh_results(force=True)
        self.refresh_home_stats()

        # 滚动到评估过的候选人位置
        if scroll_to_geek_ids:
            self._scroll_to_ai_evaluated_candidates(scroll_to_geek_ids)

        self._show_ai_eval_batch_summary()

        # 启动定时刷新状态列（显示结果约3秒后自动恢复）
        self._ai_eval_status_refresh_count = 0
        self._refresh_ai_eval_result_status(scroll_to_geek_ids)

    def _set_ai_eval_batch_outcome(self, success_items, failed_items):
        """记录批量 AI 评估结果；单人评估不弹汇总，但仍复用同一状态结构。"""
        summary = getattr(self, '_ai_eval_batch_summary', None)
        if not summary:
            return
        summary['success'] = success_items
        summary['failed'] = failed_items

    @staticmethod
    def _format_ai_eval_batch_summary(summary):
        """生成批量 AI 评估完成后的汇总弹窗文案。"""
        return candidate_presenter.format_ai_eval_batch_summary(summary)

    def _show_ai_eval_batch_summary(self):
        """批量 AI 评估结束后弹出一次汇总；单个候选人只用状态列反馈。"""
        summary = getattr(self, '_ai_eval_batch_summary', None)
        self._ai_eval_batch_summary = None
        if not summary or not summary.get('enabled'):
            return

        title, message, has_failure = self._format_ai_eval_batch_summary(summary)
        if has_failure:
            messagebox.showwarning(title, message, parent=self.root)
        else:
            messagebox.showinfo(title, message, parent=self.root)

    def _scroll_to_ai_evaluated_candidates(self, geek_ids):
        """滚动到AI评估过的候选人位置"""
        if not geek_ids:
            return

        target_ids = {str(geek_id) for geek_id in geek_ids}
        # 找到第一个评估过的候选人在 Treeview 中的位置
        for item in self.result_tree.get_children():
            candidate = self._item_to_candidate.get(item)
            if candidate and str(candidate.get('geek_id', '')) in target_ids:
                # 滚动到该位置
                self.result_tree.see(item)
                # 选中该候选人
                self.result_tree.selection_set(item)
                self.result_tree.focus(item)
                try:
                    self.result_tree.focus_set()
                except Exception:
                    pass
                return True
        return False

    def _refresh_ai_eval_result_status(self, highlight_geek_ids=None):
        """定时刷新状态列显示AI评估结果（约3秒后自动恢复）"""
        if not self._ai_eval_results:
            return

        self._ai_eval_status_refresh_count += 1

        # 检查是否所有结果都超过3秒
        now = time.time()
        all_expired = all(
            now - result.get('timestamp', 0) >= 3
            for result in self._ai_eval_results.values()
        )

        if all_expired or self._ai_eval_status_refresh_count >= 4:
            # 清除结果，恢复原状态
            self._ai_eval_results.clear()
            self.refresh_results(force=True)
            if highlight_geek_ids:
                self._scroll_to_ai_evaluated_candidates(highlight_geek_ids)
        else:
            # 继续刷新
            self.refresh_results(force=True)
            if highlight_geek_ids:
                self._scroll_to_ai_evaluated_candidates(highlight_geek_ids)
            # 启动下一次定时刷新
            self._ai_eval_result_refresh_timer = self.root.after(
                1000,
                lambda: self._refresh_ai_eval_result_status(highlight_geek_ids),
            )

    def _save_ai_eval_results(self, candidates):
        """把 AI 评估字段合并到最新候选人快照。"""
        eval_map = _candidate_controller_for(self).save_ai_evaluations(candidates)

        # 更新内存数据
        if hasattr(self, 'all_candidates'):
            for i, c in enumerate(self.all_candidates):
                identity = self._candidate_identity_key(c)
                if identity in eval_map:
                    self.all_candidates[i].update(eval_map[identity])

    def _blacklist_candidate(self, item, candidate=None, parent=None, on_saved=None):
        """把选中候选人加入黑名单。"""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人")
            return

        name = candidate.get('name', '该候选人')

        def save_blacklist(reason):
            try:
                blacklisted_at = datetime.now().strftime("%Y%m%d_%H%M%S")
                updated = self._update_candidate_blacklist(
                    candidate.get('geek_id'), reason, blacklisted_at
                )
                if not updated:
                    messagebox.showerror("错误", "加入黑名单失败：未找到候选人")
                    return
                candidate['blacklisted'] = True
                candidate['blacklist_reason'] = reason.strip()
                candidate['blacklisted_at'] = blacklisted_at
                if candidate.get('followup_status') not in {"不合适", "已归档"}:
                    apply_followup_state(
                        candidate,
                        "不合适",
                        candidate.get('followup_note', ''),
                        timestamp=blacklisted_at,
                    )
                self._sync_greet_queue_candidate_state(candidate)
                self._regenerate_excel()
                self.refresh_home_stats()
                self.refresh_stats()
                self.refresh_results()
                if on_saved:
                    on_saved()
                self._status_flash(f"已屏蔽：{name}")
            except Exception as exc:
                messagebox.showerror("错误", f"加入黑名单失败：{exc}")

        self._open_blacklist_reason_dialog(candidate, parent or self.root, save_blacklist)

    def _update_candidate_unblacklist(self, geek_id):
        """Keep the historical GUI entry point as a controller delegate."""
        return _candidate_controller_for(self).unblacklist(geek_id)

    def _unblacklist_candidate(self, item, candidate=None, parent=None, on_saved=None):
        """把选中候选人移出黑名单。"""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人")
            return

        name = candidate.get('name', '该候选人')
        if not messagebox.askyesno("移出黑名单", f"确定将 {name} 移出黑名单？"):
            return

        try:
            updated = self._update_candidate_unblacklist(candidate.get('geek_id'))
            if not updated:
                messagebox.showerror("错误", "移出黑名单失败：未找到已屏蔽记录")
                return
            candidate.pop('blacklisted', None)
            candidate.pop('blacklist_reason', None)
            candidate.pop('blacklisted_at', None)
            self._regenerate_excel()
            self.refresh_home_stats()
            self.refresh_stats()
            self.refresh_results()
            if on_saved:
                on_saved()
            self._status_flash(f"已移出黑名单：{name}")
        except Exception as exc:
            messagebox.showerror("错误", f"移出黑名单失败：{exc}")

    def _update_candidate_followup(
        self,
        geek_id,
        job_name,
        status,
        note,
        next_followup_at=None,
        timestamp=None,
    ):
        """Keep the historical GUI entry point as a controller delegate."""
        return _candidate_controller_for(self).update_followup(
            geek_id,
            job_name,
            status,
            note,
            next_followup_at,
            timestamp,
        )

    def _quick_update_candidate_followup(
        self,
        candidate,
        status,
        parent,
        on_saved=None,
        *,
        days=None,
    ):
        """Persist a common follow-up transition without opening the full form."""
        followup_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        next_due = None
        if days is not None:
            next_due = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d_%H%M%S")
        try:
            updated = self._update_candidate_followup(
                candidate.get('geek_id'),
                candidate.get('job_name', ''),
                status,
                candidate.get('followup_note', ''),
                next_due,
                followup_time,
            )
            if not updated:
                messagebox.showerror(
                    "更新跟进", "保存失败：未找到候选人", parent=parent or self.root
                )
                return
            if status == "未沟通":
                mark_candidate_not_greeted(candidate, followup_time)
            elif (
                status in CONTACTED_FOLLOWUP_STATUSES
                and not candidate.get('greet_sent')
            ):
                mark_candidate_greeted(candidate, "manual_status", followup_time)
            apply_followup_state(
                candidate,
                status,
                candidate.get('followup_note', ''),
                timestamp=followup_time,
                next_followup_at=next_due,
            )
            self._sync_greet_queue_candidate_state(candidate)
            self._regenerate_excel()
            self.refresh_home_stats()
            self.refresh_stats()
            self.refresh_results()
            if on_saved:
                on_saved()
        except Exception as exc:
            messagebox.showerror(
                "更新跟进", f"保存跟进状态失败：{exc}", parent=parent or self.root
            )

    @staticmethod
    def _manual_review_contact_approval_reason(candidate):
        """Return the contact approval implied by passing the displayed review."""
        resolved = dict(candidate)
        resolved['manual_review_required'] = False
        resolved['qualification_status'] = 'qualified'
        resolved.pop('auto_greet_blocked_reason', None)
        if candidate_can_manual_approve_contact(resolved):
            return "人工确认复核通过并可联系"
        return ""

    def _clear_manual_review(
        self,
        geek_id,
        job_name,
        contact_approval_reason="",
        review_passed_reasons=None,
        timestamp=None,
    ):
        """Keep the historical GUI entry point as a controller delegate."""
        return _candidate_controller_for(self).complete_review(
            geek_id,
            job_name,
            contact_approval_reason=contact_approval_reason,
            review_passed_reasons=review_passed_reasons,
            timestamp=timestamp,
        )

    def _reject_candidate_review(
        self,
        geek_id,
        job_name,
        review_rejected_reasons=None,
        timestamp=None,
    ):
        """Keep the historical GUI entry point as a controller delegate."""
        return _candidate_controller_for(self).reject_review(
            geek_id,
            job_name,
            review_rejected_reasons=review_rejected_reasons,
            timestamp=timestamp,
        )

    def _update_candidate_contact_approval(
        self,
        geek_id,
        job_name,
        reason,
        timestamp=None,
    ):
        """Keep the historical GUI entry point as a controller delegate."""
        return _candidate_controller_for(self).approve_contact(
            geek_id,
            job_name,
            reason,
            timestamp=timestamp,
        )

    def _approve_candidate_contact_and_queue(
        self,
        candidate,
        *,
        parent=None,
        on_saved=None,
    ):
        """Confirm, persist, and queue one manually reviewed pending candidate."""
        if not candidate_can_manual_approve_contact(candidate):
            reason = candidate_greet_skip_reason(candidate) or "当前状态无需人工批准"
            messagebox.showwarning(
                "确认联系候选人",
                f"当前不能执行人工批准：{reason}",
                parent=parent or self.root,
            )
            return 0

        name = candidate.get('name') or '该候选人'
        score = candidate.get('match_score', 0)
        if not messagebox.ask_confirmation(
            "确认联系候选人",
            headline=f"确认 {name} 可以联系？",
            message="确认后会记录人工批准并加入联系清单。",
            metrics=(("匹配分", f"{score} 分"), ("当前结论", "待定")),
            notice="此操作不会修改匹配分或推荐指数。",
            yes_label="批准并加入清单",
            no_label="返回复核",
            parent=parent or self.root,
        ):
            return 0

        approved_at = datetime.now().strftime("%Y%m%d_%H%M%S")
        approval_reason = "人工确认待定候选人可联系"
        updated = self._update_candidate_contact_approval(
            candidate.get('geek_id'),
            candidate.get('job_name', ''),
            approval_reason,
            approved_at,
        )
        if not updated:
            messagebox.showerror(
                "确认联系候选人",
                "保存人工批准失败：未找到候选人",
                parent=parent or self.root,
            )
            return 0

        candidate['contact_approved_at'] = approved_at
        candidate['contact_approval_reason'] = approval_reason
        candidate['review_passed_at'] = approved_at
        candidate['review_passed_reasons'] = [
            f"评分处于待定区间（{score} 分）"
        ]
        added = self._add_candidates_to_greet_queue(
            [candidate],
            parent=parent or self.root,
        )
        self.refresh_results(force=True)
        if on_saved:
            on_saved()
        return added

    def _confirm_manual_review(self, item, candidate=None, parent=None, on_saved=None):
        """清除候选人的需人工确认标记。"""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人")
            return

        name = candidate.get('name', '该候选人')
        review_reasons = derive_candidate_decision(candidate).review_reasons
        risk_text = (
            "\n".join(f"- {reason}" for reason in review_reasons)
            if review_reasons else "无"
        )
        contact_approval_reason = self._manual_review_contact_approval_reason(candidate)
        confirmation_effect = (
            "确认后将清除「需人工确认」标记，并记录为人工确认可联系；"
            "不会修改匹配分或推荐指数。"
            if contact_approval_reason
            else "确认后将清除「需人工确认」标记。"
        )
        if not messagebox.ask_confirmation(
            "确认通过",
            headline=f"确认 {name} 通过人工复核？",
            message=confirmation_effect,
            detail=risk_text,
            notice="通过复核不会自动联系；仍需加入联系清单。",
            yes_label="确认通过",
            no_label="继续复核",
            parent=parent or self.root
        ):
            return

        try:
            confirmed_at = datetime.now().strftime("%Y%m%d_%H%M%S")
            updated = self._clear_manual_review(
                candidate.get('geek_id'),
                candidate.get('job_name', ''),
                contact_approval_reason=contact_approval_reason,
                review_passed_reasons=review_reasons,
                timestamp=confirmed_at,
            )
            if not updated:
                self._status_flash(f"{name} 当前已无需人工确认")
                return
            candidate['manual_review_required'] = False
            candidate['qualification_status'] = 'qualified'
            candidate['qualification_reasons'] = []
            candidate.pop('auto_greet_blocked_reason', None)
            candidate['review_passed_at'] = confirmed_at
            candidate['review_passed_reasons'] = list(review_reasons)
            candidate.pop('review_rejected_at', None)
            candidate.pop('review_rejected_reasons', None)
            if contact_approval_reason:
                candidate['contact_approved_at'] = confirmed_at
                candidate['contact_approval_reason'] = contact_approval_reason
            self._regenerate_excel()
            self.refresh_home_stats()
            self.refresh_stats()
            self.refresh_results()
            if on_saved:
                on_saved()
            self._status_flash(f"已确认通过：{name}")
        except Exception as exc:
            messagebox.showerror("错误", f"操作失败：{exc}",
                                 parent=parent or self.root)

    def _confirm_review_rejection(self, item, candidate=None, parent=None, on_saved=None):
        """Confirm and persist that a pending candidate did not pass human review."""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人", parent=parent or self.root)
            return
        decision = derive_candidate_decision(candidate)
        if decision.review_status != "pending":
            self._status_flash("该候选人当前已不处于待复核状态")
            return

        name = candidate.get('name') or '该候选人'
        reasons = list(decision.review_reasons or ["人工复核不通过"])
        reason_text = "\n".join(f"- {reason}" for reason in reasons)
        if not messagebox.ask_confirmation(
            "确认不通过",
            headline=f"确认 {name} 不通过人工复核？",
            message="确认后将结束待复核并禁止联系。",
            detail=reason_text,
            notice="候选人仍会保留在淘汰记录中。",
            yes_label="确认不通过",
            no_label="继续复核",
            dangerous=True,
            parent=parent or self.root,
        ):
            return

        try:
            rejected_at = datetime.now().strftime("%Y%m%d_%H%M%S")
            updated = self._reject_candidate_review(
                candidate.get('geek_id'),
                candidate.get('job_name', ''),
                review_rejected_reasons=reasons,
                timestamp=rejected_at,
            )
            if not updated:
                messagebox.showerror(
                    "确认不通过",
                    "保存失败：候选人已不处于待复核状态",
                    parent=parent or self.root,
                )
                return
            candidate['manual_review_required'] = False
            candidate['qualification_status'] = 'rejected'
            candidate['qualification_reasons'] = reasons
            candidate['review_rejected_at'] = rejected_at
            candidate['review_rejected_reasons'] = reasons
            candidate['recommend_level'] = '未通过'
            candidate.pop('review_passed_at', None)
            candidate.pop('review_passed_reasons', None)
            candidate.pop('contact_approved_at', None)
            candidate.pop('contact_approval_reason', None)
            self._sync_greet_queue_candidate_state(candidate)
            self._regenerate_excel()
            self.refresh_home_stats()
            self.refresh_stats()
            self.refresh_results(force=True)
            if on_saved:
                on_saved()
            self._status_flash(f"复核不通过：{name}")
        except Exception as exc:
            messagebox.showerror(
                "确认不通过",
                f"操作失败：{exc}",
                parent=parent or self.root,
            )

    def _batch_confirm_manual_review(self, candidates, parent=None):
        """批量清除候选人的需人工确认标记。"""
        to_confirm = [
            c for c in candidates
            if (
                c.get('manual_review_required')
                or c.get('qualification_status') == 'manual_review'
            )
        ]
        if not to_confirm:
            self._status_flash("选中候选人中没有需确认的标记")
            return
        names = ", ".join(c.get('name', '?') for c in to_confirm[:5])
        if len(to_confirm) > 5:
            names += f" 等 {len(to_confirm)} 人"
        approval_reasons = {
            self._candidate_identity_key(c):
            self._manual_review_contact_approval_reason(c)
            for c in to_confirm
        }
        review_reasons = {
            self._candidate_identity_key(c):
            list(derive_candidate_decision(c).review_reasons)
            for c in to_confirm
        }
        approval_count = sum(bool(reason) for reason in approval_reasons.values())
        approval_text = (
            f"其中 {approval_count} 名待定候选人将同时记录为人工确认可联系。"
            if approval_count else
            "本次只会清除人工确认标记。"
        )
        if not messagebox.ask_confirmation(
            "批量确认通过",
            headline=f"确认 {len(to_confirm)} 人通过人工复核？",
            message=approval_text,
            metrics=(("待确认", f"{len(to_confirm)} 人"),),
            detail=names,
            notice="不会修改匹配分或推荐指数，也不会自动联系。",
            yes_label="确认全部通过",
            no_label="取消",
            parent=parent or self.root
        ):
            return

        confirmed = 0
        confirmed_at = datetime.now().strftime("%Y%m%d_%H%M%S")
        for c in to_confirm:
            try:
                contact_approval_reason = approval_reasons.get(
                    self._candidate_identity_key(c), ""
                )
                updated = self._clear_manual_review(
                    c.get('geek_id'),
                    c.get('job_name', ''),
                    contact_approval_reason=contact_approval_reason,
                    review_passed_reasons=review_reasons.get(
                        self._candidate_identity_key(c), []
                    ),
                    timestamp=confirmed_at,
                )
                if updated:
                    c['manual_review_required'] = False
                    c['qualification_status'] = 'qualified'
                    c['qualification_reasons'] = []
                    c.pop('auto_greet_blocked_reason', None)
                    c['review_passed_at'] = confirmed_at
                    c['review_passed_reasons'] = review_reasons.get(
                        self._candidate_identity_key(c), []
                    )
                    if contact_approval_reason:
                        c['contact_approved_at'] = confirmed_at
                        c['contact_approval_reason'] = contact_approval_reason
                    confirmed += 1
            except Exception:
                continue

        if confirmed:
            self._regenerate_excel()
            self.refresh_home_stats()
            self.refresh_stats()
            self.refresh_results()
        self._status_flash(f"已确认通过 {confirmed}/{len(to_confirm)} 人")

    def _save_candidate_followup_from_dialog(
        self,
        candidate,
        status,
        note,
        next_due,
        window,
        on_saved=None,
    ):
        """Persist validated follow-up form data and update controller state."""
        if (
            status == "未沟通"
            and (
                candidate.get("greet_sent")
                or candidate.get("followup_status") in CONTACTED_FOLLOWUP_STATUSES
            )
            and not messagebox.ask_confirmation(
                "纠正沟通状态",
                headline="将沟通状态纠正为“未沟通”？",
                message="仅在先前记录确实有误时执行。",
                notice="本地已打招呼事实、发送方式和跟进日期会同时清除。",
                yes_label="确认纠正",
                no_label="保留原状态",
                dangerous=True,
                parent=window,
            )
        ):
            return gui_candidate_state_dialogs.FollowupSaveResult(False)

        followup_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            updated = self._update_candidate_followup(
                candidate.get("geek_id"),
                candidate.get("job_name", ""),
                status,
                note,
                next_due,
                followup_time,
            )
            if not updated:
                messagebox.show_failure(
                    "保存跟进状态",
                    headline="跟进状态未保存",
                    message="本地候选人记录已发生变化，未找到当前候选人。",
                    notice="请关闭窗口并刷新候选人列表后重试。",
                    parent=window,
                )
                return gui_candidate_state_dialogs.FollowupSaveResult(False)
            if status == "未沟通":
                mark_candidate_not_greeted(candidate, followup_time)
            elif (
                status in CONTACTED_FOLLOWUP_STATUSES
                and not candidate.get("greet_sent")
            ):
                mark_candidate_greeted(
                    candidate,
                    "manual_status",
                    followup_time,
                )
            apply_followup_state(
                candidate,
                status,
                note,
                timestamp=followup_time,
                next_followup_at=next_due,
            )
            self._sync_greet_queue_candidate_state(candidate)
            self._regenerate_excel()
            self.refresh_results()
            if on_saved:
                on_saved()
            needs_feedback = (
                status == "不合适" and not candidate.get("feedback_status")
            )
            return gui_candidate_state_dialogs.FollowupSaveResult(
                True,
                request_feedback=needs_feedback,
            )
        except Exception as exc:
            messagebox.show_failure(
                "保存跟进状态",
                headline="跟进状态未保存",
                message="保存过程中出现异常，本次修改没有完成。",
                detail=str(exc),
                notice="请检查数据文件是否可写后重试。",
                parent=window,
            )
            return gui_candidate_state_dialogs.FollowupSaveResult(False)

    def _mark_candidate_followup(self, item, candidate=None, parent=None, on_saved=None):
        """标记候选人的跟进状态和备注。"""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人")
            return

        dialog_parent = parent or self.root

        def save_followup(status, note, next_due, window):
            return self._save_candidate_followup_from_dialog(
                candidate,
                status,
                note,
                next_due,
                window,
                on_saved,
            )

        def request_feedback():
            self._mark_candidate_feedback(
                None,
                candidate=candidate,
                parent=dialog_parent,
                on_saved=on_saved,
                default_status="放弃",
            )

        gui_candidate_state_dialogs.show_followup_dialog(
            self,
            candidate,
            dialog_parent,
            font_family=FONT_FAMILY,
            status_options=FOLLOWUP_STATUS_OPTIONS,
            default_next_followup=default_next_followup_at,
            format_followup_due=format_followup_due_at,
            normalize_followup=normalize_followup_at,
            on_save=save_followup,
            on_request_feedback=request_feedback,
        )

    def _update_candidate_feedback(self, geek_id, job_name, status, reasons, note):
        """Keep the historical GUI entry point as a controller delegate."""
        return _candidate_controller_for(self).update_feedback(
            geek_id,
            job_name,
            status,
            reasons,
            note,
        )

    def _save_candidate_feedback_from_dialog(
        self,
        candidate,
        status,
        reasons,
        note,
        window,
        on_saved=None,
    ):
        """Persist validated candidate feedback and synchronize controller state."""
        try:
            updated = self._update_candidate_feedback(
                candidate.get("geek_id"),
                candidate.get("job_name", ""),
                status,
                reasons,
                note,
            )
            if not updated:
                messagebox.show_failure(
                    "保存候选人反馈",
                    headline="候选人反馈未保存",
                    message="本地候选人记录已发生变化，未找到当前候选人。",
                    notice="请关闭窗口并刷新候选人列表后重试。",
                    parent=window,
                )
                return gui_candidate_state_dialogs.FeedbackSaveResult(False)
            candidate["feedback_status"] = status
            candidate["feedback_reasons"] = reasons
            candidate["feedback_note"] = note
            feedback_time = datetime.now().strftime("%Y%m%d_%H%M%S")
            candidate["feedback_updated_at"] = feedback_time
            try:
                score = int(candidate.get("match_score", 0) or 0)
            except (TypeError, ValueError):
                score = 0
            if (
                status == "合适"
                and SCORE_THRESHOLD_PASS <= score < SCORE_THRESHOLD_RECOMMEND
            ):
                candidate["review_passed_at"] = feedback_time
                candidate["review_passed_reasons"] = [
                    f"评分处于待定区间（{score} 分）"
                ]
            if status in {"误推", "放弃"}:
                candidate.pop("contact_approved_at", None)
                candidate.pop("contact_approval_reason", None)
            self._sync_greet_queue_candidate_state(candidate)
            self._regenerate_excel()
            self.refresh_results()
            if on_saved:
                on_saved()
            return gui_candidate_state_dialogs.FeedbackSaveResult(True)
        except Exception as exc:
            messagebox.show_failure(
                "保存候选人反馈",
                headline="候选人反馈未保存",
                message="保存过程中出现异常，本次修改没有完成。",
                detail=str(exc),
                notice="请检查数据文件是否可写后重试。",
                parent=window,
            )
            return gui_candidate_state_dialogs.FeedbackSaveResult(False)

    def _mark_candidate_feedback(
        self,
        item,
        candidate=None,
        parent=None,
        on_saved=None,
        default_status=None,
    ):
        """标记候选人的人工反馈状态和备注。"""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人")
            return

        dialog_parent = parent or self.root

        def save_feedback(status, reasons, note, window):
            return self._save_candidate_feedback_from_dialog(
                candidate,
                status,
                reasons,
                note,
                window,
                on_saved,
            )

        gui_candidate_state_dialogs.show_feedback_dialog(
            self,
            candidate,
            dialog_parent,
            font_family=FONT_FAMILY,
            status_options=FEEDBACK_STATUS_OPTIONS,
            reason_options=FEEDBACK_REASON_OPTIONS,
            existing_reasons=self._feedback_reasons(candidate),
            default_status=default_status,
            on_save=save_feedback,
        )

    def _format_candidate_detail(self, candidate):
        """Format candidate detail through the pure presenter."""
        from bossmaster import extract_summary_info

        dimension_labels = {}
        if (
            candidate.get('resume_eval_dimension_scores')
            or candidate.get('llm_dimension_scores')
        ):
            from llm_eval import _DIMENSION_LABELS

            dimension_labels = _DIMENSION_LABELS

        return candidate_presenter.format_candidate_detail(
            candidate,
            summary_info=extract_summary_info(candidate.get('summary', '')),
            feedback_reasons=self._feedback_reasons(candidate),
            dimension_labels=dimension_labels,
        )

    @staticmethod
    def _greet_queue_key(candidate):
        return _CONTACT_CONTROLLER.identity(candidate)

    def _greet_queue_item_for_candidate(self, candidate, *, active_only=False):
        """Return the candidate's current queue item, optionally only if active."""
        self._ensure_greet_queue_loaded()
        return _CONTACT_CONTROLLER.item_for(
            self.greet_queue_items,
            candidate,
            active_only=active_only,
        )

    def _ensure_greet_queue_loaded(self):
        """Lazily restore active queue intent against the latest candidate data."""
        if self._greet_queue_loaded:
            return
        outcome = _CONTACT_CONTROLLER.load_and_revalidate(
            CANDIDATES_PATH,
            CONTACT_QUEUE_PATH,
            load_candidates=load_candidates_all,
            load_queue=load_contact_queue,
            revalidate=self._revalidate_greet_queue_candidate,
        )
        self.greet_queue_items = list(outcome.items)
        if outcome.error:
            self.append_log(f"[联系候选人] 恢复联系清单失败：{outcome.error}")
        elif outcome.restored_count:
            self.append_log(
                f"[联系候选人] 已恢复 {outcome.restored_count} 个未完成任务"
            )
        self._greet_queue_loaded = True
        if outcome.changed:
            self._persist_greet_queue()
        else:
            self._refresh_contact_queue_badge()

    def _persist_greet_queue(self):
        """Persist active queue intent; completed rows remain session-local."""
        if not self._greet_queue_loaded:
            return
        if not self._ensure_data_storage_available(
            "保存联系清单",
            show_dialog=False,
        ):
            self.append_log("[联系候选人] 数据安全检查未通过，联系清单未写入")
            return
        try:
            save_contact_queue(self.greet_queue_items, CONTACT_QUEUE_PATH)
            self.run_on_ui(self._refresh_contact_queue_badge)
        except Exception as exc:
            self.append_log(f"[联系候选人] 保存联系清单失败：{exc}")

    def _sync_greet_queue_candidate_state(self, candidate):
        """Immediately apply a candidate state change to its active queue row."""
        if not getattr(self, '_greet_queue_loaded', False):
            return
        updated = _CONTACT_CONTROLLER.sync_candidate(
            getattr(self, "greet_queue_items", []),
            candidate,
            revalidate=self._revalidate_greet_queue_candidate,
            now=datetime.now().strftime("%Y%m%d_%H%M%S"),
        )
        if not updated:
            return
        self._persist_greet_queue()
        window = getattr(self, 'greet_queue_window', None)
        if window is not None:
            try:
                if window.winfo_exists():
                    self._refresh_greet_queue_dialog()
            except tk.TclError:
                pass

    @staticmethod
    def _has_direct_send_context(candidate):
        return contact_presenter.has_direct_send_context(candidate)

    @staticmethod
    def _greet_queue_readiness_label(candidate):
        return contact_presenter.greet_queue_readiness_label(candidate)

    @staticmethod
    def _greet_queue_readiness_tooltip(candidate):
        return contact_presenter.greet_queue_readiness_tooltip(candidate)


    def _build_greet_queue_item(self, candidate, source="manual"):
        return _CONTACT_CONTROLLER.build_item(candidate, source=source)

    @staticmethod
    def _greet_queue_skip_reason(candidate):
        return candidate_greet_skip_reason(candidate)

    def _add_scan_candidates_to_contact_queue(self, candidates, policy):
        threshold = (
            SCORE_THRESHOLD_STRONG
            if policy == "将强烈推荐加入联系清单"
            else SCORE_THRESHOLD_RECOMMEND
        )
        selected = [
            candidate for candidate in candidates
            if int(candidate.get('match_score') or 0) >= threshold
        ]
        if not selected:
            self.append_log("[联系候选人] 本轮没有符合联系清单策略的候选人")
            return 0
        return self._add_candidates_to_greet_queue(
            selected,
            parent=self.root,
            source="scan",
            open_dialog=True,
        )

    def _add_candidates_to_greet_queue(self, candidates, parent=None, source="manual", open_dialog=True):
        self._ensure_greet_queue_loaded()
        _parent = parent or self.root
        outcome = _CONTACT_CONTROLLER.add_candidates(
            self.greet_queue_items,
            candidates,
            source=source,
            skip_reason=self._greet_queue_skip_reason,
            build_item=self._build_greet_queue_item,
        )
        added = outcome.added_count
        skipped_reasons = outcome.skipped_reasons
        if open_dialog:
            self._show_greet_queue_dialog(parent=_parent)
        if added:
            self._persist_greet_queue()
            skip_text = self._format_greet_queue_skip_summary(skipped_reasons)
            self.append_log(f"[联系候选人] 已加入 {added} 人" + (f"，已跳过 {sum(skipped_reasons.values())} 人" if skipped_reasons else ""))
            if skip_text:
                self.input_support.show_text_dialog(
                    "联系候选人",
                    f"已加入联系清单：{added} 人\n\n已跳过：\n{skip_text}",
                    width=500,
                    height=280,
                    button_text="确定",
                    button_align="center",
                )
        elif skipped_reasons:
            self.input_support.show_text_dialog(
                "联系候选人",
                f"没有可加入联系清单的候选人。\n\n已跳过：\n{self._format_greet_queue_skip_summary(skipped_reasons)}",
                width=500,
                height=280,
                button_text="确定",
                button_align="center",
            )
        return added

    def _add_candidate_to_greet_queue_from_review(self, candidate, on_saved):
        """Add one review candidate and redraw the same candidate on success."""
        added = self._add_candidates_to_greet_queue(
            [candidate],
            parent=self.candidate_review_window,
        )
        if added and on_saved:
            on_saved()
        return added

    @staticmethod
    def _format_greet_queue_skip_summary(skipped_reasons):
        return contact_presenter.format_greet_queue_skip_summary(skipped_reasons)

    def _collect_selected_candidates_for_queue(self, selection, filtered_ref, tree):
        candidates = []
        for sel_item in selection:
            if tree is getattr(self, 'result_tree', None):
                candidate = self._find_candidate_by_tree_item(sel_item)
                if candidate:
                    candidates.append(candidate)
                continue
            candidate = self._find_candidate_in_detail_tree(
                tree, sel_item, filtered_ref
            )
            if candidate:
                candidates.append(candidate)
        return candidates

    def _show_greet_queue_dialog(self, parent=None):
        self._ensure_greet_queue_loaded()
        _parent = parent or self.root
        if self.greet_queue_window and self.greet_queue_window.winfo_exists():
            self.greet_queue_window.deiconify()
            self.greet_queue_window.lift()
            self._refresh_greet_queue_dialog()
            return

        initial_counts = Counter(
            (item.get('status') or "待发送") for item in self.greet_queue_items
        )
        callbacks = gui_contact_queue.ContactQueueCallbacks(
            start=self._start_greet_queue,
            pause=self._pause_greet_queue,
            resume=self._resume_greet_queue,
            group_selected=self._on_greet_queue_group_selected,
            confirm_sent=lambda: self._resolve_selected_greet_queue_pending(sent=True),
            confirm_not_sent=lambda: self._resolve_selected_greet_queue_pending(sent=False),
            retry_failed=self._retry_failed_greet_queue_items,
            remove_selected=self._remove_selected_greet_queue_items,
            show_selected_detail=self._show_selected_greet_queue_detail,
            update_action_states=self._update_greet_queue_action_states,
            row_motion=self._on_greet_queue_motion,
            hide_tooltip=self.feedback_support.hide_tooltip,
            context_menu=self._show_greet_queue_context_menu,
            select_all=self._select_all_greet_queue_rows,
            close=self._close_greet_queue_window,
        )
        widgets = gui_contact_queue.build_contact_queue_workbench(
            self,
            _parent,
            selected_group=self.greet_queue_selected_group,
            initial_counts=initial_counts,
            callbacks=callbacks,
            ui_config=UI_CONFIG,
        )
        self._greet_queue_widgets = widgets
        self.greet_queue_window = widgets.window
        self.greet_queue_metric_vars = widgets.metric_vars
        self.greet_queue_summary_var = widgets.summary_var
        self.greet_queue_action_scope_var = widgets.action_scope_var
        self.greet_queue_start_btn = widgets.start_button
        self.greet_queue_transport_frame = widgets.transport_frame
        self.greet_queue_pause_btn = widgets.pause_button
        self.greet_queue_resume_btn = widgets.resume_button
        self.greet_queue_status_filter_var = widgets.status_filter_var
        self.greet_queue_group_tree = widgets.group_tree
        self.greet_queue_detail_title_var = widgets.detail_title_var
        self.greet_queue_detail_summary_var = widgets.detail_summary_var
        self.greet_queue_selection_var = widgets.selection_var
        self.greet_queue_selected_action_buttons = widgets.selected_action_buttons
        self.greet_queue_confirm_sent_btn = widgets.confirm_sent_button
        self.greet_queue_confirm_not_sent_btn = widgets.confirm_not_sent_button
        self.greet_queue_retry_btn = widgets.retry_button
        self.greet_queue_remove_btn = widgets.remove_button
        self.greet_queue_tree = widgets.tree
        self._refresh_greet_queue_dialog()
        widgets.window.deiconify()

    def _open_greet_queue_from_result(self):
        """从结果页进入联系工作台；有待核实时直接定位对应分组。"""
        self._ensure_greet_queue_loaded()
        if any(
            item.get('status') == "待核实"
            or (item.get('candidate') or {}).get('greet_confirmation_pending')
            for item in self.greet_queue_items
        ):
            self.greet_queue_selected_group = "待核实"
        self._show_greet_queue_dialog(parent=self.root)

    def _on_greet_queue_group_selected(self):
        if not self.greet_queue_group_tree or not self.greet_queue_group_tree.winfo_exists():
            return
        selection = self.greet_queue_group_tree.selection()
        if not selection:
            return
        selected_group = str(selection[0])
        if selected_group == self.greet_queue_selected_group:
            return
        self.greet_queue_selected_group = selected_group
        if self.greet_queue_status_filter_var:
            self.greet_queue_status_filter_var.set(selected_group)
        self._refresh_greet_queue_dialog()

    def _focus_candidate_in_greet_queue(self, candidate):
        """Open the contact workbench at the candidate's actual queue status."""
        item = self._greet_queue_item_for_candidate(candidate)
        if item is None and candidate.get('greet_confirmation_pending'):
            item = self._build_greet_queue_item(candidate, source="candidate_state")
            item['status'] = "待核实"
            item['message'] = (
                candidate.get('greet_confirmation_reason')
                or "上次发送结果需要人工核实"
            )
            self.greet_queue_items.append(item)
            self._persist_greet_queue()
        elif item is not None and candidate.get('greet_confirmation_pending'):
            item['candidate'] = candidate
            item['status'] = "待核实"
            item['message'] = (
                candidate.get('greet_confirmation_reason')
                or "上次发送结果需要人工核实"
            )
            item['updated_at'] = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._persist_greet_queue()
        self._show_greet_queue_dialog(parent=self.root)
        if item is None:
            return
        self.greet_queue_selected_group = item.get('status') or "待发送"
        self._refresh_greet_queue_dialog()
        if self.greet_queue_tree and self.greet_queue_tree.winfo_exists():
            queue_id = item.get('queue_id')
            if queue_id in self.greet_queue_tree.get_children():
                self.greet_queue_tree.selection_set(queue_id)
                self.greet_queue_tree.focus(queue_id)
                self.greet_queue_tree.see(queue_id)
                self._update_greet_queue_action_states()

    def _refresh_greet_queue_dialog(self):
        if not self.greet_queue_tree or not self.greet_queue_tree.winfo_exists():
            return
        tree = self.greet_queue_tree
        tree.delete(*tree.get_children())
        counts = {}
        group_order = ("全部", "待核实", "待发送", "发送失败", "发送中", "已发送", "已跳过")
        for item in self.greet_queue_items:
            status = item.get('status') or "待发送"
            counts[status] = counts.get(status, 0) + 1
        metric_values = {
            "pending": counts.get("待发送", 0),
            "attention": counts.get("待核实", 0) + counts.get("发送失败", 0),
            "sending": counts.get("发送中", 0),
            "sent": counts.get("已发送", 0),
        }
        for key, value_var in getattr(self, 'greet_queue_metric_vars', {}).items():
            value_var.set(str(metric_values.get(key, 0)))

        if self.greet_queue_group_tree and self.greet_queue_group_tree.winfo_exists():
            group_tree = self.greet_queue_group_tree
            previous_selection = self.greet_queue_selected_group or "全部"
            group_tree.delete(*group_tree.get_children())
            group_tree.insert(
                "",
                "end",
                iid="全部",
                text=f"全部  {len(self.greet_queue_items)}",
                values=(len(self.greet_queue_items),),
                tags=("workbench_root",),
                open=True,
            )
            visible_groups = [
                group for group in group_order[1:]
                if counts.get(group, 0) > 0
            ]
            for group in visible_groups:
                group_tree.insert(
                    "全部",
                    "end",
                    iid=group,
                    text=f"{group}  {counts.get(group, 0)}",
                    values=(counts.get(group, 0),),
                    tags=("workbench_child",),
                )
            if previous_selection != "全部" and previous_selection not in visible_groups:
                previous_selection = "全部"
            self.greet_queue_selected_group = previous_selection
            if self.greet_queue_status_filter_var:
                self.greet_queue_status_filter_var.set(previous_selection)
            group_tree.selection_set(previous_selection)
            group_tree.focus(previous_selection)

        selected_status = self.greet_queue_selected_group or "全部"
        visible_count = 0
        for item in self.greet_queue_items:
            candidate = item.get('candidate') or {}
            status = item.get('status') or "待发送"
            if selected_status != "全部" and status != selected_status:
                continue
            visible_count += 1
            tree.insert(
                "",
                "end",
                iid=item.get('queue_id'),
                values=(
                    candidate.get('name', ''),
                    candidate.get('job_name', ''),
                    candidate.get('match_score', ''),
                    candidate.get('recommend_level', ''),
                    self._greet_queue_readiness_label(candidate),
                    status,
                    item.get('message') or "—",
                ),
            )
        if self.greet_queue_detail_title_var:
            self.greet_queue_detail_title_var.set(f"{selected_status}：{visible_count} 人")
        if self.greet_queue_detail_summary_var:
            self.greet_queue_detail_summary_var.set(
                self._greet_queue_group_hint(selected_status)
            )
        if self.greet_queue_summary_var:
            self.greet_queue_summary_var.set(self._greet_queue_summary_text())
        self._update_greet_queue_action_states()

    def _greet_queue_summary_text(self):
        """Return the idle summary shown above the contact queue."""
        counts = Counter(
            (item.get('status') or "待发送") for item in self.greet_queue_items
        )
        total = len(self.greet_queue_items)
        needs_attention = counts.get('待核实', 0) + counts.get('发送失败', 0)
        if total == 0:
            return "联系清单为空"
        if needs_attention:
            return f"需处理 {needs_attention} 人，请先核实发送结果或重试失败任务"
        return "发送前会再次核验候选人和岗位状态"

    @staticmethod
    def _greet_queue_group_hint(status):
        """Return one short instruction for the selected contact-queue group."""
        return contact_presenter.greet_queue_group_hint(status)

    @staticmethod
    def _greet_queue_selection_text(selected):
        """Summarize the selected scope with candidate names and status."""
        return contact_presenter.greet_queue_selection_text(selected)

    def _set_greet_queue_item_state(self, item, status, message=""):
        _CONTACT_CONTROLLER.set_item_state(item, status, message)
        self._persist_greet_queue()
        self.run_on_ui(self._refresh_greet_queue_dialog)

    def _update_greet_queue_action_states(self):
        selected = self._selected_greet_queue_items()
        selected_statuses = {item.get('status') for item in selected}
        pending_count = sum(
            1 for item in self.greet_queue_items
            if item.get('status') == "待发送"
        )
        selected_pending_count = sum(
            1 for item in selected if item.get('status') == "待发送"
        )
        action_pending_count = selected_pending_count if selected else pending_count

        start_btn = getattr(self, 'greet_queue_start_btn', None)
        preparing = getattr(self, 'greet_queue_preparing', False)
        if self.greet_queue_summary_var:
            if preparing:
                prepare_text = (
                    getattr(self, 'greet_queue_prepare_text', '')
                    or "正在准备浏览器..."
                )
                summary = f"发送准备：{prepare_text}"
            else:
                summary = self._greet_queue_summary_text()
            self.greet_queue_summary_var.set(summary)
        if start_btn and start_btn.winfo_exists():
            action_scope_var = getattr(self, 'greet_queue_action_scope_var', None)
            if action_scope_var:
                if selected:
                    action_scope_var.set(f"已选 {len(selected)} 人 · 可联系 {selected_pending_count} 人")
                else:
                    action_scope_var.set(f"待发送 {pending_count} 人")
            start_btn.configure(
                text="开始联系",
                state=(
                    "disabled"
                    if preparing or self.greet_queue_running or not action_pending_count
                    else "normal"
                ),
            )
        pause_btn = getattr(self, 'greet_queue_pause_btn', None)
        if pause_btn and pause_btn.winfo_exists():
            pause_btn.pack_forget()
            if self.greet_queue_running and not self.greet_queue_paused:
                pause_btn.configure(state="normal")
                pause_btn.pack(side="left")
        resume_btn = getattr(self, 'greet_queue_resume_btn', None)
        if resume_btn and resume_btn.winfo_exists():
            resume_btn.pack_forget()
            if self.greet_queue_running and self.greet_queue_paused:
                resume_btn.configure(state="normal")
                resume_btn.pack(side="left")
        queue_idle = not self.greet_queue_running and not getattr(
            self, 'greet_queue_preparing', False
        )
        action_states = (
            ('greet_queue_confirm_sent_btn', queue_idle and bool(selected) and selected_statuses == {"待核实"}),
            ('greet_queue_confirm_not_sent_btn', queue_idle and bool(selected) and selected_statuses == {"待核实"}),
            ('greet_queue_retry_btn', queue_idle and bool(selected) and selected_statuses == {"发送失败"}),
            (
                'greet_queue_remove_btn',
                queue_idle
                and bool(selected)
                and not selected_statuses.intersection({"发送中", "待核实"}),
            ),
        )
        selection_var = getattr(self, 'greet_queue_selection_var', None)
        if selection_var:
            if not selected:
                selection_var.set("选择候选人后，可在这里处理当前状态")
            else:
                selection_var.set(self._greet_queue_selection_text(selected))
        for attr, enabled in action_states:
            button = getattr(self, attr, None)
            if button and button.winfo_exists():
                button.configure(state="normal" if enabled else "disabled")
                button.pack_forget()
                if enabled:
                    button.pack(side="left", padx=(0, int(6 * self.dpi_scale * self.zoom_factor)))

    def _selected_greet_queue_items(self):
        tree = getattr(self, 'greet_queue_tree', None)
        if not tree or not tree.winfo_exists():
            return []
        selected = set(tree.selection())
        return [item for item in self.greet_queue_items if item.get('queue_id') in selected]

    def _show_selected_greet_queue_detail(self):
        selected = self._selected_greet_queue_items()
        if not selected:
            return
        candidate = selected[0].get('candidate') or {}
        if not candidate:
            return
        self._open_candidate_review_workbench(candidate)

    def _remove_selected_greet_queue_items(self):
        selected = self._selected_greet_queue_items()
        if not selected:
            return
        if any(item.get('status') == "发送中" for item in selected):
            messagebox.showwarning("联系候选人", "发送中的候选人不能移除，请先暂停并等待当前发送完成。", parent=self.greet_queue_window or self.root)
            return
        if any(item.get('status') == "待核实" for item in selected):
            messagebox.showwarning(
                "联系候选人",
                "待核实的候选人不能直接移除，请先确认已发送或未发送。",
                parent=self.greet_queue_window or self.root,
            )
            return
        self.greet_queue_items = _CONTACT_CONTROLLER.remove_items(
            self.greet_queue_items,
            selected,
        )
        self._persist_greet_queue()
        self._refresh_greet_queue_dialog()

    def _retry_failed_greet_queue_items(self):
        selected = self._selected_greet_queue_items()
        changed = _CONTACT_CONTROLLER.retry_failed(selected)
        if changed:
            self._persist_greet_queue()
            self._refresh_greet_queue_dialog()

    def _resolve_selected_greet_queue_pending(self, *, sent):
        selected = [
            item for item in self._selected_greet_queue_items()
            if item.get('status') == "待核实"
        ]
        if not selected:
            return
        action = "已发送" if sent else "未发送"
        if not messagebox.ask_confirmation(
            f"确认{action}",
            headline=f"将 {len(selected)} 人标记为“{action}”？",
            message="请确认已在 BOSS 沟通列表逐一核实。",
            notice=(
                "标记为未发送后，这些候选人可以重新进入发送流程。"
                if not sent else
                "标记为已发送后，不会再次自动发送。"
            ),
            yes_label=f"标记为{action}",
            no_label="取消",
            parent=self.greet_queue_window or self.root,
        ):
            return

        outcome = _CONTACT_CONTROLLER.resolve_pending(
            selected,
            sent=sent,
            candidates_path=CANDIDATES_PATH,
            resolver=resolve_candidate_greeting_confirmation,
        )
        for name, error in outcome.failures:
            self.append_log(f"[联系候选人] 核实 {name} 失败：{error}")

        self._persist_greet_queue()
        self._refresh_greet_queue_dialog()
        self.refresh_results(force=True)
        self.refresh_home_stats()
        self.append_log(
            f"[联系候选人] 已完成 {outcome.resolved_count} 人发送结果核实"
        )

    def _pause_greet_queue(self):
        if not self.greet_queue_running:
            return
        self.greet_queue_paused = True
        self.append_operation_log("[联系候选人] 已暂停，当前发送完成后停止推进")
        self._update_greet_queue_action_states()

    def _resume_greet_queue(self):
        if not self.greet_queue_running or not self.greet_queue_paused:
            return
        self.greet_queue_paused = False
        self.append_operation_log("[联系候选人] 已继续")
        self._update_greet_queue_action_states()

    def _greet_queue_cooldown_error(self):
        """Return a fail-closed user message while BOSS access is cooling down."""
        guard_state = self._boss_access_cooldown_state()
        if not guard_state.get("blocked"):
            return ""
        remaining = max(
            1,
            int(guard_state.get("remaining_seconds", 0) + 0.999),
        )
        reason = str(
            guard_state.get("reason") or "已触发 BOSS 访问保护"
        )
        message = (
            f"BOSS 访问仍在冷却中，剩余约 {remaining} 秒。\n\n{reason}"
        )
        self.append_operation_log(
            f"[访问保护] 联系候选人操作已阻止：{reason}。"
            f"剩余冷却约 {remaining} 秒。"
        )
        return message

    def _start_greet_queue(self):
        self._ensure_greet_queue_loaded()
        if getattr(self, 'greet_queue_preparing', False):
            return
        if self.greet_queue_running:
            messagebox.showinfo("联系候选人", "联系任务正在执行", parent=self.greet_queue_window or self.root)
            return
        if self.is_running:
            messagebox.showinfo("联系候选人", "候选人扫描正在运行，请等待扫描完成后再发送。", parent=self.greet_queue_window or self.root)
            return
        selected = self._selected_greet_queue_items()
        pending = _CONTACT_CONTROLLER.pending_items(
            self.greet_queue_items,
            selected,
        )
        if not pending:
            message = "选中的候选人当前不可联系" if selected else "没有待联系候选人"
            messagebox.showinfo(
                "联系候选人", message, parent=self.greet_queue_window or self.root
            )
            return
        cooldown_error = self._greet_queue_cooldown_error()
        if cooldown_error:
            messagebox.showwarning(
                "BOSS 访问保护",
                cooldown_error,
                parent=self.greet_queue_window or self.root,
            )
            return
        self.stop_event.clear()
        self.greet_queue_preparing = True
        self.greet_queue_prepare_text = "正在准备浏览器..."
        self._update_greet_queue_action_states()
        self.append_operation_log(
            f"[联系候选人] 开始准备：待处理 {len(pending)} 人，"
            "正在检查 Chrome 和 BOSS 推荐牛人页面..."
        )
        self.greet_queue_thread = threading.Thread(
            target=self._prepare_greet_queue_start,
            args=(pending,),
            daemon=True,
        )
        self.greet_queue_thread.start()

    def _begin_greet_queue_send(self, pending):
        """Start the send worker after browser readiness and user confirmation."""
        self.greet_queue_paused = False
        self.greet_queue_running = True
        self._update_greet_queue_action_states()
        self.greet_queue_thread = threading.Thread(
            target=self._run_greet_queue_worker,
            args=(pending,),
            daemon=True,
        )
        self.greet_queue_thread.start()

    def _set_greet_queue_prepare_status(self, text):
        self.greet_queue_prepare_text = text
        self.run_on_ui(self._update_greet_queue_action_states)

    def _prepare_greet_queue_start(self, pending):
        """Wait for Chrome, BOSS login and the recommendation page before confirm."""
        parent = self.greet_queue_window or self.root
        error = ""
        connection_lock_acquired = False
        try:
            connection_lock_acquired = self._browser_connection_lock.acquire(timeout=8)
            if not connection_lock_acquired:
                error = "浏览器正在执行其他操作，请稍后再试。"
                return

            error = self._greet_queue_cooldown_error()
            if error:
                return

            self._set_greet_queue_prepare_status("正在连接 Chrome...")
            if not self.browser_page or not self._is_browser_page_alive(self.browser_page):
                if not self._reconnect_browser_or_warn(
                    parent,
                    "浏览器未就绪",
                    "浏览器未就绪",
                    "无法连接或启动 Chrome。",
                ):
                    error = getattr(
                        self,
                        '_greet_queue_browser_error',
                        "无法连接或启动 Chrome。",
                    )
                    return

            recommend_url = 'https://www.zhipin.com/web/chat/recommend'
            deadline = time.monotonic() + 120
            navigation_attempted = False
            login_prompted = False
            while time.monotonic() < deadline and not self.stop_event.is_set():
                error = self._greet_queue_cooldown_error()
                if error:
                    return
                ok, current_url, _page_text, reason = self._get_greet_queue_page_state()
                if ok and self._is_boss_recommend_url(current_url):
                    self.append_operation_log(
                        "[联系候选人] Chrome、BOSS 登录和推荐牛人页面已就绪"
                    )
                    return

                if "登录" in reason:
                    if not login_prompted:
                        self.append_operation_log(
                            "[联系候选人] 请在 Chrome 中完成 BOSS 登录，程序将自动继续"
                        )
                        login_prompted = True
                    navigation_attempted = False
                    self._set_greet_queue_prepare_status("请在 Chrome 完成登录...")
                    time.sleep(1)
                    continue

                if "安全验证" in reason:
                    try:
                        from bossmaster import activate_boss_access_block
                        activate_boss_access_block(
                            "安全验证",
                            reason,
                            "联系候选人页面检查",
                        )
                    except Exception:
                        error = "检测到 BOSS 安全验证页面，已停止联系候选人操作。"
                    else:
                        error = self._greet_queue_cooldown_error()
                    return

                if not navigation_attempted and self._is_browser_page_alive(
                    self.browser_page
                ):
                    error = self._greet_queue_cooldown_error()
                    if error:
                        return
                    self._set_greet_queue_prepare_status("正在打开推荐牛人页面...")
                    try:
                        self.browser_page.get(recommend_url)
                        navigation_attempted = True
                    except Exception as exc:
                        error = f"推荐牛人页面打开失败：{str(exc)[:80]}"
                        return
                    time.sleep(1)
                    continue

                if not ok:
                    error = reason or "浏览器页面尚未就绪。"
                    return
                time.sleep(1)

            error = "等待 BOSS 登录或推荐牛人页面超时，请检查 Chrome 后重试。"
        finally:
            if error:
                self.append_operation_log(f"[联系候选人] 发送前检查未完成：{error}")
            if connection_lock_acquired:
                self._browser_connection_lock.release()
            self.run_on_ui(
                lambda items=pending, message=error: self._finish_greet_queue_preparation(
                    items, message
                )
            )

    def _finish_greet_queue_preparation(self, pending, error=""):
        """Show send confirmation only after the browser preflight completes."""
        self.greet_queue_preparing = False
        self.greet_queue_prepare_text = ""
        self._update_greet_queue_action_states()
        if error:
            messagebox.showerror(
                "发送前检查未完成",
                error,
                parent=self.greet_queue_window or self.root,
                headline="浏览器尚未就绪",
                show_icon=False,
                min_width=620,
            )
            return
        if not self._confirm_start_greet_queue(pending):
            return
        self._begin_greet_queue_send(pending)

    @staticmethod
    def _build_greet_queue_confirmation_content(pending):
        return contact_presenter.build_greet_queue_confirmation_content(pending)

    def _confirm_start_greet_queue(self, pending):
        headline, message = self._build_greet_queue_confirmation_content(pending)
        return messagebox.ask_confirmation(
            "确认联系",
            headline=headline,
            message=message,
            metrics=(("本次联系", f"{len(pending)} 人"),),
            notice="开始后仍会在每次发送前复核候选人最新状态。",
            parent=self.greet_queue_window or self.root,
            yes_label="开始联系",
            no_label="取消",
        )

    def _make_greet_queue_captcha_callback(self, parent):
        def captcha_callback(detail):
            result = [False]
            done = threading.Event()

            def show_dialog():
                answer = messagebox.ask_confirmation(
                    "检测到安全验证弹窗",
                    headline="需要在浏览器中完成人工验证",
                    message="联系队列已暂停，请先处理 BOSS 安全验证。",
                    detail=str(detail),
                    notice="继续等待不会自动绕过验证；停止将结束当前队列。",
                    yes_label="继续等待验证",
                    no_label="停止联系队列",
                    parent=parent,
                )
                result[0] = answer
                done.set()

            self.run_on_ui(show_dialog)
            while not done.is_set():
                if self.stop_event.is_set():
                    result[0] = False
                    done.set()
                    break
                done.wait(timeout=0.5)
            return result[0]
        return captcha_callback

    @staticmethod
    def _is_boss_recommend_url(url):
        return contact_presenter.is_boss_recommend_url(url)

    @staticmethod
    def _is_boss_login_page(url, page_text=""):
        return contact_presenter.is_boss_login_page(url, page_text)

    def _get_run_page_readiness(self):
        """检查当前浏览器页面是否已具备候选人扫描条件。"""
        page = self.browser_page
        if not page:
            return False, "浏览器未连接，请重新检测/连接"

        try:
            page.run_js('return 1')
            current_url = str(getattr(page, 'url', '') or '')
            page_text = str(page.run_js(
                "return (document.body && document.body.innerText || '').slice(0, 800)"
            ) or "")
        except Exception:
            return False, "浏览器连接已丢失，请重新检测/连接"

        if self._is_boss_login_page(current_url, page_text):
            return False, "当前停留在 BOSS 登录页，请先完成登录"
        if not self._is_boss_recommend_url(current_url):
            return False, "请将浏览器导航到 BOSS 直聘推荐牛人页面后再运行"

        try:
            from bossmaster import get_iframe
            target = get_iframe(page) or page
            state = target.run_js(r'''
                return (function() {
                    const href = location.href || '';
                    const text = (document.body && document.body.innerText || '').slice(0, 2000);
                    return {
                        readyState: document.readyState || '',
                        href: href,
                        hasCards: !!document.querySelector('[data-geekid]'),
                        text: text
                    };
                })()
            ''') or {}
        except Exception:
            return False, "推荐牛人页面尚未加载完成，请稍候再试"

        ready_state = str(state.get('readyState', '') or '')
        if ready_state not in ('interactive', 'complete'):
            return False, "推荐牛人页面正在加载，请稍候再试"

        target_url = str(state.get('href', '') or '')
        has_job = bool(re.search(r'[?&]job_?id=[^&]+', target_url, re.IGNORECASE))
        has_cards = bool(state.get('hasCards'))
        target_text = str(state.get('text', '') or '')
        if "您需要先发布职位，才能查看推荐牛人" in target_text:
            return False, "当前账号没有可用的已发布职位，暂时无法扫描候选人"
        has_empty_state = any(mark in target_text for mark in EMPTY_RECOMMEND_MARKS)
        if not (has_job or has_cards or has_empty_state):
            return False, "推荐页尚未加载出岗位或候选人，请选择岗位并等待页面加载完成"

        return True, ""

    def _get_greet_queue_page_state(self):
        page = self.browser_page
        if not page:
            return False, "", "", "浏览器未连接"
        try:
            page.run_js('return 1')
            current_url = str(getattr(page, 'url', '') or '')
            page_text = ""
            try:
                page_text = str(page.run_js(
                    "return (document.body && document.body.innerText || '').slice(0, 500)",
                    timeout=2,
                ) or "")
            except Exception:
                page_text = ""
        except Exception as exc:
            return False, "", "", f"浏览器页面不可用：{str(exc)[:50]}"
        if self._is_boss_login_page(current_url, page_text):
            return False, current_url, page_text, "当前停留在 BOSS 登录页，请先完成登录"
        url_lower = current_url.lower()
        risk_marks = ("安全验证", "行为验证", "请完成验证", "操作频繁", "访问受限")
        if (
            any(token in url_lower for token in ("/verify", "captcha", "security-check"))
            or any(mark in page_text for mark in risk_marks)
        ):
            return False, current_url, page_text, "当前停留在 BOSS 安全验证页面"
        if "zhipin.com" not in current_url.lower():
            return False, current_url, page_text, "当前页面不是 BOSS 直聘页面"
        return True, current_url, page_text, ""

    def _reconnect_browser_or_warn(self, parent, log_prefix, warn_title, warn_text):
        """Try to reconnect the browser and retain an actionable failure reason."""
        cooldown_error = self._greet_queue_cooldown_error()
        if cooldown_error:
            self._greet_queue_browser_error = cooldown_error
            return False
        self.append_operation_log(f"[联系候选人] {log_prefix}，正在尝试重连...")
        if self._try_reconnect_browser():
            self.append_operation_log("[联系候选人] 浏览器重连成功")
            return True

        cooldown_error = self._greet_queue_cooldown_error()
        if cooldown_error:
            self._greet_queue_browser_error = cooldown_error
            return False
        self.append_operation_log(
            "[联系候选人] 未检测到可用 Chrome，正在自动启动推荐牛人页面..."
        )
        if self._launch_boss_browser():
            self.append_operation_log(
                "[联系候选人] Chrome 已启动并打开 BOSS 推荐牛人页面"
            )
            return True
        if not getattr(self, '_greet_queue_browser_error', ''):
            self._greet_queue_browser_error = warn_text
        return False

    def _ensure_greet_queue_browser(self, parent, pending=None):
        pending_items = list(
            pending if pending is not None
            else (
                item for item in self.greet_queue_items
                if item.get('status') == "待发送"
            )
        )
        self._greet_queue_browser_error = ""
        cooldown_error = self._greet_queue_cooldown_error()
        if cooldown_error:
            self._greet_queue_browser_error = cooldown_error
            return False
        if not self.browser_page:
            if not self._reconnect_browser_or_warn(
                parent, "浏览器未连接", "浏览器未连接",
                "无法连接到 Chrome 浏览器。\n请切换到「运行控制」页点击「检测/连接浏览器」。",
            ):
                return False
        try:
            self.browser_page.run_js('return 1')
        except Exception:
            if not self._reconnect_browser_or_warn(
                parent, "浏览器连接已断开", "浏览器连接断开",
                "浏览器连接已断开且无法自动重连。\n请切换到「运行控制」页点击「检测/连接浏览器」。",
            ):
                return False
        ok, current_url, _page_text, reason = self._get_greet_queue_page_state()
        if not ok:
            if "安全验证" in reason:
                try:
                    from bossmaster import activate_boss_access_block
                    risk_exc = activate_boss_access_block(
                        "安全验证",
                        reason,
                        "联系候选人页面检查",
                    )
                    self.append_operation_log(
                        f"[访问保护] {risk_exc.reason}。已停止联系候选人操作。"
                    )
                except Exception:
                    pass
            self.append_operation_log(f"[联系候选人] {reason}")
            needs_job_page = any(
                not self._has_direct_send_context(item.get('candidate') or {})
                for item in pending_items
            )
            if needs_job_page:
                action = "请登录 BOSS，并打开对应岗位的“推荐牛人”页面。"
            else:
                action = "请在 Chrome 中登录 BOSS 账号，无需打开“推荐牛人”页面。"
            self._greet_queue_browser_error = f"{reason}。\n\n{action}"
            return False

        direct_count = sum(
            1 for item in pending_items
            if self._has_direct_send_context(item.get('candidate') or {})
        )
        list_page_count = len(pending_items) - direct_count
        if list_page_count and not self._is_boss_recommend_url(current_url):
            if direct_count:
                self.append_operation_log(
                    f"[联系候选人] 当前不在推荐页，将先发送已就绪的 {direct_count} 人；"
                    f"其余 {list_page_count} 人保留待发送"
                )
                return True
            self.append_operation_log(
                "[联系候选人] 待发送候选人需要对应岗位推荐页"
            )
            self._greet_queue_browser_error = (
                "请在 Chrome 中登录 BOSS，并打开对应岗位的“推荐牛人”页面后再次发送。"
            )
            return False
        return True

    def _reload_greet_queue_candidate(self, item):
        """Bind an item to the latest durable candidate state before sending."""
        key = item.get('key') or self._greet_queue_key(item.get('candidate') or {})
        try:
            candidates = load_candidates_all(CANDIDATES_PATH)
        except Exception as exc:
            return None, f"读取最新候选人状态失败：{exc}"
        candidate = next(
            (candidate for candidate in candidates if self._greet_queue_key(candidate) == key),
            None,
        )
        if candidate is None:
            return None, "候选人记录已不存在"
        item['candidate'] = candidate
        return candidate, ""

    @staticmethod
    def _revalidate_greet_queue_candidate(candidate):
        """Map the latest candidate truth to a safe queue action."""
        return contact_presenter.revalidate_greet_queue_candidate(candidate)

    def _greet_queue_candidate_page_ready(self, candidate):
        """Inspect whether a list-page send is on the candidate's job page."""
        ok, current_url, _page_text, reason = self._get_greet_queue_page_state()
        if not ok:
            return False, reason, ""
        if not self._is_boss_recommend_url(current_url):
            return False, f"请打开“{candidate.get('job_name') or '对应'}”岗位推荐页", ""
        from bossmaster import (
            ApiRiskBlocked,
            get_iframe,
            _job_titles_match,
            _read_recommend_page_identity,
        )
        try:
            target = get_iframe(self.browser_page) or self.browser_page
            actual_job = str(
                (_read_recommend_page_identity(target) or {}).get('job_title') or ''
            ).strip()
            matched = _job_titles_match(candidate.get('job_name', ''), actual_job)
        except ApiRiskBlocked:
            raise
        except Exception as exc:
            return False, f"无法确认当前岗位页面：{str(exc)[:60]}", ""
        if matched is True:
            return True, "", actual_job
        if not actual_job:
            return False, f"无法读取当前岗位，请打开“{candidate.get('job_name') or '对应'}”岗位推荐页", ""
        return (
            False,
            f"BOSS 当前岗位“{actual_job}”与本地岗位“{candidate.get('job_name') or '对应岗位'}”名称不同",
            actual_job,
        )

    def _ensure_greet_queue_candidate_page_ready(self, candidate, parent, mismatch_decisions):
        """Allow a list-page send after the user confirms a job-title mismatch."""
        page_ready, page_message, actual_job = self._greet_queue_candidate_page_ready(candidate)
        if page_ready or not actual_job:
            return page_ready, page_message

        expected_job = str(candidate.get('job_name') or '对应岗位').strip()
        decision_key = (expected_job.casefold(), actual_job.casefold())
        if decision_key not in mismatch_decisions:
            mismatch_decisions[decision_key] = self._confirm_job_name_mismatch(
                expected_job,
                actual_job,
                context="contact",
                parent=parent,
            )
        if mismatch_decisions[decision_key]:
            return True, ""
        return False, page_message

    def _run_greet_queue_worker(self, pending=None):
        from bossmaster import (
            ApiRiskBlocked,
            get_boss_access_block_state,
            send_greeting_on_list_page,
            send_greeting_with_context,
        )

        connection_lock_acquired = False
        parent = self.greet_queue_window or self.root
        queue_snapshot = list(
            self.greet_queue_items if pending is None else pending
        )
        counters = ContactRunCounters()
        job_mismatch_decisions = {}
        run_error = ""
        active_item = [None]
        self.append_operation_log(
            f"[联系候选人] 开始发送：待处理 {len(queue_snapshot)} 人"
        )

        def commit_state():
            self._persist_greet_queue()
            self.run_on_ui(self._refresh_greet_queue_dialog)

        def ensure_page_ready(candidate):
            return self._ensure_greet_queue_candidate_page_ready(
                candidate,
                parent,
                job_mismatch_decisions,
            )

        def send_candidate(candidate):
            context = candidate.get("greet_context") or {}
            if self._has_direct_send_context(candidate):
                success, message = send_greeting_with_context(
                    self.browser_page,
                    context,
                    stop_event=self.stop_event,
                    captcha_callback=captcha_callback,
                )
                method = "queue_context"
                if success is False and ("缺少" in message or "字段" in message):
                    page_ready, page_message = ensure_page_ready(candidate)
                    if page_ready:
                        success, message = send_greeting_on_list_page(
                            self.browser_page,
                            candidate.get("geek_id"),
                            stop_event=self.stop_event,
                            captcha_callback=captcha_callback,
                        )
                        method = "queue_list"
                    else:
                        success, message = False, page_message
                return success, message, method
            success, message = send_greeting_on_list_page(
                self.browser_page,
                candidate.get("geek_id"),
                stop_event=self.stop_event,
                captcha_callback=captcha_callback,
            )
            return success, message, "queue_list"

        def classify_send_exception(exc):
            if not isinstance(exc, ApiRiskBlocked):
                return None
            guard_state = get_boss_access_block_state()
            remaining = max(
                1,
                int(guard_state.get("remaining_seconds", 0) + 0.999),
            )
            signal = (
                f"HTTP {exc.status}"
                if isinstance(exc.status, int)
                else str(exc.status)
            )
            message = (
                f"BOSS 访问保护已触发：{signal}，{exc.reason}。"
                f"剩余冷却约 {remaining} 秒"
            )
            return SendExceptionDecision(
                "发送失败",
                message,
                (
                    f"[访问保护] 联系候选人时 BOSS 返回 {signal}：{exc.reason}。"
                    f"已停止后续发送，冷却约 {remaining} 秒。"
                ),
                ContactNotice(
                    "warning",
                    "BOSS 访问保护",
                    "后续发送已停止",
                    message,
                ),
            )

        def refresh_candidate_views():
            self.run_on_ui(self.refresh_results)
            self.run_on_ui(self.refresh_home_stats)

        def show_notice(notice):
            if notice.level == "warning":
                messagebox.showwarning(
                    notice.title,
                    notice.message,
                    parent=parent,
                )
                return
            messagebox.show_notice(
                notice.title,
                headline=notice.headline,
                message=notice.message,
                detail=notice.detail,
                parent=parent,
            )

        try:
            connection_lock_acquired = self._browser_connection_lock.acquire(timeout=8)
            if not connection_lock_acquired:
                run_error = "浏览器正在执行其他操作，请稍后再次发送。"
                self.append_operation_log(f"[联系候选人] {run_error}")
                return
            if not self._ensure_greet_queue_browser(parent, queue_snapshot):
                run_error = getattr(
                    self,
                    "_greet_queue_browser_error",
                    "浏览器未准备好，请检查 Chrome 和 BOSS 登录状态。",
                )
                return

            captcha_callback = self._make_greet_queue_captcha_callback(parent)
            outcome = _CONTACT_CONTROLLER.run_queue(
                self.greet_queue_items,
                queue_snapshot,
                stop_requested=self.stop_event.is_set,
                is_paused=lambda: self.greet_queue_paused,
                reload_candidate=self._reload_greet_queue_candidate,
                revalidate=self._revalidate_greet_queue_candidate,
                has_direct_context=self._has_direct_send_context,
                ensure_page_ready=ensure_page_ready,
                send_candidate=send_candidate,
                classify_send_exception=classify_send_exception,
                persist_pending=lambda candidate, message: (
                    persist_candidate_greeting_pending(
                        candidate,
                        message,
                        CANDIDATES_PATH,
                    )
                ),
                persist_success=self._update_greet_status,
                commit_state=commit_state,
                refresh_results=refresh_candidate_views,
                log=self.append_operation_log,
                uncertain_limit=GREET_UNCERTAIN_LIMIT,
                set_active_item=lambda item: active_item.__setitem__(0, item),
                sleep=time.sleep,
                delay_seconds=lambda: random.uniform(2, 4),
            )
            counters = outcome.counters
            if outcome.pause_requested:
                self.greet_queue_paused = True
            if outcome.notice is not None:
                self.run_on_ui(
                    lambda notice=outcome.notice: show_notice(notice)
                )
        except ApiRiskBlocked as exc:
            guard_state = get_boss_access_block_state()
            remaining = max(
                1,
                int(guard_state.get("remaining_seconds", 0) + 0.999),
            )
            signal = (
                f"HTTP {exc.status}"
                if isinstance(exc.status, int)
                else str(exc.status)
            )
            run_error = (
                f"BOSS 访问保护已触发：{signal}，{exc.reason}。"
                f"剩余冷却约 {remaining} 秒"
            )
            current_item = active_item[0]
            if current_item and current_item.get("status") == "待发送":
                current_item["message"] = run_error
                current_item["updated_at"] = datetime.now().strftime(
                    "%Y%m%d_%H%M%S"
                )
            self.append_operation_log(
                f"[访问保护] 联系候选人准备阶段 BOSS 返回 {signal}：{exc.reason}。"
                f"已停止后续发送，冷却约 {remaining} 秒。"
            )
            self.run_on_ui(
                lambda message=run_error: messagebox.showwarning(
                    "BOSS 访问保护",
                    message,
                    parent=parent,
                )
            )
        except Exception as exc:
            safe_error = self._sanitize_runtime_log_message(exc)
            run_error = f"发送过程出现异常：{safe_error}"
            self.append_operation_log(f"[联系候选人] 异常：{safe_error}")
        finally:
            self.greet_queue_running = False
            self.greet_queue_paused = False
            recovered, failures = _CONTACT_CONTROLLER.finalize_interrupted(
                self.greet_queue_items,
                persist_pending=lambda candidate, message: (
                    persist_candidate_greeting_pending(
                        candidate,
                        message,
                        CANDIDATES_PATH,
                    )
                ),
            )
            counters.pending += recovered
            for _name, failure in failures:
                self.append_operation_log(
                    "[联系候选人] 中断后的待核实状态未能写入候选人记录："
                    f"{self._sanitize_runtime_log_message(failure)}"
                )
            if connection_lock_acquired:
                self._browser_connection_lock.release()
            self._persist_greet_queue()
            self.run_on_ui(self._refresh_greet_queue_dialog)
            self.append_operation_log(
                f"[联系候选人] 完成：成功 {counters.success} 人，"
                f"失败 {counters.failed} 人，待核实 {counters.pending} 人，"
                f"待切换岗位页 {counters.page_waiting} 人，"
                f"已跳过 {counters.skipped} 人"
            )
            feedback = counters.feedback(
                stopped=self.stop_event.is_set(),
                error=run_error,
            )
            self.run_on_ui(
                lambda result=feedback: self._show_greet_queue_run_result(result)
            )
    @staticmethod
    def _build_greet_queue_run_feedback(result):
        return contact_presenter.build_greet_queue_run_feedback(result)

    def _show_greet_queue_run_result(self, result):
        title, headline, message, level = self._build_greet_queue_run_feedback(result)
        show = {
            "info": messagebox.showinfo,
            "warning": messagebox.showwarning,
            "error": messagebox.showerror,
        }[level]
        show(
            title,
            message,
            parent=self.greet_queue_window or self.root,
            headline=headline,
            show_icon=False,
            min_width=620 if message.count("\n") >= 2 else 540,
            content_bottom_padding=28,
        )

    @staticmethod
    def _candidate_identity_key(candidate):
        """Return the persisted candidate identity used by result-page actions."""
        return CandidateController.identity(candidate)

    @staticmethod
    def _format_display_datetime(value, missing="未知"):
        """Format persisted timestamps for compact user-facing display."""
        return candidate_presenter.format_display_datetime(value, missing)

    def _format_candidate_decision_summary(self, candidate):
        """Format the first-screen information needed to make a candidate decision."""
        return candidate_presenter.format_candidate_decision_summary(candidate)

    def _open_candidate_review_workbench(self, candidate, candidates=None):
        """Open or reuse the continuous candidate review workbench."""
        self._candidate_review_uses_result_scope = candidates is None
        candidates = list(
            candidates
            if candidates is not None
            else (getattr(self, 'result_tree_data', []) or [])
        )
        target_key = self._candidate_identity_key(candidate)
        target_index = next(
            (
                index for index, item in enumerate(candidates)
                if item is candidate
                or (
                    target_key[0]
                    and self._candidate_identity_key(item) == target_key
                )
            ),
            None,
        )
        if target_index is None:
            candidates = [candidate]
            target_index = 0
        self._candidate_review_candidates = candidates or [candidate]
        self._candidate_review_index = target_index

        existing = getattr(self, 'candidate_review_window', None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    self._render_candidate_review_workbench()
                    existing.deiconify()
                    existing.lift()
                    return
            except tk.TclError:
                pass

        def close_workbench(window):
            self.candidate_review_window = None
            self._candidate_review_widgets = None
            window.destroy()

        widgets = gui_candidate_review.build_candidate_review_workbench(
            self,
            navigate=self._navigate_candidate_review,
            show_view=self._show_candidate_review_view,
            toggle_view=self._toggle_candidate_review_view,
            close_window=close_workbench,
        )
        self._candidate_review_widgets = widgets
        self.candidate_review_window = widgets.window
        self.candidate_review_title_var = widgets.title_var
        self.candidate_review_meta_var = widgets.meta_var
        self.candidate_review_position_var = widgets.position_var
        self.candidate_review_prev_button = widgets.previous_button
        self.candidate_review_next_button = widgets.next_button
        self.candidate_review_result_var = widgets.result_var
        self.candidate_review_reason_var = widgets.reason_var
        self.candidate_review_communication_var = widgets.communication_var
        self.candidate_review_state_labels = widgets.state_labels
        self.candidate_review_primary_section = widgets.primary_section
        self.candidate_review_primary_label = widgets.primary_label
        self.candidate_review_primary_actions = widgets.primary_actions
        self.candidate_review_secondary_section = widgets.secondary_section
        self.candidate_review_secondary_actions = widgets.secondary_actions
        self.candidate_review_view_buttons = widgets.view_buttons
        self.candidate_review_view_indicators = widgets.view_indicators
        self.candidate_review_view_frames = widgets.view_frames
        self.candidate_review_summary_text = widgets.summary_text
        self.candidate_review_detail_text = widgets.detail_text
        self._show_candidate_review_view('summary')
        self._render_candidate_review_workbench()
        widgets.window.deiconify()

    def _on_greet_queue_motion(self, event):
        """Show full readiness or result text for compact contact-queue columns."""
        tree = self.greet_queue_tree
        item_id = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        if not item_id or column_id not in ("#5", "#7"):
            self.feedback_support.hide_tooltip()
            return
        queue_item = next(
            (item for item in self.greet_queue_items if item.get('queue_id') == item_id),
            None,
        )
        if queue_item is None:
            self.feedback_support.hide_tooltip()
            return
        if column_id == "#5":
            full_text = self._greet_queue_readiness_tooltip(
                queue_item.get('candidate') or {}
            )
        else:
            full_text = str(queue_item.get('message') or "暂无最近结果")
        tooltip_key = ("greet_queue", item_id, column_id)
        if (
            tooltip_key == getattr(self, "_tooltip_item", None)
            and getattr(self, "_tooltip", None)
            and self._tooltip.winfo_exists()
        ):
            return
        self.feedback_support.hide_tooltip()
        self._tooltip_item = tooltip_key
        x = event.x_root + 12
        y = event.y_root + 12
        parent = tree.winfo_toplevel()
        screen_width = max(1, int(tree.winfo_screenwidth()))
        tooltip_wraplength = max(
            360,
            min(
                int(560 * self.dpi_scale * self.zoom_factor),
                int(screen_width * 0.42),
            ),
        )
        self._tooltip_after_id = self.root.after(
            250,
            lambda: self.feedback_support.show_tooltip(
                full_text,
                x,
                y,
                tooltip_key,
                parent=parent,
                wraplength=tooltip_wraplength,
            ),
        )

    def _show_greet_queue_context_menu(self, event):
        """Send only the pending candidates in the right-click selection."""
        tree = self.greet_queue_tree
        item_id = tree.identify_row(event.y)
        if not item_id:
            return
        if item_id not in tree.selection():
            tree.selection_set(item_id)
        tree.focus(item_id)
        self._update_greet_queue_action_states()

        selected = self._selected_greet_queue_items()
        pending_count = sum(
            1 for item in selected if item.get('status') == "待发送"
        )
        context_menu_font = (FONT_FAMILY, int(11 * self.font_scale))
        parent = self.greet_queue_window or self.root
        menu = tk.Menu(parent, tearoff=0, font=context_menu_font)
        icon_detail = self.icons.button(
            'candidate_review', self.colors['primary']
        )
        icon_send = self.icons.button('chat', self.colors['success'])
        menu._icon_refs = [icon_detail, icon_send]
        if len(selected) == 1:
            menu.add_command(
                label=" 查看与复核",
                image=icon_detail,
                compound=tk.LEFT,
                command=self._show_selected_greet_queue_detail,
            )
        send_label = (
            "联系此候选人"
            if len(selected) == 1
            else f"联系选中候选人（{pending_count} 人）"
        )
        menu.add_command(
            label=f" {send_label}",
            image=icon_send,
            compound=tk.LEFT,
            command=self._start_greet_queue,
            state="normal" if pending_count else "disabled",
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _select_all_greet_queue_rows(self, _event=None):
        """Select every candidate currently visible in the contact table."""
        tree = getattr(self, 'greet_queue_tree', None)
        if tree is None or not tree.winfo_exists():
            return "break"
        item_ids = tree.get_children()
        if item_ids:
            tree.selection_set(item_ids)
            tree.focus(item_ids[0])
            tree.see(item_ids[0])
        self._update_greet_queue_action_states()
        return "break"

    def _close_greet_queue_window(self):
        if not self.greet_queue_window or not self.greet_queue_window.winfo_exists():
            return
        if self.greet_queue_running:
            self.greet_queue_window.withdraw()
            return
        self.greet_queue_window.destroy()


    def _show_candidate_review_view(self, view_name):
        """Switch the review content immediately and refresh the flat selected state."""
        frames = getattr(self, 'candidate_review_view_frames', {})
        if view_name not in frames:
            return 'break'
        self._candidate_review_view_name = view_name
        return gui_candidate_review.show_candidate_review_view(
            view_name,
            frames=frames,
            buttons=self.candidate_review_view_buttons,
            indicators=self.candidate_review_view_indicators,
            colors=self.colors,
        )

    def _toggle_candidate_review_view(self):
        """Toggle summary/detail without forcing a distracting focus repaint."""
        current = getattr(self, '_candidate_review_view_name', 'summary')
        return gui_candidate_review.toggle_candidate_review_view(
            current,
            self._show_candidate_review_view,
        )

    @staticmethod
    def _replace_readonly_text(text_widget, text):
        gui_candidate_review.replace_readonly_text(text_widget, text)

    def _render_candidate_review_workbench(self):
        candidates = getattr(self, '_candidate_review_candidates', [])
        if not candidates:
            self.candidate_review_title_var.set("当前范围已处理完成")
            self.candidate_review_meta_var.set("")
            self.candidate_review_position_var.set("0 / 0")
            self.candidate_review_result_var.set("无待处理候选人")
            self.candidate_review_reason_var.set("无")
            self.candidate_review_communication_var.set("无")
            self._replace_readonly_text(
                self.candidate_review_summary_text,
                "当前结果范围内已没有需要继续处理的候选人。",
            )
            self._replace_readonly_text(self.candidate_review_detail_text, "")
            self._clear_candidate_review_actions()
            self.candidate_review_prev_button.configure(state='disabled')
            self.candidate_review_next_button.configure(state='disabled')
            return

        index = max(0, min(self._candidate_review_index, len(candidates) - 1))
        self._candidate_review_index = index
        candidate = candidates[index]
        decision = derive_candidate_decision(candidate)
        name = candidate.get('name') or '未命名候选人'
        job_name = candidate.get('job_name') or '未标记岗位'
        self.candidate_review_title_var.set(f"{name}  ·  {job_name}")
        first_seen = self._format_display_datetime(
            candidate.get('first_seen_at') or candidate.get('batch_timestamp')
        )
        self.candidate_review_meta_var.set(
            f"匹配分 {candidate.get('match_score', 0)}    首次发现 {first_seen}"
        )
        self.candidate_review_position_var.set(f"{index + 1} / {len(candidates)}")
        self.candidate_review_result_var.set(decision.screening_result)
        self.candidate_review_reason_var.set(
            (
                decision.primary_review_reason or "请人工确认"
                if decision.review_status == "pending"
                else {
                    "passed": "复核已通过",
                    "not_passed": "复核未通过",
                    "cancelled": "复核已结束",
                }.get(decision.review_status, "无需复核")
            )
        )
        self.candidate_review_communication_var.set(decision.communication_status)
        result_color = {
            '推荐候选人': self.colors['success'],
            '复核通过': self.colors['success'],
            '待复核': self.colors['warning'],
            '淘汰记录': self.colors['danger'],
        }.get(decision.result_view, self.colors['text_primary'])
        self.candidate_review_state_labels[0].configure(fg=result_color)
        self.candidate_review_state_labels[1].configure(
            fg=(
                self.colors['warning']
                if decision.review_status == "pending"
                else (
                    self.colors['danger']
                    if decision.review_status in {"not_passed", "cancelled"}
                    else self.colors['success']
                )
            )
        )
        self.candidate_review_state_labels[2].configure(fg=self.colors['primary'])

        self._replace_readonly_text(
            self.candidate_review_summary_text,
            self._format_candidate_decision_summary(candidate),
        )
        self._replace_readonly_text(
            self.candidate_review_detail_text,
            self._format_candidate_detail(candidate),
        )
        self.candidate_review_prev_button.configure(
            state='normal' if index > 0 else 'disabled'
        )
        self.candidate_review_next_button.configure(
            state='normal' if index < len(candidates) - 1 else 'disabled'
        )
        self._render_candidate_review_actions(candidate, decision)

    def _clear_candidate_review_actions(self):
        for frame in (
            self.candidate_review_primary_actions,
            self.candidate_review_secondary_actions,
        ):
            for child in frame.winfo_children():
                child.destroy()

    def _add_candidate_review_action(
        self, parent, text, icon_name, color, command
    ):
        icon = self.icons.button(icon_name, color)
        options = {
            'image': icon,
            'text': f" {text}",
            'compound': tk.LEFT,
            'command': command,
        }
        button = ttk.Button(parent, **options)
        button._icon_ref = icon
        button.pack(side='left', padx=(0, int(7 * self.dpi_scale)))
        return button

    def _render_candidate_review_actions(self, candidate, decision):
        self._clear_candidate_review_actions()
        key = self._candidate_identity_key(candidate)
        on_saved = lambda: self._candidate_review_action_saved(key)
        active_queue_item = self._greet_queue_item_for_candidate(
            candidate, active_only=True
        )
        binary_review_actions = bool(
            decision.review_status == "pending"
            and not candidate.get('greet_confirmation_pending')
            and (
                candidate.get('manual_review_required')
                or candidate.get('qualification_status') == 'manual_review'
            )
        )
        primary_label = getattr(self, 'candidate_review_primary_label', None)
        if primary_label is not None:
            primary_label.configure(
                text="复核结论" if binary_review_actions else "建议下一步"
            )
        primary_action = ""

        if binary_review_actions:
            primary_action = "review_decision"
            self._add_candidate_review_action(
                self.candidate_review_primary_actions,
                "确认通过",
                'stamp_check',
                self.colors['success'],
                lambda: self._confirm_manual_review(
                    None,
                    candidate=candidate,
                    parent=self.candidate_review_window,
                    on_saved=on_saved,
                ),
            )
            self._add_candidate_review_action(
                self.candidate_review_primary_actions,
                "确认不通过",
                'close',
                self.colors['danger'],
                lambda: self._confirm_review_rejection(
                    None,
                    candidate=candidate,
                    parent=self.candidate_review_window,
                    on_saved=on_saved,
                ),
            )
        elif candidate.get('greet_confirmation_pending'):
            primary_action = "verify_sent"
            self._add_candidate_review_action(
                self.candidate_review_primary_actions,
                "核实发送结果",
                'stamp_check',
                self.colors['warning'],
                lambda: self._focus_candidate_in_greet_queue(candidate),
            )
        elif candidate_can_manual_approve_contact(candidate):
            primary_action = "approve_contact"
            self._add_candidate_review_action(
                self.candidate_review_primary_actions,
                "确认并加入联系清单",
                'chat',
                self.colors['success'],
                lambda: self._approve_candidate_contact_and_queue(
                    candidate,
                    parent=self.candidate_review_window,
                    on_saved=on_saved,
                ),
            )
        elif active_queue_item is not None:
            primary_action = "contact_queue"
            self._add_candidate_review_action(
                self.candidate_review_primary_actions,
                "查看联系清单",
                'chat',
                self.colors['primary'],
                lambda: self._focus_candidate_in_greet_queue(candidate),
            )
        elif not candidate_greet_skip_reason(candidate):
            primary_action = "greet"
            self._add_candidate_review_action(
                self.candidate_review_primary_actions,
                "加入联系清单",
                'chat',
                self.colors['success'],
                lambda: self._add_candidate_to_greet_queue_from_review(
                    candidate, on_saved
                ),
            )
        elif candidate.get('greet_sent'):
            primary_action = "followup"
            self._add_candidate_review_action(
                self.candidate_review_primary_actions,
                "更新跟进",
                'pencil',
                self.colors['primary'],
                lambda: self._mark_candidate_followup(
                    None,
                    candidate=candidate,
                    parent=self.candidate_review_window,
                    on_saved=on_saved,
                ),
            )
        else:
            primary_action = "feedback"
            self._add_candidate_review_action(
                self.candidate_review_primary_actions,
                "标记反馈",
                'check',
                self.colors['primary'],
                lambda: self._mark_candidate_feedback(
                    None,
                    candidate=candidate,
                    parent=self.candidate_review_window,
                    on_saved=on_saved,
                ),
            )

        if decision.review_status == "pending" and not binary_review_actions:
            self._add_candidate_review_action(
                self.candidate_review_secondary_actions,
                "确认不通过",
                'close',
                self.colors['danger'],
                lambda: self._confirm_review_rejection(
                    None,
                    candidate=candidate,
                    parent=self.candidate_review_window,
                    on_saved=on_saved,
                ),
            )
        if primary_action != "followup":
            self._add_candidate_review_action(
                self.candidate_review_secondary_actions,
                "更新跟进",
                'pencil',
                self.colors['primary'],
                lambda: self._mark_candidate_followup(
                    None,
                    candidate=candidate,
                    parent=self.candidate_review_window,
                    on_saved=on_saved,
                ),
            )
        if primary_action != "feedback":
            self._add_candidate_review_action(
                self.candidate_review_secondary_actions,
                "标记反馈",
                'check',
                self.colors['primary'],
                lambda: self._mark_candidate_feedback(
                    None,
                    candidate=candidate,
                    parent=self.candidate_review_window,
                    on_saved=on_saved,
                ),
            )
        self._add_candidate_review_action(
            self.candidate_review_secondary_actions,
            "导入简历",
            'document',
            self.colors['primary'],
            lambda: self._import_resume(
                None,
                candidate=candidate,
                parent=self.candidate_review_window,
            ),
        )
        if not _candidate_has_ai_eval(candidate):
            self._add_candidate_review_action(
                self.candidate_review_secondary_actions,
                "AI 评估",
                'ai_spark',
                self.colors['primary'],
                lambda: self._ai_eval_selected_candidates([candidate]),
            )

    def _navigate_candidate_review(self, offset):
        candidates = getattr(self, '_candidate_review_candidates', [])
        if not candidates:
            return
        target = self._candidate_review_index + offset
        if 0 <= target < len(candidates):
            self._candidate_review_index = target
            self._render_candidate_review_workbench()

    def _candidate_review_action_saved(self, current_key):
        """Refresh persisted data while keeping the current candidate selected."""
        old_index = getattr(self, '_candidate_review_index', 0)
        session_candidates = list(
            getattr(self, '_candidate_review_candidates', []) or []
        )
        self.refresh_results(force=True)
        try:
            persisted_candidates = load_candidates_all(CANDIDATES_PATH)
        except Exception as exc:
            logger.warning("刷新候选人复核工作台数据失败：%s", exc)
            persisted_candidates = []
        persisted_by_key = {
            self._candidate_identity_key(candidate): candidate
            for candidate in persisted_candidates
            if self._candidate_identity_key(candidate)[0]
        }
        candidates = [
            persisted_by_key.get(self._candidate_identity_key(candidate), candidate)
            for candidate in session_candidates
        ]
        self._candidate_review_candidates = candidates
        if not candidates:
            self._candidate_review_index = 0
            self._render_candidate_review_workbench()
            return

        current_index = next(
            (
                index for index, candidate in enumerate(candidates)
                if self._candidate_identity_key(candidate) == current_key
            ),
            None,
        )
        if current_index is None:
            self._candidate_review_index = min(old_index, len(candidates) - 1)
        else:
            self._candidate_review_index = current_index
        self._render_candidate_review_workbench()

    def _show_candidate_detail(self, item):
        """打开候选人查看与复核工作台。"""
        try:
            candidate = self._find_candidate_by_tree_item(item)
            if not candidate:
                return
            self._open_candidate_review_workbench(candidate)

        except Exception as e:
            messagebox.showerror("错误", f"打开查看与复核失败：{e}")




    def _update_greet_status(self, candidate, method) -> bool:
        """更新 candidates_all.json 中指定候选人的打招呼状态"""
        try:
            return persist_candidate_greeted(candidate, method, CANDIDATES_PATH)
        except Exception as e:
            self.append_log(f"[打招呼] 更新状态失败：{e}")
            return False

    def _regenerate_excel(self):
        """打招呼后同步更新 Excel 文件（静默，不弹窗）"""
        try:
            if not CANDIDATES_PATH.exists():
                return
            from bossmaster import export_to_excel
            candidates = load_candidates_all(CANDIDATES_PATH)
            export_to_excel(candidates, str(CANDIDATES_XLSX_PATH))
        except Exception as e:
            self.append_log(f"[Excel] 同步更新失败：{e}")

    def _remove_candidate_records(self, predicate) -> int:
        """Remove records and reclaim only managed resumes no longer referenced."""
        removed, cleanup = _candidate_controller_for(self).remove_records(predicate)
        if cleanup.failure_count:
            messagebox.showwarning(
                "简历副本未完全清理",
                (
                    f"候选人记录已更新，但有 {cleanup.failure_count} 项"
                    "受管简历清理失败，可稍后运行简历存储体检。"
                ),
                parent=self.root,
            )
        return removed

    def _remove_candidate(self, item):
        """移除选中候选人"""
        candidate = self._find_candidate_by_tree_item(item)
        name = candidate.get('name', '该候选人') if candidate else '该候选人'
        target_geek_id = candidate.get('geek_id') if candidate else None
        target_job_name = candidate.get('job_name', '') if candidate else ''
        if not target_geek_id:
            messagebox.showerror("错误", f"未找到候选人：{name}")
            return
        if not messagebox.ask_confirmation(
            "移除候选人",
            headline=f"移除 {name}？",
            message="该记录将从当前结果和本地候选人数据中移除。",
            notice=(
                "无人继续引用的受管简历副本也会删除，共享副本保留；"
                "重新扫描时仍可能再次发现该候选人。"
            ),
            yes_label="移除候选人",
            no_label="取消",
            dangerous=True,
            parent=self.root,
        ):
            return

        try:
            # 同一候选人可能出现在多个岗位，只移除当前岗位记录。
            if hasattr(self, 'result_tree_data'):
                self.result_tree_data = [
                    c for c in self.result_tree_data
                    if not (
                        c.get('geek_id') == target_geek_id
                        and normalize_job_name(c.get('job_name'))
                        == normalize_job_name(target_job_name)
                    )
                ]

            # 从 JSON 文件中移除
            if CANDIDATES_PATH.exists():
                self._remove_candidate_records(
                    lambda persisted: (
                        persisted.get('geek_id') == target_geek_id
                        and normalize_job_name(persisted.get('job_name'))
                        == normalize_job_name(target_job_name)
                    ),
                )

                # 从树中移除
                self.result_tree.delete(item)

                # 刷新统计
                self.refresh_results()

                self._status_flash(f"已移除：{name}")
        except Exception as e:
            messagebox.showerror("错误", f"移除失败：{e}")

    def _run_export(self, candidates, file_path, preserve_input=False):
        """后台线程写 Excel，避免大数据量导出冻结界面；完成 toast 反馈，失败弹窗。"""
        from bossmaster import export_to_excel
        count = len(candidates)
        if hasattr(self, 'status_bar_left_var'):
            self.status_bar_left_var.set(f"正在导出 {count} 名候选人…")

        def worker():
            try:
                export_to_excel(candidates, file_path, preserve_input=preserve_input)
            except Exception as exc:
                error_message = f"导出失败：{exc}"

                def show_error(message=error_message):
                    if hasattr(self, 'status_bar_left_var'):
                        self.status_bar_left_var.set("")
                    messagebox.showerror("错误", message, parent=self.root)
                self.root.after(0, show_error)
                return

            def notify_success():
                if hasattr(self, 'status_bar_left_var'):
                    self.status_bar_left_var.set("")
                self.append_log(f"已导出 {count} 名候选人：{file_path}")
                self._status_flash(f"已导出 {count} 名候选人")
            self.root.after(0, notify_success)

        threading.Thread(target=worker, daemon=True).start()

    def _export_selected(self):
        """导出选中的候选人"""
        selection = self.result_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择要导出的候选人")
            return

        # 获取选中项的数据
        selected_data = []
        for item in selection:
            candidate = self._find_candidate_by_tree_item(item)
            if candidate:
                selected_data.append(candidate)

        if not selected_data:
            return

        # 导出到 Excel
        if len(selected_data) == 1:
            init_name = f"{selected_data[0].get('name', '候选人')}.xlsx"
        else:
            init_name = f"{selected_data[0].get('name', '候选人')}等{len(selected_data)}人_{datetime.now().strftime('%Y%m%d')}.xlsx"
        file_path = filedialog.asksaveasfilename(
            title="保存选中的候选人",
            defaultextension=".xlsx",
            filetypes=[("Excel 文件", "*.xlsx")],
            initialfile=init_name
        )

        if file_path:
            self._run_export(selected_data, file_path)

    def export_excel(self):
        """Export the candidates currently visible in the result table."""
        try:
            self.refresh_results(force=True)
            candidates = []
            for item in self.result_tree.get_children():
                candidate = self._find_candidate_by_tree_item(item)
                if candidate:
                    candidates.append(candidate)
            if not candidates:
                messagebox.showwarning("警告", "当前筛选范围没有可导出的候选人")
                return

            job_name = self.result_job_var.get() if hasattr(self, 'result_job_var') else "全部岗位"
            result_view = self.result_view_var.get() if hasattr(self, 'result_view_var') else "全部记录"
            start_str, end_str = self._get_result_date_filter() if hasattr(self, 'result_date_start_entry') else (None, None)
            if start_str and end_str:
                date_part = f"{start_str}_{end_str}"
            elif start_str:
                date_part = f"{start_str}起"
            elif end_str:
                date_part = f"至{end_str}"
            else:
                date_part = "全部时间"

            file_path = filedialog.asksaveasfilename(
                title="保存 Excel 文件",
                defaultextension=".xlsx",
                filetypes=[("Excel 文件", "*.xlsx")],
                initialfile=f"{job_name}_{result_view}_{date_part}.xlsx"
            )

            if file_path:
                self._run_export(candidates, file_path, preserve_input=True)
        except Exception as e:
            messagebox.showerror("错误", f"导出失败：{e}")


    def _clear_candidates_from_dialog(
        self,
        choice,
        keep_greeted,
        selected_job,
    ):
        """Apply a confirmed candidate-data cleanup request."""
        try:
            backup_path = CANDIDATES_PATH.with_suffix(".json.bak")
            cleanup = _DATA_MAINTENANCE_CONTROLLER.clear_candidates(
                scope=choice,
                selected_job=selected_job,
                keep_greeted=keep_greeted,
                candidates_path=CANDIDATES_PATH,
                base_dir=BASE_DIR,
                mutate_with_resume_cleanup=mutate_candidates_with_resume_cleanup,
                clear_in_place=clear_candidates_in_place,
            )
            removed = cleanup.removed_count
            kept_count = cleanup.greeted_kept_count
            blacklist_kept_count = cleanup.blacklist_kept_count
            resume_cleanup = cleanup.resume_cleanup
            if removed:
                self.append_log(
                    f"已备份候选人数据到 {backup_path.name}"
                )

            log_message = cleanup.message(choice, selected_job)
            result_message = (
                f"已清空 {removed} 条候选人数据"
                if choice == "current"
                else f"已清空全部 {removed} 条候选人数据"
            )
            if kept_count:
                result_message += f"，保留 {kept_count} 条已打招呼记录"
            if blacklist_kept_count:
                result_message += f"，保留 {blacklist_kept_count} 条黑名单记录"
            self.append_log(log_message)
            messagebox.show_result(
                "清空候选人",
                headline="候选人数据已清理",
                message=result_message,
                metrics=(
                    ("已清理", f"{removed} 条"),
                    ("已打招呼保留", f"{kept_count} 条"),
                    ("黑名单保留", f"{blacklist_kept_count} 条"),
                    (
                        "简历副本清理",
                        f"{resume_cleanup.deleted_file_count} 个 / "
                        f"{_format_storage_bytes(resume_cleanup.reclaimed_bytes)}",
                    ),
                ),
                notice=(
                    f"候选人数据备份已保存为 {backup_path.name}。"
                    + (
                        f"另有 {resume_cleanup.failure_count} 项受管简历清理失败，"
                        "可稍后运行存储体检。"
                        if resume_cleanup.failure_count
                        else "已删除的无人引用简历副本不包含在 JSON 备份中。"
                    )
                ),
                notice_kind=(
                    "warning" if resume_cleanup.failure_count else "success"
                ),
                parent=self.root,
            )
            self._regenerate_excel()
            self.refresh_results()
            self.refresh_home_stats()
            self.refresh_stats()
        except Exception as exc:
            messagebox.show_failure(
                "清空候选人",
                headline="候选人数据未清理",
                message="原有候选人数据保持不变。",
                detail=str(exc),
                parent=self.root,
            )

    def clear_candidates(self):
        """Show candidate cleanup choices and delegate confirmed persistence."""
        if not CANDIDATES_PATH.exists():
            self.feedback_support.show_inline_banner(
                self.result_page,
                "info",
                "暂无候选人数据。",
            )
            return

        selected_job = (
            self.result_job_var.get()
            if hasattr(self, "result_job_var")
            else "全部岗位"
        )
        is_all_jobs = selected_job == "全部岗位"
        greeted_count = 0
        try:
            candidates = load_candidates_all(CANDIDATES_PATH)
            greeted_count = _DATA_MAINTENANCE_CONTROLLER.count_greeted(
                candidates,
                selected_job,
            )
        except (OSError, RuntimeError):
            pass

        gui_data_maintenance_dialogs.show_clear_candidates_dialog(
            self,
            self.root,
            font_family=FONT_FAMILY,
            selected_job=selected_job,
            is_all_jobs=is_all_jobs,
            greeted_count=greeted_count,
            on_confirm=lambda choice, keep_greeted: (
                self._clear_candidates_from_dialog(
                    choice,
                    keep_greeted,
                    selected_job,
                )
            ),
        )


    def show_about(self):
        """显示关于弹窗"""
        gui_dialogs.show_about_dialog(self, __version__)

    def show_changelog(self):
        """显示更新日志（版本列表 + 详情分栏）"""
        gui_dialogs.show_changelog_dialog(self, __version__)


def main():
    if (
        sys.platform == "win32"
        and len(sys.argv) == 3
        and sys.argv[1] == "--apply-windows-update"
    ):
        import updater

        updater.run_windows_update_helper(sys.argv[2])
        return

    _enable_high_dpi_awareness()
    startup_monitor_area = _get_windows_monitor_area()
    root = tk.Tk()

    # 先隐藏窗口
    root.withdraw()

    # 创建应用（会初始化界面）
    app = BossFilterGUI(root)

    # 显示窗口前后复位，避免启动首帧偏移闪烁。
    _show_main_window_centered(root, startup_monitor_area)

    root.mainloop()


if __name__ == "__main__":
    main()
