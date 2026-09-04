"""毕业证书图片识别模块测试。"""
import base64
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from education_certificate import (
    CHSI_QUERY_URL,
    CHSI_SCREENSHOT_WIDTH,
    ChsiResultNotReadyError,
    build_name_disambiguation_messages,
    build_initial_recognition_messages,
    build_orientation_messages,
    build_chsi_screenshot_filename,
    build_pdf_text_messages,
    build_vision_messages,
    capture_captcha_image,
    capture_captcha_variants,
    capture_chsi_result_png,
    classify_chsi_terminal_result,
    extract_pdf_text,
    fill_chsi_query_page,
    is_pdf_path,
    is_chsi_result_text,
    is_chsi_qr_confirmation_text,
    is_valid_chsi_screenshot,
    likely_supports_vision,
    normalize_recognition,
    normalize_chsi_screenshot_png,
    prepare_image_data_url,
    prepare_captcha_image_variants,
    prepare_detail_sheet_data_url,
    prepare_orientation_sheet_data_url,
    read_chsi_result_page_text,
    recognize_certificate_pdf,
    recognize_certificate_image,
    resolve_vision_api_config,
    save_chsi_result_screenshot,
    validate_chsi_fields,
    validate_document_path,
)


def test_name_disambiguation_prompt_includes_both_conflicting_candidates():
    messages = build_name_disambiguation_messages(
        {
            "api_provider": "openai",
            "base_url": "https://example.test/v1",
            "model": "MiniMax-M3",
        },
        ("tile-1", "tile-2"),
        ("鲍珠", "鲍殊"),
    )

    message_text = str(messages)
    assert "鲍珠" in message_text
    assert "鲍殊" in message_text
    assert "不同汉字的实际结构" in message_text


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_prepare_image_data_url_resizes_and_encodes_jpeg():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "certificate.png"
        Image.new("RGB", (2400, 1200), "white").save(path)
        data_url = prepare_image_data_url(path)

    assert data_url.startswith("data:image/jpeg;base64,")
    assert len(base64.b64decode(data_url.split(",", 1)[1])) > 100


def test_build_vision_messages_supports_openai_and_anthropic():
    data_url = "data:image/jpeg;base64,YWJj"
    orientation_url = "data:image/jpeg;base64,ZGVm"
    openai_messages = build_vision_messages(
        {"api_provider": "openai", "base_url": "https://api.openai.com/v1"},
        data_url,
    )
    anthropic_messages = build_vision_messages(
        {"api_provider": "anthropic", "base_url": "https://api.anthropic.com/v1"},
        data_url,
    )
    orientation_messages = build_orientation_messages(
        {"api_provider": "openai", "base_url": "https://api.openai.com/v1"},
        orientation_url,
    )
    initial_messages = build_initial_recognition_messages(
        {"api_provider": "openai", "base_url": "https://api.openai.com/v1"},
        orientation_url,
        data_url,
    )

    assert openai_messages[1]["content"][1]["type"] == "image_url"
    assert anthropic_messages[1]["content"][0]["source"]["data"] == "YWJj"
    assert '"field_confidence"' in openai_messages[0]["content"]
    assert '"rotation":0' in orientation_messages[0]["content"]
    assert orientation_messages[1]["content"][1]["image_url"]["url"] == orientation_url
    assert '"certificate_number"' in initial_messages[0]["content"]
    assert [
        block["image_url"]["url"]
        for block in initial_messages[1]["content"]
        if block["type"] == "image_url"
    ] == [orientation_url, data_url]


def test_xiaomi_pro_uses_omnimodal_model_for_image_recognition_only():
    original = {
        "api_provider": "xiaomi",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
    }

    resolved = resolve_vision_api_config(original)

    assert resolved["model"] == "mimo-v2.5"
    assert original["model"] == "mimo-v2.5-pro"


def test_non_xiaomi_model_is_not_rewritten():
    original = {
        "api_provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
    }

    assert resolve_vision_api_config(original) == original


def test_k3_is_recognized_as_vision_capable():
    assert likely_supports_vision({
        "api_provider": "kimi",
        "base_url": "https://api.kimi.com/coding/v1",
        "model": "k3",
    }) is True


def test_minimax_m3_is_recognized_as_vision_capable():
    """MiniMax M3 支持图片输入，不应触发学历核验的能力警告。"""
    assert likely_supports_vision({
        "api_provider": "minimax",
        "base_url": "https://api.minimaxi.com/v1",
        "model": "MiniMax-M3",
    }) is True


def test_kimi_code_image_recognition_uses_larger_output_budget():
    from unittest.mock import patch

    captured = {}
    stages = []

    def fake_invoke(config, api_key, messages, *, timeout=60, max_tokens=500):
        captured["max_tokens"] = max_tokens
        return {
            "rotation": 0,
            "rotation_confidence": 95,
            "name": "张三",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "confidence": 90,
            "warnings": [],
        }

    with patch("education_certificate.prepare_image_data_url", return_value="data:image/jpeg;base64,YQ=="), \
            patch("education_certificate.prepare_orientation_sheet_data_url", return_value="data:image/jpeg;base64,Yg=="), \
            patch("education_certificate.prepare_detail_sheet_data_url", return_value="data:image/jpeg;base64,Yw=="), \
            patch("education_certificate._invoke_model", side_effect=fake_invoke) as invoke:
        result = recognize_certificate_image(
            "fake.jpg",
            {
                "api_provider": "kimi",
                "base_url": "https://api.kimi.com/coding/v1",
                "model": "k3",
            },
            "key",
            on_progress=lambda stage, percent: stages.append((stage, percent)),
        )

    assert captured["max_tokens"] == 4096
    assert result.name == "张三"
    assert result.certificate_number == "123456789012345678"
    assert result.critical_conflicts == ()
    assert invoke.call_count == 1
    assert stages[0] == ("正在准备证书方向和高清图", 5)
    assert ("正在核对姓名和证书编号", 70) not in stages
    assert stages[-1] == ("正在整理识别结果", 95)


def test_kimi_code_captcha_recognition_uses_larger_output_budget():
    from unittest.mock import patch
    from education_certificate import recognize_captcha

    captured = {}

    def fake_invoke(config, api_key, messages, *, timeout=60, max_tokens=500):
        captured["max_tokens"] = max_tokens
        return {"type": "letter", "answer": "aB3x", "confidence": 90}

    with patch("education_certificate._invoke_model", side_effect=fake_invoke):
        result = recognize_captcha(
            "data:image/jpeg;base64,YQ==",
            {
                "api_provider": "kimi",
                "base_url": "https://api.kimi.com/coding/v1",
                "model": "k3",
            },
            "key",
        )

    assert captured["max_tokens"] == 4096
    assert result[:3] == ("letter", "aB3x", 90)
    assert "4 位字符" in result[3]


