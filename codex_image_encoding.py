"""Metadata-free model copies without inflating compressed photo pixels."""

from __future__ import annotations

import io
import math
from PIL import Image, ImageChops, ImageOps, ImageStat


def export_image_bytes(image: Image.Image, *, preserve_pixels: bool = False) -> bytes:
    """Choose a compact copy; photo encoding must retain at least 42 dB PSNR."""
    lossless = io.BytesIO()
    image.save(lossless, "PNG", optimize=True)
    baseline = lossless.getvalue()
    if preserve_pixels or "A" in image.getbands() or "transparency" in image.info:
        return baseline
    with image.convert("RGB") as rgb:
        sample = rgb.copy()
        try:
            sample.thumbnail((256, 256))
            if sample.getcolors(128) is not None:
                return baseline
        finally:
            sample.close()
        encoded = io.BytesIO()
        rgb.save(encoded, "JPEG", quality=95, subsampling=0, optimize=True)
        candidate = encoded.getvalue()
        if len(candidate) >= len(baseline) * 0.95:
            return baseline
        with Image.open(io.BytesIO(candidate)) as decoded:
            with ImageChops.difference(rgb, decoded) as difference:
                mse = sum(value * value for value in ImageStat.Stat(difference).rms) / 3
        return (
            candidate if not mse or 10 * math.log10(255 * 255 / mse) >= 42 else baseline
        )


def _jpeg_pixels(data: bytes) -> bytes:
    # Keep coding/color-transform segments, discard APP/COM and trailing bytes.
    # Entropy-coded scans are copied verbatim, including progressive scans.
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("invalid JPEG header")
    result = bytearray(data[:2])
    position = 2
    while position < len(data):
        start = position
        if data[position] != 255:
            raise ValueError("invalid JPEG marker")
        while position < len(data) and data[position] == 255:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker == 0xD9:
            return bytes(result) + b"\xff\xd9"
        if marker in {0, 0xD8} or 0xD0 <= marker <= 0xD7:
            raise ValueError("unexpected JPEG marker")
        if position + 2 > len(data):
            break
        length = int.from_bytes(data[position : position + 2], "big")
        end = position + length
        if length < 2 or end > len(data):
            break
        if not (0xE0 <= marker <= 0xEF and marker != 0xEE) and marker != 0xFE:
            result.extend(data[start:end])
        position = end
        if marker == 0xDA:
            start = position
            while position < len(data):
                if data[position] != 255:
                    position += 1
                    continue
                next_byte = position + 1
                while next_byte < len(data) and data[next_byte] == 255:
                    next_byte += 1
                if next_byte >= len(data):
                    break
                if data[next_byte] == 0 or 0xD0 <= data[next_byte] <= 0xD7:
                    position = next_byte + 1
                    continue
                break
            result.extend(data[start:position])
    raise ValueError("incomplete JPEG")


def _webp_pixels(data: bytes) -> bytes:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("invalid WebP header")
    end = int.from_bytes(data[4:8], "little") + 8
    if end > len(data):
        raise ValueError("incomplete WebP")
    chunks = bytearray()
    position = 12
    while position + 8 <= end:
        kind = data[position : position + 4]
        length = int.from_bytes(data[position + 4 : position + 8], "little")
        next_position = position + 8 + length + length % 2
        if next_position > end:
            raise ValueError("incomplete WebP chunk")
        payload = data[position + 8 : position + 8 + length]
        if kind == b"VP8X":
            if length != 10 or payload[0] & 2:
                raise ValueError("unsupported animated WebP")
            payload = bytes([payload[0] & ~0x2C]) + payload[1:]
        if kind in {b"VP8X", b"VP8 ", b"VP8L", b"ALPH"}:
            chunks.extend(
                kind
                + length.to_bytes(4, "little")
                + payload
                + (b"\0" if length % 2 else b"")
            )
        position = next_position
    if position != end:
        raise ValueError("invalid WebP size")
    return b"RIFF" + (len(chunks) + 4).to_bytes(4, "little") + b"WEBP" + chunks


def model_image_bytes(data: bytes, image: Image.Image) -> bytes:
    """Caller validates frame/pixel limits before this operation."""
    if image.getexif().get(274, 1) == 1:
        if image.format == "JPEG":
            return _jpeg_pixels(data)
        if image.format == "WEBP":
            return _webp_pixels(data)
    # Physically normalize EXIF orientation before discarding its metadata.
    with ImageOps.exif_transpose(image) as oriented:
        mode = (
            "RGBA"
            if "A" in oriented.getbands() or "transparency" in oriented.info
            else "RGB"
        )
        with oriented.convert(mode) as pixels:
            pixels.info.clear()
            return export_image_bytes(pixels, preserve_pixels=image.format == "PNG")


def image_suffix(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "jpg"
    return "webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "png"
