import socket
from unittest.mock import Mock

from api_connectivity import probe_api_connectivity, probe_model_capability


def test_api_connectivity_runs_dns_then_returns_compatible_result():
    dns_lookup = Mock(return_value="203.0.113.10")
    probe = Mock(
        return_value={
            "status": "compatible",
            "response_time": 1.25,
            "message": "ok",
        }
    )
    clock_values = iter((0.0, 0.1, 1.3, 1.4))

    result = probe_api_connectivity(
        {
            "api_provider": "qwen",
            "base_url": "https://api.example.test/v1",
            "model": "qwen-plus",
        },
        "secret",
        dns_lookup=dns_lookup,
        probe=probe,
        clock=lambda: next(clock_values),
    )

    assert result.status == "compatible"
    assert result.successful is True
    assert result.elapsed_seconds == 1.4
    assert result.hostname == "api.example.test"
    dns_lookup.assert_called_once_with("api.example.test")
    assert probe.call_args.args[1] == "secret"
    assert probe.call_args.kwargs == {"force": True}


def test_model_capability_preserves_limited_compatibility():
    result = probe_model_capability(
        {"model": "limited-model"},
        "secret",
        probe=lambda *_args, **_kwargs: {
            "status": "limited",
            "response_time": "2.5",
        },
    )

    assert result.status == "limited"
    assert result.successful is True
    assert result.elapsed_seconds == 2.5


def test_model_capability_classifies_incompatible_response_message():
    result = probe_model_capability(
        {"model": "chat-only"},
        "secret",
        probe=lambda *_args, **_kwargs: {
            "status": "incompatible",
            "message": "缺少结构化输出",
        },
    )

    assert result.status == "incompatible"
    assert result.successful is False
    assert result.message == "缺少结构化输出"


def test_model_capability_classifies_probe_exception_without_raising():
    def fail_probe(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    result = probe_model_capability(
        {"model": "broken"},
        "secret",
        probe=fail_probe,
    )

    assert result.status == "error"
    assert result.successful is False
    assert result.message == "provider unavailable"


def test_api_connectivity_stops_before_probe_when_dns_fails():
    probe = Mock()

    def fail_dns(_hostname):
        raise socket.gaierror("not found")

    result = probe_api_connectivity(
        {
            "base_url": "https://missing.example.test/v1",
            "model": "model",
        },
        "secret",
        dns_lookup=fail_dns,
        probe=probe,
    )

    assert result.status == "dns_error"
    assert result.hostname == "missing.example.test"
    probe.assert_not_called()


def test_api_connectivity_classifies_base_url_without_hostname():
    dns_lookup = Mock()

    result = probe_api_connectivity(
        {"base_url": "not-a-url", "model": "model"},
        "secret",
        dns_lookup=dns_lookup,
        probe=Mock(),
    )

    assert result.status == "dns_error"
    assert result.hostname == ""
    dns_lookup.assert_not_called()
