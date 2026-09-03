"""Compare saved standalone vision models on one certificate image."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
    image_path: Path,
    config: dict[str, Any],
    expected: dict[str, str],
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
        result = education_certificate.recognize_certificate_image(
            image_path,
            config,
            api_key,
        )
    finally:
        education_certificate._invoke_model = original_invoke
    outcome = {
        "model": str(config.get("model") or ""),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "api_calls": len(calls),
        "call_seconds": calls,
        "name": result.name,
        "certificate_number": result.certificate_number,
        "school": result.school,
        "major": result.major,
        "confidence": result.confidence,
        "critical_conflicts": list(result.critical_conflicts),
        "warnings": list(result.warnings),
    }
    if expected:
        outcome["exact_matches"] = {
            field: getattr(result, field) == value
            for field, value in expected.items()
        }
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--expected-name", default="")
    parser.add_argument("--expected-certificate-number", default="")
    parser.add_argument("--expected-school", default="")
    parser.add_argument("--expected-major", default="")
    args = parser.parse_args()
    saved_models = _load_saved_models()
    missing = [name for name in MODEL_NAMES if name not in saved_models]
    if missing:
        raise RuntimeError(f"缺少模型配置：{', '.join(missing)}")

    results: list[dict[str, Any]] = []
    expected = {
        field: value
        for field, value in {
            "name": args.expected_name,
            "certificate_number": args.expected_certificate_number,
            "school": args.expected_school,
            "major": args.expected_major,
        }.items()
        if value
    }
    rounds = max(1, int(args.rounds))
    for round_no in range(1, rounds + 1):
        order = MODEL_NAMES if round_no % 2 else tuple(reversed(MODEL_NAMES))
        for model_name in order:
            print(f"RUN round={round_no} model={model_name}", flush=True)
            outcome = _recognize_once(
                args.image,
                saved_models[model_name],
                expected,
            )
            outcome["round"] = round_no
            results.append(outcome)
            print(json.dumps(outcome, ensure_ascii=True), flush=True)
    print("SUMMARY")
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