def test_captcha_image_preprocessing_upscales_and_uses_lossless_png():
    from education_certificate import _image_bytes_to_data_url

    source = io.BytesIO()
    Image.new("RGB", (100, 40), "white").save(source, format="PNG")

    data_url = _image_bytes_to_data_url(source.getvalue())
    payload = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(payload)) as processed:
        assert data_url.startswith("data:image/png;base64,")
        assert processed.width >= 400
        assert processed.height >= 160


def test_captcha_variants_are_same_size_lossless_and_keep_expected_length():
    source = io.BytesIO()
    image = Image.new("RGB", (120, 40), "white")
    ImageDraw.Draw(image).text((12, 10), "A8b5", fill="black")
    image.save(source, format="PNG")

    variants = prepare_captcha_image_variants(
        source.getvalue(),
        expected_length=4,
        maximum_length=5,
    )

    decoded = []
    for data_url in (variants.original, variants.grayscale, variants.binary):
        assert data_url.startswith("data:image/png;base64,")
        payload = base64.b64decode(data_url.split(",", 1)[1])
        with Image.open(io.BytesIO(payload)) as processed:
            decoded.append((processed.size, processed.convert("RGB").tobytes()))
    assert variants.expected_length == 4
    assert variants.maximum_length == 5
    assert decoded[0][0] == decoded[1][0] == decoded[2][0]
    assert len({content for _size, content in decoded}) == 3


def test_captcha_parser_validates_length_and_computes_arithmetic_locally():
    from education_certificate import (
        CAPTCHA_AUTO_SUBMIT_MIN_CONFIDENCE,
        parse_captcha_result,
    )

    assert CAPTCHA_AUTO_SUBMIT_MIN_CONFIDENCE == 80

    assert parse_captcha_result(
        {"type": "letter", "answer": "aB3x", "confidence": 92},
        expected_length=4,
    ) == ("letter", "aB3x", 92)
    assert parse_captcha_result(
        {"type": "letter", "answer": "aB3x!", "confidence": 99},
        expected_length=5,
    )[0] == "unknown"
    assert parse_captcha_result(
        {"type": "letter", "answer": "aB3x", "confidence": 91},
        maximum_length=5,
    ) == ("letter", "aB3x", 91)
    assert parse_captcha_result(
        {"type": "letter", "answer": "aB3xZ9", "confidence": 91},
        maximum_length=5,
    ) == ("letter", "aB3xZ9", 91)
    assert parse_captcha_result(
        {"type": "letter", "answer": "aB3x", "confidence": 91},
        expected_length=5,
    ) == ("letter", "aB3x", 91)
    assert parse_captcha_result(
        {"type": "alphanumeric", "code": "Ａ8ｂ5", "confidence": 93},
    ) == ("letter", "A8b5", 93)
    assert parse_captcha_result(
        {"type": "letter", "answer": "A8b", "confidence": 99},
    ) == ("letter", "A8b", 99)
    assert parse_captcha_result(
        {"type": "letter", "answer": "A8", "confidence": 99},
    )[0] == "unknown"
    assert parse_captcha_result(
        {"type": "letter", "answer": "A8b5Z91", "confidence": 99},
    )[0] == "unknown"
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "12÷4=?",
            "answer": "99",
            "confidence": 95,
        }
    )[0] == "unknown"
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "12÷4=?",
            "answer": "3",
            "confidence": 95,
        }
    ) == ("arithmetic", "3", 95)
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "2+3★4=?",
            "answer": "14",
            "confidence": 96,
        }
    ) == ("arithmetic", "14", 96)
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "3★4+5★2=?",
            "answer": "22",
            "confidence": 96,
        }
    ) == ("arithmetic", "22", 96)
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "8÷2★3=12",
            "answer": 12,
            "confidence": 94,
        }
    ) == ("arithmetic", "12", 94)
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "8-2★3",
            "answer": "2",
            "confidence": 93,
        }
    ) == ("arithmetic", "2", 93)
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "8-8",
            "answer": 0,
            "confidence": 92,
        }
    ) == ("arithmetic", "0", 92)
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "0★8+3",
            "answer": "3",
            "confidence": 91,
        }
    ) == ("arithmetic", "3", 91)
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "2+0★5",
            "answer": "2",
            "confidence": 90,
        }
    ) == ("arithmetic", "2", 90)
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "3＋5＝8",
            "answer": "8",
            "confidence": 89,
        }
    ) == ("arithmetic", "8", 89)
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "answer": 0,
            "confidence": 88,
        }
    ) == ("arithmetic", "0", 88)
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "8÷0",
            "answer": "0",
            "confidence": 99,
        }
    )[0] == "unknown"
    assert parse_captcha_result(
        {
            "type": "arithmetic",
            "expression": "3+5",
            "answer": "9",
            "confidence": 99,
        }
    )[0] == "unknown"


def test_captcha_arithmetic_uses_standard_precedence_and_preserves_zero():
    from education_certificate import _evaluate_captcha_expression

    cases = {
        "3+5": "8",
        "7★8": "56",
        "2+3★4": "14",
        "8÷2+3": "7",
        "8-2★3": "2",
        "8÷2★3": "12",
        "8-3+2": "7",
        "3★4+5★2": "22",
        "0★8": "0",
        "8★0": "0",
        "2+0★5": "2",
        "0★8+3": "3",
        "8-8": "0",
    }

    assert {
        expression: _evaluate_captcha_expression(expression)
        for expression in cases
    } == cases
    for invalid_expression in (
        "8÷0",
        "★8",
        "8★",
        "3++5",
        "3+★5",
        "+3",
        "3+",
    ):
        assert _evaluate_captcha_expression(invalid_expression) == ""


def test_captcha_high_confidence_primary_submits_without_extra_model_call():
    from unittest.mock import patch
    from education_certificate import CaptchaImageVariants, recognize_captcha

    images = CaptchaImageVariants("raw", "gray", "binary", expected_length=4)
    config = {
        "api_provider": "openai",
        "base_url": "https://example.test/v1",
        "model": "MiniMax-M3",
    }
    captured = {}

    def fake_invoke(*_args, max_tokens=2048, **_kwargs):
        captured["max_tokens"] = max_tokens
        return {"type": "letter", "answer": "a8cD", "confidence": 85}

    with patch(
        "education_certificate._invoke_model",
        side_effect=fake_invoke,
    ) as invoke:
        result = recognize_captcha(images, config, "key")
    assert result[:3] == ("letter", "a8cD", 85)
    assert "置信度 85" in result[3]
    assert result[4] is False
    assert invoke.call_count == 1
    assert captured["max_tokens"] == 512


