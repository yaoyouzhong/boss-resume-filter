"""Certificate truncation recovery and PDF manual-direction integration."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests

import education_certificate as certificate
from education_controller import EducationController
from tests.pdf_samples import write_pdf


CONFIG = {"api_provider": "deepseek", "base_url": "https://example.invalid", "model": "test"}


def _payload(rotation=270, rotation_confidence=99):
    return {
        "certificate_type": "education", "name": "测试", "certificate_number": "12345678901234567",
        "school": "测试大学", "major": "测试专业", "confidence": 99,
        "rotation": rotation, "rotation_confidence": rotation_confidence,
        "field_confidence": dict.fromkeys(("name", "certificate_number", "school", "major"), 99),
    }


def test_truncated_parseable_answer_is_retried_instead_of_accepted():
    import json
    from types import SimpleNamespace

    replies = [
        {"choices": [{"finish_reason": "length", "message": {"content": '{"rotation":90}'}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(_payload())}}]},
    ]
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        reply = replies.pop(0)
        return SimpleNamespace(status_code=200, json=lambda: reply)

    with patch.object(certificate.requests, "post", side_effect=post), patch(
        "vision_capability.observe_image_recognition",
    ):
        result = certificate._invoke_certificate_model(
            CONFIG, "test-key", [{"role": "user", "content": "test"}], max_tokens=4096,
        )
    assert result["rotation"] == 270
    assert [body["max_tokens"] for body in calls] == [4096, 8192]
    assert calls[0]["messages"] == calls[1]["messages"]


def test_output_limit_retry_is_bounded_and_timeout_is_not_retried():
    for error, expected_calls in (
        (certificate.ModelOutputLimitError("limit"), 2),
        (requests.Timeout("private service detail"), 1),
    ):
        with patch.object(certificate, "_invoke_model", side_effect=error) as invoke:
            try:
                certificate._invoke_certificate_model(CONFIG, "test-key", [], max_tokens=4096)
            except type(error):
                pass
            else:
                raise AssertionError("failed requests must remain failures")
        assert invoke.call_count == expected_calls


def test_direction_fallback_recovers_after_output_limit():
    replies = [_payload(0, 20), certificate.ModelOutputLimitError("limit"), _payload()]
    with patch.object(certificate, "prepare_orientation_sheet_data_url", return_value="sheet"), patch.object(
        certificate, "prepare_image_data_url", return_value="image",
    ), patch.object(certificate, "_invoke_model", side_effect=replies) as invoke:
        result = certificate.recognize_certificate_image("test.png", CONFIG, "test-key")
    assert result.rotation == 270
    assert result.rotation_confidence == 99
    assert not result.warnings
    assert [call.kwargs["max_tokens"] for call in invoke.call_args_list] == [4096, 4096, 8192]


def test_failed_direction_reports_safe_cause_and_keeps_field_result():
    replies = [requests.Timeout("private service detail"),
               certificate.ModelOutputLimitError("private service detail"),
               certificate.ModelOutputLimitError("private service detail"), _payload()]
    with patch.object(certificate, "prepare_orientation_sheet_data_url", return_value="sheet"), patch.object(
        certificate, "prepare_image_data_url", return_value="image",
    ), patch.object(certificate, "_invoke_model", side_effect=replies):
        result = certificate.recognize_certificate_image("test.png", CONFIG, "test-key")
    warnings = "\n".join(result.warnings)
    assert "模型请求超时" in warnings and "模型输出达到上限" in warnings
    assert "private" not in warnings and "test-key" not in warnings
    assert result.rotation == 0 and result.name == "测试"


def test_pdf_manual_rotation_reaches_image_read_and_updates_queue():
    with TemporaryDirectory() as directory:
        source = Path(directory) / "scan.pdf"
        write_pdf(source, [b"0 0 1 rg 0 0 200 100 re f"])
        original = source.read_bytes()
        items = {"pdf": {"path": source, "is_pdf": True, "recognition_rotation": 270}}
        stages = []
        with patch.object(certificate, "_invoke_model", return_value=_payload()) as invoke, patch.object(
            certificate, "prepare_orientation_sheet_data_url",
        ) as direction, patch.object(
            certificate, "prepare_image_data_url", wraps=certificate.prepare_image_data_url,
        ) as prepare:
            results = EducationController.recognize_documents(
                items, ["pdf"], CONFIG, "test-key",
                recognize_image=certificate.recognize_certificate_image,
                recognize_pdf=certificate.recognize_certificate_pdf,
                on_stage=lambda *event: stages.append(event),
            )
        EducationController.apply_recognition_results(items, results)
        assert items["pdf"]["status"] == "已识别"
        assert items["pdf"]["auto_rotation"] == 270
        assert prepare.call_args.kwargs["rotation"] == 270
        direction.assert_not_called()
        assert invoke.call_count == 1
        assert stages and stages[-1][2] == 95
        assert source.read_bytes() == original
