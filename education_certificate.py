"""毕业证书识别（图片走视觉模型 / PDF 走文本模型）与学信网字段校验。"""
from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from ai_adapter import build_request, detect_protocol, friendly_http_error, normalize_response


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SUPPORTED_PDF_SUFFIXES = {".pdf"}
MAX_IMAGE_SIDE = 1800
JPEG_QUALITY = 88
CAPTCHA_AUTO_SUBMIT_MIN_CONFIDENCE = 80
CHSI_QUERY_URL = "https://www.chsi.com.cn/xlcx/lscx/query.do"
XIAOMI_VISION_MODEL = "mimo-v2.5"

_SYSTEM_PROMPT = """你是毕业证书字段识别器。只读取图片中明确可见的内容，不猜测、不补全。
返回严格 JSON 对象，不要使用 Markdown：
{"name":"","certificate_number":"","school":"","major":"","rotation":0,"rotation_confidence":0,"confidence":0,"warnings":[]}

规则：
1. name 只填写毕业证书持有人的姓名，不要填写校长、院长或学校名称。
2. certificate_number 只填写“证书编号”或“电子注册号”对应的完整编号。
3. school 填写毕业院校全称，major 填写证书上的专业名称。
4. 无法确认时字段留空，并在 warnings 中说明。
5. 第二张图片是方向对照图，四格分别标注 ROTATE 0/90/180/270 CW。rotation 必须填写其中“文字正常朝上、可自然阅读”的那一格角度。
6. rotation_confidence 是方向判断置信度（0-100）；低于 80 或无法可靠判断时 rotation 必须返回 0。
7. confidence 是 0 到 100 的整数，表示文字字段整体识别置信度。
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
    "minimax-vl", "minimax-m2",
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


def prepare_image_data_url(path: str | Path) -> str:
    """纠正方向、限制尺寸并编码为适合视觉模型的 JPEG data URL。"""
    image_path = validate_image_path(path)
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


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


def build_vision_messages(
    api_config: dict[str, Any],
    data_url: str,
    orientation_data_url: str | None = None,
) -> list[dict[str, Any]]:
    """按服务商协议构造图片消息。"""
    orientation_data_url = orientation_data_url or data_url
    if detect_protocol(api_config) == "anthropic":
        media_type, encoded = data_url.split(";base64,", 1)
        orientation_media_type, orientation_encoded = orientation_data_url.split(";base64,", 1)
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type.removeprefix("data:"),
                            "data": encoded,
                        },
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": orientation_media_type.removeprefix("data:"),
                            "data": orientation_encoded,
                        },
                    },
                    {"type": "text", "text": "第一张图识别字段；第二张四格对照图选择文字正常朝上的旋转角度。"},
                ],
            },
        ]
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "第一张图识别字段；第二张四格对照图选择文字正常朝上的旋转角度。"},
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "image_url", "image_url": {"url": orientation_data_url}},
            ],
        },
    ]


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError("AI 未返回可解析的 JSON")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("AI 返回结果不是 JSON 对象")
    return value


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
    )


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
) -> CertificateRecognition:
    """调用当前视觉模型识别毕业证书图片。"""
    vision_config = resolve_vision_api_config(api_config)
    data_url = prepare_image_data_url(path)
    orientation_data_url = prepare_orientation_sheet_data_url(path)
    messages = build_vision_messages(vision_config, data_url, orientation_data_url)
    base_url = str(vision_config.get("base_url") or "").lower()
    max_tokens = 4096 if "api.kimi.com/coding" in base_url else 2048
    parsed = _invoke_model(
        vision_config, api_key, messages, timeout=timeout, max_tokens=max_tokens,
    )
    return normalize_recognition(parsed, str(vision_config.get("model") or ""))


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
验证码有两种类型：
1. 字母/数字混合型：由英文字母和阿拉伯数字组成（例如 aB3x、K9mP）
2. 算术型：一个简单算术题，包含加减乘除（例如 3+5=?、12÷4=?、7×8=?）

返回严格 JSON 对象，不要使用 Markdown：
{"type":"letter","answer":"aB3x","confidence":90}
或
{"type":"arithmetic","expression":"3+5","answer":"8","confidence":95}

规则：
- type 为 "letter" 时，answer 是图中可见的字母/数字组合，保留大小写。
- type 为 "arithmetic" 时，answer 是算术计算结果（纯数字，整数）。expression 是原题文字。
- confidence 是 0-100 的识别置信度。
- 如果看不清或无法识别，返回 {"type":"unknown","answer":"","confidence":0}
"""


