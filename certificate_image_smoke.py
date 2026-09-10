"""Offline image-format checks run inside the packaged application."""
import base64
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from education_certificate import (
    build_initial_recognition_messages,
    prepare_image_data_url,
    validate_image_path,
)


def run_image_smoke_test() -> None:
    """Verify advertised formats decode and produce model-ready JPEGs, offline."""
    with TemporaryDirectory(prefix="certificate-image-smoke-") as directory:
        for extension, image_format in (
            ("webp", "WEBP"), ("png", "PNG"), ("jpg", "JPEG"), ("bmp", "BMP"),
        ):
            path = Path(directory) / f"certificate.{extension}"
            with Image.new("RGB", (240, 120), (20, 80, 160)) as image:
                image.save(path, format=image_format)
            validate_image_path(path)
            with Image.open(path) as image:
                image.load()
                if image.size != (240, 120):
                    raise RuntimeError(f"{image_format} image dimensions changed")
            data_url = prepare_image_data_url(path)
            if not data_url.startswith("data:image/jpeg;base64,"):
                raise RuntimeError(f"{image_format} did not produce a JPEG request")
            with Image.open(BytesIO(base64.b64decode(data_url.split(",", 1)[1]))) as image:
                image.load()
                if image.size != (240, 120):
                    raise RuntimeError(f"{image_format} request dimensions changed")
            messages = build_initial_recognition_messages({}, data_url, data_url)
            if not messages:
                raise RuntimeError(f"{image_format} request is empty")