def test_captcha_high_confidence_arithmetic_submits_without_binary_review():
    from unittest.mock import patch
    from education_certificate import CaptchaImageVariants, recognize_captcha

    images = CaptchaImageVariants("raw", "gray", "binary")
    config = {
        "api_provider": "openai",
        "base_url": "https://example.test/v1",
        "model": "MiniMax-M3",
    }
    response = {
        "type": "arithmetic",
        "expression": "2+3★4",
        "answer": "14",
        "confidence": 95,
    }
    with patch("education_certificate._invoke_model", return_value=response) as invoke:
        result = recognize_captcha(images, config, "key")

    assert result[:3] == ("arithmetic", "14", 95)
    assert "算术结果" in result[3]
    assert result[4] is False
    assert invoke.call_count == 1


def test_captcha_arithmetic_conflict_is_not_resolved_by_self_reported_confidence():
    from unittest.mock import patch
    from education_certificate import CaptchaImageVariants, recognize_captcha

    images = CaptchaImageVariants("raw", "gray", "binary")
    config = {
        "api_provider": "openai",
        "base_url": "https://example.test/v1",
        "model": "MiniMax-M3",
    }
    responses = [
        {
            "type": "arithmetic",
            "expression": "7★8",
            "answer": "56",
            "confidence": 72,
        },
        {
            "type": "arithmetic",
            "expression": "7★3",
            "answer": "21",
            "confidence": 87,
        },
    ]
    with patch("education_certificate._invoke_model", side_effect=responses) as invoke:
        result = recognize_captcha(images, config, "key")

    assert result[:3] == ("unknown", "", 87)
    assert "两路识别结果不一致" in result[3]
    assert result[4] is False
    assert invoke.call_count == 2


def test_captcha_review_can_rescue_invalid_primary_result():
    from unittest.mock import patch
    from education_certificate import CaptchaImageVariants, recognize_captcha

    images = CaptchaImageVariants(
        "raw", "gray", "binary", maximum_length=5,
    )
    config = {
        "api_provider": "openai",
        "base_url": "https://example.test/v1",
        "model": "MiniMax-M3",
    }
    responses = [
        {"type": "letter", "answer": "a8", "confidence": 20},
        {"type": "letter", "answer": "a8cD", "confidence": 88},
    ]
    with patch("education_certificate._invoke_model", side_effect=responses) as invoke:
        result = recognize_captcha(images, config, "key")
    assert result[:3] == ("letter", "a8cD", 88)
    assert "采用补充识别" in result[3]
    assert result[4] is False
    assert invoke.call_count == 2


def test_captcha_low_confidence_conflict_is_rejected_instead_of_trusting_confidence():
    from unittest.mock import patch
    from education_certificate import CaptchaImageVariants, recognize_captcha

    images = CaptchaImageVariants("raw", "gray", "binary", expected_length=4)
    config = {
        "api_provider": "openai",
        "base_url": "https://example.test/v1",
        "model": "MiniMax-M3",
    }
    responses = [
        {"type": "letter", "answer": "a8cD", "confidence": 62},
        {"type": "letter", "answer": "aBcD", "confidence": 68},
    ]
    with patch("education_certificate._invoke_model", side_effect=responses) as invoke:
        result = recognize_captcha(images, config, "key")
    assert result[:3] == ("unknown", "", 68)
    assert "两路识别结果不一致" in result[3]
    assert result[4] is False
    assert invoke.call_count == 2


def test_captcha_two_low_confidence_reads_can_agree_without_third_call():
    from unittest.mock import patch
    from education_certificate import CaptchaImageVariants, recognize_captcha

    images = CaptchaImageVariants("raw", "gray", "binary", expected_length=4)
    responses = [
        {"type": "letter", "answer": "a8cD", "confidence": 62},
        {"type": "letter", "answer": "a8cD", "confidence": 66},
    ]
    with patch("education_certificate._invoke_model", side_effect=responses) as invoke:
        result = recognize_captcha(
            images,
            {
                "api_provider": "openai",
                "base_url": "https://example.test/v1",
                "model": "MiniMax-M3",
            },
            "key",
        )

    assert result[:3] == ("letter", "a8cD", 66)
    assert "结果一致" in result[3]
    assert result[4] is True
    assert invoke.call_count == 2


def test_capture_captcha_variants_reads_input_max_length():
    raw = _png_bytes(Image.new("RGB", (100, 40), "white"))

    class Page:
        def run_js(self, _script):
            return {
                "src": "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
                "left": 0,
                "top": 0,
                "width": 100,
                "height": 40,
                "maxLength": 5,
            }

    variants = capture_captcha_variants(Page())
    assert variants.expected_length == 0
    assert variants.maximum_length == 5


def test_capture_captcha_variants_uses_exact_length_only_when_min_equals_max():
    raw = _png_bytes(Image.new("RGB", (100, 40), "white"))

    class Page:
        def run_js(self, _script):
            return {
                "src": "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
                "left": 0,
                "top": 0,
                "width": 100,
                "height": 40,
                "minLength": 5,
                "maxLength": 5,
            }

    variants = capture_captcha_variants(Page())
    assert variants.expected_length == 5
    assert variants.maximum_length == 5


def test_query_result_timeout_is_unknown_instead_of_success():
    from unittest.mock import patch
    from education_certificate import check_query_result

    with patch("time.sleep"):
        assert check_query_result(object(), timeout=0) == (
            None,
            "未检测到明确结果",
        )


def test_query_result_checks_explicit_captcha_error_without_initial_sleep():
    import json
    from unittest.mock import patch
    from education_certificate import check_query_result

    class Page:
        def run_js(self, _script):
            return json.dumps({"success": False, "message": "验证码错误"})

    with patch("time.sleep") as sleep:
        assert check_query_result(Page()) == (False, "验证码错误")

    sleep.assert_not_called()


