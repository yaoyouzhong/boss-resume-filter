"""Safety and coverage contract for the synthetic job-review GUI preview."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import stats_presenter


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREVIEW_PATH = PROJECT_ROOT / "tests" / "manual" / "preview_job_review_locator.py"
PREVIEW_SOURCE = PREVIEW_PATH.read_text(encoding="utf-8")
PREVIEW_TREE = ast.parse(PREVIEW_SOURCE)


def _load_preview_module():
    spec = importlib.util.spec_from_file_location("job_review_locator_preview", PREVIEW_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PREVIEW = _load_preview_module()


def test_preview_has_no_business_data_or_main_gui_imports():
    forbidden_imports = {"gui_main", "storage", "paths", "job_config_store"}
    imported_roots = set()
    for node in ast.walk(PREVIEW_TREE):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    assert not imported_roots.intersection(forbidden_imports)
    assert "candidates_all.json" not in PREVIEW_SOURCE
    assert "job_config.json" not in PREVIEW_SOURCE


def test_preview_has_no_file_write_calls():
    forbidden_calls = {
        "open",
        "write_text",
        "write_bytes",
        "unlink",
        "replace",
        "rename",
        "mkdir",
    }
    call_names = set()
    for node in ast.walk(PREVIEW_TREE):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            call_names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            call_names.add(node.func.attr)
    assert not call_names.intersection(forbidden_calls)


def test_preview_sets_gui_safety_flags_before_project_imports():
    first_project_import = PREVIEW_SOURCE.index("import gui_config_page")
    for flag in (
        "BOSS_RESUME_FILTER_DISABLE_DATA_MIGRATION",
        "BOSS_RESUME_FILTER_DISABLE_GUARD_PERSISTENCE",
        "BOSS_RESUME_FILTER_DISABLE_STARTUP_UPDATE",
    ):
        assert PREVIEW_SOURCE.index(flag) < first_project_import


def test_synthetic_review_reaches_threshold_and_all_locator_targets():
    review = PREVIEW.build_synthetic_review()
    assert review["feedback_count"] == stats_presenter.JOB_REVIEW_FEEDBACK_MINIMUM
    assert PREVIEW.review_targets(review) == PREVIEW.EXPECTED_TARGETS
    assert PREVIEW.EXPECTED_TARGETS == {
        "requirement",
        "education",
        "minimum_experience",
        "salary",
        "work_location",
        "skills",
        "required_conditions",
    }


def test_preview_reuses_production_workbench_and_locator():
    assert "gui_job_review.build_job_review_workbench(" in PREVIEW_SOURCE
    assert "gui_config_page.locate_job_config_review_target(" in PREVIEW_SOURCE
