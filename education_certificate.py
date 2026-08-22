"""毕业证书识别（图片走视觉模型 / PDF 走文本模型）与学信网字段校验。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

from ai_adapter import build_request, detect_protocol, friendly_http_error, normalize_response


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SUPPORTED_PDF_SUFFIXES = {".pdf"}
MAX_IMAGE_SIDE = 2400
JPEG_QUALITY = 95
CAPTCHA_AUTO_SUBMIT_MIN_CONFIDENCE = 80
CHSI_QUERY_URL = "https://www.chsi.com.cn/xlcx/lscx/query.do"
XIAOMI_VISION_MODEL = "mimo-v2.5"
CHSI_SCREENSHOT_WIDTH = 3840
CHSI_SCREENSHOT_PADDING = 48
_CHSI_RESULT_CAPTURE_ATTR = "data-boss-education-result-capture"
_CHSI_RESULT_STRONG_LABELS = (
    "性别",
    "出生日期",
    "出生年月",
    "入学日期",
    "毕业日期",
    "毕业时间",
    "学历类别",
    "学历层次",
    "学校名称",
    "院校名称",
    "毕业院校",
    "专业",
    "学习形式",
    "学制",
    "毕结业结论",
)
_CHSI_NOT_FOUND_MARKERS = (
    "未找到学历信息",
    "未查询到学历信息",
    "没有查询到学历信息",
)
_CHSI_QR_CONFIRMATION_MARKERS = (
    "扫码验证",
    "请使用学信网APP扫码",
    "请使用学信网App扫码",
)
_CHSI_QR_EXPIRED_MARKERS = (
    "二维码已过期",
    "二维码失效",
)
_CHSI_CAPTCHA_ERROR_MARKERS = (
    "图片验证码输入有误",
    "图片验证码输入错误",
    "验证码错误",
    "验证码不正确",
    "验证码失效",
    "验证码过期",
    "验证码有误",
    "请重新输入验证码",
)
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ChsiResultNotReadyError(RuntimeError):
    """The CHSI tab has not reached a candidate-specific final result page."""


class ChsiScreenshotError(RuntimeError):
    """The CHSI result page was detected but could not be captured safely."""

_ORIENTATION_SYSTEM_PROMPT = """你只判断毕业证书图片的正确阅读方向，不识别证书字段。
图片是同一证书顺时针旋转 0/90/180/270 度的四格对照图。
返回严格 JSON 对象，不要使用 Markdown：
{"rotation":0,"rotation_confidence":0}

rotation 必须是文字正常朝上、可以从左到右自然阅读的那一格角度，只能填写 0、90、180、270。
rotation_confidence 是 0 到 100 的整数；无法可靠判断或低于 80 时 rotation 必须返回 0。
"""


_INITIAL_RECOGNITION_SYSTEM_PROMPT = """你同时完成毕业证书方向判断和第一遍字段识别。
第一张图是同一证书顺时针旋转 0/90/180/270 度的四格方向对照图；第二张图是原始高清证书。
先从第一张图选择文字正常朝上的角度，再按照该方向逐字读取第二张图。不要深入推理，不要解释。
只返回严格 JSON 对象，不要使用 Markdown：
{"rotation":0,"rotation_confidence":0,"name":"","certificate_number":"","school":"","major":"","field_confidence":{"name":0,"certificate_number":0,"school":0,"major":0},"confidence":0,"warnings":[]}

规则：
1. rotation 只能填写 0、90、180、270，表示把原始图顺时针旋转多少度后文字正常朝上。
2. name 只填写证书持有人的姓名；certificate_number 只逐字符抄录“证书编号”或“电子注册号”旁的完整编号。
3. 重点核对证书编号中的 0/O、1/I/l、5/S、8/B，不得猜测或按常识纠错。
4. school 填写毕业院校全称，major 填写证书上的专业名称。
5. 无法确认的字段留空；所有置信度均填写 0 到 100 的整数。
"""


_SYSTEM_PROMPT = """你是毕业证书字段识别器。图片已经纠正为正常阅读方向。
只逐字读取图片中明确可见的内容，不猜测、不补全、不按常识纠错。
返回严格 JSON 对象，不要使用 Markdown：
{"name":"","certificate_number":"","school":"","major":"","field_confidence":{"name":0,"certificate_number":0,"school":0,"major":0},"confidence":0,"warnings":[]}

规则：
1. name 只填写毕业证书持有人的姓名，不要填写校长、院长或学校名称。
2. certificate_number 只逐字符抄录“证书编号”或“电子注册号”标签旁的完整编号；特别核对 0/O、1/I/l、5/S、8/B，不得擅自替换。
3. school 填写毕业院校全称，major 填写证书上的专业名称。
4. 无法确认时字段留空，并在 warnings 中说明。
5. field_confidence 分别填写四个字段的识别置信度（0-100），看不清的字段必须低于 80。
6. confidence 是 0 到 100 的整数，表示文字字段整体识别置信度。
"""


_FIELD_REVIEW_SYSTEM_PROMPT = """你是毕业证书字段复核器。只复核用户指定的可疑字段。
第一张图是转正后的完整证书，第二张图是同一证书的四区域高清放大图。
逐字符抄录标签旁的原文，不猜测、不补全、不使用常识纠错。
返回严格 JSON 对象，不要使用 Markdown：
{"name":"","certificate_number":"","school":"","major":"","field_confidence":{"name":0,"certificate_number":0,"school":0,"major":0},"confidence":0,"warnings":[]}
未要求复核的字段必须留空。证书编号特别核对 0/O、1/I/l、5/S、8/B。
姓名和证书编号属于查询关键字段：必须独立重新读取，不得沿用或猜测第一次识别结果。
"""


_NAME_REVIEW_SYSTEM_PROMPT = """你是毕业证书姓名专项识别器。
图片是同一张已转正毕业证书的多个相互重叠原始分区，不是多张证书。
只查找证书持有人的姓名：优先读取“姓名”标签旁的内容，或“学生”之后、性别/出生日期之前的 2 至 4 个汉字。
逐字观察偏旁和笔画；字符生僻不是留空理由，但确实看不清时必须留空，禁止按常见姓名猜测。
返回严格 JSON 对象，不要使用 Markdown：
{"name":"","field_confidence":{"name":0},"confidence":0,"warnings":[]}
除 name 外不要返回其他证书字段。
"""


_NAME_COMPONENT_REVIEW_SYSTEM_PROMPT = """你是毕业证书姓名逐字结构复核器。
图片是同一张证书的灰度增强重叠分区。只识别证书持有人的姓名，不读取校长等其他人名。
对姓名中的每个汉字先观察实际可见的左部、右部或上下结构，再填写 character；不得把生僻字改成更常见的同音或形近姓名用字。
返回严格 JSON 对象，不要使用 Markdown：
{"name":"","character_evidence":[{"character":"","visible_structure":""}],"field_confidence":{"name":0},"confidence":0,"warnings":[]}
看不清时 name 留空，禁止猜测。
"""


_NAME_DISAMBIGUATION_SYSTEM_PROMPT = """你是毕业证书姓名形近字裁决器。
用户会提供两个人名候选以及同一张证书的多个原始彩色分区。候选只是待核对文本，不能作为答案依据。
只比较两个候选中不同的汉字：观察该字真实可见的左部、右部或上下结构，再决定图中更符合哪个候选。
禁止按常见姓名、读音或词频选择；看不清时 name 必须留空。
返回严格 JSON 对象，不要使用 Markdown：
{"name":"","character_evidence":[{"character":"","visible_structure":""}],"field_confidence":{"name":0},"confidence":0,"warnings":[]}
name 只能是两个候选之一或空字符串。
"""


_PDF_SYSTEM_PROMPT = """你是毕业证书字段识别器。下面是从 PDF 提取的文本（可能无版式、字段顺序混乱）。
只填写文本中明确出现的内容，不猜测、不补全。
返回严格 JSON 对象，不要使用 Markdown：
{"name":"","certificate_number":"","school":"","major":"","confidence":0,"warnings":[]}

