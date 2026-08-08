"""Persistence guarantees for the multi-job configuration."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import safe_json_store
from data_schema import JOB_CONFIG_SCHEMA_VERSION, upgrade_job_config
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

        expected, _ = upgrade_job_config(first)
        assert restored == expected
        assert json.loads(path.read_text(encoding="utf-8")) == expected
        assert json.loads(backup.read_text(encoding="utf-8")) == expected


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

        expected_third, _ = upgrade_job_config(third)
        assert json.loads(path.read_text(encoding="utf-8")) == expected_third
        expected_backup, _ = upgrade_job_config(first)
        assert json.loads(backup.read_text(encoding="utf-8")) == expected_backup


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

        expected, _ = upgrade_job_config(initial)
        assert json.loads(path.read_text(encoding="utf-8")) == expected
        assert not Path(str(path) + ".tmp").exists()


def test_job_config_load_upgrades_legacy_schema_in_memory_only():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "job_config.json"
        legacy = _config("Java")
        path.write_text(
            json.dumps(legacy, ensure_ascii=False),
            encoding="utf-8",
        )

        loaded = load_job_config_snapshot(path)

        rule = loaded["job_requirements"]["Java"]
        assert loaded["schema_version"] == JOB_CONFIG_SCHEMA_VERSION
        assert rule["job_uuid"]
        assert json.loads(path.read_text(encoding="utf-8")) == legacy


def test_job_config_save_persists_schema_and_stable_job_id():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "job_config.json"

        save_job_config_snapshot(_config("Java"), path)
        first = json.loads(path.read_text(encoding="utf-8"))
        save_job_config_snapshot(first, path)
        second = json.loads(path.read_text(encoding="utf-8"))

        assert first["schema_version"] == JOB_CONFIG_SCHEMA_VERSION
        assert (
            first["job_requirements"]["Java"]["job_uuid"]
            == second["job_requirements"]["Java"]["job_uuid"]
        )


def test_job_config_roundtrip_preserves_gender_requirement():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "job_config.json"
        payload = _config("客户经理")
        payload["job_requirements"]["客户经理"]["gender"] = "女"

        save_job_config_snapshot(payload, path)
        loaded = load_job_config_snapshot(path)

        assert loaded["job_requirements"]["客户经理"]["gender"] == "女"
