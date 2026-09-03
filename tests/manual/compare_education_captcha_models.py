"""Compare saved vision models on identical live CHSI captchas without submitting."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from DrissionPage import ChromiumOptions, ChromiumPage

import education_certificate
from education_tool_security import get_education_api_key


MODEL_NAMES = (
    "deepseek-v4-flash-vision-exp",
    "MiniMax-M3",
)


def _load_saved_models() -> dict[str, dict[str, Any]]:
    config_path = (
        Path(os.environ["LOCALAPPDATA"])
        / "EducationCertificateTool"
        / "config.json"
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return {
        str(model.get("model") or ""): dict(model)
        for model in config.get("saved_models") or []
    }


def _recognize_once(
    images: education_certificate.CaptchaImageVariants,
    config: dict[str, Any],
) -> dict[str, Any]:
    api_key = get_education_api_key(
        str(config.get("api_provider") or ""),
        str(config.get("base_url") or ""),
    )
    if not api_key:
        raise RuntimeError(f"{config.get('model')} 未配置 API Key")

    original_invoke = education_certificate._invoke_model
    calls: list[float] = []

    def counted_invoke(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return original_invoke(*args, **kwargs)
        finally:
            calls.append(round(time.perf_counter() - started, 3))

    education_certificate._invoke_model = counted_invoke
    started = time.perf_counter()
    try:
        result = education_certificate.recognize_captcha(
            images,
            config,
            api_key,
        )
    finally:
        education_certificate._invoke_model = original_invoke
    captcha_type, answer, confidence, detail, independently_agreed = result
    return {
        "model": str(config.get("model") or ""),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "api_calls": len(calls),
        "call_seconds": calls,
        "type": captcha_type,
        "answer": answer,
        "confidence": confidence,
        "independently_agreed": independently_agreed,
        "detail": detail,
    }


def _capture_with_wait(
    tab: Any,
    *,
    timeout: float = 5.0,
) -> education_certificate.CaptchaImageVariants:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return education_certificate.capture_captcha_variants(tab)
        except RuntimeError as error:
            last_error = error
            time.sleep(0.2)
    snapshot = education_certificate.read_chsi_page_snapshot(tab)
    raise RuntimeError(
        f"验证码等待超时 url={snapshot.get('url')!r} "
        f"text={snapshot.get('text', '')[:160]!r}"
    ) from last_error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--name", required=True)
    parser.add_argument("--certificate-number", required=True)
    args = parser.parse_args()

    saved_models = _load_saved_models()
    missing = [name for name in MODEL_NAMES if name not in saved_models]
    if missing:
        raise RuntimeError(f"缺少模型配置：{', '.join(missing)}")

    options = ChromiumOptions()
    options.set_address(args.address)
    browser = ChromiumPage(options)
    tab = browser.new_tab()
    results: list[dict[str, Any]] = []
    try:
        for sample_no in range(1, max(1, int(args.samples)) + 1):
            education_certificate.navigate_to_chsi(tab)
            education_certificate.fill_chsi_query_page(
                tab,
                args.name,
                args.certificate_number,
                skip_navigation=True,
            )
            images = _capture_with_wait(tab)
            image_id = hashlib.sha256(
                images.original.encode("ascii")
            ).hexdigest()[:12]
            order = MODEL_NAMES if sample_no % 2 else tuple(reversed(MODEL_NAMES))
            sample_results: list[dict[str, Any]] = []
            for model_name in order:
                print(
                    f"RUN sample={sample_no} image={image_id} model={model_name}",
                    flush=True,
                )
                try:
                    outcome = _recognize_once(images, saved_models[model_name])
                except Exception as error:
                    outcome = {
                        "model": model_name,
                        "error_type": type(error).__name__,
                        "error": str(error).splitlines()[0][:160],
                    }
                outcome["sample"] = sample_no
                outcome["image_id"] = image_id
                sample_results.append(outcome)
                results.append(outcome)
                print(json.dumps(outcome, ensure_ascii=True), flush=True)
            successful = [
                item
                for item in sample_results
                if item.get("type") and item.get("answer")
            ]
            answers = {
                (item.get("type"), item.get("answer"))
                for item in successful
            }
            print(
                json.dumps(
                    {
                        "sample": sample_no,
                        "image_id": image_id,
                        "models_agree": len(successful) == 2 and len(answers) == 1,
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
    finally:
        try:
            tab.close()
        except Exception:
            pass

    print("SUMMARY")
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
