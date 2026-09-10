from pathlib import Path
from tempfile import TemporaryDirectory

from education_tool_pdf import extract_pdf_text_lightweight
from education_certificate import extract_pdf_text
from education_pdf_images import render_preview_page
from tests.pdf_samples import write_pdf


def test_pdf_text_is_complete_and_shared_by_both_hosts():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "certificate.pdf"
        write_pdf(path, [
            b"BT /F1 12 Tf 10 70 Td (Name  Test) Tj ET",
            b"BT /F1 12 Tf 10 70 Td (ID 102891202305002814) Tj ET",
        ], width=400)
        expected = "Name Test\nID 102891202305002814"
        assert extract_pdf_text_lightweight(path) == expected
        assert extract_pdf_text(path) == expected


def test_pdf_reader_handles_empty_text_and_releases_resources_after_failure():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "scan.pdf"
        write_pdf(path, [b"0 0 1 rg 0 0 200 100 re f"])
        assert extract_pdf_text_lightweight(path) == ""
        invalid = Path(directory) / "broken.pdf"
        invalid.write_bytes(b"not a pdf")
        try:
            extract_pdf_text_lightweight(invalid)
        except RuntimeError as error:
            assert "PDF 无法读取" in str(error)
        else:
            raise AssertionError("unreadable PDF should fail")
        preview, pages = render_preview_page(path)
        assert pages == 1
        assert preview.getpixel((20, 20)) == (0, 0, 255)
        preview.close()
        path.rename(Path(directory) / "released.pdf")


def test_parallel_text_and_preview_requests_are_serialized():
    from concurrent.futures import ThreadPoolExecutor
    with TemporaryDirectory() as directory:
        path = Path(directory) / "text.pdf"
        write_pdf(path, [b"BT /F1 12 Tf 10 70 Td (ID 123456789012345678) Tj ET"], width=400)
        def read(index):
            if index % 2:
                return extract_pdf_text(path)
            image, count = render_preview_page(path)
            try:
                return count, image.size
            finally:
                image.close()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(read, range(16)))
        assert results[1::2] == ["ID 123456789012345678"] * 8
        assert all(count == 1 and size == (1200, 300) for count, size in results[::2])
