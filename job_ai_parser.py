# -*- coding: utf-8 -*-
"""AI-assisted enhancement for parsed job requirements.

The regex parser remains the source of the initial config. This module asks an
OpenAI-compatible chat model for a bounded patch and merges only validated
fields back into the one-job config.
"""
from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from ai_adapter import build_request
from constants import CHINESE_NUMERALS, MAJOR_CITIES
from doc_parser import _extract_gender_requirement, skill_identity_key


AI_PARSE_TIMEOUT = (6, 80)
AI_PARSE_MAX_RETRIES = 2
AI_PARSE_MAX_TOKENS = 2000
AI_PARSE_TEMPERATURE = 0.1
AI_PARSE_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}

_EDU_VALUES = {"不限", "高中", "中专", "大专", "本科", "硕士", "博士"}
_GENDER_VALUES = {"不限", "男", "女"}
_NOISY_KEYWORD_RE = re.compile(
    r"^(?:AI|人工智能|API|Wind|Bloomberg|万得(?:API)?|彭博|数据库(?:技术)?|数据开发工具|"
    r"数据清洗|数据加工|因子|因子计算|因子结果|报表|报表开发|证券|证券行业)$",
    re.IGNORECASE,
)
_SOFT_TRAIT_RE = re.compile(r"服务意识|团队精神|学习能力|执行能力|沟通能力|责任心|抗压能力|主动性|积极性")
_GENERAL_EXPERIENCE_RE = re.compile(
    r"([0-9零一二三四五六七八九十两]+)\s*年\s*(?:以上|及以上|起|\+)?\s*(?:相关)?(?:工作)?经验"
)
_GENERAL_EXPERIENCE_RANGE_RE = re.compile(
    r"([0-9零一二三四五六七八九十两]+)\s*[-~～至]\s*"
    r"[0-9零一二三四五六七八九十两]+\s*年\s*(?:相关)?(?:工作)?经验"
)
_HARD_SECTION_HEADING_RE = re.compile(
    r"^(?:必要条件|硬性条件|硬性约束|硬性要求|必须条件|必备条件|一票否决)"
)
_OTHER_SECTION_HEADING_RE = re.compile(
    r"^(?:职位描述|岗位职责|工作职责|职位要求|岗位要求|任职要求|任职资格|"
    r"基本信息|优先条件|优先项|加分项)"
)
_EXPLICIT_HARD_MARKER_RE = re.compile(r"必须|硬性|一票否决|必备|不可缺少|缺一不可")


@dataclass
class AIParseEnhancementResult:
    """Result of AI enhancement."""

    success: bool
    config: dict[str, Any]
    reason: str = ""
    model: str = ""
    warnings: list[str] | None = None


def _normalize_warnings(raw_warnings: Any) -> list[str]:
    """Return one clean list item for each AI warning."""
    if isinstance(raw_warnings, str):
        values = [raw_warnings]
    elif isinstance(raw_warnings, (list, tuple)):
        values = raw_warnings
    else:
        return []

    normalized = []
    seen = set()
    split_pattern = re.compile(
        r"(?:\r?\n)+|[；;]+|(?<!\S)(?=\d{1,2}[.、)]\s*)"
    )
    prefix_pattern = re.compile(r"^\s*(?:[-*•·]\s*|\d{1,2}[.、)]\s*)")
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        for part in split_pattern.split(text):
            item = prefix_pattern.sub("", part).strip()
            if item and item not in seen:
                normalized.append(item)
                seen.add(item)
    return normalized


def enhance_config_with_ai(
    requirements_text: str,
    regex_config: dict[str, Any],
    api_config: dict[str, Any],
    api_key: str,
) -> AIParseEnhancementResult:
    """Enhance a regex-generated one-job config with an LLM patch.

    On any failure, returns success=False and the original regex_config copy.
    """
    base_config = copy.deepcopy(regex_config)
    if not requirements_text or not requirements_text.strip():
        return AIParseEnhancementResult(False, base_config, "需求文本为空")

    base_url = str(api_config.get("base_url", "")).rstrip("/")
    model = str(api_config.get("model", ""))
    if not base_url or not model or not api_key:
        return AIParseEnhancementResult(False, base_config, "AI 配置不完整")

    try:
        messages = _build_messages(requirements_text, base_config)
        content = _call_chat_completion(api_config, api_key, messages)
        patch = _parse_json_response(content)
        enhanced = _merge_patch(base_config, patch, requirements_text)
        warnings = _normalize_warnings(patch.get("warnings", []))
        return AIParseEnhancementResult(True, enhanced, "AI 增强完成", model=model, warnings=warnings)
    except Exception as exc:
        return AIParseEnhancementResult(False, base_config, str(exc)[:120], model=model)


