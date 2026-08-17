# -*- coding: utf-8 -*-
"""AI-enhanced profile extraction for externally imported resumes.

Regex extraction (``bossmaster.extract_summary_info``) stays the primary
source. When the import dialog switch is on, this module asks an
OpenAI-compatible chat model for the same profile fields and merges the
result with strict rules: AI only fills blanks, never overrides regex
values; conflicts keep the regex value and are reported for manual review.

Transport intentionally mirrors ``job_ai_parser._call_chat_completion``
instead of reusing ``llm_eval._call_llm_api``: the evaluation API forces
tool-calling mode and an adjustment-shaped response, which would corrupt
plain profile payloads.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

import requests

from ai_adapter import build_request
from filtering import normalize_candidate_gender
from llm_eval import _repair_json_text, _resolve_request_timeout


PROFILE_FIELDS = (
    "salary", "gender", "age", "exp_years", "education",
    "city", "job_status", "company", "school",
)
PROFILE_FIELD_LABELS = {
    "salary": "薪资",
    "gender": "性别",
    "age": "年龄",
    "exp_years": "工作经验",
    "education": "学历",
    "city": "工作地点",
    "job_status": "求职状态",
    "company": "最近公司",
    "school": "毕业学校",
}

_RESUME_TEXT_LIMIT = 8000
_PROFILE_MAX_TOKENS = 800
_PROFILE_TEMPERATURE = 0.1
_PROFILE_MAX_RETRIES = 2  # 首次 + 1 次退避重试
_PROFILE_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

_EDUCATION_VALUES = ("博士", "硕士", "本科", "大专", "高中", "中专")
_EDUCATION_ALIASES = {
    "专科": "大专",
    "学士": "本科",
    "研究生": "硕士",
    "mba": "硕士",
    "emba": "硕士",
}
_SALARY_RANGE_RE = re.compile(r"^(\d{1,3}(?:\.\d+)?)\s*[-~～—–]\s*(\d{1,3}(?:\.\d+)?)[Kk]$")
_SALARY_SINGLE_RE = re.compile(r"^(\d{1,3}(?:\.\d+)?)[Kk]$")


def build_profile_messages(resume_text: str) -> list[dict[str, str]]:
    """Build the extraction prompt; the regex draft is NOT fed to the model.

    Independent extraction is what makes conflict detection meaningful —
    showing the regex result would anchor the model and hide real
    disagreements.
    """
    excerpt = str(resume_text or "")[:_RESUME_TEXT_LIMIT]
    current_year = datetime.now().year
    system = (
        "你是简历信息提取助手。从简历原文中提取候选人画像字段，只提取原文明确写出的信息，"
        "禁止推测。只输出一个严格 JSON 对象，不要输出任何其他文字、解释或 Markdown 标记。"
    )
    user = (
        "从以下简历全文中提取这些字段，原文未明确写出时填空字符串：\n"
        '  "salary": 期望薪资，格式 "15-25K"（K=千元/月）或 "15K" 或 "面议"；'
        "原文是元/月口径则换算为 K，原文是年薪则除以 12 换算为月薪 K\n"
        '  "gender": "男" 或 "女"\n'
        f'  "age": 整数年龄；原文只写出生日期时按当前年份 {current_year} 换算\n'
        '  "exp_years": 整数工作年限；原文写 "X年经验" 直接取，否则按工作起始年份换算\n'
        '  "education": 最高学历，取值 博士/硕士/本科/大专/高中/中专\n'
        '  "city": 期望工作城市（原文明确写出的期望地点，不是现居地）\n'
        '  "job_status": 求职状态，取值 离职/在职/应届/在校/暂不考虑\n'
        '  "company": 最近一家任职公司（雇主）全称；项目名称、项目客户方、合作方'
        "都不是任职公司，原文只有项目经历、没有明确写出雇主时填空字符串\n"
        '  "school": 最高学历毕业学校全称\n'
        "只输出 JSON，示例："
        '{"salary":"15-25K","gender":"男","age":30,"exp_years":6,"education":"本科",'
        '"city":"上海","job_status":"离职","company":"某公司","school":"某大学"}\n\n'
        f"简历全文：\n{excerpt}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_profile_payload(text: str) -> dict[str, Any]:
    """Parse the model response into a dict, raising ``ValueError`` on failure."""
    if not text or not text.strip():
        raise ValueError("AI 返回为空")
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    braced = re.search(r"\{.*\}", text, re.DOTALL)
    if braced:
        candidates.append(braced.group(0))
    repaired = _repair_json_text(text)
    candidates.append(repaired)
    repaired_braced = re.search(r"\{.*\}", repaired, re.DOTALL)
    if repaired_braced:
        candidates.append(repaired_braced.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            raise ValueError("AI JSON 顶层必须是对象")
        return data
    raise ValueError("AI 返回不是可解析的 JSON")


def normalize_ai_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize raw model output; illegal values are dropped, never guessed."""
    info: dict[str, Any] = {}
    normalizers = {
        "salary": _normalize_salary,
        "gender": _normalize_gender,
        "age": lambda value: _normalize_bounded_int(value, 16, 80),
        "exp_years": lambda value: _normalize_bounded_int(value, 0, 50),
        "education": _normalize_education,
        "city": _normalize_text_value,
        "job_status": _normalize_job_status,
        "company": _normalize_text_value,
        "school": _normalize_text_value,
    }
    for field in PROFILE_FIELDS:
        value = normalizers[field](payload.get(field))
        if value:
            info[field] = value
    return info


