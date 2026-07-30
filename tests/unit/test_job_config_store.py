"""Persistence guarantees for the multi-job configuration."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import safe_json_store
from job_config_store import load_job_config_snapshot, save_job_config_snapshot


def _config(job_name: str) -> dict:
    return {
        "requirement_template": "template",
        "job_requirements": {
            job_name: {
                "original_requirement": f"{job_name} requirement",
                "min_exp": 3,
            },
        },
    }


def test_job_config_restores_valid_backup_after_primary_corruption():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "job_config.json"
        backup = Path(str(path) + ".bak")
        first = _config("Java")
        second = _config("Python")
        save_job_config_snapshot(first, path)
        save_job_config_snapshot(second, path)
        path.write_text("{broken", encoding="utf-8")

        restored = load_job_config_snapshot(path)

        assert restored == first
        assert json.loads(path.read_text(encoding="utf-8")) == first
        assert json.loads(backup.read_text(encoding="utf-8")) == first


def test_job_config_save_never_rotates_corrupt_primary_over_good_backup():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "job_config.json"
        backup = Path(str(path) + ".bak")
        first = _config("Java")
        second = _config("Python")
        third = _config("Go")
        save_job_config_snapshot(first, path)
        save_job_config_snapshot(second, path)
        path.write_text("{broken", encoding="utf-8")

        save_job_config_snapshot(third, path)

        assert json.loads(path.read_text(encoding="utf-8")) == third
        assert json.loads(backup.read_text(encoding="utf-8")) == first


def test_job_config_failed_replace_keeps_previous_primary_readable():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "job_config.json"
        initial = _config("Java")
        save_job_config_snapshot(initial, path)
        real_replace = safe_json_store.os.replace

        def fail_primary_replace(source, target):
            if Path(target) == path:
                raise OSError("simulated replace failure")
            return real_replace(source, target)

        with patch.object(
            safe_json_store.os,
            "replace",
            side_effect=fail_primary_replace,
        ):
            try:
                save_job_config_snapshot(_config("Python"), path)
            except OSError:
                pass
            else:
                raise AssertionError("replace failure must be reported")

        assert json.loads(path.read_text(encoding="utf-8")) == initial
        assert not Path(str(path) + ".tmp").exists()
