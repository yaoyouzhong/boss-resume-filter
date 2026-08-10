from types import SimpleNamespace

from settings_controller import SettingsController


def test_sanitize_removes_every_plaintext_key_without_mutating_input():
    config = {
        "api_key": "top-secret",
        "model": "m1",
        "saved_models": [{"model": "m1", "api_key": "nested", "capability": {}}],
    }

    clean = SettingsController.sanitize_for_save(config)

    assert "api_key" not in clean
    assert "api_key" not in clean["saved_models"][0]
    assert config["saved_models"][0]["api_key"] == "nested"


def test_prepare_saved_models_sets_first_default_and_merges_batch_once():
    controller = SettingsController()
    outcome = controller.prepare_saved_models(
        {"providers": {}, "fetched_models": {}},
        [],
        provider="qwen",
        base_url="https://example.test/v1",
        model_name="",
        pending_models=("qwen-a", "qwen-b", "qwen-a"),
        api_key="secret",
        llm_read_timeout=60,
    )

    assert outcome.default_changed is True
    assert outcome.api_config["model"] == "qwen-a"
    assert outcome.api_config["api_key"] == "secret"
    assert [model["model"] for model in outcome.saved_models] == ["qwen-a", "qwen-b"]
    assert outcome.added_count == 2


def test_prepare_saved_models_preserves_existing_default_and_its_explicit_key():
    controller = SettingsController()
    current = {
        "api_provider": "deepseek",
        "base_url": "https://deepseek.test/v1",
        "model": "default-model",
        "api_key": "current-key",
    }
    saved = [{
        "api_provider": "deepseek",
        "base_url": "https://deepseek.test/v1/",
        "model": "default-model",
    }]

    outcome = controller.prepare_saved_models(
        current,
        saved,
        provider="qwen",
        base_url="https://qwen.test/v1",
        model_name="qwen-plus",
        pending_models=(),
        api_key="new-key",
        llm_read_timeout=30,
    )

    assert outcome.default_changed is False
    assert outcome.api_config["model"] == "default-model"
    assert outcome.api_config["api_key"] == "current-key"


def test_provider_selection_prefers_current_then_latest_saved_then_default():
    current = {"api_provider": "qwen", "base_url": "current", "model": "m0"}
    saved = [
        {"api_provider": "kimi", "base_url": "old", "model": "m1"},
        {"api_provider": "kimi", "base_url": "new", "model": "m2"},
    ]

    assert SettingsController.resolve_provider_selection("qwen", current, saved).base_url == "current"
    assert SettingsController.resolve_provider_selection("kimi", current, saved).model == "m2"
    assert SettingsController.resolve_provider_selection("openai", current, saved).base_url == "https://api.openai.com/v1"


def test_catalog_fetch_returns_plain_success_and_classified_failure():
    response = SimpleNamespace(
        http_status=200,
        response_text="ok",
        payload={"data": [{"id": "m1"}]},
        base_url="https://resolved.test/v1",
        service_name="Resolved",
        resolution_status="confirmed",
        endpoint_confirmed=True,
    )
    analysis = SimpleNamespace(models=("m1",), catalog_key="key")
    seen = {}

    def fetcher(provider, api_key, base_url):
        seen.update(provider=provider, api_key=api_key, base_url=base_url)
        return response

    outcome = SettingsController.fetch_catalog(
        provider="qwen",
        api_key="one-shot-secret",
        base_url="https://input.test/v1",
        fetched_models={},
        configured_base_url="",
        fetcher=fetcher,
        analyzer=lambda *_args, **_kwargs: analysis,
    )
    failure = SettingsController.fetch_catalog(
        provider="qwen",
        api_key="secret",
        base_url="url",
        fetched_models={},
        configured_base_url="",
        fetcher=lambda *_args: (_ for _ in ()).throw(ConnectionError("offline")),
        analyzer=lambda *_args, **_kwargs: analysis,
        connection_errors=(ConnectionError,),
    )

    assert outcome.status == "success"
    assert outcome.analysis is analysis
    assert seen["api_key"] == "one-shot-secret"
    assert failure.status == "connection_error"
    assert vars(SettingsController()) == {}