def _build_messages(requirements_text: str, regex_config: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "你是招聘需求结构化解析助手。你只能基于原文和正则初稿做补充、纠错、归一化。"
        "不要虚构原文没有的信息。返回严格 JSON 对象，不要 Markdown，不要解释。"
    )
    user = (
        "目标：在正则解析初稿基础上增强岗位配置。\n\n"
        "关键规则：\n"
        "1. '优先'、'加分'、'更佳'类条件进入 preferred_keywords_add，不进入 keywords_add。\n"
        "2. 必要条件以本地规则为基线；只有原文明确写在必要条件/硬性约束段，或带有必须、硬性、"
        "一票否决等强约束词时，才可写入 required_conditions_add。普通任职要求不得提升为必要条件。\n"
        "3. 普通任职要求里的'具备/有/熟练掌握/精通 X 经验'进入 keywords_add。\n"
        "4. 学历最低门槛不要被'硕士优先'、'博士优先'覆盖。\n"
        "5. keywords 是核心技能匹配项，weight 只能 1-3；preferred_keywords 是优先加分项，bonus 默认 2，不要自行放大。\n\n"
        "6. AI、人工智能、万得/Wind、彭博/Bloomberg、API、数据库技术、数据清洗、因子计算、报表开发、证券行业"
        "这类泛化词、数据来源、职责产出或行业词不要加入 keywords。\n"
        "7. 本科及以上、3年以上工作经验这类基础门槛只放进 basic_info.edu/min_exp。\n"
        "8. salary_min、salary_max 的单位固定为 K/月整数，例如 12K 或 12000元都返回 12。"
        "正则初稿已有明确薪资时不要改写，只补充初稿缺失的薪资字段。\n"
        "9. warnings 中每个待确认事项必须单独作为一个数组元素，不要用分号合并多件事；"
        "直接说明用户需要确认什么，避免'属于 OR/AND 关系'等技术表述和同义重复。\n\n"
        "10. gender 只能依据原文明确的性别要求返回不限、男或女；不能根据岗位名称、行业或职责推测。\n\n"
        "返回 JSON schema：\n"
        "{\n"
        "  \"job_title\": \"可选，岗位名修正\",\n"
        "  \"basic_info\": {\"min_exp\": 可选整数, \"edu\": 可选枚举, \"gender\": 可选枚举, \"max_age\": 可选整数或null,"
        " \"work_location\": 可选字符串, \"salary_min\": 可选K/月整数或null, \"salary_max\": 可选K/月整数或null},\n"
        "  \"keywords_add\": [{\"name\":\"技能\", \"weight\":1-3}],\n"
        "  \"keywords_update\": [{\"name\":\"已有技能\", \"weight\":1-3}],\n"
        "  \"preferred_keywords_add\": [{\"name\":\"优先项\", \"bonus\":1-10}],\n"
        "  \"required_conditions_add\": [\"明确硬条件\", {\"type\":\"or|and\", \"items\":[\"项1\",\"项2\"], \"category\":\"可选\"}],\n"
        "  \"warnings\": [\"不确定或需要人工确认的点，用普通业务语言说明，不要出现 keywords、"
        "preferred_keywords、required_conditions、JSON 等内部字段名\"]\n"
        "}\n\n"
        "原始招聘需求：\n"
        f"{requirements_text}\n\n"
        "正则初稿 JSON：\n"
        f"{json.dumps(regex_config, ensure_ascii=False, indent=2)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_json_from_reasoning(reasoning: str) -> str:
    """从推理模型的 reasoning_content 中提取 JSON 内容。

    推理模型（如小米 mimo、DeepSeek-R1）可能把最终输出放在 reasoning_content 中，
    需要从中提取 JSON 块。支持以下格式：
    1. ```json ... ``` 代码块
    2. 纯 JSON 文本
    3. JSON 嵌在其他文字中（提取最外层的 {...}）
    """
    if not reasoning or not reasoning.strip():
        return ""

    # 尝试 1: 提取 ```json ... ``` 代码块
    code_block_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', reasoning, re.DOTALL)
    if code_block_match:
        return code_block_match.group(1).strip()

    # 尝试 2: 找到最外层的 {...} JSON 对象
    brace_start = reasoning.find('{')
    if brace_start >= 0:
        # 从后往前找最后一个 }
        brace_end = reasoning.rfind('}')
        if brace_end > brace_start:
            candidate = reasoning[brace_start:brace_end + 1]
            # 验证是否为合法 JSON
            try:
                json.loads(candidate)
                return candidate
            except (json.JSONDecodeError, ValueError):
                pass

    # 兜底：返回原文，让调用方处理
    return reasoning.strip()


def _call_chat_completion(api_config: dict[str, Any], api_key: str, messages: list[dict[str, str]]) -> str:
    try:
        import certifi

        verify_path: str | bool = certifi.where()
    except ImportError:
        verify_path = True

    # 请求构造统一走 ai_adapter.build_request（base_url 归一化 + 端点方言单点维护）。
    # 结构化 JD 解析关闭推理：实测 k3 推理 54-90 秒且长度不稳定，关闭后约 4 秒返回
    url, headers, payload, _protocol = build_request(
        api_config,
        api_key,
        messages,
        max_tokens=AI_PARSE_MAX_TOKENS,
        temperature=AI_PARSE_TEMPERATURE,
        disable_thinking=True,
    )

    last_error = ""
    for attempt in range(AI_PARSE_MAX_RETRIES):
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=AI_PARSE_TIMEOUT,
                verify=verify_path,
            )
        except requests.exceptions.ConnectTimeout as exc:
            last_error = f"AI 连接超时：{AI_PARSE_TIMEOUT[0]} 秒内无法建立连接（DNS/代理/网络不通）"
            if attempt < AI_PARSE_MAX_RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise ValueError(last_error) from exc
        except requests.exceptions.ReadTimeout as exc:
            last_error = f"AI 读取超时：模型服务 {AI_PARSE_TIMEOUT[1]} 秒内未返回响应"
            if attempt < AI_PARSE_MAX_RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise ValueError(last_error) from exc
        except requests.exceptions.Timeout as exc:
            last_error = f"AI 请求超时（connect={AI_PARSE_TIMEOUT[0]}s, read={AI_PARSE_TIMEOUT[1]}s）"
            if attempt < AI_PARSE_MAX_RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise ValueError(last_error) from exc
        except requests.exceptions.SSLError as exc:
            raise ValueError("AI SSL 证书错误：请检查 Base URL、代理或证书配置") from exc
        except requests.exceptions.ConnectionError as exc:
            last_error = "AI 连接失败：无法连接到 Base URL，或连接被代理/服务端重置"
            if attempt < AI_PARSE_MAX_RETRIES - 1:
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
            # 推理模型（如小米 mimo、DeepSeek-R1）可能把输出放在 reasoning_content 中
            if not content.strip():
                reasoning = str(message.get("reasoning_content", "") or "")
                # 从 reasoning 中提取 JSON 块
                content = _extract_json_from_reasoning(reasoning)
            return content

        last_error = _format_ai_http_error(response)
        if response.status_code in AI_PARSE_RETRYABLE_STATUS and attempt < AI_PARSE_MAX_RETRIES - 1:
            time.sleep(0.8 * (attempt + 1))
            continue
        raise ValueError(last_error)

    raise ValueError(last_error or "AI 请求失败")