规则：
1. name 只填写毕业证书持有人的姓名，不要填写校长、院长或学校名称。
2. certificate_number 只填写"证书编号"或"电子注册号"对应的完整编号。
3. school 填写毕业院校全称，major 填写证书上的专业名称。
4. 无法确认时字段留空，并在 warnings 中说明。
5. confidence 是 0 到 100 的整数，表示全部字段整体识别置信度。
6. 若文本明显不是毕业证书内容，所有字段留空，warnings 写"未识别到毕业证书内容"。
"""


@dataclass(frozen=True)
class CertificateRecognition:
    """结构化毕业证书识别结果。"""

    name: str
    certificate_number: str
    school: str
    major: str
    rotation: int
    rotation_confidence: int
    confidence: int
    warnings: tuple[str, ...]
    model: str
    critical_conflicts: tuple[str, ...] = ()


def resolve_vision_api_config(api_config: dict[str, Any]) -> dict[str, Any]:
    """为图片识别选择当前服务商的视觉模型，不改变全局配置。"""
    resolved = dict(api_config)
    provider = str(resolved.get("api_provider") or "").lower()
    base_url = str(resolved.get("base_url") or "").lower()
    model = str(resolved.get("model") or "").lower()
    if (
        provider == "xiaomi"
        or "xiaomimimo.com" in base_url
        or "api.ai.xiaomi.com" in base_url
    ):
        if model == "mimo-v2.5-pro":
            resolved["model"] = XIAOMI_VISION_MODEL
        resolved["_disable_thinking"] = True
    return resolved


# 已知支持图片输入的模型名称关键词（小写匹配）
_VISION_MODEL_KEYWORDS: tuple[str, ...] = (
    "vision", "-vl", "vl-", "_vl", "vl2", "omni",
    "gpt-4o", "gpt-4-turbo", "gpt-4.1", "o1", "o3", "o4",
    "claude-3", "claude-4", "claude-sonnet-4", "claude-opus-4",
    "mimo-v2.5", "mimo-v2.5-vl",
    "qwen-vl", "qwen2.5-vl", "qwen3-vl",
    "qwen3.5", "qwen3.6", "qwen3.7",
    "glm-4v", "glm-5v",
    "kimi-k2.5", "kimi-k2.6",
    "minimax-vl", "minimax-m2", "minimax-m3",
    "step-1v", "step-2v",
    "gemini", "gemma",
    "deepseek-vl",
    "internvl",
)


def likely_supports_vision(api_config: dict[str, Any]) -> bool:
    """根据模型名称启发式判断是否可能支持图片输入。

    返回 True 不保证一定支持（名称不含关键词的视觉模型会漏判）；
    返回 False 基本确定不支持（纯文本模型名称不含这些关键词）。
    """
    provider = str(api_config.get("api_provider") or "").lower()
    model = str(api_config.get("model") or "").lower()
    base_url = str(api_config.get("base_url") or "").lower()
    if provider == "kimi" and model == "k3":
        return True
    # 小米服务：mimo-v2.5 系列支持视觉
    if (
        provider == "xiaomi"
        or "xiaomimimo.com" in base_url
        or "api.ai.xiaomi.com" in base_url
    ):
        return "mimo" in model
    # Anthropic：claude-3 及以后的多模态系列
    if provider == "anthropic" or "api.anthropic.com" in base_url:
        return any(kw in model for kw in ("claude-3", "claude-4", "sonnet", "opus"))
    # OpenAI：gpt-4o / gpt-4-turbo / o1 / o3 / o4 系列
    if provider == "openai" or "api.openai.com" in base_url:
        return any(kw in model for kw in ("gpt-4o", "gpt-4-turbo", "o1", "o3", "o4"))
    # 通用关键词匹配
    return any(kw in model for kw in _VISION_MODEL_KEYWORDS)


def validate_image_path(path: str | Path) -> Path:
    """校验图片路径及格式。"""
    image_path = Path(path)
    if image_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError("仅支持 JPG、JPEG、PNG、BMP、WEBP 图片")
    if not image_path.is_file():
        raise ValueError("图片文件不存在")
    return image_path


def is_pdf_path(path: str | Path) -> bool:
    """判断路径是否为 PDF 文件。"""
    return Path(path).suffix.lower() in SUPPORTED_PDF_SUFFIXES


def validate_document_path(path: str | Path) -> Path:
    """校验图片或 PDF 路径（导入时用）。"""
    doc_path = Path(path)
    suffix = doc_path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES and suffix not in SUPPORTED_PDF_SUFFIXES:
        raise ValueError("仅支持 JPG、JPEG、PNG、BMP、WEBP 图片或 PDF 文件")
    if not doc_path.is_file():
        raise ValueError("文件不存在")
    return doc_path


def extract_pdf_text(path: str | Path) -> str:
    """提取 PDF 文本层内容。扫描件无文本层或加密 PDF 提不出文本时抛 RuntimeError。

    pdfminer 只解析文本，不栅格化；返回的是无版式纯文本，字段顺序可能混乱。
    """
    try:
        from pdfminer.high_level import extract_text as _extract
    except ImportError as error:
        raise RuntimeError("PDF 解析依赖未安装") from error
    try:
        raw = _extract(str(path))
    except Exception as error:
        raise RuntimeError(f"PDF 无法读取：{error}") from error
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in (raw or "").splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _load_upright_certificate_image(
    path: str | Path,
    *,
    rotation: int = 0,
) -> Image.Image:
    """Load an EXIF-corrected RGB certificate and apply one CW rotation."""
    image_path = validate_image_path(path)
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    if rotation in (90, 180, 270):
        image = image.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
    return image


def _jpeg_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def prepare_image_data_url(
    path: str | Path,
    *,
    rotation: int = 0,
) -> str:
    """Rotate, preserve small text, and encode the field-reading image."""
    image = _load_upright_certificate_image(path, rotation=rotation)
    try:
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
        return _jpeg_data_url(image)
    finally:
        image.close()


def prepare_orientation_sheet_data_url(path: str | Path) -> str:
    """生成顺转0/90/180/270四格方向对照图，供模型选择正向版本。"""
    image_path = validate_image_path(path)
    cell_width, cell_height = 700, 520
    header_height = 36
    sheet = Image.new("RGB", (cell_width * 2, cell_height * 2), "white")
    with Image.open(image_path) as source:
        base = ImageOps.exif_transpose(source).convert("RGB")
        for index, angle in enumerate((0, 90, 180, 270)):
            variant = base.rotate(-angle, expand=True, resample=Image.Resampling.BICUBIC)
            variant.thumbnail(
                (cell_width - 20, cell_height - header_height - 20),
                Image.Resampling.LANCZOS,
            )
            cell = Image.new("RGB", (cell_width, cell_height), "#F3F4F6")
            from PIL import ImageDraw
            draw = ImageDraw.Draw(cell)
            draw.text((12, 10), f"ROTATE {angle} CW", fill="black")
            x = (cell_width - variant.width) // 2
            y = header_height + (cell_height - header_height - variant.height) // 2
            cell.paste(variant, (x, y))
            sheet.paste(cell, ((index % 2) * cell_width, (index // 2) * cell_height))
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def prepare_detail_sheet_data_url(
    path: str | Path,
    *,
    rotation: int = 0,
) -> str:
    """Build an overlapping four-region sheet for conditional field review."""
    base = _load_upright_certificate_image(path, rotation=rotation)
    cell_width, cell_height = 1000, 720
    header_height = 34
    sheet = Image.new("RGB", (cell_width * 2, cell_height * 2), "white")
    try:
        crop_width = max(1, min(base.width, round(base.width * 0.58)))
        crop_height = max(1, min(base.height, round(base.height * 0.58)))
        positions = (
            (0, 0, "TOP LEFT"),
            (base.width - crop_width, 0, "TOP RIGHT"),
            (0, base.height - crop_height, "BOTTOM LEFT"),
            (base.width - crop_width, base.height - crop_height, "BOTTOM RIGHT"),
        )
        from PIL import ImageDraw
        for index, (left, top, label) in enumerate(positions):
            region = base.crop((
                max(0, left),
                max(0, top),
                min(base.width, left + crop_width),
                min(base.height, top + crop_height),
            ))
            region.thumbnail(
                (cell_width - 20, cell_height - header_height - 20),
                Image.Resampling.LANCZOS,
            )
            cell = Image.new("RGB", (cell_width, cell_height), "#F3F4F6")
            ImageDraw.Draw(cell).text((12, 9), label, fill="black")
            x = (cell_width - region.width) // 2
            y = header_height + (cell_height - header_height - region.height) // 2
            cell.paste(region, (x, y))
            sheet.paste(cell, ((index % 2) * cell_width, (index // 2) * cell_height))
            region.close()
        return _jpeg_data_url(sheet)
    finally:
        sheet.close()
        base.close()


def prepare_name_detail_data_urls(
    path: str | Path,
    *,
    rotation: int = 0,
    enhanced: bool = False,
) -> tuple[str, ...]:
    """Return overlapping color or enhanced-gray tiles for independent name reads."""
    base = _load_upright_certificate_image(path, rotation=rotation)
    try:
        crop_width = max(1, min(base.width, round(base.width * 0.62)))
        crop_height = max(1, min(base.height, round(base.height * 0.58)))
        positions = (
            (0, 0),
            (base.width - crop_width, 0),
            (0, base.height - crop_height),
            (base.width - crop_width, base.height - crop_height),
        )
        urls: list[str] = []
        for left, top in positions:
            tile = base.crop((
                max(0, left),
                max(0, top),
                min(base.width, left + crop_width),
                min(base.height, top + crop_height),
            ))
            try:
                tile.thumbnail(
                    (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE),
                    Image.Resampling.LANCZOS,
                )
                if enhanced:
                    processed = ImageOps.autocontrast(
                        ImageOps.grayscale(tile),
                        cutoff=1,
                    )
                    processed = ImageEnhance.Contrast(processed).enhance(1.3)
                    processed = processed.filter(
                        ImageFilter.UnsharpMask(
                            radius=1,
                            percent=140,
                            threshold=2,
                        )
                    ).convert("RGB")
                    try:
                        urls.append(_jpeg_data_url(processed))
                    finally:
                        processed.close()
                else:
                    urls.append(_jpeg_data_url(tile))
            finally:
                tile.close()
        return tuple(urls)
    finally:
        base.close()


def _build_image_messages(
    api_config: dict[str, Any],
    system_prompt: str,
    instruction: str,
    data_urls: list[str],
) -> list[dict[str, Any]]:
    """Build one provider-compatible vision request."""
    if detect_protocol(api_config) == "anthropic":
        image_blocks = []
        for data_url in data_urls:
            media_type, encoded = data_url.split(";base64,", 1)
            image_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type.removeprefix("data:"),
                    "data": encoded,
                },
            })
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [*image_blocks, {"type": "text", "text": instruction}],
            },
        ]
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                *(
                    {"type": "image_url", "image_url": {"url": data_url}}
                    for data_url in data_urls
                ),
            ],
        },
    ]


def build_orientation_messages(
    api_config: dict[str, Any],
    orientation_data_url: str,
) -> list[dict[str, Any]]:
    """Build the orientation-only request."""
    return _build_image_messages(
        api_config,
        _ORIENTATION_SYSTEM_PROMPT,
        "只选择文字正常朝上的旋转角度。",
        [orientation_data_url],
    )


def build_initial_recognition_messages(
    api_config: dict[str, Any],
    orientation_data_url: str,
    original_data_url: str,
) -> list[dict[str, Any]]:
    """Build the combined orientation and first-field-read request."""
    return _build_image_messages(
        api_config,
        _INITIAL_RECOGNITION_SYSTEM_PROMPT,
        "第一张图判断方向，第二张图按该方向逐字读取字段；只返回 JSON。",
        [orientation_data_url, original_data_url],
    )


def build_vision_messages(
    api_config: dict[str, Any],
    data_url: str,
) -> list[dict[str, Any]]:
    """Build the field-only request for an already upright certificate."""
    return _build_image_messages(
        api_config,
        _SYSTEM_PROMPT,
        "逐字识别姓名、证书编号、学校和专业，并分别给出字段置信度。",
        [data_url],
    )


def build_field_review_messages(
    api_config: dict[str, Any],
    data_url: str,
    detail_data_url: str,
    fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Build a focused second read for fields rejected by deterministic checks."""
    return _build_image_messages(
        api_config,
        _FIELD_REVIEW_SYSTEM_PROMPT,
        "只复核这些字段：" + ", ".join(fields),
        [data_url, detail_data_url],
    )


def build_name_review_messages(
    api_config: dict[str, Any],
    detail_data_urls: tuple[str, ...],
    *,
    component_review: bool = False,
) -> list[dict[str, Any]]:
    """Build one independent name read from uncompressed source tiles."""
    return _build_image_messages(
        api_config,
        (
            _NAME_COMPONENT_REVIEW_SYSTEM_PROMPT
            if component_review
            else _NAME_REVIEW_SYSTEM_PROMPT
        ),
        (
            "逐字检查偏旁和结构后，只返回证书持有人的姓名。"
            if component_review
            else "逐块检查同一证书，只返回证书持有人的姓名。"
        ),
        list(detail_data_urls),
    )