def parse_captcha_result(payload: dict[str, Any]) -> tuple[str, str, int]:
    """解析验证码识别模型返回的 JSON 对象。

    返回 (captcha_type, answer, confidence)。
    captcha_type 为 "letter"、"arithmetic" 或 "unknown"。
    """
    captcha_type = str(payload.get("type") or "unknown").strip().lower()
    if captcha_type not in ("letter", "arithmetic"):
        captcha_type = "unknown"
    answer = str(payload.get("answer") or "").strip()
    try:
        confidence = int(payload.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))
    if captcha_type == "unknown" or not answer:
        return "unknown", "", confidence
    return captcha_type, answer, confidence


def build_captcha_messages(
    api_config: dict[str, Any], data_url: str
) -> list[dict[str, Any]]:
    """构造验证码识别的视觉消息（复用证书识别的协议判断逻辑）。"""
    if detect_protocol(api_config) == "anthropic":
        media_type, encoded = data_url.split(";base64,", 1)
        return [
            {"role": "system", "content": _CAPTCHA_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type.removeprefix("data:"),
                            "data": encoded,
                        },
                    },
                    {"type": "text", "text": "请识别图片中的验证码内容。"},
                ],
            },
        ]
    return [
        {"role": "system", "content": _CAPTCHA_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请识别图片中的验证码内容。"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


# -- 验证码图片捕获 ----------------------------------------------------------

_CAPTCHA_FIND_JS = """
const input = document.querySelector('input[name="yzm"]');
if (!input) return null;
let img = null;
const selectors = [
  '.yzm-box', '.captcha-box', '.verify-img', '.imgCode',
  '.code-img', '.yzm_img', '.validate-img'
];
for (const sel of selectors) {
  const c = input.closest(sel);
  if (c) { img = c.querySelector('img'); if (img) break; }
}
if (!img) {
  let p = input.parentElement;
  for (let i = 0; i < 5 && p; i++) {
    img = p.querySelector('img');
    if (img) break;
    p = p.parentElement;
  }
}
if (!img) return null;
const rect = img.getBoundingClientRect();
return {
  src: img.src,
  left: rect.left, top: rect.top,
  width: rect.width, height: rect.height
};
"""


def _image_bytes_to_data_url(raw_bytes: bytes) -> str:
    """将小尺寸验证码放大增强后转为无损 PNG data URL。"""
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
    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Contrast(image).enhance(1.35)
    image = image.filter(ImageFilter.SHARPEN)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def capture_captcha_image(page: Any) -> str:
    """从浏览器页面截取验证码图片，返回 data URL。

    策略优先级：
    1. 直接提取 img src 的 data URL（质量最高）
    2. DrissionPage 元素截图（稳定可靠）
    3. 全页截图后按元素坐标裁剪（最终降级）

    失败时抛 RuntimeError。
    """
    import tempfile

    # 定位验证码图片元素
    info = page.run_js(_CAPTCHA_FIND_JS)
    if not info:
        raise RuntimeError("无法定位验证码图片元素，页面结构可能已变化")

    # 策略 1：直接提取 img src（如果是 data URL）
    src = info.get("src") or ""
    if src.startswith("data:image"):
        try:
            media_type, encoded = src.split(";base64,", 1)
            raw_bytes = base64.b64decode(encoded)
            return _image_bytes_to_data_url(raw_bytes)
        except Exception:
            pass

    # 策略 2：DrissionPage 元素截图（稳定可靠）
    bbox = (
        info.get("left", 0), info.get("top", 0),
        info.get("width", 0), info.get("height", 0),
    )
    tmp_path = Path(tempfile.mktemp(suffix=".png"))
    for method_name in ("get_screenshot", "save_screenshot"):
        try:
            ele = page.ele("css:input[name='yzm']")
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
                raw_bytes = tmp_path.read_bytes()
                tmp_path.unlink(missing_ok=True)
                return _image_bytes_to_data_url(raw_bytes)
        except Exception:
            continue
    tmp_path.unlink(missing_ok=True)

    # 策略 3：全页截图 + 区域裁剪
    if bbox[2] > 0 and bbox[3] > 0:
        full_path = Path(tempfile.mktemp(suffix=".png"))
        try:
            for m in ("get_screenshot", "save_screenshot", "screenshot"):
                method = getattr(page, m, None)
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
                        buf = io.BytesIO()
                        cropped.convert("RGB").save(buf, format="PNG", optimize=True)
                        full_path.unlink(missing_ok=True)
                        return _image_bytes_to_data_url(buf.getvalue())
        finally:
            full_path.unlink(missing_ok=True)

    raise RuntimeError("验证码图片截取失败，所有策略均未成功")


def recognize_captcha(
    data_url: str,
    api_config: dict[str, Any],
    api_key: str,
    *,
    timeout: int = 60,
) -> tuple[str, str, int]:
    """调用视觉模型识别验证码图片。返回 (captcha_type, answer, confidence)。"""
    vision_config = resolve_vision_api_config(api_config)
    messages = build_captcha_messages(vision_config, data_url)
    base_url = str(vision_config.get("base_url") or "").lower()
    max_tokens = 4096 if "api.kimi.com/coding" in base_url else 2048
    parsed = _invoke_model(
        vision_config,
        api_key,
        messages,
        timeout=timeout,
        max_tokens=max_tokens,
    )
    return parse_captcha_result(parsed)


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
    // 方法1: 直接调用 click()
    try {
        el.click();
    } catch(e) {}

    // 方法2: 触发鼠标事件序列（Vue.js 需要）
    const events = ['mousedown', 'mouseup', 'click'];
    for (const eventType of events) {
        const event = new MouseEvent(eventType, {
            bubbles: true,
            cancelable: true,
            view: window
        });
        el.dispatchEvent(event);
    }

    // 方法3: 如果是 iView 按钮，尝试触发其内部点击
    if (el.classList.contains('ivu-btn')) {
        const innerBtn = el.querySelector('span') || el;
        innerBtn.click();
    }
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


def check_query_result(page: Any, timeout: float = 15.0) -> tuple[bool, str]:
    """检查查询结果：验证码是否正确、是否出现二维码。

    返回 (success, message)。
    success=True 表示查询成功（出现二维码或无错误），False 表示验证码错误。
    """
    import time

    # 先等待页面开始加载（点击查询后页面需要时间响应和刷新）
    time.sleep(3.0)

    script = """
// 检查是否有错误提示
const errorKeywords = ['图片验证码输入有误', '验证码错误', '验证码不正确', '验证码失效', '验证码过期',
                       '输入不正确', '请重新输入', '验证失败', '验证码有误'];
const allText = document.body.innerText || '';
for (const keyword of errorKeywords) {
    if (allText.includes(keyword)) {
        return JSON.stringify({success: false, message: keyword});
    }
}
// 检查是否有二维码（iView 的二维码组件通常有特定 class）
const qrCodes = document.querySelectorAll('.ivu-qrcode, canvas, [class*="qrcode"], [class*="qr-code"]');
if (qrCodes.length > 0) {
    return JSON.stringify({success: true, message: '已出现二维码'});
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
    start_time = time.time()
    error_count = 0
    while time.time() - start_time < timeout:
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
                    time.sleep(0.5)
                    continue

                # 未检测到明确结果，再等一下
                time.sleep(0.5)
        except Exception:
            # 页面正在刷新/加载，完全静默等待
            error_count += 1
            time.sleep(1.0)

    # 超时后仍未检测到明确结果，默认认为成功
    return True, "未检测到明确结果"
