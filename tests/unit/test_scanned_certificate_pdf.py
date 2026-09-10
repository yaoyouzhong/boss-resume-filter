from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tests.pdf_samples import write_pdf
from PIL import Image

from education_certificate import CertificateRecognition, recognize_certificate_pdf
from education_pdf_images import extract_certificate_pages


def test_scanned_pdf_uses_image_pipeline_and_cleans_temporary_files():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        image = root / "scan.png"
        Image.new("RGB", (600, 400), "blue").save(image)
        source = root / "scan.pdf"
        write_pdf(source, [b"0 0 1 rg 0 0 600 400 re f"], width=600, height=400)
        output = root / "pages"
        output.mkdir()
        pages = extract_certificate_pages(source, output)
        with Image.open(pages[0]) as extracted:
            assert extracted.size == (1800, 1200)
            assert extracted.getpixel((100, 100)) == (0, 0, 255)
        seen = []

        def recognize(path, config, key, **kwargs):
            assert Path(path).is_file()
            seen.append(Path(path))
            raise RuntimeError("model unavailable")

        with patch("education_certificate.recognize_certificate_image", side_effect=recognize):
            try:
                recognize_certificate_pdf(source, {}, "test", text_extractor=lambda _: "")
            except RuntimeError as error:
                assert str(error) == "model unavailable"
            else:
                raise AssertionError("model error must propagate")
        assert seen and all(not path.exists() for path in seen)
        expected = CertificateRecognition("测试", "123456", "学校", "专业", 0, 100, 99, (), "vision", certificate_type="degree")
        with patch("education_certificate.recognize_certificate_image", return_value=expected) as model:
            result = recognize_certificate_pdf(source, {"model": "vision"}, "test", text_extractor=lambda _: "")
        assert result == expected
        assert model.call_count == 1


def test_pdf_render_preserves_rotation_and_rejects_oversized_page_count():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "rotated.pdf"
        write_pdf(source, [b"1 0 0 rg 0 0 200 100 re f"], rotation=90)
        pages = extract_certificate_pages(source, root)
        with Image.open(pages[0]) as image:
            assert image.size == (300, 600)
        write_pdf(root / "large.pdf", [b""] * 7)
        try:
            extract_certificate_pages(root / "large.pdf", root)
        except ValueError as error:
            assert "6" in str(error)
        else:
            raise AssertionError("too many pages must not be silently truncated")