def build_name_disambiguation_messages(
    api_config: dict[str, Any],
    detail_data_urls: tuple[str, ...],
    candidates: tuple[str, str],
) -> list[dict[str, Any]]:
    """Build a contrastive visual read for two explicitly conflicting names."""
    candidate_text = json.dumps(list(candidates), ensure_ascii=False)
    return _build_image_messages(
        api_config,
        _NAME_DISAMBIGUATION_SYSTEM_PROMPT,
        f"待裁决候选姓名：{candidate_text}。只根据图中不同汉字的实际结构裁决。",
        list(detail_data_urls),
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a model JSON object and repair harmless formatting mistakes."""
    cleaned = text.strip()
    repaired = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    repaired = (
        repaired.replace("“", '"')
        .replace("”", '"')
        .replace("：", ":")
        .replace("，", ",")
    )
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    candidates = [cleaned, repaired]
    for candidate in tuple(candidates):
        match = re.search(r"\{.*\}", candidate, flags=re.DOTALL)
        if match:
            candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("AI 未返回可解析的 JSON")


def normalize_recognition(payload: dict[str, Any], model: str = "") -> CertificateRecognition:
    """清洗并校验模型返回字段。"""
    name = re.sub(r"\s+", "", str(payload.get("name") or "").strip())
    school = re.sub(r"\s+", "", str(payload.get("school") or "").strip())
    major = re.sub(r"\s+", "", str(payload.get("major") or "").strip())
    certificate_number = re.sub(
        r"[\s\-—_]+", "", str(payload.get("certificate_number") or "").strip()
    )
    certificate_number = re.sub(r"[^0-9A-Za-z]", "", certificate_number)
    try:
        rotation = int(payload.get("rotation", 0))
    except (TypeError, ValueError):
        rotation = 0
    if rotation not in (0, 90, 180, 270):
        rotation = 0
    try:
        rotation_confidence = int(payload.get("rotation_confidence", 0))
    except (TypeError, ValueError):
        rotation_confidence = 0
    rotation_confidence = max(0, min(100, rotation_confidence))
    if rotation_confidence < 80:
        rotation = 0
    try:
        confidence = int(payload.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))
    raw_warnings = payload.get("warnings") or []
    if isinstance(raw_warnings, str):
        raw_warnings = [raw_warnings]
    warnings = [str(item).strip() for item in raw_warnings if str(item).strip()]
    raw_conflicts = payload.get("_critical_conflicts") or []
    if isinstance(raw_conflicts, str):
        raw_conflicts = [raw_conflicts]
    critical_conflicts = tuple(
        field
        for field in ("name", "certificate_number")
        if field in raw_conflicts
    )
    if not name:
        warnings.append("未能确认姓名")
    if not certificate_number:
        warnings.append("未能确认证书编号")
    elif len(certificate_number) != 18:
        warnings.append(f"证书编号为 {len(certificate_number)} 位，请人工核对")
    return CertificateRecognition(
        name=name,
        certificate_number=certificate_number,
        school=school,
        major=major,
        rotation=rotation,
        rotation_confidence=rotation_confidence,
        confidence=confidence,
        warnings=tuple(dict.fromkeys(warnings)),
        model=model,
        critical_conflicts=critical_conflicts,
    )


_RECOGNITION_FIELDS = ("name", "certificate_number", "school", "major")
_CRITICAL_RECOGNITION_FIELDS = ("name", "certificate_number")


def _normalize_orientation(payload: dict[str, Any]) -> tuple[int, int]:
    try:
        rotation = int(payload.get("rotation", 0))
    except (TypeError, ValueError):
        rotation = 0
    if rotation not in (0, 90, 180, 270):
        rotation = 0
    try:
        confidence = int(payload.get("rotation_confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))
    if confidence < 80:
        rotation = 0
    return rotation, confidence


def _questionable_recognition_fields(
    payload: dict[str, Any],
    result: CertificateRecognition,
) -> tuple[str, ...]:
    """Return fields for focused review; CHSI query fields are always reviewed."""
    questionable: set[str] = set(_CRITICAL_RECOGNITION_FIELDS)
    if not result.name or not 2 <= len(result.name) <= 20:
        questionable.add("name")
    if not result.certificate_number or len(result.certificate_number) != 18:
        questionable.add("certificate_number")
    if not result.school:
        questionable.add("school")
    if not result.major:
        questionable.add("major")

    raw_confidences = payload.get("field_confidence")
    if isinstance(raw_confidences, dict):
        for field in _RECOGNITION_FIELDS:
            try:
                confidence = int(raw_confidences.get(field, 0))
            except (TypeError, ValueError):
                confidence = 0
            if confidence < 80:
                questionable.add(field)
    elif result.confidence < 80:
        questionable.update(_RECOGNITION_FIELDS)
    return tuple(field for field in _RECOGNITION_FIELDS if field in questionable)


def _merge_field_review(
    primary_payload: dict[str, Any],
    review_payload: dict[str, Any],
    fields: tuple[str, ...],
    *,
    model: str,
) -> dict[str, Any]:
    """Merge only deterministic improvements; surface disagreements for review."""
    merged = dict(primary_payload)
    primary = normalize_recognition(primary_payload, model)
    review = normalize_recognition(review_payload, model)
    warnings = list(primary.warnings)
    raw_review_warnings = review_payload.get("warnings") or []
    if isinstance(raw_review_warnings, str):
        raw_review_warnings = [raw_review_warnings]
    warnings.extend(
        str(warning).strip()
        for warning in raw_review_warnings
        if str(warning).strip()
    )
    raw_critical_conflicts = primary_payload.get("_critical_conflicts") or []
    if isinstance(raw_critical_conflicts, str):
        raw_critical_conflicts = [raw_critical_conflicts]
    critical_conflicts = list(raw_critical_conflicts)
    for field in fields:
        primary_value = str(getattr(primary, field) or "")
        review_value = str(getattr(review, field) or "")
        if not review_value:
            warnings.append(f"{field} 复核仍无法确认")
            continue
        if not primary_value or primary_value == review_value:
            merged[field] = review_value
            continue
        if (
            field == "certificate_number"
            and len(primary_value) != 18
            and len(review_value) == 18
        ):
            merged[field] = review_value
            warnings.append("证书编号已由高清区域复核纠正，请提交前核对")
            continue
        if field == "name" and not 2 <= len(primary_value) <= 20:
            merged[field] = review_value
            warnings.append("姓名已由高清区域复核纠正，请提交前核对")
            continue
        review_value_valid = (
            2 <= len(review_value) <= 20
            if field == "name"
            else len(review_value) == 18
        )
        if field in _CRITICAL_RECOGNITION_FIELDS and review_value_valid:
            merged[field] = ""
            critical_conflicts.append(field)
            field_label = "姓名" if field == "name" else "证书编号"
            warnings.append(
                f"{field_label}两次识别结果不一致，已留空，请对照证书人工填写"
            )
            continue
        warnings.append(f"{field} 两次识别结果不一致，请人工核对")

    primary_field_confidence = primary_payload.get("field_confidence")
    review_field_confidence = review_payload.get("field_confidence")
    merged_confidence = (
        dict(primary_field_confidence)
        if isinstance(primary_field_confidence, dict)
        else {}
    )
    if isinstance(review_field_confidence, dict):
        for field in fields:
            if field in review_field_confidence:
                merged_confidence[field] = review_field_confidence[field]
    if merged_confidence:
        merged["field_confidence"] = merged_confidence
    merged["confidence"] = max(primary.confidence, review.confidence)
    merged["warnings"] = list(dict.fromkeys(warnings))
    merged["_critical_conflicts"] = list(dict.fromkeys(critical_conflicts))
    return merged


def _resolve_critical_conflicts(
    merged_payload: dict[str, Any],
    primary_payload: dict[str, Any],
    review_payload: dict[str, Any],
    tie_payload: dict[str, Any],
    *,
    model: str,
) -> dict[str, Any]:
    """Use a third focused read only when it agrees with one prior candidate."""
    resolved = dict(merged_payload)
    primary = normalize_recognition(primary_payload, model)
    review = normalize_recognition(review_payload, model)
    tie = normalize_recognition(tie_payload, model)
    conflicts = list(merged_payload.get("_critical_conflicts") or [])
    warnings = list(merged_payload.get("warnings") or [])
    remaining: list[str] = []
    for field in conflicts:
        tie_value = str(getattr(tie, field) or "")
        candidates = {
            str(getattr(primary, field) or ""),
            str(getattr(review, field) or ""),
        }
        candidates.discard("")
        valid = (
            2 <= len(tie_value) <= 20
            if field == "name"
            else len(tie_value) == 18
        )
        if valid and tie_value in candidates:
            resolved[field] = tie_value
            field_label = "姓名" if field == "name" else "证书编号"
            warnings = [
                warning
                for warning in warnings
                if not str(warning).startswith(f"{field_label}两次识别结果不一致")
            ]
            warnings.append(f"{field_label}已由第三次高清复核确认，请提交前核对")
        else:
            remaining.append(field)
    resolved["warnings"] = list(dict.fromkeys(warnings))
    resolved["_critical_conflicts"] = remaining
    return resolved


def _reliable_name_candidate(
    payload: dict[str, Any],
    *,
    model: str,
) -> tuple[str, int]:
    """Return a name only when the dedicated read is structurally valid and confident."""
    result = normalize_recognition(payload, model)
    raw_confidences = payload.get("field_confidence")
    try:
        confidence = int(
            raw_confidences.get("name", result.confidence)
            if isinstance(raw_confidences, dict)
            else result.confidence
        )
    except (TypeError, ValueError):
        confidence = 0
    if not 2 <= len(result.name) <= 20 or confidence < 80:
        return "", confidence
    return result.name, confidence


def _invoke_model(
    config: dict[str, Any],
    api_key: str,
    messages: list[dict[str, Any]],
    *,
    timeout: int = 60,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """发送消息给当前模型并返回解析后的 JSON 对象（视觉/文本协议通用）。

    max_tokens 是上限不是目标：普通模型输出约 200 tokens 即停止；
    推理模型（kimi k3/k2.6 等）的 reasoning tokens 也计入该预算，需要更大余量。
    故意不传 disable_thinking：证书与验证码是视觉识别任务，保留推理换识别质量
    （AI 评估、JD 解析等结构化任务已在各自调用方关闭推理）。
    """
    if not api_key:
        raise ValueError("当前模型未配置 API Key")
    if not config.get("base_url") or not config.get("model"):
        raise ValueError("当前模型配置不完整")
    url, headers, body, protocol = build_request(
        config, api_key, messages, max_tokens=max_tokens, temperature=0,
    )
    response = requests.post(url, headers=headers, json=body, timeout=timeout)
    try:
        response_payload = response.json()
    except ValueError:
        response_payload = response.text
    if response.status_code != 200:
        raise RuntimeError(friendly_http_error(response.status_code, response_payload))
    if not isinstance(response_payload, dict):
        raise RuntimeError("AI 服务返回了无效响应")
    raw_final_content = True
    if protocol != "anthropic":
        raw_choice = (response_payload.get("choices") or [{}])[0]
        raw_message = raw_choice.get("message") or {}
        raw_final_content = bool(raw_message.get("content"))
    message, finish_reason = normalize_response(protocol, response_payload)
    content = str(message.get("content") or message.get("reasoning_content") or "")
    if finish_reason == "length" and not raw_final_content:
        raise RuntimeError("AI 输出长度达到上限，未返回最终识别结果")
    return _extract_json_object(content)


def recognize_certificate_image(
    path: str | Path,
    api_config: dict[str, Any],
    api_key: str,
    *,
    timeout: int = 120,
    on_progress: Callable[[str, int], None] | None = None,
    rotation_override: int | None = None,
) -> CertificateRecognition:
    """Orient and read a certificate in two normal requests, with safe fallback."""
    emit = on_progress or (lambda _stage, _percent: None)
    vision_config = resolve_vision_api_config(api_config)
    emit("正在准备证书方向和高清图", 5)
    manual_rotation = (
        int(rotation_override)
        if rotation_override in (0, 90, 180, 270)
        else None
    )
    orientation_data_url = (
        prepare_orientation_sheet_data_url(path)
        if manual_rotation is None
        else ""
    )
    prepared_rotation = manual_rotation if manual_rotation is not None else 0
    original_data_url = prepare_image_data_url(
        path,
        rotation=prepared_rotation,
    )
    base_url = str(vision_config.get("base_url") or "").lower()
    model = str(vision_config.get("model") or "")
    max_tokens = 4096 if "api.kimi.com/coding" in base_url else 2048
    orientation_tokens = max_tokens if max_tokens == 4096 else 512
    rotation = 0
    rotation_confidence = 0
    pipeline_warnings: list[str] = []
    parsed: dict[str, Any] | None = None
    if manual_rotation is not None:
        emit("正在按手动方向读取证书字段", 15)
        parsed = _invoke_model(
            vision_config,
            api_key,
            build_vision_messages(vision_config, original_data_url),
            timeout=timeout,
            max_tokens=max_tokens,
        )
        rotation = manual_rotation
        rotation_confidence = 100
        pipeline_warnings.append("已按手动指定方向识别")
    else:
        try:
            emit("正在判断方向并读取证书字段", 15)
            parsed = _invoke_model(
                vision_config,
                api_key,
                build_initial_recognition_messages(
                    vision_config,
                    orientation_data_url,
                    original_data_url,
                ),
                timeout=timeout,
                max_tokens=max_tokens,
            )
            rotation, rotation_confidence = _normalize_orientation(
                parsed
            )
        except Exception:
            pipeline_warnings.append("首轮方向与字段识别失败，已自动切换兼容流程")

    direction_needs_field_rescue = False
    if manual_rotation is None and parsed is not None:
        initial_result = normalize_recognition(parsed, model)
        direction_needs_field_rescue = (
            rotation != prepared_rotation
            and (
                not initial_result.name
                or not initial_result.certificate_number
            )
        )

    if manual_rotation is None and (
        parsed is None
        or rotation_confidence < 80
        or direction_needs_field_rescue
    ):
        try:
            emit("正在复核证书方向", 35)
            orientation_payload = _invoke_model(
                vision_config,
                api_key,
                build_orientation_messages(vision_config, orientation_data_url),
                timeout=timeout,
                max_tokens=orientation_tokens,
            )
            fallback_rotation, fallback_confidence = _normalize_orientation(
                orientation_payload
            )
            if fallback_confidence >= 80:
                direction_changed = fallback_rotation != rotation
                rotation = fallback_rotation
                rotation_confidence = fallback_confidence
                if (
                    direction_changed
                    and parsed is not None
                    and direction_needs_field_rescue
                ):
                    parsed = None
                    pipeline_warnings.append(
                        "关键字段缺失，方向复核已纠正角度并重新识别"
                    )
            else:
                pipeline_warnings.append("方向复核置信度不足，已按原方向识别")
        except Exception:
            pipeline_warnings.append("方向复核失败，已按原方向识别")

    data_url = (
        original_data_url
        if rotation == prepared_rotation
        else prepare_image_data_url(path, rotation=rotation)
    )
    if parsed is None:
        emit("正在读取转正后的证书", 55)
        parsed = _invoke_model(
            vision_config,
            api_key,
            build_vision_messages(vision_config, data_url),
            timeout=timeout,
            max_tokens=max_tokens,
        )
    primary = normalize_recognition(parsed, model)
    questionable_fields = _questionable_recognition_fields(parsed, primary)
    primary_payload = dict(parsed)
    review_payload: dict[str, Any] | None = None
    tie_payload: dict[str, Any] | None = None
    if questionable_fields:
        detail_data_url = ""
        try:
            emit("正在生成关键字段高清区域", 65)
            detail_data_url = prepare_detail_sheet_data_url(
                path,
                rotation=rotation,
            )
            emit("正在核对姓名和证书编号", 70)
            review_payload = _invoke_model(
                vision_config,
                api_key,
                build_field_review_messages(
                    vision_config,
                    data_url,
                    detail_data_url,
                    questionable_fields,
                ),
                timeout=timeout,
                max_tokens=max_tokens,
            )
            emit("关键字段复核完成", 85)
            parsed = _merge_field_review(
                primary_payload,
                review_payload,
                questionable_fields,
                model=model,
            )
        except Exception:
            pipeline_warnings.append("可疑字段高清复核失败，请人工核对")
        critical_conflicts = tuple(parsed.get("_critical_conflicts") or [])
        if review_payload is not None and critical_conflicts:
            try:
                emit("两次结果不一致，正在做最终高清确认", 86)
                tie_payload = _invoke_model(
                    vision_config,
                    api_key,
                    build_field_review_messages(
                        vision_config,
                        data_url,
                        detail_data_url,
                        critical_conflicts,
                    ),
                    timeout=timeout,
                    max_tokens=max_tokens,
                )
                parsed = _resolve_critical_conflicts(
                    parsed,
                    primary_payload,
                    review_payload,
                    tie_payload,
                    model=model,
                )
            except Exception:
                pipeline_warnings.append("关键字段最终复核失败，请人工填写")

    current = normalize_recognition(parsed, model)
    current_conflicts = tuple(parsed.get("_critical_conflicts") or [])
    reviewed_names = [normalize_recognition(primary_payload, model).name]
    if review_payload is not None:
        reviewed_names.append(normalize_recognition(review_payload, model).name)
    if tie_payload is not None:
        reviewed_names.append(normalize_recognition(tie_payload, model).name)
    current_name_support = sum(
        name == current.name
        for name in reviewed_names
        if name
    )
    has_prior_name_consensus = bool(
        current.name
        and current_name_support >= 2
        and "name" not in current_conflicts
    )
    needs_name_audit = (
        "minimax-m3" in model.lower()
        or not current.name
        or "name" in current_conflicts
    )
    if needs_name_audit:
        explicit_name_conflict = False
        raw_warnings = parsed.get("warnings") or []
        if isinstance(raw_warnings, str):
            raw_warnings = [raw_warnings]
        parsed["warnings"] = [
            warning
            for warning in raw_warnings
            if str(warning) != "未能确认姓名"
            and not str(warning).startswith("name 复核仍无法确认")
            and not str(warning).startswith("姓名两种原始分区视图")
        ]
        try:
            emit("正在用增强灰度图逐字复核姓名", 89)
            component_name_payload = _invoke_model(
                vision_config,
                api_key,
                build_name_review_messages(
                    vision_config,
                    prepare_name_detail_data_urls(
                        path,
                        rotation=rotation,
                        enhanced=True,
                    ),
                    component_review=True,
                ),
                timeout=timeout,
                max_tokens=max_tokens,
            )
            component_name, component_confidence = _reliable_name_candidate(
                component_name_payload,
                model=model,
            )
            if current.name and component_name == current.name:
                parsed["warnings"] = [
                    warning
                    for warning in parsed["warnings"]
                    if not str(warning).startswith("姓名两次识别结果不一致")
                    and not str(warning).startswith("姓名已由第三次高清复核确认")
                ]
                parsed["_critical_conflicts"] = [
                    field
                    for field in parsed.get("_critical_conflicts") or []
                    if field != "name"
                ]
            elif not component_name and has_prior_name_consensus:
                parsed["warnings"].append(
                    "姓名字形复核未形成有效结果，已保留前两次一致识别，请提交前核对"
                )
            else:
                explicit_name_conflict = bool(current.name and component_name)
                emit("姓名结果存在分歧，正在用彩色原图裁决", 92)
                color_name_payload = _invoke_model(
                    vision_config,
                    api_key,
                    (
                        build_name_disambiguation_messages(
                            vision_config,
                            prepare_name_detail_data_urls(path, rotation=rotation),
                            (current.name, component_name),
                        )
                        if explicit_name_conflict
                        else build_name_review_messages(
                            vision_config,
                            prepare_name_detail_data_urls(path, rotation=rotation),
                        )
                    ),
                    timeout=timeout,
                    max_tokens=max_tokens,
                )
                color_name, color_confidence = _reliable_name_candidate(
                    color_name_payload,
                    model=model,
                )
                if current.name and color_name == current.name:
                    parsed["warnings"] = [
                        warning
                        for warning in parsed["warnings"]
                        if not str(warning).startswith("姓名两次识别结果不一致")
                        and not str(warning).startswith("姓名已由第三次高清复核确认")
                    ]
                    parsed["_critical_conflicts"] = [
                        field
                        for field in parsed.get("_critical_conflicts") or []
                        if field != "name"
                    ]
                    parsed["warnings"].append(
                        "姓名专项复核已确认原识别结果，请提交前核对"
                    )
                elif component_name and color_name == component_name:
                    original_name = current.name
                    parsed["name"] = component_name
                    parsed["warnings"] = [
                        warning
                        for warning in parsed["warnings"]
                        if not str(warning).startswith("姓名两次识别结果不一致")
                        and not str(warning).startswith("姓名已由第三次高清复核确认")
                    ]
                    field_confidence = parsed.get("field_confidence")
                    if not isinstance(field_confidence, dict):
                        field_confidence = {}
                    else:
                        field_confidence = dict(field_confidence)
                    field_confidence["name"] = min(
                        color_confidence,
                        component_confidence,
                    )
                    parsed["field_confidence"] = field_confidence
                    parsed["_critical_conflicts"] = [
                        field
                        for field in parsed.get("_critical_conflicts") or []
                        if field != "name"
                    ]
                    parsed["warnings"].append(
                        "姓名已由两种原始分区视图一致复核纠正，请提交前核对"
                        if original_name and original_name != component_name
                        else "姓名已由两种原始分区视图一致复核确认，请提交前核对"
                    )
                else:
                    parsed["name"] = ""
                    parsed["_critical_conflicts"] = list(dict.fromkeys([
                        *(parsed.get("_critical_conflicts") or []),
                        "name",
                    ]))
                    parsed["warnings"].append(
                        "姓名专项复核存在明确分歧，已留空，请人工填写"
                    )
        except Exception:
            if explicit_name_conflict or not has_prior_name_consensus:
                parsed["name"] = ""
                parsed["_critical_conflicts"] = list(dict.fromkeys([
                    *(parsed.get("_critical_conflicts") or []),
                    "name",
                ]))
            pipeline_warnings.append(
                "姓名专项复核暂不可用，已保留前两次一致识别，请提交前核对"
                if has_prior_name_consensus and not explicit_name_conflict
                else "姓名专项复核失败，已转人工确认"
            )

    current = normalize_recognition(parsed, model)
    missing_critical = []
    if not current.name:
        missing_critical.append("name")
    if not current.certificate_number:
        missing_critical.append("certificate_number")
    parsed["_critical_conflicts"] = list(dict.fromkeys([
        *(parsed.get("_critical_conflicts") or []),
        *missing_critical,
    ]))

    parsed["rotation"] = rotation
    parsed["rotation_confidence"] = rotation_confidence
    raw_parsed_warnings = parsed.get("warnings") or []
    if isinstance(raw_parsed_warnings, str):
        raw_parsed_warnings = [raw_parsed_warnings]
    parsed["warnings"] = list(dict.fromkeys([
        *raw_parsed_warnings,
        *pipeline_warnings,
    ]))
    emit("正在整理识别结果", 95)
    return normalize_recognition(parsed, model)


def build_pdf_text_messages(text: str) -> list[dict[str, Any]]:
    """构造从 PDF 文本提取字段的文本消息（不带图片，走文本协议）。"""
    return [
        {"role": "system", "content": _PDF_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]


def recognize_certificate_pdf(
    path: str | Path,
    api_config: dict[str, Any],
    api_key: str,
    *,
    timeout: int = 120,
) -> CertificateRecognition:
    """从 PDF 文本层提取字段，走当前文本模型识别。

    不调视觉模型、不栅格化 PDF；扫描件无文本层时抛 ValueError 提示用户转图片。
    """
    try:
        text = extract_pdf_text(path)
    except RuntimeError as error:
        raise ValueError(str(error)) from error
    if len(text) < 20:
        raise ValueError("该 PDF 是扫描件无文本层，请导出为图片后导入")
    config = dict(api_config)
    messages = build_pdf_text_messages(text)
    parsed = _invoke_model(config, api_key, messages, timeout=timeout)
    return normalize_recognition(parsed, str(config.get("model") or ""))


def validate_chsi_fields(name: str, certificate_number: str) -> tuple[str, str]:
    """校验人工确认后的学信网查询字段。"""
    clean_name = re.sub(r"\s+", "", name.strip())
    clean_number = re.sub(r"[\s\-—_]+", "", certificate_number.strip())
    if not clean_name:
        raise ValueError("请输入姓名")
    if len(clean_name) > 40:
        raise ValueError("姓名长度不能超过 40 个字符")
    if not clean_number:
        raise ValueError("请输入证书编号")
    if len(clean_number) > 18:
        raise ValueError("证书编号长度不能超过 18 位")
    if not re.fullmatch(r"[0-9A-Za-z]+", clean_number):
        raise ValueError("证书编号只能包含数字或英文字母")
    return clean_name, clean_number


def navigate_to_chsi(page: Any) -> None:
    """导航到学信网查询页（不填表单）。供 gui_main.py 在锁外并行调用。"""
    page.get(CHSI_QUERY_URL)


def fill_chsi_query_page(
    page: Any, name: str, certificate_number: str, *, skip_navigation: bool = False,
) -> None:
    """打开学信网查询页并填写姓名、证书编号，验证码留给人工输入。

    skip_navigation: 为 True 时跳过 page.get()，假设页面已由 navigate_to_chsi 加载。
    """
    clean_name, clean_number = validate_chsi_fields(name, certificate_number)
    if not skip_navigation:
        page.get(CHSI_QUERY_URL)

    # 注入代码覆盖弹窗，避免阻塞自动化操作
    disable_popups_script = """
// 覆盖 window.prompt 和 window.alert，自动返回/关闭
const originalPrompt = window.prompt;
const originalAlert = window.alert;
window.prompt = function(message, default_) {
    console.log('[自动化] 拦截 prompt:', message);
    return default_ !== undefined ? default_ : '';
};
window.alert = function(message) {
    console.log('[自动化] 拦截 alert:', message);
    // 不执行任何操作，自动关闭
};
"""
    try:
        page.run_js(disable_popups_script)
    except Exception:
        pass  # 如果失败，继续执行

    script = """
const values = {zsbh: arguments[0], xm: arguments[1]};
for (const [field, value] of Object.entries(values)) {
  const input = document.querySelector(`input[name="${field}"]:not([type="hidden"])`);
  if (!input) return `missing:${field}`;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "value"
  ).set;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", {bubbles: true}));
  input.dispatchEvent(new Event("change", {bubbles: true}));
}
const captcha = document.querySelector('input[name="yzm"]:not([type="hidden"])');
if (!captcha) return "missing:yzm";
const agreement = document.querySelector(
  'input[type="checkbox"][name="yhxy"], .agree-yhxy input[type="checkbox"]'
);
if (!agreement) return "missing:yhxy";
if (!agreement.checked) agreement.click();
if (!agreement.checked) return "unchecked:yhxy";
captcha.focus();
return "ok";
"""
    result = page.run_js(script, clean_number, clean_name)
    if result != "ok":
        raise RuntimeError(f"学信网页面结构已变化（{result}）")


# ---------------------------------------------------------------------------
# 学信网验证码自动识别
# ---------------------------------------------------------------------------

_CAPTCHA_SYSTEM_PROMPT = """你是图片验证码识别器。图中是一个学信网登录验证码图片。
用户可能同时提供同一张验证码的原色放大图和灰度增强图，请交叉核对，不要把它们当成两道题。
验证码有两种类型：
1. 字母/数字混合型：由 3 至 6 位英文字母和阿拉伯数字组成（例如 A8b、aB3x、K9mP7）
2. 算术型：由一个或多个加减乘除运算组成。五角星 ★ 表示乘法，乘除优先于加减，同级运算从左到右（例如 3+5=?、7★8=?、2+3★4=?）

