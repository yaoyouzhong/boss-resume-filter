"""LLM-based candidate evaluation for BOSS resume screening."""
import json
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse
from constants import (
    SCORE_THRESHOLD_PASS,
    SCORE_THRESHOLD_RECOMMEND,
    SCORE_THRESHOLD_STRONG,
    USER_AGENT,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_CONNECT_TIMEOUT,
    LLM_READ_TIMEOUT_DEFAULT,
    LLM_RELAY_READ_TIMEOUT_DEFAULT,
    LLM_MAX_RETRIES,
    LLM_MAX_WORKERS,
    LLM_RELAY_MAX_WORKERS,
)

import requests
from ai_adapter import (
    build_request,
    detect_protocol,
    friendly_http_error,
    load_capability,
    normalize_response,
    save_capability,
)

logger = logging.getLogger(__name__)

# 429 限流退避延迟（秒），无 Retry-After header 时按此阶梯退避
_BACKOFF_DELAYS = (5, 15, 30)
_NETWORK_BACKOFF_DELAYS = (3, 8, 15)
_OFFICIAL_API_HOSTS = {
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


def _is_relay_endpoint(api_config: dict) -> bool:
    """Return whether the configured API endpoint is a relay service."""
    provider = str(api_config.get("api_provider") or "").lower()
    hostname = (urlparse(str(api_config.get("base_url") or "")).hostname or "").lower()
    official_hosts = _OFFICIAL_API_HOSTS.get(provider)
    return bool(
        hostname
        and (
            provider == "custom"
            or not official_hosts
            or not any(hostname == host or hostname.endswith(f".{host}") for host in official_hosts)
        )
    )


def _resolve_request_timeout(api_config: dict) -> tuple[int, int]:
    """Return provider-aware HTTP connect/read timeouts.

    Connect timeout is fixed at LLM_CONNECT_TIMEOUT (10s).
    Read timeout: user-configured llm_read_timeout > relay default (120s) > official default (60s).
    """
    if "llm_read_timeout" in api_config and api_config["llm_read_timeout"]:
        read = api_config["llm_read_timeout"]
    else:
        read = LLM_RELAY_READ_TIMEOUT_DEFAULT if _is_relay_endpoint(api_config) else LLM_READ_TIMEOUT_DEFAULT
    return (LLM_CONNECT_TIMEOUT, read)


# resume prompt 构建时的额外字符缓冲（JSON 结构、format() 占位符等）
_RESUME_PROMPT_OVERHEAD_BUFFER = 200
_AI_LOG_REASON_LIMIT = 80
_JSON_FORMAT_RETRY_LIMIT = 1
_EVALUATION_TOOL_NAME = "submit_candidate_evaluation"
_EVALUATION_TOOL = {
    "type": "function",
    "function": {
        "name": _EVALUATION_TOOL_NAME,
        "description": "提交候选人匹配度及硬条件评估结果",
        "parameters": {
            "type": "object",
            "properties": {
                "adjustment": {"type": "integer", "minimum": -15, "maximum": 15},
                "reason": {"type": "string"},
                "hard_condition_verdict": {
                    "type": "string",
                    "enum": ["pass", "fail", "unknown"],
                },
                "hard_condition_findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "condition": {"type": "string"},
                            "verdict": {
                                "type": "string",
                                "enum": ["pass", "fail", "unknown"],
                            },
                            "evidence": {"type": "string"},
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                        "required": ["condition", "verdict", "evidence", "confidence"],
                    },
                },
                "dimension_scores": {
                    "type": "object",
                    "properties": {
                        "skill_depth": {"type": "integer", "minimum": 1, "maximum": 10, "description": "技能掌握深度"},
                        "experience_quality": {"type": "integer", "minimum": 1, "maximum": 10, "description": "经验质量"},
                        "industry_fit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "行业匹配"},
                        "growth_potential": {"type": "integer", "minimum": 1, "maximum": 10, "description": "发展潜力"},
                    },
                    "required": ["skill_depth", "experience_quality", "industry_fit", "growth_potential"],
                },
            },
            "required": [
                "adjustment",
                "reason",
                "hard_condition_verdict",
                "hard_condition_findings",
                "dimension_scores",
            ],
        },
    },
}

_DIMENSION_LABELS = {
    "skill_depth": "技能深度",
    "experience_quality": "经验质量",
    "industry_fit": "行业匹配",
    "growth_potential": "发展潜力",
}


@dataclass
class LLMEvalResult:
    """Result of a single LLM evaluation call."""
    success: bool
    adjustment: int = 0       # -15 ~ +15
    reason: str = ""          # ≤50 chars
    model: str = ""
    hard_condition_verdict: str = "unknown"
    hard_condition_findings: list[dict[str, Any]] | None = None
    dimension_scores: dict[str, int] | None = None


