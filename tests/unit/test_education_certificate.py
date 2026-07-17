"""毕业证书图片识别模块测试。"""
import base64
import io
import tempfile
from pathlib import Path

from PIL import Image

from education_certificate import (
    CHSI_QUERY_URL,
    build_pdf_text_messages,
    build_vision_messages,
    extract_pdf_text,
    fill_chsi_query_page,
    is_pdf_path,
    likely_supports_vision,
    normalize_recognition,
    prepare_image_data_url,
    prepare_orientation_sheet_data_url,
    recognize_certificate_pdf,
    recognize_certificate_image,
    resolve_vision_api_config,
    validate_chsi_fields,
    validate_document_path,
)


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
        orientation_url,
    )
    anthropic_messages = build_vision_messages(
        {"api_provider": "anthropic", "base_url": "https://api.anthropic.com/v1"},
        data_url,
        orientation_url,
    )

    assert openai_messages[1]["content"][1]["type"] == "image_url"
    assert openai_messages[1]["content"][2]["image_url"]["url"] == orientation_url
    assert anthropic_messages[1]["content"][0]["source"]["data"] == "YWJj"
    assert anthropic_messages[1]["content"][1]["source"]["data"] == "ZGVm"
    assert '"rotation":0' in openai_messages[0]["content"]
    assert "ROTATE 0/90/180/270 CW" in openai_messages[0]["content"]


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


def test_kimi_code_image_recognition_uses_larger_output_budget():
    from unittest.mock import patch

    captured = {}

    def fake_invoke(config, api_key, messages, *, timeout=60, max_tokens=500):
        captured["max_tokens"] = max_tokens
        return {
            "name": "张三",
            "certificate_number": "123456789012345678",
            "school": "某大学",
            "major": "计算机",
            "confidence": 90,
            "warnings": [],
        }

    with patch("education_certificate.prepare_image_data_url", return_value="data:image/jpeg;base64,YQ=="), \
            patch("education_certificate.prepare_orientation_sheet_data_url", return_value="data:image/jpeg;base64,Yg=="), \
            patch("education_certificate._invoke_model", side_effect=fake_invoke):
        result = recognize_certificate_image(
            "fake.jpg",
            {
                "api_provider": "kimi",
                "base_url": "https://api.kimi.com/coding/v1",
                "model": "k3",
            },
            "key",
        )

    assert captured["max_tokens"] == 1024
    assert result.name == "张三"


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