返回严格 JSON 对象，不要使用 Markdown：
{"type":"letter","answer":"aB3x","confidence":90}
或
{"type":"arithmetic","expression":"2+3★4","answer":"14","confidence":95}

规则：
- type 为 "letter" 时，answer 是图中可见的字母/数字组合，保留大小写。
- 字符型验证码的位数会变化；必须从左到右逐个计数，不要补齐、截断或按输入框属性猜位数。
- type 为 "arithmetic" 时，逐字抄写 expression，并按乘除优先、加减次之计算 answer；操作数和结果都允许为 0，只有除数为 0 才是无效算式。
- 不要把五角星 ★ 当成装饰、字母 X 或未知字符，它在算术题中就是乘号。
- confidence 是 0-100 的识别置信度。
- 如果看不清或无法识别，返回 {"type":"unknown","answer":"","confidence":0}
"""


_CAPTCHA_REVIEW_SYSTEM_PROMPT = """你是验证码补充识别器。图片是同一张学信网验证码的高对比二值化版本。
独立识别其中的验证码。字符型是 3 至 6 位字符；算术型可能包含多个运算，五角星 ★ 表示乘法，必须按乘除优先、加减次之计算。
返回格式与普通验证码识别完全相同的严格 JSON，不要使用 Markdown。字母/数字型必须保留大小写；算术型必须逐字抄写 expression 并填写 answer；看不清就返回 unknown，禁止猜测。
"""


@dataclass(frozen=True)
class CaptchaImageVariants:
    """Lossless views of one captcha plus non-authoritative length hints."""

    original: str
    grayscale: str
    binary: str
    expected_length: int = 0
    maximum_length: int = 0


def _evaluate_captcha_expression(expression: Any) -> str:
    """Safely calculate an integer captcha expression with normal precedence."""
    raw_expression = "" if expression is None else str(expression)
    normalized = unicodedata.normalize("NFKC", raw_expression).strip().translate(
        str.maketrans({
            "×": "*", "✕": "*", "★": "*", "☆": "*", "X": "*", "x": "*",
            "÷": "/", "−": "-", "–": "-", "—": "-", "﹣": "-",
        })
    )
    normalized = re.sub(r"\s+|\ufe0f", "", normalized)
    match = re.fullmatch(
        r"(?P<expression>\d+(?:[+\-*/]\d+)+)(?:=(?:\?|[+\-]?\d*)|\?)?",
        normalized,
    )
    if not match:
        return ""
    tokens = re.findall(r"\d+|[+\-*/]", match.group("expression"))
    values = [Fraction(int(tokens[0]), 1)]
    additive_operators: list[str] = []
    for index in range(1, len(tokens), 2):
        operator = tokens[index]
        operand = Fraction(int(tokens[index + 1]), 1)
        if operator == "*":
            values[-1] *= operand
        elif operator == "/":
            if operand == 0:
                return ""
            values[-1] /= operand
        else:
            additive_operators.append(operator)
            values.append(operand)
    result = values[0]
    for operator, operand in zip(additive_operators, values[1:]):
        result = result + operand if operator == "+" else result - operand
    if result.denominator != 1:
        return ""
    return str(result.numerator)


def _normalize_captcha_integer(value: Any) -> str:
    """Normalize a model-provided integer without treating numeric zero as empty."""
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).strip().translate(
        str.maketrans({"−": "-", "–": "-", "—": "-", "﹣": "-"})
    )
    normalized = re.sub(r"\s+", "", normalized)
    if not re.fullmatch(r"[+\-]?\d+", normalized):
        return ""
    return str(int(normalized))


def parse_captcha_result(
    payload: dict[str, Any],
    *,
    expected_length: int = 0,
    maximum_length: int = 0,
) -> tuple[str, str, int]:
    """解析验证码识别模型返回的 JSON 对象。

    返回 (captcha_type, answer, confidence)。
    captcha_type 为 "letter"、"arithmetic" 或 "unknown"。
    """
    captcha_type = str(payload.get("type") or "unknown").strip().lower()
    if captcha_type in {
        "alphanumeric", "character", "characters", "code", "text",
    }:
        captcha_type = "letter"
    elif captcha_type in {"math", "calculation", "expression"}:
        captcha_type = "arithmetic"
    if captcha_type not in ("letter", "arithmetic"):
        captcha_type = "unknown"
    raw_answer = payload.get("answer")
    if raw_answer is None:
        raw_answer = payload.get("code")
    answer = unicodedata.normalize(
        "NFKC",
        "" if raw_answer is None else str(raw_answer),
    ).strip()
    try:
        confidence = int(payload.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))
    if captcha_type == "arithmetic":
        raw_expression = payload.get("expression")
        expression_supplied = bool(
            "" if raw_expression is None else str(raw_expression).strip()
        )
        calculated_answer = _evaluate_captcha_expression(raw_expression)
        model_answer = _normalize_captcha_integer(raw_answer)
        if expression_supplied and not calculated_answer:
            return "unknown", "", confidence
        if calculated_answer and model_answer and calculated_answer != model_answer:
            return "unknown", "", confidence
        # v2.30 accepted a confident model answer directly. Keep that compatible
        # fallback only when an otherwise usable model omits expression entirely.
        answer = calculated_answer or model_answer
        if not answer:
            return "unknown", "", confidence
    elif captcha_type == "letter":
        answer = re.sub(r"\s+", "", answer)
        # DOM 的长度属性与动态验证码位数并不总是一致，只作为诊断提示。
        valid_length = 3 <= len(answer) <= 6
        if not valid_length or not re.fullmatch(r"[A-Za-z0-9]+", answer):
            return "unknown", "", confidence
    if captcha_type == "unknown" or not answer:
        return "unknown", "", confidence
    return captcha_type, answer, confidence


def _captcha_result_detail(
    payload: dict[str, Any],
    result: tuple[str, str, int],
    *,
    expected_length: int = 0,
    maximum_length: int = 0,
) -> str:
    """Explain one model result without exposing the captcha answer."""
    captcha_type, answer, confidence = result
    if captcha_type != "unknown" and answer:
        label = "算术结果" if captcha_type == "arithmetic" else f"{len(answer)} 位字符"
        return f"模型识别出{label}，置信度 {confidence}"

    raw_type = str(payload.get("type") or "unknown").strip().lower()
    detail_answer = payload.get("answer")
    detail_answer = "" if detail_answer is None else str(detail_answer)
    detail_answer = re.sub(r"\s+", "", detail_answer)
    if raw_type == "letter" and detail_answer:
        if not re.fullmatch(r"[A-Za-z0-9]+", detail_answer):
            return "模型结果包含非字母数字字符"
        if not 3 <= len(detail_answer) <= 6:
            return f"模型返回 {len(detail_answer)} 位，不在常见的 3 至 6 位范围"
    if raw_type == "arithmetic":
        calculated_answer = _evaluate_captcha_expression(payload.get("expression"))
        model_answer = _normalize_captcha_integer(payload.get("answer"))
        if calculated_answer and model_answer and calculated_answer != model_answer:
            return "模型答案与程序复算结果不一致"
        return "模型返回的验证码算式无法解析"
    return "模型未返回可用的验证码内容"


def build_captcha_messages(
    api_config: dict[str, Any],
    data_urls: str | list[str] | tuple[str, ...],
    *,
    review: bool = False,
) -> list[dict[str, Any]]:
    """构造验证码识别的视觉消息（复用证书识别的协议判断逻辑）。"""
    urls = [data_urls] if isinstance(data_urls, str) else list(data_urls)
    system_prompt = _CAPTCHA_SYSTEM_PROMPT
    instruction = "请交叉核对图片中的同一验证码。"
    if review:
        system_prompt = _CAPTCHA_REVIEW_SYSTEM_PROMPT
        instruction = "请独立复核图片中的验证码内容。"
    return _build_image_messages(
        api_config,
        system_prompt,
        instruction,
        urls,
    )


# -- 验证码图片捕获 ----------------------------------------------------------

_CAPTCHA_FIND_JS = """
const input = document.querySelector('input[name="yzm"]:not([type="hidden"])');
if (!input) return null;
let img = null;
const selectors = [
  '.yzm-box', '.captcha-box', '.verify-img', '.imgCode',
  '.code-img', '.yzm_img', '.validate-img'
];
for (const sel of selectors) {
  const c = input.closest(sel);
  if (c) {
    img = Array.from(c.querySelectorAll('img')).find((candidate) => {
      const r = candidate.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
    if (img) break;
  }
}
if (!img) {
  let p = input.parentElement;
  for (let i = 0; i < 5 && p; i++) {
    img = Array.from(p.querySelectorAll('img')).find((candidate) => {
      const r = candidate.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
    if (img) break;
    p = p.parentElement;
  }
}
if (!img) return null;
const rect = img.getBoundingClientRect();
return {
  src: img.src,
  left: rect.left, top: rect.top,
  width: rect.width, height: rect.height,
  minLength: Number.isFinite(input.minLength) && input.minLength > 0 ? input.minLength : 0,
  maxLength: Number.isFinite(input.maxLength) && input.maxLength > 0 ? input.maxLength : 0
};
"""


def _upscale_captcha(raw_bytes: bytes) -> Image.Image:
    """Decode and enlarge one captcha without destructive filtering."""
    image = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("验证码图片尺寸无效")
    scale = 1
    if width < 480 or height < 160:
        scale = min(6, max(
            4,
            (480 + width - 1) // width,
            (160 + height - 1) // height,
        ))
    if scale > 1:
        image = image.resize(
            (width * scale, height * scale),
            Image.Resampling.LANCZOS,
        )
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    return image


def _png_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _image_bytes_to_data_url(raw_bytes: bytes) -> str:
    """将小尺寸验证码原色放大后转为无损 PNG data URL。"""
    image = _upscale_captcha(raw_bytes)
    try:
        return _png_data_url(image)
    finally:
        image.close()


def prepare_captcha_image_variants(
    raw_bytes: bytes,
    *,
    expected_length: int = 0,
    maximum_length: int = 0,
) -> CaptchaImageVariants:
    """Produce complementary views of one captcha for cross-checking."""
    original = _upscale_captcha(raw_bytes)
    grayscale = ImageOps.autocontrast(ImageOps.grayscale(original), cutoff=1)
    grayscale = ImageEnhance.Contrast(grayscale).enhance(1.2)
    grayscale = grayscale.filter(
        ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3)
    )

    histogram = grayscale.histogram()
    total = sum(histogram)
    weighted_sum = sum(index * count for index, count in enumerate(histogram))
    background_weight = 0
    background_sum = 0
    best_variance = -1.0
    threshold = 127
    for index, count in enumerate(histogram):
        background_weight += count
        if background_weight == 0:
            continue
        foreground_weight = total - background_weight
        if foreground_weight == 0:
            break
        background_sum += index * count
        background_mean = background_sum / background_weight
        foreground_mean = (weighted_sum - background_sum) / foreground_weight
        variance = background_weight * foreground_weight * (
            background_mean - foreground_mean
        ) ** 2
        if variance > best_variance:
            best_variance = variance
            threshold = index
    binary = grayscale.point(lambda value: 255 if value > threshold else 0, mode="1")
    grayscale_rgb = grayscale.convert("RGB")
    binary_rgb = binary.convert("RGB")
    try:
        return CaptchaImageVariants(
            original=_png_data_url(original),
            grayscale=_png_data_url(grayscale_rgb),
            binary=_png_data_url(binary_rgb),
            expected_length=max(0, int(expected_length or 0)),
            maximum_length=max(0, int(maximum_length or 0)),
        )
    finally:
        original.close()
        grayscale.close()
        binary.close()
        grayscale_rgb.close()
        binary_rgb.close()


def _capture_captcha_bytes(page: Any) -> tuple[bytes, int, int]:
    """Capture captcha bytes plus exact/maximum HTML length hints."""
    import tempfile

    info = page.run_js(_CAPTCHA_FIND_JS)
    if not info:
        raise RuntimeError("无法定位验证码图片元素，页面结构可能已变化")
    try:
        minimum_length = max(0, int(info.get("minLength") or 0))
    except (TypeError, ValueError):
        minimum_length = 0
    try:
        maximum_length = max(0, int(info.get("maxLength") or 0))
    except (TypeError, ValueError):
        maximum_length = 0
    expected_length = (
        minimum_length
        if minimum_length > 0 and minimum_length == maximum_length
        else 0
    )

    src = info.get("src") or ""
    if src.startswith("data:image"):
        try:
            _media_type, encoded = src.split(";base64,", 1)
            raw_bytes = base64.b64decode(encoded)
            with Image.open(io.BytesIO(raw_bytes)) as source:
                source.verify()
            return raw_bytes, expected_length, maximum_length
        except (ValueError, TypeError, OSError):
            pass

    bbox = (
        info.get("left", 0), info.get("top", 0),
        info.get("width", 0), info.get("height", 0),
    )
    with tempfile.TemporaryDirectory(prefix="boss-captcha-") as temp_dir:
        tmp_path = Path(temp_dir) / "element.png"
        for method_name in ("get_screenshot", "save_screenshot"):
            try:
                ele = page.ele("css:input[name='yzm']:not([type='hidden'])")
                if not ele:
                    break
                parent = ele.parent()
                img_ele = None
                while parent:
                    img_ele = parent.ele("tag:img", timeout=0.1)
                    if img_ele:
                        break
                    parent = parent.parent()
                if not img_ele:
                    break
                method = getattr(img_ele, method_name, None)
                if not method:
                    continue
                method(path=str(tmp_path))
                if tmp_path.exists() and tmp_path.stat().st_size > 0:
                    return tmp_path.read_bytes(), expected_length, maximum_length
            except Exception:
                continue

        if bbox[2] > 0 and bbox[3] > 0:
            full_path = Path(temp_dir) / "page.png"
            for method_name in ("get_screenshot", "save_screenshot", "screenshot"):
                method = getattr(page, method_name, None)
                if not method:
                    continue
                try:
                    method(path=str(full_path))
                    if full_path.exists() and full_path.stat().st_size > 0:
                        break
                except Exception:
                    continue
            if full_path.exists():
                with Image.open(full_path) as full_img:
                    w, h = full_img.size
                    x1 = max(0, int(bbox[0]))
                    y1 = max(0, int(bbox[1]))
                    x2 = min(w, int(bbox[0] + bbox[2]))
                    y2 = min(h, int(bbox[1] + bbox[3]))
                    if x2 > x1 and y2 > y1:
                        cropped = full_img.crop((x1, y1, x2, y2))
                        buffer = io.BytesIO()
                        cropped.convert("RGB").save(buffer, format="PNG", optimize=True)
                        return buffer.getvalue(), expected_length, maximum_length
    raise RuntimeError("验证码图片截取失败，所有策略均未成功")


def capture_captcha_image(page: Any) -> str:
    """从浏览器页面截取验证码图片，返回兼容的单张 data URL。"""
    raw_bytes, _expected_length, _maximum_length = _capture_captcha_bytes(page)
    return _image_bytes_to_data_url(raw_bytes)


def capture_captcha_variants(page: Any) -> CaptchaImageVariants:
    """Capture one captcha and return original, grayscale, and binary views.

    策略优先级：
    1. 直接提取 img src 的 data URL（质量最高）
    2. DrissionPage 元素截图（稳定可靠）
    3. 全页截图后按元素坐标裁剪（最终降级）

    失败时抛 RuntimeError。三个视图始终来自同一次页面截图。
    """
    raw_bytes, expected_length, maximum_length = _capture_captcha_bytes(page)
    return prepare_captcha_image_variants(
        raw_bytes,
        expected_length=expected_length,
        maximum_length=maximum_length,
    )


def recognize_captcha(
    data_url: str | CaptchaImageVariants,
    api_config: dict[str, Any],
    api_key: str,
    *,
    timeout: int = 60,
) -> tuple[str, str, int, str, bool]:
    """Recognize one captcha, using a second view only for weak primary results."""
    vision_config = resolve_vision_api_config(api_config)
    base_url = str(vision_config.get("base_url") or "").lower()
    max_tokens = 4096 if "api.kimi.com/coding" in base_url else 2048
    if isinstance(data_url, CaptchaImageVariants):
        primary_urls = [data_url.original, data_url.grayscale]
        expected_length = data_url.expected_length
        maximum_length = data_url.maximum_length
    else:
        primary_urls = [data_url]
        expected_length = 0
        maximum_length = 0
    parsed = _invoke_model(
        vision_config,
        api_key,
        build_captcha_messages(vision_config, primary_urls),
        timeout=timeout,
        max_tokens=max_tokens,
    )
    primary = parse_captcha_result(
        parsed,
        expected_length=expected_length,
        maximum_length=maximum_length,
    )
    primary_detail = _captcha_result_detail(
        parsed,
        primary,
        expected_length=expected_length,
        maximum_length=maximum_length,
    )
    needs_review = (
        isinstance(data_url, CaptchaImageVariants)
        and (
            primary[0] == "unknown"
            or primary[2] < CAPTCHA_AUTO_SUBMIT_MIN_CONFIDENCE
        )
    )
    if not needs_review:
        return (*primary, primary_detail, False)
    try:
        reviewed_payload = _invoke_model(
            vision_config,
            api_key,
            build_captcha_messages(vision_config, data_url.binary, review=True),
            timeout=timeout,
            max_tokens=max_tokens,
        )
    except Exception:
        if primary[0] == "arithmetic":
            return (
                "unknown",
                "",
                primary[2],
                primary_detail + "；算术题补充识别未完成",
                False,
            )
        return (*primary, primary_detail + "；补充识别未完成", False)
    reviewed = parse_captcha_result(
        reviewed_payload,
        expected_length=expected_length,
        maximum_length=maximum_length,
    )
    reviewed_detail = _captcha_result_detail(
        reviewed_payload,
        reviewed,
        expected_length=expected_length,
        maximum_length=maximum_length,
    )
    if primary[0] == "unknown" and reviewed[0] != "unknown":
        return (
            *reviewed,
            f"主识别无有效结果，采用补充识别：{reviewed_detail}",
            False,
        )
    if reviewed[0] == "unknown":
        if primary[0] == "arithmetic":
            return (
                "unknown",
                "",
                max(primary[2], reviewed[2]),
                f"{primary_detail}；算术题补充识别无有效结果：{reviewed_detail}",
                False,
            )
        return (
            *primary,
            f"{primary_detail}；补充识别无有效结果：{reviewed_detail}",
            False,
        )
    if primary[:2] == reviewed[:2]:
        agreement_label = (
            "算术答案一致"
            if primary[0] == "arithmetic"
            else f"{len(primary[1])} 位字符一致"
        )
        return (
            primary[0],
            primary[1],
            max(primary[2], reviewed[2]),
            (
                f"原色/灰度与二值图识别结果一致（{agreement_label}），"
                f"模型置信度 {max(primary[2], reviewed[2])}"
            ),
            True,
        )
    if primary[0] == "arithmetic" or reviewed[0] == "arithmetic":
        return (
            "unknown",
            "",
            max(primary[2], reviewed[2]),
            "算术题两路识别结果不一致，已放弃本次结果",
            False,
        )
    selected = reviewed if reviewed[2] > primary[2] else primary
    selected_source = "补充识别" if selected is reviewed else "主识别"
    return (
        selected[0],
        selected[1],
        selected[2],
        f"两路结果不一致，采用置信度较高的{selected_source}结果",
        False,
    )


def fill_captcha_answer(page: Any, answer: str) -> bool:
    """将识别结果填入验证码输入框。成功返回 True。"""
    script = """
const input = document.querySelector('input[name="yzm"]:not([type="hidden"])');
if (!input) return false;
const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "value"
).set;
setter.call(input, arguments[0]);
input.dispatchEvent(new Event("input", {bubbles: true}));
input.dispatchEvent(new Event("change", {bubbles: true}));
return true;
"""
    result = page.run_js(script, answer)
    return bool(result)


def click_chsi_query_button(page: Any) -> bool:
    """点击学信网查询页面的提交按钮。成功返回 True。"""
    script = """
// 辅助函数：触发完整鼠标事件（用于 Vue.js/iView 框架）
function triggerClick(el) {
    // A native click already dispatches the framework click event. Sending a
    // second synthetic click can open duplicate QR/result tabs.
    el.click();
}

// 策略 1: 精确匹配"免费查询"按钮
const allButtons = document.querySelectorAll('button');
for (const btn of allButtons) {
    const text = (btn.textContent || '').trim();
    if (text === '免费查询') {
        triggerClick(btn);
        return true;
    }
}

// 策略 2: 查找包含"免费查询"的任意元素
const allElements = document.querySelectorAll('*');
for (const el of allElements) {
    const text = (el.textContent || el.value || '').trim();
    if (text === '免费查询' && el.tagName !== 'BODY' && el.tagName !== 'HTML') {
        triggerClick(el);
        return true;
    }
}

// 策略 3: 标准表单提交按钮
const standardSelectors = [
  'input[type="submit"]',
  'button[type="submit"]',
  'input[value="查询"]',
  'input[value="免费查询"]',
  '.query-btn',
  '#queryButton',
  '#tj'
];
for (const sel of standardSelectors) {
  try {
    const btn = document.querySelector(sel);
    if (btn) {
      triggerClick(btn);
      return true;
    }
  } catch(e) {}
}

return false;
"""
    result = page.run_js(script)
    return bool(result)


def read_chsi_page_snapshot(page: Any) -> dict[str, str]:
    """Read the facts used to classify one live CHSI tab."""
    script = """
const visibleValue = name => {
  const input = document.querySelector(`input[name="${name}"]:not([type="hidden"])`);
  return input ? String(input.value || '') : '';
};
return {
  text: document.body ? (document.body.innerText || '') : '',
  url: location.href || '',
  certificate_number: visibleValue('zsbh'),
  name: visibleValue('xm'),
  captcha: visibleValue('yzm')
};
"""
    result = page.run_js(script)
    if not isinstance(result, dict):
        return {"text": "", "url": str(getattr(page, "url", "") or "")}
    return {
        str(key): str(value or "")
        for key, value in result.items()
    }


def classify_chsi_page_state(snapshot: dict[str, Any]) -> str:
    """Classify the current CHSI page from visible facts, not workflow history."""
    text = re.sub(r"\s+", "", str(snapshot.get("text") or ""))
    url = str(snapshot.get("url") or "").lower()
    if any(marker in text for marker in _CHSI_CAPTCHA_ERROR_MARKERS):
        return "captcha_error"
    if any(marker in text for marker in _CHSI_NOT_FOUND_MARKERS):
        return "not_found"
    if classify_chsi_terminal_result(text) == "record":
        return "record"
    if any(marker in text for marker in _CHSI_QR_EXPIRED_MARKERS):
        return "qr_expired"
    if is_chsi_qr_confirmation_text(text) or "/qrcode.do" in url:
        return "qr_waiting"
    if "/query.do" in url or "/queryinfo.do" in url:
        values = (
            str(snapshot.get("certificate_number") or "").strip(),
            str(snapshot.get("name") or "").strip(),
            str(snapshot.get("captcha") or "").strip(),
        )
        return "query_filled" if any(values) else "query_empty"
    return "unknown"


def refresh_chsi_qr_code(page: Any) -> bool:
    """Refresh an expired CHSI QR code without restarting captcha validation."""
    script = r"""
const visible = element => {
  if (!element) return false;
  const style = window.getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden'
    && rect.width > 0 && rect.height > 0;
};
for (const element of document.querySelectorAll('button, a, span, div')) {
  const text = String(element.textContent || '').replace(/\s+/g, '');
  if (text !== '点击刷新' || !visible(element)) continue;
  element.click();
  return true;
}
return false;
"""
    return bool(page.run_js(script))


def check_query_result(page: Any, timeout: float = 15.0) -> tuple[bool | None, str]:
    """检查查询结果：验证码是否正确、是否出现二维码。

    返回 (success, message)。
    success=True 表示明确出现二维码或结果页，False 表示明确验证码错误；
    None 表示当前页面结果尚未确认。
    """
    import time

    script = """
// 检查是否有错误提示
const errorKeywords = ['图片验证码输入有误', '验证码错误', '验证码不正确', '验证码失效', '验证码过期',
                       '输入不正确', '请重新输入', '验证失败', '验证码有误'];
const allText = document.body.innerText || '';
const currentUrl = location.href || '';
for (const keyword of errorKeywords) {
    if (allText.includes(keyword)) {
        return JSON.stringify({success: false, message: keyword});
    }
}
// 检查是否有二维码（iView 的二维码组件通常有特定 class）
const qrCodes = document.querySelectorAll('.ivu-qrcode, canvas, [class*="qrcode"], [class*="qr-code"]');
if (qrCodes.length > 0 || currentUrl.includes('/qrcode.do') || allText.includes('扫码验证')) {
    return JSON.stringify({success: true, message: '已出现二维码'});
}
if (currentUrl.includes('/xlresult.do')) {
    return JSON.stringify({success: true, message: '已出现查询结果'});
}
// 检查页面是否还在加载中
const loading = document.querySelector('.ivu-spin-show, .loading, [class*="loading"]');
if (loading) {
    return JSON.stringify({success: null, message: '加载中'});
}
// 没有明确结果
return JSON.stringify({success: null, message: '未检测到明确结果'});
"""

    # 轮询检测，最多等待 timeout 秒
    start_time = time.monotonic()
    while time.monotonic() - start_time < timeout:
        try:
            result = page.run_js(script)
            if result:
                import json
                data = json.loads(result)
                status = data.get("success")
                message = data.get("message", "")

                # 如果检测到明确结果（成功或失败），立即返回
                if status is not None:
                    return status, message

                # 如果还在加载中，继续等待
                if "加载中" in message:
                    time.sleep(0.2)
                    continue

                # 未检测到明确结果，再等一下
                time.sleep(0.2)
        except Exception:
            # 页面正在刷新/加载，短暂让出后立刻复查。
            time.sleep(0.25)

    return None, "未检测到明确结果"


def is_chsi_result_text(text: str, expected_name: str) -> bool:
    """Return whether visible CHSI text is a candidate-specific final result."""
    normalized_text = re.sub(r"\s+", "", str(text or ""))
    normalized_name = re.sub(r"\s+", "", str(expected_name or ""))
    if not normalized_name or normalized_name not in normalized_text:
        return False
    matched_labels = sum(
        1 for label in _CHSI_RESULT_STRONG_LABELS
        if label in normalized_text
    )
    return matched_labels >= 4


def classify_chsi_terminal_result(text: str) -> str:
    """Classify a bound CHSI tab as a positive or not-found final result."""
    normalized_text = re.sub(r"\s+", "", str(text or ""))
    if any(marker in normalized_text for marker in _CHSI_NOT_FOUND_MARKERS):
        return "not_found"
    matched_labels = sum(
        1 for label in _CHSI_RESULT_STRONG_LABELS
        if label in normalized_text
    )
    return "record" if matched_labels >= 4 else ""


def is_chsi_qr_confirmation_text(text: str) -> bool:
    """Return whether CHSI is waiting for the user's phone QR confirmation."""
    normalized_text = re.sub(r"\s+", "", str(text or ""))
    return any(
        marker in normalized_text
        for marker in _CHSI_QR_CONFIRMATION_MARKERS
    )


def read_chsi_result_page_text(page: Any) -> str:
    """Read visible text from a CHSI tab and its directly accessible frames."""
    try:
        frames = list(page.get_frames(timeout=0))
    except Exception:
        frames = []
    texts: list[str] = []
    last_error: Exception | None = None
    for context in [page, *frames]:
        try:
            text = context.run_js(
                "return document.body ? (document.body.innerText || '') : '';"
            )
        except Exception as error:
            last_error = error
            continue
        if text:
            texts.append(str(text))
    if not texts and last_error is not None:
        raise ChsiScreenshotError(f"结果页文字读取失败：{last_error}") from last_error
    return "\n".join(texts)


_CHSI_RESULT_CONTAINER_JS = r"""
const expectedName = String(arguments[0] || '').replace(/\s+/g, '');
const token = String(arguments[1] || '');
const labels = JSON.parse(String(arguments[2] || '[]'));
const attributeName = 'data-boss-education-result-capture';
const normalize = value => String(value || '').replace(/\s+/g, '');
const visible = element => {
  if (!element) return false;
  const style = window.getComputedStyle(element);
  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
    return false;
  }
  const rect = element.getBoundingClientRect();
  return rect.width >= 320 && rect.height >= 180;
};

document.querySelectorAll(`[${attributeName}]`).forEach(
  element => element.removeAttribute(attributeName)
);
if (!expectedName) return {matched: false, reason: 'missing-name'};

const candidates = Array.from(
  document.querySelectorAll('main, article, section, table, [role="main"], div')
);
if (document.body) candidates.push(document.body);
let best = null;
for (const element of candidates) {
  if (!visible(element)) continue;
  const text = normalize(element.innerText || element.textContent || '');
  if (!text.includes(expectedName)) continue;
  const hits = labels.reduce((count, label) => count + (text.includes(label) ? 1 : 0), 0);
  if (hits < 4) continue;
  const rect = element.getBoundingClientRect();
  const area = Math.max(1, rect.width * rect.height);
  const score = area + Math.max(0, text.length - 3000) * 20;
  if (!best || score < best.score) {
    best = {element, score, hits, textLength: text.length};
  }
}
if (!best) return {matched: false, reason: 'not-result'};
best.element.setAttribute(attributeName, token);
return {
  matched: true,
  token,
  keyword_count: best.hits,
  text_length: best.textLength
};
"""


def capture_chsi_result_png(page: Any, expected_name: str) -> bytes:
    """Capture the complete final-result page as PNG bytes.

    The capture uses Chromium content APIs, so browser tabs, address bars, the
    desktop, and the taskbar are never part of the image.
    """
    clean_name = str(expected_name or "").strip()
    if not clean_name:
        raise ChsiResultNotReadyError("缺少候选人姓名，无法确认结果页归属")
    try:
        page.set.activate()
    except Exception as error:
        raise ChsiScreenshotError(f"结果页标签激活失败：{error}") from error
    try:
        frames = list(page.get_frames(timeout=0))
    except Exception:
        frames = []
    contexts = [page, *frames]
    strong_labels_json = json.dumps(
        _CHSI_RESULT_STRONG_LABELS,
        ensure_ascii=False,
    )
    successful_probes = 0
    last_probe_error: Exception | None = None
    for index, context in enumerate(contexts):
        token = hashlib.sha256(
            f"{clean_name}\0{id(page)}\0{index}".encode("utf-8")
        ).hexdigest()[:16]
        try:
            probe = context.run_js(
                _CHSI_RESULT_CONTAINER_JS,
                clean_name,
                token,
                strong_labels_json,
            )
            successful_probes += 1
        except Exception as error:
            last_probe_error = error
            continue
        if not isinstance(probe, dict) or not probe.get("matched"):
            continue

        try:
            raw = page.get_screenshot(
                as_bytes="png",
                full_page=True,
            )
            if not isinstance(raw, (bytes, bytearray)) or not raw:
                raise RuntimeError("浏览器未返回截图数据")
            return bytes(raw)
        except Exception as error:
            raise ChsiScreenshotError(f"完整结果页截图失败：{error}") from error
        finally:
            try:
                context.run_js(
                    "document.querySelectorAll(arguments[0]).forEach("
                    "element => element.removeAttribute(arguments[1]));",
                    f'[{_CHSI_RESULT_CAPTURE_ATTR}="{token}"]',
                    _CHSI_RESULT_CAPTURE_ATTR,
                )
            except Exception:
                pass
    if not successful_probes and last_probe_error is not None:
        raise ChsiScreenshotError(
            f"结果页检测失败：{last_probe_error}"
        ) from last_probe_error
    raise ChsiResultNotReadyError("尚未检测到手机确认后的学历查询结果")


def _trim_uniform_border(image: Image.Image, *, threshold: int = 12) -> Image.Image:
    """Trim a large near-uniform border while preserving a small safe edge."""
    if image.width < 40 or image.height < 40:
        return image
    background = Image.new("RGB", image.size, image.getpixel((0, 0)))
    difference = ImageChops.difference(image, background).convert("L")
    mask = difference.point(lambda value: 255 if value > threshold else 0)
    bbox = mask.getbbox()
    if not bbox:
        return image
    left, top, right, bottom = bbox
    content_width = right - left
    content_height = bottom - top
    if content_width < image.width * 0.2 or content_height < image.height * 0.2:
        return image
    safety = 12
    return image.crop((
        max(0, left - safety),
        max(0, top - safety),
        min(image.width, right + safety),
        min(image.height, bottom + safety),
    ))


def normalize_chsi_screenshot_png(
    raw_png: bytes,
    *,
    output_width: int = CHSI_SCREENSHOT_WIDTH,
    padding: int = CHSI_SCREENSHOT_PADDING,
) -> bytes:
    """Trim and normalize a CHSI result screenshot to a fixed-width PNG."""
    if output_width <= padding * 2:
        raise ValueError("截图宽度必须大于两侧留白")
    try:
        with Image.open(io.BytesIO(raw_png)) as source:
            if source.mode in {"RGBA", "LA"} or "transparency" in source.info:
                rgba = source.convert("RGBA")
                white = Image.new("RGBA", rgba.size, "white")
                image = Image.alpha_composite(white, rgba).convert("RGB")
            else:
                image = source.convert("RGB")
    except Exception as error:
        raise ChsiScreenshotError(f"截图图片无法读取：{error}") from error
    image = _trim_uniform_border(image)
    content_width = output_width - padding * 2
    scale = content_width / max(1, image.width)
    content_height = max(1, round(image.height * scale))
    resized = image.resize(
        (content_width, content_height),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new(
        "RGB",
        (output_width, content_height + padding * 2),
        "white",
    )
    canvas.paste(resized, (padding, padding))
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True, dpi=(144, 144))
    return buffer.getvalue()


def _safe_screenshot_name_component(value: str, *, fallback: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value or "").strip())
    text = re.sub(r"\s+", " ", text).rstrip(" .")[:50]
    if not text:
        text = fallback
    if text.upper() in _WINDOWS_RESERVED_NAMES:
        text = f"_{text}"
    return text


def build_chsi_screenshot_filename(name: str, certificate_number: str) -> str:
    """Build a deterministic, screenshot-spec-aware privacy-safe filename."""
    clean_name = _safe_screenshot_name_component(name, fallback="未命名")
    compact_number = re.sub(r"\s+", "", str(certificate_number or ""))
    tail = _safe_screenshot_name_component(
        compact_number[-6:],
        fallback="未知",
    )
    digest = hashlib.sha256(
        (
            f"{str(name or '').strip()}\0{compact_number}"
            f"\0{CHSI_SCREENSHOT_WIDTH}"
        ).encode("utf-8")
    ).hexdigest()[:8]
    return f"{clean_name}_证书尾号{tail}_学历核验_{digest}.png"


def is_valid_chsi_screenshot(path: str | Path) -> bool:
    """Validate that an existing output is a readable normalized PNG."""
    screenshot_path = Path(path)
    if not screenshot_path.is_file() or screenshot_path.suffix.lower() != ".png":
        return False
    try:
        with Image.open(screenshot_path) as image:
            image_format = image.format
            image_width = image.width
            image.verify()
            return image_format == "PNG" and image_width == CHSI_SCREENSHOT_WIDTH
    except (OSError, ValueError):
        return False


def save_chsi_result_screenshot(raw_png: bytes, path: str | Path) -> Path:
    """Normalize and exclusively create one screenshot without overwriting."""
    target = Path(path)
    if not target.parent.is_dir():
        raise ChsiScreenshotError("截图保存目录不存在")
    normalized = normalize_chsi_screenshot_png(raw_png)
    try:
        with target.open("xb") as stream:
            try:
                stream.write(normalized)
                stream.flush()
                os.fsync(stream.fileno())
            except Exception:
                stream.close()
                target.unlink(missing_ok=True)
                raise
    except FileExistsError:
        raise ChsiScreenshotError("同名截图已经存在，未覆盖")
    except OSError as error:
        raise ChsiScreenshotError(f"截图保存失败：{error}") from error
    return target