_SYSTEM_PROMPT = (
    "你是一个资深技术招聘助手。根据岗位需求评估候选人的匹配程度。\n"
    "返回严格的 JSON 对象（不要包含其他文字）：\n"
    '{"adjustment": 整数(-15到+15), "hard_condition_verdict": "pass|fail|unknown", '
    '"hard_condition_findings": [{"condition":"条件","verdict":"fail","evidence":"候选人原文","confidence":"high|medium|low"}], '
    '"dimension_scores": {"skill_depth": 1-10, "experience_quality": 1-10, "industry_fit": 1-10, "growth_potential": 1-10}, '
    '"reason": "100字以内评估理由"}\n'
    "硬条件结论必须引用候选人原文；推测、大概率、疑似只能返回 unknown，不能返回 fail。\n"
    "评分标准：\n"
    "+12~+15: 高度匹配，有明显优势\n"
    "+5~+11: 较为匹配，有加分项\n"
    "+1~+4: 略有加分\n"
    "0: 基本匹配，无特别加减分\n"
    "-1~-5: 存在不匹配之处\n"
    "-6~-10: 明显不匹配\n"
    "-11~-15: 严重不匹配\n"
    "维度评分标准（各维度 1-10）：\n"
    "- skill_depth：技能掌握深度（1=了解概念，5=熟练应用，10=专家级）\n"
    "- experience_quality：经验质量（1=水经验，5=正常，10=顶级项目）\n"
    "- industry_fit：行业匹配（1=完全跨行，5=相关行业，10=同行业）\n"
    "- growth_potential：发展潜力（1=停滞，5=稳定，10=快速上升）"
)

_USER_TEMPLATE = (
    "## 岗位需求\n{job_requirement}\n\n"
    "{hard_conditions}"
    "## 候选人信息\n{candidate_summary}\n\n"
    "请评估匹配度，返回 JSON。"
)


# ── 二次评估（基于完整简历） ──

_RESUME_SYSTEM_PROMPT = (
    "你是一个资深技术招聘助手。你已获得候选人的完整简历，请基于简历内容进行深度评估。\n"
    "重点关注：项目经历的深度和技术复杂度、量化成果、技能匹配度、职业发展轨迹。\n"
    "返回严格的 JSON 对象（不要包含其他文字）：\n"
    '{"adjustment": 整数(-15到+15), "reason": "200字以内详细评估，含优势与顾虑"}\n'
    "评分标准：\n"
    "+12~+15: 简历展现出色的项目深度和成果量化\n"
    "+5~+11: 简历有明确的加分信息\n"
    "+1~+4: 简历略有加分\n"
    "0: 简历未提供显著新信息\n"
    "-1~-5: 简历暴露不匹配之处\n"
    "-6~-10: 简历显示明显不适合\n"
    "-11~-15: 简历显示严重不适合"
)

_RESUME_USER_TEMPLATE = (
    "## 岗位需求\n{job_requirement}\n\n"
    "{hard_conditions}"
    "## 一次评估结论\n规则分：{rule_score}，AI调整：{llm_adjustment}，理由：{llm_reason}\n\n"
    "## 候选人完整简历\n{resume_text}\n\n"
    "请基于简历内容做二次评估，返回 JSON。"
)