def test_chsi_page_state_classifies_visible_page_facts_precisely():
    from education_certificate import classify_chsi_page_state

    assert classify_chsi_page_state({
        "url": "https://www.chsi.com.cn/xlcx/lscx/queryinfo.do",
        "text": "图片验证码输入有误。",
    }) == "captcha_error"
    assert classify_chsi_page_state({
        "url": "https://www.chsi.com.cn/xlcx/lscx/complex/qrcode.do",
        "text": "扫码验证 二维码已过期 点击刷新",
    }) == "qr_expired"
    assert classify_chsi_page_state({
        "url": "https://www.chsi.com.cn/xlcx/lscx/complex/qrcode.do",
        "text": "扫码验证 通过微信扫一扫验证",
    }) == "qr_waiting"
    assert classify_chsi_page_state({
        "url": "https://www.chsi.com.cn/xlcx/lscx/query.do",
        "text": "证书编号 姓名 图片验证码 免费查询",
        "certificate_number": "",
        "name": "",
        "captcha": "",
    }) == "query_empty"
    assert classify_chsi_page_state({
        "url": "https://www.chsi.com.cn/xlcx/lscx/query.do",
        "text": "证书编号 姓名 图片验证码 免费查询",
        "certificate_number": "123456",
        "name": "张三",
        "captcha": "AB3",
    }) == "query_filled"


def test_refresh_chsi_qr_code_clicks_only_the_refresh_action():
    from education_certificate import refresh_chsi_qr_code

    class Page:
        script = ""

        def run_js(self, script):
            self.script = script
            return True

    page = Page()
    assert refresh_chsi_qr_code(page) is True
    assert "text !== '点击刷新'" in page.script


def test_captcha_element_screenshot_uses_an_isolated_temporary_directory():
    captured_paths = []

    class ImageElement:
        def get_screenshot(self, *, path):
            captured_path = Path(path)
            captured_paths.append(captured_path)
            Image.new("RGB", (100, 40), "white").save(captured_path)

    image_element = ImageElement()

    class Parent:
        def ele(self, *_args, **_kwargs):
            return image_element

    class InputElement:
        def parent(self):
            return Parent()

    class Page:
        def run_js(self, _script):
            return {"src": "", "left": 0, "top": 0, "width": 100, "height": 40}

        def ele(self, *_args, **_kwargs):
            return InputElement()

    data_url = capture_captcha_image(Page())

    assert data_url.startswith("data:image/png;base64,")
    assert len(captured_paths) == 1
    assert not captured_paths[0].exists()
    assert not captured_paths[0].parent.exists()


def test_invoke_model_reports_reasoning_only_length_exhaustion():
    from unittest.mock import patch
    from education_certificate import _invoke_model

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "choices": [{
                    "finish_reason": "length",
                    "message": {
                        "content": "",
                        "reasoning_content": "still reasoning without final json",
                    },
                }],
            }

    with patch("education_certificate.requests.post", return_value=FakeResponse()):
        try:
            _invoke_model(
                {
                    "api_provider": "kimi",
                    "base_url": "https://api.kimi.com/coding/v1",
                    "model": "k3",
                },
                "key",
                [{"role": "user", "content": "test"}],
                max_tokens=500,
            )
        except RuntimeError as exc:
            assert "输出长度达到上限" in str(exc)
        else:
            raise AssertionError("reasoning-only length response should fail clearly")


def test_normalize_recognition_cleans_fields_and_warns_non_18_digit_number():
    result = normalize_recognition({
        "name": " 张 三 ",
        "certificate_number": "1234-5678 90",
        "school": " 某某 大学 ",
        "major": " 计算机 科学与技术 ",
        "rotation": 90,
        "rotation_confidence": 95,
        "confidence": 120,
        "warnings": [],
    })

    assert result.name == "张三"
    assert result.certificate_number == "1234567890"
    assert result.school == "某某大学"
    assert result.major == "计算机科学与技术"
    assert result.rotation == 90
    assert result.rotation_confidence == 95
    assert result.confidence == 100
    assert "10 位" in result.warnings[0]


def test_normalize_recognition_rejects_uncertain_rotation_value():
    result = normalize_recognition({
        "name": "张三",
        "certificate_number": "123456789012345678",
        "rotation": 45,
        "rotation_confidence": 99,
    })

    assert result.rotation == 0


def test_low_rotation_confidence_keeps_original_orientation():
    result = normalize_recognition({
        "name": "张三",
        "certificate_number": "123456789012345678",
        "rotation": 180,
        "rotation_confidence": 79,
    })

    assert result.rotation == 0


def test_orientation_sheet_contains_four_labeled_rotations():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "certificate.png"
        Image.new("RGB", (1200, 800), "white").save(path)
        data_url = prepare_orientation_sheet_data_url(path)

    payload = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(payload)) as sheet:
        assert sheet.size == (1400, 1040)


def test_detail_sheet_contains_four_enlarged_overlapping_regions():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "certificate.png"
        Image.new("RGB", (1600, 1000), "white").save(path)
        data_url = prepare_detail_sheet_data_url(path, rotation=90)

    payload = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(payload)) as sheet:
        assert sheet.size == (2000, 1440)


def test_certificate_image_pipeline_rotates_before_read_and_reviews_bad_number():
    from unittest.mock import patch

    responses = iter((
        {
            "rotation": 90,
            "rotation_confidence": 96,
            "name": "张三",
            "certificate_number": "12345678901234567",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {
                "name": 96,
                "certificate_number": 55,
                "school": 94,
                "major": 92,
            },
            "confidence": 86,
            "warnings": [],
        },
        {
            "name": "",
            "certificate_number": "123456789012345678",
            "school": "",
            "major": "",
            "field_confidence": {"certificate_number": 96},
            "confidence": 96,
            "warnings": [],
        },
    ))
    rotations = []

    def fake_image(_path, *, rotation=0):
        rotations.append(("full", rotation))
        return "data:image/jpeg;base64,YQ=="

    def fake_detail(_path, *, rotation=0):
        rotations.append(("detail", rotation))
        return "data:image/jpeg;base64,Yg=="

    with patch(
        "education_certificate.prepare_orientation_sheet_data_url",
        return_value="data:image/jpeg;base64,Yw==",
    ), patch(
        "education_certificate.prepare_image_data_url",
        side_effect=fake_image,
    ), patch(
        "education_certificate.prepare_detail_sheet_data_url",
        side_effect=fake_detail,
    ), patch(
        "education_certificate._invoke_model",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ):
        result = recognize_certificate_image(
            "fake.jpg",
            {
                "api_provider": "minimax",
                "base_url": "https://api.minimaxi.com/v1",
                "model": "MiniMax-M3",
            },
            "key",
        )

    assert rotations == [("full", 0), ("full", 90), ("detail", 90)]
    assert result.rotation == 90
    assert result.rotation_confidence == 96
    assert result.certificate_number == "123456789012345678"
    assert "高清区域复核纠正" in "；".join(result.warnings)


