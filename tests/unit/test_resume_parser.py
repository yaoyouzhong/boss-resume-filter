from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from resume_parser import (
    ResumeContentTooShortError,
    ResumeParserDependencyError,
    ResumeTextReadError,
    UnsupportedResumeFormatError,
    parse_resume_text,
)


def _capture_exception(exception_type, callback):
    try:
        callback()
    except exception_type as exc:
        return exc
    raise AssertionError(f"未抛出 {exception_type.__name__}")


def test_parse_resume_text_reads_utf8_and_strips_outer_whitespace():
    content = "  " + "Python 后端开发经验，负责交易系统、数据库和自动化交付。" * 3 + "  "
    with TemporaryDirectory() as directory:
        path = Path(directory) / "resume.txt"
        path.write_text(content, encoding="utf-8")

        parsed = parse_resume_text(path)

    assert parsed == content.strip()


def test_parse_resume_text_falls_back_to_gbk_for_markdown():
    content = "Java 开发工程师，熟悉金融交易、数据库设计、故障排查和项目交付。" * 3
    with TemporaryDirectory() as directory:
        path = Path(directory) / "resume.md"
        path.write_bytes(content.encode("gbk"))

        parsed = parse_resume_text(path)

    assert parsed == content


def test_parse_resume_text_removes_html_code_and_restores_entities():
    visible = "候选人 & 简历：十年软件开发经验，熟悉 Python、SQL 和证券交易系统。" * 3
    document = (
        "<html><style>.hidden {display:none}</style>"
        "<script>secret();</script><body>"
        f"<p>{visible.replace('&', '&amp;')}</p></body></html>"
    )
    with TemporaryDirectory() as directory:
        path = Path(directory) / "resume.html"
        path.write_text(document, encoding="utf-8")

        parsed = parse_resume_text(path)

    assert parsed == visible
    assert "secret" not in parsed
    assert "hidden" not in parsed


def test_parse_resume_text_classifies_unsupported_format():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "resume.pages"
        path.write_text("x" * 100, encoding="utf-8")

        exc = _capture_exception(
            UnsupportedResumeFormatError,
            lambda: parse_resume_text(path),
        )

    assert exc.extension == ".pages"


def test_parse_resume_text_classifies_missing_optional_parser_dependency():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "resume.pdf"
        path.write_bytes(b"not-read-when-parser-is-missing")
        with patch.dict("sys.modules", {"pdfminer.high_level": None}):
            exc = _capture_exception(
                ResumeParserDependencyError,
                lambda: parse_resume_text(path),
            )

    assert exc.format_name == "PDF"
    assert exc.package_name == "pdfminer.six"


def test_parse_resume_text_classifies_empty_text_file():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "resume.txt"
        path.write_text("", encoding="utf-8")

        exc = _capture_exception(
            ResumeTextReadError,
            lambda: parse_resume_text(path),
        )

    assert exc.format_name == "文本"


def test_parse_resume_text_reports_short_extracted_content():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "resume.txt"
        path.write_text("简历内容过少", encoding="utf-8")

        exc = _capture_exception(
            ResumeContentTooShortError,
            lambda: parse_resume_text(path),
        )

    assert exc.text_length == 6
    assert exc.minimum_length == 50
