"""Measure live CHSI captcha acceptance for saved vision models."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from DrissionPage import ChromiumOptions, ChromiumPage

import education_certificate
from education_controller import EducationController
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


def _page_id(page: Any) -> object:
    return EducationController.page_identity(page)


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
    raise RuntimeError("验证码等待超时") from last_error


def _new_result_tab(browser: Any, before_ids: set[object]) -> Any | None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        for candidate in list(browser.get_tabs() or []):
            if _page_id(candidate) not in before_ids:
                return candidate
        time.sleep(0.05)
    return None


def _recognize(
    images: education_certificate.CaptchaImageVariants,
    config: dict[str, Any],
) -> tuple[tuple[str, str, int, str, bool], float]:
    api_key = get_education_api_key(
        str(config.get("api_provider") or ""),
        str(config.get("base_url") or ""),
    )
    if not api_key:
        raise RuntimeError(f"{config.get('model')} 未配置 API Key")
    started = time.perf_counter()
    result = education_certificate.recognize_captcha(images, config, api_key)
    return result, round(time.perf_counter() - started, 3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", required=True)
    parser.add_argument("--trials-per-model", type=int, default=5)
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
    original_ids = {_page_id(page) for page in list(browser.get_tabs() or [])}
    test_tab = browser.new_tab()
    results: list[dict[str, Any]] = []
    try:
        trial_count = max(1, int(args.trials_per_model))
        schedule = [model for _ in range(trial_count) for model in MODEL_NAMES]
        for trial_no, model_name in enumerate(schedule, start=1):
            outcome: dict[str, Any] = {
                "trial": trial_no,
                "model": model_name,
            }
            created_tab = None
            try:
                education_certificate.navigate_to_chsi(test_tab)
                education_certificate.fill_chsi_query_page(
                    test_tab,
                    args.name,
                    args.certificate_number,
                    skip_navigation=True,
                )
                images = _capture_with_wait(test_tab)
                recognized, elapsed = _recognize(images, saved_models[model_name])
                captcha_type, answer, confidence, detail, agreed = recognized
                outcome.update({
                    "elapsed_seconds": elapsed,
                    "type": captcha_type,
                    "answer": answer,
                    "confidence": confidence,
                    "independently_agreed": agreed,
                    "detail": detail,
                })
                if (
                    captcha_type == "unknown"
                    or not answer
                    or (
                        confidence
                        < education_certificate.CAPTCHA_AUTO_SUBMIT_MIN_CONFIDENCE
                        and not agreed
                    )
                ):
                    outcome["accepted"] = False
                    outcome["result"] = "model_rejected"
                else:
                    before_ids = {
                        _page_id(page)
                        for page in list(browser.get_tabs() or [])
                    }
                    if not education_certificate.fill_captcha_answer(test_tab, answer):
                        raise RuntimeError("验证码写入失败")
                    if not education_certificate.click_chsi_query_button(test_tab):
                        raise RuntimeError("查询按钮点击失败")
                    created_tab = _new_result_tab(browser, before_ids)
                    result_page = created_tab or test_tab
                    accepted, message = education_certificate.check_query_result(
                        result_page,
                        timeout=15.0,
                    )
                    outcome["accepted"] = accepted
                    outcome["result"] = message
            except Exception as error:
                outcome.update({
                    "accepted": False,
                    "result": "error",
                    "error_type": type(error).__name__,
                    "error": str(error).splitlines()[0][:160],
                })
            finally:
                if created_tab is not None and _page_id(created_tab) not in original_ids:
                    try:
                        created_tab.close()
                    except Exception:
                        pass
            results.append(outcome)
            print(json.dumps(outcome, ensure_ascii=True), flush=True)
    finally:
        try:
            test_tab.close()
        except Exception:
            pass

    print("SUMMARY")
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
