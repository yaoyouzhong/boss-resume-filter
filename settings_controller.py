"""Model configuration and catalog orchestration without Tk dependencies."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


ModelConfig = dict[str, Any]


PROVIDER_DEFAULTS: dict[str, tuple[str, str]] = {
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "deepseek": ("https://api.deepseek.com", "deepseek-v4-pro"),
    "kimi": ("https://api.moonshot.ai/v1", "kimi-k2.6"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-5.1"),
    "minimax": ("https://api.minimaxi.com/v1", "MiniMax-M3"),
    "xiaomi": ("https://api.xiaomimimo.com/v1", "mimo-v2.5-pro"),
    "stepfun": ("https://api.stepfun.com/v1", "step-3.7-flash"),
    "openai": ("https://api.openai.com/v1", "GPT-5.5"),
    "anthropic": ("https://api.anthropic.com/v1", "claude-sonnet4.8"),
    "custom": ("", ""),
}


@dataclass(frozen=True)
class SavedModelOutcome:
    """Prepared in-memory model config and a privacy-safe save summary."""

    api_config: ModelConfig
    saved_models: tuple[ModelConfig, ...]
    models: tuple[str, ...]
    added_count: int
    updated_count: int
    default_changed: bool

    @property
    def summary(self) -> str:
        if len(self.models) > 1:
            return (
                f"已保存 {len(self.models)} 个模型到列表"
                f"（新增 {self.added_count}，更新 {self.updated_count}）"
            )
        provider = self.api_config.get("api_provider", "")
        return f"模型 {provider}/{self.models[0]} 已保存到已保存模型列表"


@dataclass(frozen=True)
class ProviderSelection:
    """Resolved endpoint and model shown after selecting one provider."""

    base_url: str
    model: str


@dataclass(frozen=True)
class CatalogOutcome:
    """Plain model-catalog result suitable for one UI-thread renderer."""

    status: str
    provider: str
    base_url: str
    http_status: int = 0
    response_text: str = ""
    resolution_status: str = ""
    service_name: str = ""
    endpoint_confirmed: bool = False
    payload: Any = None
    analysis: Any = None
    error: str = ""


@dataclass(frozen=True)
class ModelProbeOutcome:
    """Plain compatibility result for one explicitly supplied model key."""

    status: str
    response_time: float = 0.0
    mode: str = ""
    message: str = ""


class SettingsController:
    """Coordinate model configuration while never retaining API credentials."""

    @staticmethod
    def default_api_config() -> ModelConfig:
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

    @staticmethod
    def sanitize_for_save(config: Mapping[str, Any]) -> ModelConfig:
        """Return a detached config without any plaintext key fields."""
        clean = {key: value for key, value in config.items() if key != "api_key"}
        clean["saved_models"] = [
            {
                key: value
                for key, value in dict(model).items()
                if key not in {"api_key", "api_key_ref"}
            }
            for model in clean.get("saved_models", [])
        ]
        return clean

    @staticmethod
    def model_ref_key(model: Mapping[str, Any]) -> tuple[str, str, str]:
        return (
            str(model.get("api_provider") or ""),
            str(model.get("base_url") or "").strip().rstrip("/"),
            str(model.get("model") or ""),
        )

    @classmethod
    def model_ref_matches(
        cls,
        left: Mapping[str, Any],
        right: Mapping[str, Any],
    ) -> bool:
        return cls.model_ref_key(left) == cls.model_ref_key(right)

    def prepare_saved_models(
        self,
        current_config: Mapping[str, Any],
        saved_models: Sequence[Mapping[str, Any]],
        *,
        provider: str,
        base_url: str,
        model_name: str,
        pending_models: Sequence[str],
        api_key: str,
        llm_read_timeout: int,
    ) -> SavedModelOutcome:
        """Merge one or many models while preserving the active model identity."""
        models = tuple(
            dict.fromkeys(
                str(model).strip()
                for model in (pending_models or (model_name,))
                if str(model).strip()
            )
        )
        if not models:
            raise ValueError("请输入模型名称")

        merged = [dict(model) for model in saved_models]
        current_ref = {
            "api_provider": current_config.get("api_provider", ""),
            "base_url": current_config.get("base_url", ""),
            "model": current_config.get("model", ""),
        }
        has_saved_current = any(
            self.model_ref_matches(model, current_ref) for model in merged
        )
        default_changed = not has_saved_current
        if default_changed:
            top_provider, top_base_url, top_model = provider, base_url, models[0]
        else:
            top_provider = str(current_config.get("api_provider") or provider)
            top_base_url = str(current_config.get("base_url") or base_url)
            top_model = str(current_config.get("model") or "")

        same_endpoint = _endpoint_key(provider, base_url) == _endpoint_key(
            top_provider,
            top_base_url,
        )
        top_api_key = api_key if same_endpoint else str(current_config.get("api_key") or "")
        prepared: ModelConfig = {
            "api_provider": top_provider,
            "api_key": top_api_key,
            "base_url": top_base_url,
            "model": top_model,
            "saved_models": merged,
            "providers": dict(current_config.get("providers", {}) or {}),
            "fetched_models": dict(current_config.get("fetched_models", {}) or {}),
            "llm_read_timeout": llm_read_timeout,
        }
        education_ref = current_config.get("education_model_ref")
        if education_ref:
            prepared["education_model_ref"] = dict(education_ref)

        added_count = 0
        updated_count = 0
        for name in models:
            requested = {
                "api_provider": provider,
                "base_url": base_url,
                "model": name,
            }
            existing = next(
                (model for model in merged if self.model_ref_matches(model, requested)),
                None,
            )
            if existing is None:
                merged.append(requested)
                added_count += 1
            else:
                existing["api_provider"] = provider
                existing["base_url"] = base_url
                updated_count += 1

        return SavedModelOutcome(
            api_config=prepared,
            saved_models=tuple(merged),
            models=models,
            added_count=added_count,
            updated_count=updated_count,
            default_changed=default_changed,
        )

    @staticmethod
    def resolve_provider_selection(
        provider: str,
        current_config: Mapping[str, Any],
        saved_models: Sequence[Mapping[str, Any]],
    ) -> ProviderSelection:
        """Resolve the most relevant saved or default endpoint for a provider."""
        if str(current_config.get("api_provider") or "") == provider:
            return ProviderSelection(
                str(current_config.get("base_url") or ""),
                str(current_config.get("model") or ""),
            )
        matching = [
            model
            for model in saved_models
            if str(model.get("api_provider") or "") == provider
        ]
        if matching:
            return ProviderSelection(
                str(matching[-1].get("base_url") or ""),
                str(matching[-1].get("model") or ""),
            )
        base_url, model = PROVIDER_DEFAULTS.get(provider, ("", ""))
        return ProviderSelection(base_url, model)

    @staticmethod
    def fetch_catalog(
        *,
        provider: str,
        api_key: str,
        base_url: str,
        fetched_models: Mapping[str, Any],
        configured_base_url: str,
        fetcher: Callable[..., Any],
        analyzer: Callable[..., Any],
        timeout_errors: tuple[type[BaseException], ...] = (),
        connection_errors: tuple[type[BaseException], ...] = (),
    ) -> CatalogOutcome:
        """Fetch and classify one catalog without retaining the supplied API key."""
        try:
            response = fetcher(provider, api_key, base_url)
            resolved_base_url = str(response.base_url or base_url)
            status = int(response.http_status or 0)
            resolution_status = str(response.resolution_status or "")
            common = {
                "provider": provider,
                "base_url": resolved_base_url,
                "http_status": status,
                "response_text": str(response.response_text or ""),
                "resolution_status": resolution_status,
                "service_name": str(response.service_name or ""),
                "endpoint_confirmed": bool(response.endpoint_confirmed),
                "payload": response.payload,
            }
            if status == 200:
                analysis = analyzer(
                    response.payload,
                    fetched_models=fetched_models,
                    provider=provider,
                    base_url=resolved_base_url,
                    configured_base_url=configured_base_url,
                )
                return CatalogOutcome(
                    status="success" if analysis is not None else "empty",
                    analysis=analysis,
                    **common,
                )
            if not resolution_status and status == 401:
                kind = "auth_error"
            elif not resolution_status and status == 404:
                kind = "unsupported"
            elif resolution_status in {"probable", "unavailable"}:
                kind = "temporary"
            else:
                kind = "error"
            return CatalogOutcome(status=kind, **common)
        except timeout_errors as exc:
            return CatalogOutcome("timeout", provider, base_url, error=str(exc)[:200])
        except connection_errors as exc:
            return CatalogOutcome("connection_error", provider, base_url, error=str(exc)[:200])
        except Exception as exc:
            return CatalogOutcome("exception", provider, base_url, error=str(exc)[:200])

    @staticmethod
    def probe_model(
        *,
        provider: str,
        base_url: str,
        model: str,
        api_key: str,
        probe: Callable[..., Mapping[str, Any]],
    ) -> ModelProbeOutcome:
        """Probe one model without storing the explicitly supplied API key."""
        try:
            capability = probe(
                {
                    "api_provider": provider,
                    "base_url": base_url,
                    "model": model,
                },
                api_key,
                force=True,
            )
            if capability.get("status") in {"compatible", "limited"}:
                mode = "工具" if capability.get("output_mode") == "tool" else "兼容"
                return ModelProbeOutcome(
                    status="success",
                    response_time=float(capability.get("response_time") or 0),
                    mode=mode,
                )
            return ModelProbeOutcome(
                status="error",
                message=str(capability.get("message") or "不兼容"),
            )
        except Exception as exc:
            return ModelProbeOutcome(
                status="error",
                message=f"异常: {str(exc)[:50]}",
            )


def _endpoint_key(provider: object, base_url: object) -> tuple[str, str]:
    return (
        str(provider or "").strip(),
        str(base_url or "").strip().rstrip("/"),
    )
