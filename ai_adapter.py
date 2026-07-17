"""Provider-aware AI API adapter and model capability cache."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from constants import USER_AGENT
from paths import BASE_DIR


CAPABILITY_CACHE_PATH = BASE_DIR / ".storage" / "model_capabilities.json"
DEFAULT_AZURE_API_VERSION = "2024-10-21"


@dataclass(frozen=True)
class EndpointCandidate:
    """Documented model-list endpoint for one provider channel."""

    channel: str
    service_name: str
    base_url: str
    auth_style: str = "bearer"
    key_prefixes: tuple[str, ...] = ()
    public_catalog: bool = False


@dataclass(frozen=True)
class EndpointResolution:
    """Deterministic result of validating credentials against known endpoints."""

    status: str
    provider: str
    channel: str = ""
    service_name: str = ""
    base_url: str = ""
    models: tuple[str, ...] = ()
    http_status: int | None = None
    message: str = ""


API_ENDPOINT_DISCOVERY_RULES: dict[str, tuple[EndpointCandidate, ...]] = {
    "qwen": (
        EndpointCandidate("qwen_cn", "阿里云百炼（中国）", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        EndpointCandidate("qwen_intl", "阿里云百炼（国际）", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        EndpointCandidate("qwen_us", "阿里云百炼（美国）", "https://dashscope-us.aliyuncs.com/compatible-mode/v1"),
        EndpointCandidate(
            "qwen_token_plan",
            "阿里云百炼 Token Plan",
            "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            key_prefixes=("sk-sp-",),
            public_catalog=True,
        ),
        EndpointCandidate(
            "qwen_coding_plan",
            "阿里云百炼 Coding Plan",
            "https://coding.dashscope.aliyuncs.com/v1",
            key_prefixes=("sk-sp-",),
            public_catalog=True,
        ),
    ),
    "deepseek": (
        EndpointCandidate("deepseek", "DeepSeek", "https://api.deepseek.com"),
    ),
    "kimi": (
        EndpointCandidate("kimi_cn", "Kimi 开放平台（国内）", "https://api.moonshot.cn/v1"),
        EndpointCandidate("kimi_global", "Kimi 开放平台（海外）", "https://api.moonshot.ai/v1"),
        EndpointCandidate("kimi_code", "Kimi Code", "https://api.kimi.com/coding/v1"),
    ),
    "zhipu": (
        EndpointCandidate("zhipu", "智谱开放平台", "https://open.bigmodel.cn/api/paas/v4"),
    ),
    "minimax": (
        EndpointCandidate("minimax_cn", "MiniMax（国内）", "https://api.minimaxi.com/v1"),
        EndpointCandidate("minimax_global", "MiniMax（海外）", "https://api.minimax.io/v1"),
    ),
    "xiaomi": (
        EndpointCandidate("xiaomi", "小米 MiMo", "https://api.xiaomimimo.com/v1"),
    ),
    "stepfun": (
        EndpointCandidate("stepfun", "阶跃星辰", "https://api.stepfun.com/v1"),
    ),
    "openai": (
        EndpointCandidate("openai", "OpenAI", "https://api.openai.com/v1"),
    ),
    "anthropic": (
        EndpointCandidate(
            "anthropic", "Anthropic", "https://api.anthropic.com/v1", auth_style="anthropic"
        ),
    ),
}

# Official endpoint evidence is kept beside the matching rule so updates can be
# reviewed against provider documentation instead of extending ad-hoc lists.
OFFICIAL_API_ENDPOINT_RULES = {
    "qwen": {
        "exact_hosts": (
            "dashscope.aliyuncs.com",
            "dashscope-intl.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
            "coding.dashscope.aliyuncs.com",
            "coding-intl.dashscope.aliyuncs.com",
        ),
        "suffix_hosts": ("maas.aliyuncs.com",),
        "service_name": "阿里云百炼",
        "docs_url": "https://help.aliyun.com/zh/model-studio/base-url",
    },
    "deepseek": {
        "exact_hosts": ("api.deepseek.com",),
        "service_name": "DeepSeek",
        "docs_url": "https://api-docs.deepseek.com/",
    },
    "kimi": {
        "exact_hosts": ("api.moonshot.ai", "api.moonshot.cn", "api.kimi.com"),
        "service_name": "Kimi",
        "docs_url": "https://platform.kimi.ai/docs/api/overview",
    },
    "zhipu": {
        "exact_hosts": ("open.bigmodel.cn",),
        "service_name": "智谱开放平台",
        "docs_url": "https://docs.bigmodel.cn/cn/guide/develop/http/introduction",
    },
    "minimax": {
        "exact_hosts": ("api.minimax.io", "api.minimaxi.com"),
        "service_name": "MiniMax",
        "docs_url": "https://platform.minimax.io/docs/token-plan/other-tools",
    },
    "xiaomi": {
        "exact_hosts": ("api.xiaomimimo.com",),
        "suffix_hosts": ("xiaomimimo.com",),
        "service_name": "小米 MiMo",
        "docs_url": "https://mimo.mi.com/docs/zh-CN/tokenplan/integration/tools-overview",
    },
    "stepfun": {
        "exact_hosts": ("api.stepfun.com",),
        "service_name": "阶跃星辰",
        "docs_url": "https://platform.stepfun.com/docs/zh/api-reference/files/create",
    },
    "openai": {
        "exact_hosts": ("api.openai.com",),
        "service_name": "OpenAI",
        "docs_url": "https://platform.openai.com/docs/api-reference/models",
    },
    "anthropic": {
        "exact_hosts": ("api.anthropic.com",),
        "service_name": "Anthropic",
        "docs_url": "https://platform.claude.com/docs/en/api/overview",
    },
}


def classify_api_endpoint(api_config: dict) -> dict[str, Any]:
    """Classify an endpoint using provider identity and documented host ownership.

    Exact hosts cover fixed public APIs. Suffix rules are only used for provider-
    controlled regional/workspace hosts documented by that provider. An unknown
    host is treated as relay/custom; a missing host remains unknown.
    """
    provider = str(api_config.get("api_provider") or "").strip().lower()
    parsed = urlsplit(str(api_config.get("base_url") or ""))
    hostname = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    rule = OFFICIAL_API_ENDPOINT_RULES.get(provider, {})
    exact_hosts = tuple(rule.get("exact_hosts", ()))
    suffix_hosts = tuple(rule.get("suffix_hosts", ()))
    is_official = bool(
        hostname
        and (
            hostname in exact_hosts
            or any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffix_hosts)
        )
    )

    service_name = str(rule.get("service_name") or provider or "未配置服务")
    if is_official:
        if provider == "kimi" and hostname == "api.kimi.com":
            service_name = "Kimi Code"
        elif hostname == "token-plan.cn-beijing.maas.aliyuncs.com":
            service_name = "阿里云百炼 Token Plan"
        elif hostname in {"coding.dashscope.aliyuncs.com", "coding-intl.dashscope.aliyuncs.com"}:
            service_name = "阿里云百炼 Coding Plan"
        elif provider == "xiaomi" and hostname.startswith("token-plan-"):
            service_name = "小米 MiMo Token Plan"
        elif provider == "zhipu" and "/api/coding/" in path:
            service_name = "智谱 GLM Coding Plan"
        elif provider == "stepfun" and path.startswith("/step_plan"):
            service_name = "阶跃星辰 Step Plan"

    return {
        "provider": provider,
        "hostname": hostname,
        "channel_type": "official" if is_official else ("relay" if hostname else "unknown"),
        "is_official": is_official,
        "is_relay": bool(hostname and not is_official),
        "service_name": service_name,
        "docs_url": str(rule.get("docs_url") or ""),
    }


def normalize_api_base_url(api_config: dict) -> str:
    """Normalize documented provider Base URLs without changing unknown relays."""
    raw_url = str(api_config.get("base_url") or "").strip().rstrip("/")
    provider = str(api_config.get("api_provider") or "").strip().lower()
    parts = urlsplit(raw_url)
    if (
        provider == "kimi"
        and (parts.hostname or "").lower() == "api.kimi.com"
        and parts.path.rstrip("/") == "/coding"
    ):
        return urlunsplit((parts.scheme, parts.netloc, "/coding/v1", parts.query, parts.fragment))
    if (
        provider == "qwen"
        and (parts.hostname or "").lower() == "token-plan.cn-beijing.maas.aliyuncs.com"
        and parts.path.rstrip("/") == "/v1"
    ):
        return urlunsplit((
            parts.scheme,
            parts.netloc,
            "/compatible-mode/v1",
            parts.query,
            parts.fragment,
        ))
    return raw_url


def has_endpoint_discovery(provider: str) -> bool:
    """Return whether a provider has documented endpoints safe to auto-probe."""
    return str(provider or "").strip().lower() in API_ENDPOINT_DISCOVERY_RULES


def model_catalog_cache_key(provider: str, base_url: str) -> str:
    """Return an endpoint-scoped key for cached model catalog comparisons."""
    normalized = normalize_api_base_url({
        "api_provider": provider,
        "base_url": base_url,
    })
    return f"{str(provider or '').strip().lower()}|{normalized.lower()}"


def _candidate_headers(candidate: EndpointCandidate, api_key: str) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Connection": "close"}
    if candidate.auth_style == "anthropic":
        headers.update({
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        })
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _extract_model_ids(payload: Any) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        return ()
    raw_models = payload.get("data")
    if raw_models is None:
        raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return ()
    models: list[str] = []
    for item in raw_models:
        model_id = item.get("id") if isinstance(item, dict) else item
        model_id = str(model_id or "").strip()
        if model_id:
            models.append(model_id)
    return tuple(dict.fromkeys(models))


def _ordered_endpoint_candidates(
    provider: str,
    api_key: str,
    preferred_base_url: str = "",
) -> tuple[EndpointCandidate, ...]:
    provider = str(provider or "").strip().lower()
    candidates = list(API_ENDPOINT_DISCOVERY_RULES.get(provider, ()))
    if provider == "kimi" and str(api_key or "").startswith("sk-kimi-"):
        candidates.sort(key=lambda item: item.channel != "kimi_code")

    preferred = normalize_api_base_url({
        "api_provider": provider,
        "base_url": preferred_base_url,
    })
    if preferred:
        matched = next(
            (item for item in candidates if item.base_url.rstrip("/") == preferred),
            None,
        )
        if matched is not None:
            candidates.remove(matched)
            candidates.insert(0, matched)
        else:
            endpoint = classify_api_endpoint({
                "api_provider": provider,
                "base_url": preferred,
            })
            candidates.insert(0, EndpointCandidate(
                "configured",
                str(endpoint.get("service_name") or "当前配置"),
                preferred,
                auth_style="anthropic" if provider == "anthropic" else "bearer",
                public_catalog=(
                    provider == "qwen"
                    and (urlsplit(preferred).hostname or "").lower() in {
                        "token-plan.cn-beijing.maas.aliyuncs.com",
                        "coding.dashscope.aliyuncs.com",
                        "coding-intl.dashscope.aliyuncs.com",
                    }
                ),
            ))

    deduped: list[EndpointCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.base_url.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return tuple(deduped)


def discover_api_endpoint(
    provider: str,
    api_key: str,
    preferred_base_url: str = "",
    *,
    request_get=None,
    timeout: tuple[int, int] = (5, 10),
) -> EndpointResolution:
    """Identify the credential channel through documented model-list endpoints.

    Key prefixes only affect candidate order. A 200 response confirms the
    endpoint; no inference request is made. Unknown providers are never probed.
    """
    provider = str(provider or "").strip().lower()
    if not api_key:
        return EndpointResolution("invalid", provider, message="API Key 为空")
    candidates = _ordered_endpoint_candidates(provider, api_key, preferred_base_url)
    preferred = normalize_api_base_url({
        "api_provider": provider,
        "base_url": preferred_base_url,
    })
    if not candidates:
        return EndpointResolution(
            "unsupported", provider, message="该服务商没有可自动识别的官方端点"
        )

    if request_get is None:
        import certifi
        import requests

        def request_get(url, **kwargs):
            kwargs.setdefault("verify", certifi.where())
            return requests.get(url, **kwargs)

    transient: EndpointResolution | None = None
    public_catalog_seen: EndpointResolution | None = None
    last_status: int | None = None
    last_auth_status: int | None = None
    for candidate in candidates:
        models_url = f"{candidate.base_url.rstrip('/')}/models"
        try:
            response = request_get(
                models_url,
                headers=_candidate_headers(candidate, api_key),
                timeout=timeout,
            )
        except Exception as exc:
            if transient is None:
                transient = EndpointResolution(
                    "unavailable",
                    provider,
                    candidate.channel,
                    candidate.service_name,
                    candidate.base_url,
                    message=f"{type(exc).__name__}: {str(exc)[:120]}",
                )
            continue

        status_code = int(getattr(response, "status_code", 0) or 0)
        last_status = status_code
        if not candidate.public_catalog:
            last_auth_status = status_code
        if status_code == 200:
            try:
                payload = response.json()
            except (TypeError, ValueError):
                payload = None
            models = _extract_model_ids(payload)
            if candidate.public_catalog:
                public_catalog_seen = EndpointResolution(
                    "catalog",
                    provider,
                    candidate.channel,
                    candidate.service_name,
                    candidate.base_url,
                    models,
                    status_code,
                    "该渠道的模型目录无需认证，不能据此证明 API Key 有效",
                )
                prefix_matches = candidate.key_prefixes and any(
                    str(api_key).startswith(prefix) for prefix in candidate.key_prefixes
                )
                if prefix_matches and candidate.base_url.rstrip("/") == preferred:
                    return public_catalog_seen
                continue
            return EndpointResolution(
                "confirmed",
                provider,
                candidate.channel,
                candidate.service_name,
                candidate.base_url,
                models,
                status_code,
                "接入渠道和 API Key 已确认",
            )
        if status_code in (401, 403, 404):
            continue
        if status_code in (402, 429):
            return EndpointResolution(
                "probable",
                provider,
                candidate.channel,
                candidate.service_name,
                candidate.base_url,
                http_status=status_code,
                message=(
                    "接入渠道可能已匹配，但会员状态异常"
                    if status_code == 402
                    else "接入渠道可能已匹配，但请求受到限流"
                ),
            )
        if status_code >= 500 and transient is None:
            transient = EndpointResolution(
                "unavailable",
                provider,
                candidate.channel,
                candidate.service_name,
                candidate.base_url,
                http_status=status_code,
                message=f"服务暂时不可用（HTTP {status_code}）",
            )

    if transient is not None:
        return transient
    preferred_host = (urlsplit(preferred).hostname or "").lower()
    qwen_plan_hosts = {
        "token-plan.cn-beijing.maas.aliyuncs.com",
        "coding.dashscope.aliyuncs.com",
        "coding-intl.dashscope.aliyuncs.com",
    }
    if (
        public_catalog_seen is not None
        and provider == "qwen"
        and preferred_host in qwen_plan_hosts
    ):
        return EndpointResolution(
            "invalid",
            provider,
            http_status=last_auth_status or last_status,
            message=(
                "当前地址是阿里云套餐专属渠道，但 API Key 不是其专属 sk-sp- Key，"
                "并且也未通过百炼按量付费渠道认证；请检查 Key 类型和计费方案"
            ),
        )
    return EndpointResolution(
        "invalid",
        provider,
        http_status=last_auth_status or last_status,
        message="API Key 与已登记的官方接入渠道均不匹配",
    )


def detect_protocol(api_config: dict) -> str:
    """Return the wire protocol required by the configured endpoint."""
    provider = str(api_config.get("api_provider") or "").lower()
    base_url = str(api_config.get("base_url") or "").lower()
    if provider == "anthropic" or "api.anthropic.com" in base_url:
        return "anthropic"
    if provider == "azure" or ".openai.azure.com" in base_url:
        return "azure"
    return "openai_compatible"


def capability_cache_key(api_config: dict) -> str:
    """Build a non-secret cache key scoped to endpoint and model."""
    return "|".join((
        detect_protocol(api_config),
        normalize_api_base_url(api_config).lower(),
        str(api_config.get("model") or "").strip().lower(),
    ))


def load_capability(api_config: dict) -> dict | None:
    """Load cached capability data, returning None for missing/corrupt cache."""
    try:
        data = json.loads(CAPABILITY_CACHE_PATH.read_text(encoding="utf-8"))
        value = data.get(capability_cache_key(api_config))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def save_capability(api_config: dict, capability: dict) -> None:
    """Atomically cache capability metadata without credentials or responses."""
    try:
        CAPABILITY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(CAPABILITY_CACHE_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError, TypeError):
            data = {}
        clean = {
            "status": str(capability.get("status") or "unknown"),
            "protocol": str(capability.get("protocol") or detect_protocol(api_config)),
            "output_mode": str(capability.get("output_mode") or "json_text"),
            "message": str(capability.get("message") or "")[:200],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        data[capability_cache_key(api_config)] = clean
        temp_path = Path(f"{CAPABILITY_CACHE_PATH}.tmp")
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, CAPABILITY_CACHE_PATH)
    except OSError:
        return


def _append_query(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault(key, value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _azure_url(api_config: dict) -> str:
    base_url = normalize_api_base_url(api_config)
    if base_url.endswith("/chat/completions"):
        return _append_query(
            base_url,
            "api-version",
            str(api_config.get("api_version") or DEFAULT_AZURE_API_VERSION),
        )
    if "/openai/v1" in base_url:
        return f"{base_url}/chat/completions"
    deployment = str(api_config.get("deployment") or api_config.get("model") or "").strip()
    url = f"{base_url}/openai/deployments/{deployment}/chat/completions"
    return _append_query(
        url,
        "api-version",
        str(api_config.get("api_version") or DEFAULT_AZURE_API_VERSION),
    )


def build_request(
    api_config: dict,
    api_key: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int,
    temperature: float,
    tool: dict | None = None,
    force_tool: bool = False,
) -> tuple[str, dict, dict, str]:
    """Build a provider-specific request with one normalized input shape."""
    protocol = detect_protocol(api_config)
    base_url = normalize_api_base_url(api_config)
    model = str(api_config.get("model") or "")
    if protocol == "anthropic":
        url = f"{base_url}/messages" if base_url.endswith("/v1") else f"{base_url}/v1/messages"
        system_parts = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
        body: dict[str, Any] = {
            "model": model,
            "messages": [m for m in messages if m.get("role") != "system"],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if tool:
            fn = tool["function"]
            body["tools"] = [{
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn["parameters"],
            }]
            if force_tool:
                body["tool_choice"] = {"type": "tool", "name": fn["name"]}
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": USER_AGENT,
            "Connection": "close",
        }
        return url, headers, body, protocol

    url = _azure_url(api_config) if protocol == "azure" else f"{base_url}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Connection": "close",
    }
    headers["api-key" if protocol == "azure" else "Authorization"] = (
        api_key if protocol == "azure" else f"Bearer {api_key}"
    )
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if tool:
        body["tools"] = [tool]
        if force_tool:
            body["tool_choice"] = {
                "type": "function",
                "function": {"name": tool["function"]["name"]},
            }
    base_lower = base_url.lower()
    model_lower = model.lower()
    if "api.kimi.com/coding" in base_lower:
        # Kimi Code accepts temperature=1 and prefers max_completion_tokens.
        body["temperature"] = 1
        body["max_completion_tokens"] = body.pop("max_tokens")
    if "dashscope.aliyuncs.com" in base_lower and model_lower.startswith("qwen3.7"):
        body["enable_thinking"] = False
    if api_config.get("_disable_thinking") and "xiaomimimo.com" in base_lower:
        body["max_completion_tokens"] = body.pop("max_tokens")
        body["thinking"] = {"type": "disabled"}
    return url, headers, body, protocol


def normalize_response(protocol: str, payload: dict) -> tuple[dict, str]:
    """Normalize OpenAI and Anthropic responses to an OpenAI-like message."""
    if protocol != "anthropic":
        choice = (payload.get("choices") or [{}])[0]
        message = dict(choice.get("message") or {})
        if not message.get("content") and message.get("reasoning_content"):
            message["content"] = message["reasoning_content"]
        return message, str(choice.get("finish_reason") or "unknown")

    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in payload.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "type": "function",
                "function": {
                    "name": str(block.get("name") or ""),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                },
            })
    return {
        "content": "\n".join(text_parts),
        "tool_calls": tool_calls,
    }, str(payload.get("stop_reason") or "unknown")


def friendly_http_error(status_code: int, payload_or_text: Any) -> str:
    """Convert common provider errors to concise user-facing diagnostics."""
    if isinstance(payload_or_text, dict):
        error = payload_or_text.get("error") or {}
        if isinstance(error, dict):
            text = str(error.get("message") or error.get("type") or "")
        else:
            text = str(error)
    else:
        text = str(payload_or_text or "")
    lowered = text.lower()
    if status_code in (401, 403):
        return "认证失败（API Key 无效、无权限或已过期）"
    if status_code == 404:
        return "模型或 API 地址不存在"
    if status_code == 429:
        return "请求受限或配额不足"
    if "not activated" in lowered:
        return "该服务商未开通此模型"
    if "quota" in lowered or "limit" in lowered:
        return "配额超限"
    return f"HTTP {status_code}" + (f"：{text[:120]}" if text else "")
