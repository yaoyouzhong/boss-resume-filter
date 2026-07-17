"""Unit tests for provider-aware AI request adaptation."""
import json

from ai_adapter import (
    API_ENDPOINT_DISCOVERY_RULES,
    OFFICIAL_API_ENDPOINT_RULES,
    build_request,
    classify_api_endpoint,
    discover_api_endpoint,
    detect_protocol,
    has_endpoint_discovery,
    model_catalog_cache_key,
    normalize_api_base_url,
    normalize_response,
)


TOOL = {
    "type": "function",
    "function": {
        "name": "submit",
        "description": "submit result",
        "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}},
    },
}
MESSAGES = [
    {"role": "system", "content": "system rule"},
    {"role": "user", "content": "evaluate"},
]


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_detect_protocols():
    assert detect_protocol({"api_provider": "anthropic"}) == "anthropic"
    assert detect_protocol({"base_url": "https://x.openai.azure.com"}) == "azure"
    assert detect_protocol({"api_provider": "deepseek"}) == "openai_compatible"


def test_official_endpoint_rules_cover_every_supported_provider_with_evidence():
    expected = {
        "qwen", "deepseek", "kimi", "zhipu", "minimax",
        "xiaomi", "stepfun", "openai", "anthropic",
    }

    assert set(OFFICIAL_API_ENDPOINT_RULES) == expected
    assert all(rule["docs_url"].startswith("https://") for rule in OFFICIAL_API_ENDPOINT_RULES.values())


def test_endpoint_discovery_registry_covers_supported_non_custom_providers():
    assert set(API_ENDPOINT_DISCOVERY_RULES) == set(OFFICIAL_API_ENDPOINT_RULES)
    assert has_endpoint_discovery("kimi") is True
    assert has_endpoint_discovery("custom") is False


def test_kimi_code_key_prefix_only_prioritizes_then_models_confirms():
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(200, {"data": [{"id": "k3"}]})

    result = discover_api_endpoint("kimi", "sk-kimi-secret", request_get=fake_get)

    assert result.status == "confirmed"
    assert result.channel == "kimi_code"
    assert result.base_url == "https://api.kimi.com/coding/v1"
    assert result.models == ("k3",)
    assert calls[0][0] == "https://api.kimi.com/coding/v1/models"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer sk-kimi-secret"


def test_kimi_discovery_falls_through_auth_mismatch_to_matching_channel():
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        if "api.moonshot.cn" in url:
            return FakeResponse(401, {"error": {"message": "invalid"}})
        return FakeResponse(200, {"data": [{"id": "kimi-k2.6"}]})

    result = discover_api_endpoint("kimi", "sk-platform", request_get=fake_get)

    assert result.status == "confirmed"
    assert result.channel == "kimi_global"
    assert calls[:2] == [
        "https://api.moonshot.cn/v1/models",
        "https://api.moonshot.ai/v1/models",
    ]


def test_preferred_registered_endpoint_is_probed_first_for_every_provider():
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return FakeResponse(200, {"data": ["model-a"]})

    result = discover_api_endpoint(
        "minimax",
        "secret",
        preferred_base_url="https://api.minimax.io/v1/",
        request_get=fake_get,
    )

    assert result.status == "confirmed"
    assert result.channel == "minimax_global"
    assert calls == ["https://api.minimax.io/v1/models"]