def test_certificate_image_reviews_warned_fields_once_then_blocks_conflicts():
    from unittest.mock import patch

    responses = iter((
        {
            "rotation": 0,
            "rotation_confidence": 98,
            "name": "张三",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {
                "name": 96,
                "certificate_number": 97,
                "school": 94,
                "major": 93,
            },
            "confidence": 96,
            "warnings": ["姓名存疑，证书编号不清晰"],
        },
        {
            "name": "张山",
            "certificate_number": "123456789012345679",
            "school": "",
            "major": "",
            "field_confidence": {
                "name": 95,
                "certificate_number": 96,
            },
            "confidence": 95,
            "warnings": [],
        },
    ))

    with patch(
        "education_certificate.prepare_orientation_sheet_data_url",
        return_value="data:image/jpeg;base64,YQ==",
    ), patch(
        "education_certificate.prepare_image_data_url",
        return_value="data:image/jpeg;base64,Yg==",
    ), patch(
        "education_certificate.prepare_detail_sheet_data_url",
        return_value="data:image/jpeg;base64,Yw==",
    ), patch(
        "education_certificate._invoke_model",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ) as invoke:
        result = recognize_certificate_image(
            "fake.jpg",
            {
                "api_provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash-vision-exp",
            },
            "key",
        )

    assert result.name == ""
    assert result.certificate_number == ""
    assert result.critical_conflicts == ("name", "certificate_number")
    warning_text = "；".join(result.warnings)
    assert "姓名两次识别结果不一致" in warning_text
    assert "证书编号两次识别结果不一致" in warning_text
    assert invoke.call_count == 2


def test_missing_name_is_rescued_by_one_focused_review():
    from unittest.mock import patch

    responses = iter((
        {
            "rotation": 0,
            "rotation_confidence": 98,
            "name": "",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {"name": 0, "certificate_number": 96},
            "confidence": 92,
            "warnings": [],
        },
        {
            "name": "鲍殊",
            "certificate_number": "123456789012345678",
            "field_confidence": {"name": 94, "certificate_number": 97},
            "confidence": 94,
            "warnings": [],
        },
    ))

    with patch(
        "education_certificate.prepare_orientation_sheet_data_url",
        return_value="orientation",
    ), patch(
        "education_certificate.prepare_image_data_url",
        return_value="full",
    ), patch(
        "education_certificate.prepare_detail_sheet_data_url",
        return_value="detail",
    ), patch(
        "education_certificate.prepare_name_detail_data_urls",
        return_value=("unused",),
    ) as prepare_name_tiles, patch(
        "education_certificate._invoke_model",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ) as invoke:
        result = recognize_certificate_image(
            "fake.jpg",
            {
                "api_provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash-vision-exp",
            },
            "key",
        )

    assert result.name == "鲍殊"
    assert result.critical_conflicts == ()
    prepare_name_tiles.assert_not_called()
    assert invoke.call_count == 2


def test_missing_name_stays_manual_after_one_uncertain_review():
    from unittest.mock import patch

    responses = iter((
        {
            "rotation": 0,
            "rotation_confidence": 98,
            "name": "",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {"name": 0, "certificate_number": 96},
            "confidence": 92,
            "warnings": [],
        },
        {
            "name": "",
            "certificate_number": "123456789012345678",
            "field_confidence": {"name": 0, "certificate_number": 97},
            "confidence": 91,
            "warnings": [],
        },
    ))

    with patch(
        "education_certificate.prepare_orientation_sheet_data_url",
        return_value="orientation",
    ), patch(
        "education_certificate.prepare_image_data_url",
        return_value="full",
    ), patch(
        "education_certificate.prepare_detail_sheet_data_url",
        return_value="detail",
    ), patch(
        "education_certificate.prepare_name_detail_data_urls",
        return_value=("unused",),
    ), patch(
        "education_certificate._invoke_model",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ) as invoke:
        result = recognize_certificate_image(
            "fake.jpg",
            {
                "api_provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash-vision-exp",
            },
            "key",
        )

    assert result.name == ""
    assert result.critical_conflicts == ("name",)
    assert "停止重复识别" in "；".join(result.warnings)
    assert invoke.call_count == 2


def test_unreliable_minimax_name_is_manual_without_extra_calls():
    from unittest.mock import patch

    responses = iter((
        {
            "rotation": 0,
            "rotation_confidence": 98,
            "name": "鲍珠",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {"name": 95, "certificate_number": 96},
            "confidence": 94,
            "warnings": ["姓名第二个字不清晰，'殊'/'珠'存疑"],
        },
        {
            "name": "鲍珠",
            "certificate_number": "123456789012345678",
            "field_confidence": {"name": 96, "certificate_number": 97},
            "confidence": 95,
            "warnings": [],
        },
        {
            "name": "鲍殊",
            "field_confidence": {"name": 93},
            "confidence": 93,
            "warnings": [],
        },
        {
            "name": "鲍殊",
            "character_evidence": [
                {"character": "鲍", "visible_structure": "鱼字旁加包"},
                {"character": "殊", "visible_structure": "歹字旁加朱"},
            ],
            "field_confidence": {"name": 95},
            "confidence": 95,
            "warnings": [],
        },
    ))

    with patch(
        "education_certificate.prepare_orientation_sheet_data_url",
        return_value="orientation",
    ), patch(
        "education_certificate.prepare_image_data_url",
        return_value="full",
    ), patch(
        "education_certificate.prepare_detail_sheet_data_url",
        return_value="detail",
    ), patch(
        "education_certificate.prepare_name_detail_data_urls",
        return_value=("unused",),
    ), patch(
        "education_certificate._invoke_model",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ) as invoke:
        result = recognize_certificate_image(
            "fake.jpg",
            {
                "api_provider": "minimax",
                "base_url": "https://api.minimaxi.com/v1",
                "model": "MiniMax-M3",
            },
            "key",
        )

    assert result.name == "鲍珠"
    assert result.critical_conflicts == ("name",)
    assert "停止重复识别" in "；".join(result.warnings)
    assert invoke.call_count == 1


