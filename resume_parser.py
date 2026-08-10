"""本地简历文件解析。

该模块只负责将支持的文件转为文本并分类可预期错误，
不负责文件选择、界面反馈、候选人更新或简历存储。
"""

from html import unescape
from pathlib import Path
import re


TEXT_ENCODINGS = ("utf-8", "gbk", "gb2312", "latin-1")


class ResumeParseError(Exception):
    """简历文件中可以向用户解释的预期解析错误。"""


class ResumeParserDependencyError(ResumeParseError):
    """当前文件格式缺少可选解析依赖。"""

    def __init__(self, format_name: str, package_name: str) -> None:
        self.format_name = format_name
        self.package_name = package_name
        super().__init__(f"{format_name} 解析需要 {package_name}")


class ResumeTextReadError(ResumeParseError):
    """文本型简历无法使用支持的编码读取。"""

    def __init__(self, format_name: str) -> None:
        self.format_name = format_name
        super().__init__(f"无法读取 {format_name} 简历")


class UnsupportedResumeFormatError(ResumeParseError):
    """文件扩展名不在支持范围内。"""

    def __init__(self, extension: str) -> None:
        self.extension = extension
        super().__init__(f"不支持的简历格式：{extension or '无扩展名'}")


class ResumeContentTooShortError(ResumeParseError):
    """解析结果不足以进行简历评估。"""

    def __init__(self, text_length: int, minimum_length: int) -> None:
        self.text_length = text_length
        self.minimum_length = minimum_length
        super().__init__(
            f"简历文本仅 {text_length} 字，少于 {minimum_length} 字"
        )


def _read_encoded_text(path: Path, format_name: str) -> str:
    """按旧有编码顺序读取文本型简历。"""
    for encoding in TEXT_ENCODINGS:
        try:
            content = path.read_text(encoding=encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if content:
            return content
    raise ResumeTextReadError(format_name)


def _parse_pdf(path: Path) -> str:
    try:
        from pdfminer.high_level import extract_text
    except ImportError as exc:
        raise ResumeParserDependencyError("PDF", "pdfminer.six") from exc
    return extract_text(str(path)) or ""


def _parse_docx(path: Path) -> str:
    try:
        import docx
    except ImportError as exc:
        raise ResumeParserDependencyError("Word", "python-docx") from exc
    document = docx.Document(str(path))
    return "\n".join(
        paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()
    )


def _parse_rtf(path: Path) -> str:
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as exc:
        raise ResumeParserDependencyError("RTF", "striprtf") from exc
    content = path.read_text(encoding="utf-8", errors="replace")
    return rtf_to_text(content)


def _parse_html(path: Path) -> str:
    content = _read_encoded_text(path, "HTML")
    content = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        "",
        content,
        flags=re.S | re.I,
    )
    text = re.sub(r"<[^>]+>", " ", content)
    return unescape(re.sub(r"\s+", " ", text).strip())


def parse_resume_text(
    filepath: str | Path,
    *,
    minimum_length: int = 50,
) -> str:
    """将支持的简历文件转为可评估文本。

    Args:
        filepath: 本地简历文件路径。
        minimum_length: 剥离首尾空白后可接受的最小字符数。

    Raises:
        ResumeParserDependencyError: 缺少当前格式的可选解析依赖。
        ResumeTextReadError: 文本型文件没有可读内容。
        UnsupportedResumeFormatError: 文件格式不受支持。
        ResumeContentTooShortError: 提取文本不足以进行评估。
    """
    path = Path(filepath)
    extension = path.suffix.lower()

    if extension == ".pdf":
        text = _parse_pdf(path)
    elif extension == ".docx":
        text = _parse_docx(path)
    elif extension in {".txt", ".md"}:
        text = _read_encoded_text(path, "文本")
    elif extension == ".rtf":
        text = _parse_rtf(path)
    elif extension in {".html", ".htm"}:
        text = _parse_html(path)
    else:
        raise UnsupportedResumeFormatError(extension)

    text = text.strip()
    if len(text) < minimum_length:
        raise ResumeContentTooShortError(len(text), minimum_length)
    return text
