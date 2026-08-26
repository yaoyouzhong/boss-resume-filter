"""Privacy contracts for public screenshot demo candidate data."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import runpy

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / "docs" / "assets" / "ai-recruitment-ppt"
GENERATOR_PATH = ASSET_DIR / "generate_latest_screenshots.py"
DEMO_PATHS = (
    ASSET_DIR / "demo-candidates.json",
    ASSET_DIR / "demo-candidates-menu.json",
)
README_DEMO_GENERATOR = ROOT / "scripts" / "generate_readme_demo.py"
USER_GUIDE_DIR = ROOT / "docs" / "assets" / "user-guide"


def _load_generator():
    spec = importlib.util.spec_from_file_location("demo_candidate_generator", GENERATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_public_demo_candidates_match_the_fully_synthetic_generator():
    generator = _load_generator()
    expected = generator.build_demo_candidates()

    assert len(expected) == 12
    for path in DEMO_PATHS:
        assert json.loads(path.read_text(encoding="utf-8")) == expected


def test_public_demo_candidates_expose_no_direct_identifiers_or_real_data_path():
    generator_source = GENERATOR_PATH.read_text(encoding="utf-8")
    assert "candidates_all.json" not in generator_source
    assert "build_sanitized_real_candidates" not in generator_source
    assert "select_real_job" not in generator_source
    assert "privacy_badge=False" not in generator_source

    generator = _load_generator()
    original_loader = generator.gui_main.load_job_config_snapshot
    try:
        generator.install_demo_job_config_source()
        loaded = generator.gui_main.load_job_config_snapshot(
            Path("ignored"), Path("ignored.bak")
        )
        assert loaded == {"job_requirements": generator.build_demo_job_rules()}
    finally:
        generator.gui_main.load_job_config_snapshot = original_loader

    for path in DEMO_PATHS:
        records = json.loads(path.read_text(encoding="utf-8"))
        serialized = json.dumps(records, ensure_ascii=False)
        assert not re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", serialized)
        assert not re.search(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            serialized,
        )
        assert not re.search(r"(?<!\d)\d{17}[0-9Xx](?!\d)", serialized)
        assert all(item["demo_data_origin"] == "fully_synthetic" for item in records)
        assert all(str(item["geek_id"]).startswith("DEMO-") for item in records)
        assert all("演示岗位" in str(item["job_name"]) for item in records)


def test_readme_video_uses_only_allowlisted_synthetic_screenshots():
    namespace = runpy.run_path(
        str(README_DEMO_GENERATOR),
        run_name="readme_demo_generator",
    )
    screenshot_names = {
        scene.screenshot
        for scene in namespace["SCENES"]
        if scene.screenshot is not None
    }

    assert namespace["SCREENSHOT_DIR"] == USER_GUIDE_DIR
    assert screenshot_names == {
        "01-home.png",
        "02-job-config-full.png",
        "04-run-full.png",
        "05-results.png",
        "11-review-workbench.png",
        "12-contact-workbench.png",
    }
    assert all((USER_GUIDE_DIR / name).is_file() for name in screenshot_names)

    generator_source = README_DEMO_GENERATOR.read_text(encoding="utf-8")
    assert "candidates_all.json" not in generator_source
    assert "api_config.json" not in generator_source
    assert "chrome_profile" not in generator_source.lower()


def test_readme_gif_is_animated_and_within_github_limit():
    namespace = runpy.run_path(
        str(README_DEMO_GENERATOR),
        run_name="readme_demo_generator",
    )
    preview_path = namespace["PREVIEW_GIF_PATH"]

    assert preview_path.is_file()
    assert preview_path.stat().st_size <= namespace["GITHUB_GIF_LIMIT_BYTES"]
    with Image.open(preview_path) as image:
        assert image.format == "GIF"
        assert image.size == (960, 540)
        assert image.is_animated
        assert image.n_frames > 100
