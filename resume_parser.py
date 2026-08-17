"""本地简历文件解析。

该模块只负责将支持的文件转为文本并分类可预期错误，
不负责文件选择、界面反馈、候选人更新或简历存储。
旧版 .doc 先嗅探实际格式：HTML/MHT 伪格式直接解析，
真 OLE2 二进制委托 legacy_doc_converter 在本机 Word 提取全文。
"""

from email import policy
from email.parser import BytesParser
from html import unescape
from pathlib import Path
import re
import shutil

from legacy_doc_converter import convert_legacy_doc


TEXT_ENCODINGS = (
    "utf-8-sig",
    "utf-16",
    "gbk",
    "gb2312",
    "latin-1",
)


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
    from docx.oxml.ns import qn

    document = docx.Document(str(path))
    # 表格排版简历的正文全在表格单元格里，document.paragraphs 读不到；
    # 按文档顺序遍历正文块（段落/表格），段落聚合其全部文本节点
    # （含嵌套文本框、超链接内的 run），表格逐行逐格提取。
    lines: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            lines.append("".join(node.text or "" for node in child.iter(qn("w:t"))))
        elif child.tag == qn("w:tbl"):
            for row in child.iter(qn("w:tr")):
                for cell in row.iter(qn("w:tc")):
                    for paragraph in cell.iter(qn("w:p")):
                        lines.append(
                            "".join(
                                node.text or "" for node in paragraph.iter(qn("w:t"))
                            )
                        )
    return "\n".join(line for line in lines if line.strip())


def _looks_like_html(head: bytes) -> bool:
    """招聘网站常把 HTML 简历直接命名为 .doc；按文件头识别。"""
    raw = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    lowered = raw.lower()
    if lowered.startswith((b"<html", b"<!doctype html")):
        return True
    # UTF-16 网页的标签字节之间带 NUL，直接按 bytes 前缀无法识别。
    for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = head.decode(encoding).lstrip("\ufeff \t\r\n").lower()
        except UnicodeDecodeError:
            continue
        if text.startswith(("<html", "<!doctype html")):
            return True
    return False


def _looks_like_mht(head: bytes) -> bool:
    """Word 另存的"单个文件网页"（MIME HTML）也常被命名为 .doc。"""
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return text.startswith(b"mime-version:")


def _html_to_text(content: str) -> str:
    # Word/WPS 导出的 HTML 把文档属性、样式定义放在条件注释里，先整体剥离注释
    content = re.sub(r"<!--.*?-->", " ", content, flags=re.S)
    content = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        "",
        content,
        flags=re.S | re.I,
    )
    text = re.sub(r"<[^>]+>", " ", content)
    return unescape(re.sub(r"\s+", " ", text).strip())


def _decode_html_bytes(raw: bytes) -> str:
    """解码 HTML 字节流：优先 part 声明或 meta charset，再按常见编码逐个尝试。"""
    meta = re.search(rb'charset=["\']?([\w-]+)', raw[:2048])
    candidates: list[str] = []
    if meta:
        candidates.append(meta.group(1).decode("ascii", errors="ignore"))
    candidates.extend(TEXT_ENCODINGS)
    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_mht(path: Path) -> str:
    """从 MIME HTML（.mht 伪装 .doc）中提取 HTML 部分并转为文本。"""
    try:
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    except Exception as exc:
        raise ResumeTextReadError("MHT") from exc
    part = next(
        (item for item in message.walk() if item.get_content_type() == "text/html"),
        None,
    )
    raw = part.get_payload(decode=True) if part is not None else None
    if not raw:
        raise ResumeTextReadError("MHT")
    charset = part.get_content_charset() if part is not None else None
    if charset:
        try:
            return _html_to_text(raw.decode(charset))
        except (UnicodeDecodeError, LookupError):
            pass
    return _html_to_text(_decode_html_bytes(raw))


def _parse_legacy_doc(path: Path) -> str:
    """旧版 .doc：伪格式（HTML/MHT）直接解析；真 OLE2 经本机 Word 提取全文。"""
    head = path.read_bytes()[:4096]
    if _looks_like_html(head):
        return _parse_html(path)
    if _looks_like_mht(head):
        return _parse_mht(path)
    converted = convert_legacy_doc(path)
    try:
        text = converted.read_text(encoding="utf-8")
        # Word 全文导出使用 \r 换行，表格单元格以 \x07 分隔
        return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x07", "\t")
    finally:
        shutil.rmtree(converted.parent, ignore_errors=True)


def _parse_rtf(path: Path) -> str:
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as exc:
        raise ResumeParserDependencyError("RTF", "striprtf") from exc
    content = path.read_text(encoding="utf-8", errors="replace")
    return rtf_to_text(content)


def _parse_html(path: Path) -> str:
    return _html_to_text(_read_encoded_text(path, "HTML"))


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
        LegacyDocConversionError: 旧版 .doc 无法在本机完成转换。
        ResumeContentTooShortError: 提取文本不足以进行评估。
    """
    path = Path(filepath)
    extension = path.suffix.lower()

    if extension == ".pdf":
        text = _parse_pdf(path)
    elif extension == ".docx":
        text = _parse_docx(path)
    elif extension == ".doc":
        text = _parse_legacy_doc(path)
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