def test_clear_high_confidence_name_stops_after_primary_read():
    from unittest.mock import patch

    responses = iter((
        {
            "rotation": 0,
            "rotation_confidence": 98,
            "name": "李四",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {"name": 95, "certificate_number": 96},
            "confidence": 94,
            "warnings": [],
        },
        {
            "name": "鲍珠",
            "certificate_number": "123456789012345678",
            "field_confidence": {"name": 96, "certificate_number": 97},
            "confidence": 95,
            "warnings": [],
        },
        {
            "name": "鲍殊",
            "field_confidence": {"name": 94},
            "confidence": 94,
            "warnings": [],
        },
        {
            "name": "鲍珠",
            "field_confidence": {"name": 95},
            "confidence": 95,
            "warnings": [],
        },
    ))

    with patch(
        "education_certificate.prepare_orientation_sheet_data_url",
        return_value="orientation",
    ), patch(
        "education_certificate.prepare_image_data_url",
        return_value="full",
    ), patch(
        "education_certificate.prepare_detail_sheet_data_url",
        return_value="detail",
    ), patch(
        "education_certificate.prepare_name_detail_data_urls",
        return_value=("unused",),
    ) as prepare_name_tiles, patch(
        "education_certificate._invoke_model",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ) as invoke:
        result = recognize_certificate_image(
            "fake.jpg",
            {
                "api_provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash-vision-exp",
            },
            "key",
        )

    assert result.name == "李四"
    assert result.critical_conflicts == ()
    prepare_name_tiles.assert_not_called()
    assert invoke.call_count == 1


def test_warned_name_stays_manual_even_when_two_reads_agree():
    from unittest.mock import patch

    responses = iter((
        {
            "rotation": 0,
            "rotation_confidence": 98,
            "name": "李四",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {"name": 95, "certificate_number": 96},
            "confidence": 94,
            "warnings": ["姓名字形存疑，建议人工确认"],
        },
        {
            "name": "李四",
            "certificate_number": "123456789012345678",
            "field_confidence": {"name": 96, "certificate_number": 97},
            "confidence": 95,
            "warnings": [],
        },
        {
            "name": "",
            "field_confidence": {"name": 0},
            "confidence": 40,
            "warnings": ["字形不清晰"],
        },
    ))

    with patch(
        "education_certificate.prepare_orientation_sheet_data_url",
        return_value="orientation",
    ), patch(
        "education_certificate.prepare_image_data_url",
        return_value="full",
    ), patch(
        "education_certificate.prepare_detail_sheet_data_url",
        return_value="detail",
    ), patch(
        "education_certificate.prepare_name_detail_data_urls",
        return_value=("unused",),
    ) as prepare_name_tiles, patch(
        "education_certificate._invoke_model",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ) as invoke:
        result = recognize_certificate_image(
            "fake.jpg",
            {
                "api_provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash-vision-exp",
            },
            "key",
        )

    assert result.name == "李四"
    assert result.critical_conflicts == ("name",)
    assert "停止重复识别" in "；".join(result.warnings)
    prepare_name_tiles.assert_not_called()
    assert invoke.call_count == 2


def test_critical_field_disagreement_stops_after_two_reads():
    from unittest.mock import patch

    responses = iter((
        {
            "rotation": 0,
            "rotation_confidence": 95,
            "name": "张三",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {"name": 95, "certificate_number": 95},
            "confidence": 95,
            "warnings": ["姓名和证书编号存疑"],
        },
        {
            "name": "张山",
            "certificate_number": "123456789012345679",
            "field_confidence": {"name": 94, "certificate_number": 94},
            "confidence": 94,
            "warnings": [],
        },
        {
            "name": "张三",
            "certificate_number": "123456789012345678",
            "field_confidence": {"name": 96, "certificate_number": 96},
            "confidence": 96,
            "warnings": [],
        },
    ))
    with patch(
        "education_certificate.prepare_orientation_sheet_data_url",
        return_value="data:image/jpeg;base64,YQ==",
    ), patch(
        "education_certificate.prepare_image_data_url",
        return_value="data:image/jpeg;base64,Yg==",
    ), patch(
        "education_certificate.prepare_detail_sheet_data_url",
        return_value="data:image/jpeg;base64,Yw==",
    ), patch(
        "education_certificate._invoke_model",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ) as invoke:
        result = recognize_certificate_image(
            "fake.jpg",
            {
                "api_provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash-vision-exp",
            },
            "key",
        )

    assert result.name == ""
    assert result.certificate_number == ""
    assert result.critical_conflicts == ("name", "certificate_number")
    assert invoke.call_count == 2


def test_low_confidence_vertical_orientation_uses_only_orientation_fallback():
    from unittest.mock import patch

    responses = iter((
        {
            "rotation": 90,
            "rotation_confidence": 65,
            "name": "张三",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {
                "name": 92,
                "certificate_number": 91,
                "school": 90,
                "major": 89,
            },
            "confidence": 91,
            "warnings": [],
        },
        {"rotation": 90, "rotation_confidence": 96},
        {
            "name": "张三",
            "certificate_number": "123456789012345678",
            "school": "",
            "major": "",
            "field_confidence": {"name": 95, "certificate_number": 96},
            "confidence": 95,
            "warnings": [],
        },
    ))
    rotations = []

    def fake_image(_path, *, rotation=0):
        rotations.append(rotation)
        return "data:image/jpeg;base64,YQ=="

    with patch(
        "education_certificate.prepare_orientation_sheet_data_url",
        return_value="data:image/jpeg;base64,Yg==",
    ), patch(
        "education_certificate.prepare_image_data_url",
        side_effect=fake_image,
    ), patch(
        "education_certificate.prepare_detail_sheet_data_url",
        return_value="data:image/jpeg;base64,Yw==",
    ), patch(
        "education_certificate._invoke_model",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ) as invoke:
        result = recognize_certificate_image(
            "vertical.jpg",
            {
                "api_provider": "minimax",
                "base_url": "https://api.minimaxi.com/v1",
                "model": "MiniMax-M3",
            },
            "key",
        )

    assert rotations == [0, 90]
    assert invoke.call_count == 2
    assert result.rotation == 90
    assert result.rotation_confidence == 96
    assert result.name == "张三"
    assert result.certificate_number == "123456789012345678"


def test_wrong_high_confidence_rotation_with_missing_name_is_rechecked_and_reread():
    from unittest.mock import patch

    responses = iter((
        {
            "rotation": 180,
            "rotation_confidence": 96,
            "name": "",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {
                "name": 0,
                "certificate_number": 95,
                "school": 92,
                "major": 90,
            },
            "confidence": 83,
            "warnings": ["姓名无法确认"],
        },
        {"rotation": 0, "rotation_confidence": 97},
        {
            "name": "孙慧勇",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "field_confidence": {
                "name": 96,
                "certificate_number": 97,
                "school": 94,
                "major": 93,
            },
            "confidence": 96,
            "warnings": [],
        },
    ))
    rotations = []

    def fake_image(_path, *, rotation=0):
        rotations.append(rotation)
        return "data:image/jpeg;base64,YQ=="

    with patch(
        "education_certificate.prepare_orientation_sheet_data_url",
        return_value="data:image/jpeg;base64,Yg==",
    ), patch(
        "education_certificate.prepare_image_data_url",
        side_effect=fake_image,
    ), patch(
        "education_certificate._invoke_model",
        side_effect=lambda *_args, **_kwargs: next(responses),
    ) as invoke:
        result = recognize_certificate_image(
            "upside-down.jpg",
            {
                "api_provider": "openai",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-vision",
            },
            "key",
        )

    assert rotations == [0]
    assert invoke.call_count == 3
    assert result.rotation == 0
    assert result.rotation_confidence == 97
    assert result.name == "孙慧勇"
    assert "方向复核已纠正角度并重新识别" in "；".join(result.warnings)