def merge_profile(
    regex_info: dict[str, Any] | None,
    ai_info: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge AI values into the regex profile: fill blanks, keep conflicts.

    Returns ``{"info", "filled", "conflicts"}``; ``filled`` entries carry
    ``field/label/value`` and ``conflicts`` entries carry
    ``field/label/rule/ai``. The regex value always wins on conflict.
    """
    info = dict(regex_info or {})
    filled: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for field in PROFILE_FIELDS:
        ai_value = (ai_info or {}).get(field)
        if ai_value is None or not str(ai_value).strip():
            continue
        rule_value = info.get(field)
        if rule_value is None or not str(rule_value).strip():
            info[field] = ai_value
            filled.append({
                "field": field,
                "label": PROFILE_FIELD_LABELS[field],
                "value": ai_value,
            })
            continue
        if _values_match(field, rule_value, ai_value):
            continue
        conflicts.append({
            "field": field,
            "label": PROFILE_FIELD_LABELS[field],
            "rule": rule_value,
            "ai": ai_value,
        })
    return {"info": info, "filled": filled, "conflicts": conflicts}


def extract_profile_with_ai(
    resume_text: str,
    regex_info: dict[str, Any],
    api_config: dict[str, Any],
    api_key: str,
    *,
    stop_event: Any = None,
) -> dict[str, Any] | None:
    """Run AI profile extraction and merge with the regex result.

    Returns ``{"info", "filled", "conflicts", "error"}``. Any transport or
    parsing failure is swallowed into ``error`` (≤120 chars) with ``info``
    left as the regex result, so import is never blocked. A set
    ``stop_event`` returns ``None`` — a silent skip, distinct from failure.
    """
    if stop_event is not None and stop_event.is_set():
        return None
    fallback = {
        "info": dict(regex_info or {}),
        "filled": [],
        "conflicts": [],
        "error": "",
    }
    try:
        messages = build_profile_messages(resume_text)
        content = _call_profile_chat(api_config, api_key, messages)
        payload = parse_profile_payload(content)
        ai_info = normalize_ai_profile(payload)
        merged = merge_profile(regex_info, ai_info)
        merged["error"] = ""
        return merged
    except Exception as exc:  # 增强失败不得阻断导入，一律落 error
        fallback["error"] = str(exc)[:120]
        return fallback


def _call_profile_chat(
    api_config: dict[str, Any],
    api_key: str,
    messages: list[dict[str, str]],
) -> str:
    """Thin chat-completions transport mirroring ``job_ai_parser``."""
    try:
        import certifi

        verify_path: str | bool = certifi.where()
    except ImportError:
        verify_path = True

    # 画像提取是短输出任务，关闭推理以压缩延迟（与 JD 解析同理）。
    url, headers, payload, _protocol = build_request(
        api_config,
        api_key,
        messages,
        max_tokens=_PROFILE_MAX_TOKENS,
        temperature=_PROFILE_TEMPERATURE,
        disable_thinking=True,
    )
    timeout = _resolve_request_timeout(api_config)

    last_error = ""
    for attempt in range(_PROFILE_MAX_RETRIES):
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
                verify=verify_path,
            )
        except requests.exceptions.ConnectTimeout as exc:
            last_error = f"AI 连接超时：{timeout[0]} 秒内无法建立连接"
            if attempt < _PROFILE_MAX_RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise ValueError(last_error) from exc
        except requests.exceptions.ReadTimeout as exc:
            last_error = f"AI 读取超时：模型服务 {timeout[1]} 秒内未返回响应"
            if attempt < _PROFILE_MAX_RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise ValueError(last_error) from exc
        except requests.exceptions.Timeout as exc:
            last_error = f"AI 请求超时（connect={timeout[0]}s, read={timeout[1]}s）"
            if attempt < _PROFILE_MAX_RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise ValueError(last_error) from exc
        except requests.exceptions.SSLError as exc:
            raise ValueError("AI SSL 证书错误：请检查 Base URL、代理或证书配置") from exc
        except requests.exceptions.ConnectionError as exc:
            last_error = "AI 连接失败：无法连接到 Base URL，或连接被代理/服务端重置"
            if attempt < _PROFILE_MAX_RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise ValueError(last_error) from exc
        except requests.exceptions.RequestException as exc:
            raise ValueError(f"AI 请求异常：{type(exc).__name__}: {str(exc)[:100]}") from exc

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as exc:
                raise ValueError("AI 返回不是合法 JSON 响应") from exc
            message = data.get("choices", [{}])[0].get("message", {})
            content = str(message.get("content", "") or "")
            if not content.strip():
                # 推理模型可能把输出放在 reasoning_content；花括号提取交给
                # parse_profile_payload 处理。
                content = str(message.get("reasoning_content", "") or "")
            if not content.strip():
                raise ValueError("AI 返回为空")
            return content

        last_error = _format_http_error(response)
        if (
            response.status_code in _PROFILE_RETRYABLE_STATUS
            and attempt < _PROFILE_MAX_RETRIES - 1
        ):
            time.sleep(0.8 * (attempt + 1))
            continue
        raise ValueError(last_error)

    raise ValueError(last_error or "AI 请求失败")


def _format_http_error(response: requests.Response) -> str:
    status = response.status_code
    body = (response.text or "").strip()[:160]
    if status in {401, 403}:
        return f"AI 鉴权失败 HTTP {status}：请检查 API Key、服务商权限或模型开通状态"
    if status == 404:
        return "AI 接口不存在 HTTP 404：请检查 Base URL 是否为 OpenAI 兼容接口地址"
    if status == 429:
        return "AI 请求限流 HTTP 429：服务商额度不足、并发过高或触发限流"
    if status in {500, 502, 503, 504}:
        return f"AI 服务端错误 HTTP {status}：模型服务暂时不可用"
    return f"AI HTTP {status}: {body}"


def _format_k(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def _normalize_salary(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return ""
    if "面议" in text:
        return "面议"
    match = _SALARY_RANGE_RE.match(text)
    if match:
        low, high = float(match.group(1)), float(match.group(2))
        if low > high:
            return ""
        return f"{_format_k(low)}-{_format_k(high)}K"
    match = _SALARY_SINGLE_RE.match(text)
    if match:
        return f"{_format_k(float(match.group(1)))}K"
    return ""


def _normalize_gender(value: Any) -> str:
    return normalize_candidate_gender(value) or ""


def _first_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group()) if match else None


def _normalize_bounded_int(value: Any, low: int, high: int) -> str:
    number = _first_number(value)
    if number is None:
        return ""
    normalized = int(number)
    return str(normalized) if low <= normalized <= high else ""


def _normalize_education(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in _EDUCATION_VALUES:
        return text
    alias = _EDUCATION_ALIASES.get(text) or _EDUCATION_ALIASES.get(text.lower())
    if alias:
        return alias
    for known in _EDUCATION_VALUES:
        if known in text:
            return known
    return ""


def _normalize_job_status(value: Any) -> str:
    """Map to the tightened enum; unknown wording is dropped.

    ``filtering`` hard-rejects any status containing "不考虑", so loose
    AI phrasing must never pass through unmapped.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if "不考虑" in text:
        return "暂不考虑"
    if "离职" in text:
        return "离职"
    if "在职" in text:
        return "在职"
    if "应届" in text:
        return "应届"
    if "在校" in text:
        return "在校"
    return ""


def _normalize_text_value(value: Any, max_len: int = 40) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text or len(text) > max_len:
        return ""
    if not re.search(r"[一-龥]", text):
        return ""
    return text


def _values_match(field: str, rule_value: Any, ai_value: Any) -> bool:
    left = re.sub(r"\s+", "", str(rule_value))
    right = re.sub(r"\s+", "", str(ai_value))
    if field in {"age", "exp_years"}:
        try:
            return float(left) == float(right)
        except (TypeError, ValueError):
            return False
    if field == "city":
        left = left.removesuffix("市")
        right = right.removesuffix("市")
    return left.casefold() == right.casefold()