def _clean_summary_line(value: Any) -> str:
    """Normalize one summary value for compact prompt usage."""
    text = str(value or '').replace('\r', '\n')
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _truncate_text(text: str, limit: int) -> str:
    """Return text capped to limit chars, preserving a visible truncation marker."""
    text = _clean_summary_line(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _format_ai_log_summary(candidate: dict[str, Any], reason: str, score: int) -> str:
    """Return a compact one-line AI result for the runtime log."""
    if candidate.get('qualification_status') == 'rejected':
        conclusion = "硬条件淘汰"
    elif score < SCORE_THRESHOLD_PASS:
        conclusion = "评分淘汰"
    elif candidate.get('qualification_status') == 'manual_review' or candidate.get('manual_review_required'):
        conclusion = "待确认"
    else:
        conclusion = "通过"

    compact_reason = re.sub(r'\s+', ' ', reason or '').strip()
    if not compact_reason:
        compact_reason = "未提供评估理由"
    if len(compact_reason) > _AI_LOG_REASON_LIMIT:
        compact_reason = compact_reason[:_AI_LOG_REASON_LIMIT].rstrip() + "…"
    return f"{conclusion}：{compact_reason}"


def _build_llm_summary_from_api_profile(candidate: dict, profile: dict, max_chars: int) -> str:
    """Build LLM candidate summary directly from structured API profile.

    Produces the same output format as the text-parsing path but avoids
    re-parsing the flat text summary with regex.
    """
    lines: list[str] = []

    # --- Header fields (same as text path) ---
    name = candidate.get('name')
    if name:
        lines.append(f"姓名：{name}")
    lines.append(f"规则评分：{candidate.get('match_score', 0)}")
    if candidate.get('recommend_level'):
        lines.append(f"规则推荐：{candidate.get('recommend_level')}")
    if candidate.get('skill_match_ratio'):
        lines.append(f"技能匹配：{candidate.get('skill_match_ratio')}")
    skill_matches = candidate.get('skill_matches') or []
    if skill_matches:
        skill_names = []
        for item in skill_matches:
            if isinstance(item, dict):
                skill_names.append(str(item.get('name', '')).strip())
            else:
                skill_names.append(str(item).strip())
        skill_names = [s for s in skill_names if s]
        if skill_names:
            lines.append("命中技能：" + "、".join(skill_names[:20]))

    risk_flags = candidate.get('risk_flags') or []
    if risk_flags:
        lines.append("风险提示：" + "；".join(str(flag) for flag in risk_flags if flag))

    # Personal summary (from API geekDesc)
    personal = profile.get('personal_summary', '')
    if personal:
        lines.append("基础摘要：" + _truncate_text(personal, 600))

    # --- Education ---
    edus = profile.get('educations') or []
    if edus:
        edu_parts = []
        for edu in edus[:5]:
            parts = [v for v in (edu.get('school'), edu.get('major'),
                                 edu.get('degree'), edu.get('start'), edu.get('end')) if v]
            edu_parts.append(" ".join(parts))
        lines.append("教育经历：" + _truncate_text("；".join(edu_parts), 700))

    # --- Work experience ---
    works = profile.get('works') or []
    if works:
        work_parts = []
        for w in works[:5]:
            parts = [v for v in (w.get('company'), w.get('position'),
                                 w.get('category'), w.get('start'), w.get('end')) if v]
            work_parts.append(" ".join(parts))
        lines.append("工作经历：" + _truncate_text("；".join(work_parts), 800))

    # --- Work responsibilities ---
    if works:
        resp_parts = []
        for w in works[:5]:
            r = w.get('responsibility', '')
            if r:
                resp_parts.append(r)
        if resp_parts:
            lines.append("工作职责：" + _truncate_text("；".join(resp_parts), 1600))

    # --- Skills from work emphasis ---
    if works:
        tags: list[str] = []
        seen: set[str] = set()
        for w in works:
            for tag in (w.get('skills') or []):
                tag = tag.strip()
                if tag and tag not in seen:
                    seen.add(tag)
                    tags.append(tag)
        if tags:
            lines.append("技能标签：" + "、".join(tags[:40]))

    # --- Rule explanation (from candidate, same as text path) ---
    explanation = candidate.get('score_explanation') or []
    if explanation:
        explanation_text = "；".join(_clean_summary_line(item) for item in explanation[:8] if item)
        if explanation_text:
            lines.append("规则解释：" + _truncate_text(explanation_text, 500))

    compact = "\n".join(line for line in lines if line)
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "..."


def build_llm_candidate_summary(candidate: dict, max_chars: int = 4000) -> str:
    """Build a deterministic compact candidate summary for LLM evaluation.

    The full candidate summary stays in JSON/Excel/detail views; this function only
    controls the prompt payload to reduce latency and timeout risk.

    When candidate contains ``_api_profile`` (from BOSS API extraction), education,
    work history, and skills are read directly from structured fields instead of
    re-parsing the text summary.
    """
    api_profile = candidate.get('_api_profile')
    if api_profile:
        return _build_llm_summary_from_api_profile(candidate, api_profile, max_chars)

    summary = str(candidate.get('summary') or '')
    sections: dict[str, list[str]] = {
        '教育经历': [],
        '工作经历': [],
        '工作职责': [],
        '技能标签': [],
    }
    other_lines: list[str] = []

    for raw_line in summary.splitlines():
        line = _clean_summary_line(raw_line)
        if not line:
            continue
        matched = False
        for label in sections:
            prefix = f"{label}："
            if line.startswith(prefix):
                value = line[len(prefix):].strip()
                if value:
                    sections[label].append(value)
                matched = True
                break
        if not matched:
            other_lines.append(line)

    lines: list[str] = []
    name = candidate.get('name')
    if name:
        lines.append(f"姓名：{name}")
    lines.append(f"规则评分：{candidate.get('match_score', 0)}")
    if candidate.get('recommend_level'):
        lines.append(f"规则推荐：{candidate.get('recommend_level')}")
    if candidate.get('skill_match_ratio'):
        lines.append(f"技能匹配：{candidate.get('skill_match_ratio')}")
    skill_matches = candidate.get('skill_matches') or []
    if skill_matches:
        skill_names = []
        for item in skill_matches:
            if isinstance(item, dict):
                skill_names.append(str(item.get('name', '')).strip())
            else:
                skill_names.append(str(item).strip())
        skill_names = [item for item in skill_names if item]
        if skill_names:
            lines.append("命中技能：" + "、".join(skill_names[:20]))

    risk_flags = candidate.get('risk_flags') or []
    if risk_flags:
        lines.append("风险提示：" + "；".join(str(flag) for flag in risk_flags if flag))

    if other_lines:
        lines.append("基础摘要：" + _truncate_text("；".join(other_lines[:6]), 600))

    if sections['教育经历']:
        edu_text = "；".join(_truncate_text(item, 250) for item in sections['教育经历'][:5])
        lines.append("教育经历：" + edu_text)

    if sections['工作经历']:
        work_text = "；".join(_truncate_text(item, 300) for item in sections['工作经历'][:5])
        lines.append("工作经历：" + work_text)

    if sections['工作职责']:
        responsibility_text = "；".join(_truncate_text(item, 450) for item in sections['工作职责'][:5])
        lines.append("工作职责：" + responsibility_text)

    if sections['技能标签']:
        tags: list[str] = []
        seen: set[str] = set()
        for item in sections['技能标签']:
            for tag in re.split(r'[、,，;/；\s]+', item):
                tag = tag.strip()
                if tag and tag not in seen:
                    seen.add(tag)
                    tags.append(tag)
        if tags:
            lines.append("技能标签：" + "、".join(tags[:40]))

    explanation = candidate.get('score_explanation') or []
    if explanation:
        explanation_text = "；".join(_clean_summary_line(item) for item in explanation[:8] if item)
        if explanation_text:
            lines.append("规则解释：" + _truncate_text(explanation_text, 500))

    compact = "\n".join(line for line in lines if line)
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "..."


def _build_prompt(job_requirement: str, candidate_summary: str, hard_conditions: str = "") -> list:
    """Build chat messages for LLM evaluation."""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _USER_TEMPLATE.format(
            job_requirement=job_requirement,
            hard_conditions=hard_conditions,
            candidate_summary=candidate_summary,
        )},
    ]


