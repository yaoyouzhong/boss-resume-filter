"""
BOSS 简历筛选器 - 图形界面版本
优化：浏览器状态检测 + 进度条 + 数据安全性 + UI 细节增强
"""

__version__ = "2.16"

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
import socket
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk
from urllib.parse import urlparse

import icons

logger = logging.getLogger(__name__)
from constants import (
    API_CANDIDATE_LIMIT_DEFAULT,
    SCORE_THRESHOLD_PASS,
    SCORE_THRESHOLD_RECOMMEND,
    SCORE_THRESHOLD_STRONG,
    USER_AGENT,
    GREET_UNCERTAIN_LIMIT,
)
from storage import (
    mark_candidate_greeted,
    persist_candidate_greeted,
    persist_candidate_greeting_pending,
    save_candidates_all,
)
import gui_dialogs

# ========== 路径常量 - 解决相对路径问题 ==========
# PyInstaller --onefile 模式下 __file__ 指向临时解压目录，需特殊处理
from paths import BASE_DIR, get_base_dir, ensure_config_files

CONFIG_PATH = BASE_DIR / "job_config.json"
CANDIDATES_PATH = BASE_DIR / "candidates_all.json"
CANDIDATES_XLSX_PATH = BASE_DIR / "candidates_all.xlsx"
CONFIG_BACKUP_PATH = BASE_DIR / "job_config.json.bak"
API_CONFIG_PATH = BASE_DIR / "api_config.json"
CHROME_DEBUG_PORT_FILE = BASE_DIR / ".chrome_debug_port"

FEEDBACK_STATUS_OPTIONS = ["合适", "误推", "误杀", "放弃"]
FOLLOWUP_STATUS_OPTIONS = ["未沟通", "已打招呼", "已回复", "待约面", "已约面", "不合适", "已归档"]

# 服务商显示名称映射（内部键 -> 显示名称）
PROVIDER_DISPLAY = {
    "qwen": "通义千问 (Qwen)",
    "deepseek": "DeepSeek",
    "kimi": "Kimi (月之暗面)",
    "zhipu": "智谱 (Zhipu)",
    "minimax": "MiniMax",
    "xiaomi": "小米 (Xiaomi)",
    "stepfun": "阶跃星辰 (StepFun)",
    "openai": "OpenAI",
    "anthropic": "Anthropic (Claude)",
    "custom": "自定义 (Custom)"
}
DISPLAY_TO_KEY = {v: k for k, v in PROVIDER_DISPLAY.items()}

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


# _resolve_rule_score 已挪到 llm_eval（evaluate_batch 与撤回流程共用，统一规则分还原逻辑）


def get_font_family():
    """获取字体 - 支持跨平台降级"""
    # 优先使用微软雅黑，macOS/Linux 降级到系统字体
    import sys
    if sys.platform == 'win32':
        return 'Microsoft YaHei UI'
    elif sys.platform == 'darwin':
        return 'PingFang SC'
    else:
        return 'Helvetica'


def get_font_family_semibold():
    """获取 Semibold 字体变体 - 支持跨平台降级"""
    import sys
    if sys.platform == 'win32':
        return 'Microsoft YaHei UI Semibold'
    elif sys.platform == 'darwin':
        return 'PingFang SC'  # macOS 无独立 Semibold 变体，配合 'bold' 使用
    else:
        return 'Helvetica'


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
    'entry_width_api_key': 55,       # API Key Entry 宽度
    'entry_width_url': 55,           # Base URL Entry 宽度
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


def _clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


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


def _get_windows_monitor_area(window=None, parent=None):
    """返回当前相关显示器工作区 (left, top, width, height)。"""
    if sys.platform != 'win32':
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL

        monitor = None
        if parent is not None:
            parent.update_idletasks()
            point = wintypes.POINT(
                parent.winfo_rootx() + parent.winfo_width() // 2,
                parent.winfo_rooty() + parent.winfo_height() // 2,
            )
            monitor = user32.MonitorFromPoint(point, 2)  # MONITOR_DEFAULTTONEAREST
        else:
            point = wintypes.POINT()
            if user32.GetCursorPos(ctypes.byref(point)):
                monitor = user32.MonitorFromPoint(point, 2)
            if not monitor and window is not None:
                monitor = user32.MonitorFromWindow(window.winfo_id(), 2)

        if not monitor:
            return None

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None

        work = info.rcWork
        return work.left, work.top, work.right - work.left, work.bottom - work.top
    except (ImportError, OSError, AttributeError, tk.TclError):
        return None


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


def _place_window_centered(
    window,
    width=None,
    height=None,
    parent=None,
    screen_width=None,
    screen_height=None,
    screen_left=None,
    screen_top=None,
    max_width_ratio=0.9,
    max_height_ratio=0.85,
):
    """居中放置窗口，并把最终位置夹在屏幕可见范围内。"""
    if parent is not None:
        parent.update_idletasks()
    window.update_idletasks()

    current_width = int(window.winfo_width() or 0)
    current_height = int(window.winfo_height() or 0)
    req_width = int(window.winfo_reqwidth() or 0)
    req_height = int(window.winfo_reqheight() or 0)
    width = int(width or (current_width if current_width > 1 else req_width))
    height = int(height or (current_height if current_height > 1 else req_height))
    monitor_area = None
    if screen_width is None or screen_height is None:
        monitor_area = _get_windows_monitor_area(window, parent)

    if monitor_area is not None:
        screen_left, screen_top, screen_width, screen_height = monitor_area
    else:
        screen_left = int(screen_left or 0)
        screen_top = int(screen_top or 0)
        screen_width = int(screen_width or window.winfo_screenwidth())
        screen_height = int(screen_height or window.winfo_screenheight())

    if width > screen_width:
        width = max(1, int(screen_width * max_width_ratio))
    if height > screen_height:
        height = max(1, int(screen_height * max_height_ratio))

    if parent is not None:
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        x = parent_x + (parent_width - width) // 2
        y = parent_y + (parent_height - height) // 2
        y -= _get_parent_titlebar_center_offset(parent)
    else:
        x = screen_left + (screen_width - width) // 2
        y = screen_top + (screen_height - height) // 2

    min_x = screen_left
    min_y = screen_top
    max_x = screen_left + max(0, screen_width - width)
    max_y = screen_top + max(0, screen_height - height)
    x = min(max(min_x, x), max_x)
    y = min(max(min_y, y), max_y)
    window.geometry(f"{width}x{height}+{x}+{y}")
    if parent is not None:
        _bind_parent_center_correction(window, parent, width, height, screen_left, screen_top, screen_width, screen_height)
    return width, height, x, y


def _get_parent_titlebar_center_offset(parent):
    """估算父窗口标题栏导致的视觉中心下偏，只修正纵向中心。"""
    try:
        titlebar_height = int(parent.winfo_rooty()) - int(parent.winfo_y())
    except (tk.TclError, AttributeError, TypeError, ValueError):
        return 0
    if titlebar_height <= 0 or titlebar_height > 120:
        return 0
    return titlebar_height // 2


def _bind_parent_center_correction(window, parent, width, height, screen_left, screen_top, screen_width, screen_height):
    """窗口显示后用 Tk 实际坐标再校正一次父子中心。"""
    try:
        if getattr(window, "_parent_center_correction_bound", False):
            return
        window._parent_center_correction_bound = True

        def correct_once(event=None):
            try:
                window.unbind("<Map>", getattr(window, "_parent_center_correction_bind_id", ""))
            except tk.TclError:
                pass
            try:
                parent.update_idletasks()
                window.update_idletasks()
                parent_center_x = parent.winfo_rootx() + parent.winfo_width() // 2
                parent_center_y = (
                    parent.winfo_rooty()
                    + parent.winfo_height() // 2
                    - _get_parent_titlebar_center_offset(parent)
                )
                window_center_x = window.winfo_rootx() + window.winfo_width() // 2
                window_center_y = window.winfo_rooty() + window.winfo_height() // 2
                dx = parent_center_x - window_center_x
                dy = parent_center_y - window_center_y
                if abs(dx) < 1 and abs(dy) < 1:
                    return
                new_x = window.winfo_rootx() + dx
                new_y = window.winfo_rooty() + dy
                max_x = screen_left + max(0, screen_width - width)
                max_y = screen_top + max(0, screen_height - height)
                new_x = min(max(screen_left, new_x), max_x)
                new_y = min(max(screen_top, new_y), max_y)
                window.geometry(f"{width}x{height}+{int(new_x)}+{int(new_y)}")
            except (tk.TclError, AttributeError):
                return

        bind_id = window.bind("<Map>", correct_once, add="+")
        window._parent_center_correction_bind_id = bind_id
        window.after(50, correct_once)
    except (tk.TclError, AttributeError):
        return


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

def _draw_search_icon(S, fill, sw_ratio=0.10):
    """在 S×S 画布上绘制放大镜图标（🔍 风格），返回 RGBA Image"""
    from PIL import Image, ImageDraw
    import math
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 镜片
    rim_color = '#4B5563'      # 金属边框
    glass_fill = (147, 197, 253, 80)  # 淡蓝玻璃
    rim_w = max(3, int(S * 0.07))
    r = int(S * 0.24)
    cx, cy = int(S * 0.33), int(S * 0.33)
    # 镜片玻璃底色
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=glass_fill)
    # 金属边框
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=rim_color, width=rim_w)
    # 反光斜线（白色）
    shine_color = (255, 255, 255, 140)
    shine_w = max(2, int(S * 0.025))
    shine_y = int(cy - r * 0.4)
    shine_len = int(r * 1.1)
    angle = math.radians(-30)
    sx1 = int(cx - shine_len * math.cos(angle))
    sy1 = int(shine_y - shine_len * math.sin(angle))
    sx2 = int(cx + shine_len * math.cos(angle))
    sy2 = int(shine_y + shine_len * math.sin(angle))
    d.line([(sx1, sy1), (sx2, sy2)], fill=shine_color, width=shine_w)
    # 手柄
    handle_color = '#374151'
    handle_w = max(3, int(S * 0.07))
    angle45 = math.radians(45)
    hx0 = int(cx + (r + rim_w // 2) * math.cos(angle45))
    hy0 = int(cy + (r + rim_w // 2) * math.sin(angle45))
    handle_len = int(S * 0.42)
    hx1 = int(hx0 + handle_len * math.cos(angle45))
    hy1 = int(hy0 + handle_len * math.sin(angle45))
    d.line([(hx0, hy0), (hx1, hy1)], fill=handle_color, width=handle_w)
    # 手柄圆头
    cap_r = handle_w // 2
    d.ellipse([hx1 - cap_r, hy1 - cap_r, hx1 + cap_r, hy1 + cap_r], fill=handle_color)
    return img


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
        self.log_queue = queue.Queue()
        self.progress_queue = queue.Queue()  # 进度条队列
        self.confirm_queue = queue.Queue()  # 岗位切换确认队列
        self.ui_queue = queue.Queue()  # UI 更新队列（线程安全）
        self.stop_event = threading.Event()  # 停止信号

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
        if standalone_education:
            self.api_config = dict(education_api_config or {})
        else:
            self.load_config()
            # 首屏启动只读 api_config.json，不同步查询 keyring。
            # keyring 初始化在 Windows 上可能耗时明显，等用户进入模型配置或真正运行时再按需读取。
            self.load_api_config(resolve_keys=False)

        # 缓存：job_config 读取（mtime 未变则跳过磁盘 IO）
        self._job_rules_cache = None
        self._job_rules_mtime = 0
        # 缓存：Treeview 刷新（数据未变则跳过重建）
        self._result_tree_fingerprint = None
        self._result_last_job = None
        self._result_last_dates = None
        self._result_last_show_blacklist = False
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
        self._pending_idle_tasks = set()
        self._page_width_policy_after_id = None

        # 设置样式
        self.setup_styles()

        # 创建进度状态图标（依赖 self.colors，必须在 setup_styles 之后）
        self._create_status_icons()

        # 创建界面
        if standalone_education:
            self.create_education_main_content()
        else:
            self.create_sidebar()
            self.create_main_content()

        # 启动日志更新
        if not standalone_education:
            self.update_log()

        # 启动 UI 更新队列处理（线程安全）
        self._process_ui_queue()

        # 结果页数据等用户进入结果页时再加载，避免启动时导入自动化链路。

        # 注册窗口关闭处理
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._setup_macos_reopen_handler()

        # 标记鼠标是否在 Text 控件上（用于 Cocoa scroll hook 跳过页面滚动）
        self._over_text_widget = False

        # 统一绑定滚轮事件 - 根据当前页面分发到对应的 Canvas
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        # macOS/Linux 触控板可能生成 Button-4/5 事件
        if sys.platform != 'win32':
            self.root.bind_all("<Button-4>", self._on_mousewheel)
            self.root.bind_all("<Button-5>", self._on_mousewheel)

        # macOS Tk 9.0+: Cocoa 层拦截触控板滚动事件并转发给 Tk
        if _NEED_COCOA_SCROLL_HOOK:
            self.root.after(500, self._setup_cocoa_scroll_hook)

        # 更新模块含 requests 等重型依赖，延迟并在后台导入，避免阻塞 GUI 冷启动。
        if not standalone_education:
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

    def setup_styles(self):
        """设置自定义样式"""
        style = ttk.Style()

        # 尝试使用现代主题
        try:
            style.theme_use('vista')
        except tk.TclError:
            try:
                style.theme_use('clam')
            except tk.TclError:
                pass  # 使用默认主题

        # 配色方案 - 现代化渐变色
        self.colors = {
            'primary': '#1E88E5',      # 主蓝色
            'primary_dark': '#1565C0',
            'primary_light': '#64B5F6',
            'success': '#43A047',       # 成功绿
            'success_light': '#81C784',
            'warning': '#FB8C00',       # 警告橙
            'danger': '#E53935',        # 危险红
            'purple': '#8E24AA',        # 紫色
            'pending': '#546E7A',       # 待定蓝灰色
            'bg_main': '#F8F9FA',       # 主背景
            'bg_card': '#FFFFFF',       # 卡片背景
            'bg_input': '#FAFAFA',      # 输入框背景
            'bg_sidebar': '#2D3748',    # 侧边栏背景
            'bg_tree_tag_high': '#E8F5E9',   # 表格高权重行
            'bg_tree_tag_mid': '#FFF3E0',    # 表格中权重行
            'bg_tree_tag_low': '#F5F5F5',    # 表格低权重行
            'text_primary': '#1A202C',  # 主文字
            'text_secondary': '#718096',# 次要文字
            'text_muted': '#999999',    # 弱化文字
            'text_sidebar': '#A0AEC0',  # 侧边栏文字
            'text_sidebar_active': '#FFFFFF',      # 侧边栏激活文字
            'text_sidebar_subtitle': '#94A3B8',    # 侧边栏副标题
            'text_sidebar_version': '#64748B',     # 侧边栏版本号
            'border': '#E2E8F0',        # 边框
            'bg_hover': '#EDF2F7',      # 悬停背景
        }

        # 设置右侧功能页字体。左侧边栏在 create_sidebar() 中单独计算，避免被这里牵动。
        fs = self.dpi_scale * self.zoom_factor
        page_fs = fs * 0.92 * self.font_boost
        self.font_title = (FONT_FAMILY, int(28 * page_fs))
        self.font_section = (FONT_FAMILY, int(16 * page_fs))
        self.font_label = (FONT_FAMILY, int(13 * page_fs))  # 通用 UI 字体（表单标签、按钮、下拉框、副标题）
        self.font_stat = (FONT_FAMILY, int(36 * page_fs))
        self.font_stat_label = (FONT_FAMILY, int(15 * page_fs))
        self.font_log = (FONT_FAMILY, int(11 * page_fs))
        self.font_table = (FONT_FAMILY, int(12 * page_fs))  # 表格字体

        # 设置 Combobox 下拉列表字体（与 font_label 保持一致）
        # 必须用元组格式 + priority 80，确保 Tk option database 正确解析并覆盖默认值
        self.root.option_add('*TCombobox*Listbox.font', self.font_label, 80)

        # 禁用所有 Combobox 的鼠标滚轮（防止误触改变选中值）
        self.root.bind_class('TCombobox', '<MouseWheel>', lambda e: 'break')
        self.root.bind_class('TCombobox', '<Button-4>', lambda e: 'break')
        self.root.bind_class('TCombobox', '<Button-5>', lambda e: 'break')

        # 配置样式
        style.configure('TFrame', background=self.colors['bg_card'])
        style.configure('Page.TFrame', background=self.colors['bg_main'])
        style.configure('TLabel', font=self.font_label, foreground=self.colors['text_primary'],
                        background=self.colors['bg_card'])
        style.configure('TButton', font=self.font_label, padding=(15, 8))
        style.configure('Accent.TButton', font=(FONT_FAMILY_SEMIBOLD, int(13 * page_fs)), padding=(20, 8))
        style.configure('Card.TFrame', background=self.colors['bg_card'], relief='solid', borderwidth=1)
        style.configure('WelcomeCard.TFrame', background=self.colors['bg_card'],
                        relief='flat', borderwidth=0)
        style.configure('WelcomeInner.TFrame', background=self.colors['bg_card'])
        style.configure('PageHeader.TFrame', background=self.colors['bg_card'],
                        relief='flat', borderwidth=0)
        style.configure('PageHeaderInner.TFrame', background=self.colors['bg_card'])
        style.configure('Sidebar.TFrame', background=self.colors['bg_sidebar'])
        sidebar_font_size = int(11 * self.font_scale)
        style.configure('Sidebar.TLabel', font=(FONT_FAMILY, sidebar_font_size),
                       foreground=self.colors['text_sidebar'], background=self.colors['bg_sidebar'])
        style.configure('SidebarSelected.TLabel', font=(FONT_FAMILY, sidebar_font_size, 'bold'),
                       foreground=self.colors['text_sidebar_active'], background=self.colors['bg_sidebar'])
        style.configure('Header.TLabel', font=self.font_title, foreground=self.colors['text_primary'])
        style.configure('Section.TLabel', font=self.font_section, foreground=self.colors['text_primary'])
        style.configure('Stat.TLabel', font=self.font_stat, foreground=self.colors['primary'])
        style.configure('StatLabel.TLabel', font=self.font_stat_label, foreground=self.colors['text_secondary'])
        style.configure('Primary.TLabel', font=self.font_label, foreground=self.colors['primary'])
        style.configure('Success.TLabel', font=self.font_label, foreground=self.colors['success'])
        style.configure('Warning.TLabel', font=self.font_label, foreground=self.colors['warning'])
        # 下拉菜单样式 - 设置行高确保文字垂直居中
        combo_font_size = int(15 * self.font_scale)
        style.configure('TCombobox', font=self.font_label)
        style.configure('TCombobox', rowheight=int(combo_font_size * 1.8))
        # macOS aqua 下 fieldbackground 只能通过 map 设置，configure 被原生渲染忽略
        style.map('TCombobox',
                  fieldbackground=[('readonly', self.colors['bg_card']),
                                   ('disabled', self.colors['bg_input']),
                                   ('!disabled', self.colors['bg_card'])])
        style.map('TSpinbox',
                  fieldbackground=[('!disabled', self.colors['bg_card']),
                                   ('disabled', self.colors['bg_input'])])
        style.map('TEntry',
                  fieldbackground=[('!disabled', self.colors['bg_card']),
                                   ('disabled', self.colors['bg_input'])])
        style.configure('Custom.TLabelframe', font=self.font_label, background=self.colors['bg_card'])
        style.configure('Custom.TLabelframe.Label', font=self.font_label, background=self.colors['bg_card'])

    def create_sidebar(self):
        """创建左侧边栏"""
        sidebar = ttk.Frame(self.root, style='Sidebar.TFrame', width=int(UI_CONFIG['sidebar_width'] * self.dpi_scale * self.zoom_factor))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # Logo 区域 - 上下布局，增加间距
        logo_frame = ttk.Frame(sidebar, style='Sidebar.TFrame')
        logo_frame.pack(fill="x", padx=int(20 * self.dpi_scale * self.zoom_factor), pady=(int(30 * self.dpi_scale * self.zoom_factor), int(20 * self.dpi_scale * self.zoom_factor)))

        # 主标题 "BOSS" - 带彩色放大镜图标，大字体
        title_row = ttk.Frame(logo_frame, style='Sidebar.TFrame')
        title_row.pack(anchor="center")
        gap = int(4 * self.dpi_scale * self.zoom_factor)
        logo_icon = self.icons.logo('search_color', self.colors['text_sidebar_active'], self.colors['bg_sidebar'])
        logo_icon_label = ttk.Label(title_row, image=logo_icon, background=self.colors['bg_sidebar'])
        logo_icon_label._icon_ref = logo_icon
        logo_icon_label.pack(side="left")
        logo_text = ttk.Label(title_row, text="BOSS",
                              font=(FONT_FAMILY_SEMIBOLD, int(26 * self.font_scale)),
                              foreground=self.colors['text_sidebar_active'], background=self.colors['bg_sidebar'])
        logo_text.pack(side="left", padx=(gap, 0))

        # 副标题 "简历筛选器" - 调大字体，居中
        subtitle_label = ttk.Label(logo_frame, text="简历筛选器",
                                   font=(FONT_FAMILY, int(16 * self.font_scale)),
                                   foreground=self.colors['text_sidebar_subtitle'], background=self.colors['bg_sidebar'])
        subtitle_label.pack(anchor="center", pady=(int(6 * self.dpi_scale * self.zoom_factor), 0))

        # 分隔线
        sep = ttk.Separator(sidebar, orient='horizontal')
        sep.pack(fill="x", padx=0, pady=int(10 * self.dpi_scale * self.zoom_factor))

        # 导航项 - 使用 Frame 容器确保文字对齐（图标固定宽度）
        nav_items = [
            ("home", "首页", self.show_page_home),
            ("briefcase", "岗位配置", self.show_page_config),
            ("play", "运行控制", self.show_page_run),
            ("filter", "筛选结果", self.show_page_result),
            ("document", "学历核验", self.show_page_education),
            ("chart", "数据统计", self.show_page_stats),
        ]

        self.nav_labels = []
        self.nav_components = []  # 保存所有导航组件引用，用于 hover 效果
        sidebar_nav_font_size = int(15 * self.font_scale)

        # 设置导航项样式
        style = ttk.Style()
        style.configure('SidebarNav.TLabel',
                       font=(FONT_FAMILY, sidebar_nav_font_size),
                       foreground=self.colors['text_sidebar'],
                       background=self.colors['bg_sidebar'])
        style.configure('SidebarNavSelected.TLabel',
                       font=(FONT_FAMILY_SEMIBOLD, sidebar_nav_font_size),
                       foreground=self.colors['text_sidebar_active'],
                       background=self.colors['bg_sidebar'])

        # emoji 容器内边距（固定宽度，确保文字对齐）- 增大左侧距使导航项整体居中
        emoji_padx = int(40 * self.dpi_scale * self.zoom_factor)
        text_padx = int(10 * self.dpi_scale * self.zoom_factor)

        for idx, (icon_name, text, command) in enumerate(nav_items):
            # 生成两个颜色版本的图标
            icon_default = self.icons.nav(icon_name, self.colors['text_sidebar'], self.colors['bg_sidebar'])
            icon_active = self.icons.nav(icon_name, self.colors['text_sidebar_active'], self.colors['bg_sidebar'])

            # 使用 Frame 容器
            nav_frame = ttk.Frame(sidebar, style='Sidebar.TFrame')
            nav_frame.pack(fill="x", padx=0, pady=1)

            # 图标标签
            icon_label = ttk.Label(nav_frame, image=icon_default,
                                   style='SidebarNav.TLabel', cursor="hand2")
            icon_label._icon_default = icon_default
            icon_label._icon_active = icon_active
            icon_label.pack(side="left", padx=(emoji_padx, 0))

            # 文字标签
            text_label = ttk.Label(nav_frame, text=text,
                                  style='SidebarNav.TLabel', cursor="hand2",
                                  padding=(text_padx, int(14 * self.dpi_scale * self.zoom_factor)))
            text_label.pack(side="left", fill="x", expand=True)

            # 绑定点击和 hover 事件 - 所有子组件绑定到同一个 command
            for widget in [nav_frame, icon_label, text_label]:
                widget.bind("<Button-1>", lambda e, c=command: c())
                widget.bind("<Enter>", lambda e, i=idx: self.on_nav_enter(i))
                widget.bind("<Leave>", lambda e, i=idx: self.on_nav_leave(i))

            # 保存所有组件引用，用于 hover 效果
            self.nav_components.append({
                'frame': nav_frame,
                'icon': icon_label,
                'icon_default': icon_default,
                'icon_active': icon_active,
                'text': text_label,
                'command': command,
                'index': idx
            })

            self.nav_labels.append(text_label)

        # 分隔线 - 导航与设置之间
        sep2 = ttk.Separator(sidebar, orient='horizontal')
        sep2.pack(fill="x", padx=0, pady=int(10 * self.dpi_scale * self.zoom_factor))

        # 系统设置（独立导航项）- 使用 Frame 容器保持一致对齐
        settings_idx = len(nav_items)
        settings_frame = ttk.Frame(sidebar, style='Sidebar.TFrame')
        settings_frame.pack(fill="x", padx=0, pady=1)

        settings_icon_default = self.icons.nav('gear', self.colors['text_sidebar'], self.colors['bg_sidebar'])
        settings_icon_active = self.icons.nav('gear', self.colors['text_sidebar_active'], self.colors['bg_sidebar'])
        settings_icon_label = ttk.Label(settings_frame, image=settings_icon_default,
                                  style='SidebarNav.TLabel', cursor="hand2")
        settings_icon_label._icon_default = settings_icon_default
        settings_icon_label._icon_active = settings_icon_active
        settings_icon_label.pack(side="left", padx=(emoji_padx, 0))

        settings_text = ttk.Label(settings_frame, text="系统设置",
                                 style='SidebarNav.TLabel', cursor="hand2",
                                 padding=(text_padx, int(14 * self.dpi_scale * self.zoom_factor)))
        settings_text.pack(side="left", fill="x", expand=True)

        for widget in [settings_frame, settings_icon_label, settings_text]:
            widget.bind("<Button-1>", lambda e: self.show_page_api())
            widget.bind("<Enter>", lambda e, i=settings_idx: self.on_nav_enter(i))
            widget.bind("<Leave>", lambda e, i=settings_idx: self.on_nav_leave(i))

        self.nav_components.append({
            'frame': settings_frame,
            'icon': settings_icon_label,
            'icon_default': settings_icon_default,
            'icon_active': settings_icon_active,
            'text': settings_text,
            'command': self.show_page_api,
            'index': settings_idx
        })
        self.nav_labels.append(settings_text)

        # 底部信息 - 仅版本号 - 调大字体
        bottom_frame = ttk.Frame(sidebar, style='Sidebar.TFrame')
        bottom_frame.pack(side="bottom", fill="x", padx=int(20 * self.dpi_scale * self.zoom_factor), pady=int(20 * self.dpi_scale * self.zoom_factor))

        version_label = ttk.Label(bottom_frame, text=f"v{__version__}",
                                  font=(FONT_FAMILY, int(12 * self.font_scale)),
                                  foreground=self.colors['text_sidebar_version'], background=self.colors['bg_sidebar'],
                                  cursor="hand2")
        version_label.pack(anchor="w")
        version_label.bind("<Button-1>", lambda e: self.show_changelog())

    def create_main_content(self):
        """创建主内容区域"""
        # 主容器
        self.main_frame = ttk.Frame(self.root, style='Page.TFrame')
        self.main_frame.pack(side="left", fill="both", expand=True)
        self._last_page_pack_padx = None

        # 创建页面容器
        self.pages_frame = ttk.Frame(self.main_frame, style='Page.TFrame')
        self.pages_frame.pack(
            fill="both",
            expand=True,
            padx=int(UI_CONFIG['page_padding_x'] * self.dpi_scale * self.zoom_factor),
            pady=int(UI_CONFIG['page_padding_y'] * self.dpi_scale * self.zoom_factor),
        )
        self.main_frame.bind("<Configure>", lambda _e: self._schedule_page_width_policy(), add="+")

        self.home_page = None
        self.config_page = None
        self.api_config_page = None
        self.run_page = None
        self.result_page = None
        self.stats_page = None
        self.education_page = None

        # 首屏只创建首页，其他页面首次点击时再构建并缓存。
        self.create_home_page()

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

    def _defer_ui_work(self, key, callback):
        """Run non-urgent UI work after the current redraw, coalescing duplicates."""
        if key in self._pending_idle_tasks:
            return
        self._pending_idle_tasks.add(key)

        def _run():
            self._pending_idle_tasks.discard(key)
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

    def _schedule_page_width_policy(self):
        """Debounce width policy recalculation during resize/layout churn."""
        if self._page_width_policy_after_id is not None:
            try:
                self.root.after_cancel(self._page_width_policy_after_id)
            except tk.TclError:
                pass

        def _run():
            self._page_width_policy_after_id = None
            self._apply_page_width_policy()

        self._page_width_policy_after_id = self.root.after(60, _run)

    def _apply_page_width_policy(self):
        """Limit form-like pages on wide screens while keeping data tables wide."""
        if not hasattr(self, 'pages_frame') or not hasattr(self, 'main_frame'):
            return

        scale = self.dpi_scale * self.zoom_factor
        base_pad_x = int(UI_CONFIG['page_padding_x'] * scale)
        base_pad_y = int(UI_CONFIG['page_padding_y'] * scale)
        current_page = getattr(self, 'current_page_index', 0)

        # Result and stats pages are table-first surfaces; they should use the
        # available width. Other pages read better when the content stays bounded.
        full_width_pages = {3, 4}
        if current_page in full_width_pages:
            target_pad_x = base_pad_x
        else:
            try:
                available_width = max(0, self.main_frame.winfo_width())
            except tk.TclError:
                available_width = 0
            max_content_width = int(UI_CONFIG['content_max_width'] * scale)
            extra_pad = max(0, (available_width - max_content_width) // 2)
            target_pad_x = max(base_pad_x, extra_pad)

        if self._last_page_pack_padx != target_pad_x:
            self._last_page_pack_padx = target_pad_x
            self.pages_frame.pack_configure(
                padx=target_pad_x,
                pady=base_pad_y,
            )

        if current_page == 6:
            self._update_model_list_height()
            self._update_model_list_columns()
        elif current_page == 1:
            self._update_config_page_dynamic_heights()
        elif current_page == 3:
            self._update_result_tree_columns()
        elif current_page == 4:
            self._update_education_queue_columns()

    def _is_window_maximized(self) -> bool:
        """Return True when the main window is maximized or effectively fullscreen."""
        try:
            if self.root.state() == "zoomed":
                return True
            return (
                self.root.winfo_width() >= self.root.winfo_screenwidth() * 0.9
                and self.root.winfo_height() >= self.root.winfo_screenheight() * 0.85
            )
        except (tk.TclError, ValueError):
            return False

    def _update_result_tree_columns(self):
        """Show 8, 11, or 13 columns according to maximized state and table width."""
        if not hasattr(self, 'result_tree'):
            return

        base_columns = ("name", "exp", "salary", "skills", "score", "ai_eval", "level", "status")
        extra_columns = ("education", "age", "job_status")
        wide_columns = ("school", "company")
        display_columns = base_columns
        if self._is_window_maximized():
            display_columns += extra_columns
            try:
                tree_width = int(self.result_tree.winfo_width())
            except (tk.TclError, ValueError):
                tree_width = 0
            if tree_width >= 1500:
                display_columns += wide_columns
        self._apply_result_tree_column_widths(display_columns)
        if tuple(self.result_tree.cget("displaycolumns")) != display_columns:
            self.result_tree.configure(displaycolumns=display_columns)

    def _apply_result_tree_column_widths(self, display_columns):
        """Balance visible columns while keeping education and age readable."""
        base_widths = {
            "name": 80, "exp": 85, "salary": 85, "skills": 85,
            "score": 70, "ai_eval": 70, "level": 80, "status": 180,
            "education": 140, "age": 110, "job_status": 120,
            "school": 150, "company": 170,
        }
        min_widths = {
            "name": 60, "exp": 70, "salary": 70, "skills": 70,
            "score": 60, "ai_eval": 60, "level": 70, "status": 150,
            "education": 115, "age": 90, "job_status": 80,
            "school": 120, "company": 130,
        }

        wide_mode = "company" in display_columns
        widths = {column: base_widths[column] for column in display_columns}
        if wide_mode:
            try:
                available_width = max(0, int(self.result_tree.winfo_width()) - 2)
            except (tk.TclError, ValueError):
                available_width = 0
            compact_columns = {"education", "age"}
            flexible_columns = [
                column for column in display_columns
                if column not in compact_columns
            ]
            compact_width = sum(widths[column] for column in compact_columns)
            flexible_base_width = sum(widths[column] for column in flexible_columns)
            flexible_available = max(0, available_width - compact_width)
            if flexible_available > flexible_base_width:
                scale = flexible_available / flexible_base_width
                for column in flexible_columns:
                    widths[column] = int(widths[column] * scale)
                rounding_gap = available_width - sum(widths.values())
                widths["company"] += rounding_gap

        for column in display_columns:
            self.result_tree.column(
                column,
                width=widths[column],
                minwidth=min_widths[column],
                stretch=not wide_mode,
            )

    def _is_tall_window(self) -> bool:
        """Return True if the window height exceeds 85% of screen height (min 1000px)."""
        try:
            window_height = int(self.root.winfo_height())
            screen_height = int(self.root.winfo_screenheight())
        except (tk.TclError, ValueError):
            return False
        return window_height >= max(1000, int(screen_height * 0.85))

    def _get_tall_window_extra_rows(self):
        """Return extra visible rows for pages that can use fullscreen height."""
        if not self._is_tall_window():
            return 0
        try:
            window_height = int(self.root.winfo_height())
        except (tk.TclError, ValueError):
            return 0
        return max(2, (window_height - UI_CONFIG['window_base_height']) // 70)

    def _update_config_page_dynamic_heights(self):
        """Increase job-config text/list heights only for tall or fullscreen windows."""
        extra_rows = self._get_tall_window_extra_rows()
        requirement_extra_rows = 0 if extra_rows == 0 else max(1, extra_rows // 2)
        requirement_rows = min(24, UI_CONFIG['text_height_large'] + requirement_extra_rows)
        skills_rows = min(18, UI_CONFIG['treeview_height'] + extra_rows * 2)

        try:
            if hasattr(self, 'requirement_text'):
                self.requirement_text.configure(height=requirement_rows)
            if hasattr(self, 'skills_tree'):
                self.skills_tree.configure(height=skills_rows)
        except tk.TclError:
            return

    def _create_page_header(self, parent, title, subtitle=None):
        """创建页面标题区域：白色背景 + 左侧蓝色竖线，无灰色底色"""
        _pad = int(16 * self.dpi_scale * self.zoom_factor)
        _bar_w = int(4 * self.dpi_scale * self.zoom_factor)

        card = ttk.Frame(parent, style='PageHeader.TFrame')
        card.pack(fill="x", pady=(0, int(25 * self.dpi_scale * self.zoom_factor)))

        accent_bar = tk.Frame(card, width=_bar_w, bg=self.colors['primary'])
        accent_bar.pack(side="left", fill="y")

        inner = ttk.Frame(card, style='PageHeaderInner.TFrame')
        inner.pack(fill="x", padx=(_pad, _pad), pady=(_pad, _pad))

        title_label = ttk.Label(inner, text=title, font=self.font_section,
                                foreground=self.colors['text_primary'],
                                background=self.colors['bg_card'])
        title_label.pack(anchor="w")

        if subtitle:
            sub = ttk.Label(inner, text=subtitle, font=self.font_label,
                            foreground=self.colors['text_secondary'],
                            background=self.colors['bg_card'])
            sub.pack(anchor="w", pady=(int(8 * self.dpi_scale * self.zoom_factor), 0))

        return inner

    def _create_card(self, parent, title, padding=None, title_trailing_builder=None, **pack_opts):
        """创建带标题的白色卡片区域。

        替代 ttk.LabelFrame，因为 macOS aqua 主题的 Labelframe.border 元素
        强制使用 systemWindowBackgroundColor（灰色），无法通过 style 覆盖。

        标题行：左侧 3px 蓝色竖线 + 浅灰背景，与页面标题风格统一。

        返回内部内容 Frame，调用方将子控件放入返回的 Frame 中。

        title_trailing_builder: 可选回调 (title_bar, padding) -> None，
        用于在标题栏右侧注入附加控件（如操作按钮），不占用内容区空间。
        """
        if padding is None:
            padding = int(UI_CONFIG['label_frame_padding'] * self.dpi_scale * self.zoom_factor)
        title_font = pack_opts.pop("title_font", self.font_label)

        card = tk.Frame(parent, bg=self.colors['bg_card'],
                        highlightbackground=self.colors['border'], highlightthickness=1)
        card.pack(**pack_opts)

        # 标题行 - 左侧蓝色竖线 + 浅灰背景，与页面标题风格一致
        title_bg = '#F7F8FA'
        title_bar = tk.Frame(card, bg=title_bg)
        title_bar.pack(fill="x")

        # 左侧蓝色竖线（2px，与页面标题的 4px 竖线呼应但更细）
        accent = tk.Frame(title_bar, width=int(2 * self.dpi_scale * self.zoom_factor),
                          bg=self.colors['primary'])
        accent.pack(side="left", fill="y")

        title_label = tk.Label(title_bar, text=f" {title} ",
                               font=title_font,
                               fg=self.colors['text_primary'], bg=title_bg)

        # 标题栏右侧附加控件先 pack（side="right" 占右侧），再 pack 标题（side="top" 占顶部剩余空间）
        # 这样两者共享同一行，避免附加控件把标题栏撑高
        if title_trailing_builder is not None:
            title_trailing_builder(title_bar, padding)

        title_label.pack(anchor="w", padx=padding, pady=(int(padding * 0.7), int(padding * 0.7)))

        # 标题下方分隔线
        sep = tk.Frame(card, bg=self.colors['border'], height=1)
        sep.pack(fill="x")

        # 内容区（带内边距）
        content = ttk.Frame(card, style='TFrame')
        content.pack(fill="both", expand=True, padx=padding, pady=padding)
        return content

    def create_home_page(self):
        """创建首页"""
        self.home_page = ttk.Frame(self.pages_frame, style='Page.TFrame')

        # 页面标题 - 白色卡片 + 左侧蓝色竖线，避免文字直接浮在灰色背景上
        _card_pad = int(20 * self.dpi_scale * self.zoom_factor)
        header_card = ttk.Frame(self.home_page, style='WelcomeCard.TFrame')
        header_card.pack(fill="x", pady=(0, int(25 * self.dpi_scale * self.zoom_factor)))

        # 左侧蓝色竖线
        accent_bar = tk.Frame(header_card, width=int(4 * self.dpi_scale * self.zoom_factor),
                              bg=self.colors['primary'])
        accent_bar.pack(side="left", fill="y")

        header_frame = ttk.Frame(header_card, style='WelcomeInner.TFrame')
        header_frame.pack(fill="x", padx=(_card_pad, _card_pad), pady=(_card_pad, _card_pad))

        title_label = ttk.Label(header_frame, text="欢迎使用 BOSS 简历筛选器",
                               font=self.font_title, foreground=self.colors['text_primary'],
                               background=self.colors['bg_card'])
        title_label.pack(anchor="w")

        subtitle_label = ttk.Label(header_frame, text="智能解析、智能匹配、AI 评估、自动打招呼、学历核验、人工反馈、跟进状态、数据复盘",
                                   font=self.font_label, foreground=self.colors['text_secondary'],
                                   background=self.colors['bg_card'])
        subtitle_label.pack(anchor="w", pady=(int(10 * self.dpi_scale * self.zoom_factor), 0))

        # 岗位过滤
        home_filter_frame = ttk.Frame(self.home_page, style='Page.TFrame')
        home_filter_frame.pack(fill="x", pady=(int(15 * self.dpi_scale * self.zoom_factor), 0))
        ttk.Label(home_filter_frame, text="岗位过滤:", font=self.font_label,
                 background=self.colors['bg_main']).pack(side="left")
        self.home_job_var = tk.StringVar(value="全部岗位")
        self.home_job_combo = ttk.Combobox(home_filter_frame, textvariable=self.home_job_var,
                                            values=["全部岗位"], width=28, state="readonly",
                                            font=self.font_label)
        self.home_job_combo.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        self.home_job_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_home_stats())

        # 统计卡片区
        stats_container = ttk.Frame(self.home_page, style='Page.TFrame')
        stats_container.pack(fill="x", pady=int(30 * self.dpi_scale * self.zoom_factor))

        # 卡片数据
        cards_data = [
            ("passed_filter", "通过筛选", "total_home", self.colors['primary']),
            ("strong_recommend", "强烈推荐", "strong_home", self.colors['purple']),
            ("thumbs_up", "推荐", "recommended_home", self.colors['success']),
            ("chat", "已打招呼", "greeted_home", self.colors['warning']),
        ]

        self.home_stats_vars = {}
        self.home_stats_labels = {}  # 保存标签引用用于绑定事件
        for icon_name, label_text, var_name, color in cards_data:
            card_frame = ttk.Frame(stats_container, style='Card.TFrame')
            card_frame.pack(side="left", fill="x", expand=True, padx=int(15 * self.dpi_scale * self.zoom_factor), pady=int(12 * self.dpi_scale * self.zoom_factor))

            # 图标容器 - 彩色圆形背景
            icon_size = int(UI_CONFIG['stat_icon_size'] * self.dpi_scale * self.zoom_factor)
            icon_canvas = tk.Canvas(card_frame, width=icon_size, height=icon_size,
                                    bg=self.colors['bg_card'], highlightthickness=0)
            icon_canvas.pack(anchor="center",
                            pady=(int(20 * self.dpi_scale * self.zoom_factor), int(8 * self.dpi_scale * self.zoom_factor)))

            # 绘制彩色圆形背景
            margin = int(UI_CONFIG['icon_margin'] * self.dpi_scale * self.zoom_factor)
            icon_canvas.create_oval(margin, margin, icon_size - margin, icon_size - margin,
                                    fill=color, outline='')

            # 在圆形上绘制白色图标（使用 PhotoImage）
            stat_icon = self.icons.stat(icon_name, 'white')
            icon_canvas.create_image(icon_size // 2, icon_size // 2, image=stat_icon)
            icon_canvas._icon_ref = stat_icon

            # 数值
            var = tk.StringVar(value="0")
            self.home_stats_vars[var_name] = var
            value_label = ttk.Label(card_frame, textvariable=var,
                                   font=self.font_stat, foreground=color,
                                   background=self.colors['bg_card'],
                                   cursor="hand2")
            value_label.pack(anchor="center", pady=(0, int(8 * self.dpi_scale * self.zoom_factor)))

            # 绑定点击事件
            self.home_stats_labels[var_name] = (value_label, label_text)
            value_label.bind("<Button-1>", lambda e, vt=var_name: self.show_stat_detail(vt))

            # 标签
            text_label = ttk.Label(card_frame, text=label_text,
                                  font=self.font_stat_label, foreground=self.colors['text_secondary'],
                                  background=self.colors['bg_card'])
            text_label.pack(anchor="center", pady=(0, int(20 * self.dpi_scale * self.zoom_factor)))

        # 快速操作区
        quick_frame = self._create_card(self.home_page, "快速操作",
            padding=int(UI_CONFIG['card_padding'] * self.dpi_scale * self.zoom_factor),
            fill="x", pady=int(30 * self.dpi_scale * self.zoom_factor))

        quick_buttons = ttk.Frame(quick_frame, style='TFrame')
        quick_buttons.pack(fill="x")

        icon_play = self.icons.button('play', self.colors['text_primary'])
        btn1 = ttk.Button(quick_buttons, image=icon_play, text=" 开始筛选", compound=tk.LEFT, command=self.show_page_run, style='TButton')
        btn1._icon_ref = icon_play
        btn1.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        icon_filter = self.icons.button('filter', self.colors['text_primary'])
        btn2 = ttk.Button(quick_buttons, image=icon_filter, text=" 查看结果", compound=tk.LEFT, command=self.show_page_result, style='TButton')
        btn2._icon_ref = icon_filter
        btn2.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        icon_briefcase = self.icons.button('briefcase', self.colors['text_primary'])
        btn3 = ttk.Button(quick_buttons, image=icon_briefcase, text=" 配置岗位", compound=tk.LEFT, command=self.show_page_config, style='TButton')
        btn3._icon_ref = icon_briefcase
        btn3.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))

    def create_config_page(self):
        """创建岗位配置页面"""
        self.config_page = ttk.Frame(self.pages_frame, style='Page.TFrame')

        # 页面标题
        self._create_page_header(self.config_page, "岗位配置")

        # 配置容器 - 支持垂直滚动（macOS Tk 9.0+ 用 Text，其他用 Canvas）
        scroll_frame = ttk.Frame(self.config_page, style='Card.TFrame')
        scroll_frame.pack(fill="both", expand=True)

        self.config_canvas, self.config_scrollable_frame = self._create_scroll_container(
            scroll_frame, self.colors['bg_card'])

        # 使用 scrollable_frame 作为实际容器
        config_container = self.config_scrollable_frame

        # 岗位选择区域
        select_frame = ttk.Frame(config_container, style='TFrame')
        self._config_select_frame = select_frame
        select_frame.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=(int(25 * self.dpi_scale * self.zoom_factor), int(10 * self.dpi_scale * self.zoom_factor)))

        ttk.Label(select_frame, text="选择岗位:", font=self.font_label,
                 background=self.colors['bg_card']).pack(side="left")
        # 按钮靠右
        icon_trash_small = self.icons.button('trash', self.colors['text_primary'])
        btn_del = ttk.Button(select_frame, image=icon_trash_small, text="删除", compound=tk.LEFT, command=self.delete_job)
        btn_del._icon_ref = icon_trash_small
        btn_del.pack(side="right", padx=(int(8 * self.dpi_scale * self.zoom_factor), 0))
        icon_plus_small = self.icons.button('plus', self.colors['success'])
        btn_add = ttk.Button(select_frame, image=icon_plus_small, text="新建", compound=tk.LEFT, command=self.add_job)
        btn_add._icon_ref = icon_plus_small
        btn_add.pack(side="right", padx=int(8 * self.dpi_scale * self.zoom_factor))

        # "点此新增岗位→" 提示标签（呼吸动画，与三处新提示风格一致）
        self.btn_add_hint = ttk.Label(select_frame, text="点此新增岗位→", foreground=self.colors['success'],
                                       background=self.colors['bg_card'], font=self.font_label)
        self.btn_add_hint.pack(side="right", padx=int(4 * self.dpi_scale * self.zoom_factor))
        self._start_breathing(self.btn_add_hint, color_key='success', bg_key='bg_card')
        # 下拉框
        self.config_job_combo = ttk.Combobox(select_frame, values=list(self.job_rules.keys()), width=28, font=self.font_label)
        self.config_job_combo.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        self.config_job_combo.bind("<<ComboboxSelected>>", self.on_job_selected)

        # ===== 新建岗位步骤引导条 =====
        _fs = self.dpi_scale * self.zoom_factor
        self._job_step_bar = ttk.Frame(config_container, style='TFrame')
        # 默认隐藏，add_job 时显示

        self._job_step_labels: list[ttk.Label] = []
        _step_texts = ["① 填入需求", "② 解析需求", "③ 检查结果", "④ 保存配置"]
        _step_font = (FONT_FAMILY, int(12 * self.font_scale))

        # 标题行
        _step_title = ttk.Label(self._job_step_bar, text="新建岗位流程",
                                font=self.font_section,
                                foreground=self.colors['primary'],
                                background=self.colors['bg_card'])
        _step_title.pack(anchor="w", padx=int(20 * _fs), pady=(int(12 * _fs), int(4 * _fs)))

        # 步骤行
        _steps_row = ttk.Frame(self._job_step_bar, style='TFrame')
        _steps_row.pack(fill="x", padx=int(20 * _fs), pady=(0, int(12 * _fs)))

        for i, text in enumerate(_step_texts):
            if i > 0:
                arrow = ttk.Label(_steps_row, text="→", font=_step_font,
                                  foreground=self.colors['text_muted'],
                                  background=self.colors['bg_card'])
                arrow.pack(side="left", padx=int(6 * _fs))
            lbl = ttk.Label(_steps_row, text=text, font=_step_font,
                            background=self.colors['bg_card'])
            lbl.pack(side="left", padx=int(2 * _fs))
            self._job_step_labels.append(lbl)

        self._job_step_active = -1  # -1 = 隐藏

        # ===== 需求文档解析区域 =====
        parse_frame = self._create_card(config_container, "需求文档解析（可选）",
            fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(20 * self.dpi_scale * self.zoom_factor))

        # 需求输入框
        self._req_header_frame = ttk.Frame(parse_frame, style='TFrame')
        req_header = self._req_header_frame
        req_header.pack(fill="x", pady=(0, int(10 * self.dpi_scale * self.zoom_factor)))
        ttk.Label(req_header, text="粘贴招聘需求内容:", font=self.font_label,
                 background=self.colors['bg_card']).pack(side="left")
        icon_clipboard = self.icons.button('clipboard', self.colors['text_primary'])
        self.requirement_template_btn = ttk.Button(req_header, image=icon_clipboard, text=" 招聘需求示例", compound=tk.LEFT, command=self._insert_requirement_template)
        self.requirement_template_btn._icon_ref = icon_clipboard
        self.requirement_template_btn.pack(side="right")
        self.requirement_template_btn.state(['disabled'])
        # "点击查看需求示例->" 提示标签（新建岗位时显示）
        self.requirement_hint_label = ttk.Label(
            req_header, text="点击查看需求示例->", font=self.font_label,
            foreground=self.colors['success'], background=self.colors['bg_card'])
        self.requirement_hint_label.pack(side="right", padx=(0, int(4 * self.dpi_scale * self.zoom_factor)))
        self.requirement_hint_label.bind("<Button-1>", lambda e: self._insert_requirement_template())
        self.requirement_hint_label.destroy()  # 初始隐藏（show 时重建）

        # 需求输入框 - 白底 + focus蓝边框 + 占位提示
        text_container = ttk.Frame(parse_frame, style='TFrame')
        text_container.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))

        self.requirement_text = tk.Text(text_container, height=UI_CONFIG['text_height_large'],
                                        font=(FONT_FAMILY, int(10 * self.font_scale)),
                                        bg='#FFFFFF', fg=self.colors['text_primary'],
                                        borderwidth=0, highlightthickness=2,
                                        highlightbackground=self.colors['border'],
                                        highlightcolor=self.colors['primary'])
        self.requirement_text.pack(side="left", fill="both", expand=True)

        req_scroll = ttk.Scrollbar(text_container, orient="vertical", command=self.requirement_text.yview)
        req_scroll.pack(side="right", fill="y")
        self.requirement_text.config(yscrollcommand=req_scroll.set)

        # 占位提示文字
        self._req_placeholder_text = "在此粘贴招聘需求内容..."
        _placeholder_color = self.colors['text_muted']
        self.requirement_text.tag_configure("placeholder", foreground=_placeholder_color)
        self.requirement_text.insert("1.0", self._req_placeholder_text, "placeholder")
        self._req_placeholder_active = True

        def _req_focus_in(event):
            if self._req_placeholder_active:
                self.requirement_text.delete("1.0", tk.END)
                self.requirement_text.tag_remove("placeholder", "1.0", tk.END)
                self._req_placeholder_active = False

        def _req_focus_out(event):
            content = self.requirement_text.get("1.0", tk.END).strip()
            if not content:
                self.requirement_text.delete("1.0", tk.END)
                self.requirement_text.insert("1.0", self._req_placeholder_text, "placeholder")
                self._req_placeholder_active = True

        self.requirement_text.bind('<FocusIn>', _req_focus_in)
        self.requirement_text.bind('<FocusOut>', _req_focus_out)
        # 粘贴后显示"点击解析"提示
        def _on_paste(event):
            self._hide_requirement_hint()
            self._show_parse_hint()
        self.requirement_text.bind('<<Paste>>', _on_paste, add='+')

        # Text 控件 Enter/Leave 绑定，防止页面滚动干扰 Text 自身滚动
        self.requirement_text.bind('<Enter>', lambda e: setattr(self, '_over_text_widget', True))
        self.requirement_text.bind('<Leave>', lambda e: setattr(self, '_over_text_widget', False))

        self.bind_text_context_menu(self.requirement_text)

        # 解析按钮
        self._parse_btn_frame = ttk.Frame(parse_frame, style='TFrame')
        parse_btn_frame = self._parse_btn_frame
        parse_btn_frame.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))
        icon_search_parse = self.icons.button('search', self.colors['text_primary'])
        self.btn_parse_requirement = ttk.Button(parse_btn_frame, image=icon_search_parse, text=" 解析招聘需求", compound=tk.LEFT, command=self.parse_requirement)
        self.btn_parse_requirement._icon_ref = icon_search_parse
        self.btn_parse_requirement.pack(side="left")
        # "<-点击解析招聘需求" 提示标签（粘贴或填入模板后显示）
        self.parse_hint_label = ttk.Label(
            parse_btn_frame, text="<-点击解析招聘需求", font=self.font_label,
            foreground=self.colors['success'], background=self.colors['bg_card'])
        self.parse_hint_label.pack(side="left", padx=(int(8 * self.dpi_scale * self.zoom_factor), 0))
        self.parse_hint_label.destroy()  # 初始隐藏（show 时重建）

        # 解析结果展示
        self.parse_result_label = ttk.Label(parse_frame, text="", font=self.font_label,
                                           foreground=self.colors['success'], background=self.colors['bg_card'],
                                           justify="left")
        self.parse_result_label.pack(fill="x", anchor="w", pady=int(10 * self.dpi_scale * self.zoom_factor))

        # ===== 解析结果详细展示区域 =====
        self.result_detail_frame = ttk.Frame(config_container, style='Card.TFrame')
        # 先隐藏，等 show_page_config 或 on_job_selected 时再显示

        # 基本信息区
        basic_frame = self._create_card(self.result_detail_frame, "基本信息",
            fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

        # 岗位名称
        row1 = ttk.Frame(basic_frame, style='TFrame')
        row1.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))
        ttk.Label(row1, text="岗位名称:", font=self.font_label, width=UI_CONFIG['entry_width_job'],
                 background=self.colors['bg_card']).pack(side="left")
        self.job_name_var = tk.StringVar()
        self.job_name_entry = ttk.Entry(row1, textvariable=self.job_name_var, width=50, font=self.font_label)
        self.job_name_entry.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        self.bind_entry_context_menu(self.job_name_entry)

        # 学历和经验
        row2 = ttk.Frame(basic_frame, style='TFrame')
        row2.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))
        ttk.Label(row2, text="最低学历:", font=self.font_label, width=UI_CONFIG['entry_width_job'],
                 background=self.colors['bg_card']).pack(side="left")
        self.edu_var = tk.StringVar(value="本科")
        edu_combo = ttk.Combobox(row2, textvariable=self.edu_var,
                                 values=["不限", "高中", "中专", "大专", "本科", "硕士", "博士"],
                                 width=UI_CONFIG['combobox_width_edu'], font=self.font_label)
        edu_combo.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        # 禁用滚轮切换，防止误操作
        edu_combo.bind('<Enter>', lambda e: edu_combo.bind('<MouseWheel>', lambda ev: 'break'))
        edu_combo.bind('<Leave>', lambda e: edu_combo.unbind('<MouseWheel>'))

        ttk.Label(row2, text="最低经验:", font=self.font_label, width=UI_CONFIG['entry_width_label'],
                 background=self.colors['bg_card']).pack(side="left", padx=(int(30 * self.dpi_scale * self.zoom_factor), 0))
        self.min_exp_var = tk.StringVar(value="3")
        min_exp_spin = ttk.Spinbox(row2, from_=UI_CONFIG['spinbox_exp_min'], to=UI_CONFIG['spinbox_exp_max'], textvariable=self.min_exp_var, width=15, font=self.font_label)
        min_exp_spin.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        # 禁用滚轮切换，防止误操作
        min_exp_spin.bind('<Enter>', lambda e: min_exp_spin.bind('<MouseWheel>', lambda ev: 'break'))
        min_exp_spin.bind('<Leave>', lambda e: min_exp_spin.unbind('<MouseWheel>'))
        ttk.Label(row2, text="年", font=self.font_label, background=self.colors['bg_card']).pack(side="left")

        # 最大年龄
        self.max_age_var = tk.StringVar(value="35")
        row_age = ttk.Frame(basic_frame, style='TFrame')
        row_age.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))
        ttk.Label(row_age, text="最大年龄:", font=self.font_label, width=UI_CONFIG['entry_width_job'],
                 background=self.colors['bg_card']).pack(side="left")
        max_age_spin = ttk.Spinbox(row_age, from_=0, to=99, textvariable=self.max_age_var, width=15, font=self.font_label)
        max_age_spin.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        max_age_spin.bind('<Enter>', lambda e: max_age_spin.bind('<MouseWheel>', lambda ev: 'break'))
        max_age_spin.bind('<Leave>', lambda e: max_age_spin.unbind('<MouseWheel>'))
        ttk.Label(row_age, text="岁", font=self.font_label, background=self.colors['bg_card']).pack(side="left")
        ttk.Label(row_age, text="  留空表示不限制",
                 font=(FONT_FAMILY, int(10 * self.font_scale)),
                 foreground=self.colors['text_secondary'],
                 background=self.colors['bg_card']).pack(side="left", padx=(int(10 * self.dpi_scale * self.zoom_factor), 0))

        # 薪资范围
        self.salary_min_var = tk.StringVar()
        self.salary_max_var = tk.StringVar()
        self.salary_min_var.trace_add('write', self._validate_salary_input)
        self.salary_max_var.trace_add('write', self._validate_salary_input)
        row_salary = ttk.Frame(basic_frame, style='TFrame')
        row_salary.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))
        ttk.Label(row_salary, text="薪资范围:", font=self.font_label, width=UI_CONFIG['entry_width_job'],
                 background=self.colors['bg_card']).pack(side="left")
        salary_min_entry = ttk.Entry(row_salary, textvariable=self.salary_min_var, width=8, font=self.font_label)
        salary_min_entry.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        self.bind_entry_context_menu(salary_min_entry)
        self.salary_min_entry = salary_min_entry
        ttk.Label(row_salary, text="K  ~", font=self.font_label,
                 background=self.colors['bg_card']).pack(side="left")
        salary_max_entry = ttk.Entry(row_salary, textvariable=self.salary_max_var, width=8, font=self.font_label)
        salary_max_entry.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))
        self.bind_entry_context_menu(salary_max_entry)
        self.salary_max_entry = salary_max_entry
        ttk.Label(row_salary, text="K", font=self.font_label,
                 background=self.colors['bg_card']).pack(side="left")
        ttk.Label(row_salary, text="  留空表示不限制薪资",
                 font=(FONT_FAMILY, int(10 * self.font_scale)),
                 foreground=self.colors['text_secondary'],
                 background=self.colors['bg_card']).pack(side="left", padx=(int(10 * self.dpi_scale * self.zoom_factor), 0))

        # 工作地点
        row3 = ttk.Frame(basic_frame, style='TFrame')
        row3.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))
        ttk.Label(row3, text="工作地点:", font=self.font_label, width=UI_CONFIG['entry_width_job'],
                 background=self.colors['bg_card']).pack(side="left")
        self.work_location_var = tk.StringVar()
        work_location_entry = ttk.Entry(row3, textvariable=self.work_location_var, width=25, font=self.font_label)
        work_location_entry.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        self.bind_entry_context_menu(work_location_entry)
        ttk.Label(row3, text="留空表示不限   多地点用 / 分隔，如：南京/上海",
                 font=(FONT_FAMILY, int(10 * self.font_scale)),
                 foreground=self.colors['text_secondary'], background=self.colors['bg_card']).pack(side="left", padx=(int(10 * self.dpi_scale * self.zoom_factor), 0))

        # 技能关键词区域（带权重显示）- 左右分栏布局
        skills_frame = self._create_card(self.result_detail_frame, "技能关键词（可增删技能、可编辑权重）",
            fill="both", side="top", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

        # 左右分栏容器
        skills_container = ttk.Frame(skills_frame, style='TFrame')
        skills_container.pack(fill="both", expand=True)

        # 左侧：技能列表（可伸缩）
        skills_left = ttk.Frame(skills_container, style='TFrame')
        skills_left.pack(side="left", fill="both", expand=True)

        # 右侧：操作面板（固定宽度，上下布局）
        skills_right = ttk.Frame(skills_container, style='Card.TFrame', width=int(280 * self.dpi_scale * self.zoom_factor))
        skills_right.pack(side="right", fill="y")
        # 不固定高度，让内容自动撑开

        # === 左侧：技能列表 ===
        list_container = ttk.Frame(skills_left, style='Card.TFrame')
        list_container.pack(fill="both", expand=True)

        # 使用 Treeview 显示技能列表
        columns = ("name", "weight", "source", "evidence")
        tree_font = (FONT_FAMILY, int(11 * self.font_scale))

        self.skills_tree = ttk.Treeview(list_container, columns=columns, show="headings", height=UI_CONFIG['treeview_height'])
        self.skills_tree.heading("name", text="技能名称")
        self.skills_tree.heading("weight", text="权重")
        self.skills_tree.heading("source", text="来源")
        self.skills_tree.heading("evidence", text="原文出处")
        # 设置列 - 全部居中
        self.skills_tree.column("name", width=200, anchor='center')
        self.skills_tree.column("weight", width=60, anchor='center')
        self.skills_tree.column("source", width=60, anchor='center')
        self.skills_tree.column("evidence", width=280, anchor='w')
        # 设置颜色标记（带字体）- 覆盖所有情况
        self.skills_tree.tag_configure('high_weight', font=tree_font, background=self.colors['bg_tree_tag_high'])
        self.skills_tree.tag_configure('mid_weight', font=tree_font, background=self.colors['bg_tree_tag_mid'])
        self.skills_tree.tag_configure('low_weight', font=tree_font, background=self.colors['bg_tree_tag_low'])

        # 设置 Treeview 默认字体和行高
        _style = ttk.Style()
        _style.configure('Treeview', font=tree_font, rowheight=int(30 * self.dpi_scale * self.zoom_factor))
        _style.configure('Treeview.Heading', font=(FONT_FAMILY, int(12 * self.font_scale), 'bold'))

        skills_scroll = ttk.Scrollbar(list_container, orient="vertical", command=self.skills_tree.yview)
        self.skills_tree.configure(yscrollcommand=skills_scroll.set)
        self.skills_tree.pack(side="left", fill="both", expand=True)
        skills_scroll.pack(side="right", fill="y")

        # 技能表"原文出处"列 tooltip
        self._skills_tooltip = None
        self._skills_tooltip_item = None

        def _on_skills_motion(event):
            """鼠标悬停在 evidence 列时显示完整原文"""
            item = self.skills_tree.identify_row(event.y)
            column = self.skills_tree.identify_column(event.x)
            if not item or column != "#4":  # evidence 是第4列
                self._hide_skills_tooltip()
                return
            values = self.skills_tree.item(item, 'values')
            if not values or len(values) < 4:
                self._hide_skills_tooltip()
                return
            # 从 skills_data 获取完整 evidence（Treeview 中可能被截断）
            idx = self.skills_tree.index(item)
            if idx < len(self.skills_data):
                full_text = self.skills_data[idx].get("evidence", "")
            else:
                full_text = str(values[3])
            if not full_text:
                self._hide_skills_tooltip()
                return
            tooltip_key = (item, column)
            if tooltip_key == self._skills_tooltip_item and self._skills_tooltip and self._skills_tooltip.winfo_exists():
                return
            self._hide_skills_tooltip()
            self._skills_tooltip_item = tooltip_key
            x = self.root.winfo_pointerx() + 15
            y = self.root.winfo_pointery() + 10
            self._skills_tooltip = self._create_simple_tooltip(full_text, x, y)

        def _on_skills_leave(event):
            self._hide_skills_tooltip()

        self.skills_tree.bind("<Motion>", _on_skills_motion)
        self.skills_tree.bind("<Leave>", _on_skills_leave)

        # 选中技能编辑区
        edit_card = self._create_card(skills_right, "编辑选中技能",
            padding=int(12 * self.dpi_scale * self.zoom_factor),
            fill="x", padx=int(10 * self.dpi_scale * self.zoom_factor), pady=(int(10 * self.dpi_scale * self.zoom_factor), int(15 * self.dpi_scale * self.zoom_factor)))

        # 选中技能名称
        ttk.Label(edit_card, text="当前选中:", font=self.font_label,
                 background=self.colors['bg_card']).pack(anchor="w", pady=(0, int(5 * self.dpi_scale * self.zoom_factor)))
        self.selected_skill_var = tk.StringVar(value="未选择")
        self.selected_skill_label = ttk.Label(edit_card, textvariable=self.selected_skill_var,
                                              font=self.font_label,
                                              foreground=self.colors['primary'], background=self.colors['bg_card'],
                                              wraplength=int(240 * self.dpi_scale * self.zoom_factor), justify='left')
        self.selected_skill_label.pack(fill="x", pady=(0, int(10 * self.dpi_scale * self.zoom_factor)))

        # 权重输入框（标签和输入框同一行）
        weight_row = ttk.Frame(edit_card, style='TFrame')
        weight_row.pack(fill="x", pady=(0, int(10 * self.dpi_scale * self.zoom_factor)))
        ttk.Label(weight_row, text="权重 (1-3):", font=self.font_label,
                 background=self.colors['bg_card'], width=UI_CONFIG['entry_width_label']).pack(side="left")
        self.new_skill_weight_var = tk.StringVar(value="1")
        weight_entry = ttk.Entry(weight_row, textvariable=self.new_skill_weight_var,
                                font=self.font_label, width=5, justify='center')
        weight_entry.pack(side="left")
        self.bind_entry_context_menu(weight_entry)

        # 操作按钮
        icon_pencil_skill = self.icons.button('pencil', self.colors['text_primary'])
        btn_update = ttk.Button(edit_card, image=icon_pencil_skill, text=" 更新权重", compound=tk.LEFT, command=self.update_skill_weight)
        btn_update._icon_ref = icon_pencil_skill
        btn_update.pack(fill="x", pady=(0, int(5 * self.dpi_scale * self.zoom_factor)))
        icon_trash_skill = self.icons.button('trash', self.colors['text_primary'])
        btn_del_skill = ttk.Button(edit_card, image=icon_trash_skill, text=" 删除技能", compound=tk.LEFT, command=self.delete_skill)
        btn_del_skill._icon_ref = icon_trash_skill
        btn_del_skill.pack(fill="x")

        # 添加新技能区
        add_card = self._create_card(skills_right, "添加新技能",
            padding=int(12 * self.dpi_scale * self.zoom_factor),
            fill="x", padx=int(10 * self.dpi_scale * self.zoom_factor), pady=int(10 * self.dpi_scale * self.zoom_factor))

        ttk.Label(add_card, text="技能名称:", font=self.font_label,
                 background=self.colors['bg_card']).pack(anchor="w", pady=(0, int(5 * self.dpi_scale * self.zoom_factor)))
        self.new_skill_var = tk.StringVar()
        skill_entry = ttk.Entry(add_card, textvariable=self.new_skill_var, font=self.font_label)
        skill_entry.pack(fill="x", pady=(0, int(8 * self.dpi_scale * self.zoom_factor)))
        self.bind_entry_context_menu(skill_entry)

        # 权重输入框（标签和输入框同一行）
        weight_row = ttk.Frame(add_card, style='TFrame')
        weight_row.pack(fill="x", pady=(0, int(8 * self.dpi_scale * self.zoom_factor)))
        ttk.Label(weight_row, text="权重 (1-3):", font=self.font_label,
                 background=self.colors['bg_card'], width=UI_CONFIG['entry_width_label']).pack(side="left")
        self.new_skill_add_weight_var = tk.StringVar(value="1")
        add_weight_entry = ttk.Entry(weight_row, textvariable=self.new_skill_add_weight_var,
                                    font=self.font_label, width=5, justify='center')
        add_weight_entry.pack(side="left")
        self.bind_entry_context_menu(add_weight_entry)

        icon_plus_add = self.icons.button('plus', self.colors['text_primary'])
        btn_add_skill = ttk.Button(add_card, image=icon_plus_add, text=" 添加技能", compound=tk.LEFT, command=self.add_skill)
        btn_add_skill._icon_ref = icon_plus_add
        btn_add_skill.pack(fill="x", pady=(int(8 * self.dpi_scale * self.zoom_factor), 0))

        # 绑定选中事件
        self.skills_tree.bind("<<TreeviewSelect>>", self.on_skill_selected)

        # 必要条件区域
        required_frame = self._create_card(self.result_detail_frame, "必要条件（硬性约束）",
            fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

        # 使用说明
        required_help = ttk.Label(required_frame,
            text="简单匹配：输入关键词，简历中包含即可通过\n"
                 "OR（满足任一）：多个关键词用逗号分隔，满足任意一个即通过\n"
                 "AND（全部满足）：多个关键词用逗号分隔，必须全部满足才通过\n"
                 "示例：统招本科  |  微服务,分布式（OR）  |  Spring Boot,MySQL（AND）",
            font=self.font_log, foreground=self.colors['text_secondary'],
            background=self.colors['bg_card'], justify='left')
        required_help.pack(anchor='w', pady=(0, int(6 * self.dpi_scale * self.zoom_factor)))

        # 必要条件列表显示
        self.required_listbox = tk.Listbox(required_frame, height=UI_CONFIG['listbox_height'],
                                          font=self.font_label,
                                          borderwidth=1, highlightthickness=0)
        self.required_listbox.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))

        # 必要条件 tooltip（显示原文出处）
        self._req_tooltip = None
        self._req_tooltip_idx = None

        def _on_req_motion(event):
            """鼠标悬停在必要条件上时显示原文出处"""
            idx = self.required_listbox.nearest(event.y)
            if idx < 0:
                self._hide_req_tooltip()
                return
            if idx == self._req_tooltip_idx and self._req_tooltip and self._req_tooltip.winfo_exists():
                return
            self._hide_req_tooltip()
            evidence = ""
            if idx < len(self.required_conditions_data):
                cond = self.required_conditions_data[idx]
                if isinstance(cond, dict):
                    evidence = cond.get("_evidence", "")
                else:
                    evidence = self._required_evidence_map.get(str(cond), "") if hasattr(self, '_required_evidence_map') else ""
            if not evidence:
                return
            self._req_tooltip_idx = idx
            x = self.root.winfo_pointerx() + 15
            y = self.root.winfo_pointery() + 10
            self._req_tooltip = self._create_simple_tooltip(evidence, x, y)

        def _on_req_leave(event):
            self._hide_req_tooltip()

        self.required_listbox.bind("<Motion>", _on_req_motion)
        self.required_listbox.bind("<Leave>", _on_req_leave)

        # 必要条件编辑 - 条件类型选择 + 关键词（逗号分隔）
        required_edit_frame = ttk.Frame(required_frame, style='TFrame')
        required_edit_frame.pack(fill="x")
        ttk.Label(required_edit_frame, text="类型:", font=self.font_label,
                 background=self.colors['bg_card']).pack(side="left")
        self.required_cond_type_var = tk.StringVar(value="简单匹配")
        cond_type_combo = ttk.Combobox(required_edit_frame, textvariable=self.required_cond_type_var,
                                        values=["简单匹配", "OR（满足任一）", "AND（全部满足）"],
                                        width=12, state="readonly", font=self.font_label)
        cond_type_combo.pack(side="left", padx=int(3 * self.dpi_scale * self.zoom_factor))
        ttk.Label(required_edit_frame, text="关键词:", font=self.font_label,
                 background=self.colors['bg_card']).pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), 0))
        self.new_required_var = tk.StringVar()
        required_edit = ttk.Entry(required_edit_frame, textvariable=self.new_required_var, font=self.font_label)
        required_edit.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor), fill="x", expand=True)
        self.bind_entry_context_menu(required_edit)
        ttk.Button(required_edit_frame, text="添加", command=self.add_required_condition).pack(side="left", padx=(int(8 * self.dpi_scale * self.zoom_factor), int(3 * self.dpi_scale * self.zoom_factor)))
        ttk.Button(required_edit_frame, text="删除选中", command=self.delete_required_condition).pack(side="left", padx=(int(3 * self.dpi_scale * self.zoom_factor), 0))

        # 按钮行（居中布局，固定在页面底部，不随 Canvas 滚动）
        self.btn_frame = ttk.Frame(self.config_page, style='Page.TFrame')
        self._btn_inner = ttk.Frame(self.btn_frame, style='Page.TFrame')
        btn_inner = self._btn_inner
        btn_inner.pack(anchor="center")

        # "点击保存配置->" 提示标签（解析成功后显示，位于保存按钮左侧）
        self.save_hint_label = ttk.Label(btn_inner, text="点击保存配置->", font=self.font_label,
                                          foreground=self.colors['success'], background=self.colors['bg_main'])
        self.save_hint_label.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))
        self.save_hint_label.destroy()  # 初始隐藏（show 时重建）

        icon_save_cfg = self.icons.button('save', self.colors['text_primary'])
        self.btn_save = ttk.Button(btn_inner, image=icon_save_cfg, text=" 保存配置", compound=tk.LEFT, command=self.save_current_job)
        self.btn_save._icon_ref = icon_save_cfg
        self.btn_save.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))
        icon_refresh_cfg = self.icons.button('refresh', self.colors['text_primary'])
        btn_reset = ttk.Button(btn_inner, image=icon_refresh_cfg, text=" 重置", compound=tk.LEFT, command=self.reset_job_form)
        btn_reset._icon_ref = icon_refresh_cfg
        btn_reset.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))
        icon_import_cfg = self.icons.button('import', self.colors['text_primary'])
        btn_import = ttk.Button(btn_inner, image=icon_import_cfg, text=" 导入配置", compound=tk.LEFT, command=self.import_config)
        btn_import._icon_ref = icon_import_cfg
        btn_import.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))
        icon_export_cfg = self.icons.button('export', self.colors['text_primary'])
        btn_export = ttk.Button(btn_inner, image=icon_export_cfg, text=" 导出配置", compound=tk.LEFT, command=self.export_config)
        btn_export._icon_ref = icon_export_cfg
        btn_export.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))

        # 存储技能数据的列表（带权重）；source="优先" 时保存到 preferred_keywords
        self.skills_data = []  # [{"name": "Java", "weight": 2, "source": "解析"}, ...]
        self.required_conditions_data = []  # ["统招本科", ...]

        # 设置下拉框的值
        self.config_job_combo['values'] = list(self.job_rules.keys())

        # 如果有已存在的岗位，自动加载第一个并显示详细结果区域
        if self.job_rules:
            first_job = list(self.job_rules.keys())[0]
            self.config_job_combo.set(first_job)
            rule = self.job_rules[first_job]
            self.load_job_to_form(rule)
            # 注意：这里不 pack result_detail_frame，因为 config_page 还没有被显示
            # 将在 show_page_config 中 pack

        # 底部按钮固定在页面底部，不随 Canvas 滚动
        self.btn_frame.pack(fill="x", side="bottom", pady=(int(10 * self.dpi_scale * self.zoom_factor), int(10 * self.dpi_scale * self.zoom_factor)))

        # 在所有控件创建完毕后绑定滚轮事件
        self._bind_mousewheel(self.config_canvas, self.config_scrollable_frame)

    def create_api_config_page(self):
        """创建 API 配置页面"""
        # 创建带滚动条的页面
        self.api_config_page = ttk.Frame(self.pages_frame, style='Page.TFrame')

        # 创建可滚动容器（macOS Tk 9.0+ 用 Text，其他用 Canvas）
        self.api_canvas, self.api_scrollable_frame = self._create_scroll_container(
            self.api_config_page, self.colors['bg_card'])

        # 在可滚动框架中创建内容
        self._create_api_config_content()

    def _on_api_canvas_configure(self, event):
        """调整可滚动框架宽度以匹配 Canvas"""
        self.api_canvas.itemconfig(self.api_canvas_frame, width=event.width)

    @staticmethod
    def _delta_to_units(delta):
        """将鼠标滚轮 delta 转换为滚动单位数。

        Windows 鼠标滚轮每格 delta=±120；macOS 触控板 delta 通常为 ±1。
        直接除以 120 取整在 macOS 上恒为 0，故按平台分别处理。
        """
        if sys.platform == 'darwin':
            return -1 if delta > 0 else 1
        return int(-1 * (delta / 120))

    @staticmethod
    def _create_scroll_container(parent, bg_color):
        """创建可滚动容器，返回 (canvas, container_frame)。

        所有平台统一使用 Canvas + create_window。
        macOS Tk 9.0+ 触控板滚动通过 _setup_cocoa_scroll_hook() 在 ObjC 层拦截。
        """
        canvas = tk.Canvas(parent, bg=bg_color, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        container = ttk.Frame(canvas, style='TFrame')

        container.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas_window = canvas.create_window((0, 0), window=container, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Canvas 宽度变化 → 同步嵌入 Frame 宽度
        def _on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return canvas, container

    @staticmethod
    def _bind_mousewheel(canvas, parent_frame):
        """在 Canvas 及其所有子控件上绑定滚轮事件（instance binding 优先级最高）。

        macOS 上 ttk 控件的 class binding 会先消费 <MouseWheel> 事件，
        bind_all 优先级最低无法拦截。必须在每个控件上用 bind() 绑定 instance handler，
        返回 'break' 阻止后续 class binding。

        macOS 触控板可能生成 <MouseWheel>（delta=±1）或 <Button-4>/<Button-5> 事件，
        需要同时绑定三种事件类型。

        首次绑定后标记 canvas._mousewheel_bound，后续调用直接跳过，避免页面切换时
        递归遍历所有子控件重复绑定导致卡顿。
        """
        if getattr(canvas, '_mousewheel_bound', False):
            return

        def _on_wheel(event):
            """处理滚轮/触控板滚动事件"""
            # 优先使用 delta（MouseWheel 事件）
            if hasattr(event, 'delta') and event.delta != 0:
                units = BossFilterGUI._delta_to_units(event.delta)
            # 回退到 num（Button-4/5 事件，macOS X11 兼容模式）
            elif hasattr(event, 'num'):
                if event.num == 4:
                    units = -1
                elif event.num == 5:
                    units = 1
                else:
                    return
            else:
                return
            if units != 0:
                canvas.yview_scroll(units, "units")
            return 'break'

        # 跳过自带滚轮的控件类型
        _skip_types = (ttk.Spinbox, ttk.Combobox, ttk.Scrollbar, tk.Text, tk.Entry, tk.Listbox)

        def _bind_recursive(widget):
            if isinstance(widget, _skip_types):
                return
            # Treeview 也跳过
            if hasattr(widget, 'identify_region'):
                return
            widget.bind("<MouseWheel>", _on_wheel)
            # macOS/Linux 触控板可能生成 Button-4/5 事件
            if sys.platform != 'win32':
                widget.bind("<Button-4>", _on_wheel)
                widget.bind("<Button-5>", _on_wheel)
            for child in widget.winfo_children():
                _bind_recursive(child)

        # Canvas 自身
        canvas.bind("<MouseWheel>", _on_wheel)
        if sys.platform != 'win32':
            canvas.bind("<Button-4>", _on_wheel)
            canvas.bind("<Button-5>", _on_wheel)
        # 递归绑定所有子控件
        _bind_recursive(parent_frame)
        canvas._mousewheel_bound = True

    # ── macOS Tk 9.0+ Cocoa 触控板滚动 hook ──────────────────────────────
    # Tk 9.0 的 Cocoa 后端在 NSView.scrollWheel: 中消费触控板事件，
    # 不向 Canvas 等非原生滚动控件生成 Tk MouseWheel 事件。
    # 通过 ObjC Runtime swizzle 拦截 scrollWheel:，直接滚动当前页面的 Canvas。

    _cocoa_hook_installed = False
    _cocoa_refs = {}            # 防止 ObjC 对象/回调被 GC

    def _setup_cocoa_scroll_hook(self):
        """设置 Cocoa scrollWheel: 拦截（仅 macOS Tk 9.0+）。

        通过 ObjC Runtime swizzle NSView.scrollWheel:，
        对非 NSScrollView 子视图直接调用当前页面 Canvas 的 yview_scroll。
        如果设置失败（ctypes/libobjc 不可用），静默降级（触控板不可滚动）。
        """
        if BossFilterGUI._cocoa_hook_installed:
            return
        try:
            import ctypes
            import ctypes.util

            objc_path = ctypes.util.find_library('objc')
            if not objc_path:
                return
            objc = ctypes.cdll.LoadLibrary(objc_path)

            # ── ObjC Runtime 函数签名 ──
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.class_getInstanceMethod.restype = ctypes.c_void_p
            objc.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            objc.method_getImplementation.restype = ctypes.c_void_p
            objc.method_getImplementation.argtypes = [ctypes.c_void_p]
            objc.method_setImplementation.restype = ctypes.c_void_p
            objc.method_setImplementation.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

            # objc_msgSend 用于方法调用
            objc.objc_msgSend.restype = ctypes.c_void_p
            objc.objc_msgSend.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]

            sel_scroll = objc.sel_registerName(b'scrollWheel:')
            sel_shared = objc.sel_registerName(b'sharedApplication')
            sel_keywin = objc.sel_registerName(b'keyWindow')
            sel_cv = objc.sel_registerName(b'contentView')
            sel_super = objc.sel_registerName(b'superview')
            sel_is_kind = objc.sel_registerName(b'isKindOfClass:')
            sel_delta_y = objc.sel_registerName(b'scrollingDeltaY')

            cls_nsapp = objc.objc_getClass(b'NSApplication')
            cls_nsview = objc.objc_getClass(b'NSView')
            cls_nssv = objc.objc_getClass(b'NSScrollView')

            if not all([cls_nsapp, cls_nsview, cls_nssv]):
                return

            # ── 获取 NSApplication.sharedApplication.keyWindow.contentView ──
            app = objc.objc_msgSend(cls_nsapp, sel_shared, None)
            if not app:
                self.root.after(1000, self._setup_cocoa_scroll_hook)
                return
            kw = objc.objc_msgSend(app, sel_keywin, None)
            if not kw:
                self.root.after(1000, self._setup_cocoa_scroll_hook)
                return
            content_view = objc.objc_msgSend(kw, sel_cv, None)
            if not content_view:
                self.root.after(1000, self._setup_cocoa_scroll_hook)
                return

            # ── scrollingDeltaY 调用函数（处理 x86_64 fpret vs ARM64） ──
            try:
                objc.objc_msgSend_fpret.restype = ctypes.c_double
                objc.objc_msgSend_fpret.argtypes = [
                    ctypes.c_void_p, ctypes.c_void_p]
                _msg_send_double = objc.objc_msgSend_fpret
            except AttributeError:
                # ARM64 没有 fpret，创建独立的 CFUNCTYPE 避免修改 objc_msgSend 签名
                _msg_send_double = ctypes.CFUNCTYPE(
                    ctypes.c_double, ctypes.c_void_p, ctypes.c_void_p
                )(objc.objc_msgSend)

            # ── isKindOfClass: 调用函数（3 个 c_void_p 参数） ──
            _msg_send_is_kind = ctypes.CFUNCTYPE(
                ctypes.c_bool,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
            )(objc.objc_msgSend)

            # ── 保存引用，防止被 GC ──
            BossFilterGUI._cocoa_refs['app'] = app
            BossFilterGUI._cocoa_refs['content_view'] = content_view

            # ── scrollWheel: 替代实现 ──
            # C 签名: void scrollWheel:(id self, SEL _cmd, id event)
            SCROLL_CB = ctypes.CFUNCTYPE(
                None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

            def _cocoa_scroll_impl(view, _cmd, event):
                """swizzle 后的 scrollWheel: 实现。

                对 NSScrollView 内部视图（Text/Treeview/Listbox）跳过，
                让 Cocoa 原生滚动处理。对其他视图直接滚动当前页面的 Canvas。
                """
                try:
                    # 鼠标在 Text 控件上时，让 Text 自身处理滚动
                    if getattr(self, '_over_text_widget', False):
                        return

                    # 检查 view 是否在 NSScrollView 内部
                    # （Text/Treeview/Listbox 的 Cocoa 实现是 NSScrollView）
                    v = view
                    for _ in range(10):  # 最多向上 10 层
                        sv = objc.objc_msgSend(v, sel_super, None)
                        if not sv:
                            break
                        if _msg_send_is_kind(sv, sel_is_kind, cls_nssv):
                            return  # 在 NSScrollView 内部 → 让原生滚动处理
                        v = sv

                    # 获取 deltaY（浮点数）
                    delta_y = _msg_send_double(event, sel_delta_y)
                    if delta_y == 0:
                        return

                    # Cocoa deltaY > 0 = 向上 → units = -1（内容上移）
                    # Cocoa deltaY < 0 = 向下 → units = 1（内容下移）
                    units = -1 if delta_y > 0 else 1

                    # 直接滚动当前页面的 Canvas
                    page_canvas = {
                        1: getattr(self, 'config_canvas', None),
                        2: getattr(self, 'run_canvas', None),
                        4: getattr(self, 'education_canvas', None),
                        6: getattr(self, 'api_canvas', None),
                    }.get(getattr(self, 'current_page_index', -1))

                    if page_canvas:
                        page_canvas.yview_scroll(units, "units")

                except Exception:
                    pass

            # ── Swizzle NSView.scrollWheel: ──
            scroll_callback = SCROLL_CB(_cocoa_scroll_impl)
            cb_ptr = ctypes.cast(scroll_callback, ctypes.c_void_p).value

            method = objc.class_getInstanceMethod(cls_nsview, sel_scroll)
            if not method:
                return

            # 保存原始实现（用于 fallback）并替换
            orig_impl = objc.method_getImplementation(method)
            objc.method_setImplementation(method, cb_ptr)

            # 防止回调和 ObjC 引用被 GC
            BossFilterGUI._cocoa_refs['callback'] = scroll_callback
            BossFilterGUI._cocoa_refs['orig_impl'] = orig_impl

            BossFilterGUI._cocoa_hook_installed = True

        except Exception:
            pass

    def _on_mousewheel(self, event):
        """统一处理滚轮事件 - 根据当前页面分发到对应的 Canvas

        使用 bind_all（最高优先级），从事件源控件向上遍历找到所属 Canvas，
        避免 macOS 上 ttk class binding 消费事件的问题。
        """
        widget = event.widget

        # 让自带滚轮处理的控件自行处理
        if isinstance(widget, (tk.Text, tk.Entry, tk.Listbox, ttk.Scrollbar, ttk.Combobox, ttk.Spinbox)):
            return
        # Treeview 也需要跳过（自带垂直滚动）
        if hasattr(widget, 'identify_region'):
            return

        # 计算滚动量
        if hasattr(event, 'delta') and event.delta != 0:
            units = self._delta_to_units(event.delta)
        elif hasattr(event, 'num'):
            if event.num == 4:
                units = -1
            elif event.num == 5:
                units = 1
            else:
                return
        else:
            return

        if units == 0:
            return

        # 检查事件源是否直接就是目标 Canvas
        target_canvas = None
        if hasattr(self, 'config_canvas') and widget is self.config_canvas:
            target_canvas = self.config_canvas
        elif hasattr(self, 'api_canvas') and widget is self.api_canvas:
            target_canvas = self.api_canvas
        elif hasattr(self, 'run_canvas') and widget is self.run_canvas:
            target_canvas = self.run_canvas
        elif hasattr(self, 'education_canvas') and widget is self.education_canvas:
            target_canvas = self.education_canvas
        else:
            # 从事件源控件向上遍历，找到所属的可滚动 Canvas
            try:
                w = widget
                while w is not None:
                    parent = w.master
                    if parent is getattr(self, 'config_canvas', None):
                        target_canvas = self.config_canvas
                        break
                    elif parent is getattr(self, 'api_canvas', None):
                        target_canvas = self.api_canvas
                        break
                    elif parent is getattr(self, 'run_canvas', None):
                        target_canvas = self.run_canvas
                        break
                    elif parent is getattr(self, 'education_canvas', None):
                        target_canvas = self.education_canvas
                        break
                    w = parent
            except Exception:
                return

        if target_canvas is None:
            target_canvas = {
                1: getattr(self, 'config_canvas', None),
                2: getattr(self, 'run_canvas', None),
                4: getattr(self, 'education_canvas', None),
            }.get(getattr(self, 'current_page_index', -1))

        if target_canvas is None:
            return

        target_canvas.yview_scroll(units, "units")
        return 'break'

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

    def _create_api_config_content(self):
        """创建 API 配置页面内容（在可滚动框架中）"""
        api_container = self.api_scrollable_frame

        # 系统设置页面标题
        self._create_page_header(api_container, "系统设置")

        # 新电脑提示：检测到已保存配置但 API Key 丢失
        self.reconfig_card = None
        if hasattr(self, 'api_config') and self.api_config.get("needs_reconfigure"):
            _pad = int(UI_CONFIG['label_frame_padding'] * self.dpi_scale * self.zoom_factor)
            self.reconfig_card = tk.Frame(api_container, bg=self.colors['bg_card'],
                                          highlightbackground=self.colors['border'], highlightthickness=1)
            self.reconfig_card.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))
            tk.Label(self.reconfig_card, text="  ⚠️  提示  ",
                     font=(FONT_FAMILY_SEMIBOLD, int(13 * self.font_scale)),
                     fg=self.colors['text_primary'], bg=self.colors['bg_card']).pack(anchor="w", padx=_pad, pady=(_pad, 0))
            _inner = ttk.Frame(self.reconfig_card, style='TFrame')
            _inner.pack(fill="both", expand=True, padx=_pad, pady=_pad)
            ttk.Label(_inner, text="检测到已保存的模型配置，但 API Key 未配置（可能是新电脑）",
                     font=self.font_label, foreground=self.colors['warning'],
                     background=self.colors['bg_card']).pack(anchor="w")
            ttk.Label(_inner, text="请在下方重新输入 API Key 并点击「保存并添加到列表」",
                     font=self.font_label, foreground=self.colors['text_secondary'],
                     background=self.colors['bg_card']).pack(anchor="w", pady=(5, 0))

        # API 配置卡片
        config_card = self._create_card(api_container, "API 配置",
            fill="both", expand=True, padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(20 * self.dpi_scale * self.zoom_factor))

        # 1. 当前使用模型显示
        current_model_frame = ttk.Frame(config_card, style='TFrame')
        current_model_frame.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

        ttk.Label(current_model_frame, text="当前使用模型:", font=self.font_label,
                 background=self.colors['bg_card']).pack(side="left")

        self.current_model_label = ttk.Label(current_model_frame, text="未配置",
                                             font=(FONT_FAMILY, int(14 * self.font_scale), 'bold'),
                                             foreground=self.colors['primary'],
                                             background=self.colors['bg_card'])
        self.current_model_label.pack(side="left", padx=(int(10 * self.dpi_scale * self.zoom_factor), 0))

        # 2. API 配置输入区（服务商、Key、URL、模型名称）
        input_frame = ttk.Frame(config_card, style='TFrame')
        input_frame.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

        # 第一行：服务商
        row1 = ttk.Frame(input_frame, style='TFrame')
        row1.pack(fill="x")

        # 引用模块级常量（兼容旧代码 self.PROVIDER_DISPLAY / self.DISPLAY_TO_KEY）
        self.PROVIDER_DISPLAY = PROVIDER_DISPLAY
        self.DISPLAY_TO_KEY = DISPLAY_TO_KEY

        ttk.Label(row1, text="服务商:", font=self.font_label, width=UI_CONFIG['label_width_provider']).pack(side="left")
        self.api_provider_var = tk.StringVar(value=self.PROVIDER_DISPLAY["qwen"])
        self.api_provider_combo = ttk.Combobox(row1, textvariable=self.api_provider_var,
                                               values=list(self.PROVIDER_DISPLAY.values()),
                                               width=18, font=self.font_label)
        self.api_provider_combo.pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), int(20 * self.dpi_scale * self.zoom_factor)))
        self.api_provider_combo.bind("<<ComboboxSelected>>", self.on_api_provider_changed)

        # 第二行：模型名称
        row2 = ttk.Frame(input_frame, style='TFrame')
        row2.pack(fill="x", pady=(int(10 * self.dpi_scale * self.zoom_factor), 0))

        ttk.Label(row2, text="模型名称:", font=self.font_label, width=UI_CONFIG['label_width_model']).pack(side="left")
        self.api_model_var = tk.StringVar()
        model_entry = ttk.Entry(row2, textvariable=self.api_model_var, width=30, font=self.font_label)
        model_entry.pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), int(10 * self.dpi_scale * self.zoom_factor)))
        self.bind_entry_context_menu(model_entry)

        # 获取模型列表按钮
        icon_download_models = self.icons.button('download', self.colors['text_primary'])
        btn_fetch = ttk.Button(row2, image=icon_download_models, text=" 获取模型列表", compound=tk.LEFT, command=self.fetch_model_list)
        btn_fetch._icon_ref = icon_download_models
        btn_fetch.pack(side="left")

        # 第三行：API Key
        row3 = ttk.Frame(input_frame, style='TFrame')
        row3.pack(fill="x", pady=(int(10 * self.dpi_scale * self.zoom_factor), 0))

        ttk.Label(row3, text="API Key:", font=self.font_label, width=UI_CONFIG['label_width_api_key']).pack(side="left")
        self.api_key_var = tk.StringVar()
        self.api_key_entry = ttk.Entry(row3, textvariable=self.api_key_var, width=55, font=self.font_label, show="*")
        self.api_key_entry.pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), 0))
        self.bind_entry_context_menu(self.api_key_entry)

        # 明文/密文切换按钮（无边框 Button 实现，使用图标）
        self.api_key_show_var = tk.BooleanVar(value=False)
        eye_icon = self.icons.button('eye', self.colors['text_primary'])
        eye_off_icon = self.icons.button('eye_off', self.colors['text_primary'])
        self.api_key_toggle_btn = tk.Button(row3, image=eye_icon,
            relief="flat", bd=0, highlightthickness=0, cursor="hand2",
            command=self.toggle_api_key_visibility)
        self.api_key_toggle_btn._icon_eye = eye_icon
        self.api_key_toggle_btn._icon_eye_off = eye_off_icon
        self.api_key_toggle_btn.pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), 0))

        # 第四行：Base URL
        row4 = ttk.Frame(input_frame, style='TFrame')
        row4.pack(fill="x", pady=(int(10 * self.dpi_scale * self.zoom_factor), 0))

        ttk.Label(row4, text="Base URL:", font=self.font_label, width=UI_CONFIG['label_width_url']).pack(side="left")
        self.api_base_url_var = tk.StringVar()
        url_entry = ttk.Entry(row4, textvariable=self.api_base_url_var, width=55, font=self.font_label)
        url_entry.pack(side="left", padx=(int(5 * self.dpi_scale * self.zoom_factor), 0))
        self.bind_entry_context_menu(url_entry)

        # 操作按钮行
        button_row = ttk.Frame(config_card, style='TFrame')
        button_row.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

        icon_save_api = self.icons.button('save', self.colors['text_primary'])
        btn_save_api = ttk.Button(button_row, image=icon_save_api, text=" 保存并添加到列表", compound=tk.LEFT, command=self.save_api_config)
        btn_save_api._icon_ref = icon_save_api
        btn_save_api.pack(side="left", padx=(int(10 * self.dpi_scale * self.zoom_factor), int(5 * self.dpi_scale * self.zoom_factor)))
        icon_search_test = self.icons.button('search', self.colors['text_primary'])
        btn_test = ttk.Button(button_row, image=icon_search_test, text=" 测试连接", compound=tk.LEFT, command=self.test_api_connection)
        btn_test._icon_ref = icon_search_test
        btn_test.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))

        # API 配置状态提示（改为 Frame 容器，支持多段可点击文本）
        self.api_status_frame = ttk.Frame(config_card)
        self.api_status_frame.pack(anchor="w", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=(0, int(10 * self.dpi_scale * self.zoom_factor)))
        self.api_status_label = ttk.Label(self.api_status_frame, text="",
                                         font=(FONT_FAMILY, int(11 * self.font_scale)),
                                         foreground=self.colors['success'])
        self.api_status_label.pack(side="left")
        # 用于存放可点击的标签引用
        self._status_clickable_labels = []

        # 3. 已保存模型列表
        model_list_card = self._create_card(api_container, "已保存模型（双击切换）",
            fill="both", expand=True, padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

        # 模型列表 Treeview
        model_columns = ("name", "provider", "compat", "edu_ref", "base_url")
        self.model_list_tree = ttk.Treeview(model_list_card, columns=model_columns, show="headings", selectmode='extended')
        self.model_list_tree.heading("name", text="模型名称")
        self.model_list_tree.heading("provider", text="服务商")
        self.model_list_tree.heading("compat", text="状态")
        self.model_list_tree.heading("edu_ref", text="学历核验")
        self.model_list_tree.heading("base_url", text="Base URL")
        self.model_list_tree.column("name", width=260, minwidth=200, anchor='center', stretch=False)
        self.model_list_tree.column("provider", width=240, minwidth=200, anchor='center', stretch=False)
        self.model_list_tree.column("compat", width=140, minwidth=100, anchor='center', stretch=False)
        self.model_list_tree.column("edu_ref", width=130, minwidth=90, anchor='center', stretch=False)
        self.model_list_tree.column("base_url", width=200, minwidth=100, anchor='w', stretch=True)
        # 默认显示全部列
        self.model_list_tree.configure(displaycolumns=("name", "provider", "compat", "edu_ref", "base_url"))

        # 已保存模型列表字体比表格字体小一号
        fs = self.dpi_scale * self.zoom_factor
        model_list_font = (FONT_FAMILY, int(12 * self.font_scale))
        model_tree_style = ttk.Style()
        model_tree_style.configure("ModelList.Treeview", font=model_list_font,
                                  rowheight=int(UI_CONFIG['treeview_rowheight'] * fs))
        model_tree_style.configure("ModelList.Treeview.Heading",
                                  font=(FONT_FAMILY, int(12 * self.font_scale), 'bold'))
        self.model_list_tree.configure(style="ModelList.Treeview")

        # 滚动条（垂直 + 水平）
        model_v_scrollbar = ttk.Scrollbar(model_list_card, orient="vertical", command=self.model_list_tree.yview)
        model_h_scrollbar = ttk.Scrollbar(model_list_card, orient="horizontal", command=self.model_list_tree.xview)
        self.model_list_tree.configure(yscrollcommand=model_v_scrollbar.set, xscrollcommand=model_h_scrollbar.set)

        self.model_list_tree.pack(side="top", fill="both", expand=True)
        model_v_scrollbar.pack(side="right", fill="y")
        model_h_scrollbar.pack(side="bottom", fill="x")

        # 右键菜单 - 模型列表
        model_menu_font = (FONT_FAMILY, int(12 * self.font_scale))
        self.model_context_menu = tk.Menu(self.model_list_tree, tearoff=0, font=model_menu_font)
        self.model_context_menu.add_command(label="切换模型", command=self.use_selected_model)
        self.model_context_menu.add_command(label="测试连通性", command=self.test_saved_model_connectivity)
        self.model_context_menu.add_separator()
        self.model_context_menu.add_command(label="设为学历核验模型", command=self._set_education_model)
        self.model_context_menu.add_command(label="取消学历核验模型", command=self._unset_education_model)
        self.model_context_menu.add_separator()
        self.model_context_menu.add_command(label="删除模型", command=self.delete_selected_model)

        def show_model_context_menu(event):
            item = self.model_list_tree.identify_row(event.y)
            if item:
                # 右键点击的行已在多选集合内时，保持现有选区
                if item not in self.model_list_tree.selection():
                    self.model_list_tree.selection_set(item)
                # 动态切换学历核验菜单项可用状态
                is_edu = self._is_education_model_item(item)
                self.model_context_menu.entryconfig("设为学历核验模型", state="disabled" if is_edu else "normal")
                self.model_context_menu.entryconfig("取消学历核验模型", state="normal" if is_edu else "disabled")
                self.model_context_menu.tk_popup(event.x_root, event.y_root)

        self.model_list_tree.bind("<Button-3>", show_model_context_menu)

        # 列 tooltip（文字截断时弹出）
        self._model_tooltip_after_id = None
        self._model_tooltip = None
        self._model_tooltip_item = None
        # 列标识 → values 下标
        self._model_col_idx = {"#1": 0, "#2": 1, "#3": 2, "#4": 3, "#5": 4}

        def _on_model_motion(event):
            """鼠标移动时检查是否需要显示 tooltip"""
            item = self.model_list_tree.identify_row(event.y)
            column = self.model_list_tree.identify_column(event.x)
            if not item or column not in self._model_col_idx:
                self._hide_model_tooltip()
                return
            idx = self._model_col_idx[column]
            values = self.model_list_tree.item(item, 'values')
            if not values or len(values) <= idx:
                self._hide_model_tooltip()
                return
            text = str(values[idx])
            if not text:
                self._hide_model_tooltip()
                return
            # 用 bbox 获取单元格实际像素宽度
            try:
                bbox = self.model_list_tree.bbox(item, column)
                if bbox:
                    cell_width = bbox[2]  # (x, y, width, height)
                else:
                    cell_width = self.model_list_tree.column(column, "width")
            except Exception:
                cell_width = self.model_list_tree.column(column, "width")
            # 用字体度量文字像素宽度
            try:
                style = ttk.Style()
                font_name = style.lookup("ModelList.Treeview", "font") or (FONT_FAMILY, 12)
                from tkinter.font import Font
                text_width = Font(font=font_name).measure(text)
            except Exception:
                text_width = len(text) * 8
            # 内边距 16px
            if text_width <= cell_width - 16:
                self._hide_model_tooltip()
                return
            tooltip_key = (item, column)
            if tooltip_key == self._model_tooltip_item and self._model_tooltip and self._model_tooltip.winfo_exists():
                return
            self._model_tooltip_item = tooltip_key
            if self._model_tooltip_after_id:
                self.root.after_cancel(self._model_tooltip_after_id)
            x = self.root.winfo_pointerx() + 15
            y = self.root.winfo_pointery() + 10
            self._model_tooltip_after_id = self.root.after(
                300, lambda t=text, k=tooltip_key, px=x, py=y: self._show_model_tooltip(t, px, py, k)
            )

        def _on_model_leave(event):
            """鼠标离开时隐藏 tooltip"""
            self._hide_model_tooltip()

        self.model_list_tree.bind("<Motion>", _on_model_motion)
        self.model_list_tree.bind("<Leave>", _on_model_leave)
        self.model_list_tree.bind(
            "<Configure>",
            lambda _event: self._update_model_list_columns(),
            add="+",
        )

        # 初始化模型列表
        self.saved_models = []

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
            saved_key = get_api_key(provider_key, _base_url)
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
            if hasattr(self, '_timeout_hint_label'):
                _pk = self.api_config.get("api_provider", "")
                _pn = PROVIDER_DISPLAY.get(_pk, _pk)
                _mn = self.api_config.get("model", "")
                if _is_relay:
                    _label = f"中转服务/{_mn}" if _mn else "中转服务"
                    _hint = f"（{_label}，默认 120 秒）"
                else:
                    _label = f"{_pn}/{_mn}" if _mn else _pn
                    _hint = f"（{_label}，默认 60 秒）"
                self._timeout_hint_label.config(text=_hint)

        # 更新当前使用模型显示
        self.update_current_model_display()

        # 加载已保存的模型列表
        self.load_saved_models_to_tree()

    def _api_config_file_mtime(self):
        """Return a stable file fingerprint for api_config.json."""
        try:
            return API_CONFIG_PATH.stat().st_mtime_ns if API_CONFIG_PATH.exists() else 0
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
        self._resolve_api_keys_async()

    def _resolve_api_keys_async(self):
        """后台读取 keyring，避免首次打开 API 页阻塞主线程。"""
        if self._api_key_resolve_thread and self._api_key_resolve_thread.is_alive():
            return
        if not getattr(self, 'api_config', None):
            return

        provider = self.api_config.get("api_provider", "")
        base_url = self.api_config.get("base_url", "")
        saved_models = list(self.api_config.get("saved_models", []))

        def _worker():
            current_key = ""
            missing_saved_key = False
            try:
                if provider:
                    current_key = get_api_key(provider, base_url) or ""
                for model_config in saved_models:
                    model_provider = model_config.get("api_provider", "")
                    if model_provider and not get_api_key(model_provider, model_config.get("base_url", "")):
                        missing_saved_key = True
                        break
            except Exception:
                current_key = ""

            def _apply():
                if not getattr(self, 'api_config', None):
                    return
                if (self.api_config.get("api_provider", ""), self.api_config.get("base_url", "")) != (provider, base_url):
                    return
                self.api_config["api_key"] = current_key
                if missing_saved_key:
                    self.api_config["needs_reconfigure"] = True
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
        """更新当前使用模型显示"""
        if not hasattr(self, 'current_model_label'):
            return

        current_model = self.api_config.get("model", "")
        current_provider = self.api_config.get("api_provider", "")

        if current_model:
            provider_display = self.PROVIDER_DISPLAY.get(current_provider, current_provider)
            display_text = f"{provider_display} / {current_model}"
            self.current_model_label.config(text=display_text, foreground=self.colors['primary'])
        else:
            self.current_model_label.config(text="未配置", foreground=self.colors['text_secondary'])

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
        current_model = self.api_config.get("model", "")
        edu_ref = self.api_config.get("education_model_ref") or {}

        # 同步到 self.saved_models（关键修复！）
        self.saved_models = saved_models

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
            is_current = "✓ 使用中" if name == current_model else ""
            # 学历核验标记：匹配 provider + model
            is_edu = (
                edu_ref
                and edu_ref.get("model") == name
                and edu_ref.get("api_provider") == provider_key
            )
            edu_display = "✓" if is_edu else ""
            tags = []
            if is_current:
                tags.append('current')
            if is_edu:
                tags.append('edu_ref')
            self.model_list_tree.insert("", "end", values=(name, provider_display, status_display, edu_display, base_url), tags=tuple(tags))

        # 设置使用中标记和学历核验标记的样式
        self.model_list_tree.tag_configure('current', foreground=self.colors['success'])
        self.model_list_tree.tag_configure('edu_ref', foreground=self.colors['primary'])

        # 动态调整高度：普通窗口保持原来的最多6行，全屏/高窗口显示更多行。
        self._update_model_list_height()
        # 根据窗口状态显示/隐藏 Base URL 列
        self._update_model_list_columns()

        # 绑定双击事件 - 双击切换模型
        self.model_list_tree.bind("<Double-1>", lambda e: self.use_selected_model())

        # 在所有控件创建完毕后绑定滚轮事件
        self._bind_mousewheel(self.api_canvas, self.api_scrollable_frame)

    def _get_model_list_max_rows(self):
        """Return saved-model list max rows for the current window height."""
        base_rows = 6
        if not self._is_tall_window():
            return base_rows
        try:
            window_height = int(self.root.winfo_height())
        except (tk.TclError, ValueError):
            return base_rows
        extra_rows = max(0, (window_height - UI_CONFIG['window_base_height']) // 42)
        return min(18, max(10, base_rows + extra_rows))

    def _update_model_list_height(self):
        """Resize saved-model Treeview height without changing normal-window layout."""
        if not hasattr(self, 'model_list_tree'):
            return
        try:
            row_count = len(self.model_list_tree.get_children())
            max_rows = self._get_model_list_max_rows()
            self.model_list_tree['height'] = max(1, min(row_count, max_rows))
        except tk.TclError:
            return

    def _update_model_list_columns(self):
        """Fit saved-model columns while preserving the wider 4K layout."""
        if not hasattr(self, 'model_list_tree'):
            return
        display = ("name", "provider", "compat", "edu_ref", "base_url")
        current = tuple(self.model_list_tree.cget("displaycolumns"))
        if current != display:
            self.model_list_tree.configure(displaycolumns=display)

        if self._is_window_maximized():
            base_widths = {
                "name": 320, "provider": 280, "compat": 170,
                "edu_ref": 150, "base_url": 200,
            }
        else:
            base_widths = {
                "name": 260, "provider": 240, "compat": 140,
                "edu_ref": 130, "base_url": 200,
            }

        min_widths = {
            "name": 180, "provider": 160, "compat": 90,
            "edu_ref": 80, "base_url": 170,
        }
        widths = dict(base_widths)
        try:
            available_width = max(0, int(self.model_list_tree.winfo_width()) - 24)
        except (tk.TclError, ValueError):
            available_width = 0

        overflow = sum(widths.values()) - available_width
        if available_width > 0 and overflow > 0:
            for column in ("provider", "edu_ref", "compat", "name"):
                reducible = max(0, widths[column] - min_widths[column])
                reduction = min(reducible, overflow)
                widths[column] -= reduction
                overflow -= reduction
                if overflow <= 0:
                    break
            if overflow > 0:
                widths["base_url"] = max(
                    min_widths["base_url"], widths["base_url"] - overflow
                )

        for column in display:
            self.model_list_tree.column(
                column,
                width=widths[column],
                minwidth=min_widths[column],
                stretch=column == "base_url",
            )

    def _update_education_queue_columns(self):
        """Keep the education queue status column visible on 1080p screens."""
        if not hasattr(self, 'education_queue_tree'):
            return

        base_widths = {
            "file": 230, "name": 120, "number": 160,
            "school": 175, "major": 210, "status": 140,
        }
        min_widths = {
            "file": 150, "name": 80, "number": 130,
            "school": 130, "major": 150, "status": 120,
        }
        widths = dict(base_widths)
        try:
            available_width = max(0, int(self.education_queue_tree.winfo_width()) - 24)
        except (tk.TclError, ValueError):
            available_width = 0

        overflow = sum(widths.values()) - available_width
        if available_width > 0 and overflow > 0:
            for column in ("major", "file", "school", "name", "number", "status"):
                reducible = max(0, widths[column] - min_widths[column])
                reduction = min(reducible, overflow)
                widths[column] -= reduction
                overflow -= reduction
                if overflow <= 0:
                    break

        for column in ("file", "name", "number", "school", "major", "status"):
            self.education_queue_tree.column(
                column,
                width=widths[column],
                minwidth=min_widths[column],
                anchor="w" if column == "file" else "center",
                stretch=column in ("file", "number", "school", "major"),
            )

    def create_run_page(self):
        """创建运行控制页面 - 增强版：浏览器状态检测 + 进度条 + 滚动支持"""
        self.run_page = ttk.Frame(self.pages_frame, style='Page.TFrame')

        # 可滚动容器（macOS Tk 9.0+ 用 Text，其他用 Canvas）
        scroll_frame = ttk.Frame(self.run_page, style='Page.TFrame')
        scroll_frame.pack(fill="both", expand=True)

        self.run_canvas, scrollable_frame = self._create_scroll_container(
            scroll_frame, self.colors['bg_card'])

        self.run_scrollable_frame = scrollable_frame  # 保存引用，供 mousewheel 绑定使用

        # 所有内容放入 scrollable_frame
        content = scrollable_frame

        # 页面标题
        self._create_page_header(content, "运行控制")

        # 控制卡片
        control_container = ttk.Frame(content, style='Card.TFrame')
        control_container.pack(fill="x", pady=int(15 * self.dpi_scale * self.zoom_factor))

        # === 浏览器连接状态检测 ===
        browser_frame = self._create_card(control_container, "浏览器状态",
            fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(20 * self.dpi_scale * self.zoom_factor))

        browser_status_row = ttk.Frame(browser_frame, style='TFrame')
        browser_status_row.pack(fill="x")

        # 状态指示灯
        self.browser_status_indicator = ttk.Label(browser_status_row, text="🔴 未连接",
                                                  font=(FONT_FAMILY, int(11 * self.font_scale)),
                                                  foreground=self.colors['danger'])
        self.browser_status_indicator.pack(side="left")

        # 检测按钮
        icon_browser = self.icons.button('search', self.colors['text_primary'])
        btn_browser = ttk.Button(browser_status_row, image=icon_browser, text=" 检测/连接浏览器", compound=tk.LEFT, command=self.check_browser_connection)
        btn_browser._icon_ref = icon_browser
        btn_browser.pack(side="left", padx=int(20 * self.dpi_scale * self.zoom_factor))

        # 状态说明
        self.browser_status_help = ttk.Label(browser_status_row, text="请点击按钮连接 BOSS 直聘页面",
                                             font=(FONT_FAMILY, int(11 * self.font_scale)),
                                             foreground=self.colors['text_secondary'])
        self.browser_status_help.pack(side="left", padx=int(20 * self.dpi_scale * self.zoom_factor))

        # 运行参数
        param_frame = ttk.Frame(control_container, style='TFrame')
        param_frame.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(20 * self.dpi_scale * self.zoom_factor))

        # 滚动轮次
        row1 = ttk.Frame(param_frame, style='TFrame')
        row1.pack(fill="x", pady=int(15 * self.dpi_scale * self.zoom_factor))
        ttk.Label(row1, text="滚动轮次:", font=self.font_label, width=12,
                 background=self.colors['bg_card']).pack(side="left")
        self.rounds_var = tk.StringVar(value="100")
        self.rounds_spin = ttk.Spinbox(row1, from_=UI_CONFIG['spinbox_rounds_min'],
                                       to=UI_CONFIG['spinbox_rounds_max'],
                                       increment=10, textvariable=self.rounds_var,
                                       width=15, font=self.font_label)
        self.rounds_spin.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        # 鼠标滚轮绑定
        self.rounds_spin.bind('<Enter>',
            lambda e: self.rounds_spin.bind('<MouseWheel>', self._on_rounds_mousewheel))
        self.rounds_spin.bind('<Leave>',
            lambda e: self.rounds_spin.unbind('<MouseWheel>'))
        self.rounds_hint_label = ttk.Label(row1, text="(推荐 50-200 轮次)", font=(FONT_FAMILY, int(11 * self.font_scale)),
                 foreground=self.colors['text_muted'], background=self.colors['bg_card'])
        self.rounds_hint_label.pack(side="left", padx=int(10 * self.dpi_scale * self.zoom_factor))

        # 选择岗位（多岗位运行时指定处理哪个岗位）
        row_job = ttk.Frame(param_frame, style='TFrame')
        row_job.pack(fill="x", pady=int(15 * self.dpi_scale * self.zoom_factor))
        ttk.Label(row_job, text="选择岗位:", font=self.font_label, width=12,
                 background=self.colors['bg_card']).pack(side="left")
        self.job_select_var = tk.StringVar(value="全部岗位")
        self.job_combo = ttk.Combobox(row_job, textvariable=self.job_select_var,
                                       values=["全部岗位"], width=28, state="readonly",
                                       font=self.font_label)
        self.job_combo.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        self.job_combo.bind("<<ComboboxSelected>>", self.on_run_job_selected)
        ttk.Label(row_job, text="建议每次选择一个岗位，\"全部岗位\"将依次处理",
                 font=(FONT_FAMILY, int(11 * self.font_scale)),
                 foreground=self.colors['text_muted'],
                 background=self.colors['bg_card']).pack(side="left", padx=int(10 * self.dpi_scale * self.zoom_factor))

        # 打招呼等级
        row2 = ttk.Frame(param_frame, style='TFrame')
        row2.pack(fill="x", pady=int(15 * self.dpi_scale * self.zoom_factor))
        ttk.Label(row2, text="自动打招呼:", font=self.font_label, width=12,
                 background=self.colors['bg_card']).pack(side="left")
        self.greet_level_var = tk.StringVar(value="仅强烈推荐")
        greet_combo = ttk.Combobox(row2, textvariable=self.greet_level_var,
                                    values=["不打招呼（仅筛选）", "仅强烈推荐", "强烈推荐 + 推荐"],
                                    width=20, state="readonly", font=self.font_label)
        greet_combo.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        # 动态备注：根据选择的打招呼等级实时变化
        self._greet_note_label = ttk.Label(row2, text="",
                 font=(FONT_FAMILY, int(11 * self.font_scale)),
                 foreground=self.colors['text_muted'], background=self.colors['bg_card'])
        self._greet_note_label.pack(side="left", padx=int(10 * self.dpi_scale * self.zoom_factor))

        def _update_greet_note(*_):
            level = self.greet_level_var.get()
            if level == "不打招呼（仅筛选）":
                self._greet_note_label.config(text="不自动打招呼")
            elif level == "仅强烈推荐":
                self._greet_note_label.config(text=f"给评分≥{SCORE_THRESHOLD_STRONG}分的候选人打招呼")
            else:
                self._greet_note_label.config(text=f"给评分≥{SCORE_THRESHOLD_RECOMMEND}分的候选人打招呼")

        _update_greet_note()
        greet_combo.bind("<<ComboboxSelected>>", _update_greet_note)

        # AI 辅助评估开关
        row_ai = ttk.Frame(param_frame, style='TFrame')
        row_ai.pack(fill="x", pady=int(15 * self.dpi_scale * self.zoom_factor))
        ttk.Label(row_ai, text="AI 评估:", font=self.font_label, width=12,
                 background=self.colors['bg_card']).pack(side="left")
        # API Key 状态：先显示"检测中"，后台查 keyring 后更新（避免主线程阻塞）
        self.ai_eval_var = tk.BooleanVar(value=False)
        # 大 indicator + 文字一体，用父容器 anchor 做垂直居中
        _cb_style = ttk.Style()
        _indicator_size = int(32 * self.dpi_scale * self.zoom_factor)
        _cb_style.configure('AIEval.TCheckbutton',
                            font=self.font_label,
                            background=self.colors['bg_card'],
                            indicatordiameter=_indicator_size)
        _cb_style.map('AIEval.TCheckbutton',
                      background=[('active', self.colors['bg_card'])])
        ai_check = ttk.Checkbutton(row_ai, text="启用 AI 辅助评估",
                                   variable=self.ai_eval_var,
                                   style='AIEval.TCheckbutton')
        ai_check.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))
        # API Key 状态标签（先显示检测中，后台查询完毕后由 _update_ai_eval_status 更新）
        _status_font = (FONT_FAMILY, int(11 * self.font_scale))
        self.ai_status_label = tk.Label(row_ai, text="⏳ 检测中...", font=_status_font,
                                        foreground=self.colors['text_secondary'],
                                        background=self.colors['bg_card'])
        self.ai_status_label.pack(side="left", padx=int(5 * self.dpi_scale * self.zoom_factor))
        # 页面先完成绘制，再后台查询 keyring，避免导入 keyring 与 Tk 控件创建争抢主线程。
        def _check_run_page_key_bg():
            _provider = self.api_config.get("api_provider", "")
            if not _provider:
                self.run_on_ui(self._update_ai_eval_status)
                return
            try:
                _key = get_api_key(_provider, self.api_config.get("base_url", ""))
            except Exception:
                _key = None
            def _apply():
                if _key and not self.api_config.get("api_key"):
                    self.api_config["api_key"] = _key
                self._update_ai_eval_status()
            self.run_on_ui(_apply)
        self.root.after(
            150,
            lambda: threading.Thread(target=_check_run_page_key_bg, daemon=True).start(),
        )
        # 备注：+- 分色显示
        _note_prefix = "(对通过筛选的候选人进行 LLM 二次评分，"
        _note_suffix = "10分调整)"
        _note_font = (FONT_FAMILY, int(11 * self.font_scale))
        _sign_font = (FONT_FAMILY, int(14 * self.font_scale))  # +/- 显式加大
        tk.Label(row_ai, text=_note_prefix, font=_note_font,
                 foreground=self.colors['text_muted'], background=self.colors['bg_card']).pack(side="left", padx=int(10 * self.dpi_scale * self.zoom_factor))
        tk.Label(row_ai, text="+", font=_sign_font,
                 foreground=self.colors['success'], background=self.colors['bg_card']).pack(side="left")
        tk.Label(row_ai, text="-", font=_sign_font,
                 foreground=self.colors['danger'], background=self.colors['bg_card']).pack(side="left")
        tk.Label(row_ai, text=_note_suffix, font=_note_font,
                 foreground=self.colors['text_muted'], background=self.colors['bg_card']).pack(side="left")

        # AI 评估超时设置（紧跟 AI 评估行下方，缩进对齐）
        row_ai_timeout = ttk.Frame(param_frame, style='TFrame')
        row_ai_timeout.pack(fill="x", pady=(0, int(5 * self.dpi_scale * self.zoom_factor)))
        ttk.Label(row_ai_timeout, text="", width=12, background=self.colors['bg_card']).pack(side="left")
        _spin_font = (FONT_FAMILY, int(12 * self.font_scale))
        _spin_w = 5
        _spin_pad = int(3 * self.dpi_scale * self.zoom_factor)
        _sub_font = (FONT_FAMILY, int(11 * self.font_scale))

        # 根据是否中转服务决定默认读取超时
        _is_relay = self._is_relay_endpoint_for_timeout()
        _default_read = 120 if _is_relay else 60
        _init_read = self.api_config.get("llm_read_timeout") or _default_read
        self.llm_read_timeout_var = tk.IntVar(value=_init_read)

        ttk.Label(row_ai_timeout, text="AI 响应超时:", font=_sub_font,
                 background=self.colors['bg_card'],
                 foreground=self.colors['text_secondary']).pack(side="left")
        ttk.Spinbox(row_ai_timeout, from_=10, to=300, increment=10, width=_spin_w,
                    textvariable=self.llm_read_timeout_var,
                    font=_spin_font).pack(side="left", padx=(_spin_pad, 0))
        ttk.Label(row_ai_timeout, text="秒", font=_sub_font,
                 background=self.colors['bg_card'],
                 foreground=self.colors['text_secondary']).pack(side="left", padx=(_spin_pad, int(10 * self.dpi_scale * self.zoom_factor)))
        _provider_key = self.api_config.get("api_provider", "")
        _provider_name = PROVIDER_DISPLAY.get(_provider_key, _provider_key)
        _model_name = self.api_config.get("model", "")
        if _is_relay:
            _label = f"中转服务/{_model_name}" if _model_name else "中转服务"
            _hint = f"（{_label}，默认 120 秒）"
        else:
            _label = f"{_provider_name}/{_model_name}" if _model_name else _provider_name
            _hint = f"（{_label}，默认 60 秒）"
        self._timeout_hint_label = ttk.Label(row_ai_timeout, text=_hint, font=_sub_font,
                 background=self.colors['bg_card'],
                 foreground=self.colors['text_muted'])
        self._timeout_hint_label.pack(side="left")

        # === 进度条 ===
        progress_frame = ttk.Frame(param_frame, style='TFrame')
        progress_frame.pack(fill="x", pady=int(15 * self.dpi_scale * self.zoom_factor))

        # 第一行：标签 + 进度条
        progress_row = ttk.Frame(progress_frame, style='TFrame')
        progress_row.pack(fill="x")

        ttk.Label(progress_row, text="筛选进度:", font=self.font_label,
                 background=self.colors['bg_card']).pack(side="left")

        # 自定义 Progressbar 样式：高度与文字对齐
        _progress_height = int(20 * self.dpi_scale * self.zoom_factor)
        _progress_style = ttk.Style()
        _progress_style.configure('Run.Horizontal.TProgressbar',
                                  thickness=_progress_height,
                                  troughcolor=self.colors['bg_input'],
                                  background=self.colors['primary'])

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_row, variable=self.progress_var,
                                            maximum=100, mode='determinate', length=400,
                                            style='Run.Horizontal.TProgressbar')
        self.progress_bar.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor), fill="x", expand=True)

        # 第二行：进度描述文字（全宽，不截断）
        self.progress_label = ttk.Label(progress_frame, text="",
                                       font=self.font_label,
                                       foreground=self.colors['primary'],
                                       anchor="w", justify="left",
                                       background=self.colors['bg_card'])
        self.progress_label.pack(fill="x", pady=(int(4 * self.dpi_scale * self.zoom_factor), 0))

        # 控制按钮区
        btn_container = ttk.Frame(control_container, style='TFrame')
        btn_container.pack(fill="x", padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(20 * self.dpi_scale * self.zoom_factor))

        # 开始/停止按钮
        icon_play_run = self.icons.button('play', self.colors['text_primary'])
        self.start_btn = ttk.Button(btn_container, image=icon_play_run, text=" 开始运行", compound=tk.LEFT, command=self.start_run, style='Accent.TButton', state="disabled")
        self.start_btn._icon_ref = icon_play_run
        self.start_btn.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))

        icon_stop = self.icons.button('stop', self.colors['text_primary'])
        self.stop_btn = ttk.Button(btn_container, image=icon_stop, text=" 停止", compound=tk.LEFT, command=self.stop_run, style='Accent.TButton', state="disabled")
        self.stop_btn._icon_ref = icon_stop
        self.stop_btn.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))

        # 状态指示器
        self.status_label = ttk.Label(btn_container, text="🟢 就绪",
                                      font=(FONT_FAMILY, int(13 * self.font_scale)), foreground=self.colors['success'])
        self.status_label.pack(side="left", padx=int(50 * self.dpi_scale * self.zoom_factor))

        # 日志区域 — 与浏览器状态卡片一致的卡片式设计
        log_card = self._create_card(content, "运行日志",
            fill="both", expand=True, padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

        log_container = ttk.Frame(log_card, style='TFrame')
        log_container.pack(fill="both", expand=True)

        # 日志文本框 - 等宽字体
        self.log_text = tk.Text(log_container, wrap="word", state="disabled",
                               font=self.font_log, bg=self.colors['bg_input'], borderwidth=0,
                               highlightthickness=0, height=20)
        self.log_text.pack(side="left", fill="both", expand=True)
        self.bind_text_context_menu(self.log_text, editable=False)

        log_scroll = ttk.Scrollbar(log_container, orient="vertical", command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=log_scroll.set)

        self.log_text.bind('<Enter>', lambda e: setattr(self, '_over_text_widget', True))
        self.log_text.bind('<Leave>', lambda e: setattr(self, '_over_text_widget', False))

        # 日志工具栏 — 放在卡片内容区底部
        log_toolbar = ttk.Frame(log_card, style='TFrame')
        log_toolbar.pack(fill="x", pady=(int(8 * self.dpi_scale * self.zoom_factor), 0))

        icon_trash_log = self.icons.button('trash', self.colors['text_primary'])
        btn_clear_log = ttk.Button(log_toolbar, image=icon_trash_log, text=" 清空日志", compound=tk.LEFT, command=self.clear_log)
        btn_clear_log._icon_ref = icon_trash_log
        btn_clear_log.pack()

        # 启动进度条更新循环
        self.update_progress()

        # 在所有控件创建完毕后绑定滚轮事件
        self._bind_mousewheel(self.run_canvas, self.run_scrollable_frame)

    def create_result_page(self):
        """创建筛选结果页面"""
        self.result_page = ttk.Frame(self.pages_frame, style='Page.TFrame')

        # 页面标题
        self._create_page_header(self.result_page, "筛选结果")

        # 岗位过滤
        filter_frame = ttk.Frame(self.result_page, style='Page.TFrame')
        filter_frame.pack(fill="x", pady=(0, int(10 * self.dpi_scale * self.zoom_factor)))
        ttk.Label(filter_frame, text="岗位过滤:", font=self.font_label,
                 background=self.colors['bg_main']).pack(side="left")
        self.result_job_var = tk.StringVar(value="全部岗位")
        self.result_job_combo = ttk.Combobox(filter_frame, textvariable=self.result_job_var,
                                              values=["全部岗位"], width=28, state="readonly",
                                              font=self.font_label)
        self.result_job_combo.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        self.result_job_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_results())

        # 日期过滤（日历控件）
        ttk.Label(filter_frame, text="日期:", font=self.font_label,
                 background=self.colors['bg_main']).pack(side="left", padx=int(20 * self.dpi_scale * self.zoom_factor))
        _cal_font = (FONT_FAMILY, int(11 * self.font_scale))
        _cal_kw = dict(width=12, font=_cal_font, date_pattern='yyyy-mm-dd',
                       showweeknumbers=False)
        self.result_date_start_entry = self._create_result_date_entry(filter_frame, **_cal_kw)
        self.result_date_start_entry.pack(side="left", padx=int(4 * self.dpi_scale * self.zoom_factor))
        self.result_date_start_entry.bind("<<DateEntrySelected>>",
                                          lambda e: self._validate_date_range('start'))
        self.result_date_start_entry.bind("<Return>", lambda e: self._validate_date_range('start'))
        self.result_date_start_entry.bind("<FocusOut>", lambda e: self._validate_date_range('start'))

        ttk.Label(filter_frame, text="~", font=self.font_label,
                 background=self.colors['bg_main']).pack(side="left", padx=int(2 * self.dpi_scale * self.zoom_factor))

        self.result_date_end_entry = self._create_result_date_entry(filter_frame, **_cal_kw)
        self.result_date_end_entry.pack(side="left", padx=int(4 * self.dpi_scale * self.zoom_factor))
        self.result_date_end_entry.bind("<<DateEntrySelected>>",
                                        lambda e: self._validate_date_range('end'))
        self.result_date_end_entry.bind("<Return>", lambda e: self._validate_date_range('end'))
        self.result_date_end_entry.bind("<FocusOut>", lambda e: self._validate_date_range('end'))

        # 默认日期范围：一周前 ~ 今天
        _today = datetime.now().date()
        self.result_date_start_entry.set_date(_today - timedelta(days=7))
        self.result_date_end_entry.set_date(_today)

        # 互斥关闭：展开一个日历前收起另一个，避免两个弹层同时存在。
        self._wrap_date_dropdown_mutex(self.result_date_start_entry, self.result_date_end_entry)
        self._wrap_date_dropdown_mutex(self.result_date_end_entry, self.result_date_start_entry)

        ttk.Button(filter_frame, text="重置日期", command=self._clear_result_dates).pack(
            side="left", padx=(int(15 * self.dpi_scale * self.zoom_factor), int(8 * self.dpi_scale * self.zoom_factor)))

        # 统计卡片区（纵向卡片布局）
        stats_container = ttk.Frame(self.result_page, style='Page.TFrame')
        stats_container.pack(fill="x", pady=int(15 * self.dpi_scale * self.zoom_factor))

        self.result_stats_vars = {}
        self.result_stats_greeted = {}
        self.result_stats_click = {}
        stats_data = [
            ("strong_recommend", "强烈推荐", "strong", self.colors['purple']),
            ("thumbs_up", "推荐", "recommended", self.colors['success']),
            ("hourglass", "待定", "pending", self.colors['pending']),
            ("chat", "已打招呼", "greeted", self.colors['warning']),
        ]

        for icon_name, label_text, var_name, color in stats_data:
            card_frame = ttk.Frame(stats_container, style='Card.TFrame')
            card_frame.pack(side="left", fill="x", expand=True, padx=int(12 * self.dpi_scale * self.zoom_factor))

            # 彩色圆形图标（大号）
            icon_size = int(UI_CONFIG['stat_icon_size'] * self.dpi_scale * self.zoom_factor)
            icon_canvas = tk.Canvas(card_frame, width=icon_size, height=icon_size,
                                    bg=self.colors['bg_card'], highlightthickness=0)
            icon_canvas.pack(anchor="center",
                            pady=(int(12 * self.dpi_scale * self.zoom_factor), int(4 * self.dpi_scale * self.zoom_factor)))
            margin = int(UI_CONFIG['icon_margin'] * self.dpi_scale * self.zoom_factor)
            icon_canvas.create_oval(margin, margin, icon_size - margin, icon_size - margin,
                                    fill=color, outline='')
            stat_icon = self.icons.stat(icon_name, 'white')
            icon_canvas.create_image(icon_size // 2, icon_size // 2, image=stat_icon)
            icon_canvas._icon_ref = stat_icon

            # 数值
            var = tk.StringVar(value="0")
            self.result_stats_vars[var_name] = var
            value_label = ttk.Label(card_frame, textvariable=var, font=self.font_stat,
                                   foreground=color, background=self.colors['bg_card'],
                                   cursor="hand2")
            value_label.pack(anchor="center", pady=(0, int(2 * self.dpi_scale * self.zoom_factor)))

            # 已打招呼
            greeted_var = tk.StringVar(
                value="通过筛选中" if var_name == "greeted" else "0 已打招呼"
            )
            self.result_stats_greeted[var_name] = greeted_var
            greeted_label = ttk.Label(card_frame, textvariable=greeted_var,
                                     font=(FONT_FAMILY, int(10 * self.font_scale)),
                                     foreground=self.colors['success'], background=self.colors['bg_card'])
            greeted_label.pack(anchor="center", pady=(0, int(2 * self.dpi_scale * self.zoom_factor)))

            # 标签
            label = ttk.Label(card_frame, text=label_text, font=self.font_stat_label,
                             foreground=self.colors['text_secondary'], background=self.colors['bg_card'])
            label.pack(anchor="center", pady=(0, int(10 * self.dpi_scale * self.zoom_factor)))

            # 绑定点击事件
            self.result_stats_click[var_name] = label_text
            value_label.bind("<Button-1>", lambda e, vt=var_name: self.show_result_stat_detail(vt))
            label.bind("<Button-1>", lambda e, vt=var_name: self.show_result_stat_detail(vt))

        # 搜索框 — 位于统计卡片和候选人列表之间
        search_frame = ttk.Frame(self.result_page, style='Page.TFrame')
        search_frame.pack(fill="x", pady=(int(12 * self.dpi_scale * self.zoom_factor), int(6 * self.dpi_scale * self.zoom_factor)))
        ttk.Label(search_frame, text="搜索:", font=self.font_label,
                 background=self.colors['bg_main']).pack(side="left")
        self.result_search_var = tk.StringVar()
        self.result_search_var.trace_add('write', lambda *_: self._filter_result_tree())
        self.result_search_entry = ttk.Entry(
            search_frame, textvariable=self.result_search_var, width=16, font=self.font_label)
        self.result_search_entry.pack(side="left", padx=int(6 * self.dpi_scale * self.zoom_factor))
        ttk.Label(search_frame, text="（姓名/匹配分/推荐指数/状态，Esc 清空）",
                 font=(FONT_FAMILY, int(10 * self.font_scale)),
                 foreground=self.colors.get('text_secondary', '#666'),
                 background=self.colors['bg_main']).pack(side="left", padx=int(4 * self.dpi_scale * self.zoom_factor))
        self.result_search_entry.bind('<Escape>', lambda e: self.result_search_var.set(''))

        # 显示已屏蔽候选人开关（搜索栏最右侧）
        self.result_show_blacklist_var = tk.BooleanVar(value=False)
        _cb_style = ttk.Style()
        _cb_style.configure("Blacklist.TCheckbutton", font=self.font_label)
        blacklist_check = ttk.Checkbutton(
            search_frame, text="显示已屏蔽", variable=self.result_show_blacklist_var,
            command=lambda: self.refresh_results(), style="Blacklist.TCheckbutton")
        blacklist_check.pack(side="right", padx=int(15 * self.dpi_scale * self.zoom_factor))

        # 结果表格
        table_container = ttk.Frame(self.result_page, style='Card.TFrame')
        table_container.pack(fill="both", expand=True, pady=int(8 * self.dpi_scale * self.zoom_factor))

        # 表格
        columns = ("name", "exp", "salary", "skills", "score", "ai_eval", "level", "status",
                   "education", "age", "job_status", "school", "company")
        base_display_columns = ("name", "exp", "salary", "skills", "score", "ai_eval", "level", "status")
        self.result_tree = ttk.Treeview(
            table_container,
            columns=columns,
            displaycolumns=base_display_columns,
            show="headings",
            height=4,
        )

        self.result_tree.heading("name", text="姓名")
        self.result_tree.heading("exp", text="工作年限")
        self.result_tree.heading("salary", text="薪资")
        self.result_tree.heading("skills", text="技能匹配")
        self.result_tree.heading("score", text="匹配分")
        self.result_tree.heading("ai_eval", text="AI评估")
        self.result_tree.heading("level", text="推荐指数")
        self.result_tree.heading("status", text="状态")
        self.result_tree.heading("education", text="学历")
        self.result_tree.heading("age", text="年龄")
        self.result_tree.heading("job_status", text="求职状态")
        self.result_tree.heading("school", text="毕业学校")
        self.result_tree.heading("company", text="最近公司")

        # 普通窗口 8 列；最大化显示 11 列；表格足够宽时再显示学校和公司。
        self.result_tree.column("name", width=80, minwidth=60, anchor='center')
        self.result_tree.column("exp", width=85, minwidth=70, anchor='center')
        self.result_tree.column("salary", width=85, minwidth=70, anchor='center')
        self.result_tree.column("skills", width=85, minwidth=70, anchor='center')
        self.result_tree.column("score", width=70, minwidth=60, anchor='center')
        self.result_tree.column("ai_eval", width=70, minwidth=60, anchor='center')
        self.result_tree.column("level", width=80, minwidth=70, anchor='center')
        self.result_tree.column("status", width=180, minwidth=150, anchor='center')
        self.result_tree.column("education", width=140, minwidth=115, anchor='center')
        self.result_tree.column("age", width=110, minwidth=90, anchor='center')
        self.result_tree.column("job_status", width=120, minwidth=80, anchor='center')
        self.result_tree.column("school", width=150, minwidth=120, anchor='center')
        self.result_tree.column("company", width=170, minwidth=130, anchor='center')

        # 设置表格字体和样式
        style = ttk.Style()
        style.configure("Result.Treeview", font=self.font_table, rowheight=int(UI_CONFIG['treeview_rowheight'] * self.dpi_scale * self.zoom_factor))
        style.configure("Result.Treeview.Heading", font=(FONT_FAMILY, int(12 * self.font_scale), 'bold'))
        self.result_tree.configure(style="Result.Treeview")

        self._update_result_tree_columns()

        tree_scroll = ttk.Scrollbar(table_container, orient="vertical", command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=tree_scroll.set)

        pad_x = int(20 * self.dpi_scale * self.zoom_factor)
        pad_y = int(12 * self.dpi_scale * self.zoom_factor)
        self.result_tree.pack(
            side="left", fill="both", expand=True,
            padx=pad_x, pady=pad_y,
        )
        self.result_tree.bind(
            "<Configure>",
            lambda _event: self._schedule_page_width_policy(),
            add="+",
        )
        tree_scroll.pack(side="right", fill="y", pady=int(10 * self.dpi_scale * self.zoom_factor))

        # 操作按钮 - 放在表格下方
        btn_frame = ttk.Frame(self.result_page, style='Page.TFrame')
        btn_frame.pack(fill="x", padx=int(20 * self.dpi_scale * self.zoom_factor), pady=(int(8 * self.dpi_scale * self.zoom_factor), int(12 * self.dpi_scale * self.zoom_factor)))
        btn_inner = ttk.Frame(btn_frame, style='Page.TFrame')
        btn_inner.pack(anchor="center")

        icon_refresh_result = self.icons.button('refresh', self.colors['text_primary'])
        btn_refresh = ttk.Button(btn_inner, image=icon_refresh_result, text=" 刷新结果", compound=tk.LEFT, command=self.refresh_results)
        btn_refresh._icon_ref = icon_refresh_result
        btn_refresh.pack(side="left", padx=int(8 * self.dpi_scale * self.zoom_factor))
        icon_chart_excel = self.icons.button('chart', self.colors['text_primary'])
        btn_excel = ttk.Button(btn_inner, image=icon_chart_excel, text=" 导出 Excel", compound=tk.LEFT, command=self.export_excel)
        btn_excel._icon_ref = icon_chart_excel
        btn_excel.pack(side="left", padx=int(8 * self.dpi_scale * self.zoom_factor))
        icon_folder_json = self.icons.button('folder', self.colors['text_primary'])
        btn_json = ttk.Button(btn_inner, image=icon_folder_json, text=" 打开 JSON", compound=tk.LEFT, command=self.open_json)
        btn_json._icon_ref = icon_folder_json
        btn_json.pack(side="left", padx=int(8 * self.dpi_scale * self.zoom_factor))

        icon_clear = self.icons.button('trash', self.colors['danger'])
        btn_clear = ttk.Button(btn_inner, image=icon_clear, text=" 清空候选人", compound=tk.LEFT, command=self.clear_candidates)
        btn_clear._icon_ref = icon_clear
        btn_clear.pack(side="left", padx=int(8 * self.dpi_scale * self.zoom_factor))

    def create_education_page(self):
        """创建学历核验页面。"""
        self.education_page = ttk.Frame(self.pages_frame, style='Page.TFrame')
        self.education_canvas, self.education_scrollable_frame = self._create_scroll_container(
            self.education_page, self.colors['bg_main']
        )
        content = self.education_scrollable_frame
        self._create_page_header(
            content,
            "学历核验",
            "导入毕业证书图片/PDF，识别姓名和证书编号；验证码与手机扫码由 HR 人工完成。",
        )

        toolbar = self._create_card(
            content, "毕业证书", fill="x",
            pady=(0, int(16 * self.dpi_scale * self.zoom_factor)),
        )
        self.education_items = {}
        self.education_current_id = None
        self.education_item_counter = 0
        self.education_recognition_running = False
        self.education_manual_rotation: dict[str, int] = {}
        self.education_rotation_locked: set[str] = set()
        self.education_file_var = tk.StringVar(value="尚未导入毕业证书")
        ttk.Label(
            toolbar, textvariable=self.education_file_var, font=self.font_label,
            foreground=self.colors['text_secondary'],
        ).pack(side="left", fill="x", expand=True)
        remove_icon = self.icons.button('trash', self.colors['danger'])
        self.education_remove_btn = ttk.Button(
            toolbar, text=" 移除当前", image=remove_icon, compound=tk.LEFT,
            command=self._remove_current_education_image, state="disabled",
        )
        self.education_remove_btn._icon_ref = remove_icon
        self.education_remove_btn.pack(side="right", padx=(10, 0))
        select_icon = self.icons.button('folder', self.colors['text_primary'])
        select_btn = ttk.Button(
            toolbar, text=" 导入证书", image=select_icon, compound=tk.LEFT,
            command=self._select_education_images,
        )
        select_btn._icon_ref = select_icon
        select_btn.pack(side="right")

        queue_card = self._create_card(
            content, "待核验队列",
            fill="x",
            pady=(0, int(16 * self.dpi_scale * self.zoom_factor)),
        )
        self.education_queue_card = queue_card.master
        queue_columns = ("file", "name", "number", "school", "major", "status")
        education_style = ttk.Style()
        education_style.configure(
            "Education.Treeview",
            font=(FONT_FAMILY, int(10 * self.font_scale)),
            rowheight=int(30 * self.dpi_scale * self.zoom_factor),
        )
        education_style.configure(
            "Education.Treeview.Heading",
            font=(FONT_FAMILY, int(11 * self.font_scale), "bold"),
        )
        self._education_tree_font = font.Font(
            family=FONT_FAMILY, size=int(10 * self.font_scale)
        )
        self.education_queue_tree = ttk.Treeview(
            queue_card, columns=queue_columns, show="headings",
            height=5, selectmode="extended", style="Education.Treeview",
        )
        for column, title, width in (
            ("file", "文件", 230),
            ("name", "姓名", 120),
            ("number", "证书编号", 160),
            ("school", "学校", 175),
            ("major", "专业", 210),
            ("status", "状态", 140),
        ):
            self.education_queue_tree.heading(column, text=title)
            self.education_queue_tree.column(
                column, width=width, minwidth=80,
                anchor="w" if column == "file" else "center",
                stretch=column in ("file", "number", "school", "major"),
            )
        queue_scroll = ttk.Scrollbar(
            queue_card, orient="vertical", command=self.education_queue_tree.yview
        )
        self.education_queue_tree.configure(yscrollcommand=queue_scroll.set)
        self.education_queue_tree.pack(side="left", fill="x", expand=True)
        queue_scroll.pack(side="right", fill="y")
        self.education_queue_tree.bind(
            "<<TreeviewSelect>>", self._on_education_queue_select
        )
        self.education_queue_tree.bind(
            "<Motion>", self._on_education_queue_motion, add="+"
        )
        self.education_queue_tree.bind("<Leave>", self._hide_tooltip, add="+")
        self.education_queue_tree.bind(
            "<Button-3>", self._show_education_queue_context_menu
        )
        self.education_queue_tree.bind(
            "<Configure>",
            lambda _event: self._update_education_queue_columns(),
            add="+",
        )
        self.education_queue_menu = tk.Menu(
            self.root, tearoff=0,
            font=(FONT_FAMILY, int(11 * self.font_scale)),
        )
        self.education_queue_menu.add_command(
            label="识别证书", command=self._recognize_education_image
        )
        self.education_queue_menu.add_command(
            label="删除证书", command=self._remove_selected_education_images
        )
        self._context_menus.append(self.education_queue_menu)

        workspace = ttk.Frame(content, style='Page.TFrame')
        self.education_workspace = workspace
        workspace.pack(fill="both", expand=True)

        # 顺转 90° 构造器：注入到预览卡片标题栏右侧
        # 用 tk.Label + 点击绑定代替 ttk.Button —— 高度严格等于标题文字高度，绝不撑高标题栏
        def _build_rotate_button(title_bar, padding):
            title_bg = title_bar.cget("bg")
            self.education_rotate_btn = tk.Label(
                title_bar, text="顺转 90°",
                font=self.font_label,
                fg=self.colors['primary'], bg=title_bg,
                cursor="hand2",
            )
            self.education_rotate_btn.pack(side="right", padx=padding)
            self.education_rotate_btn.bind(
                "<Button-1>", lambda _e: self._rotate_education_image_cw90()
            )

        # 左侧预览卡片（按钮在标题栏内，预览区全部留给图片）
        preview = self._create_card(
            workspace, "证书预览", fill="both", expand=True, side="left",
            title_trailing_builder=_build_rotate_button,
        )

        self.education_preview_label = tk.Label(
            preview, text="请选择 JPG、JPEG、PNG、BMP、WEBP 图片或 PDF 文件",
            bg=self.colors['bg_card'], fg=self.colors['text_secondary'],
            font=self.font_label, justify="center",
        )
        self.education_preview_label.bind(
            "<Configure>", lambda _event: self._render_education_preview()
        )
        self.education_preview_label.pack(fill="both", expand=True)

        # 右侧识别结果卡片
        form = self._create_card(
            workspace, "识别结果", fill="both", expand=True, side="left",
            padx=(int(16 * self.dpi_scale * self.zoom_factor), 0),
        )

        self.education_name_var = tk.StringVar()
        self.education_number_var = tk.StringVar()
        self.education_status_var = tk.StringVar(value="等待选择证书")
        self.education_warning_var = tk.StringVar(value="")

        ttk.Label(form, text="姓名", font=self.font_label).pack(anchor="w")
        name_entry = ttk.Entry(form, textvariable=self.education_name_var, font=self.font_label)
        name_entry.pack(fill="x", pady=(6, 16))
        self.bind_entry_context_menu(name_entry)
        ttk.Label(form, text="证书编号", font=self.font_label).pack(anchor="w")
        number_entry = ttk.Entry(form, textvariable=self.education_number_var, font=self.font_label)
        number_entry.pack(fill="x", pady=(6, 16))
        self.bind_entry_context_menu(number_entry)

        ttk.Label(
            form, textvariable=self.education_status_var, font=self.font_label,
            foreground=self.colors['primary'],
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(
            form, textvariable=self.education_warning_var,
            font=(FONT_FAMILY, int(10 * self.font_scale)),
            foreground=self.colors['warning'], wraplength=600, justify="left",
        ).pack(anchor="w", fill="x")

        actions = ttk.Frame(form, style='TFrame')
        actions.pack(fill="x", pady=(22, 0))
        recognize_icon = self.icons.button('search', self.colors['text_primary'])
        self.education_recognize_btn = ttk.Button(
            actions, text=" 识别证书", image=recognize_icon, compound=tk.LEFT,
            command=self._recognize_education_image, state="disabled",
        )
        self.education_recognize_btn._icon_ref = recognize_icon
        self.education_recognize_btn.pack(side="left")
        fill_icon = self.icons.button('play', self.colors['text_primary'])
        self.education_fill_btn = ttk.Button(
            actions, text=" 打开学信网验证", image=fill_icon, compound=tk.LEFT,
            command=self._fill_chsi_page, state="disabled",
        )
        self.education_fill_btn._icon_ref = fill_icon
        self.education_fill_btn.pack(side="left", padx=(10, 0))
        captcha_icon = self.icons.button('refresh', self.colors['text_primary'])
        self.education_captcha_btn = ttk.Button(
            actions, text=" 重新识别验证码", image=captcha_icon, compound=tk.LEFT,
            command=self._solve_captcha, state="disabled",
        )
        self.education_captcha_btn._icon_ref = captcha_icon
        self.education_captcha_btn.pack(side="left", padx=(10, 0))

        ttk.Label(
            form,
            text="识别时图片/PDF 会发送当前配置的 AI 模型，请确认已取得候选人授权。",
            font=(FONT_FAMILY, int(10 * self.font_scale)),
            foreground=self.colors['text_secondary'], justify="left",
        ).pack(anchor="w", fill="x", pady=(20, 0))
        self.education_queue_card.pack_forget()
        self._bind_mousewheel(self.education_canvas, self.education_scrollable_frame)

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
        existing_paths = {
            str(Path(item["path"]).resolve()).lower()
            for item in self.education_items.values()
        }
        added_ids = []
        invalid_files = []
        for raw_path in paths:
            try:
                path = validate_document_path(raw_path)
            except ValueError:
                invalid_files.append(Path(raw_path).name)
                continue
            normalized = str(path.resolve()).lower()
            if normalized in existing_paths:
                continue
            existing_paths.add(normalized)
            self.education_item_counter += 1
            item_id = f"education_{self.education_item_counter}"
            self.education_items[item_id] = {
                "path": str(path),
                "is_pdf": is_pdf_path(path),
                "name": "",
                "certificate_number": "",
                "school": "",
                "major": "",
                "auto_rotation": 0,
                "status": "待识别",
                "detail": "",
                "warnings": "",
            }
            self.education_queue_tree.insert(
                "", "end", iid=item_id,
                values=(path.name, "", "", "", "", "待识别"),
            )
            added_ids.append(item_id)

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
                    messagebox.showwarning(
                        "模型可能不支持图片识别",
                        f"当前模型「{model_name}」可能不支持图片输入。\n\n"
                        "图片识别需要多模态视觉模型，如：\n"
                        "  国外：GPT-4o / GPT-4.1、Claude Sonnet 4、Gemini 2.5 Pro\n"
                        "  国内：qwen3.7-plus、mimo-v2.5、GLM-5、Kimi K2、MiniMax-M2.7\n\n"
                        "PDF 文件使用文本提取，不受此限制。\n\n"
                        "请在「API 配置」中切换支持图片的模型后再识别。",
                        parent=self.root,
                    )
        if invalid_files:
            messagebox.showwarning(
                "部分文件未导入",
                "以下文件不是支持的格式（图片或 PDF）：\n" + "\n".join(invalid_files[:10]),
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
            self._hide_tooltip()
            return
        values = tree.item(item_id, "values")
        if len(values) <= value_index:
            self._hide_tooltip()
            return
        full_text = str(values[value_index] or "")
        cell_bbox = tree.bbox(item_id, column_id)
        if (
            not full_text
            or not cell_bbox
            or self._education_tree_font.measure(full_text) <= max(0, cell_bbox[2] - 12)
        ):
            self._hide_tooltip()
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
            300, lambda: self._show_tooltip(full_text, x, y, tooltip_key)
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
            from PIL import Image, ImageOps, ImageTk
            rotation_locked = getattr(self, "education_rotation_locked", set())
            if item_id in rotation_locked:
                display_angle = self.education_manual_rotation.get(item_id, 0)
            else:
                display_angle = int((item or {}).get("auto_rotation", 0) or 0)
            with Image.open(path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                if display_angle:
                    image = image.rotate(
                        -display_angle, expand=True, resample=Image.Resampling.BICUBIC
                    )
                width = max(320, label.winfo_width() - 20)
                height = max(320, label.winfo_height() - 20)
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
        """获取学历核验使用的 API 配置。优先 education_model_ref，回退全局模型。"""
        edu_ref = (self.api_config or {}).get("education_model_ref")
        if edu_ref and edu_ref.get("model"):
            return dict(edu_ref)
        return dict(self.api_config or {})

    def _recognize_education_image(self):
        """最多三路并发识别当前选中的毕业证书。"""
        self._save_current_education_fields()
        item_ids = self._selected_education_item_ids()
        if not item_ids:
            messagebox.showinfo("请选择图片", "请先选择毕业证书。", parent=self.root)
            return
        if self.education_recognition_running:
            return
        # 学历核验专用配置（优先 education_model_ref，回退全局模型）
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
                if not messagebox.askyesno(
                    "模型可能不支持图片识别",
                    f"当前学历核验模型「{model_name}」可能不支持图片输入。\n\n"
                    "图片识别需要多模态视觉模型，如：\n"
                    "  国外：GPT-4o / GPT-4.1、Claude Sonnet 4、Gemini 2.5 Pro\n"
                    "  国内：qwen3.7-plus、mimo-v2.5、GLM-5、Kimi K2、MiniMax-M2.7\n\n"
                    "PDF 文件使用文本提取，不受此限制。\n\n"
                    "可在系统设置的已保存模型列表中右键指定学历核验专用模型。\n\n"
                    "是否仍要尝试识别？",
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
            results = {}
            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                from education_certificate import (
                    recognize_certificate_image,
                    recognize_certificate_pdf,
                )
                config = vision_config
                api_key = self._get_education_api_key(config)
                workers = min(3, len(item_ids))

                def recognize_one(item_id):
                    item = self.education_items[item_id]
                    path = item["path"]
                    if item.get("is_pdf"):
                        return recognize_certificate_pdf(path, config, api_key or "")
                    return recognize_certificate_image(path, config, api_key or "")

                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(recognize_one, item_id): item_id
                        for item_id in item_ids
                        if item_id in self.education_items
                    }
                    for future in as_completed(futures):
                        item_id = futures[future]
                        try:
                            results[item_id] = (future.result(), "")
                        except Exception as error:
                            results[item_id] = (None, str(error))
            except Exception as error:
                error_text = str(error)
                for item_id in item_ids:
                    results.setdefault(item_id, (None, error_text))

            def show_results():
                for item_id, (result, error_text) in results.items():
                    queue_item = self.education_items.get(item_id)
                    if not queue_item:
                        continue
                    if result is not None:
                        queue_item["name"] = result.name
                        queue_item["certificate_number"] = result.certificate_number
                        queue_item["school"] = result.school
                        queue_item["major"] = result.major
                        queue_item["auto_rotation"] = result.rotation
                        queue_item["status"] = "已识别"
                        queue_item["detail"] = (
                            f"识别完成 · 置信度 {result.confidence}% · {result.model}"
                        )
                        queue_item["warnings"] = "；".join(result.warnings)
                    else:
                        queue_item["status"] = "识别失败"
                        queue_item["detail"] = "识别失败"
                        queue_item["warnings"] = error_text
                    self._update_education_queue_row(item_id)
                current = self.education_items.get(self.education_current_id)
                if current and self.education_current_id in results:
                    self.education_name_var.set(current.get("name", ""))
                    self.education_number_var.set(current.get("certificate_number", ""))
                    self.education_status_var.set(current.get("detail", ""))
                    self.education_warning_var.set(current.get("warnings", ""))
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
        prepared: list[tuple[str, str, str]] = []
        for item_id in item_ids:
            item = self.education_items.get(item_id)
            if not item:
                continue
            try:
                name, certificate_number = validate_chsi_fields(
                    item.get("name", ""), item.get("certificate_number", ""),
                )
            except ValueError as error:
                item["status"] = "校验失败"
                item["detail"] = str(error)
                item["warnings"] = ""
                self._update_education_queue_row(item_id)
                continue
            prepared.append((item_id, name, certificate_number))

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

                max_retries = 2
                for attempt in range(max_retries + 1):
                    try:
                        on_progress("正在加载学信网页面...", "")
                        success, status = self._fill_and_solve_captcha(
                            page, n, cn, on_progress=on_progress,
                        )
                        # 如果成功或不是验证码错误，跳出重试循环
                        if success or status != "识别失败":
                            break
                        # 验证码错误，准备重试
                        if attempt < max_retries:
                            with self._education_browser_lock:
                                # 点击验证码图片刷新
                                try:
                                    page.run_js("""
                                    const input = document.querySelector('input[name="yzm"]');
                                    if (input) {
                                      const selectors = [
                                        '.yzm-box', '.captcha-box', '.verify-img', '.imgCode'
                                      ];
                                      for (const sel of selectors) {
                                        const c = input.closest(sel);
                                        if (c) {
                                          const img = c.querySelector('img');
                                          if (img) { img.click(); break; }
                                        }
                                      }
                                    }
                                    """)
                                except Exception:
                                    pass
                            time.sleep(2)
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
                        return  # 异常退出重试循环
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
                        queue_item["detail"] = f"验证码识别错误（已重试 {max_retries} 次），请点击「重新识别验证码」重试。"
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
    ) -> tuple[bool, str]:
        """填写表单 + 截取验证码 + AI 识别 + 填入 + 查询。

        on_progress(detail, status): 每个阶段完成时回调，用于实时更新 GUI 状态。

        返回 (success, status):
        - (True, "已提交查询"): 查询成功，等待扫码
        - (False, "识别失败"): 验证码识别错误，可重试
        - (False, "待人工验证"): 识别过程失败，需人工输入
        """
        from education_certificate import fill_chsi_query_page, navigate_to_chsi
        _emit = on_progress or (lambda *_: None)
        try:
            # 页面导航在锁外执行，各 tab 并行加载（3-8 秒网络 I/O）
            navigate_to_chsi(page)
            _emit("正在填写表单...", "正在填写姓名和证书编号")
            with self._education_browser_lock:
                # 仅 JS 注入在锁内（<100ms）
                fill_chsi_query_page(page, name, certificate_number, skip_navigation=True)
            return self._attempt_captcha_solve(page, on_progress=on_progress)
        except Exception as e:
            print(f"[验证码识别] 失败：{e}")
            return False, "待人工验证"

    def _attempt_captcha_solve(
        self, page, *, on_progress: "Callable[[str, str], None] | None" = None,
    ) -> tuple[bool, str]:
        """截取验证码图片、调用模型识别并自动填入+查询。

        on_progress(detail, status): 每个阶段完成时回调。

        返回 (success, status):
        - (True, "已提交查询"): 查询成功
        - (False, "识别失败"): 验证码错误
        - (False, "待人工验证"): 识别过程失败
        """
        from education_certificate import (
            capture_captcha_image, click_chsi_query_button,
            fill_captcha_answer, recognize_captcha,
            resolve_vision_api_config,
        )
        _emit = on_progress or (lambda *_: None)
        data_url = None
        vision_config = None
        api_key = ""
        try:
            # 阶段 1：截取验证码图片（需要浏览器锁）
            _emit("正在识别验证码...", "正在截取验证码图片")
            with self._education_browser_lock:
                config = self._get_education_api_config()
                vision_config = resolve_vision_api_config(config)
                api_key = self._get_education_api_key(config)
                data_url = capture_captcha_image(page)
        except Exception as e:
            print(f"[验证码识别] 截取验证码图片失败：{e}")
            return False, "待人工验证"

        # 阶段 2：AI 识别（释放浏览器锁，允许其他 tab 操作）
        _emit("正在识别验证码...", "AI 模型识别中")
        try:
            captcha_type, answer, confidence = recognize_captcha(
                data_url, vision_config, api_key,
            )
        except Exception as e:
            print(f"[验证码识别] AI 识别失败：{e}")
            return False, "待人工验证"

        if captcha_type == "unknown" or not answer:
            return False, "待人工验证"

        # 阶段 3：填入答案 + 点击查询（重新获取浏览器锁）
        _emit("正在提交查询...", "验证码已识别，正在提交")
        try:
            with self._education_browser_lock:
                filled = fill_captcha_answer(page, answer)
                if not filled:
                    return False, "待人工验证"
                import time
                time.sleep(0.5)
                click_chsi_query_button(page)
            # 点击后立即释放锁、立即推状态；check_query_result 的等待放到锁外
            # （各 worker 操作独立 tab，check 期间无需持有锁）
            _emit("已提交查询", "正在等待页面响应...")
            from education_certificate import check_query_result
            success, message = check_query_result(page)
            if not success:
                return False, "识别失败"
            return True, "已提交查询"
        except Exception as e:
            print(f"[验证码识别] 填入/查询失败：{e}")
            return False, "待人工验证"

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
                    # 点击验证码图片刷新（避免识别旧验证码）— 需要浏览器锁
                    try:
                        with self._education_browser_lock:
                            page.run_js("""
                            const input = document.querySelector('input[name="yzm"]');
                            if (input) {
                              const selectors = [
                                '.yzm-box', '.captcha-box', '.verify-img', '.imgCode'
                              ];
                              for (const sel of selectors) {
                                const c = input.closest(sel);
                                if (c) {
                                  const img = c.querySelector('img');
                                  if (img) { img.click(); break; }
                                }
                              }
                            }
                            """)
                        import time
                        time.sleep(1)
                    except Exception:
                        pass
                    def _retry_progress(status_text: str, detail: str, iid=iid):
                        def _update(iid=iid, s=status_text, d=detail):
                            item = self.education_items.get(iid)
                            if item:
                                item["status"] = s
                                item["detail"] = d
                                self._update_education_queue_row(iid)
                        self.run_on_ui(_update)
                    success, status = self._attempt_captcha_solve(
                        page, on_progress=_retry_progress,
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
                                item["detail"] = "验证码识别错误，请再次点击「重新识别验证码」重试。"
                                item["warnings"] = ""
                            else:
                                item["status"] = "待人工验证"
                                item["detail"] = "验证码自动识别失败，请人工输入。"
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
        """确认 DrissionPage 页面对象仍能执行命令。"""
        if page is None:
            return False
        try:
            page.run_js("return 1")
            return True
        except Exception:
            return False

    def _get_education_api_key(self, config: dict) -> str:
        """按运行模式取得学历核验专用 API Key。"""
        if self._education_api_key_provider is not None:
            return str(self._education_api_key_provider() or "")
        provider = str(config.get("api_provider") or "")
        if not provider:
            return ""
        return str(get_api_key(provider, config.get("base_url", "")) or "")

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
        self.stats_page = ttk.Frame(self.pages_frame, style='Page.TFrame')

        # 页面标题
        self._create_page_header(self.stats_page, "数据统计")

        # 过滤条件行
        filter_frame = ttk.Frame(self.stats_page, style='Page.TFrame')
        filter_frame.pack(fill="x", pady=(0, int(15 * self.dpi_scale * self.zoom_factor)))

        ttk.Label(filter_frame, text="岗位过滤:", font=self.font_label,
                 background=self.colors['bg_main']).pack(side="left")
        self.stats_job_var = tk.StringVar(value="全部岗位")
        self.stats_job_combo = ttk.Combobox(filter_frame, textvariable=self.stats_job_var,
                                             values=["全部岗位"], width=28, state="readonly",
                                             font=self.font_label)
        self.stats_job_combo.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        self.stats_job_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_stats())

        # 时间维度过滤
        ttk.Label(filter_frame, text="时间范围:", font=self.font_label,
                 background=self.colors['bg_main']).pack(side="left", padx=int(30 * self.dpi_scale * self.zoom_factor))
        self.stats_time_var = tk.StringVar(value="全部")
        time_combo = ttk.Combobox(filter_frame, textvariable=self.stats_time_var,
                                   values=["今天", "本周", "本月", "全部"], width=12, state="readonly",
                                   font=self.font_label)
        time_combo.pack(side="left", padx=int(15 * self.dpi_scale * self.zoom_factor))
        time_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_stats())

        # 汇总统计卡片
        summary_container = ttk.Frame(self.stats_page, style='Page.TFrame')
        summary_container.pack(fill="x", pady=int(10 * self.dpi_scale * self.zoom_factor))

        self.stats_summary_vars = {}
        summary_items = [
            ("passed_filter", "通过筛选", "total", self.colors['primary']),
            ("strong_recommend", "强烈推荐", "strong", self.colors['purple']),
            ("thumbs_up", "推荐", "recommended", self.colors['success']),
            ("chat", "已打招呼", "greeted", self.colors['warning']),
        ]

        for icon_name, label_text, var_name, color in summary_items:
            card = ttk.Frame(summary_container, style='Card.TFrame')
            card.pack(side="left", fill="x", expand=True, padx=int(10 * self.dpi_scale * self.zoom_factor),
                     pady=int(10 * self.dpi_scale * self.zoom_factor))

            # 图标容器
            icon_size = int(UI_CONFIG['stat_icon_size'] * self.dpi_scale * self.zoom_factor)
            icon_canvas = tk.Canvas(card, width=icon_size, height=icon_size,
                                   bg=self.colors['bg_card'], highlightthickness=0)
            icon_canvas.pack(pady=(int(12 * self.dpi_scale * self.zoom_factor), int(5 * self.dpi_scale * self.zoom_factor)))

            margin = int(UI_CONFIG['icon_margin'] * self.dpi_scale * self.zoom_factor)
            icon_canvas.create_oval(margin, margin, icon_size - margin, icon_size - margin,
                                   fill=color, outline='')

            stat_icon = self.icons.stat(icon_name, 'white')
            icon_canvas.create_image(icon_size // 2, icon_size // 2, image=stat_icon)
            icon_canvas._icon_ref = stat_icon

            # 数值
            var = tk.StringVar(value="0")
            self.stats_summary_vars[var_name] = var
            value_label = ttk.Label(card, textvariable=var, font=self.font_stat,
                                   foreground=color, background=self.colors['bg_card'])
            value_label.pack(pady=(0, int(4 * self.dpi_scale * self.zoom_factor)))

            # 标签
            text_label = ttk.Label(card, text=label_text, font=self.font_stat_label,
                                  foreground=self.colors['text_secondary'],
                                  background=self.colors['bg_card'])
            text_label.pack(pady=(0, int(12 * self.dpi_scale * self.zoom_factor)))

        # 岗位明细表格
        table_label = ttk.Label(self.stats_page, text="岗位明细",
                               font=self.font_section, foreground=self.colors['text_primary'],
                               background=self.colors['bg_main'])
        table_label.pack(anchor="w", padx=int(5 * self.dpi_scale * self.zoom_factor),
                        pady=(int(20 * self.dpi_scale * self.zoom_factor), int(10 * self.dpi_scale * self.zoom_factor)))

        table_container = ttk.Frame(self.stats_page, style='Card.TFrame')
        table_container.pack(fill="both", expand=True, pady=int(10 * self.dpi_scale * self.zoom_factor))

        columns = (
            "job", "filter_dist", "greeted", "feedback",
            "suitable_rate", "false_positive_rate",
            "replied", "interviewed", "avg_score"
        )
        self.stats_tree = ttk.Treeview(table_container, columns=columns, show="headings", height=8)

        self.stats_tree.heading("job", text="岗位名称")
        self.stats_tree.heading("filter_dist", text="筛选分布")
        self.stats_tree.heading("greeted", text="已打招呼")
        self.stats_tree.heading("feedback", text="已反馈")
        self.stats_tree.heading("suitable_rate", text="合适率")
        self.stats_tree.heading("false_positive_rate", text="误推率")
        self.stats_tree.heading("replied", text="已回复")
        self.stats_tree.heading("interviewed", text="已约面")
        self.stats_tree.heading("avg_score", text="平均分")

        self.stats_tree.column("job", width=200, minwidth=150, anchor='w')
        self.stats_tree.column("filter_dist", width=175, minwidth=140, anchor='center')
        self.stats_tree.column("greeted", width=100, minwidth=80, anchor='center')
        self.stats_tree.column("feedback", width=80, minwidth=65, anchor='center')
        self.stats_tree.column("suitable_rate", width=75, minwidth=60, anchor='center')
        self.stats_tree.column("false_positive_rate", width=75, minwidth=60, anchor='center')
        self.stats_tree.column("replied", width=100, minwidth=80, anchor='center')
        self.stats_tree.column("interviewed", width=100, minwidth=80, anchor='center')
        self.stats_tree.column("avg_score", width=65, minwidth=55, anchor='center')

        style = ttk.Style()
        style.configure("Stats.Treeview", font=self.font_table,
                       rowheight=int(UI_CONFIG['treeview_rowheight'] * self.dpi_scale * self.zoom_factor))
        style.configure("Stats.Treeview.Heading", font=(FONT_FAMILY, int(12 * self.font_scale), 'bold'))
        self.stats_tree.configure(style="Stats.Treeview")

        # 垂直和水平滚动条
        tree_scroll_y = ttk.Scrollbar(table_container, orient="vertical", command=self.stats_tree.yview)
        tree_scroll_x = ttk.Scrollbar(table_container, orient="horizontal", command=self.stats_tree.xview)
        self.stats_tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)

        self.stats_tree.grid(row=0, column=0, sticky="nsew",
                            padx=int(15 * self.dpi_scale * self.zoom_factor),
                            pady=int(15 * self.dpi_scale * self.zoom_factor))
        tree_scroll_y.grid(row=0, column=1, sticky="ns", pady=int(10 * self.dpi_scale * self.zoom_factor))
        tree_scroll_x.grid(row=1, column=0, sticky="ew", padx=int(15 * self.dpi_scale * self.zoom_factor))
        table_container.grid_rowconfigure(0, weight=1)
        table_container.grid_columnconfigure(0, weight=1)

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
            if not CANDIDATES_PATH.exists():
                return

            with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                candidates = json.load(f)
            candidates = [c for c in candidates if not c.get('blacklisted')]

            # 岗位过滤
            selected_job = self.stats_job_var.get()
            if selected_job != "全部岗位":
                candidates = [c for c in candidates if c.get('job_name', '') == selected_job.replace(" ", "")]

            # 时间范围过滤
            time_range = self.stats_time_var.get()
            if time_range != "全部":
                now = datetime.now()
                if time_range == "今天":
                    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
                elif time_range == "本周":
                    days_since_monday = now.weekday()
                    cutoff = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
                elif time_range == "本月":
                    cutoff = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                else:
                    cutoff = None

                if cutoff:
                    cutoff_str = cutoff.strftime("%Y%m%d_%H%M%S")
                    candidates = [c for c in candidates if c.get('batch_timestamp', '') >= cutoff_str]

            # 汇总统计（只计通过分的候选人）
            qualified = [c for c in candidates if c.get('match_score', 0) >= SCORE_THRESHOLD_PASS]
            total = len(qualified)
            strong = sum(1 for c in qualified if c.get('match_score', 0) >= SCORE_THRESHOLD_STRONG)
            recommended = sum(1 for c in qualified if SCORE_THRESHOLD_RECOMMEND <= c.get('match_score', 0) < SCORE_THRESHOLD_STRONG)
            greeted = sum(1 for c in qualified if c.get('greet_sent', False))

            self.stats_summary_vars['total'].set(str(total))
            self.stats_summary_vars['strong'].set(str(strong))
            self.stats_summary_vars['recommended'].set(str(recommended))
            self.stats_summary_vars['greeted'].set(str(greeted))

            # 清空表格
            for item in self.stats_tree.get_children():
                self.stats_tree.delete(item)

            # 按岗位聚合
            from collections import defaultdict
            job_stats = defaultdict(lambda: {
                'total': 0, 'strong': 0, 'recommended': 0, 'pending': 0,
                'greeted': 0, 'feedback_count': 0, 'suitable': 0,
                'false_positive': 0, 'contacted': 0, 'replied': 0,
                'interviewed': 0, 'scores': []
            })
            valid_feedback_statuses = {"合适", "误推", "误杀", "放弃"}
            contacted_statuses = {"已打招呼", "已回复", "待约面", "已约面", "不合适", "已归档"}
            replied_statuses = {"已回复", "待约面", "已约面"}

            for c in candidates:
                job = c.get('job_name', '未知')
                score = c.get('match_score', 0)
                if score < SCORE_THRESHOLD_PASS:
                    continue  # 低于通过分不计入统计

                job_stats[job]['total'] += 1
                if score >= SCORE_THRESHOLD_STRONG:
                    job_stats[job]['strong'] += 1
                elif score >= SCORE_THRESHOLD_RECOMMEND:
                    job_stats[job]['recommended'] += 1
                else:
                    job_stats[job]['pending'] += 1

                if c.get('greet_sent', False):
                    job_stats[job]['greeted'] += 1

                feedback_status = c.get('feedback_status')
                if feedback_status in valid_feedback_statuses:
                    job_stats[job]['feedback_count'] += 1
                    if feedback_status == "合适":
                        job_stats[job]['suitable'] += 1
                    elif feedback_status == "误推":
                        job_stats[job]['false_positive'] += 1

                followup_status = c.get('followup_status') or ("已打招呼" if c.get('greet_sent', False) else "未沟通")
                if c.get('greet_sent', False) or followup_status in contacted_statuses:
                    job_stats[job]['contacted'] += 1
                if followup_status in replied_statuses:
                    job_stats[job]['replied'] += 1
                if followup_status == "已约面":
                    job_stats[job]['interviewed'] += 1

                job_stats[job]['scores'].append(score)

            # 插入表格行
            for job, stats in sorted(job_stats.items(), key=lambda x: x[1]['total'], reverse=True):
                t = stats['total']
                s = stats['strong']
                r = stats['recommended']
                p = stats['pending']
                g = stats['greeted']
                fb = stats['feedback_count']
                suitable = stats['suitable']
                false_positive = stats['false_positive']
                contacted = stats['contacted']
                replied = stats['replied']
                interviewed = stats['interviewed']

                filter_dist = f"{t} (强{s}/推{r}/待{p})"
                greeted_str = f"{g} ({g*100//t}%)" if t > 0 else str(g)
                suitable_rate = f"{suitable*100//fb}%" if fb > 0 else "—"
                false_positive_rate = f"{false_positive*100//fb}%" if fb > 0 else "—"
                replied_str = f"{replied} ({replied*100//contacted}%)" if contacted > 0 else str(replied)
                interviewed_str = f"{interviewed} ({interviewed*100//replied}%)" if replied > 0 else str(interviewed)
                avg_score = f"{sum(stats['scores'])/len(stats['scores']):.1f}" if stats['scores'] else "—"

                self.stats_tree.insert("", "end", values=(
                    job, filter_dist, greeted_str, fb, suitable_rate,
                    false_positive_rate, replied_str, interviewed_str, avg_score
                ))

        except Exception as e:
            self.append_log(f"刷新统计失败：{e}")

    def show_page_home(self):
        """显示首页"""
        if self.home_page is None:
            self.create_home_page()
        self.hide_all_pages()
        self.home_page.pack(fill="both", expand=True)
        self.current_page_index = 0
        self._schedule_page_width_policy()
        self.update_nav_highlight()
        # 刷新岗位过滤列表
        try:
            job_rules = self._get_job_rules_cached()
            jobs = ["全部岗位"] + list(job_rules.keys())
            self.home_job_combo['values'] = jobs
        except Exception:
            pass
        self._defer_ui_work("home_stats", self.refresh_home_stats)

    def show_page_config(self):
        """显示配置页面"""
        if self.config_page is None:
            self.create_config_page()
        self.hide_all_pages()
        self.config_page.pack(fill="both", expand=True)
        self.current_page_index = 1
        self._schedule_page_width_policy()
        # 刷新技能树和必要条件列表
        if self.job_rules:
            self._defer_ui_work("config_lists", self._refresh_config_lists_if_needed)
        # 始终显示详细结果区域（基本信息、技能关键词、必要条件、话术模板）
        self.result_detail_frame.pack(fill="both", expand=True, padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))
        self.update_nav_highlight()
        # 重新绑定滚轮事件（覆盖动态创建的控件）
        self._bind_mousewheel(self.config_canvas, self.config_scrollable_frame)

    def show_page_run(self):
        """显示运行页面"""
        if self.run_page is None:
            self.create_run_page()
        self.hide_all_pages()
        self.run_page.pack(fill="both", expand=True)
        self.current_page_index = 2
        self._schedule_page_width_policy()
        self.update_nav_highlight()
        # 恢复浏览器自动检测（仅检测连接，不启动浏览器）
        self._start_browser_auto_check()
        # 刷新岗位选择列表
        try:
            job_rules = self._get_job_rules_cached()
            jobs = ["全部岗位"] + list(job_rules.keys())
            self.job_combo['values'] = jobs
        except Exception:
            pass
        # 重新绑定滚轮事件（覆盖动态创建的控件）
        self._bind_mousewheel(self.run_canvas, self.run_scrollable_frame)

    def show_page_result(self):
        """显示结果页面"""
        if self.result_page is None:
            self.create_result_page()
        self.hide_all_pages()
        self.result_page.pack(fill="both", expand=True)
        self.current_page_index = 3
        self._schedule_page_width_policy()
        self.update_nav_highlight()
        # 刷新岗位过滤列表
        try:
            job_rules = self._get_job_rules_cached()
            jobs = ["全部岗位"] + list(job_rules.keys())
            self.result_job_combo['values'] = jobs
        except Exception:
            pass
        self._defer_ui_work("results_refresh", self.refresh_results)

    def show_page_stats(self):
        """显示数据统计页面"""
        if self.stats_page is None:
            self.create_stats_page()
        self.hide_all_pages()
        self.stats_page.pack(fill="both", expand=True)
        self.current_page_index = 5
        self._schedule_page_width_policy()
        self.update_nav_highlight()
        # 刷新岗位过滤列表
        try:
            job_rules = self._get_job_rules_cached()
            jobs = ["全部岗位"] + list(job_rules.keys())
            self.stats_job_combo['values'] = jobs
        except Exception:
            pass
        self._defer_ui_work("stats_refresh", self.refresh_stats)

    def show_page_education(self):
        """显示学历核验页面。"""
        if self.education_page is None:
            self.create_education_page()
        self.hide_all_pages()
        self.education_page.pack(fill="both", expand=True)
        self.current_page_index = 4
        self._schedule_page_width_policy()
        self.update_nav_highlight()
        self._bind_mousewheel(self.education_canvas, self.education_scrollable_frame)

    def show_page_api(self):
        """显示 API 配置页面（系统设置）"""
        if self.api_config_page is None:
            self.create_api_config_page()
        self.hide_all_pages()
        self.api_config_page.pack(fill="both", expand=True)
        self.current_page_index = 6
        self._schedule_page_width_policy()
        self.update_nav_highlight()
        # 重置滚动条位置到顶部
        if hasattr(self, 'api_canvas'):
            self.api_canvas.yview_moveto(0.0)
        # 显示时按需加载配置到 UI，避免每次切页都同步查询 keyring。
        self._defer_ui_work("api_config_to_ui", self._load_api_config_to_ui_if_needed)
        # 重新绑定滚轮事件（覆盖动态创建的控件）
        self._bind_mousewheel(self.api_canvas, self.api_scrollable_frame)

    def hide_all_pages(self):
        """隐藏所有页面"""
        self._stop_browser_auto_check()
        for page in [
            self.home_page,
            self.config_page,
            self.api_config_page,
            self.run_page,
            self.result_page,
            self.stats_page,
            self.education_page,
        ]:
            if page is not None:
                page.pack_forget()

    def update_nav_highlight(self):
        """更新导航高亮 - 当前页面使用选中颜色，其他使用默认颜色"""
        for i, comp in enumerate(self.nav_components):
            if i == self.current_page_index:
                comp['icon'].configure(image=comp['icon_active'])
                comp['text'].configure(foreground=self.colors['text_sidebar_active'])
            else:
                comp['icon'].configure(image=comp['icon_default'])
                comp['text'].configure(foreground=self.colors['text_sidebar'])

    def on_nav_enter(self, index):
        """鼠标移入导航项时高亮（交换图标和前景色）"""
        comp = self.nav_components[index]
        comp['icon'].configure(image=comp['icon_active'])
        comp['text'].configure(foreground=self.colors['text_sidebar_active'])

    def on_nav_leave(self, index):
        """鼠标移出导航项时恢复样式（当前页面除外）"""
        if index != self.current_page_index:
            comp = self.nav_components[index]
            comp['icon'].configure(image=comp['icon_default'])
            comp['text'].configure(foreground=self.colors['text_sidebar'])

    # ===== 右键菜单功能 =====
    def bind_entry_context_menu(self, entry_widget):
        """为 Entry/Combobox 控件绑定右键复制/粘贴/全选菜单"""
        menu_font = (FONT_FAMILY, int(12 * self.font_scale))
        menu = tk.Menu(entry_widget, tearoff=0, font=menu_font)
        self._context_menus.append(menu)

        def do_cut():
            try:
                entry_widget.event_generate('<<Cut>>')
            except tk.TclError:
                pass

        def do_copy():
            try:
                entry_widget.event_generate('<<Copy>>')
            except tk.TclError:
                pass

        def do_paste():
            try:
                entry_widget.event_generate('<<Paste>>')
            except tk.TclError:
                pass

        def do_select_all():
            try:
                entry_widget.select_range(0, 'end')
                entry_widget.icursor('end')
            except tk.TclError:
                pass

        menu.add_command(label="剪切(T)", command=do_cut)
        menu.add_command(label="复制(C)", command=do_copy)
        menu.add_command(label="粘贴(P)", command=do_paste)
        menu.add_separator()
        menu.add_command(label="全选(A)", command=do_select_all)

        def show_menu(event):
            menu.tk_popup(event.x_root, event.y_root)

        entry_widget.bind("<Button-3>", show_menu)

    def bind_text_context_menu(self, text_widget, editable=True):
        """为 Text 控件绑定右键复制/粘贴/全选菜单"""
        menu_font = (FONT_FAMILY, int(12 * self.font_scale))
        menu = tk.Menu(text_widget, tearoff=0, font=menu_font)
        self._context_menus.append(menu)

        def do_cut():
            try:
                text_widget.event_generate('<<Cut>>')
            except tk.TclError:
                pass

        def do_copy():
            try:
                text_widget.event_generate('<<Copy>>')
            except tk.TclError:
                pass

        def do_paste():
            try:
                text_widget.event_generate('<<Paste>>')
            except tk.TclError:
                pass

        def do_select_all():
            try:
                text_widget.tag_add('sel', '1.0', 'end')
            except tk.TclError:
                pass

        if editable:
            menu.add_command(label="剪切(T)", command=do_cut)
        menu.add_command(label="复制(C)", command=do_copy)
        if editable:
            menu.add_command(label="粘贴(P)", command=do_paste)
        menu.add_separator()
        menu.add_command(label="全选(A)", command=do_select_all)

        def show_menu(event):
            menu.tk_popup(event.x_root, event.y_root)

        text_widget.bind("<Button-3>", show_menu)

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
                with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                    candidates = json.load(f)
                candidates = [c for c in candidates if not c.get('blacklisted')]

                # 岗位过滤
                if selected_job != "全部岗位":
                    candidates = [c for c in candidates if c.get('job_name', '') == selected_job.replace(" ", "")]

                # 只统计通过分的候选人
                candidates = [c for c in candidates if c.get('match_score', 0) >= SCORE_THRESHOLD_PASS]

                total = len(candidates)
                greeted = sum(1 for c in candidates if c.get('greet_sent', False))
                # 强烈推荐：匹配分>=SCORE_THRESHOLD_STRONG
                strong = sum(1 for c in candidates if c.get('match_score', 0) >= SCORE_THRESHOLD_STRONG)
                # 推荐：匹配分>=SCORE_THRESHOLD_RECOMMEND 且<SCORE_THRESHOLD_STRONG
                recommended = sum(1 for c in candidates if SCORE_THRESHOLD_RECOMMEND <= c.get('match_score', 0) < SCORE_THRESHOLD_STRONG)

                self.home_stats_vars['total_home'].set(str(total))
                self.home_stats_vars['recommended_home'].set(str(recommended))
                self.home_stats_vars['greeted_home'].set(str(greeted))
                self.home_stats_vars['strong_home'].set(str(strong))
        except Exception as e:
            print(f"刷新首页统计失败：{e}")

        # 如果当前在数据统计页，同步刷新统计
        if self.current_page_index == 5:
            self.refresh_stats()

    def _center_window(self, window, width, height):
        """将子窗口相对于主窗口居中"""
        _place_window_centered(window, width, height, parent=self.root)

    def _create_status_icons(self):
        """创建进度状态图标（Canvas 自绘彩色圆形+符号）"""
        from PIL import Image, ImageDraw, ImageTk

        size = int(18 * self.dpi_scale * self.zoom_factor)
        pad = max(1, size // 12)

        def make_icon(bg_color, symbol_type):
            img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([0, 0, size - 1, size - 1], fill=bg_color)
            # 白色符号线条宽度
            lw = max(2, size // 8)
            if symbol_type == 'check':
                # 勾号：三个点构成折线
                pts = [
                    (size * 0.25, size * 0.50),
                    (size * 0.42, size * 0.68),
                    (size * 0.75, size * 0.32),
                ]
                draw.line([pts[0], pts[1]], fill='white', width=lw)
                draw.line([pts[1], pts[2]], fill='white', width=lw)
            else:
                # 叉号：两条对角线
                p = size * 0.3
                draw.line([(p, p), (size - p, size - p)], fill='white', width=lw)
                draw.line([(size - p, p), (p, size - p)], fill='white', width=lw)
            return ImageTk.PhotoImage(img)

        self._icon_status_ok = make_icon(self.colors['success'], 'check')
        self._icon_status_fail = make_icon(self.colors['danger'], 'cross')

    def _set_window_icon(self):
        """设置窗口图标，替换 tkinter 默认羽毛图标"""
        try:
            from PIL import Image, ImageTk
            icon_img = _draw_search_icon(256, '#2563EB', sw_ratio=0.10)
            # 用 iconphoto 设置高分图标，Windows 10/11 原生缩放比 ICO 清晰
            self._icon_photo = ImageTk.PhotoImage(icon_img)
            self.root.iconphoto(True, self._icon_photo)
        except Exception:
            pass  # 图标设置失败不影响程序运行

    def show_stat_detail(self, stat_type):
        """显示统计详情"""
        try:
            if not CANDIDATES_PATH.exists():
                messagebox.showinfo("提示", "暂无候选人数据")
                return

            with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                candidates = json.load(f)
            candidates = [c for c in candidates if not c.get('blacklisted')]

            # 岗位过滤
            if hasattr(self, 'home_job_var'):
                selected_job = self.home_job_var.get()
                if selected_job != "全部岗位":
                    candidates = [c for c in candidates if c.get('job_name', '') == selected_job.replace(" ", "")]

            # 根据类型筛选候选人（只统计通过分）
            if stat_type == 'total_home':
                title = "通过筛选"
                filtered = [c for c in candidates if c.get('match_score', 0) >= SCORE_THRESHOLD_PASS]
            elif stat_type == 'strong_home':
                title = "强烈推荐"
                filtered = [c for c in candidates if c.get('match_score', 0) >= SCORE_THRESHOLD_STRONG]
            elif stat_type == 'recommended_home':
                title = "推荐"
                filtered = [c for c in candidates if SCORE_THRESHOLD_RECOMMEND <= c.get('match_score', 0) < SCORE_THRESHOLD_STRONG]
            elif stat_type == 'greeted_home':
                title = "已打招呼"
                filtered = [c for c in candidates if c.get('match_score', 0) >= SCORE_THRESHOLD_PASS and c.get('greet_sent', False)]
            else:
                return

            if not filtered:
                messagebox.showinfo("提示", f"{title}：暂无数据")
                return

            # 创建详情窗口
            detail_window = tk.Toplevel(self.root)
            detail_window.transient(self.root)
            detail_window.grab_set()
            detail_window.title(title)
            detail_window.configure(bg=self.colors['bg_main'])

            # 设置固定大小并相对主窗口居中
            window_width = min(1280, self.root.winfo_width() - 100)
            window_height = min(900, self.root.winfo_height() - 80)
            self._center_window(detail_window, window_width, window_height)

            # 标题
            title_label = ttk.Label(detail_window, text=title,
                                   font=(FONT_FAMILY, int(13 * self.font_scale)),
                                   foreground=self.colors['primary'],
                                   background=self.colors['bg_main'])
            title_label.pack(fill="x", padx=int(20 * self.dpi_scale * self.zoom_factor), pady=(int(15 * self.dpi_scale * self.zoom_factor), 0))

            # 统计信息
            greeted_count = len([c for c in filtered if c.get('greet_sent', False)])
            count_frame = ttk.Frame(detail_window, style='Page.TFrame')
            count_frame.pack(anchor="w", padx=int(20 * self.dpi_scale * self.zoom_factor), pady=(int(5 * self.dpi_scale * self.zoom_factor), 0))
            count_font = (FONT_FAMILY, int(11 * self.font_scale))
            ttk.Label(count_frame, text=f"共 {len(filtered)} 人", font=count_font,
                      foreground=self.colors['text_secondary'],
                      background=self.colors['bg_main']).pack(side="left")
            greeted_label = ttk.Label(count_frame, text=f"，已打招呼 {greeted_count} 人",
                                      font=count_font, foreground=self.colors['success'],
                                      background=self.colors['bg_main'])
            greeted_label.pack(side="left")
            count_label_ref = [greeted_label]

            # 表格容器
            table_frame = ttk.Frame(detail_window, style='Card.TFrame')
            table_frame.pack(fill="both", expand=True, padx=int(20 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

            # 创建表格 - 与筛选结果页主Treeview列完全一致（含推荐指数）
            columns = ("name", "exp", "salary", "skills", "score", "ai_eval", "level", "status")
            tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=18)

            tree.heading("name", text="姓名")
            tree.heading("exp", text="工作年限")
            tree.heading("salary", text="薪资")
            tree.heading("skills", text="技能匹配")
            tree.heading("score", text="匹配分")
            tree.heading("ai_eval", text="AI评估")
            tree.heading("level", text="推荐指数")
            tree.heading("status", text="状态")

            # 设置列宽 - 与筛选结果页Treeview一致
            tree.column("name", width=80, minwidth=60, anchor='center')
            tree.column("exp", width=110, minwidth=100, anchor='center')
            tree.column("salary", width=100, minwidth=80, anchor='center')
            tree.column("skills", width=140, minwidth=100, anchor='center')
            tree.column("score", width=90, minwidth=80, anchor='center')
            tree.column("ai_eval", width=90, minwidth=80, anchor='center')
            tree.column("level", width=120, minwidth=100, anchor='center')
            tree.column("status", width=220, minwidth=180, anchor='center')

            # 设置表格字体和样式 - 明细窗口使用较小字体
            detail_font = (FONT_FAMILY, int(11 * self.font_scale))
            tree_style = ttk.Style()
            tree_style.configure("Detail.Treeview",
                                font=detail_font,
                                rowheight=int(UI_CONFIG['treeview_rowheight'] * self.dpi_scale * self.zoom_factor))
            tree_style.configure("Detail.Treeview.Heading",
                                font=(FONT_FAMILY, int(11 * self.font_scale), 'bold'))
            tree.configure(style="Detail.Treeview")

            # 添加滚动条
            scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scrollbar.set)

            tree.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            # 绑定右键菜单 - 与筛选结果页一致
            filtered_ref = [filtered]  # 用列表包装以支持闭包内修改

            def on_detail_right_click(event):
                clicked_item = tree.identify_row(event.y)
                if not clicked_item:
                    return
                # 右键点击的行已在多选集合内时，保持现有选区
                if clicked_item not in tree.selection():
                    tree.selection_set(clicked_item)

                selection = tree.selection()
                # 多选时显示批量操作功能
                if len(selection) > 1:
                    def export_selected():
                        if not selection:
                            messagebox.showwarning("警告", "请先选择要导出的候选人")
                            return
                        selected_data = []
                        for sel_item in selection:
                            sv = tree.item(sel_item, 'values')
                            for c in filtered_ref[0]:
                                if c.get('name') == sv[0]:
                                    selected_data.append(c)
                                    break
                        if not selected_data:
                            return
                        from bossmaster import export_to_excel
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
                            export_to_excel(selected_data, file_path)
                            messagebox.showinfo("成功", f"已导出 {len(selected_data)} 名候选人到：\n{file_path}")

                    def remove_selected():
                        if not messagebox.askyesno("确认删除", f"确定要移除选中的 {len(selection)} 名候选人吗？"):
                            return
                        for sel_item in selection:
                            sv = tree.item(sel_item, 'values')
                            for c in filtered_ref[0]:
                                if c.get('name') == sv[0]:
                                    geek_id = c.get('geek_id')
                                    if geek_id:
                                        # 从内存数据中移除
                                        filtered_ref[0] = [x for x in filtered_ref[0] if x.get('geek_id') != geek_id]
                                        # 从文件中移除
                                        if CANDIDATES_PATH.exists():
                                            with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                                                candidates = json.load(f)
                                            candidates = [x for x in candidates if x.get('geek_id') != geek_id]
                                            save_candidates_all(candidates, CANDIDATES_PATH)
                                    break
                        # 删除 Treeview 中的项
                        for sel_item in selection:
                            tree.delete(sel_item)
                        new_greeted = len([c for c in filtered_ref[0] if c.get('greet_sent', False)])
                        count_label_ref[0].config(text=f"，已打招呼 {new_greeted} 人")
                        self.refresh_home_stats()
                        self.refresh_results()

                    context_menu_font = (FONT_FAMILY, int(11 * self.font_scale))
                    menu = tk.Menu(detail_window, tearoff=0, font=context_menu_font)
                    icon_export_menu = self.icons.button('export', self.colors['text_primary'])
                    icon_trash_menu = self.icons.button('trash', self.colors['text_primary'])
                    icon_greet = self.icons.button('chat', self.colors['success'])
                    menu._icon_refs = [icon_export_menu, icon_trash_menu, icon_greet]
                    menu.add_command(label=" 批量打招呼", image=icon_greet, compound=tk.LEFT,
                                     command=lambda: self._greet_selected_candidates(selection, filtered_ref, tree, parent=detail_window))
                    # 批量AI评估选项
                    selected_candidates = []
                    for sel_item in selection:
                        sv = tree.item(sel_item, 'values')
                        for c in filtered_ref[0]:
                            if c.get('name') == sv[0]:
                                selected_candidates.append(c)
                                break
                    if selected_candidates:
                        icon_ai_eval = self.icons.button('refresh', self.colors['primary'])
                        menu._icon_refs.append(icon_ai_eval)
                        menu.add_command(label=" 批量AI评估", image=icon_ai_eval, compound=tk.LEFT,
                                         command=lambda: self._ai_eval_selected_candidates(selected_candidates))
                    if any(c.get('manual_review_required') for c in selected_candidates):
                        icon_confirm = self.icons.button('stamp_check', self.colors['success'])
                        menu._icon_refs.append(icon_confirm)
                        menu.add_command(label=" 批量确认通过", image=icon_confirm, compound=tk.LEFT,
                                         command=lambda: self._batch_confirm_manual_review(selected_candidates, parent=detail_window))
                    menu.add_command(label=" 移除选中", image=icon_trash_menu, compound=tk.LEFT,
                                     command=remove_selected)
                    menu.add_separator()
                    menu.add_command(label=" 导出选中", image=icon_export_menu, compound=tk.LEFT,
                                     command=export_selected)
                    menu.tk_popup(event.x_root, event.y_root)
                    return

                # 从 filtered_ref 中定位候选人
                vals = tree.item(clicked_item, 'values')
                candidate = None
                for c in filtered_ref[0]:
                    if c.get('name') == vals[0] and str(c.get('match_score', '')) == str(vals[4]):
                        candidate = c
                        break
                if not candidate:
                    return

                def show_detail():
                    d_win = tk.Toplevel(detail_window)
                    d_win.title("候选人详情")
                    d_win.transient(detail_window)
                    d_win.withdraw()
                    d_title = f"姓名：{vals[0]} | 匹配分：{vals[4]} | {vals[6]}"
                    ttk.Label(d_win, text=d_title, font=(FONT_FAMILY, 16),
                             foreground=self.colors['primary']).pack(pady=15)
                    tw = tk.Text(d_win, wrap='word', font=(FONT_FAMILY, 14))
                    tw.pack(fill='both', expand=True, padx=20, pady=10)
                    tw.insert('1.0', self._format_candidate_detail(candidate))
                    self.bind_text_context_menu(tw, editable=False)
                    _place_window_centered(d_win, 1000, 880, parent=self.root)
                    d_win.deiconify()

                def remove_candidate():
                    if not messagebox.askyesno("确认删除", "确定要移除该候选人吗？"):
                        return
                    geek_id = candidate.get('geek_id')
                    if not geek_id:
                        return
                    filtered_ref[0] = [c for c in filtered_ref[0] if c.get('geek_id') != geek_id]
                    if CANDIDATES_PATH.exists():
                        with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                            candidates = json.load(f)
                        candidates = [c for c in candidates if c.get('geek_id') != geek_id]
                        save_candidates_all(candidates, CANDIDATES_PATH)
                    tree.delete(clicked_item)
                    new_greeted = len([c for c in filtered_ref[0] if c.get('greet_sent', False)])
                    count_label_ref[0].config(text=f"，已打招呼 {new_greeted} 人")
                    self.refresh_home_stats()
                    self.refresh_results()
                    detail_window.lift()

                def export_selected():
                    selection = tree.selection()
                    if not selection:
                        messagebox.showwarning("警告", "请先选择要导出的候选人")
                        return
                    selected_data = []
                    for sel_item in selection:
                        sv = tree.item(sel_item, 'values')
                        for c in filtered_ref[0]:
                            if c.get('name') == sv[0]:
                                selected_data.append(c)
                                break
                    if not selected_data:
                        return
                    from bossmaster import export_to_excel
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
                        export_to_excel(selected_data, file_path)
                        messagebox.showinfo("成功", f"已导出 {len(selected_data)} 名候选人到：\n{file_path}")

                def detail_refresh():
                    self.refresh_home_stats()
                    self.refresh_results()
                    detail_window.lift()

                self._build_candidate_context_menu(
                    parent=detail_window,
                    tree=tree,
                    tree_item=clicked_item,
                    candidate=candidate,
                    show_detail_fn=show_detail,
                    remove_fn=remove_candidate,
                    export_fn=export_selected,
                    refresh_fn=detail_refresh,
                    x_root=event.x_root,
                    y_root=event.y_root,
                )

            tree.bind('<Button-3>', on_detail_right_click)
            self._bind_detail_tree_tooltip(tree, filtered_ref)

            def on_detail_double_click(event):
                clicked_item = tree.identify_row(event.y)
                if not clicked_item:
                    return
                vals = tree.item(clicked_item, 'values')
                if vals:
                    for c in filtered_ref[0]:
                        if c.get('name') == vals[0]:
                            d_win = tk.Toplevel(detail_window)
                            d_win.title("候选人详情")
                            d_win.transient(detail_window)
                            d_win.withdraw()
                            d_title = f"姓名：{vals[0]} | 匹配分：{vals[4]} | {vals[6]}"
                            ttk.Label(d_win, text=d_title, font=(FONT_FAMILY, 16),
                                     foreground=self.colors['primary']).pack(pady=15)
                            tw = tk.Text(d_win, wrap='word', font=(FONT_FAMILY, 14))
                            tw.pack(fill='both', expand=True, padx=20, pady=10)
                            tw.insert('1.0', self._format_candidate_detail(c))
                            self.bind_text_context_menu(tw, editable=False)
                            _place_window_centered(d_win, 1000, 880, parent=self.root)
                            d_win.deiconify()
                            break

            tree.bind('<Double-Button-1>', on_detail_double_click)

            # 填充数据
            for c in sorted(filtered, key=lambda x: x.get('match_score', 0), reverse=True):
                score = c.get('match_score', 0)
                level = "强烈推荐" if score >= SCORE_THRESHOLD_STRONG else ("推荐" if score >= SCORE_THRESHOLD_RECOMMEND else "待定")
                status = self._format_candidate_status(c)
                salary, exp = self._parse_salary_exp(c.get('summary', ''), c.get('structured'))
                # AI 评估调整值：有简历时显示简历评估（替代一次评估），否则显示一次评估
                ai_adj = c.get('llm_adjustment')
                resume_adj = c.get('resume_eval_adjustment')

                if resume_adj is not None:
                    ai_text = f"+{resume_adj}" if resume_adj > 0 else str(resume_adj)
                elif ai_adj is not None and c.get('llm_evaluated'):
                    ai_text = f"+{ai_adj}" if ai_adj > 0 else str(ai_adj)
                else:
                    ai_text = "—"

                tree.insert("", "end", values=(
                    c.get('name', ''),
                    exp,
                    salary,
                    c.get('skill_match_ratio', ''),
                    score,
                    ai_text,
                    level,
                    status
                ))

            # 窗口居中
            self._center_window(detail_window, window_width, window_height)

        except Exception as e:
            messagebox.showerror("错误", f"显示详情失败：{e}")

    def show_result_stat_detail(self, stat_type):
        """显示筛选结果统计详情（新指标）"""
        try:
            if not CANDIDATES_PATH.exists():
                messagebox.showinfo("提示", "暂无候选人数据")
                return

            with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                candidates = json.load(f)
            candidates = [c for c in candidates if not c.get('blacklisted')]

            # 岗位过滤
            if hasattr(self, 'result_job_var'):
                selected_job = self.result_job_var.get()
                if selected_job != "全部岗位":
                    candidates = [c for c in candidates if c.get('job_name', '') == selected_job.replace(" ", "")]

            # 日期过滤（与 refresh_results 保持一致）
            date_start, date_end = self._get_result_date_filter() if hasattr(self, 'result_date_start_entry') else (None, None)
            if date_start or date_end:
                def _in_date_range(c):
                    ts = c.get('batch_timestamp', '')
                    if not ts or len(ts) < 8:
                        return False
                    d = ts[:8]
                    if date_start and d < date_start:
                        return False
                    if date_end and d > date_end:
                        return False
                    return True
                candidates = [c for c in candidates if _in_date_range(c)]

            # 根据类型筛选候选人
            if stat_type == 'strong':
                # 强烈推荐
                title = "强烈推荐"
                filtered = [c for c in candidates if c.get('match_score', 0) >= SCORE_THRESHOLD_STRONG]
                detail_type = 'all'
            elif stat_type == 'recommended':
                # 推荐
                title = "推荐"
                filtered = [c for c in candidates if SCORE_THRESHOLD_RECOMMEND <= c.get('match_score', 0) < SCORE_THRESHOLD_STRONG]
                detail_type = 'all'
            elif stat_type == 'pending':
                # 待定
                title = "待定"
                filtered = [c for c in candidates if SCORE_THRESHOLD_PASS <= c.get('match_score', 0) < SCORE_THRESHOLD_RECOMMEND]
                detail_type = 'all'
            elif stat_type == 'greeted':
                title = "已打招呼"
                filtered = [
                    c for c in candidates
                    if c.get('match_score', 0) >= SCORE_THRESHOLD_PASS
                    and c.get('greet_sent', False)
                ]
                detail_type = 'all'
            else:
                return

            # 计算总数和已打招呼数
            total = len(filtered)
            greeted = [c for c in filtered if c.get('greet_sent', False)]
            greeted_count = len(greeted)

            if total == 0:
                messagebox.showinfo("提示", f"{title}：暂无数据")
                return

            # 创建详情窗口
            detail_window = tk.Toplevel(self.root)
            detail_window.transient(self.root)
            detail_window.grab_set()
            detail_window.title(title)
            detail_window.configure(bg=self.colors['bg_main'])

            # 设置固定大小并相对主窗口居中
            window_width = min(1280, self.root.winfo_width() - 100)
            window_height = min(900, self.root.winfo_height() - 80)
            self._center_window(detail_window, window_width, window_height)

            # 标题
            title_label = ttk.Label(detail_window, text=title,
                                   font=(FONT_FAMILY, int(13 * self.font_scale)),
                                   foreground=self.colors['primary'],
                                   background=self.colors['bg_main'])
            title_label.pack(fill="x", padx=int(20 * self.dpi_scale * self.zoom_factor), pady=(int(15 * self.dpi_scale * self.zoom_factor), 0))

            # 统计信息
            count_frame = ttk.Frame(detail_window, style='Page.TFrame')
            count_frame.pack(anchor="w", padx=int(20 * self.dpi_scale * self.zoom_factor), pady=(int(5 * self.dpi_scale * self.zoom_factor), 0))
            count_font = (FONT_FAMILY, int(11 * self.font_scale))
            ttk.Label(count_frame, text=f"共 {total} 人", font=count_font,
                      foreground=self.colors['text_secondary'],
                      background=self.colors['bg_main']).pack(side="left")
            greeted_label = ttk.Label(count_frame, text=f"，已打招呼 {greeted_count} 人",
                                      font=count_font, foreground=self.colors['success'],
                                      background=self.colors['bg_main'])
            greeted_label.pack(side="left")
            count_label_ref = [greeted_label]

            # 表格容器
            table_frame = ttk.Frame(detail_window, style='Card.TFrame')
            table_frame.pack(fill="both", expand=True, padx=int(20 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))

            # 创建表格
            columns = ("name", "exp", "salary", "skills", "score", "ai_eval", "level", "status")
            tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=18)

            tree.heading("name", text="姓名")
            tree.heading("exp", text="工作年限")
            tree.heading("salary", text="薪资")
            tree.heading("skills", text="技能匹配")
            tree.heading("score", text="匹配分")
            tree.heading("ai_eval", text="AI评估")
            tree.heading("level", text="推荐指数")
            tree.heading("status", text="状态")

            # 设置列宽
            tree.column("name", width=80, anchor='center')
            tree.column("exp", width=110, minwidth=100, anchor='center')
            tree.column("salary", width=100, anchor='center')
            tree.column("skills", width=140, anchor='center')
            tree.column("score", width=90, minwidth=80, anchor='center')
            tree.column("ai_eval", width=90, minwidth=80, anchor='center')
            tree.column("level", width=120, anchor='center')
            tree.column("status", width=220, minwidth=180, anchor='center')

            # 设置表格字体和样式 - 明细窗口使用较小字体
            detail_font = (FONT_FAMILY, int(11 * self.font_scale))
            tree_style = ttk.Style()
            tree_style.configure("Detail.Treeview",
                                font=detail_font,
                                rowheight=int(UI_CONFIG['treeview_rowheight'] * self.dpi_scale * self.zoom_factor))
            tree_style.configure("Detail.Treeview.Heading",
                                font=(FONT_FAMILY, int(11 * self.font_scale), 'bold'))
            tree.configure(style="Detail.Treeview")

            # 添加滚动条
            scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scrollbar.set)

            tree.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            # 填充数据
            for c in sorted(filtered, key=lambda x: x.get('match_score', 0), reverse=True):
                score = c.get('match_score', 0)
                level = "强烈推荐" if score >= SCORE_THRESHOLD_STRONG else ("推荐" if score >= SCORE_THRESHOLD_RECOMMEND else "待定")
                status = self._format_candidate_status(c)
                salary, exp = self._parse_salary_exp(c.get('summary', ''), c.get('structured'))
                # AI 评估调整值：有简历时显示简历评估（替代一次评估），否则显示一次评估
                ai_adj = c.get('llm_adjustment')
                resume_adj = c.get('resume_eval_adjustment')

                if resume_adj is not None:
                    ai_text = f"+{resume_adj}" if resume_adj > 0 else str(resume_adj)
                elif ai_adj is not None and c.get('llm_evaluated'):
                    ai_text = f"+{ai_adj}" if ai_adj > 0 else str(ai_adj)
                else:
                    ai_text = "—"

                tree.insert("", "end", values=(
                    c.get('name', ''),
                    exp,
                    salary,
                    c.get('skill_match_ratio', ''),
                    score,
                    ai_text,
                    level,
                    status
                ))

            # 绑定右键菜单 - 与筛选结果页一致
            filtered_ref = [filtered]

            def on_result_detail_right_click(event):
                clicked_item = tree.identify_row(event.y)
                if not clicked_item:
                    return
                # 右键点击的行已在多选集合内时，保持现有选区
                if clicked_item not in tree.selection():
                    tree.selection_set(clicked_item)

                selection = tree.selection()
                # 多选时显示批量操作功能
                if len(selection) > 1:
                    def export_selected():
                        if not selection:
                            messagebox.showwarning("警告", "请先选择要导出的候选人")
                            return
                        selected_data = []
                        for sel_item in selection:
                            sv = tree.item(sel_item, 'values')
                            for c in filtered_ref[0]:
                                if c.get('name') == sv[0]:
                                    selected_data.append(c)
                                    break
                        if not selected_data:
                            return
                        from bossmaster import export_to_excel
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
                            export_to_excel(selected_data, file_path)
                            messagebox.showinfo("成功", f"已导出 {len(selected_data)} 名候选人到：\n{file_path}")

                    def remove_selected():
                        if not messagebox.askyesno("确认删除", f"确定要移除选中的 {len(selection)} 名候选人吗？"):
                            return
                        for sel_item in selection:
                            sv = tree.item(sel_item, 'values')
                            for c in filtered_ref[0]:
                                if c.get('name') == sv[0]:
                                    geek_id = c.get('geek_id')
                                    if geek_id:
                                        # 从内存数据中移除
                                        filtered_ref[0] = [x for x in filtered_ref[0] if x.get('geek_id') != geek_id]
                                        # 从文件中移除
                                        if CANDIDATES_PATH.exists():
                                            with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                                                candidates = json.load(f)
                                            candidates = [x for x in candidates if x.get('geek_id') != geek_id]
                                            save_candidates_all(candidates, CANDIDATES_PATH)
                                    break
                        # 删除 Treeview 中的项
                        for sel_item in selection:
                            tree.delete(sel_item)
                        new_greeted = len([c for c in filtered_ref[0] if c.get('greet_sent', False)])
                        count_label_ref[0].config(text=f"，已打招呼 {new_greeted} 人")
                        self.refresh_results()
                        detail_window.lift()

                    context_menu_font = (FONT_FAMILY, int(11 * self.font_scale))
                    menu = tk.Menu(detail_window, tearoff=0, font=context_menu_font)
                    icon_export_menu = self.icons.button('export', self.colors['text_primary'])
                    icon_trash_menu = self.icons.button('trash', self.colors['text_primary'])
                    icon_greet = self.icons.button('chat', self.colors['success'])
                    menu._icon_refs = [icon_export_menu, icon_trash_menu, icon_greet]
                    menu.add_command(label=" 批量打招呼", image=icon_greet, compound=tk.LEFT,
                                     command=lambda: self._greet_selected_candidates(selection, filtered_ref, tree, parent=detail_window))
                    # 批量AI评估选项
                    selected_candidates = []
                    for sel_item in selection:
                        sv = tree.item(sel_item, 'values')
                        for c in filtered_ref[0]:
                            if c.get('name') == sv[0]:
                                selected_candidates.append(c)
                                break
                    if selected_candidates:
                        icon_ai_eval = self.icons.button('refresh', self.colors['primary'])
                        menu._icon_refs.append(icon_ai_eval)
                        menu.add_command(label=" 批量AI评估", image=icon_ai_eval, compound=tk.LEFT,
                                         command=lambda: self._ai_eval_selected_candidates(selected_candidates))
                    if any(c.get('manual_review_required') for c in selected_candidates):
                        icon_confirm = self.icons.button('stamp_check', self.colors['success'])
                        menu._icon_refs.append(icon_confirm)
                        menu.add_command(label=" 批量确认通过", image=icon_confirm, compound=tk.LEFT,
                                         command=lambda: self._batch_confirm_manual_review(selected_candidates, parent=detail_window))
                    menu.add_command(label=" 移除选中", image=icon_trash_menu, compound=tk.LEFT,
                                     command=remove_selected)
                    menu.add_separator()
                    menu.add_command(label=" 导出选中", image=icon_export_menu, compound=tk.LEFT,
                                     command=export_selected)
                    menu.tk_popup(event.x_root, event.y_root)
                    return

                # 从 filtered_ref 中定位候选人
                vals = tree.item(clicked_item, 'values')
                candidate = None
                for c in filtered_ref[0]:
                    if c.get('name') == vals[0] and str(c.get('match_score', '')) == str(vals[4]):
                        candidate = c
                        break
                if not candidate:
                    return

                def show_detail():
                    d_win = tk.Toplevel(detail_window)
                    d_win.title("候选人详情")
                    d_win.transient(detail_window)
                    d_win.withdraw()
                    d_title = f"姓名：{vals[0]} | 匹配分：{vals[4]} | {vals[6]}"
                    ttk.Label(d_win, text=d_title, font=(FONT_FAMILY, 16),
                             foreground=self.colors['primary']).pack(pady=15)
                    tw = tk.Text(d_win, wrap='word', font=(FONT_FAMILY, 14))
                    tw.pack(fill='both', expand=True, padx=20, pady=10)
                    tw.insert('1.0', self._format_candidate_detail(candidate))
                    self.bind_text_context_menu(tw, editable=False)
                    _place_window_centered(d_win, 1000, 880, parent=self.root)
                    d_win.deiconify()

                def remove_candidate():
                    if not messagebox.askyesno("确认删除", "确定要移除该候选人吗？"):
                        return
                    geek_id = candidate.get('geek_id')
                    if not geek_id:
                        return
                    filtered_ref[0] = [c for c in filtered_ref[0] if c.get('geek_id') != geek_id]
                    if CANDIDATES_PATH.exists():
                        with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                            candidates_all = json.load(f)
                        candidates_all = [c for c in candidates_all if c.get('geek_id') != geek_id]
                        save_candidates_all(candidates_all, CANDIDATES_PATH)
                    tree.delete(clicked_item)
                    new_greeted = len([c for c in filtered_ref[0] if c.get('greet_sent', False)])
                    count_label_ref[0].config(text=f"，已打招呼 {new_greeted} 人")
                    self.refresh_results()
                    detail_window.lift()

                def export_selected():
                    selection = tree.selection()
                    if not selection:
                        messagebox.showwarning("警告", "请先选择要导出的候选人")
                        return
                    selected_data = []
                    for sel_item in selection:
                        sv = tree.item(sel_item, 'values')
                        for c in filtered_ref[0]:
                            if c.get('name') == sv[0]:
                                selected_data.append(c)
                                break
                    if not selected_data:
                        return
                    from bossmaster import export_to_excel
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
                        export_to_excel(selected_data, file_path)
                        messagebox.showinfo("成功", f"已导出 {len(selected_data)} 名候选人到：\n{file_path}")

                def detail_refresh():
                    self.refresh_results()
                    detail_window.lift()

                self._build_candidate_context_menu(
                    parent=detail_window,
                    tree=tree,
                    tree_item=clicked_item,
                    candidate=candidate,
                    show_detail_fn=show_detail,
                    remove_fn=remove_candidate,
                    export_fn=export_selected,
                    refresh_fn=detail_refresh,
                    x_root=event.x_root,
                    y_root=event.y_root,
                )

            tree.bind('<Button-3>', on_result_detail_right_click)
            self._bind_detail_tree_tooltip(tree, filtered_ref)

            def on_result_detail_double_click(event):
                clicked_item = tree.identify_row(event.y)
                if not clicked_item:
                    return
                vals = tree.item(clicked_item, 'values')
                if vals:
                    for c in filtered_ref[0]:
                        if c.get('name') == vals[0]:
                            d_win = tk.Toplevel(detail_window)
                            d_win.title("候选人详情")
                            d_win.transient(detail_window)
                            d_win.withdraw()
                            d_title = f"姓名：{vals[0]} | 匹配分：{vals[4]} | {vals[6]}"
                            ttk.Label(d_win, text=d_title, font=(FONT_FAMILY, 16),
                                     foreground=self.colors['primary']).pack(pady=15)
                            tw = tk.Text(d_win, wrap='word', font=(FONT_FAMILY, 14))
                            tw.pack(fill='both', expand=True, padx=20, pady=10)
                            tw.insert('1.0', self._format_candidate_detail(c))
                            self.bind_text_context_menu(tw, editable=False)
                            _place_window_centered(d_win, 1000, 880, parent=self.root)
                            d_win.deiconify()
                            break

            tree.bind('<Double-Button-1>', on_result_detail_double_click)

        except Exception as e:
            messagebox.showerror("错误", f"显示详情失败：{e}")

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
        if not CONFIG_PATH.exists():
            return {}
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

        if "job_requirements" in config and isinstance(config["job_requirements"], dict):
            return config["job_requirements"]
        if "jobs" in config and isinstance(config["jobs"], dict):
            return config["jobs"]
        return {}

    def load_config(self):
        """加载岗位配置"""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 支持新旧两种格式
                    if "job_requirements" in config:
                        self.job_rules = config["job_requirements"]
                    elif "jobs" in config:
                        self.job_rules = config["jobs"]
                    else:
                        self.job_rules = {}
            except Exception as e:
                # 配置文件损坏时尝试从备份恢复
                if CONFIG_BACKUP_PATH.exists():
                    try:
                        shutil.copy(CONFIG_BACKUP_PATH, CONFIG_PATH)
                        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                            config = json.load(f)
                            if "job_requirements" in config:
                                self.job_rules = config["job_requirements"]
                            elif "jobs" in config:
                                self.job_rules = config["jobs"]
                    except (json.JSONDecodeError, IOError) as e:
                        print(f"从备份恢复配置失败：{e}")
                        self.job_rules = {}
                else:
                    self.job_rules = {}

    def load_api_config(self, resolve_keys=True):
        """加载 API 配置 - 从系统钥匙串读取加密的 API Key（按服务商管理）"""
        if API_CONFIG_PATH.exists():
            try:
                with open(API_CONFIG_PATH, 'r', encoding='utf-8') as f:
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
                        encrypted_key = get_api_key(current_provider, self.api_config.get("base_url", ""))
                        if encrypted_key:
                            self.api_config["api_key"] = encrypted_key

                    # 检测是否有 saved_models 但 keyring 中无对应 API Key（新电脑场景）
                    if self.api_config["saved_models"]:
                        has_missing_key = False
                        for m in self.api_config["saved_models"]:
                            provider = m.get("api_provider", "")
                            if provider and not get_api_key(provider, m.get("base_url", "")):
                                has_missing_key = True
                                break
                        if has_missing_key:
                            self.api_config["needs_reconfigure"] = True
                            print("[提示] 检测到已保存的模型配置，但 API Key 未配置（可能是新电脑）")
                            print("[提示] 请在「岗位配置」->「API 配置」中重新输入 API Key 并保存")
            except Exception as e:
                print(f"加载 API 配置失败：{e}")
                self.api_config = self._default_api_config()
        else:
            self.api_config = self._default_api_config()

    def _default_api_config(self):
        """返回默认 API 配置"""
        return {
            "api_provider": "deepseek",
            "api_key": "",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "saved_models": [],
            "providers": {},
            "fetched_models": {},
            "llm_read_timeout": None,
        }

    def _sanitize_config_for_save(self, config):
        """移除所有 api_key 字段（顶层 + saved_models 内嵌），返回可安全写入磁盘的副本"""
        clean = {k: v for k, v in config.items() if k != "api_key"}
        if "saved_models" in clean:
            clean["saved_models"] = [
                {k: v for k, v in m.items() if k not in ("api_key", "api_key_ref")}
                for m in clean["saved_models"]
            ]
        return clean

    def _is_education_model_item(self, item_id: str) -> bool:
        """判断 Treeview 中的模型项是否为当前指定的学历核验模型"""
        if not hasattr(self, 'api_config') or not self.api_config:
            return False
        edu_ref = self.api_config.get("education_model_ref")
        if not edu_ref:
            return False
        values = self.model_list_tree.item(item_id, 'values')
        if not values or len(values) < 2:
            return False
        name = values[0]
        provider_display = values[1]
        provider_key = self.DISPLAY_TO_KEY.get(provider_display, provider_display)
        return edu_ref.get("model") == name and edu_ref.get("api_provider") == provider_key

    def _set_education_model(self):
        """将选中模型设为学历核验专用模型"""
        selection = self.model_list_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择一个模型")
            return
        item = self.model_list_tree.item(selection[0])
        values = item['values']
        if not values or len(values) < 2:
            return
        name = values[0]
        provider_display = values[1]
        provider_key = self.DISPLAY_TO_KEY.get(provider_display, provider_display)
        # 从 saved_models 中查找完整配置
        base_url = ""
        for m in getattr(self, 'saved_models', []):
            if m.get("model") == name and m.get("api_provider") == provider_key:
                base_url = m.get("base_url", "")
                break
        if not base_url:
            # fallback: 从 Treeview values 取 base_url（最大化时可见）
            if len(values) >= 5:
                base_url = values[4]
        if not hasattr(self, 'api_config') or not self.api_config:
            return
        self.api_config["education_model_ref"] = {
            "api_provider": provider_key,
            "base_url": base_url,
            "model": name,
        }
        self._save_api_config_to_file()
        self.load_saved_models_to_tree()
        self._update_api_status(
            text=f"✓ 学历核验模型已设为 {provider_display} / {name}",
            foreground=self.colors['success'],
        )

    def _unset_education_model(self):
        """取消学历核验专用模型，回退到全局模型"""
        if not hasattr(self, 'api_config') or not self.api_config:
            return
        if not self.api_config.get("education_model_ref"):
            return
        self.api_config.pop("education_model_ref", None)
        self._save_api_config_to_file()
        self.load_saved_models_to_tree()
        self._update_api_status(
            text="✓ 已取消学历核验专用模型，将使用全局模型",
            foreground=self.colors['success'],
        )

    def _save_api_config_to_file(self):
        """将当前 api_config 持久化到 api_config.json"""
        if not hasattr(self, 'api_config') or not self.api_config:
            return
        try:
            with open(API_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self._sanitize_config_for_save(self.api_config), f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存配置失败：{e}")

    def delete_selected_model(self):
        """删除选中的模型"""
        selection = self.model_list_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择要删除的模型")
            return

        if not messagebox.askyesno("确认", "确定要删除选中的模型吗？"):
            return

        # 获取选中的模型信息
        item = self.model_list_tree.item(selection[0])
        model_name = item['values'][0]
        provider_display = item['values'][1]
        provider_key = self.DISPLAY_TO_KEY.get(provider_display, provider_display)

        # 从列表移除
        if hasattr(self, 'saved_models'):
            self.saved_models = [m for m in self.saved_models if m.get("model") != model_name]

        # 如果删除的是学历核验模型，清除配置
        if hasattr(self, 'api_config') and self.api_config:
            edu_ref = self.api_config.get("education_model_ref")
            if edu_ref and edu_ref.get("model") == model_name and edu_ref.get("api_provider") == provider_key:
                self.api_config.pop("education_model_ref", None)

        # 同步更新 api_config 并持久化到文件
        if hasattr(self, 'api_config') and self.api_config:
            # 检查是否删除了当前正在使用的模型
            current_model = self.api_config.get("model", "")
            if current_model == model_name:
                # 清空当前模型配置
                self.api_config["model"] = ""
                self.api_config["api_provider"] = ""
                self.api_config["api_key"] = ""
                self.api_config["base_url"] = ""
                # 清空 UI 输入
                self.api_provider_var.set(self.PROVIDER_DISPLAY["qwen"])
                self.api_key_var.set("")
                self.api_base_url_var.set("")
                self.api_model_var.set("")
                self.update_current_model_display()
            self.api_config["saved_models"] = self.saved_models
            try:
                save_config = self._sanitize_config_for_save(self.api_config)
                with open(API_CONFIG_PATH, 'w', encoding='utf-8') as f:
                    json.dump(save_config, f, ensure_ascii=False, indent=4)
                self._mark_api_config_ui_current()
            except Exception as e:
                print(f"保存配置失败：{e}")

        # 刷新显示
        self.load_saved_models_to_tree()
        self._update_api_status(text=f"✓ 已删除模型 {model_name}", foreground=self.colors['success'])

    def _update_api_status(self, text, foreground=None):
        """更新 API 状态标签，同时清理之前的可点击标签"""
        # 清理之前的可点击标签
        for lbl in self._status_clickable_labels:
            lbl.destroy()
        self._status_clickable_labels.clear()
        # 更新主标签
        config = {"text": text}
        if foreground is not None:
            config["foreground"] = foreground
        self.api_status_label.config(**config)

    def _is_relay_endpoint_for_timeout(self) -> bool:
        """判断当前 API 配置是否为中转服务（用于读取超时默认值）"""
        from urllib.parse import urlparse
        _OFFICIAL_HOSTS = {
            "qwen": ("dashscope.aliyuncs.com",),
            "deepseek": ("api.deepseek.com",),
            "kimi": ("api.moonshot.cn",),
            "zhipu": ("open.bigmodel.cn",),
            "minimax": ("api.minimaxi.com",),
            "xiaomi": ("api.ai.xiaomi.com", "token-plan-cn.xiaomimimo.com"),
            "stepfun": ("api.stepfun.com",),
            "openai": ("api.openai.com",),
            "anthropic": ("api.anthropic.com",),
        }
        provider = str(self.api_config.get("api_provider") or "").lower()
        hostname = (urlparse(str(self.api_config.get("base_url") or "")).hostname or "").lower()
        official = _OFFICIAL_HOSTS.get(provider)
        if not hostname:
            return False
        if provider == "custom":
            return True
        if not official:
            return True
        return not any(hostname == h or hostname.endswith(f".{h}") for h in official)

    def _update_ai_eval_status(self):
        """更新 AI 评估状态标签和 checkbox 默认值（根据当前 API Key 是否已配置）"""
        if not hasattr(self, 'ai_status_label'):
            return  # UI 尚未创建完成
        has_key = bool(self.api_config.get("api_key"))
        # 首次检测到已配置 Key 时自动启用 AI 评估，后续不覆盖用户手动取消
        if not getattr(self, '_ai_eval_auto_done', False):
            self._ai_eval_auto_done = True
            if has_key:
                self.ai_eval_var.set(True)
        if has_key:
            self.ai_status_label.config(text="✓ 已配置", foreground=self.colors['success'])
        else:
            self.ai_status_label.config(text="⚠ 未配置", foreground=self.colors['warning'])
            # 无 key 时自动关闭 checkbox，防止用户勾选后静默跳过
            if self.ai_eval_var.get():
                self.ai_eval_var.set(False)

    def use_selected_model(self):
        """使用选中的模型 - 从系统钥匙串读取加密的 API Key（按服务商管理）"""
        selection = self.model_list_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择要使用的模型")
            return

        # 获取选中的模型信息
        item = self.model_list_tree.item(selection[0])
        model_name = item['values'][0]
        provider_display = item['values'][1]
        # 将显示名称转换为内部键
        provider_key = self.DISPLAY_TO_KEY.get(provider_display, provider_display)

        # 查找对应的配置
        model_config = None
        for saved in self.saved_models:
            if saved.get("model") == model_name:
                model_config = saved
                break

        if model_config:
            # 从系统钥匙串读取该服务商的 API Key
            _model_base_url = model_config.get("base_url", "")
            saved_api_key = get_api_key(provider_key, _model_base_url)

            if not saved_api_key:
                messagebox.showwarning("警告",
                    f"模型 '{model_name}' 的 API Key 未在系统钥匙串中找到\n\n"
                    f"可能原因：\n"
                    f"1. 系统钥匙串被清理\n"
                    f"2. 配置文件来自其他电脑\n\n"
                    f"请重新输入 API Key 并保存该模型")
                return

            # 更新当前使用的模型配置（包括 API Key）
            self.api_provider_var.set(provider_display)
            self.api_key_var.set(saved_api_key)
            self.api_base_url_var.set(model_config.get("base_url", ""))
            self.api_model_var.set(model_name)

            # 更新 api_config 中的所有字段
            if hasattr(self, 'api_config') and self.api_config:
                self.api_config["model"] = model_name
                self.api_config["api_provider"] = provider_key
                self.api_config["api_key"] = saved_api_key
                self.api_config["base_url"] = model_config.get("base_url", "")

            # 更新显示
            self.update_current_model_display()
            self.load_saved_models_to_tree()

            # 保存到文件（排除 api_key，Key 仅存 keyring）
            try:
                save_config = self._sanitize_config_for_save(self.api_config)
                with open(API_CONFIG_PATH, 'w', encoding='utf-8') as f:
                    json.dump(save_config, f, ensure_ascii=False, indent=4)
                self._mark_api_config_ui_current()
            except Exception as e:
                print(f"保存配置失败：{e}")

            self._update_api_status(text=f"✓ 已切换到 {provider_key}/{model_name}", foreground=self.colors['success'])
            self._update_ai_eval_status()
            messagebox.showinfo("切换成功", f"已切换到模型：\n\n{provider_display} / {model_name}")
        else:
            messagebox.showerror("错误", f"未找到模型 '{model_name}' 的配置信息")

    def test_saved_model_connectivity(self):
        """测试已保存模型连通性，结果直接回写到列表状态列。"""
        selection = self.model_list_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择要测试的模型（Ctrl+点击多选）")
            return

        # 收集所有选中模型的配置
        models_to_test = []
        for item_id in selection:
            item = self.model_list_tree.item(item_id)
            model_name = item['values'][0]
            provider_display = item['values'][1]
            provider_key = self.DISPLAY_TO_KEY.get(provider_display, provider_display)

            model_config = None
            for saved in getattr(self, 'saved_models', []):
                if saved.get("model") == model_name:
                    model_config = saved
                    break
            if not model_config:
                continue

            base_url = model_config.get("base_url", "").strip()
            api_key = get_api_key(provider_key, base_url)
            models_to_test.append({
                "item_id": item_id,
                "model_name": model_name,
                "provider_display": provider_display,
                "provider_key": provider_key,
                "model_config": model_config,
                "base_url": base_url,
                "api_key": api_key,
            })

        if not models_to_test:
            messagebox.showerror("错误", "未找到选中模型的配置信息")
            return

        total = len(models_to_test)
        self._update_api_status(text=f"正在测试 {total} 个模型...", foreground=self.colors['warning'])
        for entry in models_to_test:
            self._set_model_list_item_status(entry["item_id"], "测试中...")

        progress = {"done": 0, "success": 0, "fail": 0}

        def _test_one(entry):
            if not entry["api_key"]:
                result = {"status": "error", "msg": "API Key 未配置"}
            elif not entry["base_url"]:
                result = {"status": "error", "msg": "Base URL 未配置"}
            else:
                try:
                    from llm_eval import probe_model_compatibility
                    config = dict(entry["model_config"])
                    config["api_provider"] = entry["provider_key"]
                    capability = probe_model_compatibility(config, entry["api_key"], force=True)
                    if capability.get("status") in ("compatible", "limited"):
                        result = {
                            "status": "success",
                            "time": capability.get("response_time", 0),
                            "capability": capability,
                        }
                    else:
                        result = {"status": "error", "msg": capability.get("message", "模型不兼容")}
                except Exception as e:
                    result = {"status": "error", "msg": f"异常: {str(e)[:80]}"}

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

        if progress["fail"] == 0:
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
            tmp_path = API_CONFIG_PATH.with_suffix('.json.tmp')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._sanitize_config_for_save(self.api_config), f, ensure_ascii=False, indent=4)
            os.replace(tmp_path, API_CONFIG_PATH)
        except Exception:
            # 写盘失败不影响内存状态，清理临时文件
            try:
                os.remove(API_CONFIG_PATH.with_suffix('.json.tmp'))
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

            if not model_name:
                messagebox.showwarning("警告", "请输入模型名称")
                return
            if not api_key:
                messagebox.showwarning("警告", "请输入 API Key")
                return
            if not base_url:
                messagebox.showwarning("警告", "请输入 Base URL")
                return

            # 按服务商 + Base URL 组合存储 API Key（区分同一服务商的不同接入方式）
            save_api_key(provider, api_key, base_url)

            # 构建当前配置（保留 education_model_ref）
            edu_ref = (self.api_config or {}).get("education_model_ref")
            self.api_config = {
                "api_provider": provider,
                "base_url": base_url,
                "model": model_name,
                "saved_models": getattr(self, 'saved_models', []),
                "providers": self.api_config.get("providers", {}),
                "fetched_models": self.api_config.get("fetched_models", {}),
                "llm_read_timeout": self.llm_read_timeout_var.get() if hasattr(self, 'llm_read_timeout_var') else 60,
            }
            if edu_ref:
                self.api_config["education_model_ref"] = edu_ref

            # 检查当前模型是否已存在于列表
            model_exists = False
            for m in self.api_config["saved_models"]:
                if m.get("model") == model_name and m.get("api_provider") == provider:
                    # 更新已存在模型的配置，保留 capability 字段
                    m["api_provider"] = provider
                    m["base_url"] = base_url
                    # capability 字段保留，不覆盖
                    model_exists = True
                    break

            if not model_exists:
                # 添加新模型
                self.api_config["saved_models"].append({
                    "api_provider": provider,
                    "base_url": base_url,
                    "model": model_name
                })

            with open(API_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(self._sanitize_config_for_save(self.api_config), f, ensure_ascii=False, indent=4)
            self._mark_api_config_ui_current()

            # 更新内存中的模型列表
            self.saved_models = self.api_config["saved_models"]

            # 刷新列表显示
            self.load_saved_models_to_tree()

            # 更新当前模型显示
            self.update_current_model_display()

            self._update_api_status(text="✓ 配置已保存并添加到列表", foreground=self.colors['success'])
            # 更新 AI 评估状态标签（可能从未配置变为已配置）
            self._update_ai_eval_status()

            # 保存成功后清除"API Key 未配置"警示卡片
            if getattr(self, 'reconfig_card', None) and self.reconfig_card.winfo_exists():
                self.reconfig_card.destroy()
                self.reconfig_card = None

            messagebox.showinfo("成功", f"API 配置已保存\n模型 {provider}/{model_name} 已添加到已保存模型列表\n\nAPI Key 已按服务商加密存储（同一服务商的模型共享）")
        except Exception as e:
            self._update_api_status(text=f"✗ 保存失败：{e}", foreground=self.colors['danger'])
            messagebox.showerror("错误", f"保存 API 配置失败：{e}")

    def on_api_provider_changed(self, event):
        """API 服务商改变时更新默认配置"""
        display_name = self.api_provider_var.get()
        # 将显示名称转换为内部键（兼容旧配置）
        provider = self.DISPLAY_TO_KEY.get(display_name, display_name)

        # 主流服务商默认配置（各服务商当前最新主力模型）
        provider_defaults = {
            "qwen": {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-plus"
            },
            "deepseek": {
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-pro"
            },
            "kimi": {
                "base_url": "https://api.moonshot.cn/v1",
                "model": "kimi-k2.6"
            },
            "zhipu": {
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "model": "glm-5.1"
            },
            "minimax": {
                "base_url": "https://api.minimaxi.com/v1",
                "model": "MiniMax-M3"
            },
            "xiaomi": {
                "base_url": "https://api.ai.xiaomi.com/v1",
                "model": "mimo-v2.5-pro"
            },
            "stepfun": {
                "base_url": "https://api.stepfun.com/v1",
                "model": "step-3.7-flash"
            },
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "model": "GPT-5.5"
            },
            "anthropic": {
                "base_url": "https://api.anthropic.com/v1",
                "model": "claude-sonnet4.8"
            },
            "custom": {
                "base_url": "",
                "model": ""
            }
        }

        # 优先从已保存模型中读取该服务商最近使用的配置
        current_provider = self.api_config.get("api_provider", "") if hasattr(self, 'api_config') and self.api_config else ""
        saved_models = getattr(self, 'saved_models', [])
        resolved_base_url = ""

        if current_provider == provider:
            # 正在使用这个服务商，显示当前使用的模型配置
            resolved_base_url = self.api_config.get("base_url", "")
            self.api_base_url_var.set(resolved_base_url)
            self.api_model_var.set(self.api_config.get("model", ""))
        else:
            # 不是当前服务商，从已保存模型中找该服务商最近使用的配置
            provider_saved = [m for m in saved_models if m.get("api_provider") == provider]
            if provider_saved:
                last_config = provider_saved[-1]
                resolved_base_url = last_config.get("base_url", "")
                self.api_base_url_var.set(resolved_base_url)
                self.api_model_var.set(last_config.get("model", ""))
            elif provider in provider_defaults:
                config = provider_defaults[provider]
                resolved_base_url = config["base_url"]
                self.api_base_url_var.set(resolved_base_url)
                self.api_model_var.set(config["model"])

        # 切换服务商时，从 keyring 读取该服务商的 API Key，没有则清空
        saved_key = get_api_key(provider, resolved_base_url)
        self.api_key_var.set(saved_key if saved_key else "")

    _model_dialog = None  # 防止重复打开模型列表对话框

    def fetch_model_list(self):
        """获取服务商的模型列表 - 使用当前输入的 API Key 和 Base URL"""
        import requests
        import certifi
        import json

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
        provider = self.api_provider_var.get()

        if not api_key:
            messagebox.showwarning("警告", "请先输入 API Key")
            return

        if not base_url:
            messagebox.showwarning("警告", "请先输入 Base URL")
            return

        # 显示加载中状态（不使用 update()，避免重入）
        self._update_api_status(text="⏳ 正在获取模型列表...", foreground=self.colors['warning'])

        def fetch_thread():
            try:
                # 构建模型列表 API 端点
                # 大部分服务商兼容 OpenAI 格式：GET /v1/models
                models_url = f"{base_url.rstrip('/')}/models"

                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": USER_AGENT
                }

                # 发送请求，超时 15 秒
                response = requests.get(
                    models_url,
                    headers=headers,
                    timeout=15,
                    verify=certifi.where()
                )

                if response.status_code == 200:
                    data = response.json()

                    # 解析模型列表（兼容 OpenAI 格式）
                    raw_models = []
                    if "data" in data:
                        # OpenAI / DeepSeek / Kimi / 智谱等格式
                        for item in data["data"]:
                            if isinstance(item, dict):
                                model_id = item.get("id", "")
                                if model_id:
                                    raw_models.append(model_id)
                            elif isinstance(item, str):
                                raw_models.append(item)
                    elif "models" in data:
                        # 部分服务商格式
                        raw_models = data["models"]

                    if raw_models:
                        # 过滤非聊天模型
                        # 排除 embedding、rerank、tts、whisper 等非对话模型
                        exclude_keywords = ['embedding', 'embed-', 'rerank', 'tts-', 'whisper',
                                           'similarity', 'moderation', 'dap', 'tokenizer']
                        chat_models = []
                        for model_id in raw_models:
                            model_lower = model_id.lower()
                            # 检查是否包含排除关键词
                            is_excluded = any(kw in model_lower for kw in exclude_keywords)
                            if not is_excluded:
                                chat_models.append(model_id)

                        # 去重并排序
                        models = sorted(list(set(chat_models)))
                        filtered_count = len(raw_models) - len(models)

                        # 对比上次获取的模型列表，找出新增和下线模型
                        fetched_models_map = self.api_config.get("fetched_models", {})
                        previous_models = set(fetched_models_map.get(provider, []))
                        current_models = set(models)
                        new_models = current_models - previous_models
                        removed_models = previous_models - current_models

                        # 更新已获取模型列表并持久化
                        if "fetched_models" not in self.api_config:
                            self.api_config["fetched_models"] = {}
                        self.api_config["fetched_models"][provider] = models
                        try:
                            with open(API_CONFIG_PATH, 'w', encoding='utf-8') as _f:
                                json.dump(self._sanitize_config_for_save(self.api_config), _f, ensure_ascii=False, indent=4)
                            self._mark_api_config_ui_current()
                        except Exception:
                            pass  # 持久化失败不影响主流程

                        # 创建选择对话框
                        def show_model_dialog():
                            # 防止重复打开（可能在 after 调度期间再次触发）
                            if self._model_dialog is not None:
                                try:
                                    self._model_dialog.lift()
                                    return
                                except tk.TclError:
                                    self._model_dialog = None

                            def _close_dialog():
                                """统一关闭对话框，清理引用"""
                                self._model_dialog = None
                                try:
                                    dialog.destroy()
                                except tk.TclError:
                                    pass

                            dialog = tk.Toplevel(self.root)
                            self._model_dialog = dialog
                            dialog.title("选择模型")
                            dialog.transient(self.root)
                            dialog.withdraw()  # 先隐藏，布局完成后再定位显示
                            dialog.configure(background=self.colors['bg_card'])
                            # 对话框内标签统一白底，避免 macOS aqua 灰底上出现白色方块
                            _dlg_style = ttk.Style(dialog)
                            _dlg_style.configure('Dialog.TLabel', background=self.colors['bg_card'])

                            # 对话框大小
                            dialog_width = 750
                            dialog_height = 800
                            dialog.resizable(True, True)
                            dialog.minsize(500, 400)

                            # 关闭按钮（红叉）也走统一清理
                            dialog.protocol("WM_DELETE_WINDOW", _close_dialog)

                            # 标题
                            title_text = f"{provider} - 可用模型 ({len(models)} 个)"
                            info_label = ttk.Label(dialog, text=title_text,
                                                   font=self.font_section,
                                                   style='Dialog.TLabel')
                            info_label.pack(pady=(15, 0))

                            # 过滤说明
                            filter_note = "已自动过滤 embedding、rerank、tts 等非聊天模型" if filtered_count > 0 else ""
                            if filter_note:
                                note_label = ttk.Label(dialog, text=filter_note,
                                                       font=(FONT_FAMILY, int(11 * self.font_scale)),
                                                       foreground=self.colors['warning'],
                                                       style='Dialog.TLabel')
                                note_label.pack(pady=(4, 0))

                            # 新增模型提醒（放在过滤说明和列表之间）
                            if new_models:
                                new_frame = ttk.Frame(dialog, style='Dialog.TFrame')
                                new_frame.pack(pady=(4, 0))
                                ttk.Label(new_frame, text="✦ 发现 ",
                                    font=(FONT_FAMILY, int(11 * self.font_scale)),
                                    foreground=self.colors['success'],
                                    style='Dialog.TLabel').pack(side="left")
                                new_num_label = ttk.Label(new_frame,
                                    text=f"{len(new_models)}",
                                    font=(FONT_FAMILY, int(11 * self.font_scale), 'bold'),
                                    foreground=self.colors['success'],
                                    cursor="hand2",
                                    style='Dialog.TLabel')
                                new_num_label.pack(side="left")
                                new_num_label.bind("<Button-1>", lambda e: _show_model_detail('new'))
                                ttk.Label(new_frame, text=" 个新增模型（绿色标记）",
                                    font=(FONT_FAMILY, int(11 * self.font_scale)),
                                    foreground=self.colors['success'],
                                    style='Dialog.TLabel').pack(side="left")
                            # 下线模型提醒
                            if removed_models:
                                removed_frame = ttk.Frame(dialog, style='Dialog.TFrame')
                                removed_frame.pack(pady=(4, 0))
                                ttk.Label(removed_frame, text="⚠ ",
                                    font=(FONT_FAMILY, int(11 * self.font_scale)),
                                    foreground=self.colors['danger'],
                                    style='Dialog.TLabel').pack(side="left")
                                removed_num_label = ttk.Label(removed_frame,
                                    text=f"{len(removed_models)}",
                                    font=(FONT_FAMILY, int(11 * self.font_scale), 'bold'),
                                    foreground=self.colors['danger'],
                                    cursor="hand2",
                                    style='Dialog.TLabel')
                                removed_num_label.pack(side="left")
                                removed_num_label.bind("<Button-1>", lambda e: _show_model_detail('removed'))
                                ttk.Label(removed_frame, text=" 个模型已下线（已从服务商移除）",
                                    font=(FONT_FAMILY, int(11 * self.font_scale)),
                                    foreground=self.colors['danger'],
                                    style='Dialog.TLabel').pack(side="left")

                            # 列表前的间距（有提醒文字时加间距，没有时由列表自带间距）
                            if filter_note or new_models:
                                ttk.Frame(dialog, height=8).pack()

                            # 搜索框
                            search_frame = ttk.Frame(dialog)
                            search_frame.pack(fill="x", padx=20, pady=(6, 0))

                            search_var = tk.StringVar()
                            search_entry = ttk.Entry(search_frame, textvariable=search_var,
                                                     font=self.font_label)
                            search_entry.pack(fill="x")

                            # 占位文字
                            _search_placeholder = "输入关键词搜索模型..."
                            search_entry.config(foreground=self.colors['text_muted'])
                            search_var.set(_search_placeholder)
                            _search_active = [False]  # 用列表避免闭包问题

                            def _on_search_focus_in(event=None):
                                if not _search_active[0]:
                                    _search_active[0] = True
                                    search_var.set("")
                                    search_entry.config(foreground=self.colors['text_primary'])

                            def _on_search_focus_out(event=None):
                                if not search_var.get():
                                    _search_active[0] = False
                                    search_var.set(_search_placeholder)
                                    search_entry.config(foreground=self.colors['text_muted'])

                            search_entry.bind("<FocusIn>", _on_search_focus_in)
                            search_entry.bind("<FocusOut>", _on_search_focus_out)

                            # 模型列表框
                            listbox_frame = ttk.Frame(dialog)
                            listbox_frame.pack(fill="both", expand=True, padx=20, pady=10)

                            listbox = tk.Listbox(listbox_frame, font=self.font_label, height=10, selectmode=tk.EXTENDED)
                            scrollbar = ttk.Scrollbar(listbox_frame, orient="vertical", command=listbox.yview)
                            listbox.configure(yscrollcommand=scrollbar.set)

                            scrollbar.pack(side="right", fill="y")
                            listbox.pack(side="left", fill="both", expand=True)

                            def _refresh_listbox(query=""):
                                """根据搜索词刷新列表，保持新增模型绿色高亮"""
                                listbox.delete(0, "end")
                                q = query.lower()
                                for model in models:
                                    if not q or q in model.lower():
                                        listbox.insert("end", model)
                                # 新增模型绿色高亮
                                if new_models:
                                    for i in range(listbox.size()):
                                        if listbox.get(i) in new_models:
                                            listbox.itemconfig(i, foreground=self.colors['success'])
                                # 自动选中第一项
                                if listbox.size() > 0:
                                    listbox.selection_set(0)
                                    listbox.see(0)

                            def _on_search_changed(*args):
                                if _search_active[0]:
                                    _refresh_listbox(search_var.get().strip())

                            search_var.trace_add("write", _on_search_changed)

                            # 初始填充
                            _refresh_listbox()

                            # 右键菜单 - 测试连通性
                            _ctx_menu_font = (FONT_FAMILY, int(12 * self.font_scale))
                            _ctx_menu = tk.Menu(listbox, tearoff=0, font=_ctx_menu_font)
                            _ctx_menu.add_command(label="测试连通性", command=lambda: _test_model_in_dialog())

                            def _show_ctx_menu(event):
                                idx = listbox.nearest(event.y)
                                if idx >= 0:
                                    # 如果点击的项未选中，清除其他选择只选这一项
                                    # 如果已选中，保持当前多选状态
                                    if idx not in listbox.curselection():
                                        listbox.selection_clear(0, "end")
                                        listbox.selection_set(idx)
                                    _ctx_menu.tk_popup(event.x_root, event.y_root)

                            def _test_model_in_dialog():
                                """在选择模型对话框中测试选中模型的连通性（支持多选并行测试）"""
                                selection = listbox.curselection()
                                if not selection:
                                    return

                                test_models = [listbox.get(idx) for idx in selection]

                                # 获取 API Key 和 Base URL
                                provider_key = self.DISPLAY_TO_KEY.get(provider, provider)
                                test_base_url = self.api_base_url_var.get().strip()
                                test_api_key = get_api_key(provider_key, test_base_url)

                                if not test_api_key:
                                    messagebox.showwarning("警告",
                                        f"请先配置 {self.PROVIDER_DISPLAY.get(provider_key, provider)} 的 API Key",
                                        parent=dialog)
                                    return
                                if not test_base_url:
                                    messagebox.showwarning("警告", "请先配置 Base URL", parent=dialog)
                                    return

                                # 在列表项中显示测试状态
                                for idx in selection:
                                    current_text = listbox.get(idx)
                                    # 清除旧的状态标记（如果有）
                                    if " [" in current_text:
                                        current_text = current_text.split(" [")[0]
                                    listbox.delete(idx)
                                    listbox.insert(idx, f"{current_text} [测试中...]")

                                # 测试结果收集
                                results = {}
                                results_lock = threading.Lock()

                                def _test_single_model(model_name):
                                    """测试单个模型能否稳定生成程序所需评估格式。"""
                                    try:
                                        from llm_eval import probe_model_compatibility
                                        capability = probe_model_compatibility({
                                            "api_provider": provider_key,
                                            "base_url": test_base_url,
                                            "model": model_name,
                                        }, test_api_key, force=True)
                                        if capability.get("status") in ("compatible", "limited"):
                                            mode = "工具" if capability.get("output_mode") == "tool" else "兼容"
                                            result = {
                                                "status": "success",
                                                "time": capability.get("response_time", 0),
                                                "mode": mode,
                                            }
                                        else:
                                            result = {"status": "error", "msg": capability.get("message", "不兼容")}
                                    except Exception as e:
                                        result = {"status": "error", "msg": f"异常: {str(e)[:50]}"}

                                    with results_lock:
                                        results[model_name] = result

                                    # 更新列表项状态
                                    for idx in selection:
                                        if listbox.get(idx).startswith(model_name):
                                            # 清除旧状态
                                            current_text = listbox.get(idx)
                                            if " [" in current_text:
                                                current_text = current_text.split(" [")[0]
                                            # 设置新状态
                                            if result["status"] == "success":
                                                new_text = f"{current_text} [✓ {result.get('mode', '兼容')} {result['time']:.1f}s]"
                                                self.root.after(0, lambda i=idx, t=new_text: (
                                                    listbox.delete(i),
                                                    listbox.insert(i, t),
                                                    listbox.itemconfig(i, foreground=self.colors['success'])
                                                ))
                                            else:
                                                new_text = f"{current_text} [✗ {result['msg']}]"
                                                self.root.after(0, lambda i=idx, t=new_text: (
                                                    listbox.delete(i),
                                                    listbox.insert(i, t),
                                                    listbox.itemconfig(i, foreground=self.colors['text_muted'])
                                                ))
                                            break

                                # 启动所有测试线程
                                threads = []
                                for model_name in test_models:
                                    t = threading.Thread(target=_test_single_model, args=(model_name,), daemon=True)
                                    threads.append(t)
                                    t.start()

                                # 等待所有测试完成并显示汇总
                                def _show_summary():
                                    for t in threads:
                                        t.join()

                                    success_count = sum(1 for r in results.values() if r["status"] == "success")
                                    fail_count = len(results) - success_count

                                    if len(test_models) == 1:
                                        # 单个模型测试，直接显示结果
                                        model_name = test_models[0]
                                        result = results[model_name]
                                        if result["status"] == "success":
                                            self.root.after(0, lambda: messagebox.showinfo(
                                                "测试成功",
                                                f"模型 {model_name} 连通正常，响应时间 {result['time']:.1f} 秒",
                                                parent=dialog
                                            ))
                                        else:
                                            self.root.after(0, lambda: messagebox.showerror(
                                                "测试失败",
                                                f"模型 {model_name}: {result['msg']}",
                                                parent=dialog
                                            ))
                                    else:
                                        # 多个模型测试，显示简要汇总
                                        summary = f"测试完成：{success_count} 个可用，{fail_count} 个不可用"

                                        self.root.after(0, lambda: messagebox.showinfo(
                                            "批量测试结果",
                                            summary,
                                            parent=dialog
                                        ))

                                threading.Thread(target=_show_summary, daemon=True).start()

                            listbox.bind("<Button-3>", _show_ctx_menu)

                            def _select_all(event=None):
                                listbox.selection_set(0, "end")
                                return "break"

                            listbox.bind("<Control-a>", _select_all)
                            listbox.bind("<Control-A>", _select_all)

                            # 按钮行
                            btn_frame = ttk.Frame(dialog)
                            btn_frame.pack(fill="x", padx=25, pady=(10, 15))

                            def _get_model_name(idx):
                                """获取模型名称，去掉连通性测试的状态后缀"""
                                text = listbox.get(idx)
                                if " [" in text:
                                    text = text.split(" [")[0]
                                return text

                            def on_select(event=None):
                                selection = listbox.curselection()
                                if selection:
                                    selected_model = _get_model_name(selection[0])
                                    self.api_model_var.set(selected_model)
                                    self._update_api_status(
                                        text=f"✓ 已选择 {selected_model}",
                                        foreground=self.colors['success']
                                    )
                                    _close_dialog()

                            def on_double_click(event):
                                selection = listbox.curselection()
                                if selection:
                                    selected_model = _get_model_name(selection[0])
                                    self.api_model_var.set(selected_model)
                                    _close_dialog()
                                    self._update_api_status(text="⏳ 正在测试连接...", foreground=self.colors['warning'])
                                    self.root.after(300, self.test_api_connection)

                            # 按钮布局（居中）
                            btn_inner = ttk.Frame(btn_frame)
                            btn_inner.pack()
                            ttk.Button(btn_inner, text="确定", command=on_select, width=12).pack(side="left", padx=8)
                            ttk.Button(btn_inner, text="取消", command=_close_dialog, width=12).pack(side="left", padx=8)

                            # 绑定回车键和双击
                            dialog.bind("<Return>", lambda e: on_select())
                            listbox.bind("<Double-Button-1>", on_double_click)

                            _place_window_centered(dialog, dialog_width, dialog_height, parent=self.root)
                            dialog.deiconify()
                            dialog.grab_set()
                            # 不使用 wait_window()：它会创建嵌套事件循环，
                            # 在 macOS 上与 Cocoa scroll hook 和浏览器轮询冲突导致崩溃。
                            # grab_set() 已提供模态行为，无需阻塞。

                        _new_count = len(new_models)
                        _removed_count = len(removed_models)
                        _total_count = len(models)

                        def _show_model_detail(detail_type):
                            """点击状态栏数字时显示详细列表"""
                            if detail_type == 'new' and new_models:
                                messagebox.showinfo(
                                    "新增模型列表",
                                    f"{provider} 新增 {_new_count} 个模型：\n\n"
                                    + "\n".join(f"  • {m}" for m in sorted(new_models)[:20])
                                    + (f"\n  …等共 {_new_count} 个" if _new_count > 20 else "")
                                )
                            elif detail_type == 'removed' and removed_models:
                                messagebox.showwarning(
                                    "下线模型列表",
                                    f"{provider} 有 {_removed_count} 个模型已下线：\n\n"
                                    + "\n".join(f"  • {m}" for m in sorted(removed_models)[:20])
                                    + (f"\n  …等共 {_removed_count} 个" if _removed_count > 20 else "")
                                    + "\n\n如正在使用这些模型，请尽快切换。"
                                )

                        def _update_status():
                            # 清理之前的可点击标签
                            for lbl in self._status_clickable_labels:
                                lbl.destroy()
                            self._status_clickable_labels.clear()

                            # 基础信息
                            base_text = f"✓ 找到 {_total_count} 个模型"
                            if _new_count == 0 and _removed_count == 0:
                                # 无变更，只显示基础信息
                                self._update_api_status(
                                    text=base_text,
                                    foreground=self.colors['success']
                                )
                            else:
                                # 有变更，先清理旧标签，再分段显示
                                for lbl in self._status_clickable_labels:
                                    lbl.destroy()
                                self._status_clickable_labels.clear()
                                self.api_status_label.config(
                                    text=base_text + "（",
                                    foreground=self.colors['success']
                                )

                                # 新增数量（可点击）
                                if _new_count > 0:
                                    lbl_new = ttk.Label(self.api_status_frame, text=f"{_new_count} 个新增",
                                                       font=(FONT_FAMILY, int(11 * self.font_scale)),
                                                       foreground=self.colors['success'],
                                                       cursor="hand2")
                                    lbl_new.pack(side="left")
                                    lbl_new.bind("<Button-1>", lambda e: _show_model_detail('new'))
                                    self._status_clickable_labels.append(lbl_new)

                                # 分隔符
                                if _new_count > 0 and _removed_count > 0:
                                    lbl_sep = ttk.Label(self.api_status_frame, text="，",
                                                       font=(FONT_FAMILY, int(11 * self.font_scale)),
                                                       foreground=self.colors['success'])
                                    lbl_sep.pack(side="left")
                                    self._status_clickable_labels.append(lbl_sep)

                                # 下线数量（可点击）
                                if _removed_count > 0:
                                    lbl_removed = ttk.Label(self.api_status_frame, text=f"{_removed_count} 个下线",
                                                           font=(FONT_FAMILY, int(11 * self.font_scale)),
                                                           foreground=self.colors['warning'],
                                                           cursor="hand2")
                                    lbl_removed.pack(side="left")
                                    lbl_removed.bind("<Button-1>", lambda e: _show_model_detail('removed'))
                                    self._status_clickable_labels.append(lbl_removed)

                                # 右括号
                                lbl_close = ttk.Label(self.api_status_frame, text="）",
                                                     font=(FONT_FAMILY, int(11 * self.font_scale)),
                                                     foreground=self.colors['success'])
                                lbl_close.pack(side="left")
                                self._status_clickable_labels.append(lbl_close)

                        self.root.after(0, _update_status)
                        if new_models or removed_models:
                            def _show_models_alert():
                                msg_parts = []
                                if new_models:
                                    msg_parts.append(f"✦ 新增 {len(new_models)} 个模型：\n"
                                        + "\n".join(f"  • {m}" for m in sorted(new_models)[:10])
                                        + (f"\n  …等共 {len(new_models)} 个" if len(new_models) > 10 else ""))
                                if removed_models:
                                    msg_parts.append(f"⚠ 下线 {len(removed_models)} 个模型：\n"
                                        + "\n".join(f"  • {m}" for m in sorted(removed_models)[:10])
                                        + (f"\n  …等共 {len(removed_models)} 个" if len(removed_models) > 10 else "")
                                        + "\n\n如正在使用这些模型，请尽快切换。")
                                messagebox.showinfo(
                                    "模型列表变更",
                                    f"{provider} 模型列表有变更：\n\n" + "\n\n".join(msg_parts)
                                )
                                self.root.after(100, show_model_dialog)
                            self.root.after(0, _show_models_alert)
                        else:
                            self.root.after(100, show_model_dialog)
                    else:
                        self.root.after(0, lambda: self._update_api_status(
                            text="⚠️ 未找到模型列表",
                            foreground=self.colors['warning']
                        ))
                        self.root.after(0, lambda: messagebox.showwarning(
                            "未找到模型",
                            f"API 返回的数据中没有模型列表\n\n响应内容：{json.dumps(data, ensure_ascii=False)[:500]}"
                        ))
                elif response.status_code == 401:
                    self.root.after(0, lambda: self._update_api_status(
                        text="✗ 认证失败",
                        foreground=self.colors['danger']
                    ))
                    self.root.after(0, lambda: messagebox.showerror(
                        "认证失败",
                        "API Key 无效或已过期\n\n请检查 API Key 是否正确"
                    ))
                elif response.status_code == 404:
                    self.root.after(0, lambda: self._update_api_status(
                        text="✗ 接口不存在",
                        foreground=self.colors['danger']
                    ))
                    self.root.after(0, lambda: messagebox.showwarning(
                        "接口不支持",
                        f"该服务商不支持 /models 接口获取模型列表\n\n"
                        f"HTTP 状态码：404\n\n"
                        f"建议：\n"
                        f"• 手动输入模型名称\n"
                        f"• 参考服务商文档获取可用模型"
                    ))
                else:
                    self.root.after(0, lambda: self._update_api_status(
                        text=f"✗ 请求失败 ({response.status_code})",
                        foreground=self.colors['danger']
                    ))
                    self.root.after(0, lambda: messagebox.showerror(
                        "请求失败",
                        f"HTTP 状态码：{response.status_code}\n\n"
                        f"响应：{response.text[:300]}"
                    ))

            except requests.exceptions.Timeout:
                self.root.after(0, lambda: self._update_api_status(
                    text="⏱️ 请求超时",
                    foreground=self.colors['warning']
                ))
                self.root.after(0, lambda: messagebox.showwarning(
                    "请求超时",
                    "获取模型列表超时\n\n"
                    "可能原因：\n"
                    "• 网络连接不稳定\n"
                    "• 服务商不支持模型列表接口\n"
                    "• 需要配置代理"
                ))
            except requests.exceptions.ConnectionError as e:
                self.root.after(0, lambda: self._update_api_status(
                    text="✗ 连接失败",
                    foreground=self.colors['danger']
                ))
                self.root.after(0, lambda m=str(e)[:200]: messagebox.showerror(
                    "连接失败",
                    f"无法连接到 API 服务器\n\n"
                    f"错误详情：{m}"
                ))
            except Exception as e:
                self.root.after(0, lambda: self._update_api_status(
                    text="✗ 请求失败",
                    foreground=self.colors['danger']
                ))
                self.root.after(0, lambda m=str(e)[:200]: messagebox.showerror(
                    "请求失败",
                    f"获取模型列表时发生错误\n\n"
                    f"错误详情：{m}"
                ))

        threading.Thread(target=fetch_thread, daemon=True).start()

    def toggle_api_key_visibility(self):
        """切换 API Key 明文/密文显示"""
        if self.api_key_show_var.get():
            # 当前是明文，切换为密文
            self.api_key_entry.configure(show="*")
            self.api_key_toggle_btn.configure(image=self.api_key_toggle_btn._icon_eye)
            self.api_key_show_var.set(False)
        else:
            # 当前是密文，切换为明文
            self.api_key_entry.configure(show="")
            self.api_key_toggle_btn.configure(image=self.api_key_toggle_btn._icon_eye_off)
            self.api_key_show_var.set(True)

    def test_api_connection(self):
        """测试 API 连接 - 高可用版本：每次全新连接 + 并行双策略 + 宽松超时"""
        api_key = self.api_key_var.get().strip()
        base_url = self.api_base_url_var.get().strip()
        model = self.api_model_var.get().strip()

        if not api_key:
            messagebox.showwarning("警告", "请先输入 API Key")
            return

        if not base_url:
            messagebox.showwarning("警告", "请先输入 Base URL")
            return

        if not model:
            messagebox.showwarning("警告", "请先输入模型名称")
            return

        # 显示测试中状态
        self._update_api_status(text="⏳ 正在验证...", foreground=self.colors['warning'])

        def test_thread():
            import socket
            start_time = time.time()

            # 关键优化：每次测试使用全新 Session，避免 stale connection
            # 这是 50% 失败率的根本原因
            import requests
            import certifi

            # 解析 URL 获取主机，用于 DNS 预检查
            parsed = urlparse(base_url)
            hostname = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)

            # === 阶段 1: DNS 解析检查（快速失败）===
            try:
                ip_addr = socket.gethostbyname(hostname)
                # DNS 解析成功，继续
            except socket.gaierror:
                elapsed = time.time() - start_time
                self.root.after(0, lambda: self._update_api_status(text="✗ DNS 解析失败", foreground=self.colors['danger']))
                self.root.after(0, lambda: messagebox.showerror(
                    "DNS 解析失败",
                    f"无法解析域名：{hostname}\n\n"
                    f"请检查：\n"
                    f"• Base URL 中的域名是否正确\n"
                    f"• DNS 服务器是否可用\n"
                    f"• 是否需要配置 hosts 文件"
                ))
                return

            # 连通不等于可用：真实验证该模型能否生成程序可解析的评估结果。
            try:
                from llm_eval import probe_model_compatibility
                provider_display = self.api_provider_var.get().strip()
                provider_key = self.DISPLAY_TO_KEY.get(provider_display, provider_display)
                capability = probe_model_compatibility({
                    "api_provider": provider_key,
                    "base_url": base_url,
                    "model": model,
                }, api_key, force=True)
                elapsed = time.time() - start_time
                if capability.get("status") in ("compatible", "limited"):
                    compatibility = "完整兼容" if capability.get("status") == "compatible" else "兼容模式"
                    output_mode = "结构化工具调用" if capability.get("output_mode") == "tool" else "JSON 文本自动纠错"
                    self.root.after(0, lambda: self._update_api_status(
                        text=f"✓ {compatibility} ({elapsed:.1f}s)",
                        foreground=self.colors['success'],
                    ))
                    self.root.after(0, lambda: messagebox.showinfo(
                        "连接测试成功",
                        f"模型可用于 AI 评估\n\n"
                        f"响应时间：{elapsed:.1f}秒\n"
                        f"服务商：{provider_display}\n"
                        f"模型：{model}\n"
                        f"兼容状态：{compatibility}\n"
                        f"输出方式：{output_mode}",
                    ))
                else:
                    error_message = capability.get("message", "模型无法生成程序所需评估格式")
                    self.root.after(0, lambda: self._update_api_status(
                        text="✗ 模型不兼容",
                        foreground=self.colors['danger'],
                    ))
                    self.root.after(0, lambda: messagebox.showerror(
                        "连接测试失败",
                        f"API 可访问，但模型不能用于 AI 评估\n\n原因：{error_message}",
                    ))
                return
            except Exception as e:
                error_message = str(e)[:120]
                self.root.after(0, lambda: self._update_api_status(
                    text="✗ 能力验证失败",
                    foreground=self.colors['danger'],
                ))
                self.root.after(0, lambda: messagebox.showerror(
                    "连接测试失败",
                    f"模型能力验证异常：{error_message}",
                ))
                return

            # === 阶段 2: TCP 连接检查（可选，快速判断网络可达性）===
            # 国内 API 跳过此步（减少一次握手），海外 API 执行
            is_domestic = any(dom in base_url.lower() for dom in ['aliyun', 'tencent', 'baidu', 'volcengine', 'deepseek', 'zhipu', 'stepfun', 'moonshot', 'minimax', 'xiaomi'])

            # === 阶段 3: HTTPS 请求（宽松超时）===
            # 关键：每次使用全新 Session + 禁用 keep-alive，确保连接新鲜
            session = requests.Session()

            # 不配置 HTTPAdapter，让 requests 使用默认行为（每次新建连接）
            # 这样可以避免连接池中的 stale connection 问题

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "User-Agent": USER_AGENT,
                "Connection": "close"  # 强制关闭连接，不复用
            }

            data = {
                "model": model,
                "messages": [{"role": "user", "content": "1"}],  # 最小请求
                "max_tokens": 1,
                "stream": False
            }

            url = f"{base_url.rstrip('/')}/chat/completions"

            # 宽松超时：连接 5 秒 + 读取 25 秒 = 总 30 秒
            # 宁可慢，也要成功，避免假阳性失败
            timeout = (8, 30)

            max_retries = 3  # 增加重试次数
            last_error = None
            last_status = None

            for attempt in range(max_retries):
                try:
                    # 每次重试都使用全新 Session（关键！）
                    if attempt > 0:
                        session.close()
                        session = requests.Session()

                    response = session.post(
                        url,
                        json=data,
                        headers=headers,
                        timeout=timeout,
                        verify=certifi.where()
                    )
                    elapsed = time.time() - start_time
                    last_status = response.status_code

                    if response.status_code == 200:
                        session.close()
                        self.root.after(0, lambda: self._update_api_status(
                            text=f"✓ 验证成功 ({elapsed:.1f}s)",
                            foreground=self.colors['success']
                        ))
                        self.root.after(0, lambda: messagebox.showinfo(
                            "连接测试成功",
                            f"API 连接正常\n\n"
                            f"响应时间：{elapsed:.1f}秒\n"
                            f"服务商：{self.api_provider_var.get().upper()}\n"
                            f"模型：{model}"
                        ))
                        return
                    elif response.status_code == 401:
                        session.close()
                        self.root.after(0, lambda: self._update_api_status(text="✗ 认证失败", foreground=self.colors['danger']))
                        self.root.after(0, lambda: messagebox.showerror(
                            "认证失败",
                            f"API Key 无效或已过期\n\n"
                            f"状态码：401\n"
                            f"请检查 API Key 是否正确"
                        ))
                        return
                    elif response.status_code == 429:
                        session.close()
                        self.root.after(0, lambda: self._update_api_status(text="⚠️ 请求受限", foreground=self.colors['warning']))
                        self.root.after(0, lambda: messagebox.showwarning(
                            "请求限额",
                            f"API 请求超限额\n\n"
                            f"状态码：429\n"
                            f"请稍后重试"
                        ))
                        return
                    else:
                        # 其他状态码，解析响应内容
                        session.close()
                        last_status = response.status_code
                        err_msg = response.text[:500] if response.text else "无响应内容"

                        # 识别常见业务错误
                        friendly = None
                        try:
                            err_json = response.json()
                            code = err_json.get("error", {}).get("code", "")
                            msg_text = err_json.get("error", {}).get("message", "")
                            if "not activated" in msg_text.lower():
                                friendly = "模型未开通\n\n请在服务商控制台开通该模型后再试"
                            elif "quota" in msg_text.lower() or "limit" in msg_text.lower():
                                friendly = "配额超限\n\n" + msg_text
                            elif "free tier" in msg_text.lower() or "allocationquota" in code.lower():
                                friendly = "免费额度已用完\n\n如需继续使用，请在服务商控制台关闭「仅使用免费额度」选项，切换到付费模式"
                        except Exception:
                            pass

                        if attempt < max_retries - 1 and not friendly:
                            time.sleep(0.5)
                            self.root.after(0, lambda a=attempt+2: self._update_api_status(
                                text=f"⏳ 重试中 ({a}/{max_retries})...",
                                foreground=self.colors['warning']
                            ))
                            continue

                        # 重试耗尽或业务错误
                        self.root.after(0, lambda: self._update_api_status(text="✗ 验证失败", foreground=self.colors['danger']))
                        if friendly:
                            self.root.after(0, lambda: messagebox.showerror("连接测试失败", friendly))
                        else:
                            self.root.after(0, lambda: messagebox.showerror(
                                "连接测试失败",
                                f"无法连接到 API 服务\n\nHTTP {response.status_code}"
                            ))
                        return

                except requests.exceptions.Timeout as e:
                    last_error = "连接超时"
                    if attempt < max_retries - 1:
                        # 超时后重试，指数退避
                        wait_time = 1.0 * (attempt + 1)
                        time.sleep(wait_time)
                        self.root.after(0, lambda a=attempt+2: self._update_api_status(
                            text=f"⏳ 重试中 ({a}/{max_retries})...",
                            foreground=self.colors['warning']
                        ))
                        continue
                    # 重试耗尽
                    self.root.after(0, lambda: self._update_api_status(text="✗ 连接超时", foreground=self.colors['danger']))
                    self.root.after(0, lambda: messagebox.showerror(
                        "连接测试失败",
                        "连接超时，请检查网络连接"
                    ))
                    return
                except requests.exceptions.ConnectionError as e:
                    last_error = "无法连接服务器"
                    if attempt < max_retries - 1:
                        wait_time = 0.5 * (attempt + 1)
                        time.sleep(wait_time)
                        self.root.after(0, lambda a=attempt+2: self._update_api_status(
                            text=f"⏳ 重试中 ({a}/{max_retries})...",
                            foreground=self.colors['warning']
                        ))
                        continue
                    # 重试耗尽
                    self.root.after(0, lambda: self._update_api_status(text="✗ 无法连接", foreground=self.colors['danger']))
                    self.root.after(0, lambda: messagebox.showerror(
                        "连接测试失败",
                        "无法连接到服务器，请检查网络和 Base URL"
                    ))
                    return
                except requests.exceptions.SSLError as e:
                    # SSL 错误不重试，直接提示警告
                    last_error = "SSL 证书错误"
                    self.root.after(0, lambda: self._update_api_status(text="⚠️ SSL 错误", foreground=self.colors['warning']))
                    self.root.after(0, lambda: messagebox.showwarning(
                        "SSL 证书错误",
                        "SSL 证书验证失败，可忽略此错误，保存配置后尝试实际使用"
                    ))
                    return
                except Exception as e:
                    last_error = f"{type(e).__name__}: {str(e)[:100]}"
                    if attempt < max_retries - 1:
                        time.sleep(0.5)
                        continue

            # 所有重试失败
            session.close()
            self.root.after(0, lambda: self._update_api_status(text="✗ 验证失败", foreground=self.colors['danger']))

            # 根据最后错误类型给出针对性建议
            if last_status == 401:
                msg = "API Key 无效或已过期，请检查 API Key 是否正确"
            elif "超时" in str(last_error):
                msg = "连接超时，请检查网络连接"
            elif "无法连接" in str(last_error):
                msg = "无法连接到服务器，请检查网络和 Base URL"
            else:
                msg = "连接测试失败，请稍后重试"

            self.root.after(0, lambda: messagebox.showerror(
                "连接测试失败",
                msg
            ))

        # 启动测试线程
        threading.Thread(target=test_thread, daemon=True).start()

    def save_config(self):
        """保存配置文件 - 带备份保护，保留 requirement_template 等顶层字段"""
        # 先备份旧配置
        if CONFIG_PATH.exists():
            try:
                shutil.copy(CONFIG_PATH, CONFIG_BACKUP_PATH)
            except IOError as e:
                print(f"备份配置失败：{e}")

        # 加载已有配置，保留顶层字段（如 requirement_template）
        existing = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        # 更新 job_requirements，保留其他顶层字段；剔除解析溯源等 GUI 临时字段
        existing = {
            key: value
            for key, value in existing.items()
            if not str(key).startswith("_")
        }
        existing["job_requirements"] = self._strip_transient_fields(self.job_rules)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=4)

    def on_job_selected(self, event):
        """岗位选择改变"""
        job_name = self.config_job_combo.get()
        if job_name in self.job_rules:
            rule = self.job_rules[job_name]
            self.load_job_to_form(rule)
            self.requirement_template_btn.state(['disabled'])
            self._hide_requirement_hint()
            self._hide_parse_hint()
            self._hide_save_hint()
            self._show_btn_add_hint()  # 切换到已有岗位时重新显示"点此新增岗位→"提示
            self._hide_job_step_bar()
            # 显示详细结果区域
            self.result_detail_frame.pack(fill="both", expand=True, padx=int(25 * self.dpi_scale * self.zoom_factor), pady=int(15 * self.dpi_scale * self.zoom_factor))
        else:
            # 岗位未选中时也隐藏提示
            self._hide_requirement_hint()
            self._hide_parse_hint()
            self._hide_save_hint()

    def load_job_to_form(self, rule):
        """将岗位配置加载到表单（包含话术模板）"""
        # 岗位名称使用 combo 中选中的名称（而不是 rule 中的 job_title）
        job_name = self.config_job_combo.get()
        self.job_name_var.set(job_name)
        self.min_exp_var.set(str(rule.get("min_exp", 0)))
        self.max_age_var.set(_optional_int_to_entry(rule.get("max_age", 35)))
        self.edu_var.set(rule.get("edu", "不限"))
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

    def _populate_skills_from_config(self, job_config, source_map, source_override=None):
        """从 job_config 构建 skills_data（含原文出处）。

        source_override: 若提供，应为 {normalized_key: source_str} 映射，
        用于 AI 增强阶段保留 regex 阶段的来源标记。未命中时 fallback 为 "AI新增"/"AI优先"。
        """
        evidence = {item.get("name", ""): item.get("evidence", "")
                    for item in source_map.get("skills", [])}
        self.skills_data = []
        for kw in job_config.get("keywords", []):
            name = kw.get("name", "") if isinstance(kw, dict) else kw
            if source_override is not None:
                key = re.sub(r'\s+', '', name).lower()
                source = source_override.get(key, "AI新增")
            else:
                source = "解析"
            self.skills_data.append({
                "name": name,
                "weight": kw.get("weight", 1) if isinstance(kw, dict) else 1,
                "source": source,
                "evidence": evidence.get(name, ""),
            })
        for kw in job_config.get("preferred_keywords", []):
            name = kw.get("name", "") if isinstance(kw, dict) else kw
            if source_override is not None:
                key = re.sub(r'\s+', '', name).lower()
                source = source_override.get(key, "AI优先")
            else:
                source = "优先"
            self.skills_data.append({
                "name": name,
                "weight": kw.get("bonus", kw.get("weight", 1)) if isinstance(kw, dict) else 1,
                "source": source,
                "evidence": evidence.get(name, ""),
            })
        self.refresh_skills_tree()

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
            "min_exp": self.min_exp_var.get(),
            "max_age": self.max_age_var.get(),
            "work_location": self.work_location_var.get(),
            "salary": (self.salary_min_var.get(), self.salary_max_var.get()),
            "skills": self._skills_data_fingerprint(),
            "required_conditions": self._required_conditions_fingerprint(),
        }

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

        # 检查是否已存在
        for s in self.skills_data:
            if s["name"].lower() == skill_name.lower():
                messagebox.showwarning("警告", "该技能已存在")
                return

        self.skills_data.append({"name": skill_name, "weight": weight, "source": "手动"})
        self.refresh_skills_tree()
        self.new_skill_var.set("")
        messagebox.showinfo("成功", f"已添加技能：{skill_name}（权重{weight}）")

    def delete_skill(self):
        """删除选中技能"""
        selection = self.skills_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先在列表中选择要删除的技能")
            return
        if messagebox.askyesno("确认删除", f"确定要删除选中的 {len(selection)} 个技能吗？"):
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
        messagebox.showinfo("成功", f"已更新技能权重为 {weight}")

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
                entry.configure(foreground='red')
            else:
                entry.configure(foreground=self.colors['text_primary'])

    # ── 筛选结果页日期过滤（日历控件） ─────────────────────────────────

    @staticmethod
    def _wrap_date_dropdown_mutex(this_entry, other_entry):
        """包装 DateEntry.drop_down，展开自己前先收起对方的下拉日历"""
        original_drop_down = getattr(this_entry, 'drop_down', None)
        if not callable(original_drop_down):
            return

        def _wrapped_drop_down():
            other_top = getattr(other_entry, '_top_cal', None)
            if other_top and other_top.winfo_ismapped():
                other_top.withdraw()
            original_drop_down()

        this_entry.drop_down = _wrapped_drop_down

    def _clear_result_dates(self):
        """重置两个日期控件为一周前 ~ 今天"""
        today = datetime.now().date()
        self.result_date_start_entry.set_date(today - timedelta(days=7))
        self.result_date_end_entry.set_date(today)
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

    def _show_requirement_hint(self):
        """显示「点击查看需求示例->」提示标签（重建控件并启动呼吸动画）"""
        if hasattr(self, 'requirement_hint_label') and self.requirement_hint_label.winfo_exists():
            return
        self.requirement_hint_label = ttk.Label(
            self._req_header_frame, text="点击查看需求示例->", font=self.font_label,
            foreground=self.colors['success'], background=self.colors['bg_card'])
        self.requirement_hint_label.pack(side="right", padx=(0, int(4 * self.dpi_scale * self.zoom_factor)))
        self.requirement_hint_label.bind("<Button-1>", lambda e: self._insert_requirement_template())
        self._start_breathing(self.requirement_hint_label, color_key='success', bg_key='bg_card')

    def _hide_requirement_hint(self):
        """隐藏「点击查看需求示例->」提示标签"""
        if hasattr(self, 'requirement_hint_label') and self.requirement_hint_label.winfo_exists():
            self.requirement_hint_label.destroy()

    def _show_btn_add_hint(self):
        """显示「点此新增岗位→」提示标签（重建控件并启动呼吸动画）"""
        if hasattr(self, 'btn_add_hint') and self.btn_add_hint.winfo_exists():
            return  # 已显示，不重复创建
        self.btn_add_hint = ttk.Label(
            self._config_select_frame, text="点此新增岗位→", font=self.font_label,
            foreground=self.colors['success'], background=self.colors['bg_card'])
        self.btn_add_hint.pack(side="right", padx=int(4 * self.dpi_scale * self.zoom_factor))
        self._start_breathing(self.btn_add_hint, color_key='success', bg_key='bg_card')

    def _hide_btn_add_hint(self):
        """隐藏「点此新增岗位→」提示标签"""
        if hasattr(self, 'btn_add_hint') and self.btn_add_hint.winfo_exists():
            self.btn_add_hint.destroy()

    def _show_parse_hint(self):
        """显示「<-点击解析招聘需求」提示标签（重建控件并启动呼吸动画）"""
        if hasattr(self, 'parse_hint_label') and self.parse_hint_label.winfo_exists():
            return
        self.parse_hint_label = ttk.Label(
            self._parse_btn_frame, text="<-点击解析招聘需求", font=self.font_label,
            foreground=self.colors['success'], background=self.colors['bg_card'])
        self.parse_hint_label.pack(side="left", padx=(int(8 * self.dpi_scale * self.zoom_factor), 0))
        self._start_breathing(self.parse_hint_label, color_key='success', bg_key='bg_card')

    def _hide_parse_hint(self):
        """隐藏「<-点击解析招聘需求」提示标签"""
        if hasattr(self, 'parse_hint_label') and self.parse_hint_label.winfo_exists():
            self.parse_hint_label.destroy()

    def _show_save_hint(self):
        """显示「点击保存配置->」提示标签（重建控件并启动呼吸动画）"""
        if hasattr(self, 'save_hint_label') and self.save_hint_label.winfo_exists():
            return  # 已显示，不重复创建
        self.save_hint_label = ttk.Label(
            self._btn_inner, text="点击保存配置->", font=self.font_label,
            foreground=self.colors['success'], background=self.colors['bg_main'])
        self.save_hint_label.pack(side="left", before=self.btn_save,
                                  padx=int(5 * self.dpi_scale * self.zoom_factor))
        self.save_hint_label.bind("<Button-1>", lambda e: self.save_current_job())
        self._start_breathing(self.save_hint_label, color_key='success', bg_key='bg_main')

    def _hide_save_hint(self):
        """隐藏「点击保存配置->」提示标签"""
        if hasattr(self, 'save_hint_label') and self.save_hint_label.winfo_exists():
            self.save_hint_label.destroy()

    def _insert_requirement_template(self):
        """插入招聘需求模板到输入框（模板文本从 job_config.json 读取）"""
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            template = config.get("requirement_template", "")
        except Exception:
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
        ai_key = get_api_key(ai_provider, ai_base_url) if ai_provider and ai_base_url and ai_model else None
        if hasattr(self, "btn_parse_requirement"):
            self._parse_requirement_button_text = self.btn_parse_requirement.cget("text")
            self.btn_parse_requirement.config(state="disabled", text=" 解析中...")
        self._set_parse_result_text("正在解析：使用本地规则提取岗位要求…", self.colors['warning'])
        self._start_requirement_parse_progress(bool(ai_key))
        # 跟踪用户手动修改的字段，AI 增强时不覆盖
        self._dirty_fields = set()
        self._ai_enhance_pending = bool(ai_key)

        def _worker():
            try:
                # 阶段 1：regex 解析（快速）
                regex_result = self._build_regex_parse_result(requirement_text)
                self.root.after(0, lambda: self._apply_requirement_parse_result(regex_result))

                # 阶段 2：AI 增强（慢速，仅在有 key 时执行）
                if ai_key:
                    ai_result = self._build_ai_enhance_result(
                        requirement_text, regex_result["config"],
                        ai_provider, ai_base_url, ai_model, ai_key
                    )
                    self.root.after(0, lambda: self._apply_ai_enhance_result(ai_result))
                else:
                    # 无 AI key，恢复按钮
                    self.root.after(0, self._finish_parse_button)
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._handle_requirement_parse_error(e))

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
            ("OR", "满足任一项"),
            ("AND", "需要同时满足"),
        ]
        for old, new in replacements:
            text = re.sub(re.escape(old), new, text, flags=re.IGNORECASE)
        text = re.sub(r'\s+', ' ', text).strip()
        return text or "有一处解析结果需要人工确认"

    def _apply_requirement_parse_result(self, result):
        """在主线程中把解析结果填回界面。"""
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
            self.max_age_var.set(_optional_int_to_entry(job_config.get("max_age", 35)))
            self.edu_var.set(job_config.get("edu", "本科"))
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
            parsed_edu = job_config.get("edu", "本科")
            parsed_location = job_config.get("work_location", "")
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
                f"经验={parsed_min_exp}年，学历={parsed_edu}{loc_part}{salary_part}，"
                f"技能={skills_count}个{preferred_part}，必要条件={required_count}条，方式={ai_parse_status}"
            )

            if skills_count == 0:
                self._set_parse_result_text(f"⚠ 解析成功但无技术关键字：{summary_base}", self.colors['warning'])
                messagebox.showwarning(
                    "关键字缺失",
                    "解析成功，但未提取到任何技术关键字。\n\n"
                    "没有技术关键字无法精确筛选简历，筛选将仅依赖\n"
                    "经验和学历，匹配精度会大幅下降。\n\n"
                    "建议：\n"
                    "1. 完善招聘需求文档，详细列出技术栈要求\n"
                    "2. 在下方「技能关键词」区域手工添加关键字"
                )
            elif skills_count <= 5:
                self._set_parse_result_text(f"⚠ 关键字较少：{summary_base}", self.colors['warning'])
                messagebox.showwarning(
                    "关键字偏少",
                    f"仅提取到 {skills_count} 个技术关键字（建议 6 个以上）。\n\n"
                    "关键字偏少会导致评分区分度不足，\n"
                    "无法有效排序候选人。\n\n"
                    "建议：\n"
                    "1. 完善招聘需求文档，补充更多技术栈要求\n"
                    "2. 在下方「技能关键词」区域手工添加关键字"
                )
            else:
                if getattr(self, '_ai_enhance_pending', False):
                    self._set_parse_result_text(f"✓ 本地规则解析成功：{summary_base}", self.colors['success'])
                    self._start_ai_progress_animation()
                else:
                    self._set_parse_result_text(f"✓ 解析成功：{summary_base}", self.colors['success'])
                if ai_parse_warnings:
                    friendly_warnings = [
                        self._humanize_ai_parse_warning(w)
                        for w in ai_parse_warnings[:5]
                    ]
                    messagebox.showwarning(
                        "请确认解析结果",
                        "AI 已帮你补全解析结果。下面这些地方可能需要你看一眼：\n\n"
                        + "\n".join(f"- {w}" for w in friendly_warnings)
                        + "\n\n不影响继续使用；确认无误后保存岗位配置即可。"
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
            if not _ai_pending:
                self._finish_parse_button()

    def _apply_ai_enhance_result(self, result):
        """阶段 2：AI 增强完成后，增量更新界面。"""
        self._ai_enhance_pending = False
        self._stop_ai_progress_animation()
        self._stop_requirement_parse_progress()

        if not result.get("ai_success"):
            # AI 失败，只更新状态文字，保留 regex 结果
            status = result.get("ai_parse_status", "本地规则")
            self._set_parse_result_text(f"✓ 解析成功：{status}", self.colors['success'])
            self._finish_parse_button()
            return

        # AI 成功，用 AI 结果更新界面（不覆盖用户已修改的字段）
        try:
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
                self.edu_var.set(job_config.get("edu", "本科"))
            if 'min_exp' not in dirty:
                self.min_exp_var.set(str(job_config.get("min_exp", 0)))
            if 'max_age' not in dirty:
                self.max_age_var.set(_optional_int_to_entry(job_config.get("max_age", 35)))
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
                warnings_text = "\n".join(f"• {self._humanize_ai_parse_warning(w)}" for w in ai_parse_warnings[:5])
                messagebox.showinfo("AI 解析提醒", f"AI 增强解析完成，以下内容需要确认：\n\n{warnings_text}")

        except Exception:
            self._set_parse_result_text("✓ AI 增强解析完成（部分字段更新失败）", self.colors['warning'])
        finally:
            self._finish_parse_button()

    def _start_ai_progress_animation(self):
        """启动 AI 增强进度动画（状态栏文字循环闪烁）"""
        self._ai_anim_base = "\n⏳ AI 增强解析中"
        self._ai_anim_dots = 0
        self._ai_anim_running = True
        # 更新按钮文字为 AI 进度
        if hasattr(self, "btn_parse_requirement"):
            self.btn_parse_requirement.config(state="disabled", text=" AI 增强中…")
        self._tick_ai_animation()

    def _tick_ai_animation(self):
        """动画帧：循环显示 . / .. / ... / …"""
        if not getattr(self, '_ai_anim_running', False):
            return
        self._ai_anim_dots = (self._ai_anim_dots + 1) % 4
        dots = "." * self._ai_anim_dots if self._ai_anim_dots > 0 else "…"
        # 在 parse_result_label 上追加动画后缀
        current_text = self.parse_result_label.cget("text")
        # 移除旧的动画后缀（支持换行分隔）
        base = current_text.split("\n⏳ AI 增强")[0] if "\n⏳ AI 增强" in current_text else current_text
        self.parse_result_label.config(text=f"{base}{self._ai_anim_base}{dots}")
        self._ai_anim_after_id = self.root.after(500, self._tick_ai_animation)

    def _stop_ai_progress_animation(self):
        """停止 AI 增强进度动画"""
        self._ai_anim_running = False
        if hasattr(self, '_ai_anim_after_id'):
            self.root.after_cancel(self._ai_anim_after_id)
            self._ai_anim_after_id = None

    def _finish_parse_button(self):
        """恢复解析按钮状态"""
        if hasattr(self, "btn_parse_requirement"):
            self.btn_parse_requirement.config(
                state="normal",
                text=getattr(self, "_parse_requirement_button_text", " 解析招聘需求"),
            )

    def _handle_requirement_parse_error(self, exc):
        self._stop_ai_progress_animation()
        self._stop_requirement_parse_progress()
        if hasattr(self, "btn_parse_requirement"):
            self.btn_parse_requirement.config(
                state="normal",
                text=getattr(self, "_parse_requirement_button_text", " 解析招聘需求"),
            )
        messagebox.showerror("解析失败", f"这段招聘需求暂时没能解析出来。\n\n原因：{self._friendly_ai_parse_reason(str(exc))}\n\n可以稍后再试，或先手工填写岗位配置。")
        self._set_parse_result_text(f"解析失败：{self._friendly_ai_parse_reason(str(exc))}", self.colors['danger'])

    def _start_requirement_parse_progress(self, use_ai):
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
                lambda m=message: self._set_parse_result_text(m, self.colors['warning'])
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
                lbl.config(text=original, foreground=self.colors['text_muted'])

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
        self.reset_job_form()
        self.job_name_var.set("新岗位")
        self.config_job_combo.set("")  # 清空岗位选择
        self.requirement_template_btn.state(['!disabled'])
        self._show_requirement_hint()
        self._hide_btn_add_hint()  # 新建时隐藏"点此新增岗位→"提示
        self._update_job_step(0)  # 步骤1：填入需求

    def delete_job(self):
        """删除岗位"""
        job_name = self.config_job_combo.get()
        if job_name in self.job_rules:
            if messagebox.askyesno("确认", f"确定要删除岗位 '{job_name}' 吗？"):
                del self.job_rules[job_name]
                self.save_config()
                self.config_job_combo['values'] = list(self.job_rules.keys())
                self.config_job_combo.set('')
                self.reset_job_form()
                self._hide_job_step_bar()

    def save_current_job(self):
        """保存当前岗位配置"""
        self._hide_save_hint()
        job_name = self.job_name_var.get().strip()
        if not job_name:
            messagebox.showwarning("警告", "岗位名称不能为空")
            return

        # 规范化岗位名称：去除多余空格
        normalized_job_name = re.sub(r'\s+', ' ', job_name).strip()

        # 检查是否已存在相同（规范化后）的岗位
        existing_key_to_delete = None
        for key in self.job_rules.keys():
            normalized_key = re.sub(r'\s+', ' ', key).strip()
            if normalized_key.lower() == normalized_job_name.lower():
                existing_key_to_delete = key
                break

        if existing_key_to_delete and existing_key_to_delete != job_name:
            if messagebox.askyesno("岗位已存在", f"检测到重复岗位：'{existing_key_to_delete}'\n是否覆盖更新？"):
                del self.job_rules[existing_key_to_delete]
            else:
                return

        # 从 skills_data 构建带权重的 keywords / preferred_keywords 列表
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

        # 从 required_conditions_data 构建必要条件列表
        required_conditions = [
            self._strip_transient_fields(cond)
            for cond in self.required_conditions_data
        ]

        # 获取原始招聘需求（从需求文档解析框）
        original_requirement = self._get_requirement_text()

        # 验证薪资输入格式（非空则必须为数字）
        salary_min = None
        salary_max = None
        salary_min_str = self.salary_min_var.get().strip()
        salary_max_str = self.salary_max_var.get().strip()
        if salary_min_str:
            try:
                salary_min = int(salary_min_str)
            except ValueError:
                messagebox.showwarning("警告", "薪资范围最低值必须为数字（如：12）")
                return
        if salary_max_str:
            try:
                salary_max = int(salary_max_str)
            except ValueError:
                messagebox.showwarning("警告", "薪资范围最高值必须为数字（如：15）")
                return

        try:
            min_exp = int(self.min_exp_var.get())
            max_age = _parse_optional_int_entry(self.max_age_var.get(), "最大年龄")
        except ValueError as e:
            messagebox.showwarning("警告", str(e))
            return

        self.job_rules[normalized_job_name] = {
            "min_exp": min_exp,
            "edu": self.edu_var.get(),
            "max_age": max_age,
            "work_location": self.work_location_var.get().strip() or None,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "keywords": keywords,
            "preferred_keywords": preferred_keywords,
            "required_conditions": required_conditions,
            "original_requirement": original_requirement if original_requirement else None
        }

        self.save_config()
        self.config_job_combo['values'] = list(self.job_rules.keys())
        self.config_job_combo.set(normalized_job_name)
        # 步骤完成：先显示全绿，800ms 后隐藏引导条
        if self._job_step_active >= 0:
            _step_texts = ["① 填入需求", "② 解析需求", "③ 检查结果", "④ 保存配置"]
            for i, lbl in enumerate(self._job_step_labels):
                lbl.config(text=f"✓ {_step_texts[i][2:]}", foreground=self.colors['success'])
            self.root.after(800, self._hide_job_step_bar)
        else:
            self._hide_job_step_bar()
        self._show_btn_add_hint()
        messagebox.showinfo("成功", "岗位配置已保存")

    def reset_job_form(self):
        """重置表单"""
        self.job_name_var.set("")
        self.min_exp_var.set("3")
        self.max_age_var.set("35")
        self.edu_var.set("本科")
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
        self.parse_result_label.config(text="")
        self._hide_requirement_hint()
        self._hide_parse_hint()
        self._hide_save_hint()

    def load_config_dialog(self):
        """打开配置对话框"""
        filename = filedialog.askopenfilename(title="选择配置文件", filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if filename:
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
                messagebox.showinfo("成功", "配置已加载")
            except Exception as e:
                messagebox.showerror("错误", f"加载配置失败：{e}")

    def save_config_dialog(self):
        """保存配置对话框"""
        filename = filedialog.asksaveasfilename(title="保存配置文件", defaultextension=".json", filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if filename:
            try:
                config = {"jobs": self.job_rules}
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=4)
                messagebox.showinfo("成功", "配置已保存")
            except Exception as e:
                messagebox.showerror("错误", f"保存配置失败：{e}")

    def import_config(self):
        """导入配置"""
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
        """追加日志"""
        self.log_queue.put(message)

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

    def set_browser_ui(self, indicator_text=None, indicator_color=None, help_text=None, start_state=None):
        """线程安全更新浏览器状态控件，并缓存状态文本供后台线程判断。"""
        if indicator_text is not None:
            self._browser_status_text = indicator_text
        if help_text is not None:
            self._browser_status_help_text = help_text

        def apply_update():
            if indicator_text is not None:
                self.browser_status_indicator.config(text=indicator_text, foreground=indicator_color)
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
            self._selectors_auto_checked = True
            from bossmaster import check_selectors_health
            results = check_selectors_health(page)

            ok_count = sum(1 for r in results if r['status'] == 'ok')
            warn_count = sum(1 for r in results if r['status'] == 'warn')
            fail_count = sum(1 for r in results if r['status'] == 'fail')

            # 只在有异常时输出日志
            if warn_count + fail_count > 0:
                self.append_log(f"选择器自动检查：{ok_count} 正常 / {warn_count} 警告 / {fail_count} 失败")

                for r in results:
                    if r['status'] != 'ok':
                        icon = {'warn': '⚠️', 'fail': '❌'}.get(r['status'], '?')
                        self.append_log(f"  {icon} [{r['group']}] {r['name']}: {r['detail']}")

                self.append_log("⚠️ 选择器异常可能导致扫描功能不正常，可编辑 selectors.json 修复")
                # 主线程弹窗提醒（线程安全）
                self.run_on_ui(lambda: messagebox.showwarning(
                    "选择器异常",
                    f"选择器检查发现 {fail_count} 个失败、{warn_count} 个警告，"
                    f"可能导致扫描功能不正常。\n\n"
                    f"可编辑 selectors.json 修复，详见日志。"
                ))
        except Exception as e:
            self._selectors_auto_checked = False
            error_text = str(e).splitlines()[0] if str(e) else type(e).__name__
            if "连接已断开" in str(e) or "disconnected" in str(e).lower():
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
        self._browser_non_target_checks = getattr(self, '_browser_non_target_checks', 0) + 1
        return silent and self._browser_non_target_checks < 2

    def _should_defer_browser_connection_failure(self, silent: bool) -> bool:
        """自动轮询首次连接失败时暂缓报错，给页面连接一次自恢复机会。"""
        self._browser_connection_failures = getattr(self, '_browser_connection_failures', 0) + 1
        return silent and self._browser_connection_failures < 2

    def check_browser_connection(self, silent=False):
        """检测浏览器连接状态

        Args:
            silent: True 时只更新 UI 不写日志（用于自动轮询）
        """
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
                        # page.url 可能阻塞（Chrome 已关闭时 WebSocket 断开），加超时保护
                        page_url_result = [None]
                        page_url_exception = [None]
                        def _get_existing_url():
                            try:
                                page_url_result[0] = self.browser_page.url
                            except Exception as e:
                                page_url_exception[0] = e
                        url_t = threading.Thread(target=_get_existing_url, daemon=True)
                        url_t.start()
                        url_t.join(timeout=1)
                        if url_t.is_alive():
                            raise TimeoutError("browser_page.url 访问超时")
                        if page_url_exception[0] is not None:
                            raise page_url_exception[0]
                        current_url = page_url_result[0] or ''
                        self._browser_connection_failures = 0
                        if 'zhipin.com/web/chat/recommend' in current_url.lower():
                            self._browser_non_target_checks = 0
                            self.browser_connected = True
                            self.set_browser_ui("🟢 已连接", self.colors['success'], "已连接到 BOSS 直聘推荐牛人页面", "normal")
                            if prev_help != "已连接到 BOSS 直聘推荐牛人页面":
                                self.append_log("✅ 已连接到 BOSS 直聘推荐牛人页面")
                        elif 'zhipin.com' in current_url.lower() or 'boss' in current_url.lower():
                            if self._should_defer_browser_navigation_warning(silent):
                                return
                            self.browser_connected = False
                            self.set_browser_ui("🟡 需导航", self.colors['warning'], "浏览器已连接，请导航到 BOSS 直聘推荐牛人页面", "disabled")
                            if prev_help != "浏览器已连接，请导航到 BOSS 直聘推荐牛人页面":
                                self.append_log("⚠️ 浏览器已连接，请导航到 BOSS 直聘推荐牛人页面")
                        else:
                            if self._should_defer_browser_navigation_warning(silent):
                                return
                            self.browser_connected = False
                            self.set_browser_ui("🟡 需导航", self.colors['warning'], "浏览器已连接，请导航到 BOSS 直聘推荐牛人页面", "disabled")
                            if prev_help != "浏览器已连接，请导航到 BOSS 直聘推荐牛人页面":
                                self.append_log("⚠️ 浏览器已连接，请导航到 BOSS 直聘推荐牛人页面")
                        return
                    except Exception:
                        # 页面对象已失效，清理后走完整检测流程
                        self.browser_page = None
                        self.browser_connected = False
                        self._selectors_auto_checked = False  # 页面失效，下次连接重新检查选择器

                # 没有可用连接，检查 Chrome 调试端口
                # 优先读取上次持久化的端口号
                addr = getattr(self, 'browser_address', None)
                if not addr:
                    try:
                        saved_port = CHROME_DEBUG_PORT_FILE.read_text(encoding='utf-8').strip()
                        if saved_port.isdigit():
                            addr = f'127.0.0.1:{saved_port}'
                    except OSError:
                        pass
                if not addr:
                    addr = '127.0.0.1:9222'
                host, port = addr.rsplit(':', 1)
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)
                port_open = s.connect_ex((host, int(port))) == 0
                s.close()

                if not port_open:
                    prev_state = self._browser_status_text
                    self.browser_connected = False
                    self.set_browser_ui("🔴 未连接", self.colors['danger'], start_state="disabled")

                    # 自动启动 Chrome（仅在手动点击时）
                    if not silent:
                        self.set_browser_ui(help_text="正在启动 Chrome 浏览器...")
                        self.append_log("正在启动 Chrome 浏览器...")

                        # 找到 Chrome 可执行文件
                        if sys.platform == 'darwin':
                            candidates = [
                                '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                                os.path.expanduser('~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
                            ]
                        else:
                            candidates = [
                                os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
                                r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                                r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
                            ]
                        chrome_path = next((p for p in candidates if os.path.exists(p)), None)
                        if not chrome_path:
                            self.set_browser_ui(help_text="未找到 Chrome 浏览器，请安装后重试")
                            self.append_log("❌ 未找到 Chrome 浏览器")
                            return

                        # 自动选一个空闲端口，避免 9222 被占用
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.bind(('127.0.0.1', 0))
                        debug_port = s.getsockname()[1]
                        s.close()

                        # 清理 Chrome 锁文件，保留登录态（SingletonLock/Socket/Cookie
                        # 是上次异常退出残留的，删掉即可，不影响 cookies）
                        profile_dir = BASE_DIR / '.chrome_profile'
                        profile_dir.mkdir(parents=True, exist_ok=True)
                        for lock_file in ['SingletonLock', 'SingletonSocket', 'SingletonCookie']:
                            lock_path = profile_dir / lock_file
                            if lock_path.exists():
                                try:
                                    lock_path.unlink()
                                except Exception:
                                    pass

                        # 用 subprocess 直接启动 Chrome（不依赖 DrissionPage 的启动逻辑）
                        self.append_log(f"正在启动 Chrome（调试端口 {debug_port}）...")
                        subprocess.Popen(
                            [
                                chrome_path,
                                f'--remote-debugging-port={debug_port}',
                                f'--user-data-dir={profile_dir}',
                                '--no-first-run',
                                '--no-default-browser-check',
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )

                        # 持久化端口号，下次启动时可复用
                        try:
                            CHROME_DEBUG_PORT_FILE.write_text(str(debug_port), encoding='utf-8')
                        except OSError:
                            pass

                        # 轮询等待端口就绪
                        port_ready = False
                        for i in range(30):
                            time.sleep(1)
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.settimeout(0.5)
                            if s.connect_ex(('127.0.0.1', debug_port)) == 0:
                                s.close()
                                port_ready = True
                                break
                            s.close()
                            if i == 0:
                                self.append_log("⏳ 等待 Chrome 就绪...")
                            elif i % 5 == 4:
                                self.append_log(f"⏳ 等待 Chrome 就绪... ({i+1}/30)")

                        if not port_ready:
                            self.set_browser_ui("🔴 未连接", self.colors['danger'], "Chrome 启动超时，请关闭所有 Chrome 窗口后重试")
                            self.append_log("❌ Chrome 启动超时，调试端口未开启")
                            return

                        # 端口已开，用 DrissionPage 连接
                        time.sleep(2)
                        try:
                            from DrissionPage import ChromiumPage, ChromiumOptions
                            co = ChromiumOptions()
                            co.set_address(f'127.0.0.1:{debug_port}')

                            # 整个连接+导航放入线程超时保护，防止 Chrome 被杀后 DrissionPage 阻塞
                            startup_result = [None]
                            startup_exception = [None]

                            def _connect_and_navigate():
                                try:
                                    p = ChromiumPage(co)
                                    u = p.url
                                    if 'zhipin.com/web/chat/recommend' not in u.lower():
                                        p.get('https://www.zhipin.com/web/chat/recommend')
                                        time.sleep(2)
                                        u = p.url
                                    startup_result[0] = (p, u)
                                except Exception as e:
                                    startup_exception[0] = e

                            st = threading.Thread(target=_connect_and_navigate, daemon=True)
                            st.start()
                            st.join(timeout=6)
                            if st.is_alive():
                                raise TimeoutError("Chrome 连接超时")
                            if startup_exception[0] is not None:
                                raise startup_exception[0]

                            page, current_url = startup_result[0]
                            if 'zhipin.com/web/chat/recommend' in current_url.lower():
                                self.browser_connected = True
                                self.browser_page = page
                                self.browser_address = page.address
                                self.set_browser_ui("🟢 已连接", self.colors['success'], "已连接到 BOSS 直聘推荐牛人页面", "normal")
                                self.append_log("✅ 已连接到 BOSS 直聘推荐牛人页面")
                            else:
                                self.browser_connected = True
                                self.browser_page = page
                                self.browser_address = page.address
                                self.set_browser_ui("🟡 需导航", self.colors['warning'], "浏览器已连接，请导航到 BOSS 直聘推荐牛人页面", "disabled")
                                self.append_log("⚠️ 浏览器已连接，请导航到 BOSS 直聘推荐牛人页面")
                        except Exception as e:
                            self.browser_connected = False
                            self.browser_page = None
                            self._selectors_auto_checked = False
                            self.set_browser_ui("🔴 未连接", self.colors['danger'], "Chrome 已启动，但页面连接失败", "disabled")
                            error_text = str(e).splitlines()[0] if str(e) else type(e).__name__
                            self.append_log(f"❌ Chrome 已启动，但页面连接失败：{error_text}")
                        return
                    else:
                        self.set_browser_ui(help_text="未检测到 Chrome，请确保浏览器已启动")
                        if prev_state != "🔴 未连接":
                            self.append_log("❌ 未检测到 Chrome 调试端口")
                    return

                from DrissionPage import ChromiumPage, ChromiumOptions

                try:
                    # 将整个 ChromiumPage 构造 + page.url 放入线程超时保护
                    # ChromiumPage() 构造函数和 page.url 都可能在 Chrome 已死时阻塞
                    co = ChromiumOptions()
                    co.set_address(addr)

                    page_result = [None]
                    url_result = [None]
                    connect_exception = [None]

                    def _connect_and_get_url():
                        try:
                            p = ChromiumPage(co)
                            page_result[0] = p
                            url_result[0] = p.url
                        except Exception as e:
                            connect_exception[0] = e

                    conn_thread = threading.Thread(target=_connect_and_get_url, daemon=True)
                    conn_thread.start()
                    conn_thread.join(timeout=3)
                    if conn_thread.is_alive():
                        raise TimeoutError("ChromiumPage 连接超时")
                    if connect_exception[0] is not None:
                        raise connect_exception[0]

                    page = page_result[0]
                    current_url = url_result[0]
                    if not current_url:
                        current_url = ''
                    self._browser_connection_failures = 0

                    # Chrome 进程还在但窗口已关闭时，page.url 可能是 about:blank
                    # 直接在现有进程里导航到 BOSS 直聘，不杀进程不重启
                    target_url = 'https://www.zhipin.com/web/chat/recommend'
                    if current_url in ('about:blank', ''):
                        if not silent:
                            self.append_log("⚠️ Chrome 进程存在但无有效页面，正在激活并导航...")
                            nav_page = self._reactivate_and_navigate(page, target_url)
                            if nav_page is not None:
                                self.browser_connected = True
                                self.browser_page = nav_page
                                self.browser_address = page.address
                                try:
                                    nav_url = nav_page.url or ''
                                except Exception:
                                    nav_url = ''
                                if 'zhipin.com/web/chat/recommend' in nav_url.lower():
                                    self.set_browser_ui("🟢 已连接", self.colors['success'], "已连接到 BOSS 直聘推荐牛人页面", "normal")
                                    self.append_log("✅ 已连接到 BOSS 直聘推荐牛人页面")
                                else:
                                    self.set_browser_ui("🟡 需导航", self.colors['warning'], "已激活 Chrome，正在加载页面...", "disabled")
                                    self.append_log("⚠️ 已激活 Chrome，请等待页面加载完成")
                            else:
                                self.browser_connected = False
                                self.browser_page = None
                                self.set_browser_ui("🟡 需导航", self.colors['warning'], "请手动打开 Chrome 窗口", "disabled")
                                self.append_log("⚠️ 无法激活 Chrome 页面，请手动打开 Chrome 窗口后点击重试")
                        else:
                            # 自动轮询：不尝试导航，避免 page.get() 挂起
                            self.browser_connected = False
                            self.browser_page = None
                            prev_state = self._browser_status_text
                            self.set_browser_ui("🟡 需导航", self.colors['warning'], "Chrome 进程存在但无有效页面", "disabled")
                            if prev_state != "🟡 需导航":
                                self.append_log("⚠️ Chrome 进程存在但无有效页面，请点击按钮激活")
                        # 处理完毕，不再往下走 URL 检查
                        return

                    if 'zhipin.com/web/chat/recommend' in current_url.lower():
                        self._browser_non_target_checks = 0
                        prev_connected = self.browser_connected
                        self.browser_connected = True
                        self.browser_page = page
                        self.browser_address = page.address
                        self.set_browser_ui("🟢 已连接", self.colors['success'], "已连接到 BOSS 直聘推荐牛人页面", "normal")
                        if not silent or not prev_connected:
                            self.append_log("✅ 已连接到 BOSS 直聘推荐牛人页面")
                    elif 'zhipin.com' in current_url.lower() or 'boss' in current_url.lower():
                        if self._should_defer_browser_navigation_warning(silent):
                            return
                        prev_state = self._browser_status_text
                        self.browser_connected = False
                        self.browser_page = page
                        self.browser_address = page.address
                        self.set_browser_ui("🟡 需导航", self.colors['warning'], "浏览器已连接，请导航到 BOSS 直聘推荐牛人页面", "disabled")
                        if not silent or prev_state != "🟡 需导航":
                            self.append_log("⚠️ 浏览器已连接，请导航到 BOSS 直聘推荐牛人页面")
                    else:
                        if self._should_defer_browser_navigation_warning(silent):
                            return
                        prev_state = self._browser_status_text
                        self.browser_connected = False
                        self.browser_page = page
                        self.browser_address = page.address
                        self.set_browser_ui("🟡 需导航", self.colors['warning'], "浏览器已连接，请导航到 BOSS 直聘推荐牛人页面", "disabled")
                        if not silent or prev_state != "🟡 需导航":
                            self.append_log("⚠️ 浏览器已连接，请导航到 BOSS 直聘推荐牛人页面")

                except Exception as e:
                    if self._should_defer_browser_connection_failure(silent):
                        self.browser_connected = False
                        self.browser_page = None
                        self._selectors_auto_checked = False
                        self.set_browser_ui(
                            "🟡 重连中",
                            self.colors['warning'],
                            "页面连接短暂中断，正在自动重连...",
                            "disabled",
                        )
                        return

                    prev_state = self._browser_status_text
                    self.browser_connected = False
                    self.browser_page = None  # 清理失效的 page 对象
                    self._selectors_auto_checked = False
                    self.set_browser_ui("🔴 未连接", self.colors['danger'], "浏览器页面连接失败", "disabled")
                    if not silent or prev_state != "🔴 未连接":
                        error_text = str(e).splitlines()[0] if str(e) else type(e).__name__
                        self.append_log(f"❌ 浏览器页面连接失败：{error_text}")

                    # 手动点击时，尝试杀掉彻底挂掉的调试 Chrome 进程并重启
                    if not silent:
                        self.append_log("⚠️ 正在尝试清理残留的调试 Chrome 进程...")
                        killed = False
                        try:
                            port_num = int(port)
                            if sys.platform == 'darwin' or sys.platform.startswith('linux'):
                                # 找到包含 remote-debugging-port=PORT 的 Chrome 进程 PID
                                result = subprocess.run(
                                    ['pgrep', '-f', f'remote-debugging-port={port_num}'],
                                    capture_output=True, text=True, timeout=3
                                )
                                pids = result.stdout.strip().split('\n')
                                for pid in pids:
                                    if pid.isdigit():
                                        try:
                                            os.kill(int(pid), 15)  # SIGTERM
                                            killed = True
                                        except ProcessLookupError:
                                            pass
                            elif sys.platform == 'win32':
                                # Windows: 用 wmic 找到包含调试端口的 Chrome 进程
                                result = subprocess.run(
                                    ['wmic', 'process', 'where',
                                     f"CommandLine like '%remote-debugging-port={port_num}%'",
                                     'get', 'ProcessId'],
                                    capture_output=True, text=True, timeout=5
                                )
                                for line in result.stdout.strip().split('\n'):
                                    pid = line.strip()
                                    if pid.isdigit():
                                        subprocess.run(['taskkill', '/PID', pid],
                                                     timeout=2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                                        killed = True
                        except Exception as kill_err:
                            self.append_log(f"清理残留进程失败：{kill_err}")

                        if killed:
                            time.sleep(1)
                            self.append_log("✅ 已清理残留的调试 Chrome 进程，2秒后自动重新启动...")
                            self._pending_chrome_restart = True
                        else:
                            self.append_log("⚠️ Chrome 调试端口被占用但无法清理，请手动关闭所有 Chrome 窗口后重试")
                            self.set_browser_ui("🔴 未连接", self.colors['danger'],
                                              "请关闭所有 Chrome 窗口后点击重试", "disabled")

            except ImportError:
                self.set_browser_ui("🔴 错误", self.colors['danger'], "未安装 DrissionPage，请运行：pip install DrissionPage")
                self.append_log("❌ DrissionPage 未安装")
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

    def update_progress(self):
        """更新进度条显示"""
        try:
            # 处理进度队列
            while True:
                progress_data = self.progress_queue.get_nowait()
                current = progress_data.get('current', 0)
                total = progress_data.get('total', 100)
                percentage = min(100, int((current / total) * 100)) if total > 0 else 0
                self.progress_var.set(percentage)
                desc = progress_data.get('desc', '')

                # 检测终态前缀，应用自绘图标
                icon = None
                if desc.startswith('[完成]'):
                    icon = self._icon_status_ok
                    desc = desc[len('[完成]'):].lstrip()
                elif desc.startswith('[已停止]') or desc.startswith('[出错]'):
                    icon = self._icon_status_fail
                    desc = desc[desc.index(']') + 1:].lstrip()

                if icon:
                    self.progress_label.config(
                        image=icon, compound='left',
                        text=f"{percentage}%  {desc}"
                    )
                else:
                    self.progress_label.config(
                        image='', compound='text',
                        text=f"{percentage}%  {desc}"
                    )
        except queue.Empty:
            pass

        # 处理岗位切换确认队列
        try:
            confirm_data = self.confirm_queue.get_nowait()
            event = confirm_data['event']
            current_idx = confirm_data['current_idx']
            total = confirm_data['total']
            next_job_name = confirm_data['next_job_name']

            result = messagebox.askokcancel(
                "岗位切换确认",
                f"请手动切换到下一个岗位的推荐页面\n\n"
                f"进度：{current_idx}/{total}\n"
                f"下一个岗位：{next_job_name}\n\n"
                f"请在 BOSS 直聘页面手动切换到该岗位的推荐页面后，\n"
                f"点击「确定」继续，或点击「取消」停止扫描。"
            )
            event.result = result
            event.set()
        except queue.Empty:
            pass

        self.root.after(200, self.update_progress)

    def _bind_run_canvas_width(self, canvas_frame):
        """绑定 run_canvas 内部窗口宽度，使其跟随 canvas 宽度"""
        window_id = getattr(self, '_run_canvas_window_id', None)
        if window_id is None:
            return
        def on_resize(event):
            self.run_canvas.itemconfig(window_id, width=event.width)
        canvas_frame.bind("<Configure>", on_resize)

    @staticmethod
    def _parse_salary_exp(summary, structured=None):
        """从候选人摘要中轻量解析薪资和工作年限。

        Args:
            summary: 候选人摘要文本
            structured: 结构化字段字典（优先使用）

        Returns:
            (salary: str, exp: str) — 如 ("15-20K", "5年")
        """
        salary = ''
        exp = ''

        # 优先使用 API 结构化字段
        if structured:
            if structured.get('salary_min') is not None:
                s_min = structured['salary_min']
                s_max = structured.get('salary_max')
                if s_max and s_max != s_min:
                    salary = f"{s_min}-{s_max}K"
                else:
                    salary = f"{s_min}K"
            if structured.get('exp_years') is not None:
                exp = f"{structured['exp_years']}年"

        # 未解析到的字段用文本解析兜底
        if not salary or not exp:
            from filtering import _parse_candidate_salary_range, parse_experience_years
            if not salary:
                salary_min, salary_max = _parse_candidate_salary_range(summary or '')
                if salary_min is not None:
                    if salary_max is not None and salary_max != salary_min:
                        salary = f"{salary_min}-{salary_max}K"
                    else:
                        salary = f"{salary_min}K"
                elif '面议' in (summary or ''):
                    salary = '面议'
            if not exp:
                exp_raw = parse_experience_years(summary or '')
                exp = f"{exp_raw}年" if exp_raw is not None else ''

        return salary, exp

    @staticmethod
    def _center_window_on_screen(window, width, height):
        """将子窗口相对于屏幕居中（不依赖父窗口位置）"""
        _place_window_centered(window, width, height)

    def start_run(self):
        """开始运行"""
        if self.is_running:
            return

        # 立即禁用按钮，防止重复点击
        self.start_btn.config(state="disabled")

        if not self.browser_connected:
            self.start_btn.config(state="normal")
            messagebox.showwarning("未连接", "请先连接到 BOSS 直聘推荐页面后再运行")
            return

        if self.browser_page is not None:
            try:
                current_url = self.browser_page.url
                if 'zhipin.com/web/chat/recommend' not in current_url.lower():
                    self.start_btn.config(state="normal")
                    messagebox.showwarning("页面错误", "请将浏览器导航到 BOSS 直聘推荐页面后再运行")
                    return
            except Exception:
                self.start_btn.config(state="normal")
                messagebox.showwarning("连接丢失", "浏览器连接已丢失，请重新检测/连接")
                return
        else:
            self.start_btn.config(state="normal")
            messagebox.showwarning("未连接", "请先检测/连接浏览器")
            return

        self.is_running = True
        self.stop_event.clear()
        self.status_label.config(text="🟡 运行中...", foreground=self.colors['warning'])
        self.stop_btn.config(state="normal")

        # 重置进度显示
        self.progress_var.set(0)
        self.progress_label.config(text="0%", image='', compound='text')

        self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] ▶ 开始运行...")

        self.worker_thread = threading.Thread(target=self.run_worker)
        self.worker_thread.daemon = True
        self.worker_thread.start()

    def stop_run(self):
        """停止运行"""
        self.is_running = False
        self.stop_event.set()
        self.status_label.config(text="🔴 已停止", foreground=self.colors['danger'])
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] ⏹ 已停止")

    def run_worker(self):
        """运行工作线程"""
        import sys
        from datetime import datetime

        old_stdout = sys.stdout
        try:
            class LogRedirector:
                def __init__(self, callback):
                    self.callback = callback
                    self.buffer = ""

                def write(self, text):
                    self.buffer += text
                    while '\n' in self.buffer:
                        line, self.buffer = self.buffer.split('\n', 1)
                        if line.strip():
                            self.callback(f"[{datetime.now().strftime('%H:%M:%S')}] {line}")

                def flush(self):
                    if self.buffer.strip():
                        self.callback(f"[{datetime.now().strftime('%H:%M:%S')}] {self.buffer}")
                    self.buffer = ""

            log_redirector = LogRedirector(self.append_log)
            sys.stdout = log_redirector

            rounds = int(self.rounds_var.get())
            effective_max_candidates = API_CANDIDATE_LIMIT_DEFAULT
            # 将中文打招呼等级映射为程序参数
            greet_level_text = self.greet_level_var.get()
            no_greet = greet_level_text == "不打招呼（仅筛选）"
            greet_level = "strong" if greet_level_text == "仅强烈推荐" else "normal"

            from bossmaster import load_job_config, ChromiumPage, time, run_smart_scan
            import argparse

            self.append_log(f">>> BOSS 直聘候选人智能提取工具 v{__version__} [图形界面模式]")
            self.append_log(f"滚动轮次：{rounds}, 自动打招呼：{greet_level_text}")
            self.append_log("提取链路：listener + refresh 优先捕获结构化数据；DOM 扫描确认可点击候选人；必要时 API 最后补全已出现候选人")

            # 获取选择的岗位
            selected_job = self.job_select_var.get()
            job_arg = None if selected_job == "全部岗位" else selected_job

            # 构造命令行参数
            ai_eval_enabled = self.ai_eval_var.get()
            ai_api_config = None
            ai_api_key = None
            if ai_eval_enabled:
                try:
                    # 同步运行页超时设置到 api_config
                    _timeout_changed = False
                    if hasattr(self, 'llm_read_timeout_var'):
                        _new_rt = self.llm_read_timeout_var.get()
                        if self.api_config.get("llm_read_timeout") != _new_rt:
                            self.api_config["llm_read_timeout"] = _new_rt
                            _timeout_changed = True
                    # 超时值变更时持久化到 api_config.json
                    if _timeout_changed:
                        try:
                            with open(API_CONFIG_PATH, 'w', encoding='utf-8') as _f:
                                json.dump(self._sanitize_config_for_save(self.api_config), _f, ensure_ascii=False, indent=4)
                        except Exception:
                            pass
                    ai_api_config = self.api_config
                    from security import get_api_key
                    ai_api_key = get_api_key(self.api_config.get('api_provider', ''), self.api_config.get('base_url', ''))
                    if not ai_api_key:
                        self.append_log("AI 评估需要 API Key，但未配置，将跳过")
                        ai_eval_enabled = False
                    else:
                        model_name = self.api_config.get('model', 'unknown')
                        self.append_log(f"AI 辅助评估已启用（模型：{model_name}）")
                except Exception as e:
                    self.append_log(f"加载 API 配置失败：{e}，跳过 AI 评估")
                    ai_eval_enabled = False

            args = argparse.Namespace(
                clear=False,
                job=job_arg,
                greet=not no_greet,
                re_greet=False,
                greet_level=greet_level,
                greet_names=None,
                list_candidates=False,
                rounds=rounds,
                max_candidates=effective_max_candidates,
                dom_only=False,
                listener_first=False,
                verbose=False,
                ai_eval=ai_eval_enabled,
                api_config=ai_api_config,
                api_key=ai_api_key,
            )

            if job_arg:
                self.append_log(f"[初次扫描模式] 指定岗位：{job_arg}")
            else:
                self.append_log("[初次扫描模式] 处理全部岗位")
            self.append_log("开始扫描候选人...")

            # 进度回调 — 将 bossmaster 的进度报告送入队列
            def on_progress(percentage, description):
                self.progress_queue.put({
                    'current': percentage,
                    'total': 100,
                    'desc': description,
                })

            def confirm_callback(current_idx, total, next_job_name):
                """岗位切换确认 — 阻塞工作线程直到用户在 GUI 中确认"""
                event = threading.Event()
                event.result = False
                self.confirm_queue.put({
                    'event': event,
                    'current_idx': current_idx,
                    'total': total,
                    'next_job_name': next_job_name,
                })
                # 轮询等待，支持 stop_event 中断
                while not event.is_set():
                    if self.stop_event.is_set():
                        event.result = False
                        break
                    event.wait(timeout=0.5)
                return event.result

            def captcha_callback(detail):
                """验证码弹窗通知 — 阻塞工作线程直到用户在 GUI 中响应

                返回:
                    True: 用户选择继续等待验证完成
                    False: 用户选择跳过验证等待（中止当前操作）
                """
                result = [False]
                done = threading.Event()

                def show_dialog():
                    answer = messagebox.askyesno(
                        "检测到安全验证弹窗",
                        f"程序检测到安全验证弹窗\n（{detail}）\n\n"
                        "请在浏览器中手动完成验证。\n\n"
                        "点击「是」继续等待验证完成\n"
                        "点击「否」跳过验证等待，停止当前操作",
                        parent=self.root,
                    )
                    result[0] = answer
                    done.set()

                self.root.after(0, show_dialog)
                # 轮询等待，支持 stop_event 中断
                while not done.is_set():
                    if self.stop_event.is_set():
                        result[0] = False
                        done.set()
                        break
                    done.wait(timeout=0.5)
                return result[0]

            def notice_callback(title, message):
                self.root.after(0, lambda: messagebox.showinfo(title, message, parent=self.root))

            def blocking_notice_callback(title, message):
                """阻塞式通知弹窗 — 等待用户点击确定后返回"""
                done = threading.Event()

                def show_dialog():
                    messagebox.showinfo(title, message, parent=self.root)
                    done.set()

                self.root.after(0, show_dialog)
                while not done.is_set():
                    if self.stop_event.is_set():
                        done.set()
                        break
                    done.wait(timeout=0.5)

            # 调用 run_smart_scan 并传入参数和进度回调
            run_smart_scan(args, progress_callback=on_progress, confirm_callback=confirm_callback,
                           stop_event=self.stop_event, existing_page=self.browser_page,
                           captcha_callback=captcha_callback, notice_callback=notice_callback,
                           blocking_notice_callback=blocking_notice_callback)

        except KeyboardInterrupt:
            self.append_log("用户取消岗位切换，已停止")
        except Exception as e:
            self.append_log(f"运行出错：{e}")
            import traceback
            self.append_log(traceback.format_exc())
        finally:
            sys.stdout = old_stdout
            self.is_running = False
            self.append_log(f"[{datetime.now().strftime('%H:%M:%S')}] ✔ 运行完成")

            def finish_ui():
                self.status_label.config(text="🟢 就绪", foreground=self.colors['success'])
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                self.progress_var.set(0)
                self.progress_label.config(text="就绪", image='', compound='text')
                self.root.after(100, self.refresh_results)

            self.run_on_ui(finish_ui)

    def on_closing(self):
        """窗口关闭处理 - 安全等待工作线程结束"""
        if self.is_running:
            if messagebox.askokcancel("退出", "程序正在运行，确定要强行退出吗？\n未保存的进度可能会丢失。"):
                self.is_running = False
                # 等待工作线程结束（最多 5 秒）
                if hasattr(self, 'worker_thread') and self.worker_thread and self.worker_thread.is_alive():
                    self.worker_thread.join(timeout=5)
                self.root.destroy()
        else:
            self.root.destroy()

    def on_run_job_selected(self, event=None):
        """运行页选择岗位后，提醒切换到 BOSS 对应发布职位"""
        selected = self.job_select_var.get()
        if selected and selected != "全部岗位":
            messagebox.showinfo(
                "提示",
                f"请在 BOSS 直聘「推荐牛人」页面，切换到「{selected}」职位后再开始运行。",
                parent=self.root,
            )

    def refresh_results(self, force=False):
        """刷新结果 - 增强版：支持表头排序、颜色标记和岗位+日期过滤"""
        # 如果结果页面尚未创建，直接返回
        if not hasattr(self, 'result_tree') or self.result_tree is None:
            return

        # 数据未变 + 过滤条件未变 → 跳过 Treeview 重建，避免页面切换卡顿
        current_job = self.result_job_var.get() if hasattr(self, 'result_job_var') else ""
        current_dates = self._get_result_date_filter() if hasattr(self, 'result_date_start_entry') else (None, None)
        show_blacklist = self.result_show_blacklist_var.get() if hasattr(self, 'result_show_blacklist_var') else False
        if CANDIDATES_PATH.exists():
            stat = CANDIDATES_PATH.stat()
            fingerprint = (stat.st_mtime, stat.st_size)
            if (
                not force
                and fingerprint == self._result_tree_fingerprint
                and current_job == self._result_last_job
                and current_dates == self._result_last_dates
                and show_blacklist == self._result_last_show_blacklist
            ):
                return
            self._result_tree_fingerprint = fingerprint
            self._result_last_job = current_job
            self._result_last_dates = current_dates
            self._result_last_show_blacklist = show_blacklist
        elif self._result_tree_fingerprint is not None:
            self._result_tree_fingerprint = None

        try:
            if CANDIDATES_PATH.exists():
                with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                    candidates = json.load(f)
                if not show_blacklist:
                    candidates = [c for c in candidates if not c.get('blacklisted')]

                # 岗位过滤
                selected_job = self.result_job_var.get()
                if selected_job != "全部岗位":
                    candidates = [c for c in candidates if c.get('job_name', '') == selected_job.replace(" ", "")]

                # 日期过滤（基于 batch_timestamp 前 8 位 YYYYMMDD）
                date_start, date_end = current_dates
                if date_start or date_end:
                    def _in_date_range(c):
                        ts = c.get('batch_timestamp', '')
                        if not ts or len(ts) < 8:
                            return False
                        d = ts[:8]
                        if date_start and d < date_start:
                            return False
                        if date_end and d > date_end:
                            return False
                        return True
                    candidates = [c for c in candidates if _in_date_range(c)]

                # 计算新的指标
                total = len(candidates)

                # 强烈推荐：匹配分>=SCORE_THRESHOLD_STRONG
                strong_list = [c for c in candidates if c.get('match_score', 0) >= SCORE_THRESHOLD_STRONG]
                strong_total = len(strong_list)
                strong_greeted = sum(1 for c in strong_list if c.get('greet_sent', False))

                # 推荐：匹配分>=SCORE_THRESHOLD_RECOMMEND 且<SCORE_THRESHOLD_STRONG
                recommended_list = [c for c in candidates if SCORE_THRESHOLD_RECOMMEND <= c.get('match_score', 0) < SCORE_THRESHOLD_STRONG]
                recommended_total = len(recommended_list)
                recommended_greeted = sum(1 for c in recommended_list if c.get('greet_sent', False))

                # 待定：匹配分>=SCORE_THRESHOLD_PASS 且<SCORE_THRESHOLD_RECOMMEND
                pending_list = [c for c in candidates if SCORE_THRESHOLD_PASS <= c.get('match_score', 0) < SCORE_THRESHOLD_RECOMMEND]
                pending_total = len(pending_list)
                pending_greeted = sum(1 for c in pending_list if c.get('greet_sent', False))

                # 已打招呼：全部通过筛选候选人中已完成沟通的人
                greeted_total = sum(
                    1 for c in candidates
                    if c.get('match_score', 0) >= SCORE_THRESHOLD_PASS
                    and c.get('greet_sent', False)
                )

                # 更新统计卡片
                self.result_stats_vars['strong'].set(str(strong_total))
                self.result_stats_vars['recommended'].set(str(recommended_total))
                self.result_stats_vars['pending'].set(str(pending_total))
                self.result_stats_vars['greeted'].set(str(greeted_total))
                # 更新已打招呼数
                self.result_stats_greeted['strong'].set(f"{strong_greeted} 已打招呼")
                self.result_stats_greeted['recommended'].set(f"{recommended_greeted} 已打招呼")
                self.result_stats_greeted['pending'].set(f"{pending_greeted} 已打招呼")
                self.result_stats_greeted['greeted'].set("通过筛选中")

                for item in self.result_tree.get_children():
                    self.result_tree.delete(item)
                self._item_to_candidate: dict[str, dict] = {}

                sorted_candidates = sorted(candidates, key=lambda x: x.get('match_score', 0), reverse=True)

                # 配置颜色标记 tag
                self.result_tree.tag_configure('strong_recommend', background=self.colors['bg_tree_tag_high'])
                self.result_tree.tag_configure('recommend', background=self.colors['bg_tree_tag_mid'])
                self.result_tree.tag_configure('pending', background=self.colors['bg_tree_tag_low'])
                self.result_tree.tag_configure('blacklisted', background='#F5F5F5', foreground='#C62828')

                visible_count = 0
                for c in sorted_candidates:
                    score = c.get('match_score', 0)
                    geek_id = str(c.get('geek_id', ''))

                    # 评估中或已完成 AI 评估的候选人，即使分数低于55分也显示
                    is_evaluating = geek_id in self._ai_evaluating_ids
                    is_just_evaluated = geek_id in self._ai_eval_results
                    is_ai_evaluated = bool(c.get('llm_evaluated'))
                    should_keep_low_score = is_evaluating or is_just_evaluated or is_ai_evaluated

                    if score < SCORE_THRESHOLD_PASS and not should_keep_low_score:
                        continue  # 低于通过分且没有 AI 评估上下文，不显示
                    if visible_count >= 100 and score >= SCORE_THRESHOLD_PASS:
                        continue  # 常规结果仍保留前100条，低分 AI 评估结果不被挤掉

                    level = (
                        "强烈推荐" if score >= SCORE_THRESHOLD_STRONG
                        else ("推荐" if score >= SCORE_THRESHOLD_RECOMMEND
                              else ("待定" if score >= SCORE_THRESHOLD_PASS else "未通过"))
                    )
                    status = self._format_candidate_status(c)

                    # 根据推荐等级设置颜色标记
                    if c.get('blacklisted'):
                        tag = 'blacklisted'
                    elif score >= SCORE_THRESHOLD_STRONG:
                        tag = 'strong_recommend'
                    elif score >= SCORE_THRESHOLD_RECOMMEND:
                        tag = 'recommend'
                    else:
                        tag = 'pending'

                    # 从 summary 中解析工作年限和薪资
                    salary, exp = self._parse_salary_exp(c.get('summary', ''), c.get('structured'))

                    # AI 评估调整值：有简历时显示简历评估（替代一次评估），否则显示一次评估
                    ai_adj = c.get('llm_adjustment')
                    resume_adj = c.get('resume_eval_adjustment')

                    if resume_adj is not None:
                        ai_text = f"+{resume_adj}" if resume_adj > 0 else str(resume_adj)
                    elif ai_adj is not None and c.get('llm_evaluated'):
                        ai_text = f"+{ai_adj}" if ai_adj > 0 else str(ai_adj)
                    else:
                        ai_text = "—"

                    edu, age, job_status, school, company = self._extract_extra_fields(c)
                    c['_extra_fields'] = (edu, age, job_status, school, company)
                    item_id = self.result_tree.insert("", "end", values=(
                        c.get('name', ''),
                        exp,
                        salary,
                        c.get('skill_match_ratio', ''),
                        score,
                        ai_text,
                        level,
                        status,
                        edu,
                        age,
                        job_status,
                        school,
                        company,
                    ), tags=(tag,))
                    self._item_to_candidate[item_id] = c
                    visible_count += 1

                # 存储原始数据用于排序和详情展示
                self.result_tree_data = sorted_candidates[:100]
                self.all_candidates = candidates  # 存储全部数据用于详情展示
                self._tree_original_order = None  # 搜索排序缓存失效，下次搜索时重建
        except Exception as e:
            self.append_log(f"加载结果失败：{e}")

        # 绑定表头排序（只绑定一次）
        if not hasattr(self, '_sort_bound'):
            self._bind_treeview_sorting()
            self._bind_treeview_context_menu()
            self._sort_bound = True

    def _bind_treeview_sorting(self):
        """绑定 Treeview 表头排序功能"""
        # 设置中文表头显示
        column_headers = {
            "name": "姓名",
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
        columns = self.result_tree['columns']
        for col in columns:
            # 为每个表头添加点击事件，使用中文显示
            header_text = column_headers.get(col, col)
            self.result_tree.heading(col, text=header_text, command=lambda c=col: self._sort_treeview(c))

    def _sort_treeview(self, col):
        """按指定列排序 Treeview"""
        try:
            # 获取当前数据
            items = [(self.result_tree.set(item, col), item) for item in self.result_tree.get_children()]

            # 尝试数值排序
            def sort_key(val):
                try:
                    # 移除单位（如"年"、"K"）
                    val_clean = val.replace('年', '').replace('K', '').replace('千', '')
                    if '-' in val_clean:
                        # 范围值取平均
                        parts = val_clean.split('-')
                        return (float(parts[0]) + float(parts[1])) / 2
                    return float(val_clean)
                except (ValueError, TypeError):
                    return 0 if val == '' else 999999

            items.sort(key=sort_key)

            # 移动项
            for index, (val, item) in enumerate(items):
                self.result_tree.move(item, '', index)

            # 切换排序方向（可选优化：下次点击反向排序）
            if not hasattr(self, '_sort_reverse'):
                self._sort_reverse = False
            self._sort_reverse = not self._sort_reverse

        except Exception as e:
            pass

    def _filter_result_tree(self):
        """根据搜索框内容实时过滤 Treeview 行。

        搜索范围：姓名、匹配分、推荐指数、状态。
        匹配项红色文字高亮并按优先级排序（完全匹配姓名 > 部分匹配 > 分数 ≥ 搜索值 > 等级 > 状态），
        清空搜索时恢复原始排序。
        """
        query = self.result_search_var.get().strip().lower()
        all_items = self.result_tree.get_children()

        # 保存原始顺序（首次过滤时）
        if not hasattr(self, '_tree_original_order') or self._tree_original_order is None:
            self._tree_original_order = list(all_items)

        # 构建 item_id → candidate 映射（插入时建立，不受排序影响）
        item_map = getattr(self, '_item_to_candidate', {}) or {}

        if not query:
            # 清空搜索：恢复原始排序，清除高亮
            for item_id in all_items:
                tags = list(self.result_tree.item(item_id, 'tags') or ())
                if 'search_match' in tags:
                    tags.remove('search_match')
                    self.result_tree.item(item_id, tags=tuple(tags))
            for i, item_id in enumerate(self._tree_original_order):
                if self.result_tree.exists(item_id):
                    self.result_tree.move(item_id, '', i)
            self._tree_original_order = None
            return

        # 匹配判断：返回匹配类型用于优先级排序
        def _match_type(cand: dict) -> str | None:
            if not cand:
                return None
            name = str(cand.get('name', '')).lower()
            score_str = str(cand.get('match_score', '')).lower()
            level = str(cand.get('recommend_level', '')).lower()
            status = str(cand.get('followup_status', '')).lower()
            if query == name:
                return 'exact_name'
            if query in name:
                return 'partial_name'
            if query in level:
                return 'level'
            if query in status:
                return 'status'
            # 数字查询：匹配分数 ≥ 搜索值
            try:
                q_num = int(query)
                s_num = int(score_str) if score_str else 0
                if s_num >= q_num:
                    return 'score'
            except (ValueError, TypeError):
                pass
            return None

        _priority = {'exact_name': 0, 'partial_name': 1, 'score': 2, 'level': 3, 'status': 4}
        matched_with_type: list[tuple[str, str]] = []
        unmatched: list[str] = []
        for item_id in all_items:
            mt = _match_type(item_map.get(item_id, {}))
            if mt:
                matched_with_type.append((item_id, mt))
            else:
                unmatched.append(item_id)

        # 匹配项按优先级排序：完全匹配姓名 > 部分匹配姓名 > 分数 > 等级 > 状态
        matched_with_type.sort(key=lambda x: _priority.get(x[1], 99))

        # 清除旧高亮 tag
        for item_id in all_items:
            tags = list(self.result_tree.item(item_id, 'tags') or ())
            if 'search_match' in tags:
                tags.remove('search_match')
                self.result_tree.item(item_id, tags=tuple(tags))

        # 匹配项：深青加粗高亮 tag（bold 继承 self.font_table 基础字号，避免行高不一致）
        self.result_tree.tag_configure('search_match', foreground='#00695C', font=(*self.font_table, 'bold'))
        for item_id, _ in matched_with_type:
            tags = list(self.result_tree.item(item_id, 'tags') or ())
            if 'search_match' not in tags:
                tags.append('search_match')
            self.result_tree.item(item_id, tags=tuple(tags))

        # detach 全部 → 按优先级 reattach
        for item_id in all_items:
            self.result_tree.detach(item_id)
        for item_id, _ in matched_with_type:
            self.result_tree.reattach(item_id, '', 'end')
        for item_id in unmatched:
            self.result_tree.reattach(item_id, '', 'end')

        # 滚动到第一个匹配项
        if matched_with_type:
            self.result_tree.see(matched_with_type[0][0])
            self.result_tree.selection_set(matched_with_type[0][0])

    def _bind_treeview_context_menu(self):
        """绑定 Treeview 右键菜单和双击"""
        self.result_tree.bind('<Button-3>', self._show_context_menu)
        self.result_tree.bind('<Double-Button-1>', self._on_tree_double_click)
        # 状态列 tooltip（截断时显示完整状态）
        self._tooltip = None
        self._tooltip_after_id = None
        self._tooltip_item = None
        self.result_tree.bind('<Motion>', self._on_tree_motion)
        self.result_tree.bind('<Leave>', self._hide_tooltip)

    def _on_tree_motion(self, event):
        """Treeview 鼠标移动：长状态、学校和公司显示完整 tooltip。"""
        item = self.result_tree.identify_row(event.y)
        column_id = self.result_tree.identify_column(event.x)
        if not item or not column_id:
            self._hide_tooltip()
            return

        try:
            display_columns = tuple(self.result_tree.cget("displaycolumns"))
            column_index = int(column_id[1:]) - 1
            column_name = display_columns[column_index]
        except (IndexError, TypeError, ValueError):
            self._hide_tooltip()
            return

        cand = self._item_to_candidate.get(item)
        full = ''
        if cand and column_name == 'status':
            full = cand.get('_full_status', '')
            show_tooltip = full.count('｜') >= 2
        elif cand and column_name in ('school', 'company'):
            extra = cand.get('_extra_fields') or ('', '', '', '', '')
            school, company = extra[3], extra[4]
            full = school if column_name == 'school' else company
            show_tooltip = len(full) > (8 if column_name == 'school' else 10)
        else:
            show_tooltip = False

        if not full or not show_tooltip:
            self._hide_tooltip()
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
            300, lambda: self._show_tooltip(full, x, y, tooltip_key)
        )

    def _show_tooltip(self, text, x, y, tooltip_key=None, parent=None):
        """显示 tooltip 窗口。"""
        self._hide_tooltip()
        tip = tk.Toplevel(parent or self.root)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f'+{x}+{y}')
        label = tk.Label(
            tip, text=text, background='#FFFFE0', relief='solid', borderwidth=1,
            font=(FONT_FAMILY, int(10 * self.dpi_scale * self.zoom_factor)),
            padx=6, pady=3
        )
        label.pack()
        self._tooltip = tip
        self._tooltip_item = tooltip_key

    def _hide_tooltip(self, event=None):
        """隐藏 tooltip 窗口。"""
        after_id = getattr(self, '_tooltip_after_id', None)
        if after_id:
            self.root.after_cancel(after_id)
            self._tooltip_after_id = None
        tip = getattr(self, '_tooltip', None)
        if tip:
            tip.destroy()
            self._tooltip = None
        self._tooltip_item = None

    def _show_model_tooltip(self, text, x, y, tooltip_key=None):
        """显示模型列表的 Base URL tooltip"""
        self._hide_model_tooltip()
        tip = tk.Toplevel(self.root)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f'+{x}+{y}')
        label = tk.Label(
            tip, text=text, background='#FFFFE0', relief='solid', borderwidth=1,
            font=(FONT_FAMILY, int(10 * self.dpi_scale * self.zoom_factor)),
            padx=6, pady=3, wraplength=400
        )
        label.pack()
        self._model_tooltip = tip
        self._model_tooltip_item = tooltip_key

    def _hide_model_tooltip(self, event=None):
        """隐藏模型列表的 tooltip"""
        if self._model_tooltip_after_id:
            self.root.after_cancel(self._model_tooltip_after_id)
            self._model_tooltip_after_id = None
        if self._model_tooltip:
            self._model_tooltip.destroy()
            self._model_tooltip = None
        self._model_tooltip_item = None

    def _create_simple_tooltip(self, text, x, y):
        """创建简单的浮动 tooltip，返回 Toplevel 对象。"""
        tip = tk.Toplevel(self.root)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f'+{x}+{y}')
        label = tk.Label(
            tip, text=text, background='#FFFFE0', relief='solid', borderwidth=1,
            font=(FONT_FAMILY, int(10 * self.dpi_scale * self.zoom_factor)),
            padx=6, pady=3, wraplength=500
        )
        label.pack()
        return tip

    def _hide_skills_tooltip(self, event=None):
        """隐藏技能表 tooltip"""
        if self._skills_tooltip:
            self._skills_tooltip.destroy()
            self._skills_tooltip = None
        self._skills_tooltip_item = None

    def _hide_req_tooltip(self, event=None):
        """隐藏必要条件 tooltip"""
        if self._req_tooltip:
            self._req_tooltip.destroy()
            self._req_tooltip = None
        self._req_tooltip_idx = None

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
            self._hide_tooltip()
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
            values = tree.item(item, 'values')
            full = ''
            for c in filtered_ref[0]:
                if c.get('name') == values[0]:
                    full = c.get('_full_status', '')
                    break
            if not full or full.count('｜') < 2:
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
                300, lambda: self._show_tooltip(full, x, y, tooltip_key, parent=_parent)
            )

        tree.bind('<Motion>', on_motion)
        tree.bind('<Leave>', lambda e: _hide_all())

    def _on_tree_double_click(self, event):
        """双击候选人查看详情"""
        item = self.result_tree.identify_row(event.y)
        if item:
            self._show_candidate_detail(item)

    def _show_context_menu(self, event):
        """显示右键菜单"""
        item = self.result_tree.identify_row(event.y)
        if not item:
            return
        # 右键点击的行已在多选集合内时，保持现有选区
        if item not in self.result_tree.selection():
            self.result_tree.selection_set(item)

        selection = self.result_tree.selection()
        # 多选时显示批量操作功能
        if len(selection) > 1:
            context_menu_font = (FONT_FAMILY, int(11 * self.font_scale))
            menu = tk.Menu(self.root, tearoff=0, font=context_menu_font)
            icon_export_menu = self.icons.button('export', self.colors['text_primary'])
            icon_trash_menu = self.icons.button('trash', self.colors['text_primary'])
            icon_greet = self.icons.button('chat', self.colors['success'])
            menu._icon_refs = [icon_export_menu, icon_trash_menu, icon_greet]

            def remove_selected():
                if not messagebox.askyesno("确认删除", f"确定要移除选中的 {len(selection)} 名候选人吗？"):
                    return
                for sel_item in selection:
                    candidate = self._find_candidate_by_tree_item(sel_item)
                    if candidate:
                        geek_id = candidate.get('geek_id')
                        if geek_id:
                            # 从内存数据中移除
                            self.result_tree_data = [c for c in self.result_tree_data if c.get('geek_id') != geek_id]
                            # 从文件中移除
                            if CANDIDATES_PATH.exists():
                                with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                                    candidates = json.load(f)
                                candidates = [c for c in candidates if c.get('geek_id') != geek_id]
                                save_candidates_all(candidates, CANDIDATES_PATH)
                # 删除 Treeview 中的项
                for sel_item in selection:
                    self.result_tree.delete(sel_item)
                self.refresh_home_stats()

            menu.add_command(label=" 批量打招呼", image=icon_greet, compound=tk.LEFT,
                             command=lambda: self._greet_selected_candidates(selection, [self.result_tree_data], self.result_tree, parent=self.root))

            # 批量AI评估选项
            selected_candidates = []
            for sel_item in selection:
                c = self._find_candidate_by_tree_item(sel_item)
                if c:
                    selected_candidates.append(c)
            if selected_candidates:
                icon_ai_eval = self.icons.button('refresh', self.colors['primary'])
                menu._icon_refs.append(icon_ai_eval)
                menu.add_command(label=" 批量AI评估", image=icon_ai_eval, compound=tk.LEFT,
                                 command=lambda: self._ai_eval_selected_candidates(selected_candidates))
            if any(c.get('manual_review_required') for c in selected_candidates):
                icon_confirm = self.icons.button('stamp_check', self.colors['success'])
                menu._icon_refs.append(icon_confirm)
                menu.add_command(label=" 批量确认通过", image=icon_confirm, compound=tk.LEFT,
                                 command=lambda: self._batch_confirm_manual_review(selected_candidates, parent=self.root))

            menu.add_command(label=" 移除选中", image=icon_trash_menu, compound=tk.LEFT,
                             command=remove_selected)
            menu.add_separator()
            menu.add_command(label=" 导出选中", image=icon_export_menu, compound=tk.LEFT,
                             command=lambda: self._export_selected())
            menu.tk_popup(event.x_root, event.y_root)
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
            export_fn=lambda: self._export_selected(),
            refresh_fn=lambda: (self.refresh_results(), self.refresh_home_stats()),
            x_root=event.x_root,
            y_root=event.y_root,
        )

    def _build_candidate_context_menu(self, parent, tree, tree_item, candidate,
                                       show_detail_fn, remove_fn, export_fn,
                                       refresh_fn, x_root, y_root):
        """构建候选人右键菜单（筛选结果页和详细列表窗口共用）。"""
        context_menu_font = (FONT_FAMILY, int(11 * self.font_scale))
        menu = tk.Menu(parent, tearoff=0, font=context_menu_font)

        icon_detail = self.icons.button('eye', self.colors['text_primary'])
        icon_document = self.icons.button('document', self.colors['primary'])
        icon_greet = self.icons.button('chat', self.colors['success'])
        icon_followup = self.icons.button('pencil', self.colors['primary'])
        icon_feedback = self.icons.button('check', self.colors['primary'])
        icon_blacklist = self.icons.button('close', self.colors['danger'])
        icon_unblacklist = self.icons.button('check', self.colors['success'])
        icon_trash_menu = self.icons.button('trash', self.colors['text_primary'])
        icon_export_menu = self.icons.button('export', self.colors['text_primary'])
        icon_undo = self.icons.button('refresh', self.colors['text_primary'])

        icon_refs = [icon_detail, icon_document, icon_greet, icon_followup,
                     icon_feedback, icon_blacklist, icon_unblacklist,
                     icon_trash_menu, icon_export_menu, icon_undo]
        menu._icon_refs = icon_refs

        menu.add_command(label=" 查看详情", image=icon_detail, compound=tk.LEFT,
                         command=show_detail_fn)

        # AI评估选项：仅当未评估成功时显示
        if not candidate.get('llm_evaluated'):
            icon_ai_eval = self.icons.button('refresh', self.colors['primary'])
            menu._icon_refs.append(icon_ai_eval)
            menu.add_command(label=" AI评估", image=icon_ai_eval, compound=tk.LEFT,
                             command=lambda: self._ai_eval_selected_candidates([candidate]))

        menu.add_command(label=" 导入简历", image=icon_document, compound=tk.LEFT,
                         command=lambda: self._import_resume(
                             None, candidate=candidate, parent=parent,
                             tree=tree, tree_item=tree_item))

        if candidate.get('resume_eval_adjustment') is not None:
            menu.add_command(label=" 撤销简历评估", image=icon_undo, compound=tk.LEFT,
                             command=lambda: self._revert_resume_eval(
                                 None, candidate=candidate, parent=parent))

        if candidate.get('manual_review_required'):
            icon_confirm = self.icons.button('stamp_check', self.colors['success'])
            menu._icon_refs.append(icon_confirm)
            menu.add_command(label=" 确认通过", image=icon_confirm, compound=tk.LEFT,
                             command=lambda: self._confirm_manual_review(
                                 None, candidate=candidate, parent=parent))

        if not candidate.get('greet_sent', False):
            menu.add_command(label=" 打招呼", image=icon_greet, compound=tk.LEFT,
                             command=lambda: self._greet_single_candidate(
                                 None, candidate=candidate, parent=parent,
                                 tree=tree, tree_item=tree_item))

        menu.add_command(label=" 更新跟进", image=icon_followup, compound=tk.LEFT,
                         command=lambda: self._mark_candidate_followup(
                             None, candidate=candidate, parent=parent))
        menu.add_command(label=" 标记反馈", image=icon_feedback, compound=tk.LEFT,
                         command=lambda: self._mark_candidate_feedback(
                             None, candidate=candidate, parent=parent))

        if candidate.get('blacklisted'):
            menu.add_command(label=" 移出黑名单", image=icon_unblacklist, compound=tk.LEFT,
                             command=lambda: self._unblacklist_candidate(
                                 None, candidate=candidate, parent=parent))
        else:
            menu.add_command(label=" 加入黑名单", image=icon_blacklist, compound=tk.LEFT,
                             command=lambda: self._blacklist_candidate(
                                 None, candidate=candidate, parent=parent))

        menu.add_command(label=" 移除此人", image=icon_trash_menu, compound=tk.LEFT,
                         command=remove_fn)
        menu.add_separator()
        menu.add_command(label=" 导出选中", image=icon_export_menu, compound=tk.LEFT,
                         command=export_fn)

        menu.tk_popup(x_root, y_root)

    def _find_candidate_by_tree_item(self, item):
        """按结果表选中行定位候选人记录。"""
        values = self.result_tree.item(item, 'values')
        if not values:
            return None
        name = values[0]
        score = values[4]
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
        """提取最大化结果表使用的学历、年龄、状态、学校和公司字段。"""
        structured = candidate.get('structured') or {}
        edu = structured.get('degree', '')
        age = structured.get('age', '')
        job_status = structured.get('job_status', '')
        api_profile = candidate.get('_api_profile') or {}
        school = self._latest_history_value(
            api_profile.get('educations'), 'school',
            candidate.get('summary', ''), '教育经历：',
        )
        company = self._latest_history_value(
            api_profile.get('works'), 'company',
            candidate.get('summary', ''), '工作经历：',
        )
        # 有缺失时使用本地轻量正则兜底，避免仅为列表展示导入完整自动化模块。
        if not edu or not age or not job_status:
            info = self._extract_summary_display_fields(candidate.get('summary', ''))
            if not edu:
                edu = info.get('education', '')
            if not age:
                age = info.get('age', '')
            if not job_status:
                job_status = info.get('job_status', '')
        if age:
            age = f"{age}岁"
        return edu, age, job_status, school, company

    @staticmethod
    def _extract_summary_display_fields(summary):
        """从摘要提取结果表需要的学历、年龄和求职状态。"""
        text = str(summary or '')
        education = next(
            (value for value in ('博士', '硕士', '本科', '大专', '高中', '中专') if value in text),
            '',
        )
        age_match = re.search(r'年龄[：:]\s*(\d+)|(\d+)\s*岁', text)
        status_match = re.search(r'(?:求职状态[：:]\s*)?(离职|在职|在校|应届)', text)
        return {
            'education': education,
            'age': next((group for group in age_match.groups() if group), '') if age_match else '',
            'job_status': status_match.group(1) if status_match else '',
        }

    @staticmethod
    def _latest_history_value(entries, field, summary, summary_prefix):
        """按结束时间取最近一段经历的字段，缺失时从摘要对应行降级提取。"""
        valid_entries = [
            entry for entry in (entries or [])
            if isinstance(entry, dict) and str(entry.get(field, '')).strip()
        ]
        if valid_entries:
            def _date_key(entry):
                end = str(entry.get('end', '')).strip()
                if any(marker in end for marker in ('至今', '现在', '今')):
                    return (2, 99999999)
                digits = re.sub(r'\D', '', end)
                if digits:
                    return (1, int(digits[:8]))
                return (0, 0)

            dated_entries = [entry for entry in valid_entries if _date_key(entry)[0] > 0]
            latest = max(dated_entries, key=_date_key) if dated_entries else valid_entries[0]
            return str(latest.get(field, '')).strip()

        for line in str(summary or '').splitlines():
            stripped = line.strip()
            if stripped.startswith(summary_prefix):
                value = stripped[len(summary_prefix):].strip()
                return value.split()[0] if value else ''
        return ''

    def _format_candidate_status(self, candidate):
        """生成结果表中的候选人状态文本。超过 3 段且列未拉伸时截断，完整文本存 _full_status。"""
        # AI评估中状态（使用全局集合，refresh_results 后仍有效）
        geek_id = str(candidate.get('geek_id', ''))
        if geek_id in getattr(self, '_ai_evaluating_ids', set()):
            return "AI评估中..."

        # AI评估结果状态（显示约3秒后自动恢复）
        ai_eval_results = getattr(self, '_ai_eval_results', {})
        if geek_id in ai_eval_results:
            result = ai_eval_results[geek_id]
            # 检查是否在3秒内
            if time.time() - result.get('timestamp', 0) < 3:
                if result['status'] == 'success':
                    return f"✓ {result['message']}"
                else:
                    return f"✗ {result['message']}"
            else:
                # 超过3秒，清除结果
                del ai_eval_results[geek_id]

        followup_status = candidate.get('followup_status')
        if not followup_status:
            followup_status = "已打招呼" if candidate.get('greet_sent', False) else "未沟通"
        status_parts = [followup_status]
        if candidate.get('greet_confirmation_pending'):
            status_parts.append("发送待确认")
        if candidate.get('manual_review_required'):
            status_parts.append("需人工确认")
        if candidate.get('feedback_status'):
            status_parts.append(candidate.get('feedback_status'))
        if candidate.get('blacklisted'):
            status_parts.append("已屏蔽")
        full = "｜".join(status_parts)
        candidate['_full_status'] = full
        return full

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
        parent = parent or self.root
        name = candidate.get('name') or "该候选人"
        job_name = candidate.get('job_name') or "未标记岗位"
        existing_reason = candidate.get('blacklist_reason') or ""
        reason_placeholder = "简历造假/性格原因/信用差/其它恶劣行为"
        dialog_scale = self.dpi_scale * self.zoom_factor
        width = max(500, int(500 * dialog_scale))
        height = max(320, int(320 * dialog_scale))
        pad = int(20 * dialog_scale)

        win = tk.Toplevel(parent)
        win.title("加入黑名单")
        win.withdraw()
        win.transient(parent)
        win.grab_set()
        win.configure(bg=self.colors['bg_main'])
        win.resizable(False, False)
        _place_window_centered(win, width, height, parent=parent)

        container = ttk.Frame(win, style='Page.TFrame', padding=pad)
        container.pack(fill="both", expand=True)

        ttk.Label(
            container,
            text="加入黑名单",
            font=self.font_section,
            foreground=self.colors['text_primary'],
            background=self.colors['bg_main']
        ).pack(anchor="w")

        info = f"{name}｜{job_name}"
        ttk.Label(
            container,
            text=info,
            font=self.font_label,
            foreground=self.colors['text_secondary'],
            background=self.colors['bg_main'],
            wraplength=width - pad * 2
        ).pack(anchor="w", pady=(int(6 * dialog_scale), int(16 * dialog_scale)))

        ttk.Label(
            container,
            text="屏蔽原因",
            font=self.font_label,
            foreground=self.colors['text_primary'],
            background=self.colors['bg_main']
        ).pack(anchor="w", pady=(0, int(6 * dialog_scale)))

        reason_text = tk.Text(
            container,
            height=4,
            wrap="word",
            font=self.font_label,
            bg=self.colors['bg_card'],
            fg=self.colors['text_primary'],
            insertbackground=self.colors['text_primary'],
            relief="solid",
            bd=1,
            padx=int(10 * dialog_scale),
            pady=int(8 * dialog_scale)
        )
        reason_text.pack(fill="x")
        placeholder_active = {'value': False}

        def show_placeholder():
            placeholder_active['value'] = True
            reason_text.config(fg=self.colors['text_muted'])
            reason_text.delete("1.0", "end")
            reason_text.insert("1.0", reason_placeholder)

        def hide_placeholder():
            if placeholder_active['value']:
                placeholder_active['value'] = False
                reason_text.config(fg=self.colors['text_primary'])
                reason_text.delete("1.0", "end")

        if existing_reason:
            reason_text.insert("1.0", existing_reason)
        else:
            show_placeholder()

        ttk.Label(
            container,
            text="后续扫描、统计和导出会跳过此候选人。",
            font=self.font_log,
            foreground=self.colors['text_secondary'],
            background=self.colors['bg_main']
        ).pack(anchor="w", pady=(int(8 * dialog_scale), 0))

        button_frame = tk.Frame(container, bg=self.colors['bg_main'])
        button_frame.pack(anchor='center', pady=(int(16 * dialog_scale), 0))

        def close():
            try:
                win.grab_release()
            except tk.TclError:
                pass
            win.destroy()

        def save():
            reason = "" if placeholder_active['value'] else reason_text.get("1.0", "end").strip()
            close()
            on_confirm(reason)

        icon_check = self.icons.button('check', self.colors['primary'])
        icon_close = self.icons.button('close', self.colors['text_secondary'])
        button_pad = int(8 * dialog_scale)
        button_width = int(108 * dialog_scale)
        button_height = int(32 * dialog_scale)

        def create_dialog_button(icon, text, command):
            frame = tk.Frame(
                button_frame,
                bg=self.colors['bg_card'],
                highlightbackground=self.colors['border'],
                highlightthickness=1,
                width=button_width,
                height=button_height,
                cursor='hand2'
            )
            frame.pack_propagate(False)
            content = tk.Frame(frame, bg=self.colors['bg_card'])
            content.pack(expand=True)
            icon_label = tk.Label(content, image=icon, bg=self.colors['bg_card'])
            icon_label.image = icon
            icon_label.pack(side='left', padx=(0, 2), anchor='center')
            text_label = tk.Label(
                content,
                text=text,
                bg=self.colors['bg_card'],
                font=self.font_label,
                fg=self.colors['text_primary']
            )
            text_label.pack(side='left', padx=(2, 0), anchor='center')

            children = [frame, content, icon_label, text_label]

            def on_enter(_event):
                for widget in children:
                    widget.config(bg=self.colors['bg_hover'])

            def on_leave(_event):
                for widget in children:
                    widget.config(bg=self.colors['bg_card'])

            for widget in children:
                widget.bind('<Enter>', on_enter)
                widget.bind('<Leave>', on_leave)
                widget.bind('<Button-1>', lambda _event, cmd=command: cmd())
            return frame

        create_dialog_button(icon_check, "确定", save).pack(side="left", padx=button_pad)
        create_dialog_button(icon_close, "取消", close).pack(side="left", padx=button_pad)

        win.protocol("WM_DELETE_WINDOW", close)
        reason_text.bind("<FocusIn>", lambda _event: hide_placeholder())
        reason_text.bind("<FocusOut>", lambda _event: show_placeholder() if not reason_text.get("1.0", "end").strip() else None)
        win.bind("<Escape>", lambda _event: close())
        win.bind("<Control-Return>", lambda _event: save())
        win.deiconify()
        win.lift(parent)
        if existing_reason:
            reason_text.focus_set()
            reason_text.tag_add("sel", "1.0", "end-1c")
        else:
            win.focus_set()

    def _update_candidate_blacklist(self, geek_id, reason):
        """按 geek_id 标记候选人黑名单，跨岗位生效。"""
        if not CANDIDATES_PATH.exists():
            return 0
        with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
            candidates = json.load(f)

        updated = 0
        blacklisted_at = datetime.now().strftime("%Y%m%d_%H%M%S")
        for c in candidates:
            if str(c.get('geek_id')) == str(geek_id):
                c['blacklisted'] = True
                c['blacklist_reason'] = reason.strip()
                c['blacklisted_at'] = blacklisted_at
                if not c.get('followup_status'):
                    c['followup_status'] = "不合适"
                updated += 1

        if updated:
            save_candidates_all(candidates, CANDIDATES_PATH)
        return updated

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

        # 2. 解析文件
        ext = os.path.splitext(filepath)[1].lower()
        resume_text = ""
        try:
            if ext == '.pdf':
                try:
                    from pdfminer.high_level import extract_text as _pdfminer_extract
                except ImportError:
                    messagebox.showwarning("缺少依赖",
                        "需要安装 pdfminer.six 才能解析 PDF 文件。\n\n"
                        "安装命令：pip install pdfminer.six")
                    return
                resume_text = _pdfminer_extract(filepath) or ""
            elif ext == '.docx':
                try:
                    import docx
                except ImportError:
                    messagebox.showwarning("缺少依赖",
                        "需要安装 python-docx 才能解析 Word 文件。\n\n"
                        "安装命令：pip install python-docx")
                    return
                doc = docx.Document(filepath)
                resume_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            elif ext in ('.txt', '.md'):
                # 纯文本 / Markdown：直接读取，尝试多种编码
                for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
                    try:
                        with open(filepath, 'r', encoding=enc) as f:
                            resume_text = f.read()
                        break
                    except (UnicodeDecodeError, UnicodeError):
                        continue
                if not resume_text:
                    messagebox.showwarning("读取失败", "无法以常见编码读取文件，请检查文件是否为文本格式。")
                    return
            elif ext == '.rtf':
                try:
                    from striprtf.striprtf import rtf_to_text
                except ImportError:
                    messagebox.showwarning("缺少依赖",
                        "需要安装 striprtf 才能解析 RTF 文件。\n\n"
                        "安装命令：pip install striprtf")
                    return
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    rtf_content = f.read()
                resume_text = rtf_to_text(rtf_content)
            elif ext in ('.html', '.htm'):
                import re
                html_content = ""
                for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
                    try:
                        with open(filepath, 'r', encoding=enc) as f:
                            html_content = f.read()
                        break
                    except (UnicodeDecodeError, UnicodeError):
                        continue
                if not html_content:
                    messagebox.showwarning("读取失败", "无法以常见编码读取 HTML 文件。")
                    return
                # 去除 <script>/<style> 块，再剥离标签
                html_content = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html_content, flags=re.S | re.I)
                resume_text = re.sub(r'<[^>]+>', ' ', html_content)
                resume_text = re.sub(r'\s+', ' ', resume_text).strip()
                # 还原常见 HTML 实体
                import html as _html_module
                resume_text = _html_module.unescape(resume_text)
            else:
                messagebox.showwarning("不支持的格式",
                    f"支持的格式：PDF、DOCX、TXT、MD、RTF、HTML\n当前文件：{ext}")
                return
        except Exception as e:
            messagebox.showerror("解析失败", f"无法解析简历文件：\n{e}")
            return

        resume_text = resume_text.strip()
        if len(resume_text) < 50:
            messagebox.showwarning("内容过少", "简历提取的文本内容过少，可能不是有效的简历文件。")
            return

        # 3. 拷贝文件到 resumes/ 目录
        from paths import get_base_dir
        resumes_dir = get_base_dir() / "resumes"
        resumes_dir.mkdir(exist_ok=True)
        geek_id = candidate.get('geek_id', 'unknown')
        name = candidate.get('name', '未知')
        # 清理姓名中的特殊字符（避免文件名问题）
        safe_name = ''.join(c for c in name if c.isalnum() or c in '_-一-鿿') or '未知'
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        dest = resumes_dir / f"{safe_name}_{geek_id}_{ts}{ext}"
        try:
            shutil.copy2(filepath, dest)
        except Exception as e:
            messagebox.showerror("存储失败", f"无法保存简历文件：\n{e}")
            return

        # 更新候选人记录（文件路径和导入时间）
        candidate['resume_file'] = str(dest)
        candidate['resume_imported_at'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 4. 预览确认（只显示前 300 字）
        preview = resume_text[:300]
        if len(resume_text) > 300:
            preview += f"\n\n... (共 {len(resume_text)} 字)"

        confirm = messagebox.askyesno(
            "简历预览",
            f"成功提取简历文本（{len(resume_text)} 字）：\n\n{preview}\n\n是否进行 AI 二次评估？",
        )

        # 即使不评估，也保存已导入的文件路径
        save_candidates_all(self.all_candidates, CANDIDATES_PATH)

        if not confirm:
            self.refresh_results()
            return

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
                api_key = get_api_key(provider_key, base_url)

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
                    _parent.after(0, _no_key)
                    return

                # 读取岗位需求
                job_config_path = get_base_dir() / "job_config.json"
                job_requirement = ""
                hard_conditions = ""
                if job_config_path.exists():
                    import json as _json
                    with open(job_config_path, 'r', encoding='utf-8') as f:
                        jc = _json.load(f)
                    job_requirement = jc.get('raw_text', '') or jc.get('description', '')
                    hc = jc.get('required_conditions') or jc.get('hard_conditions', [])
                    if hc:
                        hard_conditions = "## 硬性条件\n" + "\n".join(
                            f"- {c}" if isinstance(c, str) else f"- {c.get('text','')}"
                            for c in (hc if isinstance(hc, list) else [hc])
                        ) + "\n\n"

                self.append_log(f"[简历评估] 正在评估 {name}...")
                result = evaluate_with_resume(
                    candidate, resume_text, job_requirement,
                    api_config, api_key, hard_conditions=hard_conditions,
                )

                def _on_done():
                    save_candidates_all(self.all_candidates, CANDIDATES_PATH)
                    self.refresh_results()
                    self.refresh_home_stats()
                    if result.success:
                        sign = "+" if result.adjustment > 0 else ""
                        self.append_log(
                            f"[简历评估] ✅ {name}: {sign}{result.adjustment} "
                            f"→ 总分 {candidate.get('match_score', '?')}")
                        # 自定义对话框
                        eval_dialog = tk.Toplevel(_parent)
                        eval_dialog.transient(_parent)
                        eval_dialog.grab_set()
                        eval_dialog.title("简历二次评估完成")
                        eval_dialog.configure(bg=self.colors['bg_main'])
                        dialog_scale = self.dpi_scale * self.zoom_factor
                        dialog_width = int(520 * dialog_scale)
                        reason_text = candidate.get('resume_eval_reason', '')
                        # 估算文本行数：每行约 25 个中文字符
                        line_count = max(3, len(reason_text) // 25 + 1)
                        line_count = min(line_count, 12)  # 最多 12 行
                        # 高度 = 摘要区 + 文本区 + 按钮区，保持完成弹窗留白不过度拥挤
                        dialog_height = int((108 + line_count * 18 + 58) * dialog_scale)
                        self._center_window(eval_dialog, dialog_width, dialog_height)
                        # 摘要信息（较小字体）
                        summary_frame = ttk.Frame(eval_dialog, style='Page.TFrame')
                        outer_pad_x = int(24 * dialog_scale)
                        summary_frame.pack(fill="x", padx=outer_pad_x, pady=(int(18 * dialog_scale), int(8 * dialog_scale)))
                        summary_font = (FONT_FAMILY, int(11 * self.font_scale))
                        ttk.Label(summary_frame, text=f"候选人：{name}",
                                  font=summary_font, background=self.colors['bg_main']).pack(anchor="w")
                        ttk.Label(summary_frame, text=f"调整分：{sign}{result.adjustment}  最终分：{candidate.get('match_score', '?')}",
                                  font=summary_font, background=self.colors['bg_main'],
                                  foreground=self.colors['success']).pack(anchor="w")
                        # 评估理由（小字体，紧凑）
                        reason_frame = ttk.Frame(eval_dialog, style='Card.TFrame')
                        reason_frame.pack(fill="x", padx=outer_pad_x, pady=(int(6 * dialog_scale), int(12 * dialog_scale)))
                        reason_font = (FONT_FAMILY, int(10 * self.font_scale))
                        reason_text_widget = tk.Text(reason_frame, wrap='char', font=reason_font,
                                                     bg=self.colors['bg_card'], relief='flat',
                                                     padx=int(8 * dialog_scale), pady=int(8 * dialog_scale),
                                                     height=line_count)
                        reason_text_widget.insert('1.0', reason_text)
                        reason_text_widget.config(state='disabled')
                        reason_text_widget.pack(fill="x")
                        # 关闭按钮
                        btn_container = ttk.Frame(eval_dialog, style='Page.TFrame')
                        btn_container.pack(fill="x", pady=(int(8 * dialog_scale), int(6 * dialog_scale)))
                        btn_style = ttk.Style()
                        btn_style.configure(
                            'ResumeEval.TButton',
                            font=(FONT_FAMILY, int(11 * self.font_scale)),
                            padding=(int(18 * dialog_scale), int(5 * dialog_scale)),
                        )
                        ok_btn = ttk.Button(btn_container, text="确定",
                                            command=eval_dialog.destroy,
                                            style='ResumeEval.TButton')
                        ok_btn.pack(anchor="center")
                    else:
                        self.append_log(f"[简历评估] ❌ {name}: {result.reason}")
                        messagebox.showwarning("评估失败",
                            f"LLM 返回错误：{result.reason}",
                            parent=_parent)

                _parent.after(0, _on_done)

            except Exception as e:
                def _on_error():
                    self.append_log(f"[简历评估] ❌ {name} 异常：{e}")
                    if _tree_item is not None:
                        try:
                            _tree.set(_tree_item, 'status',
                                self._format_candidate_status(candidate))
                        except Exception:
                            pass
                    messagebox.showerror("评估异常",
                        f"二次评估出错：\n{e}", parent=_parent)
                _parent.after(0, _on_error)

        threading.Thread(target=_eval_worker, daemon=True).start()

    def _revert_resume_eval(self, item, candidate=None, parent=None):
        """撤销简历评估：清空简历数据和二次评估结果，回退分数。"""
        from llm_eval import _recalc_recommend_level

        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            return

        _parent = parent or self.root
        name = candidate.get('name', '')
        confirm = messagebox.askyesno(
            "撤销简历评估",
            f"确定要撤销 {name} 的简历评估吗？\n\n"
            f"将清空简历文件和二次评估结果，分数回退到一次评估状态。",
            parent=_parent,
        )
        if not confirm:
            return

        # 删除简历文件
        resume_file = candidate.get('resume_file')
        if resume_file:
            try:
                if os.path.exists(resume_file):
                    os.remove(resume_file)
            except Exception as e:
                self.append_log(f"[撤销评估] 删除简历文件失败：{e}")

        # 回退分数：match_score = rule_score + llm_adjustment
        # rule_score 缺失时（候选人未跑过一次评估却导入简历）由 _resolve_rule_score 还原，
        # 否则会落到含 resume_adj 的 match_score 上，撤销后 resume 调整值未被扣除
        from llm_eval import _resolve_rule_score
        rule_score = _resolve_rule_score(candidate)
        llm_adj = candidate.get('llm_adjustment', 0) or 0
        reverted_score = max(0, min(100, rule_score + llm_adj))

        # 清空简历相关字段
        for field in ('resume_file', 'resume_imported_at', 'resume_eval_adjustment',
                      'resume_eval_reason', 'resume_eval_model', 'resume_eval_at',
                      'resume_eval_dimension_scores'):
            candidate.pop(field, None)

        # 回退分数和推荐等级
        candidate['match_score'] = reverted_score
        candidate['recommend_level'] = _recalc_recommend_level(reverted_score)

        # 更新 score_breakdown
        breakdown = candidate.get('score_breakdown')
        if isinstance(breakdown, dict):
            breakdown.pop('resume_adjustment', None)
            breakdown['total'] = reverted_score

        # 保存并刷新
        save_candidates_all(self.all_candidates, CANDIDATES_PATH)
        self.refresh_results()
        self.refresh_home_stats()
        self.append_log(f"[撤销评估] {name}: 分数回退到 {reverted_score}")

    # ===== 一键AI评估功能 =====

    def _ai_eval_selected_candidates(self, candidates):
        """对选中的候选人发起AI评估"""
        if not candidates:
            return

        # 检查API配置
        api_config = self.api_config
        if not api_config or not api_config.get('api_provider'):
            messagebox.showwarning("警告", "请先配置AI模型")
            return

        from security import get_api_key
        api_key = get_api_key(api_config.get('api_provider', ''), api_config.get('base_url', ''))
        if not api_key:
            messagebox.showwarning("警告", "请先配置API Key")
            return

        # 获取岗位需求
        job_requirement, rule = self._get_job_requirement_for_candidates(candidates)
        if not job_requirement:
            messagebox.showwarning("警告", "无法获取岗位需求信息")
            return

        batch_requested = len(candidates) > 1

        # 过滤已在评估中的候选人
        candidates_to_eval = [c for c in candidates if str(c.get('geek_id', '')) not in self._ai_evaluating_ids]
        if not candidates_to_eval:
            messagebox.showinfo("提示", "选中的候选人正在评估中")
            return

        # 过滤已评估成功的候选人（一次评估或简历二次评估任一即视为已评估；
        # 对已导入简历的候选人再跑一次评估会叠加两次调整、污染 rule_score）
        already_evaluated = [c for c in candidates_to_eval if _candidate_has_ai_eval(c)]
        not_evaluated = [c for c in candidates_to_eval if not _candidate_has_ai_eval(c)]

        if already_evaluated and not not_evaluated:
            # 全部已评估
            messagebox.showinfo("提示", f"选中的 {len(already_evaluated)} 名候选人已全部评估过")
            return

        if already_evaluated:
            # 部分已评估，提示跳过
            skip_names = '、'.join(c.get('name', '?') for c in already_evaluated[:5])
            suffix = f"等{len(already_evaluated)}人" if len(already_evaluated) > 5 else ""
            messagebox.showinfo("提示",
                f"{len(already_evaluated)} 名候选人已评估过，将跳过：{skip_names}{suffix}\n"
                f"将对剩余 {len(not_evaluated)} 名候选人进行评估")
            candidates_to_eval = not_evaluated

        # 确认操作
        count = len(candidates_to_eval)
        if count > 1:
            if not messagebox.askyesno("确认", f"确定要对 {count} 名候选人进行AI评估吗？"):
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
            'skipped': [{'name': c.get('name', '?'), 'reason': '已评估过'} for c in already_evaluated],
            'success': [],
            'failed': [],
        }

        # 启动定时刷新
        self._ai_eval_refresh_timer = self.root.after(1000, self._refresh_ai_eval_status)

        threading.Thread(
            target=self._do_ai_eval_batch,
            args=(candidates_to_eval, job_requirement, rule, api_config, api_key),
            daemon=True
        ).start()

    def _get_job_requirement_for_candidates(self, candidates):
        """根据候选人获取岗位需求文本和规则。返回 (job_requirement, rule)。"""
        if not candidates:
            return "", {}

        # 获取第一个候选人的岗位名称
        job_name = candidates[0].get('job_name', '')
        if not job_name:
            return "", {}

        # 从job_config.json获取岗位需求
        job_rules = self._get_job_rules_cached()
        rule = job_rules.get(job_name, {})

        job_requirement = rule.get('original_requirement', '')
        if not job_requirement:
            min_exp = rule.get('min_exp', 0)
            edu = rule.get('edu', '不限')
            job_requirement = f"岗位：{job_name}，{min_exp}年经验，{edu}学历"

        return job_requirement, rule

    def _do_ai_eval_batch(self, candidates, job_requirement, rule, api_config, api_key):
        """后台执行AI评估"""
        import sys
        import io
        from llm_eval import evaluate_batch

        try:
            def progress_callback(percentage, description):
                self._ai_eval_done = int(percentage / 100 * self._ai_eval_total)

            # 构建硬条件摘要，供 LLM 评估时参考
            hard_parts = []
            if rule.get('min_exp'):
                hard_parts.append(f"- 经验：要求≥{rule['min_exp']}年，候选人需满足")
            if rule.get('edu') and rule.get('edu') != '不限':
                hard_parts.append(f"- 学历：要求{rule['edu']}")
            if rule.get('max_age'):
                hard_parts.append(f"- 年龄：上限{rule['max_age']}岁")
            if rule.get('work_location'):
                hard_parts.append(f"- 地点：要求{rule['work_location']}，候选人期望城市需匹配")
            if rule.get('salary_max'):
                hard_parts.append(f"- 薪资：岗位最高{rule['salary_max']}K，候选人期望不应超过")
            req_conds = rule.get('required_conditions', [])
            if req_conds:
                cond_names = [c if isinstance(c, str) else c.get('name', str(c)) for c in req_conds]
                hard_parts.append(f"- 必要条件：{'、'.join(cond_names)}")
            hard_conditions = "## 筛选硬条件\n" + "\n".join(hard_parts) + "\n\n" if hard_parts else ""

            # 抑制 evaluate_batch 的 print 输出
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()

            try:
                # 执行评估
                evaluate_batch(
                    candidates, job_requirement, api_config, api_key,
                    hard_conditions=hard_conditions,
                    rule=rule,
                    progress_callback=progress_callback,
                )
            finally:
                sys.stdout = old_stdout

            # 记录评估结果状态
            success_items = []
            failed_items = []
            for c in candidates:
                geek_id = str(c.get('geek_id', ''))
                name = c.get('name') or '未命名候选人'
                if c.get('llm_evaluated'):
                    adjustment = c.get('llm_adjustment', 0)
                    self._ai_eval_results[geek_id] = {
                        'status': 'success',
                        'message': f"评估完成，调整分：{'+' if adjustment >= 0 else ''}{adjustment}",
                        'timestamp': time.time()
                    }
                    success_items.append({'name': name, 'adjustment': adjustment})
                else:
                    error = c.get('llm_error', '评估失败')
                    self._ai_eval_results[geek_id] = {
                        'status': 'failed',
                        'message': error,
                        'timestamp': time.time()
                    }
                    failed_items.append({'name': name, 'reason': error})
            self._set_ai_eval_batch_outcome(success_items, failed_items)

            # 保存结果
            self._save_ai_eval_results(candidates)

        except Exception as e:
            # 记录异常
            failed_items = []
            for c in candidates:
                name = c.get('name') or '未命名候选人'
                self._ai_eval_results[str(c.get('geek_id', ''))] = {
                    'status': 'failed',
                    'message': str(e),
                    'timestamp': time.time()
                }
                failed_items.append({'name': name, 'reason': str(e)})
            self._set_ai_eval_batch_outcome([], failed_items)
        finally:
            self._ai_eval_in_progress = False
            # 从评估中集合移除
            for c in candidates:
                self._ai_evaluating_ids.discard(str(c.get('geek_id', '')))
            self.root.after(0, self._on_ai_eval_complete)

    def _refresh_ai_eval_status(self):
        """定时刷新AI评估状态"""
        if self._ai_eval_in_progress:
            self.refresh_results(force=True)
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
        def compact_text(value, max_chars):
            text = str(value or "").strip()
            if len(text) <= max_chars:
                return text
            return text[:max_chars - 1] + "…"

        success = summary.get('success') or []
        failed = summary.get('failed') or []
        skipped = summary.get('skipped') or []
        selected_count = summary.get('selected_count') or (len(success) + len(failed) + len(skipped))

        lines = [
            f"本次共选择 {selected_count} 人",
            f"成功 {len(success)} 人",
            f"失败 {len(failed)} 人",
            f"跳过 {len(skipped)} 人",
        ]

        if failed:
            lines.append("")
            lines.append("失败候选人：")
            for item in failed[:6]:
                name = compact_text(item.get('name') or '?', 12)
                reason = compact_text(item.get('reason') or '评估失败', 36)
                lines.append(f"- {name}：{reason}")
            if len(failed) > 6:
                lines.append(f"- 另有 {len(failed) - 6} 人失败，请在状态列查看")

        if skipped:
            lines.append("")
            lines.append("已跳过：")
            for item in skipped[:3]:
                name = compact_text(item.get('name') or '?', 12)
                reason = compact_text(item.get('reason') or '已跳过', 24)
                lines.append(f"- {name}：{reason}")
            if len(skipped) > 3:
                lines.append(f"- 另有 {len(skipped) - 3} 人已跳过")

        return "AI 评估完成", "\n".join(lines), bool(failed)

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
        """保存AI评估结果到candidates.json"""
        from storage import save_candidates_all

        # 读取现有数据
        if CANDIDATES_PATH.exists():
            try:
                with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                    all_candidates = json.load(f)
            except Exception:
                all_candidates = self.all_candidates if hasattr(self, 'all_candidates') else []
        else:
            all_candidates = self.all_candidates if hasattr(self, 'all_candidates') else []

        # 更新评估结果
        eval_map = {c.get('geek_id'): c for c in candidates}
        for i, c in enumerate(all_candidates):
            geek_id = c.get('geek_id')
            if geek_id in eval_map:
                eval_result = eval_map[geek_id]
                all_candidates[i].update({
                    'llm_evaluated': eval_result.get('llm_evaluated'),
                    'llm_adjustment': eval_result.get('llm_adjustment'),
                    'llm_reason': eval_result.get('llm_reason'),
                    'llm_model': eval_result.get('llm_model'),
                    'llm_error': eval_result.get('llm_error'),
                    'match_score': eval_result.get('match_score'),
                    'recommend_level': eval_result.get('recommend_level'),
                    'rule_score': eval_result.get('rule_score'),
                    'score_breakdown': eval_result.get('score_breakdown'),
                    'llm_hard_condition_verdict': eval_result.get('llm_hard_condition_verdict'),
                    'llm_hard_condition_findings': eval_result.get('llm_hard_condition_findings'),
                    'llm_dimension_scores': eval_result.get('llm_dimension_scores'),
                    'qualification_status': eval_result.get('qualification_status'),
                    'qualification_reasons': eval_result.get('qualification_reasons'),
                    'qualification_evidence': eval_result.get('qualification_evidence'),
                    'manual_review_required': eval_result.get('manual_review_required'),
                    'auto_greet_blocked_reason': eval_result.get('auto_greet_blocked_reason'),
                })

        # 保存
        save_candidates_all(all_candidates, CANDIDATES_PATH)

        # 更新内存数据
        if hasattr(self, 'all_candidates'):
            for i, c in enumerate(self.all_candidates):
                geek_id = c.get('geek_id')
                if geek_id in eval_map:
                    self.all_candidates[i].update(eval_map[geek_id])

    def _blacklist_candidate(self, item, candidate=None, parent=None):
        """把选中候选人加入黑名单。"""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人")
            return

        name = candidate.get('name', '该候选人')

        def save_blacklist(reason):
            try:
                updated = self._update_candidate_blacklist(candidate.get('geek_id'), reason)
                if not updated:
                    messagebox.showerror("错误", "加入黑名单失败：未找到候选人")
                    return
                candidate['blacklisted'] = True
                candidate['blacklist_reason'] = reason.strip()
                candidate['blacklisted_at'] = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._regenerate_excel()
                self.refresh_home_stats()
                self.refresh_stats()
                self.refresh_results()
                messagebox.showinfo("成功", f"已屏蔽：{name}")
            except Exception as exc:
                messagebox.showerror("错误", f"加入黑名单失败：{exc}")

        self._open_blacklist_reason_dialog(candidate, parent or self.root, save_blacklist)

    def _update_candidate_unblacklist(self, geek_id):
        """按 geek_id 移除候选人黑名单，跨岗位生效。"""
        if not CANDIDATES_PATH.exists():
            return 0
        with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
            candidates = json.load(f)

        updated = 0
        for c in candidates:
            if str(c.get('geek_id')) == str(geek_id) and c.get('blacklisted'):
                c.pop('blacklisted', None)
                c.pop('blacklist_reason', None)
                c.pop('blacklisted_at', None)
                updated += 1

        if updated:
            save_candidates_all(candidates, CANDIDATES_PATH)
        return updated

    def _unblacklist_candidate(self, item, candidate=None, parent=None):
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
            messagebox.showinfo("成功", f"已移出黑名单：{name}")
        except Exception as exc:
            messagebox.showerror("错误", f"移出黑名单失败：{exc}")

    def _update_candidate_followup(self, geek_id, job_name, status, note):
        """更新候选人的跟进状态。"""
        if not CANDIDATES_PATH.exists():
            return False
        with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
            candidates = json.load(f)

        updated = False
        followup_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        for c in candidates:
            if c.get('geek_id') == geek_id and c.get('job_name', '').replace(" ", "") == job_name.replace(" ", ""):
                c['followup_status'] = status
                c['followup_note'] = note.strip()
                c['followup_updated_at'] = followup_time
                if status == "已打招呼":
                    mark_candidate_greeted(c, "manual_status", followup_time)
                    c['followup_note'] = note.strip()
                updated = True
                break

        if updated:
            save_candidates_all(candidates, CANDIDATES_PATH)
        return updated

    def _clear_manual_review(self, geek_id):
        """按 geek_id 清除人工确认标记，跨岗位生效。"""
        if not CANDIDATES_PATH.exists():
            return 0
        with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
            candidates = json.load(f)

        updated = 0
        for c in candidates:
            if str(c.get('geek_id')) == str(geek_id):
                if c.get('manual_review_required'):
                    c['manual_review_required'] = False
                    c['qualification_status'] = 'qualified'
                    c.pop('auto_greet_blocked_reason', None)
                    updated += 1

        if updated:
            save_candidates_all(candidates, CANDIDATES_PATH)
        return updated

    def _confirm_manual_review(self, item, candidate=None, parent=None):
        """清除候选人的需人工确认标记。"""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人")
            return

        name = candidate.get('name', '该候选人')
        risk_flags = candidate.get('risk_flags') or []
        risk_text = "\n".join(f"- {flag}" for flag in risk_flags) if risk_flags else "无"
        if not messagebox.askyesno(
            "确认通过",
            f"确认 {name} 的人工审查已通过？\n\n"
            f"审查原因：\n{risk_text}\n\n"
            f"确认后将清除「需人工确认」标记。如需联系此人，请直接右键打招呼。",
            parent=parent or self.root
        ):
            return

        try:
            updated = self._clear_manual_review(candidate.get('geek_id'))
            if not updated:
                messagebox.showinfo("提示", f"{name} 已无需确认的标记",
                                    parent=parent or self.root)
                return
            candidate['manual_review_required'] = False
            candidate['qualification_status'] = 'qualified'
            candidate.pop('auto_greet_blocked_reason', None)
            self._regenerate_excel()
            self.refresh_home_stats()
            self.refresh_stats()
            self.refresh_results()
            messagebox.showinfo("成功", f"已确认通过：{name}",
                                parent=parent or self.root)
        except Exception as exc:
            messagebox.showerror("错误", f"操作失败：{exc}",
                                 parent=parent or self.root)

    def _batch_confirm_manual_review(self, candidates, parent=None):
        """批量清除候选人的需人工确认标记。"""
        to_confirm = [c for c in candidates if c.get('manual_review_required')]
        if not to_confirm:
            messagebox.showinfo("提示", "选中候选人中无需确认的标记",
                                parent=parent or self.root)
            return
        names = ", ".join(c.get('name', '?') for c in to_confirm[:5])
        if len(to_confirm) > 5:
            names += f" 等 {len(to_confirm)} 人"
        if not messagebox.askyesno(
            "批量确认通过",
            f"确认以下 {len(to_confirm)} 人的人工审查已通过？\n\n{names}\n\n"
            f"确认后将清除「需人工确认」标记。如需联系，请手动打招呼。",
            parent=parent or self.root
        ):
            return

        confirmed = 0
        for c in to_confirm:
            try:
                updated = self._clear_manual_review(c.get('geek_id'))
                if updated:
                    c['manual_review_required'] = False
                    c['qualification_status'] = 'qualified'
                    c.pop('auto_greet_blocked_reason', None)
                    confirmed += 1
            except Exception:
                continue

        if confirmed:
            self._regenerate_excel()
            self.refresh_home_stats()
            self.refresh_stats()
            self.refresh_results()
        messagebox.showinfo("完成", f"已确认通过 {confirmed}/{len(to_confirm)} 人",
                            parent=parent or self.root)

    def _mark_candidate_followup(self, item, candidate=None, parent=None):
        """标记候选人的跟进状态和备注。"""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人")
            return

        _parent = parent or self.root
        win = tk.Toplevel(_parent)
        win.title("更新跟进")
        win.transient(_parent)
        win.grab_set()
        win.withdraw()
        win.configure(bg=self.colors['bg_main'])

        pad = int(18 * self.dpi_scale * self.zoom_factor)
        frame = ttk.Frame(win, style='Page.TFrame', padding=pad)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=f"{candidate.get('name', '未知')}｜{candidate.get('job_name', '未知')}",
            font=(FONT_FAMILY, int(13 * self.font_scale)),
            foreground=self.colors['primary'],
            background=self.colors['bg_main']
        ).pack(anchor='w', pady=(0, int(12 * self.dpi_scale * self.zoom_factor)))

        ttk.Label(
            frame,
            text="跟进状态",
            font=(FONT_FAMILY, int(12 * self.font_scale)),
            style='Page.TLabel'
        ).pack(anchor='w')

        default_status = candidate.get('followup_status') or ("已打招呼" if candidate.get('greet_sent') else FOLLOWUP_STATUS_OPTIONS[0])
        status_var = tk.StringVar(value=default_status)
        status_combo = ttk.Combobox(
            frame,
            textvariable=status_var,
            values=FOLLOWUP_STATUS_OPTIONS,
            state='readonly',
            font=(FONT_FAMILY, int(12 * self.font_scale)),
            width=18
        )
        status_combo.pack(anchor='w', fill='x', pady=(int(5 * self.dpi_scale * self.zoom_factor), int(12 * self.dpi_scale * self.zoom_factor)))

        ttk.Label(
            frame,
            text="备注",
            font=(FONT_FAMILY, int(12 * self.font_scale)),
            style='Page.TLabel'
        ).pack(anchor='w')

        note_text = tk.Text(
            frame,
            height=5,
            wrap='word',
            font=(FONT_FAMILY, int(12 * self.font_scale)),
            bg=self.colors['bg_card'],
            fg=self.colors['text_primary'],
            relief='solid',
            bd=1
        )
        note_text.pack(fill='both', expand=True, pady=(int(5 * self.dpi_scale * self.zoom_factor), int(14 * self.dpi_scale * self.zoom_factor)))
        if candidate.get('followup_note'):
            note_text.insert('1.0', candidate.get('followup_note', ''))

        btn_frame = ttk.Frame(frame, style='Page.TFrame')
        btn_frame.pack(anchor='center')

        def close():
            win.grab_release()
            win.destroy()

        def save_followup():
            status = status_var.get().strip()
            note = note_text.get('1.0', 'end').strip()
            if status not in FOLLOWUP_STATUS_OPTIONS:
                messagebox.showerror("错误", "请选择有效的跟进状态")
                return
            try:
                updated = self._update_candidate_followup(
                    candidate.get('geek_id'),
                    candidate.get('job_name', ''),
                    status,
                    note
                )
                if not updated:
                    messagebox.showerror("错误", "保存跟进状态失败：未找到候选人")
                    return
                candidate['followup_status'] = status
                candidate['followup_note'] = note
                candidate['followup_updated_at'] = datetime.now().strftime("%Y%m%d_%H%M%S")
                if status == "已打招呼":
                    mark_candidate_greeted(
                        candidate,
                        "manual_status",
                        candidate['followup_updated_at'],
                    )
                    candidate['followup_note'] = note
                self._regenerate_excel()
                self.refresh_results()
                close()
            except Exception as exc:
                messagebox.showerror("错误", f"保存跟进状态失败：{exc}")

        ttk.Button(btn_frame, text="保存", command=save_followup).pack(side='left', padx=(0, int(8 * self.dpi_scale * self.zoom_factor)))
        ttk.Button(btn_frame, text="取消", command=close).pack(side='left')

        win.protocol("WM_DELETE_WINDOW", close)
        _place_window_centered(win, int(460 * self.dpi_scale * self.zoom_factor), int(360 * self.dpi_scale * self.zoom_factor), parent=_parent)
        win.deiconify()

    def _update_candidate_feedback(self, geek_id, job_name, status, note):
        """更新候选人的人工反馈。"""
        if not CANDIDATES_PATH.exists():
            return False
        with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
            candidates = json.load(f)

        updated = False
        feedback_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        for c in candidates:
            if c.get('geek_id') == geek_id and c.get('job_name', '').replace(" ", "") == job_name.replace(" ", ""):
                c['feedback_status'] = status
                c['feedback_note'] = note.strip()
                c['feedback_updated_at'] = feedback_time
                updated = True
                break

        if updated:
            save_candidates_all(candidates, CANDIDATES_PATH)
        return updated

    def _mark_candidate_feedback(self, item, candidate=None, parent=None):
        """标记候选人的人工反馈状态和备注。"""
        candidate = self._resolve_candidate(item, candidate)
        if not candidate:
            messagebox.showerror("错误", "未找到候选人")
            return

        _parent = parent or self.root
        win = tk.Toplevel(_parent)
        win.title("标记反馈")
        win.transient(_parent)
        win.grab_set()
        win.withdraw()
        win.configure(bg=self.colors['bg_main'])

        pad = int(18 * self.dpi_scale * self.zoom_factor)
        frame = ttk.Frame(win, style='Page.TFrame', padding=pad)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=f"{candidate.get('name', '未知')}｜{candidate.get('job_name', '未知')}",
            font=(FONT_FAMILY, int(13 * self.font_scale)),
            foreground=self.colors['primary'],
            background=self.colors['bg_main']
        ).pack(anchor='w', pady=(0, int(12 * self.dpi_scale * self.zoom_factor)))

        ttk.Label(
            frame,
            text="反馈状态",
            font=(FONT_FAMILY, int(12 * self.font_scale)),
            style='Page.TLabel'
        ).pack(anchor='w')

        status_var = tk.StringVar(value=candidate.get('feedback_status') or FEEDBACK_STATUS_OPTIONS[0])
        status_combo = ttk.Combobox(
            frame,
            textvariable=status_var,
            values=FEEDBACK_STATUS_OPTIONS,
            state='readonly',
            font=(FONT_FAMILY, int(12 * self.font_scale)),
            width=18
        )
        status_combo.pack(anchor='w', fill='x', pady=(int(5 * self.dpi_scale * self.zoom_factor), int(12 * self.dpi_scale * self.zoom_factor)))

        ttk.Label(
            frame,
            text="备注",
            font=(FONT_FAMILY, int(12 * self.font_scale)),
            style='Page.TLabel'
        ).pack(anchor='w')

        note_text = tk.Text(
            frame,
            height=5,
            wrap='word',
            font=(FONT_FAMILY, int(12 * self.font_scale)),
            bg=self.colors['bg_card'],
            fg=self.colors['text_primary'],
            relief='solid',
            bd=1
        )
        note_text.pack(fill='both', expand=True, pady=(int(5 * self.dpi_scale * self.zoom_factor), int(14 * self.dpi_scale * self.zoom_factor)))
        if candidate.get('feedback_note'):
            note_text.insert('1.0', candidate.get('feedback_note', ''))

        btn_frame = ttk.Frame(frame, style='Page.TFrame')
        btn_frame.pack(anchor='center')

        def close():
            win.grab_release()
            win.destroy()

        def save_feedback():
            status = status_var.get().strip()
            note = note_text.get('1.0', 'end').strip()
            if status not in FEEDBACK_STATUS_OPTIONS:
                messagebox.showerror("错误", "请选择有效的反馈状态")
                return
            try:
                updated = self._update_candidate_feedback(
                    candidate.get('geek_id'),
                    candidate.get('job_name', ''),
                    status,
                    note
                )
                if not updated:
                    messagebox.showerror("错误", "保存反馈失败：未找到候选人")
                    return
                candidate['feedback_status'] = status
                candidate['feedback_note'] = note
                candidate['feedback_updated_at'] = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._regenerate_excel()
                self.refresh_results()
                close()
            except Exception as exc:
                messagebox.showerror("错误", f"保存反馈失败：{exc}")

        ttk.Button(btn_frame, text="保存", command=save_feedback).pack(side='left', padx=(0, int(8 * self.dpi_scale * self.zoom_factor)))
        ttk.Button(btn_frame, text="取消", command=close).pack(side='left')

        win.protocol("WM_DELETE_WINDOW", close)
        _place_window_centered(win, int(460 * self.dpi_scale * self.zoom_factor), int(360 * self.dpi_scale * self.zoom_factor), parent=_parent)
        win.deiconify()

    def _format_candidate_detail(self, c):
        """格式化候选人详情为结构化文本（替代原始 JSON dump）"""
        from bossmaster import extract_summary_info

        summary = c.get('summary', '')
        info = extract_summary_info(summary)

        lines = []
        lines.append("═" * 50)
        lines.append(f"  姓名：{c.get('name', '未知')}")
        lines.append(f"  岗位：{c.get('job_name', '未知')}")

        # 核心信息速览
        core_parts = []
        age = info.get('age')
        if age:
            core_parts.append(f"{age} 岁")
        exp = info.get('exp_years')
        if exp:
            core_parts.append(f"{exp} 年")
        salary = info.get('salary')
        if salary:
            core_parts.append(f"期望薪资 {salary}")
        status = info.get('job_status')
        if status:
            core_parts.append(status)
        if core_parts:
            lines.append(f"  {'｜'.join(core_parts)}")

        # 学历/学校/专业 — 支持多学历，每条一行
        edu_entries: list[str] = []
        edu = info.get('education')
        api_profile = c.get('_api_profile')

        # API 结构化画像优先
        if api_profile and api_profile.get('educations'):
            for entry in api_profile['educations']:
                parts = [entry.get(k, '') for k in ('school', 'major', 'degree')]
                parts = [p for p in parts if p]
                start = entry.get('start', '')
                end = entry.get('end', '')
                if start or end:
                    parts.append(f"{start}-{end}")
                if parts:
                    edu_entries.append("·".join(parts))
        else:
            # 优先从 "教育经历：" 标签行解析（API 格式，可能有多条）
            # 格式："教育经历：清华大学 计算机 本科 2015.09 2018.06"
            api_edu_found = False
            for sline in summary.split('\n'):
                sline = sline.strip()
                if sline.startswith("教育经历："):
                    api_edu_found = True
                    val = sline[len("教育经历："):].strip()
                    parts = val.split()
                    if len(parts) >= 3:
                        # 学校 专业 学历 [起始] [结束]
                        entry_parts = [parts[0], parts[1], parts[2]]
                        if len(parts) >= 4:
                            entry_parts.append("-".join(parts[3:5]))
                        edu_entries.append("·".join(entry_parts))
                    elif len(parts) == 2:
                        edu_entries.append("·".join(parts))

            # DOM 格式兜底（无标签，学校名+专业+学历连写，可能有多条）
            if not api_edu_found:
                edu_entry_pat = re.compile(r'(.+(?:大学|学院))(.+?)(本科|硕士|博士|大专|MBA|EMBA)')
                edu_nopat = re.compile(r'(.+(?:大学|学院))(本科|硕士|博士|大专|MBA|EMBA)')
                for sline in summary.split('\n'):
                    sline = sline.strip()
                    m = edu_entry_pat.match(sline)
                    if m:
                        entry_parts = [m.group(1)]
                        if m.group(2):
                            entry_parts.append(m.group(2))
                        edu_entries.append("·".join(entry_parts))
                        continue
                    m2 = edu_nopat.match(sline)
                    if m2:
                        edu_entries.append(m2.group(1))

        # 展示多学历
        if edu_entries:
            lines.append(f"  最高学历：{edu}" if edu else "  学历信息")
            for entry in edu_entries:
                lines.append(f"    📚 {entry}")
        elif edu:
            lines.append(f"  {edu}")

        lines.append(f"  geek_id：{c.get('geek_id', '')}")
        lines.append("═" * 50)

        # 评分信息
        lines.append("")
        lines.append("【评分信息】")
        score = c.get('match_score', 0)
        level = "强烈推荐" if score >= SCORE_THRESHOLD_STRONG else ("推荐" if score >= SCORE_THRESHOLD_RECOMMEND else "待定")
        lines.append(f"  匹配分：{score}（{level}）")
        lines.append(f"  技能匹配：{c.get('skill_match_ratio', '—')}")
        breakdown = c.get('score_breakdown') or {}
        if breakdown:
            parts = [
                f"基础{breakdown.get('base', 0)}",
                f"技能{breakdown.get('skill', 0)}",
                f"经验{breakdown.get('experience', 0)}",
                f"学历{breakdown.get('education', 0)}",
                f"优先项{breakdown.get('preferred', 0)}",
            ]
            ai_adj = breakdown.get('ai_adjustment')
            resume_adj = breakdown.get('resume_adjustment')
            if resume_adj is not None:
                # 有简历评估时只显示简历调整值（替代一次评估）
                # resume_adj=0 时不追加任何项，保证拆解各项合计 = 总分（total 仅含 resume_adjustment）
                if resume_adj != 0:
                    sign = "+" if resume_adj > 0 else ""
                    parts.append(f"简历{sign}{resume_adj}")
            elif ai_adj is not None and ai_adj != 0:
                sign = "+" if ai_adj > 0 else ""
                parts.append(f"AI{sign}{ai_adj}")
            lines.append(f"  评分拆解：{' + '.join(parts)}")
        if c.get('greet_sent'):
            lines.append(f"  状态：已打招呼")
        else:
            lines.append(f"  状态：未打招呼")
        if c.get('manual_review_required'):
            lines.append(f"  沟通限制：需人工确认后再打招呼")
        if c.get('blacklisted'):
            lines.append(f"  屏蔽状态：已加入黑名单")

        risk_flags = c.get('risk_flags') or []
        if risk_flags:
            lines.append("")
            lines.append("【风险提示】")
            for flag in risk_flags:
                lines.append(f"  - {flag}")
            blocked_reason = c.get('auto_greet_blocked_reason')
            if blocked_reason:
                lines.append(f"  自动打招呼阻断原因：{blocked_reason}")

        followup_status = c.get('followup_status') or ("已打招呼" if c.get('greet_sent') else "未沟通")
        if followup_status or c.get('followup_note'):
            lines.append("")
            lines.append("【跟进状态】")
            lines.append(f"  状态：{followup_status}")
            if c.get('followup_updated_at'):
                lines.append(f"  时间：{c.get('followup_updated_at')}")
            if c.get('followup_note'):
                lines.append("  备注：")
                for note_line in str(c.get('followup_note', '')).split('\n'):
                    lines.append(f"    {note_line}")

        if c.get('feedback_status'):
            lines.append("")
            lines.append("【人工反馈】")
            lines.append(f"  状态：{c.get('feedback_status')}")
            if c.get('feedback_updated_at'):
                lines.append(f"  时间：{c.get('feedback_updated_at')}")
            if c.get('feedback_note'):
                lines.append("  备注：")
                for note_line in str(c.get('feedback_note', '')).split('\n'):
                    lines.append(f"    {note_line}")

        if c.get('blacklisted'):
            lines.append("")
            lines.append("【黑名单】")
            lines.append("  状态：已屏蔽")
            if c.get('blacklisted_at'):
                lines.append(f"  时间：{c.get('blacklisted_at')}")
            if c.get('blacklist_reason'):
                lines.append("  原因：")
                for note_line in str(c.get('blacklist_reason', '')).split('\n'):
                    lines.append(f"    {note_line}")

        explanation = c.get('score_explanation') or []
        if explanation:
            lines.append("")
            lines.append("【评分解释】")
            for item in explanation:
                lines.append(f"  - {item}")

        evidence_items = c.get('keyword_evidence') or []
        if evidence_items:
            lines.append("")
            lines.append("【命中证据】")
            for item in evidence_items:
                if not isinstance(item, dict):
                    continue
                name = item.get('name', '')
                weight = item.get('weight', 1)
                evidence = item.get('evidence', '')
                label = "优先项" if item.get('type') == 'preferred' else "技能"
                if evidence:
                    lines.append(f"  ✓ [{label}] {name}（权重{weight}）：{evidence}")
                else:
                    lines.append(f"  ✓ [{label}] {name}（权重{weight}）")

        # AI 评估信息
        lines.append("")
        if c.get('llm_evaluated'):
            lines.append("【AI 一次评估】")
            lines.append(f"  原始规则分：{c.get('rule_score', '—')}")
            adj = c.get('llm_adjustment', 0)
            sign = "+" if adj > 0 else ""
            lines.append(f"  AI 调整值：{sign}{adj}")
            # 调整后分数 = 规则分 + 一次评估调整值；不读 match_score（简历二次评估已替代为 rule+resume_adj）
            r1_score = max(0, min(100, (c.get('rule_score', 0) or 0) + adj))
            lines.append(f"  调整后分数：{r1_score}")
            lines.append(f"  评估模型：{c.get('llm_model', '未知')}")
            lines.append("")
            lines.append(f"  AI评估：")
            reason = c.get('llm_reason', '无').replace('\n', ' ').replace('\r', '').strip()
            lines.append(f"    {reason}")

            # AI 硬条件复核详情
            hc_verdict = c.get('llm_hard_condition_verdict', 'unknown')
            hc_findings = c.get('llm_hard_condition_findings') or []
            if hc_verdict != 'unknown' or hc_findings:
                verdict_label = {'pass': '通过', 'fail': '不通过', 'unknown': '未判定'}.get(hc_verdict, hc_verdict)
                lines.append("")
                lines.append(f"  硬条件复核：{verdict_label}")
                for finding in hc_findings:
                    if not isinstance(finding, dict):
                        continue
                    cond = finding.get('condition', '')
                    f_verdict = finding.get('verdict', 'unknown')
                    f_conf = finding.get('confidence', 'low')
                    evidence = finding.get('evidence', '')
                    icon = {'pass': '✓', 'fail': '✗', '?': '?'}.get(f_verdict, '?')
                    conf_label = {'high': '高置信', 'medium': '中置信', 'low': '低置信'}.get(f_conf, f_conf)
                    lines.append(f"    {icon} {cond}（{conf_label}）")
                    if evidence:
                        lines.append(f"      证据：{evidence}")

            # 资格审查状态
            qual_status = c.get('qualification_status', 'qualified')
            qual_reasons = c.get('qualification_reasons') or []
            if qual_status != 'qualified' or qual_reasons:
                status_label = {'qualified': '合格', 'rejected': '淘汰', 'manual_review': '待人工确认'}.get(qual_status, qual_status)
                lines.append("")
                lines.append(f"  资格审查：{status_label}")
                for reason_item in qual_reasons:
                    lines.append(f"    - {reason_item}")

            # AI 维度评估（有简历二次评估时优先显示简历评估的维度评分）
            dim_scores = c.get('resume_eval_dimension_scores') or c.get('llm_dimension_scores') or {}
            if dim_scores:
                from llm_eval import _DIMENSION_LABELS
                lines.append("")
                lines.append("  维度评估：")
                for key in ('skill_depth', 'experience_quality', 'industry_fit', 'growth_potential'):
                    val = dim_scores.get(key)
                    if val is None:
                        continue
                    label = _DIMENSION_LABELS.get(key, key)
                    filled = round(val / 10 * 8)
                    bar = "█" * filled + "░" * (8 - filled)
                    lines.append(f"    {label}：{val}/10 {bar}")
        else:
            lines.append("【AI 一次评估】未启用")

        # 二次评估（基于导入简历）
        if c.get('resume_eval_adjustment') is not None:
            lines.append("")
            lines.append("【AI 二次评估（简历）】")
            r_adj = c.get('resume_eval_adjustment', 0)
            r_sign = "+" if r_adj > 0 else ""
            lines.append(f"  调整值：{r_sign}{r_adj}")
            lines.append(f"  评估时间：{c.get('resume_eval_at', '—')}")
            lines.append(f"  评估模型：{c.get('resume_eval_model', '未知')}")
            r_reason = c.get('resume_eval_reason', '无').replace('\n', ' ').replace('\r', '').strip()
            lines.append(f"  评估理由：")
            lines.append(f"    {r_reason}")
            if c.get('resume_file'):
                lines.append(f"  简历文件：{os.path.basename(c.get('resume_file', ''))}")
            if c.get('resume_imported_at'):
                lines.append(f"  导入时间：{c.get('resume_imported_at')}")

        # 技能匹配详情
        skill_matches = c.get('skill_matches', [])
        if skill_matches:
            lines.append("")
            ratio = c.get('skill_match_ratio', '')
            lines.append(f"【技能匹配详情 {ratio}】")
            for sm in skill_matches:
                if isinstance(sm, dict):
                    sname = sm.get('name', '')
                    sweight = sm.get('weight', 1)
                    lines.append(f"  ✓ {sname}（权重{sweight}）")
                else:
                    lines.append(f"  ✓ {sm}")

        structured_summary: dict[str, list[str]] = {
            "教育经历": [],
            "工作经历": [],
            "工作职责": [],
            "技能标签": [],
        }

        # API 结构化画像优先
        if api_profile:
            for edu in (api_profile.get('educations') or []):
                parts = [edu.get(k, '') for k in ('school', 'major', 'degree')]
                parts = [p for p in parts if p]
                if parts:
                    structured_summary["教育经历"].append(" ".join(parts))
            for work in (api_profile.get('works') or []):
                parts = [work.get(k, '') for k in ('company', 'position', 'category', 'start', 'end')]
                parts = [p for p in parts if p]
                if parts:
                    structured_summary["工作经历"].append(" ".join(parts))
                resp = work.get('responsibility', '')
                if resp:
                    structured_summary["工作职责"].append(resp)
                skills = work.get('skills') or []
                if skills:
                    structured_summary["技能标签"].append("、".join(skills))
            # 个人优势
            personal = api_profile.get('personal_summary', '')
            if personal:
                structured_summary.setdefault("个人优势", []).append(personal)
        else:
            for sline in summary.split('\n'):
                text = sline.strip()
                for label in structured_summary:
                    prefix = f"{label}："
                    if text.startswith(prefix):
                        value = text[len(prefix):].strip()
                        if value:
                            structured_summary[label].append(value)
                        break

        if any(structured_summary.values()):
            section_titles = {
                "教育经历": "【教育经历】",
                "工作经历": "【工作经历】",
                "工作职责": "【工作职责】",
                "技能标签": "【技能标签】",
                "个人优势": "【个人优势】",
            }
            for label in ("教育经历", "工作经历", "工作职责", "技能标签", "个人优势"):
                items = structured_summary.get(label) or []
                if not items:
                    continue
                lines.append("")
                lines.append(section_titles[label])
                for idx, item in enumerate(items, 1):
                    if label in ("工作职责", "技能标签", "个人优势"):
                        lines.append(f"  {idx}. {item}")
                    else:
                        lines.append(f"  - {item}")

        # 候选人摘要
        if summary:
            lines.append("")
            lines.append("【候选人摘要】")
            for sline in summary.split('\n'):
                lines.append(f"  {sline}")

        return '\n'.join(lines)

    def _show_candidate_detail(self, item):
        """显示候选人详情"""
        try:
            values = self.result_tree.item(item, 'values')
            if not values:
                return

            # 创建详情窗口
            detail_window = tk.Toplevel(self.root)
            detail_window.title("候选人详情")
            detail_window.transient(self.root)
            detail_window.grab_set()
            detail_window.withdraw()

            # 标题
            title = f"姓名：{values[0]} | 匹配分：{values[4]} | {values[6]}"
            ttk.Label(detail_window, text=title, font=(FONT_FAMILY, 16), foreground=self.colors['primary']).pack(pady=15)

            # 详情文本
            text_widget = tk.Text(detail_window, wrap='char', font=(FONT_FAMILY, 14))
            text_widget.pack(fill='both', expand=True, padx=20, pady=10)
            self.bind_text_context_menu(text_widget, editable=False)

            # 查找对应候选人数据
            for i, c in enumerate(self.result_tree_data):
                if c.get('name') == values[0]:
                    detail_text = self._format_candidate_detail(c)
                    text_widget.insert('1.0', detail_text)
                    break

            _place_window_centered(detail_window, 1000, 880, parent=self.root)
            detail_window.deiconify()

        except Exception as e:
            messagebox.showerror("错误", f"查看详情失败：{e}")

    def _greet_single_candidate(self, item, candidate=None, parent=None, tree=None, tree_item=None):
        """对单个候选人打招呼（在后台线程执行）"""
        _parent = parent or self.root
        _tree = tree or self.result_tree
        _tree_item = tree_item if tree_item is not None else item

        if candidate is None:
            values = self.result_tree.item(item, 'values')
            if not values:
                return
            name = values[0]
            score = values[4]
            if hasattr(self, 'result_tree_data'):
                for c in self.result_tree_data:
                    if c.get('name') == name and str(c.get('match_score', '')) == str(score):
                        candidate = c
                        break
        else:
            name = candidate.get('name', '')
            score = candidate.get('match_score', 0)

        if not candidate:
            messagebox.showwarning("警告", f"未找到候选人 {name} 的数据", parent=_parent)
            return

        geek_id = candidate.get('geek_id')
        if not geek_id:
            messagebox.showwarning("警告", f"未找到候选人 {name} 的数据", parent=_parent)
            return

        # 确认操作
        job_name = candidate.get('job_name', '未知岗位')
        risk_text = ""
        if candidate.get('manual_review_required'):
            risk_flags = candidate.get('risk_flags') or []
            risk_text = "\n\n风险提示：\n" + "\n".join(f"- {flag}" for flag in risk_flags)
            risk_text += "\n\n该候选人已被自动流程跳过。继续操作视为人工确认后手动打招呼。"
        greet_hint = self._get_greet_confirmation_hint(candidate)
        if not messagebox.askyesno("确认打招呼",
                                   f"确定要向 {name}（{candidate.get('recommend_level', '')}，{score}分）打招呼吗？\n\n"
                                   f"岗位：{job_name}"
                                   f"{risk_text}\n\n"
                                   f"{greet_hint}",
                                   parent=_parent):
            return

        # 立即更新表格状态为"打招呼中..."，给用户即时反馈
        if _tree_item is not None:
            try:
                _tree.set(_tree_item, 'status', '打招呼中...')
                _tree.update_idletasks()
            except Exception:
                pass

        # 后台线程执行打招呼
        def greet_worker():
            connection_lock_acquired = False
            try:
                connection_lock_acquired = self._browser_connection_lock.acquire(timeout=8)
                if not connection_lock_acquired:
                    self.append_log("[打招呼] ❌ 浏览器正在执行其他连接操作，请稍后重试")
                    def _revert_busy():
                        if _tree_item is not None:
                            try:
                                _tree.set(_tree_item, 'status', self._format_candidate_status(candidate))
                            except Exception:
                                pass
                    _parent.after(0, _revert_busy)
                    return

                # 浏览器未连接时自动尝试重连（读取持久化端口）
                if not self.browser_page:
                    self.append_log(f"[打招呼] 浏览器未连接，正在尝试重连...")
                    def _revert_connecting():
                        if _tree_item is not None:
                            try:
                                _tree.set(_tree_item, 'status', self._format_candidate_status(candidate))
                            except Exception:
                                pass
                    if not self._try_reconnect_browser():
                        self.append_log(f"[打招呼] ❌ 浏览器重连失败，请先在「运行控制」页连接浏览器")
                        _parent.after(0, _revert_connecting)
                        _parent.after(0, lambda: messagebox.showwarning(
                            "浏览器未连接",
                            "无法连接到 Chrome 浏览器。\n请切换到「运行控制」页点击「检测/连接浏览器」。",
                            parent=_parent))
                        return
                    self.append_log(f"[打招呼] ✅ 浏览器重连成功")

                # 检查 page 连接是否还活着（标签页可能已关闭或切换导致引用失效）
                try:
                    self.browser_page.run_js('return 1')
                except Exception:
                    self.append_log(f"[打招呼] 浏览器连接已断开，正在尝试重连...")
                    def _revert_stale():
                        if _tree_item is not None:
                            try:
                                _tree.set(_tree_item, 'status', self._format_candidate_status(candidate))
                            except Exception:
                                pass
                    if not self._try_reconnect_browser():
                        self.append_log(f"[打招呼] ❌ 浏览器重连失败，请先在「运行控制」页连接浏览器")
                        _parent.after(0, _revert_stale)
                        _parent.after(0, lambda: messagebox.showwarning(
                            "浏览器连接断开",
                            "浏览器连接已断开且无法自动重连。\n请切换到「运行控制」页点击「检测/连接浏览器」。",
                            parent=_parent))
                        return
                    self.append_log(f"[打招呼] ✅ 浏览器重连成功")

                from bossmaster import send_greeting_on_list_page, send_greeting_with_context
                self.append_log(f"[打招呼] 正在向 {name} 打招呼...")

                def captcha_callback(detail):
                    result = [False]
                    done = threading.Event()

                    def show_dialog():
                        answer = messagebox.askyesno(
                            "检测到安全验证弹窗",
                            f"程序检测到安全验证弹窗\n（{detail}）\n\n"
                            "请在浏览器中手动完成验证。\n\n"
                            "点击「是」继续等待验证完成\n"
                            "点击「否」停止当前操作",
                            parent=_parent,
                        )
                        result[0] = answer
                        done.set()

                    _parent.after(0, show_dialog)
                    while not done.is_set():
                        if self.stop_event.is_set():
                            result[0] = False
                            done.set()
                            break
                        done.wait(timeout=0.5)
                    return result[0]

                greet_context = candidate.get('greet_context') or {}
                greet_method = "manual_list"
                if greet_context.get('chat_start'):
                    self.append_log(f"[打招呼] 使用已保存上下文发送，不依赖推荐牛人页面")
                    success, msg = send_greeting_with_context(
                        self.browser_page, greet_context, stop_event=self.stop_event,
                        captcha_callback=captcha_callback
                    )
                    if success:
                        greet_method = "manual_context"
                    if not success:
                        self.append_log(f"[打招呼] 上下文直接发送失败（{msg}），尝试回退到推荐列表按钮")
                        # 回退路径需要浏览器在对应岗位的推荐牛人页面，提示用户切换
                        ack_done = threading.Event()
                        def _ask_switch_page():
                            messagebox.showinfo(
                                "直接发送失败",
                                f"向 {name} 直接发送打招呼未成功（{msg}）。\n\n"
                                f"接下来将尝试从推荐列表页面发送，\n"
                                f"请确认浏览器已打开「{job_name}」的推荐牛人页面。\n\n"
                                f"点击「确定」继续尝试。",
                                parent=_parent,
                            )
                            ack_done.set()
                        _parent.after(0, _ask_switch_page)
                        ack_done.wait(timeout=30)
                        if self.stop_event.is_set():
                            return
                        success, msg = send_greeting_on_list_page(
                            self.browser_page, geek_id, stop_event=self.stop_event,
                            captcha_callback=captcha_callback
                        )
                else:
                    success, msg = send_greeting_on_list_page(
                        self.browser_page, geek_id, stop_event=self.stop_event,
                        captcha_callback=captcha_callback
                    )
                if success is None:
                    self.append_log(f"[打招呼] ⚠️ {name} 待确认：{msg}")
                    persist_candidate_greeting_pending(candidate, msg)
                    if _tree_item is not None:
                        _parent.after(
                            0,
                            lambda: self._safe_tree_set(
                                _tree,
                                _tree_item,
                                'status',
                                self._format_candidate_status(candidate),
                            ),
                        )
                    _parent.after(0, lambda: messagebox.showwarning(
                        "发送结果待确认",
                        f"{name} 的打招呼操作已点击，但页面没有返回明确成功状态。\n\n"
                        "程序未将其标记为已沟通，请先在 BOSS 沟通列表核实，避免重复发送。",
                        parent=_parent))
                elif success:
                    self.append_log(f"[打招呼] ✅ {name} — {msg}")
                    persisted = self._update_greet_status(
                        candidate, greet_method
                    )
                    if persisted:
                        # 手动打招呼成功 → 视为人工确认，清除标记
                        if candidate.get('manual_review_required'):
                            self._clear_manual_review(candidate.get('geek_id'))
                            candidate['manual_review_required'] = False
                            candidate['qualification_status'] = 'qualified'
                        self._regenerate_excel()
                    else:
                        self.append_log(
                            f"[打招呼] ⚠️ {name} 已发送成功，但本地状态保存失败"
                        )
                        _parent.after(0, lambda: messagebox.showerror(
                            "本地保存失败",
                            f"{name} 已在 BOSS 直聘发送成功，但本地状态未能保存。\n"
                            "请勿重复发送，并检查 candidates_all.json。",
                            parent=_parent,
                        ))
                    # 刷新结果页和首页统计
                    _parent.after(0, self.refresh_results)
                    _parent.after(0, self.refresh_home_stats)
                else:
                    self.append_log(f"[打招呼] ❌ {name} 失败：{msg}")
                    # 恢复表格状态（item 可能已被刷新删除，需 try/except）
                    def _revert_status():
                        if _tree_item is not None:
                            try:
                                _tree.set(_tree_item, 'status', self._format_candidate_status(candidate))
                            except Exception:
                                pass
                    _parent.after(0, _revert_status)
                    # 沟通次数上限
                    if "上限" in msg or "次数" in msg:
                        _parent.after(0, lambda: messagebox.showwarning(
                            "沟通次数已达上限",
                            "BOSS 直聘今日沟通次数已用完，请明天再试。",
                            parent=_parent))
            except Exception as e:
                self.append_log(f"[打招呼] ❌ {name} 异常：{e}")
                def _revert_status_exc():
                    if _tree_item is not None:
                        try:
                            _tree.set(_tree_item, 'status', self._format_candidate_status(candidate))
                        except Exception:
                            pass
                _parent.after(0, _revert_status_exc)
            finally:
                if connection_lock_acquired:
                    self._browser_connection_lock.release()

        threading.Thread(target=greet_worker, daemon=True).start()

    def _greet_selected_candidates(self, selection, filtered_ref, tree, parent=None):
        """批量打招呼（在后台线程执行）"""
        _parent = parent or self.root

        # 收集选中的候选人数据
        candidates_to_greet = []
        for sel_item in selection:
            sv = tree.item(sel_item, 'values')
            if not sv:
                continue
            name = sv[0]
            score = sv[4]
            for c in filtered_ref[0]:
                if c.get('name') == name and str(c.get('match_score', '')) == str(score):
                    # 跳过已打招呼的候选人
                    if not c.get('greet_sent', False):
                        candidates_to_greet.append((sel_item, c))
                    break

        if not candidates_to_greet:
            messagebox.showinfo("提示", "选中的候选人已全部打过招呼", parent=_parent)
            return

        # 分组：有 greet_context 和没有 greet_context 的候选人
        with_context = []
        without_context = []
        for item, c in candidates_to_greet:
            greet_context = c.get('greet_context') or {}
            if greet_context.get('chat_start'):
                with_context.append((item, c))
            else:
                without_context.append((item, c))

        # 构建确认信息
        names_with = [c.get('name', '') for _, c in with_context[:3]]
        names_without = [c.get('name', '') for _, c in without_context[:3]]

        confirm_parts = []
        if with_context:
            confirm_parts.append(f"✅ 可直接发送（{len(with_context)} 人）：{'、'.join(names_with)}{'...' if len(with_context) > 3 else ''}")
        if without_context:
            confirm_parts.append(f"⚠️ 需要页面支持（{len(without_context)} 人）：{'、'.join(names_without)}{'...' if len(without_context) > 3 else ''}")

        confirm_text = "批量打招呼将按以下分组执行：\n\n" + "\n\n".join(confirm_parts)
        if without_context:
            confirm_text += "\n\n⚠️ 没有打招呼上下文的候选人，需要浏览器在对应岗位的推荐牛人页面才能发送。\n如果页面不对，这些候选人会发送失败。"

        if not messagebox.askyesno("确认批量打招呼", confirm_text, parent=_parent):
            return

        # 后台线程执行批量打招呼
        def batch_greet_worker():
            connection_lock_acquired = False
            try:
                connection_lock_acquired = self._browser_connection_lock.acquire(timeout=8)
                if not connection_lock_acquired:
                    self.append_log("[批量打招呼] ❌ 浏览器正在执行其他连接操作，请稍后重试")
                    return

                # 浏览器未连接时自动尝试重连
                if not self.browser_page:
                    self.append_log("[批量打招呼] 浏览器未连接，正在尝试重连...")
                    if not self._try_reconnect_browser():
                        self.append_log("[批量打招呼] ❌ 浏览器重连失败，请先在「运行控制」页连接浏览器")
                        _parent.after(0, lambda: messagebox.showwarning(
                            "浏览器未连接",
                            "无法连接到 Chrome 浏览器。\n请切换到「运行控制」页点击「检测/连接浏览器」。",
                            parent=_parent))
                        return
                    self.append_log("[批量打招呼] ✅ 浏览器重连成功")

                # 检查 page 连接是否还活着
                try:
                    self.browser_page.run_js('return 1')
                except Exception:
                    self.append_log("[批量打招呼] 浏览器连接已断开，正在尝试重连...")
                    if not self._try_reconnect_browser():
                        self.append_log("[批量打招呼] ❌ 浏览器重连失败，请先在「运行控制」页连接浏览器")
                        _parent.after(0, lambda: messagebox.showwarning(
                            "浏览器连接断开",
                            "浏览器连接已断开且无法自动重连。\n请切换到「运行控制」页点击「检测/连接浏览器」。",
                            parent=_parent))
                        return
                    self.append_log("[批量打招呼] ✅ 浏览器重连成功")

                from bossmaster import send_greeting_on_list_page, send_greeting_with_context

                success_count = 0
                fail_count = 0
                skip_count = 0
                consecutive_uncertain = 0

                # 先处理有 greet_context 的候选人（稳定，不依赖页面）
                if with_context:
                    self.append_log(f"[批量打招呼] 处理 {len(with_context)} 个有上下文的候选人...")
                    for tree_item, candidate in with_context:
                        if self.stop_event.is_set():
                            self.append_log("[批量打招呼] 用户停止操作")
                            break

                        name = candidate.get('name', '')
                        geek_id = candidate.get('geek_id')

                        if not geek_id:
                            self.append_log(f"[批量打招呼] ⚠️ {name} 缺少 geek_id，跳过")
                            skip_count += 1
                            continue

                        # 更新表格状态为"打招呼中..."
                        _parent.after(0, lambda t=tree_item: self._safe_tree_set(tree, t, 'status', '打招呼中...'))

                        self.append_log(f"[批量打招呼] 正在向 {name} 打招呼（使用上下文）...")

                        def captcha_callback(detail):
                            result = [False]
                            done = threading.Event()

                            def show_dialog():
                                answer = messagebox.askyesno(
                                    "检测到安全验证弹窗",
                                    f"程序检测到安全验证弹窗\n（{detail}）\n\n"
                                    "请在浏览器中手动完成验证。\n\n"
                                    "点击「是」继续等待验证完成\n"
                                    "点击「否」停止当前操作",
                                    parent=_parent,
                                )
                                result[0] = answer
                                done.set()

                            _parent.after(0, show_dialog)
                            while not done.is_set():
                                if self.stop_event.is_set():
                                    result[0] = False
                                    done.set()
                                    break
                                done.wait(timeout=0.5)
                            return result[0]

                        greet_context = candidate.get('greet_context') or {}
                        success, msg = send_greeting_with_context(
                            self.browser_page, greet_context, stop_event=self.stop_event,
                            captcha_callback=captcha_callback
                        )

                        if success:
                            self.append_log(f"[批量打招呼] ✅ {name} — {msg}")
                            persisted = self._update_greet_status(candidate, "manual_context")
                            if persisted:
                                if candidate.get('manual_review_required'):
                                    self._clear_manual_review(candidate.get('geek_id'))
                                    candidate['manual_review_required'] = False
                                    candidate['qualification_status'] = 'qualified'
                                success_count += 1
                            else:
                                self.append_log(f"[批量打招呼] ⚠️ {name} 已发送成功，但本地状态保存失败")
                                success_count += 1
                            # 更新表格状态
                            _parent.after(0, lambda t=tree_item, c=candidate: self._safe_tree_set(tree, t, 'status', self._format_candidate_status(c)))
                        else:
                            self.append_log(f"[批量打招呼] ❌ {name} 失败：{msg}")
                            fail_count += 1
                            # 恢复表格状态
                            _parent.after(0, lambda t=tree_item, c=candidate: self._safe_tree_set(tree, t, 'status', self._format_candidate_status(c)))
                            # 沟通次数上限时停止
                            if "上限" in msg or "次数" in msg:
                                self.append_log("[批量打招呼] 沟通次数已达上限，停止批量打招呼")
                                _parent.after(0, lambda: messagebox.showwarning(
                                    "沟通次数已达上限",
                                    "BOSS 直聘今日沟通次数已用完，请明天再试。",
                                    parent=_parent))
                                break

                        # 打招呼间隔，避免触发风控
                        if self.stop_event.is_set():
                            break
                        import random
                        time.sleep(random.uniform(2, 4))

                # 再处理没有 greet_context 的候选人（需要页面支持）
                if without_context and not self.stop_event.is_set():
                    self.append_log(f"[批量打招呼] 处理 {len(without_context)} 个需要页面支持的候选人...")

                    # 弹出提示框，让用户确认浏览器是否在正确的页面上
                    page_confirm_done = threading.Event()
                    page_confirm_result = [False]

                    def show_page_confirm():
                        job_names = list(set(c.get('job_name', '未知岗位') for _, c in without_context))
                        job_text = "、".join(job_names[:3])
                        if len(job_names) > 3:
                            job_text += "等"

                        answer = messagebox.askyesno(
                            "需要切换页面",
                            f"接下来需要处理 {len(without_context)} 个没有打招呼上下文的候选人。\n\n"
                            f"这些候选人需要浏览器在对应岗位的推荐牛人页面才能发送打招呼。\n\n"
                            f"涉及岗位：{job_text}\n\n"
                            f"请确认浏览器已打开对应的推荐牛人页面。\n\n"
                            f"点击「是」继续执行\n"
                            f"点击「否」跳过这些候选人",
                            parent=_parent,
                        )
                        page_confirm_result[0] = answer
                        page_confirm_done.set()

                    _parent.after(0, show_page_confirm)
                    page_confirm_done.wait(timeout=30)

                    if not page_confirm_result[0]:
                        self.append_log("[批量打招呼] 用户选择跳过需要页面支持的候选人")
                        skip_count += len(without_context)
                    else:
                        for tree_item, candidate in without_context:
                            if self.stop_event.is_set():
                                self.append_log("[批量打招呼] 用户停止操作")
                                break

                            name = candidate.get('name', '')
                            geek_id = candidate.get('geek_id')
                            job_name = candidate.get('job_name', '未知岗位')

                            if not geek_id:
                                self.append_log(f"[批量打招呼] ⚠️ {name} 缺少 geek_id，跳过")
                                skip_count += 1
                                continue

                            # 更新表格状态为"打招呼中..."
                            _parent.after(0, lambda t=tree_item: self._safe_tree_set(tree, t, 'status', '打招呼中...'))

                            self.append_log(f"[批量打招呼] 正在向 {name} 打招呼（需要页面支持）...")

                            def captcha_callback2(detail):
                                result = [False]
                                done = threading.Event()

                                def show_dialog():
                                    answer = messagebox.askyesno(
                                        "检测到安全验证弹窗",
                                        f"程序检测到安全验证弹窗\n（{detail}）\n\n"
                                        "请在浏览器中手动完成验证。\n\n"
                                        "点击「是」继续等待验证完成\n"
                                        "点击「否」停止当前操作",
                                        parent=_parent,
                                    )
                                    result[0] = answer
                                    done.set()

                                _parent.after(0, show_dialog)
                                while not done.is_set():
                                    if self.stop_event.is_set():
                                        result[0] = False
                                        done.set()
                                        break
                                    done.wait(timeout=0.5)
                                return result[0]

                            success, msg = send_greeting_on_list_page(
                                self.browser_page, geek_id, stop_event=self.stop_event,
                                captcha_callback=captcha_callback2
                            )

                            if success is None:
                                self.append_log(f"[批量打招呼] ⚠️ {name} 待确认：{msg}")
                                skip_count += 1
                                persist_candidate_greeting_pending(candidate, msg)
                                consecutive_uncertain += 1
                                _parent.after(0, lambda t=tree_item, c=candidate: self._safe_tree_set(tree, t, 'status', self._format_candidate_status(c)))
                                if consecutive_uncertain >= GREET_UNCERTAIN_LIMIT:
                                    self.append_log(
                                        f"[批量打招呼] 连续 {consecutive_uncertain} 人发送结果待确认，"
                                        "停止批量打招呼并请人工核实"
                                    )
                                    break
                                import random
                                time.sleep(random.uniform(2, 4))
                                continue
                            if success:
                                self.append_log(f"[批量打招呼] ✅ {name} — {msg}")
                                consecutive_uncertain = 0
                                persisted = self._update_greet_status(candidate, "manual_list")
                                if persisted:
                                    if candidate.get('manual_review_required'):
                                        self._clear_manual_review(candidate.get('geek_id'))
                                        candidate['manual_review_required'] = False
                                        candidate['qualification_status'] = 'qualified'
                                    success_count += 1
                                else:
                                    self.append_log(f"[批量打招呼] ⚠️ {name} 已发送成功，但本地状态保存失败")
                                    success_count += 1
                                # 更新表格状态
                                _parent.after(0, lambda t=tree_item, c=candidate: self._safe_tree_set(tree, t, 'status', self._format_candidate_status(c)))
                            else:
                                self.append_log(f"[批量打招呼] ❌ {name} 失败：{msg}")
                                fail_count += 1
                                consecutive_uncertain = 0
                                # 恢复表格状态
                                _parent.after(0, lambda t=tree_item, c=candidate: self._safe_tree_set(tree, t, 'status', self._format_candidate_status(c)))
                                # 沟通次数上限时停止
                                if "上限" in msg or "次数" in msg:
                                    self.append_log("[批量打招呼] 沟通次数已达上限，停止批量打招呼")
                                    _parent.after(0, lambda: messagebox.showwarning(
                                        "沟通次数已达上限",
                                        "BOSS 直聘今日沟通次数已用完，请明天再试。",
                                        parent=_parent))
                                    break

                            # 打招呼间隔，避免触发风控
                            if self.stop_event.is_set():
                                break
                            import random
                            time.sleep(random.uniform(2, 4))

                # 完成后刷新结果
                _parent.after(0, self.refresh_results)
                _parent.after(0, self.refresh_home_stats)
                self.append_log(f"[批量打招呼] 完成：成功 {success_count} 人，失败 {fail_count} 人，跳过 {skip_count} 人")

            except Exception as e:
                self.append_log(f"[批量打招呼] ❌ 异常：{e}")
            finally:
                if connection_lock_acquired:
                    self._browser_connection_lock.release()

        threading.Thread(target=batch_greet_worker, daemon=True).start()

    def _safe_tree_set(self, tree, item, column, value):
        """安全设置 Treeview 单元格值（忽略已删除的 item）"""
        try:
            tree.set(item, column, value)
        except Exception:
            pass

    def _try_reconnect_browser(self) -> bool:
        """尝试重连浏览器（读取持久化端口，不启动新 Chrome）

        用于筛选结果页打招呼等场景：用户可能没去过运行控制页，
        但 Chrome 已经在运行（上次扫描启动的）。

        Returns:
            True 表示连接成功，self.browser_page 已赋值
        """
        if self.standalone_education:
            return False
        import socket
        try:
            # 读取持久化端口
            addr = getattr(self, 'browser_address', None)
            if not addr:
                try:
                    saved_port = CHROME_DEBUG_PORT_FILE.read_text(encoding='utf-8').strip()
                    if saved_port.isdigit():
                        addr = f'127.0.0.1:{saved_port}'
                except OSError:
                    pass
            if not addr:
                addr = '127.0.0.1:9222'

            host, port = addr.rsplit(':', 1)
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            port_open = s.connect_ex((host, int(port))) == 0
            s.close()

            if not port_open:
                return False

            # 端口开放，尝试 DrissionPage 连接
            from DrissionPage import ChromiumPage, ChromiumOptions
            co = ChromiumOptions()
            co.set_address(f'{host}:{port}')
            page = ChromiumPage(co)

            self.browser_page = page
            self.browser_address = page.address
            self.browser_connected = True
            return True

        except Exception as e:
            self.append_log(f"[浏览器] 重连失败：{e}")
            self.browser_page = None
            self.browser_connected = False
            return False

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
            with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                candidates = json.load(f)
            export_to_excel(candidates, str(CANDIDATES_XLSX_PATH))
        except Exception as e:
            self.append_log(f"[Excel] 同步更新失败：{e}")

    def _remove_candidate(self, item):
        """移除选中候选人"""
        if messagebox.askyesno("确认删除", "确定要移除该候选人吗？"):
            try:
                values = self.result_tree.item(item, 'values')
                name = values[0]
                score = values[4]

                # 通过 name+score 精确定位候选人，获取 geek_id
                target_geek_id = None
                if hasattr(self, 'result_tree_data'):
                    for c in self.result_tree_data:
                        if c.get('name') == name and str(c.get('match_score', '')) == str(score):
                            target_geek_id = c.get('geek_id')
                            break

                if not target_geek_id:
                    messagebox.showerror("错误", f"未找到候选人：{name}")
                    return

                # 从数据中移除（用 geek_id 精确匹配，避免同名误删）
                if hasattr(self, 'result_tree_data'):
                    self.result_tree_data = [c for c in self.result_tree_data if c.get('geek_id') != target_geek_id]

                # 从 JSON 文件中移除
                if CANDIDATES_PATH.exists():
                    with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                        candidates = json.load(f)
                    candidates = [c for c in candidates if c.get('geek_id') != target_geek_id]
                    save_candidates_all(candidates, CANDIDATES_PATH)

                    # 从树中移除
                    self.result_tree.delete(item)

                    # 刷新统计
                    self.refresh_results()

                    messagebox.showinfo("成功", f"已移除：{name}")
            except Exception as e:
                messagebox.showerror("错误", f"移除失败：{e}")

    def _export_selected(self):
        """导出选中的候选人"""
        selection = self.result_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选择要导出的候选人")
            return

        # 获取选中项的数据
        selected_data = []
        for item in selection:
            values = self.result_tree.item(item, 'values')
            for c in self.result_tree_data:
                if c.get('name') == values[0]:
                    selected_data.append(c)
                    break

        if not selected_data:
            return

        # 导出到 Excel
        from bossmaster import export_to_excel
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
            export_to_excel(selected_data, file_path)
            messagebox.showinfo("成功", f"已导出 {len(selected_data)} 名候选人到：\n{file_path}")

    def export_excel(self):
        """导出 Excel"""
        try:
            from bossmaster import export_to_excel
            if CANDIDATES_PATH.exists():
                with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                    candidates = json.load(f)
                candidates = [c for c in candidates if not c.get('blacklisted')]

                # 构建文件名：岗位 + 日期范围
                job_name = self.result_job_var.get() if hasattr(self, 'result_job_var') else "全部岗位"
                start_str, end_str = self._get_result_date_filter() if hasattr(self, 'result_date_start_entry') else (None, None)
                if start_str and end_str:
                    date_part = f"{start_str}_{end_str}"
                elif start_str:
                    date_part = f"{start_str}起"
                elif end_str:
                    date_part = f"至{end_str}"
                else:
                    date_part = datetime.now().strftime('%Y%m%d')

                # 弹出文件保存对话框
                file_path = filedialog.asksaveasfilename(
                    title="保存 Excel 文件",
                    defaultextension=".xlsx",
                    filetypes=[("Excel 文件", "*.xlsx")],
                    initialfile=f"{job_name}_{date_part}.xlsx"
                )

                if file_path:
                    export_to_excel(candidates, file_path)
                    messagebox.showinfo("成功", f"Excel 文件已导出：{file_path}")
            else:
                messagebox.showwarning("警告", "没有候选人数据")
        except Exception as e:
            messagebox.showerror("错误", f"导出失败：{e}")

    def open_json(self):
        """打开 JSON 文件"""
        if CANDIDATES_PATH.exists():
            os.startfile(str(CANDIDATES_PATH))
        else:
            messagebox.showwarning("警告", "文件不存在")

    def clear_candidates(self):
        """清空候选人数据"""
        if not CANDIDATES_PATH.exists():
            messagebox.showinfo("提示", "暂无候选人数据")
            return

        # 读取当前岗位过滤条件
        selected_job = self.result_job_var.get() if hasattr(self, 'result_job_var') else "全部岗位"
        is_all_jobs = selected_job == "全部岗位"

        # 统计已打招呼人数
        greeted_count = 0
        try:
            with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                _candidates = json.load(f)
            if is_all_jobs:
                greeted_count = sum(1 for c in _candidates if c.get('greet_sent'))
            else:
                job_name = selected_job.replace(" ", "")
                greeted_count = sum(1 for c in _candidates if c.get('greet_sent') and c.get('job_name', '') == job_name)
        except (OSError, json.JSONDecodeError):
            pass

        # 构建确认对话框
        dialog = tk.Toplevel(self.root)
        dialog.title("清空候选人")
        dialog.transient(self.root)
        dialog.configure(background=self.colors['bg_main'])
        dialog.withdraw()

        _s = self.dpi_scale * self.zoom_factor
        dialog_fs = self.font_scale * 0.88
        dialog_width = max(460, int(460 * _s))
        dialog_height = max(300, int(300 * _s))
        self._center_window(dialog, dialog_width, dialog_height)

        # 配置大号 RadioButton/CheckButton 字体
        dialog_rb_font = (FONT_FAMILY, int(14 * dialog_fs))

        # 对话框内统一灰底样式
        _cd_style = ttk.Style()
        _cd_style.configure('ClearDialog.TLabel', background=self.colors['bg_main'])
        _cd_style.configure('ClearDialog.TFrame', background=self.colors['bg_main'])
        _cd_style.configure('ClearDialog.TRadiobutton', font=dialog_rb_font,
                        background=self.colors['bg_main'])
        _cd_style.configure('ClearDialog.TCheckbutton', font=dialog_rb_font,
                        background=self.colors['bg_main'])

        # 标题
        ttk.Label(dialog, text="清空候选人数据",
                  font=(FONT_FAMILY, int(16 * dialog_fs)),
                  foreground=self.colors['danger'],
                  style='ClearDialog.TLabel').pack(pady=(int(20 * _s), int(10 * _s)))

        # 选项
        choice_var = tk.StringVar(value="all" if is_all_jobs else "current")

        radio_frame = ttk.Frame(dialog, style='ClearDialog.TFrame')
        radio_frame.pack(fill="x", padx=int(30 * _s))

        rb_current = ttk.Radiobutton(radio_frame,
                                     text=f"清空当前岗位数据（{selected_job}）",
                                     variable=choice_var, value="current",
                                     style='ClearDialog.TRadiobutton')
        rb_current.pack(anchor="w", pady=int(5 * _s))
        if is_all_jobs:
            rb_current.config(state="disabled")

        rb_all = ttk.Radiobutton(radio_frame,
                                 text="清空全部数据（所有岗位）",
                                 variable=choice_var, value="all",
                                 style='ClearDialog.TRadiobutton')
        rb_all.pack(anchor="w", pady=int(5 * _s))

        # 分隔线
        ttk.Separator(dialog, orient="horizontal").pack(
            fill="x", padx=int(30 * _s),
            pady=(int(10 * _s), int(6 * _s)))

        # 保留已打招呼复选框
        keep_greeted_var = tk.BooleanVar(value=True)
        cb_frame = ttk.Frame(dialog, style='ClearDialog.TFrame')
        cb_frame.pack(fill="x", padx=int(30 * _s),
                       pady=(int(12 * _s), 0))
        cb_text = f"保留已打招呼的候选人（{greeted_count} 人）" if greeted_count > 0 else "保留已打招呼的候选人（无）"
        cb_greeted = ttk.Checkbutton(cb_frame, text=cb_text,
                                      variable=keep_greeted_var,
                                      style='ClearDialog.TCheckbutton')
        cb_greeted.pack(anchor="w")
        if greeted_count == 0:
            cb_greeted.config(state="disabled")
            keep_greeted_var.set(False)

        # 提示
        ttk.Label(dialog, text="操作前会自动备份；已屏蔽候选人会保留为黑名单",
                  font=(FONT_FAMILY, int(13 * dialog_fs)),
                  foreground=self.colors['text_muted'],
                  style='ClearDialog.TLabel').pack(pady=(int(12 * _s), 0))

        # 按钮
        btn_frame = ttk.Frame(dialog, style='ClearDialog.TFrame')
        btn_frame.pack(pady=int(15 * _s))

        def do_clear():
            choice = choice_var.get()
            keep_greeted = keep_greeted_var.get()
            dialog.destroy()

            confirm_msg = "确定要清空候选人数据吗？\n\n操作前会自动备份，但清空后不可恢复。"
            if keep_greeted and greeted_count > 0:
                confirm_msg = f"确定要清空候选人数据吗？\n\n已打招呼的 {greeted_count} 人将被保留。\n操作前会自动备份，但清空后不可恢复。"
            if not messagebox.askyesno("确认", confirm_msg):
                return

            try:
                # 备份
                backup_path = CANDIDATES_PATH.with_suffix('.json.bak')
                shutil.copy2(CANDIDATES_PATH, backup_path)
                self.append_log(f"已备份候选人数据到 {backup_path.name}")

                with open(CANDIDATES_PATH, 'r', encoding='utf-8') as f:
                    candidates = json.load(f)

                kept_count = 0
                blacklist_kept_count = 0

                if choice == "current":
                    # 清空当前岗位
                    job_name = selected_job.replace(" ", "")
                    other_jobs = [c for c in candidates if c.get('job_name', '') != job_name]
                    current_job = [c for c in candidates if c.get('job_name', '') == job_name]

                    if keep_greeted:
                        kept = [c for c in current_job if c.get('greet_sent') or c.get('blacklisted')]
                        removed_list = [c for c in current_job if not c.get('greet_sent') and not c.get('blacklisted')]
                        candidates = other_jobs + kept
                        kept_count = sum(1 for c in kept if c.get('greet_sent'))
                        blacklist_kept_count = sum(1 for c in kept if c.get('blacklisted'))
                    else:
                        kept = [c for c in current_job if c.get('blacklisted')]
                        removed_list = [c for c in current_job if not c.get('blacklisted')]
                        candidates = other_jobs + kept
                        blacklist_kept_count = len(kept)

                    removed = len(removed_list)

                    save_candidates_all(candidates, CANDIDATES_PATH)

                    log_msg = f"已清空岗位「{selected_job}」的 {removed} 条候选人数据"
                    info_msg = f"已清空 {removed} 条候选人数据"
                    if kept_count > 0:
                        log_msg += f"，保留 {kept_count} 条已打招呼记录"
                        info_msg += f"，保留 {kept_count} 条已打招呼记录"
                    if blacklist_kept_count > 0:
                        log_msg += f"，保留 {blacklist_kept_count} 条黑名单记录"
                        info_msg += f"，保留 {blacklist_kept_count} 条黑名单记录"
                    self.append_log(log_msg)
                    messagebox.showinfo("完成", info_msg)
                else:
                    # 清空全部
                    if keep_greeted:
                        kept = [c for c in candidates if c.get('greet_sent') or c.get('blacklisted')]
                        removed = len([c for c in candidates if not c.get('greet_sent') and not c.get('blacklisted')])
                        candidates = kept
                        kept_count = sum(1 for c in kept if c.get('greet_sent'))
                        blacklist_kept_count = sum(1 for c in kept if c.get('blacklisted'))
                    else:
                        kept = [c for c in candidates if c.get('blacklisted')]
                        removed = len(candidates) - len(kept)
                        candidates = kept
                        blacklist_kept_count = len(kept)

                    save_candidates_all(candidates, CANDIDATES_PATH)

                    log_msg = f"已清空全部 {removed} 条候选人数据"
                    info_msg = f"已清空全部 {removed} 条候选人数据"
                    if kept_count > 0:
                        log_msg += f"，保留 {kept_count} 条已打招呼记录"
                        info_msg += f"，保留 {kept_count} 条已打招呼记录"
                    if blacklist_kept_count > 0:
                        log_msg += f"，保留 {blacklist_kept_count} 条黑名单记录"
                        info_msg += f"，保留 {blacklist_kept_count} 条黑名单记录"
                    self.append_log(log_msg)
                    messagebox.showinfo("完成", info_msg)

                # 同步 Excel
                self._regenerate_excel()

                # 刷新所有相关页面
                self.refresh_results()
                self.refresh_home_stats()
                self.refresh_stats()

            except Exception as e:
                messagebox.showerror("错误", f"清空失败：{e}")

        icon_check = self.icons.button('check', self.colors['primary'])
        icon_close = self.icons.button('close', self.colors['text_secondary'])
        _pad = int(10 * _s)

        def _icon_btn(parent, icon, text, command):
            frame = tk.Frame(parent, bg=self.colors['bg_main'],
                           highlightbackground=self.colors['border'],
                           highlightthickness=1, cursor='hand2')
            tk.Label(frame, image=icon, bg=self.colors['bg_main']).pack(
                side='left', padx=(_pad, 2), pady=4, anchor='center')
            tk.Label(frame, text=text, bg=self.colors['bg_main'],
                    font=(FONT_FAMILY, int(13 * dialog_fs)), fg=self.colors['text_primary']).pack(
                side='left', padx=(2, _pad), pady=4, anchor='center')
            _children = [frame] + list(frame.winfo_children())
            def _on_enter(e, f=frame, ch=_children, c=self.colors['bg_hover']):
                for w in ch:
                    w.config(bg=c)
            def _on_leave(e, f=frame, ch=_children, c=self.colors['bg_main']):
                for w in ch:
                    w.config(bg=c)
            for widget in _children:
                widget.bind('<Enter>', _on_enter)
                widget.bind('<Leave>', _on_leave)
                widget.bind('<Button-1>', lambda e, cmd=command: cmd())
            return frame

        _icon_btn(btn_frame, icon_check, '确定', do_clear).pack(side='left', padx=_pad)
        _icon_btn(btn_frame, icon_close, '取消', dialog.destroy).pack(side='left', padx=_pad)

        dialog.deiconify()

    def show_help(self):
        """显示帮助"""
        help_text = """BOSS 简历筛选器 - 使用说明

1. 岗位配置：
   - 选择或新建岗位
   - 配置经验、学历、技能要求
   - 保存配置

2. 运行控制：
    - 设置 DOM 滚动轮次（深度扫描可提高到 50-200）
   - 选择打招呼等级
   - 点击"开始运行"

3. 筛选结果：
   - 查看候选人列表
   - 导出 Excel 文件

注意事项：
- 需要 Chrome 浏览器
- 程序启动后需手动导航到 BOSS 直聘推荐页面
- 定期备份 candidates_all.json 文件"""
        messagebox.showinfo("使用说明", help_text)

    def show_about(self):
        """显示关于弹窗"""
        gui_dialogs.show_about_dialog(self, __version__)

    def show_changelog(self):
        """显示更新日志（版本列表 + 详情分栏）"""
        gui_dialogs.show_changelog_dialog(self)


def main():
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
