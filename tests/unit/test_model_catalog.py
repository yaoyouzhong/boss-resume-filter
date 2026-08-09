from ai_adapter import EndpointResolution, model_catalog_cache_key
from model_catalog import analyze_model_catalog, fetch_model_catalog


class _Response:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_fetch_model_catalog_uses_discovered_provider_endpoint():
    def discover(provider, api_key, preferred_base_url=""):
        assert (provider, api_key, preferred_base_url) == (
            "kimi",
            "sk-kimi-test",
            "https://api.moonshot.cn/v1",
        )
        return EndpointResolution(
            "confirmed",
            provider,
            "kimi_code",
            "Kimi Code",
            "https://api.kimi.com/coding/v1",
            ("kimi-for-coding", "kimi-for-coding-highspeed"),
            200,
        )

    result = fetch_model_catalog(
        "kimi",
        "sk-kimi-test",
        "https://api.moonshot.cn/v1",
        discover_endpoint=discover,
    )

    assert result.http_status == 200
    assert result.base_url == "https://api.kimi.com/coding/v1"
    assert result.service_name == "Kimi Code"
    assert result.resolution_status == "confirmed"
    assert result.endpoint_confirmed is True
    assert result.payload == {
        "data": [
            {"id": "kimi-for-coding"},
            {"id": "kimi-for-coding-highspeed"},
        ]
    }


def test_fetch_model_catalog_preserves_discovery_failure_details():
    def discover(provider, api_key, preferred_base_url=""):
        return EndpointResolution(
            "unavailable",
            provider,
            "kimi_code",
            "Kimi Code",
            "https://api.kimi.com/coding/v1",
            (),
            503,
            "temporary outage",
        )

    result = fetch_model_catalog(
        "kimi",
        "sk-kimi-test",
        "https://api.moonshot.cn/v1",
        discover_endpoint=discover,
    )

    assert result.http_status == 503
    assert result.response_text == "temporary outage"
    assert result.payload == {}
    assert result.base_url == "https://api.moonshot.cn/v1"
    assert result.resolution_status == "unavailable"
    assert result.endpoint_confirmed is False


def test_fetch_model_catalog_only_requests_explicit_custom_endpoint():
    calls = []

    def request_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response(200, {"data": [{"id": "custom-chat"}]}, "ok")

    result = fetch_model_catalog(
        "custom",
        "secret",
        "https://relay.example/v1/",
        request_get=request_get,
        verify_path="ca.pem",
        user_agent="test-agent",
    )

    assert result.http_status == 200
    assert result.payload == {"data": [{"id": "custom-chat"}]}
    assert calls == [(
        "https://relay.example/v1/models",
        {
            "headers": {
                "Authorization": "Bearer secret",
                "User-Agent": "test-agent",
            },
            "timeout": 15,
            "verify": "ca.pem",
        },
    )]


def test_analyze_model_catalog_filters_and_deduplicates_chat_models():
    result = analyze_model_catalog(
        {
            "data": [
                {"id": "chat-b"},
                {"id": "text-embedding-3-small"},
                {"id": "chat-a"},
                {"id": "chat-a"},
                "rerank-v2",
            ]
        },
        fetched_models={},
        provider="openai",
        base_url="https://api.openai.com/v1",
    )

    assert result is not None
    assert result.models == ("chat-a", "chat-b")
    assert result.filtered_count == 3
    assert result.new_models == frozenset({"chat-a", "chat-b"})
    assert result.removed_models == frozenset()


def test_analyze_model_catalog_compares_only_matching_endpoint_history():
    code_url = "https://api.kimi.com/coding/v1"
    platform_url = "https://api.moonshot.cn/v1"
    fetched = {
        model_catalog_cache_key("kimi", code_url): ["code-old", "shared"],
        model_catalog_cache_key("kimi", platform_url): ["platform-only"],
    }

    result = analyze_model_catalog(
        {"models": ["code-new", "shared"]},
        fetched_models=fetched,
        provider="kimi",
        base_url=code_url,
        configured_base_url=platform_url,
    )

    assert result is not None
    assert result.new_models == frozenset({"code-new"})
    assert result.removed_models == frozenset({"code-old"})
    assert "platform-only" not in result.removed_models


def test_analyze_model_catalog_uses_legacy_provider_history_only_same_endpoint():
    base_url = "https://relay.example/v1"
    result = analyze_model_catalog(
        {"data": [{"id": "current"}]},
        fetched_models={"custom": ["legacy"]},
        provider="custom",
        base_url=base_url,
        configured_base_url=base_url,
    )

    assert result is not None
    assert result.new_models == frozenset({"current"})
    assert result.removed_models == frozenset({"legacy"})
