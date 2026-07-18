"""
BOSS 简历筛选器 - 高清图标生成模块

使用 Pillow ImageDraw 在运行时程序化生成抗锯齿图标。
所有图标基于 24×24 逻辑坐标系，按实际像素尺寸等比缩放。
"""

import math
from typing import Dict, Tuple, Optional, Callable
from PIL import Image, ImageDraw, ImageTk

import ui_theme

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
STROKE_WIDTH = 2.0           # 基础描边宽度（缩放前）
ICON_SIZE_BUTTON = 24        # 按钮图标基础尺寸（px）
ICON_SIZE_NAV = 22           # 侧边栏导航图标基础尺寸（px）
ICON_SIZE_LOGO = 35          # Logo 图标基础尺寸（px）
ICON_SIZE_STAT = 40          # 统计卡片图标基础尺寸（px）

# 24x24 逻辑坐标系中的关键参考点
_LO = 2.5   # left offset（左边距）
_RO = 21.5  # right offset（右边距）
_TO = 2.5   # top offset（上边距）
_BO = 21.5  # bottom offset（下边距）
_CX = 12.0  # center x
_CY = 12.0  # center y


def _s(v: float, size_px: int) -> float:
    """将 24x24 逻辑坐标映射到实际像素坐标"""
    return v * size_px / 24.0


# ---------------------------------------------------------------------------
# 图标绘制函数 — 每个返回 PIL Image（RGBA）
# ---------------------------------------------------------------------------

