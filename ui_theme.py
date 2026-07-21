"""统一设计令牌（Design Tokens）：全应用唯一的色彩、间距、圆角来源。

所有 UI 代码（gui_main / ui_messagebox / gui_dialogs / icons）必须从本模块取色，
禁止在业务代码中新增一次性色值。新增颜色先加到这里并命名。
"""

# ---------------------------------------------------------------------------
# 品牌与语义色
# ---------------------------------------------------------------------------
PRIMARY = "#1E88E5"          # 主蓝（品牌色）
PRIMARY_DARK = "#1565C0"     # 主蓝 hover
PRIMARY_DEEP = "#0D47A1"     # 主蓝 pressed
PRIMARY_LIGHT = "#64B5F6"    # 主蓝浅色（图表/强调）
PRIMARY_PALE = "#BBDEFB"     # 主蓝极浅（图标镜片等内部层次色）

SUCCESS = "#43A047"          # 成功绿
SUCCESS_LIGHT = "#81C784"
WARNING = "#FB8C00"          # 警告橙（仅图标/色块，不做正文文字）
WARNING_TEXT = "#C2410C"     # 警告文字（白底对比度达标）
DANGER = "#E53935"           # 危险红（仅图标/色块）
DANGER_TEXT = "#C62828"      # 危险文字（白底对比度达标）
DANGER_DEEP = "#B71C1C"      # 危险按钮 pressed
PURPLE = "#8E24AA"           # 强烈推荐
PENDING = "#546E7A"          # 待定蓝灰

# ---------------------------------------------------------------------------
# 中性色
# ---------------------------------------------------------------------------
BG_MAIN = "#F8F9FA"          # 页面主背景
BG_CARD = "#FFFFFF"          # 卡片背景
BG_INPUT = "#FAFAFA"         # 输入框背景
BG_SIDEBAR = "#2D3748"       # 侧边栏背景
BG_SIDEBAR_PILL = "#3D4C63"  # 侧边栏选中 pill 背景
BG_SIDEBAR_ZEBRA = "#243041" # 深色侧边栏斑马行（版本历史列表等）
BG_HOVER = "#EDF2F7"         # 悬停背景
BG_ZEBRA = "#F8FAFC"         # 表格斑马纹
BG_FOOTER = "#F7F8FA"        # 弹窗底栏

TEXT_PRIMARY = "#1A202C"     # 主文字
TEXT_SECONDARY = "#718096"   # 次要文字
TEXT_MUTED = "#6B7280"       # 弱化文字（白底对比度 ≥4.5）
TEXT_SIDEBAR = "#A0AEC0"     # 侧边栏文字
TEXT_SIDEBAR_ACTIVE = "#FFFFFF"
TEXT_SIDEBAR_SUBTITLE = "#94A3B8"
TEXT_SIDEBAR_VERSION = "#94A3B8"

BORDER = "#E2E8F0"           # 常规边框
BORDER_STRONG = "#CBD5E1"    # 按钮/输入框边框

# ---------------------------------------------------------------------------
# 表格行标签底色
# ---------------------------------------------------------------------------
BG_TREE_HIGH = "#E8F5E9"     # 强烈推荐行
BG_TREE_MID = "#FFF3E0"      # 待定行
BG_TREE_LOW = "#F5F5F5"      # 淘汰/屏蔽行

# ---------------------------------------------------------------------------
# 横幅（inline banner）背景
# ---------------------------------------------------------------------------
BANNER_INFO_BG = "#E3F2FD"
BANNER_WARNING_BG = "#FFF3E0"
BANNER_ERROR_BG = "#FDECEC"
BANNER_SUCCESS_BG = "#E8F5E9"

# ---------------------------------------------------------------------------
# Tooltip
# ---------------------------------------------------------------------------
TOOLTIP_BG = "#1F2937"       # 深色 tooltip 背景
TOOLTIP_FG = "#F9FAFB"       # 深色 tooltip 文字

# ---------------------------------------------------------------------------
# 状态灯（连接状态）
# ---------------------------------------------------------------------------
LAMP_OK = SUCCESS
LAMP_WARN = WARNING
LAMP_ERROR = DANGER
LAMP_OFF = "#C8CDD5"

# ---------------------------------------------------------------------------
# 字体（跨平台降级）
# ---------------------------------------------------------------------------
def get_font_family():
    """正文字体：Windows 用微软雅黑，macOS/Linux 降级到系统字体。"""
    import sys
    if sys.platform == 'win32':
        return 'Microsoft YaHei UI'
    elif sys.platform == 'darwin':
        return 'PingFang SC'
    return 'Helvetica'


def get_font_family_semibold():
    """Semibold 字体变体：macOS 无独立变体，配合 'bold' 字重使用。"""
    import sys
    if sys.platform == 'win32':
        return 'Microsoft YaHei UI Semibold'
    elif sys.platform == 'darwin':
        return 'PingFang SC'
    return 'Helvetica'


FONT_FAMILY = get_font_family()
FONT_FAMILY_SEMIBOLD = get_font_family_semibold()


# ---------------------------------------------------------------------------
# 间距阶梯（8pt 网格）
# ---------------------------------------------------------------------------
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24
SPACE_XXL = 32

# 圆角（自绘控件用）
RADIUS_SM = 4
RADIUS_MD = 6
RADIUS_LG = 8


def build_palette():
    """返回完整色板字典；gui_main 的 self.colors 键名保持兼容。"""
    return {
        "primary": PRIMARY,
        "primary_dark": PRIMARY_DARK,
        "primary_deep": PRIMARY_DEEP,
        "primary_light": PRIMARY_LIGHT,
        "success": SUCCESS,
        "success_light": SUCCESS_LIGHT,
        "warning": WARNING,
        "warning_text": WARNING_TEXT,
        "danger": DANGER,
        "danger_text": DANGER_TEXT,
        "danger_deep": DANGER_DEEP,
        "purple": PURPLE,
        "pending": PENDING,
        "bg_main": BG_MAIN,
        "bg_card": BG_CARD,
        "bg_input": BG_INPUT,
        "bg_sidebar": BG_SIDEBAR,
        "bg_sidebar_pill": BG_SIDEBAR_PILL,
        "bg_sidebar_zebra": BG_SIDEBAR_ZEBRA,
        "bg_tree_tag_high": BG_TREE_HIGH,
        "bg_tree_tag_mid": BG_TREE_MID,
        "bg_tree_tag_low": BG_TREE_LOW,
        "bg_hover": BG_HOVER,
        "bg_zebra": BG_ZEBRA,
        "bg_footer": BG_FOOTER,
        "text_primary": TEXT_PRIMARY,
        "text_secondary": TEXT_SECONDARY,
        "text_muted": TEXT_MUTED,
        "text_sidebar": TEXT_SIDEBAR,
        "text_sidebar_active": TEXT_SIDEBAR_ACTIVE,
        "text_sidebar_subtitle": TEXT_SIDEBAR_SUBTITLE,
        "text_sidebar_version": TEXT_SIDEBAR_VERSION,
        "border": BORDER,
        "border_strong": BORDER_STRONG,
        "banner_info_bg": BANNER_INFO_BG,
        "banner_warning_bg": BANNER_WARNING_BG,
        "banner_error_bg": BANNER_ERROR_BG,
        "banner_success_bg": BANNER_SUCCESS_BG,
        "tooltip_bg": TOOLTIP_BG,
        "tooltip_fg": TOOLTIP_FG,
        "lamp_ok": LAMP_OK,
        "lamp_warn": LAMP_WARN,
        "lamp_error": LAMP_ERROR,
        "lamp_off": LAMP_OFF,
    }