def _format_ai_http_error(response: requests.Response) -> str:
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


def _parse_json_response(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("AI 返回为空")
    cleaned = text.strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if not match:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError("AI 返回不是 JSON")
        data = json.loads(match.group(1) if match.lastindex else match.group(0))
    if not isinstance(data, dict):
        raise ValueError("AI JSON 顶层必须是对象")
    return data


def _merge_patch(regex_config: dict[str, Any], patch: dict[str, Any], requirements_text: str = "") -> dict[str, Any]:
    config = copy.deepcopy(regex_config)
    jobs = config.get("job_requirements")
    if not isinstance(jobs, dict) or not jobs:
        raise ValueError("正则配置缺少 job_requirements")

    old_title = next(iter(jobs.keys()))
    job = copy.deepcopy(jobs[old_title])
    new_title = _clean_job_title(patch.get("job_title")) or _clean_job_title(old_title) or old_title

    basic = patch.get("basic_info", {})
    if isinstance(basic, dict):
        if "min_exp" in basic:
            job["min_exp"] = _optional_int(basic.get("min_exp"), job.get("min_exp"))

        original_salary = {
            key: job.get(key) for key in ("salary_min", "salary_max")
        }
        for key in ("salary_min", "salary_max"):
            if original_salary[key] in (None, "") and key in basic:
                job[key] = _optional_salary_k(basic.get(key), original_salary[key])
        salary_min = job.get("salary_min")
        salary_max = job.get("salary_max")
        if salary_min is not None and salary_max is not None and salary_min > salary_max:
            job.update(original_salary)
        if "max_age" in basic and basic.get("max_age") not in (None, ""):
            job["max_age"] = _optional_int(basic.get("max_age"), job.get("max_age"))
        if "edu" in basic:
            edu = _clean_text(basic.get("edu"))
            if edu in _EDU_VALUES:
                job["edu"] = edu
        if "gender" in basic:
            gender = _clean_text(basic.get("gender"))
            explicit_gender = _extract_gender_requirement(requirements_text)
            if gender in _GENDER_VALUES and gender == explicit_gender:
                job["gender"] = gender
        if "work_location" in basic:
            loc = _normalize_work_location(basic.get("work_location"))
            if loc:
                job["work_location"] = loc

    preferred_additions = _filter_preferred_additions(
        patch.get("preferred_keywords_add", []),
        requirements_text,
    )
    existing_keyword_keys = _weighted_name_keys(job.get("keywords", []))
    preferred_addition_keys = _weighted_name_keys(preferred_additions)
    keywords_add = [
        item for item in patch.get("keywords_add", [])
        if _normalized_weighted_key(item) not in (preferred_addition_keys - existing_keyword_keys)
    ] if isinstance(patch.get("keywords_add", []), list) else []

    job["keywords"] = _merge_weighted_items(
        job.get("keywords", []),
        keywords_add,
        patch.get("keywords_update", []),
        value_key="weight",
        min_value=1,
        max_value=3,
    )
    job["preferred_keywords"] = _merge_weighted_items(
        job.get("preferred_keywords", []),
        preferred_additions,
        [],
        value_key="bonus",
        min_value=1,
        max_value=10,
        addition_max_value=2,
    )
    preferred_keys = _weighted_name_keys(job["preferred_keywords"])
    job["keywords"] = [
        item
        for item in job["keywords"]
        if _normalized_weighted_key(item) not in preferred_keys
    ]

    required_additions = _validated_required_additions(
        patch.get("required_conditions_add", []), requirements_text
    )
    exp_from_required = _max_general_experience_years(required_additions)
    if exp_from_required:
        current_exp = job.get("min_exp") if isinstance(job.get("min_exp"), int) else 0
        job["min_exp"] = max(current_exp, exp_from_required)

    # 必要条件会直接淘汰候选人。AI 只能补充有明确原文证据的条件，
    # 且不能删除本地规则或用户已经确认的条件。
    job["required_conditions"] = _merge_required_conditions(
        job.get("required_conditions", []),
        required_additions,
        [],
        job.get("keywords", []),
    )

    config["job_requirements"] = {new_title: job}
    # 保留原始 source_map（AI 增强不重新生成溯源数据）
    if "_source_map" in regex_config:
        config["_source_map"] = regex_config["_source_map"]
    return config


def _merge_weighted_items(
    existing: Any,
    additions: Any,
    updates: Any,
    *,
    value_key: str,
    min_value: int,
    max_value: int,
    addition_max_value: int | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    index: dict[str, int] = {}

    def add_or_update(raw: Any, default_value: int, allow_add: bool = True, item_max_value: int | None = None) -> None:
        if isinstance(raw, dict):
            name = _clean_text(raw.get("name"))
            raw_value = raw.get(value_key, raw.get("weight", raw.get("bonus", default_value)))
        else:
            name = _clean_text(raw)
            raw_value = default_value
        name = _normalize_weighted_name(name)
        if not name:
            return
        if value_key == "weight" and _is_noisy_keyword(name):
            return
        value = _clamp_int(raw_value, default_value, min_value, item_max_value or max_value)
        key = skill_identity_key(name)
        if key in index:
            items[index[key]][value_key] = max(items[index[key]].get(value_key, min_value), value)
        elif allow_add:
            index[key] = len(items)
            items.append({"name": name, value_key: value})

    for raw in existing if isinstance(existing, list) else []:
        add_or_update(raw, 1)
    for raw in updates if isinstance(updates, list) else []:
        add_or_update(raw, 1, allow_add=False)
    for raw in additions if isinstance(additions, list) else []:
        add_or_update(raw, 1, item_max_value=addition_max_value)
    return items


def _merge_required_conditions(existing: Any, additions: Any, removals: Any, keywords: Any = None) -> list[Any]:
    conditions = list(existing) if isinstance(existing, list) else []
    keyword_names = _keyword_name_set(keywords)
    remove_set = {_normalize_condition_key(item) for item in removals if _normalize_condition_key(item)} if isinstance(removals, list) else set()
    if remove_set:
        conditions = [cond for cond in conditions if _normalize_condition_key(cond) not in remove_set]

    seen = {_normalize_condition_key(cond) for cond in conditions}
    for raw in additions if isinstance(additions, list) else []:
        cond = _normalize_condition(raw)
        if _is_soft_trait_condition(cond):
            continue
        if _is_keyword_requirement_condition(cond, keyword_names):
            continue
        key = _normalize_condition_key(cond)
        if cond is not None and key and key not in seen:
            conditions.append(cond)
            seen.add(key)
    return conditions


def _validated_required_additions(additions: Any, requirements_text: str) -> list[Any]:
    """Keep only AI hard-condition suggestions backed by explicit source text."""
    if not isinstance(additions, list):
        return []
    evidence_lines = _hard_requirement_evidence_lines(requirements_text)
    if not evidence_lines:
        return []
    return [
        item for item in additions
        if _required_condition_has_evidence(item, evidence_lines)
    ]


def _hard_requirement_evidence_lines(requirements_text: str) -> list[str]:
    lines = []
    in_hard_section = False
    for raw_line in (requirements_text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•·]|\d+[.、)])\s*", "", raw_line).strip()
        if not line:
            continue
        heading = re.sub(r"[：:]$", "", line).strip()
        if _HARD_SECTION_HEADING_RE.match(heading):
            in_hard_section = True
            continue
        if _OTHER_SECTION_HEADING_RE.match(heading):
            in_hard_section = False
            continue
        if in_hard_section or _EXPLICIT_HARD_MARKER_RE.search(line):
            lines.append(line)
    return lines


