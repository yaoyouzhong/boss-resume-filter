"""Compatibility entry point for the standalone tool's shared PDF reader."""
from pathlib import Path


def extract_pdf_text_lightweight(path: str | Path) -> str:
    """Use the same PDFium reader and lock as certificate previews/rendering."""
    from education_pdf_images import extract_pdf_text

    return extract_pdf_text(path)