def _eye(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 眼框：横向椭圆
    d.ellipse([_s(2, S), _s(6, S), _s(22, S), _s(18, S)], outline=fill, width=sw)
    # 瞳孔：中心圆
    r = _s(3, S)
    d.ellipse([_s(12, S) - r, _s(12, S) - r, _s(12, S) + r, _s(12, S) + r], fill=fill)
    return img


def _eye_off(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 眼框
    d.ellipse([_s(2, S), _s(6, S), _s(22, S), _s(18, S)], outline=fill, width=sw)
    # 瞳孔
    r = _s(3, S)
    d.ellipse([_s(12, S) - r, _s(12, S) - r, _s(12, S) + r, _s(12, S) + r], fill=fill)
    # 斜线
    d.line([_s(1, S), _s(1, S), _s(23, S), _s(23, S)], fill=fill, width=sw)
    return img


def _clipboard(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 主体矩形
    d.rounded_rectangle([_s(3, S), _s(6, S), _s(21, S), _s(22, S)],
                        radius=_s(1.5, S), outline=fill, width=sw)
    # 顶部小夹子
    d.rounded_rectangle([_s(8, S), _s(2, S), _s(16, S), _s(6, S)],
                        radius=_s(1, S), outline=fill, width=sw)
    return img


def _task_list(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """带勾选项的任务清单 — 今日待办。"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    d.rounded_rectangle(
        [_s(3, S), _s(2.5, S), _s(21, S), _s(21.5, S)],
        radius=_s(1.8, S), outline=fill, width=sw,
    )
    line_sw = max(1, sw - 1)
    for y in (7, 12, 17):
        d.line(
            [_s(6, S), _s(y, S), _s(7.4, S), _s(y + 1.4, S)],
            fill=fill, width=sw,
        )
        d.line(
            [_s(7.4, S), _s(y + 1.4, S), _s(9.5, S), _s(y - 1.5, S)],
            fill=fill, width=sw,
        )
        d.line(
            [_s(12, S), _s(y, S), _s(18, S), _s(y, S)],
            fill=fill, width=line_sw,
        )
    return img


def _candidate_review(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """候选人头像 + 放大镜 — 查看与复核。"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    d.ellipse(
        [_s(2.5, S), _s(2.5, S), _s(17.5, S), _s(17.5, S)],
        outline=fill, width=sw,
    )
    d.line(
        [_s(15.2, S), _s(15.2, S), _s(21.2, S), _s(21.2, S)],
        fill=fill, width=sw + 1,
    )
    head_r = _s(2.2, S)
    head_x, head_y = _s(10, S), _s(8.5, S)
    d.ellipse(
        [head_x - head_r, head_y - head_r, head_x + head_r, head_y + head_r],
        fill=fill,
    )
    d.arc(
        [_s(6, S), _s(10, S), _s(14, S), _s(16.5, S)],
        start=180, end=360, fill=fill, width=sw,
    )
    return img


def _health_shield(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """线框盾牌 + 医疗十字 — 候选人状态体检。"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    shield = [
        (_s(12, S), _s(2, S)),
        (_s(20.5, S), _s(5, S)),
        (_s(19.5, S), _s(15, S)),
        (_s(12, S), _s(22, S)),
        (_s(4.5, S), _s(15, S)),
        (_s(3.5, S), _s(5, S)),
    ]
    d.line(shield + [shield[0]], fill=fill, width=sw, joint='curve')
    cross_sw = sw + 1
    d.line(
        [_s(12, S), _s(7, S), _s(12, S), _s(17, S)],
        fill=fill, width=cross_sw,
    )
    d.line(
        [_s(7, S), _s(12, S), _s(17, S), _s(12, S)],
        fill=fill, width=cross_sw,
    )
    return img


def _ai_spark(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """主星芒 + 辅助闪光 — AI 评估。"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    main = [
        (_s(10, S), _s(2.5, S)),
        (_s(12, S), _s(8, S)),
        (_s(17.5, S), _s(10, S)),
        (_s(12, S), _s(12, S)),
        (_s(10, S), _s(17.5, S)),
        (_s(8, S), _s(12, S)),
        (_s(2.5, S), _s(10, S)),
        (_s(8, S), _s(8, S)),
    ]
    d.polygon(main, fill=fill)
    for cx, cy, radius in ((18.5, 5, 2.5), (18.5, 18.5, 2.2)):
        spark = [
            (_s(cx, S), _s(cy - radius, S)),
            (_s(cx + radius * 0.45, S), _s(cy - radius * 0.45, S)),
            (_s(cx + radius, S), _s(cy, S)),
            (_s(cx + radius * 0.45, S), _s(cy + radius * 0.45, S)),
            (_s(cx, S), _s(cy + radius, S)),
            (_s(cx - radius * 0.45, S), _s(cy + radius * 0.45, S)),
            (_s(cx - radius, S), _s(cy, S)),
            (_s(cx - radius * 0.45, S), _s(cy - radius * 0.45, S)),
        ]
        d.polygon(spark, fill=fill)
    return img


def _chart(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    w = _s(3.5, S)  # 柱宽
    gap = _s(2, S)  # 间距
    start_x = _s(3.5, S)
    base_y = _s(21, S)
    # 三根柱
    heights = [_s(8, S), _s(14, S), _s(10, S)]
    for i, h in enumerate(heights):
        x0 = start_x + i * (w + gap)
        d.rectangle([x0, base_y - h, x0 + w, base_y], fill=fill)
    # 基线
    d.line([_s(2, S), base_y, _s(22, S), base_y], fill=fill, width=sw)
    return img


def _gear(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    cx, cy = _s(_CX, S), _s(_CY, S)
    outer_r = _s(9, S)
    inner_r = _s(5.5, S)
    center_r = _s(2.5, S)
    # 外圈虚线 + 齿：绘制齿形
    for i in range(8):
        angle = math.radians(i * 45 - 90)
        # 齿
        ax = cx + (outer_r + _s(1.5, S)) * math.cos(angle)
        ay = cy + (outer_r + _s(1.5, S)) * math.sin(angle)
        bx = cx + (outer_r - _s(1, S)) * math.cos(angle)
        by = cy + (outer_r - _s(1, S)) * math.sin(angle)
        d.line([ax, ay, bx, by], fill=fill, width=sw + 1)
    # 外圈
    d.ellipse([cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r],
              outline=fill, width=sw)
    # 内圈
    d.ellipse([cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
              outline=fill, width=sw)
    # 中心圆
    d.ellipse([cx - center_r, cy - center_r, cx + center_r, cy + center_r],
              fill=fill)
    return img


def _plus(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    pad = _s(5, S)
    cx = S / 2
    d.line([cx, pad, cx, S - pad], fill=fill, width=sw)
    d.line([pad, cx, S - pad, cx], fill=fill, width=sw)
    return img


def _trash(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 盖子
    d.line([_s(3.5, S), _s(5, S), _s(20.5, S), _s(5, S)], fill=fill, width=sw + 1)
    # 盖子上把手
    d.line([_s(8, S), _s(3, S), _s(8, S), _s(5, S)], fill=fill, width=sw)
    d.line([_s(16, S), _s(3, S), _s(16, S), _s(5, S)], fill=fill, width=sw)
    d.line([_s(8, S), _s(3, S), _s(16, S), _s(3, S)], fill=fill, width=sw)
    # 桶身
    d.line([_s(5, S), _s(5, S), _s(5, S), _s(21, S)], fill=fill, width=sw)
    d.line([_s(19, S), _s(5, S), _s(19, S), _s(21, S)], fill=fill, width=sw)
    d.line([_s(4, S), _s(21, S), _s(20, S), _s(21, S)], fill=fill, width=sw)
    # 内部竖线
    lw = max(1, int(_s(1, S)))
    d.line([_s(8, S), _s(8, S), _s(8, S), _s(18, S)], fill=fill, width=lw)
    d.line([_s(12, S), _s(8, S), _s(12, S), _s(18, S)], fill=fill, width=lw)
    d.line([_s(16, S), _s(8, S), _s(16, S), _s(18, S)], fill=fill, width=lw)
    return img


def _search(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 镜片圆
    r = _s(7, S)
    cx, cy = _s(9, S), _s(9, S)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fill, width=sw)
    # 手柄
    handle_start = _s(14.5, S)
    handle_end = _s(21, S)
    d.line([handle_start, handle_start, handle_end, handle_end], fill=fill, width=sw + 1)
    return img


def _search_color(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """彩色放大镜 — BOSS 品牌 logo，填充版（超采样由 IconCache.get 统一处理）"""
    img = Image.new('RGBA', (size_px, size_px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    brand = '#1E88E5'       # 品牌蓝
    brand_dark = '#0D47A1'  # 深蓝（手柄）
    lens = '#BBDEFB'        # 浅蓝镜片
    highlight = '#E3F2FD'   # 高光

    S = size_px
    # 镜片中心 & 半径
    cx, cy = _s(9, S), _s(9, S)
    r_outer = _s(8.5, S)
    r_inner = _s(6, S)

    # 手柄：从镜片右下到右下角，圆头
    h_angle = math.radians(45)
    hx1 = cx + r_outer * math.cos(h_angle)
    hy1 = cy + r_outer * math.sin(h_angle)
    hx2 = _s(21, S)
    hy2 = _s(21, S)
    handle_w = _s(4, S)
    d.line([hx1, hy1, hx2, hy2], fill=brand_dark, width=int(handle_w))
    # 手柄末端圆头
    r_cap = handle_w / 2
    d.ellipse([hx2 - r_cap, hy2 - r_cap, hx2 + r_cap, hy2 + r_cap], fill=brand_dark)

    # 镜片：填充浅蓝 + 品牌蓝粗边框
    d.ellipse([cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer],
              fill=lens, outline=brand, width=int(_s(3.5, S)))

    # 高光反射弧（左上角，白色短弧）
    hi_r = _s(5, S)
    d.arc([cx - hi_r, cy - hi_r, cx + hi_r, cy + hi_r],
          start=200, end=280, fill=highlight, width=int(_s(2.5, S)))

    return img


def _pencil(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """铅笔 — 编辑/更新，水平构图，左尾右尖"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    top = _s(8, S)
    bot = _s(16, S)
    # 笔身：水平矩形（左→右）
    body = [(_s(3, S), top), (_s(17, S), top), (_s(17, S), bot), (_s(3, S), bot)]
    d.polygon(body, fill=fill, outline=fill, width=sw)
    # 笔尖：右侧三角
    tip = [(_s(17, S), top), (_s(21, S), _s(12, S)), (_s(17, S), bot)]
    d.polygon(tip, fill=fill)
    # 笔尾橡皮擦：左侧短横线
    d.line([(_s(3, S), top), (_s(3, S), bot)], fill=fill, width=sw + 1)
    return img


def _save(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 软盘外形
    d.rounded_rectangle([_s(3, S), _s(2, S), _s(21, S), _s(22, S)],
                        radius=_s(1.5, S), outline=fill, width=sw)
    # 上部凹槽
    d.rectangle([_s(7, S), _s(2, S), _s(17, S), _s(7, S)], fill=fill)
    # 底部标签区
    d.rectangle([_s(7, S), _s(15, S), _s(17, S), _s(21, S)], outline=fill, width=max(1, int(_s(1, S))))
    return img


def _refresh(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    cx, cy = _s(_CX, S), _s(_CY, S)
    r = _s(7, S)
    # 圆弧（约 300 度）
    d.arc([cx - r, cy - r, cx + r, cy + r], start=30, end=330,
          fill=fill, width=sw)
    # 箭头（在圆弧顶端）
    tip_x = cx + r * math.cos(math.radians(330))
    tip_y = cy + r * math.sin(math.radians(330))
    al = _s(3.5, S)
    d.line([tip_x - al, tip_y - al * 0.5, tip_x, tip_y], fill=fill, width=sw)
    d.line([tip_x - al, tip_y + al * 0.5, tip_x, tip_y], fill=fill, width=sw)
    return img


def _refresh_clean(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    cx, cy = _s(_CX, S), _s(_CY, S)
    r = _s(7.4, S)
    d.arc([cx - r, cy - r, cx + r, cy + r], start=42, end=332, fill=fill, width=sw + 1)
    al = _s(4.0, S)
    tip_x = cx + r * math.cos(math.radians(332))
    tip_y = cy + r * math.sin(math.radians(332))
    d.polygon([
        (tip_x, tip_y),
        (tip_x - al * 0.95, tip_y - al * 0.22),
        (tip_x - al * 0.28, tip_y + al * 0.95),
    ], fill=fill)
    return img


def _import_icon(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 箭头向下
    ax = _s(12, S)
    d.line([ax, _s(5, S), ax, _s(19, S)], fill=fill, width=sw)
    # 箭头尖
    al = _s(4, S)
    d.line([ax - al, _s(19, S) - al * 0.7, ax, _s(19, S)], fill=fill, width=sw)
    d.line([ax + al, _s(19, S) - al * 0.7, ax, _s(19, S)], fill=fill, width=sw)
    # 底部横线
    d.line([_s(5, S), _s(22, S), _s(19, S), _s(22, S)], fill=fill, width=sw)
    return img


def _export(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    ax = _s(12, S)
    # 箭头向上
    d.line([ax, _s(19, S), ax, _s(5, S)], fill=fill, width=sw)
    al = _s(4, S)
    d.line([ax - al, _s(5, S) + al * 0.7, ax, _s(5, S)], fill=fill, width=sw)
    d.line([ax + al, _s(5, S) + al * 0.7, ax, _s(5, S)], fill=fill, width=sw)
    # 底部横线
    d.line([_s(5, S), _s(22, S), _s(19, S), _s(22, S)], fill=fill, width=sw)
    return img


def _folder(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 文件夹主体
    d.rectangle([_s(3, S), _s(7, S), _s(21, S), _s(21, S)],
                outline=fill, width=sw)
    # 顶部标签
    d.line([_s(3, S), _s(7, S), _s(9, S), _s(7, S)], fill=fill, width=sw)
    d.line([_s(9, S), _s(7, S), _s(9, S), _s(4, S)], fill=fill, width=sw)
    d.line([_s(9, S), _s(4, S), _s(15, S), _s(4, S)], fill=fill, width=sw)
    d.line([_s(15, S), _s(4, S), _s(15, S), _s(7, S)], fill=fill, width=sw)
    return img


def _home(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 三角形屋顶
    d.line([_s(12, S), _s(3, S), _s(2, S), _s(11, S)], fill=fill, width=sw)
    d.line([_s(12, S), _s(3, S), _s(22, S), _s(11, S)], fill=fill, width=sw)
    # 墙壁
    d.rectangle([_s(4, S), _s(11, S), _s(20, S), _s(21, S)],
                outline=fill, width=sw)
    # 门
    d.rectangle([_s(9, S), _s(14, S), _s(15, S), _s(21, S)],
                outline=fill, width=max(1, int(_s(1.2, S))))
    return img


def _people(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """双人半身剪影 — 👥 风格，累计候选人"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    r = _s(3.5, S)
    # 左人：头
    d.ellipse([_s(5, S), _s(3.5, S), _s(12, S), _s(10.5, S)], outline=fill, width=sw)
    # 左人：肩/半身弧线
    d.arc([_s(2, S), _s(9, S), _s(15, S), _s(22, S)], start=180, end=360, fill=fill, width=sw)
    # 右人：头（在前）
    d.ellipse([_s(12, S), _s(3.5, S), _s(19, S), _s(10.5, S)], outline=fill, width=sw)
    # 右人：肩/半身弧线
    d.arc([_s(9, S), _s(9, S), _s(22, S), _s(22, S)], start=180, end=360, fill=fill, width=sw)
    return img


def _trophy(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 杯耳（两侧）
    d.arc([_s(1, S), _s(7, S), _s(7, S), _s(15, S)], start=90, end=270,
          fill=fill, width=sw)
    d.arc([_s(17, S), _s(7, S), _s(23, S), _s(15, S)], start=270, end=90,
          fill=fill, width=sw)
    # 杯身
    d.line([_s(5, S), _s(6, S), _s(5, S), _s(16, S)], fill=fill, width=sw)
    d.line([_s(19, S), _s(6, S), _s(19, S), _s(16, S)], fill=fill, width=sw)
    # 杯底（梯形底边）
    d.line([_s(5, S), _s(16, S), _s(7, S), _s(19, S)], fill=fill, width=sw)
    d.line([_s(19, S), _s(16, S), _s(17, S), _s(19, S)], fill=fill, width=sw)
    # 底座
    d.line([_s(7, S), _s(19, S), _s(17, S), _s(19, S)], fill=fill, width=sw + 1)
    d.rectangle([_s(8, S), _s(19, S), _s(16, S), _s(21, S)],
                outline=fill, width=sw)
    return img


def _arrow_up(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """↑ 上升箭头图标 — 24×24"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 箭头三角形（顶部）
    triangle = [(_s(12, S), _s(3, S)), (_s(5, S), _s(11, S)), (_s(19, S), _s(11, S))]
    d.polygon(triangle, outline=fill, fill=fill, width=sw)
    # 箭杆
    d.rectangle([_s(10, S), _s(10, S), _s(14, S), _s(21, S)], fill=fill)
    return img


def _arrow_down(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """↓ 下降箭头图标 — 24×24"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 箭头三角形（底部）
    triangle = [(_s(12, S), _s(21, S)), (_s(5, S), _s(13, S)), (_s(19, S), _s(13, S))]
    d.polygon(triangle, outline=fill, fill=fill, width=sw)
    # 箭杆
    d.rectangle([_s(10, S), _s(3, S), _s(14, S), _s(14, S)], fill=fill)
    return img


def _chevron_up(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """Minimal upward disclosure chevron."""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    d.line(
        [_s(6, S), _s(15, S), _s(12, S), _s(9, S), _s(18, S), _s(15, S)],
        fill=fill,
        width=max(sw, int(round(_s(2, S)))),
        joint='curve',
    )
    return img


def _chevron_down(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """Minimal downward disclosure chevron."""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    d.line(
        [_s(6, S), _s(9, S), _s(12, S), _s(15, S), _s(18, S), _s(9, S)],
        fill=fill,
        width=max(sw, int(round(_s(2, S)))),
        joint='curve',
    )
    return img


def _thumbs_up(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """👍 Lucide 图标 — 24×24 轮廓 + 拇指分割线"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 手部外轮廓（基于 Lucide thumbs-up SVG path 逐点转换）
    pts = [
        (15, 5.88),     # 拇指尖右侧
        (14, 10),       # 拇指右缘底部
        (19.83, 10),    # 手背顶部
        (21.75, 12.56), # 手背右上（弧线中点）
        (19.42, 20.56), # 手背右下（弧线中点）
        (17.5, 22),     # 底部右侧
        (4, 22),        # 底部中央
        (2, 20),        # 底部左侧（弧线中点）
        (2, 12),        # 左侧缘
        (4, 10),        # 左上角
        (6.76, 10),     # 拇指左缘底部
        (8.55, 8.89),   # 拇指左缘中部
        (12, 2),        # 拇指尖
    ]
    scaled = [(_s(x, S), _s(y, S)) for x, y in pts]
    d.polygon(scaled, outline=fill, width=sw)
    # 拇指与手掌分隔线（Lucide path 2: M7 10v12）
    d.line([_s(7, S), _s(10, S), _s(7, S), _s(22, S)], fill=fill, width=sw)
    return img


def _strong_recommend(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """点赞 + 强调光芒 — 强烈推荐，与普通推荐共用同一视觉语言。"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px

    # 点赞主体略向下移动，为顶部强调光芒留出空间。
    thumb_size = max(12, int(round(size_px * 0.95)))
    thumb_sw = max(1, int(round(sw * thumb_size / size_px)))
    thumb = _thumbs_up(thumb_size, fill, (0, 0, 0, 0), thumb_sw)
    img.alpha_composite(thumb, ((size_px - thumb_size) // 2, size_px - thumb_size + 2))

    # 三道短光芒表达“推荐升级”，线宽、圆角和其它轮廓图标保持一致。
    ray_sw = max(1, sw)
    rays = [
        (12, 0.5, 12, 3),
        (5.5, 2, 7.5, 4),
        (18.5, 2, 16.5, 4),
    ]
    for x1, y1, x2, y2 in rays:
        d.line(
            [_s(x1, S), _s(y1, S), _s(x2, S), _s(y2, S)],
            fill=fill,
            width=ray_sw,
        )
    return img


def _mail(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 矩形信封
    d.rectangle([_s(2, S), _s(5, S), _s(22, S), _s(19, S)],
                outline=fill, width=sw)
    # V 形封口
    d.line([_s(2, S), _s(5, S), _s(12, S), _s(13, S)], fill=fill, width=sw)
    d.line([_s(22, S), _s(5, S), _s(12, S), _s(13, S)], fill=fill, width=sw)
    return img


def _play(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 右三角
    points = [
        (_s(7, S), _s(5, S)),
        (_s(19, S), _s(12, S)),
        (_s(7, S), _s(19, S)),
    ]
    d.polygon([(p[0], p[1]) for p in points], fill=fill)
    return img


def _stop(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    pad = _s(5, S)
    d.rectangle([pad, pad, S - pad, S - pad], fill=fill)
    return img


def _star(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """五角星 — 星标/强烈推荐"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    cx, cy = _s(_CX, S), _s(_CY, S)
    outer_r = _s(9, S)
    inner_r = _s(3.5, S)
    points = []
    for i in range(5):
        ao = math.radians(-90 + i * 72)
        ai = math.radians(-90 + 36 + i * 72)
        points.append((cx + outer_r * math.cos(ao), cy + outer_r * math.sin(ao)))
        points.append((cx + inner_r * math.cos(ai), cy + inner_r * math.sin(ai)))
    d.polygon(points, outline=fill, width=sw)
    return img


def _hourglass(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """沙漏 — 待定/等待判断。"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px

    # 上下横梁。
    d.line([_s(5, S), _s(3, S), _s(19, S), _s(3, S)], fill=fill, width=sw + 1)
    d.line([_s(5, S), _s(21, S), _s(19, S), _s(21, S)], fill=fill, width=sw + 1)

    # 玻璃外框：上半部收束到中心，再向下展开。
    d.line([_s(6, S), _s(4, S), _s(7, S), _s(8, S), _s(12, S), _s(12, S)],
           fill=fill, width=sw, joint='curve')
    d.line([_s(18, S), _s(4, S), _s(17, S), _s(8, S), _s(12, S), _s(12, S)],
           fill=fill, width=sw, joint='curve')
    d.line([_s(12, S), _s(12, S), _s(7, S), _s(16, S), _s(6, S), _s(20, S)],
           fill=fill, width=sw, joint='curve')
    d.line([_s(12, S), _s(12, S), _s(17, S), _s(16, S), _s(18, S), _s(20, S)],
           fill=fill, width=sw, joint='curve')

    # 少量沙粒，让小尺寸下也能一眼识别为沙漏。
    d.polygon([
        (_s(8, S), _s(6, S)),
        (_s(16, S), _s(6, S)),
        (_s(12, S), _s(10, S)),
    ], fill=fill)
    d.polygon([
        (_s(12, S), _s(14, S)),
        (_s(8, S), _s(18, S)),
        (_s(16, S), _s(18, S)),
    ], fill=fill)
    return img


def _briefcase(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """公文包 — 岗位配置/职位"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 包体
    d.rounded_rectangle([_s(2, S), _s(9, S), _s(22, S), _s(21, S)],
                        radius=_s(1.5, S), outline=fill, width=sw)
    # 提手
    d.arc([_s(8, S), _s(3, S), _s(16, S), _s(9, S)],
          start=180, end=0, fill=fill, width=sw)
    # 中间横带
    d.line([_s(2, S), _s(14, S), _s(22, S), _s(14, S)], fill=fill, width=sw)
    # 锁扣
    d.rectangle([_s(10, S), _s(12.5, S), _s(14, S), _s(15.5, S)],
                outline=fill, width=max(1, int(_s(1.2, S))))
    return img


def _filter(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """漏斗 — 筛选结果"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 漏斗上部：宽口收窄
    d.polygon([
        (_s(2, S), _s(3, S)),    # 左上
        (_s(22, S), _s(3, S)),   # 右上
        (_s(14, S), _s(12, S)),  # 右肩
        (_s(10, S), _s(12, S)),  # 左肩
    ], outline=fill, width=sw)
    # 漏斗管：从收窄处向下
    d.line([_s(10, S), _s(12, S), _s(10, S), _s(21, S)], fill=fill, width=sw)
    d.line([_s(14, S), _s(12, S), _s(14, S), _s(21, S)], fill=fill, width=sw)
    return img


def _chat(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """聊天气泡 — 打招呼/已发送消息"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 气泡主体
    d.rounded_rectangle([_s(5, S), _s(3, S), _s(21, S), _s(16, S)],
                        radius=_s(3, S), outline=fill, width=sw)
    # 尾巴（左下角三角）
    d.line([_s(7, S), _s(12, S), _s(1.5, S), _s(17, S)], fill=fill, width=sw)
    d.line([_s(1.5, S), _s(17, S), _s(8, S), _s(16, S)], fill=fill, width=sw)
    # 三个省略号点
    dot_r = _s(1.2, S)
    for dx in [0, _s(3.5, S), _s(7, S)]:
        d.ellipse([_s(8.5, S) + dx - dot_r, _s(9.5, S) - dot_r,
                   _s(8.5, S) + dx + dot_r, _s(9.5, S) + dot_r], fill=fill)
    return img


def _document(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 页面主体 + 折角
    fold_x = _s(15, S)
    fold_y = _s(8, S)
    # 页面轮廓（不含被折角覆盖的部分）：(3,4)→(fold_x,4)→(19,fold_y)→(19,22)→(3,22)→(3,4)
    page_outline = [
        _s(3, S), _s(4, S),           # 左上
        fold_x, _s(4, S),              # 顶部到折角起点
        _s(19, S), fold_y,             # 折角斜边
        _s(19, S), _s(22, S),          # 右边缘
        _s(3, S), _s(22, S),           # 底部
        _s(3, S), _s(4, S),            # 回到左上
    ]
    d.polygon(page_outline, outline=fill, width=sw)
    # 折角线（从顶部到右侧）
    d.line([fold_x, _s(4, S), _s(19, S), fold_y], fill=fill, width=sw)
    # 文本行
    line_left = _s(6, S)
    line_color = fill
    lw = max(1, sw - 1) if sw > 1 else 1
    d.line([line_left, _s(10, S), _s(13, S), _s(10, S)], fill=line_color, width=sw)    # 标题（粗）
    d.line([line_left, _s(14, S), _s(17, S), _s(14, S)], fill=line_color, width=lw)    # 行2
    d.line([line_left, _s(17, S), _s(15, S), _s(17, S)], fill=line_color, width=lw)    # 行3
    return img


def _shield_check(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """盾形徽章 + 勾号 — BOSS logo，填充品牌蓝，白色勾号"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 品牌色（硬编码，不受 fill 参数影响）
    brand_blue = '#1E88E5'
    brand_dark = '#1565C0'
    # 盾牌轮廓：填充品牌蓝
    shield = [
        (_s(12, S), _s(2, S)),    # 顶部中心
        (_s(21, S), _s(5, S)),    # 右上
        (_s(21, S), _s(12, S)),   # 右中
        (_s(12, S), _s(22, S)),   # 底部尖
        (_s(3, S), _s(12, S)),    # 左中
        (_s(3, S), _s(5, S)),     # 左上
    ]
    d.polygon(shield, fill=brand_blue, outline=brand_dark, width=sw)
    # 白色勾号（粗）
    check_sw = sw + 2
    d.line([_s(7, S), _s(12, S), _s(10.5, S), _s(16, S)], fill='white', width=check_sw)
    d.line([_s(10.5, S), _s(16, S), _s(17, S), _s(8, S)], fill='white', width=check_sw)
    return img


def _download(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    ax = _s(12, S)
    # 竖线
    d.line([ax, _s(3, S), ax, _s(17, S)], fill=fill, width=sw)
    # 箭头
    al = _s(4.5, S)
    d.line([ax - al, _s(17, S) - al * 0.8, ax, _s(17, S)], fill=fill, width=sw)
    d.line([ax + al, _s(17, S) - al * 0.8, ax, _s(17, S)], fill=fill, width=sw)
    # 底部托盘
    d.line([_s(4, S), _s(21, S), _s(20, S), _s(21, S)], fill=fill, width=sw + 1)
    return img


def _check(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """勾号 ✓ — 确认/确定"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    d.line([_s(5, S), _s(12, S), _s(10, S), _s(18, S)], fill=fill, width=sw)
    d.line([_s(10, S), _s(18, S), _s(19, S), _s(6, S)], fill=fill, width=sw)
    return img


def _passed_filter(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """放大双人图案 — 通过筛选，沿用原候选人图标。"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    people_size = max(12, int(round(size_px * 1.10)))
    people_sw = max(1, int(round(sw * people_size / size_px)))
    people = _people(people_size, fill, (0, 0, 0, 0), people_sw)
    bbox = people.getbbox()
    if bbox:
        people = people.crop(bbox)
        x = (size_px - people.width) // 2
        y = (size_px - people.height) // 2
        img.alpha_composite(people, (x, y))
    return img


def _close(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """叉号 ✕ — 取消/关闭"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    pad = _s(6, S)
    d.line([pad, pad, S - pad, S - pad], fill=fill, width=sw)
    d.line([S - pad, pad, pad, S - pad], fill=fill, width=sw)
    return img


def _stamp_check(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    """圆形印章 + 勾号 — 审核通过"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    # 外圆（印章边框）
    cx, cy = _s(12, S), _s(12, S)
    r = _s(9, S)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fill, width=sw + 1)
    # 内勾号
    check_sw = sw + 1
    d.line([_s(7, S), _s(12.5, S), _s(10.5, S), _s(16.5, S)], fill=fill, width=check_sw)
    d.line([_s(10.5, S), _s(16.5, S), _s(17, S), _s(8, S)], fill=fill, width=check_sw)
    return img


def _traffic_light(size_px: int, fill: str, bg: str, sw: int, active: str) -> Image.Image:
    """三色信号灯，突出当前连通性状态，不绘制额外外框。"""
    img = Image.new('RGBA', (size_px, size_px), bg)
    d = ImageDraw.Draw(img)
    S = size_px
    colors = {
        "red": ui_theme.LAMP_ERROR,
        "yellow": ui_theme.LAMP_WARN,
        "green": ui_theme.LAMP_OK,
    }
    centers = {"red": 5.5, "yellow": 12, "green": 18.5}
    inactive = ui_theme.LAMP_OFF
    radius = _s(3.2, S)
    center_x = _s(12, S)
    for color, center_y in centers.items():
        cy = _s(center_y, S)
        d.ellipse(
            [center_x - radius, cy - radius, center_x + radius, cy + radius],
            fill=colors[color] if color == active else inactive,
        )
    return img


def _traffic_light_pending(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    return _traffic_light(size_px, fill, bg, sw, "yellow")


def _traffic_light_success(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    return _traffic_light(size_px, fill, bg, sw, "green")


def _traffic_light_error(size_px: int, fill: str, bg: str, sw: int) -> Image.Image:
    return _traffic_light(size_px, fill, bg, sw, "red")


# ---------------------------------------------------------------------------
# 图标注册表
# ---------------------------------------------------------------------------
ICON_REGISTRY: Dict[str, Callable] = {
    'eye':          _eye,
    'eye_off':      _eye_off,
    'clipboard':    _clipboard,
    'task_list':    _task_list,
    'candidate_review': _candidate_review,
    'health_shield': _health_shield,
    'ai_spark':      _ai_spark,
    'chart':        _chart,
    'gear':         _gear,
    'briefcase':    _briefcase,
    'filter':       _filter,
    'plus':         _plus,
    'trash':        _trash,
    'search':       _search,
    'search_color': _search_color,
    'pencil':       _pencil,
    'save':         _save,
    'refresh':      _refresh,
    'refresh_clean': _refresh_clean,
    'import':       _import_icon,
    'export':       _export,
    'folder':       _folder,
    'home':         _home,
    'people':       _people,
    'trophy':       _trophy,
    'thumbs_up':    _thumbs_up,
    'strong_recommend': _strong_recommend,
    'arrow_up':     _arrow_up,
    'arrow_down':   _arrow_down,
    'chevron_up':   _chevron_up,
    'chevron_down': _chevron_down,
    'mail':         _mail,
    'play':         _play,
    'stop':         _stop,
    'star':         _star,
    'hourglass':    _hourglass,
    'chat':         _chat,
    'download':     _download,
    'check':        _check,
    'passed_filter': _passed_filter,
    'close':        _close,
    'document':     _document,
    'shield_check': _shield_check,
    'stamp_check':  _stamp_check,
    'traffic_light_pending': _traffic_light_pending,
    'traffic_light_success': _traffic_light_success,
    'traffic_light_error': _traffic_light_error,
}


# ---------------------------------------------------------------------------
# IconCache — 单例缓存
# ---------------------------------------------------------------------------
_instance: Optional['IconCache'] = None


class IconCache:
    """DPI 感知的图标缓存，按 (name, size, fill, bg) 缓存 PhotoImage"""

    def __init__(self, scale: float):
        self._scale = scale
        self._cache: Dict[Tuple[str, int, str, str], ImageTk.PhotoImage] = {}

    def get(self, name: str, size_px: int, fill: str, bg: str = '') -> ImageTk.PhotoImage:
        key = (name, size_px, fill, bg)
        if key not in self._cache:
            drawer = ICON_REGISTRY[name]
            sw = max(1, int(STROKE_WIDTH * self._scale))
            # 空字符串 → 透明背景（RGBA 四元组）
            bg_resolved = bg if bg else (0, 0, 0, 0)
            # 4x 超采样 + LANCZOS 缩回，在高 DPI 和细曲线下保持平滑边缘。
            supersample = 4
            super_px = size_px * supersample
            super_sw = max(1, sw * supersample)
            pil_img = drawer(super_px, fill, bg_resolved, super_sw)
            pil_img = pil_img.resize((size_px, size_px), Image.LANCZOS)
            self._cache[key] = ImageTk.PhotoImage(pil_img)
        return self._cache[key]

    def button(self, name: str, fill: str = '#1A202C', bg: str = '') -> ImageTk.PhotoImage:
        return self.get(name, int(ICON_SIZE_BUTTON * self._scale), fill, bg)

    def nav(self, name: str, fill: str = '#1A202C', bg: str = '') -> ImageTk.PhotoImage:
        return self.get(name, int(ICON_SIZE_NAV * self._scale), fill, bg)

    def logo(self, name: str, fill: str = '#1A202C', bg: str = '') -> ImageTk.PhotoImage:
        return self.get(name, int(ICON_SIZE_LOGO * self._scale), fill, bg)

    def stat(self, name: str, fill: str = 'white', bg: str = '') -> ImageTk.PhotoImage:
        return self.get(name, int(ICON_SIZE_STAT * self._scale), fill, bg)


def init(scale: float) -> IconCache:
    global _instance
    _instance = IconCache(scale)
    return _instance


def cache() -> IconCache:
    if _instance is None:
        raise RuntimeError("IconCache not initialized — call icons.init(scale) first")
    return _instance