def _required_condition_has_evidence(condition: Any, evidence_lines: list[str]) -> bool:
    if isinstance(condition, dict):
        terms = [_clean_text(item) for item in condition.get("items", []) if _clean_text(item)]
        return bool(terms) and any(
            all(_condition_term_in_line(term, line) for term in terms)
            for line in evidence_lines
        )
    text = _clean_text(condition)
    return bool(text) and any(_condition_term_in_line(text, line) for line in evidence_lines)


def _condition_term_in_line(term: str, line: str) -> bool:
    compact_term = re.sub(r"[\s，,。；;：:（）()]", "", term).lower()
    compact_line = re.sub(r"[\s，,。；;：:（）()]", "", line).lower()
    if compact_term and compact_term in compact_line:
        return True
    term_years = _general_experience_years(term)
    line_years = _general_experience_years(line)
    return bool(term_years and line_years and term_years == line_years)


def _normalize_condition(raw: Any) -> Any:
    if isinstance(raw, str):
        text = _clean_text(raw)
        if not text:
            return None
        compact = re.sub(r"\s+", "", text)
        if "统招本科" in compact:
            return "统招本科"
        if "全日制本科" in compact:
            return "全日制"
        if _is_generic_education_condition(compact) or _general_experience_years(text):
            return None
        return text
    if not isinstance(raw, dict):
        return None
    cond_type = str(raw.get("type", "or")).lower()
    if cond_type not in {"or", "and"}:
        cond_type = "or"
    items = [_clean_text(item) for item in raw.get("items", []) if _clean_text(item)]
    if not items:
        return None
    result: dict[str, Any] = {"type": cond_type, "items": items}
    category = _clean_text(raw.get("category"))
    if category:
        result["category"] = category
    return result


