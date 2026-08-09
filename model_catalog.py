"""Model-catalog retrieval and deterministic change analysis."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ai_adapter import (
    discover_api_endpoint,
    has_endpoint_discovery,
    model_catalog_cache_key,
)
from constants import USER_AGENT


NON_CHAT_MODEL_KEYWORDS = (
    "embedding",
    "embed-",
    "rerank",
    "tts-",
    "whisper",
    "similarity",
    "moderation",
    "dap",
    "tokenizer",
)


@dataclass(frozen=True)
class ModelCatalogResponse:
    """Raw model-catalog response with endpoint-resolution metadata."""

    http_status: int
    response_text: str
    payload: Any
    base_url: str
    service_name: str = ""
    resolution_status: str = ""
    endpoint_confirmed: bool = False


@dataclass(frozen=True)
class ModelCatalogAnalysis:
    """Filtered models and endpoint-scoped changes from the previous catalog."""

    catalog_key: str
    models: tuple[str, ...]
    filtered_count: int
    new_models: frozenset[str]
    removed_models: frozenset[str]


def fetch_model_catalog(
    provider: str,
    api_key: str,
    base_url: str,
    *,
    request_get: Callable[..., Any] | None = None,
    discover_endpoint: Callable[..., Any] = discover_api_endpoint,
    verify_path: str | None = None,
    user_agent: str = USER_AGENT,
) -> ModelCatalogResponse:
    """Fetch one provider catalog without making inference requests."""
    if has_endpoint_discovery(provider):
        resolution = discover_endpoint(
            provider,
            api_key,
            preferred_base_url=base_url,
        )
        if resolution.status in ("confirmed", "catalog") and resolution.models:
            return ModelCatalogResponse(
                http_status=200,
                response_text=resolution.message,
                payload={"data": [{"id": model} for model in resolution.models]},
                base_url=resolution.base_url,
                service_name=resolution.service_name,
                resolution_status=resolution.status,
                endpoint_confirmed=resolution.status == "confirmed",
            )
        return ModelCatalogResponse(
            http_status=resolution.http_status or 0,
            response_text=resolution.message,
            payload={},
            base_url=base_url,
            resolution_status=resolution.status,
        )

    if request_get is None:
        import certifi
        import requests

        request_get = requests.get
        if verify_path is None:
            verify_path = certifi.where()

    # 自定义/中转地址只请求用户明确输入的 URL，不枚举其他域名。
    response = request_get(
        f"{base_url.rstrip('/')}/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": user_agent,
        },
        timeout=15,
        verify=verify_path,
    )
    status = int(getattr(response, "status_code", 0) or 0)
    return ModelCatalogResponse(
        http_status=status,
        response_text=str(getattr(response, "text", "")),
        payload=response.json() if status == 200 else {},
        base_url=base_url,
    )


def _extract_raw_model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    raw_items = payload.get("data")
    if raw_items is None:
        raw_items = payload.get("models")
    if not isinstance(raw_items, list):
        return []

    model_ids: list[str] = []
    for item in raw_items:
        model_id = item.get("id") if isinstance(item, Mapping) else item
        if isinstance(model_id, str) and model_id:
            model_ids.append(model_id)
    return model_ids


def analyze_model_catalog(
    payload: Any,
    *,
    fetched_models: Mapping[str, Any],
    provider: str,
    base_url: str,
    configured_base_url: str = "",
) -> ModelCatalogAnalysis | None:
    """Filter non-chat models and compare with the matching endpoint catalog."""
    raw_models = _extract_raw_model_ids(payload)
    if not raw_models:
        return None

    chat_models = [
        model_id
        for model_id in raw_models
        if not any(keyword in model_id.lower() for keyword in NON_CHAT_MODEL_KEYWORDS)
    ]
    models = tuple(sorted(set(chat_models)))
    catalog_key = model_catalog_cache_key(provider, base_url)
    previous_catalog = fetched_models.get(catalog_key)
    if previous_catalog is None:
        configured_catalog_key = model_catalog_cache_key(
            provider,
            configured_base_url,
        )
        previous_catalog = (
            fetched_models.get(provider, [])
            if configured_catalog_key == catalog_key
            else []
        )

    previous_models = set(previous_catalog or [])
    current_models = set(models)
    return ModelCatalogAnalysis(
        catalog_key=catalog_key,
        models=models,
        filtered_count=len(raw_models) - len(models),
        new_models=frozenset(current_models - previous_models),
        removed_models=frozenset(previous_models - current_models),
    )
