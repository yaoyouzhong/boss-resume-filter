"""Learn image-input capability from synthetic probes and real recognition."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_adapter import detect_protocol, normalize_api_base_url


CACHE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "BossResumeFilter" / "vision_capabilities"
_confirmed: set[tuple[str, str]] = set()
_lock = threading.RLock()
_probe_locks: dict[tuple[str, str], threading.Lock] = {}


def _identity(config: dict) -> tuple[str, str]:
    # Keep endpoint paths and model names case-sensitive; never store credentials.
    identity = [
        str(config.get("api_provider") or "").strip().lower(),
        normalize_api_base_url(config),
        str(config.get("model") or "").strip(),
        detect_protocol(config),
        str(config.get("deployment") or ""),
        str(config.get("api_version") or ""),
    ]
    digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    return str(CACHE_DIR), digest


def has_verified_vision(config: dict) -> bool:
    """Read positive evidence; missing or corrupt metadata is unconfirmed."""
    key = _identity(config)
    with _lock:
        if key in _confirmed:
            return True
        try:
            record = json.loads((CACHE_DIR / f"{key[1]}.json").read_text(encoding="utf-8"))
            supported = isinstance(record, dict) and record.get("vision_verified") is True
        except (OSError, ValueError, TypeError):
            supported = False
        if supported:
            _confirmed.add(key)
        return supported


def record_vision_success(config: dict) -> None:
    """Persist only successful capability evidence, never images or responses."""
    key = _identity(config)
    with _lock:
        if has_verified_vision(config):
            return
        _confirmed.add(key)
        temporary = None
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE_DIR, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"vision_verified": True, "checked_at": datetime.now(timezone.utc).isoformat()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, CACHE_DIR / f"{key[1]}.json")
        except OSError:
            # An unwritable cache must not turn a successful recognition into a failure.
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def observe_image_recognition(config: dict, messages: list, payload: dict) -> None:
    """Only meaningful, confident image results count; HTTP 200 alone does not."""
    has_image = any(
        isinstance(message.get("content"), list)
        and any(isinstance(block, dict) and block.get("type") in {"image", "image_url"}
                for block in message["content"])
        for message in messages
    )
    if not has_image:
        return
    from education_certificate import normalize_recognition, parse_captcha_result

    try:
        certificate = normalize_recognition(payload)
        captcha = parse_captcha_result(payload)
    except (TypeError, ValueError, OverflowError):
        return
    if (certificate.name and certificate.certificate_number and certificate.confidence >= 80) or (
        captcha[0] != "unknown" and captcha[1] and captcha[2] >= 80
    ):
        record_vision_success(config)


def probe_vision_capability(config: dict, api_key: str, *, on_verified=None, on_connected=None, force=False) -> bool:
    """Read a fresh code found only in image pixels; failure stays unconfirmed."""
    with _lock:
        probe_lock = _probe_locks.setdefault(_identity(config), threading.Lock())
    with probe_lock:
        return _probe_vision_capability(config, api_key, on_verified=on_verified, on_connected=on_connected, force=force)


def _probe_vision_capability(config: dict, api_key: str, *, on_verified=None, on_connected=None, force=False) -> bool:
    if not force and has_verified_vision(config):
        return True
    if not api_key:
        return False
    from PIL import Image, ImageDraw, ImageFont
    from education_certificate import _build_image_messages, _invoke_model, _png_data_url

    challenge = "".join(secrets.choice("23456789") for _ in range(8))
    with Image.new("RGB", (360, 90), "white") as image:
        draw = ImageDraw.Draw(image)
        draw.text((18, 20), challenge, fill="black", font=ImageFont.load_default(size=48))
        data_url = _png_data_url(image)
    messages = _build_image_messages(
        config, data_urls=[data_url],
        system_prompt='Read the digits in the image. Return only JSON: {"code":"digits"}.',
        instruction="Transcribe all eight digits from left to right.",
    )
    try:
        result = _invoke_model(
            config, api_key, messages, timeout=30, max_tokens=2048,
            learn_vision=False, on_connected=on_connected,
        )
    except Exception:
        return False
    if str(result.get("code") or "").strip() != challenge:
        return False
    record_vision_success(config)
    if on_verified is not None:
        on_verified()
    return True


def probe_image_connectivity(config: dict, api_key: str, *, force=True) -> dict:
    """Expose separate connection and image results to all manual test entries."""
    started = time.monotonic()
    connected = []
    supported = probe_vision_capability(
        config, api_key, force=force, on_connected=lambda: connected.append(True),
    )
    model = str(config.get("model") or "")
    if supported:
        message = f"{model} 连接成功；已验证支持图片识别。"
    elif connected:
        message = f"{model} 连接成功；图片测试未通过，多模态能力尚需验证。"
    else:
        message = f"{model} 连接尚未确认；图片测试未完成，多模态能力尚需验证。"
    return {
        "status": "limited" if connected else "incompatible",
        "output_mode": "vision_probe", "vision_verified": supported,
        "connected": bool(connected), "message": message,
        "response_time": time.monotonic() - started,
    }