def test_anthropic_discovery_uses_provider_specific_auth_headers():
    captured = {}

    def fake_get(_url, **kwargs):
        captured.update(kwargs["headers"])
        return FakeResponse(200, {"data": [{"id": "claude-test"}]})

    result = discover_api_endpoint("anthropic", "secret", request_get=fake_get)

    assert result.status == "confirmed"
    assert captured["x-api-key"] == "secret"
    assert captured["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in captured


def test_discovery_preserves_probable_rate_limited_channel_without_fallback():
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return FakeResponse(429, {"error": {"message": "limited"}})

    result = discover_api_endpoint("kimi", "sk-kimi-secret", request_get=fake_get)

    assert result.status == "probable"
    assert result.channel == "kimi_code"
    assert result.http_status == 429
    assert len(calls) == 1


def test_unknown_provider_is_not_auto_probed():
    result = discover_api_endpoint("custom", "secret")

    assert result.status == "unsupported"


def test_model_catalog_cache_is_scoped_by_provider_and_normalized_endpoint():
    code_key = model_catalog_cache_key("kimi", "https://api.kimi.com/coding/")
    platform_key = model_catalog_cache_key("kimi", "https://api.moonshot.ai/v1")

    assert code_key == "kimi|https://api.kimi.com/coding/v1"
    assert platform_key == "kimi|https://api.moonshot.ai/v1"
    assert code_key != platform_key


def test_official_endpoint_classification_uses_provider_and_documented_host():
    official_cases = [
        ("qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        ("qwen", "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"),
        ("deepseek", "https://api.deepseek.com"),
        ("kimi", "https://api.moonshot.ai/v1"),
        ("kimi", "https://api.kimi.com/coding/v1"),
        ("zhipu", "https://open.bigmodel.cn/api/paas/v4"),
        ("minimax", "https://api.minimax.io/v1"),
        ("minimax", "https://api.minimaxi.com/v1"),
        ("xiaomi", "https://api.xiaomimimo.com/v1"),
        ("xiaomi", "https://token-plan-sgp.xiaomimimo.com/v1"),
        ("stepfun", "https://api.stepfun.com/v1"),
        ("openai", "https://api.openai.com/v1"),
        ("anthropic", "https://api.anthropic.com/v1"),
    ]

    for provider, base_url in official_cases:
        result = classify_api_endpoint({"api_provider": provider, "base_url": base_url})
        assert result["channel_type"] == "official", (provider, base_url, result)
        assert result["docs_url"]


def test_endpoint_classification_rejects_provider_mismatch_and_suffix_spoofing():
    mismatch = classify_api_endpoint({
        "api_provider": "qwen",
        "base_url": "https://api.deepseek.com/v1",
    })
    spoofed = classify_api_endpoint({
        "api_provider": "qwen",
        "base_url": "https://maas.aliyuncs.com.evil.example/v1",
    })

    assert mismatch["channel_type"] == "relay"
    assert spoofed["channel_type"] == "relay"


def test_endpoint_classification_identifies_official_plan_channels():
    token_plan = classify_api_endpoint({
        "api_provider": "qwen",
        "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    })
    glm_coding = classify_api_endpoint({
        "api_provider": "zhipu",
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
    })
    step_plan = classify_api_endpoint({
        "api_provider": "stepfun",
        "base_url": "https://api.stepfun.com/step_plan/v1",
    })

    assert token_plan["service_name"] == "阿里云百炼 Token Plan"
    assert glm_coding["service_name"] == "智谱 GLM Coding Plan"
    assert step_plan["service_name"] == "阶跃星辰 Step Plan"


def test_kimi_code_base_url_is_normalized_for_openai_compatible_requests():
    config = {
        "api_provider": "kimi",
        "base_url": "https://api.kimi.com/coding/",
        "model": "kimi-for-coding",
    }

    assert normalize_api_base_url(config) == "https://api.kimi.com/coding/v1"
    url, _headers, body, protocol = build_request(
        config,
        "secret",
        MESSAGES,
        max_tokens=100,
        temperature=0,
    )

    assert protocol == "openai_compatible"
    assert url == "https://api.kimi.com/coding/v1/chat/completions"
    assert body["temperature"] == 1
    assert body["max_completion_tokens"] == 100
    assert "max_tokens" not in body


def test_legacy_qwen_token_plan_base_url_is_normalized():
    assert normalize_api_base_url({
        "api_provider": "qwen",
        "base_url": "https://token-plan.cn-beijing.maas.aliyuncs.com/v1/",
    }) == "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


def test_qwen_token_plan_public_catalog_does_not_authenticate_standard_key():
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        if "token-plan.cn-beijing" in url:
            return FakeResponse(200, {"data": [{"id": "kimi-k2.6"}]})
        return FakeResponse(401, {"error": {"message": "invalid"}})

    result = discover_api_endpoint(
        "qwen",
        "sk-standard-key",
        preferred_base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/v1",
        request_get=fake_get,
    )

    assert result.status == "invalid"
    assert result.http_status == 401
    assert "不是其专属 sk-sp- Key" in result.message
    assert calls[0] == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/models"
    )
    assert any("dashscope.aliyuncs.com/compatible-mode/v1/models" in url for url in calls)


def test_qwen_token_plan_key_prefix_identifies_public_catalog_without_claiming_auth():
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return FakeResponse(200, {"data": [{"id": "kimi-k2.6"}]})

    result = discover_api_endpoint(
        "qwen",
        "sk-sp-secret",
        preferred_base_url=(
            "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        ),
        request_get=fake_get,
    )

    assert result.status == "catalog"
    assert result.channel == "qwen_token_plan"
    assert result.models == ("kimi-k2.6",)
    assert "不能据此证明 API Key 有效" in result.message
    assert calls == [
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/models"
    ]


def test_build_openai_compatible_request():
    url, headers, body, protocol = build_request(
        {"base_url": "https://api.example.com/v1", "model": "model-a"},
        "secret",
        MESSAGES,
        max_tokens=100,
        temperature=0,
        tool=TOOL,
        force_tool=True,
    )
    assert protocol == "openai_compatible"
    assert url == "https://api.example.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret"
    assert body["tool_choice"]["function"]["name"] == "submit"


def test_build_xiaomi_vision_request_disables_thinking():
    _url, _headers, body, protocol = build_request(
        {
            "api_provider": "xiaomi",
            "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "model": "mimo-v2.5",
            "_disable_thinking": True,
        },
        "secret",
        [{"role": "user", "content": "test"}],
        max_tokens=500,
        temperature=0,
    )

    assert protocol == "openai_compatible"
    assert "max_tokens" not in body
    assert body["max_completion_tokens"] == 500
    assert body["thinking"] == {"type": "disabled"}


def test_build_anthropic_request_converts_system_and_tool():
    url, headers, body, protocol = build_request(
        {"api_provider": "anthropic", "base_url": "https://api.anthropic.com/v1", "model": "claude-test"},
        "secret",
        MESSAGES,
        max_tokens=100,
        temperature=0,
        tool=TOOL,
        force_tool=True,
    )
    assert protocol == "anthropic"
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "secret"
    assert body["system"] == "system rule"
    assert body["messages"] == [{"role": "user", "content": "evaluate"}]
    assert body["tools"][0]["input_schema"]["type"] == "object"
    assert body["tool_choice"] == {"type": "tool", "name": "submit"}


def test_build_azure_legacy_request():
    url, headers, body, protocol = build_request(
        {
            "api_provider": "azure",
            "base_url": "https://resource.openai.azure.com",
            "model": "deployment-a",
        },
        "secret",
        MESSAGES,
        max_tokens=100,
        temperature=0,
    )
    assert protocol == "azure"
    assert "/openai/deployments/deployment-a/chat/completions" in url
    assert "api-version=" in url
    assert headers["api-key"] == "secret"
    assert body["model"] == "deployment-a"


def test_normalize_anthropic_tool_response():
    message, finish_reason = normalize_response("anthropic", {
        "content": [
            {"type": "text", "text": "done"},
            {"type": "tool_use", "name": "submit", "input": {"value": 1}},
        ],
        "stop_reason": "tool_use",
    })
    assert message["content"] == "done"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"value": 1}
    assert finish_reason == "tool_use"


def test_normalize_reasoning_content_fallback():
    message, _ = normalize_response("openai_compatible", {
        "choices": [{"message": {"content": "", "reasoning_content": '{"value": 1}'}}],
    })
    assert message["content"] == '{"value": 1}'