def test_manual_rotation_override_skips_direction_model_for_clear_single_read():
    from unittest.mock import patch

    payload = {
        "name": "张三",
        "certificate_number": "123456789012345678",
        "school": "某大学",
        "major": "计算机",
        "field_confidence": {
            "name": 96,
            "certificate_number": 97,
            "school": 94,
            "major": 93,
        },
        "confidence": 96,
        "warnings": [],
    }
    rotations = []

    def fake_image(_path, *, rotation=0):
        rotations.append(("full", rotation))
        return "data:image/jpeg;base64,YQ=="

    def fake_detail(_path, *, rotation=0):
        rotations.append(("detail", rotation))
        return "data:image/jpeg;base64,Yg=="

    with patch(
        "education_certificate.prepare_orientation_sheet_data_url"
    ) as orientation_sheet, patch(
        "education_certificate.prepare_image_data_url",
        side_effect=fake_image,
    ), patch(
        "education_certificate.prepare_detail_sheet_data_url",
        side_effect=fake_detail,
    ), patch(
        "education_certificate._invoke_model",
        side_effect=(payload, payload),
    ) as invoke:
        result = recognize_certificate_image(
            "vertical.jpg",
            {
                "api_provider": "minimax",
                "base_url": "https://api.minimaxi.com/v1",
                "model": "MiniMax-M3",
            },
            "key",
            rotation_override=90,
        )

    orientation_sheet.assert_not_called()
    assert invoke.call_count == 1
    assert rotations == [("full", 90)]
    assert result.rotation == 90
    assert result.rotation_confidence == 100
    assert "已按手动指定方向识别" in "；".join(result.warnings)


def test_validate_chsi_fields_rejects_invalid_values():
    assert validate_chsi_fields(" 张三 ", "1234-5678") == ("张三", "12345678")

    try:
        validate_chsi_fields("", "123")
    except ValueError as error:
        assert "姓名" in str(error)
    else:
        raise AssertionError("empty name should fail")


def test_fill_chsi_query_page_opens_official_url_and_passes_confirmed_values():
    class FakePage:
        def __init__(self):
            self.url = ""
            self.js_args = ()

        def get(self, url):
            self.url = url

        def run_js(self, _script, *args):
            self.js_args = args
            return "ok"

    page = FakePage()
    fill_chsi_query_page(page, " 张三 ", "1234-5678")

    assert page.url == CHSI_QUERY_URL
    assert page.js_args == ("12345678", "张三")


def test_fill_chsi_query_page_requires_agreement_checkbox_to_be_checked():
    class FakePage:
        def get(self, _url):
            return None

        def run_js(self, script, *_args):
            assert 'input[type="checkbox"][name="yhxy"]' in script
            assert "agreement.click()" in script
            assert "unchecked:yhxy" in script
            return "unchecked:yhxy"

    try:
        fill_chsi_query_page(FakePage(), "张三", "123456789012345678")
    except RuntimeError as error:
        assert "unchecked:yhxy" in str(error)
    else:
        raise AssertionError("unchecked agreement should fail")

def test_is_pdf_path_detects_pdf_suffix():
    assert is_pdf_path("certificate.pdf") is True
    assert is_pdf_path("certificate.PDF") is True
    assert is_pdf_path("certificate.jpg") is False
    assert is_pdf_path("certificate.png") is False


def test_validate_document_path_accepts_pdf_and_rejects_unsupported():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdf = tmp_path / "cert.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        img = tmp_path / "cert.jpg"
        img.write_bytes(b"fake-jpeg-content")

        assert validate_document_path(pdf) == pdf
        assert validate_document_path(img) == img

        docx = tmp_path / "cert.docx"
        docx.write_bytes(b"fake")
        try:
            validate_document_path(docx)
        except ValueError as error:
            assert "PDF" in str(error)
        else:
            raise AssertionError("unsupported format should fail")


def test_build_pdf_text_messages_uses_text_content_not_image():
    messages = build_pdf_text_messages("姓名 张三 证书编号 12345")
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    # 文本协议：content 是字符串，不是 image_url 结构
    assert isinstance(messages[1]["content"], str)
    assert "张三" in messages[1]["content"]


def test_recognize_certificate_pdf_raises_on_empty_text():
    from unittest.mock import patch
    with patch("education_certificate.extract_pdf_text", return_value=""):
        try:
            recognize_certificate_pdf("fake.pdf", {"base_url": "x", "model": "y"}, "k")
        except ValueError as error:
            assert "扫描件" in str(error)
        else:
            raise AssertionError("empty text should fail")


def test_recognize_certificate_pdf_accepts_injected_text_extractor():
    from unittest.mock import patch

    captured = {}

    def fake_extract(path):
        captured["path"] = path
        return "姓名 鲍殊 证书编号 102891202305002814"

    with patch(
        "education_certificate._invoke_model",
        return_value={
            "name": "鲍殊",
            "certificate_number": "102891202305002814",
            "school": "江苏科技大学",
            "major": "金融工程",
            "confidence": 95,
            "warnings": [],
        },
    ):
        result = recognize_certificate_pdf(
            "certificate.pdf",
            {"base_url": "https://api.example.com/v1", "model": "text-model"},
            "key",
            text_extractor=fake_extract,
        )

    assert captured["path"] == "certificate.pdf"
    assert result.name == "鲍殊"


def test_recognize_certificate_pdf_raises_when_pdf_unreadable():
    from unittest.mock import patch
    def boom(_path):
        raise RuntimeError("PDF 无法读取：加密")
    with patch("education_certificate.extract_pdf_text", side_effect=boom):
        try:
            recognize_certificate_pdf("fake.pdf", {"base_url": "x", "model": "y"}, "k")
        except ValueError as error:
            assert "加密" in str(error)
        else:
            raise AssertionError("unreadable PDF should fail")