def _normalize_work_location(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    cities: list[str] = []
    seen: set[str] = set()
    for city in MAJOR_CITIES:
        if re.search(rf"{re.escape(city)}市?", text):
            if city not in seen:
                cities.append(city)
                seen.add(city)
    return "/".join(cities)


def _normalize_weighted_name(name: str) -> str:
    cleaned = _clean_text(name)
    compact = re.sub(r"\s+", "", cleaned)
    if re.fullmatch(r"(?:AI)?Agent|AIAgent|智能体|大模型Agent", compact, re.IGNORECASE):
        return "AI Agent"
    if re.fullmatch(r"LangChain", compact, re.IGNORECASE):
        return "LangChain"
    if re.fullmatch(r"证券(?:行业|从业|相关)?(?:经验|背景|经历)?", compact):
        return "证券"
    return cleaned


def _filter_preferred_additions(additions: Any, requirements_text: str) -> list[Any]:
    if not isinstance(additions, list):
        return []
    evidence_text = _preferred_evidence_text(requirements_text)
    if not evidence_text:
        return additions
    return [item for item in additions if _weighted_name_in_text(item, evidence_text)]


def _preferred_evidence_text(requirements_text: str) -> str:
    evidence: list[str] = []
    for line in (requirements_text or "").splitlines():
        if not re.search(r"优先|加分|更佳|优先考虑|优先录用", line):
            continue
        clauses = re.split(r"[；;。！？!?]", line)
        for clause in clauses:
            clause = clause.strip()
            if not re.search(r"优先|加分|更佳|优先考虑|优先录用", clause):
                continue
            comma_parts = [part.strip() for part in re.split(r"[,，]", clause) if part.strip()]
            if len(comma_parts) > 1 and re.search(r"优先|加分|更佳|优先考虑|优先录用", comma_parts[-1]):
                evidence.append(comma_parts[-1])
            else:
                evidence.append(clause)
    return "\n".join(evidence)


def _weighted_name_in_text(item: Any, text: str) -> bool:
    if isinstance(item, dict):
        raw_name = _clean_text(item.get("name"))
    else:
        raw_name = _clean_text(item)
    name = _normalize_weighted_name(raw_name)
    compact_text = re.sub(r"\s+", "", text or "").lower()
    for variant in _weighted_name_variants(name):
        compact_variant = re.sub(r"\s+", "", variant).lower()
        if compact_variant and compact_variant in compact_text:
            return True
    return False


def _weighted_name_variants(name: str) -> list[str]:
    variants = [name]
    compact = re.sub(r"\s+", "", name or "").lower()
    if compact == "aiagent":
        variants.extend(["AI Agent", "AIAgent", "Al Agent", "AlAgent", "Agent", "智能体", "大模型Agent", "大模型 Agent"])
    elif compact == "langchain":
        variants.extend(["LangChain", "Langchain"])
    elif compact == "证券":
        variants.extend(["证券行业", "证券从业", "证券相关", "证券经验", "证券背景"])
    return variants


def _weighted_name_keys(items: Any) -> set[str]:
    keys: set[str] = set()
    for item in items if isinstance(items, list) else []:
        key = _normalized_weighted_key(item)
        if key:
            keys.add(key)
    return keys


def _normalized_weighted_key(item: Any) -> str:
    if isinstance(item, dict):
        name = _clean_text(item.get("name"))
    else:
        name = _clean_text(item)
    name = _normalize_weighted_name(name)
    return skill_identity_key(name) if name else ""


def _max_general_experience_years(raw_items: Any) -> int:
    years: list[int] = []
    if not isinstance(raw_items, list):
        return 0
    for raw in raw_items:
        if isinstance(raw, str):
            year = _general_experience_years(raw)
            if year:
                years.append(year)
        elif isinstance(raw, dict):
            for item in raw.get("items", []):
                year = _general_experience_years(_clean_text(item))
                if year:
                    years.append(year)
    return max(years) if years else 0


def _general_experience_years(text: str) -> int:
    compact = re.sub(r"\s+", "", text or "")
    range_match = _GENERAL_EXPERIENCE_RANGE_RE.search(compact)
    if range_match:
        return _chinese_or_int(range_match.group(1))
    match = _GENERAL_EXPERIENCE_RE.search(compact)
    if not match:
        return 0
    return _chinese_or_int(match.group(1))


def _is_generic_education_condition(compact: str) -> bool:
    return bool(re.fullmatch(r"(?:高中|中专|大专|本科|硕士|博士)(?:及以上|以上)?(?:学历|学位)?", compact))


def _chinese_or_int(value: str) -> int:
    if value in CHINESE_NUMERALS:
        return CHINESE_NUMERALS[value]
    if value == "十":
        return 10
    if value.startswith("十") and len(value) > 1:
        return 10 + CHINESE_NUMERALS.get(value[1], 0)
    if value.endswith("十") and len(value) > 1:
        return CHINESE_NUMERALS.get(value[0], 0) * 10
    if "十" in value:
        first, _, second = value.partition("十")
        tens = CHINESE_NUMERALS.get(first, 1)
        ones = CHINESE_NUMERALS.get(second, 0)
        return tens * 10 + ones
    try:
        return int(value)
    except ValueError:
        return 0


def _normalize_condition_key(cond: Any) -> str:
    if isinstance(cond, str):
        return cond.strip().lower()
    if isinstance(cond, dict):
        cond_type = str(cond.get("type", "or")).lower()
        items = [_clean_text(item).lower() for item in cond.get("items", []) if _clean_text(item)]
        return f"{cond_type}:{','.join(items)}" if items else ""
    return ""


def _keyword_name_set(keywords: Any) -> set[str]:
    names: set[str] = set()
    for raw in keywords if isinstance(keywords, list) else []:
        name = _clean_text(raw.get("name")) if isinstance(raw, dict) else _clean_text(raw)
        if name:
            names.add(skill_identity_key(name))
    return names


def _is_keyword_requirement_condition(cond: Any, keyword_names: set[str]) -> bool:
    """判断一个条件是否实质上是普通技能要求（应归入 keywords 而非 required_conditions）。

    仅处理 string 条件：含技能动词 + 已知关键词 → True（如"具备 Java 开发经验"）。
    dict 条件（OR/AND 技术栈组）不过滤——允许 AI 把核心技术栈提升为硬条件。
    """
    if not isinstance(cond, str) or not keyword_names:
        return False
    compact = skill_identity_key(cond)
    if not re.search(r"具备|具有|有|熟悉|熟练|掌握|精通|开发经验|使用经验|处理数据经验", cond):
        return False
    return any(name and name in compact for name in keyword_names)


def _is_noisy_keyword(name: str) -> bool:
    compact = re.sub(r"\s+", "", name or "")
    return bool(_NOISY_KEYWORD_RE.match(compact))


def _is_soft_trait_condition(cond: Any) -> bool:
    if isinstance(cond, str):
        return bool(_SOFT_TRAIT_RE.search(cond))
    if isinstance(cond, dict):
        text = " ".join(_clean_text(item) for item in cond.get("items", []))
        category = _clean_text(cond.get("category"))
        return bool(_SOFT_TRAIT_RE.search(text + " " + category))
    return False


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_job_title(value: Any) -> str:
    title = _clean_text(value)
    title = re.sub(r"^(?:岗位|职位|招聘)\s*\d+\s*[：:、.\-]\s*", "", title)
    title = re.sub(r"^\d+\s*[：:、.\-]\s*", "", title)
    return title.strip()


def _optional_int(value: Any, fallback: Any) -> int | None:
    if value is None or value == "":
        return None
    return _clamp_int(value, fallback if fallback is not None else 0, 0, 1000)


def _optional_salary_k(value: Any, fallback: Any) -> int | None:
    """Normalize an AI salary value to an integer monthly K amount."""
    if value is None or value == "":
        return fallback

    text = str(value).strip().replace(",", "").replace("，", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kK]|千|元)?", text)
    if not match:
        return fallback

    amount = float(match.group(1))
    unit = match.group(2) or ""
    if unit == "元" or (not unit and amount >= 1000):
        amount /= 1000

    salary_k = int(amount + 0.5)
    if not 1 <= salary_k <= 1000:
        return fallback
    return salary_k


def _clamp_int(value: Any, fallback: Any, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        try:
            parsed = int(fallback)
        except (TypeError, ValueError):
            parsed = min_value
    return max(min_value, min(max_value, parsed))