def _use_forced_function_output(api_config: dict[str, Any]) -> bool:
    """Return whether the configured model should use forced function output."""
    base_url = str(api_config.get('base_url') or '').lower()
    model = str(api_config.get('model') or '').lower()
    return 'dashscope.aliyuncs.com' in base_url and model.startswith('qwen3.7')


def _extract_evaluation_payload(message: dict[str, Any], use_tool_output: bool) -> str:
    """Extract evaluation JSON from a tool call or normal assistant content."""
    if use_tool_output:
        for tool_call in message.get('tool_calls') or []:
            function = tool_call.get('function') or {}
            if function.get('name') == _EVALUATION_TOOL_NAME:
                return str(function.get('arguments') or '')
    content = str(message.get('content') or '')
    if content.strip():
        return content
    return str(message.get('reasoning_content') or '')


def _repair_json_text(text: str) -> str:
    """Repair common model JSON formatting mistakes without changing values."""
    repaired = text.strip()
    repaired = re.sub(r'^```(?:json)?\s*|\s*```$', '', repaired, flags=re.IGNORECASE)
    repaired = (
        repaired
        .replace('“', '"')
        .replace('”', '"')
        .replace('‘', "'")
        .replace('’', "'")
        .replace('：', ':')
        .replace('，', ',')
    )
    repaired = re.sub(r',\s*([}\]])', r'\1', repaired)
    repaired = re.sub(r'}\s*{', '},{', repaired)
    repaired = re.sub(
        r'([}"\]0-9])\s*\n\s*(?="[^"\n]+"\s*:)',
        r'\1,\n',
        repaired,
    )
    return repaired


def _parse_response(text: str) -> dict:
    """Parse LLM response text into a normalized evaluation dict.

    Handles: plain JSON, markdown code blocks, Chinese punctuation.
    Clamps adjustment to [-15, +15].
    Raises ValueError if unparseable.
    """
    if not text or not text.strip():
        raise ValueError("Empty response")

    cleaned = text.strip()

    # Try direct JSON parse
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # Try extracting from markdown code block: ```json ... ``` or ``` ... ```
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', cleaned, re.DOTALL)
        if m:
            data = json.loads(_repair_json_text(m.group(1)))
        else:
            # Try extracting and repairing the first JSON object.
            m = re.search(r'\{.*(?:["“]adjustment["”]).*\}', cleaned, re.DOTALL)
            if m:
                data = json.loads(_repair_json_text(m.group(0)))
            else:
                raise ValueError(f"Cannot extract JSON from: {cleaned[:100]}")

    if not isinstance(data, dict):
        raise ValueError(f"Expected dict, got {type(data).__name__}")

    # Extract and validate adjustment
    adjustment = data.get('adjustment', 0)
    if not isinstance(adjustment, (int, float)):
        try:
            adjustment = int(adjustment)
        except (TypeError, ValueError):
            adjustment = 0
    adjustment = int(adjustment)
    # Clamp to [-15, +15]
    adjustment = max(-15, min(15, adjustment))

    # Extract and validate reason
    reason = str(data.get('reason', '')).strip()
    if len(reason) > 200:
        reason = reason[:200]

    verdict = str(data.get('hard_condition_verdict', 'unknown')).lower()
    if verdict not in {'pass', 'fail', 'unknown'}:
        verdict = 'unknown'
    raw_findings = data.get('hard_condition_findings', [])
    findings = []
    if isinstance(raw_findings, list):
        for item in raw_findings[:10]:
            if not isinstance(item, dict):
                continue
            item_verdict = str(item.get('verdict', 'unknown')).lower()
            confidence = str(item.get('confidence', 'low')).lower()
            findings.append({
                'condition': str(item.get('condition', '')).strip()[:100],
                'verdict': item_verdict if item_verdict in {'pass', 'fail', 'unknown'} else 'unknown',
                'evidence': str(item.get('evidence', '')).strip()[:300],
                'confidence': confidence if confidence in {'high', 'medium', 'low'} else 'low',
            })

    # Extract and validate dimension scores
    raw_dims = data.get('dimension_scores', {})
    dimension_scores = {}
    if isinstance(raw_dims, dict):
        for key in ('skill_depth', 'experience_quality', 'industry_fit', 'growth_potential'):
            val = raw_dims.get(key)
            if isinstance(val, (int, float)):
                dimension_scores[key] = max(1, min(10, int(val)))

    return {
        'adjustment': adjustment,
        'reason': reason,
        'hard_condition_verdict': verdict,
        'hard_condition_findings': findings,
        'dimension_scores': dimension_scores,
    }