def test_recognize_certificate_pdf_invokes_text_model_with_extracted_text():
    from unittest.mock import patch
    captured = {}

    def fake_invoke(config, api_key, messages, *, timeout=60, max_tokens=500):
        captured["messages"] = messages
        captured["api_key"] = api_key
        return {
            "name": "张三",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "confidence": 90,
            "warnings": [],
        }

    patches = [
        patch(
            "education_certificate.extract_pdf_text",
            return_value="姓名 张三 证书编号 123456789012345678",
        ),
        patch("education_certificate._invoke_model", side_effect=fake_invoke),
    ]
    for pg in patches:
        pg.start()
    try:
        config = {"base_url": "https://api.example.com/v1", "model": "text-model"}
        result = recognize_certificate_pdf("fake.pdf", config, "key123")
    finally:
        for pg in patches:
            pg.stop()

    # 走文本协议，不走 resolve_vision_api_config（不挑视觉模型）
    assert captured["api_key"] == "key123"
    assert captured["messages"][1]["content"] == "姓名 张三 证书编号 123456789012345678"
    assert result.name == "张三"
    assert result.certificate_number == "123456789012345678"
    assert result.model == "text-model"


def test_chsi_result_text_requires_candidate_and_multiple_result_fields():
    result_text = """
    学历查询结果 姓名 张三 性别 男 出生日期 1990年1月1日
    学校名称 某大学 专业 计算机科学 学历层次 本科 学习形式 普通全日制
    """

    assert is_chsi_result_text(result_text, "张 三") is True
    assert is_chsi_result_text(result_text, "李四") is False
    assert is_chsi_result_text("姓名 张三 证书编号 123 图片验证码", "张三") is False


def test_chsi_terminal_result_distinguishes_record_not_found_and_qr_page():
    assert classify_chsi_terminal_result(
        "姓名 张三 性别 男 出生日期 1990年 入学日期 2008年 "
        "学校名称 某大学 专业 计算机 学历层次 本科"
    ) == "record"
    assert classify_chsi_terminal_result(
        "未找到学历信息，可能是因为：输入信息有误"
    ) == "not_found"
    assert classify_chsi_terminal_result(
        "请使用学信网 App 扫描二维码并在手机上确认"
    ) == ""
    assert is_chsi_qr_confirmation_text("扫码验证") is True
    assert is_chsi_qr_confirmation_text("学历查询结果") is False


def test_chsi_result_page_text_includes_accessible_frame_content():
    class Context:
        def __init__(self, text):
            self.text = text

        def run_js(self, _script):
            return self.text

    class Page(Context):
        def get_frames(self, *, timeout):
            assert timeout == 0
            return [Context("学校名称 某大学 专业 计算机")]

    text = read_chsi_result_page_text(Page("姓名 张三 性别 男 出生日期 1990年"))

    assert "姓名 张三" in text
    assert "学校名称 某大学" in text


def test_chsi_screenshot_filename_is_stable_safe_and_hides_full_number():
    number = "123456789012345678"

    filename = build_chsi_screenshot_filename("张/三", number)

    assert filename == build_chsi_screenshot_filename("张/三", number)
    assert filename.startswith("张_三_证书尾号345678_学历核验_")
    assert filename.endswith(".png")
    assert number not in filename
    assert "/" not in filename


def test_chsi_screenshot_normalization_trims_border_and_uses_fixed_width():
    assert CHSI_SCREENSHOT_WIDTH == 3840
    source = Image.new("RGB", (1000, 800), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((180, 140, 820, 660), fill="#E5F1FA", outline="black", width=4)
    draw.text((260, 240), "CHSI RESULT", fill="black")

    normalized = normalize_chsi_screenshot_png(_png_bytes(source))

    with Image.open(io.BytesIO(normalized)) as image:
        assert image.format == "PNG"
        assert image.width == CHSI_SCREENSHOT_WIDTH
        assert image.height < CHSI_SCREENSHOT_WIDTH
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_chsi_result_capture_uses_complete_page_without_browser_chrome():
    raw = _png_bytes(Image.new("RGB", (800, 600), "white"))

    class Page:
        def __init__(self):
            self.activated = False
            self.set = SimpleNamespace(
                activate=self._activate,
            )
            self.calls = 0

        def _activate(self):
            self.activated = True

        def run_js(self, _script, *_args):
            self.calls += 1
            if self.calls == 1:
                if any(
                    isinstance(argument, (dict, list, tuple))
                    for argument in _args
                ):
                    raise TypeError("DrissionPage 不支持结构化 run_js 参数")
                assert len(_args) == 3
                assert isinstance(_args[2], str)
                assert "性别" in _args[2]
                return {"matched": True}
            return None

        def get_frames(self, *, timeout):
            assert timeout == 0
            return []

        def get_screenshot(self, *, as_bytes, full_page):
            assert as_bytes == "png"
            assert full_page is True
            return raw

    page = Page()

    assert capture_chsi_result_png(page, "张三") == raw
    assert page.activated is True


def test_chsi_result_capture_rejects_query_or_qr_page():
    class Window:
        def max(self):
            return None

    class Page:
        set = SimpleNamespace(activate=lambda: None, window=Window())

        def get_frames(self, *, timeout):
            assert timeout == 0
            return []

        def run_js(self, _script, *_args):
            return {"matched": False, "reason": "not-result"}

    try:
        capture_chsi_result_png(Page(), "张三")
    except ChsiResultNotReadyError as error:
        assert "学历查询结果" in str(error)
    else:
        raise AssertionError("query or QR page must not be captured as a final result")


def test_chsi_screenshot_save_is_valid_and_never_overwrites():
    raw = _png_bytes(Image.new("RGB", (800, 600), "white"))
    with tempfile.TemporaryDirectory() as temp_dir:
        target = Path(temp_dir) / "result.png"

        saved = save_chsi_result_screenshot(raw, target)
        original = target.read_bytes()

        assert saved == target
        assert is_valid_chsi_screenshot(target) is True
        try:
            save_chsi_result_screenshot(raw, target)
        except RuntimeError as error:
            assert "已经存在" in str(error)
        else:
            raise AssertionError("existing screenshots must never be overwritten")
        assert target.read_bytes() == original


def test_chsi_existing_screenshot_validation_rejects_truncated_png():
    image = Image.new("RGB", (CHSI_SCREENSHOT_WIDTH, 900), "white")
    encoded = _png_bytes(image)
    with tempfile.TemporaryDirectory() as temp_dir:
        target = Path(temp_dir) / "truncated.png"
        target.write_bytes(encoded[:80])

        assert is_valid_chsi_screenshot(target) is False
