"""独立学历工具的轻量 PDF 文本提取。"""
from __future__ import annotations

import re
from pathlib import Path


def extract_pdf_text_lightweight(path: str | Path) -> str:
    """使用 pypdf 提取 PDF 文本层，不栅格化扫描件。"""
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("PDF 解析依赖未安装") from error
    try:
        reader = PdfReader(str(path))
        raw = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:
        raise RuntimeError(f"PDF 无法读取：{error}") from error
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
    return "\n".join(line for line in lines if line)
