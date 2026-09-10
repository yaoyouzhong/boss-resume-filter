"""Read certificate PDF text and render images through serialized PDFium calls."""
from contextlib import contextmanager
from pathlib import Path
import re
from threading import RLock
from typing import Any, Iterator

# PDFium is not thread-safe, even on different documents. All callers share this lock.
_PDF_LOCK = RLock()


@contextmanager
def _open_document(source: str | Path) -> Iterator[Any]:
    """Keep all native document/page lifetimes inside the PDFium lock."""
    import pypdfium2 as pdfium

    with _PDF_LOCK:
        try:
            document = pdfium.PdfDocument(source)
        except pdfium.PdfiumError as error:
            if error.err_code == 4:
                raise ValueError("PDF 已加密，请先解密后导入") from error
            raise ValueError(f"PDF 无法读取：{error}") from error
        try:
            yield document
        finally:
            document.close()


def _render_page(document: Any, index: int, max_edge: int):
    """Render a composed page with its rotation, masks and overlaid text."""
    page = document[index]
    try:
        scale = min(3.0, max_edge / max(page.get_size()))
        bitmap = page.render(scale=scale)
        try:
            # Detach from native memory before closing the bitmap.
            return bitmap.to_pil().convert("RGB").copy()
        finally:
            bitmap.close()
    finally:
        page.close()


def render_preview_page(source: str | Path, page_index: int = 0):
    """Return an owned PIL page image and page count with bounded render size."""
    with _open_document(source) as document:
        if not len(document):
            raise ValueError("PDF 没有可预览页面")
        index = max(0, min(page_index, len(document) - 1))
        return _render_page(document, index, 2400), len(document)


def extract_certificate_pages(source: str | Path, destination: Path) -> list[Path]:
    """Render all pages of a 1-6 page certificate in original page order."""
    paths: list[Path] = []
    with _open_document(source) as document:
        if not 1 <= len(document) <= 6:
            raise ValueError("每份证书 PDF 请保留 1 至 6 页，多份证书请分别导入")
        for index in range(len(document)):
            target = destination / f"page-{index + 1}.png"
            image = _render_page(document, index, 4000)
            try:
                image.save(target)
            finally:
                image.close()
            paths.append(target)
    return paths


def extract_pdf_text(source: str | Path) -> str:
    """Extract full Unicode text; return empty text for image-only scans."""
    pages: list[str] = []
    try:
        with _open_document(source) as document:
            for index in range(len(document)):
                page = document[index]
                try:
                    text_page = page.get_textpage()
                    try:
                        # Full-document extraction must also preserve text
                        # extending beyond the visible page (common in exports).
                        left, bottom, right, top = page.get_bbox()
                        for char_index in range(text_page.count_chars()):
                            x0, y0, x1, y1 = text_page.get_charbox(char_index)
                            left, bottom = min(left, x0), min(bottom, y0)
                            right, top = max(right, x1), max(top, y1)
                        pages.append(text_page.get_text_bounded(left, bottom, right, top))
                    finally:
                        text_page.close()
                finally:
                    page.close()
    except ImportError as error:
        raise RuntimeError("PDF 解析依赖未安装") from error
    except (ValueError, OSError) as error:
        raise RuntimeError(f"PDF 无法读取：{error}") from error
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in "\n".join(pages).splitlines()]
    return "\n".join(line for line in lines if line)