def _validated_hard_failures(
    candidate: dict[str, Any],
    findings: list[dict[str, Any]] | None,
    hard_conditions: str,
    rule: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate high-confidence LLM findings with deterministic text rules."""
    from filtering import parse_experience_months

    summary = str(candidate.get('summary') or '')
    compact_summary = re.sub(r'\s+', '', summary)
    min_exp_match = re.search(r'经验：≥(\d+)年', hard_conditions)
    requires_regular_bachelor = '统招本科' in hard_conditions
    explicit_non_regular = (
        "自考", "成教", "函授", "夜大", "网络教育", "继续教育", "非统招",
        "专升本", "电大", "远程教育", "成人高考", "成人教育", "业余",
        "第一学历为大专", "第一学历为专科",
        "2年制本科", "两年制本科", "二年制本科", "3年制本科", "三年制本科",
    )

    # 从 rule 提取阈值，用于年龄/薪资/地点验证
    max_age = rule.get('max_age') if rule else None
    salary_max = rule.get('salary_max') if rule else None
    required_locations: list[str] = []
    if rule and rule.get('work_location'):
        required_locations = [loc.strip() for loc in re.split(r'[/、]', rule['work_location']) if loc.strip()]

    validated = []
    for finding in findings or []:
        if finding.get('verdict') != 'fail' or finding.get('confidence') != 'high':
            continue
        evidence = str(finding.get('evidence') or '').strip()
        if not evidence or re.sub(r'\s+', '', evidence) not in compact_summary:
            continue
        condition = str(finding.get('condition') or '')
        if '经验' in condition and min_exp_match:
            months = parse_experience_months(evidence)
            if months is not None and months < int(min_exp_match.group(1)) * 12:
                validated.append(finding)
        elif ('学历' in condition or '统招' in condition) and requires_regular_bachelor:
            if (
                any(term in evidence for term in explicit_non_regular)
                or re.search(r'(?:大专|专科).{0,40}(?:后修|后续|在读|取得|升读).{0,20}本科', evidence)
            ):
                validated.append(finding)
        elif '年龄' in condition and max_age is not None:
            age_match = re.search(r'(\d+)\s*岁', evidence)
            if age_match and int(age_match.group(1)) > max_age:
                validated.append(finding)
        elif ('薪资' in condition or '薪' in condition) and salary_max is not None:
            salary_match = re.search(r'(\d+)\s*[kK]', evidence)
            if salary_match and int(salary_match.group(1)) >= salary_max + 1:
                validated.append(finding)
        elif '求职状态' in condition or '不考虑' in condition:
            if '暂不考虑' in evidence or '不考虑' in evidence:
                validated.append(finding)
        elif ('地点' in condition or '城市' in condition) and required_locations:
            # 从 evidence 提取候选人期望城市（"期望北京" 或 "候选人北京"）
            city_match = re.search(r'(?:期望|候选人.{0,4})([一-龥]{2,4}(?:市)?)', evidence)
            candidate_city = city_match.group(1).replace('市', '') if city_match else evidence.replace('市', '')
            if not any(loc in candidate_city for loc in required_locations):
                validated.append(finding)
    return validated


def _call_llm_api(messages: list, api_config: dict, api_key: str,
                    stop_event=None, request_label: str = "") -> LLMEvalResult:
    """Call LLM API and return evaluation result.

    Uses the provider-aware adapter and retries transient failures.
    """
    try:
        import certifi
        verify_path = certifi.where()
    except ImportError:
        verify_path = True

    base_url = api_config.get('base_url', '').rstrip('/')
    model = api_config.get('model', '')

    if not base_url or not model:
        return LLMEvalResult(success=False, reason="API config incomplete")

    cached = None if api_config.get("_ignore_capability_cache") else load_capability(api_config)
    if cached:
        use_tool_output = cached.get("output_mode") == "tool"
    else:
        use_tool_output = bool(
            api_config.get("_probe_output_mode")
            or _use_forced_function_output(api_config)
            or detect_protocol(api_config) == "anthropic"
        )

    def _make_request(tool_output: bool):
        return build_request(
            api_config,
            api_key,
            messages,
            max_tokens=LLM_MAX_TOKENS,
            temperature=0 if tool_output else LLM_TEMPERATURE,
            tool=_EVALUATION_TOOL if tool_output else None,
            force_tool=tool_output,
        )

    url, headers, body, protocol = _make_request(use_tool_output)
    timeout = _resolve_request_timeout(api_config)
    max_retries = LLM_MAX_RETRIES
    last_error = None
    json_format_retries = 0

    session = requests.Session()
    try:
        for attempt in range(max_retries):
            try:
                response = session.post(
                    url, json=body, headers=headers,
                    timeout=timeout, verify=verify_path,
                )

                # stop_event 触发后，响应返回时直接丢弃，不再处理
                if stop_event and stop_event.is_set():
                    return LLMEvalResult(success=False, reason="Stopped")

                if response.status_code == 200:
                    resp_data = response.json()
                    message, finish_reason = normalize_response(protocol, resp_data)
                    content = _extract_evaluation_payload(message, use_tool_output)
                    try:
                        parsed = _parse_response(content)
                    except (json.JSONDecodeError, ValueError) as parse_error:
                        if use_tool_output:
                            use_tool_output = False
                            url, headers, body, protocol = _make_request(False)
                            save_capability(api_config, {
                                "status": "limited",
                                "protocol": protocol,
                                "output_mode": "json_text",
                                "message": "工具调用返回不可解析，已自动改用 JSON 文本",
                            })
                            last_error = "工具调用输出不可解析"
                            continue
                        if json_format_retries < _JSON_FORMAT_RETRY_LIMIT:
                            json_format_retries += 1
                            print("  [WARN] AI 返回格式异常，正在自动纠正并重试（1/1）")
                            if not use_tool_output:
                                body["messages"] = messages + [
                                    {"role": "assistant", "content": content[:2000]},
                                    {
                                        "role": "user",
                                        "content": (
                                            "上次返回的 JSON 格式有误。请重新输出一个语法严格正确的 JSON 对象，"
                                            "不要使用 Markdown，不要添加解释文字，并确保所有字段之间有逗号。"
                                        ),
                                    },
                                ]
                                body["temperature"] = 0
                            last_error = "AI 返回格式错误"
                            continue
                        error_position = getattr(parse_error, 'pos', None)
                        detail = f"，位置 {error_position}" if error_position is not None else ""
                        return LLMEvalResult(
                            success=False,
                            reason=(
                                f"AI 返回格式错误（自动纠正后仍无法解析；"
                                f"结束原因 {finish_reason}，返回长度 {len(content)}{detail}）"
                            ),
                            model=model,
                        )
                    save_capability(api_config, {
                        "status": "compatible" if use_tool_output else "limited",
                        "protocol": protocol,
                        "output_mode": "tool" if use_tool_output else "json_text",
                        "message": "支持结构化工具调用" if use_tool_output else "使用 JSON 文本兼容模式",
                    })
                    return LLMEvalResult(
                        success=True,
                        adjustment=parsed['adjustment'],
                        reason=parsed['reason'],
                        model=model,
                        hard_condition_verdict=parsed['hard_condition_verdict'],
                        hard_condition_findings=parsed['hard_condition_findings'],
                        dimension_scores=parsed.get('dimension_scores'),
                    )
                elif response.status_code == 429:
                    # Rate limited — respect Retry-After header, fallback to 5/15/30s
                    retry_after = response.headers.get('Retry-After')
                    if retry_after and retry_after.isdigit():
                        delay = int(retry_after) + random.uniform(0, 1)
                    else:
                        delay = _BACKOFF_DELAYS[min(attempt, len(_BACKOFF_DELAYS) - 1)] + random.uniform(0, 2)
                    print(f"  ⚠️ API 限流 (429)，{delay:.1f}s 后重试 ({attempt+1}/{max_retries})")
                    time.sleep(delay)
                    last_error = "Rate limited (429)"
                    continue
                elif 500 <= response.status_code < 600:
                    # Server error — retry with delay
                    delay = 1 + random.uniform(0, 0.5)
                    print(f"  ⚠️ API 服务端错误 ({response.status_code})，{delay:.1f}s 后重试 ({attempt+1}/{max_retries})")
                    time.sleep(delay)
                    last_error = f"Server error ({response.status_code})"
                    continue
                else:
                    if use_tool_output and 400 <= response.status_code < 500:
                        use_tool_output = False
                        url, headers, body, protocol = _make_request(False)
                        save_capability(api_config, {
                            "status": "limited",
                            "protocol": protocol,
                            "output_mode": "json_text",
                            "message": "模型不支持工具调用，已自动降级",
                        })
                        last_error = f"Tool output unsupported ({response.status_code})"
                        continue
                    try:
                        error_payload = response.json()
                    except ValueError:
                        error_payload = response.text
                    friendly = friendly_http_error(response.status_code, error_payload)
                    print(f"  ❌ API 请求失败 ({response.status_code}): {response.text[:200]}")
                    return LLMEvalResult(success=False, reason=friendly)

            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                label = f"（{request_label}）" if request_label else ""
                last_error = str(e)
                if attempt + 1 >= max_retries:
                    print(
                        f"  ⚠️ 网络异常{label}：{type(e).__name__}，"
                        f"已达最大重试次数 ({attempt+1}/{max_retries})"
                    )
                    break
                delay = (
                    _NETWORK_BACKOFF_DELAYS[min(attempt, len(_NETWORK_BACKOFF_DELAYS) - 1)]
                    + random.uniform(0, 1)
                )
                print(
                    f"  ⚠️ 网络异常{label}：{type(e).__name__}，"
                    f"{delay:.1f}s 后重试 ({attempt+1}/{max_retries})"
                )
                time.sleep(delay)
                continue
            except Exception as e:
                print(f"  ❌ LLM 调用异常：{type(e).__name__}: {e}")
                return LLMEvalResult(success=False, reason=str(e)[:50])

    finally:
        session.close()

    return LLMEvalResult(success=False, reason=f"Max retries: {last_error}")


def probe_model_compatibility(api_config: dict, api_key: str, *, force: bool = False) -> dict:
    """Verify that a model can produce a parseable candidate evaluation."""
    cached = None if force else load_capability(api_config)
    if cached and cached.get("status") != "incompatible":
        return cached
    probe_config = dict(api_config)
    probe_config["_probe_output_mode"] = True
    probe_config["_ignore_capability_cache"] = force
    messages = _build_prompt(
        "Java 工程师，要求本科及以上、4年以上经验",
        "测试候选人：本科，5年 Java 开发经验",
        "硬条件：统招本科，经验：≥4年",
    )
    started = time.time()
    result = _call_llm_api(messages, probe_config, api_key)
    capability = load_capability(api_config) or {}
    if result.success:
        capability.update({
            "status": capability.get("status", "compatible"),
            "protocol": detect_protocol(api_config),
            "output_mode": capability.get("output_mode", "json_text"),
            "message": capability.get("message", "评估格式验证通过"),
            "response_time": time.time() - started,
        })
        save_capability(api_config, capability)
        return capability
    capability = {
        "status": "incompatible",
        "protocol": detect_protocol(api_config),
        "output_mode": "none",
        "message": result.reason or "无法生成程序可解析的评估结果",
        "response_time": time.time() - started,
    }
    return capability


def _recalc_recommend_level(score: int) -> str:
    """Recalculate recommend level from adjusted score."""
    if score >= SCORE_THRESHOLD_STRONG:
        return "强烈推荐"
    elif score >= SCORE_THRESHOLD_RECOMMEND:
        return "推荐"
    if score >= SCORE_THRESHOLD_PASS:
        return "待定"
    return "已淘汰"


def _evaluate_single(index: int, candidate: dict, job_requirement: str,
                     api_config: dict, api_key: str, hard_conditions: str = "",
                     stop_event=None) -> tuple:
    """Evaluate a single candidate with LLM. Returns (index, result, candidate_ref)."""
    messages = _build_prompt(job_requirement, build_llm_candidate_summary(candidate), hard_conditions)
    result = _call_llm_api(
        messages,
        api_config,
        api_key,
        stop_event=stop_event,
        request_label=str(candidate.get('name') or index + 1),
    )
    return index, result, candidate


def _resolve_eval_workers(api_config: dict, max_workers: int | None) -> tuple[int, bool]:
    """Return effective concurrency and whether the endpoint is a relay service."""
    if max_workers is not None:
        return max(1, max_workers), False

    is_relay = _is_relay_endpoint(api_config)
    return (LLM_RELAY_MAX_WORKERS if is_relay else LLM_MAX_WORKERS), is_relay


def evaluate_batch(
    candidates: list,
    job_requirement: str,
    api_config: dict,
    api_key: str,
    *,
    hard_conditions: str = "",
    rule: dict[str, Any] | None = None,
    max_candidates: int | None = None,
    progress_callback=None,
    stop_event: Optional[threading.Event] = None,
    max_workers: int | None = None,
) -> list:
    """Evaluate candidates with LLM and adjust scores (concurrent).

    Args:
        candidates: list of candidate_record dicts (passed filter, score >= SCORE_THRESHOLD_PASS)
        job_requirement: raw job requirement text
        api_config: dict with 'base_url' and 'model'
        api_key: API key string
        hard_conditions: optional hard-condition summary for LLM context
        rule: job_config rule dict for deterministic hard-condition validation
        max_candidates: max number of candidates to evaluate (None = no limit)
        progress_callback: callable(percentage, description)
        stop_event: threading.Event for cancellation
        max_workers: explicit concurrency override; None selects 5 for official APIs and 2 for relays

    Returns:
        Updated candidates list (same objects, modified in-place).
    """
    if not candidates:
        return candidates

    # Take top N by score (most impactful to evaluate), or all if unlimited
    to_evaluate = candidates[:max_candidates] if max_candidates is not None else candidates
    total = len(to_evaluate)
    for candidate in to_evaluate:
        candidate['llm_evaluated'] = False
        candidate['llm_error'] = ""

    base_url = str(api_config.get("base_url") or "")
    if base_url.startswith(("http://", "https://")):
        capability = probe_model_compatibility(api_config, api_key)
        status_label = {
            "compatible": "完整兼容",
            "limited": "兼容模式",
            "incompatible": "不兼容",
        }.get(capability.get("status"), "未知")
        print(
            f"模型能力：{status_label}，协议 {capability.get('protocol', 'unknown')}，"
            f"输出 {capability.get('output_mode', 'unknown')}"
        )
        if capability.get("status") == "incompatible":
            print(f"  [WARN] 模型能力验证失败：{capability.get('message', '未知原因')}")

    effective_workers, is_relay = _resolve_eval_workers(api_config, max_workers)
    relay_hint = "（检测到中转服务）" if is_relay else ""
    print(
        f"开始 AI 评估：{total} 人（共 {len(candidates)} 人通过筛选），"
        f"并发数：{effective_workers}{relay_hint}"
    )

    completed_count = 0
    count_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        # Submit all tasks
        future_to_index = {}
        for i, candidate in enumerate(to_evaluate):
            if stop_event and stop_event.is_set():
                print("  [停止] 用户请求停止，跳过剩余 AI 评估")
                break
            future = executor.submit(
                _evaluate_single, i, candidate, job_requirement, api_config, api_key, hard_conditions, stop_event
            )
            future_to_index[future] = i

        # 任务全部提交后，立即更新进度描述，不等待第一个结果返回
        if progress_callback:
            progress_callback(0, f"AI 评估中... 0/{total}")

        # Collect results as they complete
        for future in as_completed(future_to_index):
            if stop_event and stop_event.is_set():
                # Cancel remaining futures
                for f in future_to_index:
                    f.cancel()
                print("  [停止] 用户请求停止，取消剩余 AI 评估")
                break

            try:
                idx, result, candidate = future.result()
                name = candidate.get('name', '?')
                rule_score = candidate.get('match_score', 0)

                if result.success:
                    # Store original score
                    candidate['rule_score'] = rule_score

                    # Apply adjustment and clamp
                    new_score = max(0, min(100, rule_score + result.adjustment))
                    candidate['match_score'] = new_score
                    candidate['recommend_level'] = _recalc_recommend_level(new_score)

                    # 同步更新 score_breakdown，让拆解合计与总分一致
                    breakdown = candidate.get('score_breakdown')
                    if isinstance(breakdown, dict):
                        breakdown['ai_adjustment'] = result.adjustment
                        breakdown['total'] = new_score

                    # Clean reason: collapse newlines into single line for storage and display
                    clean_reason = result.reason.replace('\n', ' ').replace('\r', '').strip()

                    # Store LLM metadata
                    candidate['llm_evaluated'] = True
                    candidate['llm_error'] = ""
                    candidate['llm_adjustment'] = result.adjustment
                    candidate['llm_reason'] = clean_reason
                    candidate['llm_model'] = result.model
                    candidate['llm_hard_condition_verdict'] = result.hard_condition_verdict
                    candidate['llm_hard_condition_findings'] = result.hard_condition_findings or []
                    candidate['llm_dimension_scores'] = result.dimension_scores or {}
                    validated_failures = _validated_hard_failures(
                        candidate, result.hard_condition_findings, hard_conditions, rule
                    )
                    if validated_failures:
                        candidate['qualification_status'] = 'rejected'
                        candidate['qualification_reasons'] = [
                            f"AI发现并经规则复核：{item.get('condition')}"
                            for item in validated_failures
                        ]
                        candidate['qualification_evidence'] = validated_failures
                        candidate['manual_review_required'] = False
                        candidate['auto_greet_blocked_reason'] = "硬条件不符合"
                        candidate['recommend_level'] = "已淘汰"

                    sign = "+" if result.adjustment > 0 else ""
                    log_summary = _format_ai_log_summary(candidate, clean_reason, new_score)
                    print(
                        f"  [{idx+1}/{total}] {name}：{rule_score} → {new_score} "
                        f"（{sign}{result.adjustment}）｜{log_summary}"
                    )
                else:
                    candidate['llm_evaluated'] = False
                    candidate['llm_error'] = result.reason
                    print(
                        f"  [{idx+1}/{total}] {name}：评估失败（{result.reason}），"
                        f"保留原始分数 {rule_score}"
                    )

                # Update progress (thread-safe)
                with count_lock:
                    completed_count += 1
                    current = completed_count

                # Progress callback
                if progress_callback:
                    pct = int(current / total * 100)
                    progress_callback(pct, f"AI 评估中... {current}/{total}")

            except Exception as e:
                print(f"  [错误] AI 评估异常: {e}")

    return candidates


# ── 二次评估（基于完整简历） ──

_RESUME_MAX_CHARS = 6000
_RESUME_TOTAL_MAX_CHARS = 12000  # ~4K tokens，防止小模型 context window 溢出


def _build_resume_prompt(
    candidate: dict,
    resume_text: str,
    job_requirement: str,
    hard_conditions: str = "",
) -> list:
    """Build chat messages for second-round resume evaluation."""
    rule_score = candidate.get("rule_score", candidate.get("match_score", 0))
    llm_adj = candidate.get("llm_adjustment", 0)
    llm_reason = candidate.get("llm_reason", "无")

    truncated_resume = _truncate_text(resume_text, _RESUME_MAX_CHARS)

    # 总长超限时截断 job_requirement 和 hard_conditions，优先保留简历和评估理由
    overhead = len(_RESUME_SYSTEM_PROMPT) + len(_RESUME_USER_TEMPLATE) + len(truncated_resume) + _RESUME_PROMPT_OVERHEAD_BUFFER
    available = max(200, _RESUME_TOTAL_MAX_CHARS - overhead)
    if len(job_requirement) + len(hard_conditions) > available:
        if len(job_requirement) > available:
            job_requirement = _truncate_text(job_requirement, available)
            hard_conditions = ""
        else:
            hard_conditions = _truncate_text(hard_conditions, available - len(job_requirement))

    user_content = _RESUME_USER_TEMPLATE.format(
        job_requirement=job_requirement,
        hard_conditions=hard_conditions,
        rule_score=rule_score,
        llm_adjustment=llm_adj if llm_adj else "无",
        llm_reason=llm_reason,
        resume_text=truncated_resume,
    )
    return [
        {"role": "system", "content": _RESUME_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def evaluate_with_resume(
    candidate: dict,
    resume_text: str,
    job_requirement: str,
    api_config: dict,
    api_key: str,
    *,
    hard_conditions: str = "",
    stop_event=None,
) -> LLMEvalResult:
    """Perform second-round LLM evaluation using full resume text.

    Updates candidate dict in-place with resume_eval_* fields and
    recalculates match_score. Resume evaluation replaces first-round
    adjustment (not stacked):
        final = clamp(rule_score + resume_adjustment, 0, 100)

    First-round hard condition findings (qualification_status, llm_hard_condition_findings)
    are preserved — only the score adjustment is replaced.

    Returns LLMEvalResult with the round-2 adjustment.
    """
    messages = _build_resume_prompt(candidate, resume_text, job_requirement, hard_conditions)
    result = _call_llm_api(messages, api_config, api_key, stop_event=stop_event)

    if result.success:
        # Store round-2 metadata
        candidate["resume_eval_adjustment"] = result.adjustment
        candidate["resume_eval_reason"] = result.reason.replace("\n", " ").replace("\r", "").strip()
        candidate["resume_eval_model"] = result.model
        candidate["resume_eval_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Resume evaluation replaces first-round adjustment (not stacked)
        rule_score = candidate.get("rule_score", candidate.get("match_score", 0))
        new_score = max(0, min(100, rule_score + result.adjustment))
        candidate["match_score"] = new_score
        candidate["recommend_level"] = _recalc_recommend_level(new_score)

        # Update score_breakdown: resume replaces ai_adjustment in total
        breakdown = candidate.get("score_breakdown")
        if isinstance(breakdown, dict):
            breakdown["resume_adjustment"] = result.adjustment
            breakdown["total"] = new_score

    return result
