from pathlib import Path
from unittest.mock import patch

from education_tool_pdf import extract_pdf_text_lightweight


def test_lightweight_pdf_extractor_normalizes_text_lines():
    fake_reader = type(
        "FakeReader",
        (),
        {
            "pages": [
                type("Page", (), {"extract_text": lambda self: "  姓名  鲍殊  \n\n"})(),
                type(
                    "Page",
                    (),
                    {"extract_text": lambda self: "证书编号\t102891202305002814"},
                )(),
            ]
        },
    )()

    with patch("pypdf.PdfReader", return_value=fake_reader) as reader:
        text = extract_pdf_text_lightweight(Path("certificate.pdf"))

    reader.assert_called_once_with("certificate.pdf")
    assert text == "姓名 鲍殊\n证书编号 102891202305002814"


def test_lightweight_pdf_extractor_classifies_read_failure():
    with patch("pypdf.PdfReader", side_effect=ValueError("broken")):
        try:
            extract_pdf_text_lightweight("certificate.pdf")
        except RuntimeError as error:
            assert str(error) == "PDF 无法读取：broken"
        else:
            raise AssertionError("unreadable PDF should fail")
