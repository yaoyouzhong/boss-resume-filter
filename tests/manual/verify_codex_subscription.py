"""Opt-in live Codex checks and isolated visible GUI preview with synthetic data."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE"] = "1"
os.environ["BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE"] = "1"
CONFIG = {
    "api_provider": "codex",
    "base_url": "codex://local",
    "model": "gpt-6.1-sol",
    "llm_read_timeout": 120,
}


def live(output, resume=False):
    from PIL import Image, ImageDraw, ImageFont
    from codex_adapter import ADAPTER
    from education_certificate import recognize_certificate_image
    from job_ai_parser import enhance_config_with_ai
    from llm_eval import evaluate_with_resume
    from resume_ai_profile import extract_profile_with_ai
    from vision_capability import probe_image_connectivity

    results = (
        json.loads(output.read_text(encoding="utf-8"))
        if resume and output.exists()
        else {}
    )

    def record(name, value):
        results[name] = value
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(name, json.dumps(value, ensure_ascii=False), flush=True)

    status = ADAPTER.status()
    assert status["compatible"] and status["chatgpt_logged_in"], status
    record(
        "runtime", {key: status[key] for key in ("version", "auth_mode", "compatible")}
    )
    with tempfile.TemporaryDirectory(prefix="codex-synthetic-") as directory:
        import vision_capability

        if not results.get("image_connection", {}).get("vision_verified"):
            with patch.object(
                vision_capability, "CACHE_DIR", Path(directory) / "vision"
            ):
                result = probe_image_connectivity(CONFIG, "", force=True)
            record("image_connection", result)
            assert result["vision_verified"], result

        if not results.get("resume_evaluation", {}).get("success"):
            candidate = {
                "name": "合成测试",
                "rule_score": 70,
                "match_score": 70,
                "education": "本科",
                "experience": "5年",
            }
            result = evaluate_with_resume(
                candidate,
                "合成测试简历：统招本科，5年 Java 开发经验，熟悉 Spring Boot 和 MySQL。",
                "Java 工程师，要求本科及以上、4年以上 Java 开发经验",
                CONFIG,
                "",
            )
            record(
                "resume_evaluation",
                {
                    "success": result.success,
                    "model": result.model,
                    "score": candidate["match_score"],
                    "reason": result.reason,
                },
            )
            assert (
                result.success and candidate.get("resume_eval_model") == CONFIG["model"]
            )

        result = enhance_config_with_ai(
            "招聘 Java 工程师，工作地点北京，要求5年以上经验，统招本科，薪资20-30K。",
            {
                "job_requirements": {
                    "Java 工程师": {
                        "keywords": [{"name": "Java", "weight": 3}],
                        "min_exp": 5,
                        "edu": "本科",
                    }
                }
            },
            CONFIG,
            "",
        )
        record("job_requirements", {"success": result.success, "reason": result.reason})
        assert result.success, result

        result = extract_profile_with_ai(
            "合成测试简历：张测试，男，30岁，5年Java经验，本科；期望北京，期望薪资20-30K，在职看机会。",
            {},
            CONFIG,
            "",
        )
        record("resume_profile", result)
        assert result and not result.get("error") and result.get("filled"), result

        source = Path(directory) / "synthetic-certificate.png"
        font = ImageFont.truetype(
            str(Path(os.environ["WINDIR"]) / "Fonts" / "msyh.ttc"), 44
        )
        with Image.new("RGB", (1500, 950), "white") as image:
            draw = ImageDraw.Draw(image)
            for index, text in enumerate(
                (
                    "毕业证书",
                    "姓名：张测试",
                    "学校：合成测试大学",
                    "专业：计算机科学与技术",
                    "学历：本科",
                    "毕业日期：2020年6月30日",
                    "证书编号：110001202005123456",
                    "本图仅用于软件测试，不是真实证书",
                )
            ):
                draw.text((80, 70 + index * 100), text, font=font, fill="black")
            image.save(source)
        result = recognize_certificate_image(source, CONFIG, "", rotation_override=0)
        record(
            "certificate",
            {
                "name": result.name,
                "certificate_number": result.certificate_number,
                "model": result.model,
            },
        )
        assert (
            result.name == "张测试"
            and result.certificate_number == "110001202005123456"
        ), result
    record("status", "passed")


def gui(standalone):
    import tkinter as tk
    import gui_main

    with tempfile.TemporaryDirectory(prefix="codex-ui-preview-") as directory:
        directory = Path(directory)
        path = directory / "config.json"
        config = {
            **CONFIG,
            "saved_models": [
                {k: CONFIG[k] for k in ("api_provider", "base_url", "model")}
            ],
            "providers": {},
            "fetched_models": {},
        }
        path.write_text(json.dumps(config), encoding="utf-8")
        gui_main._enable_high_dpi_awareness()
        root = tk.Tk()
        with (
            patch.object(gui_main.BossFilterGUI, "load_config"),
            patch.object(gui_main.BossFilterGUI, "_start_browser_auto_check"),
            patch.object(gui_main, "load_candidates_all", return_value=[]),
        ):
            app = gui_main.BossFilterGUI(
                root,
                standalone_education=standalone,
                education_api_config=config,
                education_api_config_path=path,
                education_api_key_getter=lambda *_: "",
                run_preferences_path=directory / "preferences.json",
                start_with_settings=True,
            )
            root.title("Codex 接入验收 - " + ("学历核验" if standalone else "招聘平台"))
            app.show_page_api()
            root.geometry("1400x950+40+20")
            # Close only this isolated preview after a bounded inspection period.
            root.after(900000, root.destroy)
            root.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="Uses the local Codex subscription with synthetic content",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Keep passed synthetic checks from the same report",
    )
    parser.add_argument("--gui", choices=("boss", "education"))
    parser.add_argument(
        "--output", type=Path, default=ROOT / "tmp/codex-subscription-live.json"
    )
    args = parser.parse_args()
    if args.live:
        live(args.output, args.resume)
    if args.gui:
        gui(args.gui == "education")
